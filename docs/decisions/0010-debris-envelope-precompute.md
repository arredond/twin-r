# ADR-0010: Precompute debris envelopes per building; scenarios only pick a ring

Status: accepted. Steps 1-2 of `milestone-1-plan.md` §10 are fully done at
**national scale**: `debris.pmtiles` (9.26GB, 848,974 tiles, max zoom 16)
covers all of Spain, built from 51,483,434 ring rows across 12,881,817
buildings. See Consequences for the Lorca-scale measurement and a new
section below for what national scale actually took and why.

**This computed data is expensive and should be treated as a durable
artifact, not something to casually regenerate.** See "Do not redo this
from scratch" below before touching `compute_debris_region` or
`tile_debris_region_by_province` again.

## Context

Debris is the last documented MERISUR component `twin-r` doesn't have
(`docs/milestone-1-plan.md` §1/§8: "Damage only, no debris... a
milestone-1.x follow-up"). The only debris methodology we have in enough
detail to implement is the 2018-tool table (`docs/merisur.md` §4.8):
damage state → buffer distance from façade (Slight 1m / Moderate 2m /
Extensive 3m / Complete 4m). The richer 2023 Gaspar-Escribano et al. model
(real volumes, calibrated against 2011 Lorca debris-removal records) is
paywalled — see `docs/questions-for-upm.md` §3, updated alongside this ADR.

The question this ADR actually answers is architectural, not the debris
formula itself: **where does debris geometry get computed, and when?**
ADR-0003 already established the pattern for buildings — geometry that
doesn't change between scenario runs is tiled once, offline, and scenarios
only ever produce a thin `building_id → result` join. Debris geometry
(the shape of the area a building's façade could shed rubble onto) has the
same property: it depends on the building's footprint and its neighbors,
not on which earthquake is being simulated. Only *which ring of that shape
is active* depends on the scenario (the modal damage state, already
computed by `engine.py`/`damage.py`).

A naive per-scenario approach — buffer every evaluated building's footprint
outward by its damage-state distance, at request time — would duplicate
ADR-0003's original mistake: recomputing static geometry on every request,
and (worse here) requires real GIS operations (buffering, neighbor
differencing) per request instead of a lookup.

A second problem is specific to debris and has no buildings-layer
equivalent: a plain omnidirectional buffer of a footprint is wrong in
dense urban fabric. Spanish town centres (Lorca's old town included) are
mostly row buildings sharing party walls; buffering on all sides paints
debris on top of the neighboring building, not into open space.

## Decision

**Precompute a "debris envelope" per building, offline, as a new exposure
pipeline step**, output as `debris.parquet` (geometry) and tiled to
`debris.pmtiles` alongside `buildings.pmtiles` — same tippecanoe path as
`tile.py`, same `building_id` join key as everywhere else in the pipeline.

For each building:

1. Classify each footprint edge as **party wall** (shared with, or within a
   small tolerance of, a neighboring footprint) or **exterior** (not
   shared) — a purely geometric operation on `buildings.parquet` alone, no
   new external data source.
2. Buffer only the exterior edges outward, at the four MERISUR distances
   (1/2/3/4 m), producing four nested rings per building.
3. Difference each ring against nearby buildings' own footprints, so a
   ring can never paint over a neighbor even where edge classification is
   imperfect (e.g. an L-shaped or offset party wall).
4. Store all four rings per building, tagged `ring: 1..4`, rather than one
   fixed-size shape — this is what lets the frontend pick the active ring
   per scenario without a second geometry fetch.

**Scenarios compute nothing new.** `engine.py`/`damage.py` already produce
`damage_state`/`damage_state_code` per building (`response.py`). The
frontend joins that existing thin result onto `debris.pmtiles` via
`setFeatureState`, the same pattern `DamageMap.tsx` already uses for
building color (ADR-0003), and shows rings `<= damage_state_code`. No new
scenario-function endpoint, no new response field.

**Prototype at Lorca scale before committing nationally.** `buildings.pmtiles`
nationwide is 1.6GB for 12.4M buildings (`data/exposure`); four ring
features per building is more geometry than a bare footprint, plausibly
3-6x that once tiled — unmeasured. Build and visually validate the Lorca
debris layer (27,884 buildings) first, same rollout order the project
already used for buildings (Lorca → Murcia+Andalucía → Spain, ADR-0005),
before deciding whether/how to tile nationally.

**Street/open-space clipping is an explicit, documented follow-up, not
part of this decision.** Restricting rings to exterior (non-party-wall)
edges gets most of the way to "debris lands on public space, not inside a
neighbor's building" without any new data source, but doesn't distinguish
a street from a private rear courtyard behind the exterior wall. Clipping
against real street/open-space geometry (most likely OSM road polygons,
buffered to a typical width — no Spain-wide public street-polygon dataset
is known to exist, unlike Catastro for footprints) is left for a second
pass once the party-wall-only version ships and its false-positive rate
against real Lorca street layout can be judged visually.

## Alternatives considered

- **Compute debris rings at scenario request time**: rejected for the same
  reason ADR-0003 rejected re-tiling buildings per scenario — throws away
  precomputation for geometry that never changes, and adds real
  buffering/differencing GIS work to the request path on top of the GMPE/
  fragility evaluation already there.
- **Omnidirectional buffer, no party-wall/neighbor handling**: simplest to
  implement, but visibly wrong in Lorca's old town and most historic
  Spanish centres — debris polygons would routinely overlap neighboring
  buildings, undermining trust in the layer for exactly the dense,
  vulnerable urban fabric MERISUR (and Lorca 2011) cared most about.
- **Clip against OSM streets from the start**: more correct, but adds a
  new external data dependency and a second geometry source to keep in
  sync with Catastro before the simpler party-wall-only version has even
  been visually checked against real data. Deferred, not rejected — see
  Decision above.
- **One geometry per building (just the Complete/4m ring) with the active
  distance computed client-side via a smaller in-browser buffer**: would
  avoid shipping 4 nested rings per building, but pushes real geometry
  computation (buffering) into the frontend, in JavaScript, per rendered
  building — against the project's static-files/precompute-once principle
  and harder to keep visually consistent with the offline-computed
  neighbor-differenced shape.

## Consequences

- New pipeline module (`pipelines/exposure`, e.g. `debris.py`) and a new
  `debris.parquet`/`debris.pmtiles` pair of artifacts, following the same
  partitioning/resumability conventions as `buildings.parquet`
  (ADR-0005) once run at region/national scale.
- `building_id` remains the single join key across buildings, scenario
  results, and now debris rings — no new key introduced.
- The debris table above (1/2/3/4 m) is a known-coarse placeholder;
  swapping in the real Gaspar-Escribano et al. (2023) volumes later (if
  UPM shares them — `docs/questions-for-upm.md` §3) only changes the ring
  distances used in step 2, not this architecture.
- Frontend gains a debris layer + toggle (matching MERISUR's own separate
  "Load result" / "Load debris" actions, `docs/merisur.md` §5), wired to
  the damage result the scenario call already returns — no new API call
  per scenario run.
- **Measured against real Lorca data** (27,884 buildings, in the
  since-retired Lorca-only dataset):
  computing all rings for the whole municipality takes ~21s (`debris.py`'s
  per-building neighbor-query + buffer/difference loop — fine offline, not
  something to run per request). 27,877 of 27,884 buildings get at least
  one ring (7 are fully party-walled on every side, e.g. genuine mid-block
  interior units); 111,508 ring rows total (~4 rings/building, as
  expected).
- **Tile size needed a second pass.** The first end-to-end run tiled to
  77MB (vs. `buildings.pmtiles`' 4.1MB for the same town — 18.8x, well
  past this ADR's original 3-6x estimate). Root cause: shapely's default
  buffer resolution (8 segments/quarter-circle) compounded across every
  ring's buffer + neighbor-difference boolean op, plus stray
  `GeometryCollection` slivers at floating-point-precision boundaries
  between rings. Fixed in `debris.py` (`BUFFER_QUAD_SEGS = 3`,
  `SIMPLIFY_TOLERANCE_M = 0.15`, `_polygonal_only` to drop non-polygon
  slivers before storing) — cut average ring-polygon vertex count from ~57
  to ~24 with no visible loss at map scale, and `debris.pmtiles` from 77MB
  to **23MB** (5.6x `buildings.pmtiles`, back inside the original
  estimate). Extrapolating that ratio nationally (`buildings.pmtiles` is
  1.6GB for 12.4M buildings) suggested ~9GB for a national
  `debris.pmtiles` — an extrapolation later confirmed almost exactly
  right (9.26GB actual, see below), though the path to get there was not
  a straightforward "run the same thing at 460x scale."

## National-scale tiling: what actually happened

Going from Lorca (27,884 buildings) to all of Spain (12.9M buildings,
51.5M ring rows) surfaced four distinct, unrelated failure modes, each
diagnosed and fixed in turn rather than worked around. Recorded here in
full because each one looks like a different problem until you've seen it
once — worth recognizing on sight next time.

1. **Invalid geometry, two different ways.** `shapely`'s `simplify()` can
   produce a self-intersecting (invalid) polygon even with its default
   `preserve_topology=True`; separately, `make_valid()`'s own repair of a
   broken input can produce a `GeometryCollection` whose `.boundary` is
   silently `None` rather than raising. Both surfaced as confusing
   `AttributeError`s several calls downstream, not at the point of the
   actual defect, and both were municipality-killing before being caught
   (one bad building's geometry took its whole municipality's debris
   output down with it). Fixed in `debris.py`: `_polygonal_only()` runs
   after both `make_valid()` and `simplify()`, and the per-building loop
   catches `(GEOSException, AttributeError, ValueError)` so one
   unrepairable building is skipped, not fatal. Repairing the ~15M (of
   51.5M, ~29%) already-computed ring geometries that predated this fix
   was a one-off in-place pass over the existing `debris.parquet` parts —
   not a recompute (see "Do not redo this from scratch" below).
2. **Disk space, disguised as something else twice over.** A single
   tippecanoe invocation over the full national GeoJSON conversion filled
   the temp filesystem outright (`OSError: No space left on device`) —
   but *before* that was diagnosed, the same underlying cause (the
   filesystem was already nearly full from a 101GB leftover temp
   directory an earlier *killed* run never got to clean up, since
   `tempfile.TemporaryDirectory`'s cleanup only runs if the process exits
   normally) surfaced as `pyogrio.errors.FeatureError: Cannot write
   feature` — GDAL wraps a plain out-of-disk write failure in the same
   generic error it uses for a genuinely bad geometry, at a *different*
   feature index on every retry. Don't trust that error message's framing
   before checking `df -h` and for stale temp directories from prior
   killed runs.
3. **pyogrio/GDAL's plain `GeoJSON` writer, replaced with `GeoJSONSeq`.**
   Once disk space and geometry were both ruled out, switching the
   per-part writer from GDAL's `GeoJSON` driver (one buffered
   `FeatureCollection` document) to `GeoJSONSeq` (RFC 8142, streamed
   feature-by-feature — both GDAL and tippecanoe read it without
   buffering the whole thing) fixed the remaining write fragility *and*
   measurably helped: ~20% faster per-file conversion and roughly half
   the gzip-compressed size for the same data, on top of parallelizing
   the per-municipality conversion step itself (`ThreadPoolExecutor`,
   same reasoning as `compute_debris_region`'s own pool — GDAL/gzip calls
   release the GIL).
4. **tippecanoe's 200,000-features-per-tile limit.** Dense city centres
   (Barcelona province was the first to hit it) can pack enough
   overlapping debris rings into one map tile to exceed tippecanoe's
   default hard cap (`"tile N/N/N has 200001 (estimated ...) features,
   >200000"`) — a real density difference from `buildings.pmtiles`,
   which has never hit this, since footprints don't overlap the way
   rings from adjacent buildings do. Fixed by passing
   `--drop-densest-as-needed` (`tile_geojson_files`'s new `extra_args`
   param) — a real, visible trade-off (the single most crowded tiles in
   the densest city blocks won't render every overlapping ring at every
   zoom) accepted in exchange for the tile existing at all.
5. **Memory pressure, and the batching + resumability response.** Even
   after fixes 1-4, a single tippecanoe process over all 51.5M national
   ring features (~8x `buildings.pmtiles`' own vertex count for the same
   country, measured directly on Madrid's municipality: 25.7M debris
   vertices vs. 3.1M building vertices) repeatedly exhausted available
   memory on a 17GB machine already under load from other applications —
   once crashing outright after ~3 hours of real work with nothing to
   resume from. The fix was architectural, not a bigger flag:
   `tile_debris_region_by_province` (`region.py`) tiles one province at a
   time (tippecanoe's own memory footprint scales with what it's
   currently processing, and Spain's largest province is a small fraction
   of the national total) and merges all ~52 results with tippecanoe's
   own `tile-join` at the end — cheap, since it only repacks already-built
   tiles, no geometry work. **Resumable at the province level**: each
   province's PMTiles is written to a temp path first and only `move`d
   into its real `<batches_dir>/<code>.debris.pmtiles` location after
   tippecanoe exits successfully, so a province whose tiling was
   interrupted (crash, OOM kill, Ctrl-C) never leaves a truncated file at
   the path the resumability check looks for — confirmed directly: this
   run was killed by the OS for low memory four separate times across the
   52 provinces, and every single time, resuming picked up exactly at the
   next unfinished province with zero lost work from already-completed
   ones. Total: ~52 province tilings + one final merge, no single step
   requiring more memory than one province's own data.

## Do not redo this from scratch

`compute_debris_region`'s 51.5M ring rows (7,764 municipality parts) and
`tile_debris_region_by_province`'s resulting `debris.pmtiles` (9.26GB)
represent real, expensive, already-paid compute — the ring computation
alone is real per-building GIS work (not just I/O), and the full tiling
pass took multiple sessions and several outright crashes to get through
end to end (see above). **Both are resumable, idempotent artifacts, not
scratch output** — treat them the way `buildings.parquet`/
`buildings.pmtiles` are already treated (ADR-0005): safe to leave in place
indefinitely, unsafe to casually delete and regenerate.

If the debris model needs to change later (real Gaspar-Escribano et al.
volumes replacing the 1/2/3/4m table if UPM shares them, street/open-space
clipping, a different party-wall tolerance, anything else) the right move
is almost always to **extend, not restart**:

- A change to per-building geometry (e.g. street clipping) can run as an
  in-place repair pass over the existing `debris.parquet` parts, the same
  pattern already used once for the invalid-geometry fix above — read a
  part, transform its `geometry` column, write it back, re-tile only the
  provinces that actually changed.
- A change that adds information (e.g. a real debris *volume* alongside
  the existing ring geometry) fits as a new column on the existing rows,
  not a new computation of the geometry itself.
- Only a change to the *ring distances themselves* (the 1/2/3/4m table)
  would need new geometry — and even then, per-municipality resumability
  means a full recompute only costs whatever wasn't already done, the
  same as any other resumed run.
- Re-tiling (`tile_debris_region_by_province`) from already-computed
  `debris.parquet` parts is comparatively cheap (hours, not the
  multi-session effort of the geometry computation itself) and safe to
  re-run in place — deleting `debris_batches/` to force a full re-tile
  is reasonable; deleting `parts/*.debris.parquet` to force a full
  re-*compute* is not, without a specific reason tied to a real geometry
  change.
