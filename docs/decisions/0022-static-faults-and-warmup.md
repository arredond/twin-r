# ADR-0022: Static faults.json on startup, fire-and-forget `/warmup`

Status: accepted

## Context

The frontend's first request on page load was `GET /faults` on the
scenario Lambda. Cold, that call was Init plus the request itself:

- **Init**: median 1.19s over 57 cold starts (2026-09-20 to 24), but
  6.7-7.7s for the first environments after a deploy or a long idle
  period, consistent with Lambda fetching the 1.8GB image into its cache
  from ECR. Our own module imports account for 0.2-0.5s natively.
- **The first request**: 2.0-3.5s, vs. 0.13s warm. The actual work is
  ~0.3s. The rest was DuckDB downloading httpfs (21MB) and spatial (81MB)
  into every new environment, now bundled in the image instead (ADR-0021).

The response itself is 201 faults, 456KB (177KB gzipped), and changes
only when `qafi_faults.parquet` does.

Separately, a new environment's first *scenario* request pays for
importing hazardlib and loading numba's cached functions. ADR-0021 cut that
from ~65s to a few seconds, but decided against doing it during Init:
Init has a hard 10s cap, and the same function serves `/faults` and
`/buildings`, which don't need hazardlib.

## Decision

**Serve the fault list as a static file.**
`python -m scenario.export_faults` writes exactly the `GET /faults` body,
using the same `faults.faults_payload` the route uses. It goes into
`apps/web/public/data/faults.json` locally, and gzipped into the public data
bucket at `tiles/faults.json` when deployed, next to the PMTiles
(docs/deploy-aws-setup.md). The frontend (`scenarioApi.listFaults`)
fetches it from the same place as the PMTiles (`staticData.staticDataUrl`,
factored out of DamageMap.tsx) and falls back to `GET /faults` if it's
missing. Opening the app no longer touches the scenario Lambda at all.

**Warm up a scenario environment while the user reads the map.** A new
`GET /warmup` route (`warmup.py`, in handler.py and local.py) imports
hazardlib, runs the same small ground-motion evaluation the image build
uses to fill the numba cache (`numba_cache.warm`), and loads the DuckDB
extensions. After the first call in an environment, it returns immediately.
The frontend calls it once on page load and never waits on it or reports
its errors (`scenarioApi.warmUpScenarioApi`).

Because it's an ordinary request, it runs under the function's own
120s timeout, not Init's 10s cap. It also only costs the call that asked for
it, not `/faults` or `/buildings`.

`faults_payload` now also returns missing values as `null`. pandas hands
them back as float NaN, which `json.dumps` wrote as a bare `NaN` that
browsers' JSON.parse rejects. No current QAFI fault has one, but a fault
without rupture geometry, which the code supports, would have broken the
list. The exporter additionally refuses to write any NaN.

## Alternatives considered

- **Keep `/faults` on the Lambda, just faster** (bundled extensions,
  ADR-0021): the post-deploy/idle Init spikes would still land on page load.
- **A separate lightweight Lambda for `/faults` and `/buildings`**: a
  small zip function would cold-start in well under a second, but it's a
  second function to deploy and keep in sync, for data that doesn't
  need to be computed per request at all.
- **Import hazardlib at Init instead of `/warmup`**: rejected in ADR-0021
  (10s cap; slows the routes that don't need it).
- **Provisioned concurrency**: pays for idle environments around the
  clock; too much for an MVP.

## Consequences

- One more manual step when fault data changes: re-export and re-upload
  `faults.json`, alongside `qafi_faults.parquet`. The frontend falls back
  to the API if the file is missing, but won't notice if it's stale.
- `/warmup` and the user's first scenario can still land on *different*
  environments: Lambda runs one request per environment, so a scenario
  started while `/warmup` is still running gets a second, cold one. The
  warm-up helps most when the user takes a few seconds to pick a fault,
  which is the normal case.
- Each page load costs one extra, short Lambda invocation (a few seconds
  on a fresh environment, milliseconds on a warm one).
- Verified locally (`bin/twiner start`): the static file is served in
  ~5ms and is identical to `GET /faults`. `/warmup` reports
  `already_warm: true` on the second call.

## Follow-up (2026-09-25): `/warmup` also runs the building query

On a fresh environment, even after `/warmup`, a scenario's first batch of
buildings took ~7.7s vs ~2.4s warm. That's S3 connection setup, both
parquet files' footers, and the first run of the join and Arrow
conversion. `/warmup` now also runs the scenario's own building query
(`engine.warm_site_query`, sharing `engine._query_sites` with the real
path) over a ~1km box in central Madrid. The connection enables DuckDB's
`parquet_metadata_cache` (db.py), so the parsed footers carry over to the
real query, alongside the byte ranges DuckDB's external file cache (on by
default) already keeps. Locally, the query takes 0.2s for 1,167 buildings,
and the cache setting is neutral on local disk; its benefit is over S3.
`/warmup`'s log line splits the numba/hazardlib time from the query time.
