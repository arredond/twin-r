"""Discover and download INSPIRE Buildings from Gobierno de Navarra /
IDENA's live WFS -- Navarra (province 31) runs a separate Foral/regional
cadastral system, entirely outside the national Catastro feed
`catastro.py` crawls (see docs/basque-navarra-cadastral-sources.md).

Navarra publishes no per-municipality breakdown (unlike Catastro or
Alava) -- just one WFS `Building` feature type covering the whole
province. Iterating the *whole* territory (rather than filtering by
bounding box, which would need per-municipality boundary geometry we
don't have) is the approach docs/basque-navarra-cadastral-sources.md
recommends, and is well within what a plain WFS 2.0 paging loop can do:
one province is a few hundred thousand buildings, not millions.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import requests

WFS_URL = "https://inspire.navarra.es/services/BU/wfs"
TYPE_NAME = "BU:Building"

# The server's own observed default page cap (verified this session) --
# passing a larger `count` doesn't get more per page, so there's no
# tuning knob here beyond "page until the server stops handing back a
# `next` link."
PAGE_SIZE = 5000

_NEXT_URL_RE = re.compile(r'next="([^"]+)"')
_NUMBER_RETURNED_RE = re.compile(r'numberReturned="(\d+)"')


def _first_page_url() -> str:
    return (
        f"{WFS_URL}?service=WFS&version=2.0.0&request=GetFeature"
        f"&typenames={TYPE_NAME}&count={PAGE_SIZE}&startindex=0"
    )


def download_pages(dest_dir: str | Path, timeout: int = 60) -> list[Path]:
    """Page through Navarra's whole-territory Buildings WFS, saving each
    page's raw GML response under `dest_dir`.

    Follows the WFS 2.0 `next` link the server itself returns on each
    page (rather than computing `startIndex` ourselves) -- survives the
    server's page-size cap changing, and stops naturally once a page
    comes back with zero features (no explicit "how many pages total"
    call needed; `numberMatched` is reported as "unknown" by this server
    when a real count is requested, see docs/basque-navarra-cadastral-
    sources.md's Navarra section).

    Returns the saved page paths, in fetch order -- feed each one through
    `inspire_bu.load_buildings` and concatenate.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    pages: list[Path] = []
    url: str | None = _first_page_url()
    page_num = 0
    while url:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.text

        returned_match = _NUMBER_RETURNED_RE.search(text)
        n_returned = int(returned_match.group(1)) if returned_match else 0
        if n_returned == 0:
            break

        page_path = dest_dir / f"page_{page_num:04d}.gml"
        page_path.write_bytes(resp.content)
        pages.append(page_path)

        next_match = _NEXT_URL_RE.search(text)
        # The server writes its own `next` URL HTML-entity-escaped
        # (`&amp;` between query params) inside the XML attribute --
        # unescape before treating it as an actual URL to fetch.
        url = html.unescape(next_match.group(1)) if next_match else None
        page_num += 1

    if not pages:
        raise RuntimeError(f"no building pages fetched from {WFS_URL}")
    return pages
