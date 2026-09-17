"""Build the Catastro/Araba-code -> real INE-code crosswalk this pipeline's
`municipality_code` column needs.

Context: `catastro.py`'s own `MunicipalityRef` docstring already flagged
that the code its own ATOM feed uses "is *usually* the INE municipality
code, but not always" (citing Madrid: filed as 28900, not INE 28079).
Turns out that's true for the *majority* of the country -- a systematic
check (2026-09, session notes) found 3,767 of 6,829 Catastro-crawled
municipalities' own recorded name doesn't match IGN's name for the INE
code the crawl filed them under, clustered by province (some provinces
100% mismatched, others 0%) -- e.g. Catastro's code "10098" is
Herreruela, but IGN's INE code 10098 is Hinojal (Hinojal's own buildings
are filed under Catastro's code "10101", whose real INE code is 10098's
neighbour, Huélaga). Since `region.py`'s per-municipality partitioning
and `pipeline.build_exposure`'s `municipality_code` column both use
Catastro's code as if it *were* the INE code (ADR-0014's whole "free
join key" premise), every downstream aggregate keyed by it --
`scenario.response.compute_municipality_stats`, `municipalities.py`'s
`attach_building_counts` -- silently attributes a municipality's damage
and building counts to the *wrong* place on the map.

This module builds the correction table two ways, one per data-source
family (`docs/basque-navarra-cadastral-sources.md` catalogs all four):

- **Mainland Spain / Catastro-crawled provinces** (`catastro.py`):
  Catastro itself publishes the authoritative crosswalk as a public,
  unauthenticated web service, `ConsultaMunicipioCodigos` -- no guessing
  needed, straight from the same agency that assigns both codes.
  Confirmed to *not* cover Álava (`CodigoProvincia=01` -> "LA PROVINCIA NO
  EXISTE" -- Álava's Foral system is entirely outside Catastro's national
  registry, same reason `catastro.py` itself can't crawl it).
- **Álava** (`alava.py`): no equivalent web service exists (see above),
  and Álava's own crawl records only a raw 4-digit internal code with no
  municipality name to look up (`region.py`'s `crawl_alava` stamps
  `f"Alava-{ine_code}"` as a placeholder name, not a real one) -- so this
  module locates each Álava part's buildings by centroid-majority-vote
  against IGN's own municipality polygons (`municipalities.parquet")
  instead. This is a one-time, pipeline-time spatial join over 52
  municipalities against ~8,200 polygons -- nothing like the per-request
  cost ADR-0014 removed from the *scenario* request path, and not
  reintroducing it: this module is never called from `services/scenario`.

Vizcaya (`vizcaya.py`) is validated here, not corrected: its own
`Codigo_Mun`-derived code was spot-checked by name (after stripping the
"Vizcaya-" prefix `region.py`'s `crawl_vizcaya` adds ahead of the real
name) and found to match IGN for all 113 municipalities already --
included as an automated check so a future change can't silently
reintroduce a mismatch there without anyone noticing.

Navarra and Gipuzkoa initially looked out of scope entirely --
`navarra.py`/`gipuzkoa.py` crawl their whole territory into one
placeholder-coded part each (`region.py`'s `_NAVARRA_PART_CODE`/
`_GIPUZKOA_PART_CODE`), not per municipality, so there was no
per-municipality code at all to correct. Turned out both sources *do*
carry enough to derive one, just not the same way, or the same way as
each other:

- **Navarra**: no per-municipality code, but every building's own
  `building_id` already carries Navarra's own "concejo" (a Foral
  sub-municipal entity) code as a stable 5-digit prefix -- confirmed via
  Navarra's WFS directly, present on `BU:Building` and
  `CP:CadastralParcel` alike. Not 1:1 with a municipality (several
  concejos can belong to the same one), so `build_navarra_crosswalk`
  resolves each concejo group to its real INE code the same
  centroid-majority-vote way `build_alava_crosswalk` does, then
  `apply_navarra_crosswalk` splits the one whole-territory part
  (buildings *and* exposure/debris, joined back through `building_id`)
  into real per-municipality parts. No re-crawl needed -- already present
  in the currently-crawled data.
- **Gipuzkoa**: the live WFS this pipeline was crawling
  (`gipuzkoa.download_pages`) exposes no municipality information
  per building at all. Its separate ATOM bulk-download service
  (`gipuzkoa.download_bulk`/`load_buildings_bulk`) does carry a real
  `ad:adminUnit` municipality name -- but only on ~12% of buildings
  (spot-checked: tied to which buildings have fuller record enrichment,
  not a general property), nowhere near complete enough to group by. So
  `rebuild_gipuzkoa_partitions` uses that name only as a validation
  spot-check (99.7% agreement where present) and instead does a direct
  per-building `ST_Contains` spatial join against IGN's polygons -- every
  building already has real geometry, so no grouping key is needed at
  all here, unlike Navarra/Álava. This *does* need a re-crawl -- the
  live WFS this pipeline used before doesn't carry enough information to
  fix in place, but the bulk ATOM download it's replaced by is also
  simpler (one small zip, no more bbox-quartering workaround for a slow
  server).

CLI (mainland Catastro + Álava only -- Navarra/Gipuzkoa are re-partition
operations with their own function signatures above, not a rename-style
correction table this CLI's shape fits):
    uv run python -m exposure.municipality_crosswalk \\
        <municipalities.parquet> <parts_dir> <output.parquet>

Writes one row per *mismatched* municipality (`old_code`, `new_code`,
`name`, `source`) to `output.parquet` -- re-run any time either registry
might have changed, or to re-validate after a fresh crawl.
"""

from __future__ import annotations

import shutil
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb
import geopandas as gpd
import pandas as pd
import requests

_CATASTRO_MUNICIPIO_CODIGOS_URL = (
    "http://ovc.catastro.meh.es/ovcservweb/ovcswlocalizacionrc/"
    "ovccallejerocodigos.asmx/ConsultaMunicipioCodigos"
)
_CATASTRO_XML_NS = "{http://www.catastro.meh.es/}"

# Province codes Catastro's national registry doesn't cover -- each runs
# its own separate Foral/regional cadastral system (see module docstring
# and docs/basque-navarra-cadastral-sources.md). Querying the web service
# for these returns an explicit "LA PROVINCIA NO EXISTE" error, confirmed
# directly for 01 this session.
_NON_CATASTRO_PROVINCES = {"01", "20", "31", "48"}

# Placeholder codes navarra.py/gipuzkoa.py stamp for their whole-territory
# (not per-municipality) crawls -- see region.py's own constants of the
# same name. Nothing to correct for these: there's no per-municipality
# code here in the first place.
_NAVARRA_PART_CODE = "31000"
_GIPUZKOA_PART_CODE = "20000"


def _move_staged_merging(staging_dir: Path, parts_dir: Path) -> None:
    """Move every staged part into `parts_dir`, concatenating (by
    `building_id`, deduplicated) rather than overwriting if a same-named
    part already exists there.

    Needed because more than one correction can legitimately target the
    same INE code: e.g. a "Facería" shared-grazing entity (confirmed this
    session: INE code 53002/53083) straddles a province boundary, so both
    Álava's and Navarra's own crosswalk corrections can produce buildings
    for it independently -- a plain overwrite would silently drop
    whichever one moved second.
    """
    for staged in staging_dir.iterdir():
        target = parts_dir / staged.name
        if not target.exists():
            shutil.move(str(staged), str(target))
            continue
        if staged.name.endswith(".buildings.parquet"):
            existing = gpd.read_parquet(target)
            new = gpd.read_parquet(staged)
            merged = pd.concat([existing, new], ignore_index=True).drop_duplicates("building_id")
            merged.to_parquet(target)
            staged.unlink()
        else:
            # exposure/debris parts: same building_id-keyed shape, no
            # geometry column to worry about round-tripping through
            # geopandas for.
            existing_df = pd.read_parquet(target)
            new_df = pd.read_parquet(staged)
            pd.concat([existing_df, new_df], ignore_index=True).drop_duplicates(
                "building_id"
            ).to_parquet(target, index=False)
            staged.unlink()


def _normalize_name(name: str | None) -> str:
    """Uppercase, accent-stripped, alphanumeric-only comparison key --
    good enough to tell "Cornellà de Llobregat" from "Corbera de
    Llobregat" (a genuine code mismatch, both real place names) apart
    from "Vizcaya-ABADIÑO" vs "Abadiño" (a crawl-time label prefix, not a
    code problem) once that prefix is stripped by the caller.
    """
    if not name:
        return ""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return "".join(c for c in name.upper() if c.isalnum())


def fetch_catastro_province_crosswalk(province_code: str, timeout: int = 30) -> pd.DataFrame:
    """One province's full Catastro-code <-> INE-code table, straight from
    Catastro's own `ConsultaMunicipioCodigos` web service -- no auth, a
    plain HTTP GET against its REST-style URL (the same `.asmx` endpoint
    also serves proper SOAP, but the query-string form is simpler and
    works identically).

    Returns one row per municipality Catastro has registered in this
    province: `name` (Catastro's own spelling), `catastro_code` (5-digit,
    <province><cmc> zero-padded -- matches this pipeline's own
    `<code>.buildings.parquet` partition naming), `ine_code` (5-digit,
    <province><cm> zero-padded -- the code IGN's municipality boundaries/
    INE itself actually use). The two are equal for a municipality
    Catastro numbers the same way INE does -- this function returns every
    row regardless, so the caller can tell "confirmed matching" apart
    from "no data returned at all."
    """
    resp = requests.get(
        _CATASTRO_MUNICIPIO_CODIGOS_URL,
        params={"CodigoProvincia": province_code, "CodigoMunicipio": "", "CodigoMunicipioIne": ""},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=timeout,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    ns = _CATASTRO_XML_NS
    rows = []
    for muni in root.iter(f"{ns}muni"):
        name = muni.findtext(f"{ns}nm")
        cd = muni.findtext(f"{ns}locat/{ns}cd")
        cmc = muni.findtext(f"{ns}locat/{ns}cmc")
        cp = muni.findtext(f"{ns}loine/{ns}cp")
        cm = muni.findtext(f"{ns}loine/{ns}cm")
        if not (name and cd and cmc and cp and cm):
            continue
        rows.append(
            {
                "name": name,
                "catastro_code": f"{int(cd):02d}{int(cmc):03d}",
                "ine_code": f"{int(cp):02d}{int(cm):03d}",
            }
        )
    if not rows:
        raise RuntimeError(
            f"no municipalities returned for province {province_code!r} -- "
            "either an unknown province code, or one of the four Foral/"
            "regional territories this service doesn't cover (see "
            "_NON_CATASTRO_PROVINCES)"
        )
    return pd.DataFrame(rows)


def fetch_all_catastro_crosswalks(
    province_codes: list[str], timeout: int = 30, pause_seconds: float = 0.2
) -> pd.DataFrame:
    """`fetch_catastro_province_crosswalk` over every given province,
    concatenated. `pause_seconds` between calls -- a courtesy pause
    against a public government service, not a rate limit we've hit.

    Skips (with a warning, not a crash) any code the service doesn't
    recognize as a province -- covers `_NON_CATASTRO_PROVINCES` and also
    Catastro's own "55"/"56" territorial-office overflow buckets (Ceuta/
    Melilla, already handled by the hardcoded `_CATASTRO_CODE_TO_INE`
    remap in `response.py`/`municipalities.py` -- not this general
    crosswalk's problem to solve a second time) if a caller's
    `parts_dir` scan happens to surface them as a "province".
    """
    frames = []
    for i, province_code in enumerate(province_codes):
        if province_code in _NON_CATASTRO_PROVINCES:
            continue
        try:
            frame = fetch_catastro_province_crosswalk(province_code, timeout=timeout)
        except RuntimeError as e:
            print(f"[{i + 1}/{len(province_codes)}] province {province_code}: SKIPPED ({e})")
            continue
        frames.append(frame)
        print(
            f"[{i + 1}/{len(province_codes)}] province {province_code}: {len(frame)} municipalities"
        )
        if pause_seconds:
            time.sleep(pause_seconds)
    return pd.concat(frames, ignore_index=True)


def build_alava_crosswalk(parts_dir: str | Path, municipalities_path: str | Path) -> pd.DataFrame:
    """Álava's own 4-digit code -> real INE code, via centroid-majority-vote
    spatial matching against IGN's municipality polygons (no Catastro web
    service and no municipality name available for this territory -- see
    module docstring).

    For each `0*.buildings.parquet` part (Álava's own codes all start
    with the province digits "01", 4 digits total -- distinct from every
    other source's 5-digit or 4-digit-but-different-province codes), the
    real INE code is whichever IGN polygon contains the *most* of that
    part's building centroids -- majority vote rather than a single
    averaged centroid, since a concave or oddly-shaped municipality could
    put its buildings' mean position outside its own polygon (or inside a
    neighbour's) even though every individual building is correctly
    inside it.
    """
    parts_dir = Path(parts_dir)
    con = duckdb.connect()
    con.execute("install spatial; load spatial;")

    alava_parts = sorted(
        p for p in parts_dir.glob("01*.buildings.parquet") if len(p.stem.split(".")[0]) == 4
    )
    if not alava_parts:
        raise ValueError(f"no Álava (4-digit '01xx') buildings.parquet parts found in {parts_dir}")

    rows = []
    for part in alava_parts:
        araba_code = part.stem.split(".")[0]
        result = con.execute(
            """
            select m.ine_code, count(*) as n
            from read_parquet(?) as b
            join read_parquet(?) as m
              on ST_Contains(m.geometry, ST_Point(b.centroid_lon, b.centroid_lat))
            group by m.ine_code
            order by n desc
            limit 1
            """,
            [str(part), str(municipalities_path)],
        ).df()
        if result.empty:
            print(
                f"WARNING: no IGN polygon contains any building centroid for Álava code {araba_code}"
            )
            continue
        rows.append(
            {
                "araba_code": araba_code,
                "ine_code": result.iloc[0]["ine_code"],
                "n_matched": int(result.iloc[0]["n"]),
            }
        )
    return pd.DataFrame(rows)


def build_navarra_crosswalk(parts_dir: str | Path, municipalities_path: str | Path) -> pd.DataFrame:
    """Navarra's own per-"concejo" code -> real INE code, via the same
    centroid-majority-vote spatial matching as `build_alava_crosswalk`.

    Unlike Álava, Navarra's crawl (`navarra.py`) never partitions by
    municipality at all -- `region.py`'s `crawl_navarra` writes the whole
    territory into one `_NAVARRA_PART_CODE` ("31000") part, treated
    (wrongly) as if it had no per-municipality information available.
    It does: every building's own `building_id` (e.g.
    "ES.RRTN.BU.001010001A") already carries a 5-digit code as its own
    namespace-local prefix ("00101") -- confirmed against Navarra's WFS
    directly (`nationalCadastralReference`/`inspireId` on both
    `BU:Building` and `CP:CadastralParcel` share it) to be Navarra's own
    "concejo" (a sub-municipal entity Navarra's Foral administration
    uses, below the municipality level) -- *not* the INE code, and not
    1:1 with a municipality either: verified this session that several
    different concejo codes ("12401".."12412") all spatially fall inside
    the *same* real municipality (Ibargoiti, INE 31124), while adjacent
    codes ("12501" vs "12502"/"12503") split across two different real
    municipalities (one of them a shared-grazing "Facería" entity, not an
    ordinary municipality at all). So this has to be resolved spatially,
    same as Álava, just grouped by an embedded id substring instead of by
    separate per-municipality files.

    No re-crawl needed -- this concejo code already survives into the
    currently-parsed `31000.buildings.parquet`'s own `building_id` column
    (unlike Gipuzkoa's own per-building grouping key, which does need a
    re-crawl -- see `docs/decisions` and this module's own notes on
    Gipuzkoa for why the two sources differ here).
    """
    parts_dir = Path(parts_dir)
    navarra_part = parts_dir / f"{_NAVARRA_PART_CODE}.buildings.parquet"
    if not navarra_part.exists():
        raise ValueError(f"{navarra_part} not found")

    con = duckdb.connect()
    con.execute("install spatial; load spatial;")
    result = con.execute(
        """
        with concejos as (
            select
                regexp_extract(building_id, 'ES\\.RRTN\\.BU\\.(\\d{5})', 1) as concejo_code,
                centroid_lon,
                centroid_lat
            from read_parquet(?)
        )
        select c.concejo_code, m.ine_code, count(*) as n
        from concejos c
        join read_parquet(?) as m
          on ST_Contains(m.geometry, ST_Point(c.centroid_lon, c.centroid_lat))
        group by 1, 2
        """,
        [str(navarra_part), str(municipalities_path)],
    ).df()

    # Majority vote per concejo_code (a handful of buildings near a
    # boundary can fall in a neighbouring polygon -- same reasoning as
    # Álava's own majority vote, just over many small groups instead of
    # one per file).
    winners = result.sort_values("n", ascending=False).drop_duplicates("concejo_code")
    return winners[["concejo_code", "ine_code", "n"]].rename(columns={"n": "n_matched"})


def apply_navarra_crosswalk(
    crosswalk: pd.DataFrame, parts_dir: str | Path, municipalities_path: str | Path
) -> None:
    """Re-partition Navarra's single whole-territory part into real
    `<ine_code>.buildings.parquet` files, using `build_navarra_crosswalk`'s
    concejo_code -> ine_code mapping (many concejos can map to the same
    municipality, so this is a real split, not a rename).

    `municipalities_path` is only used to read `n_buildings`-independent
    columns (the polygon parquet isn't touched here) -- actually unused
    directly by this function; kept in the signature for symmetry/future
    validation callers, and to make the "spatial matching already
    happened in build_navarra_crosswalk, this just applies its result"
    split explicit at call sites.

    Old `31000.*` parts are removed only after every new part has been
    written successfully.
    """
    parts_dir = Path(parts_dir)
    navarra_part = parts_dir / f"{_NAVARRA_PART_CODE}.buildings.parquet"
    buildings = gpd.read_parquet(navarra_part)
    buildings["concejo_code"] = buildings.building_id.str.extract(r"ES\.RRTN\.BU\.(\d{5})")

    code_map = dict(zip(crosswalk.concejo_code, crosswalk.ine_code))
    unmapped = set(buildings["concejo_code"].unique()) - set(code_map)
    if unmapped:
        print(
            f"WARNING: {len(unmapped)} concejo codes have no spatial match, dropping their buildings: {unmapped}"
        )

    buildings["municipality_code"] = buildings["concejo_code"].map(code_map)
    buildings = buildings[buildings["municipality_code"].notna()].drop(columns=["concejo_code"])
    code_by_building_id = dict(zip(buildings.building_id, buildings.municipality_code))

    staging_dir = parts_dir / ".navarra_crosswalk_staging"
    staging_dir.mkdir(exist_ok=True)
    for ine_code, group in buildings.groupby("municipality_code"):
        group.to_parquet(staging_dir / f"{ine_code}.buildings.parquet")

    # exposure.parquet doesn't carry municipality_code (only
    # buildings.parquet does, see pipeline.build_exposure) -- split it the
    # same way by joining back through building_id, so a future
    # incremental crawl's resumability check (`<ine_code>.buildings.parquet
    # and <ine_code>.exposure.parquet both exist`) doesn't wrongly think a
    # newly-real-coded municipality was never crawled. `31000.debris.parquet`
    # is deliberately left untouched (not renamed, not split, not deleted)
    # -- out of scope for this correction.
    stale_exposure = parts_dir / f"{_NAVARRA_PART_CODE}.exposure.parquet"
    if stale_exposure.exists():
        df = pd.read_parquet(stale_exposure)
        df["municipality_code"] = df["building_id"].map(code_by_building_id)
        df = df[df["municipality_code"].notna()]
        for ine_code, group in df.groupby("municipality_code"):
            group.drop(columns=["municipality_code"]).to_parquet(
                staging_dir / f"{ine_code}.exposure.parquet", index=False
            )
        stale_exposure.unlink()

    navarra_part.unlink()
    _move_staged_merging(staging_dir, parts_dir)
    staging_dir.rmdir()

    print(
        f"re-partitioned Navarra into {buildings['municipality_code'].nunique()} municipality parts"
    )


def rebuild_gipuzkoa_partitions(
    parts_dir: str | Path, municipalities_path: str | Path, gml_path: str | Path | None = None
) -> None:
    """Replace Gipuzkoa's single whole-territory part with real
    `<ine_code>.buildings.parquet`/`exposure.parquet` partitions.

    Unlike Navarra (grouped by an embedded id, then spatially matched
    *per group*) or Álava (spatially matched per already-separate file),
    Gipuzkoa's bulk ATOM download (`gipuzkoa.download_bulk`/
    `load_buildings_bulk`) gives every single building its own real
    geometry directly, with no useful per-building grouping key of its
    own -- its `ad:adminUnit` municipality name is only populated on
    ~12% of buildings (spot-checked: enrichment appears tied to whether a
    building has a photo/document record, not present for the ordinary
    case), nowhere near complete enough to group by. So this does a
    direct per-building `ST_Contains` spatial join against IGN's
    municipality polygons instead -- cross-checked against the ~12% of
    buildings that *do* carry `admin_name`: 99.7% agreement (the
    remainder are buildings genuinely on a municipal boundary line,
    correctly resolved by whichever polygon their point actually falls
    in). 123,796 buildings against ~88 polygons is a trivial one-time
    join, nothing like the per-request cost ADR-0014 ruled out.

    `gml_path`: pass an already-downloaded/extracted GML to skip
    `gipuzkoa.download_bulk`'s network call (e.g. for a dry run against a
    cached copy) -- fetches fresh otherwise.

    Also runs `pipeline.build_exposure` per resulting municipality (the
    taxonomy assignment step every other source gets at crawl time,
    which Gipuzkoa's old whole-territory part never had a real
    municipality name/code to run against correctly) and writes
    `<ine_code>.exposure.parquet` alongside each buildings part.
    """
    from . import gipuzkoa
    from .parse import add_spatial_index_columns
    from .pipeline import build_exposure

    parts_dir = Path(parts_dir)

    if gml_path is None:
        raw_dir = parts_dir / ".gipuzkoa_raw"
        gml_path = gipuzkoa.download_bulk(raw_dir)

    buildings = gipuzkoa.load_buildings_bulk(gml_path)
    buildings = add_spatial_index_columns(buildings)

    con = duckdb.connect()
    con.execute("install spatial; load spatial;")
    con.register("gipuzkoa_buildings", buildings.drop(columns=["geometry", "admin_name"]))
    matches = con.execute(
        """
        select b.building_id, m.ine_code, m.name
        from gipuzkoa_buildings b
        join read_parquet(?) as m
          on ST_Contains(m.geometry, ST_Point(b.centroid_lon, b.centroid_lat))
        """,
        [str(municipalities_path)],
    ).df()

    n_unmatched = len(buildings) - len(matches)
    if n_unmatched:
        print(
            f"WARNING: {n_unmatched} Gipuzkoa buildings matched no IGN polygon at all, dropping them"
        )

    # A handful of buildings right on the territory's edge spatially fall
    # inside a *neighbouring* province's municipality (Álava/Navarra/
    # Vizcaya) -- confirmed this session: 13 of 123,796 (0.01%). Dropped,
    # not written into that other province's part file: this crawl has no
    # authority over another territory's partition, and writing into it
    # here would either silently overwrite or need a merge this function
    # doesn't attempt -- that other province's own crawl already covers
    # its own buildings correctly.
    matches = matches[matches.ine_code.str.startswith(_GIPUZKOA_PART_CODE[:2])]
    buildings = buildings.merge(matches, on="building_id", how="inner")

    staging_dir = parts_dir / ".gipuzkoa_crosswalk_staging"
    staging_dir.mkdir(exist_ok=True)
    for (ine_code, muni_name), group in buildings.groupby(["ine_code", "name"]):
        # pandas' groupby(...) return type loses the GeoDataFrame subclass
        # statically (same known pandas-stubs limitation as damage.py's own
        # groupby(...).indices) -- it's still a real GeoDataFrame at
        # runtime (groupby on a GeoDataFrame preserves the class), so this
        # is a type-checker-only cast, not a behavior change.
        group = gpd.GeoDataFrame(group.drop(columns=["ine_code", "name"]))  # pyrefly: ignore
        group_buildings, group_exposure = build_exposure(group, muni_name, ine_code)
        group_buildings.to_parquet(staging_dir / f"{ine_code}.buildings.parquet")
        group_exposure.to_parquet(staging_dir / f"{ine_code}.exposure.parquet", index=False)

    # `20000.debris.parquet` is deliberately left untouched (not deleted,
    # not re-partitioned) -- out of scope for this correction, same as
    # Navarra's own debris file above.
    for suffix in ("buildings", "exposure"):
        stale = parts_dir / f"{_GIPUZKOA_PART_CODE}.{suffix}.parquet"
        if stale.exists():
            stale.unlink()

    _move_staged_merging(staging_dir, parts_dir)
    staging_dir.rmdir()

    print(f"re-partitioned Gipuzkoa into {buildings['ine_code'].nunique()} municipality parts")


def validate_vizcaya_crosswalk(
    parts_dir: str | Path, municipalities_path: str | Path
) -> pd.DataFrame:
    """Confirm Vizcaya's own `48xxx` codes still match IGN's INE codes by
    name (region.py's `crawl_vizcaya` stamps "Vizcaya-{name}" onto every
    building, one province-specific prefix stripped here before
    comparing) -- returns only the *mismatched* rows, empty when clean
    (as of 2026-09, all 113 municipalities matched).
    """
    parts_dir = Path(parts_dir)
    con = duckdb.connect()

    parts = con.execute(
        r"""
        select
            regexp_extract(filename, '([^/]+)\.buildings\.parquet$', 1) as catastro_code,
            municipality
        from read_parquet(?, filename=true, union_by_name=true)
        group by 1, 2
        """,
        [str(parts_dir / "48*.buildings.parquet")],
    ).df()
    ign = con.execute(f"select ine_code, name from read_parquet('{municipalities_path}')").df()

    ign_by_code = dict(zip(ign.ine_code, ign.name.map(_normalize_name)))
    parts["norm_vizcaya"] = parts.municipality.str.replace(r"^Vizcaya-", "", regex=True).map(
        _normalize_name
    )
    parts["ign_norm"] = parts.catastro_code.map(ign_by_code)
    mismatched = parts[
        parts.catastro_code.isin(ign_by_code) & (parts.norm_vizcaya != parts.ign_norm)
    ]
    return mismatched[["catastro_code", "municipality"]].rename(
        columns={"catastro_code": "old_code"}
    )


def build_full_crosswalk(
    parts_dir: str | Path, municipalities_path: str | Path, province_codes: list[str] | None = None
) -> pd.DataFrame:
    """The complete correction table across every source this pipeline
    crawls -- see module docstring for the per-source method.

    `province_codes`: mainland provinces to query Catastro's web service
    for. Defaults to every 2-digit province prefix actually present in
    `parts_dir` (excluding the four Foral/regional ones, which this
    function handles separately) -- so a partial regional crawl doesn't
    pay to query provinces it hasn't touched.

    Returns one row per municipality whose partition needs correcting:
    `old_code` (what's currently on disk), `new_code` (the real INE
    code), `name`, `source` ("catastro" | "alava"). Vizcaya is validated
    (see `validate_vizcaya_crosswalk`) and included only if it's no
    longer clean -- a loud signal something upstream changed, not a
    silent auto-fix. Navarra/Gipuzkoa are never included (see module
    docstring).
    """
    parts_dir = Path(parts_dir)

    if province_codes is None:
        province_codes = sorted(
            {
                p.stem.split(".")[0][:2]
                for p in parts_dir.glob("*.buildings.parquet")
                if len(p.stem.split(".")[0]) == 5
                and p.stem.split(".")[0] not in {_NAVARRA_PART_CODE, _GIPUZKOA_PART_CODE}
            }
        )

    catastro = fetch_all_catastro_crosswalks(province_codes)
    catastro_mismatched = catastro[catastro.catastro_code != catastro.ine_code].copy()
    catastro_mismatched["source"] = "catastro"
    catastro_rows = catastro_mismatched.rename(
        columns={"catastro_code": "old_code", "ine_code": "new_code"}
    )[["old_code", "new_code", "name", "source"]]

    alava_path_exists = any(parts_dir.glob("01[0-9][0-9].buildings.parquet"))
    if alava_path_exists:
        alava = build_alava_crosswalk(parts_dir, municipalities_path)
        alava_mismatched = alava[alava.araba_code != alava.ine_code].copy()
        alava_mismatched["name"] = None
        alava_mismatched["source"] = "alava"
        alava_rows = alava_mismatched.rename(
            columns={"araba_code": "old_code", "ine_code": "new_code"}
        )[["old_code", "new_code", "name", "source"]]
    else:
        alava_rows = pd.DataFrame(columns=["old_code", "new_code", "name", "source"])

    vizcaya_mismatched = validate_vizcaya_crosswalk(parts_dir, municipalities_path)
    if not vizcaya_mismatched.empty:
        print(
            f"WARNING: {len(vizcaya_mismatched)} Vizcaya municipalities no longer match "
            "IGN by name -- previously validated clean, something upstream changed:"
        )
        print(vizcaya_mismatched.to_string())

    return pd.concat([catastro_rows, alava_rows], ignore_index=True)


def apply_crosswalk_to_parts(crosswalk: pd.DataFrame, parts_dir: str | Path) -> None:
    """Correct every mismatched municipality's on-disk partition to the
    real INE code `build_full_crosswalk` found for it: rewrites
    `municipality_code` inside `<old_code>.buildings.parquet` (written out
    fresh, not edited in place) and renames its `exposure.parquet` to
    match. `<old_code>.debris.parquet` is deliberately left untouched --
    not renamed, not rewritten -- same as buildings.pmtiles/debris.pmtiles,
    which this function never touches either; a re-tile is a separate,
    explicit step run later, not part of this correction.

    Two-phase (stage, then move into place) rather than a direct rename,
    because the corrections aren't independent: e.g. Hinojal's real
    buildings move `10101 -> 10098`, while `10098`'s *current* (wrong)
    occupant Herreruela moves `10098 -> 10095` in the very same crosswalk
    -- a naive in-place rename could overwrite Herreruela's still-unmoved
    data with Hinojal's before Herreruela's own move runs. Staging every
    correction first, then moving all of them into `parts_dir` only once
    every read has already happened, makes the overall operation safe
    regardless of what order individual corrections are processed in.
    """
    parts_dir = Path(parts_dir)
    staging_dir = parts_dir / ".municipality_crosswalk_staging"
    staging_dir.mkdir(exist_ok=True)

    n_applied = 0
    for row in crosswalk.itertuples(index=False):
        buildings_path = parts_dir / f"{row.old_code}.buildings.parquet"
        if not buildings_path.exists():
            print(
                f"WARNING: no buildings.parquet for old_code {row.old_code!r} ({row.name}), skipping"
            )
            continue

        buildings = gpd.read_parquet(buildings_path)
        buildings = buildings.copy()
        buildings["municipality_code"] = row.new_code
        buildings.to_parquet(staging_dir / f"{row.new_code}.buildings.parquet")
        buildings_path.unlink()

        # `<old_code>.debris.parquet` is deliberately left untouched (not
        # renamed, not moved) -- out of scope for this correction.
        exposure_src = parts_dir / f"{row.old_code}.exposure.parquet"
        if exposure_src.exists():
            shutil.move(str(exposure_src), str(staging_dir / f"{row.new_code}.exposure.parquet"))
        n_applied += 1

    _move_staged_merging(staging_dir, parts_dir)
    staging_dir.rmdir()

    print(f"applied {n_applied}/{len(crosswalk)} corrections to {parts_dir}")


def main() -> None:
    if len(sys.argv) != 4:
        print(
            "usage: python -m exposure.municipality_crosswalk "
            "<municipalities.parquet> <parts_dir> <output.parquet>",
            file=sys.stderr,
        )
        raise SystemExit(2)
    municipalities_path, parts_dir, output_path = sys.argv[1:]

    crosswalk = build_full_crosswalk(parts_dir, municipalities_path)
    crosswalk.to_parquet(output_path, index=False)
    print(f"wrote {len(crosswalk)} corrections to {output_path}")
    print(crosswalk["source"].value_counts())


if __name__ == "__main__":
    main()
