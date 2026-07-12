"""Isolated hook-process and minimal live-model E2Es for Antigravity."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from e2e_support import (
    live_model_env,
    model_override,
    real_money_enabled,
    run_isolated_hook,
)


PLUGIN_ROOT = Path(__file__).parent.parent
DEFAULT_MODEL = "Gemini 3.5 Flash (Low)"


def _find_hook_script() -> Path:
    candidates = (
        PLUGIN_ROOT / "hooks" / "hook_entry.py",
        Path.home() / ".gemini" / "antigravity-cli" / "plugins" / "ar" / "hooks" / "hook_entry.py",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("Antigravity hook_entry.py is not installed or in source")


def _antigravity_model() -> str:
    return model_override("AUTORUN_ANTIGRAVITY_E2E_MODEL", DEFAULT_MODEL)


def _antigravity_print_command(tmp_path: Path, marker: str) -> list[str]:
    """Use the cheapest capable model with bounded, sandboxed print mode."""
    return [
        "agy",
        "--print",
        f"ar:st\nReply with exactly {marker} and no other text.",
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
    command = _antigravity_print_command(tmp_path, "OK")
    assert command[command.index("--model") + 1] == DEFAULT_MODEL
    assert "--sandbox" in command
    assert command[command.index("--log-file") + 1].startswith(str(tmp_path))
    assert command[command.index("--print-timeout") + 1] == "90s"


def test_antigravity_before_tool_denies_dangerous_command_without_daemon(tmp_path):
    """Exercise the installed Antigravity schema through a real hook process."""
    payload = {
        "hook_event_name": "BeforeTool",
        "session_id": f"agy-e2e-{uuid.uuid4().hex}",
        "cwd": str(tmp_path),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "rm -rf ./must-survive"},
    }
    result = run_isolated_hook(
        plugin_root=PLUGIN_ROOT,
        hook_script=_find_hook_script(),
        cli="antigravity",
        payload=payload,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["decision"] == "deny"
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.e2e
@pytest.mark.serial
@pytest.mark.timeout(120)
@pytest.mark.skipif(
    not real_money_enabled(),
    reason="Set AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 for one Antigravity call.",
)
def test_antigravity_status_hook_in_minimal_live_model_session(tmp_path):
    """Prove Antigravity can complete one prompt with autorun hooks loaded."""
    if not shutil.which("agy"):
        pytest.skip("Antigravity CLI not installed")
    marker = "ANTIGRAVITY_AUTORUN_OK"
    result = subprocess.run(
        _antigravity_print_command(tmp_path, marker),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=110,
        env=live_model_env(),
    )
    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode == 0, combined[-4000:]
    assert marker in combined, combined[-4000:]
    assert "hook" not in combined.lower() or "failed" not in combined.lower()
