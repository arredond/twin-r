# Data sources

One entry per external dataset the pipelines download, each linking to the
actual resource used (an ATOM feed, WFS endpoint, or direct download) —
not the publishing org's homepage. See `docs/decisions/` for the ADRs
behind several of these choices, and
`docs/basque-navarra-cadastral-sources.md` for the fuller per-territory
research behind the four Basque/Navarra sources below.

## Building footprints (cadastral)

| Source | Coverage | Resource | Pipeline module |
|---|---|---|---|
| Dirección General del Catastro | All of Spain except the Basque Country (01/20/48) and Navarra (31), which run their own cadastral systems | INSPIRE Buildings ATOM feed: [`ES.SDGC.bu.atom.xml`](https://www.catastro.hacienda.gob.es/INSPIRE/buildings/ES.SDGC.bu.atom.xml) | `pipelines/exposure/src/exposure/catastro.py` |
| ↳ Ceuta/Melilla (via the same feed) | Ceuta, Melilla | Filed under Catastro's own "territorial office" codes **55**/**56** in the same feed above, not their real INE province codes 51/52 (confirmed by reading the live feed directly — its `<title>` labels them "Territorial office 55 Ceuta"/"Territorial office 56 Melilla"). Originally missed nationally because `region.py`'s `SPAIN_PROVINCES` assumed 51/52 meant Ceuta/Melilla; those codes actually belong to unrelated Murcia/Asturias overflow municipalities in this feed. `services/scenario/response.py` and `pipelines/exposure/municipalities.py` each carry a small, explicit `_CATASTRO_CODE_TO_INE` map (55101→51001, 56101→52001) so the two municipalities' buildings still join correctly onto IGN's INE-keyed municipal-boundary layer. | `pipelines/exposure/src/exposure/catastro.py` |
| Diputación Foral de Álava | Álava (province 01) | INSPIRE Buildings ATOM feed: [`Buildings.atom`](https://geo.araba.eus/atom/BU/Buildings.atom), one bulk GML zip for the whole territory | `pipelines/exposure/src/exposure/alava.py` |
| Gobierno de Navarra / IDENA | Navarra (province 31) | Live WFS: [`inspire.navarra.es/services/BU/wfs`](https://inspire.navarra.es/services/BU/wfs) | `pipelines/exposure/src/exposure/navarra.py` |
| Diputación Foral de Gipuzkoa | Gipuzkoa (province 20) | Bulk ATOM download: [`buildings.xml`](https://b5m.gipuzkoa.eus/inspire/download/buildings.xml), one whole-territory zip (a live WFS also exists — [`gipuzkoa_wfs_bu`](https://b5m.gipuzkoa.eus/inspire/wfs/gipuzkoa_wfs_bu) — kept in `gipuzkoa.py` as a fallback, not used for the actual crawl) | `pipelines/exposure/src/exposure/gipuzkoa.py` |
| Diputación Foral de Bizkaia | Vizcaya (province 48) | ArcGIS Server WFS (not INSPIRE): [`geo.bizkaia.eus/.../Katastro_Catastro_WFS/MapServer/WFSServer`](https://geo.bizkaia.eus/arcgisserverinspire/services/LurraldeAntolamendua_PlanificacionTerritorial/Katastro_Catastro_WFS/MapServer/WFSServer) | `pipelines/exposure/src/exposure/vizcaya.py` |

## Municipal boundaries

| Source | Coverage | Resource | Pipeline module |
|---|---|---|---|
| Instituto Geográfico Nacional (IGN/CNIG) | All of Spain, 4 admin levels (country/CCAA/province/municipio) | INSPIRE Administrative Units ATOM feed: [`lin_lim_mun.es.xml`](https://www.ign.es/atom/dataset_feeds/lin_lim_mun.es.xml), resolving to a direct GML download: [`lineas_limite_gml.zip`](https://centrodedescargas.cnig.es/CentroDescargas/documentos/atom/au/lineas_limite_gml.zip) (CC BY 4.0 ign.es) | `pipelines/exposure/src/exposure/municipalities.py` |

## Seismic hazard

| Source | Coverage | Resource | Pipeline module |
|---|---|---|---|
| Instituto Geológico y Minero de España (IGME) — QAFI v4 (Quaternary Active Faults of Iberia) | All of Spain, 201 active faults | Official shapefile download: [`QAFI_Traces.rar`](https://info.igme.es/qafi/docs/QAFI_Traces.rar) (see [ADR-0004](docs/decisions/0004-qafi-shapefile-source.md) for why this replaces an earlier ArcGIS MapServer REST approach) | `pipelines/faults/src/faults/source.py` |

## Fragility functions

| Source | Coverage | Resource | Pipeline module |
|---|---|---|---|
| Martins & Silva (2020), *Global Fragility and Vulnerability Functions* | 3 curated taxonomy classes (CR_LDUAL-DUL, MR_LWAL-DUL height classes 1–12 storeys; MUR-STRUB_LWAL-DNO vernacular rubble-stone masonry, height classes 1–5 only — that's all the source repo publishes for it) | GitHub repo, raw CSVs: [`global_fragility_vulnerability/fragility_curves/fragility_other_IMs`](https://raw.githubusercontent.com/lmartins88/global_fragility_vulnerability/master/fragility_curves/fragility_other_IMs) | `pipelines/fragility/src/fragility/source.py` |
