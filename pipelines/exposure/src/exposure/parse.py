"""Parse Catastro INSPIRE Buildings GML into a clean per-building GeoDataFrame.

Catastro publishes two relevant feature types per municipality:

- `<ref>.building.gml`: one row per building -- footprint geometry, current
  use, construction date. `numberOfFloorsAboveGround` is frequently null
  here even when populated at the building-*part* level, so floors come from
  the other file.
- `<ref>.buildingpart.gml`: one row per building *part* (a building can have
  several parts at different heights, e.g. a rear extension) -- floors
  (above/below ground) per part, linked to its parent building via
  `localId` sharing the building's localId with a `_partN` suffix.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path

import geopandas as gpd
import pandas as pd

_PART_SUFFIX_RE = re.compile(r"_part\d+$")

# Catastro uses more than one placeholder for "construction year not
# recorded": literal 1900-01-01, and malformed dates like "--01-01" (empty
# year field) that pandas parses as year 0/1. Observed directly in Lorca's
# data (a `--01-01T00:00:00` beginning date parses to year 0) -- treat
# anything at or below this floor as unknown rather than a real date, since
# no cadastral record predates the medieval period in a way we'd trust
# without cross-checking.
_MIN_PLAUSIBLE_YEAR = 1900


def _load_floors_by_building(source_dir: Path) -> pd.Series:
    part_path = next(source_dir.glob("*.buildingpart.gml"))
    parts = gpd.read_file(part_path, columns=["localId", "numberOfFloorsAboveGround"])
    parts["building_id"] = parts["localId"].str.replace(_PART_SUFFIX_RE, "", regex=True)
    floors = parts.groupby("building_id")["numberOfFloorsAboveGround"].max()
    return floors


def load_buildings(source_dir: str | Path) -> gpd.GeoDataFrame:
    """Load one municipality's buildings into a clean GeoDataFrame.

    Columns: building_id, geometry (footprint, EPSG:4326), floors,
    construction_year (nullable Int, None if unknown/sentinel),
    current_use, floor_area_m2, num_dwellings.
    """
    source_dir = Path(source_dir)
    building_path = next(
        p for p in source_dir.glob("*.building.gml") if "buildingpart" not in p.name
    )

    buildings = gpd.read_file(building_path)
    floors_by_building = _load_floors_by_building(source_dir)

    buildings["building_id"] = buildings["localId"]
    buildings["floors"] = buildings["building_id"].map(floors_by_building)
    # A handful of buildings have no matching part record -- fall back to
    # the (often null) building-level field rather than dropping them.
    buildings["floors"] = buildings["floors"].fillna(
        pd.to_numeric(buildings.get("numberOfFloorsAboveGround"), errors="coerce")
    )

    construction_year = pd.to_datetime(buildings["beginning"], errors="coerce").dt.year
    buildings["construction_year"] = construction_year.where(
        construction_year > _MIN_PLAUSIBLE_YEAR
    )

    buildings["current_use"] = buildings["currentUse"]
    buildings["floor_area_m2"] = pd.to_numeric(buildings.get("value"), errors="coerce")
    buildings["num_dwellings"] = pd.to_numeric(buildings.get("numberOfDwellings"), errors="coerce")

    result = buildings[
        [
            "building_id",
            "floors",
            "construction_year",
            "current_use",
            "floor_area_m2",
            "num_dwellings",
            "geometry",
        ]
    ].copy()
    result = gpd.GeoDataFrame(result, geometry="geometry", crs=buildings.crs)
    result = result.to_crs("EPSG:4326")
    return add_spatial_index_columns(result)


# Columns added by add_spatial_index_columns -- useful for the scenario
# engine's parquet-level filtering (engine.py), redundant baggage in the
# *tiled* output (tile.py/region.py): the frontend only ever needs
# building_id + geometry from a tile, and re-shipping these as vector-tile
# attributes on every one of millions of features measurably bloats
# buildings.pmtiles for no benefit (found: +45% tile size on the
# Murcia+Andalucía dataset before this was excluded).
SPATIAL_INDEX_COLUMNS = [
    "centroid_lon",
    "centroid_lat",
    "bbox_xmin",
    "bbox_ymin",
    "bbox_xmax",
    "bbox_ymax",
]


def add_spatial_index_columns(buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Precompute centroid + bounding box columns onto a buildings GeoDataFrame.

    Precomputing these (project principle: static files over on-the-fly
    compute) buys two things once stored as plain parquet columns rather
    than derived at query time from the geometry column:

    1. The scenario engine's spatial pre-filter can read `centroid_lon`/
       `centroid_lat` directly instead of calling `ST_Centroid` on every
       row on every request.
    2. Parquet stores per-row-group min/max statistics for plain numeric
       columns (not for WKB geometry blobs) -- DuckDB's reader can use
       those stats to skip whole row groups/files that can't match a
       bounding-box predicate, without reading their geometry at all. This
       is what actually fixes the "touches all 819 municipality files
       regardless of rupture location" problem from
       docs/validation-region-expansion.md §4, not just a speed tweak.

    `bbox_*` follows GeoParquet's own convention for exactly this purpose
    (a struct/columns alongside geometry for non-point features, since a
    building's footprint -- not just its centroid -- has spatial extent).
    """
    # geopandas warns that centroid-on-geographic-CRS (lon/lat degrees, not
    # a projected CRS) is "likely incorrect" -- true in general, irrelevant
    # here: building footprints span tens of metres, and this centroid only
    # ever feeds a spatial filter at the scale of tens to hundreds of km
    # (engine.py). Reprojecting per municipality for sub-metre centroid
    # accuracy we don't need isn't worth the added complexity.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Geometry is in a geographic CRS")
        centroids = buildings.geometry.centroid
    bounds = buildings.geometry.bounds  # columns: minx, miny, maxx, maxy

    buildings = buildings.copy()
    buildings["centroid_lon"] = centroids.x
    buildings["centroid_lat"] = centroids.y
    buildings["bbox_xmin"] = bounds["minx"]
    buildings["bbox_ymin"] = bounds["miny"]
    buildings["bbox_xmax"] = bounds["maxx"]
    buildings["bbox_ymax"] = bounds["maxy"]
    return buildings
