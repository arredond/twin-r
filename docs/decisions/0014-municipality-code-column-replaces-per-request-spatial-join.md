# ADR-0014: Precomputed `municipality_code` column replaces per-request spatial join

Status: accepted

## Context

After ADR-0013 shipped the municipal-boundary choropleth,
`/scenarios/fault` regressed from ~1-2s to ~10-20s. Root cause:
`response.compute_municipality_stats` ran a DuckDB `ST_Contains` spatial
join between every evaluated building and all ~8,200 municipality
polygons in `municipalities.parquet`, fresh, on every request, plus a
fresh read of that (tens-of-MB) file each call.

Measured directly: ~7s for the join alone at 3M evaluated buildings --
the scale long/nationwide faults routinely hit (ADR-0009). Point-in-
polygon membership doesn't change between requests, so this cost was
pure waste, paid on every call for a relationship that's actually fixed
at crawl time.

## Decision

Buildings are already crawled and stored one municipality at a time
(`<ine_code>.buildings.parquet` partitioning, ADR-0005/region.py) --
which municipality a building belongs to is free at ingest time, no
geometry needed. `pipeline.build_exposure` now stamps a
`municipality_code` column (the same INE/Foral code already used to name
the part) onto every building. `engine.py`'s `_load_sites` reads it
straight through like `centroid_lon`/`centroid_lat` (ADR-0006), and
`compute_municipality_stats` is now a plain pandas `groupby` on that
column -- no spatial join, no `municipalities.parquet` dependency, no
DuckDB spatial-extension load, for this endpoint.

Already-crawled data needs a one-time backfill:
`exposure.backfill --municipality-code <glob>` derives the code from
each part's `<ine_code>.buildings.parquet` filename (no spatial
computation, same "backfill in place" pattern as ADR-0006's spatial-index
columns). Run and verified against all three datasets on this machine at
the time (the Lorca-only and Murcia+Andalucía-only ones have since been
retired, and the third -- then `data/exposure_spain`, since renamed to
the sole `data/exposure` -- is the one that remains): the Lorca dataset,
27,884 buildings; the Murcia+Andalucía region dataset, 819 parts,
2,774,819 buildings, 16s; the national dataset, 7,764 parts, 12,881,817
buildings, ~168s. A merged, non-partitioned `buildings.parquet` (no
`<ine_code>.` prefix) isn't backfillable this way and needs regenerating
via the pipeline instead.

## Alternatives considered

- **Index `municipalities.parquet` for the spatial join** (e.g. an
  R-tree): still pays a join proportional to evaluated-building count on
  every request; doesn't fix the fundamental "same computation repeated
  per request" problem, only makes each repetition cheaper.
- **Cache `compute_municipality_stats` results**: rejected for the same
  reason ADR-0009 deferred fault-result caching -- real complexity
  (cache key, invalidation, memory) for a problem that has a free, exact
  fix available at the source.

## Consequences

- `buildings.parquet`'s schema gains a `municipality_code` column
  everywhere -- any future consumer should expect it present, same as
  ADR-0006's spatial-index columns.
- `compute_municipality_stats`'s signature dropped `municipalities_path`
  -- `local.py`/`handler.py` no longer need `TWIN_R_MUNICIPALITIES_PATH`
  for this endpoint. `municipalities.parquet` is still used elsewhere
  (the choropleth's boundary geometry itself, served as
  `municipalities.pmtiles`), just not on the scenario request path.
- A future crawl (`region.py`'s `build_exposure` call sites) must keep
  passing the right `ine_code`/part code through -- if a new territory
  source is added without it, buildings from that source silently lack
  `municipality_code` and won't show up in any municipality's stats
  (not a crash, since the groupby just skips `NaN`/missing keys, but a
  silent gap worth testing for).
