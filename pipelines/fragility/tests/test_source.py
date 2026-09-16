from fragility.source import DAMAGE_STATES, TAXONOMY_CLASSES


def test_taxonomy_classes_defined():
    assert TAXONOMY_CLASSES == ["CR_LDUAL-DUL", "MR_LWAL-DUL"]


def test_damage_states_defined_in_severity_order():
    assert DAMAGE_STATES == [
        "Slight_damage",
        "Moderate_damage",
        "Extensive_damage",
        "Complete_damage",
    ]
