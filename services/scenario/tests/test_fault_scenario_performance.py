"""Real-scale performance integration test for /scenarios/fault and
/scenarios/manual, against the full national dataset if present locally.

test_local_api.py's own timing assertions run against a synthetic 3-building
fixture -- fast by construction regardless of whether a real performance
regression exists, since they never touch the (evaluated buildings x fault
mesh points) cost that actually dominates a real request (ADR-0009). This
module exercises that real cost directly: a long, nationwide-reach fault
(QAFI's Peñacova-Régua-Verín/PO011 and Manteigas-Vilariça-Bragança/PO007,
both flagged in ADR-0009/ADR-0014 as the faults that previously regressed
to a multi-minute hang and a 10-20s-per-request join respectively) run at
real scale, with a ceiling generous enough to absorb CI/laptop variance but
tight enough to catch a real regression back into that territory.

Skipped wherever the national dataset (`data/exposure_spain`) hasn't been
crawled locally -- same "local sanity check, not a CI requirement" pattern
as test_engine_integration.py's smaller Lorca-only version.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from scenario.engine import run_scenario
from scenario.faults import get_fault
from scenario.ground_motion import estimate_significant_distance_km
from scenario.probability_level import resolve_probability_level
from scenario.rupture import from_fault

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
FAULTS = DATA_DIR / "faults" / "qafi_faults.parquet"
FRAGILITY = DATA_DIR / "fragility" / "fragility.parquet"
NATIONAL_PARTS = DATA_DIR / "exposure_spain" / "parts"
BUILDINGS_GLOB = str(NATIONAL_PARTS / "*.buildings.parquet")
EXPOSURE_GLOB = str(NATIONAL_PARTS / "*.exposure.parquet")

pytestmark = pytest.mark.skipif(
    not (FAULTS.exists() and FRAGILITY.exists() and NATIONAL_PARTS.is_dir()),
    reason="run pipelines/exposure.region_cli --spain (or --basque-navarra) locally first",
)

# Measured directly against the real national dataset (~12.9M buildings,
# 2026-09-17): Barcelona/ME025 ~3.8s, Peñacova/PO011 ~5.5s at "high",
# ~5.6s at "very_low", Manteigas/PO007 ~6.1s at "very_low" -- a long fault
# nationwide reach routinely runs several seconds, not the sub-second a
# small-fixture test would suggest. 30s gives ~5x headroom over the
# slowest measured case for a slower CI/laptop, while still being an order
# of magnitude under the "never completed (>5min, hung)" regression
# ADR-0009 actually hit for this same fault (PO011) before it was fixed.
MAX_REAL_SCENARIO_SECONDS = 30.0

# A short/near fault should stay comfortably faster than a long/nationwide
# one -- this is the "still fast for the common case" half of the
# regression guard, not just "eventually finishes."
MAX_SHORT_FAULT_SECONDS = 15.0


def _run_fault(fault_id: str, near_lat: float, near_lon: float, probability_level: str):
    fault = get_fault(str(FAULTS), fault_id, near_lat, near_lon)
    rupture = from_fault(
        fault_id=fault["fault_id"],
        name=fault["name"],
        point_lat=fault["lat"],
        point_lon=fault["lon"],
        mmax=fault["mmax"],
        rake=fault["rake"],
        geometry_geojson=fault["geometry_geojson"],
        dip=fault["dip"],
        min_depth_km=fault["min_depth_km"],
        max_depth_km=fault["max_depth_km"],
    )
    params = resolve_probability_level(probability_level)
    radius_km = estimate_significant_distance_km(rupture, sigma_multiplier=params.sigma_multiplier)

    t0 = time.monotonic()
    result = run_scenario(
        rupture,
        BUILDINGS_GLOB,
        EXPOSURE_GLOB,
        str(FRAGILITY),
        max_distance_km=radius_km,
        sigma_multiplier=params.sigma_multiplier,
        damage_percentile=params.damage_percentile,
    )
    elapsed = time.monotonic() - t0
    return result, elapsed


def test_short_fault_at_high_probability_is_fast():
    # Barcelona (ME025) -- ADR-0009's own "typical" nationwide case.
    result, elapsed = _run_fault("ME025", near_lat=41.39, near_lon=2.15, probability_level="high")
    assert len(result) > 100_000  # actually exercised the nationwide dataset
    assert elapsed < MAX_SHORT_FAULT_SECONDS, (
        f"{elapsed:.2f}s, expected < {MAX_SHORT_FAULT_SECONDS}s"
    )


def test_long_fault_at_very_low_probability_completes_within_a_generous_ceiling():
    # Peñacova-Régua-Verín (PO011) -- the exact fault that hung for 5+
    # minutes before ADR-0009's chunking/gridding fix, run at "very_low"
    # (the slowest combination: median+1sigma ground motion over the
    # widest radius, plus 85th-percentile damage-state selection).
    result, elapsed = _run_fault(
        "PO011", near_lat=40.4168, near_lon=-3.7038, probability_level="very_low"
    )
    assert len(result) > 1_000_000  # a long fault's real evaluated-building scale
    assert elapsed < MAX_REAL_SCENARIO_SECONDS, (
        f"{elapsed:.2f}s, expected < {MAX_REAL_SCENARIO_SECONDS}s -- "
        "this is the exact regression class ADR-0009 fixed (a long fault's "
        "distance calc/ground-motion grid going unchunked/undeduped again)"
    )


def test_municipality_stats_stay_consistent_with_shipped_buildings_at_national_scale():
    # The same aggregate-vs-individual invariant as test_response.py's unit
    # test and test_local_api.py's synthetic-fixture version, but against a
    # real long-fault run over the real national dataset -- this is the
    # scale (millions of evaluated buildings, hundreds of thousands
    # affected) the reported bug actually showed up at.
    #
    # Doesn't hardcode which municipality ends up "affected" -- that
    # depends on the correctness of municipality_code itself (the exact
    # thing a real regression here would break), so asserting it for one
    # specific code would just start failing again the next time a
    # municipality-code fix legitimately changes who's near the fault
    # (as happened when the Catastro/INE code-mismatch fix moved
    # Villalpando's real buildings out from under a neighbour's damage,
    # see docs/decisions and this module's own git history).
    from scenario.response import compute_municipality_stats, prepare_response_buildings

    result, _ = _run_fault(
        "PO007", near_lat=41.825005, near_lon=-5.406425, probability_level="very_low"
    )
    stats = compute_municipality_stats(result)
    shipped = prepare_response_buildings(result)
    shipped_ids = set(shipped["building_id"])

    affected_codes = [
        s["municipality_code"] for s in stats if s["n_evaluated"] - s["counts"]["None"] > 0
    ]
    assert affected_codes, "expected at least one municipality with real damage for this fault"

    for code in affected_codes:
        muni_rows = result[result["municipality_code"] == code]
        n_affected = (muni_rows["damage_state"] != "None").sum()
        n_affected_and_shipped = (
            (muni_rows["damage_state"] != "None") & muni_rows["building_id"].isin(shipped_ids)
        ).sum()
        assert n_affected_and_shipped == n_affected, f"mismatch for municipality {code}"
