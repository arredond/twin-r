# ADR-0008: Manual mode — progressive geometry complexity, no arbitrary defaults for orientation

Status: accepted

## Context

MERISUR's manual mode exposes strike, dip, Ztor, and "style of faulting" in
addition to magnitude; ours only exposed lat/lon/mag/rake. Investigating
why (the conversation that led to ADR-0007) established that Akkar et al.
(2014) itself only needs mag/rake/Rjb/vs30 — strike/dip/Ztor matter only
for building a correct finite rupture plane (and therefore a correct Rjb),
which ADR-0007 just added for **automatic** mode, fed by QAFI's own fault
geometry. Manual mode has no such data source — a user typing in
coordinates isn't picking a real fault.

The user asked for manual mode to support the same idea, with **progressive
complexity**: require only magnitude, default everything else, but let a
user configure more if they want to.

## Decision

Three tiers, `rupture.from_manual_input`:

1. **Magnitude only** — point source at the given (lat, lon), rake defaults
   to 0 (strike-slip). Exactly today's behavior, just now the *floor* of a
   larger range of options rather than the whole feature.
2. **+ rake / "style of faulting"** — still a point source, just a
   deliberately chosen mechanism. The frontend offers a 3-way picker
   (Strike-slip/Normal/Reverse) that maps to canonical rake values
   (`rupture.STYLE_OF_FAULTING_RAKE`) rather than a raw rake number, per
   the earlier finding that "style of faulting" *is* rake, just
   categorical.
3. **+ strike/dip/Ztor** — builds a real finite rupture surface
   (`surface.build_manual_surface`), the same underlying mechanism
   ADR-0007 added for automatic mode, but fed a **synthetic** two-point
   trace instead of a real QAFI one: centered on (lat, lon), oriented along
   the given `strike`, with rupture length and down-dip width derived from
   magnitude via Wells & Coppersmith (1994) — the same relation
   `pipelines/faults` already uses in the reverse direction (Mmax from
   length).

Tier 3 only activates when the user provides **all three** of
strike/dip/Ztor. We deliberately do **not** invent a default *orientation*
for tiers 1-2, even though magnitude and rake both have defensible
defaults (0 km² isn't a real earthquake, but "strike-slip" is a common,
reasonable starting assumption). There is no equivalently reasonable
default strike — any arbitrary choice would silently shape the damage
pattern's spatial extent in a direction the user never chose, which is
worse than not offering the refinement at all. Falls back to the point
source (not an error) if hazardlib rejects the resulting geometry, same
robustness contract as `from_fault` (ADR-0007).

## Alternatives considered

- **Always synthesize a plane with default strike/dip/Ztor**: rejected for
  the reason above — an arbitrary default orientation implies false
  precision.
- **Expose raw rake instead of/alongside a style-of-faulting picker**: kept
  rake fully available at the API level (advanced callers can still pass
  any value), but the frontend's tier-2 UI is categorical-only, matching
  MERISUR's own manual mode and the "progressive complexity" framing —
  someone who wants a bespoke rake can already get it by proceeding to
  tier 3 with a matching strike/dip choice, or via the raw API.

## Consequences

- `ManualRuptureRequest` (local.py) and the Lambda handler's manual-mode
  body parsing both gained optional `strike`/`dip`/`ztor_km` fields.
  Omitting them is unchanged, tested behavior — not a breaking change for
  any existing caller.
- The API response's `rupture.finite_rupture` (added in ADR-0007) now also
  reflects manual-mode tier-3 usage, not just automatic mode.
- `surface.py` gained Wells & Coppersmith length/width-from-magnitude
  helpers — the algebraic inverse of `pipelines/faults/mmax.py`'s
  Mmax-from-length relation, a second independent use of the same 1994
  paper already cited elsewhere in this project.
