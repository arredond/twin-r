"""Ground motion: Akkar, Sandıkkaya & Bommer (2014) GMPE via openquake.hazardlib.

See docs/merisur.md §4.2 for why this is the right GMPE to match MERISUR's
own chain, and docs/milestone-1-plan.md §2 for why we skip Lorca's soil
microzonation (not public, not portable) in favour of a flat reference-rock
Vs30 for the MVP.

Computes whichever intensity measure a caller asks for (`imt`), not a single
hardcoded one -- the vendored fragility curves (pipelines/fragility) are
indexed by different IM types depending on taxonomy/height class (PGA for
1-story, SA at increasing periods for taller buildings), and evaluating a
building against the wrong IM type is a real correctness bug this module
used to have (docs/validation-lorca-2011.md §10.2/§10.4): every scenario
computed SA(0.3s) only and fed it into whichever curve a building resolved
to, regardless of what that curve was actually indexed by.
"""

from __future__ import annotations

import math

import numpy as np
from openquake.hazardlib.geo.mesh import Mesh
from openquake.hazardlib.gsim.akkar_2014 import AkkarEtAlRjb2014
from openquake.hazardlib.imt import IMT, PGA, SA
from pyproj import Geod

from .rupture import Rupture

_KM_PER_DEGREE_LAT = 111.0

# EC8 "rock" reference site condition. A flat default everywhere is the
# documented MVP simplification (no Vs30/EC8 soil-class layer yet) -- every
# building currently gets identical site amplification (none).
DEFAULT_VS30 = 800.0

# Maps the `im_type` label vendored fragility curves carry (`fragility.parquet`'s
# `im_type` column -- pipelines/fragility, e.g. "SA(0.3s) [g]") to the
# hazardlib IMT object needed to compute it. Covers every IM type currently
# vendored across all taxonomy/height classes (docs/validation-lorca-2011.md
# §10.2) -- add an entry here whenever a newly-vendored class introduces a
# new one; a lookup miss is a loud `KeyError`, not a silent wrong-axis read.
IM_TYPE_TO_IMT: dict[str, IMT] = {
    "PGA [g]": PGA(),
    "SA(0.3s) [g]": SA(0.3),
    "SA(0.6s) [g]": SA(0.6),
    "SA(1.0s) [g]": SA(1.0),
}

# The IM `estimate_significant_distance_km`'s search-radius heuristic uses
# as a representative proxy for "is this rupture's shaking still
# significant out here." It doesn't need to be the exact IM any particular
# building's fragility curve is indexed by -- that function only decides
# how far out to bother pre-filtering buildings before per-building
# fragility evaluation runs, and every vendored curve's low-intensity tail
# is well below `min_significant_sa` at comparable distances regardless of
# period, so this is a deliberately IM-agnostic-in-spirit choice, not
# something that needs to vary per building the way fragility evaluation
# itself does.
SIGNIFICANT_DISTANCE_IMT = SA(0.3)

_GEOD = Geod(ellps="WGS84")
_GMPE = AkkarEtAlRjb2014()

# hazardlib's `BaseSurface.get_joyner_boore_distance` computes one dense
# (surface mesh points x sites) geodetic distance matrix in a single shot,
# with no internal chunking. For a short fault (a few hundred mesh points
# at DEFAULT_MESH_SPACING_KM) that's fine even at millions of sites, but a
# long QAFI fault trace produces proportionally more mesh points, and the
# matrix's memory footprint blows up the *product* of the two, not their
# sum -- measured at a real fault this size (QAFI's Penacova-Regua-Verin,
# ~1200 mesh points), a 3M-site call spends >150s (almost certainly memory
# pressure/thrashing from one ~29GB temporary array, not raw FLOPs: the
# same call is only ~4.5x slower than a short fault's at 300k sites, where
# the matrix still fits comfortably in memory). Calling it in row-wise
# chunks bounds that matrix to (mesh points x chunk size) at a time --
# confirmed by measurement to cut that same call from ~150s to ~5s, with no
# change to the result (it's an exact per-chunk slice of the same
# computation, not an approximation).
_DISTANCE_CHUNK_SIZE = 200_000


def _intensity_at_distances(
    rupture: Rupture,
    rjb_km: np.ndarray,
    imt: IMT,
    vs30: float,
    sigma_multiplier: float = 0.0,
) -> np.ndarray:
    """`imt`, in g, at each given Rjb distance (km) for this rupture.

    `sigma_multiplier` shifts the result in log space by that many standard
    deviations of the GMPE's own total aleatory uncertainty (`sig`, already
    computed by every call here) -- 0.0 (default) is the median; 1.0 is
    MERISUR's "low probability / high impact" and "very low probability /
    very high impact" tiers (`probability_level.py`, `docs/merisur.md`
    §4.7). Distance-only entry point (no site lat/lon) -- shared by
    `compute_intensity` (real sites) and `estimate_significant_distance_km`
    (searching for the distance at which intensity crosses a threshold,
    direction-independent since Rjb is the only distance metric this GMPE
    uses).
    """
    n = len(rjb_km)
    if n == 0:
        return np.zeros(0)

    dtype = [("mag", float), ("rake", float), ("rjb", float), ("vs30", float), ("sids", int)]
    ctx = np.zeros(n, dtype=dtype).view(np.recarray)
    ctx.mag[:] = rupture.mag
    ctx.rake[:] = rupture.rake
    ctx.rjb[:] = rjb_km
    ctx.vs30[:] = vs30
    ctx.sids[:] = np.arange(n)

    imts = [imt]
    mean = np.zeros((1, n))
    sig = np.zeros((1, n))
    tau = np.zeros((1, n))
    phi = np.zeros((1, n))
    _GMPE.compute(ctx, imts, mean, sig, tau, phi)

    return np.exp(mean[0] + sigma_multiplier * sig[0])  # ln(IM) -> IM, in g


def compute_intensity(
    rupture: Rupture,
    lats: np.ndarray,
    lons: np.ndarray,
    imt: IMT,
    vs30: float = DEFAULT_VS30,
    sigma_multiplier: float = 0.0,
) -> np.ndarray:
    """Return `imt`, in g, at each (lat, lon) site for this rupture.

    Rjb comes from `rupture.surface` when present (ADR-0007: a real finite
    rupture plane, geometrically correct) -- falls back to the geodesic
    distance to `rupture`'s point location (rupture.py's point-source
    simplification) when it's not, which is always the case for manual-mode
    ruptures and rare for automatic-mode ones (only if hazardlib rejected
    that fault's geometry). `sigma_multiplier`: see `_intensity_at_distances`.
    """
    n = len(lats)
    if n == 0:
        return np.zeros(0)

    if rupture.surface is not None:
        rjb_km = np.empty(n)
        for start in range(0, n, _DISTANCE_CHUNK_SIZE):
            end = start + _DISTANCE_CHUNK_SIZE
            chunk = Mesh(lons[start:end], lats[start:end])
            rjb_km[start:end] = rupture.surface.get_joyner_boore_distance(chunk)
    else:
        _, _, distance_m = _GEOD.inv(np.full(n, rupture.lon), np.full(n, rupture.lat), lons, lats)
        rjb_km = np.abs(distance_m) / 1000.0
    return _intensity_at_distances(rupture, rjb_km, imt, vs30, sigma_multiplier)


# The GMPE's output is smooth in distance and barely changes across a span
# this small at regional (tens-to-hundreds-of-km) source-to-site distances --
# finer, in fact, than DEFAULT_MESH_SPACING_KM, the fault surface's own
# already-accepted discretization (surface.py). Real Spanish exposure data
# clusters tightly enough that snapping sites to a grid this coarse and
# computing ground motion once per occupied cell, instead of once per
# building, measures a 40-50x reduction in distinct points evaluated --
# and since the dominant remaining scenario cost is exactly this distance
# calculation (see compute_intensity's own docs), that's a ~25-80x cut to a
# scenario's slowest stage. Verified against the exact per-building value
# on live data: mean absolute error ~0.001g, p99 relative error under 3%
# -- comfortably inside the GMPE's own aleatory uncertainty. (Measured for
# SA(0.3s); other IM types vary just as smoothly with distance at this
# GMPE's regional scale, so the same grid resolution applies to all of
# them.)
SA_GRID_CELL_KM = 1.0


def compute_intensity_gridded(
    rupture: Rupture,
    lats: np.ndarray,
    lons: np.ndarray,
    imt: IMT,
    vs30: float = DEFAULT_VS30,
    cell_km: float = SA_GRID_CELL_KM,
    sigma_multiplier: float = 0.0,
) -> np.ndarray:
    """Like `compute_intensity`, but evaluated once per occupied grid cell
    and broadcast back to every site in it, not once per site.

    Each building keeps its own row in the caller's result (this only
    dedupes the expensive intermediate calculation) -- taxonomy/height-
    specific fragility lookups downstream still run per building as usual.
    `sigma_multiplier`: see `_intensity_at_distances`.
    """
    n = len(lats)
    if n == 0:
        return np.zeros(0)

    deg_lat = cell_km / _KM_PER_DEGREE_LAT
    deg_lon = cell_km / (_KM_PER_DEGREE_LAT * max(0.1, abs(math.cos(math.radians(lats.mean())))))
    cell_row = np.floor(lats / deg_lat).astype(np.int64)
    cell_col = np.floor(lons / deg_lon).astype(np.int64)

    # Packing into one 1D key and taking np.unique on that (rather than
    # axis=0 on the 2-column array directly) is ~7x faster in practice --
    # measured, not assumed; numpy's 2D unique goes through a slower
    # structured-view sort. 1_000_000 comfortably exceeds any realistic
    # column-index spread for a single scenario's bounding box, so the
    # packed key can't collide between two different (row, col) pairs --
    # `cell_col` can be negative (west of Greenwich), so the group
    # representative's row/col come back via `first_index` into the
    # original arrays rather than by unpacking the key arithmetically,
    # which would need a sign-aware divmod to round-trip correctly.
    keys = cell_row * 1_000_000 + cell_col
    _, first_index, inverse = np.unique(keys, return_index=True, return_inverse=True)
    cell_lats = (cell_row[first_index] + 0.5) * deg_lat
    cell_lons = (cell_col[first_index] + 0.5) * deg_lon

    cell_intensity = compute_intensity(rupture, cell_lats, cell_lons, imt, vs30, sigma_multiplier)
    return cell_intensity[inverse]


def estimate_significant_distance_km(
    rupture: Rupture,
    min_significant_sa: float = 0.02,
    min_km: float = 10.0,
    max_km: float = 300.0,
    vs30: float = DEFAULT_VS30,
    sigma_multiplier: float = 0.0,
) -> float:
    """Distance beyond which this rupture's ground motion is negligible.

    "Negligible" here means `SIGNIFICANT_DISTANCE_IMT` (SA(0.3s), a
    representative proxy -- see its own docstring) has dropped below
    `min_significant_sa` -- chosen well below the lowest intensity value
    appearing in any vendored fragility curve (~0.05g, pipelines/fragility),
    so no building's damage probability could still be materially non-zero
    past this distance. This replaces a flat search radius (the earlier
    DEFAULT_MAX_DISTANCE_KM=300 constant in engine.py) with one that's
    actually derived from this specific rupture's magnitude/mechanism --
    see docs/validation-region-expansion.md §4 for why a flat 300km scanned
    far more of the region than most scenarios ever needed.

    `sigma_multiplier` (see `_intensity_at_distances`) must match whatever
    value the scenario itself will use (`engine.run_scenario`'s own
    parameter of the same name) -- a higher-probability-level scenario's
    ground motion stays above `min_significant_sa` out to a larger radius,
    so searching at the wrong sigma would silently exclude buildings a
    "low"/"very_low" tier run should have evaluated.

    SA(0.3s) decreases monotonically with Rjb for a fixed magnitude/rake,
    so binary search is safe and cheap (a handful of GMPE evaluations, not
    a per-site cost). Clamped to [min_km, max_km]: min_km avoids a
    degenerate near-zero radius for tiny magnitudes (still want *some*
    margin around the rupture), max_km is a hard safety ceiling we've
    actually load-tested (docs/validation-region-expansion.md).
    """
    sa_at_min = _intensity_at_distances(
        rupture, np.array([min_km]), SIGNIFICANT_DISTANCE_IMT, vs30, sigma_multiplier
    )[0]
    if sa_at_min < min_significant_sa:
        return min_km
    sa_at_max = _intensity_at_distances(
        rupture, np.array([max_km]), SIGNIFICANT_DISTANCE_IMT, vs30, sigma_multiplier
    )[0]
    if sa_at_max >= min_significant_sa:
        return max_km

    lo, hi = min_km, max_km
    for _ in range(20):  # ~20 iterations narrows [10, 300] to sub-metre precision
        mid = (lo + hi) / 2
        sa_mid = _intensity_at_distances(
            rupture, np.array([mid]), SIGNIFICANT_DISTANCE_IMT, vs30, sigma_multiplier
        )[0]
        if sa_mid >= min_significant_sa:
            lo = mid
        else:
            hi = mid
    return hi
