"""S3-backed scenario results store -- the cloud counterpart of
services/scenario/results_store.py's local-disk version. Same key layout
(status.json, municipality_stats.json, buildings.parquet under
`<scenario_id>/` in the results bucket) so services/scenario/handler.py
(writes, after computing a scenario) and this package's own handler.py
(reads, per tile request) agree on where a scenario's results live without
either one hardcoding the other's paths.

`boto3` isn't a project dependency on purpose (matches services/scenario/
handler.py's own convention) -- every Lambda Python runtime bundles it
already, zip-packaged or not; this module is only ever imported inside a
Lambda handler, never during local dev.
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from io import BytesIO

import boto3  # pyrefly: ignore -- Lambda-runtime-provided, unresolvable for local type checking
import pandas as pd


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


def write_buildings(bucket: str, scenario_id: str, buildings: pd.DataFrame) -> None:
    buf = BytesIO()
    buildings.to_parquet(buf, index=False)
    _client().put_object(
        Bucket=bucket, Key=_key(scenario_id, "buildings.parquet"), Body=buf.getvalue()
    )
    _write_status(bucket, scenario_id, buildings_ready=True)


@lru_cache(maxsize=64)
def read_building_results(bucket: str, scenario_id: str) -> dict[str, dict]:
    """building_id -> the scenario's thin result row, as a plain dict of
    extra tile properties -- the S3 equivalent of services/scenario/
    tile_join.py's `_building_results`. Cached per (bucket, scenario_id)
    for this Lambda execution environment's lifetime (see this package's
    handler.py), same "don't re-fetch per tile" reasoning as local dev's
    version."""
    s3 = _client()
    try:
        obj = s3.get_object(Bucket=bucket, Key=_key(scenario_id, "buildings.parquet"))
    except s3.exceptions.NoSuchKey as e:
        raise FileNotFoundError(f"no results for scenario_id {scenario_id!r}") from e
    df = pd.read_parquet(BytesIO(obj["Body"].read()))
    # keep="last": building_id isn't always unique in the pipeline output
    # (see DATA-SOURCES.md's "non-unique building_id" known issue) -- matches
    # local dev's tile_join.py._building_results, not a new decision.
    df = df.drop_duplicates(subset="building_id", keep="last").set_index("building_id")
    return df.to_dict(orient="index")
