"""`GET /warmup`: pay a new execution environment's one-time setup ahead of
the user's first scenario request.

The frontend calls this once on page load and doesn't wait for it
(apps/web/src/App.tsx). The environment it lands on then has hazardlib
imported, numba's cached functions loaded (numba_cache.py) and DuckDB's
extensions loaded by the time the user picks a fault and runs a scenario.

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

from .db import ensure_httpfs, ensure_spatial, get_connection
from .numba_cache import warm as warm_numba

_warmed = False


def warm_up(needs_httpfs: bool) -> dict:
    """Idempotent; `needs_httpfs` when the data lives on S3 (the deployed
    stack), not on local disk."""
    global _warmed
    if _warmed:
        return {"status": "warm", "already_warm": True, "seconds": 0.0}
    t0 = time.monotonic()
    # Imports hazardlib (and the engine) and runs a tiny finite-fault and
    # point-source ground-motion evaluation: everything a scenario compiles
    # or loads from the numba cache.
    warm_numba()
    con = get_connection()
    if needs_httpfs:
        ensure_httpfs(con)
    ensure_spatial(con)  # every fault request needs it (faults.get_fault)
    _warmed = True
    seconds = round(time.monotonic() - t0, 2)
    print(f"warmup: done in {seconds}s")
    return {"status": "warm", "already_warm": False, "seconds": seconds}
