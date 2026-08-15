"""Temporal message-delivery claims are bounded and concurrency-safe."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from threading import Lock
import time
import uuid

import pytest

from autorun.config import CONFIG
from autorun.core import EventContext
from autorun.message_delivery import MessageDelivery, claim_message_delivery
from autorun.session_manager import SessionPersistenceError, SessionTimeoutError


class AtomicContext:
    """Small state_update-compatible context for deterministic claim tests."""

    event = "PreToolUse"
    cli_type = "codex"
    session_id = "message-delivery-test"

    def __init__(self):
        self._lock = Lock()
        self.state = {}

    def state_update(self, name, updater, default=None):
        with self._lock:
            current = self.state.get(name, default)
            value = updater(current)
            self.state[name] = value
            return value


@pytest.fixture
def delivery_config(monkeypatch):
    monkeypatch.setitem(CONFIG, "message_dedup_enabled", True)
    monkeypatch.setitem(CONFIG, "message_dedup_window_seconds", 3.0)
    monkeypatch.setitem(CONFIG, "message_dedup_max_entries_per_session", 4)
    monkeypatch.setitem(
        CONFIG,
        "message_dedup_categories",
        {"integration_warning": True, "informational_notification": True},
    )


def _warning(identity="git-rules", window_seconds=None):
    return MessageDelivery(
        category="integration_warning",
        identity=identity,
        window_seconds=window_seconds,
        channels=("human", "ai"),
    )


def _process_claim(session_id, barrier, results):
    # Production hooks carry a wrapper deadline, and that deadline is what
    # authorizes claim retries under store-lock contention: without one the
    # FIRST contended attempt fails open and delivers by design (duplication
    # over loss). Four spawned processes on a slow Windows runner exceed the
    # 0.5s lock attempt at the barrier, which turned this exactly-one
    # assertion into two winners. A wide explicit deadline makes every
    # process retry to a real decision, which is the atomicity property this
    # test exists to pin.
    ctx = EventContext(
        session_id,
        "PreToolUse",
        cli_type="codex",
        deadline_monotonic=time.monotonic() + 30.0,
    )
    # A timeout so a sibling that never spawns breaks the barrier into a
    # clean nonzero exit instead of leaving this child blocked forever and
    # hanging interpreter exit (see test_task_pause's spawn constants).
    barrier.wait(timeout=90.0)
    results.put(
        claim_message_delivery(
            ctx,
            _warning(),
            "Git commit rules",
            now=100.0,
        )
    )


def test_exactly_one_concurrent_claim_wins(delivery_config):
    ctx = AtomicContext()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: claim_message_delivery(ctx, _warning(), "Git commit rules", now=100.0),
                range(8),
            )
        )

    assert results.count(True) == 1
    assert results.count(False) == 7


@pytest.mark.race
@pytest.mark.timeout(300)
def test_exactly_one_process_claim_wins(delivery_config):
    process_context = multiprocessing.get_context("spawn")
    barrier = process_context.Barrier(4)
    results = process_context.Queue()
    session_id = f"message-delivery-{uuid.uuid4().hex}"
    processes = [
        process_context.Process(
            target=_process_claim,
            args=(session_id, barrier, results),
        )
        for _ in range(4)
    ]

    for process in processes:
        process.start()
    for process in processes:
        # Cold spawn interpreters import the full package before the barrier;
        # 10s was regularly exceeded on busy machines.
        process.join(timeout=120)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=1) for _ in processes]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3


def test_claim_timestamp_is_read_inside_the_locked_updater(delivery_config, monkeypatch):
    """time.time() must run under the store lock, not before it.

    A pre-lock timestamp plus a lock wait makes the stored claim look like it
    came from the future: the backward-clock guard discards it, the same
    warning delivers again, and the replacement claim carries an OLDER
    timestamp that invites a third delivery. Reproduced 2026-08-04 with four
    real hook processes racing one git warning — three deliveries. Timestamp
    order equals lock order only when the clock is read inside the updater;
    the explicit ``now`` parameter remains for deterministic tests.
    """
    from autorun import message_delivery as md

    calls = {"inside": 0, "outside": 0}
    state = {"updating": False}

    class TrackingContext(AtomicContext):
        def state_update(self, name, updater, default=None):
            def tracked(current):
                state["updating"] = True
                try:
                    return updater(current)
                finally:
                    state["updating"] = False

            return super().state_update(name, tracked, default)

    class ClockShim:
        @staticmethod
        def time():
            calls["inside" if state["updating"] else "outside"] += 1
            return 1000.0

    monkeypatch.setattr(md, "time", ClockShim)

    assert claim_message_delivery(TrackingContext(), _warning(), "Git commit rules") is True
    assert calls["outside"] == 0, "claim timestamp was captured before the lock"
    assert calls["inside"] >= 1, "claim never read the clock at all"


def test_expiry_reenables_delivery(delivery_config):
    ctx = AtomicContext()

    assert claim_message_delivery(ctx, _warning(), "Git commit rules", now=100.0)
    assert not claim_message_delivery(ctx, _warning(), "Git commit rules", now=102.9)
    assert claim_message_delivery(ctx, _warning(), "Git commit rules", now=103.0)


def test_different_identity_or_channel_is_not_suppressed(delivery_config):
    ctx = AtomicContext()

    assert claim_message_delivery(ctx, _warning("git"), "Same text", now=100.0)
    assert claim_message_delivery(ctx, _warning("ruff"), "Same text", now=100.0)
    ai_only = MessageDelivery("integration_warning", "git", channels=("ai",))
    assert claim_message_delivery(ctx, ai_only, "Same text", now=100.0)


def test_disabled_feature_or_category_fails_open(delivery_config, monkeypatch):
    ctx = AtomicContext()
    monkeypatch.setitem(CONFIG, "message_dedup_enabled", False)
    assert claim_message_delivery(ctx, _warning(), "Same text", now=100.0)
    assert claim_message_delivery(ctx, _warning(), "Same text", now=100.0)

    monkeypatch.setitem(CONFIG, "message_dedup_enabled", True)
    monkeypatch.setitem(CONFIG, "message_dedup_categories", {})
    assert claim_message_delivery(ctx, _warning(), "Same text", now=100.0)
    assert claim_message_delivery(ctx, _warning(), "Same text", now=100.0)


def test_persistence_failure_fails_open(delivery_config):
    class FailingContext(AtomicContext):
        def state_update(self, name, updater, default=None):
            raise SessionPersistenceError("disk unavailable")

    assert claim_message_delivery(FailingContext(), _warning(), "Never lose this warning", now=100.0)


def test_claim_map_is_bounded_and_prunes_expired_entries(delivery_config):
    ctx = AtomicContext()

    for index in range(8):
        assert claim_message_delivery(ctx, _warning(f"warning-{index}"), f"message-{index}", now=100.0)

    claims = ctx.state["message_delivery_claims"]
    assert len(claims) == CONFIG["message_dedup_max_entries_per_session"]

    assert claim_message_delivery(ctx, _warning("after-expiry"), "new", now=104.0)
    claims = ctx.state["message_delivery_claims"]
    assert len(claims) == 1


def test_clock_rollback_discards_future_claim(delivery_config):
    ctx = AtomicContext()

    assert claim_message_delivery(ctx, _warning(), "Same text", now=100.0)
    assert claim_message_delivery(ctx, _warning(), "Same text", now=90.0)


@pytest.mark.parametrize(
    "invalid_window",
    [0, -1, float("inf"), float("-inf"), float("nan"), "not-seconds"],
)
def test_invalid_window_configuration_fails_open(delivery_config, monkeypatch, invalid_window):
    ctx = AtomicContext()
    monkeypatch.setitem(CONFIG, "message_dedup_window_seconds", invalid_window)

    assert claim_message_delivery(ctx, _warning(), "Never suppress", now=100.0)
    assert claim_message_delivery(ctx, _warning(), "Never suppress", now=100.0)


@pytest.mark.parametrize("invalid_cap", [0, -1, True, 1.5, "4"])
def test_invalid_entry_cap_fails_open(delivery_config, monkeypatch, invalid_cap):
    ctx = AtomicContext()
    monkeypatch.setitem(
        CONFIG,
        "message_dedup_max_entries_per_session",
        invalid_cap,
    )

    assert claim_message_delivery(ctx, _warning(), "Never suppress", now=100.0)
    assert claim_message_delivery(ctx, _warning(), "Never suppress", now=100.0)


def test_claimant_crash_window_reenables_attempt_after_configured_expiry(delivery_config):
    ctx = AtomicContext()

    assert claim_message_delivery(ctx, _warning(), "Eligible notice", now=100.0)
    # Model a winner that exits before writing its already-claimed response.
    assert not claim_message_delivery(ctx, _warning(), "Eligible notice", now=100.1)
    assert claim_message_delivery(ctx, _warning(), "Eligible notice", now=103.0)


class _ContendedContext:
    """A context whose claim loses the state lock a fixed number of times.

    Mirrors what `state_update` does with a contended lock: it wraps the
    `SessionTimeoutError` in a `SessionPersistenceError` and chains the cause,
    which is the only place the original type survives.
    """

    event = "PreToolUse"
    cli_type = "codex"
    session_id = "message-delivery-contended"

    def __init__(self, timeouts, shared_state=None, deadline_monotonic=None):
        self.remaining_timeouts = timeouts
        self.state = {} if shared_state is None else shared_state
        self.attempts = 0
        self.deadline_monotonic = deadline_monotonic

    def state_update(self, name, updater, default=None):
        self.attempts += 1
        if self.remaining_timeouts > 0:
            self.remaining_timeouts -= 1
            wrapped = SessionPersistenceError("State update never ran")
            wrapped.__cause__ = SessionTimeoutError(
                "Could not acquire state lock for 'x' after 0.5s"
            )
            raise wrapped
        current = self.state.get(name, default)
        value = updater(current)
        self.state[name] = value
        return value


@pytest.fixture
def hook_deadline():
    """Return the effective deadline carried by one hook request."""

    return lambda seconds_ahead: time.monotonic() + seconds_ahead


class TestContentionDoesNotDuplicateDelivery:
    """A claim that never ran must not be treated as a claim that lost.

    Failing open on contention means every process that merely queued for the
    lock delivers, so one warning becomes as many copies as there are
    concurrent hooks. Failing open on a genuine persistence failure is the
    long-standing contract and must survive.
    """

    def test_a_contended_claim_is_retried_and_can_still_lose(
        self, delivery_config, hook_deadline
    ):
        shared = {}
        winner = _ContendedContext(timeouts=0, shared_state=shared)
        assert claim_message_delivery(
            winner, _warning(), "Git commit rules", now=100.0
        ) is True

        loser = _ContendedContext(
            timeouts=2,
            shared_state=shared,
            deadline_monotonic=hook_deadline(30.0),
        )
        delivered = claim_message_delivery(
            loser, _warning(), "Git commit rules", now=100.0
        )

        assert loser.attempts == 3, (
            "the claim must be retried while the hook deadline allows, not "
            f"abandoned on the first timeout; attempts={loser.attempts}"
        )
        assert delivered is False, (
            "after retrying, this process saw the winner's claim and must stay "
            "quiet. Delivering here is the duplicate: N contended hooks would "
            "each emit the same warning."
        )

    def test_without_a_hook_deadline_the_historic_fail_open_is_unchanged(
        self, delivery_config
    ):
        """Outside a hook there is no budget to spend, so never guess one."""
        ctx = _ContendedContext(timeouts=1)

        delivered = claim_message_delivery(
            ctx, _warning(), "Git commit rules", now=100.0
        )

        assert ctx.attempts == 1, "no deadline means no retry budget"
        assert delivered is True, "fail-open must remain the fallback"

    def test_an_expired_deadline_does_not_buy_another_attempt(
        self, delivery_config
    ):
        ctx = _ContendedContext(
            timeouts=1, deadline_monotonic=time.monotonic() - 1.0
        )

        assert claim_message_delivery(
            ctx, _warning(), "Git commit rules", now=100.0
        ) is True
        assert ctx.attempts == 1, (
            "a hook past its wrapper deadline must answer, not keep waiting"
        )

    def test_a_genuine_persistence_failure_still_delivers_without_retrying(
        self, delivery_config, hook_deadline
    ):
        """Real data loss is not contention: retrying it would only stall."""

        class _Dropped(_ContendedContext):
            def state_update(self, name, updater, default=None):
                self.attempts += 1
                raise SessionPersistenceError(
                    "State was not saved and has been dropped from memory"
                )

        ctx = _Dropped(timeouts=0, deadline_monotonic=hook_deadline(30.0))
        assert claim_message_delivery(
            ctx, _warning(), "Git commit rules", now=100.0
        ) is True
        assert ctx.attempts == 1
