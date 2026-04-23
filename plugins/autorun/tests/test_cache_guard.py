"""Tests for /ar:cache — cache-pressure / cache-miss protection gate.

Covers:
  1.  parse_quantity — 50k, 50,000, 50_000, .5M, 85%, 0.85, perm, bad
  2.  Toggle — is_cache_enabled/set_cache_enabled, TTL revert, global fallback
  3.  _read_jsonl_tail — bounded reverse read, finds last assistant message.usage
  4.  CacheGuard.check() — full gate lifecycle via EventContext
  5.  Multiprocess safety — two concurrent writers under session_state filelock
  6.  persist_statusline_snapshot — CLI tap; bad JSON fails-open
  7.  Compaction-event dispatch — PreCompact/PostCompact/PreCompress invalidate memo
  8.  Edge cases — combined axes, schema variants, clock-skew, huge entries
  9.  Regression fixes — global threshold visibility, stale GC, oversized stdin
  10. PERMANENT sentinel — parse_quantity("perm") returns None
  11. cache_command dispatch table — all subcommands
  12. _cmd_set data-driven dispatch — all parsers, unknown axis
  13. _render_status — Gemini annotations, hints, override labels
  14. Toggle TTL revert-to-prior
  15. purge_stale_overrides public API
  16. E2E lifecycle: check(ctx) → ctx.deny() path
  17. SessionStart / compaction lifecycle
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
    from autorun import session_manager
    session_manager._reset_for_testing()
    yield d
    session_manager._reset_for_testing()


def _sid(prefix: str = "cache-test") -> str:
    return f"{prefix}-{os.getpid()}-{int(time.time() * 1e6)}"


def _make_ctx(session_id: str, cli_type: str = "claude") -> object:
    """Build a minimal EventContext for PreToolUse without a running daemon."""
    from autorun.core import EventContext
    return EventContext(
        session_id=session_id,
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": "git push"},
        cli_type=cli_type,
        transcript_path=None,
    )


class _FakeUsage:
    """Stand-in for UsageReading injected via CacheGuard._read_usage monkeypatch."""
    def __init__(self, **kw):
        self.cli = kw.get("cli", "claude")
        self.total_input_tokens = kw.get("total_input_tokens")
        self.cache_read_tokens = kw.get("cache_read_tokens")
        self.cache_creation_tokens = kw.get("cache_creation_tokens")
        self.cache_hit_ratio = kw.get("cache_hit_ratio")
        self.cache_age_seconds = kw.get("cache_age_seconds")
        self.context_window_size = kw.get("context_window_size", 200_000)
        self.compaction_proximity = kw.get("compaction_proximity")
        self.rate_limit_5h = kw.get("rate_limit_5h")
        self.rate_limit_7d = kw.get("rate_limit_7d")
        self.observed_at = kw.get("observed_at", time.time())


def _guard(tmp_state_dir, threshold=None, usage=None, enabled=True):
    """Create (guard, ctx) pair with monkeypatched _read_usage."""
    from autorun.cache_guard import CacheGuard, CacheGuardConfig, set_cache_enabled
    sid = _sid()
    if enabled:
        set_cache_enabled(sid, True)
    g = CacheGuard(session_id=sid, config=threshold or CacheGuardConfig())
    if usage is not None:
        g._read_usage = lambda _ctx: usage  # type: ignore[method-assign]
    else:
        g._read_usage = lambda _ctx: None   # type: ignore[method-assign]
    ctx = _make_ctx(sid)
    return g, ctx


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
        from autorun.cache_guard import parse_quantity, PERMANENT
        assert parse_quantity(s, expect="tokens") is PERMANENT  # is None

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


# === 2. Toggle =======================================================

class TestToggle:
    def test_cache_is_false_by_default(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled
        assert is_cache_enabled(_sid()) is False

    def test_enable_persists(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True)
        assert is_cache_enabled(sid) is True

    def test_disable_persists(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True)
        set_cache_enabled(sid, False)
        assert is_cache_enabled(sid) is False

    def test_temporary_enable_expires(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True, duration=0.05)
        assert is_cache_enabled(sid) is True
        time.sleep(0.1)
        assert is_cache_enabled(sid) is False

    def test_sessions_are_isolated(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled
        a, b = _sid("a"), _sid("b")
        set_cache_enabled(a, True)
        assert is_cache_enabled(b) is False

    def test_global_fallback(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled
        sid = _sid("ft-fallback")
        set_cache_enabled("__global__", True)
        assert is_cache_enabled(sid) is True

    def test_session_override_wins_over_global(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled
        sid = _sid("override-wins")
        set_cache_enabled("__global__", True)
        set_cache_enabled(sid, False)
        assert is_cache_enabled(sid) is False


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
        from autorun.cache_guard import _read_jsonl_tail
        p = tmp_path / "big.jsonl"
        filler = {"type": "user", "timestamp": "x", "pad": "A" * 900}
        entries = [filler] * 5000 + [_make_assistant_entry(ts="2026-01-01T00:00:99Z", cache_read=7777)]
        _write_jsonl(p, entries)
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is not None
        assert r.cache_read_tokens == 7777

    def test_no_assistant_entry_returns_none(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        p = tmp_path / "nope.jsonl"
        _write_jsonl(p, [{"type": "user", "timestamp": "x"}])
        assert _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude") is None

    def test_missing_file_returns_none(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        assert _read_jsonl_tail(str(tmp_path / "missing.jsonl"), max_bytes=64 * 1024, cli="claude") is None


# === 4. CacheGuard.check() decision table ============================

class TestCacheGuardDecision:
    def test_disabled_allows(self, tmp_state_dir):
        g, ctx = _guard(tmp_state_dir, enabled=False, usage=_FakeUsage(cache_hit_ratio=0.01))
        assert g.check(ctx) is None

    def test_no_usage_info_allows_fail_open(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir, threshold=CacheGuardConfig(cache_hit_ratio_min=0.5), usage=None)
        assert g.check(ctx) is None

    def test_under_threshold_allows(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_hit_ratio_min=0.5),
                        usage=_FakeUsage(cache_hit_ratio=0.9))
        assert g.check(ctx) is None

    def test_ratio_below_floor_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_hit_ratio_min=0.5),
                        usage=_FakeUsage(cache_hit_ratio=0.1))
        result = g.check(ctx)
        assert result is not None
        assert "ratio" in str(result).lower()

    def test_cache_read_below_floor_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_read_tokens_min=50_000),
                        usage=_FakeUsage(cache_read_tokens=100))
        assert g.check(ctx) is not None

    def test_age_above_ceiling_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_age_max_seconds=300),
                        usage=_FakeUsage(cache_age_seconds=500))
        assert g.check(ctx) is not None

    def test_rate_limit_renders_distinct_message(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(rate_limit_5h_max=0.90),
                        usage=_FakeUsage(rate_limit_5h=0.94))
        result = g.check(ctx)
        assert result is not None
        assert "rate" in str(result).lower()

    def test_grant_5m_allows(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig, grant_override
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_hit_ratio_min=0.5),
                        usage=_FakeUsage(cache_hit_ratio=0.1))
        grant_override(g.session_id, ttl_seconds=300, uses=None)
        assert g.check(ctx) is None

    def test_grant_n3_decrements(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig, grant_override
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_hit_ratio_min=0.5),
                        usage=_FakeUsage(cache_hit_ratio=0.1))
        grant_override(g.session_id, ttl_seconds=None, uses=3)
        for _ in range(3):
            assert g.check(ctx) is None
        time.sleep(1.5)  # clear parallel-hook grace window
        assert g.check(ctx) is not None

    def test_grant_perm_allows_until_axis_clears(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig, grant_override
        bad = _FakeUsage(cache_hit_ratio=0.1)
        good = _FakeUsage(cache_hit_ratio=0.9)
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_hit_ratio_min=0.5),
                        usage=bad)
        grant_override(g.session_id, ttl_seconds=None, uses=None, permanent=True)
        assert g.check(ctx) is None
        g._read_usage = lambda _ctx: good  # type: ignore[method-assign]
        assert g.check(ctx) is None


# === 5. Multiprocess safety ==========================================

def _mp_writer(state_dir: str, sid: str, n: int, tag: str) -> None:
    os.environ["AUTORUN_TEST_STATE_DIR"] = state_dir
    from autorun import session_manager
    session_manager._reset_for_testing()
    from autorun.session_manager import session_state
    for i in range(n):
        with session_state(sid) as st:
            st[f"cache-mp-{tag}-{i % 3}"] = True


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

        from autorun import session_manager
        session_manager._reset_for_testing()
        from autorun.session_manager import session_state
        with session_state(sid) as st:
            keys = list(st.keys())
        assert any("cache-mp" in k for k in keys), f"no cache-mp keys present: {keys}"

    def test_grants_dont_leak_across_sessions(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard, CacheGuardConfig, set_cache_enabled, grant_override
        sid_a, sid_b = _sid("A"), _sid("B")
        for s in (sid_a, sid_b):
            set_cache_enabled(s, True)
        grant_override(sid_a, ttl_seconds=300, uses=None)

        bad = _FakeUsage(cache_hit_ratio=0.1)
        ga = CacheGuard(session_id=sid_a, config=CacheGuardConfig(cache_hit_ratio_min=0.5))
        gb = CacheGuard(session_id=sid_b, config=CacheGuardConfig(cache_hit_ratio_min=0.5))
        ctx_a, ctx_b = _make_ctx(sid_a), _make_ctx(sid_b)
        ga._read_usage = lambda _ctx: bad   # type: ignore[method-assign]
        gb._read_usage = lambda _ctx: bad   # type: ignore[method-assign]

        assert ga.check(ctx_a) is None      # grant active
        assert gb.check(ctx_b) is not None  # no grant for B


# === 6. persist_statusline_snapshot ==================================

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
    def test_precompact_is_noop_returns_none(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard
        sid = _sid("pc")
        g = CacheGuard(session_id=sid)
        result = g.on_compaction_event("PreCompact")
        assert result is None  # void — no block decision

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
        g.on_compaction_event("SessionStart")
        with session_state(sid) as st:
            assert "cache/last_usage" not in st

    def test_unknown_event_name_is_noop(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard
        from autorun.session_manager import session_state
        sid = _sid("evt-unknown")
        with session_state(sid) as st:
            st["cache/last_usage"] = {"observed_at": time.time(), "cache_read_tokens": 42}
        g = CacheGuard(session_id=sid)
        g.on_compaction_event("Notification")
        with session_state(sid) as st:
            assert st.get("cache/last_usage", {}).get("cache_read_tokens") == 42


# === 8. Edge cases ===================================================

class TestCombinedAxes:
    def test_multiple_axes_trip_single_block(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(cache_hit_ratio_min=0.5, cache_age_max_seconds=300),
                        usage=_FakeUsage(cache_hit_ratio=0.1, cache_age_seconds=500))
        result = g.check(ctx)
        assert result is not None
        s = str(result).lower()
        assert "ratio" in s
        assert "age" in s

    def test_compaction_proximity_axis_blocks(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig
        g, ctx = _guard(tmp_state_dir,
                        threshold=CacheGuardConfig(compaction_used_max=0.90),
                        usage=_FakeUsage(compaction_proximity=0.95))
        assert g.check(ctx) is not None


class TestUsageExtractionSchemaVariants:
    def test_anthropic_message_usage(self):
        from autorun.cache_guard import _extract_usage_dict
        entry = {"type": "assistant",
                 "message": {"usage": {"input_tokens": 100, "cache_read_input_tokens": 50}}}
        u = _extract_usage_dict(entry)
        assert u is not None

    def test_gemini_usage_metadata(self):
        from autorun.cache_guard import _extract_usage_dict
        entry = {"type": "model",
                 "usageMetadata": {"promptTokenCount": 200, "cachedContentTokenCount": 80}}
        u = _extract_usage_dict(entry)
        assert u is not None


class TestGitignoreSkillVisible:
    def test_skill_file_present(self):
        skill = Path(__file__).resolve().parents[1] / "skills" / "cache" / "SKILL.md"
        assert skill.exists(), f"Cache skill must exist at {skill}"


class TestGeminiTemplateHasPreCompress:
    def test_template_declares_precompress(self):
        tpl = (Path(__file__).resolve().parents[1]
               / "src" / "autorun" / "gemini_template" / "hooks" / "hooks.json")
        data = json.loads(tpl.read_text(encoding="utf-8"))
        events = set(data.get("hooks", {}).keys())
        assert "PreCompress" in events, f"PreCompress missing from {tpl}: {events}"


# === 9. Regression fixes =============================================

class TestLoadThresholdGlobalVisibility:
    def test_global_threshold_visible_after_global_set(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig, _GLOBAL_SESSION_ID
        CacheGuardConfig(cache_hit_ratio_min=0.42).save(_GLOBAL_SESSION_ID)
        got = CacheGuardConfig.load(_GLOBAL_SESSION_ID)
        assert got.cache_hit_ratio_min == pytest.approx(0.42)

    def test_session_overrides_global_field_by_field(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuardConfig, _GLOBAL_SESSION_ID
        sid = _sid("th-fbf")
        CacheGuardConfig(cache_hit_ratio_min=0.5, cache_age_max_seconds=600).save(_GLOBAL_SESSION_ID)
        CacheGuardConfig(cache_hit_ratio_min=0.7).save(sid)
        got = CacheGuardConfig.load(sid)
        assert got.cache_hit_ratio_min == pytest.approx(0.7)
        assert got.cache_age_max_seconds == pytest.approx(600)


class TestClockSkew:
    def test_future_timestamp_treated_as_unknown(self, tmp_path):
        from autorun.cache_guard import _read_jsonl_tail
        future_ts = time.gmtime(time.time() + 3600)
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", future_ts)
        p = tmp_path / "skew.jsonl"
        _write_jsonl(p, [_make_assistant_entry(ts=iso, cache_read=100)])
        r = _read_jsonl_tail(str(p), max_bytes=64 * 1024, cli="claude")
        assert r is not None
        # Far-future timestamps must not collapse to age=0 (silent under-enforcement).
        assert r.cache_age_seconds is None or r.cache_age_seconds >= 0.0
        assert r.cache_age_seconds != 0.0 or r.cache_age_seconds is None


class TestHugeSingleJsonlEntry:
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
    def test_expired_grants_pruned_on_sessionstart_cleanup(self, tmp_state_dir):
        from autorun.cache_guard import grant_override, _GRANT_KEY, purge_stale_overrides
        from autorun.session_manager import session_state
        sid = _sid("gc-exp")
        grant_override(sid, ttl_seconds=0.001, uses=None)
        time.sleep(0.05)
        grant_override(sid, ttl_seconds=300, uses=None)
        purge_stale_overrides(sid)
        with session_state(sid) as st:
            remaining = st.get(_GRANT_KEY, [])
        assert len(remaining) == 1


class TestPersistStatuslineBounded:
    def test_oversized_stdin_fails_open_without_oom(self):
        from autorun.cache_guard import persist_statusline_snapshot
        huge = '{"session_id": "x", "pad": "' + ("A" * 300_000) + '"}'
        assert persist_statusline_snapshot(io.StringIO(huge)) == 0


# === 10. PERMANENT sentinel ==========================================

class TestPermanentSentinel:
    def test_permanent_is_none(self):
        from autorun.cache_guard import PERMANENT
        assert PERMANENT is None

    @pytest.mark.parametrize("s", ["perm", "permanent", "p", "PERM", "P"])
    def test_parse_quantity_perm_returns_none(self, s):
        from autorun.cache_guard import parse_quantity, PERMANENT
        assert parse_quantity(s, expect="tokens") is PERMANENT


# === 11. cache_command dispatch table ================================

class TestCacheCommandDispatch:
    def test_unknown_subcommand_error(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        out = cache_command("foobar", _sid())
        assert "unknown subcommand" in out and "foobar" in out

    def test_empty_shows_status(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        out = cache_command("", _sid())
        assert "ar:cache" in out and "disabled" in out

    def test_status_alias(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        sid = _sid()
        assert cache_command("status", sid) == cache_command("", sid)

    def test_global_prefix_delegates(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, is_cache_enabled, _GLOBAL_SESSION_ID
        out = cache_command("global on", _sid())
        assert "[global]" in out
        assert is_cache_enabled(_GLOBAL_SESSION_ID)

    def test_on_enables_session(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, is_cache_enabled
        sid = _sid()
        cache_command("on", sid)
        assert is_cache_enabled(sid)

    def test_off_disables_session(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, is_cache_enabled, set_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True)
        cache_command("off", sid)
        assert not is_cache_enabled(sid)

    def test_on_with_bad_duration_returns_error(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        out = cache_command("on xyz123", _sid())
        assert "cannot parse duration" in out

    def test_on_permanent_enables_without_ttl(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, is_cache_enabled
        sid = _sid()
        cache_command("on perm", sid)
        assert is_cache_enabled(sid)

    def test_on_with_duration_string(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, is_cache_enabled
        sid = _sid()
        out = cache_command("on 5m", sid)
        assert is_cache_enabled(sid) and "5m" in out

    def test_ok_grants_single_use_by_default(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, _GRANT_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        cache_command("ok", sid)
        with session_state(sid) as st:
            grants = st.get(_GRANT_KEY, [])
        assert len(grants) == 1
        assert grants[0]["remaining_uses"] == 1

    def test_ok_permanent(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, _GRANT_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        cache_command("ok perm", sid)
        with session_state(sid) as st:
            grants = st.get(_GRANT_KEY, [])
        assert grants[0].get("remaining_uses") is None

    def test_no_clears_overrides(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, _GRANT_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        cache_command("ok", sid)
        cache_command("no", sid)
        with session_state(sid) as st:
            assert st.get(_GRANT_KEY) is None


# === 12. _cmd_set data-driven dispatch ===============================

class TestCmdSetDataDriven:
    def test_set_ratio(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, CacheGuardConfig
        sid = _sid()
        out = cache_command("set ratio 75%", sid)
        assert "updated" in out
        assert CacheGuardConfig.load(sid).cache_hit_ratio_min == pytest.approx(0.75)

    def test_set_read_tokens(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, CacheGuardConfig
        sid = _sid()
        cache_command("set read 50k", sid)
        assert CacheGuardConfig.load(sid).cache_read_tokens_min == 50_000

    def test_set_read_perm_disables_axis(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, CacheGuardConfig
        sid = _sid()
        cache_command("set read 50k", sid)
        cache_command("set read perm", sid)
        assert CacheGuardConfig.load(sid).cache_read_tokens_min is None

    def test_set_age_duration(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, CacheGuardConfig
        sid = _sid()
        cache_command("set age 5m", sid)
        assert CacheGuardConfig.load(sid).cache_age_max_seconds == pytest.approx(300.0)

    def test_set_age_bad_duration(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        out = cache_command("set age xyz", _sid())
        assert "cannot parse duration" in out

    def test_set_full_compaction(self, tmp_state_dir):
        from autorun.cache_guard import cache_command, CacheGuardConfig
        sid = _sid()
        cache_command("set full 90%", sid)
        assert CacheGuardConfig.load(sid).compaction_used_max == pytest.approx(0.90)

    def test_set_unknown_axis_error(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        out = cache_command("set ratelimit 50%", _sid())
        assert "unknown axis" in out and "ratelimit" in out

    def test_set_missing_value_error(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        out = cache_command("set ratio", _sid())
        assert "usage" in out.lower()

    def test_set_rate_limit_axis_not_user_settable(self):
        from autorun.cache_guard import _AXES_SET_MAP
        assert "rate_limit_5h_max" not in _AXES_SET_MAP
        assert "rate_limit_7d_max" not in _AXES_SET_MAP
        assert "" not in _AXES_SET_MAP


# === 13. _render_status ==============================================

class TestRenderStatusGemini:
    def test_gemini_fail_open_annotation(self, tmp_state_dir, monkeypatch):
        from autorun.cache_guard import cache_command
        from autorun import cache_guard
        monkeypatch.setattr(cache_guard, "detect_cli_type", lambda *a, **kw: "gemini")
        sid = _sid()
        cache_command("set ratio 70%", sid)
        out = cache_command("status", sid)
        assert "[fail-open on Gemini]" in out

    def test_claude_no_fail_open_annotation(self, tmp_state_dir, monkeypatch):
        from autorun.cache_guard import cache_command
        from autorun import cache_guard
        monkeypatch.setattr(cache_guard, "detect_cli_type", lambda *a, **kw: "claude")
        sid = _sid()
        cache_command("set ratio 70%", sid)
        out = cache_command("status", sid)
        assert "[fail-open on Gemini]" not in out

    def test_status_shows_set_hint_for_settable_axes(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        sid = _sid()
        cache_command("set ratio 70%", sid)
        assert "/ar:cache set ratio" in cache_command("status", sid)

    def test_status_shows_override_status_label(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        sid = _sid()
        cache_command("ok 3", sid)
        assert "3 uses" in cache_command("status", sid)

    def test_status_none_configured_message(self, tmp_state_dir):
        from autorun.cache_guard import cache_command
        assert "none configured" in cache_command("status", _sid())


# === 14. Toggle TTL revert-to-prior ==================================

class TestToggleTTLRevert:
    def test_ttl_revert_to_prior_false(self, tmp_state_dir):
        from autorun.cache_guard import set_cache_enabled, is_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True, duration=0.01)
        assert is_cache_enabled(sid)
        time.sleep(0.05)
        assert not is_cache_enabled(sid)

    def test_ttl_revert_to_prior_true(self, tmp_state_dir):
        from autorun.cache_guard import set_cache_enabled, is_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True)
        set_cache_enabled(sid, False, duration=0.01)
        assert not is_cache_enabled(sid)
        time.sleep(0.05)
        assert is_cache_enabled(sid)


# === 15. purge_stale_overrides public API ============================

class TestPurgeStaleOverridesPublicAPI:
    def test_purge_removes_expired_ttl(self, tmp_state_dir):
        from autorun.cache_guard import grant_override, purge_stale_overrides, _GRANT_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        grant_override(sid, ttl_seconds=0.001)
        grant_override(sid, ttl_seconds=300)
        time.sleep(0.05)
        purge_stale_overrides(sid)
        with session_state(sid) as st:
            assert len(st.get(_GRANT_KEY, [])) == 1

    def test_purge_removes_exhausted_uses(self, tmp_state_dir):
        from autorun.cache_guard import grant_override, purge_stale_overrides, _GRANT_KEY
        from autorun.cache_guard import _consume_by_call_id
        from autorun.scoped_allow import fingerprint_call
        from autorun.session_manager import session_state
        sid = _sid()
        grant_override(sid, uses=1)
        call_id = fingerprint_call(sid, "Bash", "ls")
        _consume_by_call_id(sid, call_id)
        time.sleep(1.1)  # past _PARALLEL_GRACE_SECONDS
        purge_stale_overrides(sid)
        with session_state(sid) as st:
            assert len(st.get(_GRANT_KEY, [])) == 0

    def test_purge_noop_when_all_valid(self, tmp_state_dir):
        from autorun.cache_guard import grant_override, purge_stale_overrides, _GRANT_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        grant_override(sid, ttl_seconds=300)
        grant_override(sid, ttl_seconds=300)
        purge_stale_overrides(sid)
        with session_state(sid) as st:
            assert len(st.get(_GRANT_KEY, [])) == 2


# === 16. E2E lifecycle: check(ctx) → ctx.deny() path =================

class TestCheckCtxE2EPath:
    def _guard_with_usage(self, tmp_state_dir, session_id, usage):
        from autorun.cache_guard import CacheGuard, CacheGuardConfig, set_cache_enabled
        set_cache_enabled(session_id, True)
        CacheGuardConfig(cache_hit_ratio_min=0.5).save(session_id)
        guard = CacheGuard.from_ctx(_make_ctx(session_id))
        guard._read_usage = lambda _ctx: usage  # type: ignore[method-assign]
        return guard

    def test_check_allows_when_disabled(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard, CacheGuardConfig
        sid = _sid()
        CacheGuardConfig(cache_hit_ratio_min=0.5).save(sid)
        guard = CacheGuard.from_ctx(_make_ctx(sid))
        guard._read_usage = lambda _ctx: _FakeUsage(cache_hit_ratio=0.01)  # type: ignore
        assert guard.check(_make_ctx(sid)) is None

    def test_check_blocks_and_returns_deny_dict(self, tmp_state_dir):
        sid = _sid()
        guard = self._guard_with_usage(tmp_state_dir, sid, _FakeUsage(cache_hit_ratio=0.01))
        result = guard.check(_make_ctx(sid))
        assert result is not None and isinstance(result, dict)

    def test_check_allows_when_axis_not_tripped(self, tmp_state_dir):
        sid = _sid()
        guard = self._guard_with_usage(tmp_state_dir, sid, _FakeUsage(cache_hit_ratio=0.99))
        assert guard.check(_make_ctx(sid)) is None

    def test_check_allows_when_no_usage_data(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard, CacheGuardConfig, set_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True)
        CacheGuardConfig(cache_hit_ratio_min=0.5).save(sid)
        guard = CacheGuard.from_ctx(_make_ctx(sid))
        guard._read_usage = lambda _ctx: None   # type: ignore[method-assign]
        assert guard.check(_make_ctx(sid)) is None

    def test_check_allows_after_override(self, tmp_state_dir):
        from autorun.cache_guard import grant_override
        sid = _sid()
        guard = self._guard_with_usage(tmp_state_dir, sid, _FakeUsage(cache_hit_ratio=0.01))
        grant_override(sid, uses=1)
        assert guard.check(_make_ctx(sid)) is None

    def test_check_blocks_after_override_exhausted(self, tmp_state_dir):
        from autorun.cache_guard import grant_override
        sid = _sid()
        guard = self._guard_with_usage(tmp_state_dir, sid, _FakeUsage(cache_hit_ratio=0.01))
        grant_override(sid, uses=1)
        ctx = _make_ctx(sid)
        guard.check(ctx)
        time.sleep(1.1)
        assert guard.check(ctx) is not None

    def test_from_ctx_vs_from_session_same_result(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard, CacheGuardConfig, set_cache_enabled
        sid = _sid()
        set_cache_enabled(sid, True)
        CacheGuardConfig(cache_hit_ratio_min=0.5).save(sid)
        g1 = CacheGuard.from_ctx(_make_ctx(sid))
        g2 = CacheGuard.from_session(session_id=sid)
        assert g1.session_id == g2.session_id
        assert g1.config.cache_hit_ratio_min == g2.config.cache_hit_ratio_min


# === 17. SessionStart / compaction lifecycle =========================

class TestSessionLifecycle:
    def test_sessionstart_purges_stale_cache_overrides(self, tmp_state_dir):
        from autorun.cache_guard import grant_override, purge_stale_overrides, _GRANT_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        grant_override(sid, ttl_seconds=0.001)
        grant_override(sid, ttl_seconds=300)
        time.sleep(0.05)
        purge_stale_overrides(sid)
        with session_state(sid) as st:
            assert len(st.get(_GRANT_KEY, [])) == 1

    def test_compaction_invalidates_usage_memo(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard, _LAST_USAGE_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        with session_state(sid) as st:
            st[_LAST_USAGE_KEY] = {"cache_hit_ratio": 0.9, "observed_at": time.time()}
        CacheGuard.from_session(session_id=sid).on_compaction_event("PreCompact")
        with session_state(sid) as st:
            assert st.get(_LAST_USAGE_KEY) is None

    def test_compaction_noop_for_unknown_events(self, tmp_state_dir):
        from autorun.cache_guard import CacheGuard, _LAST_USAGE_KEY
        from autorun.session_manager import session_state
        sid = _sid()
        with session_state(sid) as st:
            st[_LAST_USAGE_KEY] = {"cache_hit_ratio": 0.9, "observed_at": time.time()}
        CacheGuard.from_session(session_id=sid).on_compaction_event("SomeRandomEvent")
        with session_state(sid) as st:
            assert st.get(_LAST_USAGE_KEY) is not None

    def test_global_scope_isolation(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled, _GLOBAL_SESSION_ID
        sid = _sid()
        set_cache_enabled(sid, True)
        assert not is_cache_enabled(_GLOBAL_SESSION_ID)

    def test_global_fallback_for_toggle(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled, _GLOBAL_SESSION_ID
        sid = _sid()
        set_cache_enabled(_GLOBAL_SESSION_ID, True)
        assert is_cache_enabled(sid)

    def test_session_override_wins_over_global(self, tmp_state_dir):
        from autorun.cache_guard import is_cache_enabled, set_cache_enabled, _GLOBAL_SESSION_ID
        sid = _sid()
        set_cache_enabled(_GLOBAL_SESSION_ID, True)
        set_cache_enabled(sid, False)
        assert not is_cache_enabled(sid)


# === Additional parse_quantity edge cases ============================

class TestParseQuantityAdditional:
    @pytest.mark.parametrize("s", ["", "  ", "abc", "--50k", "50kk", "5.5.5M"])
    def test_more_bad_inputs(self, s):
        from autorun.cache_guard import parse_quantity
        with pytest.raises(ValueError):
            parse_quantity(s, expect="tokens")

    def test_percent_with_whitespace(self):
        from autorun.cache_guard import parse_quantity
        assert parse_quantity("  85%  ", expect="percent") == pytest.approx(0.85)
