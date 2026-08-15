"""Composable scoped permission grants for autorun command blocking.

Provides ScopedAllow (immutable data structure) and parse_scope_args() for
temporary permission grants with use counts, time-based TTLs, or permanent mode.

Used by: /ar:ok, /ar:globalok command handlers in plugins.py.
"""

from __future__ import annotations

import dataclasses
import math
import re
import time
from dataclasses import dataclass
from collections.abc import Sequence

from .config import CONFIG, SCOPED_ALLOW_DEFAULT_GRACE_SECONDS

_PERMANENT_KEYWORDS = frozenset({"permanent", "perm", "p"})

# Matches duration strings like "5m", "1h", "30s", "2d", "1d12h", "2h30m"
_DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def looks_like_duration(s: str) -> bool:
    """Return whether ``s`` has duration shape (``5m``, ``2d``, ``0h``).

    Shape only: a zero-valued duration still looks like one, which is how the
    parsers tell "user typed a duration that is invalid" (reject loudly)
    apart from "this token is free text" (leave it alone).
    """
    s = s.strip().lower()
    if not s or s.isdigit():
        return False
    m = _DURATION_RE.match(s)
    return bool(m and any(m.groups()))


def parse_duration(s: str) -> float | None:
    """Parse a duration string into seconds. Returns None if not a valid duration.

    Supported formats: "30s", "5m", "1h", "2d", "2h30m", "1d12h", "1d2h30m15s"
    """
    s = s.strip().lower()
    if not s or s.isdigit():
        return None
    m = _DURATION_RE.match(s)
    if not m or not any(m.groups()):
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    minutes = int(m.group(3) or 0)
    seconds = int(m.group(4) or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return float(total) if total > 0 else None


def parse_scope_args(desc: str | None) -> tuple[float | None, int | None, bool]:
    """Parse trailing scope args from _parse_args() desc.

    Returns (ttl_seconds, remaining_uses, explicit_permanent).

    Args:
        desc: Trailing text after pattern from _parse_args(),
              e.g. "3", "5m", "permanent", "3 5m", None

    Rules:
    - Bare integer: count (remaining_uses); must be > 0 (a 0-use allow is
      never active, so it is rejected with ValueError instead of granted)
    - Duration string (5m, 1h, 30s, 2h30m, 2d): ttl_seconds
    - Integer + duration: both (whichever expires first)
    - "permanent" / "perm" / "p": explicit no-limit -> returns (None, None, True)
    - None or empty: returns (None, None, False) -- caller applies 1-use default
    """
    if not desc or not desc.strip():
        return (None, None, False)

    parts = desc.strip().split()
    ttl: float | None = None
    uses: int | None = None
    permanent = False

    for part in parts:
        low = part.lower()
        if low in _PERMANENT_KEYWORDS:
            permanent = True
        elif low.isdigit():
            uses = int(low)
            if uses <= 0:
                raise ValueError(
                    f"count must be greater than 0, got {part!r}; pass a "
                    "positive count, a duration such as 5m or 2d, or perm"
                )
        elif looks_like_duration(low):
            parsed_ttl = parse_duration(low)
            if parsed_ttl is None:
                raise ValueError(
                    f"duration must be greater than 0, got {part!r}; pass a "
                    "duration such as 5m or 2d, a positive count, or perm"
                )
            ttl = parsed_ttl
        # Any other token is free text the caller owns (pattern words).

    if permanent and (ttl is not None or uses is not None):
        # The strict sibling parser (parse_scope_tokens) rejects this too;
        # silently letting perm win would widen an explicit 5m into a
        # session-permanent grant.
        raise ValueError(
            "perm cannot be combined with a count or duration; pass perm "
            "alone for the rest of the session, or a count/duration without it"
        )
    if permanent:
        return (None, None, True)  # Explicit permanent — no limits
    return (ttl, uses, False)


@dataclass(frozen=True, slots=True)
class ScopeSpec:
    """Validated count/time scope without a pattern or policy decision."""

    ttl_seconds: float | None = None
    remaining_uses: int | None = None

    def __post_init__(self) -> None:
        if self.ttl_seconds is not None and (
            isinstance(self.ttl_seconds, bool) or not isinstance(self.ttl_seconds, (int, float)) or not math.isfinite(self.ttl_seconds) or self.ttl_seconds <= 0
        ):
            raise ValueError(f"ttl_seconds must be a finite number greater than 0, got {self.ttl_seconds!r}; pass positive seconds or None")
        if self.remaining_uses is not None and (isinstance(self.remaining_uses, bool) or not isinstance(self.remaining_uses, int) or self.remaining_uses <= 0):
            raise ValueError(f"remaining_uses must be an integer greater than 0, got {self.remaining_uses!r}; pass a positive count or None")

    @property
    def permanent(self) -> bool:
        return self.ttl_seconds is None and self.remaining_uses is None


def parse_scope_tokens(
    tokens: Sequence[str],
    *,
    default_scope: ScopeSpec,
    count_unit: str,
) -> ScopeSpec:
    """Parse explicit count/duration tokens; reject ambiguous combinations."""
    if not tokens:
        return default_scope

    ttl_seconds: float | None = None
    remaining_uses: int | None = None
    permanent = False
    for raw in tokens:
        token = raw.strip().lower()
        if token in _PERMANENT_KEYWORDS:
            if permanent or ttl_seconds is not None or remaining_uses is not None:
                raise ValueError("perm cannot be combined with a count or duration; pass perm alone to keep the scope active until resumed")
            permanent = True
            continue

        if token.isdecimal():
            if permanent or remaining_uses is not None:
                raise ValueError(f"{count_unit} count may be provided once; got {raw!r}; pass one positive count")
            count = int(token)
            if count <= 0:
                raise ValueError(f"{count_unit} count must be greater than 0, got {count}; pass a positive count or omit it for the configured default")
            remaining_uses = count
            continue

        duration = parse_duration(token)
        if duration is None:
            raise ValueError(
                f"scope token must be a positive {count_unit} count, duration "
                f"(for example 5m), or perm; got {raw!r}; pass a supported "
                "scope token or start a free-form reason with a word"
            )
        if permanent:
            raise ValueError("perm cannot be combined with a count or duration; pass perm alone to keep the scope active until resumed")
        if ttl_seconds is not None:
            raise ValueError(f"duration may be provided once; got {raw!r}; pass one duration, optionally with one count")
        ttl_seconds = duration

    return (
        ScopeSpec()
        if permanent
        else ScopeSpec(
            ttl_seconds=ttl_seconds,
            remaining_uses=remaining_uses,
        )
    )


def fingerprint_call(session_id: str, tool_name: str, cmd: str) -> str:
    """Stable 16-char fingerprint for parallel-hook deduplication.

    Matches the format in plugins.check_blocked_commands (plugins.py:608-610)
    so cache-override grace is byte-identical to /ar:ok grace windows.
    """
    import hashlib

    return hashlib.md5(f"{session_id}:{tool_name}:{cmd}".encode()).hexdigest()[:16]


# Seconds after last consumption that a count=0 allow still passes is_valid().
#
# Root cause: autorun runs twice per Bash command — once via the plugin's own
# PreToolUse hook, and once via `rtk hook claude` (settings.json PreToolUse hook),
# which internally spawns the autorun subprocess. Both invocations connect to
# the same daemon. The session_manager re-reads from disk inside every lock
# acquisition, so the second invocation may read remaining_uses=0 after the
# first has already written the consumed state.
#
# The race window is the time between first hook's write and second hook's read.
# Both Python hooks start within ~50ms of each other; their state reads happen
# within ~200ms of start. With Python startup jitter, the window is 0–200ms.
#
# 1.0 s is safely above the race window while safely below the minimum time
# for a genuine second command (tool execution + Claude response ≥ 3 s):
#   - git push network call: ≥ 1 s even on fast failure
#   - Claude processes response: ≥ 1 s
#   - Total before second tool call: ≥ 2 s
#
# The last_call_id fingerprint (hash of session_id:tool_name:cmd) further
# restricts the grace to parallel invocations of the exact same call in the
# same session, preventing global allows from bleeding into concurrent sessions.
_PARALLEL_GRACE_SECONDS: float = SCOPED_ALLOW_DEFAULT_GRACE_SECONDS


def _configured_parallel_grace_seconds() -> float:
    value = CONFIG.get(
        "scoped_allow_default_grace_seconds",
        SCOPED_ALLOW_DEFAULT_GRACE_SECONDS,
    )
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return SCOPED_ALLOW_DEFAULT_GRACE_SECONDS
    if not math.isfinite(parsed) or parsed <= 0:
        return SCOPED_ALLOW_DEFAULT_GRACE_SECONDS
    return parsed


@dataclass(frozen=True, slots=True)
class GrantClaim:
    """Result of atomically claiming one logical use of a scoped grant."""

    allowed: bool
    replay: bool
    grant: ScopedGrant


@dataclass(frozen=True, slots=True, kw_only=True)
class ScopedGrant:
    """Pattern-neutral immutable time/count grant."""

    granted_at: float = 0.0
    ttl_seconds: float | None = None
    remaining_uses: int | None = None
    consumed_at: float = 0.0
    last_call_id: str = ""
    grace_seconds: float | None = None

    def _time_is_active(self, timestamp: float) -> bool:
        if self.granted_at > 0 and timestamp < self.granted_at:
            return False
        return not (self.ttl_seconds is not None and self.granted_at > 0 and (timestamp - self.granted_at) >= self.ttl_seconds)

    def is_active(self, *, now: float | None = None) -> bool:
        """Return whether time/count scope remains, excluding replay grace."""
        timestamp = time.time() if now is None else now
        if not self._time_is_active(timestamp):
            return False
        return self.remaining_uses is None or self.remaining_uses > 0

    def claim_once(
        self,
        call_id: str,
        *,
        now: float | None = None,
    ) -> GrantClaim:
        """Claim one logical call; matching concurrent retries do not decrement."""
        if not call_id:
            return GrantClaim(False, False, self)
        timestamp = time.time() if now is None else now
        if not self._time_is_active(timestamp):
            return GrantClaim(False, False, self)

        grace_seconds = self.grace_seconds if self.grace_seconds is not None else _configured_parallel_grace_seconds()
        elapsed = timestamp - self.consumed_at
        if self.last_call_id == call_id and self.consumed_at > 0 and 0 <= elapsed < grace_seconds:
            return GrantClaim(True, True, self)

        if self.remaining_uses is None:
            return GrantClaim(True, False, self)
        if self.remaining_uses <= 0:
            return GrantClaim(False, False, self)
        claimed = dataclasses.replace(
            self,
            remaining_uses=self.remaining_uses - 1,
            consumed_at=timestamp,
            last_call_id=call_id,
        )
        return GrantClaim(True, False, claimed)

    def _scope_dict(self) -> dict:
        data: dict = {}
        if self.granted_at > 0:
            data["granted_at"] = self.granted_at
        if self.ttl_seconds is not None:
            data["ttl_seconds"] = self.ttl_seconds
        if self.remaining_uses is not None:
            data["remaining_uses"] = self.remaining_uses
        if self.consumed_at > 0:
            data["consumed_at"] = self.consumed_at
        if self.last_call_id:
            data["last_call_id"] = self.last_call_id
        if self.grace_seconds is not None:
            data["grace_seconds"] = self.grace_seconds
        return data

    def to_dict(self) -> dict:
        return self._scope_dict()

    @classmethod
    def from_dict(cls, data: dict) -> ScopedGrant:
        return cls(
            granted_at=data.get("granted_at", 0.0),
            ttl_seconds=data.get("ttl_seconds"),
            remaining_uses=data.get("remaining_uses"),
            consumed_at=data.get("consumed_at", 0.0),
            last_call_id=data.get("last_call_id", ""),
            grace_seconds=data.get("grace_seconds"),
        )

    def status_label(
        self,
        *,
        count_unit: str,
        now: float | None = None,
    ) -> str:
        parts: list[str] = []
        if self.remaining_uses is not None:
            count = self.remaining_uses
            unit = count_unit if count == 1 else f"{count_unit}s"
            parts.append(f"{count} {unit}")
        if self.ttl_seconds is not None and self.granted_at > 0:
            timestamp = time.time() if now is None else now
            remaining = max(
                0,
                math.ceil(self.ttl_seconds - (timestamp - self.granted_at)),
            )
            # Render in the units the grant grammar accepts (d, h, m, s):
            # a 2d grant reads back as 2d0h0m, not 2880m0s. Below an hour the
            # historical m/s form is kept; at an hour and above seconds are
            # noise and are dropped.
            days, rest = divmod(int(remaining), 86400)
            hours, rest = divmod(rest, 3600)
            minutes, seconds = divmod(rest, 60)
            if days:
                parts.append(f"{days}d{hours}h{minutes}m")
            elif hours:
                parts.append(f"{hours}h{minutes}m")
            elif remaining >= 60:
                parts.append(f"{minutes}m{seconds}s")
            else:
                parts.append(f"{int(remaining)}s")
        if not parts:
            return "permanent"
        return ", ".join(parts) + " remaining"


@dataclass(frozen=True, slots=True)
class ScopedAllow(ScopedGrant):
    """Composable capability for temporary permission grants.

    Immutable — consume() returns a new instance. JSON-serializable via
    to_dict()/from_dict(). Backwards-compatible with legacy entries
    (dicts without temporal fields are treated as permanent).

    Parallel-hook safety: when remaining_uses reaches 0, consumed_at is stamped
    and last_call_id is set. is_valid(call_id) returns True for
    _PARALLEL_GRACE_SECONDS if the call_id matches, allowing the second autorun
    invocation (RTK-spawned subprocess) for the same Bash tool call to pass
    TIER 1 instead of falling through to TIER 2 blocks.

    Session isolation: last_call_id includes session_id in its hash, so a
    global allow consumed by session A will not grant grace to session B even
    if session B runs the same command within the grace window.
    """

    pattern: str = ""
    pattern_type: str = "literal"
    suggestion: str = ""

    def is_valid(self, call_id: str = "", *, now: float | None = None) -> bool:
        """Check if this allow is still active (not expired/exhausted).

        Args:
            call_id: Fingerprint of the current hook invocation
                     (hash of session_id:tool_name:cmd, from check_integration).
                     When provided, the grace period requires both time-within-window
                     AND fingerprint-match, preventing global allows from granting
                     grace to concurrent different sessions.

        Grace period: when remaining_uses hits 0, stays valid for
        _PARALLEL_GRACE_SECONDS if the call_id matches the consuming call.
        This lets the second autorun invocation (RTK-spawned) for the same
        Bash tool call pass TIER 1 instead of falling to TIER 2 blocks.
        """
        timestamp = time.time() if now is None else now
        if not self._time_is_active(timestamp):
            return False
        if self.remaining_uses is not None:
            if self.remaining_uses <= 0:
                grace_seconds = self.grace_seconds if self.grace_seconds is not None else _configured_parallel_grace_seconds()
                elapsed = timestamp - self.consumed_at
                if self.consumed_at > 0 and 0 <= elapsed < grace_seconds:
                    # If we have a stored fingerprint and a caller fingerprint, they must match.
                    # This prevents a global allow's grace from being claimed by a different session.
                    # Falls back to time-only if either fingerprint is absent (legacy state).
                    if self.last_call_id and call_id and self.last_call_id != call_id:
                        return False
                    return True
                return False
        return True

    def consume(
        self,
        call_id: str = "",
        *,
        now: float | None = None,
    ) -> ScopedAllow:
        """Return new ScopedAllow with one use consumed (immutable).

        Args:
            call_id: Fingerprint of the current hook invocation. Stored as
                     last_call_id so subsequent parallel invocations with the
                     same fingerprint can use the grace period.

        Sets consumed_at and last_call_id when remaining_uses hits 0, enabling
        the grace period. Refreshes both if already 0 (subsequent parallel
        invocations extend the grace window).
        """
        if self.remaining_uses is None:
            return self
        new_count = self.remaining_uses - 1
        if new_count <= 0:
            return dataclasses.replace(
                self,
                remaining_uses=max(0, new_count),
                consumed_at=time.time() if now is None else now,
                last_call_id=call_id,
            )
        return dataclasses.replace(self, remaining_uses=new_count)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict (for session_manager storage)."""
        d: dict = {
            "pattern": self.pattern,
            "pattern_type": self.pattern_type,
            **self._scope_dict(),
        }
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ScopedAllow:
        """Deserialize from JSON dict (backwards-compatible with legacy entries)."""
        return cls(
            pattern=d["pattern"],
            pattern_type=d.get("pattern_type", "literal"),
            suggestion=d.get("suggestion", ""),
            granted_at=d.get("granted_at", 0.0),
            ttl_seconds=d.get("ttl_seconds"),
            remaining_uses=d.get("remaining_uses"),
            consumed_at=d.get("consumed_at", 0.0),
            last_call_id=d.get("last_call_id", ""),
            grace_seconds=d.get("grace_seconds"),
        )

    def status_label(self, *, now: float | None = None) -> str:
        """Human-readable remaining scope for /ar:blocks display."""
        return ScopedGrant.status_label(self, count_unit="use", now=now)
