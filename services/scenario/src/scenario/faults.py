"""Load QAFI faults (pipelines/faults output) for the "automatic" rupture mode.

See docs/merisur.md §4.1: MERISUR's automatic mode lets a user pick a fault
and generates that fault's maximum-magnitude earthquake.

Where that earthquake sits is derived from the fault's own geometry, not
from the caller. A fault with a full rupture geometry in QAFI (trace + dip +
seismogenic depth range -- all 201 QAFI v4 faults today) gets a finite
rupture surface (ADR-0007), and that surface alone determines which
buildings are evaluated and how hard each one shakes. Its representative
point (`rupture.lat`/`lon`, echoed back for display) is the trace's own
midpoint (trace.py). Only a fault *missing* that geometry falls back to a
point source, and only then does a caller-supplied reference point
(`near_lat`/`near_lon`) matter: the point source sits at the trace point
closest to it. See `rupture_anchor`.

Earlier versions always anchored the rupture to the trace point closest to
the user's map view, for every fault, surface or not. That made an
otherwise-deterministic result look view-dependent and kept it from being
cached by fault_id alone (docs/decisions/0018-scenario-result-cache.md).
"""

from __future__ import annotations

import math

import pandas as pd

from .db import ensure_httpfs, ensure_spatial, get_connection
from .trace import trace_midpoint

# Decimal places a caller's near_lat/near_lon is rounded to before use
# (~1m) -- float noise from a map click shouldn't mint a distinct
# scenario_id (scenario_id.py) for what is physically the same request.
NEAR_POINT_DECIMALS = 5

# Shared by load_faults/get_fault so the two can't drift on what
# "has rupture geometry" means.
_FAULT_COLUMNS = """
    fault_id,
    name,
    mmax,
    mmax_source,
    length_km,
    rake,
    dip,
    min_depth_km,
    max_depth_km,
    (geometry IS NOT NULL AND dip IS NOT NULL
     AND min_depth_km IS NOT NULL AND max_depth_km IS NOT NULL) AS has_rupture_geometry,
    ST_AsGeoJSON(geometry) AS geometry_geojson
"""


def _connection(faults_path: str):
    con = get_connection()
    if faults_path.startswith("s3://"):
        ensure_httpfs(con)
    ensure_spatial(con)
    return con


def load_faults(faults_path: str) -> pd.DataFrame:
    """Every fault in the dataset, sorted by name.

    Columns: fault_id, name, mmax, mmax_source, length_km, rake (QAFI's
    published focal mechanism, per ADR-0004), dip, min_depth_km,
    max_depth_km, has_rupture_geometry (trace + dip + both depths all
    present -- enough to build a finite rupture surface, ADR-0007; tells a
    caller whether `near_lat`/`near_lon` would matter at all, see
    `rupture_anchor`), geometry_geojson (the full fault trace, for map
    display and surface construction).
    """
    return (
        _connection(faults_path)
        .execute(
            f"SELECT {_FAULT_COLUMNS} FROM read_parquet(?) ORDER BY name, fault_id",
            [faults_path],
        )
        .df()
    )


def faults_payload(faults_path: str) -> dict:
    """The `GET /faults` response body: `{"faults": [...]}`, one record per
    `load_faults` row. Shared by the route (handler.py/local.py) and by
    `export_faults`, which writes it as the static `faults.json` the
    frontend loads on startup, so the two can't disagree.

    Missing values (e.g. a fault without a dip or depths, i.e. without
    rupture geometry) become `None`/null: pandas hands them back as float
    NaN, which `json.dumps` would write as a bare `NaN` that browsers'
    JSON.parse rejects."""
    records = load_faults(faults_path).to_dict(orient="records")
    return {
        "faults": [
            {k: None if isinstance(v, float) and math.isnan(v) else v for k, v in r.items()}
            for r in records
        ]
    }


def get_fault(
    faults_path: str,
    fault_id: str,
    near_lat: float | None = None,
    near_lon: float | None = None,
) -> pd.Series:
    """One fault by id, with the same columns as `load_faults` plus
    `closest_lat`/`closest_lon`: the trace point nearest (near_lat,
    near_lon), or NaN when no reference point is given. Raises KeyError if
    the id isn't found."""
    has_near = near_lat is not None and near_lon is not None
    df = (
        _connection(faults_path)
        .execute(
            f"""
            WITH f AS (
                SELECT *,
                    CASE WHEN ? THEN ST_ClosestPoint(geometry, ST_Point(?, ?)) END AS closest
                FROM read_parquet(?)
                WHERE fault_id = ?
            )
            SELECT {_FAULT_COLUMNS}, ST_Y(closest) AS closest_lat, ST_X(closest) AS closest_lon
            FROM f
            """,
            [has_near, near_lon or 0.0, near_lat or 0.0, faults_path, fault_id],
        )
        .df()
    )
    if df.empty:
        raise KeyError(f"fault_id {fault_id!r} not found in {faults_path}")
    return df.iloc[0]


def round_near_point(
    near_lat: float | None, near_lon: float | None
) -> tuple[float | None, float | None]:
    """Normalizes an optional reference point: both-or-neither (a lone lat
    or lon is meaningless, treated as absent), rounded to
    NEAR_POINT_DECIMALS."""
    if near_lat is None or near_lon is None:
        return None, None
    return round(near_lat, NEAR_POINT_DECIMALS), round(near_lon, NEAR_POINT_DECIMALS)


def rupture_anchor(fault: pd.Series) -> tuple[float, float, bool]:
    """(lat, lon, near_point_used) for this fault's rupture.

    - Has full rupture geometry: the trace midpoint, always. The finite
      surface determines the result; this point is only for display, and
      any caller-supplied reference point is ignored (`near_point_used`
      False) so it can't split an identical result across scenario_ids.
    - Missing it, with a reference point (`closest_lat`/`closest_lon` from
      `get_fault`): the trace point closest to that reference -- a genuine
      input here, since a point source's location drives every distance.
    - Missing it, without a reference point: the trace midpoint, the most
      neutral geometry-derived choice.

    Note: a fault that *has* the geometry but whose surface hazardlib
    rejects (rupture.py's `from_fault` fallback -- none of QAFI v4's 201
    today) still anchors at the midpoint, not the reference point, so its
    result stays a pure function of fault_id like every other
    geometry-bearing fault's.
    """
    if pd.isna(fault["geometry_geojson"]):
        raise ValueError(f"fault {fault['fault_id']!r} has no trace geometry")

    closest_lat = fault.get("closest_lat")
    if not fault["has_rupture_geometry"] and closest_lat is not None and not pd.isna(closest_lat):
        return float(closest_lat), float(fault["closest_lon"]), True

    lat, lon = trace_midpoint(fault["geometry_geojson"])
    return lat, lon, False
