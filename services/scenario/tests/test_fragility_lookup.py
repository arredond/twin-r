import pandas as pd
import pytest
from scenario.fragility_lookup import FragilityTable


@pytest.fixture
def synthetic_df() -> pd.DataFrame:
    rows = []
    for taxonomy, height, im_values in [
        ("MR_LWAL-DUL", 1, [0.1, 0.5, 1.0]),
        ("MR_LWAL-DUL", 3, [0.1, 0.5, 1.0]),
        ("CR_LDUAL-DUL", 2, [0.1, 0.5, 1.0]),
    ]:
        for im in im_values:
            for state, base in [
                ("Slight", 0.3),
                ("Moderate", 0.1),
                ("Extensive", 0.02),
                ("Complete", 0.001),
            ]:
                rows.append(
                    {
                        "taxonomy": taxonomy,
                        "height_class": height,
                        "im_value": im,
                        "damage_state": state,
                        "prob_exceedance": min(base * im * 2, 1.0),
                    }
                )
    return pd.DataFrame(rows)


def test_exact_match_lookup(synthetic_df):
    table = FragilityTable(synthetic_df)
    curve = table.get("MR_LWAL-DUL", 1)
    assert set(curve.im_values.keys()) == {"Slight", "Moderate", "Extensive", "Complete"}


def test_interpolates_between_im_values(synthetic_df):
    table = FragilityTable(synthetic_df)
    curve = table.get("MR_LWAL-DUL", 1)
    exceedance = curve.exceedance_at(0.3)  # between the 0.1 and 0.5 rows
    assert 0.0 < exceedance["Slight"] < 1.0


def test_missing_height_falls_back_to_nearest_available(synthetic_df):
    table = FragilityTable(synthetic_df)
    # MR_LWAL-DUL has heights 1 and 3 but not 2 -- nearest should pick one
    # of them (tie-break aside, both are distance 1) rather than raise.
    curve = table.get("MR_LWAL-DUL", 2)
    assert curve is not None


def test_missing_height_picks_the_closer_of_two_options(synthetic_df):
    table = FragilityTable(synthetic_df)
    curve_at_5 = table.get("MR_LWAL-DUL", 5)  # closer to height 3 than height 1
    curve_at_3 = table.get("MR_LWAL-DUL", 3)
    assert curve_at_5 is curve_at_3


def test_unknown_taxonomy_raises_keyerror(synthetic_df):
    table = FragilityTable(synthetic_df)
    with pytest.raises(KeyError):
        table.get("NOT_A_REAL_TAXONOMY", 1)
