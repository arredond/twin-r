"""S3-backed scenario results store -- the cloud counterpart of
services/scenario/results_store.py's local-disk version. Same key layout
(status.json, municipality_stats.json, buildings.json under
`<scenario_id>/` in the results bucket) so services/scenario/handler.py
(writes, after computing a scenario) and this package's own handler.py
(reads, per tile request) agree on where a scenario's results live without
either one hardcoding the other's paths.

`buildings.json.gz`, not `.parquet`: this module is imported by the tiles
Lambda (services/tiles/handler.py), which has to stay under Lambda's
250MB zip-package size limit -- confirmed by a real deploy failure
("Unzipped size must be smaller than 262144000 bytes") with pandas/pyarrow
in the dependency closure (pyarrow alone is ~155MB unzipped), so this
module never needs pandas/pyarrow/numpy at all -- `write_buildings` takes
a plain list of dicts (the caller, services/scenario/handler.py, already
has a DataFrame and does `.to_dict(orient="records")` itself before
calling in, rather than this shared module importing pandas just to
accept one either way). Gzipped because plain JSON, tried first, measured
~5-7x larger than the parquet it replaced (real S3 storage/transfer
bloat) -- gzip closes that gap almost entirely (parquet-sized or smaller)
for a decompress cost still in the tens of milliseconds even at 400k
rows. `municipality_stats.json` stays uncompressed -- at most ~8,200
municipalities nationwide, small regardless of format.

`boto3` isn't a project dependency on purpose (matches services/scenario/
handler.py's own convention) -- every Lambda Python runtime bundles it
already, zip-packaged or not; this module is only ever imported inside a
Lambda handler, never during local dev.
"""

from __future__ import annotations

import gzip
import json
import time
from functools import lru_cache

import boto3  # pyrefly: ignore -- Lambda-runtime-provided, unresolvable for local type checking


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


def write_buildings(bucket: str, scenario_id: str, buildings: list[dict]) -> None:
    raw = json.dumps(buildings).encode("utf-8")
    _client().put_object(
        Bucket=bucket,
        Key=_key(scenario_id, "buildings.json.gz"),
        Body=gzip.compress(raw),
        ContentType="application/json",
        ContentEncoding="gzip",
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
        obj = s3.get_object(Bucket=bucket, Key=_key(scenario_id, "buildings.json.gz"))
    except s3.exceptions.NoSuchKey as e:
        raise FileNotFoundError(f"no results for scenario_id {scenario_id!r}") from e
    rows = json.loads(gzip.decompress(obj["Body"].read()))
    # keep-last: building_id isn't always unique in the pipeline output
    # (see DATA-SOURCES.md's "non-unique building_id" known issue) --
    # later rows overwriting earlier ones in this loop matches
    # pandas' drop_duplicates(keep="last"), not a new decision.
    return {
        row["building_id"]: {k: v for k, v in row.items() if k != "building_id"} for row in rows
    }
