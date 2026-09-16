"""Load fragility.parquet (pipelines/fragility output) into an interpolation-ready form."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DAMAGE_STATES_ASCENDING = ["Slight", "Moderate", "Extensive", "Complete"]


@dataclass(frozen=True)
class FragilityCurve:
    """One (taxonomy, height_class)'s exceedance curves, ready to interpolate."""

    im_values: dict[str, np.ndarray]
    prob_exceedance: dict[str, np.ndarray]

    def exceedance_at(self, im_value: float) -> dict[str, float]:
        return {
            state: float(np.interp(im_value, self.im_values[state], self.prob_exceedance[state]))
            for state in DAMAGE_STATES_ASCENDING
        }


class FragilityTable:
    """All vendored fragility curves, keyed by (taxonomy_class, height_class)."""

    def __init__(self, df: pd.DataFrame):
        self._curves: dict[tuple[str, int], FragilityCurve] = {}
        for (taxonomy, height), group in df.groupby(["taxonomy", "height_class"]):
            im_values, prob_exceedance = {}, {}
            for state, state_group in group.groupby("damage_state"):
                state_group = state_group.sort_values("im_value")
                im_values[state] = state_group["im_value"].to_numpy()
                prob_exceedance[state] = state_group["prob_exceedance"].to_numpy()
            self._curves[(taxonomy, height)] = FragilityCurve(im_values, prob_exceedance)

    @classmethod
    def from_parquet(cls, path: str) -> FragilityTable:
        return cls(pd.read_parquet(path))

    def get(self, taxonomy_class: str, height_class: int) -> FragilityCurve:
        key = (taxonomy_class, height_class)
        if key not in self._curves:
            # Fall back to the nearest height class we do have for this
            # taxonomy, rather than failing a whole scenario over one
            # building whose height exceeds what we vendored.
            available = sorted(h for t, h in self._curves if t == taxonomy_class)
            if not available:
                raise KeyError(f"No fragility curve vendored for taxonomy {taxonomy_class!r}")
            nearest = min(available, key=lambda h: abs(h - height_class))
            key = (taxonomy_class, nearest)
        return self._curves[key]
