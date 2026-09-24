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
from pathlib import Path

import pandas as pd

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
    if not path.exists() or not (d / "buildings.json.gz").exists():
        return None
    return json.loads(gzip.decompress(path.read_bytes()))
