"""Writes a scenario's incremental results to `results/<scenario_id>/`,
mirroring the S3 layout the cloud deployment will eventually use (local dev
only for now -- see docs/decisions for the plan). Kept in its own module
because both local.py and, later, handler.py's async path need it.

Compute is still synchronous end to end (no background job yet -- that's
the next step once this on-disk shape is validated locally): each `write_*`
call below just persists a stage's output right after computing it, so the
frontend can start polling `status.json` and consuming
`municipality_stats.json`/the per-building results incrementally even though, for
now, all three land in quick succession within the same request.

The per-building results file (`scenario_results.FILENAME`) is shared
with the cloud: the format, and why it's column-oriented JSON rather than
parquet, are documented in services/tiles/src/tiles/scenario_results.py
(ADR-0023), the one module that encodes and decodes it for both.
`municipality_stats.json` stays uncompressed -- at most ~8,200
municipalities nationwide, small regardless of format.

`scenario_id` (minted in local.py, not here) is content-addressed
(scenario_id.py): an identical request lands on the same directory.
`response.json.gz` -- the full response payload, always written last, once
every other file above is in place -- is what marks a directory as a
complete, reusable result (`read_response`). It's written whether or not
the scenario cache is on: with the cache off, a rerun overwrites every file
here in place, so a directory never pairs one run's response with another
run's buildings.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from tiles import scenario_results

RESULTS_DIR = Path(os.environ.get("TWINER_RESULTS_DIR", "results"))


def scenario_dir(scenario_id: str) -> Path:
    return RESULTS_DIR / scenario_id


def _write_status(scenario_id: str, **fields: object) -> None:
    path = scenario_dir(scenario_id) / "status.json"
    status = {}
    if path.exists():
        status = json.loads(path.read_text())
    status.update(fields)
    status["updated_at"] = time.time()
    path.write_text(json.dumps(status))


def init_scenario(scenario_id: str) -> None:
    scenario_dir(scenario_id).mkdir(parents=True, exist_ok=True)
    _write_status(
        scenario_id,
        municipal_stats_ready=False,
        buildings_ready=False,
        debris_ready=False,
    )


def write_municipality_stats(scenario_id: str, stats: list[dict]) -> None:
    path = scenario_dir(scenario_id) / "municipality_stats.json"
    path.write_text(json.dumps(stats))
    _write_status(scenario_id, municipal_stats_ready=True)


def write_buildings(scenario_id: str, columns: Mapping[str, Any]) -> None:
    """`columns`: the listed buildings, one column per
    `scenario_results.COLUMNS` entry, already sorted and unique by
    building_id (response.stored_results_columns)."""
    path = scenario_dir(scenario_id) / scenario_results.FILENAME
    path.write_bytes(scenario_results.encode_sorted_unique(columns))
    _write_status(scenario_id, buildings_ready=True)


def read_status(scenario_id: str) -> dict | None:
    path = scenario_dir(scenario_id) / "status.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def read_municipality_stats(scenario_id: str) -> list[dict] | None:
    path = scenario_dir(scenario_id) / "municipality_stats.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_response(scenario_id: str, payload: dict) -> None:
    """The full scenario response, for the cache (scenario_id.py). Written
    on every run, cache on or off (see module docstring). Call last: its presence is what `read_response` treats as "this directory
    holds a complete result"."""
    path = scenario_dir(scenario_id) / "response.json.gz"
    path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))


def read_response(scenario_id: str) -> dict | None:
    """A previously stored full response, or None if there isn't a complete
    one (never computed, or computed with the cache off). Also None if the
    tile-join results it depends on have gone missing, so a hit can never
    hand out a scenario_id whose /tiles/ requests would 404."""
    d = scenario_dir(scenario_id)
    path = d / "response.json.gz"
    if not path.exists() or not (d / scenario_results.FILENAME).exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()))
