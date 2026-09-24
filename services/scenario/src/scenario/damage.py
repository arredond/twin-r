"""Damage: fragility functions applied directly to ground-motion intensity.

See docs/milestone-1-plan.md §2: this deliberately replaces MERISUR's
IDCM/FEMA 440 capacity-curve method (which needs Lorca-specific capacity
curves we don't have) with the standard approach of evaluating pre-published
fragility functions (Martins & Silva 2020) directly against intensity --
this is the same fundamental operation OpenQuake's own scenario-damage
calculator performs, just without invoking the full Engine.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import pyarrow as pa

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


def percentile_damage_state(probabilities: dict[str, float], percentile: float) -> str:
    """The smallest damage state whose cumulative probability (summed from
    "None" upward, in ascending severity) reaches `percentile` -- MERISUR's
    "very low probability / very high impact" tier reports the 85th
    percentile this way (`docs/merisur.md` §4.7), rather than the modal
    (most-likely) state `modal_damage_state` returns. At `percentile=0.85`
    this picks a state such that at most 15% of the probability mass is
    *more* severe than it -- a deliberately pessimistic read of the same
    distribution `modal_damage_state` reads optimistically.
    """
    cumulative = 0.0
    for state in DAMAGE_STATES:
        cumulative += probabilities[state]
        if cumulative >= percentile:
            return state
    return DAMAGE_STATES[-1]  # unreachable once probabilities sum to 1.0, kept as a safe fallback


def select_damage_state(probabilities: dict[str, float], damage_percentile: float | None) -> str:
    """`damage_percentile=None` (default, matching every caller before this
    parameter existed) selects the modal state; a float in (0, 1] selects
    `percentile_damage_state` instead -- see `probability_level.py`."""
    if damage_percentile is None:
        return modal_damage_state(probabilities)
    return percentile_damage_state(probabilities, damage_percentile)


def evaluate_building_damage(
    fragility_table: FragilityTable,
    taxonomy_class: str,
    height_class: int,
    im_value: float,
    damage_percentile: float | None = None,
) -> tuple[str, dict[str, float]]:
    """Return (damage_state, full probability distribution) for one building.

    `damage_percentile`: see `select_damage_state`.
    """
    curve = fragility_table.get(taxonomy_class, height_class)
    exceedance = curve.exceedance_at(im_value)
    probabilities = damage_state_probabilities(exceedance)
    return select_damage_state(probabilities, damage_percentile), probabilities


def evaluate_damage_batch(
    fragility_table: FragilityTable,
    taxonomy_classes: np.ndarray,
    height_classes: np.ndarray,
    im_values_by_type: dict[str, np.ndarray],
    damage_percentile: float | None = None,
) -> pd.DataFrame:
    """Vectorized equivalent of calling `evaluate_building_damage` once per
    building, grouped by (taxonomy_class, height_class).

    The vendored fragility set (pipelines/fragility) has only ~17 such
    classes -- grouping turns "one np.interp call per building" into "one
    np.interp call per class per damage state," which is the difference
    between a scenario over a whole region (ADR-0005) taking seconds vs.
    tens of minutes. See test_damage.py for a cross-check against the
    scalar path on the same inputs.

    `im_values_by_type`: `{im_type_label: array}`, one entry per intensity
    measure `ground_motion.py` computed (e.g. `{"PGA [g]": ..., "SA(0.3s)
    [g]": ...}`), each array aligned with `taxonomy_classes`/`height_classes`
    (one value per building, at that building's location, for that IM
    type -- *not* one value per group). Each (taxonomy, height) group looks
    up its own curve's `im_type` (`FragilityCurve.im_type`) and reads its IM
    values from the matching array -- this is the fix for
    docs/validation-lorca-2011.md §10.2: previously every building was
    evaluated against a single SA(0.3s) array regardless of which IM type
    its own curve was actually indexed by.

    `damage_percentile`: see `select_damage_state` -- `None` (default)
    picks each building's modal state (argmax); a float in (0, 1] picks the
    smallest state whose cumulative probability reaches it, vectorized as
    "first row where the running cumulative sum crosses the threshold"
    (`np.argmax` on a boolean array returns its first True, and the last
    row's cumulative sum is always ~1.0 by construction, so one is always
    found -- same guarantee `percentile_damage_state`'s scalar fallback
    documents).

    Returns a DataFrame indexed like the inputs, columns: damage_state,
    im_value, im_type, prob_none, prob_slight, prob_moderate,
    prob_extensive, prob_complete. `im_value`/`im_type` record which IM
    value/type each building was *actually* evaluated against, since that
    now varies by building rather than being one scenario-wide constant.
    """
    arrays = evaluate_damage_arrays(
        fragility_table,
        taxonomy_classes,
        height_classes,
        im_values_by_type,
        damage_percentile=damage_percentile,
    )
    return pd.DataFrame(
        {
            "damage_state": np.array(DAMAGE_STATES, dtype=object)[arrays.damage_state_code],
            "im_value": arrays.im_value,
            "im_type": np.array(arrays.im_types, dtype=object)[arrays.im_type_code],
            **{f"prob_{state.lower()}": arrays.probs[i] for i, state in enumerate(DAMAGE_STATES)},
        }
    )


@dataclass(frozen=True)
class DamageArrays:
    """`evaluate_damage_batch`'s result as plain arrays, one entry per
    building -- what engine.py's streaming path works with, instead of a
    DataFrame of per-building Python strings (a large part of a
    multi-million-building scenario's peak memory, measured)."""

    damage_state_code: np.ndarray  # int8, index into DAMAGE_STATES
    probs: np.ndarray  # (len(DAMAGE_STATES), n) float64, rows in DAMAGE_STATES order
    im_value: np.ndarray  # float64
    im_type_code: np.ndarray  # int8, index into im_types
    im_types: list[str]


def evaluate_damage_arrays(
    fragility_table: FragilityTable,
    taxonomy_classes: np.ndarray | pa.Array,
    height_classes: np.ndarray,
    im_values_by_type: dict[str, np.ndarray],
    damage_percentile: float | None = None,
) -> DamageArrays:
    """The array core of `evaluate_damage_batch` (see its docstring for the
    method); `taxonomy_classes` may also be a pyarrow array, straight off
    a DuckDB record batch."""
    n = len(height_classes)
    damage_codes = np.zeros(n, dtype=np.int8)
    im_values_used = np.zeros(n, dtype=float)
    im_types = sorted(im_values_by_type)
    im_type_codes = np.zeros(n, dtype=np.int8)
    probs = np.zeros((len(DAMAGE_STATES), n))
    if n == 0:
        return DamageArrays(damage_codes, probs, im_values_used, im_type_codes, im_types)

    # Group by (taxonomy_class, height_class) without materializing a
    # Python string per building: dictionary-encode the (low-cardinality)
    # taxonomy column, pack it with the height into one integer key, and
    # split one stable argsort of that key into its groups.
    taxonomy = pa.array(taxonomy_classes, type=pa.string()).dictionary_encode()
    taxonomy_names = taxonomy.dictionary.to_pylist()
    taxonomy_codes = taxonomy.indices.to_numpy(zero_copy_only=False).astype(np.int64)
    heights = np.asarray(height_classes, dtype=np.int64)
    group_keys, group_of, group_sizes = np.unique(
        taxonomy_codes * 1_000_000 + heights, return_inverse=True, return_counts=True
    )
    order = np.argsort(group_of, kind="stable")
    group_indices = np.split(order, np.cumsum(group_sizes)[:-1])

    for key, idx in zip(group_keys.tolist(), group_indices):
        taxonomy_class = taxonomy_names[key // 1_000_000]
        height_class = key % 1_000_000
        curve = fragility_table.get(taxonomy_class, height_class)
        im = np.asarray(im_values_by_type[curve.im_type])[idx]
        im_values_used[idx] = im
        im_type_codes[idx] = im_types.index(curve.im_type)

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
        probs[:, idx] = group_probs

        if damage_percentile is None:
            state_i = np.argmax(group_probs, axis=0)
        else:
            reaches_percentile = np.cumsum(group_probs, axis=0) >= damage_percentile
            state_i = np.argmax(reaches_percentile, axis=0)
        damage_codes[idx] = state_i

    return DamageArrays(damage_codes, probs, im_values_used, im_type_codes, im_types)
