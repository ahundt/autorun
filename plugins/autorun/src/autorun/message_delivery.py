"""Bounded cross-process claims for explicitly idempotent hook messages."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import math
import time

from .config import (
    CONFIG,
    MESSAGE_DEDUP_DEFAULT_ENTRY_CAP,
    MESSAGE_DEDUP_DEFAULT_WINDOW_SECONDS,
)
from .session_manager import SessionPersistenceError, state_failure_is_contention

logger = logging.getLogger(__name__)

_STATE_FIELD = "message_delivery_claims"
_CLAIM_FIELD_COUNT = 2
_DIGEST_SIZE_BYTES = 16


def _lock_attempt_seconds() -> float:
    return float(CONFIG.get("hook_state_lock_timeout_seconds", 0.5))


def _retry_budget_allows_another_attempt(ctx) -> bool:
    """Is there room in the hook's own deadline for one more lock attempt?

    Retrying is only safe inside a hook that told us when its wrapper gives up.
    Without that instant this returns False and the caller keeps the historic
    fail-open behaviour, because guessing a budget is how a hook overruns its
    wrapper and gets killed before it can answer at all.
    """
    deadline = getattr(ctx, "deadline_monotonic", None)
    if deadline is None:
        return False
    remaining = deadline - time.monotonic()
    # One full attempt plus the margin that keeps the response writable.
    return remaining > _lock_attempt_seconds() * 2


@dataclass(frozen=True)
class MessageDelivery:
    """Opt-in temporal delivery policy for one informational message.

    The caller supplies a stable semantic identity. Event, harness, channels,
    and formatted text are added to the digest so unrelated deliveries cannot
    suppress one another accidentally.
    """

    category: str
    identity: str
    window_seconds: float | None = None
    channels: tuple[str, ...] = ("human", "ai")


def _delivery_enabled(delivery: MessageDelivery) -> bool:
    if not CONFIG.get("message_dedup_enabled", True):
        return False
    categories = CONFIG.get("message_dedup_categories", {})
    return isinstance(categories, dict) and categories.get(delivery.category) is True


def _window_seconds(delivery: MessageDelivery) -> float:
    value = (
        delivery.window_seconds
        if delivery.window_seconds is not None
        else CONFIG.get(
            "message_dedup_window_seconds",
            MESSAGE_DEDUP_DEFAULT_WINDOW_SECONDS,
        )
    )
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed > 0 else 0.0


def _entry_cap() -> int | None:
    value = CONFIG.get(
        "message_dedup_max_entries_per_session",
        MESSAGE_DEDUP_DEFAULT_ENTRY_CAP,
    )
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        logger.warning(
            "message_dedup_max_entries_per_session must be an integer greater than 0, got %r; temporal suppression is disabled for this call",
            value,
        )
        return None
    return value


def _digest(ctx, delivery: MessageDelivery, message: str) -> str:
    parts = (
        delivery.category,
        delivery.identity,
        str(getattr(ctx, "event", "")),
        str(getattr(ctx, "cli_type", "")),
        "\x1f".join(delivery.channels),
        message,
    )
    payload = "\x00".join(parts).encode("utf-8", errors="surrogatepass")
    return hashlib.blake2b(payload, digest_size=_DIGEST_SIZE_BYTES).hexdigest()


def claim_message_delivery(
    ctx,
    delivery: MessageDelivery,
    message: str,
    *,
    now: float | None = None,
) -> bool:
    """Return whether this process owns the delivery.

    Disabled, invalid, empty, or persistence-failed claims return ``True``:
    duplication is preferable to losing an informational warning. Callers must
    never use this helper for deny/block/ask responses, failures, state
    transitions, command replies, or Stop-generation instructions.
    """

    if not message or not _delivery_enabled(delivery):
        return True
    window = _window_seconds(delivery)
    if window <= 0:
        return True

    try:
        explicit_now = None if now is None else float(now)
    except (TypeError, ValueError):
        return True
    if explicit_now is not None and not math.isfinite(explicit_now):
        return True
    digest = _digest(ctx, delivery, message)
    cap = _entry_cap()
    if cap is None:
        return True
    outcome = {"deliver": True}

    def claim(current):
        # The clock is read HERE, after state_update holds the store lock. A
        # pre-lock time.time() plus a lock wait made the stored claim look
        # like it came from the future: the backward-travel guard below
        # discarded it, the warning delivered again, and the replacement
        # claim carried an older timestamp inviting a third delivery
        # (reproduced 2026-08-04 — three deliveries of one git warning from
        # four concurrent hook processes). Read under the lock, timestamp
        # order matches lock order, so the guard fires only on genuine
        # wall-clock steps. The explicit ``now`` stays for tests.
        claimed_at = time.time() if explicit_now is None else explicit_now
        expires_at = claimed_at + window
        raw = current if isinstance(current, dict) else {}
        active: dict[str, list[float]] = {}
        for key, entry in raw.items():
            if isinstance(key, str) and isinstance(entry, (list, tuple)) and len(entry) == _CLAIM_FIELD_COUNT:
                try:
                    prior_claimed, prior_expires = map(float, entry)
                except (TypeError, ValueError):
                    continue
                # Wall time is required across processes. If it moves backward,
                # a claim from the future is invalid instead of suppressing for
                # an unbounded interval.
                if prior_claimed <= claimed_at < prior_expires:
                    active[key] = [prior_claimed, prior_expires]

        if digest in active:
            outcome["deliver"] = False
            return active

        if len(active) >= cap:
            oldest = min(active, key=lambda key: active[key][1])
            active.pop(oldest, None)
        active[digest] = [claimed_at, expires_at]
        outcome["deliver"] = True
        return active

    while True:
        try:
            ctx.state_update(_STATE_FIELD, claim, {})
            break
        except SessionPersistenceError as exc:
            # Contention is not a failed claim, it is a claim that never ran:
            # nothing was read and nothing written, so no other process learned
            # anything from this one either. Failing open here means every
            # process that merely queued for the lock delivers, which turns one
            # warning into as many copies as there are concurrent hooks. Retry
            # while this hook's own deadline leaves room for another attempt.
            if state_failure_is_contention(exc) and _retry_budget_allows_another_attempt(ctx):
                continue
            # Out of budget, or a genuine persistence failure. Deliver: a
            # duplicated informational message is preferable to a lost one,
            # which is the long-standing contract of this helper.
            logger.warning(
                "Temporal message claim was not persisted; delivering fail-open: %s",
                exc,
            )
            return True
    return outcome["deliver"]


__all__ = ["MessageDelivery", "claim_message_delivery"]
