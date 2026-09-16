"""Integration test against real pipeline output, if present locally (see
test_engine_integration.py for the same pattern)."""

from pathlib import Path

import pytest
from scenario.faults import get_fault, load_nearby_faults

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
FAULTS = DATA_DIR / "faults" / "qafi_faults.parquet"

pytestmark = pytest.mark.skipif(not FAULTS.exists(), reason="run pipelines/faults locally first")

LORCA_LAT, LORCA_LON = 37.6733, -1.7013


def test_alhama_de_murcia_is_the_nearest_fault_to_lorca():
    # The fault responsible for the real 2011 Lorca earthquake
    # (docs/merisur.md, docs/milestone-1-plan.md task 9) should come back
    # as the closest QAFI fault to Lorca -- a good sanity check that our
    # nearest-point query is wired correctly, independent of any scenario
    # math.
    faults = load_nearby_faults(str(FAULTS), LORCA_LAT, LORCA_LON, radius_km=50)
    assert len(faults) > 0
    assert "Alhama de Murcia" in faults.iloc[0]["name"]
    assert faults.iloc[0]["distance_km"] < 5


def test_radius_filters_out_far_faults():
    faults = load_nearby_faults(str(FAULTS), LORCA_LAT, LORCA_LON, radius_km=5)
    assert (faults["distance_km"] <= 5).all()


def test_get_fault_raises_for_unknown_id():
    with pytest.raises(KeyError):
        get_fault(str(FAULTS), "NOT-A-REAL-FAULT-ID", LORCA_LAT, LORCA_LON)


def test_get_fault_returns_the_requested_fault():
    faults = load_nearby_faults(str(FAULTS), LORCA_LAT, LORCA_LON, radius_km=50)
    fault_id = faults.iloc[0]["fault_id"]
    fault = get_fault(str(FAULTS), fault_id, LORCA_LAT, LORCA_LON)
    assert fault["fault_id"] == fault_id
