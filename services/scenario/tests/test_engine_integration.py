"""Integration test against real pipeline outputs, if present locally.

Skipped in CI/fresh checkouts where nobody has run the exposure/fragility
pipelines yet -- this is a local sanity check, not a substitute for the
unit tests in test_damage.py / test_ground_motion.py, which don't need any
data files. See test_fault_scenario_performance.py for the heavier,
real-scale/timing version of this same idea.
"""

from pathlib import Path

import pytest
from scenario.engine import run_scenario
from scenario.rupture import Rupture

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
BUILDINGS_GLOB = str(DATA_DIR / "exposure" / "parts" / "*.buildings.parquet")
EXPOSURE = DATA_DIR / "exposure" / "exposure.parquet"
FRAGILITY = DATA_DIR / "fragility" / "fragility.parquet"

pytestmark = pytest.mark.skipif(
    not ((DATA_DIR / "exposure" / "parts").is_dir() and EXPOSURE.exists() and FRAGILITY.exists()),
    reason="run pipelines/exposure.region_cli and pipelines/fragility locally first",
)


def test_scenario_runs_against_real_data_near_lorca():
    # Lorca (municipality 30024) is part of the consolidated national
    # dataset now, not its own separate directory -- an explicit
    # max_distance_km keeps this the same light/fast sanity check it
    # always was (only Lorca's own buildings get pulled from the parquet
    # row-group stats pre-filter, see ADR-0006) rather than letting the
    # default radius-from-magnitude search the whole national glob, which
    # is exactly what test_fault_scenario_performance.py already covers.
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    result = run_scenario(
        rupture, BUILDINGS_GLOB, str(EXPOSURE), str(FRAGILITY), max_distance_km=15.0
    )

    assert len(result) > 25000  # Lorca alone is ~27,884 buildings
    assert set(result["damage_state"]) <= {"None", "Slight", "Moderate", "Extensive", "Complete"}
    prob_cols = ["prob_none", "prob_slight", "prob_moderate", "prob_extensive", "prob_complete"]
    row_sums = result[prob_cols].sum(axis=1)
    assert (row_sums.sub(1.0).abs() < 1e-6).all()


def test_streamed_summary_matches_the_full_result():
    # summarize_scenario (the API path) reduces each streamed batch as it
    # goes; forcing many small batches here must still give exactly what
    # aggregating run_scenario's full per-building result gives.
    from scenario.engine import summarize_scenario
    from scenario.response import (
        compute_municipality_stats,
        count_damaged,
        prepare_response_buildings,
    )

    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    args = (rupture, BUILDINGS_GLOB, str(EXPOSURE), str(FRAGILITY))
    full = run_scenario(*args, max_distance_km=15.0)
    summary = summarize_scenario(*args, max_distance_km=15.0, batch_rows=4_000)

    assert summary.n_evaluated == len(full)
    assert summary.n_damaged == count_damaged(full)
    assert summary.municipalities.stats() == compute_municipality_stats(full)
    shipped = prepare_response_buildings(full)
    assert sorted(summary.shipped.column("building_id").to_pylist()) == sorted(
        shipped["building_id"]
    )
