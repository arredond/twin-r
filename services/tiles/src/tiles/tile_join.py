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

from mapbox_vector_tile.Mapbox import vector_tile_pb2 as mvt_pb2

_BUILDINGS_LAYER_NAME = "buildings"
_BUILDING_ID_KEY = "building_id"


def join_tile_bytes(raw_tile_bytes: bytes, results: dict[str, dict]) -> bytes:
    """`raw_tile_bytes` is one MVT tile, already gunzipped if the source
    archive's tile_compression called for it. `results` is building_id ->
    a plain dict of extra tile properties (e.g. damage_state_code/prob_*)
    -- typically `df.to_dict(orient="index")` off a scenario's thin
    buildings.json, so values are already plain Python bool/int/float,
    never numpy scalar types (the protobuf setters below would reject
    those)."""
    tile = mvt_pb2.tile()
    tile.ParseFromString(raw_tile_bytes)
    for layer in tile.layers:
        if layer.name == _BUILDINGS_LAYER_NAME:
            _join_layer(layer, results)
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
