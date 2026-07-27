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
from .session_manager import SessionPersistenceError

logger = logging.getLogger(__name__)

_STATE_FIELD = "message_delivery_claims"
_CLAIM_FIELD_COUNT = 2
_DIGEST_SIZE_BYTES = 16


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
        claimed_at = time.time() if now is None else float(now)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(claimed_at):
        return True
    expires_at = claimed_at + window
    digest = _digest(ctx, delivery, message)
    cap = _entry_cap()
    if cap is None:
        return True
    outcome = {"deliver": True}

    def claim(current):
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

    try:
        ctx.state_update(_STATE_FIELD, claim, {})
    except SessionPersistenceError as exc:
        logger.warning(
            "Temporal message claim was not persisted; delivering fail-open: %s",
            exc,
        )
        return True
    return outcome["deliver"]


__all__ = ["MessageDelivery", "claim_message_delivery"]
