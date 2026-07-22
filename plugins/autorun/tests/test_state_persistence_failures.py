"""A write that did not reach storage must not look like one that did.

The daemon log for the window ending 2026-07-21-1620 carried seventeen
occurrences of

    ThreadSafeDB cached N field(s) for '<session>' but skipped persistent state

Each one is a write that the in-memory cache accepted and disk never
received. Nothing failed at the time. The cache kept serving the value, so
the session behaved correctly right up until the daemon restarted and the
value was simply gone — or until another process read the older value from
disk and the two disagreed with no way to tell which was right.

Two properties close that gap, and this file pins both:

  * a persistence failure is reported, not logged and forgotten; and
  * the cached value that failed to persist is dropped, so the next read
     goes back to storage rather than serving something durable state never
     agreed to.

The volatile section covers the other half of the same concern. Advisory
counters are allowed to live only in memory, but not to accumulate there
without bound for the life of a daemon that may run for weeks.
"""
from __future__ import annotations

import contextlib
import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import core, durable_io  # noqa: E402
from autorun.core import (  # noqa: E402
    EventContext,
    StateWriteStatus,
    ThreadSafeDB,
)
from autorun.session_manager import (  # noqa: E402
    SessionPersistenceError,
    SessionTimeoutError,
)


class TestDurablePublicationFailureIsLoud:
    def test_windows_does_not_attempt_the_unsupported_posix_directory_open(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(durable_io.sys, "platform", "win32")

        def forbid_open(*_args, **_kwargs):
            raise AssertionError("Windows attempted POSIX directory fsync")

        monkeypatch.setattr(durable_io.os, "open", forbid_open)
        durable_io.sync_directory(tmp_path)

    def test_directory_open_failure_is_not_reported_as_durable(
        self, tmp_path, monkeypatch
    ):
        def fail_open(*_args, **_kwargs):
            raise OSError("injected directory open failure")

        monkeypatch.setattr(durable_io.os, "open", fail_open)

        with pytest.raises(OSError, match="Could not open directory for fsync"):
            durable_io.sync_directory(tmp_path)

    def test_directory_fsync_failure_is_not_reported_as_durable(
        self, tmp_path, monkeypatch
    ):
        def fail_fsync(*_args, **_kwargs):
            raise OSError("injected directory fsync failure")

        monkeypatch.setattr(durable_io.os, "fsync", fail_fsync)

        with pytest.raises(OSError, match="Could not fsync directory"):
            durable_io.sync_directory(tmp_path)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """One state directory per test, honored by every layer.

    ``session_state`` resolves its directory from the environment and caches
    a manager per resolved directory, so redirecting the module singletons
    alone would leave writes going to the shared suite directory.
    """
    from autorun import session_manager as sm

    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@contextlib.contextmanager
def failing_persistence(error=None):
    """Make persistent writes fail the way a contended one does, then stop.

    Scoped to its own patcher rather than the test's ``monkeypatch``: that
    one is shared with the fixture above, so undoing it mid-test would also
    undo the state-directory redirect and send the rest of the test at the
    shared suite directory.
    """
    error = error or SessionTimeoutError(
        "Could not acquire state lock for 'x' after 0.25s"
    )

    @contextlib.contextmanager
    def failing_session_state(*args, **kwargs):
        raise error
        yield  # pragma: no cover - unreachable, keeps this a generator

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(core, "session_state", failing_session_state)
        yield


class TestPersistenceFailureIsLoud:
    def test_a_failed_write_raises_rather_than_logging_and_continuing(
        self, isolated_state
    ):
        db = ThreadSafeDB()

        with failing_persistence():
            with pytest.raises(SessionPersistenceError) as raised:
                db.set("session-a:file_policy", "SEARCH")

        message = str(raised.value)
        assert "session-a" in message and "file_policy" in message, (
            "The failure must name the session and field that were lost, or "
            f"nobody can tell what to re-issue. Got: {message}"
        )

    def test_the_original_cause_is_preserved(self, isolated_state):
        db = ThreadSafeDB()

        with failing_persistence():
            with pytest.raises(SessionPersistenceError) as raised:
                db.set("session-a:field", 1)

        assert isinstance(raised.value.__cause__, SessionTimeoutError), (
            "A contended write and a broken disk need different responses, so "
            "the underlying error has to survive."
        )

    def test_a_value_that_failed_to_persist_is_not_served_from_cache(
        self, isolated_state
    ):
        """The heart of it: memory must not outvote storage.

        A cache that keeps the unpersisted value reports success to every
        later reader in this process while another process, and this one
        after a restart, sees something else.
        """
        db = ThreadSafeDB()
        db.set("session-a:file_policy", "ALLOW")
        assert db.get("session-a:file_policy") == "ALLOW"

        with failing_persistence():
            with pytest.raises(SessionPersistenceError):
                db.set("session-a:file_policy", "SEARCH")

        assert db.get("session-a:file_policy") == "ALLOW", (
            "The cache is serving a value storage never accepted."
        )

    def test_an_unrelated_cached_field_is_left_alone(self, isolated_state):
        db = ThreadSafeDB()
        db.set("session-a:kept", "value")
        db.set("session-b:other", "value")

        with failing_persistence():
            with pytest.raises(SessionPersistenceError):
                db.set("session-a:failed", "value")

        assert db.get("session-a:kept") == "value"
        assert db.get("session-b:other") == "value"

    def test_a_failed_batch_flush_raises_and_drops_every_staged_value(
        self, isolated_state
    ):
        db = ThreadSafeDB()
        db.set("session-a:before", "durable")

        with failing_persistence():
            with pytest.raises(SessionPersistenceError):
                with db.batch_writes():
                    db.set("session-a:one", 1)
                    db.set("session-a:two", 2)

        assert db.get("session-a:one") is None
        assert db.get("session-a:two") is None
        assert db.get("session-a:before") == "durable"

    def test_a_successful_write_after_a_failure_still_works(self, isolated_state):
        db = ThreadSafeDB()
        with failing_persistence():
            with pytest.raises(SessionPersistenceError):
                db.set("session-a:field", "lost")

        db.set("session-a:field", "kept")
        assert db.get("session-a:field") == "kept"


class TestPersistenceFailureReachesTheCaller:
    def _context(self, db, session_id="session-a"):
        ctx = EventContext(
            session_id=session_id,
            event="PostToolUse",
            prompt="",
            tool_name="Edit",
            tool_input={},
            tool_result="",
            session_transcript=[],
            store=db,
            cli_type="claude",
        )
        ctx.autorun_active = False
        ctx.autorun_stage = EventContext.STAGE_INACTIVE
        return ctx

    def test_a_failed_state_set_tells_the_ai_and_the_user(self, isolated_state):
        """Only the caller can re-issue the write; only the user sees the fallout."""
        db = ThreadSafeDB()
        ctx = self._context(db)

        with failing_persistence():
            ctx.state_set("file_policy", "SEARCH")

        notifications = ctx._chain_notifications
        combined = " ".join(message for message, _channel in notifications)
        channels = {channel for _message, channel in notifications}

        assert "file_policy" in combined, (
            "A dropped state write produced no report, so it is invisible "
            f"until something downstream misbehaves. Notifications: {combined!r}"
        )
        assert "both" in channels, (
            "The report reached only one audience. The caller is the only one "
            "who can re-issue the write, and the user is the one who sees the "
            f"consequences. Channels used: {channels}"
        )

    def test_a_failed_state_set_does_not_break_the_hook(self, isolated_state):
        """State bookkeeping is not worth failing a tool call over."""
        db = ThreadSafeDB()
        ctx = self._context(db)

        with failing_persistence():
            ctx.state_set("file_policy", "SEARCH")  # must not raise

    def test_a_failed_state_set_does_not_leave_the_value_in_request_scope(
        self, isolated_state
    ):
        db = ThreadSafeDB()
        ctx = self._context(db)
        db.set("session-a:file_policy", "ALLOW")

        with failing_persistence():
            ctx.state_set("file_policy", "SEARCH")

        assert ctx.state_get("file_policy") == "ALLOW", (
            "The request kept serving a value that never persisted, so the "
            "rest of the dispatch made decisions on state that does not exist."
        )

    def test_cross_session_failure_does_not_delete_same_named_local_state(
        self, isolated_state
    ):
        db = ThreadSafeDB()
        ctx = self._context(db)
        ctx._state["file_policy"] = "own-session-value"

        with failing_persistence():
            outcome = ctx.state_set(
                "file_policy", "global-value", session_id="__global__"
            )

        assert outcome.status is StateWriteStatus.FAILED
        assert ctx._state["file_policy"] == "own-session-value"

    def test_state_set_reports_durable_and_staged_outcomes(self, isolated_state):
        db = ThreadSafeDB()
        ctx = self._context(db)

        durable = ctx.state_set("one", 1)
        with db.batch_writes():
            staged = ctx.state_set("two", 2)

        assert durable.status is StateWriteStatus.DURABLE
        assert staged.status is StateWriteStatus.STAGED

    def test_state_update_failure_is_typed_and_does_not_poison_local_cache(
        self, isolated_state
    ):
        db = ThreadSafeDB()
        ctx = self._context(db)
        ctx.state_set("counter", 1)

        with failing_persistence():
            with pytest.raises(SessionPersistenceError):
                ctx.state_update("counter", lambda value: value + 1, 0)

        assert ctx.state_get("counter") == 1


class TestVolatileMemoryIsBounded:
    def test_advisory_values_stop_accumulating_at_the_entry_limit(self, isolated_state):
        db = ThreadSafeDB(volatile_max_entries=50)
        for i in range(500):
            db.set_volatile(f"session-{i}:counter", i)

        assert db.volatile_entry_count() <= 50, (
            f"{db.volatile_entry_count()} advisory entries are being held. A "
            "daemon serving many sessions over days would keep every one of "
            "them for its entire life."
        )

    def test_the_most_recently_written_advisory_values_are_the_ones_kept(
        self, isolated_state
    ):
        db = ThreadSafeDB(volatile_max_entries=10)
        for i in range(100):
            db.set_volatile(f"session-{i}:counter", i)

        assert db.get("session-99:counter") == 99
        assert db.get("session-0:counter") is None

    def test_advisory_values_stop_accumulating_at_the_byte_limit(self, isolated_state):
        db = ThreadSafeDB(volatile_max_bytes=4096)
        for i in range(200):
            db.set_volatile(f"session-{i}:blob", "x" * 512)

        assert db.volatile_entry_count() < 200
        assert db.volatile_byte_estimate() <= 4096

    def test_an_advisory_value_expires(self, isolated_state):
        db = ThreadSafeDB(volatile_max_age_seconds=0.05)
        db.set_volatile("session-a:counter", 1)
        assert db.get("session-a:counter") == 1

        time.sleep(0.08)
        assert db.get("session-a:counter") is None, (
            "An advisory value outlived its configured lifetime, so a stale "
            "counter can influence a much later decision."
        )

    def test_evicting_advisory_values_never_removes_durable_ones(self, isolated_state):
        db = ThreadSafeDB(volatile_max_entries=5)
        db.set("keeper:durable", "must survive")

        for i in range(100):
            db.set_volatile(f"session-{i}:counter", i)

        assert db.get("keeper:durable") == "must survive", (
            "Advisory eviction reached a durable value. Losing one of those "
            "changes what the daemon allows or blocks."
        )

    def test_a_durable_write_makes_a_previously_advisory_key_safe_from_eviction(
        self, isolated_state
    ):
        """The same key may start advisory and later be committed."""
        db = ThreadSafeDB(volatile_max_entries=5)
        db.set_volatile("session-a:field", "advisory")
        db.set("session-a:field", "durable")

        for i in range(100):
            db.set_volatile(f"filler-{i}:counter", i)

        assert db.get("session-a:field") == "durable"
