"""db.py's shared DuckDB database under concurrent use -- local.py's
FastAPI app runs sync routes in a thread pool, so several requests query
at once. A single shared connection was observed returning None and even
another thread's result set (see db.py's docstring)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from scenario.db import get_connection


def _query_own_value(value: int) -> list[int]:
    con = get_connection()
    # Big enough that concurrent executions genuinely overlap.
    df = con.execute(
        "SELECT ?::BIGINT AS v, count(*) AS n FROM range(200000) GROUP BY v", [value]
    ).df()
    return df["v"].tolist()


def test_concurrent_queries_each_get_their_own_result():
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_query_own_value, range(400)))
    assert results == [[v] for v in range(400)]


def test_each_thread_gets_its_own_cursor_but_the_same_thread_reuses_it():
    assert get_connection() is get_connection()
    with ThreadPoolExecutor(max_workers=1) as pool:
        other = pool.submit(get_connection).result()
    assert other is not get_connection()
