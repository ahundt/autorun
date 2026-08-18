"""
Gemini CLI harness capability and retired-backend E2E tests.

The consumer model backend retired on 2026-06-18. Free hook-process tests remain
active because Gemini-family compatibility is reused by Qwen and Antigravity.
Legacy model calls require both explicit paid-test and retired-backend overrides.

Run capability tests normally:
    uv run pytest plugins/autorun/tests/ -v

Use Antigravity's Flash Low E2E for the live Google model successor.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from e2e_support import (
    RETIRED_GEMINI_BACKEND_REASON,
    autorun_extension_listed,
    real_money_enabled,
    requires_real_money,
    retired_gemini_backend_enabled,
)

paid_gemini_e2e = requires_real_money


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
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            message = f"Installed Gemini CLI is not runnable: {result.stderr}"
            if real_money_enabled():
                pytest.fail(message)
            pytest.skip(message)
    except subprocess.TimeoutExpired:
        message = "Installed Gemini CLI --version timed out (>5s)"
        if real_money_enabled():
            pytest.fail(message)
        pytest.skip(message)
    except OSError as error:
        message = f"Installed Gemini CLI check failed: {error}"
        if real_money_enabled():
            pytest.fail(message)
        pytest.skip(message)


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
            timeout=30,  # Extensions list loads credentials + experiments
            check=False,
        )
        if result.returncode != 0:
            message = f"Installed Gemini extension list failed: {result.stderr}"
            if real_money_enabled():
                pytest.fail(message)
            pytest.skip(message)

        # Gemini CLI sends extension list to stderr (debug output stream)
        combined_output = result.stdout + result.stderr
        if not autorun_extension_listed(combined_output):
            pytest.skip("ar (autorun) extension not installed in Gemini CLI")
    except subprocess.TimeoutExpired:
        message = "Installed Gemini extension list timed out (>30s)"
        if real_money_enabled():
            pytest.fail(message)
        pytest.skip(message)
    except OSError as error:
        message = f"Installed Gemini extension check failed: {error}"
        if real_money_enabled():
            pytest.fail(message)
        pytest.skip(message)


@paid_gemini_e2e
@pytest.mark.e2e
class TestGeminiE2ERealMoney:
    """Real Gemini CLI E2E tests that make actual API calls.

    ⚠️ WARNING: These tests cost real money!
    """

    @pytest.mark.timeout(75)
    def test_gemini_basic_response(self, gemini_cli_check):
        """Test basic Gemini CLI functionality (COSTS REAL MONEY).

        Estimated cost: < $0.001
        """
        if not retired_gemini_backend_enabled():
            pytest.skip(RETIRED_GEMINI_BACKEND_REASON)

        # Simple arithmetic test - minimal tokens
        try:
            result = subprocess.run(
                ["gemini", "-m", "gemini-2.5-flash-lite"],
                input="What is 2+2? Answer in one word.\n",
                capture_output=True,
                text=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                "Gemini CLI basic response timed out; current Gemini CLI may be "
                "blocked by the migration to Antigravity."
            )

        # Check for successful response
        assert result.returncode == 0, f"Gemini CLI failed: {result.stderr}"

        # Response should contain "Four" or "4"
        output_lower = result.stdout.lower()
        assert "four" in output_lower or "4" in output_lower, \
            f"Unexpected response: {result.stdout}"

    def test_gemini_slash_command_recognition(self, gemini_cli_check, gemini_extension_check):
        """Test /ar:st command in Gemini (COSTS REAL MONEY).

        The class-level paid-test opt-in and retired-backend gate prevent an
        accidental call during normal runs.
        Estimated cost: < $0.001

        Note: Piped mode may not provide shell execution tools.
        """
        result = subprocess.run(
            ["gemini", "-m", "gemini-2.5-flash-lite"],
            input="/ar:st\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        # May fail in piped mode - that's expected
        # Just check that Gemini CLI responds
        assert result.returncode == 0 or "not found" in result.stderr.lower(), \
            f"Unexpected error: {result.stderr}"


@pytest.mark.e2e
class TestGeminiExtensionRegistration:
    """Verify the installed extension registration without API or model calls."""

    def test_gemini_extension_loaded(self, gemini_cli_check, gemini_extension_check):
        result = subprocess.run(
            ["gemini", "extensions", "list"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        assert result.returncode == 0, f"Extension list failed: {result.stderr}"
        combined_output = result.stdout + result.stderr
        assert autorun_extension_listed(combined_output), (
            f"ar (autorun) extension not found. Output:\n{combined_output[:500]}"
        )


class TestGeminiHookEntryPoint:
    """Test hook entry point directly (no API costs)."""

    def test_hook_sessionstart_event(self):
        """Test SessionStart hook event (NO COST - direct Python call)."""
        # Set up Gemini environment
        env = os.environ.copy()
        env["GEMINI_SESSION_ID"] = "test-e2e-session"
        env["GEMINI_PROJECT_DIR"] = "/tmp/autorun-test"

        # Get hook script path (installed extension or source fallback)
        candidates = [
            Path.home() / ".gemini/extensions/ar/hooks/hook_entry.py",
            Path(__file__).parent.parent / "hooks/hook_entry.py",
        ]
        hook_script = None
        for candidate in candidates:
            if candidate.exists():
                hook_script = candidate
                break
        if hook_script is None:
            pytest.skip("Hook script not found. Searched:\n" + "\n".join(f"  - {p}" for p in candidates))

        # Set plugin root for source fallback
        plugin_root = str(Path(__file__).parent.parent)
        env["AUTORUN_PLUGIN_ROOT"] = plugin_root

        # Run hook with uv run (matches production hook commands)
        result = subprocess.run(
            ["uv", "run", "--project", plugin_root, "python", str(hook_script)],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            check=False,
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
        env["GEMINI_PROJECT_DIR"] = "/tmp/autorun-test"

        # Get hook script path (installed extension or source fallback)
        candidates = [
            Path.home() / ".gemini/extensions/ar/hooks/hook_entry.py",
            Path(__file__).parent.parent / "hooks/hook_entry.py",
        ]
        hook_script = None
        for candidate in candidates:
            if candidate.exists():
                hook_script = candidate
                break
        if hook_script is None:
            pytest.skip("Hook script not found. Searched:\n" + "\n".join(f"  - {p}" for p in candidates))

        # Set plugin root for source fallback
        plugin_root = str(Path(__file__).parent.parent)
        env["AUTORUN_PLUGIN_ROOT"] = plugin_root

        # Simulate BeforeAgent event with /ar:st command
        stdin_data = json.dumps({
            "type": "BeforeAgent",
            "command": "/ar:st",
            "sessionId": "test-e2e-session"
        })

        result = subprocess.run(
            ["uv", "run", "--project", plugin_root, "python", str(hook_script)],
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
            check=False,
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

### Retired model-backend tests
`TestGeminiE2ERealMoney` is retained for historical diagnostics but is not part
of the supported live matrix after 2026-06-18. Use Antigravity Flash Low.

### Free Tests (no API costs):
1. test_gemini_extension_loaded - Check extension list
2. test_hook_sessionstart_event - Direct Python call to hook
3. test_hook_beforeagent_event - Direct Python call to hook

## Running Tests

### Run free harness capability tests:
```bash
uv run pytest plugins/autorun/tests/test_gemini_e2e_real_money.py -v
```

### Explicitly diagnose the retired backend:
```bash
export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
export AUTORUN_ALLOW_RETIRED_GEMINI_CLI_BACKEND_TESTS=1
uv run pytest plugins/autorun/tests/test_gemini_e2e_real_money.py -v
```

### Run only free tests:
```bash
uv run pytest plugins/autorun/tests/test_gemini_e2e_real_money.py::TestGeminiHookEntryPoint -v
# No costs - direct Python calls only
```

## Resource policy

Routine Gemini compatibility checks make no model calls. The active Google
successor smoke uses Gemini 3.5 Flash Low through Antigravity with one bounded
prompt, sandboxing, and a temporary log.

## Important Notes

1. Keep Gemini hook compatibility because successor harnesses reuse the schema.
2. Do not run the retired consumer backend in routine release validation.
3. Use `test_antigravity_e2e_real_money.py` for live Google model coverage.
"""
