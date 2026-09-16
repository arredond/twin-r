# ADR-0011: Probability-level selector (MERISUR's three scenario tiers)

Status: accepted, implemented end-to-end (backend + frontend)

## Context

MERISUR's UI exposes three selectable "scenario probability levels"
(`docs/merisur.md` §4.7), each changing *two* independent things, not just
earthquake size:

| Level | Ground motion | Damage |
|---|---|---|
| High probability | median | modal damage state |
| Low probability / high impact | median + 1σ | modal damage state |
| Very low probability / very high impact | median + 1σ | 85th-percentile damage state |

`twin-r`'s milestone-1 plan deliberately deferred this ("skip MERISUR's
three probability levels ... add them once the base chain works",
`docs/milestone-1-plan.md` §1/§8) and computed only the median-ground-motion,
modal-damage case. Every scenario `twin-r` had ever run — including the
entire Lorca 2011 validation exercise (`docs/validation-lorca-2011.md`) —
was implicitly "High probability" only, with no way to ask for anything
else.

`docs/validation-lorca-2011.md` §10.5 found this mattered concretely, not
just as a documented gap: for the actual 2011 Lorca event, median+1σ PGA
(from the same Akkar et al. 2014 GMPE already in use) lands within 3.4% of
the recorded near-fault PGA. The GMPE call the engine already makes returns
`sig` (total aleatory standard deviation) alongside `mean`; the code
computed it and discarded it. No new data, dependency, or vendoring was
needed to close this gap — only wiring up an output the engine already had.

## Decision

**Implement all three tiers as a real, user-facing toggle**, not a one-off
script: a `probability_level` parameter (`"high" | "low" | "very_low"`)
threaded end-to-end from the frontend's rupture form through both the
manual (`POST /scenarios/manual`) and automatic/fault (`GET
/scenarios/fault`) API routes, into the scenario engine.

- **New module** `services/scenario/src/scenario/probability_level.py`:
  the single source of truth mapping each of the three level strings to
  `(sigma_multiplier, damage_percentile)`. `high` -> `(0.0, None)`, `low`
  -> `(1.0, None)`, `very_low` -> `(1.0, 0.85)`. `damage_percentile=None`
  means "modal" (today's only pre-existing behaviour), matching how every
  caller before this ADR behaved by default.
- **`ground_motion.py`**: `_sa03_at_distances`/`compute_sa03`/
  `compute_sa03_gridded`/`estimate_significant_distance_km` all gained a
  `sigma_multiplier: float = 0.0` parameter, applied in log space
  (`mean + sigma_multiplier * sig`) before exponentiating -- using GMPE
  output already computed, not a second GMPE call. The spatial pre-filter
  radius (`estimate_significant_distance_km`) must use the *same*
  `sigma_multiplier` as the scenario itself, or a "low"/"very_low" tier
  could silently exclude buildings a smaller, median-only radius wouldn't
  have reached.
- **`damage.py`**: added `percentile_damage_state` (smallest damage state
  whose cumulative probability, summed in ascending severity from "None",
  reaches a given percentile) and `select_damage_state` (dispatches to
  `modal_damage_state` when `damage_percentile is None`, else
  `percentile_damage_state`). Both the scalar (`evaluate_building_damage`)
  and vectorized (`evaluate_damage_batch`) paths take the new
  `damage_percentile` parameter; the batch path implements percentile
  selection as "first row where the running cumulative sum crosses the
  threshold" (`np.argmax` on a boolean array), cross-checked against the
  scalar path in `test_damage_batch.py`.
- **`engine.run_scenario`**: gained `sigma_multiplier`/`damage_percentile`
  parameters, both defaulting to today's only behaviour, threaded straight
  through to `compute_sa03_gridded` and `evaluate_damage_batch`.
- **API**: `local.py`'s `ManualRuptureRequest` and `/scenarios/fault` both
  gained `probability_level: ProbabilityLevel = "high"` (a `Literal`, so
  FastAPI/Pydantic reject an unknown value with 422 before any route code
  runs); `handler.py`'s Lambda path reads the same field from the query
  (fault mode) or body (manual mode) and resolves it explicitly (400 on an
  unknown value, matching its existing rupture-parameter error handling).
  Both echo `probability_level` back in the response's `rupture` object,
  the same pattern already used for `finite_rupture`.
- **CLI** (`python -m scenario`): gained an optional `--tier
  high|low|very_low` flag (default `high`), for the same quick-iteration
  use case `docs/validation-lorca-2011.md` already used this CLI for.
- **Frontend**: a shared "Probability level" radio-button selector in
  `RuptureForm.tsx` (applies to both Automatic and Manual mode, lifted to
  `App.tsx` state alongside `mode` since it means the same thing regardless
  of how the rupture was defined), using MERISUR's own tier labels verbatim
  (`probabilityLevels.ts`) so the UI reads as "the same selector MERISUR
  has." Both `runManualScenario`/`runFaultScenario` calls in `App.tsx` pass
  the selected level; the result readout shows which tier actually ran.

## Alternatives considered

- **Only fix the Lorca-specific gap ad hoc** (e.g. hardcode +1σ for this
  one validation scenario): rejected — the underlying capability (asking
  "what if this earthquake were somewhat worse than the median GMPE
  prediction") is generally useful, is literally a feature MERISUR ships,
  and was already an explicitly deferred, not rejected, milestone-1 item.
- **Damage percentile as a continuous slider** rather than fixed
  high/low/very_low tiers: rejected for MVP scope — MERISUR's own UI is
  three fixed tiers, not a continuous control, and matching it exactly
  keeps the UX comparison clean; `damage_percentile`/`sigma_multiplier`
  are already general `float`/`float | None` parameters underneath, so a
  future continuous control would not need to change the engine, only add
  a fourth caller of `run_scenario`.
- **Recompute ground motion twice (median and +1σ) and let the frontend
  pick**: rejected — doubles GMPE evaluation cost for no benefit, since the
  frontend never needs to compare tiers side by side in one response; a
  user who wants to compare tiers just re-runs the scenario, the same
  interaction MERISUR's own UI has.

## Consequences

- No behavioural change for any existing caller that doesn't pass
  `probability_level`/`sigma_multiplier`/`damage_percentile` — every new
  parameter defaults to the exact pre-existing median/modal behaviour.
- `docs/validation-lorca-2011.md`'s scenario can now be re-run at all three
  tiers (`python -m scenario 37.699 -1.672 5.2 44 --data-dir data --tier
  low`): at Lorca scale (27,884 buildings, current 2-class taxonomy,
  unchanged by this ADR) "high" still ships 0 damaged-modal buildings,
  "low" ships 2,569, "very_low" ships 12,204 (11,539 Slight, 637 Moderate,
  28 Extensive) — confirms §10.5's hand-computed estimate end-to-end
  through the real API path, not just the scratch experiment it was
  originally checked in.
- 75 backend tests (was 58) cover the new module, both damage-selection
  functions (scalar + vectorized, cross-checked against each other), the
  sigma-multiplier ground-motion/radius behaviour, and both API routes
  (default level, explicit level, invalid level, and a behavioural check
  that "low" ships strictly more damage than "high" at an identical,
  otherwise-undamaged magnitude). Frontend type-checks (`tsc -b`) and
  lints (`oxlint`) clean.
- Deliberately unchanged: this ADR does not touch exposure/taxonomy
  (`docs/validation-lorca-2011.md` §10.1-10.4's separate, still-open
  recommendations) or the debris pipeline/Lorca-specific debris dataset.
