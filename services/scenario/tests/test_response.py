import pandas as pd
from scenario.response import compute_municipality_stats


def test_compute_municipality_stats_groups_by_municipality_and_damage_state():
    result = pd.DataFrame(
        {
            "building_id": ["b1", "b2", "b3", "b4"],
            "municipality_code": ["30024", "30024", "28079", "28079"],
            "damage_state": ["Slight", "Moderate", "None", "None"],
        }
    )

    stats = compute_municipality_stats(result)
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


def test_compute_municipality_stats_with_empty_result_returns_empty_list():
    result = pd.DataFrame(columns=["building_id", "municipality_code", "damage_state"])

    assert compute_municipality_stats(result) == []
