# ADR-0006: Precomputed centroid/bbox columns + magnitude-aware search radius

Status: accepted

## Context

`docs/validation-region-expansion.md` §4 measured a 634MB JSON response for
a single scenario at regional scale, and identified two separate problems
behind it: (a) the spatial pre-filter (ADR added mid-milestone-1) computed
every building's centroid via `ST_Centroid(geometry)` on every request,
touching the full geometry column of all 819 municipality files regardless
of rupture location; (b) the filter radius was a flat 300km constant, far
wider than most real scenarios (especially smaller magnitudes) actually
need.

## Decision

**Precompute spatial index columns.** `pipelines/exposure`'s
`parse.add_spatial_index_columns` now writes `centroid_lon`, `centroid_lat`,
and `bbox_xmin/ymin/xmax/ymax` as plain columns into `buildings.parquet` at
parse time (every future crawl gets these natively). A `pipelines/exposure`
`backfill` module retrofits already-parsed data without re-downloading from
Catastro. `engine.py`'s site query now reads `centroid_lon`/`centroid_lat`
directly instead of calling `ST_Centroid` — this lets DuckDB's parquet
reader use each file/row-group's own min/max statistics to skip data that
can't match the bounding-box predicate, and avoids reading the (much
larger) WKB geometry column for this query at all. Measured: ~14x faster
for a representative regional bounding-box query (81,813 matching rows: 0.40s
→ 0.03s) against the Murcia+Andalucía dataset.

**Magnitude-aware search radius.** `ground_motion.estimate_significant_distance_km`
binary-searches (using the GMPE itself, a handful of scalar evaluations —
cheap) for the distance at which this specific rupture's SA(0.3s) drops
below a threshold set well under the lowest intensity value in any vendored
fragility curve (~0.05g) — i.e. the distance past which no building's
damage probability could be materially non-zero, for *this* magnitude and
mechanism. Replaces the flat `DEFAULT_MAX_DISTANCE_KM = 300` constant.
Clamped to `[10, 300]` km: the floor avoids a degenerate near-zero radius
for tiny magnitudes, the ceiling is the ADR-0005-era value we've actually
load-tested.

## Alternatives considered

- **A separate municipality-bbox manifest file**, intersected against the
  query before constructing the file list, as an explicit pruning step:
  considered first, but precomputed columns + parquet's own statistics give
  the same pruning behavior for free, without a second artifact to keep in
  sync with the crawl.
- **Keep the flat 300km radius, only add non-None filtering** (the
  "cheapest possible fix"): still leaves compute cost tied to a fixed,
  usually-too-wide radius; the magnitude-derived radius costs one extra
  GMPE binary search (negligible) and scales the *right* variable down for
  small/moderate events, which are the common case.

## Consequences

- `buildings.parquet`'s schema gains 6 columns everywhere (existing
  Lorca and Murcia+Andalucía data backfilled in place, 16s for 2.77M rows).
  Any future consumer of buildings.parquet should expect these columns to
  be present.
- `run_scenario`'s `max_distance_km` parameter is now optional
  (`None` → derived from the rupture) rather than defaulting to a module
  constant — existing callers passing an explicit value are unaffected.
- Response payload size is a separate, still-open problem (non-None
  filtering, tracked as a presentation-layer change in `local.py`/
  `handler.py`, not this ADR) — this ADR only addresses *compute and I/O*
  cost, not the JSON-transfer cost, though a tighter radius does also
  shrink the row count feeding into that payload for small/moderate
  magnitudes.
