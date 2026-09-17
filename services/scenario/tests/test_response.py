import pandas as pd
from scenario.response import compute_municipality_stats, prepare_response_buildings


def test_compute_municipality_stats_groups_by_municipality_and_damage_state():
    result = pd.DataFrame(
        {
            "building_id": ["b1", "b2", "b3", "b4"],
            "municipality_code": ["30024", "30024", "28079", "28079"],
            "damage_state": ["Slight", "Moderate", "None", "None"],
        }
    )

    stats = compute_municipality_stats(result)
    by_code = {s["municipality_code"]: s for s in stats}

    assert by_code["30024"]["n_evaluated"] == 2
    assert by_code["30024"]["counts"] == {
        "None": 0,
        "Slight": 1,
        "Moderate": 1,
        "Extensive": 0,
        "Complete": 0,
    }
    assert by_code["28079"]["n_evaluated"] == 2
    assert by_code["28079"]["counts"]["None"] == 2


def test_compute_municipality_stats_with_empty_result_returns_empty_list():
    result = pd.DataFrame(columns=["building_id", "municipality_code", "damage_state"])

    assert compute_municipality_stats(result) == []


def test_compute_municipality_stats_remaps_ceuta_melilla_to_real_ine_codes():
    # Catastro files Ceuta/Melilla under its own "territorial office" codes
    # (55101/56101), not their real INE codes (51001/52001) that
    # municipalities.pmtiles/parquet (sourced from IGN) actually use --
    # without the remap, DamageMap.tsx's join against the tile's ine_code
    # would silently never match these two.
    result = pd.DataFrame(
        {
            "building_id": ["b1", "b2"],
            "municipality_code": ["55101", "56101"],
            "damage_state": ["Slight", "Moderate"],
        }
    )

    stats = compute_municipality_stats(result)
    codes = {s["municipality_code"] for s in stats}

    assert codes == {"51001", "52001"}


def _full_result(rows: list[dict]) -> pd.DataFrame:
    """Build a synthetic engine.run_scenario-shaped result -- both
    compute_municipality_stats (damage_state/municipality_code) and
    prepare_response_buildings (building_id/damage_state/prob_*) read the
    *same* DataFrame in local.py/handler.py, so a real regression of the
    frontend-reported bug this guards against (a municipality's choropleth
    showing "affected" for buildings the per-building popup calls
    "Likely None (not individually evaluated)") would only ever come from
    the two functions disagreeing about a row *in this one shape*."""
    return pd.DataFrame(
        [
            {
                "building_id": r["building_id"],
                "municipality_code": r["municipality_code"],
                "damage_state": r["damage_state"],
                "prob_none": r.get("prob_none", 1.0 if r["damage_state"] == "None" else 0.0),
                "prob_slight": r.get("prob_slight", 1.0 if r["damage_state"] == "Slight" else 0.0),
                "prob_moderate": r.get(
                    "prob_moderate", 1.0 if r["damage_state"] == "Moderate" else 0.0
                ),
                "prob_extensive": r.get(
                    "prob_extensive", 1.0 if r["damage_state"] == "Extensive" else 0.0
                ),
                "prob_complete": r.get(
                    "prob_complete", 1.0 if r["damage_state"] == "Complete" else 0.0
                ),
            }
            for r in rows
        ]
    )


def test_every_non_none_building_counted_as_affected_is_also_shipped_individually():
    # The exact invariant behind the reported bug: a municipality's
    # aggregate ("N% affected", compute_municipality_stats) must never
    # count a building as affected that the per-building payload
    # (prepare_response_buildings) then omits -- that combination is what
    # renders as "dark red, mostly damaged" on the choropleth while every
    # individual building the frontend can show popups for reads "Likely
    # None (not individually evaluated)".
    result = _full_result(
        [
            {"building_id": "b1", "municipality_code": "49250", "damage_state": "Slight"},
            {"building_id": "b2", "municipality_code": "49250", "damage_state": "Moderate"},
            {"building_id": "b3", "municipality_code": "49250", "damage_state": "None"},
        ]
    )

    stats = compute_municipality_stats(result)
    shipped_ids = set(prepare_response_buildings(result)["building_id"])

    for code_stats in stats:
        n_affected = code_stats["n_evaluated"] - code_stats["counts"]["None"]
        muni_rows = result[result["municipality_code"] == code_stats["municipality_code"]]
        n_affected_and_shipped = (
            (muni_rows["damage_state"] != "None") & muni_rows["building_id"].isin(shipped_ids)
        ).sum()
        assert n_affected_and_shipped == n_affected


def test_close_call_none_building_counts_as_none_in_aggregate_but_still_ships():
    # A "None"-modal-but-uncertain building (ADR-0011's close-call carve-out)
    # ships individually with its real probabilities, but it's still
    # genuinely None-modal -- the aggregate must count it under "None", not
    # as affected, even though prepare_response_buildings includes it.
    result = _full_result(
        [
            {
                "building_id": "b1",
                "municipality_code": "49250",
                "damage_state": "None",
                "prob_none": 0.55,
                "prob_slight": 0.45,
            }
        ]
    )

    stats = compute_municipality_stats(result)
    assert stats[0]["counts"]["None"] == 1
    assert stats[0]["n_evaluated"] - stats[0]["counts"]["None"] == 0  # not "affected"

    shipped = prepare_response_buildings(result)
    assert list(shipped["building_id"]) == ["b1"]  # still shown individually, per ADR-0011
