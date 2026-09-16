from scenario.damage import damage_state_probabilities, modal_damage_state


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
