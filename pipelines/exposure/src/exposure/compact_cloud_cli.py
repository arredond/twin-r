"""CLI: uv run python -m exposure.compact_cloud_cli <parts_dir> <buildings-cloud.parquet>

Builds the single spatially-sorted buildings parquet used for cloud
deployment (see `region.compact_buildings_for_cloud`'s docstring for why
this exists separately from the per-municipality parts glob). Run once
after a crawl (or region_cli's full run) finishes, before uploading data
to S3 -- re-run whenever the underlying parts change.
"""

from __future__ import annotations

import sys
import time

from .region import compact_buildings_for_cloud


def main() -> None:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <parts_dir> <buildings-cloud.parquet>")
        raise SystemExit(1)
    parts_dir, output = sys.argv[1], sys.argv[2]

    t0 = time.monotonic()
    n = compact_buildings_for_cloud(parts_dir, output)
    print(f"wrote {n} buildings to {output} in {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
