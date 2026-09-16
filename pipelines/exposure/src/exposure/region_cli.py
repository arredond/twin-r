"""CLI: uv run python -m exposure.region_cli <raw_dir> <parts_dir> <exposure.parquet> <tiles.pmtiles> [--provinces 30,04,... | --spain] [--basque-navarra] [--max-workers N] [--debris debris.pmtiles]

Defaults to Murcia + Andalucía's 9 provinces (milestone-2 first expansion,
see ADR-0005). Pass `--spain` for every province reachable through the
national Catastro INSPIRE feed (see region.py's SPAIN_PROVINCES) -- this
still excludes the Basque Country + Navarra's own Foral cadastral systems
(province codes 01/20/31/48), which need `--basque-navarra` on top (or
alone) to run their own per-territory crawls (crawl_alava/crawl_navarra/
crawl_gipuzkoa/crawl_vizcaya) instead; those write into the same
`parts_dir`, so nothing downstream (combine_exposure/tile_region) needs to
know which crawl produced which part.
`buildings.parquet` is left partitioned under `parts_dir`
(`<parts_dir>/*.buildings.parquet`) -- there is no single combined
buildings file, by design (see region.py's module docstring).

`--debris <debris.pmtiles>` additionally runs the debris pipeline
(ADR-0010, docs/decisions/0010-debris-envelope-precompute.md) over
whatever `buildings.parquet` parts exist in `parts_dir` once the crawl
above finishes -- opt-in and separate from `tiles_output`, since it's real
extra per-building compute (buffering/neighbor-differencing, not just a
re-tiling of already-parsed geometry) and resumable the same way the crawl
itself is (`compute_debris_region` skips a municipality whose
`<ine_code>.debris.parquet` part already exists).
"""

from __future__ import annotations

import sys
import time

from .region import (
    MURCIA_ANDALUCIA_PROVINCES,
    SPAIN_PROVINCES,
    combine_exposure,
    compute_debris_region,
    crawl_alava,
    crawl_gipuzkoa,
    crawl_navarra,
    crawl_region,
    crawl_vizcaya,
    tile_debris_region,
    tile_region,
)


def main() -> None:
    args = sys.argv[1:]
    max_workers = 8
    if "--max-workers" in args:
        idx = args.index("--max-workers")
        max_workers = int(args[idx + 1])
        del args[idx : idx + 2]

    debris_tiles_output = None
    if "--debris" in args:
        idx = args.index("--debris")
        debris_tiles_output = args[idx + 1]
        del args[idx : idx + 2]

    run_basque_navarra = "--basque-navarra" in args
    if run_basque_navarra:
        args.remove("--basque-navarra")
    explicit_provinces = "--spain" in args or "--provinces" in args

    # "--basque-navarra" alone means just that -- an explicit "--spain" or
    # "--provinces" is what opts into the (otherwise-default) Murcia +
    # Andalucía crawl running alongside it.
    provinces: dict[str, str] = (
        {} if (run_basque_navarra and not explicit_provinces) else (MURCIA_ANDALUCIA_PROVINCES)
    )
    if "--spain" in args:
        args.remove("--spain")
        provinces = SPAIN_PROVINCES
    if "--provinces" in args:
        idx = args.index("--provinces")
        codes = args[idx + 1].split(",")
        provinces = {c: c for c in codes}
        del args[idx : idx + 2]

    if len(args) != 4:
        print(
            "usage: python -m exposure.region_cli <raw_dir> <parts_dir> "
            "<exposure.parquet> <tiles.pmtiles> "
            "[--provinces 30,04,... | --spain] [--basque-navarra] [--max-workers N] "
            "[--debris debris.pmtiles]",
            file=sys.stderr,
        )
        raise SystemExit(2)
    raw_dir, parts_dir, exposure_output, tiles_output = args

    t0 = time.monotonic()
    results = (
        crawl_region(provinces, raw_dir, parts_dir, max_workers=max_workers) if provinces else []
    )
    crawl_elapsed = time.monotonic() - t0

    n_ok = sum(1 for _, n, _, err in results if err is None and n != -1)
    n_skipped = sum(1 for _, n, _, err in results if err is None and n == -1)
    n_failed = sum(1 for _, _, _, err in results if err is not None)
    total_buildings = sum(n for _, n, _, err in results if err is None and n != -1)
    print(
        f"\ncrawl done in {crawl_elapsed:.0f}s: {n_ok} processed, {n_skipped} skipped "
        f"(resumed), {n_failed} failed, {total_buildings} buildings parsed this run"
    )
    if n_failed:
        print("failed municipalities:")
        for municipality, _, _, error in results:
            if error:
                print(f"  {municipality.ine_code}-{municipality.name}: {error}")

    if run_basque_navarra:
        for label, crawl in [
            ("Alava", lambda: crawl_alava(raw_dir, parts_dir)),
            ("Navarra", lambda: [crawl_navarra(raw_dir, parts_dir)]),
            ("Gipuzkoa", lambda: [crawl_gipuzkoa(raw_dir, parts_dir)]),
            ("Vizcaya", lambda: crawl_vizcaya(raw_dir, parts_dir, max_workers=max_workers)),
        ]:
            t0 = time.monotonic()
            territory_results = crawl()
            elapsed = time.monotonic() - t0
            n_ok = sum(1 for _, n, err in territory_results if err is None and n != -1)
            n_skipped = sum(1 for _, n, err in territory_results if err is None and n == -1)
            total = sum(n for _, n, err in territory_results if err is None and n != -1)
            print(
                f"\n{label}: {n_ok} processed, {n_skipped} skipped, {total} buildings in {elapsed:.0f}s"
            )
            for part_code, _, error in territory_results:
                if error:
                    print(f"  FAILED {part_code}: {error}")

    t0 = time.monotonic()
    exposure = combine_exposure(parts_dir, exposure_output)
    print(
        f"wrote {len(exposure)} exposure rows to {exposure_output} in {time.monotonic() - t0:.0f}s"
    )

    t0 = time.monotonic()
    tile_region(parts_dir, tiles_output)
    print(f"wrote tiles to {tiles_output} in {time.monotonic() - t0:.0f}s")

    print(f"\nbuildings.parquet is partitioned: {parts_dir}/*.buildings.parquet")

    if debris_tiles_output:
        t0 = time.monotonic()
        debris_results = compute_debris_region(parts_dir, max_workers=max_workers)
        debris_elapsed = time.monotonic() - t0
        n_ok = sum(1 for _, n, _, err in debris_results if err is None and n != -1)
        n_skipped = sum(1 for _, n, _, err in debris_results if err is None and n == -1)
        n_failed = sum(1 for _, _, _, err in debris_results if err is not None)
        total_rings = sum(n for _, n, _, err in debris_results if err is None and n != -1)
        print(
            f"\ndebris compute done in {debris_elapsed:.0f}s: {n_ok} processed, "
            f"{n_skipped} skipped (resumed), {n_failed} failed, "
            f"{total_rings} ring rows written this run"
        )
        if n_failed:
            print("failed debris municipalities:")
            for ine_code, _, _, error in debris_results:
                if error:
                    print(f"  {ine_code}: {error}")

        t0 = time.monotonic()
        tile_debris_region(parts_dir, debris_tiles_output)
        print(f"wrote debris tiles to {debris_tiles_output} in {time.monotonic() - t0:.0f}s")


if __name__ == "__main__":
    main()
