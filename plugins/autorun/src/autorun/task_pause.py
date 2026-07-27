"""User-owned, generation-bound pause of task-enforcement pathways."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import secrets
import time
from typing import Literal

from .config import (
    CONFIG,
    SCOPED_ALLOW_DEFAULT_GRACE_SECONDS,
    TASK_PAUSE_GENERATION_TOKEN_BYTES,
)
from .core import EventContext, logger
from .scoped_allow import ScopeSpec, ScopedGrant, fingerprint_call
from .session_manager import SessionPersistenceError


_STATE_KEY = "task_enforcement_pause"


class TaskPauseIdentityError(ValueError):
    """The current context lacks a logical-session identity."""


def _positive_float_config(key: str, default: float) -> float:
    value = CONFIG.get(key, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a finite number greater than 0, got {value!r}; pass positive seconds") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{key} must be a finite number greater than 0, got {value!r}; pass positive seconds")
    return parsed


def _positive_int_config(key: str, default: int) -> int:
    value = CONFIG.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{key} must be an integer greater than 0, got {value!r}; pass a positive byte count")
    return value


@dataclass(frozen=True, slots=True)
class TaskPause:
    """One immutable user-created task-enforcement pause."""

    grant: ScopedGrant
    generation: str
    reason: str = ""
    origin: Literal["user"] = "user"

    def to_dict(self) -> dict[str, object]:
        return {
            "grant": self.grant.to_dict(),
            "generation": self.generation,
            "reason": self.reason,
            "origin": self.origin,
        }

    @classmethod
    def from_value(cls, value: object) -> TaskPause | None:
        if not isinstance(value, dict) or value.get("origin") != "user":
            return None
        generation = value.get("generation")
        grant_value = value.get("grant")
        if not isinstance(generation, str) or not generation:
            return None
        if not isinstance(grant_value, dict):
            return None
        try:
            grant = ScopedGrant.from_dict(grant_value)
        except (TypeError, ValueError):
            return None
        reason = value.get("reason", "")
        if not isinstance(reason, str):
            return None
        return cls(grant=grant, generation=generation, reason=reason)


def activate_task_pause(
    ctx: EventContext,
    *,
    scope: ScopeSpec,
    reason: str,
    now: float | None = None,
) -> TaskPause:
    """Replace the current pause after verifying logical-session authority."""
    if ctx.session_identity_authority not in {
        "explicit-shared",
        "history",
        "payload",
    }:
        raise TaskPauseIdentityError(
            "task pause requires a logical session identity; the current "
            f"identity is {ctx.session_identity_authority!r}. Configure the "
            "harness to send a session/transcript ID, or set "
            "AUTORUN_SESSION_ID to a deliberately shared name"
        )
    timestamp = time.time() if now is None else now
    pause = TaskPause(
        grant=ScopedGrant(
            granted_at=timestamp,
            ttl_seconds=scope.ttl_seconds,
            remaining_uses=scope.remaining_uses,
            grace_seconds=_positive_float_config(
                "scoped_allow_default_grace_seconds",
                SCOPED_ALLOW_DEFAULT_GRACE_SECONDS,
            ),
        ),
        generation=secrets.token_urlsafe(
            _positive_int_config(
                "task_pause_generation_token_bytes",
                TASK_PAUSE_GENERATION_TOKEN_BYTES,
            )
        ),
        reason=reason.strip(),
    )
    ctx.state_update(_STATE_KEY, lambda _current: pause.to_dict())
    return pause


def resume_task_pause(
    ctx: EventContext,
    *,
    expected_generation: str | None = None,
) -> bool:
    """Clear the pause only if its generation still matches."""
    resumed = False

    def clear_if_current(value: object) -> dict[str, object] | None:
        nonlocal resumed
        pause = TaskPause.from_value(value)
        if pause is None:
            return None
        if expected_generation is not None and not secrets.compare_digest(
            pause.generation,
            expected_generation,
        ):
            return pause.to_dict()
        resumed = True
        return None

    ctx.state_update(_STATE_KEY, clear_if_current)
    return resumed


def task_enforcement_is_paused(
    ctx: EventContext,
    *,
    now: float | None = None,
) -> bool:
    """Read and lazily prune pause state without consuming a Stop count."""
    active = False

    def inspect(value: object) -> dict[str, object] | None:
        nonlocal active
        pause = TaskPause.from_value(value)
        if pause is None or not pause.grant.is_active(now=now):
            return None
        active = True
        return pause.to_dict()

    try:
        ctx.state_update(_STATE_KEY, inspect)
    except SessionPersistenceError as exc:
        logger.warning(
            "Task-pause state could not be read; enforcing tasks normally: %s",
            exc,
        )
        return False
    return active


def _latest_assistant_identity(ctx: EventContext) -> tuple[str, str]:
    transcript = getattr(ctx, "transcript", None)
    latest = transcript.latest_assistant_message() if transcript else None
    if isinstance(latest, tuple) and len(latest) == 2 and all(isinstance(value, str) for value in latest):
        return latest
    message = getattr(ctx, "last_assistant_message", "") or ""
    if not message:
        return "", ""
    return fingerprint_call(
        getattr(ctx, "session_id", ""),
        "assistant-message",
        message,
    ), message


def task_pause_allows_stop(
    ctx: EventContext,
    *,
    now: float | None = None,
) -> bool:
    """Atomically clear a recovery marker or claim one logical Stop."""
    assistant_identity, assistant_text = _latest_assistant_identity(ctx)
    if not assistant_identity:
        return False
    from .task_lifecycle import extract_task_pause_resume_generations

    resume_generations = set(extract_task_pause_resume_generations(assistant_text))
    call_id = fingerprint_call(
        getattr(ctx, "session_id", ""),
        "Stop",
        assistant_identity,
    )
    allowed = False

    def decide(value: object) -> dict[str, object] | None:
        nonlocal allowed
        pause = TaskPause.from_value(value)
        if pause is None:
            return None
        if pause.generation in resume_generations:
            return None
        claim = pause.grant.claim_once(call_id, now=now)
        if not claim.allowed:
            return None
        allowed = True
        return replace(pause, grant=claim.grant).to_dict()

    try:
        ctx.state_update(_STATE_KEY, decide)
    except SessionPersistenceError as exc:
        logger.warning(
            "Task-pause Stop claim failed; enforcing tasks normally: %s",
            exc,
        )
        return False
    return allowed


def task_pause_status(
    ctx: EventContext,
    *,
    now: float | None = None,
) -> str:
    """Render active pause guidance or an inactive status line."""
    guidance = task_pause_guidance(ctx, now=now, report_unavailable=True)
    return guidance or "⏸️ Task enforcement pause: inactive"


def task_pause_guidance(
    ctx: EventContext,
    *,
    now: float | None = None,
    report_unavailable: bool = False,
) -> str | None:
    """Return active pause guidance from one atomic read, or ``None``."""
    current: TaskPause | None = None

    def inspect(value: object) -> dict[str, object] | None:
        nonlocal current
        pause = TaskPause.from_value(value)
        if pause is None or not pause.grant.is_active(now=now):
            return None
        current = pause
        return pause.to_dict()

    try:
        ctx.state_update(_STATE_KEY, inspect)
    except SessionPersistenceError as exc:
        logger.warning("Task-pause status read failed: %s", exc)
        if report_unavailable:
            return "⚠️ Task enforcement pause status unavailable; normal enforcement remains active. Resolve the session-state error and retry."
        return None
    if current is None:
        return None

    scope = current.grant.status_label(
        count_unit="logical Stop",
        now=now,
    )
    reason = f"\nReason: {current.reason}" if current.reason else ""
    marker = f"AUTORUN_TASK_RECOVERY({current.generation})"
    return (
        f"⏸️ Task enforcement pause: active ({scope}){reason}\n"
        "AI recovery: when the requested discussion is satisfied, return this "
        f"exact marker on its own line:\n{marker}"
    )


__all__ = [
    "TaskPause",
    "TaskPauseIdentityError",
    "activate_task_pause",
    "resume_task_pause",
    "task_enforcement_is_paused",
    "task_pause_allows_stop",
    "task_pause_guidance",
    "task_pause_status",
]
