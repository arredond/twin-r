"""Ship numba's JIT cache inside the scenario Lambda's image.

Importing `openquake.hazardlib` compiles ~61 numba functions up front
(`openquake.baselib.performance.compile`, eager signatures), and a few more
compile on first use. numba caches that machine code on disk, but the
Lambda image's filesystem is read-only: numba silently ignores a cache
directory it can't write to, and the stack points `NUMBA_CACHE_DIR` at
an empty `/tmp/numba_cache`. So every new execution environment recompiled
from scratch: ~65s of the first scenario request per container in the
2026-09-24 cache-warm sweep (70-81s total, vs. 4-8s warm), 16-46s locally.

So the image builds the cache (`python -m scenario.numba_cache`, see
services/scenario/Dockerfile) and `seed()`, called when the handler module
loads, copies it into the writable `NUMBA_CACHE_DIR` before anything
imports hazardlib. Two things make a build-time cache valid at runtime:

- `NUMBA_CPU_NAME=generic` (set in the Dockerfile, so at build *and*
  runtime): numba keys cache entries by CPU model and features, and the
  image is built on whatever machine runs `cdk deploy` (often under amd64
  emulation), not on Lambda's hosts. A generic-CPU entry matches anywhere.
- The same source paths and mtimes: the cache is built in the final image
  layout, against the exact site-packages the function runs.

Measured locally: hazardlib's cold import 16.2s -> 1.3s with a seeded cache.
A read-only cache directory measured the full 16s, i.e. no cache at all.
"""

from __future__ import annotations

import os
import shutil
import time

SEED_DIR_ENV = "TWINER_NUMBA_CACHE_SEED"


def seed() -> None:
    """Copy the image's prebuilt cache into `NUMBA_CACHE_DIR`, once per
    execution environment. A no-op unless both are set (local dev uses
    numba's normal writable cache) or the target already exists."""
    seed_dir = os.environ.get(SEED_DIR_ENV)
    cache_dir = os.environ.get("NUMBA_CACHE_DIR")
    if not seed_dir or not cache_dir or os.path.exists(cache_dir):
        return
    if not os.path.isdir(seed_dir):
        print(f"numba_cache: no prebuilt cache at {seed_dir}, will JIT from scratch")
        return
    t0 = time.monotonic()
    # copyfile, not copytree's default copy2: don't carry over the image's
    # permission bits, and make every directory writable, so numba treats
    # the copy as a normal cache it can also add to.
    shutil.copytree(seed_dir, cache_dir, copy_function=shutil.copyfile)
    for root, _, _ in os.walk(cache_dir):
        os.chmod(root, 0o755)
    print(f"numba_cache: seeded {cache_dir} from {seed_dir} in {time.monotonic() - t0:.2f}s")


def warm() -> None:
    """Compile everything a scenario run compiles, so the cache written to
    `NUMBA_CACHE_DIR` covers it: hazardlib's import-time functions, plus
    the lazily compiled ones a finite-fault and a point-source scenario
    reach (surface build, Rjb distances, the GMPE for every IM type the
    fragility curves use). Needs no data files, so it runs at image build."""
    import numpy as np

    from .ground_motion import IM_TYPE_TO_IMT, GriddedIntensity, estimate_significant_distance_km
    from .rupture import from_fault, from_manual_input

    trace = '{"type": "LineString", "coordinates": [[-1.9, 37.6], [-1.6, 37.8]]}'
    ruptures = [
        from_fault(
            fault_id="warm",
            name="warm",
            point_lat=37.7,
            point_lon=-1.75,
            mmax=6.5,
            rake=90.0,
            geometry_geojson=trace,
            dip=60.0,
            min_depth_km=0.0,
            max_depth_km=12.0,
        ),
        from_manual_input(lat=37.7, lon=-1.75, mag=6.0, rake=0.0),
    ]
    lats = np.linspace(37.0, 38.5, 200)
    lons = np.linspace(-2.5, -1.0, 200)
    for rupture in ruptures:
        for sigma in (0.0, 1.0):
            estimate_significant_distance_km(rupture, sigma_multiplier=sigma)
            GriddedIntensity(
                rupture, IM_TYPE_TO_IMT, ref_lat=37.75, sigma_multiplier=sigma
            ).evaluate(lats, lons, np.full(200, 760.0))


if __name__ == "__main__":
    t0 = time.monotonic()
    warm()
    print(
        f"numba_cache: warmed {os.environ.get('NUMBA_CACHE_DIR')} in {time.monotonic() - t0:.1f}s"
    )
