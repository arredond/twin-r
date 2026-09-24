"""Rupture definition: MERISUR's automatic (fault-based) and manual input modes.

See docs/merisur.md §4.1: MERISUR lets a user either pick a QAFI fault and
generate its maximum-magnitude earthquake ("automatic"), or type in rupture
parameters directly ("manual"). We keep the same two entry points.

MVP simplification (docs/milestone-1-plan.md §1) partially superseded by
ADR-0007 (docs/decisions/0007-finite-rupture-surface.md): manual-mode
ruptures, and any automatic-mode fault whose geometry hazardlib rejects
(rare -- see surface.py), still use a **point source**, where the
Joyner-Boore distance (Rjb) used by the Akkar et al. (2014) GMPE is
approximated as the geodesic distance from each building to that point.
Automatic-mode ruptures normally carry a real finite `surface` instead
(built from QAFI's own trace/dip/depth data, `from_fault` below), which
gives a geometrically correct Rjb via hazardlib directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openquake.hazardlib.geo.surface.base import BaseSurface

# "Style of faulting," MERISUR's simplified alternative to a raw rake value
# (docs/merisur.md's manual mode) -- the standard Aki & Richards (1980)
# representative rake for each of the three canonical mechanisms (also
# QAFI's own convention, see docs/decisions/0004). A categorical picker is
# strictly less expressive than raw rake, but far friendlier for a "just
# give me a plausible earthquake" default -- see ADR-0008.
STYLE_OF_FAULTING_RAKE = {
    "strike-slip": 0.0,
    "normal": -90.0,
    "reverse": 90.0,
}


@dataclass(frozen=True)
class Rupture:
    """A single earthquake scenario.

    lat/lon: hypocentre (or, for a fault-based rupture, a representative
    point on the fault trace -- see `from_fault`). Always used for things
    that only need an approximate location (e.g. the spatial pre-filter's
    search radius, engine.py); superseded by `surface` for the actual Rjb
    computation (ground_motion.py) whenever `surface` is present.
    mag: moment magnitude.
    rake: degrees, per the Aki & Richards convention Akkar et al. (2014)
      expects (0 = strike-slip, 90 = reverse, -90 = normal).
    depth_km: hypocentral/Ztor depth, used only in the point-source
      fallback (no `surface`) -- ignored when a surface is present, since
      the surface's own depth range is what actually matters then.
    surface: a finite rupture plane (openquake.hazardlib `BaseSurface`),
      when available -- see ADR-0007. `None` means "use the point-source
      approximation," which is always a valid fallback.
    """

    lat: float
    lon: float
    mag: float
    rake: float
    depth_km: float = 10.0
    surface: BaseSurface | None = None
    source: str = "manual"  # "manual" | "fault:<fault_id>"

    def __post_init__(self) -> None:
        if not (3.0 <= self.mag <= 9.0):
            raise ValueError(f"mag {self.mag} outside plausible range [3.0, 9.0]")
        if not (-180.0 <= self.rake <= 180.0):
            raise ValueError(f"rake {self.rake} outside [-180, 180]")


def from_fault(
    fault_id: str,
    name: str,
    point_lat: float,
    point_lon: float,
    mmax: float,
    rake: float | None = None,
    geometry_geojson: str | None = None,
    dip: float | None = None,
    min_depth_km: float | None = None,
    max_depth_km: float | None = None,
) -> Rupture:
    """Build the fault's maximum-magnitude rupture.

    Matches MERISUR's "Automatic" mode (docs/merisur.md §4.1: "select an
    existing fault and generate the earthquake of maximum magnitude
    associated with it"). `point_lat`/`point_lon` is the rupture's
    representative point on the trace (faults.py's `rupture_anchor`: the
    trace midpoint, or for a fault without full rupture geometry, the
    trace point nearest a caller's reference point) -- always set, used as
    the point-source fallback location, the response's echoed rupture
    location and the center of its evaluated-region circle. With a surface,
    the surface alone drives the spatial pre-filter and distances. `rake` should come from QAFI's own
    published focal mechanism (ADR-0004) where available; falls back to 0
    (strike-slip) only if genuinely missing/NaN.

    When `geometry_geojson`/`dip`/`min_depth_km`/`max_depth_km` are all
    given, attempts to build a real finite rupture surface from them
    (ADR-0007, surface.py) -- QAFI publishes these for all 201 of its
    faults, so this is the normal path for automatic mode, not an edge
    case. Falls back to a point-source rupture (surface=None) if any are
    missing, or if hazardlib rejects the geometry (e.g. a self-intersecting
    trace) -- one fault's messy digitization shouldn't fail the scenario.
    """
    if rake is None or math.isnan(rake):
        rake = 0.0

    surface = None
    if (
        geometry_geojson is not None
        and dip is not None
        and min_depth_km is not None
        and max_depth_km is not None
    ):
        from .surface import build_fault_surface

        try:
            surface = build_fault_surface(geometry_geojson, dip, min_depth_km, max_depth_km)
        except ValueError:
            surface = None

    return Rupture(
        lat=point_lat,
        lon=point_lon,
        mag=mmax,
        rake=rake,
        surface=surface,
        source=f"fault:{fault_id}:{name}",
    )


def from_manual_input(
    lat: float,
    lon: float,
    mag: float,
    rake: float = 0.0,
    strike: float | None = None,
    dip: float | None = None,
    ztor_km: float | None = None,
) -> Rupture:
    """Build a manual-mode rupture (ADR-0008, docs/decisions/0008-manual-mode-progressive-geometry.md).

    Progressive complexity, per the UI's three tiers:
    - Magnitude only: point source at (lat, lon), rake defaults to 0
      (strike-slip) -- the simplest, always-valid case.
    - + rake (or the UI's "style of faulting" picker, translated to rake by
      the caller via STYLE_OF_FAULTING_RAKE): still a point source, just a
      more deliberate mechanism than the strike-slip default.
    - + strike/dip/ztor_km: attempts a real finite rupture surface
      (surface.py's `build_manual_surface`) -- a synthetic trace centered
      on (lat, lon), oriented along `strike`, with length/width derived
      from magnitude (Wells & Coppersmith 1994, same relation
      pipelines/faults already uses in the other direction). Only happens
      when the user explicitly provides all three -- there's no
      non-arbitrary default *orientation* for an earthquake the way there
      is for rake, so we don't silently fabricate one.

    Falls back to the point-source rupture if strike/dip/ztor_km are
    partially given, or if hazardlib rejects the resulting geometry (e.g.
    an implausible dip) -- never an error, same robustness contract as
    `from_fault`.
    """
    surface = None
    if strike is not None and dip is not None and ztor_km is not None:
        from .surface import build_manual_surface

        try:
            surface = build_manual_surface(lat, lon, mag, strike, dip, ztor_km)
        except ValueError:
            surface = None

    return Rupture(lat=lat, lon=lon, mag=mag, rake=rake, surface=surface, source="manual")
