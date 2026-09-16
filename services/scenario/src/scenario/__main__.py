"""CLI: python -m scenario <lat> <lon> <mag> [rake] --data-dir <dir> [--tier <level>]

Runs a manual-rupture scenario against local pipeline outputs, for quick
iteration without spinning up the dev server. Prints a damage-state summary.
"""

from __future__ import annotations

import sys
from collections import Counter

from .engine import run_scenario
from .probability_level import PROBABILITY_LEVELS, resolve_probability_level
from .rupture import Rupture


def main() -> None:
    args = sys.argv[1:]
    data_dir = "data"
    if "--data-dir" in args:
        idx = args.index("--data-dir")
        data_dir = args[idx + 1]
        del args[idx : idx + 2]

    tier = "high"
    if "--tier" in args:
        idx = args.index("--tier")
        tier = args[idx + 1]
        del args[idx : idx + 2]

    if len(args) not in (3, 4):
        print(
            "usage: python -m scenario <lat> <lon> <mag> [rake] "
            "[--data-dir <dir>] [--tier high|low|very_low]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    lat, lon, mag = float(args[0]), float(args[1]), float(args[2])
    rake = float(args[3]) if len(args) == 4 else 0.0

    try:
        level_params = resolve_probability_level(tier)
    except ValueError as e:
        print(f"{e} (got --tier {tier!r})", file=sys.stderr)
        raise SystemExit(2) from e

    rupture = Rupture(lat=lat, lon=lon, mag=mag, rake=rake)
    result = run_scenario(
        rupture,
        buildings_path=f"{data_dir}/exposure/buildings.parquet",
        exposure_path=f"{data_dir}/exposure/exposure.parquet",
        fragility_path=f"{data_dir}/fragility/fragility.parquet",
        sigma_multiplier=level_params.sigma_multiplier,
        damage_percentile=level_params.damage_percentile,
    )

    # .tolist() rather than iterating the Series directly: pandas' Series
    # iteration is typed as yielding numpy scalars, not plain str, which
    # trips up static type checkers on the counts.get(state: str, ...)
    # below even though the runtime values are always str.
    counts = Counter(result["damage_state"].tolist())
    print(
        f"rupture: Mw {mag} at ({lat}, {lon}), rake {rake}, tier {tier!r} (of {PROBABILITY_LEVELS})"
    )
    print(f"{len(result)} buildings evaluated")
    for state in ["None", "Slight", "Moderate", "Extensive", "Complete"]:
        print(f"  {state:10s} {counts.get(state, 0)}")


if __name__ == "__main__":
    main()
