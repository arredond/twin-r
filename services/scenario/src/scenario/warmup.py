"""`GET /warmup`: pay a new execution environment's one-time setup ahead of
the user's first scenario request.

The frontend calls this once on page load and doesn't wait for it
(apps/web/src/App.tsx). The environment it lands on then has hazardlib
imported, numba's cached functions loaded (numba_cache.py), DuckDB's
extensions loaded, and the scenario's building query already run once
over a tiny box (`engine.warm_site_query`: S3 connections and both
parquet footers cached) by the time the user picks a fault and runs a
scenario.

This is the "do the heavy setup before it's needed" idea without putting
it in Lambda's Init phase, which ADR-0021 decided against. Init is hard-capped
at 10s, and already ~1.2s typical and ~7s after a deploy, and `/faults` and
`/buildings` would pay for it too. As an ordinary request, it runs under the
function's own timeout and only costs the call that asked for it.

Idempotent: the first call in an environment does the work (a few
seconds); later ones return immediately.
"""

from __future__ import annotations

import time

from .db import ensure_spatial, get_connection
from .numba_cache import files_written_since_seed
from .numba_cache import prepare as prepare_numba_cache
from .numba_cache import warm as warm_numba

_warmed = False


def warm_up(buildings_path: str, exposure_path: str) -> dict:
    """Idempotent. `buildings_path`/`exposure_path` are the files scenarios
    read (local paths in dev, `s3://` in the deployed stack)."""
    global _warmed
    if _warmed:
        return {"status": "warm", "already_warm": True, "seconds": 0.0}
    t0 = time.monotonic()
    # Imports hazardlib (and the engine) and runs a tiny finite-fault and
    # point-source ground-motion evaluation: everything a scenario compiles
    # or loads from the numba cache. First: it also applies numba's
    # size-only source stamps, before anything below imports hazardlib.
    prepare_numba_cache()
    warm_numba()
    t_numba = time.monotonic()
    con = get_connection()
    ensure_spatial(con)  # every fault request needs it (faults.get_fault)
    # The scenario's own building query over a tiny box: S3 connections,
    # both parquet footers (cached for the next query, db.py) and the
    # join's first run (engine.warm_site_query). Loads httpfs when needed.
    from .engine import warm_site_query

    n_sites = warm_site_query(buildings_path, exposure_path)
    _warmed = True
    seconds = round(time.monotonic() - t0, 2)
    # > 0 means numba missed the image's prebuilt cache (numba_cache.py).
    written = files_written_since_seed()
    print(
        f"warmup: done in {seconds}s (numba/hazardlib {t_numba - t0:.1f}s, "
        f"site query {time.monotonic() - t_numba:.1f}s, {n_sites} sites), "
        f"numba cache files written {written}"
    )
    return {
        "status": "warm",
        "already_warm": False,
        "seconds": seconds,
        "numba_cache_files_written": written,
    }
