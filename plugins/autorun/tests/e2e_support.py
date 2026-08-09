"""Shared isolation and capability contracts for harness E2E tests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from autorun.core import format_command_for_cli
from autorun.platforms import PLATFORMS, to_harness_cli_event


REAL_MONEY_ENV = "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY"
RETIRED_GEMINI_BACKEND_ENV = "AUTORUN_ALLOW_RETIRED_GEMINI_CLI_BACKEND_TESTS"
GEMINI_CLI_CONSUMER_BACKEND_CUTOFF = date(2026, 6, 18)
RETIRED_GEMINI_BACKEND_REASON = (
    "Gemini CLI consumer AI backend retired after 2026-06-18; keep hook "
    "capability tests active and run live Google model E2E through Antigravity. "
    f"Set {RETIRED_GEMINI_BACKEND_ENV}=1 only to diagnose the retired backend."
)
TASK_PAUSE_E2E_DURATION = "1m"
TASK_PAUSE_E2E_REASON = "verify live task recovery"
TASK_RECOVERY_MARKER_PATTERN = re.compile(
    r"AUTORUN_TASK_RECOVERY\([A-Za-z0-9_-]+\)"
)
TASK_PAUSE_COMMAND_SENTINEL = "generation-bound recovery marker"
AUTORUN_EXTENSION_LINE = re.compile(
    r"(?im)^\s*(?:[✓✔]\s*)?(?:ar|autorun(?:-workspace)?)\s*(?:\(|$)"
)


@dataclass(frozen=True, slots=True)
class BackendE2EContract:
    """Declare the strongest meaningful E2E boundary for one platform."""

    module: str
    hook_process: bool
    live_model: bool
    isolation: str


@dataclass(frozen=True, slots=True)
class BoundedStopHookResult:
    """Observed responses and task-status snapshots from real hook processes."""

    blocked_responses: tuple[dict, ...]
    yielded_response: dict
    next_turn_response: dict
    status_before: dict
    status_after: dict


BACKEND_E2E_CONTRACTS = {
    "claude": BackendE2EContract("test_claude_e2e_real_money.py", True, True, "temporary cwd and unique session"),
    "gemini": BackendE2EContract("test_gemini_e2e_real_money.py", True, False, "retired model backend; hook process only"),
    "antigravity": BackendE2EContract("test_antigravity_e2e_real_money.py", True, True, "sandboxed print and temporary log"),
    "qwen": BackendE2EContract("test_qwen_e2e_real_money.py", True, True, "bare, no history, zero tools"),
    "codex": BackendE2EContract("test_codex_e2e_real_money.py", True, True, "read-only sandbox and temporary cwd"),
    "forgecode": BackendE2EContract("test_install_steps.py", False, False, "advisory install; no external hook API"),
    "opencode": BackendE2EContract("test_opencode_bridge.py", True, False, "in-process JS plugin carries events to the daemon socket; live deny dogfooded with a local model 2026-08-04"),
}


def real_money_enabled() -> bool:
    """Return whether the caller explicitly opted into paid model calls."""
    return os.environ.get(REAL_MONEY_ENV, "0") == "1"


def retired_gemini_backend_enabled() -> bool:
    """Return whether a legacy Gemini model call is still intentional."""
    return date.today() < GEMINI_CLI_CONSUMER_BACKEND_CUTOFF or os.environ.get(RETIRED_GEMINI_BACKEND_ENV, "0") == "1"


def autorun_extension_listed(output: str) -> bool:
    """Return whether Gemini's extension listing contains the canonical autorun ID.

    Gemini CLI registers the marketplace plugin as ``ar``. Older installs may
    still report ``autorun`` or ``autorun-workspace``; accept those aliases
    without matching unrelated prose that merely mentions "autorun".
    """
    return bool(AUTORUN_EXTENSION_LINE.search(output))


def model_override(env_name: str, default: str) -> str:
    """Resolve a paid-test model with an explicit low-cost default."""
    return os.environ.get(env_name, default).strip() or default


def isolated_hook_env(plugin_root: Path, session_id: str) -> dict[str, str]:
    """Build a direct-hook environment that cannot reach the shared daemon."""
    env = os.environ.copy()
    env.update(
        {
            "AUTORUN_PLUGIN_ROOT": str(plugin_root),
            "AUTORUN_USE_DAEMON": "0",
            "AUTORUN_TEST_MODE": "1",
            "AUTORUN_SESSION_ID": session_id,
        }
    )
    return env


def live_model_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Enable normal daemon IPC while preserving pytest's isolated AUTORUN_HOME."""
    env = dict(base) if base is not None else os.environ.copy()
    env.pop("AUTORUN_USE_DAEMON", None)
    env.pop("AUTORUN_TEST_MODE", None)
    return env


def task_pause_recovery_prompt(cli: str) -> str:
    """Ask one live harness to prove it received the generated recovery token."""
    command = format_command_for_cli("/ar:tasks pause", cli)
    return (
        f"{command} {TASK_PAUSE_E2E_DURATION} {TASK_PAUSE_E2E_REASON}\n"
        "Read the AUTORUN_TASK_RECOVERY token supplied by the autorun hook. "
        "Reply with exactly that complete token on one line and no other text."
    )


def find_task_recovery_marker(output: str) -> str | None:
    """Return the first generation-bound recovery token rendered by a harness."""
    match = TASK_RECOVERY_MARKER_PATTERN.search(output)
    return match.group(0) if match else None


def installed_task_pause_command_is_current(command_file: Path) -> bool:
    """Return whether a harness has the task-pause command generation installed."""
    try:
        return TASK_PAUSE_COMMAND_SENTINEL in command_file.read_text()
    except OSError:
        return False


def run_isolated_hook(
    *,
    plugin_root: Path,
    hook_script: Path,
    cli: str,
    payload: dict,
    event: str | None = None,
    timeout: int = 20,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute one real hook process with unique state and no shared daemon."""
    session_id = str(
        payload.get("session_id")
        or payload.get("conversationId")
        or f"e2e-{cli}-{os.getpid()}"
    )
    wire_payload = dict(payload)
    native_event = event or wire_payload.get("hook_event_name")
    command = [
        "uv",
        "run",
        "--project",
        str(plugin_root),
        sys.executable,
        str(hook_script),
        "--cli",
        cli,
    ]
    if cli == "antigravity" and native_event:
        # Antigravity stdin has no event discriminator; the manifest command
        # owns it. Keep this process test faithful to that production wire.
        wire_payload.pop("hook_event_name", None)
        command.extend(("--event", str(native_event)))
    return subprocess.run(
        command,
        input=json.dumps(wire_payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=isolated_hook_env(plugin_root, session_id),
        cwd=cwd,
    )


def run_bounded_stop_hook_sequence(
    *,
    plugin_root: Path,
    hook_script: Path,
    cli: str,
    session_id: str,
    cwd: Path,
) -> BoundedStopHookResult:
    """Exercise N blocks, N+1 yield, and next-turn reset through hook_entry.py."""
    task_subject = "Retain this task across the bounded Stop yield"
    if cli == "antigravity":
        stop_payload = {
            "conversationId": session_id,
            "workspacePaths": [str(cwd)],
            "transcriptPath": str(cwd / "transcript.jsonl"),
            "executionNum": 1,
            "terminationReason": "model_stop",
            "fullyIdle": True,
        }
        stop_event = "Stop"
    else:
        stop_payload = {
            "hook_event_name": to_harness_cli_event("Stop", cli),
            "session_id": session_id,
            "cwd": str(cwd),
            "_cwd": str(cwd),
            "stop_hook_active": False,
            "session_transcript": [],
        }
        stop_event = None

    def invoke(payload: dict, *, event: str | None = None) -> dict:
        completed = run_isolated_hook(
            plugin_root=plugin_root,
            hook_script=hook_script,
            cli=cli,
            payload=payload,
            event=event,
            cwd=cwd,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr)
        return json.loads(completed.stdout) if completed.stdout.strip() else {}

    def lifecycle_process(action: str) -> dict:
        """Seed/read lifecycle state in the same fresh-process boundary."""
        code = (
            "import json, os, sys; "
            "from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig; "
            "cfg=TaskLifecycleConfig.load(); cfg.enabled=True; cfg.save(); "
            "manager=TaskLifecycle(session_id='explicit:' + os.environ['AUTORUN_SESSION_ID'], config=cfg); "
            "manager.create_task('e2e-retained-task', {'subject': sys.argv[2], 'description': 'E2E task'}, 'created') "
            "if sys.argv[1] == 'seed' else None; "
            "print(json.dumps({'tasks': manager.tasks, 'stop_block_max_count': cfg.stop_block_max_count}))"
        )
        completed = subprocess.run(
            [
                "uv",
                "run",
                "--project",
                str(plugin_root),
                sys.executable,
                "-c",
                code,
                action,
                task_subject,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            env=isolated_hook_env(plugin_root, session_id),
            cwd=cwd,
        )
        if completed.returncode != 0:
            raise AssertionError(
                f"lifecycle subprocess failed in {cwd}:\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return json.loads(completed.stdout)

    seeded = lifecycle_process("seed")
    status_before = seeded["tasks"]
    stop_block_max_count = int(seeded["stop_block_max_count"])
    blocked = tuple(
        invoke(stop_payload, event=stop_event)
        for _ in range(stop_block_max_count)
    )
    yielded = invoke(stop_payload, event=stop_event)
    status_after = lifecycle_process("status")["tasks"]
    if cli == "antigravity":
        invoke(
            {
                "conversationId": session_id,
                "workspacePaths": [str(cwd)],
                "transcriptPath": str(cwd / "transcript.jsonl"),
                "toolCall": {
                    "name": "list_dir",
                    "args": {"DirectoryPath": str(cwd)},
                },
                "stepIdx": 1,
            },
            event="PostToolUse",
        )
    else:
        invoke(
            {
                "hook_event_name": to_harness_cli_event("PostToolUse", cli),
                "session_id": session_id,
                "cwd": str(cwd),
                "_cwd": str(cwd),
                "tool_name": "list_dir",
                "tool_input": {"path": str(cwd)},
                "tool_result": "done",
                "session_transcript": [],
            }
        )
    next_turn = invoke(stop_payload, event=stop_event)
    return BoundedStopHookResult(
        blocked_responses=blocked,
        yielded_response=yielded,
        next_turn_response=next_turn,
        status_before=status_before,
        status_after=status_after,
    )


def assert_bounded_stop_hook_result(cli: str, result: BoundedStopHookResult) -> None:
    """Assert shared lifecycle semantics using one platform's response contract."""
    protocol = PLATFORMS[cli].hook_protocol
    assert all(
        response.get("decision") == protocol.stop_blocking_decision
        for response in result.blocked_responses
    )
    assert result.yielded_response.get("decision") != protocol.stop_blocking_decision
    assert result.next_turn_response.get("decision") == protocol.stop_blocking_decision
    assert "Retain this task across the bounded Stop yield" in json.dumps(
        result.status_before
    )
    assert "Retain this task across the bounded Stop yield" in json.dumps(
        result.status_after
    )
