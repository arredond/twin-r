"""Serves buildings.pmtiles vector tiles joined against a scenario's thin
results, on the fly, one tile at a time.

This is the "(b)" option from the results-pipeline plan: rather than
re-tiling the whole buildings dataset per scenario (infeasible -- it'd mean
either shipping all of buildings.pmtiles into a Lambda to re-run tippecanoe,
or running tippecanoe synchronously in the request path), each request here
reads exactly one static tile from the existing buildings.pmtiles, reads the
scenario's own thin buildings.parquet (already filtered to just the
damaged/uncertain buildings by response.py's `prepare_response_buildings`,
so it's small -- tens of thousands of rows at most, not the national
dataset), and joins the two by `building_id` before re-encoding the tile.
Geometry is never re-tiled; only feature properties change.
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

import mapbox_vector_tile
import pandas as pd
from pmtiles.reader import MmapSource, Reader

from .results_store import scenario_dir


@lru_cache(maxsize=4)
def _pmtiles_reader(path: str) -> Reader:
    # Cached open mmap per path (there's only ever one buildings.pmtiles in
    # a given process) -- reopening per request would mean re-reading the
    # header/root directory on every tile fetch for no reason.
    f = open(path, "rb")  # noqa: SIM115 -- kept open for the process lifetime
    return Reader(MmapSource(f))


@lru_cache(maxsize=64)
def _building_results(scenario_id: str) -> dict[str, dict]:
    """building_id -> the scenario's thin result row, as a plain dict of
    extra tile properties. Cached per scenario_id (small: at most tens of
    thousands of rows) so repeated tile requests for the same scenario --
    the normal case, as a user pans/zooms -- don't re-read the parquet file
    each time."""
    path = scenario_dir(scenario_id) / "buildings.parquet"
    if not path.exists():
        raise FileNotFoundError(f"no results for scenario_id {scenario_id!r}")
    df = pd.read_parquet(path)
    # Vectorized, not `df.iterrows()` + a per-row `.drop()` -- that pattern
    # measured 36s for a 400k-row scenario (a large "very low probability"
    # nationwide run easily reaches that many rows) against 0.4s here, and
    # this is on the hot path for every tile a user's very first pan/zoom
    # touches. `keep="last"` because building_id isn't always unique in the
    # pipeline output (confirmed: 10 duplicate rows in one real 19,878-row
    # scenario) -- matches the previous per-row dict-building loop's
    # overwrite-on-conflict behavior, not a new decision.
    df = df.drop_duplicates(subset="building_id", keep="last").set_index("building_id")
    return df.to_dict(orient="index")


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
    from pmtiles.reader import Compression

    if header["tile_compression"] == Compression.GZIP:
        raw = gzip.decompress(raw)

    results = _building_results(scenario_id)
    decoded = mapbox_vector_tile.decode(raw)
    layers = []
    for layer_name, layer in decoded.items():
        features = layer["features"]
        if layer_name == "buildings":
            for feature in features:
                extra = results.get(feature["properties"].get("building_id"))
                if extra is not None:
                    feature["properties"].update(extra)
        layers.append({"name": layer_name, "features": features})

    # `quantize_bounds=None` keeps the already-tile-local coordinates
    # decode() handed back as-is, rather than re-projecting them as if they
    # were lon/lat -- geometry is untouched here, only properties changed.
    # `check_winding_order=False` skips mapbox_vector_tile's default
    # per-feature Shapely reconstruction + orient() + validity pass --
    # real cost for a dense tile (measured ~1.6s -> ~0.9s for a 22k-feature
    # tile, byte-identical output both ways). Safe here specifically
    # because this geometry always comes straight from an already-valid,
    # correctly-wound tippecanoe-produced tile (pipelines/exposure/tile.py)
    # -- we never construct or transform geometry ourselves, only copy it
    # through, so there's nothing for winding-order enforcement to catch.
    return mapbox_vector_tile.encode(
        layers, default_options={"quantize_bounds": None, "check_winding_order": False}
    )
