"""Writes a scenario's incremental results to `results/<scenario_id>/`,
mirroring the S3 layout the cloud deployment will eventually use (local dev
only for now -- see docs/decisions for the plan). Kept in its own module
because both local.py and, later, handler.py's async path need it.

Compute is still synchronous end to end (no background job yet -- that's
the next step once this on-disk shape is validated locally): each `write_*`
call below just persists a stage's output right after computing it, so the
frontend can start polling `status.json` and consuming
`municipality_stats.json`/`buildings.json` incrementally even though, for
now, all three land in quick succession within the same request.

`buildings.json.gz` (not `.parquet`, despite `write_buildings` taking a
DataFrame): the tiles Lambda that reads this file back
(`services/tiles/results_store.py`) has to stay under Lambda's 250MB
zip-package size limit, and `pyarrow` alone (needed for `pd.read_parquet`)
is ~155MB unzipped -- confirmed by a real deploy failure ("Unzipped size
must be smaller than 262144000 bytes"). Plain (uncompressed) JSON was
tried first, but measured ~5-7x larger than the parquet it replaced
(3.6MB vs 762KB at 20k rows; 72.7MB vs 10.5MB at 400k) -- real S3
storage/transfer bloat, not just a theoretical concern. Gzipping closes
that gap almost entirely (590KB/11.8MB for the same two sizes, roughly
parquet-sized or smaller) for a decompress+parse cost still measured in
the tens of milliseconds even at 400k rows. `municipality_stats.json`
stays uncompressed -- at most ~8,200 municipalities nationwide, small
regardless of format.

`scenario_id` (minted in local.py/handler.py, not here) is a random UUID4
for now -- every request gets a fresh id and a fresh directory even if an
identical rupture/probability_level was already computed. Future: derive
it instead from a hash of the scenario's actual characteristics (rupture
params, probability_level) plus the exposure/fragility data version and
pipeline version, so an identical request naturally lands on the same
scenario_id and this directory (or its S3 equivalent) can be reused/cached
rather than recomputed. Not done yet -- noted so the id isn't assumed
content-addressed before that lands.
"""

from __future__ import annotations

import gzip
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
    path = scenario_dir(scenario_id) / "buildings.json.gz"
    raw = json.dumps(buildings.to_dict(orient="records")).encode("utf-8")
    path.write_bytes(gzip.compress(raw))
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
