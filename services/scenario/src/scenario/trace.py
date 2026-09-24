"""Fault-trace geometry helpers that don't need openquake.hazardlib.

Split out of surface.py so the automatic-mode request path can derive a
fault's representative point (and, with it, a content-addressed
scenario_id -- scenario_id.py) *before* deciding whether to compute at all:
surface.py imports hazardlib at module level, a multi-second cold-start
cost on Lambda (handler.py's own comment on lazy imports) that a scenario
cache hit should never pay. Only pyproj here.
"""

from __future__ import annotations

import json
from itertools import pairwise

from pyproj import Geod

_GEOD = Geod(ellps="WGS84")


def longest_linestring_coords(geometry: dict) -> list[list[float]]:
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


def trace_midpoint(geometry_geojson: str) -> tuple[float, float]:
    """(lat, lon) halfway along the fault trace's longest piece, by geodesic length.

    The same piece `build_fault_surface` builds its rupture plane from, so
    this point sits on (the surface trace of) the rupture actually used --
    automatic mode's representative rupture location, derived purely from
    the fault's own geometry rather than from wherever the user happened to
    be looking (the old `near_lat`/`near_lon` anchor, see faults.py).
    """
    coords = longest_linestring_coords(json.loads(geometry_geojson))
    if len(coords) == 1:
        lon, lat = coords[0][:2]
        return lat, lon

    segments = []
    for (lon1, lat1, *_), (lon2, lat2, *_) in pairwise(coords):
        azimuth, _, length_m = _GEOD.inv(lon1, lat1, lon2, lat2)
        segments.append((lon1, lat1, azimuth, length_m))

    remaining = sum(s[3] for s in segments) / 2
    for lon1, lat1, azimuth, length_m in segments:
        if remaining <= length_m:
            lon, lat, _ = _GEOD.fwd(lon1, lat1, azimuth, remaining)
            return lat, lon
        remaining -= length_m
    # Float round-off only -- the loop above always lands on the last
    # segment at the latest.
    lon, lat = coords[-1][:2]
    return lat, lon
