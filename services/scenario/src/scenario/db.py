"""Shared DuckDB connection, reused across calls within one process.

engine.py/faults.py/building_lookup.py each used to open their own
`duckdb.connect()` per call. Locally that's free; in Lambda it throws away
httpfs's connection pool and metadata on every single invocation, even a
warm one reusing the same execution environment -- the S3 TLS handshake
and (for a repeat request against the same file) parquet footer get paid
for again for no reason. A lazily-created module-level connection persists
for as long as the execution environment does, so warm invocations reuse
it; a cold start pays the same one-time httpfs install either way.
"""

from __future__ import annotations

import duckdb

_con: duckdb.DuckDBPyConnection | None = None
_httpfs_loaded = False
_spatial_loaded = False


def get_connection() -> duckdb.DuckDBPyConnection:
    global _con
    if _con is None:
        _con = duckdb.connect()
    return _con


def ensure_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """Install+load httpfs at most once per process."""
    global _httpfs_loaded
    if not _httpfs_loaded:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        _httpfs_loaded = True


def ensure_spatial(con: duckdb.DuckDBPyConnection) -> None:
    """Install+load the spatial extension at most once per process."""
    global _spatial_loaded
    if not _spatial_loaded:
        con.execute("INSTALL spatial; LOAD spatial;")
        _spatial_loaded = True
