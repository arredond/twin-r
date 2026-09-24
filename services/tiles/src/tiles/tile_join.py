"""Pure MVT tile-join logic: given a base tile's raw (uncompressed) bytes
and a scenario's building_id -> result dict, returns a new tile with each
matching `buildings` feature's properties extended by its result.

Deliberately I/O-free (no pmtiles reading, no results loading) -- shared
between services/scenario's local dev server (local mmap'd PMTiles file +
local disk results/ directory, see services/scenario/tile_join.py) and
this package's own Lambda handler (S3 range-reads + S3 results, see
s3_pmtiles.py/results_store.py), so the tricky part -- the protobuf
patching itself -- can't drift between the two runtimes.

Works directly on the compiled MVT protobuf message
(`vector_tile_pb2`, the same schema `mapbox_vector_tile.encode()`/
`decode()` sit on top of) rather than going through those functions.
`encode()` always reconstructs every feature's geometry via Shapely
(construct a Polygon, `orient()` it, validate it) even when the geometry
is completely unchanged, which is real cost at scale: ~0.9s of a
22,231-feature tile's ~1.1s total was Shapely reconstruction alone, for
work whose result we already know (this geometry came from an
already-valid, correctly-wound tippecanoe-produced tile -- see
pipelines/exposure/tile.py -- so there's nothing to fix). Since a join
only ever adds property tags to a feature and never touches its geometry
bytes, patching the protobuf's `tags`/`keys`/`values` fields directly and
leaving `geometry` untouched skips that reconstruction (and the matching
decode()-side GeoJSON conversion) for every feature, not just the ones a
scenario actually joins onto. Measured: ~0.03s versus ~0.9s of `encode()`
alone for that same 22,231-feature tile.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from mapbox_vector_tile.Mapbox import vector_tile_pb2 as mvt_pb2

# The generated protobuf message class. protoc-generated modules build their
# classes at import time, so static analysis can't see this attribute.
Tile = mvt_pb2.tile  # pyrefly: ignore

_BUILDINGS_LAYER_NAME = "buildings"
_DEBRIS_LAYER_NAME = "debris"


class ResultsLookup(Protocol):
    """What a join needs from a scenario's results: one building's extra
    tile properties by id, or None if it isn't listed. Satisfied by
    `scenario_results.ScenarioResults` and by a plain `dict[str, dict]`."""

    def get(self, building_id: str, /) -> Mapping[str, Any] | None: ...


_BUILDING_ID_KEY = "building_id"
_RING_KEY = "ring"
_DAMAGE_STATE_CODE_KEY = "damage_state_code"


def join_tile_bytes(raw_tile_bytes: bytes, results: ResultsLookup) -> bytes:
    """`raw_tile_bytes` is one MVT tile, already gunzipped if the source
    archive's tile_compression called for it. `results` maps building_id ->
    a plain dict of extra tile properties (damage_state_code/prob_*) --
    in practice a `scenario_results.ScenarioResults`, decoded from JSON, so
    values are plain Python int/float, never numpy scalar types (the
    protobuf setters below would reject those)."""
    tile = Tile()
    tile.ParseFromString(raw_tile_bytes)
    for layer in tile.layers:
        if layer.name == _BUILDINGS_LAYER_NAME:
            _join_layer(layer, results)
    return tile.SerializeToString()


def join_debris_tile_bytes(raw_tile_bytes: bytes, results: ResultsLookup) -> bytes:
    """The debris.pmtiles counterpart of `join_tile_bytes` (ADR-0010 rings,
    four features per building sharing one `building_id`, `ring` 1-4 =
    the damage state that first produces it). Same `results` shape.

    Keeps only each building's *one* ring matching its predicted
    `damage_state_code` and drops every other ring feature, including all
    four rings of any building the scenario didn't list. That is exactly
    the set the map renders (one cumulative envelope per damaged
    building), so the frontend needs no per-building data to pick
    rings, and a joined debris tile is a fraction of the base tile's size
    rather than a superset of it. The kept ring gets `damage_state_code`
    as a tag; nothing else from `results` is copied (debris has no use for
    the probabilities)."""
    tile = Tile()
    tile.ParseFromString(raw_tile_bytes)
    for layer in tile.layers:
        if layer.name == _DEBRIS_LAYER_NAME:
            _join_debris_layer(layer, results)
    return tile.SerializeToString()


def _value_key(value: object) -> tuple[str, object]:
    """A hashable, type-distinguishing key for deduplicating a layer's
    `values` table -- MVT's `Value` message is a set of typed optional
    fields (not a real oneof in this compiled schema, so two `Value`s with
    the same number but different types are genuinely different wire
    values). `bool` is checked before `int` since `bool` is an `int`
    subclass in Python."""
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


def _join_layer(layer, results: ResultsLookup) -> None:
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
        value_pb = Tile.value()
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


def _join_debris_layer(layer, results: ResultsLookup) -> None:
    """Mutates `layer` in place -- see `join_debris_tile_bytes`."""
    key_index = {key: i for i, key in enumerate(layer.keys)}
    building_id_key_idx = key_index.get(_BUILDING_ID_KEY)
    ring_key_idx = key_index.get(_RING_KEY)
    if building_id_key_idx is None or ring_key_idx is None:
        del layer.features[:]  # not a debris layer we understand; show nothing
        return

    code_key_idx = key_index.get(_DAMAGE_STATE_CODE_KEY)
    if code_key_idx is None:
        layer.keys.append(_DAMAGE_STATE_CODE_KEY)
        code_key_idx = len(layer.keys) - 1

    # damage_state_code -> index into layer.values, reusing an existing
    # int Value (the ring numbers 1-4 are already in the table) when there
    # is one.
    value_index = {
        vk[1]: i
        for i, v in enumerate(layer.values)
        if (vk := _pb_value_key(v)) is not None and vk[0] == "int"
    }

    def int_value_idx(n: int) -> int:
        idx = value_index.get(n)
        if idx is None:
            value_pb = Tile.value()
            value_pb.int_value = n
            layer.values.append(value_pb)
            idx = len(layer.values) - 1
            value_index[n] = idx
        return idx

    kept = []
    for feature in layer.features:
        tags = feature.tags
        building_id = None
        ring = None
        for i in range(0, len(tags), 2):
            if tags[i] == building_id_key_idx:
                building_id = layer.values[tags[i + 1]].string_value
            elif tags[i] == ring_key_idx:
                ring = _pb_number(layer.values[tags[i + 1]])
        if building_id is None or ring is None:
            continue
        result = results.get(building_id)
        if result is None:
            continue
        code = result.get(_DAMAGE_STATE_CODE_KEY)
        if code is None or ring != code:
            continue
        feature.tags.extend([code_key_idx, int_value_idx(int(code))])
        kept.append(feature)

    # Rebuilding the repeated field (copies, not references -- protobuf
    # repeated message fields own their elements).
    kept_copies = [Tile.feature() for _ in kept]
    for dst, src in zip(kept_copies, kept, strict=True):
        dst.CopyFrom(src)
    del layer.features[:]
    layer.features.extend(kept_copies)


def _pb_number(value_pb) -> int | float | None:
    """A numeric `Value`'s number, whichever of MVT's numeric fields holds
    it (tippecanoe writes small ints as int_value, but sint/uint/double are
    all legal encodings of the same number)."""
    for field in ("int_value", "sint_value", "uint_value", "double_value", "float_value"):
        if value_pb.HasField(field):
            return getattr(value_pb, field)
    return None
