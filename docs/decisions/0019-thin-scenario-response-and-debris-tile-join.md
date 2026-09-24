# ADR-0019: Thin scenario response, debris tile-join, backend-only results bucket

Status: accepted

## Context

After ADR-0017 moved building damage onto the map through a per-scenario
tile-join, the scenario response still carried `buildings`: every damaged
or genuinely uncertain building's id, damage code and five probabilities.
In the deployed stack, `handler.py` wrote that whole payload to the results
bucket and returned a presigned URL. The browser then downloaded it
directly from S3.

The payload can be very large. Measured on the national dataset, a
Peñacova-Régua-Verín (PO011) "very low" probability scenario evaluates
4,933,273 buildings and lists 351,807 of them: **62.7 MB** of JSON. That was
reported as reaching the frontend at around 50 MB in production.

Nothing needed it any more except debris. The frontend read the list for
three things only:

1. **Debris rings**: debris.pmtiles was never tile-joined (ADR-0017
   deferred it). The frontend `setFeatureState`d every listed building onto
   the debris source, in chunks, to pick each building's ring.
2. **Debris click popup**: checked the clicked ring against the list.
3. **Sidebar "N damaged"**: `buildings.length`.

Building colours and building popups already came from the joined tiles.

## Decision

**Debris is tile-joined server-side, like buildings.** New route
`GET /tiles/{scenario_id}/debris/{z}/{x}/{y}.mvt`, served by the tiles
Lambda (`services/tiles/handler.py`) and by `local.py` in dev.
`tiles.tile_join.join_debris_tile_bytes`:

- keeps only each listed building's **one** ring whose `ring` equals its
  `damage_state_code` (ring 1-4 = Slight-Complete, ADR-0010), and drops
  every other ring feature, including all rings of unlisted or "None"
  buildings;
- tags the kept ring with `damage_state_code`.

Dropping features rather than just tagging them means the joined tile is
a fraction of the base tile, not a superset of it. On a real z15 Lorca tile
with a third of its 2,099 buildings damaged: 257 KB → 29 KB gzipped, in
~7 ms. The frontend's debris layer is now a plain source swap to that
endpoint (like buildings) with a filter
`ring == damage_state_code`, which also hides every ring of the unjoined
static archive before any scenario has run.

**The scenario response carries no per-building data.** `buildings` is
gone. `n_damaged` is new: non-None buildings over the full evaluated set,
the same definition as the municipality stats, so the sidebar total and the
choropleth agree. What remains (rupture, evaluated_region, counts,
municipality_stats) is bounded by the ~8,200 municipalities. PO011 "very
low" is 401 KB raw / **31 KB gzipped**, down from 62.7 MB. It is returned
inline from the Lambda, well under the Function URL's 6 MB cap.

**The results bucket is backend-only.** The scenario Lambda writes the
per-building results (for the tile joins) and the response (as the
ADR-0018 cache entry, now `<scenario_id>/response.json` alongside the
other per-scenario files). The scenario Lambda (cache hits) and the tiles
Lambda (joins) read them. The presigned-URL path and the bucket's CORS rule
are removed. No browser request touches the bucket.

`API_VERSION` bumped to 2: the response shape changed, so version-1 cache
entries are never served.

## Alternatives considered

- **Client-side debris join from the joined building tiles**: read
  `damage_state_code` off buildings features as their tiles load and
  `setFeatureState` it onto the debris source by `building_id`. No new
  endpoint, but the two sources load independently, so every debris tile
  load needs re-syncing against whatever building tiles happen to be
  loaded. It still costs one `setFeatureState` per damaged building per
  source on the main thread, and still downloads all four rings of every
  building (≈9× the bytes of the filtered tile on the Lorca sample).
- **Keep the list, but serve it only for the viewport** (the "bbox-scoped
  delivery" option in docs/scenario-result-api.md): still a per-building
  client-side join, just smaller; the tile join already is viewport-scoped
  by construction.
- **Keep the presigned URL, drop only `buildings`**: pointless once the
  response fits inline, and it keeps the browser coupled to the bucket's
  layout, CORS and a 5-minute URL expiry.

## Consequences

- Debris tiles now cost a tiles-Lambda invocation per tile, like buildings
  tiles (previously a direct S3 range read of debris.pmtiles). ADR-0018's
  CloudFront recommendation covers both routes the same way: both are
  immutable per content-addressed `scenario_id`.
- ADR-0018's CloudFront blocker ("the response is a 5-minute presigned
  URL") no longer exists; `/scenarios/fault` can be cached as-is.
- The tiles Lambda reads `tiles/debris.pmtiles` (9 GB) from the data bucket
  by default key, the same object docs/deploy-aws-setup.md already uploads.
  It's read by range requests, as for buildings.
- Anything needing an individual building's damage outside the map (an
  export, a table) now has to go through a backend route; there is no
  client-side copy of the list any more.
