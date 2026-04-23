"""/ar:cache — cache-pressure / cache-miss protection gate.

Plan: /Users/athundt/.claude/plans/make-a-plan-to-sunny-sparkle.md

Single-file feature per §8 of the plan. Exports:
    parse_quantity, PERMANENT           — token/percent parser
    FeatureToggle                        — DRY on/off (v1 local; extracted in follow-up commit)
    CacheThreshold, CacheGuard           — decision logic
    grant_override                       — write a ScopedAllow to session_state
    persist_statusline_snapshot          — CLI tap (`autorun --cache-snapshot`)
    HookDecision                         — allow / block result
    _read_jsonl_tail, _read_usage_claude — JSONL bounded-tail reader

The feature is OFF by default. Design principle: "stay out of the way" — no
statusline install, no new sync primitive, no new state file.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import IO, Literal, Optional

from .config import detect_cli_type
from .scoped_allow import ScopedAllow, _PARALLEL_GRACE_SECONDS, _PERMANENT_KEYWORDS
from .session_manager import session_state


# ── sentinels ─────────────────────────────────────────────────────────

class _Permanent:
    """Sentinel meaning 'no limit / never trip'. Used for token quantities."""
    _instance: "_Permanent | None" = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "PERMANENT"


PERMANENT = _Permanent()

# Single source of truth for permanent keywords, reused from scoped_allow so the
# user's mental model of `perm | permanent | p` is identical across /ar:ok, /ar:no,
# /ar:globalok, and /ar:cache. Do NOT extend this set — if a future dialect needs
# extra aliases, add them in scoped_allow so every feature picks them up at once.
_PERMANENT_WORDS = _PERMANENT_KEYWORDS


# ── parse_quantity ────────────────────────────────────────────────────

_TOKEN_RE = re.compile(
    r"^\s*(?P<neg>-)?(?P<num>\d+(?:\.\d+)?|\.\d+)\s*(?P<suf>[kKmM]?)\s*$"
)
_PERCENT_RE = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?|\.\d+)\s*%\s*$"
)


def parse_quantity(s: str, *, expect: Literal["tokens", "percent"]):
    """Parse a quantity string.

    ``expect="tokens"`` — returns ``int`` or ``PERMANENT``. Accepts:
      - bare integers with commas/underscores: ``50000``, ``50,000``, ``50_000``
      - ``k`` / ``K`` suffix (×1 000):        ``50k``, ``200K``
      - ``m`` / ``M`` suffix (×1 000 000):    ``.5M``, ``1.5M``, ``2m``
      - permanent sentinel words:             ``perm`` / ``permanent`` / ``p``
        (canonical set, reused from scoped_allow — same grammar as /ar:ok).

    ``expect="percent"`` — returns ``float`` in ``(0, 1]``. Accepts:
      - percent syntax:       ``85%``, ``92%``, ``100%``
      - bare decimal ≤ 1:     ``0.85``, ``0.5``

    Raises ``ValueError`` on malformed input (same style as ``/ar:ok``'s parser).
    """
    if not isinstance(s, str):
        raise ValueError(f"parse_quantity requires str, got {type(s).__name__}")
    raw = s.strip()
    if not raw:
        raise ValueError("parse_quantity: empty input")

    if expect == "tokens":
        if raw.lower() in _PERMANENT_WORDS:
            return PERMANENT
        cleaned = raw.replace(",", "").replace("_", "")
        m = _TOKEN_RE.match(cleaned)
        if not m or m.group("neg"):
            raise ValueError(f"parse_quantity: cannot parse tokens {s!r}")
        n = float(m.group("num"))
        suf = m.group("suf").lower()
        if suf == "k":
            n *= 1_000
        elif suf == "m":
            n *= 1_000_000
        return int(n)

    if expect == "percent":
        m = _PERCENT_RE.match(raw)
        if m:
            v = float(m.group("num")) / 100.0
        else:
            # bare decimal path — must look like a number and be in (0, 1]
            try:
                v = float(raw)
            except ValueError as e:
                raise ValueError(f"parse_quantity: cannot parse percent {s!r}") from e
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"parse_quantity: percent {s!r} out of [0, 1]")
        return v

    raise ValueError(f"parse_quantity: unknown family {expect!r}")


# ── FeatureToggle ─────────────────────────────────────────────────────

_FT_KEY_FMT    = "features/{name}/enabled"
_FT_EXP_FMT    = "features/{name}/expires_at"
_FT_PRIOR_FMT  = "features/{name}/prior_enabled"   # what to restore to on TTL expiry

# Well-known session id used for GLOBAL scope. Shares the same JSON store under
# the existing filelock (same multiprocess semantics as /ar:globalok). Chosen to
# sort before real session UUIDs for easy scanning in debug dumps.
_GLOBAL_SESSION_ID = "__global__"


@dataclass
class FeatureToggle:
    """Per-session enable flag with optional TTL, backed by session_state.

    v1 lives here; a follow-up commit migrates `PlanExportConfig` and
    `TaskLifecycleConfig` to call this (§8.3 of the plan).
    """
    name: str
    session_id: str
    default: bool = False

    def _key(self) -> str:
        return _FT_KEY_FMT.format(name=self.name)

    def _exp_key(self) -> str:
        return _FT_EXP_FMT.format(name=self.name)

    def _prior_key(self) -> str:
        return _FT_PRIOR_FMT.format(name=self.name)

    def _resolved(self, st) -> bool:
        """Resolve effective enabled-state without mutating. Order:
            session override (features/cache/enabled)
            → global default (__global__/features/cache/enabled)
            → self.default
        """
        val = st.get(self._key())
        if val is not None:
            return bool(val)
        # Global fallback — cheap: same JSON store, same lock (reentrant).
        with session_state(_GLOBAL_SESSION_ID) as gst:
            gval = gst.get(self._key())
        if gval is not None:
            return bool(gval)
        return self.default

    def is_enabled(self) -> bool:
        """Resolve with TTL handling.

        If a temporary window is in effect and expired, the toggle reverts to
        the **prior state** captured at enable/disable time (not to the default
        — so `/ar:cache off 5m` on an already-enabled gate resumes it after 5m
        exactly as the user expects). Fallback chain then mirrors `_resolved`.
        """
        with session_state(self.session_id) as st:
            exp = st.get(self._exp_key())
            if exp is not None and time.time() >= exp:
                prior = st.get(self._prior_key())
                # Clean up: TTL fields and the now-stale value.
                for k in (self._key(), self._exp_key(), self._prior_key()):
                    if k in st:
                        del st[k]
                # Restore prior state explicitly when we have one; otherwise
                # re-resolve via the normal fallback chain.
                if prior is not None:
                    return bool(prior)
                return self._resolved(st)
            return self._resolved(st)

    def _set(self, value: bool, duration_seconds: float | None) -> None:
        with session_state(self.session_id) as st:
            if duration_seconds is not None:
                # Record prior state so is_enabled() can restore it after TTL.
                # The prior is the CURRENT resolved value (not the raw key) —
                # that way an unset key that fell back to global/default still
                # restores to the right thing on expiry.
                prior_value = self._resolved(st)
                st[self._prior_key()] = prior_value
                st[self._exp_key()] = time.time() + duration_seconds
            else:
                # Permanent toggle — clear any pending TTL/prior bookkeeping.
                for k in (self._exp_key(), self._prior_key()):
                    if k in st:
                        del st[k]
            st[self._key()] = value

    def enable(self, duration_seconds: float | None = None) -> None:
        self._set(True, duration_seconds)

    def disable(self, duration_seconds: float | None = None) -> None:
        self._set(False, duration_seconds)


# ── CacheThreshold + UsageReading + HookDecision ─────────────────────

@dataclass(frozen=True)
class CacheThreshold:
    """Decision axes. ``None`` means the axis is not configured — skip it."""
    cache_hit_ratio_min:   float | None = None    # trip when ratio < this
    cache_read_tokens_min: int   | None = None    # trip when cache_read_tokens < this
    cache_age_max_seconds: float | None = None    # trip when age > this
    compaction_used_max:   float | None = None    # trip when total_in / window > this
    rate_limit_5h_max:     float | None = None    # 0.0..1.0
    rate_limit_7d_max:     float | None = None


@dataclass
class UsageReading:
    cli: str = "claude"
    total_input_tokens:     int   | None = None
    cache_read_tokens:      int   | None = None
    cache_creation_tokens:  int   | None = None
    cache_hit_ratio:        float | None = None
    cache_age_seconds:      float | None = None
    context_window_size:    int   | None = None
    compaction_proximity:   float | None = None
    rate_limit_5h:          float | None = None
    rate_limit_7d:          float | None = None
    observed_at:            float = 0.0


@dataclass
class HookDecision:
    _block: bool
    message: str = ""

    @classmethod
    def allow(cls) -> "HookDecision":
        return cls(_block=False)

    @classmethod
    def block(cls, message: str) -> "HookDecision":
        return cls(_block=True, message=message)

    def is_block(self) -> bool:
        return self._block

    def emit(self) -> int:
        """Exit-code convenience for callers that treat this as a CLI result."""
        return 2 if self._block else 0


# ── JSONL bounded-tail reader ────────────────────────────────────────

# Tail-scan window sizes tried in order. The first win short-circuits. A retry
# at 4× max handles the rare "last assistant entry is inside a big tool_result"
# case without paying O(file_size) on every PreToolUse.
_TAIL_DEFAULT_BYTES = 64 * 1024         # primary window — <10 ms on NVMe for a 50 MB file
_TAIL_RETRY_BYTES   = 256 * 1024        # fallback when primary window finds no assistant entry

# Entry-type discriminators for the two CLIs we actually support today. Kept
# minimal on purpose — broader sets risk matching unrelated entries that happen
# to carry a ``usage``-shaped blob. Add a new key here only when a real
# transcript exhibits it (don't speculate).
#   "assistant" — Claude Code JSONL type discriminator.
#   "model"     — Gemini content entries use role="model" in some schemas.
_ASSISTANT_TYPES  = frozenset({"assistant", "model"})
_TIMESTAMP_KEYS   = ("timestamp", "time", "created_at", "createdAt", "ts")


def _read_jsonl_tail(path: str, *, max_bytes: int, cli: str) -> "UsageReading | None":
    """Reverse-read up to ``max_bytes`` of the JSONL and find the latest
    assistant/model entry with a usage object. Returns ``None`` if the file is
    missing or no such entry appears in the tail window.

    Complexity: O(max_bytes). A single retry with ``_TAIL_RETRY_BYTES`` happens
    automatically when the primary window has no usable entry — keeps the
    worst case bounded at O(256 KB) while still handling sessions with large
    tool_result payloads. No unbounded file reads.
    """
    usage = _scan_once(path, start_from_end=max_bytes, cli=cli)
    if usage is not None:
        return usage
    if max_bytes < _TAIL_RETRY_BYTES:
        return _scan_once(path, start_from_end=_TAIL_RETRY_BYTES, cli=cli)
    return None


def _scan_once(path: str, *, start_from_end: int, cli: str) -> "UsageReading | None":
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size == 0:
        return None
    start = max(0, size - start_from_end)
    try:
        with open(path, "rb") as f:
            f.seek(start)
            blob = f.read()
    except OSError:
        return None
    text = blob.decode("utf-8", errors="replace")
    # If we started mid-line, drop the first partial line to avoid JSON errors.
    if start > 0:
        nl = text.find("\n")
        if nl == -1:
            return None
        text = text[nl + 1:]
    # Reverse walk by splitting on '\n' once (O(N)). Avoid reversed(list) if memory
    # matters — for <=256 KB this is negligible, and the list form is simpler.
    lines = text.split("\n")
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        entry_type = (obj.get("type") or obj.get("role") or "").lower()
        if entry_type not in _ASSISTANT_TYPES:
            continue
        usage = _extract_usage_dict(obj)
        if usage is None:
            continue
        return _usage_from_assistant(obj, usage, cli=cli)
    return None


def _extract_usage_dict(entry: dict) -> "dict | None":
    """Locate the per-message usage dict across known CLI schemas.

    Known shapes tried, in order:
      - Anthropic / Claude Code: entry['message']['usage']
      - Top-level usage:          entry['usage']
      - Gemini (LLMResponse):     entry['usageMetadata']  or  entry['response']['usageMetadata']
      - Nested variations:        entry['message']['usageMetadata'], entry['model']['usage']

    Returns the first non-empty dict found, or None. All callers handle None.
    """
    candidates = []
    msg = entry.get("message")
    if isinstance(msg, dict):
        candidates.append(msg.get("usage"))
        candidates.append(msg.get("usageMetadata"))
    candidates.append(entry.get("usage"))
    candidates.append(entry.get("usageMetadata"))
    resp = entry.get("response")
    if isinstance(resp, dict):
        candidates.append(resp.get("usageMetadata"))
        candidates.append(resp.get("usage"))
    model = entry.get("model")
    if isinstance(model, dict):
        candidates.append(model.get("usage"))
    for cand in candidates:
        if isinstance(cand, dict) and cand:
            return cand
    return None


def _usage_from_assistant(entry: dict, usage: dict, *, cli: str) -> UsageReading:
    """Normalise a per-message usage dict across known CLI shapes.

    Anthropic/Claude keys: input_tokens, output_tokens, cache_read_input_tokens,
                           cache_creation_input_tokens.
    Gemini keys:           promptTokenCount, candidatesTokenCount, totalTokenCount,
                           cachedContentTokenCount.
    Every field defaults to 0 when missing; absent fields yield ``None`` axes
    (the guard's default-skip behaviour then treats them as fail-open).
    """
    def _first_int(*keys: str) -> int:
        for k in keys:
            v = usage.get(k)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
        return 0

    input_tokens    = _first_int("input_tokens", "promptTokenCount", "prompt_tokens")
    output_tokens   = _first_int("output_tokens", "candidatesTokenCount", "completion_tokens")
    cache_read      = _first_int("cache_read_input_tokens", "cachedContentTokenCount",
                                 "cached_content_token_count", "cache_read")
    cache_creation  = _first_int("cache_creation_input_tokens", "cache_creation",
                                 "cache_creation_tokens")

    denom = input_tokens + cache_read + cache_creation
    ratio: float | None = (cache_read / denom) if denom > 0 else None

    # Timestamp extraction — try multiple keys, accept epoch seconds or ISO-8601.
    #
    # Clock-skew handling: a JSONL timestamp ahead of wall-clock (resume on a
    # machine with wrong system time, writer in a different TZ treated as
    # naive) would, under a naive `max(0.0, now-t)` clamp, silently report
    # age=0 — making the `cache_age_max_seconds` axis never trip. That is a
    # silent under-enforcement. Instead we leave `age` as None when the
    # timestamp is far in the future (beyond a small tolerance for minor NTP
    # drift). The age axis then fails-open (None), which is the documented
    # behaviour for unknown data per the module header.
    age: float | None = None
    _CLOCK_SKEW_TOLERANCE_S = 60.0  # accept ±60s as legitimate NTP / round-trip drift
    for k in _TIMESTAMP_KEYS:
        raw = entry.get(k)
        if raw is None:
            continue
        t = _parse_ts(raw)
        if t is None:
            continue
        delta = time.time() - t
        if delta >= 0:
            age = delta
        elif -delta <= _CLOCK_SKEW_TOLERANCE_S:
            age = 0.0  # small negative drift → treat as "just happened"
        else:
            age = None  # far-future timestamp → unknown / fail-open
        break

    # total_input_tokens tracks **input-only** tokens (input + cache_read +
    # cache_creation), matching the Claude statusline `used_percentage` formula
    # in https://code.claude.com/docs/en/statusline. Do NOT substitute
    # Gemini's `totalTokenCount` here — that value includes output tokens and
    # would inflate compaction_proximity. Gemini input-only sum is the same
    # `denom` expression above.
    #
    # cache_read_tokens / cache_creation_tokens are reported as ints (not
    # collapsed to None when zero) so `cache_read_tokens_min` still trips on a
    # legitimately cold cache (0 cache reads in a just-started session).
    return UsageReading(
        cli=cli,
        total_input_tokens=denom if denom > 0 else None,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        cache_hit_ratio=ratio,
        cache_age_seconds=age,
        observed_at=time.time(),
    )


def _parse_ts(raw) -> "float | None":
    """Accept epoch seconds (int/float) OR ISO-8601 strings. None on failure."""
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    if isinstance(raw, str) and raw:
        return _parse_iso(raw)
    return None


def _parse_iso(ts: str) -> float | None:
    # Minimal ISO-8601 → epoch parser; tolerant of ``Z`` suffix and milliseconds.
    try:
        from datetime import datetime, timezone
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


# ── CacheGuard ────────────────────────────────────────────────────────

_DEFAULT_JSONL_MAX_BYTES = 64 * 1024
_MEMO_TTL_SECONDS = 2.0
_GRANT_KEY = "cache/overrides"
_LAST_USAGE_KEY = "cache/last_usage"
_SNAPSHOT_KEY = "cache/statusline_snapshot"


@dataclass
class CacheGuard:
    session_id: str
    threshold: CacheThreshold = field(default_factory=CacheThreshold)

    @classmethod
    def from_session(cls, session_id: str | None = None) -> "CacheGuard":
        sid = session_id or _env_session_id() or "unknown"
        # Threshold may be persisted per session; fall back to defaults.
        th = _load_threshold(sid)
        return cls(session_id=sid, threshold=th)

    # ── main entry points ────────────────────────────────────────

    def on_pretooluse(self, stdin: dict) -> HookDecision:
        toggle = FeatureToggle("cache", session_id=self.session_id)
        if not toggle.is_enabled():
            return HookDecision.allow()
        usage = self._read_usage(stdin)
        trips = self._which_axes_trip(usage)
        if not trips:
            return HookDecision.allow()
        if self._consume_override(stdin):
            return HookDecision.allow()
        return HookDecision.block(self._render_block(usage, trips))

    # Explicit set of events that indicate the transcript/usage snapshot may
    # be stale. Kept as a literal whitelist — broader substring matching was
    # tempting but risks invalidating on user-named hooks that happen to
    # include "compact" in their name.
    _INVALIDATING_EVENTS = frozenset({
        "PreCompact",        # Claude Code: about to summarise
        "PostCompact",       # Claude Code: just summarised
        "SessionStart",      # Claude Code (matcher=compact, resume, clear); Gemini: session re-entry
        "PreCompress",       # Gemini CLI: advisory pre-compression
    })

    def on_compaction_event(self, event: str) -> HookDecision:
        """Invalidate the usage memo on any event that indicates staleness.

        Fires for Claude Code's ``PreCompact`` / ``PostCompact`` /
        ``SessionStart`` (any matcher — `compact` is the one we care about,
        others are a cheap extra invalidation), and Gemini CLI's
        ``PreCompress`` / ``SessionStart``. The cost is one ``del`` inside the
        existing filelock; erring toward extra invalidation is safe because the
        next PreToolUse simply re-reads the JSONL tail.
        """
        if (event or "") in self._INVALIDATING_EVENTS:
            with session_state(self.session_id) as st:
                for key in (_LAST_USAGE_KEY, _SNAPSHOT_KEY):
                    if key in st:
                        del st[key]
        return HookDecision.allow()

    # ── usage sources ────────────────────────────────────────────

    def _read_usage(self, stdin: dict) -> Optional[UsageReading]:
        """Resolve current usage via three tiers, coalescing session_state access.

        Lock budget: **one filelock acquire** in the common path (memo hit or
        snapshot hit). **Two acquires** when falling back to a JSONL scan
        (initial read + memoise write). The reentrant lock inside
        ``session_state`` makes the memoise path free on same-thread re-entry.
        """
        # Single lock — read BOTH memo and snapshot at once. session_state is
        # reentrant per-thread, so even if a caller wraps us we don't deadlock.
        with session_state(self.session_id) as st:
            memo = st.get(_LAST_USAGE_KEY)
            snap = st.get(_SNAPSHOT_KEY)

        if memo and (time.time() - memo.get("observed_at", 0)) < _MEMO_TTL_SECONDS:
            return _usage_from_memo(memo)

        if snap:
            usage = _usage_from_snapshot(snap)
            if usage is not None:
                _memoise(self.session_id, usage)
                return usage

        # Tier-3: JSONL bounded-tail reader (primary + one retry window).
        path = _transcript_path(stdin, self.session_id)
        if not path:
            return None
        cli = detect_cli_type(stdin) if isinstance(stdin, dict) else "claude"
        usage = _read_jsonl_tail(path, max_bytes=_DEFAULT_JSONL_MAX_BYTES, cli=cli)
        if usage is not None:
            _memoise(self.session_id, usage)
        return usage

    # ── decision axes ────────────────────────────────────────────

    def _which_axes_trip(self, usage: Optional[UsageReading]) -> list[str]:
        if usage is None:
            return []
        th = self.threshold
        trips: list[str] = []
        if th.cache_hit_ratio_min is not None and usage.cache_hit_ratio is not None:
            if usage.cache_hit_ratio < th.cache_hit_ratio_min:
                trips.append(f"ratio {usage.cache_hit_ratio:.2f} < {th.cache_hit_ratio_min:.2f}")
        if th.cache_read_tokens_min is not None and usage.cache_read_tokens is not None:
            if usage.cache_read_tokens < th.cache_read_tokens_min:
                trips.append(f"cache_read {usage.cache_read_tokens} < {th.cache_read_tokens_min}")
        if th.cache_age_max_seconds is not None and usage.cache_age_seconds is not None:
            if usage.cache_age_seconds > th.cache_age_max_seconds:
                trips.append(f"age {int(usage.cache_age_seconds)}s > {int(th.cache_age_max_seconds)}s")
        if th.compaction_used_max is not None and usage.compaction_proximity is not None:
            if usage.compaction_proximity > th.compaction_used_max:
                trips.append(f"window {usage.compaction_proximity:.2f} > {th.compaction_used_max:.2f}")
        if th.rate_limit_5h_max is not None and usage.rate_limit_5h is not None:
            if usage.rate_limit_5h > th.rate_limit_5h_max:
                trips.append(f"rate-limit 5h {usage.rate_limit_5h:.2%} > {th.rate_limit_5h_max:.2%}")
        if th.rate_limit_7d_max is not None and usage.rate_limit_7d is not None:
            if usage.rate_limit_7d > th.rate_limit_7d_max:
                trips.append(f"rate-limit 7d {usage.rate_limit_7d:.2%} > {th.rate_limit_7d_max:.2%}")
        return trips

    # ── overrides (reuses ScopedAllow verbatim) ──────────────────

    def _consume_override(self, stdin: dict) -> bool:
        call_id = _fingerprint_call(stdin, self.session_id)
        with session_state(self.session_id) as st:
            grants_raw = list(st.get(_GRANT_KEY, []))
            updated: list[dict] = []
            consumed_any = False
            for gd in grants_raw:
                try:
                    g = ScopedAllow.from_dict(gd)
                except Exception:
                    continue
                if not g.is_valid(call_id):
                    continue
                if not consumed_any:
                    new_g = g.consume(call_id)
                    consumed_any = True
                    if new_g.is_valid(call_id):
                        updated.append(new_g.to_dict())
                else:
                    updated.append(g.to_dict())
            if consumed_any:
                st[_GRANT_KEY] = updated
        return consumed_any

    # ── UX ──────────────────────────────────────────────────────

    def _render_block(self, usage: Optional[UsageReading], trips: list[str]) -> str:
        rate_limit_tripped = any(t.startswith("rate-limit") for t in trips)
        if rate_limit_tripped:
            heading = "ar:cache BLOCKED — rate limit"
        else:
            heading = "ar:cache BLOCKED — prompt cache is cold"
        axes = "\n".join(f"  • {t}" for t in trips)
        hint = (
            "\nTo proceed anyway:"
            "\n  /ar:cache ok 5m          # allow for 5 minutes"
            "\n  /ar:cache ok 3           # allow next 3 tool uses"
            "\n  /ar:cache ok perm        # allow until axes clear or session end"
            "\n\nTo change thresholds:"
            "\n  /ar:cache set ratio 0.30"
            "\n  /ar:cache set read 25k"
            "\n  /ar:cache set age 10m"
            "\n\nTo disable the gate entirely:"
            "\n  /ar:cache off             # this session"
            "\n  /ar:cache off 1h          # next hour only"
            "\n  /ar:cache global off      # all sessions"
            "\n\nTo re-arm immediately (cancel outstanding ok grants):"
            "\n  /ar:cache no"
        )
        return f"{heading}\n\nTripped axes:\n{axes}\n{hint}"


# ── grants + thresholds helpers (module-level, for tests + CLI) ──────

def grant_override(
    session_id: str,
    *,
    ttl_seconds: float | None = None,
    uses: int | None = None,
    permanent: bool = False,
) -> None:
    """Persist a ScopedAllow-shaped grant under cache/overrides for this session."""
    sa = ScopedAllow(
        pattern="__cache__",
        pattern_type="literal",
        granted_at=time.time() if ttl_seconds else 0.0,
        ttl_seconds=ttl_seconds,
        remaining_uses=None if permanent else uses,
    )
    with session_state(session_id) as st:
        existing = list(st.get(_GRANT_KEY, []))
        existing.append(sa.to_dict())
        st[_GRANT_KEY] = existing


def _purge_expired_overrides(session_id: str) -> None:
    """Drop cache/overrides entries whose ScopedAllow.is_valid() is False.

    Lazy GC — mirrors ``plugins.cleanup_expired_allows``. Runs on SessionStart
    to prevent accumulation of dead grants in session_state across many
    resumed sessions. Silent fail-open on any unexpected state shape.
    """
    try:
        with session_state(session_id) as st:
            existing = st.get(_GRANT_KEY, [])
            if not isinstance(existing, list) or not existing:
                return
            cleaned: list[dict] = []
            for gd in existing:
                try:
                    if ScopedAllow.from_dict(gd).is_valid():
                        cleaned.append(gd)
                except Exception:
                    # Malformed entry — drop it (same policy as plugins.py cleanup).
                    continue
            if len(cleaned) != len(existing):
                st[_GRANT_KEY] = cleaned
    except Exception:
        pass  # fail-open — GC must never block session-start


def _load_threshold(session_id: str) -> CacheThreshold:
    """Resolve threshold with ``session → global → default`` precedence.

    The session-level blob (if any) overrides the global blob field-by-field,
    so a user can globally set a ratio floor and then tighten just one axis per
    session. Unset fields fall through to ``CacheThreshold()`` defaults (all
    axes off → guard trips nothing).
    """
    def _read(sid: str) -> dict:
        with session_state(sid) as st:
            blob = st.get(_THRESHOLD_KEY)
        return blob if isinstance(blob, dict) else {}

    fields = CacheThreshold.__dataclass_fields__
    merged: dict = {}
    # Always read the global base first.
    for k, v in _read(_GLOBAL_SESSION_ID).items():
        if k in fields and v is not None:
            merged[k] = v
    # Overlay session-specific overrides ONLY if we're not already viewing global.
    # (When session_id == _GLOBAL_SESSION_ID, reading it again would be a no-op,
    # so skip; but the previous implementation skipped the global read entirely —
    # making `/ar:cache global status` blind to `/ar:cache global set`.)
    if session_id != _GLOBAL_SESSION_ID:
        for k, v in _read(session_id).items():
            if k in fields and v is not None:
                merged[k] = v
    return CacheThreshold(**merged)


# ── usage helpers ────────────────────────────────────────────────────

def _memoise(session_id: str, usage: UsageReading) -> None:
    with session_state(session_id) as st:
        st[_LAST_USAGE_KEY] = dataclasses.asdict(usage)


def _usage_from_memo(memo: dict) -> UsageReading:
    kwargs = {k: memo.get(k) for k in UsageReading.__dataclass_fields__}
    kwargs.setdefault("observed_at", 0.0)
    return UsageReading(**kwargs)


def _usage_from_snapshot(snap: dict) -> "UsageReading | None":
    """Build a UsageReading from a statusline-tap snapshot.

    Note on ``cache_age_seconds``: snapshot age means "how long since autorun
    ingested this snapshot", NOT "how long since the last real cache hit".
    The two are not interchangeable, so we intentionally leave
    ``cache_age_seconds=None`` here — the age axis is only meaningful via a
    JSONL timestamp (set in ``_usage_from_assistant``). Otherwise users could
    get spurious blocks during UI-idle periods when statusline ticks stop.
    """
    try:
        cw = snap.get("context_window") or {}
        cu = (cw.get("current_usage") or {}) if isinstance(cw, dict) else {}
        input_tokens = int(cu.get("input_tokens") or 0)
        cache_read = int(cu.get("cache_read_input_tokens") or 0)
        cache_creation = int(cu.get("cache_creation_input_tokens") or 0)
        denom = input_tokens + cache_read + cache_creation
        ratio = (cache_read / denom) if denom > 0 else None
        window = cw.get("context_window_size")
        proximity = None
        if isinstance(window, int) and window > 0 and denom > 0:
            proximity = denom / window
        rl = snap.get("rate_limits") or {}
        rl5 = _rate_limit_frac((rl.get("five_hour") or {}).get("used_percentage"))
        rl7 = _rate_limit_frac((rl.get("seven_day") or {}).get("used_percentage"))
        observed = snap.get("observed_at") or time.time()
        # NB: cache_age_seconds intentionally None (see docstring).
        cli = "claude"
        model = snap.get("model")
        if isinstance(model, dict) and "gemini" in str(model.get("id", "")).lower():
            cli = "gemini"
        # 0 cache_read / cache_creation are legitimate signals (cold cache),
        # not "missing data" — keep them as ints so the floor axes still trip.
        return UsageReading(
            cli=cli,
            total_input_tokens=denom if denom > 0 else None,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            cache_hit_ratio=ratio,
            cache_age_seconds=None,
            context_window_size=window if isinstance(window, int) else None,
            compaction_proximity=proximity,
            rate_limit_5h=rl5,
            rate_limit_7d=rl7,
            observed_at=observed,
        )
    except Exception:
        return None


def _rate_limit_frac(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Statusline schema reports 0..100; normalise.
    return f / 100.0 if f > 1.0 else f


# ── env + stdin helpers ──────────────────────────────────────────────

def _env_session_id() -> str | None:
    return os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("GEMINI_SESSION_ID")


def _transcript_path(stdin: dict, session_id: str) -> str | None:
    path = stdin.get("transcript_path") if isinstance(stdin, dict) else None
    if path:
        return os.path.expanduser(path)
    return None  # fail-open; caller treats None as "no usage info"


def _fingerprint_call(stdin: dict, session_id: str) -> str:
    """Match the fingerprint format used by ``plugins.check_blocked_commands``
    (``plugins.py:608-610``) so cache-override grace is consistent with /ar:ok.
    """
    import hashlib
    tool = stdin.get("tool_name", "") if isinstance(stdin, dict) else ""
    cmd = ""
    if isinstance(stdin, dict):
        ti = stdin.get("tool_input") or {}
        if isinstance(ti, dict):
            cmd = str(ti.get("command") or ti.get("file_path") or "")
    return hashlib.md5(f"{session_id}:{tool}:{cmd}".encode()).hexdigest()[:16]


# ── CLI tap (autorun --cache-snapshot) ───────────────────────────────

# ── /ar:cache slash-command dispatcher ──────────────────────────────

_THRESHOLD_KEY = "cache/threshold"


def _save_threshold(session_id: str, th: CacheThreshold) -> None:
    with session_state(session_id) as st:
        st[_THRESHOLD_KEY] = {k: v for k, v in dataclasses.asdict(th).items() if v is not None}


def _clear_overrides(session_id: str) -> None:
    with session_state(session_id) as st:
        if _GRANT_KEY in st:
            del st[_GRANT_KEY]


def cache_command(args: str, session_id: str) -> str:
    """Handle ``/ar:cache [subcommand] [args...]``. Returns the string to render.

    Grammar (mirrors existing `/ar:ok` / `/ar:no` exactly for the `ok|no|on|off`
    scope parsing via ``parse_scope_args``):

        /ar:cache                    — show status
        /ar:cache on [5m|1h|perm]    — enable (optionally for a window)
        /ar:cache off [5m|1h|perm]   — disable
        /ar:cache set ratio 0.3      — cache hit ratio floor (percent/decimal)
        /ar:cache set read 50k       — cache_read_tokens floor (tokens)
        /ar:cache set age 10m        — cache age ceiling (duration)
        /ar:cache set full 0.9       — compaction-proximity ceiling (percent)
        /ar:cache ok [5m|5|perm]     — override for a scope
        /ar:cache no                 — clear all overrides
        /ar:cache status             — alias for no-arg form
    """
    from .scoped_allow import parse_scope_args, parse_duration

    parts = (args or "").strip().split()
    sub = parts[0].lower() if parts else ""

    # `global` prefix — rewrite to act on the shared scope, then re-dispatch
    # the remaining sub-args. Produces the same `/ar:globalok`-style grammar
    # the user already knows (§6.8 of the plan). The global scope shares the
    # same JSON store + filelock, so multiprocess safety is inherited.
    if sub == "global":
        remainder = " ".join(parts[1:])
        out = cache_command(remainder, _GLOBAL_SESSION_ID)
        return f"[global] {out}"

    rest = parts[1:]
    toggle = FeatureToggle("cache", session_id=session_id)

    if sub in ("", "status"):
        return _render_status(session_id, toggle)

    if sub in ("on", "enable"):
        dur = None
        if rest:
            dur = parse_duration(rest[0])
            if dur is None and rest[0].lower() not in _PERMANENT_WORDS:
                return f"ar:cache: could not parse duration {rest[0]!r}. Try 5m, 1h, or omit."
        toggle.enable(duration_seconds=dur)
        return f"ar:cache enabled{' for ' + rest[0] if dur else ''}."

    if sub in ("off", "disable"):
        dur = None
        if rest:
            dur = parse_duration(rest[0])
            if dur is None and rest[0].lower() not in _PERMANENT_WORDS:
                return f"ar:cache: could not parse duration {rest[0]!r}. Try 5m, 1h, or omit."
        toggle.disable(duration_seconds=dur)
        return f"ar:cache disabled{' for ' + rest[0] if dur else ''}."

    if sub == "set":
        if len(rest) < 2:
            return "ar:cache set: usage `/ar:cache set <axis> <value>`. Axes: ratio, read, age, full."
        axis = rest[0].lower()
        value = rest[1]
        th = _load_threshold(session_id)
        try:
            if axis == "ratio":
                th = dataclasses.replace(th, cache_hit_ratio_min=parse_quantity(value, expect="percent"))
            elif axis == "read":
                tokens = parse_quantity(value, expect="tokens")
                th = dataclasses.replace(th, cache_read_tokens_min=None if tokens is PERMANENT else int(tokens))
            elif axis == "age":
                secs = parse_duration(value)
                if secs is None:
                    return f"ar:cache set age: cannot parse duration {value!r}. Try 5m, 10m, 1h."
                th = dataclasses.replace(th, cache_age_max_seconds=secs)
            elif axis == "full":
                th = dataclasses.replace(th, compaction_used_max=parse_quantity(value, expect="percent"))
            else:
                return f"ar:cache set: unknown axis {axis!r}. Axes: ratio, read, age, full."
        except ValueError as e:
            return f"ar:cache set {axis}: {e}"
        _save_threshold(session_id, th)
        return f"ar:cache threshold updated: {axis} = {value}"

    if sub == "ok":
        desc = " ".join(rest) if rest else ""
        ttl, uses, perm = parse_scope_args(desc or None)
        if not perm and ttl is None and uses is None:
            uses = 1  # match /ar:ok default
        grant_override(
            session_id,
            ttl_seconds=ttl,
            uses=None if perm else uses,
            permanent=perm,
        )
        label = "permanent" if perm else (f"{uses} uses" if uses else f"{int(ttl)}s")
        return f"ar:cache override granted ({label})."

    if sub == "no":
        _clear_overrides(session_id)
        return "ar:cache overrides cleared; gate re-armed."

    return f"ar:cache: unknown subcommand {sub!r}. Try `/ar:cache` for status."


def _render_status(session_id: str, toggle: FeatureToggle) -> str:
    """Pretty status line.

    Reads session + global thresholds (session wins) and surfaces which CLI we
    detected so the user knows which axes are active vs fail-open.
    """
    th = _load_threshold(session_id)
    enabled = toggle.is_enabled()
    cli = detect_cli_type() or "claude"

    # When viewing the GLOBAL scope, label it explicitly so the user doesn't
    # confuse session-level output with global-level output.
    scope_label = "global" if session_id == _GLOBAL_SESSION_ID else "session"
    lines = [f"ar:cache [{scope_label}] — {'enabled' if enabled else 'disabled (default)'} — cli: {cli}"]

    axis_lines = []
    if th.cache_hit_ratio_min is not None:
        axis_lines.append(f"  ratio floor: {th.cache_hit_ratio_min:.2f}")
    if th.cache_read_tokens_min is not None:
        axis_lines.append(f"  cache_read floor: {th.cache_read_tokens_min}")
    if th.cache_age_max_seconds is not None:
        axis_lines.append(f"  age ceiling: {int(th.cache_age_max_seconds)}s")
    if th.compaction_used_max is not None:
        axis_lines.append(f"  window ceiling: {th.compaction_used_max:.2f}")
    if th.rate_limit_5h_max is not None:
        axis_lines.append(f"  rate-limit 5h ceiling: {th.rate_limit_5h_max:.2f}")
    if th.rate_limit_7d_max is not None:
        axis_lines.append(f"  rate-limit 7d ceiling: {th.rate_limit_7d_max:.2f}")
    lines.append("thresholds:")
    lines.extend(axis_lines or ["  (none configured)"])

    with session_state(session_id) as st:
        grants = list(st.get(_GRANT_KEY, []))
    lines.append(f"active overrides: {len(grants)}")

    # Honest labelling of cross-CLI signal quality.
    if cli == "gemini":
        lines.append("note: Gemini hooks do not surface cache tokens to hooks — "
                     "ratio / cache_read axes fail-open unless the transcript JSONL carries them.")
    if not enabled:
        lines.append("enable with: /ar:cache on   (optionally `/ar:cache on 1h` for a window)")
    return "\n".join(lines)


def persist_statusline_snapshot(fp: IO[str]) -> int:
    """Read the Claude statusline JSON from ``fp`` and persist the fields we
    care about under ``cache/statusline_snapshot``. Returns 0 always (fail-open
    — the user's statusline must keep working even if we choke).
    """
    try:
        data = json.load(fp)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    sid = data.get("session_id") or _env_session_id()
    if not sid:
        return 0
    snap = {
        "context_window": data.get("context_window"),
        "rate_limits":    data.get("rate_limits"),
        "model":          data.get("model"),
        "observed_at":    time.time(),
    }
    try:
        with session_state(sid) as st:
            st[_SNAPSHOT_KEY] = snap
    except Exception:
        return 0
    return 0
