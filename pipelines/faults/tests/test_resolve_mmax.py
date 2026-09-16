import math

from faults.source import resolve_mmax


def test_uses_published_value_when_available():
    mmax, source = resolve_mmax(6.7, length_km=30.0)
    assert mmax == 6.7
    assert source == "qafi_v4_published"


def test_falls_back_to_length_estimate_when_unpublished_sentinel():
    mmax, source = resolve_mmax(0.0, length_km=30.0)
    assert source == "estimated_wells_coppersmith_1994"
    assert mmax == round(5.08 + 1.16 * math.log10(30.0), 2)


def test_falls_back_to_length_estimate_when_none():
    _mmax, source = resolve_mmax(None, length_km=30.0)
    assert source == "estimated_wells_coppersmith_1994"


def test_alhama_de_murcia_matches_published_value():
    # Real QAFI v4 record (ES626, "Alhama de Murcia (1/4)"): Length 30 km,
    # MaxMagnitu 6.7 -- consistent with MERISUR's reported 6.9
    # (docs/validation-lorca-2011.md §6).
    mmax, source = resolve_mmax(6.7, length_km=30.0)
    assert mmax == 6.7
    assert source == "qafi_v4_published"
