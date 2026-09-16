from scenario.damage import (
    damage_state_probabilities,
    modal_damage_state,
    percentile_damage_state,
    select_damage_state,
)


def test_no_exceedance_is_certainly_none():
    probs = damage_state_probabilities(
        {"Slight": 0.0, "Moderate": 0.0, "Extensive": 0.0, "Complete": 0.0}
    )
    assert probs["None"] == 1.0
    assert modal_damage_state(probs) == "None"


def test_certain_complete_exceedance_is_certainly_complete():
    probs = damage_state_probabilities(
        {"Slight": 1.0, "Moderate": 1.0, "Extensive": 1.0, "Complete": 1.0}
    )
    assert probs["Complete"] == 1.0
    assert modal_damage_state(probs) == "Complete"


def test_probabilities_sum_to_one():
    probs = damage_state_probabilities(
        {"Slight": 0.9, "Moderate": 0.6, "Extensive": 0.3, "Complete": 0.05}
    )
    assert sum(probs.values()) == 1.0
    for p in probs.values():
        assert p >= 0.0


def test_noisy_exceedance_is_clipped_not_negative():
    # Moderate slightly exceeding Slight shouldn't happen with real curves,
    # but interpolation noise near a boundary could produce it.
    probs = damage_state_probabilities(
        {"Slight": 0.5, "Moderate": 0.51, "Extensive": 0.1, "Complete": 0.0}
    )
    assert all(p >= 0.0 for p in probs.values())
    assert abs(sum(probs.values()) - 1.0) < 1e-9


def test_percentile_damage_state_picks_smallest_state_reaching_cumulative_target():
    # None=0.5, Slight=0.3, Moderate=0.15, Extensive=0.04, Complete=0.01 ->
    # cumulative: None 0.5, Slight 0.8, Moderate 0.95, Extensive 0.99, Complete 1.0
    probs = damage_state_probabilities(
        {"Slight": 0.5, "Moderate": 0.2, "Extensive": 0.05, "Complete": 0.01}
    )
    assert percentile_damage_state(probs, 0.5) == "None"  # cumulative already >= 0.5 at None
    assert percentile_damage_state(probs, 0.85) == "Moderate"  # first state reaching 0.85
    assert percentile_damage_state(probs, 0.999) == "Complete"


def test_percentile_damage_state_can_differ_from_modal():
    # Modal state is "None" (P=0.5, the single largest bucket), but the 85th
    # percentile should still read further into the tail -- the whole point
    # of MERISUR's "very low probability / very high impact" tier
    # (docs/merisur.md §4.7): report a pessimistic read of the same
    # distribution a modal-state reading treats optimistically.
    probs = damage_state_probabilities(
        {"Slight": 0.5, "Moderate": 0.2, "Extensive": 0.05, "Complete": 0.01}
    )
    assert modal_damage_state(probs) == "None"
    assert percentile_damage_state(probs, 0.85) != "None"


def test_select_damage_state_none_percentile_is_modal():
    probs = damage_state_probabilities(
        {"Slight": 0.5, "Moderate": 0.2, "Extensive": 0.05, "Complete": 0.01}
    )
    assert select_damage_state(probs, None) == modal_damage_state(probs)


def test_select_damage_state_with_percentile_matches_percentile_fn():
    probs = damage_state_probabilities(
        {"Slight": 0.5, "Moderate": 0.2, "Extensive": 0.05, "Complete": 0.01}
    )
    assert select_damage_state(probs, 0.85) == percentile_damage_state(probs, 0.85)
