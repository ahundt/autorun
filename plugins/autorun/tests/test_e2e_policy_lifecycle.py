#!/usr/bin/env python3
"""End-to-end integration test for policy lifecycle

Tests the complete flow from setting a policy via UserPromptSubmit hook
to enforcement via PreToolUse hook, simulating real Claude Code behavior.
"""
import pytest
import subprocess
import sys
import uuid
import json
from pathlib import Path
from unittest.mock import Mock

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from autorun.main import (
    claude_code_handler,  # UserPromptSubmit handler
    pretooluse_handler,
    session_state,
    CONFIG
)
from conftest import register_test_session


class TestE2EPolicyLifecycle:
    """Test complete policy lifecycle from set to enforcement"""

    def setup_method(self):
        """Create unique session ID for each test"""
        self.session_id = f"test_e2e_{uuid.uuid4().hex[:8]}"
        register_test_session(self.session_id)

    def create_userpromptsubmit_context(self, prompt):
        """Create mock context for UserPromptSubmit hook"""
        ctx = Mock()
        ctx.prompt = prompt
        ctx.session_id = self.session_id
        ctx.session_transcript = []
        return ctx

    def create_pretooluse_context(self, tool_name, file_path=None, transcript=""):
        """Create mock context for PreToolUse hook"""
        ctx = Mock()
        ctx.tool_name = tool_name
        ctx.tool_input = {}
        if file_path:
            ctx.tool_input["file_path"] = file_path
        ctx.session_id = self.session_id
        ctx.session_transcript = transcript
        return ctx

    def test_justify_policy_lifecycle_no_justification(self):
        """E2E: Set JUSTIFY policy, try Write without justification → BLOCKED"""
        # Step 1: Simulate UserPromptSubmit hook setting JUSTIFY policy
        ctx = self.create_userpromptsubmit_context("/ar:j")
        result = claude_code_handler(ctx)

        # Verify policy was set
        with session_state(self.session_id) as state:
            assert state.get("file_policy") == "JUSTIFY", "Policy should be JUSTIFY"

        # Step 2: Simulate PreToolUse hook for Write tool (no justification)
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = pretooluse_handler(ctx)

        # Should be blocked
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "Write without justification should be blocked"

    def test_justify_policy_lifecycle_with_justification(self):
        """E2E: Set JUSTIFY policy, try Write with justification → ALLOWED"""
        # Step 1: Simulate UserPromptSubmit hook setting JUSTIFY policy
        ctx = self.create_userpromptsubmit_context("/ar:j")
        claude_code_handler(ctx)

        # Step 2: Simulate PreToolUse hook for Write tool (WITH justification)
        transcript = """<AUTOFILE_JUSTIFICATION>
        Creating config file for new authentication feature
        </AUTOFILE_JUSTIFICATION>"""
        ctx = self.create_pretooluse_context("Write", "/new/config.txt", transcript)
        result = pretooluse_handler(ctx)

        # Should be allowed
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Write with justification should be allowed"

    def test_search_policy_lifecycle_new_file(self):
        """E2E: Set SEARCH policy, try Write new file → BLOCKED"""
        # Step 1: Set SEARCH policy
        ctx = self.create_userpromptsubmit_context("/ar:f")
        claude_code_handler(ctx)

        # Verify policy was set
        with session_state(self.session_id) as state:
            assert state.get("file_policy") == "SEARCH", "Policy should be SEARCH"

        # Step 2: Try to write new file
        ctx = self.create_pretooluse_context("Write", "/nonexistent/new.txt", "")
        result = pretooluse_handler(ctx)

        # Should be blocked
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny", \
            "New file creation should be blocked under SEARCH policy"

    def test_search_policy_lifecycle_existing_file(self):
        """E2E: Set SEARCH policy, try Write existing file → ALLOWED"""
        # Step 1: Set SEARCH policy
        ctx = self.create_userpromptsubmit_context("/ar:f")
        claude_code_handler(ctx)

        # Step 2: Try to write existing file (this test file exists)
        existing_file = str(Path(__file__).resolve())
        ctx = self.create_pretooluse_context("Write", existing_file, "")
        result = pretooluse_handler(ctx)

        # Should be allowed
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Existing file modification should be allowed under SEARCH policy"

    def test_allow_policy_lifecycle(self):
        """E2E: Set ALLOW policy, Write any file → ALLOWED"""
        # Step 1: Set ALLOW policy
        ctx = self.create_userpromptsubmit_context("/ar:a")
        claude_code_handler(ctx)

        # Verify policy was set
        with session_state(self.session_id) as state:
            assert state.get("file_policy") == "ALLOW", "Policy should be ALLOW"

        # Step 2: Try to write new file
        ctx = self.create_pretooluse_context("Write", "/any/new/file.txt", "")
        result = pretooluse_handler(ctx)

        # Should be allowed
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow", \
            "Any file operation should be allowed under ALLOW policy"

    def test_policy_switch_lifecycle(self):
        """E2E: Switch policies and verify enforcement changes"""
        # Start with ALLOW
        ctx = self.create_userpromptsubmit_context("/ar:a")
        claude_code_handler(ctx)

        # Write should be allowed
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = pretooluse_handler(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

        # Switch to SEARCH
        ctx = self.create_userpromptsubmit_context("/ar:f")
        claude_code_handler(ctx)

        # Same write should now be blocked
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = pretooluse_handler(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Switch to JUSTIFY
        ctx = self.create_userpromptsubmit_context("/ar:j")
        claude_code_handler(ctx)

        # Still blocked without justification
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", "")
        result = pretooluse_handler(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Allowed with justification
        transcript = "<AUTOFILE_JUSTIFICATION>Valid reason</AUTOFILE_JUSTIFICATION>"
        ctx = self.create_pretooluse_context("Write", "/new/file.txt", transcript)
        result = pretooluse_handler(ctx)
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


class TestE2ECrossProcessPersistence:
    """Test that policies persist across separate process invocations (simulates real hook behavior)"""

    def setup_method(self):
        """Create unique session ID for each test"""
        self.session_id = f"test_e2e_xproc_{uuid.uuid4().hex[:8]}"
        register_test_session(self.session_id)

    def test_policy_persists_across_subprocess_hooks(self):
        """E2E: Policy set in one subprocess persists to another (simulates hook invocations)"""
        src_path = Path(__file__).parent.parent / "src"

        # Subprocess 1: Simulate UserPromptSubmit setting policy
        result1 = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{src_path}').resolve()))
from unittest.mock import Mock
from autorun.main import claude_code_handler

ctx = Mock()
ctx.prompt = "/ar:j"
ctx.session_id = "{self.session_id}"
ctx.session_transcript = []
claude_code_handler(ctx)
print("POLICY_SET")
"""
        ], capture_output=True, text=True, timeout=10)

        assert result1.returncode == 0, f"Subprocess 1 failed: {result1.stderr}"
        assert "POLICY_SET" in result1.stdout

        # Subprocess 2: Simulate PreToolUse checking policy
        result2 = subprocess.run([
            sys.executable, "-c",
            f"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path('{src_path}').resolve()))
from unittest.mock import Mock
from autorun.main import pretooluse_handler

ctx = Mock()
ctx.tool_name = "Write"
ctx.tool_input = {{"file_path": "/new/file.txt"}}
ctx.session_id = "{self.session_id}"
ctx.session_transcript = ""

result = pretooluse_handler(ctx)
decision = result["hookSpecificOutput"]["permissionDecision"]
print(f"DECISION:{{decision}}")
"""
        ], capture_output=True, text=True, timeout=10)

        assert result2.returncode == 0, f"Subprocess 2 failed: {result2.stderr}"

        # Policy should persist and deny without justification
        assert "DECISION:deny" in result2.stdout, \
            f"Expected deny, got: {result2.stdout}"

        
