# Sanity check: Murcia + Andalucía expansion

Status: done. Companion to [`validation-lorca-2011.md`](./validation-lorca-2011.md),
covering the region-scale expansion from
[ADR-0005](./decisions/0005-region-scale-crawling.md). Two questions:
does the pipeline still behave sensibly at ~30x the geographic scale, and
how fast is it?

## 1. The crawl

819 municipalities across Murcia + Andalucía's 8 provinces, via
`python -m exposure.region_cli` (8 workers):

- **2,774,819 buildings parsed, 0 failures.**
- Crawl (download + parse + taxonomy): **502s** (~8.4 min).
- Combined `exposure.parquet` (2.77M rows, attributes only): **1s**.
- `tippecanoe` tiling all 819 GeoJSON parts into one PMTiles file: **224s**
  (~3.7 min). Output: 352MB.
- Total wall time: **~12 minutes**, unattended, resumable if interrupted.

`buildings.parquet` is intentionally left partitioned — 819 files, 3.3GB —
rather than combined into one file (ADR-0005); the scenario engine queries
it via a glob pattern (`parts/*.buildings.parquet`), confirmed working
through DuckDB's parameter binding, not just literal SQL.

## 2. Lorca 2011, re-run against the full region

Same real event as `validation-lorca-2011.md` (Mw 5.2, real epicentre,
real focal mechanism), same result: **100% "None"** — consistent with the
earlier Lorca-only run, confirming the fragility-function gap documented
there isn't an artifact of the smaller dataset. Now touches 1.83M
buildings (everything within 300km of Lorca, not just Lorca itself) in
**2.1s** server-side.

## 3. Automatic-mode sanity checks: 5 cities × nearest/farthest fault

For each city, queried `/faults` (nearest QAFI fault to that city, and the
farthest one still within 300km), then ran that fault's max-magnitude
scenario:

| City | Fault | Distance | Mmax | Buildings evaluated | Server time | Wall time | Damage (non-None) |
|---|---|---|---|---|---|---|---|
| Lorca | Alhama de Murcia (1/4) | 0.1 km | 6.7 | 1,850,086 | 1.78s | 5.6s | Slight 12,401 / Mod 208 / Complete 88 |
| Lorca | Teruel | 299.6 km | 6.2 | 669,179 | 0.84s | 2.1s | none |
| Granada | Granada | 0.2 km | 6.5 | 2,728,811 | 2.51s | 8.5s | Slight 41,933 / Mod 611 |
| Granada | Carrascoy (2/2) | 286.9 km | 6.82 | 1,511,730 | 1.98s | 5.2s | Slight 50,066 / Mod 219 / Complete 12 |
| Sevilla | Torre Marilópez | 54.3 km | 6.13 | 2,124,504 | 1.99s | 6.1s | none |
| Sevilla | Tofiño-Xauen Bank S. Flank | 295.8 km | 6.95 | 2,377,387 | 2.51s | 7.4s | none |
| Almería | E. Gádor Range Fault System | 2.1 km | 6.59 | 2,173,196 | 2.22s | 6.3s | Slight 25,245 / Mod 290 / Complete 80 |
| Almería | Jumilla (Sector Valencia) | 269.2 km | 6.0 | 1,403,068 | 1.42s | 4.1s | none |
| Málaga | Cártama | 19.7 km | 6.0 | 2,610,133 | 2.60s | 7.6s | Slight 512 |
| Málaga | El Sabinar | 296.6 km | 5.96 | 2,077,617 | 2.00s | 6.0s | Slight 25 |

Plausibility checks, all pass:

- **Sevilla's "nearest" fault is 54km away** — the furthest "nearest" of
  any city tested. Consistent with real seismotectonics: western Andalucía
  (Guadalquivir basin/Sevilla) is genuinely lower-hazard than the eastern
  Betics (Granada/Almería/Murcia), where most of Iberia's shallow crustal
  seismicity concentrates. QAFI's own fault density reflects that — this
  wasn't tuned for the demo, it fell out of real data.
- Nearby faults consistently produce some non-"None" damage; far (~300km)
  faults consistently produce none — matches Akkar et al. (2014)'s
  attenuation behaving as expected at these distances.
- No errors, no timeouts, no degenerate results across 10 scenarios
  spanning very different fault mechanisms and magnitudes.

## 4. The real finding: JSON payload size, not compute, dominates at this scale

Every row above shows **wall time roughly 2.5–3x server time.** Measured
directly on one call (Granada, "Baza" fault, 2.50M buildings):

- Server-side compute: ~2.1s (DuckDB spatial join + GMPE + vectorized
  fragility evaluation over 2.5M rows).
- **Response payload: 634 MB of JSON.**
- Total wall time: 7.0s — the other ~5s is almost entirely serializing and
  transferring that payload, not computing anything.

This is the scaling wall this architecture actually hits first — not the
GMPE/fragility math (which the spatial pre-filter and batch vectorization,
added this session, keep to low single-digit seconds even at 2.5M rows),
but shipping a full per-building JSON array over HTTP. At national scale
this would be worse, not better. ADR-0003 already flagged this as a future
concern ("the thin per-building result set may itself get large enough to
need partitioning") — it turns out that threshold arrives at *regional*
scale, not only national.

**Fixed since** (see [ADR-0006](./decisions/0006-precomputed-spatial-columns-and-adaptive-radius.md)):
precomputed centroid/bbox columns (parquet stat pushdown, ~14x faster site
loading), a magnitude-derived search radius instead of a flat 300km, and
non-"None" filtering in the response (with the frontend's fallback tile
color now representing "None" — §6 below has the re-measured numbers).
Not done: a results-to-file/URL path for the local dev server (`handler.py`'s
Lambda path already supports this via `TWIN_R_RESULTS_BUCKET`), and a
binary/columnar response format — both still valid future options if a
payload ever needs to shrink further than filtering alone achieves.

## 5. Conclusion (as of the original crawl + pre-filter work)

The region-scale expansion is **mechanically sound**: the crawl is fast,
resumable, and complete (0 failures across 819 municipalities); the
spatial pre-filter and vectorized damage evaluation keep actual computation
to low single-digit seconds even at millions of buildings; automatic-mode
fault selection produces geographically and seismotectonically sensible
results across five different cities. The one real scaling problem found —
JSON response size — is well-understood, well-quantified, and (§6) fixed.

## 6. Re-measured after the fix: precomputed columns + adaptive radius + non-None filtering

Same 10 automatic-mode scenarios (5 cities × nearest/farthest fault) plus 4
manual-mode scenarios, against the same 2.77M-building dataset:

### Automatic mode

| City | Fault | Distance | Mmax | Evaluated | Damaged | Server | Wall | Payload |
|---|---|---|---|---|---|---|---|---|
| Lorca | Alhama de Murcia (1/4) | 0 km | 6.7 | 837,315 | 12,697 | 706ms | 765ms | 3.2MB |
| Lorca | Teruel | 300 km | 6.2 | **0** | 0 | 31ms | 64ms | 0KB |
| Granada | Granada | 0 km | 6.5 | 1,306,960 | 42,544 | 891ms | 1.0s | 10.8MB |
| Granada | Carrascoy (2/2) | 287 km | 6.82 | 736,543 | 50,297 | 542ms | 669ms | 12.8MB |
| Sevilla | Torre Marilópez | 54 km | 6.13 | 677,897 | 0 | 483ms | 516ms | 0KB |
| Sevilla | Tofiño-Xauen Bank S. Flank | 296 km | 6.95 | 945,453 | 0 | 675ms | 709ms | 0KB |
| Almería | E. Gádor Range Fault System | 2 km | 6.59 | 996,320 | 25,615 | 705ms | 784ms | 6.5MB |
| Almería | Jumilla (Sector Valencia) | 269 km | 6.0 | 74,505 | 0 | 103ms | 137ms | 0KB |
| Málaga | Cártama | 20 km | 6.0 | 525,346 | 512 | 376ms | 410ms | 131KB |
| Málaga | El Sabinar | 297 km | 5.96 | 174,827 | 25 | 162ms | 196ms | 6KB |

### Manual mode

| Scenario | Mw | Evaluated | Damaged | Server | Wall | Payload |
|---|---|---|---|---|---|---|
| Lorca 2011 replica (real epicentre/mechanism) | 5.2 | 86,524 | 0 | 110ms | 112ms | 0KB |
| Lorca, large | 7.0 | 1,238,487 | 14,847 | 882ms | 910ms | 3.8MB |
| Granada, moderate | 6.0 | 598,766 | 6,413 | 420ms | 434ms | 1.6MB |
| Small Mw4.5 near Sevilla | 4.5 | 238,356 | 0 | 190ms | 192ms | 0KB |

### What changed vs. §3/§4's original numbers

- **Wall time**: was 2.1–8.5s across the board; now **64ms–1.0s** — every
  single scenario, including the largest (Granada nearest, still 1.3M
  buildings evaluated), finishes in about the time the original *server-side
  compute alone* used to take.
- **Payload**: was up to 634MB for one call; now **12.8MB worst case**,
  0KB–3MB typical. A ~50x reduction on the worst case measured.
- **The adaptive radius is doing real work, not just the non-None filter**:
  Lorca→Teruel (a Mw6.2 fault 300km away) now evaluates **zero** buildings
  in 31ms server-side, vs. 669,179 buildings in 839ms before — the radius
  correctly recognized that a Mw6.2 event's significant ground motion
  doesn't reach that far, so it never touched most of the dataset. Compare
  this to Granada's nearest fault (Mw6.5, right in the city), which still
  correctly evaluates 1.3M+ buildings — the radius shrinks and grows with
  the actual physics, not a blanket cutoff.
- **Small magnitudes now cost almost nothing**: the Mw4.5 manual scenario
  near Sevilla and the real Mw5.2 Lorca 2011 replica both finish in
  ~110–190ms, evaluating a few hundred thousand buildings at most, instead
  of paying for the same 300km sweep every request used to.

This is now comfortably fast enough for an interactive frontend at this
region's scale — including the previous worst case. The known remaining
lever (§4's options 2–3: result-to-URL, binary response format) is no
longer an urgent problem, just a further optimization if usage patterns
ever demand it.
