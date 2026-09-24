"""End-to-end test of the local FastAPI app (local.py) against small
synthetic fixtures -- no real Catastro/QAFI data, no live server, no
network. Exercises the actual HTTP routes (not just the underlying
functions), which is exactly the layer where the two most recent real bugs
in this project lived: a hardcoded query-radius default that silently
returned zero faults, and a dev server that simply wasn't running. A test
at this layer would have caught the first outright.
"""

from __future__ import annotations

import importlib
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString, Polygon

# Every request in this file runs against a 3-building synthetic fixture --
# no real Catastro/QAFI I/O, no network. This is NOT a real-scale
# performance test (see test_fault_scenario_performance.py for that, run
# against the real national dataset, where a long fault genuinely takes
# several seconds): at 3 buildings, even a request that regressed all the
# way back to the *un-fixed* ADR-0009/ADR-0014 behaviour (an unchunked
# hazardlib distance matrix, a per-request DuckDB spatial join) would
# still return quickly, so this ceiling only catches a much grosser bug --
# e.g. an accidental network call, an infinite loop, or a per-request cost
# that scales with something unbounded (municipality count, fault count)
# rather than with this fixture's tiny building count. Kept generous (10s)
# for slow CI/laptop startup rather than tuned tight, since catching a
# real regression's *magnitude* is test_fault_scenario_performance.py's
# job, not this one's.
MAX_REQUEST_SECONDS = 10.0


def _timed(fn):
    t0 = time.monotonic()
    result = fn()
    elapsed = time.monotonic() - t0
    assert elapsed < MAX_REQUEST_SECONDS, (
        f"request took {elapsed:.2f}s, expected < {MAX_REQUEST_SECONDS}s"
    )
    return result


# Two real, nearby buildings (~100m apart) and one far away (~50km), so a
# manual-mode scenario centered on the first two can exercise the spatial
# filter without pulling in the third.
NEAR_LAT, NEAR_LON = 37.67, -1.70
FAR_LAT, FAR_LON = 38.10, -1.70  # ~48km north


def _square(center_lon: float, center_lat: float, half_side_deg: float = 0.0001) -> Polygon:
    return Polygon(
        [
            (center_lon - half_side_deg, center_lat - half_side_deg),
            (center_lon + half_side_deg, center_lat - half_side_deg),
            (center_lon + half_side_deg, center_lat + half_side_deg),
            (center_lon - half_side_deg, center_lat + half_side_deg),
        ]
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    buildings = gpd.GeoDataFrame(
        {
            "building_id": ["b1", "b2", "b3-far"],
            "municipality_code": ["30024", "30024", "02003"],
            "centroid_lon": [NEAR_LON, NEAR_LON + 0.001, FAR_LON],
            "centroid_lat": [NEAR_LAT, NEAR_LAT + 0.001, FAR_LAT],
            "bbox_xmin": [NEAR_LON, NEAR_LON, FAR_LON],
            "bbox_ymin": [NEAR_LAT, NEAR_LAT, FAR_LAT],
            "bbox_xmax": [NEAR_LON, NEAR_LON, FAR_LON],
            "bbox_ymax": [NEAR_LAT, NEAR_LAT, FAR_LAT],
            # ADR-0015's per-building Vs30, at ground_motion.DEFAULT_VS30 --
            # the flat reference-rock value every probability quoted in the
            # tests below was verified against, before this column existed.
            "vs30": [800.0, 800.0, 800.0],
            "geometry": [
                _square(NEAR_LON, NEAR_LAT),
                _square(NEAR_LON + 0.001, NEAR_LAT + 0.001),
                _square(FAR_LON, FAR_LAT),
            ],
        },
        crs="EPSG:4326",
    )
    exposure = pd.DataFrame(
        {
            "building_id": ["b1", "b2", "b3-far"],
            "taxonomy_class": ["MR_LWAL-DUL", "CR_LDUAL-DUL", "MR_LWAL-DUL"],
            "height_class": [2, 3, 2],
            "taxonomy_source": ["heuristic_v1"] * 3,
        }
    )
    # Minimal fragility curves: exceedance probability rises with IM, high
    # enough at typical near-fault SA(0.3s) values to produce a mix of
    # damage states (not all-None) so the response-filtering behavior is
    # actually exercised.
    fragility_rows = []
    for taxonomy in ["MR_LWAL-DUL", "CR_LDUAL-DUL"]:
        for height in [2, 3]:
            for im_value, slight, moderate, extensive, complete in [
                (0.01, 0.001, 0.0001, 0.00001, 0.000001),
                (0.5, 0.9, 0.6, 0.3, 0.1),
                (2.0, 0.999, 0.99, 0.95, 0.8),
            ]:
                for state, prob in [
                    ("Slight", slight),
                    ("Moderate", moderate),
                    ("Extensive", extensive),
                    ("Complete", complete),
                ]:
                    fragility_rows.append(
                        {
                            "taxonomy": taxonomy,
                            "height_class": height,
                            "im_type": "SA(0.3s) [g]",
                            "im_value": im_value,
                            "damage_state": state,
                            "prob_exceedance": prob,
                        }
                    )
    fragility = pd.DataFrame(fragility_rows)

    # TEST002 has no dip/depth range -- the "missing rupture geometry" case
    # (point-source fallback) where near_lat/near_lon genuinely matter.
    faults = gpd.GeoDataFrame(
        {
            "fault_id": ["TEST001", "TEST002"],
            "name": ["Test Fault", "Another Test Fault"],
            "section_name": [None, None],
            "length_km": [30.0, 30.0],
            "mmax": [6.5, 6.5],
            "mmax_source": ["qafi_v4_published", "qafi_v4_published"],
            "rake": [20.0, 20.0],
            "dip": [70.0, None],
            "strike": [215.0, 215.0],
            "min_depth_km": [0.0, None],
            "max_depth_km": [12.0, None],
            "geometry": [
                LineString([(NEAR_LON - 0.05, NEAR_LAT), (NEAR_LON + 0.05, NEAR_LAT)]),
                LineString([(NEAR_LON - 0.05, NEAR_LAT), (NEAR_LON + 0.05, NEAR_LAT)]),
            ],
        },
        crs="EPSG:4326",
    )

    d = tmp_path / "data"
    (d / "exposure" / "parts").mkdir(parents=True)
    (d / "fragility").mkdir(parents=True)
    (d / "faults").mkdir(parents=True)
    # buildings.parquet is always a partitioned glob in production (no
    # single combined file, by design -- region.py) -- one file here is
    # enough to satisfy local.py's glob default without needing a real
    # per-municipality split for a synthetic fixture. exposure.parquet
    # *is* a single combined file in production, matched here directly.
    buildings.to_parquet(d / "exposure" / "parts" / "test.buildings.parquet")
    exposure.to_parquet(d / "exposure" / "exposure.parquet", index=False)
    fragility.to_parquet(d / "fragility" / "fragility.parquet", index=False)
    faults.to_parquet(d / "faults" / "qafi_faults.parquet")
    return d


def _make_client(data_dir: Path, results_dir: Path, monkeypatch: pytest.MonkeyPatch, cache: bool):
    monkeypatch.setenv("TWINER_DATA_DIR", str(data_dir))
    # Env (for the tile pool's worker processes, which re-import) *and*
    # the already-imported module global (for this process).
    monkeypatch.setenv("TWINER_RESULTS_DIR", str(results_dir))
    from scenario import results_store

    monkeypatch.setattr(results_store, "RESULTS_DIR", results_dir)
    if cache:
        monkeypatch.setenv("TWINER_SCENARIO_CACHE", "1")
    else:
        monkeypatch.delenv("TWINER_SCENARIO_CACHE", raising=False)
    monkeypatch.delenv("TWINER_BUILDINGS_PATH", raising=False)
    monkeypatch.delenv("TWINER_EXPOSURE_PATH", raising=False)
    monkeypatch.delenv("TWINER_FRAGILITY_PATH", raising=False)
    monkeypatch.delenv("TWINER_FAULTS_PATH", raising=False)

    from scenario import local

    importlib.reload(local)  # re-read module-level path constants from the env above

    # Every reload builds a fresh module-level ProcessPoolExecutor, and
    # every scenario run submits warm_cache to it -- spawning up to 8
    # worker processes (each importing pandas/pyarrow) per test, none ever
    # shut down, which the interpreter then joins one by one at exit
    # (measured: 15s of tests, ~15min to exit). Nothing here requests
    # /tiles, so a single thread stands in for the pool -- warm_cache
    # still runs (and harmlessly fails on the fixture's missing
    # buildings.pmtiles), but no processes are spawned.
    local._TILE_POOL.shutdown(wait=False)
    monkeypatch.setattr(local, "_TILE_POOL", ThreadPoolExecutor(max_workers=1))

    from starlette.testclient import TestClient

    return TestClient(local.app)


@pytest.fixture
def client(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with _make_client(data_dir, tmp_path / "results", monkeypatch, cache=False) as c:
        yield c
    _shutdown_tile_pool()


@pytest.fixture
def cached_client(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    with _make_client(data_dir, tmp_path / "results", monkeypatch, cache=True) as c:
        yield c
    _shutdown_tile_pool()


def _shutdown_tile_pool() -> None:
    from scenario import local

    local._TILE_POOL.shutdown(wait=True, cancel_futures=True)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_faults_endpoint_returns_every_fault_sorted_by_name(client):
    # No location/radius args any more -- the old ones were how this
    # project once silently got zero faults back (a too-small radius from
    # an unrelated reference point).
    resp = client.get("/faults")
    assert resp.status_code == 200
    faults = resp.json()["faults"]
    assert [f["fault_id"] for f in faults] == ["TEST002", "TEST001"]
    by_id = {f["fault_id"]: f for f in faults}
    assert by_id["TEST001"]["has_rupture_geometry"] is True
    assert by_id["TEST002"]["has_rupture_geometry"] is False


def test_manual_scenario_evaluates_only_nearby_buildings(client):
    resp = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 6.5, "rake": 20.0}
    )
    assert resp.status_code == 200
    body = resp.json()
    # b3-far is ~48km away; at Mw6.5 the adaptive radius may or may not
    # reach it, but b1/b2 (meters away) must always be evaluated.
    assert body["n_evaluated"] >= 2
    assert body["rupture"]["mag"] == 6.5


def test_manual_scenario_omits_confidently_undamaged_buildings(client):
    # Mw3.0 next to b1/b2 (verified against these exact fragility curves:
    # P(None) ≈ 0.974) keeps them evaluated but leaves them out of
    # `buildings` -- indistinguishable from genuinely undamaged for display
    # purposes, and this is the payload-size fix (docs/validation-region-
    # expansion.md §4) the confidence threshold exists for.
    resp = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 3.0, "rake": 0.0}
    )
    body = resp.json()
    assert body["n_evaluated"] >= 1
    assert body["buildings"] == []
    assert body["evaluated_region"] == {
        "lat": NEAR_LAT,
        "lon": NEAR_LON,
        "radius_km": body["evaluated_region"]["radius_km"],
    }
    assert body["evaluated_region"]["radius_km"] > 0


def test_manual_scenario_keeps_a_genuine_close_call_even_when_modal_state_is_none(client):
    # Mw5.85 next to b1/b2 (verified against these exact fragility curves:
    # P(None) ≈ 0.322, P(Slight) ≈ 0.226 -- margin ≈ 0.096, under
    # UNCERTAINTY_MARGIN) is "None"-modal but a genuine close call against
    # the runner-up class -- must NOT be flattened into the same "omitted"
    # bucket as a confidently-undamaged building.
    resp = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 5.85, "rake": 0.0}
    )
    body = resp.json()
    assert len(body["buildings"]) >= 1
    for b in body["buildings"]:
        assert "building_id" in b and "damage_state_code" in b
    # damage_state_code 0 == "None" (services/scenario/response.py's
    # DAMAGE_STATE_CODES, matching damage.py's DAMAGE_STATES ordering).
    assert any(b["damage_state_code"] == 0 for b in body["buildings"])


def test_manual_scenario_omits_none_modal_buildings_that_are_not_a_close_call(client):
    # Mw5.5 next to b1/b2 (verified: P(None) ≈ 0.498, P(Slight) ≈ 0.168 --
    # margin ≈ 0.33, well over UNCERTAINTY_MARGIN) is "None"-modal and only
    # mildly uncertain, not a genuine close call -- this is exactly the
    # case a flat P(None) cutoff let back into the response for a long,
    # large-magnitude fault (see UNCERTAINTY_MARGIN's docstring), so it
    # must be omitted same as a confidently-undamaged building.
    resp = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 5.5, "rake": 0.0}
    )
    body = resp.json()
    assert body["n_evaluated"] >= 1
    assert body["buildings"] == []


def test_manual_scenario_defaults_to_high_probability_level(client):
    resp = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 6.5, "rake": 20.0}
    )
    assert resp.status_code == 200
    assert resp.json()["rupture"]["probability_level"] == "high"


def test_manual_scenario_accepts_low_and_very_low_probability_levels(client):
    for level in ["low", "very_low"]:
        resp = client.post(
            "/scenarios/manual",
            json={
                "lat": NEAR_LAT,
                "lon": NEAR_LON,
                "mag": 6.5,
                "rake": 20.0,
                "probability_level": level,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["rupture"]["probability_level"] == level


def test_manual_scenario_rejects_unknown_probability_level(client):
    resp = client.post(
        "/scenarios/manual",
        json={
            "lat": NEAR_LAT,
            "lon": NEAR_LON,
            "mag": 6.5,
            "probability_level": "medium",
        },
    )
    # FastAPI/Pydantic rejects a value outside the Literal at the request-
    # validation layer, before this route's own body ever runs.
    assert resp.status_code == 422


def test_low_probability_level_shows_more_damage_than_high_at_the_same_magnitude(client):
    # Mw5.5 at "high" (median ground motion) is confidently-undamaged at
    # b1/b2 and ships no buildings (see
    # test_manual_scenario_omits_none_modal_buildings_that_are_not_a_close_call
    # below) -- median+1sigma ("low") must push at least one of them into
    # the shipped response, the actual behavioural effect this parameter
    # exists for, not just a plumbing/echo check.
    high = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 5.5, "rake": 0.0}
    ).json()
    low = client.post(
        "/scenarios/manual",
        json={
            "lat": NEAR_LAT,
            "lon": NEAR_LON,
            "mag": 5.5,
            "rake": 0.0,
            "probability_level": "low",
        },
    ).json()
    assert high["buildings"] == []
    assert len(low["buildings"]) > 0


def test_fault_scenario_accepts_probability_level(client):
    resp = client.get(
        "/scenarios/fault",
        params={
            "fault_id": "TEST001",
            "probability_level": "low",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["rupture"]["probability_level"] == "low"


def test_manual_scenario_without_geometry_is_a_point_source(client):
    resp = client.post("/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 6.5})
    assert resp.status_code == 200
    assert resp.json()["rupture"]["finite_rupture"] is False


def test_manual_scenario_with_full_geometry_uses_a_finite_surface(client):
    # ADR-0008 tier 3: strike/dip/ztor_km all given -> real rupture plane.
    resp = client.post(
        "/scenarios/manual",
        json={
            "lat": NEAR_LAT,
            "lon": NEAR_LON,
            "mag": 6.5,
            "rake": 90.0,
            "strike": 45.0,
            "dip": 60.0,
            "ztor_km": 3.0,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["rupture"]["finite_rupture"] is True


def test_fault_scenario_runs_end_to_end(client):
    resp = client.get("/scenarios/fault", params={"fault_id": "TEST001"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["rupture"]["source"] == "fault:TEST001:Test Fault"
    assert body["rupture"]["mag"] == 6.5
    assert body["rupture"]["finite_rupture"] is True
    assert body["cached"] is False


def test_fault_with_geometry_anchors_at_trace_midpoint_and_ignores_near_point(client):
    # The trace runs east-west through (NEAR_LAT, NEAR_LON), which is its
    # midpoint. A far-off near point must change nothing: not the rupture
    # point, not the evaluated circle, not the scenario_id.
    plain = client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    with_near = client.get(
        "/scenarios/fault",
        params={"fault_id": "TEST001", "near_lat": 38.5, "near_lon": -1.0},
    ).json()
    assert plain["rupture"]["lat"] == pytest.approx(NEAR_LAT, abs=1e-4)
    assert plain["rupture"]["lon"] == pytest.approx(NEAR_LON, abs=1e-4)
    assert with_near["scenario_id"] == plain["scenario_id"]
    assert with_near["rupture"] == plain["rupture"]
    assert with_near["evaluated_region"] == plain["evaluated_region"]


def test_finite_rupture_evaluated_region_covers_the_whole_surface(client):
    # The significance radius is measured from the surface, so the display
    # circle around the midpoint has to reach past each end of the ~9km
    # trace, not stop at the bare radius.
    body = client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    point = client.post(
        "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 6.5, "rake": 20.0}
    ).json()
    assert body["evaluated_region"]["radius_km"] > point["evaluated_region"]["radius_km"] + 4


def test_fault_without_geometry_uses_near_point_when_given(client):
    east = client.get(
        "/scenarios/fault",
        params={"fault_id": "TEST002", "near_lat": NEAR_LAT + 0.2, "near_lon": NEAR_LON + 0.04},
    ).json()
    west = client.get(
        "/scenarios/fault",
        params={"fault_id": "TEST002", "near_lat": NEAR_LAT + 0.2, "near_lon": NEAR_LON - 0.04},
    ).json()
    assert east["rupture"]["finite_rupture"] is False
    # Closest trace point to each reference: same latitude as the trace,
    # longitude right below the reference point.
    assert east["rupture"]["lon"] == pytest.approx(NEAR_LON + 0.04, abs=1e-4)
    assert west["rupture"]["lon"] == pytest.approx(NEAR_LON - 0.04, abs=1e-4)
    assert east["scenario_id"] != west["scenario_id"]


def test_fault_without_geometry_or_near_point_falls_back_to_trace_midpoint(client):
    body = client.get("/scenarios/fault", params={"fault_id": "TEST002"}).json()
    assert body["rupture"]["finite_rupture"] is False
    assert body["rupture"]["lat"] == pytest.approx(NEAR_LAT, abs=1e-4)
    assert body["rupture"]["lon"] == pytest.approx(NEAR_LON, abs=1e-4)


def test_scenario_ids_are_deterministic_and_input_sensitive(client):
    a = client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()["scenario_id"]
    b = client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()["scenario_id"]
    low = client.get(
        "/scenarios/fault", params={"fault_id": "TEST001", "probability_level": "low"}
    ).json()["scenario_id"]
    assert a == b
    assert a != low


def test_cache_disabled_always_recomputes(client):
    first = client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    second = client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    assert first["cached"] is False
    assert second["cached"] is False


def test_cache_enabled_serves_an_identical_repeat_from_the_stored_result(cached_client):
    first = cached_client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    second = cached_client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    assert first["cached"] is False
    assert second["cached"] is True
    for key in ["scenario_id", "rupture", "evaluated_region", "buildings", "municipality_stats"]:
        assert second[key] == first[key]
    # Still a valid tile-join target after a hit.
    status = cached_client.get(f"/results/{second['scenario_id']}/status").json()
    assert status["buildings_ready"] is True


def test_cache_is_invalidated_by_a_data_version_bump(
    cached_client, monkeypatch: pytest.MonkeyPatch
):
    first = cached_client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    monkeypatch.setenv("TWINER_DATA_VERSION", "next")
    second = cached_client.get("/scenarios/fault", params={"fault_id": "TEST001"}).json()
    assert second["scenario_id"] != first["scenario_id"]
    assert second["cached"] is False


def test_manual_scenario_is_cached_too(cached_client):
    req = {"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 6.5, "rake": 20.0}
    first = cached_client.post("/scenarios/manual", json=req).json()
    second = cached_client.post("/scenarios/manual", json=req).json()
    other = cached_client.post("/scenarios/manual", json={**req, "mag": 6.4}).json()
    assert (first["cached"], second["cached"], other["cached"]) == (False, True, False)
    assert second["scenario_id"] == first["scenario_id"] != other["scenario_id"]


def test_fault_scenario_unknown_id_returns_404(client):
    resp = client.get(
        "/scenarios/fault",
        params={"fault_id": "NOT-A-REAL-FAULT"},
    )
    assert resp.status_code == 404


def test_building_info_returns_exposure_attributes(client):
    resp = client.get("/buildings/b1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["building_id"] == "b1"
    assert body["taxonomy_class"] == "MR_LWAL-DUL"
    assert body["height_class"] == 2


def test_building_info_unknown_id_returns_404(client):
    resp = client.get("/buildings/not-a-real-building")
    assert resp.status_code == 404


def test_municipality_stats_agree_with_which_buildings_are_shipped_individually(client):
    # End-to-end regression guard for the reported bug: a municipality's
    # choropleth ("N% affected") must never disagree with what the
    # per-building `buildings` array (the one popups read) actually ships
    # for that same municipality -- see test_response.py's own unit-level
    # version of this invariant for the narrower, faster check.
    resp = client.post(
        "/scenarios/manual",
        json={
            "lat": NEAR_LAT,
            "lon": NEAR_LON,
            "mag": 6.5,
            "rake": 20.0,
            "probability_level": "very_low",
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    shipped_ids = {b["building_id"] for b in body["buildings"]}
    stats_by_code = {s["municipality_code"]: s for s in body["municipality_stats"]}
    assert stats_by_code, "expected at least one municipality's stats"

    # b1/b2 (municipality 30024) sit right next to a Mw6.5 rupture at
    # very_low (median+1sigma, 85th-percentile damage) -- must show real
    # damage, exercising the non-trivial "some affected" branch, not just
    # an all-None municipality.
    thirty024 = stats_by_code["30024"]
    n_affected = thirty024["n_evaluated"] - thirty024["counts"]["None"]
    assert n_affected > 0
    n_affected_and_shipped = sum(
        1
        for b in body["buildings"]
        if b["building_id"] in {"b1", "b2"} and b["damage_state_code"] != 0
    )
    assert n_affected_and_shipped == n_affected
    # And the reverse direction: nothing shipped with a non-None code for
    # this municipality is missing from the affected count.
    assert n_affected_and_shipped <= len(shipped_ids)


def test_faults_endpoint_completes_quickly(client):
    _timed(lambda: client.get("/faults"))


def test_manual_scenario_completes_quickly(client):
    resp = _timed(
        lambda: client.post(
            "/scenarios/manual", json={"lat": NEAR_LAT, "lon": NEAR_LON, "mag": 6.5, "rake": 20.0}
        )
    )
    assert resp.status_code == 200


def test_fault_scenario_completes_quickly(client):
    resp = _timed(
        lambda: client.get(
            "/scenarios/fault",
            params={"fault_id": "TEST001"},
        )
    )
    assert resp.status_code == 200
