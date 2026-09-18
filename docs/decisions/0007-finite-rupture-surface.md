# ADR-0007: Finite rupture surface for automatic-mode faults

Status: accepted

## Context

`rupture.py` documented a deliberate MVP simplification: every rupture is a
**point source**, and the Joyner-Boore distance (Rjb) the Akkar et al.
(2014) GMPE needs is approximated as the geodesic distance from each
building to that single point. This was known to overstate ground motion
right next to a long fault, where the real Rjb (distance to the nearest
point on the rupture's surface projection) should be ~0 along the whole
trace, not just at one point on it.

Investigating this (prompted by a user question about why MERISUR's manual
mode exposes strike/dip/Ztor and ours doesn't) established:

- Akkar et al. (2014) itself doesn't consume strike/dip/Ztor at all --
  confirmed directly from `hazardlib`'s source:
  `REQUIRES_RUPTURE_PARAMETERS = {'rake', 'mag'}`,
  `REQUIRES_DISTANCES = {'rjb'}`. They matter only for computing Rjb
  correctly from a *finite* rupture plane, not for the GMPE equation.
- QAFI's shapefile publishes everything needed to build that plane --
  `Dip`, `MinDepth` (Ztor), `MaxDepth`, plus the trace geometry itself --
  for **100% of its 201 faults** (verified directly), unlike Mmax (~60%).
  So this isn't a sparse-data feature that only helps a minority of faults.
- A real prototype against Alhama de Murcia confirmed the effect directly:
  sites along the trace but away from the anchor point got Rjb=0 from a
  real `SimpleFaultSurface` vs. up to 17.7km from the point-source
  approximation for the same sites.

## Decision

For automatic-mode faults, build a real finite rupture surface using
`openquake.hazardlib.geo.surface.simple_fault.SimpleFaultSurface`, fed
directly from QAFI's own trace geometry + `Dip` + `MinDepth` +
`MaxDepth` (`services/scenario/src/scenario/surface.py`). No hand-rolled
plane geometry -- hazardlib already implements Rjb (and Rrup/Rx, if ever
needed) correctly for a surface built this way.

`Rupture` (rupture.py) gains an optional `surface` field. When present,
`ground_motion.compute_intensity` computes Rjb via
`surface.get_joyner_boore_distance(mesh)` instead of the geodesic
point-to-point approximation. `None` (manual mode, or any automatic-mode
fault whose geometry hazardlib rejects) keeps the point-source fallback
unchanged -- this is additive, not a replacement of the existing path.

QAFI's `Dip`/`AverageStr` fields were also found to be stored as **text**
in the shapefile's DBF (unlike `Rake`/`MinDepth`/`MaxDepth`, which are
numeric) and were silently carried as strings all the way into
`faults.parquet` -- fixed as part of this change (`pipelines/faults`), since
`SimpleFaultSurface` needs `dip` as a float and would have failed outright.

A fault's `MultiLineString` trace (some QAFI faults are mapped as several
disconnected pieces) is reduced to its longest single `LineString`
component for surface construction -- `SimpleFaultSurface` needs one
continuous line. The full trace is still used as-is for map display
(`geometry_geojson`); only surface construction simplifies it.

## Alternatives considered

- **Hand-rolled plane geometry** (compute the 4 corner points ourselves
  from strike/length/width/dip/Ztor, feed `PlanarSurface` directly):
  rejected -- `SimpleFaultSurface.from_fault_data` takes the trace + dip +
  depth range directly, which is exactly QAFI's own data shape, and
  handles multi-vertex (non-straight) traces correctly. Reinventing this
  risks getting the geometry subtly wrong where hazardlib is already
  tested.
- **Exposing strike/dip/Ztor in manual mode too, at the same time**: kept
  out of this change on purpose -- manual mode has no fault trace to build
  a plane from, so exposing those fields there is a separate, smaller
  UI/API task (tracked separately), not a natural extension of this one.

## Consequences

- `pipelines/faults` output gains `min_depth_km`/`max_depth_km` columns,
  and `dip`/`strike` are now correctly typed as floats (previously
  silently strings -- a latent bug this change surfaced and fixed).
- `from_fault()` gained four new optional parameters
  (`geometry_geojson`/`dip`/`min_depth_km`/`max_depth_km`); omitting any of
  them (or passing a geometry hazardlib rejects) is a normal, tested path,
  not an error -- existing callers/tests that only pass the original
  arguments are unaffected.
- `Rupture.strike`/`Rupture.dip` (previously unused decorative fields) were
  removed in favor of the single functional `surface` field -- confirmed
  unreferenced anywhere before removing them.
- The scenario API's response now reports `rupture.finite_rupture: bool`,
  so it's visible (not hidden) which approximation produced a given result.
- Mesh spacing for the surface is fixed at 2km (`DEFAULT_MESH_SPACING_KM`)
  -- measured sub-second even for QAFI's longest fault (282km) against
  50,000 sites; tighter spacing bought negligible extra accuracy for real
  compute cost.
