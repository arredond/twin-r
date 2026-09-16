# ADR-0012: Per-building IM-type dispatch, and a vernacular-masonry taxonomy class

Status: accepted, implemented end-to-end (pipelines + scenario service)

## Context

`docs/validation-lorca-2011.md` §10 (added after the 2011 Lorca sanity
check, §1–§9) found two independent, currently-offsetting problems with
`twin-r`'s exposure/fragility chain, both real correctness issues rather
than a single "the numbers are off" gap:

1. **§10.1 — the taxonomy heuristic is too coarse, and picked the mildest
   option.** `taxonomy.py` sorted every pre-1970 building into one generic
   masonry class, `MR_LWAL-DUL`. Checked against every comparable
   vernacular-masonry class the same vendor repository (Martins & Silva
   2020) publishes, `MR_LWAL-DUL` turned out to be the *least* vulnerable
   of the six — not merely coarse, but specifically the most optimistic
   choice available for exactly the pre-modern, un-engineered construction
   era it was meant to cover.
2. **§10.2 — a live intensity-measure (IM) dispatch bug.** The vendored
   fragility curves (`fragility.parquet`) are indexed by *different*
   intensity measures depending on height class — PGA for 1-story curves,
   SA(0.3s)/(0.6s)/(1.0s) for taller ones (a per-curve `im_type` column
   already carried the correct label, but nothing downstream read it).
   `ground_motion.py` computed a single SA(0.3s) value per building and
   fed it into whichever curve the building resolved to, regardless of
   that curve's actual `im_type`. This affected 44% of Lorca's stock
   (every height-class-1 building) and is not Lorca-specific — every
   scenario run, anywhere, had this bug.

§10.3 quantified all four combinations (today's code / IM-fix only /
taxonomy-fix only / both) against real Lorca data and found the two bugs
were **currently offsetting each other by coincidence**: fixing the IM-type
bug alone made the Lorca match look *worse* (it happened to be inflating
demand on PGA-indexed curves), and even both fixes together, in that
scratch-harness estimate, landed below the original all-bugs baseline. The
two fixes had to land together, or shipping either alone would look like a
regression on this validation case despite being a real correctness
improvement.

## Decision

**Ship both fixes together**, plus a new documentation artifact
(`docs/TAXONOMY.md`) explaining the resulting taxonomy and what would
improve it further.

### IM-type dispatch (§10.2's fix)

- `ground_motion.py` no longer hardcodes SA(0.3s). `compute_intensity`/
  `compute_intensity_gridded` (renamed from `compute_sa03`/
  `compute_sa03_gridded`) take an explicit `imt` parameter; a new
  `IM_TYPE_TO_IMT` dict maps every `im_type` label the vendored data
  carries ("PGA [g]", "SA(0.3s) [g]", "SA(0.6s) [g]", "SA(1.0s) [g]") to
  the hazardlib IMT object needed to compute it. `estimate_significant_distance_km`
  keeps using SA(0.3s) internally (`SIGNIFICANT_DISTANCE_IMT`) as a
  representative proxy for its search-radius heuristic — it doesn't need
  to be the exact IM any given building's curve uses, only a reasonable
  stand-in for "is this rupture's shaking still significant out here."
- `fragility_lookup.py`'s `FragilityCurve` gained an `im_type` field (one
  per curve — every damage state within a `(taxonomy, height)` curve
  shares the same IM type in the vendored data) and `FragilityTable`
  gained `used_im_types()`, so `engine.py` only computes ground motion for
  IM types the loaded fragility set actually needs.
- `damage.py`'s `evaluate_damage_batch` now takes
  `im_values_by_type: dict[str, np.ndarray]` instead of a single
  `im_values` array; each `(taxonomy, height)` group reads its IM values
  from the array matching its own curve's `im_type`. The scalar path
  (`evaluate_building_damage`) is unchanged (still a single `im_value` —
  callers resolve which one to pass).
- `engine.run_scenario`'s result gained `im_value`/`im_type` columns
  (replacing the old single `sa03_g` column) recording which IM value and
  type each building was *actually* evaluated against — informational
  only, not consumed by the frontend (same as the column it replaces).

### Vernacular-masonry taxonomy class (§10.1's fix)

- `pipelines/fragility/src/fragility/source.py` vendors a third class,
  `MUR-STRUB_LWAL-DNO` (unreinforced rubble-stone masonry, no ductility) —
  chosen over the other vernacular-masonry options the same vendor repo
  publishes (adobe, dressed stone, confined pre/post-1999) because Lorca's
  historic old town is documented as predominantly stone masonry
  (`merisur.md` §4.5). Only H1–H5 exist upstream for this class (vs. H1–H12
  for the other two); `source.py`'s existing 404-skip handling covers that
  without special-casing.
- `taxonomy.py` gained a second construction-year threshold: `≥1970` →
  `CR_LDUAL-DUL` (unchanged), `1940–1969` → `MR_LWAL-DUL`, `<1940` or
  unknown → `MUR-STRUB_LWAL-DNO`. Unknown `construction_year` was
  previously folded into the generic masonry default; it now gets the more
  vulnerable class instead, consistent with how unknown `floors` already
  defaults to height class 1 ("low-rise") — prefer the conservative
  (more-vulnerable) guess over the optimistic one when Catastro gives us
  nothing to go on. 1940 is a judgment call, not derived from a documented
  code-generation date the way 1970 loosely is — see `docs/TAXONOMY.md`
  §4 and `docs/questions-for-upm.md` §4(b) for what would sharpen it.

### `docs/TAXONOMY.md`

New reference doc: what `twin-r` assigns today and why, what MERISUR does
instead (three lines of work — field survey, mechanical models on Risk-UE
MBTs, remote-sensing ML classification — per `merisur.md` §4.5), a direct
comparison table, and a ranked list of what would improve `twin-r`'s
heuristic further (get UPM's answer on Lorca's five Risk-UE masonry MBTs;
vendor more Martins & Silva masonry sub-classes; the fuller code-generation
timeline `initial-chatgpt.md` sketched; a remote-sensing classification
pass; ductility refinement; real Lorca capacity curves).

## Alternatives considered

- **Ship the IM-type fix alone first, taxonomy fix later**: rejected —
  §10.3 showed this actively regresses the Lorca validation case (a real
  bug fix that looks like new damage, i.e. "wrong direction," purely
  because it currently happens to cancel a separate optimistic-taxonomy
  bug). Landing them separately would have made the IM-type fix look like
  a mistake to revert.
- **Ship the taxonomy fix alone, defer the IM-type fix**: rejected on its
  own correctness merits — the IM-type bug is a live, general-purpose
  defect (not Lorca-specific), and deferring a known correctness bug
  because fixing it currently looks bad on one validation case is exactly
  the kind of coincidence-dependent decision-making that produces harder
  to find bugs later.
- **A continuous, data-driven taxonomy threshold instead of a second fixed
  year cutoff**: rejected for now — no data exists yet to fit one against
  (that's precisely `docs/questions-for-upm.md` §4(b)'s ask); a second
  documented judgment-call threshold, flagged as such, is more honest than
  a spuriously precise one.
- **Vendor all five-plus Martins & Silva masonry sub-classes immediately**:
  rejected — Catastro alone gives no signal to choose among them beyond
  construction era (they differ by construction *technique/material*, not
  just vintage); vendoring classes `taxonomy.py` has no way to actually
  select between would add vendored data with no corresponding heuristic
  logic. One new class, chosen for a documented, Lorca-specific reason
  (predominant historic-core material), is the largest taxonomy change
  currently justified by available data.

## Consequences

- No behavioural change to the API contract's *shape* — `run_scenario`'s
  result still has one row per evaluated building with the same
  probability columns; `sa03_g` is replaced by `im_value`/`im_type`
  (informational columns, not consumed by the frontend either before or
  after).
- Fragility pipeline output grew from 2 classes/~17 (taxonomy, height)
  combinations to 3 classes/22 combinations (12 + 5 + 5, since
  `MUR-STRUB_LWAL-DNO` only has H1–H5 upstream) — re-running
  `python -m fragility` is required to pick up the new class; existing
  `fragility.parquet` files with only two classes will raise a `KeyError`
  from `FragilityTable.get` for any building assigned
  `MUR-STRUB_LWAL-DNO` until regenerated.
- Exposure pipeline output's `taxonomy_class` distribution changes for
  every already-crawled region, not just Lorca (the heuristic is generic,
  not Lorca-conditional) — re-running the exposure pipeline (or at minimum
  re-deriving `exposure.parquet` from already-crawled `buildings.parquet`,
  no need to re-crawl Catastro) is required to pick up the new taxonomy
  split anywhere it matters for a live deployment.
- **Measured against real Lorca data** (27,884 buildings): the taxonomy
  split moves 5,870 buildings from `MR_LWAL-DUL` to `MUR-STRUB_LWAL-DNO`
  (pre-1940 or unknown-year). Re-running `docs/validation-lorca-2011.md`'s
  2011 scenario end-to-end with both fixes and all three probability tiers
  (ADR-0011) is written up in that doc's §10.6 — headline: the "high"
  (median ground motion) tier's expected-damage figure now lands
  essentially at the original (bugs-included) baseline rather than below
  it, and "low"/"very low" tiers now show thousands of buildings with
  Slight/Moderate expected damage rather than zero at every tier as the
  original baseline did.
- Deliberately unchanged: the debris pipeline and Lorca-specific debris
  dataset (per explicit instruction), site amplification (still blocked on
  real microzonation data, `questions-for-upm.md` §2), and IDCM/capacity-
  curve fidelity (still a fragility-function substitution, per
  `docs/milestone-1-plan.md` §2 — real Lorca capacity curves remain a UPM
  ask, `questions-for-upm.md` §1).
