"""Composable scoped permission grants for autorun command blocking.

Provides ScopedAllow (immutable data structure) and parse_scope_args() for
temporary permission grants with use counts, time-based TTLs, or permanent mode.

Used by: /ar:ok, /ar:globalok command handlers in plugins.py.
"""
from __future__ import annotations

import dataclasses
import re
import time
from dataclasses import dataclass


_PERMANENT_KEYWORDS = frozenset({"permanent", "perm", "p"})

# Matches duration strings like "5m", "1h", "30s", "2h30m", "1h15m30s"
_DURATION_RE = re.compile(
    r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$"
)


def parse_duration(s: str) -> float | None:
    """Parse a duration string into seconds. Returns None if not a valid duration.

    Supported formats: "30s", "5m", "1h", "2h30m", "1h15m30s"
    """
    s = s.strip().lower()
    if not s or s.isdigit():
        return None
    m = _DURATION_RE.match(s)
    if not m or not any(m.groups()):
        return None
    hours = int(m.group(1) or 0)
    minutes = int(m.group(2) or 0)
    seconds = int(m.group(3) or 0)
    total = hours * 3600 + minutes * 60 + seconds
    return float(total) if total > 0 else None


def parse_scope_args(desc: str | None) -> tuple[float | None, int | None, bool]:
    """Parse trailing scope args from _parse_args() desc.

    Returns (ttl_seconds, remaining_uses, explicit_permanent).

    Args:
        desc: Trailing text after pattern from _parse_args(),
              e.g. "3", "5m", "permanent", "3 5m", None

    Rules:
    - Bare integer: count (remaining_uses)
    - Duration string (5m, 1h, 30s, 2h30m): ttl_seconds
    - Integer + duration: both (whichever expires first)
    - "permanent" / "perm" / "p": explicit no-limit -> returns (None, None, True)
    - None or empty: returns (None, None, False) -- caller applies 1-use default
    """
    if not desc or not desc.strip():
        return (None, None, False)

    parts = desc.strip().split()
    ttl: float | None = None
    uses: int | None = None

    for part in parts:
        low = part.lower()
        if low in _PERMANENT_KEYWORDS:
            return (None, None, True)  # Explicit permanent — no limits
        if low.isdigit():
            uses = int(low)
        else:
            parsed_ttl = parse_duration(low)
            if parsed_ttl is not None:
                ttl = parsed_ttl

    return (ttl, uses, False)


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
_PARALLEL_GRACE_SECONDS: float = 1.0


@dataclass(frozen=True)
class ScopedAllow:
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
    pattern: str
    pattern_type: str = "literal"
    suggestion: str = ""
    granted_at: float = 0.0
    ttl_seconds: float | None = None
    remaining_uses: int | None = None
    consumed_at: float = 0.0     # Timestamp of last consume(); enables grace period
    last_call_id: str = ""       # Fingerprint of the call that consumed this allow

    def is_valid(self, call_id: str = "") -> bool:
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
        if self.ttl_seconds is not None and self.granted_at > 0:
            if (time.time() - self.granted_at) > self.ttl_seconds:
                return False
        if self.remaining_uses is not None:
            if self.remaining_uses <= 0:
                if self.consumed_at > 0 and (time.time() - self.consumed_at) < _PARALLEL_GRACE_SECONDS:
                    # If we have a stored fingerprint and a caller fingerprint, they must match.
                    # This prevents a global allow's grace from being claimed by a different session.
                    # Falls back to time-only if either fingerprint is absent (legacy state).
                    if self.last_call_id and call_id and self.last_call_id != call_id:
                        return False
                    return True
                return False
        return True

    def consume(self, call_id: str = "") -> ScopedAllow:
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
                consumed_at=time.time(),
                last_call_id=call_id,
            )
        return dataclasses.replace(self, remaining_uses=new_count)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict (for session_manager storage)."""
        d: dict = {"pattern": self.pattern, "pattern_type": self.pattern_type}
        if self.suggestion:
            d["suggestion"] = self.suggestion
        if self.granted_at > 0:
            d["granted_at"] = self.granted_at
        if self.ttl_seconds is not None:
            d["ttl_seconds"] = self.ttl_seconds
        if self.remaining_uses is not None:
            d["remaining_uses"] = self.remaining_uses
        if self.consumed_at > 0:
            d["consumed_at"] = self.consumed_at
        if self.last_call_id:
            d["last_call_id"] = self.last_call_id
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
        )

    def status_label(self) -> str:
        """Human-readable remaining scope for /ar:blocks display."""
        parts: list[str] = []
        if self.remaining_uses is not None:
            n = self.remaining_uses
            parts.append(f"{n} use{'s' if n != 1 else ''}")
        if self.ttl_seconds is not None and self.granted_at > 0:
            remaining = max(0, self.ttl_seconds - (time.time() - self.granted_at))
            if remaining >= 60:
                parts.append(f"{int(remaining // 60)}m{int(remaining % 60)}s")
            else:
                parts.append(f"{int(remaining)}s")
        if not parts:
            return "permanent"
        return ", ".join(parts) + " remaining"
