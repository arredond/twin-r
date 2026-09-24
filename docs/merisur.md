# MERISUR: research notes

This document summarizes everything we could establish about MERISUR — the seismic
scenario simulator built by Universidad Politécnica de Madrid (UPM) for the city of
Lorca — as a reference for building `twiner`. It combines primary sources (papers,
the live tool, QAFI/IGME) with a structured reconstruction of the parts that aren't
publicly documented in detail. See [`initial-chatgpt.md`](./initial-chatgpt.md) for
the exploratory conversation this doc was distilled from; treat that file as raw
material, not as a citable source.

**Confidence key:** 🟢 stated explicitly in a primary source · 🟡 inferred with
reasonable confidence · 🔴 unverified / reconstructed, needs checking before we rely
on it.

## 1. What MERISUR is

MERISUR — *Metodología para la Evaluación Efectiva del Riesgo Sísmico Urbano*
("Methodology for an Effective Risk Assessment of Urban Areas") — is a research
project (ref. **CGL2013-40492-R**, Spain's Plan Estatal de I+D+i / Retos de la
Sociedad 2013) run by UPM's Earthquake Engineering Research Group (GIIS,
E.T.S.I. en Topografía, Geodesia y Cartografía). 🟢

The project is broader than the web tool: it covers hazard, exposure, structural
and non-structural vulnerability, primary damage, secondary damage (debris),
dynamic occupancy (people/vehicles), and cost-benefit analysis of mitigation
measures. 🟢 [Gaspar-Escribano et al. 2017]

The public-facing deliverable most relevant to us is a **web-based scenario
simulator**, scoped to the city of **Lorca** (SE Spain), still live at
`http://merisur.topografia.upm.es/index.html`, currently reporting itself as
**v1.2.2 (Feb 2023)**. 🟢

Two papers document it directly:

- Pouye Yazdi, Gaspar-Escribano, Martínez-Cuevas & Chavarria-Meneces (2018),
  *"A Web-Based Tool for Scenario-Based Seismic Risk Assessment"*, presented at
  the 12th Canadian Conference on Earthquake Engineering and EGU 2018. 🟢
- Gaspar-Escribano et al. (2017), *"Methodology for an effective risk assessment
  of urban areas: progress and first results of the MERISUR project"*, 16th World
  Conference on Earthquake Engineering. 🟢
- Gaspar-Escribano et al. (2023), *"Extending urban seismic risk assessment to
  open spaces for the 2011 Lorca earthquake scenario"*, Natural Hazards
  (Springer) — the dedicated debris-model paper. 🟢 (paywalled; we only have the
  abstract/summary, not the equations — see §7.)

## 2. Why Lorca

Lorca suffered a shallow **Mw 5.2 earthquake on 11 May 2011** that caused 9
fatalities, most of them linked to falling non-structural building elements
(cornices, parapets, façade cladding) onto the street rather than structural
collapse. 🟢 That is the direct motivation for MERISUR's emphasis on debris and
open-space risk, not just building damage — a public square or sidewalk can be
as lethal as the building itself.

## 3. End-to-end architecture

```
QAFI fault (automatic) ─┐
user-defined rupture ───┴──► rupture (Mw, strike, dip, Ztor, rake, location)
                                  │
                                  ▼
                    GMPE: Akkar, Sandıkkaya & Bommer (2014)
                                  │
                                  ▼
                    site amplification (Lorca microzonation,
                    Navarro et al. 2014 — 5 soil classes)
                                  │
                                  ▼
                    per-building response spectrum / demand
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                        ▼
     building exposure (footprint,                capacity/fragility
     stories, vulnerability class)                curve per vulnerability class
              └───────────────────┬───────────────────┘
                                  ▼
                    IDCM (FEMA 440 / ATC 2005) →
                    performance point → damage state
                    (None / Slight / Moderate / Extensive / Complete)
                                  │
                                  ▼
                    debris model (damage state + façade
                    geometry → debris width/volume)
                                  │
                                  ▼
                    allocation onto adjacent streets/sidewalks
```

This chain is explicitly documented across the 2017/2018 papers. 🟢

## 4. Component-by-component

### 4.1 Seismic source

- **Automatic mode**: pick a fault from a list sourced from **QAFI**, the
  Quaternary Active Faults Database of Iberia, maintained by IGME (Instituto
  Geológico y Minero de España), and generate the fault's maximum-magnitude
  earthquake. 🟢
  - **Verified directly** (downloaded and inspected both): QAFI v4's official
    download (the `QAFI_Traces.rar` shapefile at
    `info.igme.es/qafi/docs/`) carries a much richer schema than IGME's
    public ArcGIS MapServer REST layer — `Length`, `MaxMagnitu`,
    `Recurrence`, `Rake`, `Dip`, `AverageStrike`, reliability ratings, and
    source/comment fields per attribute, none of which the MapServer layer
    exposes (it only returns ID/name/section/slip-rate-class/reliability-
    class, 8 fields total). Per IGME's own field-description PDF: *"[Maximum
    Magnitude], conversely to version 3, in which this data was
    automatically calculated; in version 4 it is only displayed if it has
    been published before somewhere (Source: LD, Literature Data)."* **60%
    of QAFI v4's 201 faults have a real, literature-published Mmax** — this
    is not a rare exception. 🟢
  - Earlier draft of this doc speculated that MERISUR's UI (reporting Mmax
    6.9 for "Alhama de Murcia (1/4)") reflected "a frozen QAFI v3 snapshot"
    different from the current v4 data. **That guess was wrong, or at least
    not the real story.** The official v4 shapefile gives this exact
    segment `Length = 30 km` and `MaxMagnitu = 6.7` (range 6.4–7.0, citing
    Ortuño et al. 2012 and Martínez-Díaz et al. 2012) — squarely consistent
    with MERISUR's 6.9. The actual problem was on our side: `twiner`'s
    faults pipeline originally queried the MapServer REST layer, whose
    geometry for this same fault record is **~96 km long — over 3x the
    official 30 km** — an unreliable geometry, not a missing-field issue.
    See [ADR-0004](./decisions/0004-qafi-shapefile-source.md) and
    `validation-lorca-2011.md` for the full trace of this investigation. 🟢
  - QAFI is CC BY-SA 4.0, versioned, and explicitly *not* a substitute for
    site-specific studies — a caveat worth repeating to any public-entity
    consumer of `twiner`. 🟢
- **Manual mode**: user supplies lat/lon, Mw, strike, dip, Ztor, rake directly —
  no fault required. 🟢

### 4.2 Ground motion (GMPE)

**Akkar, Sandıkkaya & Bommer (2014)**, *"Empirical ground-motion models for
point- and extended-source crustal earthquake scenarios in Europe and the
Middle East"*, Bull. Earthquake Eng. — explicitly named in the papers as "the
attenuation model." 🟢 It supports both point- and extended-source ruptures and
RJB distance. Useful for us: **OpenQuake's `hazardlib.gsim` already implements
this exact GMPE** (`AkkarEtAl2014Rjb` and siblings), coefficients sourced from
the original paper's Tables 3/4a — so we do not need to re-derive it. 🟢

No evidence anywhere (papers, UI, project pages) that the deployed simulator
runs on OpenQuake. The papers describe a self-contained chain (QAFI → Akkar →
Navarro site effect → IDCM → damage → debris) built without naming OpenQuake as
a component, even though they note the project used open-source software and
libraries generally. 🟡 OpenQuake does appear in *other*, later UPM/MERISUR
hazard work (regional PSHA), but that's a separate line of research from the web
tool. 🟢/🟡

### 4.3 Site effect

Lorca-specific **5-class soil microzonation** from Navarro et al. (2014),
*"Local site effect microzonation of Lorca town (SE Spain)"*, built from
geophysical measurements (Vs profiles etc.). 🟢 This is the single most
Lorca-specific, least-portable piece of the chain — there is no equivalent
microzonation for most of Spain, so a national clone must substitute something
else entirely (Vs30 / Eurocode 8 soil class — see the national-expansion notes
in `initial-chatgpt.md`).

**Resolved for milestone 2, national-scale**: [ADR-0015](./decisions/0015-eshm20-site-amplification.md)
adopts ESRM20 (European Seismic Risk Model 2020)'s national Vs30 grid
(EFEHR/SED-ETH Zürich, CC BY 4.0, ~30 arc-second resolution) instead of
waiting on a Lorca-only source, feeding it directly into the Akkar et al.
(2014) GMPE's own built-in Vs30 site term (§4.2). 🟢 The paper itself
(above) stays paywalled, but follow-up MASW/HVSR papers from the same
group report Lorca's most-damaged 2011 zones as EC8 classes B2
(360–500 m/s) and C (180–360 m/s) — checked ESRM20's grid directly against
this and found real Lorca buildings backfilled to 258–641 m/s (median
388), landing in the same B2/C range, just not quite reaching the ~180
m/s floor the finest riverbed MASW spot measurements found. 🟡 for "roughly
right, coarser than a real survey" as a characterization; 🔴 for an actual
quantified error bar, which is what `questions-for-upm.md` #2 is now
asking for specifically.

### 4.4 Exposure (buildings)

Minimum shapefile schema required by the simulator, stated explicitly in the
2018 paper: **geometry (footprint), number of stories, vulnerability class.** 🟢

The richer MERISUR *research* exposure database (not necessarily what the live
tool ingests) draws on: 🟢
- **Catastro** (Spanish cadastre) — footprint, some attributes.
- **PNOA** aerial photography + LiDAR (Plan Nacional de Ortofotografía Aérea) —
  height, roof type/slope, 3D geometry.
- Manual **fieldwork** campaigns in Lorca neighborhoods (structural system,
  irregularities, soft-storey, non-structural elements) — this is exactly the
  category of work `twiner` has ruled out doing itself (see project brief: "no
  field work will be done").
- Post-2011-earthquake ground-truth damage database, used mainly to calibrate
  vulnerability. 🟢

We found **no public download** of the actual Lorca building/vulnerability
shapefile used by the live tool (checked UPM's GIIS group page, Zenodo,
Archivo Digital UPM). 🔴 Practical implication for `twiner`: even a "clone of
MERISUR for Lorca" cannot reuse MERISUR's own exposure data as a starting
point — we'd have to (re)derive it from Catastro/OSM ourselves, same as for the
rest of Spain. This removes any incentive to special-case Lorca for milestone 1
purely to "reuse their data" — there's nothing to reuse.

### 4.5 Vulnerability / building typology

Three, partially independent, lines of work feed into "vulnerability class,"
and the papers caution not to conflate them: 🟢

1. **Empirical/EMS-98 classification** from field campaigns (structural system,
   soft-storey, height irregularities, position within block, etc.).
2. **Mechanical models**: representative FE models per building class, pushover
   analysis → capacity curves → fragility curves. This is where **Risk-UE**
   Model Building Types come in — remote-sensing work identified **6 Risk-UE
   MBTs in Lorca: one reinforced-concrete class, five masonry classes.** 🟢
   Crucially, the *simulator itself does not run pushover per building* — it
   receives a pre-assigned vulnerability class and looks up its
   pre-computed capacity/fragility curve. Computationally this makes the live
   tool cheap; the expensive part (deriving representative curves) was done
   offline, once, during the research phase. 🟢
3. **Remote-sensing + ML classification**: LiDAR + orthophoto + satellite
   features classified via Decision Trees / SVM / Logistic Regression /
   Bayesian Networks, ~77–80% accuracy/F1 in Lorca trials. 🟢 This is a research
   result about *how MERISUR assigned* vulnerability classes at scale, not
   necessarily something the live tool executes at request time.

**A fourth, separate line found later (2025), while chasing why twiner's
own damage output stayed far below MERISUR's even after site amplification
was added — ADR-0015, `validation-lorca-2011.md` §10.7):** a paper titled
*"Vulnerabilidad y daño en el terremoto de Lorca de 2011"* and a related
Bulletin of Earthquake Engineering paper propose new **RISK-UE Level 1
(LM1) Vulnerability Index Method** behaviour modifiers, derived by fitting
against Lorca's own 2011 damage data. 🟢 for the papers' existence and
their Lorca-specific recalibration; 🔴 for whether this method (as opposed
to, or alongside, the mechanical/IDCM chain in §4.6) is actually what the
*live* MERISUR tool runs at request time — not established either way yet,
flagged as `questions-for-upm.md` #1(b). Worth spelling out precisely
because it's a **different category of model**, not just different
numbers: RISK-UE LM1 is semi-empirical/macroseismic (a Vulnerability Index
per building type, calibrated directly against real EMS-98 damage
statistics from Mediterranean/Italian masonry earthquakes), whereas
`twiner`'s Martins & Silva (2020) substitute (§4.6, `validation-lorca-2011.md`)
is a globally-averaged *analytical* model (nonlinear time-history analysis
of representative archetypes, not calibrated against any real
Mediterranean masonry damage record). Analytically-derived global curves
are documented in the literature to run more conservative (lower P(damage)
at a given intensity) than damage-calibrated semi-empirical ones for
exactly this building type — a plausible, though not yet confirmed,
explanation for a large share of the remaining twiner/MERISUR gap.

### 4.6 Damage model: IDCM (FEMA 440)

Explicitly named: *"expected damage is calculated using an analytical model
(IDCM, ATC 2005)"* — ATC (2005) = FEMA 440, *Improvement of Nonlinear Static
Seismic Analysis Procedures*. 🟢 IDCM = **Improved Displacement Coefficient
Method**: an equivalent-SDOF nonlinear-static procedure that converts a demand
spectrum + a capacity curve into a displacement/performance point, then maps
that onto a damage-state probability distribution (None / Slight / Moderate /
Extensive / Complete, plus "Not estimated"). 🟢 This is *not* a full dynamic or
per-building FEM simulation — the heavy structural analysis happened once,
offline, to build representative capacity curves per class (§4.5); IDCM at
request time is a closed-form-ish, fast calculation. 🟢

### 4.7 Probability / uncertainty levels

Three selectable scenario levels change *both* the ground-motion percentile and
the damage percentile used, not just earthquake size: 🟢

| Level | Ground motion | Damage |
|---|---|---|
| High probability | median | modal damage state |
| Low probability / high impact | median + 1σ | modal damage state |
| Very low probability / very high impact | median + 1σ | 85th-percentile damage state |

### 4.8 Debris model

The mechanism (2018-tool version, simple): **damage state + façade area →
debris**, then allocated spatially from the façade toward the street. 🟢
Published debris extents by damage state: 🟢

| Damage state | Debris extent from façade |
|---|---|
| Slight | 1 m |
| Moderate | 2 m |
| Extensive | 3 m |
| Complete | 4 m |

The 2023 Natural Hazards paper (paywalled — we only have title/abstract, not
equations) formalizes a more complete **Debris Accumulation Model** and
validates it against real 2011 Lorca emergency-response / debris-removal
records, extending risk explicitly onto sidewalks and roads and reportedly
reproducing observed debris volumes to the right order of magnitude and
building damage to within roughly half a damage grade. 🟢 for the existence and
validation claim; 🔴 for the actual formulas — **getting institutional access to
this paper (Natural Hazards, 2023, DOI 10.1007/s11069-023-05911-4) should be a
concrete follow-up** before we attempt to reproduce the debris model faithfully;
until then, treat the 1/2/3/4 m table above as our only grounded reference and
build our own explicitly-simpler debris model for the MVP (see plan, milestone
1 scope).

### 4.9 What MERISUR explicitly is *not*

Worth stating for our own scope discipline — it is *not*: a dynamic
time-history structural simulation; a per-building FEM run at request time; a
ballistic/fragment simulation of falling debris; a CFD/DEM physical rubble
simulation; and (per available evidence) not an OpenQuake deployment. 🟢 It's
best described as *fast attenuation model + simplified nonlinear-static capacity
check + GIS debris heuristic*, deliberately lightweight so it can run
interactively in a browser session.

## 5. UI/UX of the live tool (v1.2.2)

From the live tool at `merisur.topografia.upm.es`: 🟢

- **Source definition**: "Available Faults" list + map selection +
  "Generate Max Magnitude" (automatic), or "Start to Edit" for manual rupture
  entry + "Generate This Earthquake."
- **Scenario probability selector**: the three levels from §4.7.
- **Outputs**: a results map over Lorca buildings; optional "Damage and
  Debris" overlay (flagged in the UI as computationally heavier); explicit
  "Load result on map" / "Load debris on map" actions, i.e. damage and debris
  are two separate, sequential computation/rendering steps rather than one
  atomic action.
- Static **Home / Information / Useful Links / Contact** navigation, no sign
  of authentication, dataset upload, or scenario save/share.

This maps well onto a first UX shape for `twiner`'s MVP: source panel → run →
damage layer → (optional/expensive) debris layer.

## 6. Data source summary table

| Component | Source | Portable beyond Lorca? |
|---|---|---|
| Faults | QAFI (IGME), CC BY-SA 4.0 | 🟢 national |
| Manual rupture | user input | 🟢 always |
| GMPE | Akkar et al. 2014 (in OpenQuake `hazardlib`) | 🟢 national/European |
| Site effect | Navarro et al. 2014, Lorca-only 5-class microzonation | 🟢 national substitute shipped: ESRM20 Vs30 grid (ADR-0015), roughly matches Navarro's reported EC8 classes at Lorca per a follow-up-paper check |
| Exposure base | Catastro + PNOA LiDAR/orthophoto + fieldwork | 🟡 Catastro/PNOA are national; fieldwork isn't reproducible under our constraints |
| Typology | Risk-UE MBTs (6 classes identified in Lorca) | 🟢 Risk-UE/GEM taxonomies are general; the *classification* of Lorca's stock isn't |
| Damage model | IDCM / FEMA 440 | 🟢 general method, needs capacity curves per class |
| Debris (simple) | damage state → 1/2/3/4 m façade buffer | 🟢 portable heuristic, coarse |
| Debris (2023 model) | Gaspar-Escribano et al. 2023, calibrated on 2011 Lorca data | 🔴 formulas not yet in hand |

## 7. Open questions / follow-ups

See [`questions-for-upm.md`](./questions-for-upm.md) for these written up as
concrete questions to send the MERISUR team, with context and what we'd do
with each answer.

1. **Get full-text access to Gaspar-Escribano et al. (2023)** — the only source
   likely to contain the actual debris-volume equations rather than the
   simplified 2018 façade-buffer table.
2. ~~Confirm whether QAFI v3 (MERISUR-era) Mmax values differ meaningfully from
   current QAFI v4~~ — **resolved**: QAFI v4's official shapefile already
   publishes a literature-sourced Mmax (6.7) for the Alhama de Murcia
   segment nearest Lorca, consistent with MERISUR's 6.9. See the corrected
   §4.1 above and [ADR-0004](./decisions/0004-qafi-shapefile-source.md).
   `twiner` now uses that published value where available and only falls
   back to a length-based estimate (on the official `Length` field) for the
   ~40% of faults with no published Mmax.
3. No public Lorca building/vulnerability dataset was found — confirms
   `twiner` must build exposure from Catastro/OSM from day one, even for a
   Lorca-scoped MVP; there's no MERISUR dataset to bootstrap from.
4. The live tool's actual request/response format (network calls, whether
   computation is server-side or client-side, tile formats) is unknown — worth
   a short, read-only inspection of the deployed site (network tab) before
   finalizing our own architecture, purely for UX/interaction-pattern
   reference, not for reuse of any code or data.

## Sources

- [MERISUR project site (UPM blogs)](https://blogs.upm.es/merisur/)
- [MERISUR live simulator v1.2.2](http://merisur.topografia.upm.es/index.html)
- Pouye Yazdi, Gaspar-Escribano, Martínez-Cuevas & Chavarria-Meneces (2018), *A Web-Based Tool for Scenario-Based Seismic Risk Assessment*, [CAEE conference PDF](https://www.caee.ca/12CCEEpdf/192-Hr3V-149.pdf), also [EGU 2018 abstract](https://ui.adsabs.harvard.edu/abs/2018EGUGA..20.1981Y/abstract)
- Gaspar-Escribano et al. (2017), *Methodology for an effective risk assessment of urban areas: progress and first results of the MERISUR project*, [Archivo Digital UPM](https://oa.upm.es/49862/)
- Gaspar-Escribano et al. (2023), *Extending urban seismic risk assessment to open spaces for the 2011 Lorca earthquake scenario*, Natural Hazards, [Springer](https://link.springer.com/article/10.1007/s11069-023-05911-4) (abstract only — paywalled)
- [QAFI — Quaternary Active Faults Database of Iberia (IGME)](https://info.igme.es/qafi/)
- [OpenQuake hazardlib: Akkar et al. 2014 GSIM](https://docs.openquake.org/old/oq-hazardlib/0.19/gsim/akkar_2014.html)
- Núñez Murillo, A. (2017), UPM doctoral thesis on seismic scenario simulation for the Iberian Peninsula, Balearic and Canary Islands, [Archivo Digital UPM](https://oa.upm.es/47779/) — adjacent UPM work, not MERISUR itself, useful for national-scale site-effect ideas later.
- Navarro, M., A. García-Jerez, F. Alcalá, F. Vidal, T. Enomoto (2014), *Local site effect microzonation of Lorca town (southern Spain)*, Bulletin of Earthquake Engineering, [Springer](https://link.springer.com/article/10.1007/s10518-013-9491-y) (paywalled — abstract/citation only)
- Follow-up MASW/HVSR papers by the same group (used for §4.3's EC8-class numbers, since the main paper above is paywalled): *Shear Wave Velocity Structure for Seismic Microzonation of Lorca town (SE Spain) from MASW Analysis* and *Shear-wave velocity based seismic microzonation of Lorca city (SE Spain) from MASW analysis*, both via [ResearchGate](https://www.researchgate.net/publication/266633194) / [Earthdoc](https://www.earthdoc.org/content/papers/10.3997/2214-4609.20131351)
- ESRM20 (European Seismic Risk Model 2020) repository, EFEHR/SED-ETH Zürich, CC BY 4.0: [gitlab.seismo.ethz.ch/efehr/esrm20](https://gitlab.seismo.ethz.ch/efehr/esrm20) — Vs30 site model source for ADR-0015
- *Vulnerabilidad y daño en el terremoto de Lorca de 2011*, [ResearchGate](https://www.researchgate.net/publication/259199076_VULNERABILIDAD_Y_DANO_EN_EL_TERREMOTO_DE_LORCA_DE_2011_Vulnerability_and_earthquake_damage_in_Lorca_2011) — RISK-UE LM1 Vulnerability Index Method applied to Lorca's 2011 damage (§4.5)
- *Proposal for new values of behaviour modifiers for seismic vulnerability evaluation of reinforced concrete buildings applied to Lorca (Spain) using damage data from the 2011 earthquake*, Bulletin of Earthquake Engineering, [Springer](https://link.springer.com/article/10.1007/s10518-017-0100-3) — Lorca-recalibrated RISK-UE LM1 behaviour modifiers (§4.5)
- [`initial-chatgpt.md`](./initial-chatgpt.md) — exploratory conversation (not a primary source; used only to shape research questions)
