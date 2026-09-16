"""CLI: python -m faults <output_path.parquet>"""

import sys

from .source import write_faults_parquet


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m faults <output_path.parquet>", file=sys.stderr)
        raise SystemExit(2)
    output_path = sys.argv[1]
    gdf = write_faults_parquet(output_path)
    print(f"wrote {len(gdf)} faults to {output_path}")


if __name__ == "__main__":
    main()
