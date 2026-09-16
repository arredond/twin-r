import pandas as pd
import pytest
from scenario.fragility_lookup import FragilityTable


@pytest.fixture
def synthetic_df() -> pd.DataFrame:
    rows = []
    # im_type varies by height within MR_LWAL-DUL on purpose -- matches the
    # real vendored data (docs/validation-lorca-2011.md §10.2: H1 is
    # PGA-indexed, taller heights are SA-indexed) and exercises
    # FragilityTable storing/exposing im_type per curve, not just per table.
    for taxonomy, height, im_type, im_values in [
        ("MR_LWAL-DUL", 1, "PGA [g]", [0.1, 0.5, 1.0]),
        ("MR_LWAL-DUL", 3, "SA(0.3s) [g]", [0.1, 0.5, 1.0]),
        ("CR_LDUAL-DUL", 2, "SA(0.3s) [g]", [0.1, 0.5, 1.0]),
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
                        "im_type": im_type,
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


def test_curve_exposes_its_own_im_type(synthetic_df):
    table = FragilityTable(synthetic_df)
    assert table.get("MR_LWAL-DUL", 1).im_type == "PGA [g]"
    assert table.get("MR_LWAL-DUL", 3).im_type == "SA(0.3s) [g]"


def test_used_im_types_covers_every_distinct_im_type_vendored(synthetic_df):
    table = FragilityTable(synthetic_df)
    assert table.used_im_types() == {"PGA [g]", "SA(0.3s) [g]"}
