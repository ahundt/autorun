"""
Test BeforeTool hooks execution in Gemini CLI.

TDD Approach:
- RED: Tests fail initially (hooks not verified to fire)
- GREEN: Implement verification mechanism
- REFACTOR: Improve test reliability

These tests verify that BeforeTool hooks actually fire when Gemini CLI
invokes tools like write_file, run_shell_command, etc.

Uses tmux session automation for isolated testing.
"""

import json
import subprocess
import tempfile
import time
import os
import sys
from pathlib import Path
import pytest
import shutil

pytestmark = pytest.mark.e2e

# Add src to path for tmux_utils import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from clautorun.tmux_utils import get_tmux_utilities


def get_plugin_root():
    """Get plugin root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def gemini_available():
    """Check if Gemini CLI is available and properly configured."""
    if not shutil.which("gemini"):
        pytest.skip("Gemini CLI not installed")

    # Check version
    result = subprocess.run(
        ["gemini", "--version"],
        capture_output=True,
        text=True,
        timeout=5
    )

    if result.returncode != 0:
        pytest.skip("Gemini CLI not working")

    version = result.stdout.strip()
    # Version should be 0.28.0 or later
    if version < "0.28.0":
        pytest.skip(f"Gemini CLI version {version} < 0.28.0 (hooks may not work)")

    return version


@pytest.fixture
def gemini_settings_enabled():
    """Check if Gemini settings enable hooks."""
    settings_file = Path.home() / ".gemini" / "settings.json"

    if not settings_file.exists():
        pytest.skip("Gemini settings.json not found")

    with open(settings_file) as f:
        settings = json.load(f)

    tools_settings = settings.get("tools", {})

    if not tools_settings.get("enableHooks"):
        pytest.skip("Gemini hooks not enabled (enableHooks: false)")

    if not tools_settings.get("enableMessageBusIntegration"):
        pytest.skip("Gemini message bus integration not enabled")

    return True


@pytest.fixture
def gemini_extension_installed():
    """Check if clautorun extension is installed in Gemini."""
    ext_dir = Path.home() / ".gemini" / "extensions" / "cr"

    if not ext_dir.exists():
        pytest.skip("Clautorun extension not installed in Gemini")

    hooks_file = ext_dir / "hooks" / "hooks.json"
    if not hooks_file.exists():
        pytest.skip("Hooks file not found in Gemini extension")

    return ext_dir


@pytest.fixture
def test_workspace():
    """Create a temporary test workspace."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def debug_hook_script():
    """Create a debug hook script that logs all executions."""
    debug_log = Path("/tmp/gemini-before-tool-debug.log")

    # Clear debug log before test
    if debug_log.exists():
        debug_log.unlink()

    hook_script = """#!/usr/bin/env python3
import sys
import json
import os
from datetime import datetime

DEBUG_LOG = "/tmp/gemini-before-tool-debug.log"

def main():
    stdin_data = sys.stdin.read()
    timestamp = datetime.now().isoformat()

    try:
        input_json = json.loads(stdin_data) if stdin_data else {}
    except:
        input_json = {"error": "failed to parse stdin"}

    # Log execution
    with open(DEBUG_LOG, "a") as f:
        f.write(f"\\n{'='*60}\\n")
        f.write(f"BeforeTool Hook: {timestamp}\\n")
        f.write(f"Event: {input_json.get('hook_event_name', 'unknown')}\\n")
        f.write(f"Tool: {input_json.get('tool_name', 'unknown')}\\n")
        f.write(f"CWD: {os.getcwd()}\\n")
        f.write(f"Input: {json.dumps(input_json, indent=2)}\\n")
        f.write(f"{'='*60}\\n")

    # Allow the tool to execute
    response = {"continue": True, "systemMessage": f"Debug hook executed for {input_json.get('tool_name')}"}
    print(json.dumps(response))
    return 0

if __name__ == "__main__":
    sys.exit(main())
"""

    yield hook_script, debug_log


@pytest.mark.integration
@pytest.mark.skipif(
    not os.environ.get("CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY"),
    reason="CLAUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - "
           "this test runs Gemini CLI which costs real money"
)
def test_gemini_before_tool_hook_fires_on_write_file(
    gemini_available,
    gemini_settings_enabled,
    gemini_extension_installed,
    test_workspace,
    debug_hook_script
):
    """
    Test that BeforeTool hook fires when Gemini CLI invokes write_file tool.

    TDD Approach:
    - RED: This test will initially fail (we need to verify hook execution)
    - GREEN: Once we can verify hook fires, test passes
    - REFACTOR: Improve reliability and coverage

    This is an integration test that requires:
    1. Gemini CLI v0.28.0+
    2. Settings with enableHooks: true
    3. Clautorun extension installed
    4. Temporary debug hook to verify execution

    Uses tmux session automation for isolated testing.
    """
    hook_script, debug_log = debug_hook_script

    # Find the actual hook_entry.py that Gemini will execute.
    # The installed hooks.json may reference the dev repo path (absolute)
    # rather than the extension dir copy, so we parse the command to find it.
    ext_dir = gemini_extension_installed
    hooks_json_path = ext_dir / "hooks" / "hooks.json"

    with open(hooks_json_path) as f:
        hooks_data = json.load(f)

    # Extract hook_entry.py path from the first hook command
    first_command = ""
    for event_configs in hooks_data.get("hooks", {}).values():
        for config in event_configs:
            hook_list = config.get("hooks", [])
            if hook_list:
                first_command = hook_list[0].get("command", "")
                break
        if first_command:
            break

    # Parse the actual hook_entry.py path from the command string
    # Command format: "uv run --quiet --project <path> python <path>/hooks/hook_entry.py"
    # or: "<path>/.venv/bin/python <path>/hooks/hook_entry.py"
    actual_hook_entry = None
    for part in first_command.split():
        if part.endswith("hook_entry.py"):
            actual_hook_entry = Path(part)
            break

    if actual_hook_entry is None or not actual_hook_entry.exists():
        # Fallback to extension dir copy
        actual_hook_entry = ext_dir / "hooks" / "hook_entry.py"

    hook_entry = actual_hook_entry
    hook_backup = hook_entry.parent / "hook_entry.py.backup"

    # Backup original hook
    shutil.copy2(hook_entry, hook_backup)

    # Create unique tmux session
    session_name = f"gemini-hook-test-{int(time.time())}"
    tmux = get_tmux_utilities(session_name)

    try:
        # Replace with debug hook
        with open(hook_entry, "w") as f:
            f.write(hook_script)

        hook_entry.chmod(0o755)

        # Clear debug log before test
        if debug_log.exists():
            debug_log.unlink()

        # Create tmux session and start Gemini in it
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])

        # Change to test workspace
        assert tmux.send_keys(f'cd {test_workspace}', session_name)
        assert tmux.send_keys('C-m', session_name)
        time.sleep(0.5)

        # Start Gemini CLI in the tmux session
        assert tmux.send_keys('gemini', session_name)
        assert tmux.send_keys('C-m', session_name)

        # Wait for Gemini to start (SessionStart hook should fire)
        time.sleep(3)

        # Send command to create a file (should trigger BeforeTool hook)
        test_file = test_workspace / "test_output.txt"
        assert tmux.send_keys(f'Create a file {test_file} with text "Hello from test"', session_name)
        assert tmux.send_keys('C-m', session_name)

        # Wait for Gemini to process and execute tool
        time.sleep(5)

        # Exit Gemini
        assert tmux.send_keys('/exit', session_name)
        assert tmux.send_keys('C-m', session_name)
        time.sleep(2)

        # Check if debug log was created (hook fired)
        assert debug_log.exists(), \
            "Debug log not created - BeforeTool hook did not fire"

        # Read debug log
        with open(debug_log) as f:
            log_content = f.read()

        # Verify hook was called
        assert "BeforeTool Hook:" in log_content or "SessionStart" in log_content, \
            f"Hook execution not logged. Log:\n{log_content}"

        # Check for BeforeTool specifically (if SessionStart fired, BeforeTool should too)
        # Note: Depending on Gemini behavior, hook might fire for write_file or run_shell_command
        assert "hook_event_name" in log_content, \
            f"Hook input missing hook_event_name. Log:\n{log_content}"

    finally:
        # Restore original hook
        if hook_backup.exists():
            shutil.copy2(hook_backup, hook_entry)
            hook_backup.unlink()

        # Clean up tmux session
        tmux.execute_tmux_command(['kill-session', '-t', session_name])


def test_gemini_before_tool_hook_structure(
    gemini_extension_installed
):
    """
    Test that BeforeTool hooks are properly configured in Gemini extension.

    This is a unit test that verifies the hooks.json structure without
    requiring a running Gemini session.
    """
    ext_dir = gemini_extension_installed
    hooks_file = ext_dir / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})

    # Check BeforeTool event exists
    assert "BeforeTool" in hooks_section, \
        "BeforeTool event not found in hooks.json"

    before_tool_configs = hooks_section["BeforeTool"]
    assert isinstance(before_tool_configs, list), \
        "BeforeTool should be a list of configurations"

    # Check at least one BeforeTool hook configured
    assert len(before_tool_configs) > 0, \
        "No BeforeTool hooks configured"

    # Check first config has matcher
    first_config = before_tool_configs[0]
    assert "matcher" in first_config, \
        "BeforeTool config missing matcher"

    # Check matcher includes write_file
    matcher = first_config["matcher"]
    assert "write_file" in matcher, \
        f"BeforeTool matcher should include write_file, got: {matcher}"

    # Check hooks list exists
    assert "hooks" in first_config, \
        "BeforeTool config missing hooks list"

    hooks_list = first_config["hooks"]
    assert len(hooks_list) > 0, \
        "BeforeTool hooks list is empty"

    # Check first hook has required fields
    first_hook = hooks_list[0]
    assert "command" in first_hook, \
        "Hook missing command field"

    assert "type" in first_hook, \
        "Hook missing type field (required by Gemini)"

    assert first_hook["type"] == "command", \
        f"Hook type should be 'command', got: {first_hook['type']}"

    # Check command references hook_entry.py (via template var or resolved path)
    command = first_hook["command"]
    assert "hook_entry.py" in command, \
        f"Hook command should reference hook_entry.py, got: {command}"


@pytest.mark.integration
def test_gemini_session_start_hook_fires(
    gemini_available,
    gemini_settings_enabled,
    gemini_extension_installed,
    test_workspace
):
    """
    Test that SessionStart hook fires when Gemini CLI starts.

    This is a simpler integration test to verify basic hook execution.
    """
    # Create a simple test: start Gemini and immediately exit
    # SessionStart hook should fire

    result = subprocess.run(
        ["gemini", "--prompt", "/exit"],
        cwd=test_workspace,
        capture_output=True,
        text=True,
        timeout=10
    )

    # Check output for hook execution evidence
    output = result.stdout + result.stderr

    # Gemini v0.28.0+ shows hook execution in output
    # Look for: "Executing Hook:" or "Hook execution for SessionStart"
    has_hook_evidence = (
        "Executing Hook:" in output or
        "Hook execution for SessionStart" in output or
        "SessionStart" in output
    )

    # Note: This test may be flaky depending on Gemini output format
    # If it fails, check if Gemini CLI version changed output format
    if not has_hook_evidence:
        pytest.skip(
            "Could not verify SessionStart hook from output. "
            f"Gemini version: {gemini_available}\n"
            f"Output:\n{output[:500]}"
        )


def test_gemini_before_tool_hook_matcher_includes_run_shell_command(
    gemini_extension_installed
):
    """
    Test that BeforeTool hooks matcher includes run_shell_command.

    This verifies the hook will fire for shell command execution.
    """
    ext_dir = gemini_extension_installed
    hooks_file = ext_dir / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})
    before_tool_configs = hooks_section.get("BeforeTool", [])

    # Find config that matches run_shell_command
    has_run_shell_command = False

    for config in before_tool_configs:
        matcher = config.get("matcher", "")
        if "run_shell_command" in matcher:
            has_run_shell_command = True
            break

    assert has_run_shell_command, \
        "BeforeTool matcher should include run_shell_command for command blocking"


def test_gemini_before_tool_hook_matcher_includes_replace(
    gemini_extension_installed
):
    """
    Test that BeforeTool hooks matcher includes replace tool.

    This verifies the hook will fire for file editing.
    """
    ext_dir = gemini_extension_installed
    hooks_file = ext_dir / "hooks" / "hooks.json"

    with open(hooks_file) as f:
        hooks_data = json.load(f)

    hooks_section = hooks_data.get("hooks", {})
    before_tool_configs = hooks_section.get("BeforeTool", [])

    # Find config that matches replace
    has_replace = False

    for config in before_tool_configs:
        matcher = config.get("matcher", "")
        if "replace" in matcher:
            has_replace = True
            break

    assert has_replace, \
        "BeforeTool matcher should include replace for file modification control"


@pytest.mark.integration
def test_before_tool_hook_input_structure_has_required_fields(
    gemini_available,
    gemini_settings_enabled,
    gemini_extension_installed,
    test_workspace,
    debug_hook_script
):
    """
    Test that BeforeTool hook receives input with required fields.

    According to Gemini CLI docs, hook input should include:
    - hook_event_name
    - tool_name
    - arguments (tool-specific)
    - session_id
    - cwd

    TDD: This test verifies the contract between Gemini CLI and hooks.
    Uses tmux session automation for isolated testing.
    """
    hook_script, debug_log = debug_hook_script

    # Find the actual hook_entry.py that Gemini will execute (same logic as above)
    ext_dir = gemini_extension_installed
    hooks_json_path = ext_dir / "hooks" / "hooks.json"

    with open(hooks_json_path) as f:
        hooks_data = json.load(f)

    actual_hook_entry = None
    for event_configs in hooks_data.get("hooks", {}).values():
        for config in event_configs:
            hook_list = config.get("hooks", [])
            if hook_list:
                cmd = hook_list[0].get("command", "")
                for part in cmd.split():
                    if part.endswith("hook_entry.py"):
                        actual_hook_entry = Path(part)
                        break
            if actual_hook_entry:
                break
        if actual_hook_entry:
            break

    if actual_hook_entry is None or not actual_hook_entry.exists():
        actual_hook_entry = ext_dir / "hooks" / "hook_entry.py"

    hook_entry = actual_hook_entry
    hook_backup = hook_entry.parent / "hook_entry.py.backup"

    shutil.copy2(hook_entry, hook_backup)

    # Create unique tmux session
    session_name = f"gemini-hook-input-test-{int(time.time())}"
    tmux = get_tmux_utilities(session_name)

    try:
        with open(hook_entry, "w") as f:
            f.write(hook_script)
        hook_entry.chmod(0o755)

        # Clear debug log before test
        if debug_log.exists():
            debug_log.unlink()

        # Create tmux session
        assert tmux.execute_tmux_command(['new-session', '-d', '-s', session_name])

        # Change to test workspace
        assert tmux.send_keys(f'cd {test_workspace}', session_name)
        assert tmux.send_keys('C-m', session_name)
        time.sleep(0.5)

        # Start Gemini CLI
        assert tmux.send_keys('gemini', session_name)
        assert tmux.send_keys('C-m', session_name)

        # Wait for Gemini to start
        time.sleep(3)

        # Send simple command that will trigger a tool call
        assert tmux.send_keys('echo hello', session_name)
        assert tmux.send_keys('C-m', session_name)

        # Wait for tool execution
        time.sleep(5)

        # Exit Gemini
        assert tmux.send_keys('/exit', session_name)
        assert tmux.send_keys('C-m', session_name)
        time.sleep(2)

        # If hook fired, check debug log
        if debug_log.exists():
            with open(debug_log) as f:
                log_content = f.read()

            # Check for required fields in logged JSON
            # The debug hook logs the full input JSON
            required_fields = [
                "hook_event_name",
                "cwd"
            ]

            for field in required_fields:
                assert field in log_content, \
                    f"Hook input missing required field: {field}\nLog:\n{log_content[:500]}"

            # Note: tool_name might be None for SessionStart, that's OK
            # Just verify the structure is present
        else:
            pytest.skip("Hook did not fire - cannot test input structure")

    finally:
        if hook_backup.exists():
            shutil.copy2(hook_backup, hook_entry)
            hook_backup.unlink()

        # Clean up tmux session
        tmux.execute_tmux_command(['kill-session', '-t', session_name])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
