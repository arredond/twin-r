"""Discover, download, and parse cadastral Buildings from the Diputacion
Foral de Bizkaia's own ArcGIS Server WFS -- Vizcaya (province 48) runs a
separate Foral cadastral system, entirely outside the national Catastro
feed `catastro.py` crawls (see docs/basque-navarra-cadastral-sources.md).

Unlike Alava/Navarra/Gipuzkoa (all INSPIRE bu-base/bu-core2d, see
inspire_bu.py), this is **not** an INSPIRE download service at all -- it's
Bizkaia's own flat cadastral schema (`Edificios`, Spanish for
"buildings"), served from a plain ArcGIS Server WFS, a different platform
to the other three's INSPIRE services entirely. That schema needs its own
loader (this module's `load_buildings`), not `inspire_bu.py`'s.

What this source *does* have that Navarra/Gipuzkoa don't: a proper
`Municipios` feature type (Codigo_Pro/Codigo_Mun/Descripcio) that a
standard OGC Filter Encoding query against `Edificios` can filter on
directly -- so, unlike Navarra/Gipuzkoa's whole-territory crawls, this
one is per real municipality, the same shape as Alava's crawl.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from .parse import add_spatial_index_columns

WFS_URL = (
    "https://geo.bizkaia.eus/arcgisserverinspire/services/"
    "LurraldeAntolamendua_PlanificacionTerritorial/Katastro_Catastro_WFS/"
    "MapServer/WFSServer"
)
MUNICIPALITY_TYPE_NAME = "Katastro_Catastro_WFS:Municipios"
BUILDING_TYPE_NAME = "Katastro_Catastro_WFS:Edificios"

# The server's own observed page cap (verified this session) -- unlike
# Gipuzkoa's WFS, this one's `startIndex` costs the same regardless of
# offset (verified: ~10-15s per page at startindex 0, 5000, and 10000
# alike, fetching Bilbao -- the largest municipality, 13,753 buildings),
# so plain count/startIndex paging is fine here; no adaptive tiling
# needed the way gipuzkoa.py's WFS forced.
PAGE_SIZE = 5000

_MUNICIPALITY_RE = re.compile(
    r"<Katastro_Catastro_WFS:Codigo_Mun>(\d+)</Katastro_Catastro_WFS:Codigo_Mun>"
    r"<Katastro_Catastro_WFS:Descripcio>([^<]*)</Katastro_Catastro_WFS:Descripcio>"
)
_NUMBER_RETURNED_RE = re.compile(r'numberReturned="(\d+)"')

# Same "unknown year" sentinel Ano_Constr/Ano_Rehabi/Ano_Reform/Ano_Calcul
# all share in this source (observed: 0, not a malformed-date string the
# way Catastro's own profile has -- see parse.py's _MIN_PLAUSIBLE_YEAR for
# that different case).
_UNKNOWN_YEAR_SENTINEL = 0


@dataclass(frozen=True)
class MunicipalityRef:
    """A municipality within Vizcaya, identified by Bizkaia's own
    province(48) + `Codigo_Mun` numbering, e.g. "48020" for Bilbao
    (`codigo_mun` 20). Same caveat as every other source module in this
    pipeline: this is *this source's* code, not a cross-checked national
    INE code, even though it happens to follow the same province +
    3-digit shape (and, per a spot check against Bilbao/Abadiño, appears
    to actually match the real INE code for most municipalities -- but
    not guaranteed for all 113, see the ~900-numbered outliers observed
    in the raw `Municipios` feature type).
    """

    name: str
    ine_code: str
    codigo_mun: int


def list_municipalities(timeout: int = 30) -> list[MunicipalityRef]:
    """Every municipality in Vizcaya, from this WFS's own `Municipios`
    feature type -- a plain attribute query, not a bulk-geometry one (no
    `Edificios` request needed just to enumerate municipalities)."""
    resp = requests.get(
        WFS_URL,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typenames": MUNICIPALITY_TYPE_NAME,
            "count": 200,  # Vizcaya has 113 municipalities, comfortably under one page
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    municipalities = [
        MunicipalityRef(name=name.strip(), ine_code=f"48{int(code):03d}", codigo_mun=int(code))
        for code, name in _MUNICIPALITY_RE.findall(resp.text)
    ]
    if not municipalities:
        raise RuntimeError(f"no municipalities found from {WFS_URL}")
    return municipalities


def _filter_xml(codigo_mun: int) -> str:
    return (
        '<Filter xmlns="http://www.opengis.net/fes/2.0">'
        "<PropertyIsEqualTo>"
        "<ValueReference>Codigo_Mun</ValueReference>"
        f"<Literal>{codigo_mun}</Literal>"
        "</PropertyIsEqualTo>"
        "</Filter>"
    )


def download_buildings(
    municipality: MunicipalityRef, dest_dir: str | Path, timeout: int = 60
) -> list[Path]:
    """Download one municipality's buildings as one or more raw GML page
    files under `dest_dir` -- most municipalities are a single page;
    Bilbao (the largest, 13,753 buildings observed) needs three at the
    5000-per-page cap.

    Returns the saved page paths, in fetch order -- feed each one through
    `load_buildings` and concatenate.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Path] = []
    start_index = 0
    page_num = 0
    while True:
        resp = requests.get(
            WFS_URL,
            params={
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typenames": BUILDING_TYPE_NAME,
                "count": PAGE_SIZE,
                "startindex": start_index,
                "filter": _filter_xml(municipality.codigo_mun),
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        match = _NUMBER_RETURNED_RE.search(resp.text)
        n_returned = int(match.group(1)) if match else 0
        if n_returned == 0:
            break

        page_path = dest_dir / f"{municipality.ine_code}_page_{page_num:04d}.gml"
        page_path.write_bytes(resp.content)
        pages.append(page_path)

        if n_returned < PAGE_SIZE:
            break
        start_index += PAGE_SIZE
        page_num += 1

    if not pages:
        raise RuntimeError(
            f"no buildings found for municipality {municipality.ine_code}-{municipality.name}"
        )
    return pages


def load_buildings(gml_path: str | Path) -> gpd.GeoDataFrame:
    """Load one page of Bizkaia's `Edificios` GML into the shared schema:
    building_id, floors, construction_year, current_use, floor_area_m2,
    num_dwellings, geometry (+ spatial index columns, see parse.py).

    Unlike `inspire_bu.load_buildings`, every field this needs is already
    flat -- `gpd.read_file` alone is enough, no XML re-walk required
    (Bizkaia's schema has no INSPIRE-style nested codelist attribute the
    way `currentUse` does in the other three sources -- `Codigo_Uso` here
    is a plain top-level text column already).
    """
    gml_path = Path(gml_path)
    buildings = gpd.read_file(gml_path)
    if buildings.empty:
        raise RuntimeError(f"no Edificios features found in {gml_path}")

    buildings["building_id"] = buildings["gml_id"]
    buildings["floors"] = pd.to_numeric(buildings.get("Numero_Alt"), errors="coerce")
    buildings["num_dwellings"] = pd.to_numeric(buildings.get("Numero_Viv"), errors="coerce")
    buildings["current_use"] = buildings.get("Codigo_Uso")

    construction_year = pd.to_numeric(buildings.get("Ano_Constr"), errors="coerce")
    buildings["construction_year"] = construction_year.where(
        construction_year > _UNKNOWN_YEAR_SENTINEL
    )

    # No floor-area attribute on this feature either (only per-building
    # cadastral value fields, a different quantity) -- left unpopulated
    # rather than guessed at, same as the other three sources.
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
    # Source geometry carries a dummy Z=0 (verified) -- dropped so this
    # source's output is 2D like every other one this pipeline produces.
    result["geometry"] = result["geometry"].force_2d()
    return add_spatial_index_columns(result)
