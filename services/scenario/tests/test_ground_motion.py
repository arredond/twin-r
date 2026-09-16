import json

import numpy as np
from scenario.ground_motion import compute_sa03, estimate_significant_distance_km
from scenario.rupture import Rupture, from_fault

SIMPLE_TRACE_GEOJSON = json.dumps(
    {"type": "LineString", "coordinates": [[-1.75, 37.60], [-1.65, 37.75]]}
)


def test_sa_decreases_with_distance():
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    lats = np.array([37.67, 37.67, 37.67])
    lons = np.array([-1.70, -1.80, -2.20])  # increasingly far east->west
    sa = compute_sa03(rupture, lats, lons)
    assert sa[0] > sa[1] > sa[2]
    assert np.all(sa > 0)


def test_sa_increases_with_magnitude():
    lats = np.array([37.70])
    lons = np.array([-1.80])
    small = compute_sa03(Rupture(lat=37.67, lon=-1.70, mag=4.5, rake=0.0), lats, lons)
    large = compute_sa03(Rupture(lat=37.67, lon=-1.70, mag=7.0, rake=0.0), lats, lons)
    assert large[0] > small[0]


def test_empty_sites_returns_empty_array():
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    result = compute_sa03(rupture, np.array([]), np.array([]))
    assert len(result) == 0


def test_significant_distance_increases_with_magnitude():
    small = estimate_significant_distance_km(Rupture(lat=37.67, lon=-1.70, mag=4.5, rake=0.0))
    large = estimate_significant_distance_km(Rupture(lat=37.67, lon=-1.70, mag=7.5, rake=0.0))
    assert small < large


def test_significant_distance_respects_bounds():
    tiny = estimate_significant_distance_km(
        Rupture(lat=37.67, lon=-1.70, mag=3.5, rake=0.0), min_km=10.0, max_km=300.0
    )
    assert tiny == 10.0  # clamped to the floor, not degenerate/zero

    huge = estimate_significant_distance_km(
        Rupture(lat=37.67, lon=-1.70, mag=9.0, rake=0.0), min_km=10.0, max_km=300.0
    )
    assert huge == 300.0  # clamped to the safety ceiling


def test_significant_distance_is_where_sa_crosses_threshold():
    # Straight north/south instead of east/west, to sidestep longitude's
    # cos(latitude) scaling and keep the km->degrees conversion exact.
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    threshold = 0.02
    d = estimate_significant_distance_km(rupture, min_significant_sa=threshold)

    lat_near = 37.67 + (d - 2) / 111.0  # ~2km inside the boundary
    lat_far = 37.67 + (d + 2) / 111.0  # ~2km outside

    sa_near = compute_sa03(rupture, np.array([lat_near]), np.array([-1.70]))[0]
    sa_far = compute_sa03(rupture, np.array([lat_far]), np.array([-1.70]))[0]
    assert sa_near >= threshold
    assert sa_far < threshold


def test_sigma_multiplier_increases_ground_motion():
    # MERISUR's "low"/"very_low" tiers (docs/merisur.md §4.7): median+1sigma
    # ground motion must be strictly larger than the median (sigma_multiplier
    # defaults to 0.0, unchanged pre-existing behaviour).
    rupture = Rupture(lat=37.67, lon=-1.70, mag=5.2, rake=44.0)
    lats, lons = np.array([37.70]), np.array([-1.72])
    median = compute_sa03(rupture, lats, lons)
    plus_one_sigma = compute_sa03(rupture, lats, lons, sigma_multiplier=1.0)
    assert plus_one_sigma[0] > median[0]


def test_significant_distance_at_higher_sigma_is_not_smaller():
    # A "low"/"very_low" tier scenario's spatial pre-filter must not shrink
    # relative to "high" -- see estimate_significant_distance_km's own
    # docstring on why this consistency matters (a too-small radius would
    # silently exclude buildings the higher-impact tier should evaluate).
    rupture = Rupture(lat=37.67, lon=-1.70, mag=5.2, rake=44.0)
    median_radius = estimate_significant_distance_km(rupture, sigma_multiplier=0.0)
    high_impact_radius = estimate_significant_distance_km(rupture, sigma_multiplier=1.0)
    assert high_impact_radius >= median_radius


def test_compute_sa03_uses_surface_rjb_when_present():
    # ADR-0007: a site sitting right on the fault trace should get Rjb≈0
    # (and therefore high SA) via the finite surface, even though it's not
    # exactly at rupture.lat/lon -- the point-source fallback would give it
    # a nonzero distance instead. This is the actual behavioral difference
    # the surface buys us, not just "a different code path runs."
    rupture_with_surface = from_fault(
        "ES626",
        "Alhama de Murcia (1/4)",
        point_lat=37.60,
        point_lon=-1.75,  # trace's own start point, for a clean fallback comparison
        mmax=6.5,
        rake=20.0,
        geometry_geojson=SIMPLE_TRACE_GEOJSON,
        dip=70.0,
        min_depth_km=0.0,
        max_depth_km=12.0,
    )
    assert rupture_with_surface.surface is not None

    rupture_point_source = Rupture(lat=37.60, lon=-1.75, mag=6.5, rake=20.0)

    # A site along the trace, away from the anchor point -- the finite
    # surface should treat it as much closer than the point-source does.
    site_lat, site_lon = np.array([37.70]), np.array([-1.68])
    sa_surface = compute_sa03(rupture_with_surface, site_lat, site_lon)[0]
    sa_point = compute_sa03(rupture_point_source, site_lat, site_lon)[0]
    assert sa_surface > sa_point
