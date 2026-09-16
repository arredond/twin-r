"""Backfill columns onto already-parsed buildings.parquet file(s) that a
pipeline change added after they were crawled -- no need to
re-download/re-parse raw Catastro GML, since what's needed is already
there.

CLI: python -m exposure.backfill [--municipality-code] <path-or-glob-of-buildings.parquet-files>

Defaults to the spatial-index backfill (centroid/bbox columns, see
`add_spatial_index_columns`). Pass `--municipality-code` for the
municipality_code backfill instead (see `backfill_municipality_code_file`).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import geopandas as gpd

from .parse import add_spatial_index_columns


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


def backfill_paths(paths: list[Path], municipality_code: bool = False) -> None:
    backfill_one = backfill_municipality_code_file if municipality_code else backfill_file
    t0 = time.monotonic()
    total = 0
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

    if len(args) != 1:
        print(
            "usage: python -m exposure.backfill [--municipality-code] "
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
    backfill_paths(paths, municipality_code=municipality_code)


if __name__ == "__main__":
    main()
