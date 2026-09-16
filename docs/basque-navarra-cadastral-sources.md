# Research: cadastral data for the Basque Country + Navarra

Status: **all four territories are implemented** and wired into the
exposure pipeline (`pipelines/exposure/src/exposure/{alava,navarra,
gipuzkoa,vizcaya,inspire_bu}.py`, dispatched from `region.py`'s
`crawl_alava`/`crawl_navarra`/`crawl_gipuzkoa`/`crawl_vizcaya` and
`region_cli.py`'s `--basque-navarra` flag). Vizcaya was the last of the
four -- the original open-data-catalog approach this doc's research
first proposed for it never panned out (its URLs 404'd), but a directly
supplied WFS endpoint (`geo.bizkaia.eus`'s ArcGIS Server WFS, a different
platform to the other three's INSPIRE services) turned out to work, with
a real per-municipality breakdown even better-suited to this pipeline's
usual per-municipality partitioning than Navarra's or Gipuzkoa's
whole-territory crawls. Original follow-up context to
[`validation-spain-national.md` §4](./validation-spain-national.md) — the
national crawl (ADR-0005) has no data for provinces 01/20/48 (Álava,
Guipúzcoa, Vizcaya) and 31 (Navarra) because they run their own Foral/
regional cadastral systems, entirely outside the national Dirección
General del Catastro's INSPIRE ATOM feed this pipeline crawls. This
document is the concrete "what's available, and is it worth building"
answer, requested directly after the national crawl.

## Summary

Three of the four provinces (Álava, Navarra, Gipuzkoa) publish
INSPIRE-compliant Buildings ("BU") data independently — same EU data
model as Catastro's, but a different schema profile, different hosting,
and a different access pattern per territory. Vizcaya turned out to run
something different again: not an INSPIRE download service at all, but
a plain ArcGIS Server WFS serving Bizkaia's own flat cadastral schema.
None of the four is reachable through the existing `catastro.py`
crawler; all four are implemented now, each with its own module.

| Territory | Source | Format/access | Verdict |
|---|---|---|---|
| Álava (01) | Diputación Foral de Álava, `geo.araba.eus` | Single bulk GML zip, whole territory, standard INSPIRE ATOM | **Implemented** (`alava.py`) |
| Navarra (31) | Gobierno de Navarra / IDENA, `inspire.navarra.es` | Live WFS, paged with the server's own `next` link | **Implemented** (`navarra.py`) |
| Guipúzcoa (20) | Diputación Foral de Gipuzkoa, `b5m.gipuzkoa.eus` | Live WFS (`b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_bu`), no `next` link -- fetched as adaptive bbox tiles instead (see its section below) | **Implemented** (`gipuzkoa.py`) |
| Vizcaya (48) | Diputación Foral de Bizkaia, `geo.bizkaia.eus` (ArcGIS Server WFS, **not** an INSPIRE service) | Live WFS with a real `Municipios` feature type + OGC filter support -- true per-municipality queries | **Implemented** (`vizcaya.py`) |

## Findings, per territory

### Álava — implemented (`alava.py`)

- INSPIRE ATOM root: `https://geo.araba.eus/atom/ATOM.atom` → Buildings
  sub-feed `https://geo.araba.eus/atom/BU/Buildings.atom` → one bulk
  download per CRS, not per municipality:
  `https://geo.araba.eus/deskargak/INSPIRE/BU/GML/4258/BU_4258_GML.zip`
  (EPSG:4258 ≈ WGS84, ~16MB compressed). `alava.py` crawls the ATOM feed
  for this URL (survives the file moving) rather than pinning it.
- The zip contains one GML file per municipality already split out (e.g.
  `ES.AFA.BU.0101_4258.gml`, 52 files total), full standard **INSPIRE
  Buildings Base (`bu-base`) schema** — richer/more deeply nested than
  Catastro's own flattened profile:
  - `localId`/`gml:id` via `bu-base:inspireId/base:Identifier/base:localId`
    (not a flat top-level attribute the way `gpd.read_file` exposes
    Catastro's) -- `gpd.read_file` itself flattens this fine, no custom
    XML walking needed for it.
  - Construction date via `bu-base:dateOfConstruction/bu-base:DateOfEvent/
    bu-base:anyPoint` (nested), not a flat `beginning` field -- also
    flattened by `gpd.read_file` into an `anyPoint` column directly.
  - `currentUse` via a nested `bu-base:CurrentUse/bu-base:currentUse` with
    an INSPIRE codelist URL value (e.g. `.../CurrentUseValue/industrial`)
    living only in an `xlink:href` attribute -- `gpd.read_file` drops this
    silently (GDAL's GML flattening doesn't pull attribute values into a
    column), so `inspire_bu.py` walks the XML itself with `ElementTree`
    for this one field. Not load-bearing for the taxonomy heuristic
    (`assign_taxonomy` only uses construction_year/floors), so this is a
    best-effort enrichment, not something a pipeline failure hinges on.
  - `numberOfFloorsAboveGround`, `numberOfDwellings` are flat and map
    directly, same as Catastro's -- and, unlike Catastro, live directly on
    the `Building` feature itself, with no separate buildingpart file to
    join against.
  - No floor-area attribute exists on this schema's Building feature at
    all (only height above ground, in metres) -- `floor_area_m2` is left
    unpopulated rather than guessed at from a different quantity.
- Implemented as `inspire_bu.load_buildings()` (shared with Navarra and
  Gipuzkoa below, all three being the same schema family) producing the
  same output schema `parse.py`'s Catastro loader does, so it still feeds
  the existing taxonomy/tiling/scenario code unchanged downstream.
- No per-municipality crawl needed at all — one small bulk download for
  the whole province, much simpler than Catastro's own per-municipality
  ATOM crawl (ADR-0005). Verified end-to-end this session: all 52
  municipalities, 81,322 buildings, zero failures.

### Navarra — implemented (`navarra.py`)

- `https://inspire.navarra.es/services/BU/wfs?service=WFS&request=GetCapabilities`
  responds: WFS 2.0.0, `BU:Building` type name, `bu-core2d`/`bu-base`
  schema family — same shape as Álava's files (`inspire_bu.py` covers
  both), just with a `beginning`/`end` date interval instead of a single
  `anyPoint`, and geometry as `gml:MultiSurface`/`Surface`/`PolygonPatch`
  rather than a plain `gml:Polygon` (both read fine via `gpd.read_file`
  either way).
- No per-municipality breakdown at all — `navarra.py` pages through the
  *whole territory* in one `Building` feature type, following the WFS
  2.0 `next` link the server returns on each page (rather than computing
  `startIndex` itself) until a page comes back empty. The server's own
  page cap is 5000 features/request. `region.py`'s `crawl_navarra`
  concatenates every page into one province-wide buildings/exposure part
  (keyed `31000`, not a real municipality code — see its docstring).
- IDENA's geoportal (`geoportal.navarra.es/es/idena/descargar`) also
  offers a predefined per-municipality GML download (via the "CatastRoNav"
  bulk tool mentioned in IDENA's own docs) as an alternative to the WFS —
  not used here since the WFS worked fine, but worth knowing about if the
  WFS ever becomes unreliable.

### Guipúzcoa — implemented (`gipuzkoa.py`)

- `b5m.gipuzkoa.eus` publishes a live Buildings WFS at
  `https://b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_bu` (confirmed live
  this session via plain `curl` — an earlier session's browser-based check
  apparently hit something that didn't render, not an actual outage).
  Type name `bu-ext2d:Building`; same `bu-base` schema family as
  Álava/Navarra, wrapped in a Gipuzkoa-specific `bu-ext2d` extension
  (extra address/document/valuation fields `inspire_bu.py` doesn't need).
- Unlike Navarra's WFS, this one returns no `next` link, and its
  `startIndex` is a plain unindexed row-skip: response time grows with the
  offset itself (verified: 0ms-ish at `startindex=0`, ~0.6s at 100, ~4.8s
  at 1000, times out past 40s beyond ~10000) — paging the whole ~124k-
  feature territory this way is not viable, and would be inconsiderate to
  a public server regardless.
- `gipuzkoa.py` instead recursively quarters the territory's bounding box
  (a `bbox` filter's `resulttype=hits` count is fast regardless of extent —
  verified: ~1.4s for the *entire* territory) until each tile's count is
  safely under the 5000/request page cap, then fetches each leaf tile in
  one un-paged request. Verified this session: 70 leaf tiles, ~14s total
  to compute the tiling, ~124k buildings covered with a small (~3%)
  boundary-overlap duplication that `region.py`'s `crawl_gipuzkoa`
  deduplicates by `building_id` after concatenating.

### Vizcaya — implemented (`vizcaya.py`)

- The `opendatabizkaia.eus` catalog approach this doc originally proposed
  never panned out -- this session's attempts at its dataset/API URLs
  both 404'd (see the earlier attempt below, kept for the record).
  Instead, a directly-supplied WFS endpoint worked immediately:
  `https://geo.bizkaia.eus/arcgisserverinspire/services/
  LurraldeAntolamendua_PlanificacionTerritorial/Katastro_Catastro_WFS/
  MapServer/WFSServer` -- a plain **ArcGIS Server WFS**, not an INSPIRE
  download service at all, unlike the other three.
- Its schema is Bizkaia's own flat cadastral profile, feature type
  `Katastro_Catastro_WFS:Edificios` ("buildings"): `Codigo_Mun` (this
  source's own municipality code), `Ano_Constr` (construction year,
  flat, `0` is the "unknown" sentinel), `Numero_Alt` (floors above
  ground, flat), `Numero_Viv` (dwellings, flat), `Codigo_Uso` (a
  single-letter use code, already flat text -- no INSPIRE-style nested
  codelist attribute the way `currentUse` needs `inspire_bu.py`'s own
  XML re-walk for the other three). Geometry is `gml:MultiSurface` in
  **EPSG:25830** (UTM, not geographic lat/lon) with a dummy `Z=0` --
  `gpd.read_file` + `to_crs` + `force_2d()` handles both without any
  custom parsing. Every field `gpd.read_file` needs is already flat, so
  `vizcaya.py` has its own small `load_buildings()` rather than reusing
  `inspire_bu.py` (a genuinely different schema, not another bu-base
  variant).
- Crucially, this WFS also serves a `Katastro_Catastro_WFS:Municipios`
  feature type (`Codigo_Pro`/`Codigo_Mun`/`Descripcio`) — confirmed
  `Codigo_Pro=48` (Vizcaya's real province code) for all 113
  municipalities, and standard **OGC Filter Encoding** (a `PropertyIsEqualTo`
  filter on `Codigo_Mun` passed as a `filter` query parameter) actually
  works against `Edificios` — so, unlike Navarra/Gipuzkoa, this crawls
  **per real municipality**, the same shape as Álava's crawl, not a
  single province-wide part.
- Paging: this server's `startIndex` is flat-cost regardless of offset
  (verified: ~10-15s per 5000-row page at `startindex` 0, 5000, and 10000
  fetching Bilbao, the largest municipality at 13,753 buildings) — no
  Gipuzkoa-style adaptive tiling needed, plain `count`/`startIndex`
  paging per municipality is fine. Verified end-to-end this session for
  three municipalities (Abadiño: 1,357 buildings/1 page; Amorebieta-Etxano:
  2,547/1 page; Ajangiz: 424/1 page) and for Bilbao specifically (3 pages,
  13,753 buildings, zero duplicates after concatenating).
- 140,003 buildings total across the province (`resulttype=hits` on the
  whole `Edificios` type, unfiltered).

#### Vizcaya — original (abandoned) approach, kept for the record

- `opendatabizkaia.eus`'s "Parcelario catastral" dataset is published
  **per municipality** (e.g. `.../parcelario-catastral-getxo/recurso/
  parcelario-catastral-gml-getxo` per the original research), not as one
  bulk file — 112 municipalities in Vizcaya means either finding a catalog
  API to enumerate dataset slugs programmatically, or a static list.
- A prior session tried the obvious next steps -- guessing the dataset
  page URL directly, and probing for a CKAN-style `package_search` API
  (the usual shape for a Spanish open-data portal) -- both 404'd. Whether
  that portal's real API could still be found with more digging is now
  moot: the WFS endpoint above made this whole approach unnecessary, and
  it's also unclear the catalog's schema was even BU/Buildings rather
  than the CP/parcels theme its "parcelario catastral" name suggests.

## Implementation notes (all four territories)

Built as a `sources/`-style split rather than growing Catastro's
`load_buildings()` with special cases -- the GML schema differs enough
(nested bu-base attributes vs. Catastro's flattened profile, and Vizcaya's
own flat-but-differently-named profile again) that a single loader
covering all of them would have stopped being readable:

- `inspire_bu.py` -- one shared loader for the INSPIRE bu-base/bu-core2d
  schema family (Álava, Navarra, Gipuzkoa all share it, just under
  different namespace prefixes and with minor per-source field-name
  differences the loader tries in order). Produces the exact same output
  schema as `parse.py`'s Catastro loader, so `pipeline.build_exposure`
  (split out of `process_municipality` for this reason),
  `taxonomy.assign_taxonomy`, and everything in `tile.py`/`region.py`
  downstream work unchanged regardless of which loader produced a given
  municipality's buildings.
- `vizcaya.py` has its own `load_buildings()` instead of using
  `inspire_bu.py` -- its ArcGIS Server WFS is a genuinely different
  schema (flat `Codigo_Uso`/`Ano_Constr`/`Numero_Alt`/`Numero_Viv`
  fields, EPSG:25830 geometry, no INSPIRE nesting at all), not another
  bu-base variant, but still produces the same shared output schema.
- `alava.py`, `navarra.py`, `gipuzkoa.py`, `vizcaya.py` -- one
  discovery/download module per source, mirroring `catastro.py`'s shape
  (a `MunicipalityRef`-style identifier, a function that gets you the raw
  GML), but each adapted to its source's actual access pattern rather
  than forcing all four into Catastro's per-municipality-zip mold: Álava
  is one bulk zip (already split by municipality inside), Navarra pages
  through the whole territory via its WFS's own `next` link, Gipuzkoa
  recursively tiles the territory's bounding box (its WFS has no `next`
  link and an unindexed, offset-position-dependent `startIndex` that
  makes plain paging impractical past a few thousand rows), and Vizcaya
  queries its WFS per real municipality via an OGC filter on `Codigo_Mun`
  (this server's `startIndex` is flat-cost regardless of offset, so plain
  paging is fine within one municipality — see each section above).
- `region.py`'s `crawl_alava`/`crawl_navarra`/`crawl_gipuzkoa`/
  `crawl_vizcaya` (dispatched from `region_cli.py`'s `--basque-navarra`
  flag) write into the same `parts_dir` `crawl_region` does, using the
  same `<code>.buildings.parquet`/`<code>.exposure.parquet` naming --
  `combine_exposure` and `tile_region` need no changes at all to pick up
  their output alongside the national crawl's.
- Álava and Vizcaya get a real per-municipality part each, per their own
  source-specific code (Álava: a 4-digit province+sequence code; Vizcaya:
  a province(48)+`Codigo_Mun` code that happens to often match the real
  INE code, but isn't guaranteed to for every municipality -- see each
  module's `MunicipalityRef` docstring, neither is a guaranteed INE
  lookup key any more than Catastro's own codes are per `catastro.py`).
  Navarra and Gipuzkoa, having no per-municipality data at all, each get
  one province-wide part under a placeholder code (`31000`, `20000`) that
  can't collide with a real municipality number. `crawl_vizcaya` also
  parallelizes across municipalities with a thread pool (like
  `crawl_region`), since -- unlike Álava's single bulk download -- each
  of its 113 municipalities is its own independent network request.
