# ADR-0003: Pre-tile building geometry once; scenarios join damage data on top

Status: accepted

## Context

Building footprint geometry doesn't change between scenario runs — only the
per-building damage result does. Re-tiling geometry on every scenario
calculation would be wasteful and slow, and would duplicate the same polygons
across every scenario's output. We want to precompute as much as possible
without losing per-building resolution (project instruction), and we're
already committed to static files over S3 as the storage model.

## Decision

- **Geometry, tiled once, offline**: the exposure pipeline runs building
  footprints (with a stable `building_id` and static attributes — taxonomy,
  floors, etc.) through **tippecanoe** to produce vector tiles, published as
  **PMTiles** on S3. This happens as part of the exposure pipeline (§4 of
  `milestone-1-plan.md`), not per scenario.
- **Scenario results are thin**: the scenario function's output is *not*
  another set of tiles — it's a small keyed dataset, `building_id → {damage
  state, probabilities}`, as GeoParquet or JSON, small enough to fetch
  directly (no tiling needed at Lorca scale; revisit if a national-scale
  result set gets too large for a flat fetch).
- **Join happens client-side**: the frontend loads the static building
  PMTiles as the base layer, fetches the scenario's thin result set, and uses
  MapLibre's `setFeatureState`/`building_id`-keyed styling to color buildings
  by damage state at render time. No server-side re-tiling per scenario, no
  geometry duplication.

## Alternatives considered

- **Re-tile per scenario** (bake damage color into freshly generated tiles
  each run): simplest mental model, but throws away precomputation, is
  slower per scenario, and duplicates static geometry across every scenario
  a user has ever run.
- **Server-side join returning full GeoJSON**: avoids the client-side
  `setFeatureState` wiring, but re-ships geometry on every scenario request
  and doesn't scale past a small city.

## Consequences

- Requires `building_id` to be stable and consistent between the tiled base
  layer and every scenario result — this becomes a hard contract enforced in
  the exposure pipeline.
- The frontend needs the MapLibre feature-state join logic built once,
  reusable for damage today and for any future hazard's per-building result
  (flood, heatwave) — a good early test of the "common exposure layer, plug-in
  hazard engines" architecture goal from the project brief.
- At national scale (milestone 2), the thin per-building result set may
  itself get large enough to need partitioning/tiling — flagged as a
  follow-up, not a milestone-1 concern. **Confirmed sooner than expected:**
  the Murcia + Andalucía expansion (ADR-0005) measured a 634MB JSON payload
  for a single 2.5M-building scenario response — "thin" (no geometry) still
  isn't thin enough once the building count reaches millions. See
  `docs/validation-region-expansion.md` §4 for the measurement and the
  next-step options (returning only non-"None" buildings is the obvious
  cheap one).
