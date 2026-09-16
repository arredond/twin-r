"""Fallback maximum-magnitude estimation from fault trace length.

QAFI v4 only publishes Mmax/recurrence where a paleoseismological estimate
has been published (docs/merisur.md §4.1) — most faults in the database have
no such value. Rather than drop those faults from the "automatic" rupture
picker, we estimate Mmax from surface rupture length using the Wells &
Coppersmith (1994) empirical regression for all fault types combined:

    Mw = 5.08 + 1.16 * log10(SRL_km)

Wells, D.L. & Coppersmith, K.J. (1994), "New Empirical Relationships among
Magnitude, Rupture Length, Rupture Width, Rupture Area, and Surface
Displacement", Bulletin of the Seismological Society of America, 84(4).

This is a deliberate simplification (documented in docs/merisur.md §7.2): it
does not distinguish fault mechanism (normal/reverse/strike-slip), which
Wells & Coppersmith also provide coefficients for, and it treats the full
mapped trace length as the surface rupture length, which overestimates Mmax
for faults that rupture only partially in a single event. Good enough for an
MVP "pick a fault, get a plausible worst-case earthquake" flow; revisit if
we need defensible absolute numbers.
"""

import math


def mmax_from_length_km(length_km: float) -> float:
    """Estimate Mw from surface rupture length (km) via Wells & Coppersmith (1994)."""
    if length_km <= 0:
        raise ValueError(f"length_km must be positive, got {length_km}")
    return 5.08 + 1.16 * math.log10(length_km)
