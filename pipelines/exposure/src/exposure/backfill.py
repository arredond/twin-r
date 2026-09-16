"""Backfill spatial index columns (centroid, bbox) onto already-parsed
buildings.parquet file(s) -- no need to re-download/re-parse raw Catastro
GML, since the geometry needed to compute them is already there.

For any *future* crawl, `parse.load_buildings` adds these columns natively
(see parse.py's `add_spatial_index_columns`) -- this module exists only to
retrofit data parsed before that was added, without paying the ~12-minute
Catastro re-crawl cost again.

CLI: python -m exposure.backfill <path-or-glob-of-buildings.parquet-files>
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


def backfill_paths(paths: list[Path]) -> None:
    t0 = time.monotonic()
    total = 0
    for i, path in enumerate(paths, 1):
        n = backfill_file(path)
        total += n
        print(f"[{i}/{len(paths)}] {path.name}: {n} buildings")
    print(f"\nbackfilled {len(paths)} files, {total} buildings, in {time.monotonic() - t0:.0f}s")


def main() -> None:
    if len(sys.argv) != 2:
        print(
            "usage: python -m exposure.backfill <path-or-glob-of-buildings.parquet-files>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    pattern = sys.argv[1]
    p = Path(pattern)
    paths = sorted(p.parent.glob(p.name)) if any(c in pattern for c in "*?[") else [p]
    if not paths:
        print(f"no files matched {pattern!r}", file=sys.stderr)
        raise SystemExit(1)
    backfill_paths(paths)


if __name__ == "__main__":
    main()
