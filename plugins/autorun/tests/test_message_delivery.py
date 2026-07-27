"""Temporal message-delivery claims are bounded and concurrency-safe."""

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
from threading import Lock
import uuid

import pytest

from autorun.config import CONFIG
from autorun.core import EventContext
from autorun.message_delivery import MessageDelivery, claim_message_delivery
from autorun.session_manager import SessionPersistenceError


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
    ctx = EventContext(session_id, "PreToolUse", cli_type="codex")
    barrier.wait()
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
        process.join(timeout=10)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=1) for _ in processes]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 3


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
