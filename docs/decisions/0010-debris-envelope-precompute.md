# ADR-0010: Precompute debris envelopes per building; scenarios only pick a ring

Status: accepted (steps 1-2 of `milestone-1-plan.md` §10 implemented and
run end-to-end against real Lorca data; see Consequences for the measured
result)

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
nationwide is 1.6GB for 12.4M buildings (`data/exposure_spain`); four ring
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
- **Measured against real Lorca data** (27,884 buildings, `data/exposure`):
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
  1.6GB for 12.4M buildings) suggests **~9GB** for a national
  `debris.pmtiles` — plausible to host, but still an extrapolation, not a
  national-scale measurement; region/national tiling (task 2's remaining
  scope) should re-measure directly rather than trust this scaling
  assumption.
