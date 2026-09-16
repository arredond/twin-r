import geopandas as gpd
import pandas as pd
from scenario.response import compute_municipality_stats
from shapely.geometry import box


def _synthetic_municipalities(path):
    # Two adjacent 1x1-degree boxes -- big enough that plausible synthetic
    # building coordinates land unambiguously inside one or the other.
    gdf = gpd.GeoDataFrame(
        {"ine_code": ["30024", "28079"], "name": ["Lorca", "Madrid"]},
        geometry=[box(-2, 37, -1, 38), box(-4, 40, -3, 41)],
        crs="EPSG:4326",
    )
    gdf.to_parquet(path)
    return path


def test_compute_municipality_stats_groups_by_municipality_and_damage_state(tmp_path):
    municipalities_path = _synthetic_municipalities(tmp_path / "municipalities.parquet")
    result = pd.DataFrame(
        {
            "building_id": ["b1", "b2", "b3", "b4"],
            "lon": [-1.5, -1.6, -3.5, -3.6],
            "lat": [37.5, 37.6, 40.5, 40.6],
            "damage_state": ["Slight", "Moderate", "None", "None"],
        }
    )

    stats = compute_municipality_stats(result, str(municipalities_path))
    by_code = {s["municipality_code"]: s for s in stats}

    assert by_code["30024"]["n_evaluated"] == 2
    assert by_code["30024"]["counts"] == {
        "None": 0,
        "Slight": 1,
        "Moderate": 1,
        "Extensive": 0,
        "Complete": 0,
    }
    assert by_code["28079"]["n_evaluated"] == 2
    assert by_code["28079"]["counts"]["None"] == 2


def test_compute_municipality_stats_with_empty_result_returns_empty_list(tmp_path):
    municipalities_path = _synthetic_municipalities(tmp_path / "municipalities.parquet")
    result = pd.DataFrame(columns=["building_id", "lon", "lat", "damage_state"])

    assert compute_municipality_stats(result, str(municipalities_path)) == []


def test_compute_municipality_stats_missing_file_returns_empty_list_not_error(tmp_path):
    result = pd.DataFrame(
        {"building_id": ["b1"], "lon": [-1.5], "lat": [37.5], "damage_state": ["Slight"]}
    )

    assert compute_municipality_stats(result, str(tmp_path / "does_not_exist.parquet")) == []
