"""Discover and download INSPIRE Buildings datasets from Catastro.

We crawl the ATOM feed hierarchy (root -> province -> municipality) rather
than hardcoding per-municipality zip URLs, because the observed URL
includes a province subfolder that isn't a pure function of the municipality
code -- crawling is slightly slower but survives the service reorganizing
its folders, which pinned URLs would not.

The province feed is fetched **once per province** and parsed into every
municipality's zip URL in one pass (`list_municipalities`) -- fetching it
once per municipality, as an earlier version of this module did, means
re-downloading the same multi-hundred-KB feed dozens to hundreds of times
when crawling a whole province (see ADR-0005, region-scale crawling).
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

ROOT_FEED_URL = "https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml"

_PROVINCE_LINK_RE = re.compile(r'href="([^"]+ES\.SDGC\.bu\.atom_(\d{2})\.xml)"', re.IGNORECASE)
# Matches one <entry>...<title> CODE-NAME buildings</title>...href="...zip"...
_MUNICIPALITY_ENTRY_RE = re.compile(
    r"<title>\s*(\d+)-([^<]+?)\s+buildings\s*</title>.*?href=\"([^\"]+\.zip)\"",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class MunicipalityRef:
    """A Spanish municipality, identified by the code Catastro's own ATOM
    feed uses for it -- this is *usually* the INE municipality code, but not
    always (Madrid capital is filed under 28900 in this feed, not its real
    INE code 28079), so treat it as "Catastro's code," not a guaranteed INE
    lookup key.
    """

    name: str
    ine_code: str
    zip_url: str | None = None  # populated when discovered via list_municipalities

    @property
    def province_code(self) -> str:
        return self.ine_code[:2]


def _province_feed_url(province_code: str, timeout: int) -> str:
    resp = requests.get(ROOT_FEED_URL, timeout=timeout)
    resp.raise_for_status()
    for href, code in _PROVINCE_LINK_RE.findall(resp.text):
        if code == province_code:
            return href
    raise RuntimeError(f"Province code {province_code!r} not found in root buildings feed")


def list_municipalities(province_code: str, timeout: int = 30) -> list[MunicipalityRef]:
    """All municipalities in a province, with their zip download URLs
    resolved in a single pass over that province's ATOM feed."""
    province_url = _province_feed_url(province_code, timeout)
    resp = requests.get(province_url, timeout=timeout)
    resp.raise_for_status()

    municipalities = [
        MunicipalityRef(name=name.strip(), ine_code=code, zip_url=zip_url)
        for code, name, zip_url in _MUNICIPALITY_ENTRY_RE.findall(resp.text)
    ]
    if not municipalities:
        raise RuntimeError(f"No municipalities found in province feed {province_url}")
    return municipalities


def _municipality_zip_url(municipality: MunicipalityRef, timeout: int) -> str:
    if municipality.zip_url:
        return municipality.zip_url
    # Fallback for a MunicipalityRef built by hand (not via
    # list_municipalities) -- re-fetches the province feed just for this
    # one lookup, fine for the single-municipality CLI path.
    for candidate in list_municipalities(municipality.province_code, timeout):
        if candidate.ine_code == municipality.ine_code:
            return candidate.zip_url  # type: ignore[return-value]
    raise RuntimeError(f"Municipality {municipality.ine_code}-{municipality.name} not found")


def download_buildings(
    municipality: MunicipalityRef, dest_dir: str | Path, timeout: int = 60
) -> Path:
    """Download + unzip a municipality's INSPIRE Buildings GML dataset.

    Returns the directory containing the extracted .gml files:
    `A.ES.SDGC.BU.<code>.building.gml` (footprints, currentUse, construction
    date) and `...buildingpart.gml` (per-part floor counts).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    zip_url = _municipality_zip_url(municipality, timeout)
    resp = requests.get(zip_url, timeout=timeout)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(dest_dir)

    return dest_dir
