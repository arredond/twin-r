"""MERISUR's three selectable scenario probability levels.

`docs/merisur.md` §4.7, sourced from the 2017/2018 papers:

| Level     | Ground motion | Damage             |
|-----------|----------------|--------------------|
| high      | median         | modal damage state |
| low       | median + 1sigma | modal damage state |
| very_low  | median + 1sigma | 85th-percentile damage state |

Deferred at MVP time (`docs/milestone-1-plan.md` §1/§8: "skip MERISUR's
three probability levels ... add them once the base chain works") and
picked back up per `docs/validation-lorca-2011.md` §10.5: for the 2011
Lorca event specifically, median+1sigma ground motion lands within 3.4% of
the recorded near-fault PGA, using GMPE `sig` output the engine already
computes and previously discarded -- no new data or vendoring needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ProbabilityLevel = Literal["high", "low", "very_low"]

PROBABILITY_LEVELS: tuple[ProbabilityLevel, ...] = ("high", "low", "very_low")


@dataclass(frozen=True)
class ProbabilityLevelParams:
    """`sigma_multiplier` feeds `ground_motion.py`'s GMPE calls (mean +
    multiplier * sig, in log space, before exponentiating); `damage_percentile`
    feeds `damage.py`'s damage-state selection (`None` means modal/argmax,
    matching today's only behaviour)."""

    sigma_multiplier: float
    damage_percentile: float | None


_PARAMS: dict[ProbabilityLevel, ProbabilityLevelParams] = {
    "high": ProbabilityLevelParams(sigma_multiplier=0.0, damage_percentile=None),
    "low": ProbabilityLevelParams(sigma_multiplier=1.0, damage_percentile=None),
    "very_low": ProbabilityLevelParams(sigma_multiplier=1.0, damage_percentile=0.85),
}


def resolve_probability_level(level: str) -> ProbabilityLevelParams:
    """Raises ValueError (not KeyError) for an unknown level -- callers
    (local.py/handler.py) already translate ValueError from rupture parsing
    into a 400 response, so an unknown level reuses that same path rather
    than needing its own."""
    try:
        return _PARAMS[level]  # type: ignore[index]
    except KeyError:
        raise ValueError(
            f"unknown probability_level {level!r}, expected one of {PROBABILITY_LEVELS}"
        ) from None
