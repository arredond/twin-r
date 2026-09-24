# ADR-0017: Per-scenario tile-join, served by its own lightweight Lambda

Status: accepted

## Context

Before this, a scenario response shipped every damaged/uncertain
building's `building_id` + damage fields inline, and the frontend joined
that onto the static `buildings.pmtiles` layer client-side via MapLibre's
`setFeatureState` (ADR-0003). That doesn't scale: a large "very low
probability" nationwide scenario can return 100k+ buildings
(`docs/scenario-result-api.md`), and applying `setFeatureState` to all of
them one at a time visibly froze the tab for several seconds even chunked
across animation frames.

Two options were on the table for joining a scenario's results onto
building geometry without shipping the whole affected-building list to the
client:

- **(a) Build a per-scenario `buildings.pmtiles`** on the backend: one
  tile-join, but means either downloading the whole multi-GB archive into
  a Lambda to re-run tippecanoe, or running tippecanoe synchronously in
  the request path. Rejected as infeasible at Lambda's ephemeral
  storage/time limits, and wasteful (re-tiling geometry that never
  changes, just to attach different properties).
- **(b) A tile-serving API that joins on the fly**: each request reads one
  static tile from the existing `buildings.pmtiles` plus the scenario's
  own thin results, joins by `building_id`, returns one MVT tile. Matches
  ADR-0003's existing design (geometry tiled once, offline; scenario
  output is a thin per-building result) -- this just moves the join from
  the browser to a Lambda, one tile at a time, instead of removing it.

(b) was chosen. Locally, this landed as `services/scenario/local.py`'s
`GET /tiles/{scenario_id}/{z}/{x}/{y}.mvt`, backed by a `results/`
directory mirroring the eventual S3 layout (`status.json`,
`municipality_stats.json`, `buildings.json.gz` per scenario). Bringing
this to production raised the concrete question this ADR is about: what
should the *deployed* version of this endpoint look like?

The existing `services/scenario` Lambda (behind `ScenarioFunctionUrl`)
pulls in `openquake.hazardlib`/`numpy`/`scipy`/`fiona`/GDAL — confirmed
7-9s+ cold starts even for its own routes that never touch physics
(`/faults`, `/buildings/{id}`), which is why `engine.py`/`ground_motion.py`
are imported lazily inside `_run_and_respond` rather than at module level.
The tile-join code needs none of that: only `pmtiles`/`mapbox_vector_tile`'s
compiled protobuf schema.

## Decision

**A separate, lightweight Lambda for tile-serving** (`services/tiles`),
not a new route on the existing scenario Lambda. Zip-packaged (via
`aws-cdk.aws-lambda-python-alpha`'s `PythonFunction`, not a container
image like the scenario function) rather than the container-image
approach the scenario function uses, since its dependency closure is small
enough not to need one. Reasoning:

- **Cold-start isolation**: a warm scenario-Lambda container already
  carries the full scientific-Python import weight; routing tile requests
  through it risks either warming that weight into containers that never
  needed it, or (if lazily imported) still paying real per-request
  overhead checking for it. A dedicated small function's cold start stays
  bounded by its own (small) dependency closure regardless of how heavy
  compute gets.
- **Independent scaling/concurrency**: a zoomed map fires a dozen-plus
  tile requests at once (bursty, latency-sensitive). Scenario compute is
  occasional and CPU/memory-heavy (3008MB, up to 120s timeout). Sharing
  one Lambda's concurrency budget between those two very different
  traffic shapes means a tile burst could compete with in-flight scenario
  computes for warm capacity, or vice versa.
- **Independent tuning**: memory/timeout that make sense for one are
  wrong for the other (tile joins measured in the tens of milliseconds
  warm; scenario computes in seconds).

**The actual join logic lives in `services/tiles/tile_join.py`, shared
by both runtimes** (local dev's `services/scenario/tile_join.py` is now a
thin I/O wrapper that delegates the join itself to `tiles.tile_join`).
This is the same "local ↔ cloud parity" split ADR-0001 established for
`local.py`/`handler.py`'s domain logic -- the tricky part (patching MVT
tags directly on the compiled protobuf message rather than going through
`mapbox_vector_tile`'s `encode()`/`decode()`, which always reconstructs
every feature's geometry via Shapely even when geometry is unchanged --
measured ~0.9s of a 22,231-feature tile's ~1.1s total was that
reconstruction alone, for a result already known ahead of time) can't
drift between the two.

**Results are S3-backed in the cloud** (`services/tiles/results_store.py`),
same key layout as local dev's `results/<scenario_id>/` directory
(`status.json`, `municipality_stats.json`, `buildings.json.gz`) so the two
runtimes agree on where a scenario's results live without hardcoding.
`services/scenario/handler.py`'s compute path now writes this layout to
the results bucket (via the same `tiles.results_store` module) in addition
to its existing large-payload-to-S3 escape hatch, and both paths return
the same `scenario_id` -- one id per scenario run, not two independent
ones.

**PMTiles is read via S3 range-GETs, not downloaded whole**
(`services/tiles/s3_pmtiles.py`, mirroring the `pmtiles` package's own
local-file `MmapSource`) -- `buildings.pmtiles` is multi-GB; PMTiles'
whole design point is that a reader only needs to fetch the header, the
relevant directory entries, and the one tile it wants, which range-reads
translate directly to.

## Alternatives considered

- **(a) Per-scenario `buildings.pmtiles`**: see Context -- infeasible at
  Lambda's storage/time limits, and re-tiles unchanged geometry for no
  reason.
- **Add `/tiles/*` to the existing scenario Lambda**: simpler (one
  function, one deploy) but risks exactly the cold-start/concurrency
  coupling this ADR's Decision explains. Rejected -- the user explicitly
  asked to evaluate this trade-off given cold-start concerns, and chose
  the split.
- **Debris tile-joining, done the same way now**: deferred at the time
  (since done -- ADR-0019, once the client-side list it relied on proved
  too large to ship). Debris
  doesn't currently need aggregate stats or a results-driven join --
  building IDs + damage state are enough for the frontend to pick the
  right precomputed ring client-side. Revisit if/when debris aggregate
  stats are needed (`docs/scenario-result-api.md`'s own future-work list).

## Consequences

- Two Lambda Function URLs now, not one -- `VITE_SCENARIO_API_URL` and
  `VITE_TILES_API_URL` (`docs/deploy-cloudflare.md`). Local dev is
  unaffected (`local.py` serves both `/scenarios/*` and `/tiles/*` itself;
  `TILES_API_URL` in `scenarioApi.ts` defaults to the same value as
  `API_URL` unless overridden).
- `services/tiles/src/requirements.txt` is a generated file (from
  `uv export`, see its own header comment for the exact command) that has
  to be regenerated by hand after changing `services/tiles/pyproject.toml`
  -- `aws-cdk.aws-lambda-python-alpha`'s bundler needs it physically
  present next to the handler code, not just declared in the package's own
  `pyproject.toml` (a src-layout package, so the two aren't the same
  directory). Same "someone has to remember" caveat ADR-0016 already
  flagged for `buildings-cloud.parquet`.
- `mapbox-vector-tile` (and its own `shapely`/`pyclipper` dependencies) is
  no longer a *direct* dependency of `services/scenario` -- only
  `services/tiles` imports it now (for its bundled protobuf schema, not
  `encode()`/`decode()` themselves, per the join-logic rationale above).
- (Superseded by ADR-0019, which removed the presigned-URL path and the
  per-building list from the response entirely.) Scenario compute's
  presigned-URL escape hatch (`_write_large_payload_to_s3`)
  and the tile-join results (`results_store.py`'s three files) are now two
  separate S3 writes per large scenario, under the same `scenario_id` --
  slightly more S3 traffic per compute request, not expected to matter at
  MVP scale.
- **Results are gzipped JSON, not parquet, specifically because of the
  tiles Lambda's size budget**: a real `cdk deploy` failed outright with
  `pandas`/`pyarrow` in `services/tiles`'s dependency closure --
  `"Unzipped size must be smaller than 262144000 bytes"` (Lambda's 250MB
  zip-package hard limit; `pyarrow` alone is ~155MB unzipped, `pandas`
  another ~70MB). `write_buildings`/`read_building_results`
  (`results_store.py`, both the local-disk and S3-backed versions) write
  JSON instead, meaning `services/tiles` never needs pandas/pyarrow/numpy
  at all (down to ~87MB unzipped: mapbox_vector_tile + its own
  shapely/pyclipper/protobuf + pmtiles + numpy, the last one still pulled
  in transitively by shapely). Plain uncompressed JSON was tried first,
  but measured ~5-7x larger than the parquet it replaced (3.6MB vs 762KB
  at 20k rows; 72.7MB vs 10.5MB at 400k) -- real S3 storage/transfer
  bloat, not just a theoretical concern, flagged before this shipped.
  Gzipping (`buildings.json.gz`) closes that gap almost entirely
  (590KB/11.8MB for the same two sizes, roughly parquet-sized or smaller)
  for a decompress+parse cost still in the tens of milliseconds even at
  400k rows -- stdlib `gzip`, no new dependency. `municipality_stats.json`
  stays uncompressed (at most ~8,200 municipalities nationwide, small
  regardless of format). `services/scenario/handler.py` converts its
  DataFrame to a plain list of dicts itself before calling
  `tiles.results_store.write_buildings` -- that shared module stays free
  of pandas even though its caller already has it.
