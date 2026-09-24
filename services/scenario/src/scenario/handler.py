"""AWS Lambda handler for the scenario function.

Thin adapter only (see docs/decisions/0001-compute-and-iac.md) -- all
domain logic lives in engine.py/rupture.py/ground_motion.py/damage.py/
faults.py/building_lookup.py, shared with the local dev server in
local.py. Mirrors local.py's four routes (`/scenarios/manual`, `/faults`,
`/scenarios/fault`, `/buildings/{id}`) plus `/health`.

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
import uuid

from .building_lookup import get_building
from .faults import get_fault, load_nearby_faults
from .probability_level import resolve_probability_level
from .response import compute_municipality_stats, prepare_response_buildings
from .rupture import Rupture, from_fault, from_manual_input

# `engine`/`ground_motion` are deliberately NOT imported at module level --
# see _run_and_respond's own comment for why.

# buildings.parquet is a partitioned glob, not a single combined file --
# see local.py's own comment on this same pair of defaults for why.
BUILDINGS_PATH = os.environ.get("TWINER_BUILDINGS_PATH", "data/exposure/parts/*.buildings.parquet")
EXPOSURE_PATH = os.environ.get("TWINER_EXPOSURE_PATH", "data/exposure/exposure.parquet")
FRAGILITY_PATH = os.environ.get("TWINER_FRAGILITY_PATH", "data/fragility/fragility.parquet")
FAULTS_PATH = os.environ.get("TWINER_FAULTS_PATH", "data/faults/qafi_faults.parquet")
RESULTS_BUCKET = os.environ.get("TWINER_RESULTS_BUCKET")  # unset -> return inline

# Default reference point when a fault-mode request doesn't include
# near_lat/near_lon -- see local.py's DEFAULT_LAT/DEFAULT_LON.
DEFAULT_LAT, DEFAULT_LON = 40.4168, -3.7038


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
            return _list_faults(query)

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


def _list_faults(query: dict) -> dict:
    """Mirrors local.py's GET /faults -- feeds the frontend's "Automatic"
    fault picker sidebar. See that route's docstring for why the default
    radius is generous."""
    lat = float(query.get("lat", DEFAULT_LAT))
    lon = float(query.get("lon", DEFAULT_LON))
    radius_km = float(query.get("radius_km", 3000.0))
    faults = load_nearby_faults(FAULTS_PATH, lat, lon, radius_km)
    return _response(200, {"faults": faults.to_dict(orient="records")})


def _building_info(building_id: str) -> dict:
    """Mirrors local.py's GET /buildings/{building_id}."""
    row = get_building(EXPOSURE_PATH, building_id)
    if row is None:
        return _response(404, {"error": f"building_id {building_id!r} not found"})
    return _response(200, row.to_dict())


def _fault_scenario(query: dict) -> dict:
    near_lat = float(query.get("near_lat", DEFAULT_LAT))
    near_lon = float(query.get("near_lon", DEFAULT_LON))
    fault = get_fault(FAULTS_PATH, query["fault_id"], near_lat, near_lon)
    rupture = from_fault(
        fault_id=fault["fault_id"],
        name=fault["name"],
        point_lat=fault["lat"],
        point_lon=fault["lon"],
        mmax=fault["mmax"],
        rake=fault["rake"],
        geometry_geojson=fault["geometry_geojson"],
        dip=fault["dip"],
        min_depth_km=fault["min_depth_km"],
        max_depth_km=fault["max_depth_km"],
    )
    probability_level = query.get("probability_level", "high")
    return _run_and_respond(rupture, probability_level)


def _manual_scenario(body: dict) -> dict:
    rupture = from_manual_input(
        lat=float(body["lat"]),
        lon=float(body["lon"]),
        mag=float(body["mag"]),
        rake=float(body.get("rake", 0.0)),
        strike=float(body["strike"]) if "strike" in body else None,
        dip=float(body["dip"]) if "dip" in body else None,
        ztor_km=float(body["ztor_km"]) if "ztor_km" in body else None,
    )
    probability_level = body.get("probability_level", "high")
    return _run_and_respond(rupture, probability_level)


def _run_and_respond(rupture: Rupture, probability_level: str) -> dict:
    # Imported here, not at module level: engine.py -> ground_motion.py
    # imports openquake.hazardlib directly, which drags in numpy/scipy/
    # numba (multi-second cold-start cost, confirmed against the real
    # deployed Lambda -- /faults and /buildings/{id} cold starts ran 7-9s+
    # even though neither route's own code touches physics at all, purely
    # from this module-level import chain). Only the two scenario routes
    # that reach this function actually need it.
    from .engine import run_scenario
    from .ground_motion import estimate_significant_distance_km

    level_params = resolve_probability_level(probability_level)
    radius_km = estimate_significant_distance_km(
        rupture, sigma_multiplier=level_params.sigma_multiplier
    )
    result = run_scenario(
        rupture,
        BUILDINGS_PATH,
        EXPOSURE_PATH,
        FRAGILITY_PATH,
        max_distance_km=radius_km,
        sigma_multiplier=level_params.sigma_multiplier,
        damage_percentile=level_params.damage_percentile,
    )
    n_evaluated = len(result)
    municipality_stats = compute_municipality_stats(result)
    result = prepare_response_buildings(result)

    # Random for now, same "not yet content-addressed" caveat as local.py's
    # own scenario_id -- see that module's comment on the future plan to
    # hash scenario characteristics + data/pipeline version into it instead.
    scenario_id = uuid.uuid4().hex
    if RESULTS_BUCKET is not None:
        # Deferred import, same reasoning as engine/ground_motion above --
        # keeps boto3 out of the cold-path routes that never reach this
        # function. Writes the same status.json/municipality_stats.json/
        # buildings.json layout local dev's results_store.py writes to
        # local disk, so the tiles Lambda (services/tiles) can read a prod
        # scenario's results the same way it reads a local one.
        # tiles.results_store.write_buildings takes a plain list of dicts,
        # not a DataFrame -- that module has to stay free of pandas/pyarrow
        # to fit Lambda's 250MB zip-package limit (see its own docstring),
        # so the DataFrame -> records conversion happens here instead,
        # where pandas is already a dependency regardless.
        from tiles.results_store import init_scenario, write_buildings, write_municipality_stats

        init_scenario(RESULTS_BUCKET, scenario_id)
        write_municipality_stats(RESULTS_BUCKET, scenario_id, municipality_stats)
        write_buildings(RESULTS_BUCKET, scenario_id, result.to_dict(orient="records"))

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
        "evaluated_region": {"lat": rupture.lat, "lon": rupture.lon, "radius_km": radius_km},
        "buildings": result.to_dict(orient="records"),
        "n_evaluated": n_evaluated,
        "municipality_stats": municipality_stats,
    }

    if RESULTS_BUCKET is None:
        return _response(200, payload)

    return _response(200, _write_large_payload_to_s3(payload, scenario_id))


def _write_large_payload_to_s3(payload: dict, scenario_id: str) -> dict:
    # Not a project dependency on purpose (ADR-0001): the Lambda Python
    # runtime bundles boto3 already, so we don't ship/pin it ourselves.
    # Unresolvable for local type checking as a result.
    import boto3  # pyrefly: ignore

    # Keyed by the same scenario_id as the results_store.py writes above
    # (not an independent uuid) -- one id per scenario run, not two, makes
    # tracing a request through CloudWatch/S3 straightforward.
    key = f"scenarios/{scenario_id}.json"
    # `endpoint_url` matters specifically for `generate_presigned_url`
    # below: boto3's default S3 client signs presigned URLs against the
    # *global* `s3.amazonaws.com` endpoint regardless of `region_name`,
    # which opt-in regions like eu-south-2 reject outright
    # (IllegalLocationConstraintException) -- confirmed against the real
    # bucket. AWS_REGION is always set by the Lambda runtime itself, not
    # something this code sets.
    region = os.environ["AWS_REGION"]
    s3 = boto3.client("s3", region_name=region, endpoint_url=f"https://s3.{region}.amazonaws.com")
    s3.put_object(
        Bucket=RESULTS_BUCKET,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    # A presigned HTTPS URL, not the raw `s3://...` URI -- the browser
    # can't resolve an s3:// scheme at all, and the results bucket is
    # otherwise private (no public-read policy, unlike the data bucket --
    # ADR-0016 -- since scenario results aren't meant to be broadly
    # public). 5 minutes is generous for the frontend to fetch this right
    # after receiving the response; it's a throwaway result either way
    # (ResultsBucket's own 30-day lifecycle rule).
    result_url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": RESULTS_BUCKET, "Key": key},
        ExpiresIn=300,
    )
    # scenario_id isn't repeated at this outer level -- scenarioApi.ts's
    # resolveScenarioResult only ever reads `result_url` off this wrapper
    # and returns the *fetched* JSON (which already has scenario_id, set
    # on `payload` above) as the real ScenarioResult.
    return {"result_url": result_url}


def _response(status_code: int, body: dict) -> dict:
    # Gzipped + base64 (Lambda Function URL requires base64 for a binary
    # body): the `buildings` payload is repetitive JSON that compresses
    # well, same rationale as local.py's GZipMiddleware. Also matters more
    # here than in local dev -- a Function URL's default BUFFERED invoke
    # mode caps responses at 6MB, and an uncompressed large-fault payload
    # can run well past that.
    raw = json.dumps(body).encode("utf-8")
    compressed = gzip.compress(raw)
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json", "Content-Encoding": "gzip"},
        "body": base64.b64encode(compressed).decode("ascii"),
        "isBase64Encoded": True,
    }
