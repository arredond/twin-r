"""Heuristic mapping: Catastro attributes -> GEM-taxonomy-ish vulnerability class.

**This is the least-validated part of the MVP.** No field survey backs it
(per the project's no-fieldwork constraint) -- it's a documented, versioned
guess, not an observation, and every building carries that provenance
(`taxonomy_source = "heuristic_v1"`) rather than silently looking as
authoritative as a surveyed value would.

The heuristic: Spain's shift from unreinforced/confined masonry to
reinforced-concrete-frame construction happened predominantly from the
1960s-70s onward (matching the introduction of modern seismic-resistant
design codes, and the general pattern described for Lorca's own building
stock in docs/merisur.md §4.5 -- 1 RC class vs. 5 masonry classes there,
consistent with masonry dominating the pre-modern stock).

Originally a single year threshold (concrete vs. one generic masonry
class) -- refined to a second threshold per
docs/validation-lorca-2011.md §10.1/§10.4: the generic masonry class alone
turned out to be the *least* vulnerable option available for exactly the
pre-modern, vernacular-construction era it was meant to cover, materially
understating damage for Lorca's old town. Still coarser than the fuller
code-generation timeline sketched in docs/initial-chatgpt.md -- a further
refinement, not the last one, once there's reason to believe *that* changes
results materially in turn.

Output classes match exactly what pipelines/fragility vendors from Martins
& Silva (2020): `CR_LDUAL-DUL` (reinforced concrete, dual lateral system,
low ductility) for newer construction; `MR_LWAL-DUL` (masonry, load-bearing
wall, low ductility) for masonry-era construction with no further
information; `MUR-STRUB_LWAL-DNO` (unreinforced rubble-stone masonry, no
ductility) for construction old enough to plausibly predate even confined/
reinforced masonry practice. "Low ductility"/"no ductility" are
conservative defaults throughout, since Catastro gives us no seismic design
information to distinguish ductility classes.
"""

from __future__ import annotations

import math

CONCRETE_ERA_THRESHOLD_YEAR = 1970

# Below this year, default to the more vulnerable vernacular-masonry class
# (MUR-STRUB_LWAL-DNO) rather than the generic one (MR_LWAL-DUL) --
# docs/validation-lorca-2011.md §10.1 found MR_LWAL-DUL alone understated
# damage for Lorca's old town, most of which predates this era. A judgment
# call, not derived from a documented code-generation date the way
# CONCRETE_ERA_THRESHOLD_YEAR loosely tracks modern seismic codes -- see
# docs/TAXONOMY.md for the reasoning and what would sharpen it (a real
# construction-era breakdown of Lorca's masonry stock, ideally from UPM).
VERNACULAR_MASONRY_THRESHOLD_YEAR = 1940

# Matches the height classes actually vendored in pipelines/fragility
# (Martins & Silva publish H1..H12 at 1-storey granularity for CR_LDUAL-DUL/
# MR_LWAL-DUL; MUR-STRUB_LWAL-DNO only goes to H5 upstream -- any building
# taller than that falls back to H5, same "nearest available height"
# handling every taxonomy class already gets, services/scenario's
# fragility_lookup.py).
_MAX_HEIGHT_CLASS = 12

TAXONOMY_SOURCE = "heuristic_v1"


def assign_taxonomy(construction_year: float | None, floors: float | None) -> tuple[str, int]:
    """Return (taxonomy_class, height_class) for one building.

    `construction_year` and `floors` may be None/NaN (unknown in Catastro);
    both fall back to a documented default rather than raising, since we'd
    rather compute an approximate scenario for every building than drop
    ones with incomplete attributes. An unknown `construction_year`
    defaults to the *older* vernacular-masonry class
    (`MUR-STRUB_LWAL-DNO`), not the generic one -- consistent with treating
    unknown floors as low-rise below: prefer the conservative (more
    vulnerable) guess over the optimistic one when Catastro gives us
    nothing to go on.
    """
    # Each branch re-checks `construction_year is not None and not
    # _is_nan(...)` inline (rather than via a precomputed `has_year` bool)
    # so the type checker can narrow `construction_year` from `float | None`
    # to `float` within each condition -- a stored bool defeats that
    # narrowing even though it's logically equivalent.
    if (
        construction_year is not None
        and not _is_nan(construction_year)
        and construction_year >= CONCRETE_ERA_THRESHOLD_YEAR
    ):
        material = "CR_LDUAL-DUL"
    elif (
        construction_year is not None
        and not _is_nan(construction_year)
        and construction_year >= VERNACULAR_MASONRY_THRESHOLD_YEAR
    ):
        material = "MR_LWAL-DUL"
    else:
        material = "MUR-STRUB_LWAL-DNO"

    if floors is None or _is_nan(floors) or floors < 1:
        height_class = 1  # conservative default: treat unknown as low-rise
    else:
        height_class = min(round(floors), _MAX_HEIGHT_CLASS)

    return material, height_class


def _is_nan(value: float) -> bool:
    return math.isnan(value)
