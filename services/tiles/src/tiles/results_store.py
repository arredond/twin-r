"""S3-backed scenario results store -- the cloud counterpart of
services/scenario/results_store.py's local-disk version. Same key layout
(status.json, municipality_stats.json, the per-building results file
(`scenario_results.FILENAME`), response.json
under `<scenario_id>/` in the results bucket) so services/scenario/handler.py
(writes, after computing a scenario) and this package's own handler.py
(reads, per tile request) agree on where a scenario's results live without
either one hardcoding the other's paths.

The per-building results file (`scenario_results.FILENAME`) has its own
module, `scenario_results`, which defines its format and explains why it's
column-oriented JSON: the tiles Lambda reads it and must stay under
Lambda's 250MB zip-package limit, so neither module may use
pandas/pyarrow/numpy (a real deploy failed with pandas/pyarrow in the
closure: "Unzipped size must be smaller than 262144000 bytes").
`municipality_stats.json` stays uncompressed -- at most ~8,200
municipalities nationwide, small regardless of format.

`boto3` isn't a project dependency on purpose (matches services/scenario/
handler.py's own convention) -- every Lambda Python runtime bundles it
already, zip-packaged or not; this module is only ever imported inside a
Lambda handler, never during local dev.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from typing import Any

import boto3  # pyrefly: ignore -- Lambda-runtime-provided, unresolvable for local type checking

from . import scenario_results
from .results_cache import ResultsCache
from .scenario_results import ScenarioResults


def _client():
    return boto3.client("s3")


def _key(scenario_id: str, filename: str) -> str:
    return f"{scenario_id}/{filename}"


def _write_status(bucket: str, scenario_id: str, **fields: object) -> None:
    s3 = _client()
    key = _key(scenario_id, "status.json")
    status: dict = {}
    try:
        status = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        pass
    status.update(fields)
    status["updated_at"] = time.time()
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(status).encode("utf-8"),
        ContentType="application/json",
    )


def init_scenario(bucket: str, scenario_id: str) -> None:
    _write_status(
        bucket, scenario_id, municipal_stats_ready=False, buildings_ready=False, debris_ready=False
    )


def write_municipality_stats(bucket: str, scenario_id: str, stats: list[dict]) -> None:
    _client().put_object(
        Bucket=bucket,
        Key=_key(scenario_id, "municipality_stats.json"),
        Body=json.dumps(stats).encode("utf-8"),
        ContentType="application/json",
    )
    _write_status(bucket, scenario_id, municipal_stats_ready=True)


def write_buildings(bucket: str, scenario_id: str, columns: Mapping[str, Any]) -> None:
    """`columns`: the listed buildings, one column per
    `scenario_results.COLUMNS` entry, already sorted and unique by
    building_id (scenario.response.stored_results_columns). Any sliceable
    column works, e.g. pyarrow arrays, without this module importing
    pyarrow (see `scenario_results.encode_sorted_unique`)."""
    _client().put_object(
        Bucket=bucket,
        Key=_key(scenario_id, scenario_results.FILENAME),
        Body=scenario_results.encode_sorted_unique(columns),
        ContentType="application/json",
        ContentEncoding="gzip",
    )
    _write_status(bucket, scenario_id, buildings_ready=True)


def write_response(bucket: str, scenario_id: str, payload: dict) -> None:
    """The scenario's full API response, stored as the scenario cache's
    entry (services/scenario/scenario_id.py). Write it last: its presence
    is what marks `<scenario_id>/` as a complete, reusable result. Never
    read by the browser -- the scenario Lambda returns it inline, on a
    cache hit via `read_response`."""
    _client().put_object(
        Bucket=bucket,
        Key=_key(scenario_id, "response.json"),
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )


def read_response(bucket: str, scenario_id: str) -> dict | None:
    """The stored response for this id, or None if there isn't one."""
    s3 = _client()
    try:
        obj = s3.get_object(Bucket=bucket, Key=_key(scenario_id, "response.json"))
    except s3.exceptions.NoSuchKey:
        return None
    return json.loads(obj["Body"].read())


# Bounded by memory, not by count: one scenario's decoded results range
# from a few MB to 1.34GB (see results_cache.py). Lives as long as this
# Lambda execution environment, so a container reads each scenario's file
# once, not once per tile.
_RESULTS_CACHE = ResultsCache()


def read_building_results(bucket: str, scenario_id: str) -> ScenarioResults:
    """The scenario's listed buildings, looked up by building_id per tile
    (`ScenarioResults.get`) -- the S3 equivalent of services/scenario/
    tile_join.py's `_building_results`."""

    def fetch() -> tuple[int, Callable[[], bytes]]:
        s3 = _client()
        try:
            obj = s3.get_object(Bucket=bucket, Key=_key(scenario_id, scenario_results.FILENAME))
        except s3.exceptions.NoSuchKey as e:
            raise FileNotFoundError(f"no results for scenario_id {scenario_id!r}") from e
        # The size is known before the body is read, so the cache can make
        # room before anything is decoded.
        return obj["ContentLength"], obj["Body"].read

    key = (bucket, scenario_id)
    if (cached := _RESULTS_CACHE.get(key)) is not None:
        return cached
    size, read = fetch()
    return _RESULTS_CACHE.get_or_load(key, size, read)
