"""CLI: python -m fragility <output_path.parquet>"""

import sys

from .source import write_fragility_parquet


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m fragility <output_path.parquet>", file=sys.stderr)
        raise SystemExit(2)
    output_path = sys.argv[1]
    df = write_fragility_parquet(output_path)
    n_classes = df[["taxonomy", "height_class"]].drop_duplicates().shape[0]
    print(f"wrote {len(df)} rows ({n_classes} taxonomy/height classes) to {output_path}")


if __name__ == "__main__":
    main()
