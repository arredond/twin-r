# Milestone 1: MVP plan — a modern MERISUR clone for Lorca

Status: proposed. This is the implementation plan for milestone 1 from the
project brief: *"Develop a modern, performant clone of the MERISUR web-based
tool."* Background research is in [`merisur.md`](./merisur.md).

## 1. Scope decisions

These were confirmed with the project owner before planning the architecture:

- **Pilot area: Lorca.** Same city MERISUR targeted. No original MERISUR
  dataset is public (see `merisur.md` §4.4), so exposure is rebuilt from
  Catastro regardless of which city we pick — Lorca gives us a real 2011
  Mw 5.2 event and published damage patterns to sanity-check against, and QAFI
  has real nearby faults to exercise "automatic" rupture selection.
- **Compute model: serverless backend**, runnable locally for development. A
  function computes the GMPE + damage calculation on demand and writes a
  result artifact (GeoParquet, tiled for the map) to object storage; the
  frontend fetches that. No RDBMS. The same function must run as a local
  process (not only "cloud-shaped code we hope works") so iteration doesn't
  require a deploy.
- **Damage only, no debris**, per the brief's suggested MVP simplification.
  Debris is now scoped as milestone-1.x — see §10 below and
  [ADR-0010](./decisions/0010-debris-envelope-precompute.md) — using the
  2018-tool façade-buffer heuristic (§4.8 of `merisur.md`) as the MVP
  formula, pending UPM access to the 2023 debris-model paper
  (`docs/questions-for-upm.md` §3).
- ~~**One damage calculation only** — skip MERISUR's three probability
  levels (median / median+1σ / median+1σ+P85) for now; add them once the
  base chain works. Compute the median-ground-motion, modal-damage case
  only.~~ — superseded:
  [ADR-0011](./decisions/0011-probability-level-selector.md) implements
  all three tiers end-to-end (backend + frontend selector), picked back up
  once `docs/validation-lorca-2011.md` §10.5 found the "base chain works"
  bar had already been cleared and the median+1σ tier closed a real,
  concretely quantified gap.

## 2. Deliberate deviations from MERISUR's method

Documented here so nobody mistakes these for oversights:

| MERISUR | twin-r MVP | Why |
|---|---|---|
| Lorca 5-class soil microzonation (Navarro et al. 2014) | No site amplification, or a flat Vs30-derived factor if trivially available | Lorca's microzonation isn't republished/accessible to us and isn't portable to the rest of Spain anyway (milestone 2 goal). Starting without it is honest about the gap rather than faking precision. |
| IDCM/FEMA 440 capacity-curve + performance-point method | Fragility functions applied directly to ground-motion intensity (standard OpenQuake scenario-damage approach) | We don't have MERISUR's Lorca capacity curves. [Martins & Silva (2020)](https://github.com/lmartins88/global_fragility_vulnerability) publish an open, GEM-taxonomy-based global fragility/vulnerability function set usable directly with OpenQuake — no pushover analysis or capacity curves needed on our side. |
| Field-surveyed vulnerability class per building | Heuristic mapping from Catastro attributes (construction year, stories, use) → GEM taxonomy string | No field work, per project constraints. This is the least-validated part of the MVP and should be flagged as such in the UI. |
| Own building/vulnerability shapefile | Catastro INSPIRE download, processed into GeoParquet | Nothing else is public for Lorca (see `merisur.md` §4.4). |

## 3. Architecture

```
                     ┌─────────────────────────┐
                     │   Data pipeline (ETL)    │   offline, run once / on refresh
                     │                          │
                     │  Catastro INSPIRE ATOM   │──► buildings.parquet (Lorca)
                     │  QAFI (IGME)             │──► faults.parquet
                     │  GEM taxonomy mapping    │──► exposure.parquet (buildings +
                     │  Martins & Silva 2020    │    taxonomy + fragility fn ref)
                     │  fragility functions     │──► fragility.parquet
                     │  tippecanoe              │──► buildings.pmtiles (geometry,
                     │                          │    keyed by stable building_id)
                     └────────────┬─────────────┘
                                  │  (static files → S3)
                                  ▼
                     ┌─────────────────────────┐
                     │   Object storage (S3)    │
                     │   exposure/fragility as  │
                     │   GeoParquet; buildings  │
                     │   as PMTiles             │
                     └────────────┬─────────────┘
                                  │  read by
                                  ▼
┌───────────────┐       ┌─────────────────────────┐
│  Web frontend  │◄─────►│  Scenario function       │
│  (React)       │ HTTP  │  (Lambda locally &        │
│  MapLibre GL:  │       │   in the cloud)            │
│  buildings.pm- │       │                            │
│  tiles as base │       │  DuckDB (spatial ext.)     │
│  layer + fault/│       │  loads exposure parquet    │
│  manual rupture│       │  openquake.hazardlib:       │
│  form → thin   │       │   Akkar et al. 2014 GMPE   │
│  result joined │       │  fragility function eval   │
│  via setFeature│       │  → THIN result (building_id │
│  State         │       │    → damage state/probs)   │
└───────────────┘       │    as GeoParquet/JSON → S3  │
                          └─────────────────────────┘
```

Key points:

- **Everything at rest is a static file** (GeoParquet, partitioned by
  municipality/H3 cell, plus PMTiles for building geometry). No
  Postgres/PostGIS. DuckDB (with `spatial` and `httpfs` extensions) is the
  query engine, run inside the scenario function, reading parquet straight
  from S3 (or local disk in dev).
- **Geometry is tiled once, offline; scenarios never re-tile.** Building
  footprints go through `tippecanoe` → PMTiles as part of the exposure
  pipeline. Every scenario run only ever produces a small `building_id →
  damage` result set; the frontend joins it onto the static PMTiles layer at
  render time via MapLibre `setFeatureState`. See
  [ADR-0003](./decisions/0003-precomputed-building-tiles.md).
- **The scenario function is the only "backend."** It's a plain Python
  package with a handler entrypoint; locally it's invoked via a small CLI/dev
  server (e.g. `uvicorn` wrapping the same handler, or AWS SAM/`lambda-local`
  — pick whichever adds the least ceremony), in the cloud it's a Lambda behind
  a minimal HTTP API (API Gateway or Lambda Function URL), deployed via AWS
  CDK. One codebase, two invocation shells. See
  [ADR-0001](./decisions/0001-compute-and-iac.md).
- **OpenQuake as a library, not a platform.** We depend on
  `openquake.hazardlib` (GMPEs) and reuse its fragility-function data model,
  but we do **not** stand up the full OpenQuake Engine (job files, workers,
  its own DB). That matches "flexible tool, varying complexity" and keeps the
  static-files-first principle intact.
- **Frontend**: React + MapLibre GL. See
  [ADR-0002](./decisions/0002-frontend-framework.md). Rupture input is a
  small form (fault picker bound to `faults.parquet`, or manual
  Mw/strike/dip/depth/rake fields), matching MERISUR's automatic/manual
  split.

## 4. Data pipeline detail

1. **Buildings (Catastro)**: use the INSPIRE ATOM download service for the
   Lorca municipality (`catastro.minhap.gob.es` ATOM feed, or the `CatastRo` R
   package / `Spanish_Inspire_Catastral_Downloader` QGIS plugin as reference
   implementations of the download logic — we'll write our own small Python
   fetcher rather than depend on either). Extract: footprint geometry, number
   of floors (from `numberOfFloorsAboveGround` / building attributes), year of
   construction, use (residential/other). Output `buildings.parquet`.
2. **Taxonomy mapping**: a documented, versioned heuristic —
   `construction_year, floors, use → GEM taxonomy string` — living in code
   (not tribal knowledge) so it can be audited and improved. Flag every
   building's taxonomy as inferred, never as observed.
3. **Fragility functions**: pull the relevant GEM-taxonomy entries from
   Martins & Silva (2020)'s published set, store as `fragility.parquet`
   (taxonomy → damage-state thresholds vs. intensity measure).
4. **Faults**: QAFI download for the Murcia/Lorca region, `faults.parquet`
   with geometry + whatever Mmax/recurrence fields QAFI v4 publishes (accept
   that some faults will lack Mmax — see `merisur.md` §7.2 — and compute a
   fallback Mmax from fault length using a documented empirical relation
   rather than silently omitting the fault).
5. All parquet outputs are geospatially partitioned (by municipality for
   now; H3 once we go national in milestone 2) and pushed to S3. The
   buildings layer is additionally run through `tippecanoe` to produce
   `buildings.pmtiles`, keyed by the same stable `building_id` used
   everywhere else in the pipeline (see [ADR-0003](./decisions/0003-precomputed-building-tiles.md)).

## 5. Scenario function detail

Input: either `{fault_id}` (automatic) or
`{lat, lon, mw, strike, dip, depth, rake}` (manual).

1. Build the rupture (reuse `openquake.hazardlib` rupture objects).
2. Load `buildings.parquet` + `exposure.parquet` for Lorca via DuckDB.
3. Compute per-building distance to rupture (DuckDB spatial or
   `hazardlib` distance calculators — whichever is simpler to keep correct).
4. Evaluate Akkar et al. (2014) GMPE per building → intensity measure(s).
5. Join each building's taxonomy to its fragility function, evaluate damage-
   state probabilities at that intensity, take the modal state.
6. Write the **thin** result — `building_id, damage_state, probabilities`
   only, no geometry — as GeoParquet or JSON; upload to S3; return the URL
   (or the payload directly, if small enough). The frontend joins this onto
   the already-published `buildings.pmtiles` layer client-side (ADR-0003) —
   the scenario function never touches tiling.

## 6. Repo structure (proposed)

```
twin-r/
  apps/
    web/              # MapLibre frontend
  services/
    scenario/          # Python scenario function (Lambda handler + local dev entrypoint)
  pipelines/
    exposure/          # Catastro fetch + taxonomy mapping
    faults/             # QAFI fetch
    fragility/           # Martins & Silva import
  data/                 # gitignored local cache of downloaded/generated parquet
  docs/
    merisur.md
    milestone-1-plan.md
    decisions/          # one file per significant technical decision (ADR-style)
```

We'll switch `main.py`/`pyproject.toml` at the repo root into this structure
(likely a `uv` workspace, given `.python-version`/`pyproject.toml` already
present) rather than keeping a single top-level script.

## 7. Milestone-1 task breakdown

1. **Repo scaffolding** ✅: workspace layout above, `docs/decisions/` ADR
   template, local dev instructions (README.md). CI skeleton still pending.
2. **Faults pipeline** ✅: QAFI → `faults.parquet`, nationwide (IGME's QAFI
   REST layer is small -- 201 faults -- so we don't pre-filter to a region).
3. **Exposure pipeline** ✅: Catastro → `buildings.parquet` → taxonomy mapping
   → `exposure.parquet` → `buildings.pmtiles`, for Lorca municipality.
   Verified end-to-end against live Catastro data: 27,884 buildings.
4. **Fragility import** ✅: Martins & Silva (2020) subset (`CR_LDUAL-DUL` and
   `MR_LWAL-DUL`, H1–H12, plus `MUR-STRUB_LWAL-DNO` vernacular masonry added
   per ADR-0012, H1–H5) → `fragility.parquet`. Verified against the live
   GitHub repo: 4,400 rows across 22 taxonomy/height classes.
5. **Scenario function v0** ✅: manual-rupture input, Akkar 2014 GMPE (via
   `openquake.hazardlib`, flat reference-rock Vs30, point-source Rjb
   approximation), fragility evaluation, thin GeoParquet/JSON output. Runs
   both as a local FastAPI dev server (`scenario.local:app`) and a CLI
   (`python -m scenario`); verified end-to-end against real Lorca data.
6. **Frontend v0** ✅: MapLibre basemap + Lorca buildings PMTiles layer,
   manual-rupture form, calls the scenario function, renders the damage
   layer via `setFeatureState` with a legend matching MERISUR's
   None/Slight/Moderate/Extensive/Complete states. Verified in-browser.
7. **Automatic mode** ✅: `GET /faults` (nearest QAFI faults to Lorca, via
   `ST_ClosestPoint`) + `POST /scenarios/fault`, wired into the frontend as
   an Automatic/Manual tab toggle. Verified: the real 2011 causative fault
   (Alhama de Murcia) comes back as the nearest fault to Lorca, and running
   its Mmax scenario returns a plausible damage distribution end-to-end.
8. **Local ↔ cloud parity** 🟡: scenario function packaged as a Lambda
   container image (Dockerfile), CDK stack defined and `cdk synth`-verified
   (`infra/`) -- not yet deployed against a real AWS account, and the data
   pipelines don't yet push their outputs to S3 automatically. Deferred by
   request.
9. **Sanity-check pass** ✅: see [`validation-lorca-2011.md`](./validation-lorca-2011.md).
   The pipeline is mechanically correct end-to-end (fault lookup, ground
   motion, fragility evaluation all check out against the real event) but
   the generic global fragility functions we vendored understate Lorca's
   actual pre-code masonry vulnerability — a concrete, quantified follow-up
   is documented there, not yet implemented.

Items 2–4 can run in parallel; 5 depends on 2–4; 6 depends on 5; 7 depends on
2; 8 can start once 5 works locally.

### Implementation gotchas worth recording

Found while building task 6, non-obvious enough to bite whoever touches the
map code next:

- **Vite pre-bundles maplibre-gl's worker incorrectly.** Requests for
  `maplibre-gl-worker.mjs` hang indefinitely with no console error, leaving
  the map canvas blank. Fix: `optimizeDeps: { exclude: ['maplibre-gl'] }` in
  `apps/web/vite.config.ts` (already applied).
- **`Map#setFeatureState` on a vector source requires `sourceLayer`.**
  Omitting it throws (caught nowhere by default), so every building
  silently keeps its fallback color instead of erroring visibly. Matters
  directly for ADR-0003's feature-state join pattern -- see
  `apps/web/src/components/DamageMap.tsx`.

## 8. Explicitly out of scope for milestone 1

- ~~Probability-level selector (median+1σ, P85 damage).~~ — superseded:
  see [ADR-0011](./decisions/0011-probability-level-selector.md).
- ~~Any area beyond Lorca municipality~~ — superseded: milestone 2's
  region-by-region expansion started with Murcia + Andalucía, see
  [ADR-0005](./decisions/0005-region-scale-crawling.md). The rest of this
  list still holds — expanding coverage didn't pull debris or site
  amplification back into scope.
- Site amplification beyond (at most) a trivial Vs30-derived factor.
- Scenario save/share, auth, multi-user features.
- Any engine other than Akkar et al. 2014 — the "pluggable engine" goal
  (project brief) is a milestone-2+ concern once there's a second engine to
  prove the abstraction against.

## 9. Open items to resolve before/while building

Resolved since the first draft of this plan: compute/IaC
([ADR-0001](./decisions/0001-compute-and-iac.md)), frontend framework
([ADR-0002](./decisions/0002-frontend-framework.md)), and tiling strategy
([ADR-0003](./decisions/0003-precomputed-building-tiles.md)). Still open:

- Exact Catastro attribute availability for `numberOfFloorsAboveGround` and
  construction year at INSPIRE-download granularity — needs a hands-on check
  against a real Lorca ATOM response before the taxonomy heuristic is
  finalized.
- Whether to fetch Martins & Silva's fragility set as a vendored snapshot or
  a pipeline step against their GitHub repo (license/attribution check
  needed either way).

## 10. Milestone-1.x: debris

Architecture decided in [ADR-0010](./decisions/0010-debris-envelope-precompute.md):
debris geometry is precomputed once per building (like `buildings.pmtiles`,
ADR-0003), not recomputed per scenario. A scenario run already produces
`damage_state`/`damage_state_code` per building; the frontend picks the
active debris ring off that existing result, so no scenario-function or API
change is needed — only new pipeline output and a new frontend layer.

1. **Debris pipeline step** ✅ (`pipelines/exposure/debris.py`): party-wall
   vs. exterior edge classification per building footprint, four nested
   1/2/3/4 m rings buffered from exterior edges only, differenced against
   neighboring footprints. Output `debris.parquet`, keyed by `building_id`.
   Wired into `pipeline.run()` as optional `debris_output`/
   `debris_tiles_output` args and `python -m exposure --debris/--debris-tiles`.
2. **Debris tiling** ✅ **nationwide**: tippecanoe → `debris.pmtiles`,
   scaled up from Lorca (111,508 ring rows, 23MB) through region scale to
   all of Spain — 51,483,434 ring rows, 12,881,817 buildings, final
   `debris.pmtiles` **9.26GB** (848,974 tiles, max zoom 16), deployed to
   `apps/web/public/data/debris.pmtiles`. Getting from region to national
   scale surfaced and fixed four distinct failure modes (invalid geometry
   from `simplify()`/`make_valid()`, disk exhaustion masquerading as a
   GDAL write error, tippecanoe's 200,000-features-per-tile limit in dense
   cities, and repeated OOM kills from tiling all 51.5M features in one
   process) — full writeup in ADR-0010's "National-scale tiling: what
   actually happened". Final approach: `tile_debris_region_by_province`
   tiles one province at a time (resumable — survived four separate OS-
   triggered kills across the run with zero lost completed work) and
   merges with `tile-join`. **This computed data is a durable artifact —
   see ADR-0010's "Do not redo this from scratch" before deleting
   `parts/*.debris.parquet` or `debris_batches/` to regenerate it.**
3. **Frontend** ✅: a debris layer, styled via `setFeatureState` off the
   `damage_state_code` a scenario call already returns, joined by
   `building_id` — same pattern `DamageMap.tsx` already uses for building
   color. Diverged from MERISUR's own separate "Load result" / "Load
   debris" toggle during national-scale UI review: always shown after a
   scenario run instead (no toggle), clickable with a popup, and in the
   legend — a deliberate simplification, not a MERISUR-parity gap.
4. **Follow-up, not blocking**: street/open-space clipping (party-wall
   exclusion alone doesn't distinguish a street from a private rear
   courtyard) and, if UPM shares it, the real 2023 debris-volume model in
   place of the 1/2/3/4 m table — see `docs/questions-for-upm.md` §3.

Sequencing: 1 depends on the exposure pipeline's existing
`buildings.parquet` output only (task 3 in §7, already done for Lorca and
beyond); 2 depends on 1; 3 depends on 2 and can otherwise proceed
independently of the damage-side frontend work.
