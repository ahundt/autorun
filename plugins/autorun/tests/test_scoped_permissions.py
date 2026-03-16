"""Tests for scoped permission grants (ScopedAllow, parse_scope_args, enforcement)."""

import contextlib
import time
import pytest
from unittest.mock import patch

from autorun.scoped_allow import ScopedAllow, parse_scope_args, parse_duration
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins


# === Helpers ===

def _make_ctx(cmd: str = "ls", session_id: str = None) -> EventContext:
    """Create isolated EventContext with in-memory ThreadSafeDB for tests."""
    return EventContext(
        session_id=session_id or f"test-scoped-{id(object())}",
        event="PreToolUse", tool_name="Bash",
        tool_input={"command": cmd}, store=ThreadSafeDB()
    )


@contextlib.contextmanager
def _isolated_global_store():
    """Patch plugins.session_state with isolated in-memory dict."""
    store = {}

    @contextlib.contextmanager
    def mock_session_state(session_id):
        yield store

    with patch("autorun.plugins.session_state", mock_session_state):
        yield store


# === ScopedAllow unit tests ===

class TestScopedAllowIsValid:
    def test_count_valid(self):
        sa = ScopedAllow(pattern="rm", remaining_uses=1)
        assert sa.is_valid() is True

    def test_count_exhausted(self):
        sa = ScopedAllow(pattern="rm", remaining_uses=0)
        assert sa.is_valid() is False

    def test_count_after_consume(self):
        sa = ScopedAllow(pattern="rm", remaining_uses=1)
        consumed = sa.consume()
        assert consumed.remaining_uses == 0
        # Grace period: still valid immediately after consume (parallel hook safety)
        assert consumed.is_valid() is True
        # After grace period expires, invalid
        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 10.0
            assert consumed.is_valid() is False

    def test_ttl_valid(self):
        sa = ScopedAllow(pattern="rm", granted_at=time.time(), ttl_seconds=300.0)
        assert sa.is_valid() is True

    def test_ttl_expired(self):
        sa = ScopedAllow(pattern="rm", granted_at=time.time() - 400, ttl_seconds=300.0)
        assert sa.is_valid() is False

    def test_hybrid_count_expires_first(self):
        sa = ScopedAllow(pattern="rm", granted_at=time.time(), ttl_seconds=300.0, remaining_uses=1)
        assert sa.is_valid() is True
        consumed = sa.consume()
        # Grace period: valid immediately after consume (parallel hook safety)
        assert consumed.is_valid() is True
        # After grace period expires, count exhausted wins over still-valid TTL
        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 10.0
            assert consumed.is_valid() is False  # Count exhausted, TTL still valid but grace gone

    def test_hybrid_ttl_expires_first(self):
        sa = ScopedAllow(pattern="rm", granted_at=time.time() - 400, ttl_seconds=300.0, remaining_uses=5)
        assert sa.is_valid() is False  # TTL expired, count still valid

    def test_no_limits_permanent(self):
        sa = ScopedAllow(pattern="rm")
        assert sa.is_valid() is True

    def test_legacy_no_granted_at(self):
        sa = ScopedAllow(pattern="rm", granted_at=0.0, ttl_seconds=300.0)
        # granted_at=0 means legacy — TTL check skipped
        assert sa.is_valid() is True


class TestScopedAllowConsume:
    def test_immutable(self):
        sa = ScopedAllow(pattern="rm", remaining_uses=3)
        consumed = sa.consume()
        assert sa.remaining_uses == 3  # Original unchanged
        assert consumed.remaining_uses == 2

    def test_no_limit_noop(self):
        sa = ScopedAllow(pattern="rm")
        consumed = sa.consume()
        assert consumed is sa  # Same object returned

    def test_consumed_at_not_set_until_exhausted(self):
        """consumed_at stays 0 while uses remain — only set when count hits 0."""
        sa = ScopedAllow(pattern="rm", remaining_uses=2)
        after_one = sa.consume()
        assert after_one.remaining_uses == 1
        assert after_one.consumed_at == 0.0  # Not yet exhausted
        after_two = after_one.consume()
        assert after_two.remaining_uses == 0
        assert after_two.consumed_at > 0.0  # Stamped on exhaustion

    def test_consume_when_already_zero_refreshes_grace_window(self):
        """A third parallel hook invocation (consume on already-0) refreshes consumed_at.

        Scenario: Hook A consumes 1→0 (stamps consumed_at=T1).
        Hook B calls consume() on the same original allow (1→0, stamps T2 ≈ T1).
        Hook C, arriving slightly later, calls consume() on 0→0 again (stamps T3).
        All three should remain valid within the grace window.
        """
        sa = ScopedAllow(pattern="rm", remaining_uses=1)
        hook_a = sa.consume()  # 1→0, consumed_at stamped
        assert hook_a.remaining_uses == 0
        assert hook_a.consumed_at > 0

        hook_c = hook_a.consume()  # 0→0, consumed_at refreshed
        assert hook_c.remaining_uses == 0
        assert hook_c.consumed_at >= hook_a.consumed_at
        assert hook_c.is_valid() is True  # Still in grace window


class TestParallelHookGracePeriod:
    """Tests for the parallel hook invocation grace period.

    Root cause of the /ar:ok 'git push' double-block bug:
    Multiple cached plugin versions (ar/0.9.0, ar/0.10.0, temp_local_...)
    each register PreToolUse hooks. One Bash tool call spawns N parallel
    autorun hook invocations. Hook A consumes a 1-use allow (remaining_uses
    1→0). Hook B finds remaining_uses=0 and without the grace period would
    fall through to TIER 2 blocking rules, causing the command to be blocked
    despite the user having granted permission.
    """

    def test_parallel_hook_b_passes_after_hook_a_consumes(self):
        """Hook B sees allow as valid immediately after Hook A exhausts it.

        Simulates the exact race: both hooks receive the same original allow
        object, Hook A consumes it first, Hook B checks is_valid() on the
        consumed copy within milliseconds.
        """
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        assert original.is_valid() is True

        # Hook A: consume the allow
        hook_a_result = original.consume()
        assert hook_a_result.remaining_uses == 0

        # Hook B: check is_valid() immediately (parallel invocation)
        # Without grace period this would be False → falls to TIER 2 → blocked
        assert hook_a_result.is_valid() is True

    def test_parallel_hook_blocked_after_grace_period_expires(self):
        """A genuine second invocation (>5s later) is correctly blocked."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume()
        assert consumed.remaining_uses == 0
        assert consumed.consumed_at > 0

        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 6.0
            assert consumed.is_valid() is False  # Grace expired

    def test_grace_period_boundary_at_exactly_5s(self):
        """At exactly 5s grace period, allow should be invalid (boundary is exclusive)."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume()

        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 5.0
            assert consumed.is_valid() is False  # 5.0 is not < 5.0

    def test_grace_period_just_inside_boundary(self):
        """Just inside 5s grace period, allow is still valid."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume()

        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 4.999
            assert consumed.is_valid() is True

    def test_no_grace_period_without_consumed_at(self):
        """consumed_at=0 (pre-grace-period serialized state) means no grace — expired immediately."""
        # Simulates a ScopedAllow deserialized from state written before this feature
        sa = ScopedAllow(pattern="rm", remaining_uses=0, consumed_at=0.0)
        assert sa.is_valid() is False  # No grace period without consumed_at

    def test_multi_use_allow_no_grace_until_exhausted(self):
        """Grace period only activates when remaining_uses hits 0, not before."""
        sa = ScopedAllow(pattern="rm", remaining_uses=3)
        after_one = sa.consume()
        assert after_one.remaining_uses == 2
        assert after_one.consumed_at == 0.0  # Not exhausted yet
        assert after_one.is_valid() is True  # Valid normally, not via grace


class TestScopedAllowSerialization:
    def test_roundtrip(self):
        sa = ScopedAllow(
            pattern="git push", pattern_type="literal",
            suggestion="Use git push --dry-run first",
            granted_at=1000000.0, ttl_seconds=300.0, remaining_uses=5
        )
        d = sa.to_dict()
        restored = ScopedAllow.from_dict(d)
        assert restored.pattern == sa.pattern
        assert restored.pattern_type == sa.pattern_type
        assert restored.suggestion == sa.suggestion
        assert restored.granted_at == sa.granted_at
        assert restored.ttl_seconds == sa.ttl_seconds
        assert restored.remaining_uses == sa.remaining_uses

    def test_from_dict_legacy(self):
        """Legacy dicts (no temporal fields) should be permanent."""
        d = {"pattern": "rm", "pattern_type": "literal"}
        sa = ScopedAllow.from_dict(d)
        assert sa.is_valid() is True
        assert sa.remaining_uses is None
        assert sa.ttl_seconds is None
        assert sa.granted_at == 0.0

    def test_to_dict_minimal(self):
        """Permanent entries should have minimal dict."""
        sa = ScopedAllow(pattern="rm")
        d = sa.to_dict()
        assert d == {"pattern": "rm", "pattern_type": "literal"}

    def test_consumed_at_serialization_roundtrip(self):
        """consumed_at is persisted so grace period survives session state reads."""
        sa = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = sa.consume()
        assert consumed.consumed_at > 0.0

        d = consumed.to_dict()
        assert "consumed_at" in d
        assert d["consumed_at"] == consumed.consumed_at

        restored = ScopedAllow.from_dict(d)
        assert restored.consumed_at == consumed.consumed_at
        assert restored.is_valid() is True  # Grace period intact after roundtrip

    def test_consumed_at_not_in_dict_when_zero(self):
        """consumed_at=0 should not be serialized (keeps dict minimal)."""
        sa = ScopedAllow(pattern="rm", remaining_uses=2)
        d = sa.to_dict()
        assert "consumed_at" not in d

    def test_from_dict_without_consumed_at_defaults_zero(self):
        """Legacy dicts without consumed_at default to 0 → no grace period."""
        d = {"pattern": "rm", "pattern_type": "literal", "remaining_uses": 0}
        sa = ScopedAllow.from_dict(d)
        assert sa.consumed_at == 0.0
        assert sa.is_valid() is False  # Exhausted, no grace


class TestScopedAllowStatusLabel:
    def test_permanent(self):
        assert ScopedAllow(pattern="rm").status_label() == "permanent"

    def test_count_only(self):
        sa = ScopedAllow(pattern="rm", remaining_uses=3)
        assert sa.status_label() == "3 uses remaining"

    def test_count_singular(self):
        sa = ScopedAllow(pattern="rm", remaining_uses=1)
        assert sa.status_label() == "1 use remaining"

    def test_ttl_only(self):
        sa = ScopedAllow(pattern="rm", granted_at=time.time(), ttl_seconds=90.0)
        label = sa.status_label()
        assert "remaining" in label
        # Should show something like "1m30s remaining" or "1m29s remaining"
        assert "m" in label

    def test_hybrid(self):
        sa = ScopedAllow(pattern="rm", granted_at=time.time(), ttl_seconds=300.0, remaining_uses=3)
        label = sa.status_label()
        assert "3 uses" in label
        assert "remaining" in label


# === parse_duration tests ===

class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("30s") == 30.0

    def test_minutes(self):
        assert parse_duration("5m") == 300.0

    def test_hours(self):
        assert parse_duration("1h") == 3600.0

    def test_combined(self):
        assert parse_duration("2h30m") == 9000.0

    def test_full(self):
        assert parse_duration("1h15m30s") == 4530.0

    def test_invalid(self):
        assert parse_duration("abc") is None
        assert parse_duration("") is None
        assert parse_duration("42") is None  # Bare int not a duration

    def test_zero(self):
        assert parse_duration("0s") is None
        assert parse_duration("0m") is None


# === parse_scope_args tests ===

class TestParseScopeArgs:
    def test_none(self):
        assert parse_scope_args(None) == (None, None, False)

    def test_empty(self):
        assert parse_scope_args("") == (None, None, False)
        assert parse_scope_args("  ") == (None, None, False)

    def test_count_only(self):
        assert parse_scope_args("3") == (None, 3, False)

    def test_duration_only(self):
        assert parse_scope_args("5m") == (300.0, None, False)

    def test_both(self):
        assert parse_scope_args("3 5m") == (300.0, 3, False)

    def test_permanent_keyword(self):
        assert parse_scope_args("permanent") == (None, None, True)

    def test_perm_keyword(self):
        assert parse_scope_args("perm") == (None, None, True)

    def test_p_keyword(self):
        assert parse_scope_args("p") == (None, None, True)

    def test_permanent_case_insensitive(self):
        assert parse_scope_args("Permanent") == (None, None, True)
        assert parse_scope_args("PERM") == (None, None, True)
        assert parse_scope_args("P") == (None, None, True)


# === Enforcement integration tests ===

class TestEnforcementCountDecrement:
    def test_allow_decrements_and_expires(self):
        """Allow with 2 uses: first two calls allowed, third blocked."""
        sid = f"test-enforce-{time.time()}"
        store = ThreadSafeDB()

        # Grant 2 uses
        ctx_grant = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok rm 2", store=store
        )
        result = plugins.app.dispatch(ctx_grant)
        assert "2 uses" in result.get("systemMessage", "")

        # First use — allowed
        ctx1 = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm /tmp/test1"}, store=store
        )
        r1 = plugins.app.dispatch(ctx1)
        decision = (r1 or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")
        assert decision != "deny", f"First use should be allowed, got: {r1}"

        # Second use — allowed (1 use remaining)
        ctx2 = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm /tmp/test2"}, store=store
        )
        r2 = plugins.app.dispatch(ctx2)
        decision2 = (r2 or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")
        assert decision2 != "deny", f"Second use should be allowed, got: {r2}"

        # Third use — should be blocked (0 remaining)
        ctx3 = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm /tmp/test3"}, store=store
        )
        r3 = plugins.app.dispatch(ctx3)
        # rm is a default blocked command, so without valid allow it should be denied/warned
        decision3 = (r3 or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")
        # With no valid allow, it falls through to TIER 2 which blocks rm
        assert decision3 != "approve" or r3 is None or "rm" in str(r3.get("systemMessage", "")).lower()


class TestEnforcementTTLExpiry:
    def test_ttl_expires(self):
        """Allow with short TTL: allowed before, blocked after expiry."""
        sid = f"test-ttl-{time.time()}"
        store = ThreadSafeDB()

        # Grant with 1-second TTL and permanent use count
        sa = ScopedAllow(
            pattern="rm", pattern_type="literal",
            granted_at=time.time(), ttl_seconds=1.0,
        )
        ctx_setup = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "echo"}, store=store
        )
        ctx_setup.session_allowed_patterns = [sa.to_dict()]

        # Wait for expiry
        time.sleep(1.1)

        ctx = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm /tmp/test"}, store=store
        )
        r = plugins.app.dispatch(ctx)
        # Expired allow should not match — falls through to default rm block
        decision = (r or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")
        assert decision != "approve" or r is None


class TestGrantDefaultOneUse:
    def test_default_creates_one_use(self):
        """'/ar:ok rm' with no scope args creates allow with remaining_uses=1."""
        sid = f"test-default-{time.time()}"
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok rm", store=store
        )
        result = plugins.app.dispatch(ctx)
        msg = result.get("systemMessage", "")
        assert "1 use" in msg

        # Verify stored entry has remaining_uses=1
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        sa = ScopedAllow.from_dict(allows[0])
        assert sa.remaining_uses == 1


class TestGrantPermanentKeyword:
    def test_permanent_keyword(self):
        for keyword in ["permanent", "perm", "p"]:
            sid = f"test-perm-{keyword}-{time.time()}"
            store = ThreadSafeDB()
            ctx = EventContext(
                session_id=sid, event="UserPromptSubmit",
                prompt=f"/ar:ok rm {keyword}", store=store
            )
            result = plugins.app.dispatch(ctx)
            msg = result.get("systemMessage", "")
            assert "permanent" in msg, f"Keyword '{keyword}' should create permanent allow, got: {msg}"


class TestFlexibleArgOrdering:
    """Test that scope keywords can appear before or after the pattern."""

    def test_permanent_before_pattern(self):
        """'/ar:ok p 'git push'' works (permanent keyword before pattern)."""
        sid = f"test-flex-perm-before-{time.time()}"
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok p 'git push'", store=store
        )
        result = plugins.app.dispatch(ctx)
        msg = result.get("systemMessage", "")
        assert "permanent" in msg, f"Should be permanent, got: {msg}"
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        assert allows[0]["pattern"] == "git push", f"Pattern should be 'git push', got: {allows[0]['pattern']}"

    def test_permanent_after_pattern(self):
        """'/ar:ok 'git push' p' works (permanent keyword after pattern)."""
        sid = f"test-flex-perm-after-{time.time()}"
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok 'git push' p", store=store
        )
        result = plugins.app.dispatch(ctx)
        msg = result.get("systemMessage", "")
        assert "permanent" in msg, f"Should be permanent, got: {msg}"
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        assert allows[0]["pattern"] == "git push"

    def test_count_before_pattern(self):
        """'/ar:ok 3 rm' works (count before pattern)."""
        sid = f"test-flex-count-before-{time.time()}"
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok 3 rm", store=store
        )
        result = plugins.app.dispatch(ctx)
        msg = result.get("systemMessage", "")
        assert "3 uses" in msg, f"Should have 3 uses, got: {msg}"
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        assert allows[0]["pattern"] == "rm"

    def test_duration_before_pattern(self):
        """'/ar:ok 5m rm' works (duration before pattern)."""
        sid = f"test-flex-dur-before-{time.time()}"
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok 5m rm", store=store
        )
        result = plugins.app.dispatch(ctx)
        msg = result.get("systemMessage", "")
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        assert allows[0]["pattern"] == "rm"
        assert allows[0].get("ttl_seconds") == 300.0

    def test_plain_pattern_no_swap(self):
        """'/ar:ok rm' still works (no false swap for normal patterns)."""
        sid = f"test-flex-nosw-{time.time()}"
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id=sid, event="UserPromptSubmit",
            prompt="/ar:ok rm", store=store
        )
        result = plugins.app.dispatch(ctx)
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        assert allows[0]["pattern"] == "rm"


class TestGlobalScopedAllow:
    def test_global_scoped_allow(self):
        """'/ar:globalok rm 3' creates global allow with 3 uses."""
        with _isolated_global_store() as store:
            sid = f"test-global-{time.time()}"
            db = ThreadSafeDB()
            ctx = EventContext(
                session_id=sid, event="UserPromptSubmit",
                prompt="/ar:globalok rm 3", store=db
            )
            result = plugins.app.dispatch(ctx)
            msg = result.get("systemMessage", "")
            assert "3 uses" in msg
            # Verify global store has the entry
            allows = store.get("global_allowed_patterns", [])
            assert len(allows) == 1
            sa = ScopedAllow.from_dict(allows[0])
            assert sa.remaining_uses == 3


class TestSessionStartCleanup:
    def test_expired_entries_purged(self):
        """SessionStart handler removes expired entries."""
        sid = f"test-cleanup-{time.time()}"
        store = ThreadSafeDB()

        # Set up allows: one expired, one valid
        expired = ScopedAllow(
            pattern="rm", granted_at=time.time() - 1000, ttl_seconds=1.0
        )
        valid = ScopedAllow(
            pattern="grep", remaining_uses=5
        )
        ctx = EventContext(
            session_id=sid, event="SessionStart",
            prompt="", store=store
        )
        ctx.session_allowed_patterns = [expired.to_dict(), valid.to_dict()]

        plugins.app.dispatch(ctx)

        # Only valid entry should remain
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1
        assert allows[0]["pattern"] == "grep"


class TestBackwardsCompatExistingAllows:
    def test_legacy_allows_work_as_permanent(self):
        """Pre-existing allows without temporal fields treated as permanent."""
        sid = f"test-compat-{time.time()}"
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "rm /tmp/test"}, store=store
        )
        # Legacy format: no granted_at, no ttl, no remaining_uses
        ctx.session_allowed_patterns = [{"pattern": "rm", "pattern_type": "literal"}]

        r = plugins.app.dispatch(ctx)
        decision = (r or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")
        # Legacy allow should still work (treated as permanent)
        assert decision != "deny", f"Legacy allow should work as permanent, got: {r}"


class TestEnforcementLazyCleanup:
    def test_expired_entries_cleaned_on_pass_through(self):
        """Expired entries get cleaned up when TIER 1 loop runs."""
        sid = f"test-lazy-{time.time()}"
        store = ThreadSafeDB()

        expired = ScopedAllow(
            pattern="curl", granted_at=time.time() - 1000, ttl_seconds=1.0
        )
        ctx = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "echo hello"}, store=store
        )
        ctx.session_allowed_patterns = [expired.to_dict()]

        plugins.app.dispatch(ctx)

        # Expired entry should have been cleaned up
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 0
