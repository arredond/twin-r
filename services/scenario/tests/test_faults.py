"""Integration test against real pipeline output, if present locally (see
test_engine_integration.py for the same pattern)."""

from pathlib import Path

import pytest
from scenario.faults import get_fault, load_faults, rupture_anchor

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
FAULTS = DATA_DIR / "faults" / "qafi_faults.parquet"

pytestmark = pytest.mark.skipif(not FAULTS.exists(), reason="run pipelines/faults locally first")

LORCA_LAT, LORCA_LON = 37.6733, -1.7013


def test_load_faults_returns_every_fault():
    faults = load_faults(str(FAULTS))
    assert len(faults) == faults["fault_id"].nunique() > 100
    assert faults["name"].is_monotonic_increasing


def test_every_qafi_fault_has_rupture_geometry():
    # The premise of making near_lat/near_lon optional: every QAFI v4 fault
    # derives its rupture from its own geometry. If a future dataset breaks
    # this, the frontend starts sending a reference point for just those
    # faults (has_rupture_geometry false) -- not a failure, but worth
    # knowing about.
    assert load_faults(str(FAULTS))["has_rupture_geometry"].all()


def test_get_fault_raises_for_unknown_id():
    with pytest.raises(KeyError):
        get_fault(str(FAULTS), "NOT-A-REAL-FAULT-ID")


def test_alhama_de_murcia_closest_point_to_lorca_is_nearby():
    # The fault behind the real 2011 Lorca earthquake runs right past
    # Lorca -- a sanity check on get_fault's closest-point query.
    faults = load_faults(str(FAULTS))
    fault_id = faults[faults["name"].str.contains("Alhama de Murcia")].iloc[0]["fault_id"]
    fault = get_fault(str(FAULTS), fault_id, LORCA_LAT, LORCA_LON)
    assert abs(fault["closest_lat"] - LORCA_LAT) < 0.1
    assert abs(fault["closest_lon"] - LORCA_LON) < 0.1


def test_rupture_anchor_ignores_near_point_for_faults_with_geometry():
    fault_id = load_faults(str(FAULTS)).iloc[0]["fault_id"]
    without = rupture_anchor(get_fault(str(FAULTS), fault_id))
    with_near = rupture_anchor(get_fault(str(FAULTS), fault_id, LORCA_LAT, LORCA_LON))
    assert without == with_near
    assert without[2] is False
