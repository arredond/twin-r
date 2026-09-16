"""twin-r faults pipeline: QAFI (IGME) active faults -> faults.parquet.

Source: IGME's official QAFI v4 shapefile download (see ADR-0004,
docs/decisions/0004-qafi-shapefile-source.md, for why this replaced an
earlier ArcGIS MapServer REST approach):

    https://info.igme.es/qafi/docs/QAFI_Traces.rar

Requires `unar` on PATH to extract the archive (`brew install unar`).

QAFI v4 publishes a real Mmax for ~60% of its 201 faults (Source: Literature
Data); we compute a fallback Mmax from the official trace length using the
Wells & Coppersmith (1994) empirical relation for the rest, and flag every
fault with whether its Mmax is QAFI-provided or estimated.
"""

from .mmax import mmax_from_length_km
from .source import fetch_qafi_faults, resolve_mmax, write_faults_parquet

__all__ = [
    "fetch_qafi_faults",
    "mmax_from_length_km",
    "resolve_mmax",
    "write_faults_parquet",
]
