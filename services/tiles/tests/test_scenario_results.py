import gzip
import json
import types
from pathlib import Path

import pytest
from tiles import scenario_results
from tiles.scenario_results import COLUMNS, FILENAME, FORMAT_VERSION, ScenarioResults, encode


def _columns(rows: list[dict]) -> dict[str, list]:
    return {name: [r[name] for r in rows] for name in COLUMNS}


def _row(building_id: str, code: int, p_none: float = 0.5) -> dict:
    rest = round((1 - p_none) / 4, 4)
    return {
        "building_id": building_id,
        "damage_state_code": code,
        "prob_none": p_none,
        "prob_slight": rest,
        "prob_moderate": rest,
        "prob_extensive": rest,
        "prob_complete": rest,
    }


def test_round_trip_gives_each_building_its_tile_properties():
    rows = [_row("B", 2, 0.1), _row("A", 0, 0.6), _row("C", 4, 0.0)]
    results = ScenarioResults.from_bytes(encode(_columns(rows)))

    assert len(results) == 3
    for row in rows:
        assert results.get(row["building_id"]) == {
            k: v for k, v in row.items() if k != "building_id"
        }
    assert results.get("not-listed") is None
    assert results.get("") is None
    assert results.get("ZZZ") is None  # past the last id


def test_file_is_the_documented_column_oriented_json():
    # The format is part of the contract (ADR-0023): pin its shape, sorted
    # ids included, so a change to it is a deliberate one.
    document = json.loads(gzip.decompress(encode(_columns([_row("b", 1), _row("a", 0)]))))

    assert document["format"] == "twiner.scenario-results"
    assert document["version"] == FORMAT_VERSION == 1
    assert list(document["columns"]) == list(COLUMNS)
    assert document["columns"]["building_id"] == ["a", "b"]
    assert document["columns"]["damage_state_code"] == [0, 1]
    assert FILENAME == "buildings.columns.v1.json.gz"


def test_a_repeated_building_id_keeps_its_last_row():
    # building_id isn't always unique in the pipeline output
    # (DATA-SOURCES.md); keep-last is what the previous format did.
    results = ScenarioResults.from_bytes(
        encode(_columns([_row("A", 1), _row("B", 0), _row("A", 3)]))
    )

    assert len(results) == 2
    kept = results.get("A")
    assert kept is not None and kept["damage_state_code"] == 3


def test_encode_rejects_missing_extra_or_ragged_columns():
    columns = _columns([_row("A", 1)])
    with pytest.raises(ValueError, match="expected columns"):
        encode({k: v for k, v in columns.items() if k != "prob_complete"})
    with pytest.raises(ValueError, match="expected columns"):
        encode({**columns, "im_value": [0.1]})
    with pytest.raises(ValueError, match="differ in length"):
        encode({**columns, "prob_none": [0.5, 0.5]})


def test_reading_another_format_or_version_fails_loudly():
    document = json.loads(gzip.decompress(encode(_columns([_row("A", 1)]))))
    for bad in ({**document, "version": 2}, {**document, "format": "something-else"}):
        with pytest.raises(ValueError, match="not a twiner.scenario-results v1 file"):
            ScenarioResults.from_bytes(gzip.compress(json.dumps(bad).encode()))
    # The previous format: a bare list of row objects.
    with pytest.raises(AttributeError):
        ScenarioResults.from_bytes(gzip.compress(json.dumps([_row("A", 1)]).encode()))


def test_empty_scenario():
    results = ScenarioResults.from_bytes(encode({name: [] for name in COLUMNS}))
    assert len(results) == 0 and results.get("A") is None


def test_module_is_stdlib_only():
    # The tiles Lambda zip can't carry pandas/pyarrow/numpy (see the module
    # docstring); this module must not grow a dependency on them.
    source = Path(scenario_results.__file__).read_text()
    for heavy in ("pandas", "pyarrow", "numpy"):
        assert f"import {heavy}" not in source and f"from {heavy}" not in source


def test_encode_sorted_unique_writes_the_same_file_from_sliceable_columns_in_chunks(monkeypatch):
    # The scenario function's path: Arrow columns already sorted/unique,
    # written a chunk at a time (tiles.scenario_results._CHUNK_ROWS).
    monkeypatch.setattr(scenario_results, "_CHUNK_ROWS", 2)

    class Sliceable:  # only len() and slicing into something with to_pylist()
        def __init__(self, values):
            self.values = values

        def __len__(self):
            return len(self.values)

        def __getitem__(self, rows):
            return types.SimpleNamespace(to_pylist=lambda: self.values[rows])

    rows = [_row(b, i % 5) for i, b in enumerate(["a", "b", "c", "d", "e"])]
    chunked = scenario_results.encode_sorted_unique(
        {name: Sliceable(values) for name, values in _columns(rows).items()}
    )
    assert json.loads(gzip.decompress(chunked)) == json.loads(
        gzip.decompress(encode(_columns(rows)))
    )


def test_encode_sorted_unique_rejects_unsorted_or_repeated_ids_even_across_chunks(monkeypatch):
    monkeypatch.setattr(scenario_results, "_CHUNK_ROWS", 2)
    for ids in (["a", "c", "b", "d"], ["a", "b", "b", "c"], ["a", "c", "c", "d"]):
        with pytest.raises(ValueError, match="not sorted/unique"):
            scenario_results.encode_sorted_unique(_columns([_row(b, 1) for b in ids]))
