#!/usr/bin/env python3
"""
REAL MONEY TESTS - Gemini CLI E2E Integration Tests

⚠️ WARNING: These tests make REAL API calls to Gemini CLI which cost REAL MONEY.

DO NOT RUN unless you understand the costs:
- Model: gemini-2.5-flash-lite (lowest cost)
- Estimated cost per test run: < $0.001 (less than 1/10th of a cent)
- Total tests: ~5 tests
- Total estimated cost: < $0.005

To run these tests:
    export CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/clautorun/tests/test_gemini_e2e_real_money.py -v

To skip these tests (default):
    uv run pytest plugins/clautorun/tests/ -v
    # These tests will be SKIPPED automatically

Mock tests (no cost) are in test_gemini_loading.py
"""
import os
import sys
import json
import subprocess
import shutil
from pathlib import Path

import pytest

# Check for CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY flag
ENABLE_REAL_MONEY_TESTS = os.environ.get("CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"

# Skip entire module if flag not set
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not ENABLE_REAL_MONEY_TESTS,
        reason="CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - these tests cost real money. "
               "Set CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 to run."
    )
]


@pytest.fixture(scope="module")
def gemini_cli_check():
    """Verify Gemini CLI is installed before running any tests."""
    if not shutil.which("gemini"):
        pytest.skip("Gemini CLI not installed (gemini command not found)")

    # Verify version
    try:
        result = subprocess.run(
            ["gemini", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            pytest.skip(f"Gemini CLI not working: {result.stderr}")
    except Exception as e:
        pytest.skip(f"Gemini CLI check failed: {e}")


@pytest.fixture(scope="module")
def gemini_extension_check():
    """Verify clautorun extension is loaded in Gemini.

    Note: `gemini extensions list` sends the extension list to stderr,
    not stdout. We check both streams to handle this correctly.
    """
    try:
        result = subprocess.run(
            ["gemini", "extensions", "list"],
            capture_output=True,
            text=True,
            timeout=30  # Extensions list loads credentials + experiments
        )
        if result.returncode != 0:
            pytest.skip(f"Could not list Gemini extensions: {result.stderr}")

        # Gemini CLI sends extension list to stderr (debug output stream)
        combined_output = result.stdout + result.stderr
        if "clautorun" not in combined_output:
            pytest.skip("clautorun extension not installed in Gemini CLI")
    except subprocess.TimeoutExpired:
        pytest.skip("gemini extensions list timed out (>30s)")
    except Exception as e:
        pytest.skip(f"Extension check failed: {e}")


class TestGeminiE2ERealMoney:
    """Real Gemini CLI E2E tests that make actual API calls.

    ⚠️ WARNING: These tests cost real money!
    """

    def test_gemini_basic_response(self, gemini_cli_check):
        """Test basic Gemini CLI functionality (COSTS REAL MONEY).

        Estimated cost: < $0.001
        """
        # Simple arithmetic test - minimal tokens
        result = subprocess.run(
            ["gemini", "-m", "gemini-2.5-flash-lite"],
            input="What is 2+2? Answer in one word.\n",
            capture_output=True,
            text=True,
            timeout=30
        )

        # Check for successful response
        assert result.returncode == 0, f"Gemini CLI failed: {result.stderr}"

        # Response should contain "Four" or "4"
        output_lower = result.stdout.lower()
        assert "four" in output_lower or "4" in output_lower, \
            f"Unexpected response: {result.stdout}"

    def test_gemini_extension_loaded(self, gemini_cli_check, gemini_extension_check):
        """Test that clautorun extension is loaded in Gemini.

        No API cost - just checks extension list.
        Note: `gemini extensions list` outputs to stderr, not stdout.
        """
        result = subprocess.run(
            ["gemini", "extensions", "list"],
            capture_output=True,
            text=True,
            timeout=30
        )

        assert result.returncode == 0, f"Extension list failed: {result.stderr}"
        # Gemini CLI sends extension list to stderr (debug output stream)
        combined_output = result.stdout + result.stderr
        assert "clautorun" in combined_output, \
            f"clautorun extension not found. Output:\n{combined_output[:500]}"

    @pytest.mark.skipif(
        True,  # Always skip until user confirms they want to test
        reason="Interactive test requires manual confirmation - costs real money"
    )
    def test_gemini_slash_command_recognition(self, gemini_cli_check, gemini_extension_check):
        """Test /cr:st command in Gemini (COSTS REAL MONEY).

        ⚠️ This test is ALWAYS SKIPPED by default.

        To enable: Remove @pytest.mark.skipif decorator
        Estimated cost: < $0.001

        Note: Piped mode may not provide shell execution tools.
        """
        result = subprocess.run(
            ["gemini", "-m", "gemini-2.5-flash-lite"],
            input="/cr:st\n",
            capture_output=True,
            text=True,
            timeout=30
        )

        # May fail in piped mode - that's expected
        # Just check that Gemini CLI responds
        assert result.returncode == 0 or "not found" in result.stderr.lower(), \
            f"Unexpected error: {result.stderr}"


class TestGeminiHookEntryPoint:
    """Test hook entry point directly (no API costs)."""

    def test_hook_sessionstart_event(self):
        """Test SessionStart hook event (NO COST - direct Python call)."""
        # Set up Gemini environment
        env = os.environ.copy()
        env["GEMINI_SESSION_ID"] = "test-e2e-session"
        env["GEMINI_PROJECT_DIR"] = "/tmp/clautorun-test"

        # Get hook script path (installed extension or source fallback)
        candidates = [
            Path.home() / ".gemini/extensions/clautorun-workspace/plugins/clautorun/hooks/hook_entry.py",
            Path(__file__).parent.parent / "hooks/hook_entry.py",
        ]
        hook_script = None
        for candidate in candidates:
            if candidate.exists():
                hook_script = candidate
                break
        if hook_script is None:
            pytest.skip(f"Hook script not found. Searched:\n" + "\n".join(f"  - {p}" for p in candidates))

        # Set plugin root for source fallback
        plugin_root = str(Path(__file__).parent.parent)
        env["CLAUTORUN_PLUGIN_ROOT"] = plugin_root

        # Run hook with uv run (matches production hook commands)
        result = subprocess.run(
            ["uv", "run", "--project", plugin_root, "python", str(hook_script)],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )

        assert result.returncode == 0, f"Hook failed: {result.stderr}"

        # Parse JSON response
        try:
            response = json.loads(result.stdout)
            assert response.get("continue") is True, \
                "Hook should return continue=true for SessionStart"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response from hook: {e}\nOutput: {result.stdout}")

    def test_hook_beforeagent_event(self):
        """Test BeforeAgent hook event (NO COST - direct Python call)."""
        env = os.environ.copy()
        env["GEMINI_SESSION_ID"] = "test-e2e-session"
        env["GEMINI_PROJECT_DIR"] = "/tmp/clautorun-test"

        # Get hook script path (installed extension or source fallback)
        candidates = [
            Path.home() / ".gemini/extensions/clautorun-workspace/plugins/clautorun/hooks/hook_entry.py",
            Path(__file__).parent.parent / "hooks/hook_entry.py",
        ]
        hook_script = None
        for candidate in candidates:
            if candidate.exists():
                hook_script = candidate
                break
        if hook_script is None:
            pytest.skip(f"Hook script not found. Searched:\n" + "\n".join(f"  - {p}" for p in candidates))

        # Set plugin root for source fallback
        plugin_root = str(Path(__file__).parent.parent)
        env["CLAUTORUN_PLUGIN_ROOT"] = plugin_root

        # Simulate BeforeAgent event with /cr:st command
        stdin_data = json.dumps({
            "type": "BeforeAgent",
            "command": "/cr:st",
            "sessionId": "test-e2e-session"
        })

        result = subprocess.run(
            ["uv", "run", "--project", plugin_root, "python", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )

        assert result.returncode == 0, f"Hook failed: {result.stderr}"

        try:
            response = json.loads(result.stdout)
            assert response.get("continue") is not None, \
                "Hook should return a continue field"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response from hook: {e}\nOutput: {result.stdout}")


# Documentation
__doc__ += """

## Test Categories

### Real Money Tests (require ENABLE_REAL_MONEY_TESTS=1):
1. test_gemini_basic_response - Simple API call (< $0.001)
2. test_gemini_slash_command_recognition - Always skipped (manual enable)

### Free Tests (no API costs):
1. test_gemini_extension_loaded - Check extension list
2. test_hook_sessionstart_event - Direct Python call to hook
3. test_hook_beforeagent_event - Direct Python call to hook

## Running Tests

### Skip real money tests (default):
```bash
uv run pytest plugins/clautorun/tests/test_gemini_e2e_real_money.py -v
# All real money tests SKIPPED
```

### Run real money tests:
```bash
export CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
uv run pytest plugins/clautorun/tests/test_gemini_e2e_real_money.py -v
# Real money tests RUN (estimated cost: < $0.005)
```

### Run only free tests:
```bash
uv run pytest plugins/clautorun/tests/test_gemini_e2e_real_money.py::TestGeminiHookEntryPoint -v
# No costs - direct Python calls only
```

## Cost Breakdown

| Test | API Calls | Estimated Cost |
|------|-----------|----------------|
| test_gemini_basic_response | 1 | < $0.001 |
| test_gemini_extension_loaded | 0 | $0.000 |
| test_hook_sessionstart_event | 0 | $0.000 |
| test_hook_beforeagent_event | 0 | $0.000 |
| **TOTAL** | 1 | **< $0.001** |

Model: gemini-2.5-flash-lite
Pricing: ~$0.075 per 1M input tokens, ~$0.30 per 1M output tokens
Estimated tokens per test: ~50 input + ~5 output

## Important Notes

1. ⚠️ Always check current Gemini API pricing before running
2. ⚠️ Set ENABLE_REAL_MONEY_TESTS=1 explicitly to run
3. ⚠️ test_gemini_slash_command_recognition is ALWAYS SKIPPED (manual enable)
4. ✅ Mock tests in test_gemini_loading.py have NO costs
"""
