"""Shared DuckDB database, reused across calls within one process.

engine.py/faults.py/building_lookup.py each used to open their own
`duckdb.connect()` per call. Locally that's free; in Lambda it throws away
httpfs's connection pool and metadata on every single invocation, even a
warm one reusing the same execution environment -- the S3 TLS handshake
and (for a repeat request against the same file) parquet footer get paid
for again for no reason. A lazily-created module-level database persists
for as long as the execution environment does, so warm invocations reuse
it; a cold start pays the same one-time httpfs install either way.

**One cursor per thread, not one connection for everyone.** A single
DuckDB connection isn't safe to use from several threads at once, and
local.py's FastAPI app runs its sync routes in a thread pool: concurrent
requests sharing one connection were observed returning `None` from
`.df()` and, worse, *another request's result set* (a faults query
failing with `KeyError: 'lat'` on columns that weren't its own) -- a wrong
result that doesn't happen to crash would then be stored by the scenario
cache (ADR-0018). `cursor()` gives each thread its own connection to the
same database (DuckDB's documented pattern for multithreading): extensions
loaded once are visible to every cursor, so the once-per-process
httpfs/spatial flags below still hold. Lambda handles one request per
execution environment at a time, so it was never affected; this matters
for local dev, and for anything else that calls in from several threads.
"""

from __future__ import annotations

import threading

import duckdb

_con: duckdb.DuckDBPyConnection | None = None
_con_lock = threading.Lock()
_thread_local = threading.local()
_httpfs_loaded = False
_spatial_loaded = False


def get_connection() -> duckdb.DuckDBPyConnection:
    """This thread's cursor onto the shared database (see module docstring)."""
    global _con
    cursor = getattr(_thread_local, "cursor", None)
    if cursor is None:
        with _con_lock:
            if _con is None:
                _con = duckdb.connect()
            cursor = _con.cursor()
        _thread_local.cursor = cursor
    return cursor


def ensure_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """Install+load httpfs at most once per process."""
    global _httpfs_loaded
    with _con_lock:
        if not _httpfs_loaded:
            con.execute("INSTALL httpfs; LOAD httpfs;")
            _httpfs_loaded = True


def ensure_spatial(con: duckdb.DuckDBPyConnection) -> None:
    """Install+load the spatial extension at most once per process."""
    global _spatial_loaded
    with _con_lock:
        if not _spatial_loaded:
            con.execute("INSTALL spatial; LOAD spatial;")
            _spatial_loaded = True
