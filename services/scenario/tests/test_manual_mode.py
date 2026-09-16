"""Tests for manual mode's progressive geometry complexity (ADR-0008)."""

import numpy as np
import pytest
from openquake.hazardlib.geo.mesh import Mesh
from scenario.rupture import STYLE_OF_FAULTING_RAKE, from_manual_input
from scenario.surface import (
    _length_from_magnitude_km,
    _width_from_magnitude_km,
    build_manual_surface,
)


def test_length_and_width_increase_with_magnitude():
    assert _length_from_magnitude_km(5.0) < _length_from_magnitude_km(7.5)
    assert _width_from_magnitude_km(5.0) < _width_from_magnitude_km(7.5)


def test_build_manual_surface_center_point_is_near_zero_rjb():
    surface = build_manual_surface(lat=40.0, lon=-3.7, mag=6.5, strike=45.0, dip=60.0, ztor_km=3.0)
    rjb = surface.get_joyner_boore_distance(Mesh(np.array([-3.7]), np.array([40.0])))[0]
    assert rjb < 5.0  # within mesh-spacing tolerance of the rupture's own center


def test_build_manual_surface_rejects_invalid_dip():
    with pytest.raises(ValueError):
        build_manual_surface(lat=40.0, lon=-3.7, mag=6.5, strike=45.0, dip=0.0, ztor_km=3.0)


# --- from_manual_input: the three progressive tiers ---


def test_tier1_magnitude_only_is_a_point_source_strike_slip():
    rupture = from_manual_input(lat=40.0, lon=-3.7, mag=6.0)
    assert rupture.surface is None
    assert rupture.rake == STYLE_OF_FAULTING_RAKE["strike-slip"]


def test_tier2_style_of_faulting_is_still_a_point_source():
    rupture = from_manual_input(lat=40.0, lon=-3.7, mag=6.0, rake=STYLE_OF_FAULTING_RAKE["reverse"])
    assert rupture.surface is None
    assert rupture.rake == 90.0


def test_tier3_full_geometry_builds_a_finite_surface():
    rupture = from_manual_input(
        lat=40.0, lon=-3.7, mag=6.5, rake=90.0, strike=45.0, dip=60.0, ztor_km=3.0
    )
    assert rupture.surface is not None


def test_tier3_partial_geometry_falls_back_to_point_source():
    # Only strike given, dip/ztor_km missing -- must not half-build a surface.
    rupture = from_manual_input(lat=40.0, lon=-3.7, mag=6.5, strike=45.0)
    assert rupture.surface is None


def test_tier3_invalid_geometry_falls_back_to_point_source_not_an_error():
    rupture = from_manual_input(lat=40.0, lon=-3.7, mag=6.5, strike=45.0, dip=0.0, ztor_km=3.0)
    assert rupture.surface is None
    assert rupture.lat == 40.0  # fallback location still correct
