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
