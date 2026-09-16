import geopandas as gpd
from exposure.debris import RING_DISTANCES_M, compute_debris_envelopes
from shapely.geometry import box


def _utm_gdf(building_ids, boxes):
    # A small projected CRS (UTM 30N, covers Spain) so buffer distances are
    # true metres -- compute_debris_envelopes reprojects internally anyway,
    # but starting in a metric CRS keeps the synthetic geometry's units
    # obviously meters, not degrees.
    return gpd.GeoDataFrame({"building_id": building_ids}, geometry=boxes, crs="EPSG:25830")


def test_isolated_building_gets_rings_on_all_sides():
    gdf = _utm_gdf(["b1"], [box(0, 0, 10, 10)])
    result = compute_debris_envelopes(gdf)

    assert set(result["building_id"]) == {"b1"}
    assert set(result["ring"]) == {1, 2, 3, 4}
    # Ring 1 (1m out) should roughly match a 1m-wider box minus the
    # building itself -- area within a small tolerance of the analytic
    # value (12*12 - 10*10 = 44, ignoring the buffer's rounded corners).
    ring1 = result[result["ring"] == 1].geometry.iloc[0]
    assert 40 < ring1.area < 48


def test_rings_are_nested_and_non_overlapping():
    gdf = _utm_gdf(["b1"], [box(0, 0, 10, 10)])
    result = compute_debris_envelopes(gdf).set_index("ring")

    # Each successive ring's area should be roughly constant (same
    # perimeter, same 1m band width) once you're past the corner rounding,
    # not cumulative -- confirms bands aren't double-counted.
    areas = [result.loc[r].geometry.area for r in range(1, 5)]
    assert all(a > 0 for a in areas)
    assert max(areas) / min(areas) < 1.5


def test_party_wall_excludes_shared_edge():
    # Two 10x10 buildings sharing the edge at x=10.
    gdf = _utm_gdf(
        ["b1", "b2"],
        [box(0, 0, 10, 10), box(10, 0, 20, 10)],
    )
    result = compute_debris_envelopes(gdf)

    b1_rings = result[result["building_id"] == "b1"]
    b2_rings = result[result["building_id"] == "b2"]

    # No ring for either building should intersect the other building's
    # footprint.
    b1_footprint = box(0, 0, 10, 10)
    b2_footprint = box(10, 0, 20, 10)
    for geom in b1_rings.geometry:
        assert geom.intersection(b2_footprint).area < 1e-6
    for geom in b2_rings.geometry:
        assert geom.intersection(b1_footprint).area < 1e-6


def test_fully_enclosed_building_gets_no_rings():
    # b1 surrounded on all four sides by neighbors within wall tolerance.
    gdf = _utm_gdf(
        ["b1", "north", "south", "east", "west"],
        [
            box(10, 10, 20, 20),
            box(10, 20, 20, 30),
            box(10, 0, 20, 10),
            box(20, 10, 30, 20),
            box(0, 10, 10, 20),
        ],
    )
    result = compute_debris_envelopes(gdf)
    assert result[result["building_id"] == "b1"].empty


def test_empty_input_returns_empty_geodataframe():
    gdf = gpd.GeoDataFrame({"building_id": []}, geometry=[], crs="EPSG:25830")
    result = compute_debris_envelopes(gdf)
    assert result.empty
    assert list(result.columns).__contains__("building_id")


def test_output_reprojected_to_input_crs():
    gdf = gpd.GeoDataFrame(
        {"building_id": ["b1"]}, geometry=[box(0, 0, 10, 10)], crs="EPSG:25830"
    ).to_crs("EPSG:4326")
    result = compute_debris_envelopes(gdf)
    assert result.crs is not None
    assert result.crs.to_epsg() == 4326


def test_ring_count_matches_damage_states():
    assert len(RING_DISTANCES_M) == 4
