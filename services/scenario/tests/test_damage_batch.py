"""Cross-check the vectorized batch path against the scalar per-building path.

Uses the real vendored fragility set if present locally (same
skip-if-absent pattern as test_engine_integration.py / test_faults.py);
this is exactly the kind of subtle-bug-prone rewrite (docs/decisions/0005)
that needs a real equivalence check, not just synthetic curves.
"""

from pathlib import Path

import numpy as np
import pytest
from scenario.damage import evaluate_building_damage, evaluate_damage_batch
from scenario.fragility_lookup import FragilityTable

FRAGILITY_PATH = Path(__file__).resolve().parents[3] / "data" / "fragility" / "fragility.parquet"

pytestmark = pytest.mark.skipif(
    not FRAGILITY_PATH.exists(), reason="run pipelines/fragility locally first"
)


@pytest.fixture
def fragility_table() -> FragilityTable:
    return FragilityTable.from_parquet(str(FRAGILITY_PATH))


def test_batch_matches_scalar_path_exactly(fragility_table: FragilityTable):
    rng = np.random.default_rng(0)
    n = 200
    taxonomy_classes = rng.choice(["CR_LDUAL-DUL", "MR_LWAL-DUL"], size=n)
    height_classes = rng.integers(1, 8, size=n)
    im_values = rng.uniform(0.01, 1.0, size=n)

    batch = evaluate_damage_batch(fragility_table, taxonomy_classes, height_classes, im_values)

    for i in range(n):
        expected_state, expected_probs = evaluate_building_damage(
            fragility_table, taxonomy_classes[i], int(height_classes[i]), im_values[i]
        )
        assert batch["damage_state"].iloc[i] == expected_state
        for state, prob in expected_probs.items():
            col = f"prob_{state.lower()}"
            assert batch[col].iloc[i] == pytest.approx(prob, abs=1e-9)


def test_batch_matches_scalar_path_with_percentile(fragility_table: FragilityTable):
    rng = np.random.default_rng(2)
    n = 200
    taxonomy_classes = rng.choice(["CR_LDUAL-DUL", "MR_LWAL-DUL"], size=n)
    height_classes = rng.integers(1, 8, size=n)
    im_values = rng.uniform(0.01, 1.0, size=n)

    batch = evaluate_damage_batch(
        fragility_table, taxonomy_classes, height_classes, im_values, damage_percentile=0.85
    )

    for i in range(n):
        expected_state, _ = evaluate_building_damage(
            fragility_table,
            taxonomy_classes[i],
            int(height_classes[i]),
            im_values[i],
            damage_percentile=0.85,
        )
        assert batch["damage_state"].iloc[i] == expected_state


def test_batch_probabilities_sum_to_one(fragility_table: FragilityTable):
    rng = np.random.default_rng(1)
    n = 100
    taxonomy_classes = rng.choice(["CR_LDUAL-DUL", "MR_LWAL-DUL"], size=n)
    height_classes = rng.integers(1, 8, size=n)
    im_values = rng.uniform(0.01, 1.0, size=n)

    batch = evaluate_damage_batch(fragility_table, taxonomy_classes, height_classes, im_values)
    prob_cols = [c for c in batch.columns if c.startswith("prob_")]
    sums = batch[prob_cols].sum(axis=1)
    assert (sums.sub(1.0).abs() < 1e-9).all()
