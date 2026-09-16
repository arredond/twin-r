"""Integration test against real pipeline outputs, if present locally.

Skipped in CI/fresh checkouts where nobody has run the exposure/fragility
pipelines yet -- this is a local sanity check, not a substitute for the
unit tests in test_damage.py / test_ground_motion.py, which don't need any
data files.
"""

from pathlib import Path

import pytest
from scenario.engine import run_scenario
from scenario.rupture import Rupture

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
BUILDINGS = DATA_DIR / "exposure" / "buildings.parquet"
EXPOSURE = DATA_DIR / "exposure" / "exposure.parquet"
FRAGILITY = DATA_DIR / "fragility" / "fragility.parquet"

pytestmark = pytest.mark.skipif(
    not (BUILDINGS.exists() and EXPOSURE.exists() and FRAGILITY.exists()),
    reason="run pipelines/exposure and pipelines/fragility locally first",
)


def test_scenario_runs_against_lorca_data():
    rupture = Rupture(lat=37.67, lon=-1.70, mag=6.0, rake=0.0)
    result = run_scenario(rupture, str(BUILDINGS), str(EXPOSURE), str(FRAGILITY))

    assert len(result) > 25000  # ~27,884 Lorca buildings
    assert set(result["damage_state"]) <= {"None", "Slight", "Moderate", "Extensive", "Complete"}
    prob_cols = ["prob_none", "prob_slight", "prob_moderate", "prob_extensive", "prob_complete"]
    row_sums = result[prob_cols].sum(axis=1)
    assert (row_sums.sub(1.0).abs() < 1e-6).all()
