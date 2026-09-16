"""AWS Lambda handler for the scenario function.

Thin adapter only (see docs/decisions/0001-compute-and-iac.md) -- all
domain logic lives in engine.py/rupture.py/ground_motion.py/damage.py,
shared with the local dev server in local.py.

Expects a Lambda Function URL / API Gateway proxy event with a JSON body
matching ManualRuptureRequest in local.py. Exposure/fragility parquet paths
come from environment variables so the same code reads local files in dev
and S3-backed paths (via DuckDB's httpfs extension, given an S3 URI) in the
cloud.
"""

from __future__ import annotations

import base64
import gzip
import json
import os

from .engine import run_scenario
from .faults import get_fault
from .ground_motion import estimate_significant_distance_km
from .response import compute_municipality_stats, prepare_response_buildings
from .rupture import Rupture, from_fault, from_manual_input

BUILDINGS_PATH = os.environ.get("TWIN_R_BUILDINGS_PATH", "data/exposure/buildings.parquet")
EXPOSURE_PATH = os.environ.get("TWIN_R_EXPOSURE_PATH", "data/exposure/exposure.parquet")
FRAGILITY_PATH = os.environ.get("TWIN_R_FRAGILITY_PATH", "data/fragility/fragility.parquet")
FAULTS_PATH = os.environ.get("TWIN_R_FAULTS_PATH", "data/faults/qafi_faults.parquet")
MUNICIPALITIES_PATH = os.environ.get(
    "TWIN_R_MUNICIPALITIES_PATH", "data/exposure/municipalities.parquet"
)
RESULTS_BUCKET = os.environ.get("TWIN_R_RESULTS_BUCKET")  # unset -> return inline

# Default reference point when a fault-mode request doesn't include
# near_lat/near_lon -- see local.py's DEFAULT_LAT/DEFAULT_LON.
DEFAULT_LAT, DEFAULT_LON = 40.4168, -3.7038


def handler(event: dict, context) -> dict:
    query = event.get("queryStringParameters") or {}
    body = json.loads(event.get("body") or "{}")
    try:
        rupture = _build_rupture(query, body)
    except (KeyError, ValueError) as e:
        return _response(400, {"error": f"invalid rupture parameters: {e}"})

    radius_km = estimate_significant_distance_km(rupture)
    result = run_scenario(
        rupture, BUILDINGS_PATH, EXPOSURE_PATH, FRAGILITY_PATH, max_distance_km=radius_km
    )
    n_evaluated = len(result)
    municipality_stats = compute_municipality_stats(result, MUNICIPALITIES_PATH)
    result = prepare_response_buildings(result)
    payload = {
        "rupture": {
            "lat": rupture.lat,
            "lon": rupture.lon,
            "mag": rupture.mag,
            "source": rupture.source,
            "finite_rupture": rupture.surface is not None,
        },
        "evaluated_region": {"lat": rupture.lat, "lon": rupture.lon, "radius_km": radius_km},
        "buildings": result.to_dict(orient="records"),
        "n_evaluated": n_evaluated,
        "municipality_stats": municipality_stats,
    }

    if RESULTS_BUCKET is None:
        return _response(200, payload)

    return _response(200, _write_to_s3(payload))


def _build_rupture(query: dict, body: dict) -> Rupture:
    """Automatic mode (fault_id) arrives as GET query params, matching
    local.py's `/scenarios/fault` (cacheable: fault_id alone determines the
    evaluated buildings, see that route's docstring); manual mode
    (lat/lon/mag) as a POST JSON body. A Lambda Function URL has no
    path-based routing without API Gateway in front of it, so mode is
    dispatched on which of the two carries `fault_id`, not on the URL."""
    if "fault_id" in query:
        near_lat = float(query.get("near_lat", DEFAULT_LAT))
        near_lon = float(query.get("near_lon", DEFAULT_LON))
        fault = get_fault(FAULTS_PATH, query["fault_id"], near_lat, near_lon)
        return from_fault(
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
    return from_manual_input(
        lat=float(body["lat"]),
        lon=float(body["lon"]),
        mag=float(body["mag"]),
        rake=float(body.get("rake", 0.0)),
        strike=float(body["strike"]) if "strike" in body else None,
        dip=float(body["dip"]) if "dip" in body else None,
        ztor_km=float(body["ztor_km"]) if "ztor_km" in body else None,
    )


def _write_to_s3(payload: dict) -> dict:
    import uuid

    # Not a project dependency on purpose (ADR-0001): the Lambda Python
    # runtime bundles boto3 already, so we don't ship/pin it ourselves.
    # Unresolvable for local type checking as a result.
    import boto3  # pyrefly: ignore

    key = f"scenarios/{uuid.uuid4()}.json"
    boto3.client("s3").put_object(
        Bucket=RESULTS_BUCKET,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    return {"result_url": f"s3://{RESULTS_BUCKET}/{key}"}


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
