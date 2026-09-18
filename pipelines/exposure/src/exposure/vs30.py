"""Per-building Vs30 from ESRM20's national site model.

See ADR-0015 (docs/decisions/0015-eshm20-site-amplification.md) for why:
`services/scenario/ground_motion.py` currently evaluates every building
against a flat `DEFAULT_VS30 = 800` (EC8 reference rock) -- the documented
MVP simplification of omitting site amplification entirely
(docs/validation-lorca-2011.md §5, §9). The Akkar, Sandıkkaya & Bommer
(2014) GMPE already has a built-in Vs30 site term (`ground_motion.py`'s
`_intensity_at_distances` already threads `vs30` into the GMPE context),
so closing this gap is a data problem, not a new model: get a real
per-building Vs30 instead of the flat default.

Source: the ESRM20 (European Seismic Risk Model 2020) repository
(https://gitlab.seismo.ethz.ch/efehr/esrm20), CC BY 4.0, published by
EFEHR/SED-ETH Zürich. `Vs30_30arcsec/Site_model_30arcsec_Spain.csv` is an
irregular point cloud (not a raster with a fixed header) of inferred Vs30
values covering Spain at ~30 arc-second spacing (~800m at Spain's
latitudes), built from slope + geology proxies (Wald & Allen 2007-style
topographic slope, refined with geological era). This is the same site
model OpenQuake-based European risk calculations use as GMPE input, so
using it here keeps twin-r consistent with how the rest of Europe runs
Akkar-family GMPEs with site effects, without inventing a second
amplification model on top of the GMPE's own site term.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

SPAIN_VS30_CSV_URL = (
    "https://gitlab.seismo.ethz.ch/efehr/esrm20/-/raw/main/"
    "Vs30_30arcsec/Site_model_30arcsec_Spain.csv"
)

# Points in the source CSV further than this from a building are treated as
# "no coverage" rather than silently returning a misleadingly precise
# nearest value -- the grid is built for Spain's mainland + islands but
# gaps (offshore points, edge-of-coverage artifacts) are possible. Chosen
# as ~3x the ~30 arc-second nominal spacing (~0.83km at Spain's latitudes)
# -- generous enough to tolerate coastline/edge irregularity in the source
# grid, tight enough that a real gap doesn't get silently papered over with
# a point from a different geological setting many km away.
_MAX_LOOKUP_DISTANCE_DEG = 0.025


def fetch_spain_vs30_grid(timeout: int = 60) -> pd.DataFrame:
    """Download ESRM20's Vs30 point grid for Spain.

    Columns: lon, lat, slope, vs30, geology, xvf, region, vs30measured
    (vs30measured: 1 where the point is a real measurement rather than a
    slope/geology-inferred estimate -- vendored as-is in case a future
    consumer wants to weight or flag inferred vs. measured points; unused
    by `add_vs30_column` today, which treats every point uniformly).
    """
    resp = requests.get(SPAIN_VS30_CSV_URL, timeout=timeout)
    resp.raise_for_status()
    from io import StringIO

    return pd.read_csv(StringIO(resp.text))


def build_vs30_lookup(grid: pd.DataFrame) -> cKDTree:
    """A KD-tree over the grid's (lon, lat) points, for nearest-neighbor
    lookup. Built once per pipeline run (or process) and reused across
    every buildings.parquet part -- rebuilding it per part would dominate
    the actual lookup cost for a grid this size (~370k points for Spain).
    """
    return cKDTree(grid[["lon", "lat"]].to_numpy())


def lookup_vs30(
    lons: np.ndarray,
    lats: np.ndarray,
    grid: pd.DataFrame,
    tree: cKDTree | None = None,
) -> np.ndarray:
    """Nearest-neighbor Vs30 (m/s) for each (lon, lat) site.

    Plain nearest-neighbor, not interpolation -- consistent with how the
    grid itself was built (one inferred value per ~30 arc-second cell, not
    a continuous field) and cheap enough at national scale (a KD-tree query
    is O(log n) per site) that there's no accuracy/cost tradeoff worth
    making here. Returns `NaN` for any site farther than
    `_MAX_LOOKUP_DISTANCE_DEG` from its nearest grid point (see that
    constant's docstring) -- callers should fall back to
    `ground_motion.DEFAULT_VS30` for those, the same as they do for
    buildings this backfill hasn't reached yet.
    """
    if tree is None:
        tree = build_vs30_lookup(grid)
    points = np.column_stack([lons, lats])
    distances, indices = tree.query(points)
    vs30 = grid["vs30"].to_numpy()[indices]
    vs30 = np.where(distances <= _MAX_LOOKUP_DISTANCE_DEG, vs30, np.nan)
    return vs30


@lru_cache(maxsize=1)
def _cached_grid_and_tree() -> tuple[pd.DataFrame, cKDTree]:
    """Fetch + index the Spain Vs30 grid once per process, not once per
    municipality.

    A crawl (`region.py`) processes hundreds to thousands of municipalities
    in one run, each calling `build_exposure` -> `add_vs30_column`
    separately -- without this cache, that's one redundant 17MB HTTP fetch
    and one redundant ~230k-point KD-tree build per municipality instead of
    one per process. `lru_cache(maxsize=1)` (not a plain module-level
    global) so it's still lazy -- a caller that never touches vs30 (e.g.
    unit tests) never pays the network cost.
    """
    grid = fetch_spain_vs30_grid()
    return grid, build_vs30_lookup(grid)


def add_vs30_column(
    buildings: gpd.GeoDataFrame,
    grid: pd.DataFrame | None = None,
    tree: cKDTree | None = None,
) -> gpd.GeoDataFrame:
    """Precompute a `vs30` column from each building's centroid.

    Requires `centroid_lon`/`centroid_lat` to already be present
    (`parse.add_spatial_index_columns`, ADR-0006) -- reuses those instead
    of recomputing a centroid, same rationale as every other precomputed
    spatial column in this pipeline: a building's footprint centroid
    doesn't change, so its Vs30 lookup shouldn't be redone at request time
    either (ADR-0015).

    `grid`/`tree` let a caller processing many parts (`backfill.py`) fetch
    + build the KD-tree once and pass it through explicitly. When neither
    is given (the default -- this is what `pipeline.build_exposure` calls
    with for every live crawl), falls back to `_cached_grid_and_tree`'s
    process-wide cache instead of a bare per-call fetch, so a normal crawl
    run gets the same "fetch once" behavior without every call site having
    to manage the grid/tree itself.
    """
    if "centroid_lon" not in buildings.columns:
        raise ValueError(
            "add_vs30_column requires centroid_lon/centroid_lat -- run "
            "add_spatial_index_columns first (parse.py)"
        )
    if grid is None:
        grid, cached_tree = _cached_grid_and_tree()
        tree = tree or cached_tree
    result = buildings.copy()
    result["vs30"] = lookup_vs30(
        result["centroid_lon"].to_numpy(),
        result["centroid_lat"].to_numpy(),
        grid,
        tree=tree,
    )
    return result


def cache_grid_locally(dest: str | Path, timeout: int = 60) -> Path:
    """Download the Spain Vs30 CSV to `dest` for reuse across pipeline runs
    without re-fetching from GitLab every time (e.g. a national backfill
    that reads the same grid for ~7,700 parts). Not called automatically --
    an explicit step for whoever's running a large batch job."""
    dest = Path(dest)
    resp = requests.get(SPAIN_VS30_CSV_URL, timeout=timeout)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest
