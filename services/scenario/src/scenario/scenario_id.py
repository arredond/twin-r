"""Content-addressed scenario ids, and the switch for reusing them as a cache.

A scenario_id is a hash of everything that determines a scenario's result:

- the request's own inputs (fault_id + probability_level for automatic
  mode, plus near_lat/near_lon only when that fault actually uses them --
  faults.py's `rupture_anchor`; every manual-mode field for manual mode),
- `API_VERSION`: the calculation code's version. **Bump it whenever a
  change to rupture/ground-motion/damage/response logic could change any
  result or the response shape** -- that's what invalidates every cached
  result computed by older code.
- `data_version()`: which pipeline outputs were read (`TWINER_DATA_VERSION`,
  set per deployment -- infra/stacks/twiner_stack.py). **Bump it whenever
  exposure/fragility/faults/buildings data is re-uploaded.**

Identical requests land on the same id, which means the results already
stored under it (results_store.py locally, the results bucket in the cloud)
can be served instead of recomputing -- when `cache_enabled()`. The id is
always content-addressed, cache or not; the flag only decides whether an
existing entry is trusted. See docs/decisions/0018-scenario-result-cache.md.
"""

from __future__ import annotations

import hashlib
import json
import os

# Bump on any change that could alter a scenario's result or response
# shape (see module docstring). A plain counter, not a git sha: a sha
# would bust the cache on every unrelated commit (docs, frontend, infra).
API_VERSION = "1"

_TRUTHY = {"1", "true", "yes", "on"}


def data_version() -> str:
    """The deployed data's version label. Defaults to "dev" -- fine with
    caching off (the local default); with caching on, set it explicitly or
    a data rebuild won't invalidate old results."""
    return os.environ.get("TWINER_DATA_VERSION", "dev")


def cache_enabled() -> bool:
    """`TWINER_SCENARIO_CACHE` -- off unless set to a truthy value. Off by
    default everywhere in code, so local dev (where the calculation code is
    what's being changed) always recomputes; the deployed stack turns it
    on explicitly."""
    return os.environ.get("TWINER_SCENARIO_CACHE", "").strip().lower() in _TRUTHY


def _hash(params: dict) -> str:
    key = {"api_version": API_VERSION, "data_version": data_version(), **params}
    canonical = json.dumps(key, sort_keys=True, separators=(",", ":"))
    # 32 hex chars (128 bits) -- same length as the uuid4().hex ids this
    # replaced, so nothing downstream (S3 keys, tile URLs) changes shape.
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def fault_scenario_id(
    fault_id: str,
    probability_level: str,
    near_lat: float | None = None,
    near_lon: float | None = None,
) -> str:
    """Pass near_lat/near_lon only when the rupture actually used them
    (faults.py's `rupture_anchor` says so) -- including an ignored point
    would split one result across as many ids as there are map views."""
    params: dict = {"mode": "fault", "fault_id": fault_id, "probability_level": probability_level}
    if near_lat is not None and near_lon is not None:
        params["near_lat"] = near_lat
        params["near_lon"] = near_lon
    return _hash(params)


def manual_scenario_id(
    lat: float,
    lon: float,
    mag: float,
    rake: float,
    strike: float | None,
    dip: float | None,
    ztor_km: float | None,
    probability_level: str,
) -> str:
    return _hash(
        {
            "mode": "manual",
            "lat": float(lat),
            "lon": float(lon),
            "mag": float(mag),
            "rake": float(rake),
            "strike": None if strike is None else float(strike),
            "dip": None if dip is None else float(dip),
            "ztor_km": None if ztor_km is None else float(ztor_km),
            "probability_level": probability_level,
        }
    )
