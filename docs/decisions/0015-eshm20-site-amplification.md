# ADR-0015: National site amplification via ESRM20's Vs30 grid

Status: accepted

## Context

`docs/validation-lorca-2011.md` traced why re-running the real 2011 Lorca
earthquake (and, separately, an automatic-mode max-magnitude "high
probability" scenario on the causative fault) through `twin-r` predicts
essentially no damage, while MERISUR's own tool and the real event show
widespread Slight/Moderate damage in Lorca's old town. Several
contributing factors were found and mostly fixed (ADR-0007's finite
rupture surface, ADR-0011's probability-level selector, ADR-0012's
vernacular-masonry taxonomy + IM-type dispatch fix). One remains entirely
unaddressed: `ground_motion.py` evaluates **every building in Spain**
against a flat `DEFAULT_VS30 = 800` (EC8 "rock" reference), explicitly
flagged in that module's own comment as "every building currently gets
identical site amplification (none)."

This isn't cosmetic. Lorca's historic centre sits on a sedimentary basin
with documented soft-soil amplification (Navarro et al. 2014's 5-class
microzonation, `docs/merisur.md` §4.3) — precisely the area that actually
took damage in 2011. `twin-r`'s flat-rock assumption suppresses ground
motion exactly where MERISUR's Lorca-specific microzonation amplifies it.

MERISUR's own microzonation is Lorca-only, not public, and explicitly not
portable (`docs/questions-for-upm.md` #2, still open) — `twin-r`'s scope is
the whole of Spain, so reusing it even if UPM shares it would only fix
Lorca, not the underlying gap.

The GMPE already in use, **Akkar, Sandıkkaya & Bommer (2014)**
(`services/scenario/ground_motion.py`, chosen specifically to match
MERISUR's own chain — `docs/merisur.md` §4.2), has a **built-in Vs30 site
term**: `_intensity_at_distances` already builds a `ctx.vs30` array and
passes it straight into `AkkarEtAlRjb2014.compute`. Closing this gap does
not require a new amplification model layered on top of the GMPE — it
requires a real per-building Vs30 value instead of the flat default. This
reframes the problem as data acquisition + lookup, not new physics.

## Decision

Use the **ESRM20 (European Seismic Risk Model 2020)** national site model
as the Vs30 source, specifically
`Vs30_30arcsec/Site_model_30arcsec_Spain.csv` from
<https://gitlab.seismo.ethz.ch/efehr/esrm20> (CC BY 4.0, published by
EFEHR / SED-ETH Zürich). Verified directly:

- **Format**: a flat CSV, not a raster with a fixed grid header — one row
  per point: `lon,lat,slope,vs30,geology,xvf,region,vs30measured`. Not
  LFS-tracked (17.8MB, plain git blob) — fetchable directly, no LFS
  tooling needed.
- **Coverage/resolution**: 228,429 points covering Spain (mainland +
  islands) at ~30 arc-second spacing (~800m at Spain's latitudes) — an
  irregular point cloud built from slope + geology proxies
  (Wald & Allen 2007-style topographic slope, refined by geological era),
  not measured Vs30 everywhere (`vs30measured` flags the minority that are).
- **Provenance/fit**: this is the same site model OpenQuake-based European
  seismic risk calculations use as GMPE input (the ESRM20 repository ships
  it as an OpenQuake NRML site-model file per country, alongside the
  exposure/vulnerability/hazard inputs for the same 44-country model) — so
  adopting it keeps `twin-r` consistent with how the rest of Europe already
  runs Akkar-family GMPEs with site effects, rather than inventing a
  second, bespoke amplification scheme.
- **Verified directly against Lorca**: nearest-neighbor lookup at Lorca's
  town centre (37.6714, -1.6997) returns **Vs30 ≈ 383 m/s** (EC8 class D,
  soft soil) vs. the flat `DEFAULT_VS30 = 800` (class A/B, rock) `twin-r`
  uses today. Feeding both into the existing Akkar et al. (2014) GMPE for
  the real 2011 rupture (Mw 5.2, strike 240°, dip 54°, rake 44°, Ztor 2km):
  **SA(0.3s) goes from 0.191g (flat rock) to 0.297g (real Vs30) — a ~55%
  increase**, entirely from the GMPE's own existing site term, no other
  change. This is the right direction and roughly the right order of
  magnitude to matter for the fragility curves already vendored
  (`docs/validation-lorca-2011.md` §3/§4 found ~9-11% P(≥Slight) at
  SA(0.3s)=0.2g for the old generic class, vs. much higher at the higher
  intensities/vernacular classes now in use).

**Per-building Vs30 is precomputed and stored, not looked up at request
time.** Following the project's established pattern (ADR-0006's
centroid/bbox columns, ADR-0014's `municipality_code` column):
`pipelines/exposure`'s buildings already have precomputed
`centroid_lon`/`centroid_lat`. A new `vs30.py` module
(`pipelines/exposure/src/exposure/vs30.py`) does a KD-tree nearest-neighbor
lookup from each building's existing centroid into the ESRM20 grid and
writes the result as a `vs30` column on `buildings.parquet`.
`pipeline.build_exposure` calls it directly, so **every future crawl gets
`vs30` natively at ingest time**, the same as `municipality_code`
(ADR-0014) — backed by an `lru_cache(maxsize=1)` singleton
(`vs30._cached_grid_and_tree`) so a multi-thousand-municipality crawl run
fetches the grid and builds the KD-tree once, not once per municipality. A
`backfill.py --vs30` mode retrofits already-crawled parts from before this
landed, mirroring `--municipality-code`. `engine.py`'s `_load_sites` reads
`COALESCE(b.vs30, ?)` (falling back to `DEFAULT_VS30`) instead of deriving
anything at request time, and `ground_motion.py`'s `compute_intensity`/
`compute_intensity_gridded` now accept `vs30` as either a scalar or a
per-site array.

Plain nearest-neighbor, not interpolation — matches how the source grid
itself was built (one inferred value per cell, not a continuous field) and
is cheap at national scale. Lookups farther than ~0.025° (~3x the grid's
nominal spacing) from any grid point return `NaN` (treated as "no
coverage", falling back to `DEFAULT_VS30`) rather than silently returning a
misleadingly precise but far-away value.

`compute_intensity_gridded`'s existing 1km-grid deduplication (buildings
snapped to a shared cell, ground motion computed once per occupied cell)
now also snaps Vs30 to the same cell's representative site rather than
computing per-building — an approximation, but the grid cell (1km) is
close to the source data's own spacing (~0.8km), so a cell rarely spans
more than one or two distinct grid points to begin with.

## Alternatives considered

- **USGS Global Vs30 Mosaic** (Wald & Allen 2007, updated hybrid,
  "active tectonic region" coefficients): free, global, one-shot GeoTIFF/
  grd download, no access friction. Kept as a documented fallback if the
  ESRM20 source becomes unavailable or unsuitable in practice — ESRM20's
  own Vs30 is itself derived from this same slope-based method, refined
  with geology, so falling back wouldn't be a large conceptual step down,
  just a smaller one already taken for us upstream.
- **NCSE-02 (Spain's national seismic code) soil coefficient C**: an
  official, government-published national classification, but only 4
  discrete classes at municipality granularity, from a 2002-era code.
  Considered as the primary source and rejected for resolution — kept as a
  candidate **cross-check**: a municipality where a fine-grained Vs30
  lookup disagrees sharply with NCSE-02's own classification is worth
  investigating before trusting the finer source there.
- **A bespoke Spain-specific proxy-based amplification model**
  (Weatherill et al. 2023's ESRM20 proxy/amplification coefficients,
  Zenodo record 8072116): provides regression coefficients to predict
  amplification from proxies (slope, geology, sediment thickness) rather
  than a precomputed Vs30 field, meant to complement GMPEs that *don't*
  already have a Vs30 term. Akkar et al. (2014) already has one, so
  layering this on top would double-count/conflict with the GMPE's own
  site term rather than add information. Rejected for that reason, not
  because the underlying research is unsound.
- **Building the whole thing at request time** (fetch grid + KD-tree
  lookup per scenario call): rejected on the same "static files over
  on-the-fly compute" principle every other spatial precompute in this
  project follows (ADR-0006, ADR-0010, ADR-0014) — a building's location
  doesn't change between requests, so neither should its Vs30 lookup.

## Consequences

- `buildings.parquet`'s schema gains a `vs30` column, following the same
  precompute-then-backfill pattern as `centroid_lon`/`municipality_code`.
  `pipeline.build_exposure` now stamps it on every future crawl natively
  (like `municipality_code`, ADR-0014). **Backfilled onto the real national
  dataset**: all 8,141 parts / 13,013,185 buildings, in 120s. 513,075
  buildings (~3.9%) fell outside the grid's coverage
  (`_MAX_LOOKUP_DISTANCE_DEG`) and got `NULL`, falling back to
  `DEFAULT_VS30` via `engine.py`'s `COALESCE` at query time — concentrated
  at coastal/edge locations, as expected. Ceuta (51001) fully covered (0
  nulls); Melilla (52001) ~1.3% null (126/9,799) — both usable, not the
  "worth checking before trusting" gap this consequence originally
  flagged.
- `pipelines/exposure` gains a new runtime dependency (`scipy`, for
  `cKDTree`) and a new network dependency (fetches a 17.8MB CSV from
  GitLab, cached per-process via `vs30._cached_grid_and_tree`) — paid once
  per crawl run or backfill invocation, not per municipality/part.
- `ground_motion.py`'s `vs30` parameters (`compute_intensity`,
  `compute_intensity_gridded`) now accept `float | np.ndarray`; any other
  caller passing a scalar (existing tests, `estimate_significant_distance_km`)
  is unaffected. Full scenario test suite (70 tests) passes with these
  changes, including the real-data `test_engine_integration.py` case that
  requires the backfill.
- Re-ran `validation-lorca-2011.md`'s real 2011 scenario against the
  backfilled data (§10.7 of that doc): expected ≥Slight damage (Σ P) goes
  from 983 to 2,165 (+120%) at the "high"/median tier — confirms the
  ~55% SA(0.3s) increase this ADR measured at Lorca propagates through to
  a large jump in expected damage, though not yet enough to flip modal
  counts at that tier by itself (still all-None; combining with the
  "low"/"very_low" tiers, ADR-0011, is the natural next check).
- Does not resolve `docs/questions-for-upm.md` #2 (Lorca's own Navarro et
  al. 2014 microzonation) — that request stays open as a Lorca-specific
  validation reference (a finer source to compare ESRM20's coarser
  national grid against locally), not as a replacement for it.
