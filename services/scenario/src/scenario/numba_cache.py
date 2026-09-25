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
- The same source files: the cache is built in the final image layout,
  against the exact site-packages the function runs. numba stamps each
  cached entry with its source file's modification time and size, and the
  modification times *don't* survive deployment to Lambda, so the image
  stamps by size only (`use_size_only_source_stamps`). Without that, every
  entry looked stale in prod and was recompiled (2026-09-25).

Measured locally: hazardlib's cold import 16.2s -> 1.3s with a seeded cache.
A read-only cache directory measured the full 16s, i.e. no cache at all.
"""

from __future__ import annotations

import os
import shutil
import time

SEED_DIR_ENV = "TWINER_NUMBA_CACHE_SEED"

# When `seed()` finished copying, if it ran: see `files_written_since_seed`.
_seeded_at: float | None = None


def seed() -> None:
    """Once per execution environment, during Init: make numba use the
    image's prebuilt cache. A no-op outside the image (local dev keeps
    numba's normal writable cache).

    Only copies: it must not import numba, because Init is capped at 10s
    and already ran 9.8s on the first environments after a deploy
    (2026-09-25) when this also applied `use_size_only_source_stamps`.
    That patch is applied right before hazardlib is imported instead
    (handler.py's `_import_hazardlib`, and `warm`), which is all it needs.
    The copy is skipped if `NUMBA_CACHE_DIR` already exists, because Lambda
    can re-run Init in an environment whose `/tmp` survived; the stamps
    applied later make that copy usable too."""
    seed_dir = os.environ.get(SEED_DIR_ENV)
    cache_dir = os.environ.get("NUMBA_CACHE_DIR")
    if not seed_dir or not cache_dir:
        return
    if not os.path.exists(cache_dir):
        if not os.path.isdir(seed_dir):
            print(f"numba_cache: no prebuilt cache at {seed_dir}, will JIT from scratch")
            return
        t0 = time.monotonic()
        # copyfile, not copytree's default copy2: don't carry over the
        # image's permission bits, and make every directory writable, so
        # numba treats the copy as a normal cache it can also add to.
        shutil.copytree(seed_dir, cache_dir, copy_function=shutil.copyfile)
        for root, _, _ in os.walk(cache_dir):
            os.chmod(root, 0o755)
        print(f"numba_cache: seeded {cache_dir} from {seed_dir} in {time.monotonic() - t0:.2f}s")
    else:
        print(f"numba_cache: {cache_dir} already present (re-run Init), not re-seeding")
    global _seeded_at
    _seeded_at = time.time()


def use_size_only_source_stamps() -> None:
    """In the image only (a no-op unless `TWINER_NUMBA_CACHE_SEED` is set):
    make numba stamp each source file by its size alone, not by
    `(st_mtime, st_size)`. Must run before hazardlib is imported, both at
    build (`warm`, writing the cache) and at runtime (handler.py's
    `_import_hazardlib`, and `warm` via /warmup, reading it), so the two
    agree. Idempotent. Not called from `seed()`: importing numba there
    would put it in Lambda's Init phase (see `seed`).

    Why: numba only trusts a cache entry whose source file's stamp matches
    the one recorded when the entry was written, and file modification
    times don't survive the trip from `docker build` to a running Lambda
    (Lambda converts container images into its own block format). So
    every entry looked stale in prod, and each new environment recompiled
    everything: ~55s, with the handler's diagnostic logging `numba cache
    files written 110` (2026-09-25). Reproduced locally by changing every
    openquake source file's mtime inside the image: first rupture 154.8s
    vs. 12.0s untouched. Size-only is safe here because the image is
    immutable: a source file can't change between build and run.

    numba 0.61 (pinned through hazardlib) has no supported hook for this,
    so it patches the cache locator numba picks when `NUMBA_CACHE_DIR` is
    set. Later numba versions' `NUMBA_CACHE_LOCATOR_CLASSES` would replace
    the patch with configuration."""
    if not os.environ.get(SEED_DIR_ENV):
        return
    from numba.core import caching

    # Private in numba 0.61 (the image's), public in later versions (local
    # dev's) -- getattr for both, since each name exists in only one.
    locator = getattr(caching, "UserProvidedCacheLocator", None) or getattr(  # noqa: B009
        caching, "_UserProvidedCacheLocator"
    )

    def size_only_stamp(self) -> tuple[float, int]:
        return 0.0, os.stat(self._py_file).st_size

    locator.get_source_stamp = size_only_stamp


def files_written_since_seed() -> int | None:
    """How many files numba has written to `NUMBA_CACHE_DIR` since `seed()`
    copied the prebuilt cache in, or None if it didn't run. numba writes
    only on a cache *miss* (it compiled something and saved it), never on
    a hit, so 0 after a scenario means the prebuilt cache covered it.

    A diagnostic (handler.py and warmup.py log it): it's how the ~55s first
    scenarios in prod were traced to cache misses (110 files written; see
    `use_size_only_source_stamps`), and how to tell if they come back."""
    cache_dir = os.environ.get("NUMBA_CACHE_DIR")
    if _seeded_at is None or not cache_dir:
        return None
    return sum(
        1
        for root, _, files in os.walk(cache_dir)
        for name in files
        if os.stat(os.path.join(root, name)).st_mtime > _seeded_at
    )


def warm() -> None:
    """Compile everything a scenario run compiles, so the cache written to
    `NUMBA_CACHE_DIR` covers it: hazardlib's import-time functions, plus
    the lazily compiled ones a finite-fault and a point-source scenario
    reach (surface build, Rjb distances, the GMPE for every IM type the
    fragility curves use). Needs no data files, so it runs at image build."""
    use_size_only_source_stamps()  # before anything below compiles or loads
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
