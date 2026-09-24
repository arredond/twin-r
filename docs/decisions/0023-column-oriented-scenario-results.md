# ADR-0023: Column-oriented JSON for per-building scenario results; a right-sized tiles Lambda

Status: accepted

## Context

After a scenario runs, the scenario function stores the buildings it lists
(damaged or uncertain, `response.shipped_mask`) for the tile joins
(ADR-0017, ADR-0019). Every tile request for that scenario looks buildings
up in that file. The file was a gzipped JSON list with one object per
building, parsed into a dict of dicts once per container and scenario
(`tiles.results_store.read_building_results`, `lru_cache(maxsize=64)`).

An M9 manual scenario on Madrid (`c6cacc9d...`, 6.48M buildings
evaluated) lists 448,557 buildings: 2.19MB gzipped, 80MB of JSON. In the
tiles Lambda (512MB, ~0.3 vCPU, 10s timeout), CloudWatch showed, on
2026-09-24:

- Each of ~10 containers loading it in parallel on the first tiles of a new
  scenario, in synchronized bursts: 2-5s for small scenarios, 8.7-10s for
  this one. Warm tiles took 140-420ms.
- Requests ending at exactly 10,000ms (the timeout), and 10
  `Runtime.OutOfMemory` kills at 512MB.
- Container memory ratcheting 200 → 440 → 488MB across scenarios: the
  64-entry cache kept every scenario a container had served.

Loaded in a fresh process, that file alone takes +568MB RSS.

## Decision

1. **Cache two scenarios per container, not 64**
   (`tiles.results_store.RESULTS_CACHE_SCENARIOS`; local dev's tile workers,
   `scenario.tile_join`, likewise). That's enough to switch between two
   scenarios; anything older is re-read if revisited.

2. **Tiles Lambda: 1769MB (one full vCPU), 30s timeout**, up from
   512MB/10s (`infra/stacks/twiner_stack.py`). For CPU-bound work the cost
   stays about the same, since requests finish ~3.5x sooner. The longer
   timeout keeps a slow first load a slow tile, not a failed one.

3. **Store the results column-oriented**: one JSON array per column, ids
   sorted and unique, looked up by binary search. It's defined and
   documented in `services/tiles/src/tiles/scenario_results.py`, which is
   the spec. That module is the only encoder/decoder, shared by the
   scenario function (write), the tiles Lambda and local dev (read).
   Stdlib only. It uses a new, versioned file name
   (`buildings.columns.v1.json.gz`) and checks `format`/`version` on read.
   `API_VERSION` → 4.

On the M9 file: load 418ms → 129ms, fresh-process RSS +568MB → +186MB, a
3,000-building tile's lookups 1.0ms, and every one of its 448,545 distinct
buildings reads back identical. Encoding takes 0.84s once per scenario,
about what the previous format cost. That's with gzip at level 6: level 9
took 2.72s for a file 2% smaller.

## Alternatives considered

Benchmarked on the same file (the module docstring has the full table):

- **Protobuf**, already a dependency via vector tiles: loads no faster than
  JSON (108ms vs 94ms, both with integer-coded probabilities; converting
  448k ids to Python strings dominates both). It would add a `.proto`
  schema and generated code.
- **Parquet via pyarrow**: the best standard option (34ms). But pyarrow is
  157MB unzipped; with the tiles package at 87MB, that's 244MB against
  Lambda's 262MB zip limit, the same limit a pandas/pyarrow deploy already
  failed on. A custom container image (as in aws-sdk-pandas#320) removes
  the limit, but trades this function's ~0.85s zip Init for the
  multi-second image-fetch cold starts the scenario function sees
  (ADR-0021). Worth revisiting if the tiles function needs pyarrow for
  something else.
- **A bespoke binary layout** (length-prefixed ids, `array` columns): the
  fastest (18ms) and smallest in memory, but a custom format to maintain,
  for ~100ms saved once per container and scenario.
- **Results split per tile** (one object per z12 tile, say), so a tile
  request reads only its own rows: makes tile cost independent of scenario
  size, but means many more S3 writes per scenario and a larger change.
  Kept in reserve if these fixes aren't enough.
- **CloudFront in front of `/tiles/*`** (ADR-0018's proposal): still
  worthwhile, but it only helps repeat views, not the first one.

## Consequences

- The previous format isn't readable any more. Scenarios computed before
  this deploy have no `buildings.columns.v1.json.gz`, so their tiles 404
  instead of misreading. The `API_VERSION` bump means none of them are
  served from the cache either; re-run `bin/warm-scenario-cache` after
  deploying.
- A future change to the file's shape bumps `FORMAT_VERSION`, which also
  changes the file name, along with `API_VERSION`.
- Each tiles invocation is billed at 1769MB instead of 512MB; see the
  cost note in decision 2.
- `tile_join` now takes any `ResultsLookup` (anything with
  `.get(building_id)`), so `ScenarioResults` and a plain dict both work.
