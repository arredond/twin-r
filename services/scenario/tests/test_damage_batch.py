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


def _same_values_for_every_im_type(fragility_table: FragilityTable, im_values: np.ndarray) -> dict:
    # These tests cross-check the batch path's *probability math* against
    # the scalar path, independent of dispatch-by-im_type (that's covered
    # separately by test_batch_dispatches_each_building_to_its_own_curves_im_type
    # below) -- broadcasting the same per-building array under every im_type
    # key means whichever im_type a building's curve resolves to, it reads
    # back the same value evaluate_building_damage is given directly.
    return dict.fromkeys(fragility_table.used_im_types(), im_values)


def test_batch_matches_scalar_path_exactly(fragility_table: FragilityTable):
    rng = np.random.default_rng(0)
    n = 200
    taxonomy_classes = rng.choice(["CR_LDUAL-DUL", "MR_LWAL-DUL"], size=n)
    height_classes = rng.integers(1, 8, size=n)
    im_values = rng.uniform(0.01, 1.0, size=n)

    batch = evaluate_damage_batch(
        fragility_table,
        taxonomy_classes,
        height_classes,
        _same_values_for_every_im_type(fragility_table, im_values),
    )

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
        fragility_table,
        taxonomy_classes,
        height_classes,
        _same_values_for_every_im_type(fragility_table, im_values),
        damage_percentile=0.85,
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

    batch = evaluate_damage_batch(
        fragility_table,
        taxonomy_classes,
        height_classes,
        _same_values_for_every_im_type(fragility_table, im_values),
    )
    prob_cols = [c for c in batch.columns if c.startswith("prob_")]
    sums = batch[prob_cols].sum(axis=1)
    assert (sums.sub(1.0).abs() < 1e-9).all()


def test_batch_dispatches_each_building_to_its_own_curves_im_type(fragility_table: FragilityTable):
    # The actual bug this fixes (docs/validation-lorca-2011.md §10.2): a
    # height-1 MR_LWAL-DUL building is PGA-indexed, a height-3 one is
    # SA(0.3s)-indexed -- feed deliberately different values under each key
    # and confirm each building reads back *its own curve's* value, not
    # whichever array happened to be passed first/only.
    taxonomy_classes = np.array(["MR_LWAL-DUL", "MR_LWAL-DUL"])
    height_classes = np.array([1, 3])  # PGA-indexed, SA(0.3s)-indexed respectively
    pga_curve_im_type = fragility_table.get("MR_LWAL-DUL", 1).im_type
    sa03_curve_im_type = fragility_table.get("MR_LWAL-DUL", 3).im_type
    assert pga_curve_im_type != sa03_curve_im_type  # otherwise this test proves nothing

    im_values_by_type = {
        pga_curve_im_type: np.array([0.05, 0.05]),  # low -> building 0 should read this
        sa03_curve_im_type: np.array([0.9, 0.9]),  # high -> building 1 should read this
    }
    batch = evaluate_damage_batch(
        fragility_table, taxonomy_classes, height_classes, im_values_by_type
    )
    assert batch["im_value"].iloc[0] == pytest.approx(0.05)
    assert batch["im_value"].iloc[1] == pytest.approx(0.9)
    assert batch["im_type"].iloc[0] == pga_curve_im_type
    assert batch["im_type"].iloc[1] == sa03_curve_im_type
    # Low PGA vs. high SA(0.3s) should produce meaningfully different
    # P(>=Slight) -- not just different im_value bookkeeping.
    assert (
        batch["prob_slight"].iloc[1] + batch["prob_moderate"].iloc[1]
        > batch["prob_slight"].iloc[0] + batch["prob_moderate"].iloc[0]
    )
