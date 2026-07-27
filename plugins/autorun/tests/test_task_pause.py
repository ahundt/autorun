"""User-owned task pauses suspend enforcement without mutating tasks."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import multiprocessing
from threading import Barrier

import pytest

from autorun import plugins
from autorun import session_manager
from autorun.core import EventContext, ThreadSafeDB
from autorun.scoped_allow import ScopeSpec
from autorun.session_manager import SessionPersistenceError
from autorun.platforms import hook_platforms
from autorun.task_pause import (
    activate_task_pause,
    resume_task_pause,
    task_enforcement_is_paused,
    task_pause_allows_stop,
    task_pause_status,
)
from autorun.task_lifecycle import TaskLifecycle


PROCESS_JOIN_TIMEOUT_SECONDS = 30.0
QUEUE_READ_TIMEOUT_SECONDS = 5.0


def _process_task_pause_claim(
    backend: str,
    session_id: str,
    message: str,
    barrier,
    results,
) -> None:
    """Claim one distinct Stop from an isolated spawned interpreter."""
    session_manager._CONFIG["state_backend"] = backend
    session_manager._reset_for_testing()
    try:
        ctx = _context(session_id, message=message)
        barrier.wait()
        results.put(task_pause_allows_stop(ctx, now=101.0))
    finally:
        session_manager._reset_for_testing()


def _context(
    session_id: str,
    event: str = "Stop",
    prompt: str = "",
    *,
    message: str = "assistant-stop-a",
    authority: str = "payload",
    cwd: str | None = None,
) -> EventContext:
    return EventContext(
        session_id=session_id,
        event=event,
        prompt=prompt,
        last_assistant_message=message,
        store=ThreadSafeDB(),
        session_identity_authority=authority,
        cli_type="codex",
        cwd=cwd,
    )


def test_default_task_pause_is_five_minutes_and_preserves_reason(monkeypatch):
    ctx = _context(
        "pause-default",
        event="UserPromptSubmit",
        prompt="ar:task pause Discuss the release boundary",
    )
    ctx.activation_prompt = ctx.prompt
    monkeypatch.setattr("autorun.task_pause.time.time", lambda: 100.0)

    response = plugins.handle_task_command(ctx)

    assert "5m0s" in response
    assert "Discuss the release boundary" in response
    assert task_enforcement_is_paused(ctx, now=399.9)
    assert not task_enforcement_is_paused(ctx, now=400.0)


def test_task_and_tasks_roots_share_status_and_space_subcommands():
    singular = _context("task-singular", event="UserPromptSubmit", prompt="ar:task")
    plural = _context("task-plural", event="UserPromptSubmit", prompt="ar:tasks")
    singular.activation_prompt = singular.prompt
    plural.activation_prompt = plural.prompt

    assert "Task enforcement pause: inactive" in plugins.handle_task_command(singular)
    assert "Task enforcement pause: inactive" in plugins.handle_task_command(plural)


@pytest.mark.parametrize("platform", hook_platforms(), ids=lambda item: item.name)
def test_pause_renders_resume_command_for_every_hook_platform(platform):
    ctx = _context(
        f"pause-render-{platform.name}",
        event="UserPromptSubmit",
        prompt="ar:task pause discuss",
    )
    object.__setattr__(ctx, "_cli_type", platform.name)
    ctx.activation_prompt = ctx.prompt

    response = plugins.handle_task_command(ctx)

    expected = f"{platform.command_display_prefix}task resume"
    assert expected in response
    if platform.command_display_prefix == "ar:":
        assert "/ar:task resume" not in response


@pytest.mark.parametrize("platform", hook_platforms(), ids=lambda item: item.name)
def test_pause_command_dispatch_returns_valid_response_for_every_hook_platform(platform):
    ctx = _context(
        f"pause-schema-{platform.name}",
        event="UserPromptSubmit",
        prompt="/ar:task pause 5m schema check",
    )
    object.__setattr__(ctx, "_cli_type", platform.name)
    ctx.activation_prompt = ctx.prompt

    response = plugins.app.dispatch(ctx)
    rendered = " ".join(
        str(value)
        for value in (
            response.get("systemMessage", ""),
            response.get("reason", ""),
            response.get("hookSpecificOutput", {}).get("additionalContext", ""),
        )
    )

    assert isinstance(response, dict)
    assert "Task enforcement pause: active" in rendered


def test_pause_supports_count_duration_and_multiword_reason():
    ctx = _context(
        "pause-scope",
        event="UserPromptSubmit",
        prompt="ar:tasks pause 3 10m Compare two release strategies",
    )
    ctx.activation_prompt = ctx.prompt

    response = plugins.handle_task_command(ctx)

    assert "3 logical Stops" in response
    assert "10m0s" in response
    assert "Compare two release strategies" in response


def test_pause_without_scope_accepts_freeform_reason():
    ctx = _context(
        "pause-reason",
        event="UserPromptSubmit",
        prompt="ar:task pause why is the lock safe",
    )
    ctx.activation_prompt = ctx.prompt

    assert "why is the lock safe" in plugins.handle_task_command(ctx)


@pytest.mark.parametrize("token", ["0", "-1", "2m3m", "2", "perm"])
def test_pause_reports_actionable_scope_errors(token):
    suffix = " 3" if token in {"2", "perm"} else ""
    ctx = _context(
        f"pause-invalid-{token}",
        event="UserPromptSubmit",
        prompt=f"ar:task pause {token}{suffix}",
    )
    ctx.activation_prompt = ctx.prompt

    response = plugins.handle_task_command(ctx)

    assert response.startswith("❌")
    assert "pass" in response.lower()


def test_process_only_identity_rejects_pause_with_actionable_guidance():
    ctx = _context(
        "pid-birth:11:22",
        event="UserPromptSubmit",
        prompt="ar:task pause discuss",
        authority="process-birth",
    )
    ctx.activation_prompt = ctx.prompt

    response = plugins.handle_task_command(ctx)

    assert response.startswith("❌")
    assert "session" in response.lower()
    assert "AUTORUN_SESSION_ID" in response
    assert not task_enforcement_is_paused(ctx)


def test_explicit_shared_identity_is_visible_in_pause_status():
    ctx = _context(
        "team-release-discussion",
        event="UserPromptSubmit",
        prompt="ar:task pause discuss",
        authority="explicit-shared",
    )
    ctx.activation_prompt = ctx.prompt

    response = plugins.handle_task_command(ctx)

    assert "shared" in response.lower()
    assert "AUTORUN_SESSION_ID" in response


def test_non_user_event_cannot_activate_pause_from_command_text():
    ctx = _context(
        "pause-assistant-command",
        event="PostToolUse",
        prompt="ar:task pause escape unfinished work",
    )
    ctx.activation_prompt = ctx.prompt

    response = plugins.app.dispatch(ctx)

    assert "Task enforcement pause: active" not in str(response)
    assert not task_enforcement_is_paused(ctx)


def test_counted_pause_replays_same_stop_once_then_consumes_distinct_stops():
    ctx = _context("pause-count")
    activate_task_pause(
        ctx,
        scope=ScopeSpec(remaining_uses=2),
        reason="",
        now=100.0,
    )

    assert task_pause_allows_stop(ctx, now=100.0)
    assert task_pause_allows_stop(ctx, now=100.1)
    object.__setattr__(ctx, "_last_assistant_message", "assistant-stop-b")
    assert task_pause_allows_stop(ctx, now=100.2)
    object.__setattr__(ctx, "_last_assistant_message", "assistant-stop-c")
    assert not task_pause_allows_stop(ctx, now=100.3)


def test_parallel_replays_consume_one_of_five_stops():
    ctx = _context("pause-parallel")
    activate_task_pause(
        ctx,
        scope=ScopeSpec(remaining_uses=5),
        reason="",
        now=100.0,
    )
    barrier = Barrier(8)

    def claim():
        barrier.wait()
        return task_pause_allows_stop(ctx, now=100.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda _index: claim(), range(8))) == [True] * 8

    assert "4 logical Stops" in task_pause_status(ctx, now=100.1)


@pytest.mark.parametrize("backend", ["json", "sqlite"])
def test_spawned_processes_claim_counted_pause_atomically(
    backend,
    tmp_path,
    monkeypatch,
):
    previous_backend = session_manager._CONFIG["state_backend"]
    monkeypatch.setenv(
        "AUTORUN_TEST_STATE_DIR",
        str(tmp_path / backend / "state"),
    )
    session_manager._CONFIG["state_backend"] = backend
    session_manager._reset_for_testing()
    process_count = 8
    allowed_count = 5
    session_id = f"pause-process-{backend}"
    try:
        ctx = _context(session_id)
        activate_task_pause(
            ctx,
            scope=ScopeSpec(remaining_uses=allowed_count),
            reason="",
            now=100.0,
        )

        spawn = multiprocessing.get_context("spawn")
        barrier = spawn.Barrier(process_count)
        results = spawn.Queue()
        processes = [
            spawn.Process(
                target=_process_task_pause_claim,
                args=(
                    backend,
                    session_id,
                    f"assistant-stop-{index}",
                    barrier,
                    results,
                ),
            )
            for index in range(process_count)
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=PROCESS_JOIN_TIMEOUT_SECONDS)
            assert process.exitcode == 0

        assert sum(results.get(timeout=QUEUE_READ_TIMEOUT_SECONDS) for _process in processes) == allowed_count
        assert not task_enforcement_is_paused(_context(session_id), now=101.1)
    finally:
        session_manager._CONFIG["state_backend"] = previous_backend
        session_manager._reset_for_testing()


def test_missing_stop_fingerprint_fails_safe():
    ctx = _context("pause-no-fingerprint", message="")
    activate_task_pause(
        ctx,
        scope=ScopeSpec(remaining_uses=1),
        reason="",
        now=100.0,
    )

    assert not task_pause_allows_stop(ctx, now=100.0)


def test_generation_marker_must_be_latest_standalone_non_code_line():
    ctx = _context("pause-marker")
    pause = activate_task_pause(
        ctx,
        scope=ScopeSpec(ttl_seconds=300.0),
        reason="",
        now=100.0,
    )
    marker = f"AUTORUN_TASK_RECOVERY({pause.generation})"

    for message in (
        f"Use `{marker}` when done.",
        f"> {marker}",
        f"prefix {marker}",
        f"```\n{marker}\n```",
    ):
        object.__setattr__(ctx, "_last_assistant_message", message)
        assert task_pause_allows_stop(ctx, now=100.0)

    object.__setattr__(ctx, "_last_assistant_message", marker)
    assert not task_pause_allows_stop(ctx, now=100.0)
    assert not task_enforcement_is_paused(ctx, now=100.0)


def test_old_generation_marker_cannot_clear_new_pause():
    ctx = _context("pause-generation")
    old = activate_task_pause(ctx, scope=ScopeSpec(ttl_seconds=300.0), reason="", now=100.0)
    current = activate_task_pause(ctx, scope=ScopeSpec(ttl_seconds=300.0), reason="", now=101.0)
    object.__setattr__(
        ctx,
        "_last_assistant_message",
        f"AUTORUN_TASK_RECOVERY({old.generation})",
    )

    assert task_pause_allows_stop(ctx, now=101.0)
    assert current.generation in task_pause_status(ctx, now=101.0)


def test_resume_is_idempotent_and_generation_compare_is_atomic():
    ctx = _context("pause-resume")
    pause = activate_task_pause(ctx, scope=ScopeSpec(ttl_seconds=300.0), reason="", now=100.0)

    assert not resume_task_pause(ctx, expected_generation="wrong")
    assert resume_task_pause(ctx, expected_generation=pause.generation)
    assert not resume_task_pause(ctx)


def test_state_failure_enforces_tasks_normally():
    class FailingContext:
        session_identity_authority = "payload"
        last_assistant_message = "assistant-stop"
        transcript = None

        def state_update(self, *_args, **_kwargs):
            raise SessionPersistenceError("database unavailable")

    ctx = FailingContext()
    assert not task_enforcement_is_paused(ctx)
    assert not task_pause_allows_stop(ctx)


def test_ar_go_clears_pause_before_activation():
    ctx = _context(
        "pause-new-run",
        event="UserPromptSubmit",
        prompt="ar:go Finish release checks",
    )
    ctx.activation_prompt = ctx.prompt
    activate_task_pause(ctx, scope=ScopeSpec(), reason="")

    response = plugins.handle_activate(ctx)

    assert response.startswith("✅ Autorun")
    assert not task_enforcement_is_paused(ctx)


@pytest.mark.parametrize("command", ["ar:go Finish release checks", "ar:proc Finish release checks"])
def test_autorun_start_commands_clear_pause(command):
    ctx = _context(
        f"pause-start-{command.split()[0]}",
        event="UserPromptSubmit",
        prompt=command,
    )
    ctx.activation_prompt = ctx.prompt
    activate_task_pause(ctx, scope=ScopeSpec(), reason="")

    plugins.handle_activate(ctx)

    assert not task_enforcement_is_paused(ctx)


def test_pause_state_is_shared_by_session_not_working_directory():
    first = _context("pause-cwd-shared", cwd="/tmp/project-a")
    second = _context("pause-cwd-shared", cwd="/tmp/project-b")
    activate_task_pause(first, scope=ScopeSpec(ttl_seconds=300.0), reason="")

    assert task_enforcement_is_paused(second)


def test_pause_state_does_not_cross_session_ids_in_same_working_directory():
    first = _context("pause-session-a", cwd="/tmp/shared-project")
    second = _context("pause-session-b", cwd="/tmp/shared-project")
    activate_task_pause(first, scope=ScopeSpec(ttl_seconds=300.0), reason="")

    assert not task_enforcement_is_paused(second)


def test_pause_resume_and_expiry_do_not_mutate_tasks(tmp_path):
    ctx = _context("pause-task-integrity")
    manager = TaskLifecycle(
        ctx=ctx,
        config=plugins.task_lifecycle.TaskLifecycleConfig(
            storage_dir=tmp_path / "task-lifecycle",
        ),
    )
    manager.create_task("release", {"subject": "Prepare release"}, "created")
    tasks_before = deepcopy(manager.tasks)

    pause = activate_task_pause(
        ctx,
        scope=ScopeSpec(ttl_seconds=300.0),
        reason="",
        now=100.0,
    )
    assert resume_task_pause(ctx, expected_generation=pause.generation)
    activate_task_pause(
        ctx,
        scope=ScopeSpec(ttl_seconds=1.0),
        reason="",
        now=200.0,
    )
    assert not task_enforcement_is_paused(ctx, now=201.0)

    assert manager.tasks == tasks_before


def test_session_start_injects_current_pause_once_without_arming_enforcement():
    ctx = _context(
        "pause-session-start",
        event="SessionStart",
        message="",
    )
    object.__setattr__(ctx, "_source", "resume")
    activate_task_pause(
        ctx,
        scope=ScopeSpec(ttl_seconds=300.0),
        reason="Discuss release risks",
    )
    manager = TaskLifecycle(ctx=ctx)

    first = manager.handle_session_start(ctx)
    second = manager.handle_session_start(ctx)

    rendered = first.get("systemMessage", "") + first.get("reason", "") + first.get("hookSpecificOutput", {}).get("additionalContext", "")
    assert "Task enforcement pause: active" in rendered
    assert "Discuss release risks" in rendered
    assert second is None
    assert not ctx.task_staleness_enforce_next
