"""Shape `engine.run_scenario`'s full per-building result into the thin
payload the frontend actually needs, shared between local.py and handler.py
so the two runtimes can't drift on this logic.

The frontend only ever reads `building_id`, `damage_state` (map colour) and
the five `prob_*` fields (click-popup breakdown) -- see
apps/web/src/components/DamageMap.tsx. `lon`/`lat`/`sa03_g` ride along on
engine.py's own contract (useful to a caller that wants the full picture),
but every building the frontend colors is already a feature in the
buildings PMTiles layer it joins against by `building_id`, so shipping its
coordinates a second time here is pure waste -- confirmed unused via a
repo-wide reference check, not left out by inference. Dropping them, plus
encoding `damage_state` as its `DAMAGE_STATES` index instead of a string,
noticeably shrinks a payload that can otherwise run into the tens of MB.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from .damage import DAMAGE_STATES

# A "None"-modal building only ships if its probability margin over the
# next-most-likely damage state is *this* narrow -- see local.py's own copy
# of this constant (kept in sync; docs/validation-region-expansion.md §4)
# for the full rationale.
UNCERTAINTY_MARGIN = 0.15

DAMAGE_STATE_CODES = {state: i for i, state in enumerate(DAMAGE_STATES)}

# Columns the frontend actually reads (see this module's docstring) --
# building_id + damage_state_code + the five probabilities.
_THIN_COLUMNS = ["building_id", "damage_state_code", *(f"prob_{s.lower()}" for s in DAMAGE_STATES)]


def prepare_response_buildings(result: pd.DataFrame) -> pd.DataFrame:
    """Filter to damaged/uncertain buildings and trim to the thin payload.

    `result` is engine.py's full per-building DataFrame (building_id, lon,
    lat, sa03_g, damage_state, prob_*). Returns a DataFrame with only the
    columns the frontend needs, ready for `.to_dict(orient="records")`.
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


def compute_municipality_stats(result: pd.DataFrame, municipalities_path: str) -> list[dict]:
    """Aggregate engine.py's *full* per-building result (every evaluated
    building, before `prepare_response_buildings` trims it down) into
    per-municipality damage-state counts, for the map's low-zoom
    choropleth (apps/web/src/components/DamageMap.tsx).

    Deliberately computed server-side rather than joined client-side: the
    frontend's thin per-building payload never carries lon/lat (this
    module's own docstring explains why), and buildings.pmtiles carries no
    municipality attribute either -- but `result` here already has
    `lon`/`lat` (engine.py's `run_scenario` docstring), and a spatial join
    against `municipalities_path` (a small GeoParquet of ~8,200 municipal
    boundary polygons, pipelines/exposure/src/exposure/municipalities.py)
    is cheap for however many buildings one scenario evaluates -- no need
    to precompute/store a municipality_code on every one of the millions
    of rows in buildings.parquet just for this.

    Uses DuckDB's spatial extension (`ST_Contains`) rather than adding a
    geopandas/shapely dependency to this service -- this service already
    leans on DuckDB for engine.py's own spatial pre-filter, and DuckDB
    reads GeoParquet's geometry column natively once the extension is
    loaded (verified this session: no separate WKB parsing needed).

    Unlike buildings/exposure/fragility, a missing `municipalities_path`
    doesn't fail the whole scenario -- it's a newer, separately-produced
    dataset (municipalities.py) that an existing dev/test setup may not
    have yet, and the choropleth is additive: falling back to `[]` just
    means the frontend keeps showing individual buildings at every zoom,
    not a broken scenario run.
    """
    if result.empty:
        return []

    con = duckdb.connect()
    if municipalities_path.startswith("s3://"):
        con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL spatial; LOAD spatial;")
    con.register("result_df", result[["lon", "lat", "damage_state"]])
    try:
        counts = con.execute(
            """
            SELECT m.ine_code, r.damage_state, COUNT(*) AS n
            FROM result_df AS r
            JOIN read_parquet(?) AS m ON ST_Contains(m.geometry, ST_Point(r.lon, r.lat))
            GROUP BY m.ine_code, r.damage_state
            """,
            [municipalities_path],
        ).df()
    except duckdb.IOException:
        return []
    if counts.empty:
        return []

    stats = []
    for ine_code, group in counts.groupby("ine_code"):
        state_counts = dict(zip(group["damage_state"], group["n"].astype(int)))
        stats.append(
            {
                "municipality_code": ine_code,
                "n_evaluated": int(group["n"].sum()),
                "counts": {state: state_counts.get(state, 0) for state in DAMAGE_STATES},
            }
        )
    return stats
