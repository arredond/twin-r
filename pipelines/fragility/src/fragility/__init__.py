"""twin-r fragility pipeline: Martins & Silva (2020) global fragility functions.

Source: https://github.com/lmartins88/global_fragility_vulnerability
(CC BY-SA 4.0 -- attribution required, see docs/merisur.md §4.5/§4.9 and
docs/milestone-1-plan.md §2 for why we use this instead of MERISUR's own,
Lorca-specific capacity curves).

Martins, L. & Silva, V. (2020), "Development of a Fragility and
Vulnerability Model for Global Seismic Risk Analyses", Bulletin of
Earthquake Engineering.

We vendor a curated subset (not the full ~561-file repository) covering the
GEM-taxonomy classes our exposure pipeline's taxonomy heuristic
(pipelines/exposure) can actually produce: reinforced-concrete dual-system
low-ductility (CR_LDUAL-DUL) and unreinforced masonry load-bearing-wall
low-ductility (MR_LWAL-DUL), across the height classes (H1..H12) relevant to
Lorca's building stock.
"""

from .source import TAXONOMY_CLASSES, fetch_fragility_functions, write_fragility_parquet

__all__ = ["TAXONOMY_CLASSES", "fetch_fragility_functions", "write_fragility_parquet"]
