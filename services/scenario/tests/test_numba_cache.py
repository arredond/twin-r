import os
import stat
import types

import pytest
from numba.core import caching
from scenario import numba_cache

# The locator numba_cache.use_size_only_source_stamps patches (public in
# local dev's numba, private in the image's 0.61).
_LOCATOR = getattr(caching, "UserProvidedCacheLocator", None) or getattr(  # noqa: B009
    caching, "_UserProvidedCacheLocator"
)


@pytest.fixture(autouse=True)
def _restore_numba_stamps(monkeypatch):
    """seed() patches numba process-wide; undo it after every test here so
    it can't leak into other tests (or into each other)."""
    monkeypatch.setattr(_LOCATOR, "get_source_stamp", _LOCATOR.get_source_stamp)
    # seed() runs once per process; each test starts unseeded.
    monkeypatch.setattr(numba_cache, "_seeded_at", None)


def _read_only_seed(tmp_path):
    seed = tmp_path / "seed"
    (seed / "geo_abc").mkdir(parents=True)
    (seed / "geo_abc" / "f.nbi").write_bytes(b"index")
    for path in [seed / "geo_abc" / "f.nbi", seed / "geo_abc", seed]:
        path.chmod(0o555 if path.is_dir() else 0o444)
    return seed


def test_seed_copies_the_prebuilt_cache_into_a_writable_numba_cache_dir(tmp_path, monkeypatch):
    # The image's copy is read-only (as Lambda's image filesystem is); the
    # seeded one must not be, or numba won't treat it as its cache.
    seed = _read_only_seed(tmp_path)
    target = tmp_path / "numba_cache"
    monkeypatch.setenv(numba_cache.SEED_DIR_ENV, str(seed))
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(target))

    numba_cache.seed()

    assert (target / "geo_abc" / "f.nbi").read_bytes() == b"index"
    for root, _, _ in os.walk(target):
        assert os.stat(root).st_mode & stat.S_IWUSR
    (target / "geo_abc" / "new.nbc").write_bytes(b"numba can add to it")


def test_seed_is_a_no_op_without_a_seed_dir_or_when_already_seeded(tmp_path, monkeypatch):
    target = tmp_path / "numba_cache"
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(target))
    monkeypatch.delenv(numba_cache.SEED_DIR_ENV, raising=False)
    numba_cache.seed()
    assert not target.exists()  # local dev: numba's own cache, untouched

    target.mkdir()
    (target / "kept").write_bytes(b"warm environment")
    monkeypatch.setenv(numba_cache.SEED_DIR_ENV, str(_read_only_seed(tmp_path)))
    numba_cache.seed()
    assert os.listdir(target) == ["kept"]


def test_files_written_since_seed_counts_only_what_numba_adds_afterwards(tmp_path, monkeypatch):
    # numba writes to its cache only on a miss, so this is the diagnostic's
    # "did the prebuilt cache cover it" signal (numba_cache.py).
    seed = tmp_path / "seed"
    (seed / "geo_abc").mkdir(parents=True)
    (seed / "geo_abc" / "f.nbi").write_bytes(b"index")
    target = tmp_path / "numba_cache"
    monkeypatch.setenv(numba_cache.SEED_DIR_ENV, str(seed))
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(target))
    monkeypatch.setattr(numba_cache, "_seeded_at", None)
    assert numba_cache.files_written_since_seed() is None  # seed() hasn't run

    numba_cache.seed()
    assert numba_cache.files_written_since_seed() == 0  # the copy itself doesn't count

    new = target / "geo_abc" / "g.nbc"
    new.write_bytes(b"compiled on a miss")
    later = numba_cache._seeded_at + 1  # pyrefly: ignore -- set by seed() above
    os.utime(new, (later, later))
    assert numba_cache.files_written_since_seed() == 1


def test_size_only_stamps_ignore_modification_time_in_the_image(tmp_path, monkeypatch):
    # Lambda doesn't preserve the image's file mtimes, which made numba
    # treat every prebuilt cache entry as stale (numba_cache.py).
    locator = _LOCATOR
    source = tmp_path / "module.py"
    source.write_text("x = 1\n")
    fake = types.SimpleNamespace(_py_file=str(source))

    monkeypatch.delenv(numba_cache.SEED_DIR_ENV, raising=False)
    numba_cache.use_size_only_source_stamps()  # outside the image: untouched
    assert locator.get_source_stamp(fake)[0] == os.stat(source).st_mtime

    monkeypatch.setenv(numba_cache.SEED_DIR_ENV, str(tmp_path))
    numba_cache.use_size_only_source_stamps()
    before = locator.get_source_stamp(fake)
    os.utime(source, (1577836800, 1577836800))
    assert locator.get_source_stamp(fake) == before == (0.0, len("x = 1\n"))


def test_prepare_seeds_once_and_switches_to_size_only_stamps(tmp_path, monkeypatch):
    seed = _read_only_seed(tmp_path)
    target = tmp_path / "numba_cache"
    monkeypatch.setenv(numba_cache.SEED_DIR_ENV, str(seed))
    monkeypatch.setenv("NUMBA_CACHE_DIR", str(target))
    source = tmp_path / "module.py"
    source.write_text("x = 1\n")

    numba_cache.prepare()
    assert (target / "geo_abc" / "f.nbi").exists()
    assert _LOCATOR.get_source_stamp(types.SimpleNamespace(_py_file=str(source)))[0] == 0.0

    (target / "geo_abc" / "f.nbi").unlink()
    numba_cache.prepare()  # already seeded in this environment: no second copy
    assert not (target / "geo_abc" / "f.nbi").exists()
