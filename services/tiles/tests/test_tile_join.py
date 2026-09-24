import mapbox_vector_tile as mvt
import pytest
from tiles.tile_join import join_debris_tile_bytes, join_tile_bytes


def _base_tile() -> bytes:
    # A minimal two-feature "buildings" layer, same shape tippecanoe
    # produces (building_id + a couple of base attributes) -- built via
    # mapbox_vector_tile.encode() here since correctness, not speed, is
    # what this test cares about (the speed-sensitive path is join_tile_bytes
    # itself, which never calls encode()/decode()).
    features = [
        {
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 10], [10, 10], [0, 0]]]},
            "properties": {"building_id": "A1", "floors": 2},
        },
        {
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 5], [5, 5], [0, 0]]]},
            "properties": {"building_id": "A2", "floors": 3},
        },
    ]
    return mvt.encode([{"name": "buildings", "features": features}])


def test_join_adds_properties_only_to_matching_building():
    raw = _base_tile()
    results = {"A1": {"damage_state_code": 2, "prob_none": 0.1}}

    joined = join_tile_bytes(raw, results)
    decoded = mvt.decode(joined)
    by_id = {f["properties"]["building_id"]: f for f in decoded["buildings"]["features"]}

    assert by_id["A1"]["properties"]["damage_state_code"] == 2
    # MVT's float_value is 32-bit (protobuf `float`, not `double`) -- a
    # roundtrip through it loses precision past ~7 significant digits,
    # same as engine.py's own thin-payload rounding elsewhere in the app.
    assert by_id["A1"]["properties"]["prob_none"] == pytest.approx(0.1)
    assert by_id["A1"]["properties"]["floors"] == 2  # base property untouched

    assert "damage_state_code" not in by_id["A2"]["properties"]
    assert by_id["A2"]["properties"]["floors"] == 3


def test_join_preserves_geometry_and_feature_count():
    raw = _base_tile()
    results = {"A1": {"damage_state_code": 1}}

    joined = join_tile_bytes(raw, results)

    original = mvt.decode(raw)["buildings"]["features"]
    after = mvt.decode(joined)["buildings"]["features"]
    assert len(original) == len(after)

    original_by_id = {f["properties"]["building_id"]: f["geometry"] for f in original}
    after_by_id = {f["properties"]["building_id"]: f["geometry"] for f in after}
    assert original_by_id == after_by_id


def test_join_with_no_matches_is_a_no_op_on_properties():
    raw = _base_tile()
    joined = join_tile_bytes(raw, {"nonexistent-id": {"damage_state_code": 4}})

    decoded = mvt.decode(joined)["buildings"]["features"]
    for feature in decoded:
        assert "damage_state_code" not in feature["properties"]


def test_join_dedupes_repeated_values_across_features():
    # Both A1 and A2 get the same damage_state_code -- the join should
    # reuse one `values` table entry for it, not add a duplicate per
    # feature (see tile_join.py's _value_key/get_or_add_value).
    raw = _base_tile()
    results = {"A1": {"damage_state_code": 3}, "A2": {"damage_state_code": 3}}

    joined = join_tile_bytes(raw, results)

    from mapbox_vector_tile.Mapbox import vector_tile_pb2 as mvt_pb2

    tile = mvt_pb2.tile()
    tile.ParseFromString(joined)
    layer = next(layer for layer in tile.layers if layer.name == "buildings")
    matching_values = [v for v in layer.values if v.HasField("int_value") and v.int_value == 3]
    assert len(matching_values) == 1


def _base_debris_tile() -> bytes:
    # Two buildings x four cumulative rings each, same shape
    # pipelines/exposure/debris.py produces.
    features = [
        {
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [0, 2 * ring], [2 * ring, 2 * ring], [0, 0]]],
            },
            "properties": {"building_id": building_id, "ring": ring},
        }
        for building_id in ["A1", "A2"]
        for ring in [1, 2, 3, 4]
    ]
    return mvt.encode([{"name": "debris", "features": features}])


def test_debris_join_keeps_only_the_ring_matching_each_buildings_damage_state():
    results = {"A1": {"damage_state_code": 3, "prob_none": 0.1}}

    decoded = mvt.decode(join_debris_tile_bytes(_base_debris_tile(), results))
    features = decoded["debris"]["features"]

    # A2 isn't in the results at all -> none of its rings survive.
    assert [(f["properties"]["building_id"], f["properties"]["ring"]) for f in features] == [
        ("A1", 3)
    ]
    assert features[0]["properties"]["damage_state_code"] == 3
    # Probabilities aren't copied onto debris.
    assert "prob_none" not in features[0]["properties"]


def test_debris_join_drops_rings_for_undamaged_buildings():
    # damage_state_code 0 ("None") has no ring -- rings are 1-4.
    results = {"A1": {"damage_state_code": 0}, "A2": {"damage_state_code": 1}}

    features = mvt.decode(join_debris_tile_bytes(_base_debris_tile(), results))["debris"][
        "features"
    ]

    assert [(f["properties"]["building_id"], f["properties"]["ring"]) for f in features] == [
        ("A2", 1)
    ]


def test_debris_join_preserves_kept_geometry():
    raw = _base_debris_tile()
    results = {"A2": {"damage_state_code": 4}}

    before = {
        (f["properties"]["building_id"], f["properties"]["ring"]): f["geometry"]
        for f in mvt.decode(raw)["debris"]["features"]
    }
    (after,) = mvt.decode(join_debris_tile_bytes(raw, results))["debris"]["features"]

    assert after["geometry"] == before[("A2", 4)]


def test_buildings_join_leaves_debris_layers_alone_and_vice_versa():
    assert mvt.decode(join_tile_bytes(_base_debris_tile(), {"A1": {"damage_state_code": 1}})) == (
        mvt.decode(_base_debris_tile())
    )
    assert mvt.decode(join_debris_tile_bytes(_base_tile(), {"A1": {"damage_state_code": 1}})) == (
        mvt.decode(_base_tile())
    )
