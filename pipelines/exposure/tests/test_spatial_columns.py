import geopandas as gpd
from exposure.parse import add_spatial_index_columns
from shapely.geometry import box


def test_adds_centroid_and_bbox_columns():
    # A 2x2 square centered at (10, 20) in lon/lat terms.
    geom = box(9, 19, 11, 21)
    gdf = gpd.GeoDataFrame({"building_id": ["b1"]}, geometry=[geom], crs="EPSG:4326")

    result = add_spatial_index_columns(gdf)

    assert result["centroid_lon"].iloc[0] == 10.0
    assert result["centroid_lat"].iloc[0] == 20.0
    assert result["bbox_xmin"].iloc[0] == 9.0
    assert result["bbox_ymin"].iloc[0] == 19.0
    assert result["bbox_xmax"].iloc[0] == 11.0
    assert result["bbox_ymax"].iloc[0] == 21.0


def test_does_not_mutate_input():
    geom = box(0, 0, 2, 2)
    gdf = gpd.GeoDataFrame({"building_id": ["b1"]}, geometry=[geom], crs="EPSG:4326")
    add_spatial_index_columns(gdf)
    assert "centroid_lon" not in gdf.columns
