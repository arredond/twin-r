# ADR-0018: Geometry-anchored fault ruptures, content-addressed scenario ids, and a result cache

Status: accepted (cache); proposed (CloudFront, see last section)

## Context

Three related problems with the automatic-mode (fault) API, now that the
backend is deployed (ADR-0016/0017):

1. **`GET /faults` took `lat`/`lon`/`radius_km`**, but only to sort and
   filter a list small enough to ship whole (QAFI v4: 201 faults). The
   frontend refetched it on every map pan to keep it sorted nearest-first,
   a Lambda round trip (sometimes a cold start) each time.
2. **`GET /scenarios/fault` took `near_lat`/`near_lon`**, anchoring the
   rupture at the trace point closest to the user's view. We checked
   whether that still matters:
   - All 201 QAFI v4 faults have a trace, dip and depth range, and all 201
     build a finite rupture surface (checked by building each one through
     `rupture.from_fault`). None fall back to a point source.
   - With a surface, the reference point never reaches the damage
     computation. `engine._load_sites` pads the surface mesh's own
     extent, `ground_motion.compute_intensity` takes Rjb from the surface,
     and `estimate_significant_distance_km` depends only on distance.
     ADR-0009 had already noted the result was independent of it.
   - Checked empirically on the national dataset with Alhama de Murcia
     (ES626, 1,666,207 buildings evaluated), anchored at each end of its
     trace. Same rows, same radius, same `damage_state` for every
     building. `im_value` differed on 1,356 rows, but running the *same*
     anchor twice differed on 3,960. That noise is existing run-to-run
     nondeterminism (see Consequences), not an effect of the anchor.

   So for every current fault the point only chose what got *echoed back*:
   `rupture.lat/lon` and the center of the `evaluated_region` circle. That
   made a deterministic result look view-dependent and blocked caching by
   `fault_id`.
3. **Every request recomputed.** Scenario ids were random UUIDs, so an
   identical request never found the results already sitting in the
   results bucket. Real deployed fault requests take 12-33s
   (docs/known-issues-cloud-deploy.md).

## Decision

**`GET /faults` takes no arguments** and returns every fault, sorted by
name, with a new `has_rupture_geometry` flag. Its per-request
`lat`/`lon`/`distance_km` columns are gone. The frontend fetches the list
once and sorts it nearest-first on the client (`App.tsx`'s
`sortFaultsByDistance`) from the trace geometry it already has.

**The fault rupture's location comes from its own geometry**
(`faults.rupture_anchor`):

- Full rupture geometry (all QAFI v4 faults): the midpoint of the trace's
  longest piece by geodesic length (`trace.trace_midpoint`), which is the
  same piece the surface is built from. `near_lat`/`near_lon` are ignored.
- No rupture geometry, with `near_lat`/`near_lon`: a point source at the
  trace point closest to them. This is the only case where they matter.
- No rupture geometry, no reference point: the trace midpoint.

`near_lat`/`near_lon` are now optional. The frontend sends them **only**
for a fault with `has_rupture_geometry: false`, so no request sends them
today.

**`evaluated_region` is centered on that same rupture point**, with its
radius widened by the finite surface's farthest mesh point from the center
(`response.evaluated_region`). Without the widening, a long fault's
midpoint-centered circle would render evaluated buildings near both ends
of the trace grey ("never evaluated"). The old view-anchored circle had
the same flaw at whichever end the user *wasn't* looking at. Manual-mode
finite ruptures get the same treatment.

**Scenario ids are content-addressed** (`scenario_id.py`): the first 32
hex characters of a SHA-256 over canonical JSON of:

- the request inputs: `fault_id` + `probability_level`, plus the rounded
  `near_lat`/`near_lon` only when `rupture_anchor` actually used them. For
  manual mode, every rupture field.
- `API_VERSION`, a counter in `scenario_id.py`. **Bump it whenever
  calculation or response code changes in a way that could change a
  result.**
- `TWINER_DATA_VERSION`, set per deployment (`DATA_VERSION` in
  `infra/stacks/twiner_stack.py`). **Bump it and `cdk deploy` whenever
  data the scenario function reads is re-uploaded.**

A version bump changes every id, so older results are never looked up
again. They age out under the results bucket's lifecycle rule (30 days
originally, 365 since `bin/warm-scenario-cache` -- see Consequences).
Nothing needs deleting.

**The backend checks for a stored result before computing**, when
`TWINER_SCENARIO_CACHE` is truthy:

- Lambda (`handler._cached_response`): `HEAD scenarios/<id>.json` in the
  results bucket, then a fresh presigned URL on a hit. (ADR-0019 since
  moved the entry to `<id>/response.json` and returns it inline -- no
  presigned URL.) This check runs
  before the rupture is built, so a hit never imports
  engine/hazardlib. The key is written last, after the tile-join results,
  so if it exists the tiles Lambda can serve that id too.
- Local (`local._cached_response`): `results/<id>/response.json.gz`, plus
  a check that `buildings.json.gz` exists. Also warms the tile pool, same
  as a fresh compute.
- Responses carry `cached: true|false`, shown in the sidebar.

**Defaults: off in code and in `bin/twiner` (local dev), on in the
deployed stack.** Locally, the calculation code is what's being changed,
so silently serving old results would be a trap. Opt in with
`TWINER_SCENARIO_CACHE=1 twiner start`. To force recomputation in the
cloud, set the Lambda's env var to `0`.

## Alternatives considered

- **Keep `near_lat`/`near_lon` in the id always**: splits one result
  across as many ids as there are map views, so the cache would almost
  never hit. Rejected; they're included only when used.
- **Git SHA as the API version**: invalidates the cache on every commit,
  including docs/frontend/infra ones. An explicit counter makes you
  decide, at the cost of having to remember (see Consequences).
- **Derive the data version from S3 ETags/local mtimes automatically**:
  more robust to a forgotten bump, but costs a HEAD per input file per
  cold start. It also still goes stale for warm containers after a data
  upload made without a redeploy. Worth revisiting if a forgotten bump
  ever bites.
- **In-process LRU only**: Lambda containers are short-lived and each has
  its own memory, so hit rates would be poor. The results bucket
  already held everything needed; it just wasn't addressable.

## Consequences

- **Two manual steps now guard cache correctness**: bump `API_VERSION`
  on calculation changes, and `DATA_VERSION` on data uploads. Forgetting
  either serves stale results until the next bump or the lifecycle
  expiry, now 365 days.
- Measured locally for ES626 (1.67M evaluated, 19,878 shipped): 2.09s to
  compute, 0.16s for the cache hit (wall time through the FastAPI app).
  Deployed, the saving is the whole 12-33s compute. A hit still costs a
  Lambda invocation (cold start included), a HEAD and a presign (a GET
  since ADR-0019).
- **Pre-populating**: `bin/warm-scenario-cache` requests every fault x
  probability level (603 scenarios for QAFI v4) against a scenario API,
  with bounded concurrency and retries. Run it after any deploy that bumps
  either version. Estimated from a month of CloudWatch billed durations
  (warm computes 2-19s, cold starts 50-120s, at 3,008MB): roughly
  18,000-35,000 GB-s per full sweep, well under $1, and ~15-25 minutes at
  concurrency 8-10. To keep a full cache from expiring monthly, the
  results bucket's lifecycle went from 30 to 365 days.
- **Existing nondeterminism, surfaced by the check above, not fixed
  here**: `compute_intensity_gridded` takes each 1 km cell's Vs30 from
  its *first* row (`first_index`), and DuckDB doesn't guarantee row order.
  Identical requests can differ slightly in `im_value`/`prob_*`: up to
  ~0.08 in `prob_none` on ES626, with no `damage_state` flips observed in
  that run. With the cache on, the first computed result is what everyone
  gets from then on, which is arguably better. Fixing it (ordering
  `_load_sites`, or a deterministic per-cell Vs30 such as the median)
  changes results, so it needs its own decision.
- Frontend: the `/faults` refetch on pan is gone, and a scenario request
  URL no longer depends on map position, a prerequisite for any HTTP cache
  (below).

## CloudFront evaluation (proposed, not implemented)

With the result cache in place, the expensive part (compute) is already
skipped on repeats. CloudFront would remove what's left: the Lambda
invocation itself (including cold starts), and, more importantly, the
**tile requests**. Those are the highest-volume traffic by far: a dozen or
more per pan/zoom, each one a tiles-Lambda invocation. Assessed per route:

| Route | Cacheable? | Invalidation needed? |
|---|---|---|
| `GET /tiles/<scenario_id>/z/x/y.mvt` | Yes. Content-addressed, immutable per id | **None**. A version bump changes the id, so the URL changes |
| `GET /scenarios/fault?...` | Yes, keyed on the query string. The URL no longer depends on map view | **Yes**. The URL doesn't contain the versions, so a bump needs `create-invalidation /scenarios/*` |
| `GET /faults` | Yes | Yes, on data upload (`/faults`) |
| `POST /scenarios/manual` | No. CloudFront never caches POST | n/a; the Lambda-level cache covers it |
| Results payload (`scenarios/<id>.json`) | Only if served via CloudFront, not presigned S3 URLs | None (content-addressed) |

**Blocker for caching `/scenarios/fault`** (resolved by ADR-0019: the
response is now small and returned inline, so step 2 below is moot and
debris tiles join `/tiles/*` in row 1): the deployed response was
`{result_url: <presigned URL, 5-minute expiry>}`. A CloudFront copy that
lives longer than 5 minutes would hand out dead URLs. Serve the results
bucket through the same distribution (Origin Access Control, a
`/results/*` behavior) and return a stable path instead. The content is
immutable, so no invalidation is ever needed there.

**Recommendation**: yes, as a follow-up, in this order:

1. **`/tiles/*` behind CloudFront, long TTL.** Biggest request-volume
   win, zero invalidation burden. Also add
   `Cache-Control: public, max-age=31536000, immutable` in the tiles
   Lambda so browsers cache tiles too.
2. **`/results/*` from the results bucket via OAC**, replacing presigned
   URLs (this also fixes the 5-minute-expiry fragility).
3. **`/scenarios/fault` and `/faults`**, with a cache policy on
   `fault_id`/`probability_level`/`near_lat`/`near_lon`. Use a moderate
   TTL (e.g. 1 day) as a backstop, plus a deploy step (script or CDK
   custom resource) that runs `aws cloudfront create-invalidation --paths
   "/scenarios/*" "/faults"` whenever `API_VERSION`/`DATA_VERSION`
   changes. A wildcard counts as one path; the first 1,000 paths/month
   are free. Alternatively, put the version in the URL (a
   `VITE_API_CACHE_KEY` query param) so no invalidation is needed. That
   ties a frontend rebuild to every backend version bump, which seems
   worse.

Put the Function URL origins behind OAC too (`AuthType: AWS_IAM`), so the
cache can't be bypassed by calling the Function URLs directly. That
changes `VITE_SCENARIO_API_URL`/`VITE_TILES_API_URL` to the distribution
domain. ADR-0016 deferred CloudFront while the backend was still being
tuned. The version-in-id scheme is what makes that concern manageable now:
tiles and results never need invalidating, and the two routes that do
have a well-defined trigger.
