"""Isolated hook-process and minimal live-model E2Es for Antigravity."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from e2e_support import (
    assert_bounded_stop_hook_result,
    find_task_recovery_marker,
    installed_task_pause_command_is_current,
    live_model_env,
    model_override,
    requires_real_money,
    run_bounded_stop_hook_sequence,
    run_isolated_hook,
    task_pause_recovery_prompt,
)


PLUGIN_ROOT = Path(__file__).parent.parent
# `agy models` exposes canonical slugs. Gemini 3.5 Flash-Lite is available via
# the Gemini API but is not in Antigravity's catalog; 3.6 Flash is the current
# low-cost AGY model and avoids relying on a display label the CLI may reject.
DEFAULT_MODEL = "gemini-3.6-flash-low"
ANTIGRAVITY_TASK_COMMAND = (
    Path.home()
    / ".gemini"
    / "config"
    / "plugins"
    / "ar"
    / "commands"
    / "ar"
    / "task.toml"
)
ANTIGRAVITY_HEADLESS_PERMISSION_DENIAL = (
    'a tool required the "command" permission that headless mode cannot prompt for'
)


def _find_hook_script() -> Path:
    candidates = (
        PLUGIN_ROOT / "hooks" / "hook_entry.py",
        Path.home() / ".gemini" / "config" / "plugins" / "ar" / "hooks" / "hook_entry.py",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Antigravity hook_entry.py is not installed or in source")


def _antigravity_model() -> str:
    return model_override("AUTORUN_ANTIGRAVITY_E2E_MODEL", DEFAULT_MODEL)


def _antigravity_print_command(tmp_path: Path, prompt: str) -> list[str]:
    """Use the cheapest capable model with bounded, sandboxed print mode."""
    return [
        "agy",
        "--print",
        prompt,
        "--model",
        _antigravity_model(),
        "--sandbox",
        "--log-file",
        str(tmp_path / "antigravity-e2e.log"),
        "--print-timeout",
        "90s",
    ]


def test_antigravity_print_command_is_bounded_and_isolated(tmp_path, monkeypatch):
    """The paid command must use the low-cost model and isolated resources."""
    monkeypatch.delenv("AUTORUN_ANTIGRAVITY_E2E_MODEL", raising=False)
    command = _antigravity_print_command(tmp_path, "Reply exactly: OK")
    assert command[command.index("--model") + 1] == DEFAULT_MODEL
    assert "--sandbox" in command
    assert command[command.index("--log-file") + 1].startswith(str(tmp_path))
    assert command[command.index("--print-timeout") + 1] == "90s"


def test_antigravity_pre_tool_use_denies_dangerous_command_without_daemon(tmp_path):
    """Exercise the installed Antigravity schema through a real hook process.

    Antigravity's documented stdin omits the event name, so the installed
    command supplies ``--event PreToolUse``. The nested ``toolCall`` and
    PascalCase ``CommandLine`` fields are vendor-shaped; a Claude/Gemini-style
    synthetic payload can pass while the real harness remains fail-open.
    """
    payload = {
        "conversationId": f"agy-e2e-{uuid.uuid4().hex}",
        "workspacePaths": [str(tmp_path)],
        "transcriptPath": str(tmp_path / "transcript.jsonl"),
        "toolCall": {
            "name": "run_command",
            "args": {
                "CommandLine": "rm -rf ./must-survive",
                "Cwd": str(tmp_path),
                "WaitMsBeforeAsync": 5000,
            },
        },
        "stepIdx": 0,
    }
    result = run_isolated_hook(
        plugin_root=PLUGIN_ROOT,
        hook_script=_find_hook_script(),
        cli="antigravity",
        payload=payload,
        event="PreToolUse",
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    # Antigravity's PreToolUse schema puts decision/reason at the JSON root and
    # has no hookSpecificOutput wrapper (pretool_decision_location="root").
    assert response["decision"] == "deny"
    assert "trash" in response["reason"]
    assert "hookSpecificOutput" not in response


def test_antigravity_bounded_stop_retains_tasks_and_resets_on_next_prompt(tmp_path):
    result = run_bounded_stop_hook_sequence(
        plugin_root=PLUGIN_ROOT,
        hook_script=_find_hook_script(),
        cli="antigravity",
        session_id=f"e2e-antigravity-stop-{uuid.uuid4().hex[:8]}",
        cwd=tmp_path,
    )
    assert_bounded_stop_hook_result("antigravity", result)


@pytest.mark.e2e
@pytest.mark.serial
@pytest.mark.timeout(120)
@requires_real_money
def test_antigravity_task_pause_recovery_in_minimal_live_model_session(tmp_path):
    """Prove Antigravity receives the generated task-pause recovery token."""
    if not shutil.which("agy"):
        pytest.skip("Antigravity CLI not installed")
    if not installed_task_pause_command_is_current(ANTIGRAVITY_TASK_COMMAND):
        pytest.fail(
            "Antigravity's installed autorun command assets predate task pause. "
            "After active sessions are safe to interrupt, run "
            "`uv run --project plugins/autorun python -m autorun --install --force` "
            "and rerun this test."
        )
    result = subprocess.run(
        _antigravity_print_command(
            tmp_path,
            task_pause_recovery_prompt("antigravity"),
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=110,
        env=live_model_env(),
    )
    combined = f"{result.stdout}\n{result.stderr}"
    if ANTIGRAVITY_HEADLESS_PERMISSION_DENIAL in combined:
        pytest.fail(
            "Antigravity headless mode cannot prompt for the native task-command "
            "permission. Add the narrow `/ar:tasks` command permission to "
            "~/.gemini/antigravity-cli/settings.json, then rerun this test; do "
            "not use --dangerously-skip-permissions."
        )
    assert result.returncode == 0, combined[-4000:]
    assert find_task_recovery_marker(combined), combined[-4000:]
    assert "hook" not in combined.lower() or "failed" not in combined.lower()
