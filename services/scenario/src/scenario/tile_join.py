"""Serves buildings.pmtiles vector tiles joined against a scenario's thin
results, on the fly, one tile at a time, for local dev.

This is the "(b)" option from the results-pipeline plan: rather than
re-tiling the whole buildings dataset per scenario (infeasible -- it'd mean
either shipping all of buildings.pmtiles into a Lambda to re-run tippecanoe,
or running tippecanoe synchronously in the request path), each request here
reads exactly one static tile from the existing buildings.pmtiles, reads the
scenario's own thin buildings.json (already filtered to just the
damaged/uncertain buildings by response.py's `prepare_response_buildings`,
so it's small -- tens of thousands of rows at most, not the national
dataset), and joins the two by `building_id` before re-encoding the tile.
Geometry is never re-tiled; only feature properties change.

Only the local I/O (mmap'd PMTiles file, local disk results/ directory)
lives here -- the actual join (protobuf tag patching, see its own
docstring for why that's faster than mapbox_vector_tile's decode()/
encode()) is in the sibling `tiles` package's `tile_join.py`, shared with
the deployed tiles Lambda (services/tiles/src/tiles/handler.py), which
does the same join against S3-sourced bytes instead of local ones. Keeping
the join itself in one place means the two runtimes can't drift on it --
same "local <-> cloud parity" split as local.py/handler.py's own domain
logic (docs/decisions/0001-compute-and-iac.md).
"""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

from pmtiles.reader import Compression, MmapSource, Reader
from tiles.tile_join import join_tile_bytes

from .results_store import scenario_dir


@lru_cache(maxsize=4)
def _pmtiles_reader(path: str) -> Reader:
    # Cached open mmap per path (there's only ever one buildings.pmtiles in
    # a given process) -- reopening per request would mean re-reading the
    # header/root directory on every tile fetch for no reason.
    f = open(path, "rb")  # noqa: SIM115 -- kept open for the process lifetime
    return Reader(MmapSource(f))


def _building_results(scenario_id: str) -> dict[str, dict]:
    """See `_load_building_results`. Keyed on the file's mtime as well as
    `scenario_id`: ids are content-addressed (scenario_id.py), so with the
    scenario cache off a rerun rewrites the *same* scenario_id's file --
    e.g. after regenerating local data without bumping TWINER_DATA_VERSION
    -- and a pool worker's cache must not keep serving the old rows."""
    path = scenario_dir(scenario_id) / "buildings.json.gz"
    if not path.exists():
        raise FileNotFoundError(f"no results for scenario_id {scenario_id!r}")
    return _load_building_results(scenario_id, path.stat().st_mtime_ns)


@lru_cache(maxsize=64)
def _load_building_results(scenario_id: str, mtime_ns: int) -> dict[str, dict]:
    """building_id -> the scenario's thin result row, as a plain dict of
    extra tile properties. Cached per scenario_id (small: at most tens of
    thousands of rows) so repeated tile requests for the same scenario --
    the normal case, as a user pans/zooms -- don't re-read the results file
    each time.

    Gzipped JSON, not parquet -- see results_store.py's own comment on why
    (the tiles Lambda that reads this same file shape in the cloud has to
    stay under Lambda's 250MB zip-package limit, and pandas/pyarrow alone
    blow past that; plain uncompressed JSON was tried first but measured
    5-7x larger than the parquet it replaced). Reading it here also skips
    pandas/pyarrow entirely, not just to match the cloud format -- a plain
    dict-building loop over a JSON list is already fast enough at this
    scale (see the note this replaced: a *pandas* `iterrows()` + per-row
    `.drop()` loop measured 36s for 400k rows; this is a single pass
    building plain dicts, no DataFrame construction at all)."""
    path = scenario_dir(scenario_id) / "buildings.json.gz"
    rows = json.loads(gzip.decompress(path.read_bytes()))
    # keep-last: building_id isn't always unique in the pipeline output
    # (see DATA-SOURCES.md's "non-unique building_id" known issue) --
    # later rows overwriting earlier ones in this loop matches
    # pandas' drop_duplicates(keep="last"), not a new decision.
    return {
        row["building_id"]: {k: v for k, v in row.items() if k != "building_id"} for row in rows
    }


def warm_cache(pmtiles_path: str | Path, scenario_id: str) -> None:
    """Populates this process's `_pmtiles_reader`/`_building_results`
    caches for `scenario_id` ahead of any real tile request -- each cache
    lives in whichever process pool worker happens to run it (see
    local.py's `_TILE_POOL`), not shared across workers, so a fresh
    scenario is "cold" on every worker until something warms it. Submitted
    once per pool worker right after a scenario's results are written
    (local.py's `_run_and_serialize`) so that cost lands in the background
    before the user's first pan/zoom, not on it."""
    _pmtiles_reader(str(pmtiles_path))
    _building_results(scenario_id)


def join_tile(pmtiles_path: str | Path, scenario_id: str, z: int, x: int, y: int) -> bytes | None:
    """Returns a raw (uncompressed) MVT tile with each `buildings` feature's
    properties extended by the scenario's result for that `building_id`
    (when present), or None if the base tile has no data at (z, x, y).
    Left uncompressed -- the FastAPI app's own GZipMiddleware (local.py)
    handles response compression, same as every other route here."""
    reader = _pmtiles_reader(str(pmtiles_path))
    raw = reader.get(z, x, y)
    if raw is None:
        return None

    header = reader.header()
    # buildings.pmtiles is always gzip-internal-compressed (tippecanoe's
    # default, see pipelines/exposure/tile.py) -- decompress only if the
    # header says so rather than assuming, since a future re-tile could in
    # principle change this.
    if header["tile_compression"] == Compression.GZIP:
        raw = gzip.decompress(raw)

    results = _building_results(scenario_id)
    return join_tile_bytes(raw, results)
