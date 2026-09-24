"""AWS Lambda handler for tile-serving.

Deliberately its own Lambda, separate from services/scenario's compute
function (docs/decisions -- ask the user before assuming which ADR number
this becomes). That one pulls in openquake.hazardlib/numpy/scipy/fiona
(confirmed 7-9s+ cold starts even for routes that don't touch physics, see
services/scenario/handler.py's own comment on why engine.py/ground_motion.py
are imported lazily there) -- this function needs none of that, only
pmtiles/mapbox_vector_tile's protobuf schema (this package's own
tile_join.py; deliberately not pandas/pyarrow either, see
results_store.py's own docstring on the deploy-size limit that ruled
those out) -- so it stays small and fast to cold-start regardless of how
heavy the compute Lambda gets. A tile-request burst also shouldn't compete
with scenario compute for the same Lambda's concurrency/memory budget --
splitting them means each scales independently.

Serves two routes, one per archive:
- GET /tiles/{scenario_id}/{z}/{x}/{y}.mvt -- buildings.pmtiles, each
  feature extended with its scenario result (`join_tile_bytes`).
- GET /tiles/{scenario_id}/debris/{z}/{x}/{y}.mvt -- debris.pmtiles, cut
  down to each damaged building's one matching ring
  (`join_debris_tile_bytes`, ADR-0019).

Exposure/fragility/faults parquet paths come from environment variables in
services/scenario's Lambda -- this one only needs the data bucket (for
buildings.pmtiles) and the results bucket (for a scenario's per-building
results file, `scenario_results.FILENAME`, written by services/scenario/
handler.py's compute path via this package's own results_store.py), both
via env vars for the same
local/cloud parity reason.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import re

from pmtiles.reader import Compression, Reader

from .results_store import read_building_results
from .s3_pmtiles import s3_source
from .tile_join import join_debris_tile_bytes, join_tile_bytes

DATA_BUCKET = os.environ["TWINER_DATA_BUCKET"]
# Matches docs/deploy-aws-setup.md's own upload target
# (`aws s3 cp data/exposure/buildings.pmtiles s3://<DataBucketName>/tiles/buildings.pmtiles`).
BUILDINGS_PMTILES_KEY = os.environ.get("TWINER_BUILDINGS_PMTILES_KEY", "tiles/buildings.pmtiles")
DEBRIS_PMTILES_KEY = os.environ.get("TWINER_DEBRIS_PMTILES_KEY", "tiles/debris.pmtiles")
RESULTS_BUCKET = os.environ["TWINER_RESULTS_BUCKET"]

_ROUTE_RE = re.compile(
    r"^/tiles/(?P<scenario_id>[^/]+)/(?:(?P<layer>debris)/)?(?P<z>\d+)/(?P<x>\d+)/(?P<y>\d+)\.mvt$"
)

# Module-level, persisted across warm invocations of the same execution
# environment (same pattern as services/scenario/db.py's DuckDB
# connection) -- avoids re-reading the PMTiles header/root directory on
# every request from a warm container, same rationale as local dev's
# tile_join.py's own `_pmtiles_reader` cache, just without the lru_cache
# wrapper since there are only ever these two archives to read here.
_READERS: dict[str, Reader] = {}


def _reader(key: str) -> Reader:
    if key not in _READERS:
        _READERS[key] = Reader(s3_source(DATA_BUCKET, key))
    return _READERS[key]


def handler(event: dict, context) -> dict:
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    # Collapse repeated slashes -- same rationale as services/scenario/
    # handler.py's own routing (a Function URL's trailing-slash base).
    path = re.sub(r"/+", "/", event.get("rawPath") or "/")

    if method == "GET" and path == "/health":
        return _response(200, {"status": "ok"})

    match = _ROUTE_RE.match(path) if method == "GET" else None
    if not match:
        return _response(404, {"error": f"no route for {method} {path}"})

    scenario_id = match["scenario_id"]
    z, x, y = int(match["z"]), int(match["x"]), int(match["y"])

    is_debris = match["layer"] == "debris"
    reader = _reader(DEBRIS_PMTILES_KEY if is_debris else BUILDINGS_PMTILES_KEY)
    raw = reader.get(z, x, y)
    if raw is None:
        # No base-tile data at this z/x/y (e.g. open ocean) -- a 204, not a
        # 404: the scenario_id may well be valid, this tile is just
        # legitimately empty, same as buildings.pmtiles itself would return.
        return {"statusCode": 204, "headers": {}, "body": "", "isBase64Encoded": False}

    header = reader.header()
    if header["tile_compression"] == Compression.GZIP:
        raw = gzip.decompress(raw)

    try:
        results = read_building_results(RESULTS_BUCKET, scenario_id)
    except FileNotFoundError as e:
        return _response(404, {"error": str(e)})

    tile = (join_debris_tile_bytes if is_debris else join_tile_bytes)(raw, results)
    return _binary_response(200, tile, "application/vnd.mapbox-vector-tile")


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
        "isBase64Encoded": False,
    }


def _binary_response(status_code: int, body: bytes, content_type: str) -> dict:
    # Gzipped + base64, same as services/scenario/handler.py's own
    # `_response` -- a Function URL's default BUFFERED invoke mode caps
    # responses at 6MB, and an uncompressed dense-tile response can run
    # well past what an uncompressed one would take.
    compressed = gzip.compress(body)
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": content_type, "Content-Encoding": "gzip"},
        "body": base64.b64encode(compressed).decode("ascii"),
        "isBase64Encoded": True,
    }
