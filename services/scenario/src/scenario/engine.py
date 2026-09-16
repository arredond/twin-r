"""Orchestrate one scenario run: rupture -> per-building damage.

Loads `buildings.parquet` + `exposure.parquet` (pipelines/exposure output)
and `fragility.parquet` (pipelines/fragility output) via DuckDB
(architecture: docs/milestone-1-plan.md §3/§5), evaluates the Akkar et al.
(2014) GMPE and fragility functions per building, and returns the **thin**
result (docs/decisions/0003-precomputed-building-tiles.md) -- building_id +
damage state + probabilities, no geometry. The frontend joins this onto the
already-published buildings PMTiles layer client-side.

This module always returns every evaluated building, including "None"
damage ones -- filtering the response down to only-changed buildings (the
fix for docs/validation-region-expansion.md §4's payload-size finding) is a
presentation-layer decision, made in local.py/handler.py, not here. Keeps
this function's contract stable for callers that *do* want the full
picture (tests, future aggregate-stats/precompute jobs).
"""

from __future__ import annotations

import math

import duckdb
import pandas as pd

from .damage import evaluate_damage_batch
from .fragility_lookup import FragilityTable
from .ground_motion import (
    IM_TYPE_TO_IMT,
    compute_intensity_gridded,
    estimate_significant_distance_km,
)
from .rupture import Rupture

_KM_PER_DEGREE_LAT = 111.0

_RESULT_COLUMNS = [
    "building_id",
    "lon",
    "lat",
    "municipality_code",
    "damage_state",
    "im_value",
    "im_type",
    "prob_none",
    "prob_slight",
    "prob_moderate",
    "prob_extensive",
    "prob_complete",
]


def _load_sites(
    con: duckdb.DuckDBPyConnection,
    buildings_path: str,
    exposure_path: str,
    rupture: Rupture,
    max_distance_km: float,
) -> pd.DataFrame:
    if buildings_path.startswith("s3://") or exposure_path.startswith("s3://"):
        # httpfs + DuckDB's default AWS credential chain (picks up the
        # Lambda execution role automatically) -- no explicit credentials
        # wired here, matching S3 access via IAM rather than secrets.
        con.execute("INSTALL httpfs; LOAD httpfs;")

    # A simple lat/lon degree bounding box, not a true geodesic radius --
    # cheap to evaluate and generous enough (longitude degrees narrow
    # towards the poles, so this box is always at least as wide as a true
    # circle of the same radius, never narrower) that it can't wrongly
    # exclude an in-range building.
    #
    # Padded around the rupture *surface's* own extent when one exists,
    # not just `rupture.lat`/`rupture.lon` (a single representative point
    # on the trace -- see rupture.py). A single-point box is a correctness
    # bug for a long fault: QAFI's longest traces run past 100km, so a site
    # near the *far* end of the trace but still within max_distance_km of
    # it can sit well outside a box only padded around the *near* end,
    # and get silently excluded rather than correctly evaluated (and,
    # likely, correctly found undamaged). Sizing the box off the surface's
    # own mesh instead means it always covers the whole trace, regardless
    # of the fault's own length.
    if rupture.surface is not None:
        mesh = rupture.surface.mesh
        assert mesh is not None  # always populated once a surface is built
        lon_min, lon_max = float(mesh.lons.min()), float(mesh.lons.max())
        lat_min, lat_max = float(mesh.lats.min()), float(mesh.lats.max())
    else:
        lon_min = lon_max = rupture.lon
        lat_min = lat_max = rupture.lat

    lat_pad = max_distance_km / _KM_PER_DEGREE_LAT
    lon_pad = max_distance_km / (
        _KM_PER_DEGREE_LAT * max(0.1, abs(_cos_deg((lat_min + lat_max) / 2)))
    )

    # centroid_lon/centroid_lat are precomputed columns (pipelines/exposure,
    # see parse.py's add_spatial_index_columns), not derived here via
    # ST_Centroid -- this is the fix from docs/validation-region-expansion.md
    # §4: filtering on plain stored columns lets DuckDB's parquet reader
    # prune whole row groups/files by their min/max statistics, and skips
    # reading the (much larger) geometry column for this query entirely.
    # Measured: ~14x faster than the ST_Centroid-on-the-fly equivalent for a
    # regional bounding-box query (see docs/decisions/0006).
    return con.execute(
        """
        SELECT
            b.building_id,
            b.centroid_lon AS lon,
            b.centroid_lat AS lat,
            b.municipality_code,
            e.taxonomy_class,
            e.height_class
        FROM read_parquet(?) AS b
        JOIN read_parquet(?) AS e USING (building_id)
        WHERE b.centroid_lon BETWEEN ? AND ?
          AND b.centroid_lat BETWEEN ? AND ?
        """,
        [
            buildings_path,
            exposure_path,
            lon_min - lon_pad,
            lon_max + lon_pad,
            lat_min - lat_pad,
            lat_max + lat_pad,
        ],
    ).df()


def _cos_deg(degrees: float) -> float:
    return math.cos(math.radians(degrees))


def run_scenario(
    rupture: Rupture,
    buildings_path: str,
    exposure_path: str,
    fragility_path: str,
    max_distance_km: float | None = None,
    sigma_multiplier: float = 0.0,
    damage_percentile: float | None = None,
) -> pd.DataFrame:
    """Run the full scenario chain and return the thin per-building result.

    `buildings_path`/`exposure_path` may be glob patterns (e.g.
    `parts/*.buildings.parquet`, per ADR-0005) as well as single files --
    DuckDB's `read_parquet` accepts both.

    `max_distance_km`: search radius for the spatial pre-filter. Defaults
    to `None`, meaning "derive it from this rupture's own magnitude/rake"
    via `estimate_significant_distance_km` (ground_motion.py) rather than a
    flat constant -- a small earthquake shouldn't pay to scan 300km of
    buildings it can't possibly affect. Pass an explicit value to override
    (e.g. tests pinning a known radius).

    `sigma_multiplier`/`damage_percentile`: MERISUR's probability-level
    selector (`probability_level.py`, `docs/merisur.md` §4.7) -- callers
    resolve a `ProbabilityLevel` ("high"/"low"/"very_low") to these two via
    `resolve_probability_level` and pass the result straight through. Both
    default to today's only behaviour (median ground motion, modal damage
    state) so an existing caller that doesn't pass them is unaffected. When
    `max_distance_km` is left as `None`, the derived radius uses the same
    `sigma_multiplier` -- see `estimate_significant_distance_km`'s own
    docstring for why that consistency matters.

    Columns: building_id, lon, lat, municipality_code, damage_state,
    im_value, im_type, prob_none, prob_slight, prob_moderate,
    prob_extensive, prob_complete.
    `lon`/`lat` (the same precomputed centroid columns `_load_sites`
    already reads) ride along so a caller that keeps only a subset of rows
    (local.py/handler.py drop the confidently-undamaged majority, see
    their own docstrings) can still place the ones it keeps on a map
    without a second lookup. `municipality_code` is the plain INE/Foral
    code column pipelines/exposure now stamps onto every building at
    ingest time (`pipeline.build_exposure`) -- a column read, not a
    per-request spatial join, is what lets `response.compute_municipality_stats`
    aggregate cheaply. `im_value`/`im_type` are the ground-motion
    value and intensity-measure type each building was *actually*
    evaluated against -- see `evaluate_damage_batch`'s docstring for why
    that varies by building instead of being one scenario-wide SA(0.3s)
    value (docs/validation-lorca-2011.md §10.2).
    """
    if max_distance_km is None:
        max_distance_km = estimate_significant_distance_km(
            rupture, sigma_multiplier=sigma_multiplier
        )

    con = duckdb.connect()
    sites = _load_sites(con, buildings_path, exposure_path, rupture, max_distance_km)
    if sites.empty:
        return pd.DataFrame(columns=_RESULT_COLUMNS)

    fragility_table = FragilityTable.from_parquet(fragility_path)

    # Only the IM types this fragility set actually vendors (FragilityTable.
    # used_im_types), not every entry in IM_TYPE_TO_IMT -- avoids paying for
    # a GMPE evaluation of an IM type nothing here is indexed by.
    lats = sites["lat"].to_numpy()
    lons = sites["lon"].to_numpy()
    im_values_by_type = {
        im_type: compute_intensity_gridded(
            rupture, lats, lons, IM_TYPE_TO_IMT[im_type], sigma_multiplier=sigma_multiplier
        )
        for im_type in fragility_table.used_im_types()
    }

    damage = evaluate_damage_batch(
        fragility_table,
        sites["taxonomy_class"].to_numpy(),
        sites["height_class"].to_numpy(),
        im_values_by_type,
        damage_percentile=damage_percentile,
    )

    result = pd.concat(
        [
            sites[["building_id", "lon", "lat", "municipality_code"]].reset_index(drop=True),
            damage.reset_index(drop=True),
        ],
        axis=1,
    )
    return result
