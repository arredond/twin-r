import pytest
from scenario.probability_level import PROBABILITY_LEVELS, resolve_probability_level


def test_high_is_median_ground_motion_modal_damage():
    params = resolve_probability_level("high")
    assert params.sigma_multiplier == 0.0
    assert params.damage_percentile is None


def test_low_is_plus_one_sigma_ground_motion_modal_damage():
    params = resolve_probability_level("low")
    assert params.sigma_multiplier == 1.0
    assert params.damage_percentile is None


def test_very_low_is_plus_one_sigma_ground_motion_85th_percentile_damage():
    params = resolve_probability_level("very_low")
    assert params.sigma_multiplier == 1.0
    assert params.damage_percentile == 0.85


def test_unknown_level_raises_value_error():
    with pytest.raises(ValueError, match="unknown probability_level"):
        resolve_probability_level("medium")


def test_probability_levels_tuple_matches_resolvable_values():
    for level in PROBABILITY_LEVELS:
        resolve_probability_level(level)  # must not raise
