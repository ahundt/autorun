"""Tests for /ar:cache — cache-pressure / cache-miss protection gate.

Plan: /Users/athundt/.claude/plans/make-a-plan-to-sunny-sparkle.md

Covers (all in one file to match the one-source-file design):
  1. parse_quantity — 50k, 50,000, 50_000, .5M, 85%, 0.85, perm, bad
  2. FeatureToggle — default False for 'cache', enable/disable, temporary TTL
  3. _read_jsonl_tail — bounded reverse read, finds last assistant message.usage
  4. CacheGuard.on_pretooluse — decision table (ratio/read/age/rate-limit axes)
  5. Multiprocess safety — two concurrent writers under session_state filelock
  6. persist_statusline_snapshot — CLI tap; bad JSON fails-open
  7. Compaction-event dispatch — PreCompact/PostCompact/PreCompress invalidate memo
"""

from __future__ import annotations

import io
import json
import multiprocessing
import os
import tempfile
import time
from pathlib import Path

import pytest


# === helpers ========================================================

@pytest.fixture
def tmp_state_dir(tmp_path, monkeypatch):
    """Redirect session_state to a per-test directory."""
    d = tmp_path / "sessions"
    d.mkdir()
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(d))
    # Reset session_manager singletons so AUTORUN_TEST_STATE_DIR is picked up.
    from autorun import session_manager
    session_manager._reset_for_testing()
    yield d
    session_manager._reset_for_testing()


def _sid(prefix: str = "cache-test") -> str:
    return f"{prefix}-{os.getpid()}-{int(time.time() * 1e6)}"


# === 1. parse_quantity ================================================

class TestParseQuantity:
    @pytest.mark.parametrize("s,expected", [
        ("50000", 50_000),
        ("50,000", 50_000),
        ("50_000", 50_000),
        ("50k", 50_000),
        ("50K", 50_000),
        (".5M", 500_000),
        ("0.5m", 500_000),
        ("1.5M", 1_500_000),
        ("2M", 2_000_000),
        ("200k", 200_000),
        ("  50k  ", 50_000),
    ])
    def test_tokens_ok(self, s, expected):
        from autorun.cache_guard import parse_quantity
        assert parse_quantity(s, expect="tokens") == expected

    @pytest.mark.parametrize("s,expected", [
        ("85%", 0.85),
        ("0.85", 0.85),
        ("100%", 1.0),
        ("1%", 0.01),
        ("  92%  ", 0.92),
        ("0.5", 0.5),
    ])
    def test_percent_ok(self, s, expected):
        from autorun.cache_guard import parse_quantity
        assert parse_quantity(s, expect="percent") == pytest.approx(expected)

    @pytest.mark.parametrize("s", ["perm", "permanent", "p", "PERM", "Permanent", "P"])
    def test_tokens_permanent_sentinel(self, s):
        """Accept the canonical `{perm, permanent, p}` set reused from scoped_allow.
        Case-insensitive. No speculative aliases (forever/inf/etc) — keeps the
        grammar identical to `/ar:ok`, `/ar:no`, `/ar:globalok`.
        """
        from autorun.cache_guard import parse_quantity, PERMANENT
        assert parse_quantity(s, expect="tokens") is PERMANENT

    @pytest.mark.parametrize("s", ["", "  ", "abc", "50xx", "12g", "-5", "1.2.3"])
    def test_bad_raises(self, s):
        from autorun.cache_guard import parse_quantity
        with pytest.raises(ValueError):
            parse_quantity(s, expect="tokens")

    def test_percent_out_of_range(self):
        from autorun.cache_guard import parse_quantity
        with pytest.raises(ValueError):
            parse_quantity("150%", expect="percent")
        with pytest.raises(ValueError):
            parse_quantity("1.5", expect="percent")


# === 2. FeatureToggle =================================================

class TestFeatureToggle:
    def test_cache_is_false_by_default(self, tmp_state_dir):
        from autorun.cache_guard import FeatureToggle
        sid = _sid()
        ft = FeatureToggle("cache", session_id=sid)
        assert ft.is_enabled() is False

    def test_enable_persists(self, tmp_state_dir):
        from autorun.cache_guard import FeatureToggle
        sid = _sid()
        FeatureToggle("cache", session_id=sid).enable()
        # A fresh instance with the same sid must see it enabled (durable via session_state).
        assert FeatureToggle("cache", session_id=sid).is_enabled() is True

    def test_disable_persists(self, tmp_state_dir):
        from autorun.cache_guard import FeatureToggle
        sid = _sid()
        ft = FeatureToggle("cache", session_id=sid)
        ft.enable()
        ft.disable()
        assert FeatureToggle("cache", session_id=sid).is_enabled() is False

    def test_temporary_enable_expires(self, tmp_state_dir):
        from autorun.cache_guard import FeatureToggle
        sid = _sid()
        ft = FeatureToggle("cache", session_id=sid)
        ft.enable(duration_seconds=0.05)  # 50ms
        assert ft.is_enabled() is True
        time.sleep(0.1)
        assert ft.is_enabled() is False

    def test_sessions_are_isolated(self, tmp_state_dir):
        from autorun.cache_guard import FeatureToggle
        a, b = _sid("a"), _sid("b")
        FeatureToggle("cache", session_id=a).enable()
        assert FeatureToggle("cache", session_id=b).is_enabled() is False


# === 3. _read_jsonl_tail =============================================

def _make_assistant_entry(
    *,
    ts: str,
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read: int = 0,
    cache_creation: int = 0,
) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
            },
        },
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


class TestReadJsonlTail:
    def test_finds_last_assistant_usage(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        p = tmp_path / "t.jsonl"
        _write_jsonl(p, [
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z"},
            _make_assistant_entry(ts="2026-01-01T00:00:01Z", cache_read=1000, cache_creation=500),
            {"type": "user", "timestamp": "2026-01-01T00:00:02Z"},
            _make_assistant_entry(ts="2026-01-01T00:00:03Z", cache_read=50000, cache_creation=100, input_tokens=200),
        ])
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is not None
        assert r.cache_read_tokens == 50000
        assert r.total_input_tokens == 200 + 50000 + 100
        assert 0.0 < r.cache_hit_ratio <= 1.0

    def test_bounded_reverse_read_on_large_file(self, tmp_path):
        """5 MB JSONL — ensure we read only the tail and still find the last assistant entry."""
        from autorun.cache_guard import _read_jsonl_tail
        p = tmp_path / "big.jsonl"
        filler = {"type": "user", "timestamp": "x", "pad": "A" * 900}
        # ~5000 filler lines × ~1 KB = ~5 MB; last line is the assistant entry we expect.
        entries = [filler] * 5000 + [_make_assistant_entry(ts="2026-01-01T00:00:99Z", cache_read=7777)]
        _write_jsonl(p, entries)
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is not None
        assert r.cache_read_tokens == 7777

    def test_no_assistant_entry_returns_none(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        p = tmp_path / "nope.jsonl"
        _write_jsonl(p, [{"type": "user", "timestamp": "x"}])
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is None

    def test_missing_file_returns_none(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        assert _read_jsonl_tail(str(tmp_path / "missing.jsonl"), max_bytes=64 * 1024, cli="claude") is None


# === 4. CacheGuard decision table ====================================

class _FakeUsage:
    """Stand-in for a UsageReading injected via CacheGuard._read_usage monkeypatch."""
    def __init__(self, **kw):
        self.cli = kw.get("cli", "claude")
        self.total_input_tokens = kw.get("total_input_tokens")
        self.cache_read_tokens = kw.get("cache_read_tokens")
        self.cache_hit_ratio = kw.get("cache_hit_ratio")
        self.cache_age_seconds = kw.get("cache_age_seconds")
        self.context_window_size = kw.get("context_window_size", 200_000)
        self.compaction_proximity = kw.get("compaction_proximity")
        self.rate_limit_5h = kw.get("rate_limit_5h")
        self.rate_limit_7d = kw.get("rate_limit_7d")
        self.observed_at = kw.get("observed_at", time.time())


def _guard(tmp_state_dir, threshold=None, usage=None, enabled=True):
    from autorun.cache_guard import CacheGuard, CacheThreshold, FeatureToggle
    sid = _sid()
    if enabled:
        FeatureToggle("cache", session_id=sid).enable()
    g = CacheGuard(session_id=sid, threshold=threshold or CacheThreshold())
    if usage is not None:
        g._read_usage = lambda _stdin: usage  # type: ignore
    else:
        g._read_usage = lambda _stdin: None  # type: ignore
    return g


class TestCacheGuardDecision:
    def test_disabled_allows(self, tmp_state_dir):
        g = _guard(tmp_state_dir, enabled=False, usage=_FakeUsage(cache_hit_ratio=0.01))
        assert g.on_pretooluse({}).is_block() is False

    def test_no_usage_info_allows_fail_open(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5),
                   usage=None)
        assert g.on_pretooluse({}).is_block() is False

    def test_under_threshold_allows(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5),
                   usage=_FakeUsage(cache_hit_ratio=0.9))
        assert g.on_pretooluse({}).is_block() is False

    def test_ratio_below_floor_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5),
                   usage=_FakeUsage(cache_hit_ratio=0.1))
        d = g.on_pretooluse({})
        assert d.is_block()
        assert "ratio" in d.message.lower()

    def test_cache_read_below_floor_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_read_tokens_min=50_000),
                   usage=_FakeUsage(cache_read_tokens=100))
        assert g.on_pretooluse({}).is_block()

    def test_age_above_ceiling_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_age_max_seconds=300),
                   usage=_FakeUsage(cache_age_seconds=500))
        assert g.on_pretooluse({}).is_block()

    def test_rate_limit_renders_distinct_message(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(rate_limit_5h_max=0.90),
                   usage=_FakeUsage(rate_limit_5h=0.94))
        d = g.on_pretooluse({})
        assert d.is_block()
        assert "rate-limit" in d.message.lower() or "rate limit" in d.message.lower()

    def test_grant_5m_allows(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold, grant_override
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5),
                   usage=_FakeUsage(cache_hit_ratio=0.1))
        grant_override(g.session_id, ttl_seconds=300, uses=None)
        assert g.on_pretooluse({}).is_block() is False

    def test_grant_n3_decrements(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold, grant_override
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5),
                   usage=_FakeUsage(cache_hit_ratio=0.1))
        grant_override(g.session_id, ttl_seconds=None, uses=3)
        for _ in range(3):
            assert g.on_pretooluse({}).is_block() is False
        # 4th call: blocked (use count + grace already exhausted if we wait)
        time.sleep(1.5)  # clear parallel-hook grace window
        assert g.on_pretooluse({}).is_block() is True

    def test_grant_perm_allows_until_axis_clears(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold, grant_override
        bad = _FakeUsage(cache_hit_ratio=0.1)
        good = _FakeUsage(cache_hit_ratio=0.9)
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5),
                   usage=bad)
        grant_override(g.session_id, ttl_seconds=None, uses=None, permanent=True)
        assert g.on_pretooluse({}).is_block() is False
        # When ratio recovers, the guard simply returns allow (axis not tripped).
        g._read_usage = lambda _stdin: good  # type: ignore
        assert g.on_pretooluse({}).is_block() is False


# === 5. Multiprocess safety ==========================================

def _mp_writer(state_dir: str, sid: str, n: int, tag: str) -> None:
    os.environ["AUTORUN_TEST_STATE_DIR"] = state_dir
    from autorun import session_manager
    session_manager._reset_for_testing()
    from autorun.cache_guard import FeatureToggle
    for i in range(n):
        FeatureToggle(f"cache-mp-{tag}-{i % 3}", session_id=sid).enable()


class TestCacheMultiprocess:
    def test_no_torn_writes_concurrent_same_session(self, tmp_state_dir):
        sid = _sid("mp")
        procs = [
            multiprocessing.Process(target=_mp_writer, args=(str(tmp_state_dir), sid, 20, f"tag{i}"))
            for i in range(4)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=20)
            assert p.exitcode == 0

        # After everyone has written, read back and assert all keys are present.
        from autorun import session_manager
        session_manager._reset_for_testing()
        from autorun.session_manager import session_state
        with session_state(sid) as st:
            keys = list(st.keys())
        # We expect many "cache-mp-*-enabled" keys; the exact set is less important
        # than "the read doesn't crash with a JSON parse error" (torn writes would cause that).
        assert any("cache-mp" in k for k in keys), f"no cache-mp keys present: {keys}"

    def test_grants_dont_leak_across_sessions(self, tmp_state_dir):
        from autorun.cache_guard import (CacheGuard, CacheThreshold,
                                         FeatureToggle, grant_override)
        sid_a, sid_b = _sid("A"), _sid("B")
        for s in (sid_a, sid_b):
            FeatureToggle("cache", session_id=s).enable()
        grant_override(sid_a, ttl_seconds=300, uses=None)

        bad = _FakeUsage(cache_hit_ratio=0.1)
        ga = CacheGuard(session_id=sid_a, threshold=CacheThreshold(cache_hit_ratio_min=0.5))
        gb = CacheGuard(session_id=sid_b, threshold=CacheThreshold(cache_hit_ratio_min=0.5))
        ga._read_usage = lambda _s: bad  # type: ignore
        gb._read_usage = lambda _s: bad  # type: ignore

        assert ga.on_pretooluse({}).is_block() is False  # grant active
        assert gb.on_pretooluse({}).is_block() is True   # no grant for B


# === 6. persist_statusline_snapshot (CLI tap) =========================

class TestStatuslineTap:
    def test_valid_json_persists(self, tmp_state_dir):
        from autorun.cache_guard import persist_statusline_snapshot
        from autorun.session_manager import session_state
        sid = _sid("tap")
        payload = {
            "session_id": sid,
            "model": {"id": "claude-opus-4-7", "display_name": "Opus"},
            "context_window": {
                "total_input_tokens": 120,
                "context_window_size": 200_000,
                "used_percentage": 0.06,
                "current_usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 15,
                    "cache_read_input_tokens": 80,
                },
            },
            "rate_limits": {"five_hour": {"used_percentage": 23.5}},
        }
        rc = persist_statusline_snapshot(io.StringIO(json.dumps(payload)))
        assert rc == 0
        with session_state(sid) as st:
            snap = st.get("cache/statusline_snapshot")
        assert snap is not None
        assert snap["context_window"]["current_usage"]["cache_read_input_tokens"] == 80

    def test_bad_json_fails_open(self, tmp_state_dir):
        from autorun.cache_guard import persist_statusline_snapshot
        assert persist_statusline_snapshot(io.StringIO("not json")) == 0


# === 7. Compaction-event dispatch ====================================

class TestCompactionDispatch:
    def test_precompact_flushes(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard
        sid = _sid("pc")
        g = CacheGuard(session_id=sid)
        d = g.on_compaction_event("PreCompact")
        # PreCompact is an advisory no-op that still returns a valid decision.
        assert d.is_block() is False

    def test_postcompact_invalidates_memo(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard
        from autorun.session_manager import session_state
        sid = _sid("pc2")
        with session_state(sid) as st:
            st["cache/last_usage"] = {"observed_at": time.time(), "cache_read_tokens": 1000}
        g = CacheGuard(session_id=sid)
        g.on_compaction_event("PostCompact")
        with session_state(sid) as st:
            assert "cache/last_usage" not in st

    def test_precompress_invalidates_on_gemini(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard
        from autorun.session_manager import session_state
        sid = _sid("pc3")
        with session_state(sid) as st:
            st["cache/last_usage"] = {"observed_at": time.time(), "cache_read_tokens": 1000}
        g = CacheGuard(session_id=sid)
        g.on_compaction_event("PreCompress")
        # PreCompress is advisory; implementation may flush or not. Whichever, it must not crash.
        # The authoritative invalidation point on Gemini is SessionStart.
        g.on_compaction_event("SessionStart")
        with session_state(sid) as st:
            assert "cache/last_usage" not in st

    def test_unknown_event_name_is_noop(self, tmp_state_dir):
        """A non-whitelisted event must not crash and must not touch the memo."""
        from autorun.cache_guard import CacheGuard
        from autorun.session_manager import session_state
        sid = _sid("evt-unknown")
        with session_state(sid) as st:
            st["cache/last_usage"] = {"observed_at": time.time(), "cache_read_tokens": 42}
        g = CacheGuard(session_id=sid)
        g.on_compaction_event("Notification")  # not in _INVALIDATING_EVENTS
        with session_state(sid) as st:
            assert st.get("cache/last_usage", {}).get("cache_read_tokens") == 42


# === 8. Edge cases (task #48) ========================================

class TestCombinedAxes:
    def test_multiple_axes_trip_single_block(self, tmp_state_dir):
        """When ratio AND age both exceed, exactly one block fires and mentions both."""
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(cache_hit_ratio_min=0.5, cache_age_max_seconds=300),
                   usage=_FakeUsage(cache_hit_ratio=0.1, cache_age_seconds=500))
        d = g.on_pretooluse({})
        assert d.is_block()
        msg = d.message.lower()
        assert "ratio" in msg
        assert "age" in msg

    def test_compaction_proximity_axis_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheThreshold
        g = _guard(tmp_state_dir,
                   threshold=CacheThreshold(compaction_used_max=0.90),
                   usage=_FakeUsage(compaction_proximity=0.95))
        d = g.on_pretooluse({})
        assert d.is_block()


class TestUsageExtractionSchemaVariants:
    """_extract_usage_dict must probe Anthropic, Gemini, and OpenAI variants."""

    def test_anthropic_message_usage(self):
        from autorun.cache_guard import _extract_usage_dict
        entry = {"type": "assistant",
                 "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 50}}}
        u = _extract_usage_dict(entry)
        assert u is not None
        assert u.get("input_tokens") == 100 or u.get("cache_read_input_tokens") == 50

    def test_gemini_usage_metadata(self):
        from autorun.cache_guard import _extract_usage_dict
        entry = {"type": "model",
                 "usageMetadata": {"promptTokenCount": 200, "cachedContentTokenCount": 80}}
        u = _extract_usage_dict(entry)
        assert u is not None


class TestGitignoreSkillVisible:
    """Regression for the .gitignore bug that hid plugins/autorun/skills/cache/."""

    def test_skill_file_present(self):
        from pathlib import Path
        skill = Path(__file__).resolve().parents[1] / "skills" / "cache" / "SKILL.md"
        assert skill.exists(), f"Cache skill must exist at {skill}"


class TestGeminiTemplateHasPreCompress:
    """Regression for Gemini template missing PreCompress — cache_guard's
    memo invalidation on Gemini compaction depends on this hook firing."""

    def test_template_declares_precompress(self):
        import json as _json
        from pathlib import Path
        tpl = (Path(__file__).resolve().parents[1]
               / "src" / "autorun" / "gemini_template" / "hooks" / "hooks.json")
        data = _json.loads(tpl.read_text(encoding="utf-8"))
        events = set(data.get("hooks", {}).keys())
        assert "PreCompress" in events, f"PreCompress missing from {tpl}: {events}"


class TestFeatureToggleGlobalFallback:
    def test_session_missing_falls_to_global(self, tmp_state_dir):
        """Session-level FeatureToggle with no override reads from global default."""
        from autorun.cache_guard import FeatureToggle
        sid = _sid("ft-fallback")
        # Enable at global scope
        FeatureToggle("fb", session_id="__global__").enable()
        # Session with no explicit setting should observe True via fallback.
        assert FeatureToggle("fb", session_id=sid).is_enabled() is True


class TestParseQuantityAdditional:
    @pytest.mark.parametrize("s", ["", "  ", "abc", "--50k", "50kk", "5.5.5M"])
    def test_more_bad_inputs(self, s):
        from autorun.cache_guard import parse_quantity
        with pytest.raises(ValueError):
            parse_quantity(s, expect="tokens")

    def test_percent_with_whitespace(self):
        from autorun.cache_guard import parse_quantity
        assert abs(parse_quantity("  85%  ", expect="percent") - 0.85) < 1e-9


# === 9. Regression fixes from audit pass (tasks #64-#69) =============

class TestLoadThresholdGlobalVisibility:
    """HIGH: `_load_threshold(_GLOBAL_SESSION_ID)` previously skipped both
    loop iterations when session_id == _GLOBAL_SESSION_ID — so
    `/ar:cache global status` always showed (none) even after a `global set`."""

    def test_global_threshold_visible_after_global_set(self, tmp_state_dir):
        from autorun.cache_guard import _load_threshold, _save_threshold, CacheThreshold, _GLOBAL_SESSION_ID
        _save_threshold(_GLOBAL_SESSION_ID, CacheThreshold(cache_hit_ratio_min=0.42))
        got = _load_threshold(_GLOBAL_SESSION_ID)
        assert got.cache_hit_ratio_min == 0.42, (
            f"/ar:cache global status must see the value written by /ar:cache global set — got {got}"
        )

    def test_session_overrides_global_field_by_field(self, tmp_state_dir):
        from autorun.cache_guard import _load_threshold, _save_threshold, CacheThreshold, _GLOBAL_SESSION_ID
        sid = _sid("th-fbf")
        _save_threshold(_GLOBAL_SESSION_ID, CacheThreshold(cache_hit_ratio_min=0.5, cache_age_max_seconds=600))
        _save_threshold(sid, CacheThreshold(cache_hit_ratio_min=0.7))  # overrides ratio only
        got = _load_threshold(sid)
        assert got.cache_hit_ratio_min == 0.7
        assert got.cache_age_max_seconds == 600


class TestClockSkew:
    """MEDIUM: `cache_age_seconds = max(0.0, now - t)` clamps future timestamps
    to 0, making the age axis never trip if the JSONL timestamp is ahead of
    wall-clock. Treat far-future timestamps as unknown instead of 0."""

    def test_future_timestamp_treated_as_unknown(self, tmp_path):
        import time as _t
        from autorun.cache_guard import _read_jsonl_tail
        future_ts = _t.gmtime(_t.time() + 3600)  # 1h in the future
        iso = _t.strftime("%Y-%m-%dT%H:%M:%SZ", future_ts)
        p = tmp_path / "skew.jsonl"
        _write_jsonl(p, [_make_assistant_entry(ts=iso, cache_read=100)])
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is not None
        # Future timestamps must NOT silently produce age=0 (which would make
        # age axis never trip). Either None (unknown) or a non-negative value
        # based on a sensible clamp.
        assert r.cache_age_seconds is None or r.cache_age_seconds >= 0.0
        # Specifically, must not falsely report "cache is fresh" — if we did
        # the old `max(0.0, now-t)` clamp, age_seconds would be 0.0 even for a
        # 1-hour-future timestamp. Assert we did not produce that.
        assert r.cache_age_seconds != 0.0 or r.cache_age_seconds is None


class TestHugeSingleJsonlEntry:
    """MEDIUM: _read_jsonl_tail must still find the last assistant message
    even when a single preceding entry is larger than the default tail window.
    """

    def test_finds_assistant_after_300kb_preceding_entry(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        p = tmp_path / "huge.jsonl"
        huge = {"type": "user", "timestamp": "t", "pad": "X" * 310_000}
        assistant = _make_assistant_entry(ts="2026-01-01T00:00:00Z", cache_read=13579)
        _write_jsonl(p, [huge, assistant])
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is not None, "Must find the assistant entry even after a 300KB preceding line"
        assert r.cache_read_tokens == 13579


class TestStaleOverrideGC:
    """MEDIUM: cache/overrides accumulated stale entries (ScopedAllow with
    expired TTL / zero uses) on SessionStart. Expired grants must be pruned
    lazily — same pattern as plugins.cleanup_expired_allows."""

    def test_expired_grants_pruned_on_sessionstart_cleanup(self, tmp_state_dir):
        from autorun.cache_guard import grant_override, _GRANT_KEY, _purge_expired_overrides
        from autorun.session_manager import session_state
        sid = _sid("gc-exp")
        # Grant with 1ms TTL → expire immediately.
        grant_override(sid, ttl_seconds=0.001, uses=None)
        time.sleep(0.05)
        # Plus one still-valid grant.
        grant_override(sid, ttl_seconds=300, uses=None)
        # Call the cleanup hook directly (bypassing @app.on plumbing).
        _purge_expired_overrides(sid)
        with session_state(sid) as st:
            remaining = st.get(_GRANT_KEY, [])
        assert len(remaining) == 1, f"Expected exactly one valid grant left, got {remaining}"


class TestPersistStatuslineBounded:
    """LOW: persist_statusline_snapshot reads stdin with json.load — a
    runaway statusline piping >100MB would OOM before fail-open. Bound the
    read size to keep memory flat."""

    def test_oversized_stdin_fails_open_without_oom(self):
        import io
        from autorun.cache_guard import persist_statusline_snapshot
        # ~300KB of junk — well over any legitimate statusline payload.
        # Function must return 0 (fail-open) without raising.
        huge = '{"session_id": "x", "pad": "' + ("A" * 300_000) + '"}'
        fp = io.StringIO(huge)
        rc = persist_statusline_snapshot(fp)
        assert rc == 0
