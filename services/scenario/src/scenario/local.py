"""Local dev entrypoint for the scenario function.

Runs the same domain logic (engine.run_scenario) as the Lambda handler
(handler.py), behind a small FastAPI app instead of API Gateway. This is the
"local ↔ cloud parity" adapter split from docs/decisions/0001-compute-and-iac.md
-- only this file and handler.py know about their respective runtimes.

Run with: uv run --package twin-r-scenario uvicorn scenario.local:app --reload
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from .building_lookup import get_building
from .engine import run_scenario
from .faults import get_fault, load_nearby_faults
from .ground_motion import estimate_significant_distance_km
from .probability_level import ProbabilityLevel, resolve_probability_level
from .response import prepare_response_buildings
from .rupture import Rupture, from_fault, from_manual_input

app = FastAPI(title="twin-r scenario function (local)")

# Dev-only: the Vite dev server runs on a different origin. Locked down
# properly once there's a real deployed frontend origin to allow instead.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# The `buildings` payload is repetitive JSON (short keys, floats over
# millions of similar rows) -- it compresses extremely well. Starlette only
# compresses responses at/above `minimum_size`, so tiny error responses
# aren't touched.
app.add_middleware(GZipMiddleware, minimum_size=1024)

DATA_DIR = os.environ.get("TWIN_R_DATA_DIR", "data")
# Individually overridable (mirrors handler.py) -- needed once
# buildings.parquet is a partitioned glob rather than a single file
# (ADR-0005), which doesn't fit the single-DATA_DIR convention.
BUILDINGS_PATH = os.environ.get("TWIN_R_BUILDINGS_PATH", f"{DATA_DIR}/exposure/buildings.parquet")
EXPOSURE_PATH = os.environ.get("TWIN_R_EXPOSURE_PATH", f"{DATA_DIR}/exposure/exposure.parquet")
FRAGILITY_PATH = os.environ.get("TWIN_R_FRAGILITY_PATH", f"{DATA_DIR}/fragility/fragility.parquet")
FAULTS_PATH = os.environ.get("TWIN_R_FAULTS_PATH", f"{DATA_DIR}/faults/qafi_faults.parquet")

# Default reference point when a caller doesn't specify one -- Madrid, as
# an arbitrary central point, not because it's seismically special. Any
# scenario/fault call can override via lat/lon or near_lat/near_lon; the
# frontend's map-driven flows always do.
DEFAULT_LAT, DEFAULT_LON = 40.4168, -3.7038


class ManualRuptureRequest(BaseModel):
    lat: float
    lon: float
    mag: float
    rake: float = 0.0
    # Advanced/optional: only combine into a finite rupture surface when
    # all three are given (ADR-0008) -- otherwise a point source at
    # (lat, lon) with `rake`, same as leaving them out entirely.
    strike: float | None = None
    dip: float | None = None
    ztor_km: float | None = None
    # MERISUR's probability-level selector (probability_level.py,
    # docs/merisur.md §4.7) -- defaults to "high" (median ground motion,
    # modal damage state), today's only pre-existing behaviour.
    probability_level: ProbabilityLevel = "high"


def _run_and_serialize(rupture: Rupture, probability_level: str) -> dict:
    try:
        level_params = resolve_probability_level(probability_level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        t0 = time.monotonic()
        # Computed here (not left to run_scenario's own default) so the
        # exact radius actually used for the spatial pre-filter is known
        # and can ride along in the response as `evaluated_region` --
        # that's what lets the frontend tell "never evaluated" (outside
        # this radius) apart from "evaluated, confidently undamaged"
        # (inside it, but not in `buildings` below) without a per-building
        # entry for either. Uses this same request's sigma_multiplier
        # (see estimate_significant_distance_km's docstring).
        radius_km = estimate_significant_distance_km(
            rupture, sigma_multiplier=level_params.sigma_multiplier
        )
        result = run_scenario(
            rupture,
            BUILDINGS_PATH,
            EXPOSURE_PATH,
            FRAGILITY_PATH,
            max_distance_km=radius_km,
            sigma_multiplier=level_params.sigma_multiplier,
            damage_percentile=level_params.damage_percentile,
        )
        n_evaluated = len(result)
        # Filters to damaged/uncertain buildings and trims to the thin
        # frontend-facing payload (see response.py's docstring for why
        # lon/lat/sa03_g are dropped and damage_state becomes an int code).
        result = prepare_response_buildings(result)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"missing pipeline output: {e}") from e

    print(
        f"scenario: {rupture.source} -> {n_evaluated} evaluated, "
        f"{len(result)} sent (damaged or uncertain), "
        f"finite_rupture={rupture.surface is not None}, in {elapsed_ms}ms"
    )
    return {
        "rupture": {
            "lat": rupture.lat,
            "lon": rupture.lon,
            "mag": rupture.mag,
            "source": rupture.source,
            # Whether ADR-0007's finite rupture plane was used (real Rjb)
            # vs. the point-source fallback -- surfaces the distinction
            # rather than hiding which approximation produced this result.
            "finite_rupture": rupture.surface is not None,
            # Echoes back which of the three tiers actually ran (see
            # ManualRuptureRequest/run_fault_scenario's own parameter) --
            # a caller that didn't specify one still sees "high" rather
            # than needing to remember the default.
            "probability_level": probability_level,
        },
        # The circle the frontend colors green-by-default within (any
        # building not individually listed below) -- an approximation of
        # the true spatial pre-filter, which is a padded lon/lat *box*
        # around this same point (_load_sites in engine.py), always at
        # least as large as this circle. A building right in the box's
        # corner, just outside this circle, could in principle have been
        # evaluated (and, correctly, omitted for being confidently
        # undamaged) yet render grey instead of green -- geometrically the
        # farthest, least-relevant sliver of the evaluated area, not worth
        # a second exact-shape payload to close.
        "evaluated_region": {"lat": rupture.lat, "lon": rupture.lon, "radius_km": radius_km},
        "buildings": result.to_dict(orient="records"),
        "n_evaluated": n_evaluated,
        "elapsed_ms": elapsed_ms,
    }


@app.post("/scenarios/manual")
def run_manual_scenario(req: ManualRuptureRequest) -> dict:
    rupture = from_manual_input(
        lat=req.lat,
        lon=req.lon,
        mag=req.mag,
        rake=req.rake,
        strike=req.strike,
        dip=req.dip,
        ztor_km=req.ztor_km,
    )
    return _run_and_serialize(rupture, req.probability_level)


@app.get("/faults")
def list_faults(
    lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON, radius_km: float = 3000.0
) -> dict:
    """Faults within `radius_km` of (lat, lon), nearest first -- feeds the
    frontend's "Automatic" fault picker (docs/merisur.md §4.1). QAFI only
    has 201 faults nationwide, so the default radius is generous enough
    that this effectively returns all of them, sorted by distance from
    (lat, lon), regardless of where in Spain that reference point is."""
    try:
        faults = load_nearby_faults(FAULTS_PATH, lat, lon, radius_km)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"missing pipeline output: {e}") from e
    return {"faults": faults.to_dict(orient="records")}


@app.get("/scenarios/fault")
def run_fault_scenario(
    fault_id: str,
    near_lat: float = DEFAULT_LAT,
    near_lon: float = DEFAULT_LON,
    probability_level: ProbabilityLevel = "high",
) -> dict:
    """Automatic mode (docs/merisur.md §4.1): a QAFI fault's own
    maximum-magnitude earthquake. A GET, not a POST -- unlike manual mode,
    every parameter that actually changes the returned `buildings` is
    already fixed by `fault_id` and `probability_level` alone (mmax/
    geometry/dip/rake all come from QAFI, see rupture.py's `from_fault`);
    `near_lat`/`near_lon` only pick which point on the trace gets echoed
    back as `rupture`'s location and `evaluated_region`'s display circle
    center. That makes this cacheable and testable as a plain URL, the
    same as `/faults` below.
    """
    try:
        fault = get_fault(FAULTS_PATH, fault_id, near_lat, near_lon)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"missing pipeline output: {e}") from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    rupture = from_fault(
        fault_id=fault["fault_id"],
        name=fault["name"],
        point_lat=fault["lat"],
        point_lon=fault["lon"],
        mmax=fault["mmax"],
        rake=fault["rake"],
        geometry_geojson=fault["geometry_geojson"],
        dip=fault["dip"],
        min_depth_km=fault["min_depth_km"],
        max_depth_km=fault["max_depth_km"],
    )
    return _run_and_serialize(rupture, probability_level)


@app.get("/buildings/{building_id}")
def building_info(building_id: str) -> dict:
    """Static exposure attributes for one building -- powers the
    frontend's building-click popup. Floors/construction year/use are
    already on the clicked PMTiles feature client-side; this only needs to
    cover what isn't baked into the tiles (taxonomy_class/height_class,
    see building_lookup.py)."""
    try:
        row = get_building(EXPOSURE_PATH, building_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"missing pipeline output: {e}") from e
    if row is None:
        raise HTTPException(status_code=404, detail=f"building_id {building_id!r} not found")
    return row.to_dict()


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
