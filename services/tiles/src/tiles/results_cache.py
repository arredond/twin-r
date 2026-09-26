"""Decoded per-building scenario results (`scenario_results.ScenarioResults`),
cached by memory size rather than by count.

Every tile request for a scenario looks buildings up in that scenario's
decoded results, so each process keeps recent ones decoded: the tiles
Lambda (`results_store.read_building_results`) and local dev's tile
workers (`scenario.tile_join`). How much memory one takes varies ~200x:
~4MB for a small scenario, 1.34GB for an M9 "very_low" on Madrid listing
3.4M buildings (2026-09-26). A count limit (the previous
`lru_cache(maxsize=2)`) couldn't be right for both: two of the largest
need more than the 1769MB tiles Lambda has. So this keeps entries within
a byte budget, evicting least recently used first, and makes room
*before* decoding a new entry, from an estimate based on its compressed
size, so the old and the new one are never both in memory. It always
keeps the entry it just loaded, even one that alone exceeds the budget:
that request needs it.

Stdlib only, like the rest of this package (see results_store.py).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable

from .scenario_results import ScenarioResults

# Memory a decoded file holds, measured in a fresh process (2026-09-25/26):
# M9 "high" on Madrid, 448,545 buildings, 3.02MB gzipped -> +186MB RSS;
# M9 "very_low", 3,418,317 buildings, 29.2MB gzipped -> +1,339MB. That's
# 411-435 bytes per building, or 46-64 per compressed byte; the upper ends
# (rounded up), so the estimates err towards evicting early.
HELD_BYTES_PER_BUILDING = 450
HELD_BYTES_PER_COMPRESSED_BYTE = 64

# Room for one worst-case scenario (1.34GB alone, kept regardless) or a
# few ordinary ones, within the tiles Lambda's 1769MB alongside the rest
# of the process (~75-100MB measured).
DEFAULT_BUDGET_BYTES = 1_000 * 2**20


class ResultsCache:
    """Least-recently-used, bounded by estimated memory (module docstring)."""

    def __init__(self, budget_bytes: int = DEFAULT_BUDGET_BYTES):
        self._budget = budget_bytes
        self._entries: OrderedDict[Hashable, tuple[ScenarioResults, int]] = OrderedDict()

    def get(self, key: Hashable) -> ScenarioResults | None:
        """The cached results for `key`, if any (marking it recently used)."""
        if key not in self._entries:
            return None
        self._entries.move_to_end(key)
        return self._entries[key][0]

    def get_or_load(
        self, key: Hashable, compressed_size: int, read: Callable[[], bytes]
    ) -> ScenarioResults:
        """The decoded results for `key`, decoding `read()` (the gzipped
        file, `compressed_size` bytes) on a miss."""
        if key in self._entries:
            self._entries.move_to_end(key)
            return self._entries[key][0]
        # Make room first, so the old entries and the new one are never
        # both in memory when the budget can't hold both.
        self._evict_until(self._budget - compressed_size * HELD_BYTES_PER_COMPRESSED_BYTE)
        results = ScenarioResults.from_bytes(read())
        self._entries[key] = (results, len(results) * HELD_BYTES_PER_BUILDING)
        self._evict_until(self._budget, keep=key)
        return results

    def held_bytes(self) -> int:
        return sum(size for _, size in self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def _evict_until(self, limit: int, keep: Hashable | None = None) -> None:
        while self._entries and self.held_bytes() > limit:
            oldest = next(iter(self._entries))
            if oldest == keep:
                break  # only the entry just loaded is left: always kept
            del self._entries[oldest]
