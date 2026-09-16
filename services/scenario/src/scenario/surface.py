"""Finite rupture-plane construction from QAFI fault geometry.

See ADR-0007 (docs/decisions/0007-finite-rupture-surface.md): replaces the
point-source Rjb approximation (rupture.py's documented MVP simplification)
with a geometrically correct one for automatic-mode faults, using
`openquake.hazardlib`'s own `SimpleFaultSurface` -- built directly from a
QAFI fault's trace + dip + seismogenic depth range, which QAFI publishes
for all 201 of its faults (unlike Mmax, ~60%). No hand-rolled plane
geometry: hazardlib already implements Rjb/Rrup/Rx correctly for a surface
built this way.
"""

from __future__ import annotations

import json
import math

from openquake.hazardlib.geo.line import Line
from openquake.hazardlib.geo.point import Point
from openquake.hazardlib.geo.surface.base import BaseSurface
from openquake.hazardlib.geo.surface.simple_fault import SimpleFaultSurface
from pyproj import Geod

# Sub-second to build + compute distances for even QAFI's longest fault
# (282km) against tens of thousands of sites at this spacing -- see
# docs/decisions/0007 for the measurements that picked it. Tighter spacing
# buys negligible extra accuracy at this distance scale for a real cost
# increase (1km spacing was ~4x slower for the worst case).
DEFAULT_MESH_SPACING_KM = 2.0

_GEOD = Geod(ellps="WGS84")


def _longest_linestring_coords(geometry: dict) -> list[list[float]]:
    """Coordinates of the longest component of a (Multi)LineString GeoJSON geometry.

    Some QAFI traces are mapped as several disconnected LineString pieces
    (digitizing artifacts / separately-mapped segments) -- SimpleFaultSurface
    needs one continuous line, so we use the longest single piece rather
    than stitching pieces that may not be geographically contiguous. This
    is a documented simplification: a trace's minor secondary strands are
    dropped when building the rupture plane (the full trace is still used
    as-is for map display, via geometry_geojson -- only surface
    construction simplifies it).
    """
    if geometry["type"] == "LineString":
        return geometry["coordinates"]
    if geometry["type"] == "MultiLineString":

        def planar_length(coords: list[list[float]]) -> float:
            # Rough (non-geodesic) length -- only used to rank a single
            # fault's own pieces against each other, not compared across
            # faults, so the small distortion doesn't matter.
            return sum(
                ((coords[i][0] - coords[i - 1][0]) ** 2 + (coords[i][1] - coords[i - 1][1]) ** 2)
                ** 0.5
                for i in range(1, len(coords))
            )

        return max(geometry["coordinates"], key=planar_length)
    raise ValueError(f"unsupported geometry type for a fault trace: {geometry['type']}")


def build_fault_surface(
    geometry_geojson: str,
    dip: float,
    min_depth_km: float,
    max_depth_km: float,
    mesh_spacing_km: float = DEFAULT_MESH_SPACING_KM,
) -> BaseSurface:
    """Build a finite rupture plane from a QAFI fault's own trace + geometry.

    Raises ValueError if hazardlib rejects the input (e.g. a
    self-intersecting trace, or a dip/depth combination it considers
    invalid) -- callers should catch this and fall back to the point-source
    approximation (rupture.py) for that one fault rather than fail the
    whole scenario over one fault's messy digitization.
    """
    coords = _longest_linestring_coords(json.loads(geometry_geojson))
    trace = Line([Point(lon, lat) for lon, lat in coords])
    return SimpleFaultSurface.from_fault_data(
        trace, min_depth_km, max_depth_km, dip, mesh_spacing_km
    )


# Wells & Coppersmith (1994), all-fault-types coefficients (same paper
# pipelines/faults/mmax.py already uses for the reverse relation --
# Mw = 5.08 + 1.16*log10(SRL)). Solving for length given magnitude:
#   log10(SRL) = (Mw - 4.38) / 1.49  =>  SRL = 10^((Mw - 4.38) / 1.49)
# Rupture width (down-dip) has its own, separate W&C relation:
#   log10(RW) = -1.01 + 0.32*Mw
def _length_from_magnitude_km(mag: float) -> float:
    return 10 ** ((mag - 4.38) / 1.49)


def _width_from_magnitude_km(mag: float) -> float:
    return 10 ** (-1.01 + 0.32 * mag)


def build_manual_surface(
    lat: float,
    lon: float,
    mag: float,
    strike: float,
    dip: float,
    ztor_km: float,
    mesh_spacing_km: float = DEFAULT_MESH_SPACING_KM,
) -> BaseSurface:
    """Build a finite rupture plane for a manual-mode scenario.

    Unlike `build_fault_surface` (a real QAFI fault trace), manual mode has
    no trace to draw from -- only a center point plus the geometry the user
    chose to specify. We synthesize a two-point trace centered on
    (lat, lon), oriented along `strike`, with length derived from magnitude
    via Wells & Coppersmith (1994) (the same relation `pipelines/faults`
    already uses in the other direction, for Mmax-from-length). Down-dip
    width is derived the same way, giving a lower seismogenic depth of
    `ztor_km + width * sin(dip)`.

    This only runs when the user has explicitly provided strike/dip/ztor
    (see rupture.py's `from_manual_input`) -- deliberately not a silent
    default, since there's no non-arbitrary default *orientation* for an
    earthquake the way there is for e.g. rake (see ADR-0008).

    Raises ValueError if hazardlib rejects the resulting geometry (e.g. an
    implausible dip) -- callers should fall back to a point-source rupture.
    """
    half_length_km = _length_from_magnitude_km(mag) / 2
    width_km = _width_from_magnitude_km(mag)
    lower_depth_km = ztor_km + width_km * math.sin(math.radians(dip))

    lon1, lat1, _ = _GEOD.fwd(lon, lat, strike, half_length_km * 1000)
    lon2, lat2, _ = _GEOD.fwd(lon, lat, strike + 180, half_length_km * 1000)
    trace = Line([Point(lon2, lat2), Point(lon1, lat1)])

    return SimpleFaultSurface.from_fault_data(trace, ztor_km, lower_depth_km, dip, mesh_spacing_km)
