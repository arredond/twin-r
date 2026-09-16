import json
import math

import pytest
from scenario.rupture import Rupture, from_fault

SIMPLE_TRACE_GEOJSON = json.dumps(
    {"type": "LineString", "coordinates": [[-1.75, 37.60], [-1.65, 37.75]]}
)


def test_rupture_rejects_implausible_magnitude():
    with pytest.raises(ValueError):
        Rupture(lat=37.0, lon=-1.0, mag=12.0, rake=0.0)


def test_from_fault_builds_point_source_at_given_point():
    rupture = from_fault("ES412", "Concud", point_lat=40.5, point_lon=-1.3, mmax=6.4)
    assert rupture.mag == 6.4
    assert rupture.lat == 40.5
    assert rupture.source == "fault:ES412:Concud"


def test_from_fault_defaults_missing_rake_to_strike_slip():
    rupture = from_fault("ES412", "Concud", point_lat=40.5, point_lon=-1.3, mmax=6.4, rake=None)
    assert rupture.rake == 0.0
    rupture_nan = from_fault(
        "ES412", "Concud", point_lat=40.5, point_lon=-1.3, mmax=6.4, rake=math.nan
    )
    assert rupture_nan.rake == 0.0


def test_from_fault_uses_qafis_published_rake_when_present():
    # ES626 "Alhama de Murcia (1/4)": QAFI v4 publishes rake=20 (oblique
    # reverse) -- see docs/decisions/0004-qafi-shapefile-source.md.
    rupture = from_fault(
        "ES626", "Alhama de Murcia (1/4)", point_lat=37.67, point_lon=-1.70, mmax=6.7, rake=20.0
    )
    assert rupture.rake == 20.0


def test_from_fault_without_geometry_has_no_surface():
    # Matches every existing test above: omitting geometry_geojson/dip/
    # depths (as e.g. a fault missing that data would) falls back to the
    # point-source rupture, not an error.
    rupture = from_fault("ES412", "Concud", point_lat=40.5, point_lon=-1.3, mmax=6.4)
    assert rupture.surface is None


def test_from_fault_with_full_geometry_builds_a_surface():
    rupture = from_fault(
        "ES626",
        "Alhama de Murcia (1/4)",
        point_lat=37.67,
        point_lon=-1.70,
        mmax=6.7,
        rake=20.0,
        geometry_geojson=SIMPLE_TRACE_GEOJSON,
        dip=70.0,
        min_depth_km=0.0,
        max_depth_km=12.0,
    )
    assert rupture.surface is not None


def test_from_fault_falls_back_to_point_source_on_invalid_geometry():
    # dip=0 is geometrically invalid (surface.py/hazardlib rejects it) --
    # from_fault must not propagate that as an error, just skip the surface.
    rupture = from_fault(
        "ES626",
        "Alhama de Murcia (1/4)",
        point_lat=37.67,
        point_lon=-1.70,
        mmax=6.7,
        rake=20.0,
        geometry_geojson=SIMPLE_TRACE_GEOJSON,
        dip=0.0,
        min_depth_km=0.0,
        max_depth_km=12.0,
    )
    assert rupture.surface is None
    assert rupture.lat == 37.67  # point-source fallback location still set


def test_from_fault_partial_geometry_data_skips_surface():
    # Missing just one of the four required fields -- still a valid
    # point-source rupture, not an error.
    rupture = from_fault(
        "ES626",
        "Alhama de Murcia (1/4)",
        point_lat=37.67,
        point_lon=-1.70,
        mmax=6.7,
        rake=20.0,
        geometry_geojson=SIMPLE_TRACE_GEOJSON,
        dip=70.0,
        min_depth_km=None,
        max_depth_km=12.0,
    )
    assert rupture.surface is None
