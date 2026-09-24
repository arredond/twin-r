"""AWS Lambda handler for the scenario function.

Thin adapter only (see docs/decisions/0001-compute-and-iac.md) -- all
domain logic lives in engine.py/rupture.py/ground_motion.py/damage.py/
faults.py/building_lookup.py, shared with the local dev server in
local.py. Mirrors local.py's five routes (`/scenarios/manual`, `/faults`,
`/scenarios/fault`, `/buildings/{id}`, `/warmup`) plus `/health`.

A Lambda Function URL has no *routing rules* the way API Gateway does (no
per-route Lambda mapping, no path-parameter extraction), but the event
itself still carries `rawPath`/`requestContext.http.method` -- Function
URLs use the same HTTP API v2.0 payload format API Gateway does. So this
handler does its own tiny routing by inspecting those fields directly,
rather than the "does the query have fault_id" heuristic an earlier
version of this file used, which only ever covered scenario computation
and silently 400'd every `/faults`/`/buildings/{id}` request (confirmed
against the real deployed Lambda -- the frontend's `/faults` sidebar
lookup has no `fault_id` and no body, so it fell into manual-mode parsing
and failed on a missing `lat`).

Exposure/fragility/faults parquet paths come from environment variables so
the same code reads local files in dev and S3-backed paths (via DuckDB's
httpfs extension, given an S3 URI) in the cloud.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re
import time

from . import numba_cache
from .building_lookup import get_building
from .faults import faults_payload, get_fault, round_near_point, rupture_anchor
from .probability_level import resolve_probability_level
from .response import evaluated_region
from .rupture import Rupture, from_fault, from_manual_input
from .scenario_id import cache_enabled, fault_scenario_id, manual_scenario_id
from .warmup import warm_up

# `engine`/`ground_motion` are deliberately NOT imported at module level --
# see _run_and_respond's own comment for why.

# buildings.parquet is a partitioned glob, not a single combined file --
# see local.py's own comment on this same pair of defaults for why.
BUILDINGS_PATH = os.environ.get("TWINER_BUILDINGS_PATH", "data/exposure/parts/*.buildings.parquet")
EXPOSURE_PATH = os.environ.get("TWINER_EXPOSURE_PATH", "data/exposure/exposure.parquet")
FRAGILITY_PATH = os.environ.get("TWINER_FRAGILITY_PATH", "data/fragility/fragility.parquet")
FAULTS_PATH = os.environ.get("TWINER_FAULTS_PATH", "data/faults/qafi_faults.parquet")
RESULTS_BUCKET = os.environ.get("TWINER_RESULTS_BUCKET")  # unset -> no tile results, no cache

# Once per execution environment, during Lambda's Init phase: put the
# image's prebuilt numba cache where numba will look, before anything
# imports hazardlib (nothing above does -- see numba_cache.py for why this
# took ~65s off each new container's first scenario request).
numba_cache.seed()


def handler(event: dict, context) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    # Collapse repeated slashes: apps/web's scenarioApi.ts builds URLs as
    # `${API_URL}/faults` etc, and the Function URL CDK/AWS hands back
    # always ends in a trailing slash -- `.../on.aws//faults` otherwise,
    # which `rawPath` passes through literally.
    path = re.sub(r"/+", "/", event.get("rawPath") or "/")
    query = event.get("queryStringParameters") or {}

    try:
        if method == "GET" and path == "/health":
            return _response(200, {"status": "ok"})

        if method == "GET" and path == "/faults":
            return _list_faults()

        if method == "GET" and path == "/warmup":
            data_paths = (BUILDINGS_PATH, EXPOSURE_PATH, FRAGILITY_PATH, FAULTS_PATH)
            return _response(
                200, warm_up(needs_httpfs=any(p.startswith("s3://") for p in data_paths))
            )

        if method == "GET" and path == "/scenarios/fault":
            return _fault_scenario(query)

        if method == "POST" and path == "/scenarios/manual":
            body = json.loads(event.get("body") or "{}")
            return _manual_scenario(body)

        if method == "GET" and path.startswith("/buildings/"):
            building_id = path[len("/buildings/") :]
            return _building_info(building_id)
    except (KeyError, ValueError) as e:
        return _response(400, {"error": f"invalid request parameters: {e}"})

    return _response(404, {"error": f"no route for {method} {path}"})


def _list_faults() -> dict:
    """Mirrors local.py's GET /faults -- every fault, no location args."""
    return _response(200, faults_payload(FAULTS_PATH))


def _building_info(building_id: str) -> dict:
    """Mirrors local.py's GET /buildings/{building_id}."""
    row = get_building(EXPOSURE_PATH, building_id)
    if row is None:
        return _response(404, {"error": f"building_id {building_id!r} not found"})
    return _response(200, row.to_dict())


def _optional_float(value: str | None) -> float | None:
    return None if value in (None, "") else float(value)


def _fault_scenario(query: dict) -> dict:
    """Mirrors local.py's GET /scenarios/fault -- see that route's
    docstring for when near_lat/near_lon matter (only for a fault without
    full rupture geometry)."""
    fault_id = query["fault_id"]  # missing -> KeyError -> 400, via handler()
    probability_level = query.get("probability_level", "high")
    resolve_probability_level(probability_level)  # ValueError -> 400, before anything else
    near_lat, near_lon = round_near_point(
        _optional_float(query.get("near_lat")), _optional_float(query.get("near_lon"))
    )
    try:
        fault = get_fault(FAULTS_PATH, fault_id, near_lat, near_lon)
    except KeyError as e:
        return _response(404, {"error": str(e)})
    anchor_lat, anchor_lon, near_used = rupture_anchor(fault)

    scenario_id = fault_scenario_id(
        fault["fault_id"],
        probability_level,
        near_lat if near_used else None,
        near_lon if near_used else None,
    )
    if (cached := _cached_response(scenario_id)) is not None:
        return cached

    t_rupture = time.monotonic()
    rupture = from_fault(
        fault_id=fault["fault_id"],
        name=fault["name"],
        point_lat=anchor_lat,
        point_lon=anchor_lon,
        mmax=fault["mmax"],
        rake=fault["rake"],
        geometry_geojson=fault["geometry_geojson"],
        dip=fault["dip"],
        min_depth_km=fault["min_depth_km"],
        max_depth_km=fault["max_depth_km"],
    )
    return _run_and_respond(rupture, probability_level, scenario_id, time.monotonic() - t_rupture)


def _manual_scenario(body: dict) -> dict:
    probability_level = body.get("probability_level", "high")
    resolve_probability_level(probability_level)
    lat, lon, mag = float(body["lat"]), float(body["lon"]), float(body["mag"])
    rake = float(body.get("rake", 0.0))
    strike = _optional_float(body.get("strike"))
    dip = _optional_float(body.get("dip"))
    ztor_km = _optional_float(body.get("ztor_km"))

    scenario_id = manual_scenario_id(lat, lon, mag, rake, strike, dip, ztor_km, probability_level)
    if (cached := _cached_response(scenario_id)) is not None:
        return cached

    t_rupture = time.monotonic()
    rupture = from_manual_input(
        lat=lat, lon=lon, mag=mag, rake=rake, strike=strike, dip=dip, ztor_km=ztor_km
    )
    return _run_and_respond(rupture, probability_level, scenario_id, time.monotonic() - t_rupture)


def _cached_response(scenario_id: str) -> dict | None:
    """This content-addressed id's stored response (scenario_id.py), if the
    cache is on and one exists -- checked before the rupture is even
    built, so a hit never pays for engine/hazardlib's import or any
    compute. `response.json` is written last in `_run_and_respond` (after
    the tile-join results), so its presence implies the tiles Lambda can
    serve this id too; both expire together under ResultsBucket's
    lifecycle rule, which doubles as the cache's TTL."""
    if not cache_enabled() or RESULTS_BUCKET is None:
        return None
    # Deferred import: keeps boto3 out of the cold path of routes that
    # never reach a scenario (see _run_and_respond's own comment).
    from tiles.results_store import read_response

    payload = read_response(RESULTS_BUCKET, scenario_id)
    if payload is None:
        return None
    print(f"scenario: cache hit {scenario_id}")
    return _response(200, {**payload, "cached": True})


def _run_and_respond(
    rupture: Rupture, probability_level: str, scenario_id: str, rupture_seconds: float = 0.0
) -> dict:
    # Imported here, not at module level: engine.py -> ground_motion.py
    # imports openquake.hazardlib directly, which drags in numpy/scipy/
    # numba (multi-second cold-start cost, confirmed against the real
    # deployed Lambda -- /faults and /buildings/{id} cold starts ran 7-9s+
    # even though neither route's own code touches physics at all, purely
    # from this module-level import chain). Only the two scenario routes
    # that reach this function actually need it. Kept lazy deliberately
    # (ADR-0021): Init already runs ~7s of Lambda's hard 10s Init cap, and
    # overrunning it re-runs the whole Init inside the first request. In
    # practice from_fault/from_manual_input (surface.py) import hazardlib
    # before this line; its numba JIT is prebuilt (numba_cache.py).
    t0 = time.monotonic()
    from .engine import summarize_scenario
    from .ground_motion import estimate_significant_distance_km

    t_import = time.monotonic()
    level_params = resolve_probability_level(probability_level)
    radius_km = estimate_significant_distance_km(
        rupture, sigma_multiplier=level_params.sigma_multiplier
    )
    summary = summarize_scenario(
        rupture,
        BUILDINGS_PATH,
        EXPOSURE_PATH,
        FRAGILITY_PATH,
        max_distance_km=radius_km,
        sigma_multiplier=level_params.sigma_multiplier,
        damage_percentile=level_params.damage_percentile,
    )
    t_compute = time.monotonic()
    municipality_stats = summary.municipalities.stats()

    # The response itself: everything the frontend needs, and nothing
    # per-building -- building and debris damage reach the map through the
    # tiles Lambda's joins against the results written below (ADR-0019),
    # so the response stays a few hundred KB at most (municipality_stats
    # is bounded by ~8,200 municipalities) and always fits inline under a
    # Function URL's 6MB BUFFERED cap.
    payload = {
        "scenario_id": scenario_id,
        "rupture": {
            "lat": rupture.lat,
            "lon": rupture.lon,
            "mag": rupture.mag,
            "source": rupture.source,
            "finite_rupture": rupture.surface is not None,
            "probability_level": probability_level,
        },
        "evaluated_region": evaluated_region(rupture, radius_km),
        "n_evaluated": summary.n_evaluated,
        "n_damaged": summary.n_damaged,
        "municipality_stats": municipality_stats,
    }

    if RESULTS_BUCKET is not None:
        # Deferred import, same reasoning as engine/ground_motion above --
        # keeps boto3 out of the cold-path routes that never reach this
        # function. Writes the same layout local dev's results_store.py
        # writes to local disk, so the tiles Lambda (services/tiles) can
        # read a prod scenario's results the same way it reads a local one.
        # tiles.results_store.write_buildings takes plain Python columns
        # (`to_pydict()`), not an Arrow table -- that package has to stay
        # free of pandas/pyarrow to fit Lambda's 250MB zip-package limit
        # (tiles.scenario_results documents the file it writes).
        from tiles.results_store import (
            init_scenario,
            write_buildings,
            write_municipality_stats,
            write_response,
        )

        init_scenario(RESULTS_BUCKET, scenario_id)
        write_municipality_stats(RESULTS_BUCKET, scenario_id, municipality_stats)
        write_buildings(RESULTS_BUCKET, scenario_id, summary.shipped.to_pydict())
        # Last: marks this id as a complete, reusable result (the cache).
        write_response(RESULTS_BUCKET, scenario_id, payload)

    # Per-stage timings, so a slow request in CloudWatch says where its
    # time went. `rupture` covers building the rupture surface, which is
    # where hazardlib (and its numba JIT, numba_cache.py) first gets
    # imported in a fresh execution environment -- ~65s per new container
    # before the image shipped a numba cache; `import` is then ~0.
    t_end = time.monotonic()
    print(
        f"scenario: computed {scenario_id} {rupture.source} {probability_level}: "
        f"{summary.n_evaluated} evaluated, {summary.shipped.num_rows} shipped; "
        f"rupture {rupture_seconds:.1f}s, import {t_import - t0:.1f}s, first batch {summary.seconds_to_first_batch:.1f}s, "
        f"compute {t_compute - t_import:.1f}s, "
        f"write {t_end - t_compute:.1f}s"
    )
    return _response(200, {**payload, "cached": False})


def _response(status_code: int, body: dict) -> dict:
    # Gzipped + base64 (Lambda Function URL requires base64 for a binary
    # body): /faults' trace geometries and a nationwide scenario's
    # municipality_stats are repetitive JSON that compresses well, same
    # rationale as local.py's GZipMiddleware.
    raw = json.dumps(body).encode("utf-8")
    compressed = gzip.compress(raw)
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Content-Encoding": "gzip"},
        "body": base64.b64encode(compressed).decode("ascii"),
        "isBase64Encoded": True,
    }
