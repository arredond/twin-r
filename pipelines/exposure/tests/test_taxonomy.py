import math

from exposure.taxonomy import assign_taxonomy


def test_modern_low_rise_is_concrete():
    cls, height = assign_taxonomy(1995, 3)
    assert cls == "CR_LDUAL-DUL"
    assert height == 3


def test_old_building_is_masonry():
    cls, height = assign_taxonomy(1930, 2)
    assert cls == "MR_LWAL-DUL"
    assert height == 2


def test_threshold_year_counts_as_concrete():
    cls, _ = assign_taxonomy(1970, 1)
    assert cls == "CR_LDUAL-DUL"


def test_unknown_year_defaults_to_masonry():
    cls, _ = assign_taxonomy(None, 2)
    assert cls == "MR_LWAL-DUL"
    cls, _ = assign_taxonomy(math.nan, 2)
    assert cls == "MR_LWAL-DUL"


def test_unknown_floors_defaults_to_height_class_one():
    _, height = assign_taxonomy(2000, None)
    assert height == 1
    _, height = assign_taxonomy(2000, math.nan)
    assert height == 1


def test_height_class_is_clamped_to_max():
    _, height = assign_taxonomy(2000, 40)
    assert height == 12
