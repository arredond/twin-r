"""Discover and download INSPIRE Buildings from the Diputación Foral de
Alava's own INSPIRE download service -- Alava (province 01) runs a
separate Foral cadastral system, entirely outside the national Catastro
feed `catastro.py` crawls (see docs/basque-navarra-cadastral-sources.md).

Unlike Catastro, Alava publishes one bulk zip for the *whole* province
(not one per municipality), and that zip already contains one GML file
per municipality inside it (verified: 51 files, one per Alava
municipality) -- so there's no separate "list municipalities, then
download each one" step the way catastro.py has; one ~16MB download +
extract gets the whole province.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

BUILDINGS_ATOM_URL = "https://geo.araba.eus/atom/BU/Buildings.atom"

# This feed has one <entry> per CRS/format combination (we've seen
# EPSG:3042/UTM and EPSG:4258/~WGS84) -- match the EPSG:4258 one
# specifically, by its <category> tag, then take the first .zip href
# after it. Crawling the feed for this (rather than pinning
# BU_4258_GML.zip directly) survives the service moving the file, same
# rationale as catastro.py's own ATOM crawl.
_EPSG_4258_ENTRY_RE = re.compile(
    r'<category term="[^"]*EPSG/0/4258"[^/]*/>.*?href="([^"]+\.zip)"',
    re.IGNORECASE | re.DOTALL,
)

# Filenames inside the zip look like "ES.AFA.BU.0101_4258.gml" -- the
# 4-digit code is Araba's own province+sequence numbering, *not* the
# national 5-digit INE code (see MunicipalityRef's docstring).
_MUNICIPALITY_FILENAME_RE = re.compile(r"ES\.AFA\.BU\.(\d{4})_4258\.gml$")


@dataclass(frozen=True)
class MunicipalityRef:
    """A municipality within Alava, identified by Araba's own 4-digit
    province+sequence code (e.g. "0101") baked into its GML filename --
    *not* the national 5-digit INE code (province + 3-digit municipality).
    Same caveat as catastro.py's own MunicipalityRef: treat this as "this
    source's own code," not a guaranteed INE lookup key.
    """

    ine_code: str
    gml_path: Path | None = None  # populated once extracted locally


def _zip_url(timeout: int) -> str:
    resp = requests.get(BUILDINGS_ATOM_URL, timeout=timeout)
    resp.raise_for_status()
    match = _EPSG_4258_ENTRY_RE.search(resp.text)
    if not match:
        raise RuntimeError(f"EPSG:4258 buildings zip entry not found in {BUILDINGS_ATOM_URL}")
    return match.group(1)


def download_and_extract(dest_dir: str | Path, timeout: int = 120) -> list[MunicipalityRef]:
    """Download (once) + extract Alava's whole-province buildings zip.

    Returns one `MunicipalityRef` per municipality GML file found inside,
    each with `gml_path` already populated -- there's no separate
    "download, then extract one municipality" step to mirror
    `catastro.download_buildings`, since the whole province is already
    one cheap download.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    zip_url = _zip_url(timeout)
    resp = requests.get(zip_url, timeout=timeout)
    resp.raise_for_status()

    municipalities = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(dest_dir)
        for name in zf.namelist():
            match = _MUNICIPALITY_FILENAME_RE.search(name)
            if match:
                municipalities.append(
                    MunicipalityRef(ine_code=match.group(1), gml_path=dest_dir / name)
                )
    if not municipalities:
        raise RuntimeError(f"no municipality GML files found in {zip_url}")
    return municipalities
