"""Backfill columns onto already-parsed buildings.parquet file(s) that a
pipeline change added after they were crawled -- no need to
re-download/re-parse raw Catastro GML, since what's needed is already
there.

CLI: python -m exposure.backfill [--municipality-code|--vs30] <path-or-glob-of-buildings.parquet-files>

Defaults to the spatial-index backfill (centroid/bbox columns, see
`add_spatial_index_columns`). Pass `--municipality-code` for the
municipality_code backfill instead (see `backfill_municipality_code_file`),
or `--vs30` for the ESRM20 site-amplification backfill (ADR-0015, see
`backfill_vs30_file`).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from scipy.spatial import (
    cKDTree,  # pyrefly: ignore -- no stub for this compiled extension re-export
)

from .parse import add_spatial_index_columns
from .vs30 import add_vs30_column, build_vs30_lookup, fetch_spain_vs30_grid


def backfill_file(path: Path) -> int:
    """Add spatial index columns to one buildings.parquet file, in place.
    Returns the row count. Skipped (no-op) if the columns already exist."""
    gdf = gpd.read_parquet(path)
    if "centroid_lon" in gdf.columns:
        return len(gdf)
    gdf = add_spatial_index_columns(gdf)
    gdf.to_parquet(path)
    return len(gdf)


def backfill_municipality_code_file(path: Path) -> int:
    """Add a `municipality_code` column to one `<ine_code>.buildings.parquet`
    part, in place. Returns the row count. Skipped (no-op) if the column
    already exists.

    Derived from the part's own filename (region.py's `<ine_code>.
    buildings.parquet` partitioning convention -- see `_part_paths`), not a
    spatial join: a part already only contains one municipality's
    buildings (that's how it got crawled), so which municipality every row
    in it belongs to is already known for free. Retrofits data crawled
    before `pipeline.build_exposure` started stamping this column on new
    parts -- see `scenario.response.compute_municipality_stats`'s
    docstring for why this replaced a per-request `ST_Contains` spatial
    join against municipalities.parquet.
    """
    if "." not in path.name:
        raise ValueError(
            f"{path} doesn't look like a '<ine_code>.buildings.parquet' part "
            "(region.py's partitioning convention) -- can't derive its "
            "municipality_code from the filename"
        )
    ine_code = path.name.split(".", 1)[0]
    gdf = gpd.read_parquet(path)
    if "municipality_code" in gdf.columns:
        return len(gdf)
    gdf = gdf.copy()
    gdf["municipality_code"] = ine_code
    gdf.to_parquet(path)
    return len(gdf)


def backfill_vs30_file(path: Path, grid: pd.DataFrame, tree: cKDTree) -> int:
    """Add a `vs30` column to one buildings.parquet part, in place, from
    ESRM20's national site grid (ADR-0015, `vs30.py`). Returns the row
    count. Skipped (no-op) if the column already exists.

    `grid`/`tree` are built once by the caller (`main`) and threaded
    through every part -- re-downloading the 17MB source CSV and rebuilding
    the KD-tree per part would dominate a national-scale backfill's runtime
    for no benefit, the same reasoning as ADR-0014's municipality_code
    backfill reusing one parsed source across parts.
    """
    gdf = gpd.read_parquet(path)
    if "vs30" in gdf.columns:
        return len(gdf)
    gdf = add_vs30_column(gdf, grid=grid, tree=tree)
    gdf.to_parquet(path)
    return len(gdf)


def backfill_paths(paths: list[Path], municipality_code: bool = False, vs30: bool = False) -> None:
    t0 = time.monotonic()
    total = 0

    if vs30:
        print("fetching ESRM20 Spain Vs30 grid...")
        grid = fetch_spain_vs30_grid()
        tree = build_vs30_lookup(grid)
        print(f"  {len(grid)} grid points, fetched in {time.monotonic() - t0:.0f}s")
        backfill_one = lambda p: backfill_vs30_file(p, grid, tree)
    elif municipality_code:
        backfill_one = backfill_municipality_code_file
    else:
        backfill_one = backfill_file

    for i, path in enumerate(paths, 1):
        n = backfill_one(path)
        total += n
        print(f"[{i}/{len(paths)}] {path.name}: {n} buildings")
    print(f"\nbackfilled {len(paths)} files, {total} buildings, in {time.monotonic() - t0:.0f}s")


def main() -> None:
    args = sys.argv[1:]
    municipality_code = "--municipality-code" in args
    if municipality_code:
        args.remove("--municipality-code")
    vs30 = "--vs30" in args
    if vs30:
        args.remove("--vs30")

    if len(args) != 1:
        print(
            "usage: python -m exposure.backfill [--municipality-code|--vs30] "
            "<path-or-glob-of-buildings.parquet-files>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    pattern = args[0]
    p = Path(pattern)
    paths = sorted(p.parent.glob(p.name)) if any(c in pattern for c in "*?[") else [p]
    if not paths:
        print(f"no files matched {pattern!r}", file=sys.stderr)
        raise SystemExit(1)
    backfill_paths(paths, municipality_code=municipality_code, vs30=vs30)


if __name__ == "__main__":
    main()
