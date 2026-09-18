# Sanity check: the 2011 Lorca earthquake (milestone-1 plan, task 9)

Status: done. This is a plausibility check, not a calibration exercise —
we have no access to MERISUR's own Lorca-specific capacity curves (see
[`merisur.md`](./merisur.md) §4.4), so an exact match was never the bar.
The bar was: does the pipeline behave sensibly end-to-end on a real,
well-documented event? It does, mechanically — and it surfaces one
substantive, well-understood gap worth fixing before this MVP is used for
anything beyond a demo.

## 1. The real event

- **Mw 5.2**, 11 May 2011, on the Alhama de Murcia fault.
- Epicentre **37.699°N, 1.672°W** (BGS/Wikipedia), depth ~1–4.6 km
  depending on source/method (unusually shallow).
- Focal mechanism (moment tensor): oblique-reverse, **strike 240°, dip 54°,
  rake 44°**.
- 9 fatalities, almost all from falling non-structural building elements,
  not structural collapse — the reason MERISUR built a debris model at all
  (`merisur.md` §2).
- Of **6,416 buildings inspected**, EMS-98 grading came back: 4,035 at
  grade 2 (slight), 1,328 at grade 3 (moderate), 689 at grade 4
  (substantial/heavy), 329 at grade 5 (destruction) — i.e. essentially every
  *inspected* building had at least slight damage (inspection targeted
  reported-damaged buildings, not a full census, so this isn't directly "X%
  of Lorca's building stock" — see caveats below).

Sources: [2011 Lorca earthquake — Wikipedia](https://en.wikipedia.org/wiki/2011_Lorca_earthquake),
[BGS event page](https://www.earthquakes.bgs.ac.uk/research/events/LorcaSpainMay2011.html),
source-parameter studies cited there for the focal mechanism.

## 2. What we ran

```
uv run python -m scenario 37.699 -1.672 5.2 44
```

(manual mode; automatic mode independently confirmed the Alhama de Murcia
fault comes back as the nearest QAFI fault to Lorca — see
`services/scenario/tests/test_faults.py`.)

**Result: 27,884/27,884 buildings predicted "None".**

## 3. Is the pipeline broken? No — traced end-to-end

1. **Fault lookup**: automatic mode correctly identifies "Alhama de Murcia"
   as the nearest QAFI fault to Lorca (83 m from the town centroid) — the
   real causative fault. ✅
2. **Ground motion**: at the town centre, the Akkar et al. (2014) GMPE
   returns **SA(0.3s) ≈ 0.18–0.22 g** for this rupture. Real near-fault
   stations recorded PGA up to ~0.36 g in 2011 — same order of magnitude,
   plausible for a shallow Mw 5.2 event at ~3 km. Not the problem. ✅
3. **Fragility evaluation**: at SA(0.3s) = 0.2 g, our vendored masonry class
   (`MR_LWAL-DUL`, Martins & Silva 2020) gives **P(exceed Slight) ≈ 9–11%**
   — meaning "None" is still the single most likely (modal) state for every
   building, even though a real minority would exceed Slight. This is where
   the mismatch actually is.

## 4. Root cause: generic global fragility functions understate Lorca's real vulnerability

`MR_LWAL-DUL` ("masonry, load-bearing wall, low ductility") is a broad,
globally-averaged class. Lorca's historic old town is largely older,
un-engineered rubble/adobe-type masonry with exactly the defects MERISUR's
own field campaigns documented (soft storeys, height irregularities, heavy
unrestrained non-structural elements — `merisur.md` §4.5/§4.9) — a much
weaker building type than "generic masonry."

Martins & Silva's repository has narrower, more specific masonry classes,
e.g. `MUR-STRUB` (unreinforced stone rubble masonry, no ductility). Checked
directly against the same repository (not yet vendored into
`pipelines/fragility`):

| Intensity | `MR_LWAL-DUL` P(≥Slight) | `MUR-STRUB` P(≥Slight) | `MUR-STRUB` P(≥Moderate) |
|---|---|---|---|
| ~0.20 g | ~9–11% | **41%** | 3.8% |
| ~0.36 g (recorded PGA) | — | **84%** | **29%** |

`MUR-STRUB` at the recorded PGA is far more consistent with what actually
happened (a large share of buildings at Slight/Moderate or worse) than the
generic class we currently default old buildings to. Note the IM type
differs (`MUR-STRUB` is indexed by PGA, `MR_LWAL-DUL` by SA(0.3s)) — our
current `ground_motion.py`/`fragility_lookup.py` only handle one IM type at
a time, so adopting this isn't a one-line swap (see recommendations).

## 5. Other simplifications that also contribute (smaller effect, not investigated in depth)

- **Point-source Rjb approximation** (`rupture.py`): treats the rupture as
  a point rather than an extended fault plane. For a shallow, near-field
  event this can meaningfully under- or over-state motion depending on
  geometry; not separately isolated here.
- **No site amplification** (`ground_motion.py`, `DEFAULT_VS30 = 800`):
  Lorca sits on a sedimentary basin with documented amplification (Navarro
  et al. 2014, `merisur.md` §4.3) — real motion at soft-soil sites in town
  was very likely higher than our flat reference-rock assumption gives.
  This would push our numbers *toward* reality, not away from it.
- **Taxonomy heuristic** (`pipelines/exposure/src/exposure/taxonomy.py`):
  a single 1970 year threshold, no seismic-code-era gradation. Reasonable
  as a first cut, but coarser than the "construction-year → code generation"
  chain sketched in `initial-chatgpt.md`.

## 6. Automatic-mode Mmax accuracy: a second, separate investigation

The 2011 event also gave us a chance to check the **automatic** (fault-picker)
mode specifically, since MERISUR's own UI reports Mmax = 6.9 for the fault
responsible ("Alhama de Murcia (1/4)"). Our pipeline originally computed
**7.38** for the same-named fault — traced to a bad data source, not a
formula bug:

- `twin-r`'s faults pipeline originally queried IGME's public ArcGIS
  MapServer REST layer, which only exposes 8 basic fields (no length, no
  Mmax) and whose *geometry* for "Alhama de Murcia (1/4)" turned out to be
  **~96 km — over 3x** the fault's official length.
- Downloading and inspecting QAFI v4's actual shapefile
  (`QAFI_Traces.rar`) directly showed the official record: `Length = 30 km`,
  `MaxMagnitu = 6.7` (range 6.4–7.0, citing Ortuño et al. 2012 and
  Martínez-Díaz et al. 2012) — consistent with MERISUR's 6.9, and **already
  published**, not something we need to estimate at all for this fault.
  60% of QAFI's 201 faults have a similarly published Mmax.

`twin-r` now sources faults from the official shapefile
([ADR-0004](./decisions/0004-qafi-shapefile-source.md)), uses the published
Mmax where available, and only falls back to a length-based estimate (on
the correct official `Length` field) for the remainder. Full trace of this
investigation in `merisur.md` §4.1 and §7.

## 7. Conclusion

The scenario engine is **mechanically sound and internally consistent** —
correct fault selection, plausible ground motion, correct fragility-curve
evaluation and damage-state aggregation, verified against a real, documented
event. The gap is a **fragility-function selection issue**, not a bug: the
generic global masonry class we defaulted to for MVP speed understates the
vulnerability of Lorca's actual pre-code masonry stock.

## 8. Recommendations (not done here — out of scope for "sanity check")

1. **Near-term, cheap**: vendor `MUR-STRUB` (and/or `MUR-ADO`, `MUR-CB99`,
   `MUR-CL99`) alongside `MR_LWAL-DUL` in `pipelines/fragility`, refine the
   taxonomy heuristic to pick a vernacular-masonry class for pre-code
   construction (say, pre-1940 or pre-1960) vs. the generic class for
   1940s–1970s masonry, and extend `ground_motion.py`/`fragility_lookup.py`
   to carry an intensity-measure type per taxonomy class instead of
   assuming SA(0.3s) everywhere.
2. **Longer-term**: per the UPM collaboration (this project has a direct
   line to UPM for MERISUR's original data/papers — see project memory),
   ask whether the actual Lorca capacity/fragility curves used by MERISUR
   itself can be shared, even for validation purposes only. That would let
   us replace this whole section with a real calibration exercise instead
   of a plausibility check. Written up as question 2 in
   [`questions-for-upm.md`](./questions-for-upm.md), alongside the soil
   microzonation (question 3). The QAFI Mmax question originally listed
   here (question 1) turned out to be answerable ourselves — see §6 above
   — so it's been removed from that doc.
3. Once (1) or (2) lands, rerun this exact scenario and update this
   document with the new distribution.

## 9. Addendum: re-run with the finite rupture surface (ADR-0007)

After building a real rupture plane for automatic-mode faults
(docs/decisions/0007-finite-rupture-surface.md) instead of the point-source
approximation, re-ran the same Alhama de Murcia (1/4) scenario from §6
(live API, `finite_rupture: true` confirmed in the response):

| | Point-source (§6/§3) | Finite surface (this section) |
|---|---|---|
| Evaluated | 837,315 | 838,051 |
| Damaged | 12,697 | 10,341 |
| Slight | 12,401 | 10,329 |
| Moderate | 208 | 11 |
| Complete | 88 | 1 |

**Total and severe damage went down, not up** — worth explaining, since
"a more physically correct model shows a fault is more dangerous close up"
is the naive expectation. What's actually happening: the old point-source
rupture was anchored at a single point (the trace point nearest Lorca), and
treated "close to that point" as isotropic — equally near in every
direction, including perpendicular to the fault, where the real rupture
plane doesn't extend at all. The finite surface confines "close to the
rupture" to a narrow band along the fault's actual 30km strike direction.
Buildings that happened to sit near the anchor point but off to the side of
the true fault orientation were getting inflated proximity (and thus
inflated damage) from the point-source approximation — exactly the kind of
error a real geometry fixes, just not in the direction intuition suggests.
This is a plausible, explainable shift, not a red flag — but it's a
concrete illustration of why "point near a fault" and "point on a fault's
rupture plane" aren't the same question.

## 10. Addendum: exposure/typology follow-up, and a second bug it uncovered

Re-ran the §2 scenario (`uv run python -m scenario 37.699 -1.672 5.2 44`)
against a freshly-built Lorca dataset — still **27,884/27,884 "None"**, §2's
finding holds unchanged. This addendum digs into the exposure/typology angle
flagged as the likely dominant contributor (§8 recommendation 1) with
actual numbers, and surfaces a second, independent bug along the way.

### 10.1 `MR_LWAL-DUL` isn't just generic — it's the *least* vulnerable
masonry option on offer

Pulled every vernacular-masonry class the same vendor repo (Martins & Silva
2020) publishes and compared `P(≥Slight)` at a fixed SA(0.3s)=0.15g, H2:

| Class | Description | P(≥Slight)@0.15g |
|---|---|---|
| `MUR-ADO_LWAL-DNO` | adobe | 17.5% |
| `MUR-STRUB_LWAL-DNO` | rubble stone, no ductility | 14.6% |
| `MUR-STDRE_LWAL-DNO` | dressed stone, no ductility | 13.7% |
| `MUR-CL99_LWAL-DNO` | confined masonry (pre-'99 code) | 8.9% |
| `MUR-CB99_LWAL-DNO` | confined block masonry (pre-'99 code) | 5.3% |
| **`MR_LWAL-DUL`** | **generic masonry (our current default)** | **3.7%** |

Every one of Risk-UE's five Lorca masonry sub-types (`merisur.md` §4.5) is
almost certainly closer to one of the top rows than to our catch-all
default — `MR_LWAL-DUL` isn't merely coarse, it's specifically the most
optimistic choice available. `pipelines/exposure/buildings.parquet` shows
**12,250/27,884 buildings (44%)** currently get this class, of which
**3,149 predate 1940** — old-town-era construction most plausibly matching
`MUR-STRUB` (Lorca's historic core is stone masonry, not adobe).

### 10.2 New bug found while instrumenting this: IM type isn't tracked per curve

`data/fragility/fragility.parquet` (same Martins & Silva vendor set already
in use) mixes intensity-measure types **within** a single taxonomy class,
by height:

| `taxonomy` | `height_class` | `im_type` |
|---|---|---|
| `MR_LWAL-DUL` / `CR_LDUAL-DUL` | 1 | `PGA [g]` |
| `MR_LWAL-DUL` (2–3) / `CR_LDUAL-DUL` (2–4) | | `SA(0.3s) [g]` |
| taller | | `SA(0.6s) [g]`, then `SA(1.0s) [g]` |

`ground_motion.py` computes **only** SA(0.3s) (`INTENSITY_MEASURE = SA(0.3)`,
hardcoded), and `engine.py`/`damage.py` feed that single value into
whichever curve a building's `(taxonomy, height)` resolves to, with no check
of `im_type` at all. **Height-class-1 buildings — 44% of Lorca's stock — are
being evaluated against a PGA-indexed curve using an SA(0.3s) value**, and
buildings with `height_class ≥ 5` (SA(0.6s)/SA(1.0s)-indexed) have the same
problem. This is a real correctness bug, independent of the typology
question, present for every scenario run today, not Lorca-specific.

Its effect isn't one-directional: at Lorca's town centre, Akkar et al.
(2014) gives SA(0.3s)=0.218g vs. PGA=0.172g for this rupture — since SA(0.3s)
is *larger* here, feeding it into a PGA-indexed curve currently
**over-states** H1 damage, not under-states it (verified directly against
`MR_LWAL-DUL_H1.csv`: this is coincidentally why §3's "9–11%" figure came out
matching the vendored data as read today). Whether that direction holds at
other magnitudes/distances/mechanisms isn't something to assume — this
needs fixing on its own correctness merits, not because it's making Lorca
better or worse.

### 10.3 Quantified experiment (standalone script, not merged — see below)

Built a scratch harness reusing `damage.py`'s exact probability math, run
against the real 27,884-building Lorca exposure set, comparing **expected
`≥Slight` count** (Σ P(exceed Slight) across all buildings — the right
metric here; see caveat below) across four variants:

| Variant | Taxonomy | IM type | Expected ≥Slight (of 27,884) |
|---|---|---|---|
| A — baseline (today's code) | 2 classes | always SA(0.3s) | **977** |
| B — IM-type fix only | 2 classes | correct per curve | 626 |
| C — typology fix only (pre-1940 masonry → `MUR-STRUB`) | 3 classes | always SA(0.3s) | **1,281** |
| D — both fixes together | 3 classes | correct per curve | 854 |

Two takeaways:

1. **The typology split alone moves things the right direction, substantially**
   (+31% vs. baseline) — confirms §8 recommendation 1's hypothesis with a
   number, not just a plausibility argument.
2. **Fixing the IM-type bug *without* the typology fix moves the wrong way**
   (977 → 626), and even fixing both together nets *below* baseline
   (854 vs. 977) — the two bugs are currently offsetting each other by
   coincidence, not by design. Shipping the IM-type fix alone, as a "pure
   correctness" change with no typology work attached, would look like a
   regression against this validation case even though it's fixing a real
   bug. **Don't land one without the other.**

**Caveat on "expected count" as the metric**: modal-damage-state counting
(what §2's headline number and the CLI both report) is structurally unable
to show partial damage here — flipping a building's *modal* state to Slight
needs `P(≥Slight) > 50%`, but real 2011 damage was concentrated in a
minority of the municipality's full building stock (6,416 *inspected*,
flagged as damaged, out of 27,884 total — §1), so most individual buildings
legitimately have `P(≥Slight)` well under 50% even in a correctly-calibrated
model. None of variants A–D ever produce a nonzero modal-state count at the
whole-municipality scale for exactly this reason. Expected-value counting
(Σ probability) is what actually reflects "how much of the stock plausibly
took some damage," and is a better validation metric than the modal-count
headline this doc has used so far — worth adopting alongside (not instead
of) the modal count in future validation runs.

### 10.4 Recommendations, superseding §8's recommendation 1 — 1–3 done

1. ~~**Fix the IM-type bug** (`ground_motion.py`/`fragility_lookup.py`/
   `damage.py`/`engine.py`): compute PGA, SA(0.3s), SA(0.6s), SA(1.0s) from
   the same Akkar et al. (2014) GMPE (it already supports all four —
   confirmed directly), tag each vendored curve with its `im_type`
   (already a column in `fragility.parquet`, just unused downstream), and
   dispatch each building to the IM value matching its own curve. Same GMPE,
   no new dependency — this is a correctness fix that's overdue regardless
   of Lorca.~~ Done — see [ADR-0012](./decisions/0012-im-type-dispatch-and-vernacular-masonry-taxonomy.md).
2. ~~**Vendor `MUR-STRUB_LWAL-DNO`** (H1–H5, comfortably covers 99.95% of
   Lorca's masonry stock by height) alongside the existing two classes in
   `pipelines/fragility`, and extend `taxonomy.py`'s single 1970 threshold to
   a second one (pre-1940 → `MUR-STRUB`, 1940–1970 → `MR_LWAL-DUL`, ≥1970 →
   `CR_LDUAL-DUL`) — exactly the refinement `taxonomy.py`'s own docstring
   already anticipates ("refine into multiple periods once we have reason to
   believe it changes results materially" — §10.3 is that reason, with a
   number attached).~~ Done — see ADR-0012 and
   [`docs/TAXONOMY.md`](./TAXONOMY.md) for the full class breakdown and
   what would sharpen the 1940 threshold further.
3. ~~**Land (1) and (2) together**, and re-run this exact scenario afterward —
   §10.3 shows why doing only one is actively misleading.~~ Done — see
   §10.6 below for the re-run.
4. Real Lorca capacity curves from UPM (`questions-for-upm.md` §1) remain
   the only path to an actual calibration rather than a plausibility check —
   unchanged from §8 recommendation 2. A related, narrower question (does
   our masonry-era split match reality, or Lorca's field-surveyed Risk-UE
   mix?) has been added to `questions-for-upm.md` §4.
5. Out of scope here, deliberately: site amplification (blocked on real
   microzonation data, `questions-for-upm.md` §2) and the debris model (the
   Lorca-specific debris dataset/pipeline was left untouched, per explicit
   instruction). Note for later, at no cost to that constraint: debris rings
   are derived purely from `damage_state` at render time
   (`pipelines/exposure/src/exposure/debris.py`), so fixing (1)/(2) improved
   debris-layer fidelity for free, with no change to the debris pipeline
   itself.

### 10.6 Re-run with both fixes shipped (ADR-0012)

Re-ran this exact scenario end-to-end through the real (now-shipped) code
path, not the §10.3 scratch harness — three taxonomy classes, correct
per-building IM dispatch, at all three probability levels (ADR-0011):

| Tier | Expected ≥Slight (Σ P, of 27,884) | Modal-state counts |
|---|---|---|
| High (median GM) | **983** | 27,884 None (unchanged — see §10.3's caveat, still applies) |
| Low (median+1σ GM) | 5,087 | 25,742 None, 2,142 Slight |
| Very low (median+1σ GM, P85 damage) | 5,087 | 16,460 None, 9,944 Slight, 1,480 Moderate |

The expected-value number at the original "high" tier (983) now lands
essentially *at* the original all-bugs baseline (977, §10.3's variant A) —
not below it, as the earlier scratch estimate for "both fixes together"
(854) suggested. The difference from that estimate comes from this being
the real shipped taxonomy split (1940 threshold, unknown-year buildings
conservatively assigned to `MUR-STRUB_LWAL-DNO` — 5,870 buildings, vs. the
scratch experiment's cruder pre-1945/unknown handling) and the real
per-building IM dispatch (including H4/H5 buildings' SA(0.6s)/SA(1.0s)
curves, which the scratch harness didn't isolate). Combined with the
probability-level tiers (§10.5/ADR-0011), "low" and "very low" now produce
a qualitatively different, non-degenerate picture — thousands of buildings
showing Slight/Moderate damage, rather than zero at every tier as the
original §2/§3 baseline did.

This remains a plausibility check, not a calibration exercise — §1's
caveat about the 6,416 *inspected* buildings not being directly comparable
to a full-stock modal or expected-value count still applies, and real
Lorca capacity curves (`questions-for-upm.md` §1) remain the only path to
an actual calibration.

### 10.7 Addendum: national Vs30 site amplification (ADR-0015)

`ground_motion.py`'s `DEFAULT_VS30 = 800` flat-rock assumption — flagged
in §5 as a simplification that "would push our numbers *toward* reality,
not away from it" — is now replaced with a real per-building value from
ESRM20's national Vs30 grid ([ADR-0015](./decisions/0015-eshm20-site-amplification.md)),
backfilled onto all 13,013,185 buildings in the national dataset (8,141
parts, 120s; 513,075 buildings — ~3.9% — fell outside the grid's coverage
and fall back to `DEFAULT_VS30` via `COALESCE`, concentrated at
coastal/edge locations; Ceuta fully covered, Melilla ~1.3% null).

Re-ran this section's exact scenario end-to-end against the real,
now-backfilled data (`uv run python -m scenario 37.699 -1.672 5.2 44`,
"high" tier, both taxonomy/IM-type fixes from §10.6 already shipped):

| | Flat Vs30=800 (§10.6, "high") | Real per-building Vs30 (this section) |
|---|---|---|
| Buildings evaluated | 27,884 | 86,705 (wider evaluation radius: real Vs30 raises ground motion at range, so `estimate_significant_distance_km`'s magnitude-derived search radius, ADR-0006, now also reaches further before decaying below the significance threshold) |
| Expected ≥Slight (Σ P) | 983 | **2,165** (+120%, restricted to the same 27,884-building footprint as §10.6's comparison would show an even larger relative jump, since the extra 58,821 buildings are Rjb-distant, low-probability additions that pull the average down, not up) |
| Modal-state counts | 27,884 None | 86,705 None (unchanged — §10.3's caveat about modal thresholds at "high"/median ground motion still applies; this pushes probabilities up, not (yet) past the 50% modal threshold) |

Confirms the direction predicted back in §5: real site conditions at
Lorca's town centre (Vs30 ≈ 383 m/s, EC8 class D) versus the old flat
class-A/B default raise SA(0.3s) by ~55% for this exact rupture (0.191g →
0.297g — see ADR-0015's own verification), and that increase propagates
through to a large jump in expected damage. Doesn't flip modal counts at
the "high" tier by itself — combining this with the "low"/"very_low"
tiers (§10.5/ADR-0011) is the next natural check, left for a future
re-run.

### 10.5 A third lever, found independently: the missing probability-level
dimension, and how well it happens to fit this specific event

§10.1–10.4 treat ground motion as fixed and vary only exposure/fragility.
But `merisur.md` §4.7 documents that MERISUR's own UI exposes **three
selectable scenario levels**, and the ground-motion percentile is one of the
two things that changes between them, not just damage percentile:

| Level | Ground motion | Damage |
|---|---|---|
| High probability | median | modal damage state |
| Low probability / high impact | median + 1σ | modal damage state |
| Very low probability / very high impact | median + 1σ | 85th-percentile damage state |

`twin-r` implements none of this — `ground_motion.py` always computes the
GMPE's bare median (`np.exp(mean[0])`, `compute_sa03`/`compute_sa03_gridded`
in `ground_motion.py`), there's no `sigma`/percentile parameter anywhere in
`rupture.py`, `engine.py`, `handler.py`, or `local.py`, and the CLI used for
this whole validation (`python -m scenario`) has no way to ask for anything
but the median. Every run in this doc, §1 through §10.4, is implicitly
"High probability" tier only.

Checked what MERISUR's own **"Low probability / high impact"** tier would
give for this exact rupture, using the same Akkar et al. (2014) GMPE
already wired up (`AkkarEtAlRjb2014`, which reports `sig` alongside `mean`
— no new dependency, no new data, this is entirely unused output from a
call the engine already makes):

| | Median (today's only mode) | Median + 1σ |
|---|---|---|
| PGA at nearest building (Rjb=0.71km) | 0.171 g | **0.348 g** |
| SA(0.3s) at same building | 0.217 g | 0.464 g |

**Real near-fault stations recorded PGA up to ~0.36g in 2011 (§1/§3).**
Median+1σ PGA (0.348g) lands within 3.4% of that recorded value — this
specific event sits almost exactly at +1σ on this GMPE, which is squarely
inside normal aleatory variability (events routinely land above or below
the median; nothing about this is a red flag for the GMPE or an artifact of
cherry-picking) but means **§3's framing — comparing our median-only SA(0.3s)
output against the recorded PGA and calling them "the same order of
magnitude" — was comparing the wrong percentile of our own model against
reality.** The model's *median* output was never going to match a recorded
value that happened to land near +1σ; the model's own high-impact tier
does.

Re-ran §10.3's variant D (narrower taxonomy + IM-type fix) at median+1σ
instead of median, same 27,884 buildings, same rupture — this time with
modal-state counts (not expected-value), since at this intensity the shift
is large enough to actually flip modal states, not just move probabilities
around:

| Variant | Ground motion | None | Slight | Moderate+ |
|---|---|---|---|---|
| D (§10.3) | median | 27,884 | 0 | 0 |
| D + high-impact tier | median + 1σ | 25,188 | 2,696 | 0 |

Going from "0 buildings modally damaged" to "2,696 modally Slight" is a
qualitative change, not a tweak — and it comes from a UI feature MERISUR
already ships and documents, using a GMPE output twin-r's own code already
computes and discards. This doesn't fully close the gap against the
6,416-inspected figure (§1's caveat about inspection targeting reported
damage, not a census, still applies — these numbers are not directly
comparable), but it's the cheapest, most-grounded of the three levers: no
vendoring, no taxonomy judgment calls, no blocked-on-UPM data dependency.

**Added recommendation, ranked alongside §10.4 — done:**

6. ~~**Implement the probability-level selector** (median / median+1σ
   ground motion, modal / 85th-percentile damage — `merisur.md` §4.7):
   thread a `sigma_multiplier` (0.0 / 1.0) through `ground_motion.py`'s
   existing `sig` output into `compute_sa03`/`compute_sa03_gridded`,
   expose it as a scenario parameter (CLI flag + API field + frontend
   selector), default to today's median-only behaviour so nothing changes
   unless a caller asks.~~ Implemented in
   [ADR-0011](./decisions/0011-probability-level-selector.md): CLI
   (`--tier`), both API routes (`probability_level`), and a frontend
   selector are all in, still defaulting to "high" (today's original
   behaviour) when unspecified. Re-running this scenario end-to-end (not
   just the scratch experiment above) at Lorca scale: "high" 0
   damaged-modal buildings (unchanged), "low" 2,569, "very_low" 12,204 —
   see ADR-0011's Consequences for the exact breakdown. Recommendations 1–2
   (taxonomy + IM-type fix) remain open — independent of, and complementary
   to, this one; the full 3×3 (taxonomy × IM-fix × probability-tier) grid
   this item originally asked for still needs 1–2 landed first.
