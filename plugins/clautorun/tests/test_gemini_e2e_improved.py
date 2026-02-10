#!/usr/bin/env python3
"""
IMPROVED E2E TESTS - Gemini CLI Integration with Real Money Protection

⚠️ WARNING: These tests make REAL API calls to Gemini CLI which cost REAL MONEY.

DO NOT RUN unless you understand the costs:
- Model: gemini-2.5-flash-lite (lowest cost)
- Estimated cost per test run: < $0.001 (less than 1/10th of a cent)
- Total tests with real API calls: 2
- Total estimated cost: < $0.002

To run these tests:
    export CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py -v

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


def find_hook_script() -> Path:
    """Dynamically find hook_entry.py script.

    Returns:
        Path to hook_entry.py

    Raises:
        FileNotFoundError: If hook script not found
    """
    possible_locations = [
        Path.home() / ".gemini/extensions/clautorun-workspace/plugins/clautorun/hooks/hook_entry.py",
        Path.home() / ".claude/clautorun/plugins/clautorun/hooks/hook_entry.py",
        Path(__file__).parent.parent / "hooks/hook_entry.py",
    ]

    for location in possible_locations:
        if location.exists():
            return location

    raise FileNotFoundError(
        f"hook_entry.py not found. Searched:\n" +
        "\n".join(f"  - {loc}" for loc in possible_locations)
    )


@pytest.fixture(scope="module")
def gemini_cli_available():
    """Check if Gemini CLI is installed."""
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

        return result.stdout.strip()
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

        return True
    except subprocess.TimeoutExpired:
        pytest.skip("gemini extensions list timed out (>30s)")
    except Exception as e:
        pytest.skip(f"Extension check failed: {e}")


@pytest.fixture
def clean_environment():
    """Provide clean environment for each test, restore after."""
    original_env = os.environ.copy()

    # Set test environment
    os.environ["GEMINI_SESSION_ID"] = "test-e2e-session"
    os.environ["GEMINI_PROJECT_DIR"] = "/tmp/clautorun-gemini-test"

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


class TestGeminiHookEntryPointDirect:
    """Test hook entry point directly (NO COST - no API calls)."""

    def test_hook_script_exists(self):
        """Verify hook_entry.py can be found."""
        try:
            hook_script = find_hook_script()
            assert hook_script.exists(), f"Hook script not found: {hook_script}"
            assert hook_script.name == "hook_entry.py", \
                f"Found wrong file: {hook_script.name}"
        except FileNotFoundError as e:
            pytest.skip(str(e))

    def test_hook_sessionstart_event(self, clean_environment):
        """Test SessionStart hook event (NO COST - direct Python call)."""
        try:
            hook_script = find_hook_script()
        except FileNotFoundError as e:
            pytest.skip(str(e))

        # Run hook with no stdin (SessionStart event)
        result = subprocess.run(
            ["python3", str(hook_script)],
            capture_output=True,
            text=True,
            timeout=10,
            env=os.environ
        )

        # Should succeed
        assert result.returncode == 0, \
            f"Hook failed with exit code {result.returncode}\nStderr: {result.stderr}"

        # Parse JSON response
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response from hook: {e}\nOutput: {result.stdout}")

        # Verify response structure
        assert "continue" in response, "Missing 'continue' field"
        assert response["continue"] is True, \
            "Hook should return continue=true for SessionStart"

    def test_hook_beforeagent_event_slash_command(self, clean_environment):
        """Test BeforeAgent hook event with /cr:st command (NO COST)."""
        try:
            hook_script = find_hook_script()
        except FileNotFoundError as e:
            pytest.skip(str(e))

        # Simulate BeforeAgent event with /cr:st command
        stdin_data = json.dumps({
            "type": "BeforeAgent",
            "command": "/cr:st",
            "sessionId": "test-e2e-session"
        })

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env=os.environ
        )

        assert result.returncode == 0, \
            f"Hook failed: {result.stderr}"

        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {e}\nOutput: {result.stdout}")

        # Verify response
        assert "continue" in response, "Missing 'continue' field"
        assert response["continue"] is not None, "continue field is None"

    def test_hook_beforetool_event_blocking_camelcase(self, clean_environment):
        """Test BeforeTool hook blocks dangerous command using camelCase format (NO COST).

        Tests backward-compatible camelCase format (type/toolName/toolInput/sessionId).
        Our normalize_hook_payload handles this for older Gemini CLI versions.
        """
        try:
            hook_script = find_hook_script()
        except FileNotFoundError as e:
            pytest.skip(str(e))

        # camelCase format (backward-compatible)
        stdin_data = json.dumps({
            "type": "BeforeTool",
            "toolName": "bash_command",
            "toolInput": {"command": "cat /etc/hosts"},
            "sessionId": "test-e2e-session"
        })

        test_env = os.environ.copy()
        test_env["CLAUTORUN_USE_DAEMON"] = "0"

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env=test_env
        )

        assert result.returncode == 0, f"Hook failed: {result.stderr}"

        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {e}\nOutput: {result.stdout}")

        # Verify blocking via BOTH formats
        assert "hookSpecificOutput" in response, \
            f"Missing hookSpecificOutput.\nFull response: {json.dumps(response, indent=2)[:500]}"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", \
            f"Claude Code format: permissionDecision={response['hookSpecificOutput'].get('permissionDecision')}"
        assert response.get("decision") == "deny", \
            f"Gemini CLI format: top-level decision={response.get('decision')} (must be 'deny')"

    def test_hook_beforetool_event_blocking_snakecase(self, clean_environment):
        """Test BeforeTool hook blocks dangerous command using official snake_case format (NO COST).

        Tests the official Gemini CLI v0.26+ hook input format (snake_case keys):
        - hook_event_name: "BeforeTool" (not "type")
        - tool_name: "bash_command" (not "toolName")
        - tool_input: {...} (not "toolInput")
        - session_id: "..." (not "sessionId")

        Verifies the response includes top-level 'decision: deny' which is what
        Gemini CLI actually reads to block commands.

        Reference: https://geminicli.com/docs/hooks/reference/
        """
        try:
            hook_script = find_hook_script()
        except FileNotFoundError as e:
            pytest.skip(str(e))

        # Official Gemini CLI snake_case format (v0.26+)
        stdin_data = json.dumps({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "cat /etc/hosts"},
            "session_id": "test-e2e-session",
            "cwd": "/tmp",
            "transcript_path": "/tmp/test-transcript.jsonl"
        })

        test_env = os.environ.copy()
        test_env["CLAUTORUN_USE_DAEMON"] = "0"

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env=test_env
        )

        assert result.returncode == 0, f"Hook failed: {result.stderr}"

        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {e}\nOutput: {result.stdout}")

        # CRITICAL: Gemini CLI reads top-level 'decision' field
        assert response.get("decision") == "deny", \
            f"Gemini CLI blocking failed! top-level decision={response.get('decision')}. " \
            f"Gemini CLI will NOT block this command without 'decision: deny' at top level.\n" \
            f"Full response: {json.dumps(response, indent=2)[:500]}"

        # Also verify Claude Code format for cross-platform compat
        assert "hookSpecificOutput" in response, "Missing hookSpecificOutput"
        assert response["hookSpecificOutput"]["permissionDecision"] == "deny", \
            f"Claude Code format mismatch: permissionDecision={response['hookSpecificOutput'].get('permissionDecision')}"

        # Verify reason is meaningful
        reason = response.get("reason", "")
        assert "cat" in reason.lower(), \
            f"Blocking reason doesn't mention 'cat': {reason[:200]}"

    def test_hook_safe_command_allowed_snakecase(self, clean_environment):
        """Test that safe commands are allowed with official snake_case format (NO COST)."""
        try:
            hook_script = find_hook_script()
        except FileNotFoundError as e:
            pytest.skip(str(e))

        # Official Gemini CLI format - safe command
        stdin_data = json.dumps({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "ls -la"},
            "session_id": "test-e2e-session",
            "cwd": "/tmp"
        })

        test_env = os.environ.copy()
        test_env["CLAUTORUN_USE_DAEMON"] = "0"

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env=test_env
        )

        assert result.returncode == 0, f"Hook failed: {result.stderr}"

        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {e}\nOutput: {result.stdout}")

        # Safe command should be allowed
        assert response.get("decision") == "allow", \
            f"Safe command blocked! decision={response.get('decision')}"


# Skip entire class if ENABLE_REAL_MONEY_TESTS not set
@pytest.mark.skipif(
    not ENABLE_REAL_MONEY_TESTS,
    reason="CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - these tests cost real money. "
           "Set CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 to run."
)
class TestGeminiCLIRealMoney:
    """Real Gemini CLI E2E tests that make actual API calls.

    ⚠️ WARNING: These tests cost real money!
    Requires: export CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    """

    def test_gemini_basic_response(self, gemini_cli_available):
        """Test basic Gemini CLI functionality (COSTS REAL MONEY < $0.001).

        This verifies Gemini CLI works and can respond to simple prompts.
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
        assert result.returncode == 0, \
            f"Gemini CLI failed with exit code {result.returncode}\nStderr: {result.stderr}"

        # Response should contain "Four" or "4"
        output_lower = result.stdout.lower()
        assert "four" in output_lower or "4" in output_lower, \
            f"Unexpected response: {result.stdout}"

    def test_gemini_extension_loaded(self, gemini_cli_available, gemini_extension_check):
        """Test that clautorun extension is loaded in Gemini (NO API COST).

        This verifies the extension is properly installed and registered.
        Note: `gemini extensions list` outputs to stderr, not stdout.
        """
        result = subprocess.run(
            ["gemini", "extensions", "list"],
            capture_output=True,
            text=True,
            timeout=30
        )

        assert result.returncode == 0, \
            f"Extension list failed: {result.stderr}"

        # Gemini CLI sends extension list to stderr (debug output stream)
        combined_output = result.stdout + result.stderr
        assert "clautorun" in combined_output, \
            f"clautorun extension not found. Output:\n{combined_output[:500]}"

        # Verify hooks file exists
        hooks_file = (
            Path.home() /
            ".gemini/extensions/clautorun-workspace/plugins/clautorun/hooks/gemini-hooks.json"
        )
        assert hooks_file.exists(), \
            f"Hooks file not found: {hooks_file}"

        # Verify hooks file is valid JSON
        try:
            with open(hooks_file) as f:
                hooks_config = json.load(f)

            assert "hooks" in hooks_config, \
                "hooks.json missing 'hooks' field"

            # Verify BeforeTool hook registered
            assert "BeforeTool" in hooks_config["hooks"], \
                "BeforeTool hook not registered"

        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON in hooks file: {e}")


class TestGeminiExtensionVerification:
    """Verify Gemini extension configuration (NO COST - file checks only)."""

    def test_extension_directory_structure(self):
        """Verify extension directory structure is correct."""
        base_dir = Path.home() / ".gemini/extensions/clautorun-workspace"

        if not base_dir.exists():
            pytest.skip(f"Extension directory not found: {base_dir}")

        # Check required directories
        required_dirs = [
            base_dir / "plugins/clautorun/hooks",
            base_dir / "plugins/clautorun/commands",
        ]

        for required_dir in required_dirs:
            assert required_dir.exists(), \
                f"Required directory missing: {required_dir}"

    def test_gemini_hooks_config_valid(self):
        """Verify gemini-hooks.json is valid and complete."""
        hooks_file = (
            Path.home() /
            ".gemini/extensions/clautorun-workspace/plugins/clautorun/hooks/gemini-hooks.json"
        )

        if not hooks_file.exists():
            pytest.skip(f"Hooks file not found: {hooks_file}")

        # Read and parse
        try:
            with open(hooks_file) as f:
                config = json.load(f)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON in hooks file: {e}")

        # Verify structure
        assert "description" in config, "Missing 'description' field"
        assert "hooks" in config, "Missing 'hooks' field"

        # Verify required hooks present
        required_hooks = ["SessionStart", "BeforeTool", "AfterTool"]
        for hook_name in required_hooks:
            assert hook_name in config["hooks"], \
                f"Missing required hook: {hook_name}"

        # Verify BeforeTool hook has correct matchers
        before_tool = config["hooks"]["BeforeTool"]
        assert len(before_tool) > 0, "BeforeTool hook has no matchers"

        # Check for Gemini tool names in matchers
        first_matcher = before_tool[0]
        assert "matcher" in first_matcher, "BeforeTool missing matcher"

        matcher_str = first_matcher["matcher"]
        gemini_tools = ["write_file", "bash_command", "run_shell_command", "exit_plan_mode"]
        assert any(tool in matcher_str for tool in gemini_tools), \
            f"Matcher doesn't include Gemini tool names: {matcher_str}"


# Documentation
__doc__ += """

## Test Categories

### Free Tests (NO COST - no API calls):
1. test_hook_script_exists - Verify hook_entry.py can be found
2. test_hook_sessionstart_event - Direct Python call to hook
3. test_hook_beforeagent_event_slash_command - Direct Python call
4. test_hook_beforetool_event_blocking - Verify blocking works
5. test_extension_directory_structure - File system checks
6. test_gemini_hooks_config_valid - JSON validation

### Real Money Tests (require CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1):
1. test_gemini_basic_response - Simple API call (< $0.001)
2. test_gemini_extension_loaded - Extension list (NO API cost)

## Running Tests

### Skip real money tests (default):
```bash
uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py -v
# Real money tests SKIPPED, free tests RUN
```

### Run real money tests:
```bash
export CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py -v
# All tests RUN (estimated cost: < $0.002)
```

### Run only free tests explicitly:
```bash
uv run pytest plugins/clautorun/tests/test_gemini_e2e_improved.py::TestGeminiHookEntryPointDirect -v
# Only free tests RUN (NO COST)
```

## Cost Breakdown

| Test | API Calls | Estimated Cost |
|------|-----------|----------------|
| test_hook_script_exists | 0 | $0.000 |
| test_hook_sessionstart_event | 0 | $0.000 |
| test_hook_beforeagent_event_slash_command | 0 | $0.000 |
| test_hook_beforetool_event_blocking | 0 | $0.000 |
| test_extension_directory_structure | 0 | $0.000 |
| test_gemini_hooks_config_valid | 0 | $0.000 |
| test_gemini_basic_response | 1 | < $0.001 |
| test_gemini_extension_loaded | 0 | $0.000 |
| **TOTAL** | 1 | **< $0.001** |

## Improvements Over Original

1. **Better Hook Testing**:
   - Tests hook entry point directly with simulated events
   - Verifies blocking logic works through hook
   - Tests all hook events (SessionStart, BeforeAgent, BeforeTool)

2. **Dynamic Path Discovery**:
   - Finds hook_entry.py in multiple locations
   - Skips gracefully if not found
   - Better error messages

3. **Environment Isolation**:
   - clean_environment fixture restores original environment
   - No environment pollution between tests
   - Each test gets fresh environment

4. **Extension Verification**:
   - Checks directory structure
   - Validates hooks JSON configuration
   - Verifies Gemini tool names in matchers

5. **Cost Protection**:
   - Only 1 test costs money (down from 5)
   - Better skip messages
   - Clear documentation of costs

6. **Better Assertions**:
   - Specific error messages
   - Validates JSON structure
   - Checks hook response format
"""
