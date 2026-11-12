"""Hook-shaped drivers for the Stop gate tests.

The Stop gate decides from recorded task state: a task is actionable, or it
waits on a subagent, on the user, or on another task. The files that test it
(test_stop_gate_decides_from_state.py, test_awaiting_user_state.py,
test_delegation_liveness_evidence.py, test_task_status_not_in_schema.py,
test_stop_gate_e2e.py) drive ``plugins.app.dispatch`` with the
``EventContext`` a hook builds, and read back what the harness would receive
and what was stored. Nothing here calls the gate's helpers directly.

Names the redesign adds (a status, a ``Platform`` field, an ``EventContext``
keyword, ``resolve_prompt_author``) are looked up when a test runs, so a
missing one fails that test with its name instead of failing collection.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from autorun import ipc, plugins
from autorun import session_manager as sm
from autorun import task_lifecycle
from autorun.config import CONFIG
from autorun.core import EventContext, ThreadSafeDB, dispatch_timeout_for_event
from autorun.platforms import PLATFORMS, hook_platforms, registered_task_tools
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig
from task_tool_evidence import task_tool_evidence

DELEGATE = CONFIG["delegate_marker_template"]
# The design's spelling (stop_gate_design.md 3a). Read through .get() so that,
# before the key exists, each test fails on its own assertion.
AWAITING = CONFIG.get("awaiting_user_marker_template", "AUTORUN_TASK_AWAITING_USER({id})")
AWAITING_PREFIX = AWAITING.split("{id}")[0]
STALE_CLEAR = CONFIG["ghost_clear_marker_template"]
AWAITING_STATUS = "awaiting-user"

# Claude's spawn result as a live `claude -p` fan-out delivered it on
# 2026-08-05 (tests/test_delegation_ledger.py SPAWN_RESULT_STRUCTURED).
CLAUDE_SPAWN_ID = "a1b2c3d4e5f6a7b8c"
CLAUDE_SPAWN_RESULT = {
    "isAsync": True,
    "status": "async_launched",
    "agentId": CLAUDE_SPAWN_ID,
    "description": "Delegated sweep",
    "resolvedModel": "example-model",
    "prompt": "Reply with the single word done",
    "outputFile": f"/tmp/example-session/tasks/{CLAUDE_SPAWN_ID}.output",
    "canReadOutputFile": True,
}
# Codex's spawn_agent result (tests/test_delegation_ledger.py CODEX_SPAWN_RESULT,
# from codex-rs core/src/tools/handlers/multi_agents_tests.rs:257).
CODEX_SPAWN_ID = "01997b21-0e6f-7bb2-9d1e-4f0a2c3d5e6f"
CODEX_SPAWN_RESULT = {"agent_id": CODEX_SPAWN_ID, "status": "spawned"}
SPAWNS = {
    "claude": ("Agent", CLAUDE_SPAWN_RESULT, CLAUDE_SPAWN_ID),
    "codex": ("spawn_agent", CODEX_SPAWN_RESULT, CODEX_SPAWN_ID),
}

HOOK_PLATFORMS = sorted(p.name for p in hook_platforms())
STOP_PLATFORMS = sorted(p.name for p in hook_platforms() if "Stop" in p.autorun_to_harness_cli_events)
TASK_TOOL_PLATFORMS = sorted(
    name for name, p in PLATFORMS.items() if p.has_hooks and registered_task_tools(name)
)


def awaiting(task_ids: str, decision: str = "approve pushing release-1.0 to origin/main") -> str:
    marker = AWAITING.format(id=task_ids)
    return f"{marker}: {decision}" if decision else marker


def lookup(name: str):
    """A function the permission workstream adds, found when the test runs."""
    from autorun import core

    for module in (core, plugins):
        if hasattr(module, name):
            return getattr(module, name)
    pytest.fail(f"{name} is not defined in autorun.core or autorun.plugins; the Stop gate's awaiting-user clear depends on it")


def text(response) -> str:
    return json.dumps(response or {}, ensure_ascii=False)


def blocks(response, cli_type: str) -> bool:
    protocol = PLATFORMS[cli_type].hook_protocol
    return isinstance(response, dict) and response.get("decision") == protocol.stop_blocking_decision


def bash_tool(cli_type: str) -> str:
    return PLATFORMS[cli_type].tool_names.get("bash", "Bash")


class ShiftedClock:
    """``time.time`` moved forward by a constant the test controls.

    Every difference between two readings is unchanged, so locks and
    durations behave; only a comparison against a stored timestamp sees time
    pass. That is the comparison a delegation TTL makes, and patching
    ``time.time`` itself reaches it wherever it lives.
    """

    def __init__(self, monkeypatch):
        self._real = time.time
        self.offset = 0.0
        monkeypatch.setattr(time, "time", lambda: self._real() + self.offset)

    def advance(self, seconds: float) -> None:
        self.offset += seconds


@pytest.fixture(name="gate_env")
def gate_env_fixture(tmp_path, monkeypatch):
    """Private state directory and task-lifecycle config with the audit log on."""
    state_dir = tmp_path / "sessions"
    state_dir.mkdir()
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    monkeypatch.setattr(task_lifecycle, "CONFIG_PATH", tmp_path / "task-lifecycle.config.json")
    config = TaskLifecycleConfig(enabled=True, storage_dir=tmp_path / "task-tracking", debug_logging=True)
    config.save()
    sm._reset_for_testing()
    yield state_dir
    sm._reset_for_testing()


@dataclass
class Session:
    """One harness session as its hooks see it."""

    session_id: str
    cli_type: str
    store: ThreadSafeDB = field(default_factory=ThreadSafeDB)
    harness_pid: int = field(default_factory=os.getpid)
    harness_started_at_units: int | None = None

    def __post_init__(self) -> None:
        if self.harness_started_at_units is None and self.harness_pid == os.getpid():
            self.harness_started_at_units = ipc.process_start_units(self.harness_pid) or None

    def context(self, event: str, **fields) -> EventContext:
        if PLATFORMS[self.cli_type].policy_commands_arrive_in_transcript:
            fields.setdefault("transcript_path", str(Path(__file__).parent / "fixtures/cache_expiry/codex_rollout_no_usable_record.jsonl"))
        fields.setdefault("session_transcript", [])
        fields.setdefault("tool_input", {})
        fields.setdefault("active_tools", task_tool_evidence(self.cli_type))
        fields.setdefault("harness_pid", self.harness_pid)
        fields.setdefault("harness_started_at_units", self.harness_started_at_units)
        fields.setdefault("deadline_monotonic", time.monotonic() + dispatch_timeout_for_event(event))
        autorun_stage = fields.pop("autorun_stage", None)
        try:
            ctx = EventContext(
                session_id=fields.pop("session_id", self.session_id),
                event=event,
                store=self.store,
                cli_type=self.cli_type,
                **fields,
            )
        except TypeError as error:
            pytest.fail(f"EventContext does not accept a field this test needs ({error}); the permission workstream adds prompt_author=")
        if autorun_stage is not None:
            ctx.autorun_active = True
            ctx.autorun_stage = autorun_stage
        return ctx

    def dispatch(self, event: str, **fields):
        return plugins.app.dispatch(self.context(event, **fields))

    def stop(self, reply: str = "", **fields):
        return self.dispatch("Stop", last_assistant_message=reply, **fields)

    def tool(self, tool_name: str, tool_input=None, tool_result="", **fields):
        return self.dispatch("PostToolUse", tool_name=tool_name, tool_input=tool_input or {}, tool_result=tool_result, **fields)

    def prompt(self, prompt: str, **fields):
        return self.dispatch("UserPromptSubmit", prompt=prompt, **fields)

    @property
    def manager(self) -> TaskLifecycle:
        return TaskLifecycle(session_id=self.session_id, config=TaskLifecycleConfig.load())

    def seed(self, task_id: str, subject: str, status: str = "in_progress", blocked_by=()) -> None:
        manager = self.manager
        manager.create_task(task_id, {"subject": subject}, "created")
        updates: dict = {"status": status}
        if blocked_by:
            updates["addBlockedBy"] = list(blocked_by)
        manager.update_task(task_id, updates, "seeded")

    def task(self, task_id: str) -> dict:
        return self.manager.tasks[task_id]

    def status(self, task_id: str) -> str:
        return self.task(task_id)["status"]

    def audit(self) -> str:
        path = self.manager.audit_log
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def delegate(self, task_id: str, *, spawn: bool = True, **fields):
        """Delegate one task the way an agent does: spawn tool, then marker."""
        if spawn and self.cli_type in SPAWNS:
            tool_name, result, _agent = SPAWNS[self.cli_type]
            self.tool(tool_name, {"description": "Delegated sweep"}, result, **fields)
        return self.stop(DELEGATE.format(id=task_id), **fields)


def dead_pid() -> int:
    """The pid of a process that has exited and been reaped."""
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def running_agents_fixture(platform_name: str) -> Path:
    """Where a captured Stop payload listing running agents would live."""
    return Path(__file__).parent / "fixtures" / "running_agents" / f"{platform_name}.json"
