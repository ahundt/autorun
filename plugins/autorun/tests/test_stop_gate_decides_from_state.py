"""The Stop gate decides from recorded task state, never from a count or a clock.

A task blocks Stop when it is actionable: its status blocks, and it has no
incomplete blocker, or a blocker id is unknown, or it sits in a ``blockedBy``
cycle. A task waiting on a delegated, awaiting-user or itself-waiting task is
not actionable; it is reported as waiting and the gate passes through with
``None`` so stage verification (``autorun_injection``) still runs. The only
number left is the loop guard: consecutive main-agent Stops with no tool call
between them. Design: notes/2026-11-11-usage-and-cruft-audit-support/
stop_gate_design.md sections 3a, 3d, 3e and 4; decisions file items 4 and 5.

Every test drives ``plugins.app.dispatch`` with a hook-shaped context and
asserts what the harness receives and what is stored.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from autorun.config import CONFIG
from autorun.core import EventContext, format_command_for_cli
from autorun.platforms import PLATFORMS, StopPayloadRunningAgents
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    AWAITING_PREFIX,
    DELEGATE,
    HOOK_PLATFORMS,
    STALE_CLEAR,
    STOP_PLATFORMS,
    Session,
    awaiting,
    bash_tool,
    blocks,
    gate_env_fixture,
    text,
)
from test_command_surface_one_name import _retired_mentions


def _session(cli_type: str, label: str) -> Session:
    return Session(f"gate-{label}-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type)


def _blocking_ids(reason: str) -> set[str]:
    """Task ids the block message lists as blocking (``N. #id: subject``)."""
    return set(re.findall(r"\d+\. #([\w.-]+): ", reason))


# --- actionable versus waiting ----------------------------------------------


@pytest.mark.parametrize("cli_type", STOP_PLATFORMS)
def test_blocks_naming_only_actionable_tasks(gate_env, cli_type):
    """#1 is actionable; #2 waits on #3, which the agent delegates in this Stop."""
    session = _session(cli_type, "actionable")
    session.seed("1", "Write the release notes")
    session.seed("3", "Sweep the fixtures")
    session.seed("2", "Publish after the sweep", status="pending", blocked_by=["3"])

    response = session.stop(DELEGATE.format(id="3"))

    assert blocks(response, cli_type), f"{cli_type}: actionable task #1 did not block: {response}"
    reason = text(response)
    listed = _blocking_ids(reason)
    assert "1" in listed, f"{cli_type}: the actionable task #1 is not named as blocking: {reason}"
    assert "2" not in listed, (
        f"{cli_type}: #2 waits on delegated #3 and must not be named as blocking: {sorted(listed)}"
    )
    assert re.search(r"#2 waits on #3\b", reason), f"{cli_type}: no waiting line for #2 on #3: {reason}"
    assert session.status("3") == "delegated"
    assert session.status("2") == "pending", "a waiting task is derived, never stored as a new status"


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
def test_only_waiting_tasks_passes_through_to_autorun_injection(gate_env, cli_type):
    """With nothing actionable, the gate returns None and stage verification answers.

    autorun is active at stage 1 and the agent's reply carries the stage-1
    marker, so ``autorun_injection`` answers with the stage-2 instruction. A
    gate that blocks, or that returns ``allow``, hides that answer.
    """
    session = _session(cli_type, "passthrough")
    session.seed("3", "Sweep the fixtures")
    session.seed("2", "Publish after the sweep", status="pending", blocked_by=["3"])
    reply = f"{DELEGATE.format(id='3')}\n{CONFIG['stage1_message']}"

    response = session.dispatch(
        "Stop",
        session_transcript=[{"role": "assistant", "content": reply}],
        last_assistant_message=reply,
        autorun_stage=EventContext.STAGE_1,
    )

    reason = text(response)
    assert "CANNOT STOP" not in reason, (
        f"{cli_type}: the task gate blocked although #2 only waits on delegated #3: {reason}"
    )
    assert blocks(response, cli_type) and "STAGE 2" in reason, (
        f"{cli_type}: stage verification did not answer the Stop; the task gate must return None, never allow: {response}"
    )
    assert re.search(r"#2 waits on #3\b", reason), f"{cli_type}: the user was not told who waits on whom: {reason}"


def test_dependent_of_delegated_task_does_not_block(gate_env):
    """Dogfood item 2 (session 6eaa3402, tasks 474 to 482): a pending task
    whose only blocker is delegated to a running subagent blocked every Stop."""
    session = _session("claude", "dependency")
    session.seed("474", "Audit the installer")
    session.seed("476", "Fix what the audit finds", status="pending", blocked_by=["474"])

    response = session.delegate("474")

    assert session.status("474") == "delegated"
    assert not blocks(response, "claude"), f"#476 waits on delegated #474 and still blocked the Stop: {response}"
    assert re.search(r"#476 waits on #474\b", text(response)), (
        f"the Stop did not report #476 as waiting on #474: {response}"
    )


@pytest.mark.parametrize("shape", ["dangling", "cycle"])
def test_dangling_and_cyclic_blockers_are_actionable(gate_env, shape):
    """A broken dependency graph is the agent's to fix, so it blocks and says what to fix."""
    session = _session("claude", shape)
    if shape == "dangling":
        session.seed("5", "Ship after the review", status="pending", blocked_by=["999"])
    else:
        session.seed("5", "Ship after the review", status="pending", blocked_by=["6"])
        session.seed("6", "Review before shipping", status="pending", blocked_by=["5"])

    response = session.stop("Waiting on the dependency.")

    assert blocks(response, "claude"), f"{shape} blockedBy graph allowed the Stop: {response}"
    reason = text(response)
    assert "5" in _blocking_ids(reason), f"#5 is not named as blocking: {reason}"
    if shape == "dangling":
        assert "#999" in reason, f"the block does not name the unknown blocker #999 to fix: {reason}"
    else:
        assert "cycle" in reason.lower() and "#6" in reason, (
            f"the block does not name the #5/#6 blockedBy cycle to break: {reason}"
        )


# --- the loop guard ---------------------------------------------------------


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
def test_stop_block_max_count_releases_after_n_toolless_main_agent_stops(gate_env, cli_type):
    """The one count that can pass a Stop is ``stop_block_max_count``.

    After N consecutive main-agent Stops with no main-agent tool call, the next
    Stop passes through with a notice naming the task-status command. A
    main-agent tool call or user-authored prompt restarts the sequence; a
    subagent's tool call does not count as the main agent acting. Other tests in
    this module exercise the user-prompt restart and pass-through notice.
    """
    session = _session(cli_type, "loopguard")
    session.seed("1", "Finish the migration")
    limit = session.manager.config.stop_block_max_count

    for cycle in range(limit * 4):
        response = session.stop("Still working.")
        assert blocks(response, cli_type), (
            f"{cli_type}: Stop {cycle + 1} after main-agent tool work was released by the loop guard: {response}"
        )
        session.tool(bash_tool(cli_type), {"command": f"pytest -k part{cycle}"}, "1 passed")

    session.stop("Still working.")  # opens a fresh sequence: tool activity preceded it
    for _ in range(limit - 1):
        assert blocks(session.stop("Still working."), cli_type)
        session.tool(bash_tool(cli_type), {"command": "ls"}, "ok", agent_id="subagent-7f3e")
    released = session.stop("Still working.")

    assert not blocks(released, cli_type), (
        f"{cli_type}: a subagent's tool calls reset the parent's loop guard; "
        f"{limit + 1} main-agent Stops with no main-agent tool call between them still blocked: {released}"
    )
    assert session.status("1") == "in_progress", "the loop guard changed a task"
    assert "[STOP_LOOP_GUARD]" in session.audit(), "the release was not logged as STOP_LOOP_GUARD"


# The Pi-family bridge (pi_template/extensions/autorun/index.ts): a blocked
# Stop makes agent_settled send the block reason with triggerTurn; that turn's
# before_agent_start has no input event before it, so it sends
# UserPromptSubmit with prompt_author "automation".
PI_FAMILY = sorted(name for name in HOOK_PLATFORMS if PLATFORMS[name].memory_template == "pi_template/AGENTS.md")


@pytest.mark.parametrize("cli_type", PI_FAMILY)
def test_bridge_continuation_prompt_does_not_restart_the_loop_guard(gate_env, cli_type):
    """Replay of the bridge's frames for a model that stops without acting:
    Stop (blocked), the continuation UserPromptSubmit, Stop, ... The
    continuation is automation, not the user, so the guard still releases
    after ``stop_block_max_count`` blocks; a typed prompt does restart it."""
    session = _session(cli_type, "bridgeloop")
    session.seed("1", "Finish the migration")
    limit = session.manager.config.stop_block_max_count

    def settle():
        response = session.stop("I will stop here.")
        if blocks(response, cli_type):
            reason = response.get("reason") or text(response)
            session.prompt(reason, prompt_author="automation")
        return response

    for cycle in range(limit):
        assert blocks(settle(), cli_type), f"{cli_type}: Stop {cycle + 1} was not blocked"
    released = settle()
    assert not blocks(released, cli_type), (
        f"{cli_type}: {limit + 1} Stops with only the bridge's automation-authored continuation "
        f"prompts between them still blocked; the continuation restarted the loop guard: {released}"
    )

    session.prompt("please keep going")  # typed by the user (interactive input)
    assert blocks(session.stop("I will stop here."), cli_type), (
        f"{cli_type}: a user-authored prompt did not restart the loop guard"
    )


@pytest.mark.parametrize("cli_type", HOOK_PLATFORMS)
def test_loop_guard_release_passes_through_to_stage_verification(gate_env, cli_type):
    """The release returns None, not allow, so autorun_injection still answers."""
    session = _session(cli_type, "guardpass")
    session.seed("1", "Finish the migration")
    limit = session.manager.config.stop_block_max_count
    reply = CONFIG["stage1_message"]

    def stop():
        return session.dispatch(
            "Stop",
            session_transcript=[{"role": "assistant", "content": reply}],
            last_assistant_message=reply,
            autorun_stage=EventContext.STAGE_1,
        )

    for _ in range(limit):
        assert "CANNOT STOP" in text(stop())
    released = stop()

    assert "CANNOT STOP" not in text(released), f"{cli_type}: Stop {limit + 1} with no tool call was not released: {released}"
    assert blocks(released, cli_type) and "STAGE 2" in text(released), (
        f"{cli_type}: the loop-guard release returned allow and skipped stage verification: {released}"
    )


# --- staleness is a fact, not a count -----------------------------------------


def test_native_absence_evidence_clears_without_count(gate_env):
    """The stale-clear marker works from the first block; no identical-block count gates it."""
    session = _session("claude", "stale")
    session.seed("9", "Task the harness list no longer has")

    response = session.stop(STALE_CLEAR.format(id="9"))

    assert session.status("9") == "ignored", (
        "the stale-clear marker on the first Stop was ignored: a count of identical blocks still gates it"
    )
    assert not blocks(response, "claude"), f"the cleared task still blocked: {response}"
    assert "[GHOST_CLEAR]" in session.audit()
    metadata = session.manager.session_metadata
    assert "consecutive_identical_stop_block_count" not in metadata, (
        "the identical-block counter is still written"
    )


# --- what the gate prints ---------------------------------------------------


@pytest.mark.parametrize("cli_type", STOP_PLATFORMS)
def test_gate_messages_name_no_retired_command_spelling(gate_env, cli_type):
    """Every gate message (block, waiting lines, rejected marker, loop-guard
    release) is rendered for this harness and names no retired spelling
    (tests/test_command_surface_one_name.py::_retired_mentions)."""
    session = _session(cli_type, "spelling")
    session.seed("1", "Write the release notes")
    session.seed("3", "Sweep the fixtures")
    session.seed("2", "Publish after the sweep", status="pending", blocked_by=["3"])
    session.seed("4", "Push the tag")

    work = (bash_tool(cli_type), {"command": "git status"}, "clean")
    messages = {"block with waiting line": session.stop(DELEGATE.format(id="3"))}
    session.tool(*work)
    messages["rejected bare marker"] = session.stop(awaiting("4", decision=""))
    session.tool(*work)
    session.manager.update_task("1", {"status": "completed"}, "release notes written")
    messages["awaiting-user entry"] = session.stop(awaiting("4"))
    if cli_type == "antigravity":
        messages["awaiting-user entry"] = session.dispatch("BeforeModel")
    session.tool(*work)
    session.seed("5", "Bump the version")
    for _ in range(session.manager.config.stop_block_max_count):
        assert blocks(session.stop("Still working."), cli_type)
    messages["loop-guard release"] = session.stop("Still working.")
    if cli_type == "antigravity":
        messages["loop-guard release"] = session.dispatch("BeforeModel")

    rendered = {name: text(response) for name, response in messages.items()}
    assert "waits on #3" in rendered["block with waiting line"], f"no waiting line: {rendered}"
    assert AWAITING_PREFIX in rendered["rejected bare marker"], f"the bare marker's rejection is not named: {rendered}"
    assert "Waiting on you" in rendered["awaiting-user entry"], f"no awaiting-user line: {rendered}"
    assert format_command_for_cli("/ar:task status", cli_type) in rendered["loop-guard release"], (
        f"the loop-guard release does not point at {format_command_for_cli('/ar:task status', cli_type)}: {rendered}"
    )
    offenders = {name: _retired_mentions(message) for name, message in rendered.items() if _retired_mentions(message)}
    assert not offenders, f"{cli_type}: gate messages name retired command spellings: {offenders}"


# --- the running-agents evidence is declared per harness --------------------


def test_claude_background_arrays_are_not_running_agent_evidence():
    fixture = (
        Path(__file__).parent
        / "fixtures/hook_contracts/claude-2.1.322-background-work.json"
    )
    contract = json.loads(fixture.read_text(encoding="utf-8"))

    assert contract["safe_running_agent_signal"] is None
    assert "stale" in contract["reason"].lower()
    assert PLATFORMS["claude"].stop_payload_lists_running_agents is None


@pytest.mark.parametrize("platform_name", sorted(PLATFORMS))
def test_every_platform_declares_running_agent_reporting(platform_name):
    """``stop_payload_lists_running_agents`` is a recorded fact per harness.

    A signal is allowed only with a pinned native Stop contract. It may name a
    list-valued running-agent field or an inverse idle boolean; absent evidence
    stays None.
    """
    platform = PLATFORMS[platform_name]
    assert hasattr(platform, "stop_payload_lists_running_agents"), (
        "Platform.stop_payload_lists_running_agents is missing"
    )
    value = platform.stop_payload_lists_running_agents
    assert value is None or isinstance(value, StopPayloadRunningAgents), (
        f"{platform_name}: expected a StopPayloadRunningAgents signal or None, got {value!r}"
    )
    if value is not None:
        fixture = Path(__file__).parent / "fixtures" / value.fixture
        assert fixture.is_file(), (
            f"{platform_name} claims native Stop running-work evidence without a captured fixture at {fixture}"
        )
