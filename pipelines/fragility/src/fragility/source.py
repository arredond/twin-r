"""Fetch a curated subset of Martins & Silva (2020) fragility functions."""

from __future__ import annotations

import io

import pandas as pd
import requests

RAW_BASE = (
    "https://raw.githubusercontent.com/lmartins88/global_fragility_vulnerability"
    "/master/fragility_curves/fragility_other_IMs"
)

# Height classes present in the source repo for our two taxonomy classes go
# up to H12 at 1-storey granularity (then jumps to H15/H20/H25/H30/H35) --
# 1..12 comfortably covers Lorca's building stock (see pipelines/exposure).
_HEIGHT_CLASSES = range(1, 13)

# (material, lateral system + ductility) pairs we vendor. See exposure's
# taxonomy heuristic (pipelines/exposure/src/exposure/taxonomy.py) for how a
# building's (construction_year, floors) maps to one of these.
TAXONOMY_CLASSES = ["CR_LDUAL-DUL", "MR_LWAL-DUL"]

DAMAGE_STATES = ["Slight_damage", "Moderate_damage", "Extensive_damage", "Complete_damage"]


def _file_names() -> list[str]:
    return [f"{cls}_H{h}.csv" for cls in TAXONOMY_CLASSES for h in _HEIGHT_CLASSES]


def fetch_fragility_functions(timeout: int = 30) -> pd.DataFrame:
    """Download the curated fragility CSVs and return one long-format DataFrame.

    Columns: taxonomy, height_class, im_type, im_value, damage_state,
    prob_exceedance. `damage_state` values are the discrete states above the
    threshold (i.e. "Slight" row means "probability of being in Slight damage
    state *or worse*" at that intensity) -- matches how the CSVs are
    published (cumulative exceedance probabilities, not discrete-state
    probabilities). The scenario function is responsible for converting
    exceedance probabilities into discrete damage-state probabilities
    (successive differences).
    """
    frames = []
    for cls in TAXONOMY_CLASSES:
        for h in _HEIGHT_CLASSES:
            file_name = f"{cls}_H{h}.csv"
            url = f"{RAW_BASE}/{file_name}"
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 404:
                # Not every (class, height) combination necessarily exists;
                # skip rather than fail the whole pipeline.
                continue
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            im_col = df.columns[0]  # e.g. "SA(0.3s) [g]"
            long_df = df.melt(
                id_vars=[im_col],
                value_vars=DAMAGE_STATES,
                var_name="damage_state",
                value_name="prob_exceedance",
            )
            long_df = long_df.rename(columns={im_col: "im_value"})
            long_df["taxonomy"] = cls
            long_df["height_class"] = h
            long_df["im_type"] = im_col
            long_df["damage_state"] = long_df["damage_state"].str.replace("_damage", "")
            frames.append(long_df)

    if not frames:
        raise RuntimeError("No fragility functions were fetched -- check RAW_BASE / class names")

    return pd.concat(frames, ignore_index=True)


def write_fragility_parquet(output_path: str, timeout: int = 30) -> pd.DataFrame:
    df = fetch_fragility_functions(timeout=timeout)
    df.to_parquet(output_path, index=False)
    return df
