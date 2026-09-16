"""Precompute per-building debris envelopes (ADR-0010).

Geometry, not scenario data: a building's exterior (non-party-wall)
boundary and its neighbors don't change between scenario runs, so debris
rings are computed once here, offline, the same way `parse.py`/`tile.py`
handle building footprints (docs/decisions/0003). A scenario run only ever
needs to pick which of a building's precomputed rings are active, off the
`damage_state_code` it already computes (services/scenario/response.py) --
this module produces the rings, nothing scenario-specific.

Ring distances are the 2018 MERISUR tool's damage-state -> facade-buffer
table (docs/merisur.md §4.8): ring 1 = Slight/1m ... ring 4 = Complete/4m.
This lines up exactly with `DAMAGE_STATE_CODES` in
services/scenario/src/scenario/response.py (None=0, Slight=1, ...,
Complete=4) by construction, not coincidence -- a scenario's
`damage_state_code` is meant to be used directly as "show every ring
<= this number" at render time, so `ring` must equal the damage-state
code it first becomes visible at. Changing one without the other breaks
that contract.
"""

from __future__ import annotations

import geopandas as gpd
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from shapely.validation import make_valid

RING_DISTANCES_M: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0)

# How close two footprints have to be to count as sharing a party wall,
# rather than sitting across a narrow gap/alley. Catastro polygons for
# adjoining buildings are rarely topologically exact (small snapping gaps,
# sliver overlaps), so requiring true touching (distance 0) is too strict;
# a few tens of cm catches a real shared wall without also swallowing a
# genuine narrow passageway. Unvalidated against real data yet -- flagged
# in ADR-0010 alongside the street/open-space clipping follow-up.
WALL_TOLERANCE_M = 0.3

# shapely's default buffer resolution (8 segments per quarter-circle) is
# far finer than a 1-4m debris ring needs, and the cost compounds: every
# ring is itself differenced against every neighbor's buffer, and each
# boolean op roughly preserves (or worsens, at slivers) the input vertex
# count. Measured directly against real Lorca data (27,884 buildings):
# default resolution produced a 77MB debris.pmtiles for a town whose
# buildings.pmtiles is 4.1MB (18.8x) -- most of that was avoidable curve
# detail, not real geometric complexity. Dropping to 3 segments/quarter
# (a 12-sided approximation of a circle) plus SIMPLIFY_TOLERANCE_M below
# cut average polygon vertex count from ~57 to ~24 with no visible loss at
# map scale (a debris ring is already a coarse heuristic, not a precise
# footprint).
BUFFER_QUAD_SEGS = 3

# Post-hoc simplification tolerance for each output band, after all
# buffer/difference ops -- removes the small slivers and colinear-ish
# points those boolean ops tend to leave behind, on top of the coarser
# buffer resolution above.
SIMPLIFY_TOLERANCE_M = 0.15


def _polygonal_only(geom: BaseGeometry) -> BaseGeometry:
    """Drop non-polygonal parts (points/lines) a boolean op can leave behind
    at floating-point-precision edges -- a debris ring is only ever
    meaningful as an area, and PMTiles/tippecanoe expects one geometry type
    per feature collection, not a stray GeometryCollection."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return geom
    if isinstance(geom, GeometryCollection):
        parts = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        if parts:
            return unary_union(parts)
    return Polygon()


def compute_debris_envelopes(buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Compute nested 1-4m debris rings for every building.

    `buildings` must have `building_id` + `geometry` columns, any CRS --
    buffering happens in a locally estimated metric CRS (`estimate_utm_crs`)
    so ring distances are true metres regardless of the input CRS, then
    results are reprojected back to `buildings`' own CRS.

    Returns a GeoDataFrame with one row per (building_id, ring) that has
    non-empty geometry -- `ring` is an int, 1-4 (see module docstring for
    why this must equal `DAMAGE_STATE_CODES`). A building fully enclosed by
    neighbors on every non-party-wall side (or with no exterior boundary
    left after wall exclusion) contributes no rows at all, rather than
    empty-geometry rows.

    Ring geometry only ever buffers outward from a building's exterior
    (non-party-wall) boundary, and is differenced against every nearby
    building's own footprint -- see ADR-0010 for why (dense row-house
    fabric, e.g. Lorca's old town, shares walls; an omnidirectional buffer
    would paint debris on top of the neighboring building instead of open
    space).
    """
    empty = gpd.GeoDataFrame({"building_id": [], "ring": []}, geometry=[], crs=buildings.crs)
    if buildings.empty:
        return empty

    original_crs = buildings.crs
    assert original_crs is not None, "buildings GeoDataFrame must have a CRS set"
    metric_crs = buildings.estimate_utm_crs()
    metric = buildings[["building_id", "geometry"]].to_crs(metric_crs).reset_index(drop=True)
    # Real Catastro footprints occasionally have minor topology defects
    # (self-touching rings, near-duplicate vertices) invisible until a
    # boolean op downstream throws GEOSException ("side location conflict")
    # deep inside a buffer/difference call -- found running this at
    # region scale (Murcia+Andalucía), one whole municipality (23067) lost
    # entirely to a single bad input geometry before this fix. `make_valid`
    # is GEOS's own repair for exactly this class of defect; cheap relative
    # to the buffering this feeds into, and applied once here rather than
    # relying on every downstream op to tolerate invalid input.
    #
    # `make_valid` can turn a broken Polygon into a GeometryCollection (a
    # mix of the salvaged polygon plus stray points/lines from the
    # self-intersection) -- found running this at *national* scale (3 of
    # ~2,400 municipalities into a full-Spain run): `.boundary` on a
    # GeometryCollection silently returns `None` rather than raising, which
    # then surfaces several calls later as a confusing
    # `AttributeError: 'NoneType' object has no attribute 'is_empty'`, not
    # a catchable GEOSException. `_polygonal_only` (defined above for the
    # same reason on ring *outputs*) reduces the repaired input back to
    # just its polygonal part before anything else touches it.
    # pandas-stubs' Series.apply overloads don't cover a Callable returning
    # a shapely BaseGeometry -- same known stubs limitation as damage.py's
    # groupby(...).indices annotation, not a real type error.
    metric["geometry"] = metric["geometry"].apply(  # pyrefly: ignore
        lambda g: _polygonal_only(g if g.is_valid else make_valid(g))
    )

    max_dist = max(RING_DISTANCES_M) + WALL_TOLERANCE_M
    sindex = metric.sindex
    geoms = metric["geometry"].to_numpy()
    building_ids = metric["building_id"].to_numpy()

    out_building_id: list = []
    out_ring: list[int] = []
    out_geom: list = []

    for i, geom in enumerate(geoms):
        if geom.is_empty:
            continue  # nothing left after sanitization above (e.g. an unrepairable footprint)
        try:
            candidate_idx = sindex.query(geom.buffer(max_dist), predicate="intersects")
            neighbor_idx = [j for j in candidate_idx if j != i]
            neighbors = unary_union(geoms[neighbor_idx]) if neighbor_idx else None

            exterior_boundary = geom.boundary
            if neighbors is not None:
                exterior_boundary = exterior_boundary.difference(
                    neighbors.buffer(WALL_TOLERANCE_M, quad_segs=BUFFER_QUAD_SEGS)
                )
            if exterior_boundary.is_empty:
                continue  # party-walled on every side, e.g. a mid-block interior unit

            previous_cumulative = None
            for ring_number, distance in enumerate(RING_DISTANCES_M, start=1):
                cumulative = exterior_boundary.buffer(
                    distance, quad_segs=BUFFER_QUAD_SEGS
                ).difference(geom)
                if neighbors is not None:
                    cumulative = cumulative.difference(neighbors)
                band = (
                    cumulative
                    if previous_cumulative is None
                    else cumulative.difference(previous_cumulative)
                )
                previous_cumulative = cumulative
                band = _polygonal_only(band)
                if not band.is_empty:
                    band = band.simplify(SIMPLIFY_TOLERANCE_M)
                    # simplify() (even with its default preserve_topology)
                    # can still produce a self-touching/invalid polygon at
                    # this tolerance for a narrow enough sliver -- shapely
                    # and GeoParquet don't mind, but GDAL's GeoJSON writer
                    # does, and tile_geojson_files needs to write GeoJSON
                    # (found running this at full national scale: a
                    # pyogrio.errors.FeatureError killed the whole tiling
                    # pass over one bad feature out of ~51M). Cheap
                    # relative to the buffering already done; re-run
                    # through the same polygonal-only filter since
                    # make_valid can, again, produce a GeometryCollection.
                    if not band.is_valid:
                        band = _polygonal_only(make_valid(band))
                if band.is_empty:
                    continue
                out_building_id.append(building_ids[i])
                out_ring.append(ring_number)
                out_geom.append(band)
        except (GEOSException, AttributeError, ValueError):
            # make_valid + _polygonal_only above handle the common defects;
            # this is a backstop for whatever they don't catch. Broader
            # than just GEOSException on purpose -- found running this at
            # national scale that a repaired-but-still-degenerate geometry
            # doesn't always *raise*: a GeometryCollection's `.boundary` is
            # `None` rather than an exception, surfacing several calls
            # later as a plain AttributeError, not a GEOS error. Either
            # way, one building's geometry shouldn't cost its whole
            # municipality's debris output (same "one bad unit doesn't
            # abort the run" principle as ADR-0005's per-municipality
            # crawl failures) -- a bare `except Exception` would also catch
            # real bugs (e.g. a typo'd variable name) silently, so this
            # stays scoped to the specific exception shapes actually
            # observed from bad geometry.
            continue

    if not out_building_id:
        return empty

    result = gpd.GeoDataFrame(
        {"building_id": out_building_id, "ring": out_ring},
        geometry=out_geom,
        crs=metric_crs,
    )
    return result.to_crs(original_crs)
