"""Load QAFI faults (pipelines/faults output) for the "automatic" rupture mode.

See docs/merisur.md §4.1: MERISUR's automatic mode lets a user pick a fault
and generates that fault's maximum-magnitude earthquake. We match that, with
one refinement over a plain fault-trace centroid: the rupture point is the
point on the fault trace *closest to a given reference location* (typically
wherever the user clicked/is looking at, passed in as near_lat/near_lon),
not the trace's overall centroid -- for a fault whose mapped trace runs
hundreds of km (QAFI is nationwide), the centroid can sit tens to hundreds
of km from the area the user actually cares about. Still a point-source
simplification (see rupture.py) -- just a better-placed point.
"""

from __future__ import annotations

import duckdb
import pandas as pd


def load_nearby_faults(
    faults_path: str, near_lat: float, near_lon: float, radius_km: float = 150.0
) -> pd.DataFrame:
    """Faults within `radius_km` of (near_lat, near_lon), nearest first.

    Columns: fault_id, name, mmax, mmax_source, length_km, rake (QAFI's
    published focal mechanism, per ADR-0004 -- used instead of assuming
    strike-slip for every fault), dip, min_depth_km, max_depth_km (with
    the trace geometry below, enough to build a finite rupture surface --
    ADR-0007), lat, lon (closest point on the trace to the reference
    location -- the point-source fallback location, and what the "N km
    away" sort/display uses), distance_km, geometry_geojson (the full fault
    trace, for map display and surface construction).
    """
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial;")
    df = con.execute(
        """
        WITH site AS (SELECT ST_Point(?, ?) AS pt),
        nearest AS (
            SELECT
                fault_id,
                name,
                mmax,
                mmax_source,
                length_km,
                rake,
                dip,
                min_depth_km,
                max_depth_km,
                ST_X(ST_ClosestPoint(geometry, site.pt)) AS lon,
                ST_Y(ST_ClosestPoint(geometry, site.pt)) AS lat,
                ST_Distance_Sphere(ST_ClosestPoint(geometry, site.pt), site.pt) / 1000 AS distance_km,
                ST_AsGeoJSON(geometry) AS geometry_geojson
            FROM read_parquet(?), site
        )
        SELECT * FROM nearest WHERE distance_km <= ? ORDER BY distance_km ASC
        """,
        [near_lon, near_lat, faults_path, radius_km],
    ).df()
    return df


def get_fault(faults_path: str, fault_id: str, near_lat: float, near_lon: float) -> pd.Series:
    """One fault's nearest-to-site point, by id. Raises KeyError if not found."""
    # radius_km=20000 -- effectively "no cap", we already know the id exists
    # somewhere; the ST_ClosestPoint computation only needs a reference
    # point to anchor to (near_lat/near_lon), not a search radius.
    faults = load_nearby_faults(faults_path, near_lat, near_lon, radius_km=20000)
    matches = faults[faults["fault_id"] == fault_id]
    if matches.empty:
        raise KeyError(f"fault_id {fault_id!r} not found in {faults_path}")
    return matches.iloc[0]
