# Building typology / vulnerability class taxonomy

Status: current as of [ADR-0012](./decisions/0012-im-type-dispatch-and-vernacular-masonry-taxonomy.md).
Reference doc, not a plan or a decision record — explains what `twin-r`
actually assigns each building today, what MERISUR does instead, where the
two diverge, and what would need to change to close that gap further.
Background: [`merisur.md`](./merisur.md) §4.5 (vulnerability/typology in
general) and [`validation-lorca-2011.md`](./validation-lorca-2011.md) §10
(the concrete gap this heuristic caused, and the fix ADR-0012 shipped).

## 1. What `twin-r` does today

`pipelines/exposure/src/exposure/taxonomy.py`'s `assign_taxonomy` maps two
Catastro attributes — `construction_year` and `floors` — to a
`(taxonomy_class, height_class)` pair, with **no field survey involved**:
per the project's no-fieldwork constraint, this is a documented, versioned
*guess*, not an observation. Every building carries that provenance
(`taxonomy_source = "heuristic_v1"`) rather than silently looking as
authoritative as a surveyed value would.

**Material class**, by construction year:

| `construction_year` | `taxonomy_class` | Meaning |
|---|---|---|
| ≥ 1970 | `CR_LDUAL-DUL` | Reinforced concrete, dual lateral system, low ductility |
| 1940–1969 | `MR_LWAL-DUL` | Generic unreinforced masonry, load-bearing wall, low ductility |
| < 1940, or unknown | `MUR-STRUB_LWAL-DNO` | Unreinforced rubble-stone masonry, no ductility |

- **1970** loosely tracks Spain's shift toward modern seismic-resistant
  design codes and reinforced-concrete-frame construction becoming the
  default — see `merisur.md` §4.5's own framing (Lorca's stock: 1 RC class
  vs. 5 masonry classes).
- **1940** is a judgment call, not derived from a documented code-generation
  date the way 1970 loosely is — added per
  `validation-lorca-2011.md` §10.1 once the original single-threshold
  heuristic was found to systematically understate Lorca's real
  vulnerability (§4 below has what would sharpen it).
- **Unknown `construction_year`** (Catastro sometimes omits it, especially
  for older buildings) defaults to the *older*, more vulnerable class —
  consistent with unknown `floors` defaulting to height class 1
  ("low-rise"): prefer the conservative guess over the optimistic one when
  Catastro gives us nothing to go on, rather than silently defaulting to
  the mildest available class.

**Height class**: `floors` rounded to the nearest integer, clamped to
`[1, 12]`; unknown/zero/negative `floors` defaults to 1. `CR_LDUAL-DUL` and
`MR_LWAL-DUL` are conceptually 1–12 stories; `MUR-STRUB_LWAL-DNO` only goes
up to 5 in the vendored data (rubble-stone construction taller than ~5
stories isn't a real category upstream) — a building assigned that class
past height 5 falls back to the nearest height actually vendored (5), the
same "nearest available height" handling every class already gets
(`services/scenario/src/scenario/fragility_lookup.py`).

**Fragility functions**: each `(taxonomy_class, height_class)` maps to a
pre-published fragility curve from Martins & Silva (2020)'s global
GEM-taxonomy fragility/vulnerability model (`pipelines/fragility`) — not a
Lorca-specific capacity curve (no public one exists, see §2 below), and not
computed by `twin-r` itself. This is a deliberate substitute for MERISUR's
own method (IDCM/FEMA 440 against a real capacity curve per class,
`merisur.md` §4.6), documented as a simplification in
`docs/milestone-1-plan.md` §2.

## 2. What MERISUR does

Per `merisur.md` §4.5, MERISUR's vulnerability classification draws on
**three, partially independent lines of work**:

1. **Empirical/EMS-98 classification** from field campaigns — structural
   system, soft-storey, height irregularities, position within block, etc.
   `twin-r` cannot reproduce this at all (no fieldwork, by project
   constraint).
2. **Mechanical models**: representative finite-element models per
   building class, pushover analysis → capacity curves → fragility curves.
   Remote-sensing work identified **six Risk-UE Model Building Types (MBTs)
   in Lorca: one reinforced-concrete class, five masonry classes** — the
   simulator itself doesn't run pushover per request, it looks up a
   pre-computed curve per class (computationally cheap; the expensive part
   happened once, offline, during MERISUR's research phase).
3. **Remote-sensing + ML classification**: LiDAR + orthophoto + satellite
   features classified via Decision Trees / SVM / Logistic Regression /
   Bayesian Networks, ~77–80% accuracy/F1 in Lorca trials — this is *how*
   MERISUR assigned classes at scale, not something `twin-r` currently has
   the inputs or scope to reproduce (would need LiDAR/orthophoto coverage
   and a trained classifier, not just Catastro attributes).

**We do not know the specific names or proportions of Lorca's five Risk-UE
masonry MBTs** — `merisur.md` §4.5 states the count (five) and that they're
Risk-UE MBTs, but neither the 2017 nor 2018 paper (the only primary sources
in hand) breaks out which five, or how much of Lorca's stock each one
covers. This is a real gap in what we can validate our own heuristic
against — see §4 and `questions-for-upm.md` §4 (extended per this doc).

**No public Lorca building/vulnerability dataset exists** (`merisur.md`
§4.4/§7): we checked Archivo Digital UPM, Zenodo, and UPM's GIIS group page
directly. There is no MERISUR exposure data to bootstrap from, for Lorca or
anywhere else — `twin-r`'s from-Catastro heuristic isn't a shortcut taken
instead of using MERISUR's own data; it's the only option available to us.

## 3. How the two compare

| | MERISUR | `twin-r` |
|---|---|---|
| Classification basis | Field survey + remote-sensing ML (77–80% accuracy) | Two Catastro attributes (construction year, floors) |
| Number of classes (Lorca) | 6 Risk-UE MBTs (1 RC, 5 masonry) | 3 (1 RC, 2 masonry) |
| Masonry granularity | 5 distinct sub-types (names/proportions unknown to us) | 2: "generic" (1940–1969) vs. "vernacular rubble-stone" (pre-1940/unknown) |
| Capacity/fragility source | Lorca-specific capacity curves (pushover on representative FE models, not public) | Generic global curves, Martins & Silva (2020) |
| Ductility | Presumably assessed per class from the FE models | Always defaulted to low/none — Catastro carries no seismic-design information |
| Validated against | Post-2011 ground-truth damage database (used to calibrate vulnerability) | Not yet validated against any ground truth (see `validation-lorca-2011.md`) |

The biggest structural mismatch: MERISUR's five Lorca masonry MBTs almost
certainly capture real, materially different vulnerability differences
(adobe vs. rubble stone vs. dressed stone vs. confined masonry pre/post the
1999 Spanish masonry code, going by the sibling classes Martins & Silva
publish — see §4) that `twin-r`'s two-way masonry split collapses into a
single boundary. `MUR-STRUB_LWAL-DNO` (rubble stone) was chosen as the
pre-1940 default because Lorca's historic old town is documented as
predominantly stone masonry, not adobe (`merisur.md` §4.5,
`validation-lorca-2011.md` §10.1) — a reasonable single pick, not a
faithful reproduction of five classes.

## 4. What would improve this further

Roughly in order of expected impact per unit of effort:

1. **Get UPM's answer on the five Risk-UE masonry MBTs actually present in
   Lorca** (`questions-for-upm.md` §4b) — the cheapest, highest-leverage
   next step. Even just the five class *names* and their approximate share
   of Lorca's stock would let us pick a materially better default (or two)
   than "assume rubble stone for everything pre-1940," and would validate
   or correct the 1940 threshold itself, which right now is a judgment call
   with no data behind it.
2. **Vendor more of Martins & Silva's masonry sub-classes.** The same
   repository `pipelines/fragility` already draws from publishes at least
   `MUR-ADO_LWAL-DNO` (adobe), `MUR-STDRE_LWAL-DNO` (dressed stone, no
   ductility), `MUR-CB99_LWAL-DNO` / `MUR-CL99_LWAL-DNO` (confined block/
   confined masonry, pre-1999 Spanish code) alongside the
   `MUR-STRUB_LWAL-DNO` this ADR added — comparable vulnerability, in some
   cases 3–4x `MR_LWAL-DUL`'s `P(≥Slight)` at the same shaking
   (`validation-lorca-2011.md` §10.1's table). The blocker isn't
   availability, it's that Catastro alone gives no signal to choose among
   them (construction year distinguishes *era*, not construction
   material/technique) — (1) above would tell us whether that signal is
   even findable, or whether it needs a different data source entirely
   (e.g. a coarse remote-sensing pass, MERISUR's own approach).
3. **Replace the single/double year-threshold heuristic with the fuller
   code-generation timeline** sketched in `initial-chatgpt.md` (Spain's
   seismic code history has more than two eras — pre-code, PGS-1 (1968),
   PDS-1 (1974), NCSE-94, NCSE-02, current NCSE — each with materially
   different implied ductility/detailing, not just material). Currently
   deferred the same way the original single-threshold heuristic was:
   refine once there's reason to believe it changes results materially,
   which (1)/(2) would help establish.
4. **A remote-sensing classification pass**, matching MERISUR's own method
   (§2 above) — LiDAR/orthophoto features → trained classifier. The
   largest lift of these four: needs a labeled training set (which, absent
   (1), we don't have) and a new pipeline stage, not just a heuristic
   tweak. Worth reconsidering once/if (1) produces enough real Lorca
   ground truth to train or validate against.
5. **Ductility class refinement.** Every vendored class currently defaults
   to the lowest ductility option (`-DUL`/`-DNO`) since Catastro carries no
   seismic-design information — conservative, and not worth revisiting
   without a data source that could actually distinguish ductility levels
   (fieldwork or a code-era-aware heuristic per (3) might get partway
   there for post-code construction, but not for masonry).
6. **Real Lorca capacity curves from UPM** (`questions-for-upm.md` §1)
   remain the only path to replacing the whole generic-fragility-function
   substitution with an actual calibration, rather than incrementally
   improving which generic class each building maps to. Independent of,
   and a bigger ask than, (1)–(5) above.

## 5. Where this is implemented

- `pipelines/exposure/src/exposure/taxonomy.py` — the heuristic itself.
- `pipelines/fragility/src/fragility/source.py` — which classes are
  vendored (`TAXONOMY_CLASSES`) from Martins & Silva's GitHub repository.
- `services/scenario/src/scenario/fragility_lookup.py` — loads vendored
  curves, including each curve's own intensity-measure type
  (`FragilityCurve.im_type`) and per-taxonomy nearest-height fallback.
- `services/scenario/src/scenario/ground_motion.py` /
  `services/scenario/src/scenario/damage.py` — compute each intensity
  measure a vendored curve actually needs and dispatch each building to
  its own curve's IM type (`docs/validation-lorca-2011.md` §10.2's fix,
  ADR-0012) — a taxonomy refinement is only as good as feeding it the
  right intensity value, which this pairing exists to guarantee.
