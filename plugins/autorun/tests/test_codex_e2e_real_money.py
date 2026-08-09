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
from dataclasses import dataclass
from pathlib import Path

import pytest

from autorun.task_lifecycle import TaskLifecycleConfig
from e2e_support import (
    assert_bounded_stop_hook_result,
    find_task_recovery_marker,
    live_model_env,
    run_bounded_stop_hook_sequence,
    task_pause_recovery_prompt,
)


ENABLE_REAL_MONEY_TESTS = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"
_LOG_DIR = Path("/tmp") / "autorun-e2e-test-logs"

paid_codex_e2e = pytest.mark.skipif(
    not ENABLE_REAL_MONEY_TESTS,
    reason=(
        "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set - these tests "
        "spawn real Codex CLI sessions and can cost money."
    ),
)


PLUGIN_ROOT = Path(__file__).parent.parent
REPO_ROOT = PLUGIN_ROOT.parent.parent


@dataclass(frozen=True)
class CodexExecResult:
    model: str
    output_file: Path
    last_message: str
    completed: subprocess.CompletedProcess
    log_path: Path

    @property
    def combined_output(self) -> str:
        return "\n".join(
            part for part in (self.completed.stdout, self.completed.stderr, self.last_message) if part
        )


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


def _is_spark_model(model: str) -> bool:
    return "spark" in model.lower()


def _allow_non_spark_codex_e2e_model() -> bool:
    return os.environ.get("AUTORUN_CODEX_E2E_ALLOW_NON_SPARK_MODEL", "0") == "1"


def _load_codex_model_catalog(args: list[str]) -> dict:
    command = ["codex", "debug", "models", *args]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        pytest.fail(f"Codex model catalog command failed: {error}")
    if result.returncode != 0:
        pytest.fail(
            f"Codex model catalog command exited {result.returncode}: "
            f"{(result.stderr or result.stdout)[-1000:]}"
        )
    try:
        return _json_from_codex_debug_models(result.stdout + "\n" + result.stderr)
    except (json.JSONDecodeError, ValueError) as error:
        pytest.fail(f"Codex model catalog was not valid JSON: {error}")


def _choose_codex_e2e_model() -> str:
    override = os.environ.get("AUTORUN_CODEX_E2E_MODEL", "").strip()
    if override:
        if not _is_spark_model(override) and not _allow_non_spark_codex_e2e_model():
            pytest.skip(
                "AUTORUN_CODEX_E2E_MODEL is not a Spark model. Codex/OpenAI "
                "real-money tests require a Spark model by default; set "
                "AUTORUN_CODEX_E2E_ALLOW_NON_SPARK_MODEL=1 to override intentionally."
            )
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
        "-c",
        'model_reasoning_effort="low"',
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


def test_codex_exec_command_uses_low_supported_reasoning_effort(tmp_path):
    """Trivial paid E2Es must not inherit costly or incompatible effort."""
    command = _codex_exec_command("gpt-5.3-codex-spark", tmp_path, tmp_path / "out", "ok")

    assert ["-c", 'model_reasoning_effort="low"'] == command[2:4]


def _read_output_file(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _run_codex_exec_case(
    label: str,
    prompt: str,
    tmp_path: Path,
    timeout: int = 120,
) -> CodexExecResult:
    model = _choose_codex_e2e_model()
    output_file = tmp_path / f"{label}.last-message.txt"
    completed = subprocess.run(
        _codex_exec_command(model, REPO_ROOT, output_file, prompt),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=live_model_env(),
    )
    last_message = _read_output_file(output_file)
    log_path = _log_run(
        label,
        {"model": model, "prompt": prompt, "last_message": last_message},
        completed.returncode,
        completed.stdout,
        completed.stderr,
    )
    return CodexExecResult(
        model=model,
        output_file=output_file,
        last_message=last_message,
        completed=completed,
        log_path=log_path,
    )


@pytest.fixture(scope="module")
def codex_cli_check():
    if not shutil.which("codex"):
        pytest.skip("Codex CLI not installed")

    result = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        pytest.fail(f"Installed Codex CLI is not runnable: {result.stderr[:500]}")


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
        # Root "reason" must be ABSENT, not populated. Codex's parser
        # (codex-rs/hooks/src/engine/output_parser.rs:144-158, parse_pre_tool_use)
        # sets use_hook_specific_decision=true whenever hookSpecificOutput carries
        # permissionDecision/permissionDecisionReason/updatedInput, and then reads
        # block_reason ONLY from permissionDecisionReason. Root "reason" is read
        # solely in the legacy else-branch, which this response shape never hits.
        # Populating both made Codex's TUI render the same text twice, as separate
        # "warning:" and "feedback:" rows (hook_cell.rs:804-812).
        assert "reason" not in resp or not resp.get("reason")
        assert "continue" not in resp
        assert "stopReason" not in resp
        assert "suppressOutput" not in resp
        hook_output = resp.get("hookSpecificOutput", {})
        assert hook_output.get("hookEventName") == "PreToolUse"
        assert hook_output.get("permissionDecision") == "deny"
        # The block text must survive on the one field Codex actually reads.
        assert hook_output.get("permissionDecisionReason")

    def test_bounded_stop_sequence_retains_tasks_and_resets_on_next_prompt(
        self,
        tmp_path,
    ):
        result = run_bounded_stop_hook_sequence(
            plugin_root=PLUGIN_ROOT,
            hook_script=_find_hook_script(),
            cli="codex",
            session_id=f"e2e-codex-stop-{uuid.uuid4().hex[:8]}",
            cwd=tmp_path,
        )
        assert_bounded_stop_hook_result("codex", result)


@paid_codex_e2e
@pytest.mark.e2e
class TestCodexE2ERealMoney:
    """Real Codex CLI E2E tests using codex exec.

    These tests may spend money and require Codex CLI authentication. They are
    gated by AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1, matching the Claude
    and Gemini real-money tests.
    """

    @pytest.mark.serial
    @pytest.mark.timeout(180)
    def test_codex_userprompt_hook_does_not_fail_in_real_cli(self, codex_cli_check, tmp_path):
        """Run a real Codex prompt through UserPromptSubmit hooks.

        This intentionally uses ar:st because current Codex TUI can consume
        unknown first-line slash commands before prompt hooks see them. The
        hook-safe spelling exercises the same command handler and Codex schema
        path that previously broke with decision="approve".
        """
        prompt = "ar:st\nThen answer exactly: DONE"
        run = _run_codex_exec_case("real-cli-userprompt-ar-st", prompt, tmp_path)
        combined = run.combined_output
        assert "UserPromptSubmit hook (failed)" not in combined
        assert "invalid user prompt submit JSON output" not in combined.lower()
        assert "decision\\\":\\\"approve" not in combined
        assert run.completed.returncode == 0, f"Full output in: {run.log_path}\n{combined[-4000:]}"

    @pytest.mark.serial
    @pytest.mark.timeout(180)
    def test_codex_no_tool_stop_recursion_ends_at_configured_bound(
        self,
        codex_cli_check,
        tmp_path,
    ):
        """A real Codex model can end a no-tool loop without losing its task."""
        prompt = "\n".join(
            [
                "Use update_plan once to create one pending task named "
                "'Retain after this interaction'.",
                "After update_plan succeeds, do not call any more tools.",
                "Reply exactly BOUNDED_STOP_EXIT.",
                "If an autorun Stop hook asks you to continue, still call no "
                "tools and reply exactly BOUNDED_STOP_EXIT again.",
                "Never complete, remove, or ignore the pending task.",
            ]
        )
        run = _run_codex_exec_case("real-cli-bounded-stop", prompt, tmp_path)
        combined = run.combined_output

        assert run.completed.returncode == 0, (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )
        assert run.last_message.strip() == "BOUNDED_STOP_EXIT", (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )
        events = [
            json.loads(line)
            for line in run.completed.stdout.splitlines()
            if line.startswith("{")
        ]
        pending_todos = [
            item
            for event in events
            if (item := event.get("item", {})).get("type") == "todo_list"
            and any(not todo.get("completed") for todo in item.get("items", []))
        ]
        exit_messages = [
            item
            for event in events
            if (item := event.get("item", {})).get("type") == "agent_message"
            and item.get("text") == "BOUNDED_STOP_EXIT"
        ]
        assert pending_todos, f"Full output in: {run.log_path}\n{combined[-4000:]}"
        assert len(exit_messages) == TaskLifecycleConfig.load().stop_block_max_count + 1, (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )
        assert "Stop hook (failed)" not in combined

    @pytest.mark.serial
    @pytest.mark.timeout(180)
    def test_codex_task_pause_recovery_marker_in_real_cli(
        self,
        codex_cli_check,
        tmp_path,
    ):
        """Prove Codex receives the no-slash task pause and recovery token."""
        run = _run_codex_exec_case(
            "real-cli-task-pause-recovery",
            task_pause_recovery_prompt("codex"),
            tmp_path,
        )
        combined = run.combined_output

        assert "UserPromptSubmit hook (failed)" not in combined
        assert find_task_recovery_marker(combined), (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )
        assert run.completed.returncode == 0, (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )

    @pytest.mark.serial
    @pytest.mark.timeout(180)
    def test_codex_ar_ok_allows_git_push_dry_run_in_real_cli(self, codex_cli_check, tmp_path):
        """Prove Codex can consume ar:ok and run the next git push tool call.

        The command uses --dry-run with a nonexistent remote, so success means
        the autorun block cleared and Git reached its own missing-remote error.
        """
        prompt = "\n".join(
            [
                "ar:ok git push",
                "Use the shell tool to run exactly:",
                "git push --dry-run no-such-remote HEAD",
                "After the command returns, answer exactly: COMMAND_RAN",
                "If autorun blocks it, answer exactly: AUTORUN_BLOCKED",
            ]
        )
        run = _run_codex_exec_case("real-cli-ar-ok-git-push-dry-run", prompt, tmp_path)
        combined = run.combined_output

        forbidden_fragments = [
            "Blocked: git push requires explicit user permission",
            "PreToolUse hook (failed)",
            "unsupported permissionDecision:allow",
            "updatedInput without permissionDecision:allow",
        ]
        for fragment in forbidden_fragments:
            assert fragment not in combined, f"Full output in: {run.log_path}\n{combined[-4000:]}"
        assert run.last_message.strip() != "AUTORUN_BLOCKED", (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )
        assert run.last_message.strip() == "COMMAND_RAN", (
            f"Full output in: {run.log_path}\n{combined[-4000:]}"
        )
        assert "COMMAND_RAN" in combined, f"Full output in: {run.log_path}\n{combined[-4000:]}"
        assert "no-such-remote" in combined, f"Full output in: {run.log_path}\n{combined[-4000:]}"
        assert run.completed.returncode == 0, f"Full output in: {run.log_path}\n{combined[-4000:]}"
