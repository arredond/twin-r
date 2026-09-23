"""Writes a scenario's incremental results to `results/<scenario_id>/`,
mirroring the S3 layout the cloud deployment will eventually use (local dev
only for now -- see docs/decisions for the plan). Kept in its own module
because both local.py and, later, handler.py's async path need it.

Compute is still synchronous end to end (no background job yet -- that's
the next step once this on-disk shape is validated locally): each `write_*`
call below just persists a stage's output right after computing it, so the
frontend can start polling `status.json` and consuming
`municipality_stats.json`/`buildings.parquet` incrementally even though, for
now, all three land in quick succession within the same request.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pandas as pd

RESULTS_DIR = Path(os.environ.get("TWIN_R_RESULTS_DIR", "results"))


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


def write_buildings(scenario_id: str, buildings: pd.DataFrame) -> None:
    path = scenario_dir(scenario_id) / "buildings.parquet"
    buildings.to_parquet(path, index=False)
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
