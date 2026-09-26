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
import pyarrow as pa
import pyarrow.compute as pc
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


def shipped_mask(damage_state_code: np.ndarray, probs: np.ndarray) -> np.ndarray:
    """Which buildings the tile joins list: damaged, or "None" by a margin
    narrower than `UNCERTAINTY_MARGIN` over the next-likeliest state.
    `probs` is (len(DAMAGE_STATES), n), rows in DAMAGE_STATES order."""
    is_close_call = (probs[0] - probs[1:].max(axis=0, initial=0.0)) < UNCERTAINTY_MARGIN
    return (damage_state_code != 0) | is_close_call


def stored_results_columns(shipped: pa.Table) -> dict[str, _RowsInOrder]:
    """`shipped` (one row per listed building, from `shipped_buildings_table`
    batches) as the columns the per-building results file stores: one row
    per building_id, keeping the last, sorted by building_id. Those are the
    invariants `tiles.scenario_results.encode_sorted_unique` needs.

    Without materializing a sorted copy of the table: this computes only
    the row order (a stable sort of the ids, then drops all but the last
    of any repeated id), and each column is read in that order one chunk
    at a time as the encoder asks for it (`_RowsInOrder`). A scenario can
    list millions of buildings (3.4M for an M9 "very_low" on Madrid, which
    OOM-killed the scenario function, 2026-09-26). Converting them to Python
    objects, or copying the table to sort it, costs about a GB at that
    size. Arrow sorts strings by their UTF-8 bytes, which is the same order
    as Python's `<` on `str`, the order the reader's binary search uses."""
    ids = shipped.column("building_id")
    # Stable, so among rows sharing an id, the last one given stays last.
    order = pc.sort_indices(ids)  # pyrefly: ignore -- pyarrow.compute is generated at runtime
    sorted_ids = ids.take(order)
    n = len(sorted_ids)
    if n > 1:
        # Keep a row unless the next one (in sorted order) has the same id.
        differs_from_next = pc.not_equal(  # pyrefly: ignore -- generated at runtime
            sorted_ids.slice(0, n - 1), sorted_ids.slice(1)
        )
        keep = pa.concat_arrays([differs_from_next.combine_chunks(), pa.array([True])])
        order = order.filter(keep)
    del sorted_ids
    return {name: _RowsInOrder(shipped.column(name), order) for name in shipped.column_names}


class _RowsInOrder:
    """One column read in a given row order, a slice at a time: what
    `encode_sorted_unique` needs (`len()` and slicing into something with
    `to_pylist()`), without ever holding the reordered column whole."""

    def __init__(self, column: pa.ChunkedArray, order: pa.Array):
        self._column = column
        self._order = order

    def __len__(self) -> int:
        return len(self._order)

    def __getitem__(self, rows: slice) -> pa.ChunkedArray:
        return self._column.take(self._order[rows])


def shipped_buildings_table(
    building_ids: pa.Array,
    damage_state_code: np.ndarray,
    probs: np.ndarray,
) -> pa.Table:
    """The thin per-building rows (`_THIN_COLUMNS`) for the buildings
    `shipped_mask` keeps, from one batch of array-shaped results."""
    keep = shipped_mask(damage_state_code, probs)
    # Rounded to keep the extra bytes from a full-precision float64
    # round-trip down -- this subset is already small, but no reason to pay
    # for digits no one reads.
    return pa.table(
        {
            "building_id": building_ids.filter(pa.array(keep)),
            "damage_state_code": damage_state_code[keep].astype(np.int64),
            **{
                f"prob_{state.lower()}": np.round(probs[i][keep], 4)
                for i, state in enumerate(DAMAGE_STATES)
            },
        }
    )


def prepare_response_buildings(result: pd.DataFrame) -> pd.DataFrame:
    """Filter to damaged/uncertain buildings and trim to the thin payload.

    `result` is engine.py's full per-building DataFrame (building_id, lon,
    lat, damage_state, im_value, im_type, prob_*). Returns a DataFrame with
    only the columns the frontend needs, ready for `.to_dict(orient="records")`.
    Same rule as the streaming path (`shipped_buildings_table`).
    """
    codes = result["damage_state"].map(DAMAGE_STATE_CODES).to_numpy(dtype=np.int64)
    probs = result[[f"prob_{s.lower()}" for s in DAMAGE_STATES]].to_numpy(dtype=float).T
    table = shipped_buildings_table(
        pa.array(result["building_id"].to_numpy(), type=pa.string()), codes, probs
    )
    return table.to_pandas()


class MunicipalityCounter:
    """Per-municipality damage-state counts, accumulated batch by batch --
    engine.py streams a scenario's buildings through in chunks, so these
    counts are built up without ever holding every evaluated building at
    once. `stats()` gives the `municipality_stats` payload (see
    `compute_municipality_stats`). Buildings with no municipality_code are
    left out, as a pandas groupby on that column would."""

    def __init__(self) -> None:
        self._counts: dict[str, np.ndarray] = {}

    def add(self, municipality_codes: pa.Array, damage_state_code: np.ndarray) -> None:
        encoded = pa.array(municipality_codes, type=pa.string()).dictionary_encode()
        indices = encoded.indices.to_numpy(zero_copy_only=False)
        valid = encoded.indices.is_valid().to_numpy(zero_copy_only=False)
        n_codes, n_states = len(encoded.dictionary), len(DAMAGE_STATES)
        counts = np.bincount(
            indices[valid].astype(np.int64) * n_states + damage_state_code[valid],
            minlength=n_codes * n_states,
        ).reshape(n_codes, n_states)
        for code, row in zip(encoded.dictionary.to_pylist(), counts):
            if code in self._counts:
                self._counts[code] += row
            elif row.any():
                self._counts[code] = row.copy()

    @property
    def n_damaged(self) -> int:
        """Non-None buildings, the same "affected" definition as the stats
        (`count_damaged`)."""
        return int(sum(row[1:].sum() for row in self._counts.values()))

    def stats(self) -> list[dict]:
        return [
            {
                "municipality_code": _CATASTRO_CODE_TO_INE.get(code, code),
                "n_evaluated": int(row.sum()),
                "counts": {state: int(n) for state, n in zip(DAMAGE_STATES, row)},
            }
            for code, row in sorted(self._counts.items())
        ]


def compute_municipality_stats(result: pd.DataFrame) -> list[dict]:
    """Aggregate engine.py's *full* per-building result (every evaluated
    building, before `prepare_response_buildings` trims it down) into
    per-municipality damage-state counts, for the map's low-zoom
    choropleth (apps/web/src/components/DamageMap.tsx).

    Keyed on `result`'s `municipality_code` column -- pipelines/exposure
    stamps that column onto every building at ingest time
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

    Same counting as the streaming path (`MunicipalityCounter`), which
    engine.py's `summarize_scenario` uses; this DataFrame form is for
    callers holding a full `run_scenario` result.
    """
    counter = MunicipalityCounter()
    if not result.empty:
        counter.add(
            pa.array(result["municipality_code"].to_numpy(), type=pa.string()),
            result["damage_state"].map(DAMAGE_STATE_CODES).to_numpy(dtype=np.int64),
        )
    return counter.stats()


def count_damaged(result: pd.DataFrame) -> int:
    """Buildings whose predicted damage state isn't "None", over the *full*
    evaluated result (before `prepare_response_buildings` trims it) -- the
    same "affected" definition the municipality stats use (n_evaluated
    minus the None count), so the sidebar total and the choropleth can't
    disagree."""
    return int((result["damage_state"] != "None").sum()) if len(result) else 0


def evaluated_region(rupture: Rupture, radius_km: float) -> dict:
    """The circle the frontend colors green-by-default within (any
    building not individually listed in `buildings`), centered on the
    rupture's own representative point.

    For a finite rupture, buildings are evaluated within `radius_km` of the
    *whole surface* (engine.py's `_site_batches` pads the surface mesh's
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
