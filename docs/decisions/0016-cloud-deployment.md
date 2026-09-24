# ADR-0016: Cloud deployment -- AWS Lambda + S3 backend, Cloudflare Pages frontend

Status: accepted

## Context

ADR-0001 already picked Lambda + CDK for the scenario function and sketched
a stack (`infra/stacks/twin_r_stack.py`) -- data bucket, results bucket, one
container-image Lambda behind a Function URL -- but it was explicitly
"not yet `cdk synth`-tested against a real AWS account". Getting this ready
to actually deploy raised a few concrete questions:

- Where does the frontend live, and how does it reach the Lambda and the
  PMTiles data?
- Is the scenario function's DuckDB-over-S3 read pattern actually fast, or
  does something about how the data is laid out fight it?
- Does the Lambda need GDAL at runtime, and does that change how it's
  packaged?

## Decision

**Backend**: keep ADR-0001's shape -- one CDK stack, one container-image
Lambda behind a Function URL, no API Gateway (nothing here needs routing,
custom domains, or request transformation yet -- a Function URL is a
straight HTTPS endpoint in front of the same handler). CloudFront stays
deferred (see ADR-0001-adjacent milestone doc) -- we're still in
debugging/perf territory, and adding a cache in front of an endpoint you're
actively tuning just means invalidating it constantly.

**Data bucket is public-read + CORS-enabled**, not gated behind CloudFront
or presigned URLs. Everything in it is already-public government data
(Catastro/QAFI/IGN, see DATA-SOURCES.md) -- there's no confidentiality
reason to lock it down, and the frontend needs to range-GET PMTiles files
directly from the browser regardless of whatever the Lambda does.

**Buildings get a separate, spatially-sorted, single-file cloud artifact**
(`buildings-cloud.parquet`, built by
`pipelines/exposure/compact_cloud_cli.py`'s `compact_buildings_for_cloud`),
trimmed to just the columns `engine.py`'s scenario query actually reads
(building_id/centroid_lon/centroid_lat/municipality_code/vs30). The
per-municipality `parts/*.buildings.parquet` glob (ADR-0005) stays exactly
as it is for the ETL pipeline's own resumability -- this is an additional
build step before upload, not a replacement.

Why this matters: `parts/` is ~24,000 files today (one buildings + one
debris + one exposure part per Spanish municipality). Reading that as a
glob is free on local disk -- DuckDB opens thousands of small files in
milliseconds. Over S3, every file open is a network round trip; a glob read
against thousands of small objects is latency-bound, not data-bound, and
would dominate the scenario function's runtime regardless of how well the
column pruning (ADR-0006) works. `exposure.parquet` already sidesteps this
by being a single combined file; `buildings-cloud.parquet` does the same
for buildings, sorted by `centroid_lon, centroid_lat` so DuckDB's row-group
min/max stats cluster geographically -- a bounding-box scenario query then
range-GETs only the row groups that actually overlap it, one footer fetch
plus a handful of range reads, the same shape that made ADR-0006's
column-pruning fix ~14x faster, just paid over the network instead of disk.

**The Lambda's DuckDB connection is created once per process, not once per
request** (`services/scenario/src/scenario/db.py`). `engine.py`/`faults.py`/
`building_lookup.py` each used to call `duckdb.connect()` fresh on every
invocation. Locally that's free; in a warm Lambda execution environment it
throws away httpfs's connection pool for no reason -- a module-level
connection persists across warm invocations of the same environment, so
only a cold start pays the one-time httpfs/spatial-extension install and
TLS handshake cost.

**GDAL is needed at Lambda runtime, but only transitively and only via pip
wheels** -- `openquake.hazardlib.gsim.zhao_2016` does an unconditional
`import fiona` at module load (confirmed: removing `fiona` from
`services/scenario/pyproject.toml` breaks every scenario run, not just
`pipelines/exposure`'s own direct GDAL usage for crawling/parsing). This
doesn't change the packaging story -- `pip install fiona` on the Lambda's
Linux container pulls a manylinux wheel with GDAL bundled in, no
`yum install gdal` or system library step needed in the Dockerfile.

**Frontend**: Cloudflare Pages, built from `apps/web` (`npm run build`),
served at `twiner.arredon.do` via a CNAME onto Pages' subdomain (Cloudflare
manages the certificate automatically once the zone is on Cloudflare and
the CNAME is set). `VITE_SCENARIO_API_URL`/`VITE_*_PMTILES_URL` are Pages
build-time environment variables pointing at the Lambda Function URL and
the public data bucket, matching the existing `import.meta.env.VITE_*`
pattern `scenarioApi.ts`/`DamageMap.tsx` already use for local dev.
PMTiles/large parquet files are **not** bundled into the Pages deploy
(Cloudflare Pages caps individual assets at 25MB; `debris.pmtiles` alone is
9.26GB) -- they're fetched directly from the S3 data bucket at runtime,
which is exactly why that bucket needs to be public + CORS-enabled for the
Pages origin.

## Alternatives considered

- **API Gateway in front of the Lambda**: would add custom-domain routing
  and request validation, neither needed yet -- a Function URL is simpler
  and ADR-0001 already ruled this in favor of deferring it.
- **CloudFront in front of the data bucket now**: the user's own framing
  going in ("we're still in debugging + performance optimization
  territory") -- caching now would mean invalidating constantly while the
  data layout and Lambda tuning are still moving. Revisit once the backend
  is stable and traffic/cost justify it.
- **Keep `buildings.parquet` as the`parts/*.buildings.parquet` glob in the
  cloud too**: rejected -- see Decision above, this is the perf-critical
  fix for S3 latency dominating a glob read of thousands of small files.
- **Presigned URLs / a private data bucket**: unnecessary complexity for
  data that's already public by source (government cadastral/seismic
  feeds); public-read + CORS is the simplest thing that works.
- **Running pipelines in the cloud** (a batch job producing S3 data
  directly): explicitly out of scope per the user -- pipeline outputs are
  built locally and uploaded by hand (`aws s3 cp`/`sync`) for now.

## Consequences

- Someone (the crawl operator) has to remember to run
  `compact_cloud_cli.py` and re-upload `buildings-cloud.parquet` whenever
  `parts/` changes -- this is a manual step today, not wired into any CI/CD
  yet. Worth automating once the pipelines get any kind of cloud-triggered
  run.
- No caching layer means every scenario request and every PMTiles tile
  fetch goes straight to Lambda/S3 -- fine at MVP traffic, revisit
  (CloudFront, or a cache in front of the Function URL) once traffic or
  latency numbers say otherwise.
- The data bucket's public-read policy means anyone with the bucket name
  can read all pipeline outputs -- acceptable since none of it is
  sensitive, but this stack should not be reused as-is for a future bucket
  that *does* hold non-public data.
- `services/scenario/pyproject.toml` keeps `fiona` as a direct dependency
  (not just a transitive one openquake happens to need) so this constraint
  is visible and pinned rather than rediscovered by a future dependency
  bump breaking gsim imports silently.
