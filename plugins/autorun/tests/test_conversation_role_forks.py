"""A forked or side conversation does not inherit the parent's tasks as its own.

A fork is a fact recorded once per session: the first event whose ``source``
is in the harness's ``Platform.fork_session_start_sources`` (Pi and Prime
Agent send ``fork`` on their ``/fork`` reprojection and SessionStart frames,
Claude Code sends it for ``--fork-session``). ``resolve_conversation_role``
answers ``main``, ``subagent`` or ``fork`` from that fact and ``agent_id``;
nothing else reads ``source`` to decide it. Task rows a fork received from
its parent carry ``metadata.inherited_from``: they are listed at Stop as
waiting and never block, until this conversation's own task update claims
one. A harness that sends no fork fact keeps today's behavior (``main``).

Reproduced failure (side_chat_findings.md 3.3): a Pi ``/fork`` reprojected
the parent's in_progress row under the fork's session id and the fork's Stop
blocked on it. Design: stop_gate_design_decisions.md, "forked and side
conversations", items 1 to 5.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from autorun.core import AutorunApp, AutorunDaemon
from autorun.platforms import PLATFORMS, hook_platforms, to_harness_cli_event
from e2e_support import run_isolated_hook
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    HOOK_PLATFORMS,
    STOP_PLATFORMS,
    Session,
    bash_tool,
    blocks,
    gate_env_fixture,
    lookup,
    text,
)

# Harnesses whose task list autorun rebuilds from the conversation's own
# history, the ones that can hand a fork its parent's rows: the Pi-family
# extension (pi_template) declares task_operations_v1 and reprojects on every
# session_start. That is a capability the extension negotiates, not a
# Platform field, so the family is named here.
REPROJECTING = sorted(p.name for p in hook_platforms() if p.memory_template == "pi_template/AGENTS.md")
PARENT_ROW = {"id": "1", "subject": "Parent orchestration row", "status": "in_progress"}
PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "hook_entry.py"


def _session(cli_type: str, label: str) -> Session:
    return Session(f"fork-{label}-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type)


def _reproject(session: Session, source: str, records=(PARENT_ROW,)) -> None:
    """The Pi-family extension's session_start reprojection frame."""
    ctx = session.context("SessionStart", source=source)
    AutorunDaemon(AutorunApp())._run_pi_task_operation(ctx, "task_reproject_v1", {"task_records": [dict(r) for r in records]})


def _start_fork(session: Session) -> dict:
    """What a Pi-family /fork sends: reprojection first, then SessionStart."""
    _reproject(session, "fork")
    return session.dispatch("SessionStart", source="fork")


@pytest.mark.parametrize("cli_type", REPROJECTING)
def test_fork_inherits_no_blocking_tasks(gate_env, cli_type):
    assert "fork" in getattr(PLATFORMS[cli_type], "fork_session_start_sources", ()), (
        f"{cli_type}: Platform.fork_session_start_sources does not declare the /fork reason"
    )
    fork = _session(cli_type, "child")
    _start_fork(fork)

    response = fork.stop("Answered the side question.")

    assert not blocks(response, cli_type), f"{cli_type}: the fork's Stop blocked on the parent's task: {response}"
    shown = text(response)
    assert "#1" in shown and "inherited" in shown.lower(), f"{cli_type}: the inherited task was not listed as waiting: {response}"
    assert fork.status("1") == "in_progress", "an inherited row changed status"
    assert fork.task("1").get("metadata", {}).get("inherited_from"), "the inherited row carries no inherited_from"

    # Control: the same rows reprojected into a conversation that is not a fork block it.
    main = _session(cli_type, "main")
    _reproject(main, "startup")
    main.dispatch("SessionStart", source="startup")
    assert blocks(main.stop("Done."), cli_type), f"{cli_type}: control: a main conversation's own task did not block"


@pytest.mark.parametrize("cli_type", REPROJECTING)
def test_fork_claims_inherited_task_by_updating_it(gate_env, cli_type):
    fork = _session(cli_type, "claim")
    _start_fork(fork)

    fork.tool("TaskUpdate", {"taskId": "1", "status": "in_progress"}, "Updated task #1 status")
    assert "inherited_from" not in fork.task("1").get("metadata", {}), "the fork's own update did not claim #1"
    assert blocks(fork.stop("Working on it."), cli_type), f"{cli_type}: a task the fork took on did not block"

    # A later reprojection of the same history (Pi /tree) keeps the claim.
    _reproject(fork, "startup")
    assert blocks(fork.stop("Still working."), cli_type), f"{cli_type}: a reprojection undid the fork's claim on #1"


@pytest.mark.parametrize("cli_type", REPROJECTING)
def test_fork_does_not_touch_parent_state(gate_env, cli_type):
    parent = _session(cli_type, "parent")
    parent.seed("1", "Parent orchestration row")
    parent.seed("2", "Parent follow-up", status="pending")
    parent.manager.atomic_update_metadata(lambda metadata: metadata.update(stop_block_count=3))
    tasks_before = {task_id: task["status"] for task_id, task in parent.manager.tasks.items()}

    fork = _session(cli_type, "sibling")
    _start_fork(fork)
    fork.stop("Done.")
    fork.tool(bash_tool(cli_type), {"command": "git status"}, "clean")
    fork.prompt("thanks")
    fork.stop("Done.")

    assert {task_id: task["status"] for task_id, task in parent.manager.tasks.items()} == tasks_before
    assert parent.manager.session_metadata.get("stop_block_count") == 3, "a fork reset the parent's loop guard"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
def test_unknown_source_stays_main(gate_env, cli_type):
    """A SessionStart with no fork source leaves the conversation main, so the gate is unchanged."""
    resolve_conversation_role = lookup("resolve_conversation_role")
    session = _session(cli_type, "plain")
    session.dispatch("SessionStart")
    assert resolve_conversation_role(session.context("Stop")) == "main"
    assert resolve_conversation_role(session.context("PostToolUse", agent_id="subagent-7f3e")) == "subagent"
    if cli_type in STOP_PLATFORMS:
        session.seed("1", "Own task")
        assert blocks(session.stop("Done."), cli_type), f"{cli_type}: a main conversation's own task did not block"


def _drive_tools(session: Session, count: int) -> tuple[list, list]:
    tool = bash_tool(session.cli_type)
    pre, post = [], []
    for index in range(count):
        command = {"command": f"ls part{index}"}
        pre.append(session.dispatch("PreToolUse", tool_name=tool, tool_input=command))
        post.append(session.tool(tool, command, "ok"))
    return pre, post


def test_zero_task_nag_offers_in_fork(gate_env):
    """A fork with no tasks of its own is offered a task list, never ordered to make one or denied a tool."""
    fork = _session("claude", "nag")
    fork.dispatch("SessionStart", source="fork")
    pre, post = _drive_tools(fork, 60)

    messages = [text(response) for response in pre + post]
    assert not [m for m in messages if "BLOCKED" in m], "a fork with no tasks was denied a tool"
    reminders = [m for m in messages if "task" in m.lower() and "tool calls" in m.lower()]
    assert reminders, "the fork was not reminded that it tracks no tasks"
    assert not [m for m in reminders if "must" in m.lower()], f"the fork was ordered to create tasks: {reminders[0]}"

    # Control: a main conversation with no tasks is ordered and then denied.
    main = _session("claude", "nagmain")
    main.dispatch("SessionStart", source="startup")
    pre, post = _drive_tools(main, 60)
    assert [r for r in pre if "BLOCKED" in text(r)], "control: the main conversation's zero-task nag never denied"


def test_hook_entry_passes_the_fork_source_through(tmp_path):
    """Through hooks/hook_entry.py, a Claude SessionStart with source fork
    says once that this conversation is a fork; a startup one does not."""

    def session_start(session_id: str, source: str) -> str:
        payload = {
            "hook_event_name": to_harness_cli_event("SessionStart", "claude"),
            "session_id": session_id,
            "cwd": str(tmp_path),
            "source": source,
        }
        done = run_isolated_hook(plugin_root=PLUGIN_ROOT, hook_script=HOOK_SCRIPT, cli="claude", payload=payload, cwd=tmp_path, timeout=60)
        assert done.returncode == 0, done.stderr
        return json.dumps(json.loads(done.stdout) if done.stdout.strip() else {})

    forked = session_start(f"fork-entry-{uuid.uuid4().hex[:8]}", "fork")
    started = session_start(f"main-entry-{uuid.uuid4().hex[:8]}", "startup")

    assert "fork" in forked.lower() and "/goal clear" in forked, f"no fork notice at SessionStart: {forked}"
    assert "/goal clear" not in started, f"a startup SessionStart was told it is a fork: {started}"
