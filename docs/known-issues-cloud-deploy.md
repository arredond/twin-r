# Known issues: deployed backend performance

The AWS-deployed scenario Lambda (ADR-0016) is functionally correct --
verified end-to-end against both fault mode (`fault_id=ES412`) and manual
mode -- but noticeably slower than the local dev server for large-radius
scenarios. This is a working-notes doc, not an ADR: nothing here has been
root-caused yet, just observed and hypothesized. Use `bin/twinr start prod`
to reproduce against the real deployed backend from the local frontend.

## Observed

Two real requests against the deployed Lambda (`eu-south-2`, 2048MB,
warm -- no `Init Duration` in either CloudWatch REPORT line, so this isn't
cold-start overhead):

| Request | Evaluated buildings | Radius | Duration | Max memory used |
|---|---|---|---|---|
| `fault_id=ES412` (mag 6.7, finite rupture) | 1,715,262 | 155.9km | 18.0s | **2035MB / 2048MB** |
| Manual mode (mag 6.0, point source) | 186,701 | 70.8km | 2.5s | not captured |

Locally, both are sub-second (see `test_manual_scenario_completes_quickly`/
`test_fault_scenario_completes_quickly` in
`services/scenario/tests/test_local_api.py`, which assert a 10s ceiling
against a synthetic 3-building fixture -- not a real comparison point, but
the order of magnitude gap to 18s on a 1.7M-building request over S3 is the
concern here).

Duration clearly scales with evaluated building count/radius, not a fixed
per-request tax (2.5s → 18s tracks 186k → 1.7M evaluated buildings
reasonably well). The 2035MB/2048MB memory figure is the sharper concern:
a bigger fault (QAFI's longest traces run well past ES412's -- see
ADR-0009) could plausibly OOM-kill the Lambda outright rather than just
run slow.

**Update (same day, after the `/faults` cold-start fix below):** repeat
`fault_id=ES412` requests against the (still 2048MB/60s) Lambda showed far
more variance than the single 18s sample above suggested -- 145ms (warm,
presumably a duplicate/cached request), 12.3s, 15.3s, 18.4s, 32.8s, and
**two outright 60000ms timeouts with no exception logged**, which surfaced
to the frontend as a 502 (a Function URL's response wait is bounded by the
Lambda's own configured timeout). Memory peaked at 2047MB/2048MB twice.
Mitigated by bumping to 3008MB/120s timeout (ADR-0016's stack) -- this is
headroom, not a fix for *why* the same request varies 12s-60s+ run to run;
that's still open, see below.

## Suspected contributors (unverified)

1. **`exposure.parquet` join has no filter on the exposure side.**
   `engine.py`'s `_load_sites` does:
   ```sql
   FROM read_parquet(?) AS b
   JOIN read_parquet(?) AS e USING (building_id)
   WHERE b.centroid_lon BETWEEN ? AND ? AND b.centroid_lat BETWEEN ? AND ?
   ```
   The bbox filter prunes `buildings-cloud.parquet` (b) via row-group
   stats (ADR-0006/ADR-0016), but there's no comparable filter on `e`
   (`exposure.parquet`) -- DuckDB may need to read most or all of that
   71MB file over httpfs to build the join, on every request, regardless
   of how small the evaluated radius is. Locally this is instant off
   disk; over S3 it's a real, unmeasured cost. **Not yet verified** --
   would need `EXPLAIN ANALYZE` against the deployed Lambda (or a local
   repro pointed at S3) to confirm this is actually where the time goes
   rather than the physics computation itself.

2. **Raw compute cost of 1.7M GMPE/fragility evaluations.** `run_scenario`
   evaluates ground motion and damage state for every row `_load_sites`
   returns, unconditionally (engine.py's own docstring: "always returns
   every evaluated building"). 1.7M rows of numpy/numba-vectorized GMPE
   math plus openquake.hazardlib overhead could itself be multiple
   seconds regardless of I/O -- untested in isolation.

3. **Lambda's network throughput ceiling.** 2048MB memory (bumped from
   1536MB in ADR-0016 specifically for more network bandwidth) may still
   be the binding constraint for a request that has to range-read a good
   chunk of two S3 objects (`buildings-cloud.parquet` at 320MB,
   `exposure.parquet` at 71MB) rather than one.

4. **Memory**: worth checking whether the pandas/numpy intermediate
   representations of 1.7M rows (sites dataframe, per-IM-type intensity
   arrays, damage probability arrays -- `_RESULT_COLUMNS` alone is 12
   float/string columns) are the dominant memory cost, or whether DuckDB's
   own buffer/threads settings (`_load_sites` doesn't set `PRAGMA
   memory_limit` or `SET threads`) are using more of the 2048MB/3008MB
   budget than necessary for the actual query.

5. **Run-to-run variance for the identical request** (12s-60s+ for the
   same `fault_id=ES412` params, not a range that tracks any input
   difference). Possibilities: S3 throughput variance per-request (no two
   httpfs range-read sessions necessarily land on the same S3 partition/
   shard), Lambda host-level noisy-neighbor contention, or GC pauses once
   memory pressure gets high enough (the 2047MB/2048MB near-OOM samples).
   Unverified which, if any, of these actually explains it.

## Avenues to investigate (not started)

- Add a `WHERE e.building_id IN (SELECT building_id FROM <bbox-filtered
  b>)` semi-join hint, or restructure the query so DuckDB filters `e` by
  the same bbox via a precomputed `centroid_lon`/`centroid_lat` on the
  exposure side too (would need those columns backfilled onto
  `exposure.parquet`, which doesn't have them today -- only
  `buildings.parquet`/`buildings-cloud.parquet` does).
- `EXPLAIN ANALYZE` the actual query against the deployed Lambda (or a
  local DuckDB connection pointed at the real S3 paths) to see where time
  is actually going -- httpfs range-read count/bytes vs. query execution
  vs. Python-side GMPE evaluation.
- Try raising Lambda memory further (network throughput scales with it)
  and see if duration drops roughly proportionally -- would help
  distinguish "network-bound" from "compute-bound".
- Consider precomputing/caching per-region result sets for common large
  faults (ADR-0009 explicitly deferred a fault-result cache -- this may be
  the actual trigger to revisit that deferral, at least for QAFI's
  longest/most damage-relevant traces).
- Check whether `con.execute` benefits from `SET threads=N` tuned to
  Lambda's allotted vCPUs at 2048MB (DuckDB defaults to detected core
  count, which may not match what Lambda actually grants at this memory
  tier).
- Get a memory profile (not just the CloudWatch max-used figure) to see
  whether it's the DuckDB query, the pandas dataframes, or openquake's own
  working set that's closest to the 2048MB ceiling, before just bumping
  memory further as a blunt fix.
