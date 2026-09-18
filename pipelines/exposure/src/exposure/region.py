"""Region crawl: download + parse + taxonomy-assign every municipality in a
set of provinces.

See ADR-0005 (docs/decisions/0005-region-scale-crawling.md) for the design
rationale. Summary:

- One buildings/exposure **part file per municipality** under `parts_dir`,
  skipped if already present -- crawling hundreds to thousands of
  municipalities can take a while and may need to be restarted; this makes
  that resumable without redownloading everything.
- Raw GML is deleted after parsing each municipality (can be 100s of MB for
  a large city) -- a region crawl keeping every raw GML around would need
  ~150-200GB of scratch disk for Murcia+Andalucía alone; we only need the
  parsed parquet.
- `buildings.parquet` stays **partitioned** (one file per municipality) --
  DuckDB's `read_parquet()` accepts a glob pattern directly, so nothing
  downstream needs a single combined file. `exposure.parquet` is small
  enough (attributes only, no geometry) to concatenate into one file.
- Tiling reads buildings.parquet parts directly and converts each to
  GeoJSON in a throwaway temp directory just for the `tippecanoe` call
  (which needs GeoJSON/CSV input, not parquet) -- **not** persisted
  alongside the other parts. Two reasons: (1) they're ~3x the size of the
  buildings.parquet parts they're redundant with (found: a full
  Murcia+Andalucía crawl left 2.7GB of these sitting on disk indefinitely,
  see docs/validation-region-expansion.md), and (2) persisting them made
  them part of the crawl's own "is this municipality done" resumability
  check -- deleting them after tiling would make a *later* resumed crawl
  re-download municipalities that were actually already finished. Keeping
  them out of `parts_dir` entirely sidesteps both problems at once.
- A modest thread pool (network-bound: download + GDAL parsing, which
  releases the GIL) -- default 8 workers, kept deliberately conservative
  out of courtesy to a public government server, not because higher
  wouldn't work.
"""

from __future__ import annotations

import gzip
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import alava, gipuzkoa, navarra, vizcaya
from .catastro import MunicipalityRef, list_municipalities
from .debris import compute_debris_envelopes
from .inspire_bu import load_buildings as load_inspire_bu_buildings
from .parse import SPATIAL_INDEX_COLUMNS
from .pipeline import build_exposure, process_municipality
from .tile import tile_geojson_files

# Andalucía's 8 provinces + Murcia (uni-provincial region) -- milestone-2
# first expansion target, chosen for size (see ADR-0005).
MURCIA_ANDALUCIA_PROVINCES = {
    "30": "Murcia",
    "04": "Almería",
    "11": "Cádiz",
    "14": "Córdoba",
    "18": "Granada",
    "21": "Huelva",
    "23": "Jaén",
    "29": "Málaga",
    "41": "Sevilla",
}

# All 50 provinces + Ceuta/Melilla, MINUS the Basque Country (01, 20, 48)
# and Navarra (31) -- those run their own separate cadastral systems (Foral
# offices) not reachable through this INSPIRE feed at all (confirmed: their
# province codes are simply absent from the root ATOM feed). Reaching them
# needs their own per-territory crawl instead (crawl_alava/crawl_navarra/
# crawl_gipuzkoa below, see docs/basque-navarra-cadastral-sources.md) --
# each still writes into the same `parts_dir` this dict's crawl does, so
# combine_exposure/tile_region below pick their output up automatically
# without caring which crawl produced it.
#
# Catastro splits its INSPIRE Buildings ATOM feed by its own territorial
# "delegación" (office), not strictly by INE province -- most provinces
# have exactly one delegación with a matching number, but several have a
# *second* delegación, numbered outside the normal 01-52 province range,
# covering a subset of that same province's municipalities. Missing one
# of these numbers from this dict doesn't error or warn anywhere -- the
# affected municipalities just never appear in any feed this pipeline
# queries, silently absent from the crawl (confirmed this session: Jerez
# de la Frontera, one of Cádiz's larger cities, plus 7 others, filed only
# under delegación 53, not under Cádiz's own 11; Vigo plus 3 others only
# under 54, not Pontevedra's own 36 -- both delegaciones missing from an
# earlier version of this dict, so those 12 municipalities were absent
# from the national dataset entirely until backfilled by hand once
# noticed, see docs/decisions).
#
# Ceuta/Melilla are codes **55**/**56** here, not their real INE province
# codes 51/52 -- confirmed directly against the live root feed, which
# labels them "Territorial office 55 Ceuta"/"Territorial office 56
# Melilla". "51"/"52" *are* real codes in this feed too, but for unrelated
# Murcia/Asturias overflow municipalities (Cartagena, Gijón, etc.) -- an
# earlier version of this dict wrongly assumed 51/52 meant Ceuta/Melilla
# and never crawled 55/56 at all, so those two were missing from the
# national dataset until backfilled by hand (DATA-SOURCES.md has the full
# note, including the `_CATASTRO_CODE_TO_INE` remap this caused downstream
# in services/scenario/response.py and this package's municipalities.py).
#
# None of these overflow delegación numbers (51/52/53/54/55/56) are the
# real INE code for the municipalities filed under them -- every one of
# them needs `municipality_crosswalk.py`'s correction after crawling, the
# same as every "ordinary" mismatched municipality does.
SPAIN_PROVINCES = {
    code: code
    for code in [
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
        "08",
        "09",
        "10",
        "11",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
        "21",
        "22",
        "23",
        "24",
        "25",
        "26",
        "27",
        "28",
        "29",
        "30",
        "32",
        "33",
        "34",
        "35",
        "36",
        "37",
        "38",
        "39",
        "40",
        "41",
        "42",
        "43",
        "44",
        "45",
        "46",
        "47",
        "49",
        "50",
        "51",
        "52",
        "53",
        "54",
        "55",
        "56",
    ]
}
# (01 Álava, 20 Guipúzcoa, 48 Vizcaya, 31 Navarra deliberately excluded)


def _part_paths(parts_dir: Path, ine_code: str) -> tuple[Path, Path]:
    return (
        parts_dir / f"{ine_code}.buildings.parquet",
        parts_dir / f"{ine_code}.exposure.parquet",
    )


def _process_one(
    municipality: MunicipalityRef, raw_dir: Path, parts_dir: Path
) -> tuple[MunicipalityRef, int, float, str | None]:
    """Returns (municipality, n_buildings, elapsed_seconds, error_or_None)."""
    buildings_part, exposure_part = _part_paths(parts_dir, municipality.ine_code)
    if buildings_part.exists() and exposure_part.exists():
        return municipality, -1, 0.0, None  # already done (resume) -- -1 marks "skipped"

    t0 = time.monotonic()
    muni_raw_dir = raw_dir / municipality.ine_code
    try:
        buildings, exposure = process_municipality(municipality, muni_raw_dir, skip_download=False)
        buildings.to_parquet(buildings_part)
        exposure.to_parquet(exposure_part, index=False)
        return municipality, len(buildings), time.monotonic() - t0, None
    except Exception as e:  # noqa: BLE001 -- one municipality's failure shouldn't kill the crawl
        return municipality, 0, time.monotonic() - t0, str(e)
    finally:
        # Raw GML is large and not needed once parsed -- delete it so a
        # region crawl doesn't fill the disk. Runs whether or not parsing
        # succeeded, so a failed municipality doesn't leave its raw GML
        # behind either.
        shutil.rmtree(muni_raw_dir, ignore_errors=True)


def crawl_region(
    province_codes: dict[str, str] | list[str],
    raw_dir: str | Path,
    parts_dir: str | Path,
    max_workers: int = 8,
) -> list[tuple[MunicipalityRef, int, float, str | None]]:
    """Download+parse every municipality in the given provinces.

    Returns a list of (municipality, n_buildings, elapsed_seconds, error)
    results, in completion order -- errors are collected, not raised, so
    one bad municipality doesn't abort the whole crawl.
    """
    raw_dir = Path(raw_dir)
    parts_dir = Path(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    codes = (
        list(province_codes.keys()) if isinstance(province_codes, dict) else list(province_codes)
    )

    municipalities: list[MunicipalityRef] = []
    for code in codes:
        municipalities.extend(list_municipalities(code))
    print(f"found {len(municipalities)} municipalities across {len(codes)} provinces")

    results: list[tuple[MunicipalityRef, int, float, str | None]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_process_one, m, raw_dir, parts_dir): m for m in municipalities}
        for i, future in enumerate(as_completed(futures), 1):
            municipality, n_buildings, elapsed, error = future.result()
            results.append((municipality, n_buildings, elapsed, error))
            if error:
                status = f"FAILED: {error}"
            elif n_buildings == -1:
                status = "skipped (resumed)"
            else:
                status = f"{n_buildings} buildings in {elapsed:.1f}s"
            print(
                f"[{i}/{len(municipalities)}] {municipality.ine_code}-{municipality.name}: {status}"
            )

    return results


# Placeholder "municipality" codes for the two Foral/regional sources that
# publish no per-municipality breakdown at all (Navarra, Gipuzkoa -- see
# navarra.py/gipuzkoa.py's module docstrings). Shaped like a real 5-digit
# INE code (province + 3 digits) but "000" is not a real municipality
# number in either province, so this can't collide with one -- each
# territory still gets exactly one buildings/exposure part, just
# province-wide instead of per-municipality.
_NAVARRA_PART_CODE = "31000"
_GIPUZKOA_PART_CODE = "20000"


def crawl_alava(raw_dir: str | Path, parts_dir: str | Path) -> list[tuple[str, int, str | None]]:
    """Download + parse every municipality in Alava (province 01).

    Unlike `crawl_region`, there's no thread pool here -- Alava's whole
    province is one ~16MB download (alava.download_and_extract), already
    split into per-municipality GML files once extracted, so there's
    nothing to parallelize over network I/O the way Catastro's
    hundreds-of-separate-downloads crawl has.

    Returns (ine_code, n_buildings, error) tuples, one per municipality --
    `ine_code` here is Araba's own 4-digit code (see alava.MunicipalityRef),
    not the national 5-digit INE code.
    """
    raw_dir = Path(raw_dir)
    parts_dir = Path(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    results: list[tuple[str, int, str | None]] = []
    try:
        municipalities = alava.download_and_extract(raw_dir)
    except Exception as e:  # noqa: BLE001 -- report as a failed crawl, don't crash the caller
        shutil.rmtree(raw_dir, ignore_errors=True)
        return [("01", 0, str(e))]

    for municipality in municipalities:
        buildings_part, exposure_part = _part_paths(parts_dir, municipality.ine_code)
        if buildings_part.exists() and exposure_part.exists():
            results.append((municipality.ine_code, -1, None))
            continue
        try:
            assert municipality.gml_path is not None
            buildings = load_inspire_bu_buildings(municipality.gml_path)
            buildings, exposure = build_exposure(
                buildings, f"Alava-{municipality.ine_code}", municipality.ine_code
            )
            buildings.to_parquet(buildings_part)
            exposure.to_parquet(exposure_part, index=False)
            results.append((municipality.ine_code, len(buildings), None))
        except Exception as e:  # noqa: BLE001 -- one municipality's failure shouldn't kill the crawl
            results.append((municipality.ine_code, 0, str(e)))

    shutil.rmtree(raw_dir, ignore_errors=True)
    return results


def _process_one_vizcaya(
    municipality: vizcaya.MunicipalityRef, raw_dir: Path, parts_dir: Path
) -> tuple[str, int, str | None]:
    buildings_part, exposure_part = _part_paths(parts_dir, municipality.ine_code)
    if buildings_part.exists() and exposure_part.exists():
        return (municipality.ine_code, -1, None)

    muni_raw_dir = raw_dir / municipality.ine_code
    try:
        pages = vizcaya.download_buildings(municipality, muni_raw_dir)
        buildings = _concat_buildings([vizcaya.load_buildings(p) for p in pages])
        buildings, exposure = build_exposure(
            buildings, f"Vizcaya-{municipality.name}", municipality.ine_code
        )
        buildings.to_parquet(buildings_part)
        exposure.to_parquet(exposure_part, index=False)
        result = (municipality.ine_code, len(buildings), None)
    except Exception as e:  # noqa: BLE001 -- one municipality's failure shouldn't kill the crawl
        result = (municipality.ine_code, 0, str(e))
    finally:
        shutil.rmtree(muni_raw_dir, ignore_errors=True)
    return result


def crawl_vizcaya(
    raw_dir: str | Path, parts_dir: str | Path, max_workers: int = 8
) -> list[tuple[str, int, str | None]]:
    """Download + parse every municipality in Vizcaya (province 48).

    Unlike Alava, this *does* use a thread pool (like `crawl_region`) --
    Vizcaya has no single bulk download; each of its 113 municipalities
    is its own WFS request (or, for Bilbao, three -- see vizcaya.py's
    page cap), so there's real network-bound work to parallelize.

    Returns (ine_code, n_buildings, error) tuples, one per municipality.
    """
    raw_dir = Path(raw_dir)
    parts_dir = Path(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    municipalities = vizcaya.list_municipalities()
    results: list[tuple[str, int, str | None]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_process_one_vizcaya, m, raw_dir, parts_dir): m for m in municipalities
        }
        for future in as_completed(futures):
            results.append(future.result())
    return results


def crawl_navarra(raw_dir: str | Path, parts_dir: str | Path) -> tuple[str, int, str | None]:
    """Download + parse the whole of Navarra (province 31) as one unit.

    Navarra's WFS has no per-municipality breakdown (see navarra.py) --
    every page gets concatenated into a single buildings/exposure part,
    keyed by `_NAVARRA_PART_CODE` rather than a real municipality code.

    Returns (part_code, n_buildings, error).
    """
    raw_dir = Path(raw_dir)
    parts_dir = Path(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    buildings_part, exposure_part = _part_paths(parts_dir, _NAVARRA_PART_CODE)
    if buildings_part.exists() and exposure_part.exists():
        return (_NAVARRA_PART_CODE, -1, None)

    try:
        pages = navarra.download_pages(raw_dir)
        buildings = _concat_buildings([load_inspire_bu_buildings(p) for p in pages])
        buildings, exposure = build_exposure(buildings, "Navarra", _NAVARRA_PART_CODE)
        buildings.to_parquet(buildings_part)
        exposure.to_parquet(exposure_part, index=False)
        result = (_NAVARRA_PART_CODE, len(buildings), None)
    except Exception as e:  # noqa: BLE001 -- same "collect, don't raise" contract as crawl_region
        result = (_NAVARRA_PART_CODE, 0, str(e))
    finally:
        shutil.rmtree(raw_dir, ignore_errors=True)
    return result


def crawl_gipuzkoa(raw_dir: str | Path, parts_dir: str | Path) -> tuple[str, int, str | None]:
    """Download + parse the whole of Gipuzkoa (province 20) as one unit.

    Fetched via Gipuzkoa's bulk ATOM download (`gipuzkoa.download_bulk`/
    `load_buildings_bulk`) -- one ~34MB zip, no bbox-tiling/pagination
    workaround needed, unlike the live WFS this used to go through
    (`gipuzkoa.download_pages`, kept in gipuzkoa.py for reference/fallback
    only). This still writes one whole-territory part under a placeholder
    code (`_GIPUZKOA_PART_CODE`), same as before -- the ATOM feed's own
    `ad:adminUnit` municipality name is populated on only ~12% of
    buildings, nowhere near complete enough to partition by directly (see
    `municipality_crosswalk.rebuild_gipuzkoa_partitions`'s docstring),
    so real per-municipality codes are still a separate post-crawl step,
    same as Álava/Navarra.

    Returns (part_code, n_buildings, error).
    """
    raw_dir = Path(raw_dir)
    parts_dir = Path(parts_dir)
    parts_dir.mkdir(parents=True, exist_ok=True)

    buildings_part, exposure_part = _part_paths(parts_dir, _GIPUZKOA_PART_CODE)
    if buildings_part.exists() and exposure_part.exists():
        return (_GIPUZKOA_PART_CODE, -1, None)

    try:
        gml_path = gipuzkoa.download_bulk(raw_dir)
        buildings = gipuzkoa.load_buildings_bulk(gml_path)
        buildings, exposure = build_exposure(buildings, "Gipuzkoa", _GIPUZKOA_PART_CODE)
        buildings.to_parquet(buildings_part)
        exposure.to_parquet(exposure_part, index=False)
        result = (_GIPUZKOA_PART_CODE, len(buildings), None)
    except Exception as e:  # noqa: BLE001 -- same "collect, don't raise" contract as crawl_region
        result = (_GIPUZKOA_PART_CODE, 0, str(e))
    finally:
        shutil.rmtree(raw_dir, ignore_errors=True)
    return result


def _concat_buildings(frames: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    combined = pd.concat(frames, ignore_index=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs=frames[0].crs)


def combine_exposure(parts_dir: str | Path, exposure_output: str | Path) -> pd.DataFrame:
    """Concatenate every municipality's exposure part into one file.

    Buildings are *not* combined the same way -- see module docstring:
    buildings.parquet stays partitioned, one file per municipality, queried
    via a glob pattern (e.g. `<parts_dir>/*.buildings.parquet`).
    """
    parts_dir = Path(parts_dir)
    frames = [pd.read_parquet(p) for p in sorted(parts_dir.glob("*.exposure.parquet"))]
    combined = pd.concat(frames, ignore_index=True)
    Path(exposure_output).parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(exposure_output, index=False)
    return combined


def _debris_part_path(parts_dir: Path, ine_code: str) -> Path:
    return parts_dir / f"{ine_code}.debris.parquet"


def _compute_one_debris(
    buildings_part: Path, parts_dir: Path
) -> tuple[str, int, float, str | None]:
    """Returns (ine_code, n_ring_rows, elapsed_seconds, error_or_None)."""
    ine_code = buildings_part.name.split(".", 1)[0]
    debris_part = _debris_part_path(parts_dir, ine_code)
    if debris_part.exists():
        return ine_code, -1, 0.0, None  # already done (resume) -- -1 marks "skipped"

    t0 = time.monotonic()
    try:
        buildings = gpd.read_parquet(buildings_part, columns=["building_id", "geometry"])
        debris = compute_debris_envelopes(buildings)
        debris.to_parquet(debris_part, index=False)
        return ine_code, len(debris), time.monotonic() - t0, None
    except Exception as e:  # noqa: BLE001 -- one municipality's failure shouldn't kill the run
        return ine_code, 0, time.monotonic() - t0, str(e)


def compute_debris_region(
    parts_dir: str | Path, max_workers: int = 8
) -> list[tuple[str, int, float, str | None]]:
    """Compute debris envelopes (ADR-0010) for every already-crawled
    municipality's `buildings.parquet` part, writing one `<ine_code>.debris.parquet`
    part per municipality alongside it.

    **Do not delete existing `*.debris.parquet` parts to force a full
    recompute.** The national run (51.5M ring rows, 7,764 municipalities)
    took multiple sessions and several crashes to complete -- see
    ADR-0010's "Do not redo this from scratch" section before considering
    it. A model change (real debris volumes, street clipping, a different
    wall tolerance) should extend these rows/columns in place, not
    regenerate the geometry from zero.

    Resumable the same way `crawl_region` is: a municipality whose debris
    part already exists is skipped, not recomputed -- debris computation is
    real per-building GIS work (buffering, neighbor differencing), not just
    I/O, so unlike a re-download this is real CPU time worth not repeating.

    Process pool, not thread pool -- unlike `crawl_region`'s I/O-bound
    download+parse step, this stage is dominated by the per-building Python
    loop in `compute_debris_envelopes` (candidate filtering, list-building
    around each shapely call), which holds the GIL between the individual
    GEOS calls that do release it. A thread pool measurably under-utilizes
    multiple cores here; a process pool gives each municipality's
    computation a genuinely separate interpreter. Each worker only ever
    receives one municipality's `building_id`/`geometry` part path and
    returns small scalars (ine_code, count, elapsed, error) -- the actual
    GeoDataFrames stay in the worker process and are never pickled across
    the process boundary, so this doesn't pay a serialization tax on the
    geometry itself.

    Building geometry only (no other attributes) is read from each part --
    debris rings don't depend on floors/construction_year/etc, so there's
    no reason to pull the rest of buildings.parquet's columns into memory.

    Debris rings are computed per-municipality, independently -- a building
    right on a municipality boundary only sees neighbors within its own
    municipality's part, so a party wall shared with a building just across
    the line could be missed and (wrongly) treated as an exterior edge.
    Not fixed here -- flagged as a known limitation, same category as
    ADR-0010's street/open-space clipping follow-up, not blocking a first
    national tiling pass.
    """
    parts_dir = Path(parts_dir)
    building_parts = sorted(parts_dir.glob("*.buildings.parquet"))
    if not building_parts:
        raise ValueError(f"no buildings.parquet parts found in {parts_dir}")

    results: list[tuple[str, int, float, str | None]] = []
    with ProcessPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_compute_one_debris, p, parts_dir): p for p in building_parts}
        for i, future in enumerate(as_completed(futures), 1):
            ine_code, n_rings, elapsed, error = future.result()
            results.append((ine_code, n_rings, elapsed, error))
            if error:
                status = f"FAILED: {error}"
            elif n_rings == -1:
                status = "skipped (resumed)"
            else:
                status = f"{n_rings} ring rows in {elapsed:.1f}s"
            print(f"[{i}/{len(building_parts)}] {ine_code}: {status}")

    return results


def _convert_one_debris_part(part: Path, tmp_dir: Path) -> Path | None:
    """Read one debris.parquet part and write it as gzip-compressed
    GeoJSON Text Sequences (RFC 8142 -- one JSON-encoded Feature per line,
    not a single `{"type": "FeatureCollection", ...}` document).

    Uses geopandas' normal `to_file` (pyogrio/GDAL's `GeoJSONSeq` driver)
    -- much faster than a hand-rolled Python writer, and safe now that two
    real root causes behind an earlier
    `pyogrio.errors.FeatureError: Cannot write feature` are fixed: (1) ~15M
    of 51M ring geometries nationally were invalid from a since-fixed
    `simplify()` bug (repaired in place before this ever ran), and (2) the
    temp filesystem had actually filled up (a stale ~101GB leftover from an
    earlier killed run was masking how close to the edge a full
    uncompressed national conversion already was) -- GDAL surfaces an
    out-of-disk write failure as this same generic error, not a clear
    ENOSPC message.

    `GeoJSONSeq` over plain `GeoJSON`: both GDAL and tippecanoe can stream
    it feature-by-feature rather than buffering/parsing one giant
    `FeatureCollection` document, which is faster on both the write side
    here and tippecanoe's own read side later -- and it's still a format
    tippecanoe documents reading directly (detected by content, same as
    plain GeoJSON). Writes uncompressed first (GDAL has no built-in gzip
    output) then gzips and deletes the intermediate, so peak disk usage per
    file is still small and the *steady-state* footprint across all parts
    stays compressed -- same reason `tile_debris_region` gzips at all
    (tippecanoe reads a gzipped input natively).

    Returns `None` for an empty part (a municipality with no rings at all).
    """
    debris = gpd.read_parquet(part)
    if debris.empty:
        return None
    plain_path = tmp_dir / f"{part.stem}.geojsons"
    debris.to_file(plain_path, driver="GeoJSONSeq")
    gz_path = tmp_dir / f"{part.stem}.geojsons.gz"
    with open(plain_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
        shutil.copyfileobj(src, dst)
    plain_path.unlink()
    return gz_path


def tile_debris_region(
    parts_dir: str | Path, tiles_output: str | Path, max_workers: int = 4
) -> Path:
    """Tile every municipality's debris.parquet part (compute_debris_region)
    into one PMTiles file -- same converted-to-GeoJSON-in-a-throwaway-temp-dir
    pattern as `tile_region`, and the same reasons (tippecanoe needs
    GeoJSON/CSV, not parquet; not worth persisting the conversion output).

    Each part's conversion is fully independent (no shared state, no need
    to see other municipalities), so this parallelizes the same way
    `compute_debris_region` does -- a thread pool, not a process pool:
    both GDAL's write and gzip's compression are C calls that release the
    GIL, so threads get real parallelism here without paying a process
    pool's per-worker memory duplication, which matters given this step's
    already-tight memory conditions at national scale. Kept to a lower
    default (4, vs. `compute_debris_region`'s 8) for the same reason --
    this step's per-file work (writing full GeoJSON text, then gzipping
    it) has a larger transient memory footprint per file than the ring
    computation does.
    """
    parts_dir = Path(parts_dir)
    debris_parts = sorted(parts_dir.glob("*.debris.parquet"))
    if not debris_parts:
        raise ValueError(f"no debris.parquet parts found in {parts_dir}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        geojson_paths = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_convert_one_debris_part, part, tmp_path): part for part in debris_parts
            }
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                if result is not None:
                    geojson_paths.append(result)
                if i % 500 == 0:
                    print(f"[{i}/{len(debris_parts)}] converted to GeoJSON")
        if not geojson_paths:
            raise ValueError(f"every debris part in {parts_dir} was empty")
        return tile_geojson_files(
            sorted(geojson_paths),
            tiles_output,
            layer_name="debris",
            extra_args=["--drop-densest-as-needed"],
        )


def _province_code(part: Path) -> str:
    # Municipality parts are named `<ine_code>.debris.parquet`; the first
    # two digits of a 5-digit INE code are its province, and this holds
    # uniformly across every source this pipeline crawls -- Catastro's own
    # municipalities, and the Foral placeholder codes (_NAVARRA_PART_CODE
    # "31000", _GIPUZKOA_PART_CODE "20000") and Alava/Vizcaya's own
    # per-municipality codes are all still real province-prefixed 5-digit
    # codes, not a special case to handle separately.
    return part.name[:2]


def tile_debris_region_by_province(
    parts_dir: str | Path,
    batches_dir: str | Path,
    tiles_output: str | Path,
    max_workers: int = 4,
) -> Path:
    """Tile every municipality's debris.parquet part into one national
    PMTiles file, one province at a time, merged at the end with
    tippecanoe's own `tile-join` -- an alternative to `tile_debris_region`
    for national scale specifically.

    **`batches_dir`'s existing `*.debris.pmtiles` files are a resumability
    checkpoint, not scratch output -- don't delete them to force a full
    re-tile unless you specifically mean to.** Re-tiling from
    already-computed `debris.parquet` is comparatively cheap, but the
    national run still took ~52 province tilings across multiple sessions
    (several killed by the OS for low memory along the way) to get
    through once. See ADR-0010's "Do not redo this from scratch".

    Why this exists: a single tippecanoe invocation over the full national
    debris dataset (~51.5M ring features, ~8x `buildings.pmtiles`' own
    vertex count for the same country -- see ADR-0010) crashed after ~3
    hours of real work under sustained memory pressure (tippecanoe exit
    code 100, system swap climbing to its ceiling throughout, freed
    instantly once the process died -- exactly the signature of an
    out-of-memory abort, not a data or logic bug). Batching by province
    caps how much any single tippecanoe process has to hold at once --
    Spain's largest province is still a small fraction of the national
    total -- trading one big memory spike for ~52 much smaller ones.

    **Resumable at the province level**, the same principle ADR-0005
    applies to the municipality crawl itself: a province whose
    `<batches_dir>/<code>.debris.pmtiles` already exists is skipped
    entirely, not re-tiled. A crash, an interrupted connection, or a
    deliberate Ctrl-C partway through only costs the *currently in-flight*
    province's work -- every already-finished province's PMTiles stays on
    disk and is picked straight back up on the next call with the same
    `batches_dir`. The final `tile-join` merge is cheap relative to any one
    province's tiling (it only reads/repacks already-built tiles, doing no
    geometry work), so re-running it after an interruption costs seconds to
    minutes, not hours -- safe to just retry rather than needing its own
    checkpointing.
    """
    parts_dir = Path(parts_dir)
    batches_dir = Path(batches_dir)
    batches_dir.mkdir(parents=True, exist_ok=True)

    debris_parts = sorted(parts_dir.glob("*.debris.parquet"))
    if not debris_parts:
        raise ValueError(f"no debris.parquet parts found in {parts_dir}")

    provinces: dict[str, list[Path]] = {}
    for part in debris_parts:
        provinces.setdefault(_province_code(part), []).append(part)

    province_pmtiles: list[Path] = []
    for i, (province, parts) in enumerate(sorted(provinces.items()), 1):
        batch_output = batches_dir / f"{province}.debris.pmtiles"
        if batch_output.exists():
            print(f"[{i}/{len(provinces)}] province {province}: skipped (resumed)")
            province_pmtiles.append(batch_output)
            continue

        t0 = time.monotonic()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            geojson_paths = []
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(_convert_one_debris_part, part, tmp_path): part for part in parts
                }
                for future in as_completed(futures):
                    result = future.result()
                    if result is not None:
                        geojson_paths.append(result)
            if not geojson_paths:
                print(f"[{i}/{len(provinces)}] province {province}: no rings, skipping")
                continue
            # Tiled to a path *inside* the throwaway tmp dir first, only
            # moved to the real `batch_output` (the resumability
            # checkpoint) after tippecanoe exits successfully. Found the
            # hard way tiling province 08 (Barcelona): tippecanoe writes
            # its `-o` target incrementally as it works, so a mid-run
            # failure (here, the 200,000-features-per-tile limit below)
            # still leaves a real, but incomplete/truncated, file sitting
            # at that exact path -- if that path were `batch_output`
            # directly, the next resumed run would see it `.exists()` and
            # wrongly skip re-tiling a province that never actually
            # finished. `--drop-densest-as-needed` is what actually fixes
            # the 200,000-feature error itself (see tile_geojson_files);
            # the temp-then-move here is a second, independent safety net
            # for whatever *other* way a province tiling could fail
            # partway through.
            tmp_output = tmp_path / batch_output.name
            tile_geojson_files(
                sorted(geojson_paths),
                tmp_output,
                layer_name="debris",
                extra_args=["--drop-densest-as-needed"],
            )
            shutil.move(str(tmp_output), str(batch_output))
        province_pmtiles.append(batch_output)
        print(
            f"[{i}/{len(provinces)}] province {province}: "
            f"{len(parts)} municipalities tiled in {time.monotonic() - t0:.0f}s"
        )

    if not province_pmtiles:
        raise ValueError(f"no province batches produced any tiles from {parts_dir}")

    if shutil.which("tile-join") is None:
        raise RuntimeError(
            "tile-join not found on PATH -- it ships with tippecanoe "
            "(e.g. `brew install tippecanoe`) but as a separate binary."
        )

    tiles_output = Path(tiles_output)
    tiles_output.parent.mkdir(parents=True, exist_ok=True)
    # Merged to a temp path first, only moved over `tiles_output` once
    # tile-join succeeds -- `tiles_output` may already be a real, previously
    # -completed national file (see ADR-0010's "Do not redo this from
    # scratch"), and a merge that fails partway (this run has been killed
    # for low memory multiple times already) must never leave a truncated
    # file sitting at that path, the same reasoning as each province
    # batch's own atomic write above.
    with tempfile.TemporaryDirectory() as merge_tmp:
        merged_path = Path(merge_tmp) / tiles_output.name
        try:
            subprocess.run(
                ["tile-join", "-f", "-o", str(merged_path), *[str(p) for p in province_pmtiles]],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            print(e.stdout, file=sys.stderr)
            print(e.stderr, file=sys.stderr)
            raise
        shutil.move(str(merged_path), str(tiles_output))
    return tiles_output


def add_municipalities_to_buildings_tiles(
    parts_dir: str | Path,
    ine_codes: list[str],
    existing_tiles: str | Path,
) -> Path:
    """Merge a handful of already-crawled municipalities' buildings into an
    existing `buildings.pmtiles` via tippecanoe + `tile-join`, **without**
    re-tiling any municipality already baked into `existing_tiles`.

    For backfilling municipalities a prior crawl missed (e.g. Ceuta/Melilla:
    Catastro's own ATOM feed files them under "territorial office" codes
    55/56, not their real INE province codes 51/52 -- confirmed directly
    against the live feed, not documented anywhere Catastro publishes)
    without paying to re-tile all ~12.9M already-tiled buildings.
    `tile_region` always rebuilds from *every* part in `parts_dir` -- fine
    for a from-scratch national build, wasteful (and at this scale, a real
    multi-hour/OOM-risk run, see ADR-0010) for adding a handful of
    municipalities to an already-complete national tile set.

    Same atomic-write reasoning as `tile_debris_region_by_province`:
    `existing_tiles` is only overwritten (via `shutil.move`) once tile-join
    has fully succeeded, so a failure partway through never leaves a
    truncated file at the real path.
    """
    parts_dir = Path(parts_dir)
    existing_tiles = Path(existing_tiles)
    if not existing_tiles.exists():
        raise ValueError(f"{existing_tiles} does not exist -- nothing to merge into")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        geojson_paths = []
        for ine_code in ine_codes:
            part = parts_dir / f"{ine_code}.buildings.parquet"
            if not part.exists():
                raise ValueError(f"no buildings.parquet part for {ine_code!r} in {parts_dir}")
            buildings = gpd.read_parquet(part).drop(columns=SPATIAL_INDEX_COLUMNS, errors="ignore")
            geojson_path = tmp_path / f"{part.stem}.geojson"
            buildings.to_file(geojson_path, driver="GeoJSON")
            geojson_paths.append(geojson_path)

        new_tiles = tile_geojson_files(geojson_paths, tmp_path / "new.pmtiles")

        merged_tiles = tmp_path / "merged.pmtiles"
        try:
            subprocess.run(
                ["tile-join", "-f", "-o", str(merged_tiles), str(existing_tiles), str(new_tiles)],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            print(e.stdout, file=sys.stderr)
            print(e.stderr, file=sys.stderr)
            raise
        shutil.move(str(merged_tiles), str(existing_tiles))
    return existing_tiles


def tile_region(parts_dir: str | Path, tiles_output: str | Path) -> Path:
    """Tile every municipality's buildings.parquet part into one PMTiles file.

    Converts each part to GeoJSON in a throwaway temp directory (tippecanoe
    needs GeoJSON/CSV, not parquet) -- never persisted in `parts_dir`, see
    module docstring for why.
    """
    parts_dir = Path(parts_dir)
    building_parts = sorted(parts_dir.glob("*.buildings.parquet"))
    if not building_parts:
        raise ValueError(f"no buildings.parquet parts found in {parts_dir}")

    with tempfile.TemporaryDirectory() as tmp:
        geojson_paths = []
        for part in building_parts:
            geojson_path = Path(tmp) / f"{part.stem}.geojson"
            # Spatial index columns (centroid/bbox) are for the scenario
            # engine's parquet-level filtering, not useful as per-feature
            # tile attributes -- see parse.py's SPATIAL_INDEX_COLUMNS.
            buildings = gpd.read_parquet(part).drop(columns=SPATIAL_INDEX_COLUMNS, errors="ignore")
            buildings.to_file(geojson_path, driver="GeoJSON")
            geojson_paths.append(geojson_path)
        return tile_geojson_files(geojson_paths, tiles_output)
