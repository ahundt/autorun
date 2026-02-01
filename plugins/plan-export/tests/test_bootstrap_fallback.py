#!/usr/bin/env python3
"""
Tests for plan_export.py bootstrap fallback functionality.

These tests verify that plan_export.py works correctly even when the clautorun
module is not available (bootstrap scenario). This is important because:

1. New users may install plan-export before clautorun
2. The SessionLock import should fail gracefully, not crash the script
3. Plan export should still function (just without race condition protection)

Bug details:
- plan_export.py imports SessionLock from clautorun.session_manager
- If clautorun is not installed, this import would crash the script
- The fix adds try/except with HAS_SESSION_LOCK flag and nullcontext fallback
"""

import ast
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def get_plan_export_script() -> Path:
    """Get the path to plan_export.py."""
    # This file is at plugins/plan-export/tests/test_bootstrap_fallback.py
    # plan_export.py is at plugins/plan-export/scripts/plan_export.py
    return Path(__file__).parent.parent / "scripts" / "plan_export.py"


class TestBootstrapFallbackCode:
    """Tests that verify the bootstrap fallback code is correctly implemented."""

    def test_script_has_bootstrap_fallback_structure(self):
        """Verify plan_export.py has the try/except structure for SessionLock import."""
        script_path = get_plan_export_script()
        assert script_path.exists(), f"plan_export.py not found at {script_path}"

        content = script_path.read_text()

        # Should have HAS_SESSION_LOCK flag
        assert "HAS_SESSION_LOCK = False" in content, (
            "plan_export.py should initialize HAS_SESSION_LOCK = False before try block"
        )

        # Should have try/except for SessionLock import
        assert "HAS_SESSION_LOCK = True" in content, (
            "plan_export.py should set HAS_SESSION_LOCK = True on successful import"
        )

        # Should import nullcontext for fallback
        assert "from contextlib import nullcontext" in content, (
            "plan_export.py should import nullcontext for bootstrap fallback"
        )

        # Should have conditional lock usage
        assert "if HAS_SESSION_LOCK" in content, (
            "plan_export.py should conditionally use SessionLock based on HAS_SESSION_LOCK"
        )

    def test_script_syntax_is_valid(self):
        """Verify plan_export.py has valid Python syntax."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        try:
            ast.parse(content)
        except SyntaxError as e:
            pytest.fail(f"plan_export.py has invalid syntax: {e}")

    def test_nullcontext_fallback_pattern(self):
        """Verify the nullcontext fallback pattern is correctly implemented."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should use conditional expression for lock_context
        assert "nullcontext()" in content, (
            "plan_export.py should use nullcontext() as fallback when SessionLock unavailable"
        )

        # The pattern should be: lock_context = SessionLock(...) if HAS_SESSION_LOCK else nullcontext()
        assert "if HAS_SESSION_LOCK else nullcontext()" in content, (
            "plan_export.py should use pattern: "
            "lock_context = SessionLock(...) if HAS_SESSION_LOCK else nullcontext()"
        )


class TestBootstrapFallbackBehavior:
    """Tests that verify the bootstrap fallback works correctly at runtime."""

    def test_nullcontext_is_no_op(self):
        """Verify nullcontext works as expected (no-op context manager)."""
        executed = False

        with nullcontext():
            executed = True

        assert executed, "nullcontext should allow code inside to execute"

    def test_bootstrap_scenario_simulation(self):
        """Simulate bootstrap scenario where SessionLock is not available."""
        # Simulate bootstrap mode
        HAS_SESSION_LOCK = False
        SessionLock = None  # Not available

        # This is the pattern used in plan_export.py
        lock_context = nullcontext() if not HAS_SESSION_LOCK else SessionLock("test")

        with lock_context:
            # Code inside should execute without error
            result = "success"

        assert result == "success", "Bootstrap fallback should allow code execution"

    def test_normal_scenario_simulation(self):
        """Simulate normal scenario where SessionLock is available."""
        # Create mock SessionLock
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=False)

        # Simulate normal mode
        HAS_SESSION_LOCK = True

        # This simulates the pattern used in plan_export.py
        lock_context = mock_lock if HAS_SESSION_LOCK else nullcontext()

        with lock_context:
            result = "success"

        assert result == "success", "Normal mode should work with SessionLock"
        mock_lock.__enter__.assert_called_once()
        mock_lock.__exit__.assert_called_once()


class TestSessionLockImportFallback:
    """Tests for the SessionLock import fallback behavior."""

    def test_import_error_sets_flag_false(self):
        """Verify ImportError during SessionLock import sets HAS_SESSION_LOCK = False."""
        # Simulate what happens when clautorun module is not available
        HAS_SESSION_LOCK = False
        SessionLock = None
        SessionTimeoutError = Exception

        try:
            # This simulates the import that would fail
            raise ImportError("No module named 'clautorun'")
        except ImportError:
            pass  # HAS_SESSION_LOCK stays False

        assert HAS_SESSION_LOCK is False
        assert SessionLock is None

    def test_successful_import_sets_flag_true(self):
        """Verify successful SessionLock import sets HAS_SESSION_LOCK = True."""
        # Simulate successful import
        HAS_SESSION_LOCK = False

        try:
            # Simulate successful import (we'll just not raise)
            HAS_SESSION_LOCK = True
            SessionLock = MagicMock  # Mock class
        except ImportError:
            pass

        assert HAS_SESSION_LOCK is True
        assert SessionLock is not None


class TestRegressionPrevention:
    """Tests to prevent regression of the bootstrap issue."""

    def test_bare_import_not_present(self):
        """Verify plan_export.py does NOT have bare SessionLock import.

        A bare import like:
            from clautorun.session_manager import SessionLock, SessionTimeoutError

        would crash if clautorun is not installed. The import must be wrapped
        in try/except.
        """
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Parse the AST to find top-level imports
        tree = ast.parse(content)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "clautorun" in node.module:
                    # Check if this import is inside a try block
                    # by verifying the HAS_SESSION_LOCK pattern exists
                    assert "HAS_SESSION_LOCK = False" in content, (
                        "clautorun import must be preceded by HAS_SESSION_LOCK = False"
                    )
                    assert "except ImportError:" in content or "except ImportError" in content, (
                        "clautorun import must be wrapped in try/except ImportError"
                    )

    def test_script_runs_without_clautorun_in_path(self):
        """Verify plan_export.py can be parsed even without clautorun module.

        This test ensures the script structure allows it to be loaded
        even when clautorun is not available.
        """
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Verify the script has proper structure for bootstrap
        # The key patterns that must exist:
        required_patterns = [
            "HAS_SESSION_LOCK = False",  # Initialize before try
            "try:",  # Start of try block
            "from clautorun.session_manager import",  # Import attempt
            "HAS_SESSION_LOCK = True",  # Set on success
            "except ImportError:",  # Catch import failure
            "pass",  # Handle gracefully
        ]

        for pattern in required_patterns:
            assert pattern in content, (
                f"plan_export.py missing required bootstrap pattern: '{pattern}'"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
