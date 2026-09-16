"""Damage: fragility functions applied directly to ground-motion intensity.

See docs/milestone-1-plan.md §2: this deliberately replaces MERISUR's
IDCM/FEMA 440 capacity-curve method (which needs Lorca-specific capacity
curves we don't have) with the standard approach of evaluating pre-published
fragility functions (Martins & Silva 2020) directly against intensity --
this is the same fundamental operation OpenQuake's own scenario-damage
calculator performs, just without invoking the full Engine.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .fragility_lookup import DAMAGE_STATES_ASCENDING, FragilityTable

DAMAGE_STATES = ["None", *DAMAGE_STATES_ASCENDING]  # display/severity order


def damage_state_probabilities(exceedance: dict[str, float]) -> dict[str, float]:
    """Convert cumulative exceedance probabilities into discrete damage-state probabilities.

    `exceedance[state]` = P(damage >= state). Discrete state probabilities
    are successive differences; clipped at 0 and renormalized to guard
    against small numerical noise (e.g. an Extensive exceedance curve
    interpolated slightly above its Moderate counterpart near a
    discontinuity).
    """
    cum = [1.0] + [exceedance[s] for s in DAMAGE_STATES_ASCENDING] + [0.0]
    probs = [max(cum[i] - cum[i + 1], 0.0) for i in range(len(cum) - 1)]
    total = sum(probs)
    if total == 0:
        # Degenerate case (e.g. IM far outside the vendored curve's range on
        # the low end) -- treat as certainly undamaged rather than dividing
        # by zero.
        return dict(zip(DAMAGE_STATES, [1.0] + [0.0] * (len(DAMAGE_STATES) - 1)))
    return dict(zip(DAMAGE_STATES, [p / total for p in probs]))


def modal_damage_state(probabilities: dict[str, float]) -> str:
    # max(probabilities, key=probabilities.get) is the usual idiom here,
    # but passing a bound dict.get as `key` defeats static type checkers
    # (its overloaded, Optional-returning signature doesn't line up with
    # what max() expects) -- a plain lambda is equivalent at runtime and
    # type-checks cleanly.
    return max(probabilities, key=lambda state: probabilities[state])


def evaluate_building_damage(
    fragility_table: FragilityTable,
    taxonomy_class: str,
    height_class: int,
    im_value: float,
) -> tuple[str, dict[str, float]]:
    """Return (modal_damage_state, full probability distribution) for one building."""
    curve = fragility_table.get(taxonomy_class, height_class)
    exceedance = curve.exceedance_at(im_value)
    probabilities = damage_state_probabilities(exceedance)
    return modal_damage_state(probabilities), probabilities


def evaluate_damage_batch(
    fragility_table: FragilityTable,
    taxonomy_classes: np.ndarray,
    height_classes: np.ndarray,
    im_values: np.ndarray,
) -> pd.DataFrame:
    """Vectorized equivalent of calling `evaluate_building_damage` once per
    building, grouped by (taxonomy_class, height_class).

    The vendored fragility set (pipelines/fragility) has only ~17 such
    classes -- grouping turns "one np.interp call per building" into "one
    np.interp call per class per damage state," which is the difference
    between a scenario over a whole region (ADR-0005) taking seconds vs.
    tens of minutes. See test_damage.py for a cross-check against the
    scalar path on the same inputs.

    Returns a DataFrame indexed like the inputs, columns: damage_state,
    prob_none, prob_slight, prob_moderate, prob_extensive, prob_complete.
    """
    n = len(taxonomy_classes)
    damage_states = np.empty(n, dtype=object)
    prob_arrays = {state: np.zeros(n) for state in DAMAGE_STATES}

    groups = (
        pd.DataFrame({"taxonomy_class": taxonomy_classes, "height_class": height_classes})
        .groupby(["taxonomy_class", "height_class"], sort=False)
        .indices
    )

    # pandas' groupby(...).indices is typed with an opaque Hashable key,
    # not the actual (str, int) tuple it returns at runtime -- known
    # pandas-stubs limitation, not a real type error.
    for (taxonomy_class, height_class), idx in groups.items():  # pyrefly: ignore
        idx = np.asarray(idx)
        curve = fragility_table.get(taxonomy_class, height_class)
        im = np.asarray(im_values)[idx]

        exceedance = {
            state: np.interp(im, curve.im_values[state], curve.prob_exceedance[state])
            for state in DAMAGE_STATES_ASCENDING
        }
        cum = (
            [np.ones_like(im)]
            + [exceedance[s] for s in DAMAGE_STATES_ASCENDING]
            + [np.zeros_like(im)]
        )
        discrete = [np.clip(cum[i] - cum[i + 1], 0.0, None) for i in range(len(cum) - 1)]
        total = sum(discrete)
        # Degenerate case (total == 0, e.g. IM far below the curve's range):
        # certainly-None, same fallback as the scalar path.
        is_degenerate = total == 0
        safe_total = np.where(is_degenerate, 1.0, total)

        group_probs = np.empty((len(DAMAGE_STATES), len(im)))
        for i, state in enumerate(DAMAGE_STATES):
            normal = discrete[i] / safe_total
            fallback = 1.0 if state == "None" else 0.0
            group_probs[i] = np.where(is_degenerate, fallback, normal)
            prob_arrays[state][idx] = group_probs[i]

        modal_i = np.argmax(group_probs, axis=0)
        damage_states[idx] = np.array(DAMAGE_STATES)[modal_i]

    return pd.DataFrame(
        {
            "damage_state": damage_states,
            **{f"prob_{state.lower()}": prob_arrays[state] for state in DAMAGE_STATES},
        }
    )
