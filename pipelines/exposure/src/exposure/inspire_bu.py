"""Parse INSPIRE Buildings ("BU") GML into the same clean schema as
parse.py's `load_buildings()` -- shared by the Foral/regional cadastral
sources that publish the standard bu-base/bu-core2d INSPIRE profile
directly (Alava, Navarra, Gipuzkoa; see
docs/basque-navarra-cadastral-sources.md), unlike Catastro's own flattened,
differently-shaped profile (parse.py).

Unlike Catastro, floors/dwellings live flat on the Building feature
itself here -- there's no separate buildingpart file to join against, and
no per-source parser needed beyond this one (verified against a real
sample from each of the three sources: Alava's bulk GML download, and a
live GetFeature response from each of Navarra's and Gipuzkoa's WFS
endpoints -- all three share this same nested shape, just under different
namespace prefixes, which is why this module works purely off local-name
matching rather than a per-source namespace map).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
import pandas as pd

from .parse import add_spatial_index_columns

_GML_ID_ATTR = "{http://www.opengis.net/gml/3.2}id"
_XLINK_HREF_ATTR = "{http://www.w3.org/1999/xlink}href"

# Same sentinel-year rationale as parse.py's own _MIN_PLAUSIBLE_YEAR
# (Catastro's malformed/placeholder construction dates) -- kept as an
# independent constant rather than importing parse.py's private one, since
# these sources have their own (so far clean) date fields and no reason to
# stay coupled to Catastro's specific sentinel-handling logic.
_MIN_PLAUSIBLE_YEAR = 1900

# Construction date lives under a different flattened column name
# depending on how many `DateOfEvent` children the source populates --
# Alava uses a single instant (`anyPoint`), Navarra/Gipuzkoa an interval
# (`beginning`/`end`, sometimes only `end`). First non-null column wins.
_DATE_COLUMNS = ("anyPoint", "beginning", "end")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _current_use_by_gml_id(gml_path: str | Path) -> dict[str, str]:
    """Best-effort per-building current-use codelist value, keyed by the
    feature's `gml:id` (matches `gpd.read_file`'s own `gml_id` column).

    GDAL's GML-to-dataframe flattening (used by `load_buildings` below)
    drops this field silently: the codelist value lives only in an
    `xlink:href` attribute on a self-closing `<bu-base:currentUse>`
    element, not as text content GDAL pulls into a column. Not
    load-bearing for the taxonomy heuristic (`assign_taxonomy` only uses
    construction_year/floors) -- a parse failure here degrades to "no
    current_use," not a broken pipeline.

    A building can report more than one use with a percentage split
    (e.g. 68% residential, 24% commerce) -- picks the highest-percentage
    one, same tie-break "primary use wins" idea Catastro's own single-use
    field implicitly bakes in.
    """
    result: dict[str, str] = {}
    for _, elem in ET.iterparse(str(gml_path), events=("end",)):
        if _local_name(elem.tag) != "Building":
            continue
        gml_id = elem.get(_GML_ID_ATTR)
        best_pct = -1.0
        best_use: str | None = None
        for cu in elem.iter():
            if _local_name(cu.tag) != "CurrentUse":
                continue
            use_val: str | None = None
            pct_val: float | None = None
            for child in cu:
                name = _local_name(child.tag)
                if name == "currentUse":
                    href = child.get(_XLINK_HREF_ATTR)
                    if href:
                        use_val = href.rsplit("/", 1)[-1]
                elif name == "percentage" and child.text:
                    try:
                        pct_val = float(child.text)
                    except ValueError:
                        pct_val = None
            if use_val and (pct_val or 0.0) >= best_pct:
                best_pct = pct_val or 0.0
                best_use = use_val
        if gml_id and best_use:
            result[gml_id] = best_use
        elem.clear()
    return result


def load_buildings(gml_path: str | Path) -> gpd.GeoDataFrame:
    """Load one GML file -- a bulk download (Alava) or a saved WFS
    GetFeature response (Navarra, Gipuzkoa) -- of INSPIRE bu-base/bu-core2d
    Buildings into the shared schema: building_id, floors,
    construction_year, current_use, floor_area_m2, num_dwellings, geometry
    (+ spatial index columns, see parse.py's add_spatial_index_columns).
    """
    gml_path = Path(gml_path)
    buildings = gpd.read_file(gml_path)
    if buildings.empty:
        raise RuntimeError(f"no Building features found in {gml_path}")

    current_use = _current_use_by_gml_id(gml_path)
    buildings["current_use"] = buildings["gml_id"].map(current_use)
    buildings["building_id"] = buildings["gml_id"]

    date_col = next((c for c in _DATE_COLUMNS if c in buildings.columns), None)
    if date_col is not None:
        construction_year = pd.to_datetime(buildings[date_col], errors="coerce", utc=True).dt.year
    else:
        construction_year = pd.Series(pd.NA, index=buildings.index, dtype="Float64")
    buildings["construction_year"] = construction_year.where(
        construction_year > _MIN_PLAUSIBLE_YEAR
    )

    buildings["floors"] = pd.to_numeric(buildings.get("numberOfFloorsAboveGround"), errors="coerce")
    buildings["num_dwellings"] = pd.to_numeric(buildings.get("numberOfDwellings"), errors="coerce")
    # Unlike Catastro's profile (which repurposes a flat "value" field for
    # floor area), these sources' Building feature has no floor-area
    # attribute at all -- only height above ground, in metres, which isn't
    # the same quantity. Left unpopulated rather than guessed at.
    buildings["floor_area_m2"] = pd.NA

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
