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

    def test_hybrid_ttl_expires_during_grace_window(self):
        """TTL expiry takes precedence over grace period.

        Scenario: allow with ttl_seconds=1 AND remaining_uses=1. Hook A consumes
        at T0 (within TTL). By T0+1.5s, TTL has expired — grace period should NOT
        override TTL expiry. scoped_allow.py checks TTL before grace logic.
        """
        t0 = time.time()
        sa = ScopedAllow(
            pattern="git push", granted_at=t0, ttl_seconds=1.0, remaining_uses=1
        )
        consumed = sa.consume("call123")
        # Immediately: both TTL valid and grace active
        assert consumed.is_valid("call123") is True
        # After TTL expires (even if within grace window timing):
        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = t0 + 1.5  # TTL expired (1.5 > 1.0)
            assert consumed.is_valid("call123") is False  # TTL wins over grace

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
    autorun runs twice per Bash command — once via the plugin's own PreToolUse
    hook, and once via `rtk hook claude` (settings.json) which internally spawns
    the autorun subprocess. Both invocations connect to the same daemon via
    ~/.autorun/daemon.sock. session_manager re-reads from disk inside every lock
    acquisition (fully serialized), so the second invocation may read
    remaining_uses=0 after the first has already written the consumed state.

    The race window is ~0–200ms (startup jitter between the two Python processes).
    _PARALLEL_GRACE_SECONDS=1.0 safely covers this while being below the minimum
    time for a genuine second tool call (tool execution + Claude response ≥ 3s).

    The last_call_id fingerprint (hash of session_id:tool_name:cmd) ensures the
    grace period only applies to parallel invocations of the same call in the same
    session, preventing global allows from leaking to concurrent different sessions.
    """

    CALL_ID = "abc123def456abcd"   # Simulated fingerprint for session+cmd

    def test_parallel_hook_b_passes_after_hook_a_consumes(self):
        """Hook B (RTK-spawned) passes immediately after Hook A (direct) exhausts allow.

        Simulates the exact race: Hook A writes consumed state to daemon store,
        Hook B reads that state (remaining_uses=0) and checks is_valid() — must
        pass since both are handling the same tool call.
        """
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        assert original.is_valid(self.CALL_ID) is True

        # Hook A (direct plugin hook): consume the allow, stamps fingerprint
        hook_a_result = original.consume(self.CALL_ID)
        assert hook_a_result.remaining_uses == 0
        assert hook_a_result.last_call_id == self.CALL_ID

        # Hook B (RTK-spawned): reads already-consumed state, same call_id
        # Without grace period this would be False → falls to TIER 2 → blocked
        assert hook_a_result.is_valid(self.CALL_ID) is True

    def test_parallel_hook_blocked_after_grace_period_expires(self):
        """A genuine second command (arriving >1s after consume) is correctly blocked."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume(self.CALL_ID)
        assert consumed.remaining_uses == 0
        assert consumed.consumed_at > 0

        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 2.0
            assert consumed.is_valid(self.CALL_ID) is False  # Grace (1s) expired

    def test_grace_period_boundary_at_exactly_1s(self):
        """At exactly 1s, allow is invalid (boundary is exclusive: time < 1.0)."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume(self.CALL_ID)

        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 1.0
            assert consumed.is_valid(self.CALL_ID) is False  # 1.0 is not < 1.0

    def test_grace_period_just_inside_boundary(self):
        """Just inside 1s grace period, allow is still valid."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume(self.CALL_ID)

        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 0.999
            assert consumed.is_valid(self.CALL_ID) is True

    def test_no_grace_period_without_consumed_at(self):
        """consumed_at=0 (pre-feature serialized state) means no grace — expired immediately."""
        sa = ScopedAllow(pattern="rm", remaining_uses=0, consumed_at=0.0)
        assert sa.is_valid(self.CALL_ID) is False  # No grace period without consumed_at

    def test_multi_use_allow_no_grace_until_exhausted(self):
        """Grace period only activates when remaining_uses hits 0, not before."""
        sa = ScopedAllow(pattern="rm", remaining_uses=3)
        after_one = sa.consume(self.CALL_ID)
        assert after_one.remaining_uses == 2
        assert after_one.consumed_at == 0.0  # Not exhausted yet
        assert after_one.last_call_id == ""  # Fingerprint not stamped yet
        assert after_one.is_valid(self.CALL_ID) is True  # Valid normally, not via grace

    def test_different_call_id_blocked_within_grace_window(self):
        """Different session's fingerprint is blocked even within the 1s grace window.

        This prevents a global allow from granting grace to a concurrent
        different session that runs the same command within 1s.
        Session A's call_id = hash("sessionA:Bash:git push")
        Session B's call_id = hash("sessionB:Bash:git push") ≠ session A's
        """
        session_a_call_id = "aaaa1111aaaa1111"
        session_b_call_id = "bbbb2222bbbb2222"

        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed_by_a = original.consume(session_a_call_id)

        # Session A's parallel hook → same fingerprint → allowed ✓
        assert consumed_by_a.is_valid(session_a_call_id) is True

        # Session B's hook → different fingerprint → blocked ✓
        assert consumed_by_a.is_valid(session_b_call_id) is False

    def test_no_call_id_falls_back_to_time_only(self):
        """Legacy callers that pass no call_id fall back to time-only check."""
        original = ScopedAllow(pattern="git push", remaining_uses=1)
        consumed = original.consume(self.CALL_ID)  # Stamped with fingerprint

        # Legacy is_valid() call (no call_id) → time-only check → passes within window
        assert consumed.is_valid() is True

        # After grace expires, still blocked even with no call_id
        with patch("autorun.scoped_allow.time") as mock_time:
            mock_time.time.return_value = consumed.consumed_at + 2.0
            assert consumed.is_valid() is False


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
    def test_grace_period_allow_not_cleaned_during_pass_through(self):
        """Allows in their grace period survive lazy cleanup.

        plugins.py:625-628 calls is_valid() without call_id during cleanup.
        An allow with remaining_uses=0 and recent consumed_at should NOT be
        removed — the parallel hook (RTK-spawned) may still need it.
        is_valid() without call_id uses time-only check, which preserves grace.
        """
        sid = f"test-grace-cleanup-{time.time()}"
        store = ThreadSafeDB()

        # Create an allow that was just consumed (in grace period)
        in_grace = ScopedAllow(
            pattern="git push", remaining_uses=0,
            consumed_at=time.time(), last_call_id="abc123"
        )
        ctx = EventContext(
            session_id=sid, event="PreToolUse", tool_name="Bash",
            tool_input={"command": "echo unrelated"}, store=store
        )
        ctx.session_allowed_patterns = [in_grace.to_dict()]

        plugins.app.dispatch(ctx)

        # Allow in grace period should survive cleanup
        allows = ctx.session_allowed_patterns or []
        assert len(allows) == 1, "Grace-period allow was incorrectly cleaned up"

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


class TestQuotedRegexGrantRoundTrip:
    """End-to-end: a QUOTED `/ar:ok 'regex:...'` grant must bypass a block.

    Regression for the parser bug where the leading quote hid the regex: prefix,
    so the grant was stored as a literal `regex:...` and never matched.
    """

    def test_quoted_regex_allow_bypasses_block(self):
        sid = f"test-qregex-{time.time()}"
        store = ThreadSafeDB()

        def decision(cmd):
            ctx = EventContext(session_id=sid, event="PreToolUse", tool_name="Bash",
                               tool_input={"command": cmd}, store=store)
            r = plugins.app.dispatch(ctx)
            return (r or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")

        # git push is a default-blocked consent gate.
        assert decision("git push origin main") == "deny"

        # Grant via a QUOTED regex allow (the previously-broken form).
        grant = EventContext(session_id=sid, event="UserPromptSubmit",
                             prompt="/ar:ok 'regex:git push|git commit' perm", store=store)
        plugins.app.dispatch(grant)

        # Now allowed; an unrelated blocked command is still blocked.
        assert decision("git push origin main") != "deny"
        assert decision("rm -rf /tmp/x") == "deny"
