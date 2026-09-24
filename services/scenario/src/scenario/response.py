"""Shape `engine.run_scenario`'s full per-building result into the thin
payload the frontend actually needs, shared between local.py and handler.py
so the two runtimes can't drift on this logic.

The frontend only ever reads `building_id`, `damage_state` (map colour) and
the five `prob_*` fields (click-popup breakdown) -- see
apps/web/src/components/DamageMap.tsx. `lon`/`lat`/`im_value`/`im_type` ride along on
engine.py's own contract (useful to a caller that wants the full picture),
but every building the frontend colors is already a feature in the
buildings PMTiles layer it joins against by `building_id`, so shipping its
coordinates a second time here is pure waste -- confirmed unused via a
repo-wide reference check, not left out by inference. Dropping them, plus
encoding `damage_state` as its `DAMAGE_STATES` index instead of a string,
noticeably shrinks a payload that can otherwise run into the tens of MB.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from pyproj import Geod

from .damage import DAMAGE_STATES

if TYPE_CHECKING:
    from .rupture import Rupture

_GEOD = Geod(ellps="WGS84")

# A "None"-modal building only ships if its probability margin over the
# next-most-likely damage state is *this* narrow -- see local.py's own copy
# of this constant (kept in sync; docs/validation-region-expansion.md §4)
# for the full rationale.
UNCERTAINTY_MARGIN = 0.15

DAMAGE_STATE_CODES = {state: i for i, state in enumerate(DAMAGE_STATES)}

# Catastro's ATOM feed doesn't always file a municipality under its real
# INE code (Madrid: 28900, not INE 28079 -- pipelines/exposure/catastro.py's
# own docstring). Ceuta/Melilla are a confirmed case: Catastro lists them as
# "territorial offices" 55/56, not INE province codes 51/52, so buildings
# there carry `municipality_code` "55101"/"56101" (pipeline.build_exposure
# stamps Catastro's own code, per this module's docstring above) while
# `municipalities.pmtiles`/`municipalities.parquet` (sourced from IGN, keyed
# by real INE codes -- pipelines/exposure/municipalities.py) expect
# "51001"/"52001". Without this remap, DamageMap.tsx's join
# (`stats.municipality_code === tile's ine_code`, see its own comments)
# silently fails for these two, and a scenario there would never highlight
# them on the low-zoom choropleth. Kept in sync by hand with
# `pipelines/exposure/municipalities.py`'s own `_CATASTRO_CODE_TO_INE` --
# same two confirmed entries, not a general translator (see that module's
# comment for why one wasn't built).
_CATASTRO_CODE_TO_INE = {
    "55101": "51001",  # Ceuta
    "56101": "52001",  # Melilla
}

# Columns the frontend actually reads (see this module's docstring) --
# building_id + damage_state_code + the five probabilities.
_THIN_COLUMNS = ["building_id", "damage_state_code", *(f"prob_{s.lower()}" for s in DAMAGE_STATES)]


def prepare_response_buildings(result: pd.DataFrame) -> pd.DataFrame:
    """Filter to damaged/uncertain buildings and trim to the thin payload.

    `result` is engine.py's full per-building DataFrame (building_id, lon,
    lat, damage_state, im_value, im_type, prob_*). Returns a DataFrame with
    only the columns the frontend needs, ready for `.to_dict(orient="records")`.
    """
    max_other_prob = result[
        ["prob_slight", "prob_moderate", "prob_extensive", "prob_complete"]
    ].max(axis=1)
    is_close_call = (result["prob_none"] - max_other_prob) < UNCERTAINTY_MARGIN
    result = result[(result["damage_state"] != "None") | is_close_call]
    # Rounded to keep the extra bytes from a full-precision float64
    # round-trip down -- this subset is already small, but no reason to pay
    # for digits no one reads.
    result = result.round({c: 4 for c in result.columns if c.startswith("prob_")})
    result = result.assign(damage_state_code=result["damage_state"].map(DAMAGE_STATE_CODES))
    return result[_THIN_COLUMNS]


def compute_municipality_stats(result: pd.DataFrame) -> list[dict]:
    """Aggregate engine.py's *full* per-building result (every evaluated
    building, before `prepare_response_buildings` trims it down) into
    per-municipality damage-state counts, for the map's low-zoom
    choropleth (apps/web/src/components/DamageMap.tsx).

    A plain groupby on `result`'s `municipality_code` column -- pipelines/
    exposure now stamps that column onto every building at ingest time
    (`pipeline.build_exposure`), from the same INE/Foral code that already
    names its `<ine_code>.buildings.parquet` part (region.py), so engine.py
    carries it straight through for free.

    This used to be a DuckDB `ST_Contains` spatial join against a ~8,200-
    polygon municipalities GeoParquet, done fresh on every scenario request
    -- correct, but it scales with (buildings evaluated x municipality
    count), which made it the dominant cost of `/scenarios/fault` once
    long/nationwide faults routinely evaluate millions of buildings
    (measured: ~7s of the request at 3M evaluated buildings, see
    docs/decisions -- the regression from ~1-2s to ~10-20s per request
    that motivated this rewrite). Point-in-polygon membership doesn't
    change between requests, so doing it once per building at pipeline
    time instead of once per request removes that cost entirely, and drops
    the per-request dependency on municipalities.parquet/DuckDB's spatial
    extension for this endpoint altogether.
    """
    if result.empty:
        return []

    stats = []
    for code, group in result.groupby("municipality_code"):
        # pandas' groupby(...) key is typed as an opaque Hashable union, not
        # the actual `str` it holds at runtime here -- same known
        # pandas-stubs limitation as damage.py's groupby(...).indices.
        code = str(code)  # pyrefly: ignore
        state_counts = group["damage_state"].value_counts()
        stats.append(
            {
                "municipality_code": _CATASTRO_CODE_TO_INE.get(code, code),
                "n_evaluated": len(group),
                "counts": {state: int(state_counts.get(state, 0)) for state in DAMAGE_STATES},
            }
        )
    return stats


def evaluated_region(rupture: Rupture, radius_km: float) -> dict:
    """The circle the frontend colors green-by-default within (any
    building not individually listed in `buildings`), centered on the
    rupture's own representative point.

    For a finite rupture, buildings are evaluated within `radius_km` of the
    *whole surface* (engine.py's `_load_sites` pads the surface mesh's
    extent), not of one point, so the circle's radius grows by the
    surface's farthest mesh point from the center. Without that, a long
    fault's circle (centered on its trace midpoint, faults.py's
    `rupture_anchor`) would leave evaluated buildings near both ends of
    the trace rendered grey ("never evaluated"). The circle is still an
    approximation of the true evaluated shape (a padded lon/lat box, always
    at least as large): a building in the box's corners can be evaluated
    and omitted as confidently undamaged yet render grey, and for a long,
    narrow rupture the circle can reach slightly past the box's shorter
    sides. Both slivers sit at the farthest, least-shaken edge of the
    region, where "None" and "not evaluated" look the same in practice --
    not worth a second exact-shape payload to close.
    """
    extent_km = 0.0
    if rupture.surface is not None and rupture.surface.mesh is not None:
        lons = np.asarray(rupture.surface.mesh.lons).ravel()
        lats = np.asarray(rupture.surface.mesh.lats).ravel()
        _, _, dist_m = _GEOD.inv(
            np.full(lons.shape, rupture.lon), np.full(lats.shape, rupture.lat), lons, lats
        )
        extent_km = float(np.max(np.abs(dist_m))) / 1000.0
    return {
        "lat": rupture.lat,
        "lon": rupture.lon,
        "radius_km": round(radius_km + extent_km, 3),
    }
