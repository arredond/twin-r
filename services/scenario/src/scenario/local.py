"""Local dev entrypoint for the scenario function.

Runs the same domain logic (engine.run_scenario) as the Lambda handler
(handler.py), behind a small FastAPI app instead of API Gateway. This is the
"local ↔ cloud parity" adapter split from docs/decisions/0001-compute-and-iac.md
-- only this file and handler.py know about their respective runtimes.

Run with: uv run --package twin-r-scenario uvicorn scenario.local:app --reload
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from concurrent.futures import ProcessPoolExecutor

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

from .building_lookup import get_building
from .engine import run_scenario
from .faults import get_fault, load_nearby_faults
from .ground_motion import estimate_significant_distance_km
from .probability_level import ProbabilityLevel, resolve_probability_level
from .response import compute_municipality_stats, prepare_response_buildings
from .results_store import (
    init_scenario,
    read_municipality_stats,
    read_status,
    write_buildings,
    write_municipality_stats,
)
from .rupture import Rupture, from_fault, from_manual_input
from .tile_join import join_tile, warm_cache

app = FastAPI(title="twin-r scenario function (local)")

# join_tile does real CPU work per call (MVT decode + re-encode --
# mapbox_vector_tile.encode alone measured ~0.4s for a mid-size tile, pure
# Python). A map viewport fires a dozen-plus tile requests at once; a sync
# route (FastAPI's default thread pool) hits the GIL and serializes that
# CPU work across them instead of overlapping it, measured to stack up to
# ~3s for the last tile in a burst of 12. A process pool sidesteps the GIL
# so concurrent tile requests genuinely run in parallel across cores.
# Capped at 8 rather than the host's full core count -- this is a local
# dev convenience, not something that needs to saturate the machine.
_TILE_POOL_WORKERS = min(os.cpu_count() or 4, 8)
_TILE_POOL = ProcessPoolExecutor(max_workers=_TILE_POOL_WORKERS)

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
# (ADR-0005), which doesn't fit the single-DATA_DIR convention. `data/
# exposure` is now the one consolidated national dataset (Lorca-only/
# region-scale subsets retired once the app worked at national scale).
# buildings.parquet has no single combined file by design (region.py's
# own docstring) -- always a `parts/*.buildings.parquet` glob.
# exposure.parquet *does* have a single combined file
# (`region.combine_exposure`), regenerated whenever the crawl changes.
BUILDINGS_PATH = os.environ.get(
    "TWIN_R_BUILDINGS_PATH", f"{DATA_DIR}/exposure/parts/*.buildings.parquet"
)
EXPOSURE_PATH = os.environ.get("TWIN_R_EXPOSURE_PATH", f"{DATA_DIR}/exposure/exposure.parquet")
FRAGILITY_PATH = os.environ.get("TWIN_R_FRAGILITY_PATH", f"{DATA_DIR}/fragility/fragility.parquet")
FAULTS_PATH = os.environ.get("TWIN_R_FAULTS_PATH", f"{DATA_DIR}/faults/qafi_faults.parquet")
# The same static buildings.pmtiles the frontend already loads directly
# (apps/web's VITE_BUILDINGS_PMTILES_URL) -- tile_join.py reads individual
# tiles from it and joins in a scenario's results, never re-tiling.
BUILDINGS_PMTILES_PATH = os.environ.get(
    "TWIN_R_BUILDINGS_PMTILES_PATH", f"{DATA_DIR}/exposure/buildings.pmtiles"
)

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

    scenario_id = uuid.uuid4().hex
    init_scenario(scenario_id)

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
        # Aggregated from the *full* result (before it's trimmed below) --
        # see compute_municipality_stats's own docstring for why this needs
        # lon/lat, which the thin payload deliberately drops.
        municipality_stats = compute_municipality_stats(result)
        write_municipality_stats(scenario_id, municipality_stats)
        # Filters to damaged/uncertain buildings and trims to the thin
        # frontend-facing payload (see response.py's docstring for why
        # lon/lat/im_value/im_type are dropped and damage_state becomes an
        # int code).
        result = prepare_response_buildings(result)
        write_buildings(scenario_id, result)
        # Fire-and-forget: pays each pool worker's cold-cache cost for this
        # scenario now, in the background, rather than on the user's first
        # tile request (see warm_cache's own docstring for why this is
        # needed per scenario, not just once at process startup). One
        # submission per worker is a best-effort way to reach all of them --
        # ProcessPoolExecutor gives no direct "run on every worker" API,
        # but submitting exactly as many tasks as there are workers reaches
        # each one as long as they're otherwise idle, the common case
        # between scenario runs.
        for _ in range(_TILE_POOL_WORKERS):
            _TILE_POOL.submit(warm_cache, BUILDINGS_PMTILES_PATH, scenario_id)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"missing pipeline output: {e}") from e

    print(
        f"scenario: {rupture.source} -> {n_evaluated} evaluated, "
        f"{len(result)} sent (damaged or uncertain), "
        f"finite_rupture={rupture.surface is not None}, in {elapsed_ms}ms"
    )
    return {
        "scenario_id": scenario_id,
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
        "municipality_stats": municipality_stats,
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


@app.get("/results/{scenario_id}/status")
def scenario_status(scenario_id: str) -> dict:
    """Per-layer readiness for a scenario run, keyed by the `scenario_id`
    the /scenarios/* routes return. Compute is still synchronous today (see
    results_store.py's docstring), so by the time a client can call this
    the run has already finished and every flag reads true -- this is
    scaffolding for the async job flow (poll while compute runs) that's the
    intended next step, kept working now so the frontend's legend loading
    indicators can be built against a stable contract before that lands."""
    status = read_status(scenario_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"scenario_id {scenario_id!r} not found")
    return status


@app.get("/results/{scenario_id}/municipality_stats")
def scenario_municipality_stats(scenario_id: str) -> list[dict]:
    stats = read_municipality_stats(scenario_id)
    if stats is None:
        raise HTTPException(status_code=404, detail=f"scenario_id {scenario_id!r} not found")
    return stats


@app.get("/tiles/{scenario_id}/{z}/{x}/{y}.mvt")
async def scenario_tile(scenario_id: str, z: int, x: int, y: int) -> Response:
    """A buildings vector tile with each feature's properties extended by
    this scenario's result for its `building_id`, when present -- lets the
    frontend drive a MapLibre vector source straight off scenario results
    instead of fetching every affected building_id and setFeatureState-ing
    them in one by one (see tile_join.py's docstring for the full design
    rationale, including why this reads one tile at a time rather than
    building a per-scenario buildings.pmtiles).

    Dispatched to `_TILE_POOL` (see its own comment) rather than called
    directly -- join_tile is CPU-bound, and running it in-process would
    serialize concurrent tile requests behind the GIL."""
    loop = asyncio.get_running_loop()
    try:
        tile = await loop.run_in_executor(
            _TILE_POOL, join_tile, BUILDINGS_PMTILES_PATH, scenario_id, z, x, y
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if tile is None:
        # No base-tile data at this z/x/y (e.g. open ocean) -- a 204, not a
        # 404: the scenario_id is valid, this tile is just legitimately
        # empty, same as buildings.pmtiles itself would return.
        return Response(status_code=204)
    return Response(content=tile, media_type="application/vnd.mapbox-vector-tile")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
