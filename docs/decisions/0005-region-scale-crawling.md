# ADR-0005: Region-scale Catastro crawling — partitioned output, per-municipality parts, resumable

Status: accepted

## Context

Milestone 1 scoped exposure to a single municipality (Lorca). Expanding to
Murcia + Andalucía (9 provinces, ~830 municipalities, ~2.3M+ buildings in
Andalucía alone per Catastro's own published stats) changes the shape of
the problem:

- No single "download a whole province" endpoint exists — confirmed by
  crawling the ATOM feed hierarchy; the province feed is itself just an
  index of per-municipality zip links (see `catastro.py`).
- A single municipality's raw GML already expands ~8-24x on disk once
  unzipped (Lorca: 14MB zip → ~340MB GML). Keeping every province's raw
  GML around simultaneously would need on the order of 150-200GB of
  scratch disk for this region — not something to hold onto after parsing.
- ~830 sequential municipality fetches (2 round-trips each) would take
  hours; the crawl needs to survive being interrupted and resumed without
  redoing completed work, and needs to not hammer a public government
  server harder than necessary.

## Decision

- **One buildings/exposure/geojson part file per municipality**, under a
  `parts_dir`. A municipality is skipped if all three of its part files
  already exist — the crawl is resumable by construction, no separate
  checkpoint file needed.
- **Raw GML is deleted immediately after parsing each municipality.** Only
  the parsed parquet/geojson parts persist.
- **`buildings.parquet` stays partitioned** — one file per municipality,
  never concatenated into a single file. DuckDB's `read_parquet()` accepts
  a glob pattern directly (`<parts_dir>/*.buildings.parquet`), so nothing
  downstream needs a combined file, and we never hold a multi-million-row
  GeoDataFrame in memory in Python at once.
- **`exposure.parquet` is concatenated** into one file — it's attributes
  only (no geometry), small enough that combining it is cheap and
  convenient for the scenario function's join.
- **Tiling merges every municipality's GeoJSON part in one `tippecanoe`
  invocation** (it natively accepts multiple input files) — same
  memory-avoidance reasoning as buildings.parquet.
- **A modest thread pool** (default 8 workers) for the crawl — network + 
  GDAL-parsing bound, so threads (not processes) are the right tool, and 8
  is a deliberately conservative default out of courtesy to a public
  government server rather than a measured ceiling.

## Alternatives considered

- **Concatenate everything into single buildings.parquet / one big
  GeoDataFrame**: simpler mental model, but forces the whole region's
  geometry through Python memory at once and loses natural per-municipality
  resumability.
- **`multiprocessing` instead of threads**: would help if parsing were
  CPU-bound in pure Python, but the actual work (HTTP I/O, GDAL/pyogrio C
  calls) already releases the GIL; processes would add serialization
  overhead for no benefit here.

## Consequences

- Downstream code (the scenario function, tiling) must handle
  `buildings_path` as a glob pattern, not assume a single file — already
  true of the scenario engine's `read_parquet(?)` calls, which accept glob
  patterns unchanged.
- A partial/interrupted crawl leaves a valid, usable (if incomplete) set of
  parts — `combine_exposure`/`combine_tiles` work over whatever parts exist
  at the time they're run, not just a "fully done" state.
- Failed municipalities are collected and reported, not fatal to the whole
  crawl — a systemic problem (e.g. Catastro rate-limiting) still needs a
  human to notice the failure list is large, since nothing here retries
  automatically.
