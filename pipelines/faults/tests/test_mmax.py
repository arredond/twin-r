import math

import pytest
from faults.mmax import mmax_from_length_km


def test_mmax_matches_wells_coppersmith_formula():
    # Mw = 5.08 + 1.16 * log10(SRL_km)
    assert mmax_from_length_km(10) == pytest.approx(5.08 + 1.16 * math.log10(10))
    assert mmax_from_length_km(100) == pytest.approx(5.08 + 1.16 * math.log10(100))


def test_mmax_increases_with_length():
    assert mmax_from_length_km(50) < mmax_from_length_km(100)


def test_mmax_rejects_nonpositive_length():
    with pytest.raises(ValueError):
        mmax_from_length_km(0)
    with pytest.raises(ValueError):
        mmax_from_length_km(-5)
