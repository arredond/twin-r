"""Look up one building's static exposure attributes by id.

Powers the frontend's building-click popup (floors/construction year/use
already come from the clicked PMTiles feature directly -- see
DamageMap.tsx -- but `taxonomy_class`/`height_class` only live in
`exposure.parquet`, not baked into the tiles, so this is a small per-click
DuckDB point lookup rather than a full re-tile just to add two fields).
"""

from __future__ import annotations

import pandas as pd

from .db import ensure_httpfs, get_connection


def get_building(exposure_path: str, building_id: str) -> pd.Series | None:
    """One building's exposure row, or None if the id isn't found."""
    con = get_connection()
    if exposure_path.startswith("s3://"):
        ensure_httpfs(con)
    df = con.execute(
        "SELECT * FROM read_parquet(?) WHERE building_id = ?",
        [exposure_path, building_id],
    ).df()
    if df.empty:
        return None
    return df.iloc[0]
