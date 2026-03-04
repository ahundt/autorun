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
    export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/autorun/tests/test_gemini_e2e_improved.py -v

To skip these tests (default):
    uv run pytest plugins/autorun/tests/ -v
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

pytestmark = pytest.mark.e2e

# Check for AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY flag
ENABLE_REAL_MONEY_TESTS = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"


def find_hook_script() -> Path:
    """Dynamically find hook_entry.py script.

    Search order:
        1. Installed Gemini extension (production)
        2. Development source dir (from test file location)
        3. Absolute path for dev workspace

    Returns:
        Path to hook_entry.py

    Raises:
        FileNotFoundError: If hook script not found
    """
    possible_locations = [
        Path.home() / ".gemini/extensions/autorun-workspace/plugins/autorun/hooks/hook_entry.py",
        Path(__file__).parent.parent / "hooks/hook_entry.py",
        Path.home() / ".claude/autorun/plugins/autorun/hooks/hook_entry.py",
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
    """Verify autorun extension is loaded in Gemini.

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
        if "autorun" not in combined_output:
            pytest.skip("autorun extension not installed in Gemini CLI")

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
    os.environ["GEMINI_PROJECT_DIR"] = "/tmp/autorun-gemini-test"

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

        # AUTORUN_USE_DAEMON=0 → run_direct() in __main__.py → exercises canonical
        # plugins.py code without connecting to the live daemon socket.
        test_env = {**os.environ, "AUTORUN_USE_DAEMON": "0"}
        result = subprocess.run(
            ["python3", str(hook_script)],
            capture_output=True,
            text=True,
            timeout=10,
            env=test_env
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
        """Test BeforeAgent hook event with /ar:st command (NO COST)."""
        try:
            hook_script = find_hook_script()
        except FileNotFoundError as e:
            pytest.skip(str(e))

        # Simulate BeforeAgent event with /ar:st command
        stdin_data = json.dumps({
            "type": "BeforeAgent",
            "command": "/ar:st",
            "sessionId": "test-e2e-session"
        })

        # AUTORUN_USE_DAEMON=0 → run_direct() in __main__.py → exercises canonical
        # plugins.py code without connecting to the live daemon socket.
        test_env = {**os.environ, "AUTORUN_USE_DAEMON": "0"}
        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=10,
            env=test_env
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

        # AUTORUN_USE_DAEMON=0 → run_direct() in __main__.py → exercises canonical
        # plugins.py:check_blocked_commands without connecting to the live daemon socket.
        # GEMINI_SESSION_ID (set by clean_environment) → detect_cli_type → "gemini" → exit 0.
        # Bug #4669 workaround applies to Claude Code only, not Gemini; Gemini always exits 0.
        test_env = {**os.environ, "AUTORUN_USE_DAEMON": "0"}

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=15,
            env=test_env
        )

        assert result.returncode == 0, \
            f"Hook failed: returncode={result.returncode} (Gemini path must exit 0, not 2)\nstderr: {result.stderr}"

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

        # AUTORUN_USE_DAEMON=0 → run_direct() in __main__.py → exercises canonical
        # plugins.py:check_blocked_commands without connecting to the live daemon socket.
        # GEMINI_SESSION_ID (set by clean_environment) → detect_cli_type → "gemini" → exit 0.
        # Bug #4669 workaround applies to Claude Code only, not Gemini; Gemini always exits 0.
        test_env = {**os.environ, "AUTORUN_USE_DAEMON": "0"}

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=15,
            env=test_env
        )

        assert result.returncode == 0, \
            f"Hook failed: returncode={result.returncode} (Gemini path must exit 0)\nstderr: {result.stderr}"

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

        # AUTORUN_USE_DAEMON=0 → run_direct() in __main__.py → exercises canonical
        # plugins.py:check_blocked_commands without connecting to the live daemon socket.
        test_env = {**os.environ, "AUTORUN_USE_DAEMON": "0"}

        result = subprocess.run(
            ["python3", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=15,
            env=test_env
        )

        assert result.returncode == 0, f"Hook failed: {result.stderr}"

        # Safe command: dispatch() returns None → output_hook_response exits 0 with NO stdout.
        # Empty stdout is the correct pass-through behavior (allows RTK to apply updatedInput).
        # Accept either empty stdout (pass-through) or explicit allow JSON (both are valid).
        if result.stdout.strip():
            try:
                response = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                pytest.fail(f"Invalid JSON response: {e}\nOutput: {result.stdout}")
            decision = response.get("decision") or response.get("hookSpecificOutput", {}).get("permissionDecision")
            assert decision in ("allow", "approve", None), \
                f"Safe command blocked! decision={decision!r}"
        # else: empty stdout = pass-through allow (correct behavior)


# Skip entire class if ENABLE_REAL_MONEY_TESTS not set
@pytest.mark.skipif(
    not ENABLE_REAL_MONEY_TESTS,
    reason="AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - these tests cost real money. "
           "Set AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 to run."
)
class TestGeminiCLIRealMoney:
    """Real Gemini CLI E2E tests that make actual API calls.

    ⚠️ WARNING: These tests cost real money!
    Requires: export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
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
        """Test that autorun extension is loaded in Gemini (NO API COST).

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
        assert "autorun" in combined_output, \
            f"autorun extension not found. Output:\n{combined_output[:500]}"

        # Verify hooks file exists (installed extension or source)
        hooks_candidates = [
            Path.home() / ".gemini/extensions/autorun-workspace/plugins/autorun/hooks/hooks.json",
            Path(__file__).parent.parent / "hooks/hooks.json",
        ]
        hooks_file = None
        for candidate in hooks_candidates:
            if candidate.exists():
                hooks_file = candidate
                break
        assert hooks_file is not None, \
            f"hooks.json not found. Searched:\n" + "\n".join(f"  - {p}" for p in hooks_candidates)

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


class TestGeminiExtensionInstalledHook:
    """Test the INSTALLED hook (Gemini extension copy, not dev repo).

    NO API COST - invokes the hook_entry.py directly with subprocess,
    exactly as Gemini CLI would. Uses the extension's installed copy at
    ~/.gemini/extensions/autorun-workspace/ to verify that the deployed
    code correctly blocks dangerous commands and permits safe ones.

    This is the closest E2E validation to real Gemini CLI behavior without
    requiring the AI to actually invoke tools (which is unreliable in piped mode).
    """

    @pytest.fixture
    def extension_hook(self):
        """Get path to hook_entry.py (installed extension or source fallback)."""
        candidates = [
            Path.home() / ".gemini/extensions/autorun-workspace/plugins/autorun/hooks/hook_entry.py",
            Path(__file__).parent.parent / "hooks/hook_entry.py",
        ]
        for hook_path in candidates:
            if hook_path.exists():
                return hook_path
        pytest.skip(
            f"Gemini hook_entry.py not found. Searched:\n"
            + "\n".join(f"  - {p}" for p in candidates)
        )

    def _run_hook(self, hook_path: Path, payload: dict) -> dict:
        """Run hook_entry.py as subprocess with JSON payload, return parsed response.

        This mirrors exactly how Gemini CLI invokes hooks: subprocess with
        JSON on stdin, expecting JSON on stdout. Uses uv run for UV workspace.
        """
        env = os.environ.copy()
        env["GEMINI_SESSION_ID"] = "test-installed-hook"
        env["GEMINI_PROJECT_DIR"] = "/tmp/autorun-gemini-test"
        # Set plugin root so hook_entry.py can find the plugin source
        plugin_root = str(Path(__file__).parent.parent)
        env["AUTORUN_PLUGIN_ROOT"] = plugin_root

        # Use uv run for UV workspace (matches production hook commands)
        cmd = ["uv", "run", "--project", plugin_root, "python", str(hook_path)]

        result = subprocess.run(
            cmd,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )

        assert result.returncode == 0, \
            f"Hook failed with exit code {result.returncode}\nStderr: {result.stderr}"

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(
                f"Invalid JSON from installed hook: {e}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

    def test_installed_hook_blocks_cat(self, extension_hook):
        """Verify INSTALLED hook blocks 'cat' via bash_command (Gemini tool name).

        This uses the exact JSON format Gemini CLI sends: snake_case keys,
        hook_event_name='BeforeTool', tool_name='bash_command'.
        """
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "cat /etc/passwd"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        # Check canonical permissionDecision (raw value, always deny/allow)
        hso = response.get("hookSpecificOutput", {})
        assert hso.get("permissionDecision") == "deny", \
            f"hookSpecificOutput.permissionDecision not 'deny': {hso}"

        # Top-level 'decision' is CLI-mapped: "deny" (Gemini) or "block" (Claude)
        assert response.get("decision") in ("deny", "block"), \
            f"INSTALLED hook did NOT block 'cat'! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_blocks_head(self, extension_hook):
        """Verify INSTALLED hook blocks 'head' via bash_command."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "head -20 /etc/hosts"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert response.get("decision") in ("deny", "block"), \
            f"INSTALLED hook did NOT block 'head'! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_blocks_tail(self, extension_hook):
        """Verify INSTALLED hook blocks 'tail' via bash_command."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "tail -f /var/log/system.log"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert response.get("decision") in ("deny", "block"), \
            f"INSTALLED hook did NOT block 'tail'! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_blocks_run_shell_command(self, extension_hook):
        """Verify INSTALLED hook blocks via 'run_shell_command' tool name."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "run_shell_command",
            "tool_input": {"command": "cat /etc/shadow"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert response.get("decision") in ("deny", "block"), \
            f"INSTALLED hook did NOT block via run_shell_command! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_allows_safe_command(self, extension_hook):
        """Verify INSTALLED hook allows safe commands like 'ls'."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "ls -la /tmp"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert response.get("decision") in ("allow", "approve"), \
            f"INSTALLED hook incorrectly blocked safe 'ls'! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_allows_piped_cat(self, extension_hook):
        """Verify INSTALLED hook allows piped 'cat' (e.g., 'echo foo | cat')."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "echo hello | cat"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert response.get("decision") in ("allow", "approve"), \
            f"INSTALLED hook incorrectly blocked piped cat! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_allows_git_status(self, extension_hook):
        """Verify INSTALLED hook allows git status."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "git status"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert response.get("decision") in ("allow", "approve"), \
            f"INSTALLED hook incorrectly blocked git status! Response: {json.dumps(response, indent=2)}"

    def test_installed_hook_dual_format_consistency(self, extension_hook):
        """Verify INSTALLED hook returns BOTH Gemini and Claude Code decision formats.

        Critical cross-platform test: the response must contain:
        - Top-level 'decision' (for Gemini CLI)
        - hookSpecificOutput.permissionDecision (for Claude Code)
        - Both must agree on allow/deny
        """
        # Test with a blocked command
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "cat README.md"},
            "session_id": "test-installed-hook",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        # Both formats must be present
        top_level_decision = response.get("decision")
        hso = response.get("hookSpecificOutput", {})
        hso_decision = hso.get("permissionDecision")

        assert top_level_decision is not None, \
            "Missing top-level 'decision' (needed for Gemini CLI)"
        assert hso_decision is not None, \
            "Missing hookSpecificOutput.permissionDecision (needed for Claude Code)"

        # Both must agree semantically (top-level may use CLI-mapped values)
        # Claude: "block"/"approve", Gemini: "deny"/"allow", Raw: "deny"/"allow"
        deny_values = {"deny", "block"}
        allow_values = {"allow", "approve"}
        if hso_decision == "deny":
            assert top_level_decision in deny_values, \
                f"Decision mismatch! top-level={top_level_decision} should be deny/block, hso={hso_decision}"
        else:
            assert top_level_decision in allow_values, \
                f"Decision mismatch! top-level={top_level_decision} should be allow/approve, hso={hso_decision}"


class TestGeminiWriteFileBlocking:
    """Test write_file and edit_file tool blocking through installed hook.

    Validates that Gemini CLI tool names (write_file, edit_file, replace)
    are properly handled in justify/strict AutoFile modes.
    NO API COST - direct hook invocation.
    """

    @pytest.fixture
    def extension_hook(self):
        """Get path to hook_entry.py (installed extension or source fallback)."""
        candidates = [
            Path.home() / ".gemini/extensions/autorun-workspace/plugins/autorun/hooks/hook_entry.py",
            Path(__file__).parent.parent / "hooks/hook_entry.py",
        ]
        for hook_path in candidates:
            if hook_path.exists():
                return hook_path
        pytest.skip(
            f"Gemini hook_entry.py not found. Searched:\n"
            + "\n".join(f"  - {p}" for p in candidates)
        )

    def _run_hook(self, hook_path: Path, payload: dict) -> dict:
        """Run hook_entry.py as subprocess with JSON payload."""
        env = os.environ.copy()
        env["GEMINI_SESSION_ID"] = "test-write-file"
        env["GEMINI_PROJECT_DIR"] = "/tmp/autorun-gemini-test"
        # Set plugin root so hook_entry.py can find the plugin source
        plugin_root = str(Path(__file__).parent.parent)
        env["AUTORUN_PLUGIN_ROOT"] = plugin_root

        # Use uv run for UV workspace (matches production hook commands)
        cmd = ["uv", "run", "--project", plugin_root, "python", str(hook_path)]

        result = subprocess.run(
            cmd,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )

        assert result.returncode == 0, \
            f"Hook failed: {result.stderr}"

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON: {e}\nstdout: {result.stdout}")

    def test_write_file_returns_valid_response(self, extension_hook):
        """Verify write_file tool returns a well-formed response with both formats."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "write_file",
            "tool_input": {"file_path": "/tmp/test.txt", "content": "hello"},
            "session_id": "test-write-file",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        # Must have both decision formats
        assert "decision" in response, \
            f"Missing top-level decision for write_file: {response}"
        assert "hookSpecificOutput" in response, \
            f"Missing hookSpecificOutput for write_file: {response}"
        # Top-level decision may be CLI-mapped (block/approve for Claude, deny/allow for Gemini)
        # while permissionDecision is always raw (deny/allow). Check semantic agreement.
        raw = response["hookSpecificOutput"]["permissionDecision"]
        top = response["decision"]
        if raw in ("deny", "block"):
            assert top in ("deny", "block"), \
                f"Decision format mismatch: top={top}, raw={raw}"
        else:
            assert top in ("allow", "approve"), \
                f"Decision format mismatch: top={top}, raw={raw}"

    def test_edit_file_returns_valid_response(self, extension_hook):
        """Verify edit_file tool returns a well-formed response."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "edit_file",
            "tool_input": {"file_path": "/tmp/test.txt", "old_string": "a", "new_string": "b"},
            "session_id": "test-edit-file",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert "decision" in response, \
            f"Missing top-level decision for edit_file: {response}"
        assert "hookSpecificOutput" in response, \
            f"Missing hookSpecificOutput for edit_file: {response}"

    def test_exit_plan_mode_returns_valid_response(self, extension_hook):
        """Verify exit_plan_mode tool returns a well-formed response."""
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "exit_plan_mode",
            "tool_input": {},
            "session_id": "test-exit-plan",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        assert "decision" in response, \
            f"Missing top-level decision for exit_plan_mode: {response}"

    def test_response_json_schema_completeness(self, extension_hook):
        """Verify response contains all required fields for both CLIs.

        Claude Code required fields: continue, stopReason, suppressOutput,
            systemMessage, hookSpecificOutput.permissionDecision
        Gemini CLI required fields: decision, reason
        """
        payload = {
            "hook_event_name": "BeforeTool",
            "source": "gemini",
            "tool_name": "bash_command",
            "tool_input": {"command": "echo test"},
            "session_id": "test-schema",
            "cwd": "/tmp",
        }
        response = self._run_hook(extension_hook, payload)

        # Claude Code required fields
        claude_required = ["continue", "stopReason", "suppressOutput", "systemMessage",
                          "hookSpecificOutput"]
        for field in claude_required:
            assert field in response, \
                f"Missing Claude Code required field '{field}': {list(response.keys())}"

        hso = response["hookSpecificOutput"]
        hso_required = ["hookEventName", "permissionDecision", "permissionDecisionReason"]
        for field in hso_required:
            assert field in hso, \
                f"Missing hookSpecificOutput field '{field}': {list(hso.keys())}"

        # Gemini CLI required fields
        gemini_required = ["decision", "reason"]
        for field in gemini_required:
            assert field in response, \
                f"Missing Gemini CLI required field '{field}': {list(response.keys())}"


class TestGeminiHighQualityMocks:
    """High-quality mock tests for when real money flag is disabled.

    These tests simulate the exact JSON payloads that Gemini CLI v0.26+
    sends to hooks, based on official documentation at
    geminicli.com/docs/hooks/reference/. NO API COST.
    """

    @classmethod
    def setup_class(cls):
        """Import plugins to register handlers (same as daemon startup)."""
        import sys
        src_dir = str(Path(__file__).parent.parent / "src")
        if src_dir not in sys.path:
            sys.path.insert(0, src_dir)
        # Import plugins to register handlers on the app
        from autorun import plugins  # noqa: F401

    def _simulate_hook(self, payload: dict) -> dict:
        """Simulate hook processing through the full Python code path.

        Uses the dev repo's code directly (no subprocess), testing the
        normalization + dispatch + response pipeline. Requires handlers
        to be registered via plugins import (done in setup_class).
        """
        from autorun.core import EventContext, normalize_hook_payload, app
        from autorun.config import detect_cli_type

        normalized = normalize_hook_payload(payload)
        cli_type = detect_cli_type(payload)
        ctx = EventContext(
            session_id=normalized["session_id"] or "mock-session",
            event=normalized["hook_event_name"],
            prompt=normalized["prompt"],
            tool_name=normalized["tool_name"],
            tool_input=normalized["tool_input"],
            tool_result=normalized["tool_result"],
            session_transcript=normalized["session_transcript"],
            cli_type=cli_type
        )
        return app.dispatch(ctx)

    def test_mock_gemini_rm_blocked(self):
        """Mock: 'rm -rf /' blocked via bash_command."""
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "rm -rf /"},
            "session_id": "mock-1",
        })
        assert response.get("decision") == "deny", \
            f"rm -rf should be blocked: {response.get('decision')}"

    def test_mock_gemini_git_reset_hard_blocked(self):
        """Mock: 'git reset --hard' blocked when unstaged changes exist.

        The git reset --hard integration uses when: _has_unstaged_changes,
        which checks real git state. We create a temporary git repo with
        actual unstaged changes so the predicate fires naturally.
        """
        import subprocess
        import tempfile
        from autorun.integrations import invalidate_caches

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a real git repo with a commit and unstaged changes
            subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@test.com"],
                           cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"],
                           cwd=tmpdir, capture_output=True)
            test_file = Path(tmpdir) / "file.txt"
            test_file.write_text("initial")
            subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"],
                           cwd=tmpdir, capture_output=True)
            # Create unstaged change so _has_unstaged_changes returns True
            test_file.write_text("modified")

            original_cwd = os.getcwd()
            os.chdir(tmpdir)
            invalidate_caches()
            try:
                response = self._simulate_hook({
                    "hook_event_name": "BeforeTool",
                    "tool_name": "bash_command",
                    "tool_input": {"command": "git reset --hard HEAD~5"},
                    "session_id": "mock-2",
                })
            finally:
                os.chdir(original_cwd)
                invalidate_caches()

        assert response.get("decision") == "deny", \
            f"git reset --hard should be blocked: {response.get('decision')}"

    def test_mock_gemini_python_allowed(self):
        """Mock: 'python3 script.py' allowed (pass-through — no rules match)."""
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "python3 test.py"},
            "session_id": "mock-3",
        })
        # dispatch() returns None for unmatched commands (pass-through = allowed).
        # Claude Code ignores hooks with no stdout output — the tool runs unblocked.
        assert response is None or response.get("decision") in ("allow", "approve"), \
            f"python3 should be allowed (None=pass-through or allow): {response!r}"

    def test_mock_gemini_npm_allowed(self):
        """Mock: 'npm test' allowed (pass-through — no rules match)."""
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "run_shell_command",
            "tool_input": {"command": "npm test"},
            "session_id": "mock-4",
        })
        # dispatch() returns None for unmatched commands (pass-through = allowed).
        assert response is None or response.get("decision") in ("allow", "approve"), \
            f"npm test should be allowed (None=pass-through or allow): {response!r}"

    def test_mock_gemini_sed_blocked(self):
        """Mock: 'sed -i' blocked (direct file modification)."""
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "sed -i 's/foo/bar/g' file.txt"},
            "session_id": "mock-5",
        })
        assert response.get("decision") == "deny", \
            f"sed should be blocked: {response.get('decision')}"

    def test_mock_gemini_find_blocked(self):
        """Mock: 'find' blocked (use Glob instead)."""
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "find . -name '*.py'"},
            "session_id": "mock-6",
        })
        assert response.get("decision") == "deny", \
            f"find should be blocked: {response.get('decision')}"

    def test_mock_dual_format_on_deny(self):
        """Mock: denied response has both Gemini and Claude Code formats."""
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "cat secret.txt"},
            "session_id": "mock-7",
        })
        # Gemini format
        assert response.get("decision") == "deny"
        assert "reason" in response
        # Claude Code format
        hso = response.get("hookSpecificOutput", {})
        assert hso.get("permissionDecision") == "deny"
        assert hso.get("permissionDecisionReason") != ""

    def test_mock_dual_format_on_allow(self):
        """Mock: allowed response (with warning message) has both Gemini and Claude Code formats.

        Uses 'git status' which triggers the warn integration → ctx.respond("allow", msg).
        This tests that WHEN autorun provides an explicit allow response (not pass-through),
        it includes both Gemini format (decision) and Claude Code format (hookSpecificOutput).
        Unmatched-command pass-through (None) is tested in test_mock_gemini_python_allowed.
        """
        response = self._simulate_hook({
            "hook_event_name": "BeforeTool",
            "tool_name": "bash_command",
            "tool_input": {"command": "git status"},
            "session_id": "mock-8",
        })
        # git status triggers warn integration → explicit allow response (not None)
        assert response is not None, "git status must trigger warn integration (not pass-through)"
        # Gemini format
        assert response.get("decision") in ("allow", "approve"), \
            f"git warn must be allow, got: {response.get('decision')!r}"
        # Claude Code format
        hso = response.get("hookSpecificOutput", {})
        assert hso.get("permissionDecision") == "allow"


class TestGeminiExtensionVerification:
    """Verify Gemini extension configuration (NO COST - file checks only).

    Falls back to source directory when extension is not installed.
    """

    def _find_plugin_dir(self) -> Path:
        """Find plugin directory (installed extension or source fallback)."""
        candidates = [
            Path.home() / ".gemini/extensions/autorun-workspace/plugins/autorun",
            Path(__file__).parent.parent,  # Source dir
        ]
        for candidate in candidates:
            if (candidate / "hooks").exists():
                return candidate
        pytest.skip(
            "Plugin directory not found. Searched:\n"
            + "\n".join(f"  - {p}" for p in candidates)
        )

    def test_extension_directory_structure(self):
        """Verify plugin directory structure is correct."""
        base_dir = self._find_plugin_dir()

        # Check required directories
        required_dirs = [
            base_dir / "hooks",
            base_dir / "commands",
        ]

        for required_dir in required_dirs:
            assert required_dir.exists(), \
                f"Required directory missing: {required_dir}"

    def test_gemini_hooks_config_valid(self):
        """Verify hooks.json is valid and complete."""
        base_dir = self._find_plugin_dir()
        hooks_file = base_dir / "hooks/hooks.json"
        assert hooks_file.exists(), f"hooks.json not found at: {hooks_file}"

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

### Real Money Tests (require AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1):
1. test_gemini_basic_response - Simple API call (< $0.001)
2. test_gemini_extension_loaded - Extension list (NO API cost)

## Running Tests

### Skip real money tests (default):
```bash
uv run pytest plugins/autorun/tests/test_gemini_e2e_improved.py -v
# Real money tests SKIPPED, free tests RUN
```

### Run real money tests:
```bash
export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
uv run pytest plugins/autorun/tests/test_gemini_e2e_improved.py -v
# All tests RUN (estimated cost: < $0.002)
```

### Run only free tests explicitly:
```bash
uv run pytest plugins/autorun/tests/test_gemini_e2e_improved.py::TestGeminiHookEntryPointDirect -v
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
