#!/usr/bin/env python3
"""REAL MONEY TESTS - Codex CLI E2E Integration Tests.

These tests spawn the real `codex exec` CLI and may make paid model calls.
They are skipped unless AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1.
Prefer a Spark model via AUTORUN_CODEX_E2E_MODEL; otherwise skip unless the
local Codex model catalog exposes a slug containing "spark".

The structure intentionally mirrors test_claude_e2e_real_money.py:
hook_entry.py tests and real CLI tests live together, and the whole module is
behind the same opt-in flag so normal test runs cannot mutate daemon/session
state or spend money by accident.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest


ENABLE_REAL_MONEY_TESTS = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"
_LOG_DIR = Path("/tmp") / "autorun-e2e-test-logs"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not ENABLE_REAL_MONEY_TESTS,
        reason=(
            "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - these tests "
            "spawn real Codex CLI sessions and can cost money."
        ),
    ),
]


PLUGIN_ROOT = Path(__file__).parent.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent


def _log_run(label: str, payload_or_prompt, rc: int, stdout: str, stderr: str) -> Path:
    """Write full subprocess I/O to /tmp for failure diagnostics."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe_label = label.replace("/", "_").replace(" ", "_")[:120]
        log_path = _LOG_DIR / f"codex-{safe_label}.json"
        log_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "label": label,
                    "payload_or_prompt": payload_or_prompt,
                    "returncode": rc,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                indent=2,
                default=str,
            )
        )
        return log_path
    except Exception:
        return _LOG_DIR / "codex-log-write-failed.json"


def _find_hook_script() -> Path:
    candidates = [
        PLUGIN_ROOT / "hooks" / "hook_entry.py",
        Path.home() / ".claude" / "autorun" / "plugins" / "autorun" / "hooks" / "hook_entry.py",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "hook_entry.py not found. Searched:\n"
        + "\n".join(f"  - {candidate}" for candidate in candidates)
    )


def _run_hook(payload: dict, timeout: int = 15) -> tuple[int, str, str, dict | None]:
    env = os.environ.copy()
    env["AUTORUN_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env["AUTORUN_USE_DAEMON"] = "0"
    result = subprocess.run(
        [
            "uv",
            "run",
            "--project",
            str(PLUGIN_ROOT),
            sys.executable,
            str(_find_hook_script()),
            "--cli",
            "codex",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    parsed = None
    if result.stdout.strip():
        parsed = json.loads(result.stdout)
    return result.returncode, result.stdout, result.stderr, parsed


def _base_payload(event: str, **extra) -> dict:
    return {
        "hook_event_name": event,
        "session_id": f"e2e-codex-{event.lower()}-{uuid.uuid4().hex[:8]}",
        "cwd": str(REPO_ROOT),
        "_cwd": str(REPO_ROOT),
        "_pid": os.getpid(),
        "permission_mode": "default",
        **extra,
    }


def _json_from_codex_debug_models(output: str) -> dict:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError(f"No JSON object in codex debug models output: {output[:500]}")


def _spark_slugs_from_catalog(catalog: dict) -> list[str]:
    """Return available Spark model slugs from a Codex model catalog."""
    slugs = [
        m.get("slug", "")
        for m in catalog.get("models", [])
        if "spark" in str(m.get("slug", "")).lower()
        or "spark" in str(m.get("display_name", "")).lower()
        or "spark" in str(m.get("description", "")).lower()
    ]
    return sorted(slugs, key=lambda slug: (slug != "gpt-5.3-codex-spark", slug))


def _load_codex_model_catalog(args: list[str]) -> dict | None:
    result = subprocess.run(
        ["codex", "debug", "models", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        return _json_from_codex_debug_models(result.stdout + "\n" + result.stderr)
    except (json.JSONDecodeError, ValueError):
        return None


def _choose_codex_e2e_model() -> str:
    override = os.environ.get("AUTORUN_CODEX_E2E_MODEL", "").strip()
    if override:
        return override

    # Refresh first: account-available models may include Spark entries that
    # are not present in the binary's static bundled catalog.
    for args in ([], ["--bundled"]):
        catalog = _load_codex_model_catalog(args)
        if catalog:
            spark_slugs = _spark_slugs_from_catalog(catalog)
            if spark_slugs:
                return spark_slugs[0]
    pytest.skip(
        "No available Codex model slug contains 'spark'. Set "
        "AUTORUN_CODEX_E2E_MODEL to run this test with an explicit model."
    )


def _codex_exec_command(model: str, cwd: Path, output_file: Path, prompt: str) -> list[str]:
    return [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-hook-trust",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--cd",
        str(cwd),
        "--output-last-message",
        str(output_file),
        prompt,
    ]


@pytest.fixture(scope="module")
def codex_cli_check():
    if not shutil.which("codex"):
        pytest.skip("Codex CLI not installed")

    result = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        pytest.skip(f"Codex CLI is not runnable: {result.stderr[:500]}")


class TestCodexHookEntryPoint:
    """Hook-level Codex tests using hook_entry.py --cli codex.

    These mirror Claude's hook-entrypoint E2E layer but emit Codex-specific
    schema assertions. They do not call the Codex model, but the module still
    uses the same opt-in flag as Claude to avoid daemon/session state mutation
    during regular test runs.
    """

    def test_userpromptsubmit_ar_st_has_no_approve_decision(self):
        payload = _base_payload("UserPromptSubmit", prompt="/ar:st")
        rc, stdout, stderr, resp = _run_hook(payload)
        log_path = _log_run("hook-userprompt-ar-st", payload, rc, stdout, stderr)

        assert rc == 0, f"hook_entry.py failed. Full output in: {log_path}\n{stderr}"
        assert resp is not None, f"Expected JSON stdout. Full output in: {log_path}"
        assert resp.get("decision") != "approve"
        assert "reason" not in resp
        assert resp.get("hookSpecificOutput", {}).get("hookEventName") == "UserPromptSubmit"
        assert "additionalContext" in resp.get("hookSpecificOutput", {})

    def test_pretooluse_rm_block_uses_codex_block_schema(self):
        payload = _base_payload(
            "PreToolUse",
            tool_name="Bash",
            tool_input={"command": "rm /tmp/codex-e2e-test-file"},
        )
        rc, stdout, stderr, resp = _run_hook(payload)
        log_path = _log_run("hook-pretooluse-rm-block", payload, rc, stdout, stderr)

        assert rc == 0, f"Codex blocks through JSON, not exit 2. Full output in: {log_path}"
        assert resp is not None, f"Expected JSON stdout. Full output in: {log_path}"
        assert resp.get("decision") == "block"
        assert resp.get("reason")
        assert "continue" not in resp
        assert "stopReason" not in resp
        assert "suppressOutput" not in resp
        hook_output = resp.get("hookSpecificOutput", {})
        assert hook_output.get("hookEventName") == "PreToolUse"
        assert hook_output.get("permissionDecision") == "deny"


class TestCodexE2ERealMoney:
    """Real Codex CLI E2E tests using codex exec.

    These tests may spend money and require Codex CLI authentication. They are
    gated by AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1, matching the Claude
    and Gemini real-money tests.
    """

    @pytest.mark.serial
    def test_codex_userprompt_hook_does_not_fail_in_real_cli(self, codex_cli_check, tmp_path):
        """Run a real Codex prompt through UserPromptSubmit hooks.

        This intentionally uses /ar:st because it exercises the same command
        path that was broken by decision="approve" in Codex UserPromptSubmit.
        """
        model = _choose_codex_e2e_model()
        output_file = tmp_path / "codex-last-message.txt"
        prompt = "/ar:st\nThen answer exactly: DONE"

        result = subprocess.run(
            _codex_exec_command(model, REPO_ROOT, output_file, prompt),
            capture_output=True,
            text=True,
            timeout=120,
        )

        log_path = _log_run(
            "real-cli-userprompt-ar-st",
            {"model": model, "prompt": prompt},
            result.returncode,
            result.stdout,
            result.stderr,
        )
        combined = result.stdout + "\n" + result.stderr
        assert "UserPromptSubmit hook (failed)" not in combined
        assert "invalid user prompt submit JSON output" not in combined.lower()
        assert "decision\\\":\\\"approve" not in combined
        assert result.returncode == 0, f"Full output in: {log_path}\n{combined[-4000:]}"
