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


@dataclass(frozen=True)
class ScopedAllow:
    """Composable capability for temporary permission grants.

    Immutable — consume() returns a new instance. JSON-serializable via
    to_dict()/from_dict(). Backwards-compatible with legacy entries
    (dicts without temporal fields are treated as permanent).
    """
    pattern: str
    pattern_type: str = "literal"
    suggestion: str = ""
    granted_at: float = 0.0
    ttl_seconds: float | None = None
    remaining_uses: int | None = None

    def is_valid(self) -> bool:
        """Check if this allow is still active (not expired/exhausted)."""
        if self.ttl_seconds is not None and self.granted_at > 0:
            if (time.time() - self.granted_at) > self.ttl_seconds:
                return False
        if self.remaining_uses is not None:
            if self.remaining_uses <= 0:
                return False
        return True

    def consume(self) -> ScopedAllow:
        """Return new ScopedAllow with one use consumed (immutable)."""
        if self.remaining_uses is None:
            return self
        return dataclasses.replace(self, remaining_uses=self.remaining_uses - 1)

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
