"""Tests for the offline/no-network logic in municipality_crosswalk.py --
the actual Catastro web-service call and Álava/Gipuzkoa spatial matching
are exercised manually against real data (see the module's own docstring
and this session's notes), not here: no fixture stands in convincingly
for "call a live government SOAP service" or "match against all 8,200 IGN
polygons," and a mocked version of either would mostly test the mock.

What *is* worth pinning down with synthetic fixtures: the two-phase
staged rename's safety against swap chains (the exact shape of the real
bug this module fixes -- two municipalities' codes crossed), and the
merge-not-overwrite behavior for a target that already exists (the
Facería shared-boundary case).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from exposure.municipality_crosswalk import (
    _move_staged_merging,
    _normalize_name,
    apply_crosswalk_to_parts,
)
from shapely.geometry import box


def _building_part(building_ids: list[str], municipality_code: str) -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "building_id": building_ids,
            "municipality": ["PLACEHOLDER"] * len(building_ids),
            "municipality_code": [municipality_code] * len(building_ids),
        },
        geometry=[box(i, i, i + 1, i + 1) for i in range(len(building_ids))],
        crs="EPSG:4326",
    )


def test_normalize_name_strips_accents_case_and_punctuation():
    assert _normalize_name("Cornellà de Llobregat") == "CORNELLADELLOBREGAT"
    assert _normalize_name("CORBERA DE LLOBREGAT") == "CORBERADELLOBREGAT"
    assert _normalize_name("Cornellà de Llobregat") != _normalize_name("Corbera de Llobregat")


def test_normalize_name_handles_none_and_empty():
    assert _normalize_name(None) == ""
    assert _normalize_name("") == ""


def test_apply_crosswalk_resolves_a_two_way_swap_without_data_loss(tmp_path: Path):
    # The exact shape of the real bug: Hinojal's buildings are filed under
    # Catastro's code "10101" (real INE 10098), while "10098" is currently
    # occupied by Herreruela's buildings (real INE 10095) -- a naive
    # sequential rename could overwrite one with the other mid-run.
    _building_part(["hinojal-1", "hinojal-2"], "10101").to_parquet(
        tmp_path / "10101.buildings.parquet"
    )
    _building_part(["herreruela-1"], "10098").to_parquet(tmp_path / "10098.buildings.parquet")

    crosswalk = pd.DataFrame(
        [
            {"old_code": "10101", "new_code": "10098", "name": "HINOJAL", "source": "catastro"},
            {"old_code": "10098", "new_code": "10095", "name": "HERRERUELA", "source": "catastro"},
        ]
    )
    apply_crosswalk_to_parts(crosswalk, tmp_path)

    hinojal = gpd.read_parquet(tmp_path / "10098.buildings.parquet")
    assert set(hinojal["building_id"]) == {"hinojal-1", "hinojal-2"}
    assert set(hinojal["municipality_code"]) == {"10098"}

    herreruela = gpd.read_parquet(tmp_path / "10095.buildings.parquet")
    assert set(herreruela["building_id"]) == {"herreruela-1"}
    assert set(herreruela["municipality_code"]) == {"10095"}

    assert not (tmp_path / "10101.buildings.parquet").exists()


def test_apply_crosswalk_renames_exposure_but_leaves_debris_untouched(tmp_path: Path):
    _building_part(["b1"], "10101").to_parquet(tmp_path / "10101.buildings.parquet")
    pd.DataFrame({"building_id": ["b1"], "taxonomy_class": ["X"]}).to_parquet(
        tmp_path / "10101.exposure.parquet", index=False
    )
    pd.DataFrame({"building_id": ["b1"], "ring": [1]}).to_parquet(
        tmp_path / "10101.debris.parquet", index=False
    )

    crosswalk = pd.DataFrame(
        [{"old_code": "10101", "new_code": "10098", "name": "HINOJAL", "source": "catastro"}]
    )
    apply_crosswalk_to_parts(crosswalk, tmp_path)

    assert (tmp_path / "10098.exposure.parquet").exists()
    assert not (tmp_path / "10101.exposure.parquet").exists()
    # debris.parquet is out of scope for this correction -- left exactly
    # where it was, under the old code.
    assert (tmp_path / "10101.debris.parquet").exists()
    assert not (tmp_path / "10098.debris.parquet").exists()


def test_apply_crosswalk_skips_missing_old_code_without_raising(
    tmp_path: Path, capsys: pytest.CaptureFixture
):
    crosswalk = pd.DataFrame(
        [{"old_code": "99999", "new_code": "11111", "name": "NOWHERE", "source": "catastro"}]
    )
    apply_crosswalk_to_parts(crosswalk, tmp_path)
    assert "WARNING" in capsys.readouterr().out
    assert list(tmp_path.iterdir()) == []


def test_move_staged_merging_concatenates_instead_of_overwriting(tmp_path: Path):
    # The Facería case: two independent corrections can legitimately
    # target the same INE code (a shared-boundary entity two different
    # crawls both contribute buildings to).
    parts_dir = tmp_path / "parts"
    staging_dir = tmp_path / "staging"
    parts_dir.mkdir()
    staging_dir.mkdir()

    _building_part(["existing-1"], "53002").to_parquet(parts_dir / "53002.buildings.parquet")
    _building_part(["new-1", "new-2"], "53002").to_parquet(staging_dir / "53002.buildings.parquet")

    _move_staged_merging(staging_dir, parts_dir)

    merged = gpd.read_parquet(parts_dir / "53002.buildings.parquet")
    assert set(merged["building_id"]) == {"existing-1", "new-1", "new-2"}


def test_move_staged_merging_deduplicates_by_building_id(tmp_path: Path):
    parts_dir = tmp_path / "parts"
    staging_dir = tmp_path / "staging"
    parts_dir.mkdir()
    staging_dir.mkdir()

    _building_part(["shared-1"], "53002").to_parquet(parts_dir / "53002.buildings.parquet")
    _building_part(["shared-1"], "53002").to_parquet(staging_dir / "53002.buildings.parquet")

    _move_staged_merging(staging_dir, parts_dir)

    merged = gpd.read_parquet(parts_dir / "53002.buildings.parquet")
    assert len(merged) == 1
