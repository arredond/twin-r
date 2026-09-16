"""CLI: uv run python -m exposure.municipalities_cli <raw_dir> <parts_dir> <municipalities.pmtiles> <municipalities.parquet>

Downloads IGN/CNIG's national municipal boundaries (municipalities.py),
attaches an `n_buildings` count per municipality from whatever
`<ine_code>.buildings.parquet` parts already exist under `parts_dir` (from
a prior `region_cli`/`__main__` run -- run this *after* the exposure crawl,
not instead of it), and writes two outputs:

- `municipalities.pmtiles`: the frontend's low-zoom choropleth layer
  (apps/web/src/components/DamageMap.tsx).
- `municipalities.parquet` (GeoParquet): services/scenario's server-side
  spatial join for per-municipality aggregate stats
  (scenario/response.py's `compute_municipality_stats`) -- a separate,
  much smaller file than re-reading the PMTiles would need, and DuckDB's
  spatial extension reads GeoParquet natively.

Independent of `--provinces`/`--spain`/`--basque-navarra`: the boundaries
dataset always covers the whole country in one download, so there's
nothing region-scoped to pass here -- only which `parts_dir` to read
building counts from.
"""

from __future__ import annotations

import sys
import time

from .municipalities import attach_building_counts, download_and_extract, load_municipalities
from .tile import tile_municipalities


def main() -> None:
    args = sys.argv[1:]
    if len(args) != 4:
        print(
            "usage: python -m exposure.municipalities_cli <raw_dir> <parts_dir> "
            "<municipalities.pmtiles> <municipalities.parquet>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raw_dir, parts_dir, tiles_output, parquet_output = args

    t0 = time.monotonic()
    gml_path = download_and_extract(raw_dir)
    municipalities = load_municipalities(gml_path)
    print(f"parsed {len(municipalities)} municipalities in {time.monotonic() - t0:.0f}s")

    municipalities = attach_building_counts(municipalities, parts_dir)
    n_with_buildings = (municipalities["n_buildings"] > 0).sum()
    print(f"{n_with_buildings}/{len(municipalities)} municipalities matched a buildings part")

    municipalities.to_parquet(parquet_output)
    print(f"wrote municipalities to {parquet_output}")

    t0 = time.monotonic()
    tile_municipalities(municipalities, tiles_output)
    print(f"wrote municipality tiles to {tiles_output} in {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
