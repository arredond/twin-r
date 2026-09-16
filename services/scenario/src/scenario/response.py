"""Shape `engine.run_scenario`'s full per-building result into the thin
payload the frontend actually needs, shared between local.py and handler.py
so the two runtimes can't drift on this logic.

The frontend only ever reads `building_id`, `damage_state` (map colour) and
the five `prob_*` fields (click-popup breakdown) -- see
apps/web/src/components/DamageMap.tsx. `lon`/`lat`/`im_value`/`im_type` ride along on
engine.py's own contract (useful to a caller that wants the full picture),
but every building the frontend colors is already a feature in the
buildings PMTiles layer it joins against by `building_id`, so shipping its
coordinates a second time here is pure waste -- confirmed unused via a
repo-wide reference check, not left out by inference. Dropping them, plus
encoding `damage_state` as its `DAMAGE_STATES` index instead of a string,
noticeably shrinks a payload that can otherwise run into the tens of MB.
"""

from __future__ import annotations

import pandas as pd

from .damage import DAMAGE_STATES

# A "None"-modal building only ships if its probability margin over the
# next-most-likely damage state is *this* narrow -- see local.py's own copy
# of this constant (kept in sync; docs/validation-region-expansion.md §4)
# for the full rationale.
UNCERTAINTY_MARGIN = 0.15

DAMAGE_STATE_CODES = {state: i for i, state in enumerate(DAMAGE_STATES)}

# Columns the frontend actually reads (see this module's docstring) --
# building_id + damage_state_code + the five probabilities.
_THIN_COLUMNS = ["building_id", "damage_state_code", *(f"prob_{s.lower()}" for s in DAMAGE_STATES)]


def prepare_response_buildings(result: pd.DataFrame) -> pd.DataFrame:
    """Filter to damaged/uncertain buildings and trim to the thin payload.

    `result` is engine.py's full per-building DataFrame (building_id, lon,
    lat, damage_state, im_value, im_type, prob_*). Returns a DataFrame with
    only the columns the frontend needs, ready for `.to_dict(orient="records")`.
    """
    max_other_prob = result[
        ["prob_slight", "prob_moderate", "prob_extensive", "prob_complete"]
    ].max(axis=1)
    is_close_call = (result["prob_none"] - max_other_prob) < UNCERTAINTY_MARGIN
    result = result[(result["damage_state"] != "None") | is_close_call]
    # Rounded to keep the extra bytes from a full-precision float64
    # round-trip down -- this subset is already small, but no reason to pay
    # for digits no one reads.
    result = result.round({c: 4 for c in result.columns if c.startswith("prob_")})
    result = result.assign(damage_state_code=result["damage_state"].map(DAMAGE_STATE_CODES))
    return result[_THIN_COLUMNS]
