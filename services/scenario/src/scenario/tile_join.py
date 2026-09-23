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

The join itself works directly on the compiled MVT protobuf message
(`vector_tile_pb2`, the same schema `mapbox_vector_tile.encode`/`decode`
build on) rather than going through those functions. `encode()` always
reconstructs every feature's geometry via Shapely (construct a Polygon,
`orient()` it, validate it) even when the geometry is completely unchanged,
which is real cost at scale: ~0.9s of a 22,231-feature tile's ~1.1s total
was Shapely reconstruction alone, for work whose result we already know
(this geometry came from an already-valid, correctly-wound
tippecanoe-produced tile -- see pipelines/exposure/tile.py -- so there's
nothing to fix). Since a join only ever adds property tags to a feature and
never touches its geometry bytes, patching the protobuf's `tags`/`keys`/
`values` fields directly and leaving `geometry` untouched skips that
reconstruction (and the matching decode()-side GeoJSON conversion) for
every feature, not just the ones a scenario actually joins onto. Measured:
~0.03s versus ~0.9s of `encode()` alone for that same 22,231-feature tile.
"""

from __future__ import annotations

import gzip
from functools import lru_cache
from pathlib import Path

import pandas as pd
from mapbox_vector_tile.Mapbox import vector_tile_pb2 as mvt_pb2
from pmtiles.reader import Compression, MmapSource, Reader

from .results_store import scenario_dir

_BUILDINGS_LAYER_NAME = "buildings"
_BUILDING_ID_KEY = "building_id"


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
    if header["tile_compression"] == Compression.GZIP:
        raw = gzip.decompress(raw)

    results = _building_results(scenario_id)

    tile = mvt_pb2.tile()
    tile.ParseFromString(raw)
    for layer in tile.layers:
        if layer.name == _BUILDINGS_LAYER_NAME:
            _join_layer(layer, results)
    return tile.SerializeToString()


def _value_key(value: object) -> tuple[str, object]:
    """A hashable, type-distinguishing key for deduplicating a layer's
    `values` table -- MVT's `Value` message is a set of typed optional
    fields (not a real oneof in this compiled schema, so two `Value`s with
    the same number but different types are genuinely different wire
    values), and `df.to_dict(orient="index")` already hands back plain
    Python `bool`/`int`/`float` (never numpy scalar types, which the
    protobuf setters below would reject), so a type-tagged tuple is enough
    to dedupe correctly. `bool` is checked before `int` since `bool` is an
    `int` subclass in Python."""
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    return ("string", str(value))


def _pb_value_key(value_pb) -> tuple[str, object] | None:
    """The same `_value_key` shape, read back off an existing `Value`
    message already in a layer's `values` table -- lets new values
    dedupe against ones the base tile already had, not just ones this
    join itself adds."""
    if value_pb.HasField("bool_value"):
        return ("bool", value_pb.bool_value)
    if value_pb.HasField("int_value"):
        return ("int", value_pb.int_value)
    if value_pb.HasField("float_value"):
        return ("float", value_pb.float_value)
    if value_pb.HasField("string_value"):
        return ("string", value_pb.string_value)
    return None  # double/uint/sint -- never produced here, not worth dedup-matching


def _join_layer(layer, results: dict[str, dict]) -> None:
    """Mutates `layer` in place: for each feature whose `building_id` tag
    has a matching scenario result, appends that result's fields as new
    tag pairs. Geometry is never read or touched."""
    key_index = {key: i for i, key in enumerate(layer.keys)}
    building_id_key_idx = key_index.get(_BUILDING_ID_KEY)
    if building_id_key_idx is None:
        return  # every buildings-layer feature carries this tag; defensive only

    value_index = {
        vk: i for i, v in enumerate(layer.values) if (vk := _pb_value_key(v)) is not None
    }

    def get_or_add_key(key: str) -> int:
        idx = key_index.get(key)
        if idx is not None:
            return idx
        layer.keys.append(key)
        idx = len(layer.keys) - 1
        key_index[key] = idx
        return idx

    def get_or_add_value(value: object) -> int:
        vk = _value_key(value)
        idx = value_index.get(vk)
        if idx is not None:
            return idx
        value_pb = mvt_pb2.tile.value()
        if vk[0] == "bool":
            value_pb.bool_value = value
        elif vk[0] == "int":
            value_pb.int_value = value
        elif vk[0] == "float":
            value_pb.float_value = value
        else:
            value_pb.string_value = vk[1]
        layer.values.append(value_pb)
        idx = len(layer.values) - 1
        value_index[vk] = idx
        return idx

    for feature in layer.features:
        tags = feature.tags
        building_id = None
        for i in range(0, len(tags), 2):
            if tags[i] == building_id_key_idx:
                building_id = layer.values[tags[i + 1]].string_value
                break
        if building_id is None:
            continue
        extra = results.get(building_id)
        if extra is None:
            continue
        new_tags = []
        for key, value in extra.items():
            new_tags.append(get_or_add_key(key))
            new_tags.append(get_or_add_value(value))
        feature.tags.extend(new_tags)
