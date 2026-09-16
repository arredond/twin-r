import geopandas as gpd
import pandas as pd
from exposure.municipalities import attach_building_counts
from shapely.geometry import box


def _gdf(ine_codes, names):
    boxes = [box(i, 0, i + 1, 1) for i in range(len(ine_codes))]
    return gpd.GeoDataFrame({"ine_code": ine_codes, "name": names}, geometry=boxes, crs="EPSG:4326")


def test_attach_building_counts_matches_parts_by_ine_code(tmp_path):
    # <ine_code>.buildings.parquet naming matches region.py's own
    # per-municipality partitioning (`_part_paths`) -- this is the join key.
    pd.DataFrame({"building_id": ["b1", "b2", "b3"]}).to_parquet(
        tmp_path / "30024.buildings.parquet"
    )
    pd.DataFrame({"building_id": ["b4"]}).to_parquet(tmp_path / "28079.buildings.parquet")

    municipalities = _gdf(["30024", "28079", "04001"], ["Lorca", "Madrid", "Abla"])
    result = attach_building_counts(municipalities, tmp_path)

    counts = dict(zip(result["ine_code"], result["n_buildings"]))
    assert counts == {"30024": 3, "28079": 1, "04001": 0}


def test_attach_building_counts_with_no_parts_dir_gives_zero(tmp_path):
    municipalities = _gdf(["30024"], ["Lorca"])
    result = attach_building_counts(municipalities, tmp_path / "empty")

    assert result["n_buildings"].tolist() == [0]
