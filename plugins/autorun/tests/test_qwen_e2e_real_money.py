#!/usr/bin/env python3
"""Qwen Code + Z.AI GLM-5.2 E2E hardening tests.

The direct hook tests are no-cost and validate the Qwen-specific hook schema.
The live model test is skipped unless AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
and the caller already has Z_AI_BASE_URL, Z_AI_AUTH_TOKEN, and Z_AI_MODEL set.

Z.AI documents GLM-5.2 through OpenAI-compatible /api/paas/v4 endpoints, with
a dedicated /api/coding/paas/v4 endpoint for coding-plan tools. Qwen Code 0.18.5
supports that route through --auth-type openai and OPENAI_* environment variables.
The local Claude aliases expose Z_AI_AUTH_TOKEN, so these tests deliberately map
Z_AI_AUTH_TOKEN to OPENAI_API_KEY for Qwen.
"""
from __future__ import annotations

import json
import os
import re
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
    requires_real_money,
    run_bounded_stop_hook_sequence,
    run_isolated_hook,
    task_pause_recovery_prompt,
)


PLUGIN_ROOT = Path(__file__).parent.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"
QWEN_TASK_COMMAND = (
    Path.home() / ".qwen" / "extensions" / "ar" / "commands" / "ar" / "task.toml"
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_TRAILING_STYLE_RE = re.compile(r"(?:\[[0-9;]*m\]?)+$")


def _clean_zai_model(value: str | None) -> str:
    """Normalize a shell-provided Z.AI model id without exposing secrets."""
    model = (value or "glm-5.2").strip() or "glm-5.2"
    model = _ANSI_ESCAPE_RE.sub("", model).strip()
    # Some shell prompt/theme integrations can leave a literal "[1m" suffix
    # when exporting copied text. Z.AI treats that as part of the model id.
    model = _TRAILING_STYLE_RE.sub("", model).strip()
    return model or "glm-5.2"


def _find_qwen_hook_script() -> Path:
    candidates = [
        HOOK_ENTRY,
        Path.home() / ".qwen" / "extensions" / "ar" / "hooks" / "hook_entry.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "Qwen hook_entry.py not found. Searched:\n"
        + "\n".join(f"  - {candidate}" for candidate in candidates)
    )


def _derive_zai_coding_base_url(base_url: str) -> str:
    """Return Z.AI's OpenAI-compatible coding-plan endpoint."""
    if os.environ.get("AUTORUN_QWEN_ZAI_BASE_URL"):
        return os.environ["AUTORUN_QWEN_ZAI_BASE_URL"].strip()
    normalized = base_url.rstrip("/")
    if normalized.endswith("/api/anthropic"):
        return normalized[: -len("/api/anthropic")] + "/api/coding/paas/v4"
    if normalized.endswith("/api/paas/v4"):
        return normalized[: -len("/api/paas/v4")] + "/api/coding/paas/v4"
    if normalized.endswith("/api/coding/paas/v4"):
        return normalized
    return "https://api.z.ai/api/coding/paas/v4"


def _qwen_zai_openai_env() -> dict[str, str]:
    """Build Qwen's OpenAI-compatible env from Z.AI shell variables."""
    env = live_model_env()
    base_url = env.get("Z_AI_BASE_URL", "").strip()
    auth_token = env.get("Z_AI_AUTH_TOKEN", "").strip()
    model = _clean_zai_model(env.get("Z_AI_MODEL"))

    env["OPENAI_BASE_URL"] = _derive_zai_coding_base_url(base_url)
    env["OPENAI_API_KEY"] = auth_token
    env["OPENAI_MODEL"] = model
    env["QWEN_MODEL"] = model
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def _run_qwen_hook(payload: dict, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    return run_isolated_hook(
        plugin_root=PLUGIN_ROOT,
        hook_script=_find_qwen_hook_script(),
        cli="qwen",
        payload=payload,
        timeout=timeout,
    )


def _qwen_live_command(model: str, prompt: str) -> list[str]:
    """Build one sandboxed Qwen prompt with the current CLI surface."""
    return [
        "qwen",
        "--auth-type",
        "openai",
        "--model",
        model,
        "--output-format",
        "json",
        "--sandbox",
        "--chat-recording",
        "false",
        "--max-session-turns",
        "1",
        "--max-wall-time",
        "180s",
        "--max-tool-calls",
        "0",
        prompt,
    ]


def test_qwen_live_command_has_strict_resource_bounds():
    """One paid smoke must use noninteractive JSON and sandboxed execution."""
    command = _qwen_live_command("glm-5.2", "OK")
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--chat-recording") + 1] == "false"
    assert command[command.index("--max-session-turns") + 1] == "1"
    assert command[command.index("--max-tool-calls") + 1] == "0"
    assert command[-1] == "OK"
    assert "--sandbox" in command
    assert "--bare" not in command
    assert "--safe-mode" not in command


def test_qwen_zai_env_maps_token_to_openai_api_key(monkeypatch):
    """Qwen's Z.AI route uses OpenAI-compatible auth for GLM-5.2."""
    monkeypatch.setenv("Z_AI_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("Z_AI_AUTH_TOKEN", "placeholder-token-for-test")
    monkeypatch.setenv("Z_AI_MODEL", "glm-5.2")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "placeholder-claude-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-anthropic-token")

    env = _qwen_zai_openai_env()

    assert env["OPENAI_BASE_URL"] == "https://api.z.ai/api/coding/paas/v4"
    assert env["OPENAI_API_KEY"] == "placeholder-token-for-test"
    assert env["OPENAI_MODEL"] == "glm-5.2"
    assert env["QWEN_MODEL"] == "glm-5.2"
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "ANTHROPIC_API_KEY" not in env


def test_qwen_zai_env_strips_style_suffix_from_model(monkeypatch):
    """Do not forward copied ANSI/style suffixes as part of Z.AI model ids."""
    monkeypatch.setenv("Z_AI_BASE_URL", "https://api.z.ai/api/anthropic")
    monkeypatch.setenv("Z_AI_AUTH_TOKEN", "placeholder-token-for-test")
    monkeypatch.setenv("Z_AI_MODEL", "glm-5.2[1m]")

    env = _qwen_zai_openai_env()

    assert env["OPENAI_MODEL"] == "glm-5.2"
    assert env["QWEN_MODEL"] == "glm-5.2"
    assert _clean_zai_model("glm-5.2[1m") == "glm-5.2"


def test_qwen_pre_tool_use_denies_dangerous_shell_command_without_daemon():
    """Qwen hook entry returns deny JSON for blocked shell commands.

    Event name is "PreToolUse", not Gemini's "BeforeTool". Although Qwen Code
    forks Gemini CLI's hook types, it kept Claude-style event names:
    packages/core/src/hooks/types.ts defines HookEventName.PreToolUse =
    'PreToolUse' and contains no "BeforeTool" member at all. Sending
    "BeforeTool" left the event unmapped, so the guard never ran and the hook
    returned {"continue": true} — the test asserted no safety property.
    """
    payload = {
        "hook_event_name": "PreToolUse",
        "session_id": f"qwen-e2e-{uuid.uuid4().hex[:8]}",
        "cwd": str(REPO_ROOT),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "rm -rf /tmp/autorun-qwen-test"},
    }

    result = _run_qwen_hook(payload)

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    # Qwen carries the decision inside hookSpecificOutput
    # (pretool_decision_location="hook_specific_output"), with no root decision.
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "rm" in reason
    assert "trash" in reason


def test_qwen_bounded_stop_retains_tasks_and_resets_on_next_prompt(tmp_path):
    result = run_bounded_stop_hook_sequence(
        plugin_root=PLUGIN_ROOT,
        hook_script=_find_qwen_hook_script(),
        cli="qwen",
        session_id=f"e2e-qwen-stop-{uuid.uuid4().hex[:8]}",
        cwd=tmp_path,
    )
    assert_bounded_stop_hook_result("qwen", result)


@pytest.mark.e2e
@pytest.mark.timeout(120)
@requires_real_money
def test_qwen_zai_glm52_task_pause_recovery_real_money(tmp_path):
    """Prove Qwen returns the generation-bound task-pause recovery token."""
    if not shutil.which("qwen"):
        pytest.skip("Qwen Code not installed (qwen command not found)")
    if not installed_task_pause_command_is_current(QWEN_TASK_COMMAND):
        pytest.fail(
            "Qwen's installed autorun command assets predate task pause. "
            "After active sessions are safe to interrupt, run "
            "`uv run --project plugins/autorun python -m autorun --install --force` "
            "and rerun this test."
        )

    required = ("Z_AI_BASE_URL", "Z_AI_AUTH_TOKEN", "Z_AI_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"Missing Z.AI env vars for Qwen live test: {', '.join(missing)}")

    model = _clean_zai_model(os.environ.get("Z_AI_MODEL"))
    result = subprocess.run(
        _qwen_live_command(model, task_pause_recovery_prompt("qwen")),
        capture_output=True,
        text=True,
        timeout=200,
        env=_qwen_zai_openai_env(),
        cwd=tmp_path,
    )

    combined = result.stdout + "\n" + result.stderr
    if "Insufficient balance or no resource package" in combined:
        pytest.fail(
            "Qwen launched but Z.AI rejected the enabled live test with 429: "
            "Insufficient balance or no resource package.\n"
            + combined[-2000:]
        )
    assert result.returncode == 0, combined[-2000:]
    assert find_task_recovery_marker(combined), combined[-2000:]
