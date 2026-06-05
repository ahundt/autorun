#!/usr/bin/env python3
"""End-to-end integration test for policy lifecycle

Tests the complete flow from setting a policy via UserPromptSubmit hook
to enforcement via PreToolUse hook, simulating real Claude Code behavior.

Daemon-path architecture note:
  In production, ALL EventContexts within a daemon request share ONE ThreadSafeDB
  instance. Policy set by UserPromptSubmit (ctx.file_policy = "JUSTIFY") is visible
  to subsequent PreToolUse hooks because they share the same in-memory store.
  Tests replicate this by creating a shared ThreadSafeDB (self._store) used by all
  EventContexts within a test, exactly mirroring daemon behavior.
"""
import os
import subprocess
import sys
import uuid
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins
from conftest import register_test_session

# claude_code_handler — REMOVED: canonical replacement is plugins.app.dispatch(ctx)
# See: plugins.py UserPromptSubmit command registration via app.command()


# Daemon-path helper — replaces deleted pretooluse_handler
def _pretooluse(ctx):
    """Run daemon-path PreToolUse chain: file policy then command blocking."""
    result = plugins.enforce_file_policy(ctx)
    if result is not None:
        return result
    result = plugins.check_blocked_commands(ctx)
    if result is not None:
        return result
    return ctx.allow()


class TestE2EPolicyLifecycle:
    """Test complete policy lifecycle from set to enforcement.

    Uses a shared ThreadSafeDB store so that UserPromptSubmit policy commands
    and PreToolUse enforcement share the same in-memory state.
    This mirrors the daemon: all requests within one daemon share ONE ThreadSafeDB.
    Canonical path: plugins.app.dispatch(ctx) replaces deleted claude_code_handler.
    """

    def setup_method(self):
        """Create unique session ID and shared store for each test."""
        self.session_id = f"test_e2e_{uuid.uuid4().hex[:8]}"
        self._store = ThreadSafeDB()  # Shared store — mirrors daemon behavior
        register_test_session(self.session_id)

    def _dispatch_command(self, prompt: str):
        """Dispatch a UserPromptSubmit command via daemon-path plugins.app.dispatch().

        Canonical replacement for deleted claude_code_handler(mock_ctx).
        Uses shared self._store so policy changes persist and are visible
        to create_pretooluse_context() calls within the same test.
        """
        ctx = EventContext(
            session_id=self.session_id,
            event="UserPromptSubmit",
            prompt=prompt,
            tool_name="",
            tool_input={},
            store=self._store,
        )
        return plugins.app.dispatch(ctx)

    def create_pretooluse_context(self, tool_name, file_path=None, transcript=""):
        """Create EventContext for PreToolUse hook using shared self._store.

        Shares self._store with _dispatch_command() so file_policy set by
        UserPromptSubmit dispatch is visible here (daemon-path behavior).
        """
        tool_input = {}
        if file_path:
            tool_input["file_path"] = file_path
        transcript_list = [transcript] if transcript else []
        return EventContext(
            session_id=self.session_id,
            event="PreToolUse",
            tool_name=tool_name,
            tool_input=tool_input,
            session_transcript=transcript_list,
            store=self._store,
        )

    def _assert_policy(self, expected: str):
        """Assert that the shared store has the expected file_policy."""
        verify_ctx = EventContext(
            session_id=self.session_id,
            event="PreToolUse",
            tool_name="Bash",
            tool_input={},
            store=self._store,
        )
        assert verify_ctx.file_policy == expected, f"Policy should be {expected}"

    def test_justify_policy_lifecycle_no_justification(self):
        """E2E: Set JUSTIFY policy, try Write without justification → BLOCKED"""
        # Step 1: Simulate UserPromptSubmit hook setting JUSTIFY policy
        result = self._dispatch_command("/ar:j")

        # Verify policy was set in shared store
        self._assert_policy("JUSTIFY")

        # Step 2: Simulate PreToolUse hook for Write tool (no justification)
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = _pretooluse(ctx)

        # Should be blocked
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "Write without justification should be blocked"

    def test_justify_policy_lifecycle_with_justification(self):
        """E2E: Set JUSTIFY policy, try Write with justification → ALLOWED"""
        # Step 1: Simulate UserPromptSubmit hook setting JUSTIFY policy
        self._dispatch_command("/ar:j")

        # Step 2: Simulate PreToolUse hook for Write tool (WITH justification)
        transcript = """<AUTOFILE_JUSTIFICATION>
        Creating config file for new authentication feature
        </AUTOFILE_JUSTIFICATION>"""
        ctx = self.create_pretooluse_context("Write", "/new/config.txt", transcript)
        result = _pretooluse(ctx)

        # Should be allowed
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Write with justification should be allowed"

    def test_search_policy_lifecycle_new_file(self):
        """E2E: Set SEARCH policy, try Write new file → BLOCKED"""
        # Step 1: Set SEARCH policy
        self._dispatch_command("/ar:f")

        # Verify policy was set
        self._assert_policy("SEARCH")

        # Step 2: Try to write new file
        ctx = self.create_pretooluse_context("Write", "/nonexistent/new.txt", "")
        result = _pretooluse(ctx)

        # Should be blocked
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "New file creation should be blocked under SEARCH policy"

    def test_search_policy_lifecycle_existing_file(self):
        """E2E: Set SEARCH policy, try Write existing file → ALLOWED"""
        # Step 1: Set SEARCH policy
        self._dispatch_command("/ar:f")

        # Step 2: Try to write existing file (this test file exists)
        existing_file = str(Path(__file__).resolve())
        ctx = self.create_pretooluse_context("Write", existing_file, "")
        result = _pretooluse(ctx)

        # Should be allowed
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Existing file modification should be allowed under SEARCH policy"

    def test_allow_policy_lifecycle(self):
        """E2E: Set ALLOW policy, Write any file → ALLOWED"""
        # Step 1: Set ALLOW policy
        self._dispatch_command("/ar:a")

        # Verify policy was set
        self._assert_policy("ALLOW")

        # Step 2: Try to write new file
        ctx = self.create_pretooluse_context("Write", "/any/new/file.txt", "")
        result = _pretooluse(ctx)

        # Should be allowed
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Any file operation should be allowed under ALLOW policy"

    def test_policy_switch_lifecycle(self):
        """E2E: Switch policies and verify enforcement changes"""
        # Start with ALLOW
        self._dispatch_command("/ar:a")

        # Write should be allowed
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = _pretooluse(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

        # Switch to SEARCH
        self._dispatch_command("/ar:f")

        # Same write should now be blocked
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = _pretooluse(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Switch to JUSTIFY
        self._dispatch_command("/ar:j")

        # Still blocked without justification
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = _pretooluse(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Allowed with justification
        transcript = "<AUTOFILE_JUSTIFICATION>Valid reason</AUTOFILE_JUSTIFICATION>"
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", transcript)
        result = _pretooluse(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestE2ECrossProcessPersistence:
    """Test that policies persist across separate process invocations (simulates real hook behavior)

    Architecture note:
      Cross-process persistence is provided by ThreadSafeDB over session_state(), not by
      EventContext(store=None). In production, hook subprocesses normally connect to the daemon,
      which uses ThreadSafeDB for fast in-memory access backed by the process-safe JSON session
      store. This test exercises that persistent layer directly from two Python processes with
      an isolated AUTORUN_TEST_STATE_DIR, so it is deterministic and does not depend on a live
      daemon owned by the developer's current session.
    """

    def setup_method(self):
        """Create unique session ID for each test"""
        self.session_id = f"test_e2e_xproc_{uuid.uuid4().hex[:8]}"
        register_test_session(self.session_id)

    def test_policy_persists_across_subprocess_hooks(self, tmp_path):
        """E2E: Policy set in one subprocess persists to another through session_state."""
        src_path = Path(__file__).parent.parent / "src"
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        env = os.environ.copy()
        env["AUTORUN_TEST_STATE_DIR"] = str(state_dir)

        # Subprocess 1: Simulate UserPromptSubmit setting policy
        result1 = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{src_path}').resolve()))
from autorun.core import EventContext
from autorun.core import ThreadSafeDB
from autorun import plugins

# Canonical replacement for deleted claude_code_handler: plugins.app.dispatch(ctx)
ctx = EventContext(
    session_id="{self.session_id}",
    event="UserPromptSubmit",
    prompt="/ar:j",
    tool_name="",
    tool_input={{}},
    store=ThreadSafeDB(),
)
plugins.app.dispatch(ctx)
print("POLICY_SET")
"""
        ], capture_output=True, text=True, timeout=10, env=env)

        assert result1.returncode == 0, f"Subprocess 1 failed: {result1.stderr}"
        assert "POLICY_SET" in result1.stdout

        # Subprocess 2: Simulate PreToolUse checking policy via daemon path
        result2 = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{src_path}').resolve()))
from autorun.core import EventContext, ThreadSafeDB
from autorun import plugins

def _pretooluse(ctx):
    r = plugins.enforce_file_policy(ctx)
    if r is not None:
        return r
    r = plugins.check_blocked_commands(ctx)
    if r is not None:
        return r
    return ctx.allow()

ctx = EventContext(
    session_id="{self.session_id}",
    event="PreToolUse",
    tool_name="Write",
    tool_input={{"file_path": "/new/file.txt"}},
    store=ThreadSafeDB(),
)

result = _pretooluse(ctx)
decision = result["hookSpecificOutput"]["permissionDecision"]
print(f"DECISION:{{decision}}")
"""
        ], capture_output=True, text=True, timeout=10, env=env)

        assert result2.returncode == 0, f"Subprocess 2 failed: {result2.stderr}"

        # Policy should persist and deny without justification
        assert "DECISION:deny" in result2.stdout, \
            f"Expected deny, got: {result2.stdout}"
