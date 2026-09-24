# pipelines/

Three independent ETL pipelines that turn public data sources into the
static parquet/PMTiles files `services/scenario` reads at request time. Each
is its own `uv` workspace package (`twiner-faults`, `twiner-exposure`,
`twiner-fragility`) but they share one convention: fetch from a real public
source, write parquet (+ PMTiles for buildings), no database, no server —
see [`../docs/milestone-1-plan.md`](../docs/milestone-1-plan.md) §3 for why.

```
pipelines/faults      QAFI (IGME) -----------> faults.parquet
pipelines/exposure     Catastro (INSPIRE) ----> buildings.parquet (partitioned)
                                            \--> exposure.parquet
                                            \--> buildings.pmtiles
                        IGN/CNIG (ADR-0013) ---> municipalities.parquet
                                            \--> municipalities.pmtiles
pipelines/fragility    Martins & Silva (2020) -> fragility.parquet
                                                        |
                                                        v
                                          services/scenario reads all five
```

## `faults`: QAFI active faults

Downloads IGME's official QAFI v4 shapefile (`QAFI_Traces.rar`), extracts
the 201-fault attribute table, and resolves each fault's maximum magnitude:
uses QAFI's own published value where available (~60% of faults), falls
back to a length-based estimate (Wells & Coppersmith 1994) otherwise. See
[ADR-0004](../docs/decisions/0004-qafi-shapefile-source.md) for why the
official shapefile is used instead of IGME's ArcGIS REST layer (that layer
was found to have unreliable geometry for at least one fault).

Requires `unar` on `PATH` (`brew install unar`) to extract the RAR archive.

```bash
uv run python -m faults data/faults/qafi_faults.parquet
```

Output columns: `fault_id`, `name`, `section_name`, `length_km`, `mmax`,
`mmax_source` (`"qafi_v4_published"` or `"estimated_wells_coppersmith_1994"`),
`rake`, `dip`, `strike`, `geometry`. Nationwide by nature (only 201 rows) --
no regional variant needed.

## `exposure`: Catastro buildings -> exposure + geometry

The bulk of the data volume. Two entry points:

**Single municipality** (`exposure.__main__`, defaults to Lorca) --
downloads one municipality's INSPIRE Buildings GML from Catastro, parses
footprints/floors/construction year, assigns a taxonomy class heuristically
(no field survey -- see `taxonomy.py`'s docstring for the method and its
limits), and writes `buildings.parquet` + `exposure.parquet` (+ PMTiles if
requested). Good for local iteration on parsing/taxonomy logic without
waiting on a multi-hour crawl -- point it at a directory other than
`data/exposure` (the full national dataset, see below) so the two don't
collide.

```bash
uv run python -m exposure data/exposure-test/raw/lorca data/exposure-test/buildings.parquet \
    data/exposure-test/exposure.parquet data/exposure-test/buildings.pmtiles
```

**Region/national crawl** (`exposure.region_cli`) -- the same pipeline, run
across every municipality in a set of provinces, concurrently (default 8
workers), resumable (skips municipalities whose output already exists),
and disk-conscious (deletes each municipality's raw GML immediately after
parsing). See [ADR-0005](../docs/decisions/0005-region-scale-crawling.md).
`--spain` covers every province reachable through Catastro's national
feed (48 provinces -- excludes the Basque Country and Navarra, which run
separate Foral cadastral systems); `--basque-navarra` covers those
separately, on top of `--spain` or alone. `data/exposure` is the one
consolidated dataset the rest of the app expects -- there's no ongoing
reason to crawl a smaller subset (e.g. just Murcia + Andalucía) into its
own directory once you have this.

```bash
uv run python -m exposure.region_cli data/exposure/raw data/exposure/parts \
    data/exposure/exposure.parquet data/exposure/buildings.pmtiles \
    --spain --basque-navarra
```

Key design point: `buildings.parquet` from a region/national crawl is
**partitioned** (one file per municipality under `parts/`), never combined
into a single file -- `services/scenario` reads it via a glob pattern
(`parts/*.buildings.parquet`), and DuckDB's parquet reader uses each part's
column statistics to skip files that can't match a query's spatial filter
(see [ADR-0006](../docs/decisions/0006-precomputed-spatial-columns-and-adaptive-radius.md)).
`exposure.parquet` (attributes only, no geometry, much smaller) *is*
combined into one file.

If you already have `buildings.parquet` file(s) from before spatial index
columns existed, `exposure.backfill` retrofits them in place without
re-downloading:

```bash
uv run python -m exposure.backfill "data/exposure/parts/*.buildings.parquet"
```

### Municipal boundaries (`exposure.municipalities_cli`)

Downloads IGN/CNIG's national municipal-boundary dataset (one GML zip,
whole country, see [ADR-0013](../docs/decisions/0013-municipal-boundary-choropleth.md)
and [`../DATA-SOURCES.md`](../DATA-SOURCES.md)), derives each polygon's
5-digit INE code from IGN's own 11-digit `nationalCode` (its last 5
digits), and attaches an `n_buildings` count per municipality by counting
rows in whatever `<ine_code>.buildings.parquet` parts already exist under
a given `parts_dir` -- no new per-building processing. Independent of
region/province choice (always one national download); run it after an
`exposure`/`exposure.region_cli` crawl, pointed at that crawl's
`parts_dir`.

```bash
uv run python -m exposure.municipalities_cli data/exposure/muni_raw data/exposure/parts \
    data/exposure/municipalities.pmtiles data/exposure/municipalities.parquet
```

Two outputs: `municipalities.pmtiles` (the frontend's low-zoom choropleth
layer) and `municipalities.parquet`, a GeoParquet `services/scenario`
spatially joins scenario results against server-side
(`TWINER_MUNICIPALITIES_PATH`) to compute per-municipality aggregate
stats -- see ADR-0013 for why that join is DuckDB spatial rather than a
new geopandas/shapely dependency on the scenario service.

## `fragility`: Martins & Silva (2020) fragility functions

Downloads a curated subset of the [global fragility/vulnerability function
repository](https://github.com/lmartins88/global_fragility_vulnerability)
(CC BY-SA 4.0) -- the two GEM-taxonomy classes our exposure taxonomy
heuristic can produce (`CR_LDUAL-DUL`, `MR_LWAL-DUL`), across height
classes H1-H12. See [`merisur.md`](../docs/merisur.md) §4.5/§4.9 and
[`milestone-1-plan.md`](../docs/milestone-1-plan.md) §2 for why generic
global fragility functions stand in for MERISUR's own Lorca-specific
capacity curves (not public), and
[`validation-lorca-2011.md`](../docs/validation-lorca-2011.md) for what
that substitution costs in accuracy.

```bash
uv run python -m fragility data/fragility/fragility.parquet
```

Output is long-format: one row per (taxonomy, height_class, damage_state,
intensity value), giving cumulative exceedance probability at that
intensity. Nationwide/universal -- not region-specific, never needs
re-running for a bigger area.

## Where it all lands

`services/scenario` (see its own package for the request-handling side)
reads all five outputs -- `buildings.parquet`, `exposure.parquet`,
`fragility.parquet`, `faults.parquet`, `municipalities.parquet` -- via
configurable paths (`TWINER_BUILDINGS_PATH` etc., see
`scenario/local.py`/`handler.py`), so
switching between the Lorca, Murcia+Andalucía, or national dataset is an
environment-variable change, not a code change. `bin/twiner` (repo root)
starts the local dev stack against whichever dataset its env vars point at.
