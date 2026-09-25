# ADR-0021: Scenario Lambda cold start -- prebuilt numba cache, bundled DuckDB extensions, hazardlib stays lazy

Status: accepted

## Context

After ADR-0020, the 2026-09-24 cache-warm sweep ran all 603 scenarios
without a failure, but the 8 requests that landed on brand-new execution
environments took 70-81s against 4-8s warm. Their CloudWatch logs had no
unusual `Init Duration` (~7s), and the handler's own stage timings
(import/compute/write) added up to ~6s. The missing ~65s was spent before
the compute step, building the rupture surface: `surface.py` is the first
thing that imports `openquake.hazardlib`.

That import compiles ~61 numba functions up front
(`openquake.baselib.performance.compile`), and a few more compile on first use.
numba caches compiled code on disk. But the stack points `NUMBA_CACHE_DIR` at
`/tmp/numba_cache`, which the image's read-only filesystem forces, and that
directory is empty in every new environment. So each container recompiled
from scratch. Locally, an empty cache costs 16-46s; a warm one 1.3s.

Separately, `db.py` ran `INSTALL httpfs` / `INSTALL spatial` at first use.
With `HOME=/tmp`, that meant downloading both from extensions.duckdb.org
into every new environment: latency on the first request, and a
third-party service on the request path.

## Decision

**Build numba's cache into the image** (`scenario/numba_cache.py`,
services/scenario/Dockerfile). A build step (`python -m
scenario.numba_cache`) runs a finite-fault and a point-source ground-motion
evaluation at both sigma levels, for every IM type. That compiles everything a
scenario compiles, into `/opt/numba_cache` (7.4 MB). `handler.py` calls
`numba_cache.seed()` during Init, which copies that cache into the writable
`NUMBA_CACHE_DIR`. Nothing imports numba before that point, which is
checked. `NUMBA_CPU_NAME=generic` is set at build and at runtime. numba
keys cache entries by CPU model, and the image is built on the machine
running `cdk deploy`, usually under amd64 emulation, not on Lambda's hosts.
With a generic entry, the cache matches on any CPU.

**Bundle the DuckDB extensions in the image.** The Dockerfile installs
httpfs and spatial for the exact installed DuckDB version into
`/opt/duckdb_extensions` (98 MB). `db.py` then opens the shared connection
with `extension_directory` pointing there and `autoinstall_known_extensions`
off, and only `LOAD`s. Local dev, where that directory isn't set, keeps
DuckDB's install-then-load.

**Keep hazardlib's import lazy** rather than moving it to module level
(Init). Moving it to Init is appealing: the idea is to "do everything once
at cold start, nothing at warm time". But here:

- Init is capped at 10s for on-demand functions. If it overruns, Lambda
  re-runs Init at the first invocation, counting against the function
  timeout. The scenario Lambda's Init already measures 6.7-7.7s in
  CloudWatch, mostly the container image and module imports. Even with the
  numba cache, hazardlib's import is the heaviest single step: 1.3s natively
  on a laptop, 11s under emulation, and unmeasured on Lambda, where the
  image is also lazy-loaded from ECR. Moving it to Init risks turning every
  cold start into a timed-out Init plus a full retry.
- Init is billed either way: container-image Init always was, and since
  August 2025 all functions' Init is. So there's no cost reason to prefer it.
- The same function serves `/faults` and `/buildings/{id}`, neither of
  which needs hazardlib. Loading it eagerly would slow exactly those cold
  starts, which ADR-0016 already reduced by deferring this import.
- AWS's own guidance is to lazy-load objects only some code paths use.

With the numba cache, the lazy import costs a few seconds once per
environment, not ~65s. The handler now logs `rupture Xs` (the step that
does the import) next to its other stage timings. If that turns out small
on real Lambda, moving it to Init can be revisited against measured Init
durations.

**The DuckDB connection stays created on first use and reused** (`db.py`,
unchanged). The pattern in the AWS builder article that prompted this, a
global connection reused across warm invocations, is what `db.py` already
does: a module-level database, one cursor per thread. It's created on first
use, not at import, because `local.py` and the tests import the same
module. Opening it costs milliseconds, so there's nothing to gain from Init.
The part of that article worth adopting was bundling the extensions, above.

## Alternatives considered

- **Eager hazardlib import at Init**: see above; revisit with real numbers.
- **Provisioned concurrency / SnapStart**: provisioned concurrency pays
  for idle, pre-initialized environments around the clock, too much for an MVP.
  SnapStart doesn't support container-image functions.
- **Build the cache directly into `/tmp`**: `/tmp` is a fresh, empty
  volume in every Lambda environment, not part of the image.
- **Point numba at the read-only image cache directly**: numba ignored
  a read-only cache directory in one local test (full recompile). Another
  test loaded fine from a read-only copy. The writable copy works in both
  cases and costs ~0.05s.

## Consequences

- Verified in the built image, run as in Lambda: read-only root, non-root
  user, only `/tmp` writable, **no network** (`--network none`). The
  extensions load from `/opt/duckdb_extensions`. Without them, the same
  run fails trying to download from extensions.duckdb.org. The first
  scenario request in a fresh container took 15.0s seeded vs. 151.7s
  unseeded (under amd64 emulation, so both are slower than on Lambda).
  Results were identical either way.
- The image build gains ~2.5 minutes (numba warm-up under emulation) and
  ~105 MB. The warm-up layer comes after the source `COPY`, so it re-runs on
  every code change.
- The cache is only valid for the exact openquake/numba/Python versions
  it was built against. A dependency bump without a rebuild can't happen,
  since they're built together. If an entry doesn't match, numba silently
  recompiles: slower, never wrong.
- No result changes, so `API_VERSION` stays at 3.

## Follow-up (2026-09-25): file timestamps don't survive deployment; size-only stamps

After deploying, a new environment's first scenario still took 64-73s in
the cache-warm sweep. Each logged `numba_cache: seeded ...` and then
`rupture 55.2s`; its next scenario, `rupture 0.0s`. The same image had used
the cache fine locally.

**How it was traced**:

1. CPU mismatch ruled out: with `NUMBA_CPU_NAME=generic`, numba also fixes
   the CPU features (to `""`), so the cache key doesn't depend on the host.
2. File timestamps were the next suspect. numba stamps each cache entry
   with its source file's `(mtime, size)`, and Lambda converts container
   images into its own block format. A first local test *appeared* to rule
   them out, but it was invalid: it changed mtimes with `find ... -exec
   touch`, and the Lambda base image has no `find`, so nothing was
   changed. The error was filtered out of the output.
3. A diagnostic shipped instead (still in place): per computed scenario,
   `handler.py` logs `hazardlib import Xs`, timed on its own, and `numba
   cache files written N` (`numba_cache.files_written_since_seed`: numba
   writes only on a miss). `/warmup` logs and returns the same count. In
   prod, a manual M9 scenario on a fresh environment logged `hazardlib
   import 54.5s ... numba cache files written 110`: a full recompile
   (a from-scratch compile writes ~106 locally), not slow image reads.
4. Redone correctly (Python `os.utime` on all 988 openquake `.py` files in
   the image), changed mtimes reproduce it: first import + rupture
   154.8s, vs. 12.0s untouched and 148.8s with no cache.

**Fix** (`numba_cache.use_size_only_source_stamps`): in the image only
(gated on `TWINER_NUMBA_CACHE_SEED`), numba stamps source files by size
alone. It applies at build, when the cache is written, and at Init, when
it's read, so both sides agree. That's safe because the image is immutable
after build. numba 0.61, pinned through hazardlib, has no supported hook
for this, so it patches the locator class used under `NUMBA_CACHE_DIR`;
later versions' `NUMBA_CACHE_LOCATOR_CLASSES` could replace it with
configuration.

Also fixed: `seed()` used to skip everything when `/tmp/numba_cache`
already existed. Lambda can re-run Init in an environment whose `/tmp`
survived, and one such environment's `/warmup` took 71s. It now always
applies the size-only stamps; only the copy is skipped.
