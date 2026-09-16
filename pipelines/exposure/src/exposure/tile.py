"""Tile building geometry with tippecanoe -> PMTiles (see docs/decisions/0003).

Geometry is tiled once, offline, as part of this pipeline -- the scenario
function never re-tiles; it only ever produces a thin building_id -> damage
result that the frontend joins onto this static layer at render time.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import geopandas as gpd

from .parse import SPATIAL_INDEX_COLUMNS


def tile_buildings(
    buildings: gpd.GeoDataFrame, output_path: str | Path, layer_name: str = "buildings"
) -> Path:
    """Run tippecanoe over a buildings GeoDataFrame, producing a PMTiles file.

    `building_id` must be present -- it's the join key the frontend uses to
    attach scenario results to tiled features via `setFeatureState`.
    """
    if "building_id" not in buildings.columns:
        raise ValueError("buildings GeoDataFrame must have a building_id column")

    # Spatial index columns (centroid/bbox) are for the scenario engine's
    # parquet-level filtering, not useful as per-feature tile attributes --
    # see parse.py's SPATIAL_INDEX_COLUMNS.
    buildings = buildings.drop(columns=SPATIAL_INDEX_COLUMNS, errors="ignore")

    with tempfile.TemporaryDirectory() as tmp:
        geojson_path = Path(tmp) / "buildings.geojson"
        # tippecanoe wants nullable numeric columns as plain floats/ints, not
        # pandas' nullable Int64 -- geopandas' to_file already coerces via
        # GeoJSON's own type handling, but we go through a plain dict dump to
        # keep this explicit and avoid surprises with pandas NA serialization.
        buildings.to_file(geojson_path, driver="GeoJSON")
        return tile_geojson_files([geojson_path], output_path, layer_name=layer_name)


def tile_debris(debris: gpd.GeoDataFrame, output_path: str | Path) -> Path:
    """Tile debris envelopes (debris.py) into `debris.pmtiles` (ADR-0010).

    Same tippecanoe path as `tile_buildings`, kept as its own function
    (rather than a generic `layer_name` reuse of `tile_buildings`) because
    the required-column check differs -- `ring` is debris-specific -- and
    because the two layers' pipeline steps are independently optional
    (a caller may tile buildings without debris, or vice versa).
    """
    if "building_id" not in debris.columns or "ring" not in debris.columns:
        raise ValueError("debris GeoDataFrame must have building_id and ring columns")

    with tempfile.TemporaryDirectory() as tmp:
        geojson_path = Path(tmp) / "debris.geojson"
        debris.to_file(geojson_path, driver="GeoJSON")
        return tile_geojson_files([geojson_path], output_path, layer_name="debris")


def tile_geojson_files(
    geojson_paths: list[Path], output_path: str | Path, layer_name: str = "buildings"
) -> Path:
    """Run tippecanoe over one or more already-written GeoJSON files.

    Used by the region crawl (region.py): each municipality is parsed and
    written to its own small GeoJSON part file as it's processed, so tiling
    the whole region never requires holding every municipality's buildings
    in memory at once -- tippecanoe merges the input files itself.
    """
    if shutil.which("tippecanoe") is None:
        raise RuntimeError(
            "tippecanoe not found on PATH -- install it (e.g. `brew install tippecanoe`) "
            "before running the exposure pipeline's tiling step."
        )
    if not geojson_paths:
        raise ValueError("no GeoJSON files to tile")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "tippecanoe",
            "-o",
            str(output_path),
            "-zg",
            "--extend-zooms-if-still-dropping",
            "-l",
            layer_name,
            "--force",
            *[str(p) for p in geojson_paths],
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return output_path
