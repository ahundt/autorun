"""A harness restart must not make an existing task set look empty."""

from __future__ import annotations

import uuid

import pytest

from autorun import plugins, session_manager as sm
from autorun.platforms import PLATFORMS
from stop_gate_support import (
    DELEGATE,
    TASK_TOOL_PLATFORMS,
    Session,
    awaiting,
    bash_tool,
    blocks,
    gate_env_fixture,  # noqa: F401  (registers gate_env)
    text,
)


def _create_native_task(session: Session) -> None:
    """Create one open row through this platform's registered task tool."""
    platform = PLATFORMS[session.cli_type]
    if platform.task_management_style == "task_tools":
        tool = sorted(platform.task_create_tools)[0]
        session.tool(
            tool,
            {"subject": "Preserve the release task", "description": "survive restart"},
            "Task #1 created successfully",
        )
    elif platform.task_management_style == "bulk_todos":
        tool = sorted(platform.task_bulk_tools)[0]
        session.tool(
            tool,
            {
                "todos": [
                    {
                        "id": "1",
                        "content": "Preserve the release task",
                        "description": "Preserve the release task",
                        "status": "pending",
                    }
                ]
            },
            "Todos updated",
        )
    elif platform.task_management_style == "plan_checklist":
        tool = sorted(platform.task_plan_tools)[0]
        session.tool(
            tool,
            {"plan": [{"step": "Preserve the release task", "status": "pending"}]},
            "Plan updated",
        )
    else:  # pragma: no cover - the registry parametrization should make this impossible
        pytest.fail(f"{session.cli_type} has unsupported task style {platform.task_management_style!r}")


@pytest.mark.parametrize("cli_type", TASK_TOOL_PLATFORMS)
def test_resume_after_process_restart_keeps_registered_harness_tasks(gate_env, cli_type):
    """Real dispatch reads the durable rows after daemon-local state is rebuilt."""
    session_id = f"resume-{cli_type}-{uuid.uuid4().hex[:8]}"
    before = Session(session_id, cli_type)
    _create_native_task(before)
    assert before.manager.tasks, f"{cli_type}: registered task tool created no autorun row"

    # Model an application/daemon restart: process-local stores and cached state
    # managers are new, while the harness resumes the same session identity.
    sm._reset_for_testing()
    resumed = Session(session_id, cli_type)
    response = resumed.dispatch("SessionStart", source="resume")

    tasks = resumed.manager.tasks
    assert tasks, f"{cli_type}: durable task rows disappeared after restart"
    task_id = next(iter(tasks))
    assert f"#{task_id}" in text(response), f"{cli_type}: resume did not surface the existing task: {response}"

    reminders = []
    tool = bash_tool(cli_type)
    for index in range(80):
        reminders.append(
            resumed.tool(tool, {"command": f"printf resume-{index}"}, "ok")
        )
    assert not [response for response in reminders if "AUTORUN HAS NO" in text(response)], (
        f"{cli_type}: restart made the empty-mirror reminder contradict the stored task set"
    )

    stop = resumed.stop("Done.")
    assert blocks(stop, cli_type), f"{cli_type}: the resumed open task did not reach the Stop gate: {stop}"


@pytest.mark.parametrize(
    "cli_type",
    [name for name in TASK_TOOL_PLATFORMS if not PLATFORMS[name].aggregates_conductor_tasks],
)
def test_zero_row_reminder_does_not_claim_the_harness_has_no_tasks(gate_env, cli_type):
    """Autorun can prove only that its durable mirror contains no active row."""
    resumed = Session(f"resume-empty-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type)
    resumed.dispatch("SessionStart", source="resume")

    reminders = []
    tool = bash_tool(cli_type)
    for index in range(80):
        reminders.append(resumed.tool(tool, {"command": f"printf empty-{index}"}, "ok"))
    rendered = "\n".join(text(response) for response in reminders)

    assert "AUTORUN HAS NO" in rendered and "RECORDED" in rendered, (
        f"{cli_type}: no truthful empty-mirror reminder was emitted"
    )
    assert "NO TASKS EXIST" not in rendered and "NO CHECKLIST EXISTS" not in rendered, (
        f"{cli_type}: autorun claimed knowledge of native harness state it does not receive on resume"
    )


WAITING_CASES = {
    "delegated": ("delegated",),
    "awaiting-user": ("awaiting-user",),
    "paused": ("paused",),
    "mixed": ("delegated", "awaiting-user", "paused"),
}


def _seed_waiting_only(session: Session, statuses: tuple[str, ...]) -> None:
    for index, status in enumerate(statuses, 1):
        task_id = str(index)
        subject = f"Waiting task {index}"
        if status == "delegated":
            session.seed(task_id, subject, status="pending")
            session.stop(DELEGATE.format(id=task_id))
        elif status == "awaiting-user":
            session.seed(task_id, subject, status="pending")
            session.stop(awaiting(task_id, "choose the release target"))
        else:
            session.seed(task_id, subject, status=status)


@pytest.mark.parametrize(
    "cli_type",
    [name for name in TASK_TOOL_PLATFORMS if not PLATFORMS[name].aggregates_conductor_tasks],
)
@pytest.mark.parametrize("case,statuses", WAITING_CASES.items())
def test_waiting_only_tasks_get_update_cadence_without_next_tool_denial(
    gate_env,
    cli_type,
    case,
    statuses,
):
    """A waiting checklist exists even though none of it blocks Stop."""
    session = Session(f"stale-waiting-{cli_type}-{case}-{uuid.uuid4().hex[:8]}", cli_type)
    _seed_waiting_only(session, statuses)

    tool = bash_tool(cli_type)
    crossing = session.context(
        "PostToolUse",
        tool_name=tool,
        tool_input={"command": "printf threshold"},
        tool_result="ok",
    )
    crossing.task_staleness_initial_threshold = 1
    crossing.task_staleness_reminder_count = 1
    reminder = plugins.app.dispatch(crossing)
    following = session.dispatch(
        "PreToolUse",
        tool_name=tool,
        tool_input={"command": "printf continue"},
    )
    rendered = text(reminder)
    permission = (following or {}).get("hookSpecificOutput", {}).get("permissionDecision")

    expected_states = {
        "delegated": "Delegated",
        "awaiting-user": "Waiting on you",
        "paused": "Paused",
    }
    assert "AUTORUN HAS NO ACTIVE TASKS RECORDED" not in rendered and permission != "deny", (
        f"{cli_type}/{case}: waiting work was treated as absent; reminder={rendered}; "
        f"following PreToolUse={text(following)}"
    )
    assert "TASK UPDATE REMINDER" in rendered, f"{cli_type}/{case}: no ordinary cadence reminder: {rendered}"
    for status in statuses:
        assert expected_states[status] in rendered, (
            f"{cli_type}/{case}: reminder did not name {status} state: {rendered}"
        )
