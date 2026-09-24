"""Content-addressed scenario ids (scenario_id.py) and the trace midpoint
that makes automatic-mode ids independent of the caller's map view."""

from __future__ import annotations

import json

import pytest
from scenario.scenario_id import (
    API_VERSION,
    cache_enabled,
    fault_scenario_id,
    manual_scenario_id,
)
from scenario.trace import trace_midpoint


def test_fault_id_is_stable_and_32_hex_chars():
    a = fault_scenario_id("ES001", "high")
    assert a == fault_scenario_id("ES001", "high")
    assert len(a) == 32
    int(a, 16)


def test_fault_id_depends_on_every_input():
    base = fault_scenario_id("ES001", "high")
    assert fault_scenario_id("ES002", "high") != base
    assert fault_scenario_id("ES001", "low") != base
    assert fault_scenario_id("ES001", "high", 37.0, -1.0) != base
    assert fault_scenario_id("ES001", "high", 37.0, -1.0) != fault_scenario_id(
        "ES001", "high", 37.0, -1.1
    )


def test_ids_change_with_data_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TWINER_DATA_VERSION", raising=False)
    before = fault_scenario_id("ES001", "high")
    monkeypatch.setenv("TWINER_DATA_VERSION", "2026-10-01")
    assert fault_scenario_id("ES001", "high") != before


def test_ids_change_with_api_version(monkeypatch: pytest.MonkeyPatch):
    from scenario import scenario_id

    before = fault_scenario_id("ES001", "high")
    monkeypatch.setattr(scenario_id, "API_VERSION", API_VERSION + "-next")
    assert fault_scenario_id("ES001", "high") != before


def test_manual_and_fault_ids_live_in_separate_namespaces():
    manual = manual_scenario_id(37.0, -1.0, 6.5, 0.0, None, None, None, "high")
    assert manual == manual_scenario_id(37, -1, 6.5, 0, None, None, None, "high")
    assert manual != manual_scenario_id(37.0, -1.0, 6.5, 0.0, 45.0, 60.0, 3.0, "high")


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, False), ("", False), ("0", False), ("false", False), ("1", True), ("TRUE", True)],
)
def test_cache_flag(monkeypatch: pytest.MonkeyPatch, value, expected):
    if value is None:
        monkeypatch.delenv("TWINER_SCENARIO_CACHE", raising=False)
    else:
        monkeypatch.setenv("TWINER_SCENARIO_CACHE", value)
    assert cache_enabled() is expected


def test_trace_midpoint_of_a_straight_line():
    lat, lon = trace_midpoint(
        json.dumps({"type": "LineString", "coordinates": [[-2.0, 37.0], [-1.0, 37.0]]})
    )
    assert lat == pytest.approx(37.0, abs=0.01)
    assert lon == pytest.approx(-1.5, abs=1e-3)


def test_trace_midpoint_is_by_length_not_by_vertex():
    # Three vertices, but the first segment is 9x the second -- the
    # midpoint sits inside the first segment, not on the middle vertex.
    _, lon = trace_midpoint(
        json.dumps(
            {"type": "LineString", "coordinates": [[-2.0, 37.0], [-1.1, 37.0], [-1.0, 37.0]]}
        )
    )
    assert lon == pytest.approx(-1.5, abs=1e-2)


def test_trace_midpoint_uses_the_longest_piece_of_a_multilinestring():
    _, lon = trace_midpoint(
        json.dumps(
            {
                "type": "MultiLineString",
                "coordinates": [[[5.0, 40.0], [5.01, 40.0]], [[-2.0, 37.0], [-1.0, 37.0]]],
            }
        )
    )
    assert lon == pytest.approx(-1.5, abs=1e-3)
