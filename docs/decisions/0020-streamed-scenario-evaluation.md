# ADR-0020: Streamed scenario evaluation on one fixed ground-motion grid

Status: accepted

## Context

The first full `bin/warm-scenario-cache` sweep against the deployed stack
(ADR-0018; 201 faults × 3 probability levels) failed 49 of 603 scenarios
with HTTP 502, across 26 faults: 48 at `low`/`very_low`, one at `high`.
CloudWatch showed why: 164 `Runtime.OutOfMemory` invocations and 15
timeouts, all at `Max Memory Used: 3007 MB` of the Lambda's 3,008 MB.
Successful runs peaked at up to 2,869 MB.

The failures follow **how many buildings a scenario evaluates**, not fault
length. Enriching each scenario with its fault geometry and the building
count in its search box (Spearman correlation with request time, successful
runs):

| Metric | ρ |
|---|---|
| buildings evaluated | 0.92 |
| occupied 1 km cells | 0.90 |
| search radius | 0.50 |
| magnitude | 0.32 |
| fault length | 0.27 |
| rupture mesh points | 0.11 |

No scenario under 4.58M buildings failed; above 5.0M, 40 of 41 did; in
between it depended on the attempt. `low`/`very_low` use median+1σ ground
motion (ADR-0011), which pushes the search radius to its 300 km cap for
any magnitude above ~6.6, so those levels evaluate 4.5-6.2M buildings where
the same fault at `high` evaluates 1-2M. The short faults that failed
(Concud, 14.5 km; Vicort, 15.5 km) sit under dense parts of Spain.

Profiling ES412 (Concud) `low`, 6.25M buildings, locally: 3.3 GB peak
RSS. Most of it was `_load_sites`' `.df()` turning the DuckDB result into
one DataFrame. The resulting frame is only ~520 MB (pandas 3 already keeps
these strings Arrow-backed). The rest was DuckDB's own conversion buffers.
Fetching the same rows with `fetch_arrow_table().to_pandas()` cut that stage
from +2.7 GB to +0.8 GB, but the peak then just moved to
`evaluate_damage_batch`'s per-group temporaries and the municipality
groupby. Neither the exposure join, `SET threads`, `memory_limit` nor a
denormalized single-file layout changed the peak.

## Decision

**Stream the scenario instead of materializing it.** `engine._site_batches`
reads the bounding-box query as Arrow record batches (`to_arrow_reader`,
`SITE_BATCH_ROWS = 250_000`). `engine.summarize_scenario`, now what
`handler.py` and `local.py` call, runs ground motion and damage per
batch, then keeps only:

- the evaluated count;
- per-municipality damage-state counts (`response.MunicipalityCounter`:
  dictionary-encoded codes + `np.bincount`);
- the thin rows the tile joins list (`response.shipped_buildings_table`,
  the same damaged-or-uncertain rule as before).

The per-building hot path is pyarrow + numpy only: no per-building Python
strings, no per-building DataFrame. `damage.evaluate_damage_arrays` groups
by (taxonomy, height) via dictionary encoding and one argsort and
returns integer damage codes. pandas stays a dependency:
`openquake.hazardlib` imports it anyway, and the small cold paths (fault
list, fragility table, building lookup) keep using it.

**One fixed ground-motion grid per scenario** (`ground_motion.GriddedIntensity`).
Cells are sized from the search box's center latitude, fixed up front,
instead of from the mean latitude of whichever sites are passed in. So a
cell is the same cell whichever batch reaches it. Each cell is evaluated
once, when the first batch reaches it, and cached for later batches.
The Rjb distance calculation, ~95% of ground-motion time (ADR-0009), now
runs once per cell and is shared by all four IM types. Before, it ran once
per IM type.

`run_scenario` (every building, as a DataFrame) is kept for the tests and
CLI and is built on the same batch chain, so the two can't drift.
`API_VERSION` → 3.

Measured locally, old → new, same inputs:

| Scenario | Buildings | Peak RSS | Time | n_damaged |
|---|---|---|---|---|
| ES412 low | 6,246,892 | 3,838 → 1,338 MB | 5.9 → 1.6s | 17,217 → 17,110 |
| ES603 high | 5,008,928 | 3,677 → 1,229 MB | 4.2 → 1.4s | 47,967 → 48,379 |
| PO007 very_low | 5,389,425 | 3,479 → 1,545 MB | 6.0 → 1.7s | 129,936 → 128,677 |
| ME025 high | 3,046,675 | 2,820 → 1,179 MB | 3.2 → 1.2s | 248,022 → 248,711 |
| ES412 high | 1,715,262 | 1,799 → 818 MB | 1.6 → 0.6s | 8,633 → 8,715 |

Per-municipality `n_evaluated` is identical in every case. The ±1% in
`n_damaged` comes only from the moved grid anchor. Running the new code with
the old anchor (mean building latitude) and a single batch reproduces the
old `n_damaged` exactly for ES412 low/high and ES603 high.

## Alternatives considered

- **Only switch `.df()` to Arrow**: identical frame and 3× faster load, but
  the peak just moves to later stages; not enough headroom under 3 GB.
- **Raise Lambda memory** (up to 10 GB): would get these scenarios
  through today, but memory would still grow with the building count, and
  more memory costs more on every invocation. Still available as extra
  headroom.
- **Two passes (numeric-only columns for ground motion, then the rest)**:
  would keep the old grid anchor exactly, but reads the box from S3 twice
  and depends on two queries returning rows in the same order.
- **Skip buildings that are certainly undamaged before full evaluation**:
  only ~20% of evaluated buildings have `prob_none == 1` (the fragility
  curves' low tails reach far out), so it's a small win for real
  complexity.
- **Denormalize taxonomy/height onto `buildings-cloud.parquet`**: no
  measured memory effect locally. May still save S3 reads of
  `exposure.parquet` in the cloud (docs/known-issues-cloud-deploy.md);
  unmeasured there, so not done.

## Consequences

- Peak memory now depends on the batch size, not the building count. A 10M-building
  scenario would fit where 5M didn't. `SITE_BATCH_ROWS` is the knob:
  1M-row batches measured ~2.2 GB peak on ES412 low.
- Results shift slightly (API_VERSION 3), so the whole cache needs
  re-warming after deploy (`bin/warm-scenario-cache`).
- ADR-0018's run-to-run Vs30 nondeterminism is unchanged. A cell still takes
  the Vs30 of the first site that reaches it, which depends on DuckDB's row
  order.
- `handler.py` now logs per-stage timings (`import`, `first batch`,
  `compute`, `write`) per computed scenario. The same sweep showed the
  *first* scenario request in a fresh execution environment taking
  60-85s vs. 2-20s warm, without an `Init Duration` to explain it (the
  hazardlib/engine imports are deferred into the handler, and httpfs is
  installed on first use rather than baked into the image). Locally the
  import is 1.3s, so this is specific to the Lambda environment. The new
  log line should show which stage it is.
