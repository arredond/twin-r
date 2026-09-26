"""Serves buildings.pmtiles vector tiles joined against a scenario's thin
results, on the fly, one tile at a time, for local dev.

This is the "(b)" option from the results-pipeline plan: rather than
re-tiling the whole buildings dataset per scenario (infeasible -- it'd mean
either shipping all of buildings.pmtiles into a Lambda to re-run tippecanoe,
or running tippecanoe synchronously in the request path), each request here
reads exactly one static tile from the existing buildings.pmtiles, reads the
scenario's own per-building results file (`tiles.scenario_results`: just
the damaged/uncertain buildings, `response.shipped_mask` -- up to hundreds
of thousands of rows for a large scenario, not the national dataset), and
joins the two by `building_id` before re-encoding the tile.
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
from functools import lru_cache
from pathlib import Path

from pmtiles.reader import Compression, MmapSource, Reader
from tiles import scenario_results
from tiles.results_cache import ResultsCache
from tiles.scenario_results import ScenarioResults
from tiles.tile_join import join_debris_tile_bytes, join_tile_bytes

from .results_store import scenario_dir


@lru_cache(maxsize=4)
def _pmtiles_reader(path: str) -> Reader:
    # Cached open mmap per path (there's only ever one buildings.pmtiles in
    # a given process) -- reopening per request would mean re-reading the
    # header/root directory on every tile fetch for no reason.
    f = open(path, "rb")  # noqa: SIM115 -- kept open for the process lifetime
    return Reader(MmapSource(f))


# Bounded by memory, not by count, per tile-pool worker process: the same
# cache as the tiles Lambda's (tiles.results_cache).
_RESULTS_CACHE = ResultsCache()


def _building_results(scenario_id: str) -> ScenarioResults:
    """The scenario's listed buildings, looked up by building_id per tile.
    Cached so repeated tile requests for the same scenario -- the normal
    case, as a user pans/zooms -- don't re-read the results file each time.
    Same file and decoder as the tiles Lambda (`tiles.scenario_results`,
    which documents the format), so the two runtimes can't drift on it.

    Keyed on the file's mtime as well as `scenario_id`: ids are
    content-addressed (scenario_id.py), so with the scenario cache off a
    rerun rewrites the *same* scenario_id's file -- e.g. after regenerating
    local data without bumping TWINER_DATA_VERSION -- and a pool worker's
    cache must not keep serving the old rows."""
    path = scenario_dir(scenario_id) / scenario_results.FILENAME
    if not path.exists():
        raise FileNotFoundError(f"no results for scenario_id {scenario_id!r}")
    st = path.stat()
    return _RESULTS_CACHE.get_or_load((scenario_id, st.st_mtime_ns), st.st_size, path.read_bytes)


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


def join_tile(
    pmtiles_path: str | Path, scenario_id: str, z: int, x: int, y: int, debris: bool = False
) -> bytes | None:
    """Returns a raw (uncompressed) MVT tile with each `buildings` feature's
    properties extended by the scenario's result for that `building_id`
    (when present), or None if the base tile has no data at (z, x, y).
    With `debris`, `pmtiles_path` is debris.pmtiles and the tile is cut
    down to each damaged building's one matching ring instead
    (`tiles.tile_join.join_debris_tile_bytes`).
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
    return (join_debris_tile_bytes if debris else join_tile_bytes)(raw, results)
