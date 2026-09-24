import json

import numpy as np
from openquake.hazardlib.imt import PGA, SA
from scenario.ground_motion import (
    GriddedIntensity,
    compute_intensity,
    compute_intensity_gridded,
    estimate_significant_distance_km,
)
from scenario.rupture import Rupture, from_fault

SIMPLE_TRACE_GEOJSON = json.dumps(
    {"type": "LineString", "coordinates": [[-1.75, 37.60], [-1.65, 37.75]]}
)


def _compute_sa03(rupture, lats, lons, **kwargs):
    # Most of this file predates compute_intensity's `imt` parameter and
    # only ever exercised the SA(0.3s) case -- this local wrapper keeps
    # those tests reading the same way rather than repeating `SA(0.3)` at
    # every call site.
    return compute_intensity(rupture, lats, lons, SA(0.3), **kwargs)


def test_sa_decreases_with_distance():
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    lats = np.array([37.67, 37.67, 37.67])
    lons = np.array([-1.70, -1.80, -2.20])  # increasingly far east->west
    sa = _compute_sa03(rupture, lats, lons)
    assert sa[0] > sa[1] > sa[2]
    assert np.all(sa > 0)


def test_sa_increases_with_magnitude():
    lats = np.array([37.70])
    lons = np.array([-1.80])
    small = _compute_sa03(Rupture(lat=37.67, lon=-1.70, mag=4.5, rake=0.0), lats, lons)
    large = _compute_sa03(Rupture(lat=37.67, lon=-1.70, mag=7.0, rake=0.0), lats, lons)
    assert large[0] > small[0]


def test_empty_sites_returns_empty_array():
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    result = _compute_sa03(rupture, np.array([]), np.array([]))
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

    sa_near = _compute_sa03(rupture, np.array([lat_near]), np.array([-1.70]))[0]
    sa_far = _compute_sa03(rupture, np.array([lat_far]), np.array([-1.70]))[0]
    assert sa_near >= threshold
    assert sa_far < threshold


def test_sigma_multiplier_increases_ground_motion():
    # MERISUR's "low"/"very_low" tiers (docs/merisur.md §4.7): median+1sigma
    # ground motion must be strictly larger than the median (sigma_multiplier
    # defaults to 0.0, unchanged pre-existing behaviour).
    rupture = Rupture(lat=37.67, lon=-1.70, mag=5.2, rake=44.0)
    lats, lons = np.array([37.70]), np.array([-1.72])
    median = _compute_sa03(rupture, lats, lons)
    plus_one_sigma = _compute_sa03(rupture, lats, lons, sigma_multiplier=1.0)
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
    sa_surface = _compute_sa03(rupture_with_surface, site_lat, site_lon)[0]
    sa_point = _compute_sa03(rupture_point_source, site_lat, site_lon)[0]
    assert sa_surface > sa_point


def test_compute_intensity_dispatches_to_the_requested_imt():
    # PGA and SA(0.3s) are genuinely different curves for the same rupture
    # -- this is the whole point of docs/validation-lorca-2011.md §10.2's
    # fix: a caller must get back the IM it actually asked for, not always
    # SA(0.3s) regardless of what was requested.
    rupture = Rupture(lat=37.67, lon=-1.70, mag=5.2, rake=44.0)
    lats, lons = np.array([37.70]), np.array([-1.72])
    pga = compute_intensity(rupture, lats, lons, PGA())
    sa03 = compute_intensity(rupture, lats, lons, SA(0.3))
    assert pga[0] != sa03[0]


def test_compute_intensity_gridded_matches_ungridded_for_pga():
    # Cross-check compute_intensity_gridded against the exact per-site path
    # for a non-SA(0.3s) IMT specifically -- the gridding logic itself
    # doesn't care which IMT it's deduping, but this used to only ever be
    # exercised with SA(0.3s) (compute_sa03_gridded), so PGA is worth
    # checking explicitly now that it's a real code path.
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    lats = np.array([37.70, 37.71, 37.72])
    lons = np.array([-1.72, -1.71, -1.70])
    exact = compute_intensity(rupture, lats, lons, PGA())
    gridded = compute_intensity_gridded(rupture, lats, lons, PGA())
    assert np.allclose(exact, gridded, rtol=0.05)


def test_gridded_intensity_is_the_same_whether_sites_arrive_at_once_or_in_batches():
    # engine.py streams a scenario's sites through GriddedIntensity in
    # batches; a cell must get the same value whichever batch reaches it
    # (fixed grid, cached cells), and every IM type must come out of the
    # one shared Rjb computation matching compute_intensity_gridded.
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    rng = np.random.default_rng(0)
    lats = 37.5 + rng.random(2_000) * 0.5
    lons = -2.0 + rng.random(2_000) * 0.5
    vs30 = 300.0 + rng.random(2_000) * 500.0
    imts = {"PGA": PGA(), "SA03": SA(0.3)}

    whole = GriddedIntensity(rupture, imts, ref_lat=37.75).evaluate(lats, lons, vs30)
    batched_grid = GriddedIntensity(rupture, imts, ref_lat=37.75)
    # Reversed batches, so later batches mostly hit cells an earlier batch
    # already computed (from a different representative site).
    parts = [
        batched_grid.evaluate(lats[i : i + 300], lons[i : i + 300], vs30[i : i + 300])
        for i in range(0, 2_000, 300)
    ]
    batched = {name: np.concatenate([p[name] for p in parts]) for name in imts}

    for name, imt in imts.items():
        assert whole[name].shape == (2_000,)
        # Same cells, same values; only which site represents a cell (and
        # so its Vs30) may differ between the two orders.
        assert np.allclose(whole[name], batched[name], rtol=0.5)
        single = GriddedIntensity(rupture, {name: imt}, ref_lat=37.75).evaluate(lats, lons, vs30)
        assert np.array_equal(whole[name], single[name])
    # With a uniform Vs30 the representative doesn't matter: exact.
    uniform = GriddedIntensity(rupture, imts, ref_lat=37.75)
    batched_uniform = np.concatenate(
        [
            uniform.evaluate(lats[i : i + 300], lons[i : i + 300], 760.0)["PGA"]
            for i in range(0, 2_000, 300)
        ]
    )
    whole_uniform = GriddedIntensity(rupture, imts, ref_lat=37.75).evaluate(lats, lons, 760.0)[
        "PGA"
    ]
    assert np.array_equal(batched_uniform, whole_uniform)
