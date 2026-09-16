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
consistent with masonry dominating the pre-modern stock). We use a single
year threshold rather than the fuller code-generation timeline sketched in
docs/initial-chatgpt.md -- refine into multiple periods once we have reason
to believe it changes results materially.

Output classes match exactly what pipelines/fragility vendors from Martins &
Silva (2020): `CR_LDUAL-DUL` (reinforced concrete, dual lateral system, low
ductility) for newer construction, `MR_LWAL-DUL` (masonry, load-bearing
wall, low ductility) for older construction -- "low ductility" is a
conservative default in both cases, since Catastro gives us no seismic
design information to distinguish ductility classes.
"""

from __future__ import annotations

import math

CONCRETE_ERA_THRESHOLD_YEAR = 1970

# Matches the height classes actually vendored in pipelines/fragility
# (Martins & Silva publish H1..H12 at 1-storey granularity for our classes).
_MAX_HEIGHT_CLASS = 12

TAXONOMY_SOURCE = "heuristic_v1"


def assign_taxonomy(construction_year: float | None, floors: float | None) -> tuple[str, int]:
    """Return (taxonomy_class, height_class) for one building.

    `construction_year` and `floors` may be None/NaN (unknown in Catastro);
    both fall back to a documented default rather than raising, since we'd
    rather compute an approximate scenario for every building than drop
    ones with incomplete attributes.
    """
    material = (
        "CR_LDUAL-DUL"
        if construction_year is not None
        and not _is_nan(construction_year)
        and construction_year >= CONCRETE_ERA_THRESHOLD_YEAR
        else "MR_LWAL-DUL"
    )

    if floors is None or _is_nan(floors) or floors < 1:
        height_class = 1  # conservative default: treat unknown as low-rise
    else:
        height_class = min(round(floors), _MAX_HEIGHT_CLASS)

    return material, height_class


def _is_nan(value: float) -> bool:
    return math.isnan(value)
