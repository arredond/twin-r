"""Fetch QAFI v4 faults from IGME's official shapefile download.

See ADR-0004 (docs/decisions/0004-qafi-shapefile-source.md) for why this
replaces the earlier ArcGIS MapServer REST approach: the MapServer layer
exposes only 8 basic fields (no length, no Mmax) and its geometry for at
least one fault (Alhama de Murcia) was found to be ~3x the official length.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import geopandas as gpd
import requests

from .mmax import mmax_from_length_km

QAFI_SHAPEFILE_URL = "https://info.igme.es/qafi/docs/QAFI_Traces.rar"

# QAFI v4 uses 0.0 (not null) as the "no published value" sentinel for
# MaxMagnitu -- see the field description PDF: Mmax "is only displayed if
# it has been published before somewhere." Observed directly: 81/201 faults
# have MaxMagnitu == 0.0.
_UNPUBLISHED_MMAX_SENTINEL = 0.0


def resolve_mmax(published_mmax: float | None, length_km: float) -> tuple[float, str]:
    """Pick a fault's Mmax: QAFI's published value if there is one, else a
    length-based estimate. Pure function, kept separate from the
    network/parsing code above so it's unit-testable without a download.
    """
    if published_mmax is not None and published_mmax != _UNPUBLISHED_MMAX_SENTINEL:
        return round(published_mmax, 2), "qafi_v4_published"
    return round(mmax_from_length_km(length_km), 2), "estimated_wells_coppersmith_1994"


def _download_and_extract_shapefile(dest_dir: Path, timeout: int = 30) -> Path:
    if shutil.which("unar") is None:
        raise RuntimeError(
            "unar not found on PATH -- install it (e.g. `brew install unar`) "
            "to extract the QAFI shapefile archive."
        )
    resp = requests.get(QAFI_SHAPEFILE_URL, timeout=timeout)
    resp.raise_for_status()

    with tempfile.TemporaryDirectory() as tmp:
        rar_path = Path(tmp) / "QAFI_Traces.rar"
        rar_path.write_bytes(resp.content)
        subprocess.run(
            ["unar", "-f", "-D", "-o", str(dest_dir), str(rar_path)],
            check=True,
            capture_output=True,
            text=True,
        )

    shp_matches = list(dest_dir.glob("*.shp"))
    if not shp_matches:
        raise RuntimeError(f"No .shp file found after extracting QAFI archive into {dest_dir}")
    return shp_matches[0]


def fetch_qafi_faults(raw_dir: str | Path | None = None, timeout: int = 30) -> gpd.GeoDataFrame:
    """Download + parse QAFI v4 faults, official shapefile source.

    Columns: fault_id, name, section_name, length_km, mmax, mmax_source
    ("qafi_v4_published" or "estimated_wells_coppersmith_1994"), rake, dip,
    strike, min_depth_km, max_depth_km, geometry (EPSG:4326).

    `min_depth_km`/`max_depth_km` (QAFI's MinDepth/MaxDepth, i.e. Ztor and
    the base of the seismogenic zone) plus `dip` and the trace geometry
    are what services/scenario needs to build a finite rupture plane
    (see ADR-0007, docs/decisions/0007-finite-rupture-surface.md) instead
    of the point-source approximation -- QAFI publishes all of these for
    100% of its 201 faults, unlike Mmax (~60%).

    `raw_dir` is where the extracted shapefile is kept; a temp dir is used
    and discarded if omitted.
    """
    if raw_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return fetch_qafi_faults(raw_dir=tmp, timeout=timeout)

    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    shp_path = _download_and_extract_shapefile(raw_dir, timeout=timeout)

    gdf = gpd.read_file(shp_path)
    gdf = gdf.to_crs("EPSG:4326")

    resolved = [resolve_mmax(row.MaxMagnitu, row.Length) for row in gdf.itertuples()]

    out = gpd.GeoDataFrame(
        {
            "fault_id": gdf["ID"],
            "name": gdf["FaultName"].str.strip(),
            "section_name": gdf["SectionNam"].replace("", None),
            "length_km": gdf["Length"],
            "mmax": [m for m, _ in resolved],
            "mmax_source": [s for _, s in resolved],
            "rake": gdf["Rake"],
            # Dip/AverageStr come out of the shapefile's DBF as text fields
            # (unlike Rake/MinDepth/MaxDepth, which are already numeric) --
            # cast explicitly rather than silently carrying strings into
            # parquet, where they'd break any numeric use downstream
            # (found: SimpleFaultSurface construction needs dip as a float).
            "dip": gdf["Dip"].astype(float),
            "strike": gdf["AverageStr"].astype(float),
            "min_depth_km": gdf["MinDepth"],
            "max_depth_km": gdf["MaxDepth"],
            "geometry": gdf["geometry"],
        },
        geometry="geometry",
        crs="EPSG:4326",
    )
    return out


def write_faults_parquet(
    output_path: str, raw_dir: str | Path | None = None, timeout: int = 30
) -> gpd.GeoDataFrame:
    gdf = fetch_qafi_faults(raw_dir=raw_dir, timeout=timeout)
    gdf.to_parquet(output_path)
    return gdf
