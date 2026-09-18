# ADR-0013: Municipal-boundary choropleth for low-zoom scenario review

Status: accepted. **The spatial-join implementation described below (the
DuckDB `ST_Contains` join) was replaced by
[ADR-0014](./0014-municipality-code-column-replaces-per-request-spatial-join.md)**
after it caused a request-time regression; `compute_municipality_stats` is
now a plain `groupby` on a precomputed `municipality_code` column, not a
per-request spatial join. The choropleth decision itself (boundary source,
code-join derivation, data-driven visibility filtering) is unaffected and
still describes current behaviour.

## Context

At national scale, a scenario's damaged buildings can be scattered across
a huge area (a large fault's rupture can reach millions of buildings), and
`buildings.pmtiles`' individual-footprint fill layer becomes useless well
before the map is zoomed out far enough to see that whole area at once --
there's nothing to visually group "this town got hit hard" from "this one
didn't" until you're zoomed in close enough that most of the affected area
is off-screen.

Needed: a low-zoom aggregate view (percentage of buildings affected, and
by damage state, per municipality) that hands off to the existing
per-building layer once the user zooms in far enough to usefully pick a
building out — sourced from an official boundary dataset, and without
reprocessing the exposure pipeline's already-crawled, multi-hundred-GB
national buildings data.

## Decision

**Boundary source**: IGN/CNIG's INSPIRE Administrative Units ATOM feed
(`https://www.ign.es/atom/dataset_feeds/lin_lim_mun.es.xml`), resolving to
a single national GML zip (CC BY 4.0, no auth beyond a browser-like
User-Agent — `centrodedescargas.cnig.es` drops requests' default one).
`AdministrativeUnit`'s 4th-order features are municipio-level polygons,
8,220 nationwide, one file (`au_AdministrativeUnit_4thOrder0.gml`, under
the format's 10,000-feature-per-file cap). See DATA-SOURCES.md.

**Code join, not a new crawl**: IGN's own `nationalCode` is an 11-digit
internal code, not the 5-digit INE municipality code the exposure
pipeline's own per-municipality partitioning (`<ine_code>.buildings.parquet`,
region.py) already uses — but its **last 5 digits are** the INE code
(verified against Lorca `34143030024` → `30024` and Madrid
`34132828079` → `28079`). `pipelines/exposure/src/exposure/municipalities.py`
derives `ine_code` this way, which lets it attach a per-municipality
`n_buildings` count (the choropleth's denominator) by counting rows in
whatever `buildings.parquet` parts already exist on disk — no new
per-building processing, no re-crawl of Catastro/Basque/Navarra sources.

**Aggregate stats via DuckDB spatial, computed server-side**: the
numerator (how many of a scenario's evaluated buildings, per damage state,
fall in each municipality) is computed in `services/scenario/response.py`'s
`compute_municipality_stats`, spatially joining the scenario's full
per-building result (`engine.run_scenario`'s `lon`/`lat`, already computed
for the spatial pre-filter) against a small GeoParquet of municipality
polygons (`municipalities.parquet`, written alongside `municipalities.pmtiles`)
using DuckDB's `spatial` extension (`ST_Contains`/`ST_Point`). This adds no
new Python geo dependency to `services/scenario` (which otherwise has none)
and reuses the same "DuckDB for spatial work" pattern `engine.py`'s own
bounding-box pre-filter already established — DuckDB reads GeoParquet's
geometry column natively once the extension is loaded, no manual WKB
parsing needed.

**Visibility is data-driven, not just zoom-driven**: the frontend's
`municipalities-fill` layer (`apps/web/src/components/DamageMap.tsx`) is
`maxzoom`-gated against `buildings-fill`'s `minzoom` (11) for the
zoom-level handoff, but *also* filtered (via `map.setFilter`, not just
`fill-opacity`) to only the municipality codes present in the current
scenario's `municipality_stats` **with at least one non-"None" building**.
Two things this rules out, both found live against a real scenario run:

- Showing all ~8,200 municipalities' empty-data boxes before any scenario
  has run, or a flat grey box nationwide once one has.
- Showing a municipality as "affected" merely because it fell inside
  `engine.py`'s spatial pre-filter box (sized off the rupture's own
  magnitude, not proximity to any particular place) — a large enough
  earthquake can pull in and confidently-undamaged-evaluate a municipality
  hundreds of km from the epicenter, which isn't what "affected" means to
  a reviewer.

A `filter`, not `fill-opacity: 0`, is required for the first point too:
MapLibre still hit-tests (and can swallow clicks for) a feature with zero
opacity, which would silently break manual-mode's "click the map to set
lat/lon" for a click anywhere near an unaffected municipality.

## Alternatives considered

- **Precompute `municipality_code` as a real column on every building
  row, re-tile.** Rejected: would mean reprocessing all ~13M already-
  crawled national buildings (and re-tiling `buildings.pmtiles`, 1.68GB+)
  just to add one join key, when the exposure pipeline's existing
  per-municipality partitioning already gives the same join for free via
  file naming.
- **Client-side spatial join** (ship municipality polygons to the browser,
  join against building coordinates there). Rejected: the frontend's thin
  per-building payload deliberately never carries `lon`/`lat`
  (`response.py`'s own docstring/ADR-0003) to keep payloads small: adding
  it back just for this would undo that, for every scenario response, not
  just the low-zoom case.
- **geopandas/shapely in `services/scenario`** for the spatial join
  instead of DuckDB. Rejected: an entirely new dependency for one query,
  where DuckDB (already a hard dependency, already doing `engine.py`'s own
  spatial filtering) does the same job via one `INSTALL spatial`.

## Consequences

- A municipality with no `<ine_code>.buildings.parquet` part yet (not yet
  crawled, or one of the Basque/Navarra territories whose parts use
  placeholder codes rather than real INE ones, see `region.py`) renders
  with `n_buildings = 0` rather than being dropped — the boundary still
  exists, just with nothing to show until that gap closes.
- `compute_municipality_stats` fails soft (`duckdb.IOException` → `[]`) if
  `municipalities_path` doesn't exist yet — an existing dev/test setup
  without this newer dataset keeps working, just without the choropleth.
- The zoom threshold (11) and "at least one non-None building" bar for
  "affected" are both product judgment calls, not load-bearing constants
  elsewhere — revisit either without an architecture change if they turn
  out wrong in practice.
