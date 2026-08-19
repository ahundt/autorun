#!/usr/bin/env python3
"""Advisory in-memory state stays bounded, and bounding it stays cheap.

A daemon serves many sessions for weeks, and advisory values (counters, flags
between durable checkpoints) have no durable home, so nothing else would ever
remove them. `volatile_state_max_entries` / `_max_bytes` / `_max_age_seconds`
are what keep that from growing without limit.

Two properties matter and are easy to lose independently:

* the limits are actually enforced, and enforcing them never discards a
  *durable* cached value -- that would change what the daemon allows or blocks;
* enforcement is cheap. It runs on every advisory write, which happens on the
  hook path, so a scan proportional to the number of live entries would make
  the cost of one write grow with how busy the daemon has been.

The ordering invariant that makes the cheap version correct: `_track_volatile`
pops a key before reinserting it, so `_volatile` is an OrderedDict in ascending
order of last write. Expired entries are therefore always a prefix, and the
sweep can stop at the first live one.
"""

import time

import pytest

from autorun.core import ThreadSafeDB


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """One state directory per test, honored by every layer.

    Same shape as test_state_persistence_failures.py: ``session_state``
    resolves its directory from the environment and caches a manager per
    resolved directory, so redirecting the module singletons alone would leave
    writes going to the shared suite directory.
    """
    from autorun import session_manager as sm

    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@pytest.fixture
def db(isolated_state):
    """A store with small limits so the bounds are reachable in a test."""
    return ThreadSafeDB(
        volatile_max_entries=8,
        volatile_max_bytes=10_000,
        volatile_max_age_seconds=3600.0,
    )


def _fill(db, count, prefix="session-a:field"):
    for index in range(count):
        db.set_volatile(f"{prefix}{index}", index)


def test_the_entry_limit_is_enforced(db):
    _fill(db, 20)
    assert len(db._volatile) <= 8


def test_the_byte_limit_is_enforced(isolated_state):
    db = ThreadSafeDB(
        volatile_max_entries=10_000,
        volatile_max_bytes=200,
        volatile_max_age_seconds=3600.0,
    )
    for index in range(50):
        db.set_volatile(f"session-a:big{index}", "x" * 40)
    assert db._volatile_bytes <= 200


def test_an_expired_entry_is_dropped(db):
    db.set_volatile("session-a:old", 1)
    # Age the entry by rewriting its timestamp rather than sleeping an hour.
    written, size = db._volatile["session-a:old"]
    db._volatile["session-a:old"] = (written - 7200.0, size)

    db.set_volatile("session-a:new", 2)

    assert "session-a:old" not in db._volatile
    assert "session-a:new" in db._volatile


def test_eviction_never_discards_a_durable_value(db):
    """Only advisory keys are eligible; a durable value must survive.

    Evicting a durable value would be silently reconstructed from disk at best,
    and at worst change a permission decision.
    """
    db.set("session-a:file_policy", "SEARCH")
    _fill(db, 40)

    assert db.get("session-a:file_policy") == "SEARCH"


def test_the_age_sweep_visits_only_the_expired_prefix(db, monkeypatch):
    """Enforcement must not scan every live entry on every write.

    Counts how many entries the sweep inspects. With the limits above, a write
    that expires nothing must look at a constant number of entries rather than
    at all of them, or the cost of one advisory write grows with how many are
    already held.
    """
    _fill(db, 8)

    visited = []

    class _CountingOrderedDict(type(db._volatile)):
        def items(self):
            for key, entry in super().items():
                visited.append(key)
                yield key, entry

    db._volatile = _CountingOrderedDict(db._volatile)
    visited.clear()
    db.set_volatile("session-a:trigger", 1)

    assert len(visited) <= 2, (
        "the age sweep walked the whole table for a write that expired "
        f"nothing; it visited {len(visited)} entries"
    )


def test_the_prefix_sweep_drops_exactly_what_a_full_scan_would(db):
    """Stopping early must not leave an expired entry behind."""
    for index in range(6):
        db.set_volatile(f"session-a:k{index}", index)
    # Expire the three oldest by backdating them, preserving order.
    for index in range(3):
        key = f"session-a:k{index}"
        written, size = db._volatile[key]
        db._volatile[key] = (written - 7200.0, size)

    db.set_volatile("session-a:trigger", 1)

    remaining = set(db._volatile)
    assert not any(f"session-a:k{i}" in remaining for i in range(3))
    assert all(f"session-a:k{i}" in remaining for i in range(3, 6))


def test_a_store_at_its_limits_still_answers_reads(db):
    """Both-exhausted behavior: capped memory must not break the gate.

    With the advisory table full, a durable read still resolves -- from cache
    when present, and from storage when the advisory copy was evicted.
    """
    db.set("session-a:file_policy", "ALLOW")
    _fill(db, 200)

    assert db.get("session-a:file_policy") == "ALLOW"
    assert len(db._volatile) <= 8
