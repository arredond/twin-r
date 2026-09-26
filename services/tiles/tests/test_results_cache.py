from tiles.results_cache import (
    HELD_BYTES_PER_BUILDING,
    HELD_BYTES_PER_COMPRESSED_BYTE,
    ResultsCache,
)
from tiles.scenario_results import COLUMNS, encode


def _file(n_buildings: int, prefix: str) -> bytes:
    rows = [f"{prefix}{i:06d}" for i in range(n_buildings)]
    return encode(
        {
            "building_id": rows,
            "damage_state_code": [1] * n_buildings,
            **{name: [0.2] * n_buildings for name in COLUMNS[2:]},
        }
    )


def _loader(blob: bytes, log: list, key: str):
    def read() -> bytes:
        log.append(key)
        return blob

    return read


def test_hits_do_not_reread_and_least_recently_used_is_evicted_first():
    small = _file(10, "s")
    budget = 3 * 10 * HELD_BYTES_PER_BUILDING  # room for three 10-building scenarios
    cache = ResultsCache(budget_bytes=budget + len(small) * HELD_BYTES_PER_COMPRESSED_BYTE)
    reads: list = []
    for key in ("a", "b", "c"):
        cache.get_or_load(key, len(small), _loader(small, reads, key))
    cache.get_or_load("a", len(small), _loader(small, reads, "a"))  # hit: "a" now most recent
    assert reads == ["a", "b", "c"] and len(cache) == 3

    cache._budget = budget  # now only three fit, and a fourth needs room
    cache.get_or_load("d", 0, _loader(small, reads, "d"))
    assert cache.get("b") is None  # least recently used went first
    assert cache.get("a") is not None and cache.get("c") is not None and cache.get("d") is not None


def test_room_is_made_before_decoding_a_large_entry():
    # The old entries must go *before* the new one is decoded, so the two
    # are never in memory together when the budget can't hold both.
    small, big = _file(10, "s"), _file(2_000, "b")
    budget = 2_000 * HELD_BYTES_PER_BUILDING  # holds `big` alone, not with others
    cache = ResultsCache(budget_bytes=budget)
    cache.get_or_load("small", len(small), lambda: small)
    seen_at_read: list[int] = []

    def read_big() -> bytes:
        seen_at_read.append(len(cache))
        return big

    # A compressed size whose estimate fills the whole budget, as a real
    # file this large would (synthetic ids compress unrealistically well).
    cache.get_or_load("big", budget // HELD_BYTES_PER_COMPRESSED_BYTE, read_big)
    assert seen_at_read == [0]  # "small" already evicted when "big" was read
    assert cache.get("big") is not None and len(cache) == 1


def test_an_entry_larger_than_the_whole_budget_is_still_kept():
    # The request that loaded it needs it; it just evicts everything else.
    big = _file(2_000, "b")
    cache = ResultsCache(budget_bytes=1)
    results = cache.get_or_load("big", len(big), lambda: big)
    assert len(results) == 2_000
    assert cache.get("big") is results and len(cache) == 1
    assert cache.held_bytes() == 2_000 * HELD_BYTES_PER_BUILDING
