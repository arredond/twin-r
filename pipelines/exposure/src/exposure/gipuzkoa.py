"""Discover and download INSPIRE Buildings from the Diputacion Foral de
Gipuzkoa -- Gipuzkoa (province 20) runs a separate Foral cadastral
system, entirely outside the national Catastro feed `catastro.py` crawls
(see docs/basque-navarra-cadastral-sources.md).

Two access paths exist, and this module now uses the second:

- **The live WFS** (`WFS_URL`/`download_pages` below, kept for
  reference/fallback): doesn't hand back a `next` link, so paging has to
  compute `startIndex` itself -- and doing that naively over the whole
  territory doesn't work: this server's `startIndex` is a plain row-skip
  with no supporting index, so response time grows with the offset
  itself (verified: startindex=0 -> 0.2s, 100 -> 0.6s, 1000 -> 4.8s,
  10000+ -> times out past 40s). Paging the whole ~124k-feature territory
  in 5000-row pages this way needed a recursive bbox-quartering workaround
  (`_leaf_tiles`) just to keep every single request small. Its own
  per-building response also never includes a municipality name or
  code at all -- only a `https://ssl6.gipuzkoa.net/Catastro/map.htm?
  id=NN&...` external-reference URL, whose `id` turns out (spot-checked
  this session against IGN's own municipality polygons) to be Gipuzkoa's
  internal per-municipality identifier, but that's a URL-parsing hack,
  not a real field.
- **The bulk ATOM download** (`ATOM_URL`/`download_bulk`/
  `load_buildings_bulk` below): one predefined dataset, one ~34MB zip,
  no pagination/tiling needed at all -- *and* its GML actually carries a
  proper `ad:adminUnit` (INSPIRE Addresses' admin-unit geographic name,
  Spanish and Basque spellings both given) on every building, which the
  live WFS's own response drops entirely. That's a real municipality
  name, matchable against IGN's `municipalities.parquet` the same
  name-based way `municipality_crosswalk.py` already does for mainland
  Catastro provinces -- no spatial join needed here, unlike Álava/Navarra
  (see that module's docstring for why those two still need one).
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
import pandas as pd
import pyproj
import requests
from shapely.geometry import Polygon

WFS_URL = "https://b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_bu"
TYPE_NAME = "bu-ext2d:Building"

# The server's own observed page cap (verified this session, same as
# Navarra's) -- also doubles as the "small enough to fetch in one
# request, no pagination" threshold for a leaf tile below.
PAGE_SIZE = 5000

# Gipuzkoa's WGS84 bounding box, from this WFS's own GetCapabilities
# (verified live this session) -- padded slightly so a building right on
# the boundary can't fall outside every tile by a rounding hair.
_TERRITORY_BBOX = (42.88, -2.61, 43.41, -1.71)  # (min_lat, min_lon, max_lat, max_lon)

# Recursive quad-tree splitting shouldn't realistically need to go this
# deep (a handful of splits gets any tile from 124k down under 5000) --
# this is a safety net against an unexpected pathological density pocket
# turning into an infinite loop, not an expected code path.
_MAX_SPLIT_DEPTH = 12

_NUMBER_MATCHED_RE = re.compile(r'numberMatched="(\d+)"')

BBox = tuple[float, float, float, float]  # (min_lat, min_lon, max_lat, max_lon)


@dataclass(frozen=True)
class _Tile:
    bbox: BBox
    depth: int


def _bbox_param(bbox: BBox) -> str:
    min_lat, min_lon, max_lat, max_lon = bbox
    return f"{min_lat},{min_lon},{max_lat},{max_lon}"


def _count_in_bbox(bbox: BBox, timeout: int) -> int:
    resp = requests.get(
        WFS_URL,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typenames": TYPE_NAME,
            "count": 1,
            "resulttype": "hits",
            "bbox": _bbox_param(bbox),
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    match = _NUMBER_MATCHED_RE.search(resp.text)
    return int(match.group(1)) if match else 0


def _split(bbox: BBox) -> tuple[BBox, BBox, BBox, BBox]:
    min_lat, min_lon, max_lat, max_lon = bbox
    mid_lat = (min_lat + max_lat) / 2
    mid_lon = (min_lon + max_lon) / 2
    return (
        (min_lat, min_lon, mid_lat, mid_lon),
        (min_lat, mid_lon, mid_lat, max_lon),
        (mid_lat, min_lon, max_lat, mid_lon),
        (mid_lat, mid_lon, max_lat, max_lon),
    )


def _leaf_tiles(timeout: int) -> list[BBox]:
    """Recursively quarter `_TERRITORY_BBOX` until every tile's feature
    count is safely under `PAGE_SIZE` (a margin below it, not right at
    the cap -- a tile landing exactly on the boundary shouldn't risk
    silently truncating at the server's own page limit)."""
    leaves: list[BBox] = []
    stack = [_Tile(_TERRITORY_BBOX, depth=0)]
    while stack:
        tile = stack.pop()
        count = _count_in_bbox(tile.bbox, timeout)
        if count == 0:
            continue
        if count < PAGE_SIZE - 500 or tile.depth >= _MAX_SPLIT_DEPTH:
            leaves.append(tile.bbox)
            continue
        stack.extend(_Tile(child, tile.depth + 1) for child in _split(tile.bbox))
    return leaves


def download_pages(dest_dir: str | Path, timeout: int = 60) -> list[Path]:
    """Download every building in Gipuzkoa's territory as a set of raw
    GML tile files under `dest_dir`, one per leaf tile from
    `_leaf_tiles`.

    Returns the saved tile paths -- feed each one through
    `inspire_bu.load_buildings` and concatenate. A building whose
    geometry straddles a tile boundary can match more than one tile's
    bbox filter, so it may appear more than once across the full set;
    `drop_duplicates(subset="building_id")` after concatenating (its
    `gml:id` is stable across tiles) is the caller's job, same as
    region.py does for the rest of this pipeline's parts.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for i, bbox in enumerate(_leaf_tiles(timeout)):
        resp = requests.get(
            WFS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typenames": TYPE_NAME,
                "count": PAGE_SIZE,
                "bbox": _bbox_param(bbox),
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        tile_path = dest_dir / f"tile_{i:04d}.gml"
        tile_path.write_bytes(resp.content)
        paths.append(tile_path)

    if not paths:
        raise RuntimeError(f"no building tiles fetched from {WFS_URL}")
    return paths


# --- Bulk ATOM download (preferred over the WFS tiling above) ---

ATOM_ROOT_URL = "https://b5m.gipuzkoa.eus/web5000/es/servicios-inspire/edificios"
# The ATOM feed's own zip href, crawled for rather than pinned -- same
# "survives the service moving the file" rationale as catastro.py's own
# ATOM crawl and alava.py's EPSG:4258-entry crawl.
_ATOM_FEED_URL = "https://b5m.gipuzkoa.eus/inspire/download/buildings.xml"
_ATOM_ZIP_HREF_RE = re.compile(r'href="([^"]+\.zip)"')

_NS = {
    "gml": "http://www.opengis.net/gml/3.2",
    "base": "http://inspire.ec.europa.eu/schemas/base/3.3",
    "bu-base": "http://inspire.ec.europa.eu/schemas/bu-base/4.0",
    "ad": "http://inspire.ec.europa.eu/schemas/ad/4.0",
    "gn": "http://inspire.ec.europa.eu/schemas/gn/4.0",
}

# The bulk GML's own declared CRS (verified this session, its
# gml:Envelope/gml:Polygon both say so) -- ETRS89 / UTM zone 30N, *not*
# the live WFS's EPSG:4258 (geographic) -- reprojected to plain lon/lat
# (EPSG:4326, near-identical to 4258 at this scale) to match every other
# source this pipeline crawls.
_SOURCE_CRS = "EPSG:3042"
_TARGET_CRS = "EPSG:4326"

# 0 is this source's own "unknown year" sentinel, verified against
# real dateOfConstruction values that come back e.g. "0001-01-01" --
# same shape of problem parse.py's _MIN_PLAUSIBLE_YEAR guards against
# for Catastro's own profile.
_MIN_PLAUSIBLE_YEAR = 1900


def _zip_url(timeout: int) -> str:
    resp = requests.get(_ATOM_FEED_URL, timeout=timeout)
    resp.raise_for_status()
    match = _ATOM_ZIP_HREF_RE.search(resp.text)
    if not match:
        raise RuntimeError(f"buildings zip link not found in {_ATOM_FEED_URL}")
    return match.group(1)


def download_bulk(dest_dir: str | Path, timeout: int = 120) -> Path:
    """Download (once) + extract Gipuzkoa's whole-territory buildings zip
    from its ATOM download service -- one ~34MB download, no WFS
    pagination/tiling needed (contrast `download_pages` above).

    Returns the path to the main buildings GML (`ES.GFA.BU.gml`) -- the
    zip's second file, `ES.GFA.BUO.gml`, is "other constructions"
    (non-Building INSPIRE features Gipuzkoa also publishes in the same
    download), not needed here.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    zip_url = _zip_url(timeout)
    resp = requests.get(zip_url, timeout=timeout)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".gml") and "BUO" not in n]
        if len(names) != 1:
            raise RuntimeError(f"expected one buildings GML in {zip_url}, found {names}")
        zf.extract(names[0], dest_dir)
        return dest_dir / names[0]


def _spa_admin_name(building: ET.Element) -> str | None:
    """The Spanish-language municipality name from a Building's
    `ad:adminUnit` entries (each Building carries both a Basque and a
    Spanish spelling, per INSPIRE Addresses' multi-language model) --
    `None` for the rare building with no admin unit at all, left for the
    caller to decide how to handle rather than silently dropping it here.
    """
    for admin_unit in building.iter(f"{{{_NS['ad']}}}adminUnit"):
        language = admin_unit.find(f".//{{{_NS['gn']}}}language")
        text = admin_unit.find(f".//{{{_NS['gn']}}}text")
        if language is not None and language.text == "spa" and text is not None:
            return text.text
    return None


def _polygon_from_pos_list(pos_list: str, transformer: pyproj.Transformer) -> Polygon:
    """One exterior ring's `gml:posList` (space-separated Northing
    Easting pairs, per `_SOURCE_CRS`'s axis order) -> a lon/lat Polygon.

    Interior rings (holes) aren't read -- this pipeline's other sources
    (Catastro's own flattened profile, `inspire_bu.load_buildings` via
    `gpd.read_file`) don't preserve them as a modeled concept downstream
    either (nothing reads a building's hole geometry), so this matches
    the existing fidelity level rather than under- or over-building it
    for this one source.
    """
    values = [float(v) for v in pos_list.split()]
    pairs = list(zip(values[1::2], values[0::2]))  # (easting, northing) for always_xy transform
    lons, lats = transformer.transform([p[0] for p in pairs], [p[1] for p in pairs])
    return Polygon(zip(lons, lats))


def load_buildings_bulk(gml_path: str | Path) -> gpd.GeoDataFrame:
    """Stream-parse Gipuzkoa's bulk ATOM GML (`download_bulk`'s output)
    into the shared schema: building_id, floors, construction_year,
    current_use, num_dwellings, geometry, plus `admin_name` (this
    source's real municipality name -- not part of the shared schema,
    consumed by `region.py`'s Gipuzkoa crawl to resolve the true INE
    code via `municipality_crosswalk.build_gipuzkoa_crosswalk` before
    `pipeline.build_exposure` stamps `municipality`/`municipality_code`).

    Streamed via `ElementTree.iterparse` + `elem.clear()` rather than
    `gpd.read_file` -- this file is ~1GB uncompressed (~124k buildings),
    and (same reasoning as `inspire_bu.py`'s own `currentUse` codelist
    walk) `gpd.read_file`'s GML flattening wouldn't pull `ad:adminUnit`
    into a column at all even if memory weren't a concern.
    """
    transformer = pyproj.Transformer.from_crs(_SOURCE_CRS, _TARGET_CRS, always_xy=True)

    rows: list[dict] = []
    context = ET.iterparse(str(gml_path), events=("end",))
    for _, elem in context:
        if not elem.tag.endswith("}Building"):
            if elem.tag.endswith("}featureMember"):
                elem.clear()
            continue

        building_id = elem.get(f"{{{_NS['gml']}}}id")
        floors_el = elem.find(f".//{{{_NS['bu-base']}}}numberOfFloorsAboveGround")
        dwellings_el = elem.find(f".//{{{_NS['bu-base']}}}numberOfDwellings")
        pos_list_el = elem.find(f".//{{{_NS['gml']}}}posList")

        date_begin = elem.find(f".//{{{_NS['bu-base']}}}DateOfEvent/{{{_NS['bu-base']}}}beginning")
        date_end = elem.find(f".//{{{_NS['bu-base']}}}DateOfEvent/{{{_NS['bu-base']}}}end")
        date_text = (date_begin.text if date_begin is not None else None) or (
            date_end.text if date_end is not None else None
        )

        # A building can carry more than one CurrentUse entry (mixed-use
        # buildings, each with its own percentage) -- take the largest
        # share as this building's single current_use, same simplification
        # inspire_bu.py's own flat schema already makes.
        current_use = None
        best_pct = -1.0
        for cu in elem.iter(f"{{{_NS['bu-base']}}}CurrentUse"):
            use_el = cu.find(f"{{{_NS['bu-base']}}}currentUse")
            pct_el = cu.find(f"{{{_NS['bu-base']}}}percentage")
            href = use_el.get("{http://www.w3.org/1999/xlink}href") if use_el is not None else None
            pct = float(pct_el.text) if pct_el is not None and pct_el.text else 0.0
            if href and pct > best_pct:
                current_use = href.rsplit("/", 1)[-1]
                best_pct = pct

        if building_id is None or pos_list_el is None or pos_list_el.text is None:
            elem.clear()
            continue

        rows.append(
            {
                "building_id": building_id,
                "floors": float(floors_el.text)
                if floors_el is not None and floors_el.text
                else None,
                "construction_year": _plausible_year(date_text),
                "current_use": current_use,
                "num_dwellings": float(dwellings_el.text)
                if dwellings_el is not None and dwellings_el.text
                else None,
                "admin_name": _spa_admin_name(elem),
                "geometry": _polygon_from_pos_list(pos_list_el.text, transformer),
            }
        )
        elem.clear()

    if not rows:
        raise RuntimeError(f"no Building features parsed from {gml_path}")

    return gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs=_TARGET_CRS)


def _plausible_year(date_text: str | None) -> float | None:
    if not date_text:
        return None
    try:
        year = int(date_text[:4])
    except ValueError:
        return None
    return float(year) if year > _MIN_PLAUSIBLE_YEAR else None
