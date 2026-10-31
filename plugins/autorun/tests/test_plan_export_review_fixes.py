"""Plan export regressions found in the 1.0.0 review, each through its real entry point.

- Two sessions in one project: ExitPlanMode must back up the calling session's
  plan, not whichever plan the other session happened to record first.
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
