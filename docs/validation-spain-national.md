# Sanity check: national (Spain-wide) expansion

Status: done. Companion to
[`validation-region-expansion.md`](./validation-region-expansion.md), covering
the jump from Murcia + Andalucía (819 municipalities) to all 48 non-Foral
provinces (see [ADR-0005](./decisions/0005-region-scale-crawling.md)'s
`SPAIN_PROVINCES`). Same two questions as before: does it still work, and
building info popups also land in this pass (the user's most recent request).

## 1. The crawl

`python -m exposure.region_cli --spain` (8 workers), 15,196 municipality
parts:

- **12,369,432 buildings parsed, 0 failures.**
- `exposure.parquet` (attributes only, one file): **70.3MB**.
- `buildings.parquet` stays partitioned (ADR-0005): 15,196 files, ~1.1GB
  combined under `data/exposure/parts/`.
- `tippecanoe` tiling all 15,196 GeoJSON parts (generated transiently,
  never persisted — see ADR-0005's `tile_region()`) into one PMTiles file:
  output **1.62GB** (up from 352MB for Murcia + Andalucía, roughly
  proportional to the ~4.5x building-count increase).
- Ran unattended in the background; both crawl and tiling completed with no
  manual intervention.

Validated the PMTiles output itself, not just its size: `pmtiles show`
confirms spec v3, `mvt` tiles, 160,861 addressed tiles, zoom 0–14, and a
bounding box of (-18.15°, 27.64°) to (4.31°, 43.79°) — mainland Spain, the
Balearics, and the Canary Islands (28°N), matching `SPAIN_PROVINCES`
including provinces 35/38.

`bin/twiner` and `apps/web/public/data/buildings.pmtiles` now point at this
dataset (were `exposure_region`/the 352MB regional file).

## 2. Iberia-wide manual-mode sanity check: 8 cities, Mw 6.5

Ran the same fixed-magnitude manual scenario (Mw 6.5, strike-slip) centered
on eight cities spanning the peninsula plus the Canary Islands — deliberately
wider geographic spread than §3 of the region doc, which stayed within
Murcia + Andalucía:

| City | Evaluated | Damaged | Server | Wall |
|---|---|---|---|---|
| A Coruña (NW) | 958,702 | 20,408 | 1.20s | 1.28s |
| Barcelona (NE) | 1,250,038 | 94,515 | 1.14s | 1.44s |
| Badajoz (W, Portugal border) | 485,197 | 14,669 | 0.64s | 0.69s |
| Sevilla (SW) | 1,257,241 | 59,850 | 1.12s | 1.31s |
| Valencia (E) | 1,204,075 | 59,598 | 1.11s | 1.30s |
| Bilbao (N, Basque border) | 394,055 | 0 | 0.57s | 0.58s |
| Murcia (SE) | 1,238,102 | 47,432 | 1.17s | 1.33s |
| Santa Cruz de Tenerife (Canary Is.) | 372,231 | 28,737 | 0.58s | 0.69s |

All eight succeed, sub-1.5s wall time end to end, no errors — the
precomputed spatial columns + adaptive radius work from
[ADR-0006](./decisions/0006-precomputed-spatial-columns-and-adaptive-radius.md)
scale to the national dataset without changes. Bilbao's noticeably lower
`n_evaluated` (394K vs. 1.2M+ for similarly-sized cities) is expected, not a
bug — it's the real gap this crawl doesn't cover (§4 below): the exposure
radius extends north into the Basque Country/Cantabria, where we have no
buildings, rather than being cut short by anything wrong with the query.

Also re-ran an automatic-mode (fault-based) scenario end to end against the
national dataset (Munébrega W fault, Mw 6.56, finite rupture surface):
798,708 evaluated, 6,142 damaged, 967ms server / 1.0s wall, 1.6MB payload —
consistent with the region-scale numbers in
[`validation-region-expansion.md` §6](./validation-region-expansion.md).

## 3. Building-info endpoint, tested against the national dataset

New this session (`GET /buildings/{building_id}`, `building_lookup.py`) —
exercised live against a damaged building from the automatic-mode run above:
returns `taxonomy_class`/`height_class` in well under 100ms via a DuckDB
point lookup on the 70MB `exposure.parquet`, no full-scan re-tiling needed.
See `services/scenario/tests/test_local_api.py` for the synthetic-fixture
regression tests (200 for a known id, 404 for an unknown one).

## 4. Basque Country + Navarra: closed, not missing

**Update (2026-09-15, a later session than §1-§3 above): this gap is
closed.** §1-§3's crawl predates it -- at the time, the INSPIRE ATOM feed
this pipeline crawls had no entries for provinces 01/20/48 (Álava,
Guipúzcoa, Vizcaya) or 31 (Navarra), which run separate Foral/regional
cadastral systems, not the national Dirección General del Catastro's
(visible above as Bilbao's anomalously low `n_evaluated`). A follow-up
session added dedicated crawlers for all four
(`pipelines/exposure/src/exposure/{alava,navarra,gipuzkoa,vizcaya}.py`,
dispatched via `region_cli.py --basque-navarra`) and actually ran the
crawl end to end: `data/exposure/exposure.parquet` now has
12,881,817 rows (up from this doc's original 12,369,432) and
`apps/web/public/data/buildings.pmtiles` (1.68GB) was rebuilt and
redeployed. Full source-by-source writeup:
`docs/basque-navarra-cadastral-sources.md`.

## 5. Regression found: long faults at nationwide scale ("long faults slow again")

Prompted by user reports of a 10s/64MB response for the Barcelona fault
(ME025) locally, and a suspicion that faults with sparser real data (e.g.
Peñacova-Régua-Verín, PO011, whose trace is entirely in Portugal -- a
different, still out-of-scope country, not the Basque/Navarra gap §4
covers) were somehow *slower*, not faster. Confirmed and root-caused; see
[ADR-0009](./decisions/0009-fault-scenario-performance.md) for the full
mechanism and fix (chunking `hazardlib`'s distance calc, sizing the
pre-filter off the rupture surface's real extent, deduping the
ground-motion calculation onto a ~1km grid, trimming + compressing the
response, and converting `/scenarios/fault` to a cacheable `GET`).

Headline numbers, same nationwide dataset as §1-§3 above (now including
Basque Country + Navarra per §4):

| | Before | After |
|---|---|---|
| Barcelona (ME025) server time | 7.2s | 1.7s |
| Barcelona payload (wire, gzip) | 63.2 MB | ~1.5 MB |
| Peñacova (PO011) server time | never completed (hung >5min, pegged the dev server's single worker, blocked `/health` too) | 2.8s |
| Peñacova payload | never returned | ~117 KB |

Peñacova was the more important case: a fault with *no real exposure data
near it at all* was slower than one evaluating 3M real buildings, which is
exactly backwards. Root cause was `hazardlib`'s
`get_joyner_boore_distance` building one dense (mesh-points × sites)
distance matrix with no chunking -- Peñacova's much longer trace produces
~4.5x more mesh points at the fixed 2km spacing (ADR-0007), and that
extra factor multiplied against millions of sites, not just added to them
(measured 49x slower at 3M sites for only 4.5x more mesh points -- memory
pressure from a ~29GB temporary array, not proportional compute).
