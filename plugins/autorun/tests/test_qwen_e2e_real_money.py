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
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ENABLE_REAL_MONEY_TESTS = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"
PLUGIN_ROOT = Path(__file__).parent.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent
HOOK_ENTRY = PLUGIN_ROOT / "hooks" / "hook_entry.py"


def _find_qwen_hook_script() -> Path:
    candidates = [
        Path.home() / ".qwen" / "extensions" / "ar" / "hooks" / "hook_entry.py",
        HOOK_ENTRY,
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
    env = os.environ.copy()
    base_url = env.get("Z_AI_BASE_URL", "").strip()
    auth_token = env.get("Z_AI_AUTH_TOKEN", "").strip()
    model = env.get("Z_AI_MODEL", "glm-5.2").strip() or "glm-5.2"

    env["OPENAI_BASE_URL"] = _derive_zai_coding_base_url(base_url)
    env["OPENAI_API_KEY"] = auth_token
    env["OPENAI_MODEL"] = model
    env["QWEN_MODEL"] = model
    env.pop("ANTHROPIC_BASE_URL", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    return env


def _run_qwen_hook(payload: dict, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AUTORUN_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env["AUTORUN_USE_DAEMON"] = "0"
    env["QWEN_SESSION_ID"] = payload.get("session_id", "qwen-e2e-session")
    env["QWEN_PROJECT_DIR"] = str(REPO_ROOT)
    env.pop("GEMINI_SESSION_ID", None)
    env.pop("GEMINI_PROJECT_DIR", None)

    return subprocess.run(
        [
            sys.executable,
            str(_find_qwen_hook_script()),
            "--cli",
            "qwen",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


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


def test_qwen_before_tool_denies_dangerous_shell_command_without_daemon():
    """Qwen hook entry returns permissive deny JSON for blocked shell commands."""
    payload = {
        "hook_event_name": "BeforeTool",
        "session_id": f"qwen-e2e-{uuid.uuid4().hex[:8]}",
        "cwd": str(REPO_ROOT),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "rm -rf /tmp/autorun-qwen-test"},
    }

    result = _run_qwen_hook(payload)

    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["decision"] == "deny"
    assert response["continue"] is True
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = response["hookSpecificOutput"]["permissionDecisionReason"]
    assert "rm" in reason
    assert "trash" in reason


@pytest.mark.e2e
@pytest.mark.timeout(120)
@pytest.mark.skipif(
    not ENABLE_REAL_MONEY_TESTS,
    reason=(
        "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - this test makes "
        "one real Qwen/Z.AI GLM-5.2 API call."
    ),
)
def test_qwen_zai_glm52_basic_response_real_money():
    """Run one minimal Qwen Code call against Z.AI GLM-5.2."""
    if not shutil.which("qwen"):
        pytest.skip("Qwen Code not installed (qwen command not found)")

    required = ("Z_AI_BASE_URL", "Z_AI_AUTH_TOKEN", "Z_AI_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"Missing Z.AI env vars for Qwen live test: {', '.join(missing)}")

    model = os.environ.get("Z_AI_MODEL", "glm-5.2").strip() or "glm-5.2"
    marker = "QWEN_ZAI_GLM52_OK"
    prompt = f"Reply with exactly {marker} and no other text."
    result = subprocess.run(
        [
            "qwen",
            "--bare",
            "--auth-type",
            "openai",
            "--model",
            model,
            "--output-format",
            "json",
            "--max-wall-time",
            "180s",
            "--max-tool-calls",
            "0",
            "--chat-recording",
            "false",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=200,
        env=_qwen_zai_openai_env(),
    )

    combined = result.stdout + "\n" + result.stderr
    if "Insufficient balance or no resource package" in combined:
        pytest.skip("Z.AI returned 429 Insufficient balance or no resource package")
    assert result.returncode == 0, combined[-2000:]
    assert marker in combined, combined[-2000:]
