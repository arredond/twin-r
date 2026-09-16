"""Download + tile Spain's municipal boundaries (IGN/CNIG), for the map's
low-zoom choropleth (aggregate stats per municipality, buildings only shown
once zoomed in further -- see apps/web/src/components/DamageMap.tsx).

Source: IGN's INSPIRE Administrative Units ATOM feed
(https://www.ign.es/atom/dataset_feeds/lin_lim_mun.es.xml), which resolves
to one GML zip covering the whole country (~66MB, CC BY 4.0 ign.es;
verified downloadable with no auth). That zip bundles both
`AdministrativeBoundary` (line) and `AdministrativeUnit` (polygon) feature
types at four admin levels (country/CCAA/province/municipio) -- this module
only wants the municipio-level `AdministrativeUnit` polygons, which arrive
as a single ~144MB `au_AdministrativeUnit_4thOrder0.gml` (8,220 features,
under the 10,000-per-file cap the other admin levels sometimes split on).

Each feature's `nationalCode` is an 11-digit IGN-internal code, not the
standard 5-digit INE municipality code -- but its **last 5 digits are** the
INE code (verified this session: Lorca's nationalCode `34143030024` ends in
`30024`, its real INE code; Madrid's `34132828079` ends in `28079`). That
lets this module join straight onto the exposure pipeline's own per-
municipality `<ine_code>.buildings.parquet` partitioning (region.py) without
either side needing to change.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

ATOM_FEED_URL = "https://www.ign.es/atom/dataset_feeds/lin_lim_mun.es.xml"

# centrodedescargas.cnig.es (unlike ign.es itself) drops the connection on
# requests' default User-Agent (verified this session: bare `requests.get`
# on the zip URL fails with a RemoteDisconnected before any response line
# -- curl and a browser-like UA both succeed) -- send one just for the zip
# download below.
_ZIP_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}

# 4th-order = municipio-level AdministrativeUnit (polygon), see module
# docstring -- matched by filename rather than hardcoded, in case IGN ever
# splits it across multiple <10000-feature parts the way the boundary
# (line) layers already are.
_MUNICIPIO_UNIT_RE = re.compile(r"^au_AdministrativeUnit_4thOrder\d*\.gml$")

# INE municipality codes are 5 digits (2-digit province + 3-digit
# municipality) -- see this module's docstring for how that was confirmed
# against IGN's own 11-digit nationalCode.
_INE_CODE_LEN = 5


def _zip_url(timeout: int) -> str:
    resp = requests.get(ATOM_FEED_URL, timeout=timeout)
    resp.raise_for_status()
    match = re.search(r'href="(https://[^"]+lineas_limite_gml\.zip)"', resp.text)
    if not match:
        raise RuntimeError(f"municipal boundaries zip link not found in {ATOM_FEED_URL}")
    return match.group(1)


def download_and_extract(dest_dir: str | Path, timeout: int = 300) -> Path:
    """Download (once) + extract just the municipio-level GML from IGN's
    national boundaries zip. Returns the extracted GML file's path.

    The zip also contains province/CCAA/country-level units and all the
    line-geometry `AdministrativeBoundary` files (module docstring) --
    only the one municipio polygon file is extracted, the rest is ignored,
    since nothing downstream needs those.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    zip_url = _zip_url(timeout)
    resp = requests.get(zip_url, timeout=timeout, headers=_ZIP_REQUEST_HEADERS)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = [n for n in zf.namelist() if _MUNICIPIO_UNIT_RE.match(n)]
        if not names:
            raise RuntimeError(f"no municipio-level AdministrativeUnit GML found in {zip_url}")
        for name in names:
            zf.extract(name, dest_dir)

    # A single file today (8,220 features, well under the 10,000 cap) --
    # if IGN ever splits it, `load_municipalities` below would need to glob
    # and concat instead of taking one path; fail loudly rather than
    # silently dropping municipalities if that ever happens.
    if len(names) != 1:
        raise RuntimeError(
            f"expected one municipio-level GML file, found {len(names)}: {names} -- "
            "IGN's export has apparently been split; update load_municipalities to concat them"
        )
    return dest_dir / names[0]


def load_municipalities(gml_path: str | Path) -> gpd.GeoDataFrame:
    """Parse the municipio-level GML into a clean GeoDataFrame.

    Columns: `ine_code` (5-digit, string), `name`, `geometry` (EPSG:4326).
    """
    raw = gpd.read_file(gml_path)
    ine_code = raw["nationalCode"].astype(str).str[-_INE_CODE_LEN:]
    return gpd.GeoDataFrame(
        {"ine_code": ine_code, "name": raw["text"]},
        geometry=raw.geometry,
        crs=raw.crs,
    ).reset_index(drop=True)


def attach_building_counts(
    municipalities: gpd.GeoDataFrame, parts_dir: str | Path
) -> gpd.GeoDataFrame:
    """Add an `n_buildings` column, counted from the exposure pipeline's own
    per-municipality `<ine_code>.buildings.parquet` partitions (region.py) --
    no new per-building processing, just counting rows already on disk.

    A municipality with no matching part file (nothing crawled there yet,
    or one of the Basque/Navarra territories whose parts use placeholder
    codes rather than real INE codes -- see region.py's own note on this)
    gets `n_buildings = 0` rather than being dropped, so the boundary still
    renders, just with no aggregate stats to show yet.
    """
    parts_dir = Path(parts_dir)
    counts: dict[str, int] = {}
    for part_path in parts_dir.glob("*.buildings.parquet"):
        ine_code = part_path.name.split(".", 1)[0]
        counts[ine_code] = len(pd.read_parquet(part_path, columns=["building_id"]))

    result = municipalities.copy()
    result["n_buildings"] = result["ine_code"].map(counts).fillna(0).astype(int)
    return result
