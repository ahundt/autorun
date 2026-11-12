"""The two measured Stop-gate failures, replayed end to end.

(a) Session f330ed56 took 338 Stop denials (plus 26 bounded yields) on tasks
    that needed the user's approval: the agent had no truthful state for
    "waiting on you", so every Stop blocked and every tool call between Stops
    restarted the loop guard. Replayed through ``hooks/hook_entry.py`` as a
    subprocess (``AUTORUN_USE_DAEMON=0``, private ``AUTORUN_HOME`` and state).
(b) Tasks 478 and 479 of session 6eaa3402 were delegated to async subagents;
    the one-hour TTL reverted them at 20:07 while the subagents returned at
    20:28 and 21:23. Replayed through ``plugins.app.dispatch`` in-process,
    because the clock is controlled through ``time.time``
    (stop_gate_support.ShiftedClock).

Design: stop_gate_design.md sections 1, 3a to 3c and 4.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from autorun.platforms import PLATFORMS, registered_task_tools, to_harness_cli_event
from e2e_support import isolated_hook_env, run_isolated_hook
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    AWAITING_STATUS,
    SPAWNS,
    Session,
    ShiftedClock,
    awaiting,
    bash_tool,
    blocks,
    dead_pid,
    gate_env_fixture,
    text,
)

PLUGIN_ROOT = Path(__file__).parent.parent
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "hook_entry.py"
DECISION = "approve pushing release-1.0 to origin/main"
HOUR = 3600


def _lifecycle(session_id: str, cli: str, cwd: Path, action: str) -> dict:
    """Seed or read task state in a fresh process, as the hooks see it."""
    code = (
        "import json, os, sys; "
        "from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig; "
        "cfg=TaskLifecycleConfig.load(); cfg.enabled=True; cfg.save(); "
        "m=TaskLifecycle(session_id='explicit:' + os.environ['AUTORUN_SESSION_ID'], config=cfg); "
        "(m.create_task('7', {'subject': 'Push release-1.0'}, 'created'), "
        "m.update_task('7', {'status': 'in_progress'}, 'started')) if sys.argv[1] == 'seed' else None; "
        "print(json.dumps(m.tasks))"
    )
    done = subprocess.run(
        ["uv", "run", "--project", str(PLUGIN_ROOT), sys.executable, "-c", code, action],
        capture_output=True,
        text=True,
        timeout=60,
        env=isolated_hook_env(PLUGIN_ROOT, session_id, cli),
        cwd=cwd,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@pytest.mark.parametrize("cli", ["claude", "codex"])
def test_344_denials_scenario(tmp_path, cli):
    """One task needs the user's push approval. The agent enters awaiting-user
    once; ten further Stops with tool work between them pass with the waiting
    line; prompts from a subagent or a harness wrapper restore nothing; the
    user's own prompt restores the task and the next Stop blocks again."""
    session_id = f"e2e-344-{cli}-{uuid.uuid4().hex[:8]}"
    _lifecycle(session_id, cli, tmp_path, "seed")

    def hook(event: str, **fields) -> dict:
        payload = {
            "hook_event_name": to_harness_cli_event(event, cli),
            "session_id": session_id,
            "cwd": str(tmp_path),
            # The session has its task tools (tests/task_tool_evidence.py).
            "active_tools": sorted(registered_task_tools(cli)),
            **fields,
        }
        done = run_isolated_hook(plugin_root=PLUGIN_ROOT, hook_script=HOOK_SCRIPT, cli=cli, payload=payload, cwd=tmp_path, timeout=60)
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout) if done.stdout.strip() else {}

    def stop(reply: str) -> dict:
        return hook("Stop", stop_hook_active=False, session_transcript=[], last_assistant_message=reply)

    def work() -> None:
        hook("PostToolUse", tool_name=bash_tool(cli), tool_input={"command": "git status"}, tool_response="clean")

    def status() -> str:
        return _lifecycle(session_id, cli, tmp_path, "status")["7"]["status"]

    responses = [stop("Release branch is ready; pushing needs your approval.")]
    assert blocks(responses[0], cli), f"control: {cli}'s Stop hook did not enforce #7 at all: {responses[0]}"
    work()
    responses.append(stop(f"Release branch is ready.\n\n{awaiting('7', DECISION)}\n"))
    assert status() == AWAITING_STATUS, f"{cli}: the awaiting-user marker was not recorded (#7 is {status()!r})"
    for _ in range(10):
        work()
        responses.append(stop("Still waiting for your approval to push."))

    denials = [response for response in responses if blocks(response, cli)]
    assert len(denials) <= 1, f"{cli}: {len(denials)} of {len(responses)} Stops were denied while #7 waited on the user"
    for response in responses[1:]:
        assert "Waiting on you" in text(response) and DECISION in text(response), (
            f"{cli}: a passed Stop did not show the waiting line: {response}"
        )

    hook("UserPromptSubmit", prompt="ls the release directory", agent_id="subagent-7f3e")
    prefixes = tuple(getattr(PLATFORMS[cli], "harness_authored_prompt_prefixes", ()) or ())
    if prefixes:
        hook("UserPromptSubmit", prompt=f"{prefixes[0]}<summary>Agent finished</summary></task-notification>")
    assert status() == AWAITING_STATUS, f"{cli}: a prompt the user did not type cleared the awaiting-user state"
    assert not blocks(stop("Still waiting."), cli)

    hook("UserPromptSubmit", prompt="yes, push")
    assert status() == "in_progress", f"{cli}: the user's reply did not restore #7 (it is {status()!r})"
    assert blocks(stop("Pushed? Not yet."), cli), f"{cli}: enforcement did not return after the user's reply"


@pytest.mark.parametrize("cli_type", sorted(SPAWNS))
def test_long_running_subagent_scenario(gate_env, monkeypatch, cli_type):
    """An async subagent runs for three hours and more: no clock reverts its
    task and no Stop blocks on it; its SubagentStop returns the task, which
    then blocks until the parent verifies it."""
    clock = ShiftedClock(monkeypatch)
    session = Session(f"e2e-subagent-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type)
    _tool, _result, agent_id = SPAWNS[cli_type]
    session.seed("478", "Audit the installer")
    session.delegate("478")
    assert session.status("478") == "delegated"

    for minute in range(30):
        clock.advance(6 * 60)
        session.tool(bash_tool(cli_type), {"command": f"rg pattern part{minute}"}, "match", agent_id=agent_id)
        response = session.stop("Waiting for the subagent to finish the audit.")
        assert session.status("478") == "delegated", (
            f"{cli_type}: {clock.offset / HOUR:.1f}h after delegation, with the harness process alive, #478 reverted "
            f"to {session.status('478')!r}"
        )
        assert not blocks(response, cli_type), f"{cli_type}: Stop blocked on a task delegated to a running subagent: {response}"
    clock.advance(3 * 24 * HOUR)
    session.stop("Still waiting.")
    assert session.status("478") == "delegated", f"{cli_type}: advancing the clock three days reverted #478"
    assert "DELEGATION_EXPIRED" not in session.audit()

    child = session.session_id if cli_type == "claude" else f"{session.session_id}-child"
    session.dispatch(
        "SubagentStop",
        session_id=child,
        agent_id=agent_id,
        agent_transcript_path=f"/tmp/example-session/agent-{agent_id}.jsonl",
    )
    assert session.status("478") == "delegation-returned", (
        f"{cli_type}: SubagentStop for {agent_id} did not return #478 (it is {session.status('478')!r})"
    )

    response = session.stop("The subagent is done.")
    assert blocks(response, cli_type) and "#478" in text(response) and "verify" in text(response).lower(), (
        f"{cli_type}: the returned delegation did not block for verification: {response}"
    )


@pytest.mark.parametrize("cli_type", sorted(SPAWNS))
def test_delegation_reverts_when_parent_harness_pid_is_dead(gate_env, cli_type):
    """The harness process that ran the subagent exited before it reported:
    the next Stop (from a resumed harness) sets the task pending and says so."""
    session = Session(f"e2e-deadpid-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type, harness_pid=dead_pid())
    _tool, _result, agent_id = SPAWNS[cli_type]
    session.seed("479", "Fix what the audit finds")
    session.delegate("479")
    assert session.status("479") == "delegated"

    resumed = Session(session.session_id, cli_type, store=session.store, harness_pid=os.getpid())
    response = resumed.stop("Picking up where the last run stopped.")

    assert resumed.status("479") == "pending", (
        f"{cli_type}: the harness process that ran subagent {agent_id} is gone, yet #479 stayed {resumed.status('479')!r}"
    )
    assert "back to pending" in text(response) and "#479" in text(response), f"{cli_type}: no notice: {response}"
    assert blocks(response, cli_type), f"{cli_type}: the reverted task did not block: {response}"
