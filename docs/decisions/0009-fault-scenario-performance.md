# ADR-0009: Fault-scenario performance -- chunked distance calc, gridded ground motion, thin GET response

Status: accepted

## Context

Running the Barcelona fault (ME025) against the nationwide dataset
(`data/exposure`, ~12.4M buildings) took ~7.2s server-side and shipped
a 63MB JSON response. Peñacova-Régua-Verín (PO011) -- a much longer fault,
entirely in Portugal, a different country this pipeline has never crawled
and has **zero** exposure data for (unrelated to the Basque Country/Navarra
gap, which a prior session closed -- see
`docs/basque-navarra-cadastral-sources.md`) -- didn't just fail to be fast:
it hung for 5+ minutes and never returned, pegging the single-worker dev
server's CPU the whole time and blocking every other request (including
`/health`) behind it.

Profiling isolated the cause precisely: `hazardlib`'s
`BaseSurface.get_joyner_boore_distance` computes one dense
(surface-mesh-points × sites) geodetic distance matrix in a single call,
with no internal chunking. Measured directly on real fault geometry:

| Sites | Barcelona (270 mesh pts) | Peñacova (1,210 mesh pts) | Ratio |
|---|---|---|---|
| 300k | 0.17s | 0.67s | 4x (~matches mesh-size ratio) |
| 3M | 3.2s | **156.8s** | **49x** |

The 49x blowup at 3M sites (vs. the ~4.5x the mesh-size ratio alone
predicts) is memory pressure, not raw FLOPs: `1,210 × 3,000,000 × 8 bytes ≈
29GB` for one temporary array. A second, independent bug compounded it: the
spatial pre-filter boxed sites around a single representative point on the
fault trace (`rupture.lat`/`rupture.lon`), not the trace's own extent --
wrong for a long fault (QAFI traces run past 100km), silently under-covering
real near-fault buildings past the near end.

Once those were fixed, a further profiling pass showed the exact
(mesh × sites) distance calculation was still ~95% of remaining per-request
time (Peñacova: 6.6s of 6.94s in `compute_sa03`), because it's inherent to
evaluating millions of buildings against a real fault-surface mesh, not
wasted work on filtered-out buildings -- 99%+ of sites in the pre-filter
box already exceed the GMPE significance threshold that derives the box in
the first place, for a magnitude this large.

## Decision

**1. Chunk the distance calculation** (`ground_motion.py`,
`_DISTANCE_CHUNK_SIZE = 200_000`). Same exact result (each chunk is an exact
slice of the same computation, not an approximation), bounded memory.
Measured: Peñacova's 3M-site call, 156.8s → ~4.3-5.8s at 200k-500k chunk
sizes.

**2. Size the spatial pre-filter off the rupture surface's real extent**
(`engine.py`'s `_load_sites`), not a single point -- correctness fix for
long faults, independent of the performance fix above.

**3. Compute ground motion once per ~1km grid cell, not once per building**
(`ground_motion.compute_sa03_gridded`, `SA_GRID_CELL_KM = 1.0`, used by
`engine.run_scenario`). SA(0.3s) barely varies over a span this small at
regional distances -- finer, in fact, than the fault surface's own already-
accepted `DEFAULT_MESH_SPACING_KM = 2.0` (ADR-0007). Real Spanish exposure
data clusters tightly enough that this measures a 40-50x reduction in
distinct points fed into the expensive distance calc (e.g. Peñacova:
4.87M sites → 116,616 unique cells). Verified against the exact per-building
value on live data: mean absolute error ~0.001g, p99 relative error under
3%, well inside the GMPE's own aleatory uncertainty. Only the ground-motion
step is deduped -- the taxonomy/height-specific fragility lookup downstream
still runs per building as before, since damage genuinely depends on the
individual building's typology.

**4. Trim the response payload and compress it**
(`response.py`'s `prepare_response_buildings`, shared by `local.py` and
`handler.py`): drop `lon`/`lat`/`sa03_g` per building (confirmed unused by
the frontend -- every building in the response is already a feature in the
buildings PMTiles layer, joined by `building_id`), encode `damage_state` as
its `DAMAGE_STATES` index instead of a string. Add gzip
(`GZipMiddleware` locally; manual `gzip.compress` + base64 +
`Content-Encoding: gzip` in the Lambda handler, since a Function URL
response must be base64 for a binary body).

**5. `/scenarios/fault` is now `GET`**, not `POST`
(`fault_id`/`near_lat`/`near_lon` as query params, matching `/faults`).
Fault-mode's `rupture.surface` is built entirely from QAFI's own
`geometry_geojson`/`dip`/depths/`mmax`/`rake` -- all fixed by `fault_id`
alone (`rupture.py`'s `from_fault`) -- and after decision #2 above, the
site-filtering box is too. So the returned `buildings` array was already
100% independent of `near_lat`/`near_lon`; those only pick which point gets
echoed back in `rupture`/`evaluated_region` for display. A GET is
cacheable, testable as a plain URL, and honest about what actually varies.
The Lambda handler dispatches fault-mode from `event["queryStringParameters"]`
now instead of the POST body shape.

## Alternatives considered

- **KD-tree spatial index over the fault's mesh points**, as an
  accelerated stand-in for hazardlib's brute-force distance calc: tested
  directly (`scipy.spatial.cKDTree`) and it did *not* pay off -- 4.0s vs.
  6.6s brute-force for the same 4.87M-site query, not the 100x the
  complexity difference (`O(N log M)` vs `O(N×M)`) suggested. A tree with
  only ~1,200 points is too shallow for per-query overhead to beat
  numpy's already-vectorized-in-C broadcast. Recorded here so this isn't
  retried without new evidence.
- **Precompute and cache all 201 QAFI faults' scenario results offline**
  (fault-mode is deterministic in `fault_id` alone, see decision #5): would
  give genuinely instant responses, but deliberately **not done now** --
  caching here would hide the exact class of live-path performance problem
  this ADR just fixed, and needs its own invalidation story (re-run when
  exposure/fragility data changes). Worth revisiting for a production/demo
  deployment specifically, not as a substitute for a fast live path.

## Consequences

- `/scenarios/fault` callers must switch from `POST` with a JSON body to
  `GET` with query params -- the frontend (`scenarioApi.ts`) and Lambda
  handler are both updated; any other direct caller of this endpoint isn't.
- The per-building response schema changed: `damage_state` (string) is now
  `damage_state_code` (int, index into `damage.DAMAGE_STATES`), and
  `lon`/`lat`/`sa03_g` are gone from the *response* (they're still on
  `engine.run_scenario`'s own return value, unaffected -- this is a
  presentation-layer trim in `response.py`, same pattern as the existing
  damaged/uncertain-only filtering).
- Ground motion is now a controlled ~1km-resolution approximation rather
  than an exact per-building value -- acceptable given the error is far
  under the GMPE's own uncertainty, but worth knowing if a future change
  (e.g. a Vs30/soil-class layer, docs/milestone-1-plan.md §2) needs
  finer-than-1km spatial resolution; `SA_GRID_CELL_KM` would need
  revisiting alongside it.
- Measured end-to-end improvement (nationwide dataset, `data/exposure`):

  | | Before this ADR | After |
  |---|---|---|
  | Barcelona (ME025) server time | 7.2s | **1.7s** |
  | Barcelona payload (wire, gzip) | 63.2 MB | **~1.5 MB** |
  | Peñacova (PO011) server time | never completed (>5min, hung) | **2.8s** |
  | Peñacova payload | never returned | ~117 KB |
