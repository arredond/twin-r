# Questions for the UPM MERISUR team

Status: draft, ready to send. Compiled from open items surfaced while
researching MERISUR ([`merisur.md`](./merisur.md)) and building/validating
the milestone-1 MVP ([`milestone-1-plan.md`](./milestone-1-plan.md),
[`validation-lorca-2011.md`](./validation-lorca-2011.md)). Per the
project's UPM collaboration, these are worth asking directly rather than
continuing to work around them with public-data substitutes.

Each question notes *why* we're asking (what we found, what gap it fills)
and *what we'd do with an answer*, so it's clear these aren't idle curiosity
— each one unblocks or meaningfully improves a specific part of `twin-r`.

*A question about QAFI's Alhama de Murcia Mmax (6.9 vs. our initial 7.4)
originally lived here — resolved ourselves by downloading and inspecting
QAFI v4's official shapefile directly rather than IGME's abbreviated
ArcGIS REST layer. See `merisur.md` §4.1/§7 and `validation-lorca-2011.md`
§6 for the trace.*

## High priority

### 1. Lorca capacity/fragility curves

**What we found:** No public dataset of MERISUR's own capacity or
fragility curves exists (checked Archivo Digital UPM, Zenodo, the GIIS
group page). Our MVP substitutes Martins & Silva (2020)'s generic global
fragility functions, and running the actual 2011 event through our
pipeline predicts *zero* damaged buildings against a reality of 6,400+
damaged — traced to this substitution, not a bug (full writeup in
`validation-lorca-2011.md`).

**Question:** Could you share the capacity curves (or resulting fragility
functions) developed for Lorca's Risk-UE building classes — the ones built
from pushover analysis of representative structural models
(`merisur.md` §4.5)? Even a subset (the classes actually present in Lorca's
old town) would let us validate against a real calibration instead of a
plausibility check.

**What we'd do with it:** Replace (or at least benchmark) our generic
fragility functions with the real Lorca-specific ones, closing the gap the
2011 validation run surfaced.

### 2. Lorca soil microzonation data (Navarro et al. 2014)

**What we found:** MERISUR's 5-class soil microzonation for Lorca
(`merisur.md` §4.3) is the single most Lorca-specific, least-portable piece
of the chain, but it's also directly responsible for amplifying ground
motion in the areas that were actually damaged in 2011 — something our MVP
currently omits entirely (flat reference-rock Vs30 everywhere). This is one
of the "other simplifications" flagged as a contributor in our 2011
validation run.

**Question:** Is the microzonation itself (polygon boundaries + amplification
factors per class) available to share, even just for Lorca as a reference/
validation case?

**What we'd do with it:** Add site amplification to the Lorca scenario as a
validation exercise, before deciding how to generalize site effects
nationally (where we already know we can't reuse Lorca's own
microzonation — see `milestone-1-plan.md` §2).

## Medium priority

### 3. The 2023 debris model

**What we found:** Gaspar-Escribano et al. (2023), *"Extending urban
seismic risk assessment to open spaces for the 2011 Lorca earthquake
scenario"* (Natural Hazards), is paywalled — we only have the abstract. The
2018 tool's simpler damage-state → façade-buffer-width table (1/2/3/4 m) is
the only debris methodology we currently have in enough detail to
reproduce. We're now actively building this (see
`milestone-1-plan.md` §10, [ADR-0010](./decisions/0010-debris-envelope-precompute.md)):
debris geometry is precomputed per building offline (party-wall edges
excluded from buffering, differenced against neighboring footprints, four
nested 1/2/3/4 m rings), and a scenario just picks the active ring off the
damage state it already computes. We don't yet clip rings against real
street/open-space geometry — no Spain-wide public street-polygon dataset is
known to us, unlike Catastro for building footprints — so a debris polygon
can currently extend into a private rear courtyard as easily as a real
street.

**Question:** Three, in decreasing priority: (a) could you share the paper,
or at minimum the debris-volume formulation, so we can replace the coarse
1/2/3/4 m table with real volumes? (b) What geometry/dataset did MERISUR
itself use to allocate debris onto "streets and open spaces" specifically
(vs. any adjacent exterior space) — OSM, Catastro's cadastral parcel
boundaries, a municipal street layer, something else? (c) Does the
party-wall/exterior-edge heuristic above (buffer only non-shared footprint
edges) match how MERISUR distinguishes a party wall from a street-facing
façade, or did you use different geometry (e.g. actual building
orientation/footprint metadata) for that?

**What we'd do with it:** (a) swap the ring distances for real volumes
without changing the precompute architecture (ADR-0010 already designed
for this swap); (b) adopt the same street/open-space source instead of
picking one (likely OSM road polygons) unvalidated; (c) sanity-check or fix
our edge-classification heuristic before it ships broadly.

### 4. Lorca exposure database

**What we found:** The live MERISUR tool's minimum input schema is public
(footprint, stories, vulnerability class — `merisur.md` §4.4), but the
richer exposure database MERISUR actually built (Catastro + PNOA LiDAR +
fieldwork + post-2011 ground-truth damage) isn't. We rebuilt exposure from
Catastro ourselves for the MVP (27,884 buildings for Lorca; now expanded to
Murcia + Andalucía), but have no ground truth to check our taxonomy-
assignment heuristic against.

**Question (a):** Would it be possible to share Lorca's building-level
vulnerability classifications (even without the fieldwork attributes behind
them) for validation purposes only — comparing our Catastro-derived,
heuristic taxonomy against your surveyed one, building by building?

**What we'd do with it:** Quantify how good (or bad) our no-fieldwork
taxonomy heuristic actually is, which is currently an open, unvalidated
assumption flagged in `milestone-1-plan.md` §2 and `validation-lorca-2011.md`.

**Question (b), added per [ADR-0012](./decisions/0012-im-type-dispatch-and-vernacular-masonry-taxonomy.md)
and [`TAXONOMY.md`](./TAXONOMY.md) — a smaller, cheaper ask than (a):**
`merisur.md` §4.5 states that remote-sensing work identified **six Risk-UE
Model Building Types in Lorca (one reinforced-concrete class, five masonry
classes)**, but neither paper we have in hand names the five masonry
classes or their share of Lorca's building stock. We currently collapse
all pre-1970 masonry into just two generic classes ourselves (a "modern
masonry" class for 1940–1969, a "vernacular rubble-stone" class for
pre-1940/unknown-year construction — chosen because Lorca's old town is
documented as predominantly stone masonry, not because we have any data
confirming that split). **Could you share just the five MBT names/
descriptions and, ideally, their approximate proportion of Lorca's
building stock** — no building-level data needed for this part, unlike
question (a)?

**What we'd do with it:** Replace our two-class masonry split (and its
1940 threshold, currently an undocumented judgment call) with classes that
actually match what's present in Lorca, and/or vendor additional Martins &
Silva (2020) masonry sub-classes (adobe, dressed stone, confined
pre/post-1999) our fragility pipeline doesn't currently include — see
`TAXONOMY.md` §4 for the full list of what's available but unused.

### 5. IDCM/FEMA 440 implementation details

**What we found:** We know MERISUR uses IDCM (FEMA 440/ATC 2005) to go from
demand spectrum + capacity curve to a damage-state distribution
(`merisur.md` §4.6), but not the specific coefficients/procedure variant
used. Our MVP sidesteps this entirely by applying fragility functions
directly to intensity rather than running IDCM (`milestone-1-plan.md` §2) —
a deliberate simplification, but one that diverges from MERISUR's actual
method, not just its data.

**Question:** Is there a technical reference (beyond the 2017/2018 papers)
documenting the specific IDCM procedure and parameters used?

**What we'd do with it:** Decide whether reproducing IDCM faithfully is
worth doing later, or whether the fragility-function approach is an
acceptable permanent simplification — right now we don't have enough
information to make that call deliberately.

## Lower priority / longer-term

### 6. Source code or internal technical documentation

Is there any internal documentation, source code, or technical report for
the live simulator beyond the published papers? Even partial access (e.g.
input file formats, the exact request/response contract) would resolve
several of the above at once and save us reverse-engineering the live
tool's network behavior.

### 7. National-scale work already underway

Has UPM (or collaborators) already started any national-scale extension of
MERISUR's methodology, exposure modeling, or hazard work beyond Lorca? We'd
rather build on or coordinate with existing work than duplicate it — this
is directly relevant to `twin-r`'s milestone 2 (whole-of-Spain expansion,
currently underway for Murcia + Andalucía).

### 8. Validation cases beyond Lorca

`initial-chatgpt.md`'s exploratory discussion suggested Granada, Almería,
Melilla, and the Canary Islands as good validation cases for a national
model (different tectonic/exposure contexts). Does UPM have damage data or
existing risk assessments for any of these we could use the same way we
used the 2011 Lorca event here?

## How to use this doc

Suggested order if UPM's time is limited: **1 → 2** would resolve the
concrete, quantified gaps our validation run already surfaced. **3–5** are
well-scoped asks for specific artifacts. **6–8** are open-ended and better
suited to a conversation than an email thread.
