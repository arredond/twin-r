import math

from exposure.taxonomy import assign_taxonomy


def test_modern_low_rise_is_concrete():
    cls, height = assign_taxonomy(1995, 3)
    assert cls == "CR_LDUAL-DUL"
    assert height == 3


def test_masonry_era_building_is_generic_masonry():
    cls, height = assign_taxonomy(1955, 2)
    assert cls == "MR_LWAL-DUL"
    assert height == 2


def test_pre_1940_building_is_vernacular_masonry():
    cls, height = assign_taxonomy(1930, 2)
    assert cls == "MUR-STRUB_LWAL-DNO"
    assert height == 2


def test_concrete_threshold_year_counts_as_concrete():
    cls, _ = assign_taxonomy(1970, 1)
    assert cls == "CR_LDUAL-DUL"


def test_vernacular_masonry_threshold_year_counts_as_generic_masonry():
    cls, _ = assign_taxonomy(1940, 1)
    assert cls == "MR_LWAL-DUL"


def test_unknown_year_defaults_to_vernacular_masonry():
    # Conservative (more vulnerable) default when Catastro gives us
    # nothing to go on -- see assign_taxonomy's own docstring.
    cls, _ = assign_taxonomy(None, 2)
    assert cls == "MUR-STRUB_LWAL-DNO"
    cls, _ = assign_taxonomy(math.nan, 2)
    assert cls == "MUR-STRUB_LWAL-DNO"


def test_unknown_floors_defaults_to_height_class_one():
    _, height = assign_taxonomy(2000, None)
    assert height == 1
    _, height = assign_taxonomy(2000, math.nan)
    assert height == 1


def test_height_class_is_clamped_to_max():
    _, height = assign_taxonomy(2000, 40)
    assert height == 12
