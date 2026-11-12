"""/ar:cache — cache-pressure / cache-miss protection gate.

Single-file feature. Exports:
    parse_quantity, PERMANENT           — token/percent parser
    is_cache_enabled, set_cache_enabled — toggle query/update
    CacheGuardConfig, CacheGuard        — decision logic
    grant_override, purge_stale_overrides — ScopedAllow lifecycle
    persist_statusline_snapshot         — CLI tap (`autorun --cache-snapshot`)
    cache_command                       — /ar:cache subcommand dispatcher
    _read_jsonl_tail                    — JSONL bounded-tail reader

The feature is OFF by default. Design principle: "stay out of the way" — no
statusline install, no new sync primitive, no new state file.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import IO, Literal, Optional

from .config import CONFIG, detect_cli_type
from .scoped_allow import (
    ScopedAllow, _PERMANENT_KEYWORDS,
    call_fingerprint, parse_scope_args, parse_duration,
)
from .session_manager import session_state


# ── sentinels ─────────────────────────────────────────────────────────

PERMANENT = None          # Returned by parse_quantity("perm") — means "axis disabled"
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

    ``expect="tokens"`` — returns ``int`` or ``PERMANENT`` (``None``). Accepts:
      - bare integers with commas/underscores: ``50000``, ``50,000``, ``50_000``
      - ``k`` / ``K`` suffix (×1 000):        ``50k``, ``200K``
      - ``m`` / ``M`` suffix (×1 000 000):    ``.5M``, ``1.5M``, ``2m``
      - permanent sentinel words:             ``perm`` / ``permanent`` / ``p``
        (canonical set from scoped_allow — same grammar as /ar:ok).

    ``expect="percent"`` — returns ``float`` in ``[0, 1]``. Accepts:
      - percent syntax:       ``85%``, ``92%``, ``100%``
      - bare decimal ≤ 1:     ``0.85``, ``0.5``

    Raises ``ValueError`` on malformed input.
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
            try:
                v = float(raw)
            except ValueError as e:
                raise ValueError(f"parse_quantity: cannot parse percent {s!r}") from e
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"parse_quantity: percent {s!r} out of [0, 1]")
        return v

    raise ValueError(f"parse_quantity: unknown family {expect!r}")


# ── toggle ───────────────────────────────────────────────────────────

_GLOBAL_SESSION_ID = "__global__"
# flat dict: {enabled, expired_prompt_action, expires_at, prior}. `prior` is
# the whole entry a timed toggle replaced, or None when there was none;
# entries written before 1.0.0rc4 hold a bool there.
_TOGGLE_KEY = "cache/toggle"
_EXPIRED_PROMPT_ACTIONS = ("block", "warn")


def _effective_entry(toggle) -> "dict | None":
    """The entry in force now: a timed entry past its expiry yields its prior.

    None means nothing is stored, so the caller falls back to the global
    entry and then to CONFIG. Never raises on an old or malformed shape.
    """
    if not isinstance(toggle, dict) or not toggle:
        return None
    exp = toggle.get("expires_at")
    if isinstance(exp, (int, float)) and time.time() >= exp:
        prior = toggle.get("prior")
        if isinstance(prior, bool):
            return {"enabled": prior}
        return _effective_entry(prior)
    return toggle


def _expired_prompt_action(entry: "dict | None") -> "str | None":
    """`block`, `warn`, or None (off) for the prompt-time expiry check."""
    default = CONFIG["cache_guard_expired_prompt_action"]
    if entry is None:
        return default
    if not entry.get("enabled", False):
        return None
    action = entry.get("expired_prompt_action")
    return action if action in _EXPIRED_PROMPT_ACTIONS else default


def _read_toggle(session_id: str) -> dict:
    # ONE lock total — nested global read is reentrant (session_manager.py:172-175).
    with session_state(session_id) as st:
        t = st.get(_TOGGLE_KEY)
        if _effective_entry(t) is not None:
            return t
        if session_id != _GLOBAL_SESSION_ID:
            with session_state(_GLOBAL_SESSION_ID) as gst:   # reentrant: 1 lock total
                g = gst.get(_TOGGLE_KEY)
            return g if g and isinstance(g, dict) else {}
    return {}


def is_cache_enabled(session_id: str) -> bool:
    t = _read_toggle(session_id)
    return _toggle_enabled(t)


def _toggle_enabled(toggle: dict) -> bool:
    """Resolve an enabled flag, including temporary override expiry."""
    return bool((_effective_entry(toggle) or {}).get("enabled", False))


def _read_toggle_from_ctx(ctx: object, session_id: str) -> dict:
    """Read session then global toggle through the daemon's state cache."""
    state_get = getattr(ctx, "state_get")
    toggle = state_get(_TOGGLE_KEY, None, session_id=session_id)
    if _effective_entry(toggle) is not None:
        return toggle
    if session_id != _GLOBAL_SESSION_ID:
        global_toggle = state_get(_TOGGLE_KEY, None, session_id=_GLOBAL_SESSION_ID)
        if global_toggle and isinstance(global_toggle, dict):
            return global_toggle
    return {}


def set_cache_enabled(
    session_id: str,
    enabled: bool,
    duration: float | None = None,
    expired_prompt_action: str = "block",
) -> None:
    with session_state(session_id) as st:
        current = st.get(_TOGGLE_KEY)
        exp = current.get("expires_at") if isinstance(current, dict) else None
        if isinstance(exp, (int, float)) and time.time() < exp:
            # Replacing a still-running temporary entry: its prior is the
            # standing state. Reading the effective state instead stored the
            # temporary value, so `off 1h` twice left the gate off for good.
            prior = current.get("prior")
        else:
            # The whole entry, so expiry restores the prompt action too; None
            # when this session stored nothing, so expiry falls back to the
            # global entry and then to CONFIG.
            prior = _effective_entry(current)
        entry: dict = {"enabled": enabled}
        if enabled:
            entry["expired_prompt_action"] = expired_prompt_action
        if duration is not None:
            entry["expires_at"] = time.time() + duration
            entry["prior"] = prior
        st[_TOGGLE_KEY] = entry


# ── CacheGuardConfig + UsageReading ──────────────────────────────────

_THRESHOLD_KEY = "cache/threshold"


@dataclass
class CacheGuardConfig:
    """Decision thresholds. None = axis disabled (fail-open). Mutable for _cmd_set setattr."""
    cache_hit_ratio_min:   float | None = None
    cache_read_tokens_min: int   | None = None
    cache_age_max_seconds: float | None = None
    compaction_used_max:   float | None = None
    rate_limit_5h_max:     float | None = None
    rate_limit_7d_max:     float | None = None
    # Smallest context the prompt-time expiry check applies to; None reads
    # CONFIG cache_guard_expired_min_tokens. Not an axis: see _expired_trip.
    expired_min_tokens:    int   | None = None

    @classmethod
    def load(cls, session_id: str) -> "CacheGuardConfig":
        """Merge global base → session overlay in ONE lock acquisition (reentrant nested open)."""
        _fields = cls.__dataclass_fields__
        with session_state(session_id) as st:
            s_blob = st.get(_THRESHOLD_KEY)
            if session_id == _GLOBAL_SESSION_ID:
                g_blob, s_blob = s_blob, None
            else:
                with session_state(_GLOBAL_SESSION_ID) as gst:   # reentrant: 1 lock total
                    g_blob = gst.get(_THRESHOLD_KEY)
        merged: dict = {}
        for blob in (g_blob, s_blob):
            if isinstance(blob, dict):
                merged.update({k: v for k, v in blob.items() if k in _fields and v is not None})
        return cls(**merged)

    @classmethod
    def load_from_ctx(cls, ctx: object, session_id: str) -> "CacheGuardConfig":
        """Merge global and session thresholds through cached context state."""
        state_get = getattr(ctx, "state_get")
        global_blob = state_get(_THRESHOLD_KEY, None, session_id=_GLOBAL_SESSION_ID)
        session_blob = (
            None
            if session_id == _GLOBAL_SESSION_ID
            else state_get(_THRESHOLD_KEY, None, session_id=session_id)
        )
        fields = cls.__dataclass_fields__
        merged: dict = {}
        for blob in (global_blob, session_blob):
            if isinstance(blob, dict):
                merged.update({k: v for k, v in blob.items() if k in fields and v is not None})
        return cls(**merged)

    def save(self, session_id: str) -> None:
        data = {k: v for k, v in dataclasses.asdict(self).items() if v is not None}
        with session_state(session_id) as st:
            st[_THRESHOLD_KEY] = data


@dataclass
class UsageReading:
    cli: str = "claude"
    total_input_tokens:     int   | None = None
    cache_read_tokens:      int   | None = None
    cache_creation_tokens:  int   | None = None
    cache_hit_ratio:        float | None = None
    cache_age_seconds:      float | None = None
    # How long the cache this response wrote lives: 3600 for a 1-hour write,
    # 300 for a 5-minute one, None when the record does not say (Pi, Prime).
    cache_lifetime_seconds: float | None = None
    context_window_size:    int   | None = None
    compaction_proximity:   float | None = None
    rate_limit_5h:          float | None = None
    rate_limit_7d:          float | None = None
    observed_at:            float = 0.0


# ── JSONL bounded-tail reader ────────────────────────────────────────

# Tail-scan window sizes (from CONFIG — see config.py "Cache Guard" section).
# A retry at 4× initial handles sessions with large tool_result payloads
# without paying O(file_size) on every PreToolUse.
_TAIL_RETRY_BYTES        = CONFIG["cache_guard_jsonl_retry_bytes"]

_ASSISTANT_TYPES = frozenset({"assistant", "model"})
_TIMESTAMP_KEYS  = ("timestamp", "time", "created_at", "createdAt", "ts")


def _read_jsonl_tail(path: str, *, max_bytes: int, cli: str) -> "UsageReading | None":
    """Reverse-read up to ``max_bytes`` of the JSONL and find the latest
    assistant/model entry with a usage object.

    Complexity: O(max_bytes). A single retry with ``_TAIL_RETRY_BYTES`` handles
    sessions with large tool_result payloads. No unbounded file reads.
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
    if start > 0:
        nl = text.find("\n")
        if nl == -1:
            return None
        text = text[nl + 1:]
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
        if entry_type == "message" and isinstance(obj.get("message"), dict):
            entry_type = str(obj["message"].get("role") or "").lower()
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

    Returns the first non-empty dict found, or None.
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
    """Normalise a per-message usage dict across known CLI shapes."""
    def _first_int(*keys: str) -> int:
        for k in keys:
            v = usage.get(k)
            if v is not None:
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
        return 0

    input_tokens   = _first_int("input_tokens", "promptTokenCount", "prompt_tokens", "input")
    cache_read     = _first_int("cache_read_input_tokens", "cachedContentTokenCount",
                                "cached_content_token_count", "cache_read", "cacheRead")
    cache_creation = _first_int("cache_creation_input_tokens", "cache_creation",
                                "cache_creation_tokens", "cacheWrite")

    denom = input_tokens + cache_read + cache_creation
    ratio: float | None = (cache_read / denom) if denom > 0 else None

    # Claude Code records which lifetime the response wrote. The longer one
    # decides: its part of the cache is the last to expire.
    lifetime: float | None = None
    written = usage.get("cache_creation")
    if isinstance(written, dict):
        for key, seconds in (("ephemeral_1h_input_tokens", 3600.0), ("ephemeral_5m_input_tokens", 300.0)):
            try:
                if int(written.get(key) or 0) > 0:
                    lifetime = seconds
                    break
            except (TypeError, ValueError):
                continue

    # Clock-skew handling: far-future timestamps (beyond tolerance) → age = None
    # (fail-open), not clamped 0 which would silently suppress age-axis trips.
    age: float | None = None
    _clock_skew_tol = CONFIG["cache_guard_clock_skew_tolerance_s"]
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
        elif -delta <= _clock_skew_tol:
            age = 0.0   # small negative drift → treat as "just happened"
        else:
            age = None  # far-future timestamp → unknown → fail-open
        break

    return UsageReading(
        cli=cli,
        total_input_tokens=denom if denom > 0 else None,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        cache_hit_ratio=ratio,
        cache_age_seconds=age,
        cache_lifetime_seconds=lifetime,
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
    try:
        from datetime import datetime, timezone
        s = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


# ── declarative axis table ────────────────────────────────────────────

@dataclass(frozen=True)
class _Axis:
    field: str        # CacheGuardConfig attribute (threshold side)
    usage_field: str  # UsageReading attribute (measurement side)
    cmp: str          # "lt" → trip when usage < threshold; "gt" → trip when usage > threshold
    trip_fmt: str     # format: {u}=usage value, {t}=threshold value
    status_fmt: str   # format for _render_status: {t}=threshold value
    gemini_ok: bool   # False → axis data unavailable on Gemini → always fail-open
    set_key: str      # user-facing name for /ar:cache set <key>; "" = not user-settable
    parser: str       # "percent", "tokens", or "duration" — drives _cmd_set parsing

_AXES: tuple[_Axis, ...] = (
    _Axis("cache_hit_ratio_min",   "cache_hit_ratio",     "lt",
          "ratio {u:.2f} < {t:.2f}",
          "ratio floor:         {t:.2f}", gemini_ok=False, set_key="ratio",  parser="percent"),
    _Axis("cache_read_tokens_min", "cache_read_tokens",   "lt",
          "cache_read {u:,} < {t:,}",
          "cache_read floor:    {t:,}",  gemini_ok=False, set_key="read",   parser="tokens"),
    _Axis("cache_age_max_seconds", "cache_age_seconds",   "gt",
          "age {u:.0f}s > {t:.0f}s",
          "age ceiling:         {t:.0f}s", gemini_ok=True,  set_key="age",    parser="duration"),
    _Axis("compaction_used_max",   "compaction_proximity","gt",
          "window {u:.2f} > {t:.2f}",
          "window ceiling:      {t:.2f}", gemini_ok=True,  set_key="full",   parser="percent"),
    _Axis("rate_limit_5h_max",     "rate_limit_5h",       "gt",
          "rate-limit 5h {u:.2%} > {t:.2%}",
          "rate-limit 5h max:   {t:.2%}", gemini_ok=True,  set_key="",       parser="percent"),
    _Axis("rate_limit_7d_max",     "rate_limit_7d",       "gt",
          "rate-limit 7d {u:.2%} > {t:.2%}",
          "rate-limit 7d max:   {t:.2%}", gemini_ok=True,  set_key="",       parser="percent"),
)

# Only axes that are user-settable (set_key != "")
_AXES_SET_MAP: dict[str, _Axis] = {ax.set_key: ax for ax in _AXES if ax.set_key}
# The prompt-time expiry check's context floor; a `set` key that is not an axis.
_EXPIRED_MIN_TOKENS_KEY = "expired-min-tokens"


def _expired_min_tokens(cfg: CacheGuardConfig) -> int:
    floor = cfg.expired_min_tokens
    return CONFIG["cache_guard_expired_min_tokens"] if floor is None else floor


def _expired_trip(usage: Optional[UsageReading], cfg: CacheGuardConfig) -> bool:
    """True when the last response's cache has expired on a large context.

    Two measurements against each other, which an `_AXES` row (one field
    against one threshold) cannot express. The lifetime is the one the
    record wrote, else the user's `age` (Pi and Prime Agent records carry
    none); with neither it does not trip. Missing data never trips.
    """
    if usage is None or usage.cache_age_seconds is None or usage.total_input_tokens is None:
        return False
    lifetime = usage.cache_lifetime_seconds or cfg.cache_age_max_seconds
    if lifetime is None:
        return False
    return usage.cache_age_seconds > lifetime and usage.total_input_tokens >= _expired_min_tokens(cfg)


def _which_axes_trip(cfg: CacheGuardConfig, usage: Optional[UsageReading]) -> list[str]:
    """Return trip messages for every axis that fires."""
    if usage is None:
        return []
    trips = []
    for ax in _AXES:
        t = getattr(cfg, ax.field, None)
        u = getattr(usage, ax.usage_field, None)
        if t is None or u is None:
            continue
        if (u < t) if ax.cmp == "lt" else (u > t):
            trips.append(ax.trip_fmt.format(u=u, t=t))
    return trips


# ── CacheGuard ────────────────────────────────────────────────────────

_DEFAULT_JSONL_MAX_BYTES = CONFIG["cache_guard_jsonl_scan_bytes"]
_MEMO_TTL_SECONDS        = CONFIG["cache_guard_memo_ttl_seconds"]
_GRANT_KEY = "cache/overrides"
_LAST_USAGE_KEY = "cache/last_usage"
_SNAPSHOT_KEY = "cache/statusline_snapshot"
# sha256 of prompts the expiry check blocked; the same text then passes.
_RESENT_PROMPTS_KEY = "cache/blocked_prompts"
_RESENT_PROMPTS_KEPT = 50  # ponytail: oldest dropped; a block is rare, 50 covers a session


@dataclass
class CacheGuard:
    session_id: str
    config: CacheGuardConfig = field(default_factory=CacheGuardConfig)

    @classmethod
    def from_session(cls, session_id: str | None = None) -> "CacheGuard":
        sid = (session_id or os.environ.get("CLAUDE_SESSION_ID")
               or os.environ.get("GEMINI_SESSION_ID") or "unknown")
        return cls(session_id=sid, config=CacheGuardConfig.load(sid))

    @classmethod
    def from_ctx(cls, ctx: object) -> "CacheGuard":
        sid = getattr(ctx, "session_id", None) or "unknown"
        return cls(session_id=sid, config=CacheGuardConfig.load_from_ctx(ctx, sid))

    # ── main entry point ─────────────────────────────────────────

    def check(self, ctx: object) -> Optional[dict]:
        """Returns None (allow) or ctx.deny(msg) (block)."""
        if hasattr(ctx, "state_get"):
            enabled = _toggle_enabled(_read_toggle_from_ctx(ctx, self.session_id))
        else:
            enabled = is_cache_enabled(self.session_id)
        if not enabled:
            return None
        usage = self._read_usage(ctx)
        trips = _which_axes_trip(self.config, usage)
        if not trips:
            return None
        if self._consume_override(ctx):
            return None
        return ctx.deny(self._render_block(usage, trips))  # type: ignore[union-attr]

    def check_prompt_after_expiry(self, ctx: object) -> Optional[dict]:
        """Prompt-time check: None, or a denial when the cache has expired.

        Runs only the `expired` test (`_expired_trip`); the user's `ratio`,
        `read` and `age` thresholds stay at tool time in `check`. The caller
        decides which prompts reach it (the user's own, never a command).
        """
        action = _expired_prompt_action(_effective_entry(_read_toggle_from_ctx(ctx, self.session_id)))
        if action is None:
            return None
        usage = self._read_usage(ctx)
        if not _expired_trip(usage, self.config):
            return None
        if action == "warn":
            # Shown to the user only; the model gains nothing from it.
            return ctx.respond("allow", _render_expired(usage, self.config, blocking=False), to_ai=False)  # type: ignore[union-attr]
        prompt = (ctx.prompt or "").strip()  # type: ignore[union-attr]
        digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if digest in (ctx.state_get(_RESENT_PROMPTS_KEY, []) or []):  # type: ignore[union-attr]
            return None  # sent again after a block: the user chose to pay
        if self._consume_override(ctx):
            return None
        ctx.state_update(  # type: ignore[union-attr]
            _RESENT_PROMPTS_KEY,
            lambda kept: [*(kept if isinstance(kept, list) else []), digest][-_RESENT_PROMPTS_KEPT:],
            [],
        )
        return ctx.deny(_render_expired(usage, self.config))  # type: ignore[union-attr]

    _INVALIDATING_EVENTS = frozenset({
        "PreCompact",   "PostCompact",    # Claude Code: summarise lifecycle
        "SessionStart",                   # Claude Code + Gemini: session re-entry
        "PreCompress",                    # Gemini CLI: advisory pre-compression
    })

    def on_compaction_event(self, event: str) -> None:
        """Invalidate usage memo on compaction/session events.

        Fires on PreCompact/PostCompact/SessionStart (Claude Code) and
        PreCompress/SessionStart (Gemini). Cost is one del inside the existing
        filelock; erring toward extra invalidation is safe — the next PreToolUse
        simply re-reads the JSONL tail.
        """
        if (event or "") in self._INVALIDATING_EVENTS:
            with session_state(self.session_id) as st:
                for key in (_LAST_USAGE_KEY, _SNAPSHOT_KEY):
                    if key in st:
                        del st[key]

    # ── usage resolution ─────────────────────────────────────────

    def _read_usage(self, ctx: object) -> Optional[UsageReading]:
        """Resolve current usage via memo → snapshot → JSONL scan.

        Lock budget: one filelock in the common path (memo/snapshot hit);
        two when falling back to JSONL (read + memoise write).
        """
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

        path = getattr(ctx, "transcript_path", None)
        if path:
            path = os.path.expanduser(str(path))
        if not path:
            return None
        cli = getattr(ctx, "cli_type", None) or detect_cli_type() or "claude"
        usage = _read_jsonl_tail(path, max_bytes=_DEFAULT_JSONL_MAX_BYTES, cli=cli)
        if usage is not None:
            _memoise(self.session_id, usage)
        return usage

    # ── decision axes ────────────────────────────────────────────

    def _which_axes_trip(self, usage: Optional[UsageReading]) -> list[str]:
        return _which_axes_trip(self.config, usage)

    # ── overrides ────────────────────────────────────────────────

    def _consume_override(self, ctx: object) -> bool:
        """Spend a session override, else a `/ar:cache global ok` one."""
        call_id = call_fingerprint(ctx)
        return _consume_by_call_id(self.session_id, call_id) or _consume_by_call_id(_GLOBAL_SESSION_ID, call_id)

    # ── UX ──────────────────────────────────────────────────────

    def _render_block(self, usage: Optional[UsageReading], trips: list[str]) -> str:
        rate_limit_tripped = any(t.startswith("rate-limit") for t in trips)
        heading = ("ar:cache BLOCKED — rate limit" if rate_limit_tripped
                   else "ar:cache BLOCKED — prompt cache is cold")
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


def _duration_words(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 1:
        return f"{int(seconds)} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} m" if hours else f"{minutes} m"


def _render_expired(usage: UsageReading, cfg: CacheGuardConfig, *, blocking: bool = True) -> str:
    """The prompt-time message. Names no tool-time axis or hint."""
    lifetime = usage.cache_lifetime_seconds or cfg.cache_age_max_seconds or 0
    ago = _duration_words(max(0.0, (usage.cache_age_seconds or 0) - lifetime))
    tokens = f"{round(usage.total_input_tokens or 0, -3):,}"
    if blocking:
        cost = f"Sending now re-writes about {tokens} tokens at the cache-write price.\n"
        proceed = "  Send the same prompt again to proceed, or /ar:cache ok\n"
        switch = "/ar:cache warn   tell me, do not stop me"
    else:
        cost = f"This prompt re-writes about {tokens} tokens at the cache-write price.\n"
        proceed = ""
        switch = "/ar:cache on     stop me before sending"
    return (
        f"ar:cache: this session's prompt cache expired {ago} ago.\n"
        f"{cost}{proceed}"
        "  /clear or a new session avoids the cost; /compact lowers later turns\n"
        f"  /ar:cache off        this session      {switch}\n"
        "  /ar:cache set expired-min-tokens 200k  only warn or block at this context size or larger"
    )


# ── grants + override helpers ─────────────────────────────────────────

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
        granted_at=time.time(),   # always set for status_label() remaining-time display
        ttl_seconds=ttl_seconds,
        remaining_uses=None if permanent else uses,
    )
    with session_state(session_id) as st:
        existing = list(st.get(_GRANT_KEY, []))
        existing.append(sa.to_dict())
        st[_GRANT_KEY] = existing


def _consume_by_call_id(session_id: str, call_id: str) -> bool:
    """Consume one ScopedAllow grant, inlining GC of expired entries."""
    with session_state(session_id) as st:
        grants_raw = list(st.get(_GRANT_KEY, []))
        live: list[dict] = []
        consumed_any = False
        for gd in grants_raw:
            try:
                g = ScopedAllow.from_dict(gd)
            except Exception:
                continue             # malformed — drop (GC inline)
            if not g.is_valid(call_id):
                continue             # expired/exhausted — drop (GC inline)
            if not consumed_any:
                new_g = g.consume(call_id)
                consumed_any = True
                if new_g.is_valid(call_id):
                    live.append(new_g.to_dict())
            else:
                live.append(g.to_dict())
        if consumed_any or len(live) < len(grants_raw):
            st[_GRANT_KEY] = live
    return consumed_any


def purge_stale_overrides(session_id: str) -> None:
    """GC expired/exhausted grants from cache/overrides without consuming any.

    Called on SessionStart to keep the list tidy. The consume path also GCs
    inline, so this is maintenance-only — safe to skip if it fails (fail-open).
    """
    with session_state(session_id) as st:
        grants_raw = list(st.get(_GRANT_KEY, []))
        live = []
        for gd in grants_raw:
            try:
                g = ScopedAllow.from_dict(gd)
            except Exception:
                continue
            if g.is_valid():
                live.append(g.to_dict())
        if len(live) < len(grants_raw):
            st[_GRANT_KEY] = live


def _clear_overrides(session_id: str) -> None:
    with session_state(session_id) as st:
        if _GRANT_KEY in st:
            del st[_GRANT_KEY]


# ── usage helpers ─────────────────────────────────────────────────────

def _memoise(session_id: str, usage: UsageReading) -> None:
    with session_state(session_id) as st:
        st[_LAST_USAGE_KEY] = dataclasses.asdict(usage)


def _usage_from_memo(memo: dict) -> UsageReading:
    kwargs = {k: memo.get(k) for k in UsageReading.__dataclass_fields__}
    kwargs.setdefault("observed_at", 0.0)
    return UsageReading(**kwargs)


def _usage_from_snapshot(snap: dict) -> "UsageReading | None":
    """Build a UsageReading from a statusline-tap snapshot.

    Note: cache_age_seconds is intentionally None — snapshot age ≠ cache age.
    The age axis is only meaningful via a JSONL timestamp.
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
        cli = "claude"
        model = snap.get("model")
        if isinstance(model, dict) and "gemini" in str(model.get("id", "")).lower():
            cli = "gemini"
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
    return f / 100.0 if f > 1.0 else f


# ── /ar:cache slash-command dispatcher ───────────────────────────────

def _cmd_toggle(rest: list, session_id: str, target: bool, action: str = "block") -> str:
    # The /ar:ok scope grammar, minus counts: a toggle lasts a duration or perm.
    try:
        _ttl, uses, _perm = parse_scope_args(" ".join(rest))
    except ValueError as e:
        return f"ar:cache: {e}"
    if uses is not None:
        return "ar:cache: on/off takes a duration such as 5m or 1h, or perm; not a count."
    dur = _ttl
    set_cache_enabled(session_id, target, duration=dur, expired_prompt_action=action)
    word = ("enabled (warn only at prompt time)" if action == "warn" else "enabled") if target else "disabled"
    return f"ar:cache {word}{' for ' + ' '.join(rest) if dur else ''}."


def _cmd_on(rest: list, session_id: str) -> str:
    return _cmd_toggle(rest, session_id, True)


def _cmd_off(rest: list, session_id: str) -> str:
    return _cmd_toggle(rest, session_id, False)


def _cmd_warn(rest: list, session_id: str) -> str:
    return _cmd_toggle(rest, session_id, True, action="warn")


def _cmd_set(rest: list, session_id: str) -> str:
    """Data-driven: /ar:cache set <axis> <value>. Adding a settable axis = 1 row in _AXES."""
    keys = ", ".join([*_AXES_SET_MAP, _EXPIRED_MIN_TOKENS_KEY])
    if len(rest) < 2:
        return f"ar:cache set: usage `/ar:cache set <key> <value>`. Keys: {keys}"
    axis, value = rest[0].lower(), rest[1]
    cfg = CacheGuardConfig.load(session_id)
    if axis == _EXPIRED_MIN_TOKENS_KEY:
        try:
            floor = parse_quantity(value, expect="tokens")
        except ValueError as e:
            return f"ar:cache set {axis}: {e}"
        if floor is PERMANENT:
            return f"ar:cache set {axis}: takes a token count such as 0, 100k or 200k."
        cfg.expired_min_tokens = int(floor)
        cfg.save(session_id)
        return f"ar:cache threshold updated: {axis} = {value}"
    ax = _AXES_SET_MAP.get(axis)
    if ax is None:
        return f"ar:cache set: unknown axis {axis!r}. Try: {keys}"
    try:
        if ax.parser == "duration":
            v = parse_duration(value)
            if v is None:
                return f"ar:cache set {axis}: cannot parse duration {value!r}. Try 5m, 10m, 1h."
        else:
            v = parse_quantity(value, expect=ax.parser)
            if ax.parser == "tokens":
                v = None if v is PERMANENT else int(v)  # type: ignore[arg-type]
        setattr(cfg, ax.field, v)
        cfg.save(session_id)
    except ValueError as e:
        return f"ar:cache set {axis}: {e}"
    return f"ar:cache threshold updated: {axis} = {value}"


def _cmd_ok(rest: list, session_id: str) -> str:
    desc = " ".join(rest) if rest else ""
    try:
        ttl, uses, perm = parse_scope_args(desc or None)
    except ValueError as e:
        return f"ar:cache ok: {e}"
    if not perm and ttl is None and uses is None:
        uses = 1
    grant_override(session_id, ttl_seconds=ttl, uses=None if perm else uses, permanent=perm)
    label = "permanent" if perm else (f"{uses} uses" if uses else f"{int(ttl)}s")
    return f"ar:cache override granted ({label})."


def _cmd_no(rest: list, session_id: str) -> str:
    _clear_overrides(session_id)
    return "ar:cache overrides cleared; gate re-armed."


def _cmd_status(rest: list, session_id: str, cli_type: str | None = None) -> str:
    return _render_status(session_id, cli_type)


_CACHE_HANDLERS: dict = {
    "": _cmd_status, "status": _cmd_status, "st": _cmd_status,
    "on": _cmd_on, "enable": _cmd_on,
    "off": _cmd_off, "disable": _cmd_off,
    "warn": _cmd_warn,
    "set": _cmd_set, "ok": _cmd_ok, "no": _cmd_no,
}


def cache_command(args: str, session_id: str, cli_type: str | None = None) -> str:
    """Dispatch /ar:cache subcommands. Grammar: on|warn|off|set|ok|no|status [global]."""
    parts = (args or "").strip().split()
    sub = (parts[0] if parts else "").lower()
    if sub == "global":
        return "[global] " + cache_command(" ".join(parts[1:]), _GLOBAL_SESSION_ID, cli_type)
    fn = _CACHE_HANDLERS.get(sub)
    if fn is None:
        return f"ar:cache: unknown subcommand {sub!r}. Try: on, warn, off, set, ok, no, status"
    if fn is _cmd_status:
        return fn(parts[1:], session_id, cli_type)
    return fn(parts[1:], session_id)


def _safe_is_valid(gd: dict) -> bool:
    try:
        return ScopedAllow.from_dict(gd).is_valid()
    except Exception:
        return False


def _render_status(session_id: str, cli_type: str | None = None) -> str:
    """Pretty status — ONE filelock acquisition for all state reads."""
    with session_state(session_id) as st:
        s_toggle = st.get(_TOGGLE_KEY)
        s_thresh = st.get(_THRESHOLD_KEY)
        grants_raw = list(st.get(_GRANT_KEY, []))
        if session_id == _GLOBAL_SESSION_ID:
            g_toggle, g_thresh = s_toggle, s_thresh
            s_toggle, s_thresh = None, None
        else:
            with session_state(_GLOBAL_SESSION_ID) as gst:   # reentrant: 1 lock total
                g_toggle = gst.get(_TOGGLE_KEY)
                g_thresh = gst.get(_THRESHOLD_KEY)

    entry = _effective_entry(s_toggle)
    if entry is None:
        entry = _effective_entry(g_toggle)
    enabled = bool((entry or {}).get("enabled", False))
    action = _expired_prompt_action(entry)
    _fields = CacheGuardConfig.__dataclass_fields__
    merged: dict = {}
    for blob in (g_thresh, s_thresh):
        if isinstance(blob, dict):
            merged.update({k: v for k, v in blob.items() if k in _fields and v is not None})
    cfg = CacheGuardConfig(**merged)

    # The daemon runs this, so its own environment names whichever harness
    # started it; the caller passes the session's harness.
    cli = cli_type or detect_cli_type() or "claude"
    scope = "global" if session_id == _GLOBAL_SESSION_ID else "session"
    lines = [
        f"ar:cache [{scope}] — {'enabled' if enabled else 'disabled (default)'} — cli: {cli}",
        f"prompt-time expiry check: {action or 'off'}"
        f"{' (default)' if entry is None else ''}, context of"
        f" {_expired_min_tokens(cfg):,} tokens or more",
        "thresholds:",
    ]
    configured = False
    for ax in _AXES:
        t = getattr(cfg, ax.field, None)
        if t is not None:
            fail = " [fail-open on Gemini]" if cli == "gemini" and not ax.gemini_ok else ""
            hint = f"  ← /ar:cache set {ax.set_key} <value>" if ax.set_key else ""
            lines.append(f"  {ax.status_fmt.format(t=t)}{fail}{hint}")
            configured = True
    if not configured:
        lines.append("  (none configured — /ar:cache set ratio|read|age|full <value>)")
    active = [ScopedAllow.from_dict(g) for g in grants_raw
              if isinstance(g, dict) and _safe_is_valid(g)]
    if active:
        lines.append(f"active overrides ({len(active)}):")
        for g in active:
            lines.append(f"  {g.status_label()}")
    else:
        lines.append("active overrides: none")
    if not enabled:
        lines.append("enable with: /ar:cache on   (optionally `/ar:cache on 1h` for a window)")
    return "\n".join(lines)


def persist_statusline_snapshot(fp: IO[str]) -> int:
    """Read the Claude statusline JSON from ``fp`` and persist under ``cache/statusline_snapshot``.

    Returns 0 always (fail-open — the user's statusline must keep working even if we choke).
    """
    try:
        data = json.load(fp)
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    sid = (data.get("session_id")
           or os.environ.get("CLAUDE_SESSION_ID")
           or os.environ.get("GEMINI_SESSION_ID"))
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
