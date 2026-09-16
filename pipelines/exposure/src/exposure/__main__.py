"""CLI: python -m exposure <raw_dir> <buildings.parquet> <exposure.parquet> [tiles.pmtiles]
    [--skip-download] [--debris debris.parquet] [--debris-tiles debris.pmtiles]

Defaults to Lorca (the milestone-1 pilot municipality, see
docs/milestone-1-plan.md). The first four positional args are unchanged
from before ADR-0010 -- debris output is opt-in via flags, not a new
positional, so existing invocations (README.md, pipelines/README.md) keep
working as-is.
"""

import argparse
import sys

from . import LORCA
from .pipeline import run


def main() -> None:
    args = sys.argv[1:]
    skip_download = "--skip-download" in args
    args = [a for a in args if a != "--skip-download"]

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--debris", dest="debris_output", default=None)
    parser.add_argument("--debris-tiles", dest="debris_tiles_output", default=None)
    parsed, positional = parser.parse_known_args(args)

    if len(positional) not in (3, 4):
        print(
            "usage: python -m exposure <raw_dir> <buildings.parquet> "
            "<exposure.parquet> [tiles.pmtiles] [--skip-download] "
            "[--debris debris.parquet] [--debris-tiles debris.pmtiles]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raw_dir, buildings_output, exposure_output = positional[:3]
    tiles_output = positional[3] if len(positional) == 4 else None

    buildings, exposure = run(
        LORCA,
        raw_dir,
        buildings_output,
        exposure_output,
        skip_download=skip_download,
        tiles_output=tiles_output,
        debris_output=parsed.debris_output,
        debris_tiles_output=parsed.debris_tiles_output,
    )
    print(f"wrote {len(buildings)} buildings to {buildings_output}")
    print(f"wrote {len(exposure)} exposure rows to {exposure_output}")
    if tiles_output:
        print(f"wrote tiles to {tiles_output}")
    if parsed.debris_output:
        print(f"wrote debris rings to {parsed.debris_output}")
    if parsed.debris_tiles_output:
        print(f"wrote debris tiles to {parsed.debris_tiles_output}")


if __name__ == "__main__":
    main()
