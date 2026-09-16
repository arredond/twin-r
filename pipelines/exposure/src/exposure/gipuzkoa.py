"""Discover and download INSPIRE Buildings from the Diputacion Foral de
Gipuzkoa's own WFS -- Gipuzkoa (province 20) runs a separate Foral
cadastral system, entirely outside the national Catastro feed
`catastro.py` crawls (see docs/basque-navarra-cadastral-sources.md).

Gipuzkoa's WFS (unlike Navarra's) doesn't hand back a `next` link, so
paging has to compute `startIndex` itself -- and doing that naively over
the whole territory doesn't work: this server's `startIndex` is a plain
row-skip with no supporting index, so response time grows with the
offset itself (verified this session: startindex=0 -> 0.2s, 100 -> 0.6s,
1000 -> 4.8s, 10000+ -> times out past 40s). Paging the whole ~124k-
feature territory in 5000-row pages this way would mean the later pages
alone take longer than this crawl's total budget, and hammering a public
government server with requests that slow is inconsiderate regardless.

Instead: recursively split the territory's bounding box (a WFS `bbox`
filter is fast regardless of depth -- verified: querying the *entire*
territory's bbox for a `resulttype=hits` count takes ~1.4s) until each
tile has few enough features to fetch in one un-paged request, then fetch
each leaf tile directly. No tile ever needs `startIndex` at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import requests

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
