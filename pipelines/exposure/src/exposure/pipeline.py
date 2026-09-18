"""Orchestrate: download -> parse -> taxonomy -> buildings.parquet + exposure.parquet.

NOTE (ADR-0015, docs/decisions/0015-eshm20-site-amplification.md): `vs30`
is a precomputed-column, not a live-crawl one yet -- `vs30.add_vs30_column`
exists and `backfill.py --vs30` retrofits already-crawled parts, but this
function doesn't call it directly (it needs a network fetch of the ESRM20
grid + a KD-tree build that's wasteful to repeat per municipality during a
crawl -- see `vs30.py`'s `grid`/`tree` passthrough params, meant to be
fetched once by a caller and threaded through many buildings). Once a
national backfill has run, a future crawl orchestrator (this function or
`region.py`) should thread a pre-fetched `grid`/`tree` through here the
same way, so newly-crawled municipalities get `vs30` for free instead of
needing a follow-up backfill pass every time.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .catastro import MunicipalityRef, download_buildings
from .debris import compute_debris_envelopes
from .parse import load_buildings
from .taxonomy import TAXONOMY_SOURCE, assign_taxonomy
from .tile import tile_buildings, tile_debris


def build_exposure(
    buildings: gpd.GeoDataFrame, municipality_name: str, municipality_code: str
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Taxonomy-assign an already-loaded municipality's buildings.

    Split out of `process_municipality` (Catastro-specific: download + its
    own GML shape) so the Foral/regional sources (alava.py/navarra.py/
    gipuzkoa.py -- see inspire_bu.py, a different GML shape entirely) can
    share this half without going through Catastro's download/parse at
    all. Returns (buildings, exposure), same contract as
    `process_municipality`.

    `municipality_code` (the same INE/Foral code region.py already uses to
    name this municipality's `<ine_code>.buildings.parquet` part) is stamped
    onto every building as a plain column here -- every caller already knows
    it for free (it's how the part gets named), so this costs nothing to
    populate and lets the scenario engine group buildings by municipality
    with a column read instead of a per-request spatial join against
    municipalities.parquet (see scenario/response.py's
    `compute_municipality_stats`).
    """
    buildings = buildings.copy()
    buildings["municipality"] = municipality_name
    buildings["municipality_code"] = municipality_code

    # Zipping the two columns directly (rather than `buildings.apply(...,
    # axis=1)`) avoids per-row-apply's untyped-tuple-return ambiguity for
    # static type checkers, and is the more idiomatic pandas pattern for a
    # two-column-in, two-column-out transform anyway.
    taxonomy = [
        assign_taxonomy(year, floors)
        for year, floors in zip(buildings["construction_year"], buildings["floors"])
    ]
    exposure = pd.DataFrame(
        {
            "building_id": buildings["building_id"],
            "taxonomy_class": [t[0] for t in taxonomy],
            "height_class": [t[1] for t in taxonomy],
            "taxonomy_source": TAXONOMY_SOURCE,
        }
    )
    return buildings, exposure


def process_municipality(
    municipality: MunicipalityRef,
    raw_dir: str | Path,
    skip_download: bool = False,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Download (unless skipped) + parse + taxonomy-assign one municipality.

    Returns (buildings, exposure) -- shared by the single-municipality CLI
    (`run`, below) and the multi-municipality region crawl (region.py).
    """
    raw_dir = Path(raw_dir)
    if not skip_download:
        download_buildings(municipality, raw_dir)

    buildings = load_buildings(raw_dir)
    return build_exposure(buildings, municipality.name, municipality.ine_code)


def run(
    municipality: MunicipalityRef,
    raw_dir: str | Path,
    buildings_output: str | Path,
    exposure_output: str | Path,
    skip_download: bool = False,
    tiles_output: str | Path | None = None,
    debris_output: str | Path | None = None,
    debris_tiles_output: str | Path | None = None,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Run the full exposure pipeline for one municipality.

    Writes two parquet files with a shared `building_id`:
    - `buildings_output`: geometry + raw Catastro attributes (this is what
      gets tiled with tippecanoe -- see docs/decisions/0003).
    - `exposure_output`: building_id + taxonomy_class + height_class (no
      geometry) -- what the scenario function joins fragility functions
      against.

    Set `skip_download=True` to reuse an already-populated `raw_dir` (e.g.
    during local development, to avoid re-downloading a multi-hundred-MB
    GML zip on every run). Pass `tiles_output` to also run the tippecanoe
    tiling step (ADR-0003); omitted by default since it requires tippecanoe
    on PATH and isn't needed for iterating on parsing/taxonomy logic alone.

    Pass `debris_output` to also compute and write debris envelopes
    (ADR-0010, `debris.py`) as a parquet file (`building_id`, `ring`,
    geometry); pass `debris_tiles_output` to additionally tile them into
    `debris.pmtiles`, same on/off-by-default reasoning as `tiles_output`.
    `debris_tiles_output` without `debris_output` still computes debris in
    memory (just doesn't persist the parquet) -- both are independently
    optional.

    For more than one municipality, see `region.py` instead -- it
    parallelizes and checkpoints per municipality rather than holding
    everything in memory the way this single-municipality path does.
    """
    raw_dir = Path(raw_dir)
    try:
        buildings, exposure = process_municipality(municipality, raw_dir, skip_download)
    finally:
        if not skip_download:
            # Raw GML is large (100s of MB) and fully consumed once parsed
            # -- delete what *we* downloaded this run, success or failure.
            # Left alone when skip_download=True: that raw_dir was supplied
            # by the caller (e.g. iterating on parsing logic without
            # re-downloading), not ours to remove. Mirrors region.py's
            # per-municipality cleanup (ADR-0005) -- this single-municipality
            # path had fallen out of sync with it (found: Lorca's raw GML
            # sat around at 326MB indefinitely).
            shutil.rmtree(raw_dir, ignore_errors=True)

    Path(buildings_output).parent.mkdir(parents=True, exist_ok=True)
    Path(exposure_output).parent.mkdir(parents=True, exist_ok=True)
    buildings.to_parquet(buildings_output)
    exposure.to_parquet(exposure_output, index=False)

    if tiles_output is not None:
        tile_buildings(buildings, tiles_output)

    if debris_output is not None or debris_tiles_output is not None:
        debris = compute_debris_envelopes(buildings)
        if debris_output is not None:
            Path(debris_output).parent.mkdir(parents=True, exist_ok=True)
            debris.to_parquet(debris_output, index=False)
        if debris_tiles_output is not None:
            tile_debris(debris, debris_tiles_output)

    return buildings, exposure
