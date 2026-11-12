"""Waiting on the user is a recorded task status, entered by marker, cleared by the user.

The agent prints ``AUTORUN_TASK_AWAITING_USER(<id>[,<id>]): <decision>`` on
a standalone, unfenced line of its latest turn, for a task that blocks now.
The task becomes ``awaiting-user`` with ``metadata.awaiting_user`` holding the
decision and the status it had; the Stop passes through and the user sees the
decision. The next prompt whose author is the user restores the previous
status, as does the agent's own update of the task. A subagent, a harness
wrapper (``<task-notification>`` and friends) or automation restores nothing.

The stateless marker of commit 675f982c allowed one Stop per line and left no
state; its guard cases (fenced, mid-sentence, unknown id, finished id) are
ported here from 7f614bb1. Design: stop_gate_design.md sections 3a and 3c;
decisions file item 3 (handler order).
"""

from __future__ import annotations

import uuid
import json
from pathlib import Path

import pytest

from autorun import plugins
from autorun.platforms import PLATFORMS
from stop_gate_support import (  # noqa: F401  (gate_env_fixture registers gate_env)
    AWAITING_PREFIX,
    AWAITING_STATUS,
    TASK_TOOL_PLATFORMS,
    Session,
    awaiting,
    blocks,
    gate_env_fixture,
    lookup,
    shows_allowed_stop_message,
    text,
)

DECISION = "approve pushing release-1.0 to origin/main"
TRANSCRIPT_PLATFORMS = [name for name, platform in PLATFORMS.items() if platform.policy_commands_arrive_in_transcript]


@pytest.mark.parametrize("cli_type", TRANSCRIPT_PLATFORMS)
def test_transcript_reply_restores_waiting_task_without_prompt_hook(gate_env, tmp_path, cli_type):
    fixture = Path(__file__).parent / "fixtures/cache_expiry/codex_rollout_no_usable_record.jsonl"
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_bytes(fixture.read_bytes())
    session = _session(cli_type, "transcript-reply")
    session.seed("7", "Push release-1.0")
    assert not blocks(session.stop(awaiting("7", DECISION), transcript_path=str(transcript)), cli_type)
    assert session.status("7") == "awaiting-user"
    assert not blocks(session.stop("Waiting", transcript_path=str(transcript)), cli_type)
    # Replay the fixture's user-message shape as the next typed reply.
    reply = json.loads(transcript.read_text().splitlines()[1])
    reply["timestamp"] = "2026-11-11T10:01:00.000Z"
    reply["payload"]["content"][0]["text"] = "Approved; continue"
    with transcript.open("a") as stream:
        stream.write(json.dumps(reply) + "\n")
    response = session.stop("Work remains.", transcript_path=str(transcript))
    assert blocks(response, cli_type), f"a transcript reply left the task exempt: {response}"
    assert session.status("7") == "in_progress"


@pytest.mark.parametrize("author", ["subagent", "automation", "model", "harness"])
@pytest.mark.parametrize("cli_type", TRANSCRIPT_PLATFORMS)
def test_transcript_fallback_uses_the_prompt_author_guard(gate_env, tmp_path, author, cli_type):
    fixture = Path(__file__).parent / "fixtures/cache_expiry/codex_rollout_no_usable_record.jsonl"
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_bytes(fixture.read_bytes())
    session = _session(cli_type, "transcript-author")
    session.seed("7", "Push release-1.0")
    session.stop(awaiting("7", DECISION), transcript_path=str(transcript))
    reply = json.loads(transcript.read_text().splitlines()[1])
    reply["timestamp"] = "2026-11-11T10:01:00.000Z"
    reply["payload"]["content"][0]["text"] = "A new turn"
    with transcript.open("a") as stream:
        stream.write(json.dumps(reply) + "\n")
    fields = {"prompt_author": author}
    if author == "subagent":
        fields = {"agent_id": "child", "prompt_author": "user"}
    session.stop("Waiting", transcript_path=str(transcript), **fields)
    assert session.status("7") == "awaiting-user"


@pytest.mark.parametrize("cli_type", TRANSCRIPT_PLATFORMS)
def test_transcript_evidence_missing_keeps_tasks_actionable_and_says_why(gate_env, cli_type):
    session = _session(cli_type, "transcript-missing")
    session.seed("7", "Push release-1.0")
    response = session.stop(awaiting("7", DECISION), transcript_path=None)
    assert blocks(response, cli_type)
    assert session.status("7") == "in_progress"
    assert "no user prompt can be identified" in text(response)
    _enter(session)
    response = session.stop("Waiting", transcript_path=None)
    assert blocks(response, cli_type), "losing the transcript silently exempted the task"
    assert "Cannot verify user replies" in text(response)


@pytest.mark.parametrize("cli_type", TRANSCRIPT_PLATFORMS)
def test_legacy_waiting_decision_without_prompt_identity_is_actionable(gate_env, cli_type):
    session = _session(cli_type, "legacy-transcript")
    session.seed("7", "Push release-1.0", status="awaiting-user")
    response = session.stop("Waiting")
    assert blocks(response, cli_type)
    assert "Cannot verify user replies" in text(response)


@pytest.mark.parametrize("cli_type", TRANSCRIPT_PLATFORMS)
def test_transcript_restoration_write_failure_keeps_task_actionable(gate_env, tmp_path, monkeypatch, cli_type):
    from autorun.task_lifecycle import TaskLifecycle

    fixture = Path(__file__).parent / "fixtures/cache_expiry/codex_rollout_no_usable_record.jsonl"
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_bytes(fixture.read_bytes())
    session = _session(cli_type, "transcript-write-failure")
    session.seed("7", "Push release-1.0")
    session.stop(awaiting("7", DECISION), transcript_path=str(transcript))
    reply = json.loads(transcript.read_text().splitlines()[1])
    reply["timestamp"] = "2026-11-11T10:01:00.000Z"
    with transcript.open("a") as stream:
        stream.write(json.dumps(reply) + "\n")
    monkeypatch.setattr(TaskLifecycle, "restore_awaiting_user", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    response = session.stop("Work remains", transcript_path=str(transcript))
    assert blocks(response, cli_type)
    assert "disk full" in text(response)


def test_deferred_upgrade_records_marker_and_restores_on_reply(gate_env, monkeypatch):
    import sqlite3
    from autorun import session_manager as sm
    from test_state_schema_revision import _force_defensive_sqlite, _receipt_backed
    from test_task_status_not_in_schema import _create_pre_3472b3ee_database

    _receipt_backed(gate_env, _create_pre_3472b3ee_database)
    _force_defensive_sqlite(monkeypatch)
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    session = _session("claude", "deferred")
    session.seed("7", "Push release-1.0")
    response = _enter(session)
    assert not blocks(response, "claude")
    assert "could not record" not in text(response), response
    store = sm.get_session_manager().task_repository()._store
    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT status, blocks_stop FROM tasks WHERE session = ? AND task_id = '7'", (session.manager.global_key,)).fetchone() == ("pending", 1)
    session.prompt("Approved; continue")
    assert session.status("7") == "in_progress"
    assert blocks(session.stop("Work remains."), "claude")
    sm._reset_for_testing()


def _session(cli_type: str, label: str) -> Session:
    return Session(f"await-{label}-{cli_type}-{uuid.uuid4().hex[:8]}", cli_type)


def _enter(session: Session, task_id: str = "7"):
    """The agent marks #task_id as waiting on the user; returns the Stop response."""
    response = session.stop(f"The branch is ready.\n\n{awaiting(task_id, DECISION)}\n")
    status = session.status(task_id)
    assert status == AWAITING_STATUS, (
        f"{session.cli_type}: the awaiting-user marker left #{task_id} {status!r}; it must be recorded as "
        f"{AWAITING_STATUS!r} state, not a stateless allow"
    )
    return response


@pytest.mark.parametrize("cli_type", TASK_TOOL_PLATFORMS)
def test_awaiting_user_marker_records_status_decision_and_previous_status(gate_env, cli_type):
    session = _session(cli_type, "record")
    session.seed("7", "Push release-1.0")

    response = _enter(session)

    record = session.task("7").get("metadata", {}).get("awaiting_user")
    assert isinstance(record, dict), f"{cli_type}: metadata.awaiting_user was not stored: {session.task('7')}"
    assert record.get("decision") == DECISION
    assert record.get("previous_status") == "in_progress"
    assert not blocks(response, cli_type), f"{cli_type}: a task waiting on the user still blocked the Stop: {response}"
    shown = text(response)
    if shows_allowed_stop_message(cli_type):
        assert "Waiting on you" in shown and "#7" in shown and DECISION in shown, (
            f"{cli_type}: the user was not shown what #7 waits on: {response}"
        )
    assert "[AWAITING_USER]" in session.audit(), f"{cli_type}: no AWAITING_USER audit line"

    # Recorded, so the next Stop needs no restated marker.
    again = session.stop("Waiting for your answer.")
    assert not blocks(again, cli_type), f"{cli_type}: the recorded state did not outlive the reply that stated it: {again}"
    if shows_allowed_stop_message(cli_type):
        assert DECISION in text(again)


def test_awaiting_user_marker_without_decision_is_rejected_and_named(gate_env):
    session = _session("claude", "bare")
    session.seed("7", "Push release-1.0")

    response = session.stop(awaiting("7", decision=""))

    assert blocks(response, "claude"), f"a bare marker with no decision allowed the Stop: {response}"
    reason = text(response)
    assert AWAITING_PREFIX in reason and "#7" in reason and "decision" in reason.lower(), (
        f"the block does not say that #7's marker needs the decision text: {reason}"
    )
    assert session.status("7") == "in_progress"


@pytest.mark.parametrize(
    "reply",
    [
        pytest.param(f"```\n{awaiting('7')}\n```", id="inside-code-fence"),
        pytest.param(f"I could print {awaiting('7')} here.", id="mid-sentence"),
        pytest.param(awaiting("999"), id="unknown-task"),
        pytest.param(awaiting("8"), id="finished-task"),
    ],
)
def test_awaiting_user_marker_ignores_fenced_quoted_unknown_and_finished_ids(gate_env, reply):
    session = _session("claude", "forged")
    session.seed("7", "Push release-1.0")
    session.seed("8", "Tag release-1.0", status="completed")

    response = session.stop(reply)

    assert blocks(response, "claude"), f"a forged or stale marker released the Stop: {response}"
    assert session.status("7") == "in_progress"
    assert session.status("8") == "completed", "a marker reopened a finished task"
    assert "999" not in session.manager.tasks, "a marker created a task"
    # Control: the genuine marker on #7 does enter the state, so the refusals
    # above are the guard and not a marker that never works.
    _enter(session)


@pytest.mark.parametrize("author", ["user", "subagent", "harness", "automation"])
def test_awaiting_user_cleared_only_by_user_authored_prompt(gate_env, author):
    session = _session("claude", f"author-{author}")
    session.seed("7", "Push release-1.0")
    _enter(session)

    fields: dict = {}
    prompt = "yes, push"
    if author == "subagent":
        fields["agent_id"] = "subagent-7f3e"
    elif author == "harness":
        prefixes = getattr(PLATFORMS["claude"], "harness_authored_prompt_prefixes", None)
        assert prefixes, "Platform.harness_authored_prompt_prefixes is missing or empty for claude"
        prompt = f"{prefixes[0]}<summary>Agent finished</summary></task-notification>"
    elif author == "automation":
        fields["prompt_author"] = "automation"
    ctx = session.context("UserPromptSubmit", prompt=prompt, **fields)
    resolve_prompt_author = lookup("resolve_prompt_author")
    assert resolve_prompt_author(ctx) == author, f"control: the prompt was not attributed to {author}"

    response = plugins.app.dispatch(ctx)

    after = session.stop("Waiting for your answer.")
    if author == "user":
        assert session.status("7") == "in_progress", "the user's reply did not restore #7's previous status"
        assert "#7" in text(response), f"the agent was not told the user replied about #7: {response}"
        assert blocks(after, "claude"), f"enforcement did not return after the user's reply: {after}"
    else:
        assert session.status("7") == AWAITING_STATUS, f"a {author}-authored prompt cleared the user's decision"
        assert not blocks(after, "claude"), f"a {author}-authored prompt restored enforcement: {after}"


def test_awaiting_user_cleared_by_agent_task_update(gate_env):
    session = _session("claude", "update")
    session.seed("7", "Push release-1.0")
    _enter(session)

    session.tool("TaskUpdate", {"taskId": "7", "status": "in_progress"}, "Updated task #7 status")

    assert session.status("7") == "in_progress", "the agent's own TaskUpdate left #7 awaiting the user"
    assert blocks(session.stop("Back on it."), "claude")


def test_awaiting_user_clear_runs_before_value_returning_prompt_handlers(gate_env, monkeypatch):
    """UserPromptSubmit is first-wins (core.AutorunApp._run_chain). The clear
    must be a side effect of the first handler, returning None, so that no
    handler that answers the prompt (the prompt-time cache check, for one)
    can run before it and skip it (decisions file, item 3)."""
    session = _session("claude", "order")
    session.seed("7", "Push release-1.0")
    chain = list(plugins.app.chains["UserPromptSubmit"])
    first = chain[0]

    _enter(session)
    returned = first(session.context("UserPromptSubmit", prompt="yes, push"))
    assert returned is None, f"{first.__name__} answers the prompt ({returned}); the clear must return None"
    assert session.status("7") == "in_progress", (
        f"the first UserPromptSubmit handler, {first.__name__}, does not clear awaiting-user; "
        f"chain: {[handler.__name__ for handler in chain]}"
    )

    # Every handler answering the prompt: only the first one runs, and the
    # clear must already have happened.
    def answering(handler):
        def answers_the_prompt(ctx):
            handler(ctx)
            return ctx.respond("allow", f"answered by {handler.__name__}")

        return answers_the_prompt

    _enter(session)
    monkeypatch.setitem(plugins.app.chains, "UserPromptSubmit", [answering(handler) for handler in chain])
    response = session.prompt("yes, push")

    assert f"answered by {first.__name__}" in text(response), f"control: the chain was not first-wins: {response}"
    assert session.status("7") == "in_progress", "a value-returning first handler pre-empted the awaiting-user clear"


def _seed_two(session: Session) -> None:
    session.seed("7", "Push release-1.0")
    session.seed("8", "Write the release notes", status="pending")


def test_refused_awaiting_user_write_still_blocks_on_actionable_tasks(gate_env, monkeypatch):
    """The store refuses the awaiting-user row (the old status CHECK of a store
    that could not be upgraded). The Stop still blocks on #7 and #8, whose
    state did not change, and says why #7 is not waiting on the user."""
    from autorun import session_manager as sm

    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    session = _session("claude", "refused")
    _seed_two(session)
    real = sm.TaskRepository._write_task

    def old_check(self, conn, session_id, task_id, record):
        if record.get("status") == AWAITING_STATUS:
            raise sm.SessionBackendError(f"Could not store task {task_id!r}: CHECK constraint failed: status IN (...)")
        return real(self, conn, session_id, task_id, record)

    monkeypatch.setattr(sm.TaskRepository, "_write_task", old_check)

    response = session.stop(f"The branch is ready.\n\n{awaiting('7', DECISION)}\n")

    assert blocks(response, "claude"), f"a refused awaiting-user write let the Stop through: {response}"
    shown = text(response)
    assert "#7" in shown and "#8" in shown, f"the block does not name the unchanged actionable tasks: {response}"
    assert "CHECK constraint failed" in shown, f"the refused write was not reported: {response}"
    assert session.status("7") == "in_progress"


def test_stop_blocks_when_every_state_write_fails(gate_env, monkeypatch):
    """Any storage error on the Stop's writes (markers, counter): the decision
    is still made from the task state that was read."""
    from autorun import session_manager as sm

    session = _session("claude", "nowrite")
    _seed_two(session)

    def refuse(self):
        raise sm.SessionPersistenceError(f"Could not save state file {self._state_file}: [Errno 28] No space left on device")

    monkeypatch.setattr(sm._JSONStore, "_save", refuse)

    response = session.stop(f"The branch is ready.\n\n{awaiting('7', DECISION)}\n")

    assert blocks(response, "claude"), f"a failed state write let the Stop through: {response}"
    assert "No space left on device" in text(response), f"the failed write was not reported: {response}"


def test_failed_restore_on_the_users_reply_is_reported(gate_env, monkeypatch):
    from autorun import session_manager as sm

    session = _session("claude", "restorefail")
    session.seed("7", "Push release-1.0")
    _enter(session)

    def refuse(self):
        raise sm.SessionPersistenceError(f"Could not save state file {self._state_file}: [Errno 28] No space left on device")

    monkeypatch.setattr(sm._JSONStore, "_save", refuse)
    response = session.prompt("yes, push")

    assert "could not record your reply" in text(response), f"a failed restore was silent: {response}"


@pytest.mark.parametrize("cli_type", ["pi", "prime"])
def test_reprojection_keeps_awaiting_user_until_a_later_native_update(gate_env, cli_type):
    """Pi and Prime rebuild their task rows from TaskCreate/TaskUpdate receipts
    on resume and /tree. The awaiting-user state autorun recorded after the
    last receipt must survive that; a receipt newer than it supersedes it."""
    import time

    from test_conversation_role_forks import _reproject

    session = _session(cli_type, "reproject")
    receipt = {"id": "7", "subject": "Push release-1.0", "status": "in_progress", "updated_at": time.time() - 60}
    _reproject(session, "startup", records=(receipt,))
    session.dispatch("SessionStart", source="startup")
    _enter(session)

    _reproject(session, "resume", records=(receipt,))

    task = session.task("7")
    assert task["status"] == AWAITING_STATUS, f"{cli_type}: a reprojection of an older receipt made #7 {task['status']!r}"
    assert (task.get("metadata", {}).get("awaiting_user") or {}).get("decision") == DECISION, (
        f"{cli_type}: a reprojection erased the decision #7 waits on: {task.get('metadata')}"
    )
    assert not blocks(session.stop("Waiting for your answer."), cli_type)

    newer = dict(receipt, status="completed", updated_at=time.time() + 1)
    _reproject(session, "resume", records=(newer,))
    assert session.status("7") == "completed", f"{cli_type}: a newer native update did not supersede awaiting-user"
