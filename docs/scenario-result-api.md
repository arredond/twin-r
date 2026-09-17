# The scenario result API: current shape, limitations, potential improvements

Working notes from investigating a reported FE lag ("the app starts to lag
when simulating low probability scenarios and zooming into cities") --
recorded here because the answer turned out to be about the API's response
shape, not the map rendering itself, and is worth having in one place for
whoever picks up the performance work next.

## What exists today

Three runtimes share the same domain logic (`engine.py`/`rupture.py`/
`ground_motion.py`/`damage.py`) behind two thin adapters:

- **`services/scenario/src/scenario/local.py`** -- FastAPI app, what
  `bin/twinr` runs locally (`uvicorn scenario.local:app`). This is what the
  frontend talks to in dev, and by default (`VITE_SCENARIO_API_URL` unset).
- **`services/scenario/src/scenario/handler.py`** -- AWS Lambda adapter
  behind a Function URL (`infra/stacks/twin_r_stack.py`), for a deployed
  frontend pointed at `VITE_SCENARIO_API_URL`.

Both expose the same four routes:

| Route | Mode | Determines |
|---|---|---|
| `POST /scenarios/manual` | manual rupture (lat/lon/mag/...) | full response below |
| `GET /scenarios/fault` | pick a QAFI fault by id | full response below |
| `GET /faults` | list nearby faults (sidebar dropdown) | `Fault[]`, nearest-first |
| `GET /buildings/{id}` | one building's static attributes | popup extras |

### The scenario response shape

`_run_and_serialize` (`local.py`) / `handler()` (`handler.py`) both build:

```json
{
  "rupture": { "lat": ..., "lon": ..., "mag": ..., "source": ..., "finite_rupture": bool, "probability_level": "high" | "low" | "very_low" },
  "evaluated_region": { "lat": ..., "lon": ..., "radius_km": ... },
  "buildings": [ { "building_id": ..., "damage_state_code": 0-4, "prob_none": ..., "prob_slight": ..., "prob_moderate": ..., "prob_extensive": ..., "prob_complete": ... }, ... ],
  "n_evaluated": <int>,
  "municipality_stats": [ { "municipality_code": ..., "n_evaluated": ..., "counts": {...} }, ... ]
}
```

Two things already trim this down a lot (`response.py`, well-commented in
place, worth reading directly):

- **`buildings` only lists damaged/uncertain buildings**, not everything
  evaluated. A "None"-modal building only ships if its probability margin
  over the next-most-likely state is under `UNCERTAINTY_MARGIN` (0.15,
  `response.py`) -- shipped as a probability *margin* against the
  runner-up class rather than a flat `P(None)` cutoff, after the flat
  version regressed for long/large faults. This is the same
  non-"None" filtering `docs/validation-region-expansion.md` §4/§6
  measured a ~50x payload reduction from (634MB -> 12.8MB worst case,
  see below); the frontend colors the confidently-undamaged majority via
  the `evaluated_region` circle instead of a per-building entry.
- **Only 7 columns per building** ride along (`building_id`,
  `damage_state_code`, five `prob_*` fields, each rounded to 4dp) --
  `lon`/`lat`/`im_value`/`im_type` are dropped because the frontend already
  has every building's geometry from `buildings.pmtiles` and joins by
  `building_id`; shipping coordinates twice was confirmed unused and cut.
- Both runtimes gzip the response (`GZipMiddleware` locally,
  hand-rolled `gzip.compress` + base64 in the Lambda adapter, the latter
  because a Function URL's default **BUFFERED** invoke mode caps responses
  at 6MB uncompressed).

### How big `buildings` actually gets

`radius_km` (the spatial pre-filter radius, echoed back as
`evaluated_region.radius_km`) comes from
`ground_motion.estimate_significant_distance_km`: a binary search for the
distance at which this specific rupture's SA(0.3s) drops below a "no
fragility curve responds to this" floor, clamped to `[10, 300]` km. Two
things widen it a lot for the probability-level tiers MERISUR calls "low
probability / high impact" (`probability_level.py`):

- `"low"`/`"very_low"` use `sigma_multiplier=1.0` (median + 1 sigma ground
  motion) instead of `"high"`'s `0.0` -- the same magnitude earthquake
  stays "significant" out to a much larger radius.
- `"very_low"` additionally reports the **85th percentile** damage state
  (`damage_percentile=0.85`) instead of the modal one, which pushes many
  more buildings' damage state up a notch and out of the "confidently
  None" bucket `prepare_response_buildings` would otherwise drop.

Combined, a large/long fault at `"very_low"` can return **100k+ buildings**
nationwide (`apps/web/src/components/DamageMap.tsx`'s own comment on
`scheduleChunked`, written from having actually seen this) -- comfortably
past a "represents what's near the current view" size.

### Prior art: this has already been measured once

`docs/validation-region-expansion.md` §4/§6 hit and fixed a version of
this problem already, at *regional* scale (2.77M buildings, before the
national crawl): an early measurement found a single call returning
**634MB of JSON** (Granada, 2.5M buildings evaluated), with wall time
dominated by serializing/transferring that payload rather than the
GMPE/fragility compute itself. The fix -- the adaptive
`estimate_significant_distance_km` radius (replacing a flat 300km sweep)
plus the non-"None" filtering described above -- brought the worst
measured case down to **12.8MB** and typical cases to 0KB-3MB, fast
enough that §4's remaining two options ("results-to-URL... `handler.py`'s
Lambda path already supports this via `TWIN_R_RESULTS_BUCKET`" and "a
binary/columnar response format") were explicitly left undone, "no longer
an urgent problem, just a further optimization if usage patterns ever
demand it."

That measurement predates both the national-scale crawl (12.9M buildings,
~4.6x this test's dataset) and, per `git log`, the probability-level
selector (`"low"`/`"very_low"`, ADR-0011) -- neither `sigma_multiplier`
nor `damage_percentile` existed yet to widen the non-"None" set the way
they do now. The 100k+-building case this doc opens with is exactly the
"usage patterns ever demand it" condition that section was written to
recognize, at a scale it didn't test. The two deferred options are
revisited below with that in mind, alongside newer ones specific to what's
changed since (the debris layer, viewport-driven map interaction).

### How the frontend consumes it

`DamageMap.tsx` never re-tiles or re-queries by viewport -- it takes the
full `buildings` array from state and, per scenario run:

1. Clears the previous run's `feature-state` off every previously-colored
   building/debris-ring id (also chunked -- the old run can be just as
   large as the new one).
2. `setFeatureState`s every entry in the new `buildings` array onto
   `BUILDINGS_SOURCE_ID` (color) **and again** onto `DEBRIS_SOURCE_ID`
   (`damage_state_code`, which the debris paint expression reads to decide
   which rings show) -- two full passes over the same array, one per
   MapLibre source, since a `promoteId` is source-scoped.
3. Both passes are spread across `requestIdleCallback`/
   `requestAnimationFrame` chunks (`scheduleChunked`, 5000 items/chunk) so
   applying doesn't freeze the tab synchronously -- but this only smooths
   *when* the work happens, not *how much* work there is.

The static geometry layers (`buildings.pmtiles`, `debris.pmtiles`,
`municipalities.pmtiles`) are genuinely viewport-scoped already: PMTiles
is read via HTTP range requests, and MapLibre only requests/renders tiles
intersecting the current view at the current zoom, same as a live tile
server would. **The scenario result is the one part of this pipeline that
isn't** -- it's a computed, per-request join, not static tiled geometry,
and today it's shipped and applied in full regardless of where the map is
actually looking.

## Limitations

1. **Not viewport-scoped.** The whole point of this doc. A nationwide
   `very_low` run applies feature-state for every evaluated building even
   when the user is zoomed into one city block -- both the initial
   download (gzip helps network size, not client CPU) and the
   `setFeatureState` volume scale with the *evaluated region*, not the
   *visible* one. This is the direct cause of the reported zoom lag.
2. **No caching, despite `"automatic"` mode being close to deterministic.**
   `runFaultScenario`'s own doc comment already notes `fault_id` +
   `near_lat`/`near_lon` fully determine the rupture (QAFI supplies
   mmax/geometry/dip/rake); combined with `probability_level`, the same
   triple always produces the same result. Nothing memoizes this --
   picking the same fault/probability-level twice recomputes and
   re-downloads the full payload both times.
3. **Deployed (Lambda) path returns a shape the frontend can't read.**
   `infra/stacks/twin_r_stack.py` always sets `TWIN_R_RESULTS_BUCKET`, so
   `handler.py`'s `RESULTS_BUCKET is None` branch is dead in the deployed
   stack -- every request there returns `{"result_url": "s3://..."}`
   instead of the inline payload. `apps/web/src/scenarioApi.ts` has no
   code path that reads `result_url` at all (grepped: zero references), so
   a frontend actually pointed at the deployed Function URL would silently
   receive `{ result_url }` where it expects `{ rupture, buildings, ... }`
   and fail to render anything. Independent of that gap, the URL it
   returns wouldn't even be browser-fetchable as-is: it's a bare `s3://`
   URI (not `https://`) into a bucket created with
   `BlockPublicAccess.BLOCK_ALL` and no presigned-URL generation --
   nothing between the Lambda and a browser can currently turn
   `result_url` into bytes. This looks like an escape hatch that was
   half-built (server side only) for exactly the large-payload problem
   this doc is about, then never wired up.
4. **BUFFERED Function URL invoke mode + a 6MB cap, worked around rather
   than sized for.** The gzip-in-handler comment is explicit that this is
   to dodge the cap, not because gzip alone was judged sufficient --
   `handler.py`'s own docstring flags "an uncompressed large-fault payload
   can run well past that." A large enough `very_low` national result
   could still exceed 6MB *after* gzip.
5. **Debris rings double the per-building work for information the
   backend already sent once.** `damage_state_code` is identical between
   the buildings and debris `setFeatureState` calls -- MapLibre's
   `feature-state` is source-scoped, so today's two-source design
   (buildings source has the footprint, debris source has the rings)
   genuinely needs two `setFeatureState` calls per building, not one. Not
   obviously fixable without a MapLibre-level capability
   (cross-source feature-state reads) that doesn't currently exist --
   noted here as a cost, not proposed as something to change.
6. **No incremental feedback.** The user waits for the full
   compute-and-download-and-apply cycle before seeing anything change;
   there's no "buildings near you are ready" partial result.

## Potential improvements

Roughly in order of how directly each one addresses the viewport-scoping
problem vs. how much redesign it needs:

- **Bbox-scoped result delivery.** Have the frontend pass the current
  viewport bounds (already computed for `map.fitBounds` elsewhere) as an
  optional filter, and have the backend intersect `result` against it
  before calling `prepare_response_buildings` -- refetching as the user
  pans/zooms (`moveend`, same event already used for `mapCenter`). This is
  the most direct fix for the actual complaint, but changes the
  request/response contract (result now depends on viewport, not just
  rupture params) and needs a decision on refetch cadence/debouncing and
  on how `municipality_stats`/the choropleth (which deliberately wants the
  *full* evaluated region's aggregate, not a viewport slice) keep working
  once `buildings` itself is scoped.
- **Precompute + cache the automatic-mode result space.** `/scenarios/fault`
  is nearly a pure function of `(fault_id, near_point, probability_level)`
  -- QAFI has 201 faults and there are only 3 probability levels; even
  without full precomputation, caching (in-process LRU, or a small
  DynamoDB/S3-keyed cache in the Lambda deployment) would turn "pick the
  same fault twice" from a full recompute+re-transfer into a cache hit.
  Manual mode (arbitrary lat/lon/mag) doesn't have this same small
  enumerable space and would stay fully dynamic.
- **Stream the response instead of buffering it whole.** NDJSON or
  chunked transfer-encoding would let the frontend start applying
  `feature-state` on the first buildings received instead of waiting for
  the full JSON array to parse -- pairs naturally with the
  already-existing `scheduleChunked` client-side logic, and sidesteps the
  Lambda Function URL 6MB BUFFERED cap by switching to
  `RESPONSE_STREAM` invoke mode instead of the current
  gzip-and-hope-it's-under-6MB approach.
- **Finish or remove the `result_url`/S3 escape hatch** --
  `docs/validation-region-expansion.md` §4's "results-to-URL" option,
  built server-side (`handler.py`'s `RESULTS_BUCKET` branch) but
  deliberately left there as a future lever once regional-scale filtering
  made it non-urgent. It's currently dead code from the frontend's
  perspective and unusable even if the frontend tried, since the bucket
  blocks public access and nothing presigns a URL. Either wire it up
  properly (presigned GET URL, frontend fetches it, `RESULTS_BUCKET`
  becomes a real large-payload path) or remove it so `handler.py`'s
  behavior doesn't silently diverge from `local.py`'s. Worth noting this
  wouldn't shrink the payload or reduce `setFeatureState` volume by
  itself -- it only moves *how* the same bytes arrive, so it doesn't
  address the viewport-scoping problem on its own.
- **A binary/columnar response format** -- §4's other deferred option.
  Would shrink transfer size further (7 numeric fields x N buildings is a
  natural fit for something like a flat `Float32Array`/typed-array wire
  format instead of a JSON object per row) but, like the point above,
  doesn't reduce how much `feature-state` the client ultimately applies --
  complementary to, not a substitute for, viewport scoping.
- **Surface `n_evaluated` as an early warning.** It's already computed
  before the thin payload is built and already returned -- the frontend
  could show "this will color N buildings" (or a spinner with that count)
  before/while a very large payload downloads, rather than the UI looking
  identically unresponsive for a 50-building Lorca run and a 150,000-
  building national one.

None of this is implemented yet -- this is a survey of the current
contract and its failure modes, written to ground a follow-up decision on
which (if any) of the above is worth doing.
