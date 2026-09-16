# ADR-0004: Source QAFI faults from the official shapefile, not the ArcGIS MapServer

Status: accepted

## Context

`pipelines/faults` originally queried IGME's public ArcGIS MapServer REST
layer (`mapas.igme.es/gis/rest/services/BasesDatos/IGME_QAFI/MapServer/0`)
for QAFI fault data — discovered via search, undocumented as a bulk-access
endpoint, but working and convenient (single request, clean GeoJSON).

Investigating a discrepancy between our computed Mmax for the Alhama de
Murcia fault (7.38, length-based estimate) and MERISUR's reported value
(6.9) led to downloading and inspecting QAFI v4's official download
artifact instead: `QAFI_Traces.rar` (236 KB, a zipped/RAR'd shapefile) from
`info.igme.es/qafi/docs/`. See `docs/merisur.md` §4.1 and
`docs/validation-lorca-2011.md` §6 for the full investigation.

Findings that make this a real decision, not just a preference:

- The MapServer layer exposes only 8 fields (id, name, section, slip-rate
  class, reliability class, etc.) — no length, no Mmax, no recurrence, no
  rake/dip/strike.
- The official shapefile carries the complete QAFI v4 schema per IGME's
  own field-description document: `Length`, `MaxMagnitu` (+ range/source/
  comment), `Recurrence`, `Rake`, `Dip`, `AverageStrike`, reliability
  ratings. **60% of QAFI's 201 faults have a real, literature-published
  Mmax** in this file.
- The MapServer's *geometry* for "Alhama de Murcia (1/4)" is ~96 km long;
  the shapefile's official record for the same fault ID is 30 km — a
  roughly 3x discrepancy that directly explains our earlier bad Mmax
  estimate. The MapServer geometry for this fault is simply wrong (or at
  least inconsistent with the authoritative v4 dataset), not just missing
  attributes.

## Decision

Fetch QAFI from the official shapefile download
(`info.igme.es/qafi/docs/QAFI_Traces.rar`), not the ArcGIS MapServer REST
layer. Extract the RAR via `unar` (BSD-licensed, from The Unarchiver
project) — not the non-free `unrar` binary. Use the shapefile's own
`MaxMagnitu` field where populated (`> 0`); fall back to our
Wells & Coppersmith (1994) length-based estimate, computed on the
shapefile's own `Length` field, only for the ~40% of faults without a
published value. Carry `Rake`/`Dip`/`AverageStrike` through as well, so
automatic-mode ruptures can use QAFI's actual focal mechanism instead of
the previous hardcoded rake = 0 default.

## Alternatives considered

- **Keep the MapServer layer, just don't trust its geometry for length
  calculations** (e.g. re-derive length from a separate authoritative
  source while keeping the MapServer for everything else): more moving
  parts for no benefit once the official file is this small and easy to
  fetch.
- **Query IGME's WFS/WMS services instead**: same category of problem as
  the MapServer — live-query services aimed at interactive maps, not
  guaranteed to carry the full attribute schema, and no evidence they'd be
  more reliable than the REST layer we already found broken.

## Consequences

- One new pipeline dependency: `unar` must be on `PATH` (documented like
  `tippecanoe` already is for `pipelines/exposure`).
- `faults.parquet` gains real `mmax_source` provenance: `"qafi_v4_published"`
  vs. `"estimated_wells_coppersmith_1994"`, instead of every fault being
  estimated.
- Automatic-mode ruptures can use QAFI's real rake instead of assuming
  strike-slip (rake = 0) for every fault.
- The RAR file is small (236 KB) and updated infrequently (`Last-Modified`
  observed: May 2022) — no meaningful cost to re-fetching it on every
  pipeline run rather than caching indefinitely.
