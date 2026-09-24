"""The Lambda adapter (handler.py) against the same synthetic fixtures as
test_local_api.py, with an in-memory stand-in for S3 -- boto3 isn't a
project dependency (the Lambda runtime bundles it, ADR-0001), so a fake
module is installed in its place. Covers what only the deployed path does:
its own routing, the result_url wrapper, and the S3-backed scenario cache.
"""

from __future__ import annotations

import base64
import gzip
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

# Sibling test module (pytest puts tests/ on sys.path) -- reuses its
# synthetic data fixture rather than duplicating it.
from test_local_api import data_dir  # noqa: F401  # pyrefly: ignore

BUCKET = "results-bucket"
_FRESH_MODULES = ("tiles.results_store", "scenario.handler")


class _ClientError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _NoSuchKey(_ClientError):
    def __init__(self):
        super().__init__("NoSuchKey")


class FakeS3:
    exceptions = types.SimpleNamespace(ClientError=_ClientError, NoSuchKey=_NoSuchKey)

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _NoSuchKey()
        return {"Body": types.SimpleNamespace(read=lambda: self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise _ClientError("404")
        return {}

    def generate_presigned_url(self, _op, Params, ExpiresIn):
        return f"https://signed.example/{Params['Bucket']}/{Params['Key']}?ttl={ExpiresIn}"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    fake = FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=lambda *a, **k: fake))
    return fake


@pytest.fixture
def handler(data_dir: Path, s3: FakeS3, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    monkeypatch.setenv(
        "TWINER_BUILDINGS_PATH", str(data_dir / "exposure/parts/*.buildings.parquet")
    )
    monkeypatch.setenv("TWINER_EXPOSURE_PATH", str(data_dir / "exposure/exposure.parquet"))
    monkeypatch.setenv("TWINER_FRAGILITY_PATH", str(data_dir / "fragility/fragility.parquet"))
    monkeypatch.setenv("TWINER_FAULTS_PATH", str(data_dir / "faults/qafi_faults.parquet"))
    monkeypatch.setenv("TWINER_RESULTS_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_REGION", "eu-south-2")
    monkeypatch.setenv("TWINER_SCENARIO_CACHE", "1")

    # Fresh imports against the fake boto3 / the env above, and dropped
    # again afterwards -- both modules bind boto3 (and handler.py its path
    # constants) at import time, so neither may leak into another test.
    _drop_modules()
    handler_module = importlib.import_module("scenario.handler")
    yield handler_module.handler
    _drop_modules()


def _drop_modules() -> None:
    for name in _FRESH_MODULES:
        sys.modules.pop(name, None)
    # `from scenario import handler` would otherwise still find the old
    # module object as an attribute of the (still-imported) package.
    import scenario
    import tiles

    for pkg, attr in [(scenario, "handler"), (tiles, "results_store")]:
        if hasattr(pkg, attr):
            delattr(pkg, attr)


def _call(handler, path: str, query: dict | None = None) -> tuple[int, dict]:
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET"}},
        "queryStringParameters": query,
    }
    resp = handler(event, None)
    body = json.loads(gzip.decompress(base64.b64decode(resp["body"])))
    return resp["statusCode"], body


def _stored_payload(s3: FakeS3, result_url: str) -> dict:
    key = result_url.split(f"/{BUCKET}/", 1)[1].split("?", 1)[0]
    return json.loads(s3.objects[(BUCKET, key)])


def test_faults_takes_no_location_args(handler):
    status, body = _call(handler, "/faults")
    assert status == 200
    assert {f["fault_id"] for f in body["faults"]} == {"TEST001", "TEST002"}


def test_fault_scenario_without_fault_id_is_a_400(handler):
    status, _ = _call(handler, "/scenarios/fault", {})
    assert status == 400


def test_fault_scenario_unknown_id_is_a_404(handler):
    status, _ = _call(handler, "/scenarios/fault", {"fault_id": "NOPE"})
    assert status == 404


def test_fault_scenario_is_computed_once_then_served_from_s3(handler, s3: FakeS3):
    status, first = _call(handler, "/scenarios/fault", {"fault_id": "TEST001"})
    assert status == 200
    assert first["cached"] is False
    payload = _stored_payload(s3, first["result_url"])
    scenario_id = payload["scenario_id"]
    # The tile-join results the tiles Lambda reads are in place too.
    assert (BUCKET, f"{scenario_id}/buildings.json.gz") in s3.objects

    # An ignored near point (TEST001 has full rupture geometry) still hits.
    status, second = _call(
        handler, "/scenarios/fault", {"fault_id": "TEST001", "near_lat": "38.5", "near_lon": "-1"}
    )
    assert status == 200
    assert second["cached"] is True
    assert _stored_payload(s3, second["result_url"])["scenario_id"] == scenario_id


def test_cache_off_recomputes(handler, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TWINER_SCENARIO_CACHE", "0")
    _, first = _call(handler, "/scenarios/fault", {"fault_id": "TEST001"})
    _, second = _call(handler, "/scenarios/fault", {"fault_id": "TEST001"})
    assert first["cached"] is False
    assert second["cached"] is False


def test_near_point_matters_only_for_a_fault_without_geometry(handler, s3: FakeS3):
    ids = set()
    for near_lon in ["-1.66", "-1.74"]:
        _, body = _call(
            handler,
            "/scenarios/fault",
            {"fault_id": "TEST002", "near_lat": "37.87", "near_lon": near_lon},
        )
        assert body["cached"] is False
        ids.add(_stored_payload(s3, body["result_url"])["scenario_id"])
    assert len(ids) == 2
