"""Plan export regressions found in the 1.0.0 review, each through its real entry point.

- Two sessions in one project: ExitPlanMode must back up the calling session's
  plan, not whichever plan the other session happened to record first.
- Option 1 in one session (SessionStart source=clear) must not export the
  other session's plan, whether it is still being written or awaiting its own
  dialog.
- An absolute or ~ export directory is honored and reported as a success.
- `/ar:pe rejected off` keeps every plan that was not accepted out of notes/,
  while an Option 1 acceptance still reaches notes/.
- `/ar:pe` status shows the settings exports use, legacy keys included.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autorun import plan_export as pe
from autorun import session_manager as sm
from autorun.core import EventContext, ThreadSafeDB


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Fresh state store, default config, and a home directory inside tmp_path."""
    state = tmp_path / "sessions"
    state.mkdir()
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(pe, "CONFIG_PATH", tmp_path / "ar-home" / "plan-export.config.json")
    monkeypatch.setattr(pe, "LEGACY_CONFIG_PATH", tmp_path / "legacy" / "plan-export.config.json")
    sm._reset_for_testing()
    yield
    sm._reset_for_testing()


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def plans_dir(tmp_path):
    # is_plan_file() requires "/.claude/plans/" in the path.
    directory = tmp_path / "home" / ".claude" / "plans"
    directory.mkdir(parents=True)
    return directory


def _event(session_id, project, event, tool_name=None, file_path=None, **kwargs):
    tool_input = {"cwd": str(project)}
    if file_path is not None:
        tool_input["file_path"] = str(file_path)
    kwargs.setdefault("harness_pid", 101)
    return EventContext(
        session_id=session_id,
        event=event,
        tool_name=tool_name,
        tool_input=tool_input,
        store=ThreadSafeDB(),
        **kwargs,
    )


def _plan(plans_dir, name, body):
    path = plans_dir / f"{name}.md"
    path.write_text(f"# {name}\n\n{body}\n", encoding="utf-8")
    return path


def _bodies(directory: Path) -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in directory.glob("*.md"))


class TestExitPlanModeUsesTheCallingSessionsPlan:
    def test_option1_accept_in_second_session_exports_its_own_plan(
        self, project, plans_dir
    ):
        alpha = _plan(plans_dir, "alpha", "written by session one")
        beta = _plan(plans_dir, "beta", "written by session two")
        pe.track_and_export_plans_early(
            _event("S1", project, "PreToolUse", "Write", alpha)
        )
        pe.track_and_export_plans_early(
            _event("S2", project, "PreToolUse", "Write", beta)
        )
        # PreToolUse carries no tool_result, so the plan comes from active_plans.
        pe.track_and_export_plans_early(
            _event("S2", project, "PreToolUse", "ExitPlanMode", permission_mode="plan")
        )

        plans = pe.PlanExport(_event("S2", project, "PreToolUse")).active_plans
        assert plans[str(beta)].get("exit_attempted") is True, plans
        assert not plans[str(alpha)].get("exit_attempted"), (
            "S2's ExitPlanMode marked S1's plan as presented for acceptance"
        )

        # The user accepts with Option 1: fresh context, SessionStart source=clear.
        pe.recover_unexported_plans(
            _event("S3", project, "SessionStart", source="clear")
        )
        accepted = _bodies(project / "notes")
        assert "written by session two" in accepted
        assert "written by session one" not in accepted

    def test_newest_plan_of_the_calling_session_wins(self, project, plans_dir):
        older = _plan(plans_dir, "older", "first draft")
        newer = _plan(plans_dir, "newer", "second draft")
        for path in (older, newer):
            pe.track_and_export_plans_early(
                _event("S2", project, "PreToolUse", "Write", path)
            )

        exporter = pe.PlanExport(_event("S2", project, "PreToolUse", "ExitPlanMode"))
        assert exporter.get_current_plan() == newer

    def test_no_plan_from_another_session(self, project, plans_dir):
        alpha = _plan(plans_dir, "alpha", "written by session one")
        pe.track_and_export_plans_early(
            _event("S1", project, "PreToolUse", "Write", alpha)
        )
        exporter = pe.PlanExport(_event("S2", project, "PreToolUse", "ExitPlanMode"))
        assert exporter.get_current_plan() is None


class TestClearRecoversOnlyThePredecessorsPlans:
    """SessionStart(source=clear) must leave plans of other live sessions alone."""

    def _write(self, session_id, project, plan, pid=101):
        pe.track_and_export_plans_early(
            _event(session_id, project, "PreToolUse", "Write", plan, harness_pid=pid)
        )

    def _exit(self, session_id, project, pid=101):
        pe.track_and_export_plans_early(
            _event(session_id, project, "PreToolUse", "ExitPlanMode", permission_mode="plan", harness_pid=pid)
        )

    def _pending(self, project):
        return pe.PlanExport(_event("probe", project, "PreToolUse")).active_plans

    def test_clear_without_identity_retains_even_a_single_dialog(self, project, plans_dir):
        alpha = _plan(plans_dir, "alpha", "unaccepted dialog")
        self._write("S1", project, alpha)
        self._exit("S1", project)
        response = pe.recover_unexported_plans(
            _event("S2", project, "SessionStart", source="clear", harness_pid=None)
        )
        assert response is None
        assert not _bodies(project / "notes")
        assert str(alpha) in self._pending(project)

    def test_clear_does_not_match_a_reused_pid(self, project, plans_dir):
        alpha = _plan(plans_dir, "alpha", "previous process dialog")
        for tool, path in [("Write", alpha), ("ExitPlanMode", None)]:
            pe.track_and_export_plans_early(_event(
                "S1", project, "PreToolUse", tool, path, permission_mode="plan",
                harness_pid=101, harness_started_at_units=111,
            ))
        response = pe.recover_unexported_plans(_event(
            "S2", project, "SessionStart", source="clear",
            harness_pid=101, harness_started_at_units=222,
        ))
        assert response is None
        assert not _bodies(project / "notes")
        assert str(alpha) in self._pending(project)

    @pytest.mark.parametrize("pid", [101, 303, None], ids=["older-dialog", "plain-clear", "unknown"])
    def test_two_open_dialogs_require_matching_harness(self, project, plans_dir, pid):
        alpha = _plan(plans_dir, "alpha", "older dialog accepted")
        beta = _plan(plans_dir, "beta", "newer dialog unaccepted")
        for sid, plan, owner in [("S1", alpha, 101), ("S2", beta, 202)]:
            pe.track_and_export_plans_early(
                _event(sid, project, "PreToolUse", "Write", plan, harness_pid=owner)
            )
            self._exit(sid, project, pid=owner)

        pe.recover_unexported_plans(
            _event("S3", project, "SessionStart", source="clear", harness_pid=pid)
        )

        accepted = _bodies(project / "notes")
        assert "newer dialog unaccepted" not in accepted
        assert ("older dialog accepted" in accepted) is (pid == 101)
        pending = self._pending(project)
        assert str(beta) in pending
        assert (str(alpha) in pending) is (pid != 101)

    @pytest.mark.parametrize("s1_dialog_open", [False, True],
                             ids=["s1-still-writing", "s1-dialog-open"])
    def test_option1_in_s2_leaves_s1s_plan_pending(self, project, plans_dir, s1_dialog_open):
        # Session ids sort opposite to time; the process identifies S2.
        s1, s2 = "S9-first", "S0-second"
        alpha = _plan(plans_dir, "alpha", "written by session one")
        beta = _plan(plans_dir, "beta", "written by session two")
        self._write(s1, project, alpha, pid=101)
        if s1_dialog_open:
            self._exit(s1, project)
        self._write(s2, project, beta, pid=202)
        self._exit(s2, project, pid=202)

        # S2's user picks Option 1: S2 is replaced by S3 with source=clear.
        pe.recover_unexported_plans(_event("S3", project, "SessionStart", source="clear", harness_pid=202))

        assert "written by session two" in _bodies(project / "notes")
        assert "written by session one" not in _bodies(project / "notes"), (
            "S1's plan was exported as accepted by S2's acceptance"
        )
        if not s1_dialog_open:
            # With the dialog open, S1's own ExitPlanMode already put a backup
            # in notes/rejected/; what must not happen is S3 deciding for it.
            assert "written by session one" not in _bodies(project / "notes" / "rejected"), (
                "S1's unfinished plan was archived as abandoned"
            )
        pending = self._pending(project)
        assert str(alpha) in pending, "S1's plan was dropped from active_plans"
        assert bool(pending[str(alpha)].get("exit_attempted")) is s1_dialog_open

    def test_plain_clear_with_no_dialog_open_recovers_nothing(self, project, plans_dir):
        alpha = _plan(plans_dir, "alpha", "written by session one")
        self._write("S1", project, alpha)

        response = pe.recover_unexported_plans(
            _event("S3", project, "SessionStart", source="clear")
        )

        assert response is None, response
        assert not list(project.rglob("*.md")), list(project.rglob("*.md"))
        assert str(alpha) in self._pending(project)


def _exited_pid() -> int:
    """The pid of a process that has already exited."""
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                          capture_output=True, text=True, check=True)
    return int(done.stdout)


class TestStartupLeavesRunningSessionsPlansAlone:
    """SessionStart(startup/resume/compact) archived every unfinished plan in
    the project as abandoned: one another running session was still writing,
    and on resume or compact the continuing session's own plan."""

    def _write(self, session_id, project, plan, pid):
        pe.track_and_export_plans_early(
            _event(session_id, project, "PreToolUse", "Write", plan, harness_pid=pid)
        )

    def _pending(self, project):
        return pe.PlanExport(_event("probe", project, "PreToolUse")).active_plans

    @pytest.mark.parametrize("source", ["startup", "resume", "compact"])
    def test_a_plan_whose_session_is_still_running_is_left_pending(self, project, plans_dir, source):
        import os

        alpha = _plan(plans_dir, "alpha", "still being written")
        self._write("S1", project, alpha, os.getpid())  # this test process: alive

        pe.recover_unexported_plans(
            _event("S2", project, "SessionStart", source=source, harness_pid=_exited_pid())
        )

        assert "still being written" not in _bodies(project / "notes" / "rejected")
        assert str(alpha) in self._pending(project)

    @pytest.mark.parametrize("source", ["resume", "compact"])
    def test_the_continuing_sessions_own_plan_is_left_pending(self, project, plans_dir, source):
        alpha = _plan(plans_dir, "alpha", "mine, mid-plan")
        self._write("S1", project, alpha, _exited_pid())

        pe.recover_unexported_plans(_event("S1", project, "SessionStart", source=source))

        assert "mine, mid-plan" not in _bodies(project / "notes" / "rejected")
        assert str(alpha) in self._pending(project)

    def test_session_start_dispatch_runs_the_recovery_handler(self):
        """The handler is reached through app.dispatch, not only by direct call:
        a helper once landed between @app.on("SessionStart") and this function,
        and the decorator registered the helper instead."""
        from autorun.plugins import app

        handlers = [getattr(h, "__name__", "") for h in app.chains.get("SessionStart", [])]
        assert "recover_unexported_plans" in handlers
        assert "_plan_session_still_running" not in handlers

    def test_a_plan_whose_session_has_exited_is_still_recovered(self, project, plans_dir):
        alpha = _plan(plans_dir, "alpha", "left behind")
        self._write("S1", project, alpha, _exited_pid())

        pe.recover_unexported_plans(_event("S2", project, "SessionStart", source="startup"))

        assert "left behind" in _bodies(project / "notes" / "rejected")


def _exit_plan_mode(project, tool_result):
    """Run the PostToolUse(ExitPlanMode) export and return its notifications."""
    ctx = _event("S1", project, "PostToolUse", "ExitPlanMode", tool_result=tool_result)
    pe.export_on_exit_plan_mode(ctx)
    return " ".join(message for message, _channel in ctx._chain_notifications)


class TestDirectoriesOutsideTheProject:
    def test_absolute_dir_exports_and_reports_success(self, tmp_path, project, plans_dir):
        archive = tmp_path / "archive"
        pe.plan_export_command(f"dir {archive}", project, "claude")
        plan = _plan(plans_dir, "abs", "absolute archive")

        notice = _exit_plan_mode(project, {"filePath": str(plan)})

        assert "FAILED" not in notice, notice
        assert str(archive) in notice, notice
        assert "absolute archive" in _bodies(archive)

    def test_tilde_dir_expands_to_home(self, tmp_path, project, plans_dir):
        pe.plan_export_command("dir ~/plans-archive", project, "claude")
        plan = _plan(plans_dir, "tilde", "home archive")

        notice = _exit_plan_mode(project, {"filePath": str(plan)})

        assert "FAILED" not in notice, notice
        assert not (project / "~").exists(), "a directory literally named ~ was created"
        assert "home archive" in _bodies(tmp_path / "home" / "plans-archive")

    def test_tilde_path_in_exit_message_is_found(self, project, plans_dir):
        _plan(plans_dir, "said", "named in the message")

        notice = _exit_plan_mode(
            project, "Your plan has been saved to: ~/.claude/plans/said.md"
        )

        assert "no plan content found" not in notice, notice
        assert "named in the message" in _bodies(project / "notes")

    def test_absolute_rejected_dir_backup_returns_its_path(
        self, tmp_path, project, plans_dir
    ):
        rejected = tmp_path / "rejected-archive"
        pe.plan_export_command(f"rejected dir {rejected}", project, "claude")
        plan = _plan(plans_dir, "rej", "absolute rejected")
        exporter = pe.PlanExport(_event("S1", project, "PreToolUse", "ExitPlanMode"))

        backup = exporter.backup_to_rejected(plan, "plan")

        assert backup is not None
        assert Path(backup).parent == rejected


class TestRejectedOffWritesNoUnacceptedPlan:
    """`/ar:pe rejected off`: no plan that was not accepted reaches any directory."""

    @pytest.fixture(autouse=True)
    def rejected_off(self, project):
        pe.plan_export_command("rejected off", project, "claude")

    def _write(self, project, plans_dir, session_id="S1"):
        plan = _plan(plans_dir, "draft", "never accepted")
        pe.track_and_export_plans_early(
            _event(session_id, project, "PreToolUse", "Write", plan)
        )
        return plan

    def _recover(self, project, source):
        return pe.recover_unexported_plans(
            _event("S2", project, "SessionStart", source=source)
        )

    def test_abandoned_plan_is_not_written_to_the_accepted_dir(self, project, plans_dir):
        self._write(project, plans_dir)

        response = self._recover(project, "startup")

        assert not list(project.rglob("*.md")), list(project.rglob("*.md"))
        assert response is None, response

    def test_dismissed_plan_is_not_written_or_reported(self, project, plans_dir):
        self._write(project, plans_dir)
        pe.track_and_export_plans_early(
            _event("S1", project, "PreToolUse", "ExitPlanMode", permission_mode="plan")
        )

        response = self._recover(project, "startup")

        assert not list(project.rglob("*.md")), list(project.rglob("*.md"))
        assert response is None, response

    def test_option1_accept_still_exports_to_the_accepted_dir(self, project, plans_dir):
        self._write(project, plans_dir)
        pe.track_and_export_plans_early(
            _event("S1", project, "PreToolUse", "ExitPlanMode", permission_mode="plan")
        )

        response = self._recover(project, "clear")

        assert "never accepted" in _bodies(project / "notes")
        assert "Accepted:" in response["systemMessage"], response


def test_status_shows_the_directory_exports_use_for_a_legacy_output_dir(project):
    pe.CONFIG_PATH.parent.mkdir(parents=True)
    pe.CONFIG_PATH.write_text('{"output_dir": "docs/plans"}', encoding="utf-8")
    assert pe.PlanExportConfig.load(project).component_dir("accepted") == "docs/plans"

    status = pe.plan_export_command("", project, "claude")

    assert "accepted: on -> docs/plans" in status, status
