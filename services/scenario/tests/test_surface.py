"""Tests for surface.py's finite rupture-plane construction.

Uses a real QAFI fault's own geometry (network-independent -- the geometry
is inlined as a fixture, not fetched) so these exercise hazardlib's actual
surface construction, not a toy geometry that might not trigger real
validation edge cases.
"""

import json

import numpy as np
import pytest
from openquake.hazardlib.geo.mesh import Mesh
from scenario.surface import _longest_linestring_coords, build_fault_surface

# A simple, well-behaved two-point trace -- enough to validate the happy path.
SIMPLE_TRACE_GEOJSON = json.dumps(
    {"type": "LineString", "coordinates": [[-1.75, 37.60], [-1.65, 37.75]]}
)

# Two disconnected pieces of very different lengths, shaped like QAFI's own
# multi-part traces (see surface.py's docstring on why we pick the longest).
MULTI_TRACE_GEOJSON = json.dumps(
    {
        "type": "MultiLineString",
        "coordinates": [
            [[-1.75, 37.60], [-1.65, 37.75]],  # long piece
            [[10.0, 50.0], [10.01, 50.01]],  # short, unrelated piece
        ],
    }
)


def test_build_fault_surface_from_simple_trace():
    surface = build_fault_surface(
        SIMPLE_TRACE_GEOJSON, dip=70.0, min_depth_km=0.0, max_depth_km=12.0
    )
    # A site right on the trace should have Rjb == 0.
    rjb = surface.get_joyner_boore_distance(Mesh(np.array([-1.75]), np.array([37.60])))
    assert rjb[0] == pytest.approx(0.0, abs=0.1)


def test_build_fault_surface_picks_longest_multilinestring_component():
    coords = _longest_linestring_coords(json.loads(MULTI_TRACE_GEOJSON))
    assert coords == [[-1.75, 37.60], [-1.65, 37.75]]


def test_rjb_from_surface_is_zero_near_trace_but_positive_further_away():
    surface = build_fault_surface(
        SIMPLE_TRACE_GEOJSON, dip=70.0, min_depth_km=0.0, max_depth_km=12.0
    )
    near = Mesh(np.array([-1.70]), np.array([37.675]))  # roughly midpoint of the trace
    far = Mesh(np.array([-1.70]), np.array([38.5]))  # ~80km north
    rjb_near = surface.get_joyner_boore_distance(near)[0]
    rjb_far = surface.get_joyner_boore_distance(far)[0]
    assert rjb_near < 1.0
    assert rjb_far > 50.0


def test_invalid_dip_raises_valueerror():
    with pytest.raises(ValueError):
        build_fault_surface(SIMPLE_TRACE_GEOJSON, dip=0.0, min_depth_km=0.0, max_depth_km=12.0)


def test_lower_depth_must_exceed_upper_depth():
    with pytest.raises(ValueError):
        build_fault_surface(SIMPLE_TRACE_GEOJSON, dip=70.0, min_depth_km=10.0, max_depth_km=5.0)


def test_unsupported_geometry_type_raises():
    point_geojson = json.dumps({"type": "Point", "coordinates": [-1.7, 37.6]})
    with pytest.raises(ValueError):
        build_fault_surface(point_geojson, dip=70.0, min_depth_km=0.0, max_depth_km=12.0)
