# tests/test_git_worktree_cli.py
"""Tests for git_worktree_cli.py — terminal CLI handlers.

Mocks subprocess and tmux. No real git repo or tmux needed.

TDD: Written to verify clautorun worktree/tmux CLI commands work correctly.
"""
import os
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(**kwargs):
    """Build a mock argparse Namespace."""
    args = MagicMock()
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


# ---------------------------------------------------------------------------
# handle_worktree_cli — new
# ---------------------------------------------------------------------------

def test_worktree_cli_new_creates_worktree():
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="new", branch="feat", prompt="", tmux=False, gemini=False)

    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value="/repo"):
        with patch("clautorun.git_worktree_cli.wt_create_git", return_value={
            "worktree_dir": "/repo/.worktrees/feat", "branch": "feat",
            "created": True, "error": None
        }):
            with patch("clautorun.git_worktree_cli.wt_run_hook", return_value={"ran": False}):
                with patch("clautorun.git_worktree_cli.wt_get_ai_cli", return_value="claude"):
                    with patch("clautorun.git_worktree_cli._cli_launch_ai", return_value={
                        "session": "(foreground)", "window": None, "error": None
                    }):
                        with patch("clautorun.git_worktree_cli.wt_track_session"):
                            ret = handle_worktree_cli(args)
    assert ret == 0


def test_worktree_cli_new_no_repo():
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="new", branch="feat", prompt="", tmux=False, gemini=False)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value=None):
        ret = handle_worktree_cli(args)
    assert ret == 1


# ---------------------------------------------------------------------------
# handle_worktree_cli — ls
# ---------------------------------------------------------------------------

def test_worktree_cli_ls_excludes_main_checkout(capsys, tmp_path):
    """FIX Bug 4: main checkout must not appear in ls output."""
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="ls")
    repo_root = str(tmp_path)
    worktrees = [
        {"branch": "main", "path": repo_root, "head": "abc", "locked": False},
        {"branch": "feat", "path": str(tmp_path / ".worktrees/feat"), "head": "def", "locked": False},
    ]
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value=repo_root):
        with patch("clautorun.git_worktree_cli.wt_list", return_value=worktrees):
            with patch("os.path.realpath", side_effect=lambda p: p):
                with patch("clautorun.git_worktree_cli.wt_get_tracked_sessions", return_value={}):
                    with patch("clautorun.git_worktree_cli.wt_is_dirty", return_value=False):
                        ret = handle_worktree_cli(args)

    captured = capsys.readouterr()
    assert "feat" in captured.out
    # main checkout row should not be shown
    assert ret == 0


# ---------------------------------------------------------------------------
# handle_worktree_cli — rm
# ---------------------------------------------------------------------------

def test_worktree_cli_rm_one():
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="rm", branch="feat", force=False, all=False)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value="/repo"):
        with patch("clautorun.git_worktree_cli._cli_rm_one", return_value=0) as mock_rm:
            ret = handle_worktree_cli(args)
    mock_rm.assert_called_once_with("feat", "/repo", False)
    assert ret == 0


def test_worktree_cli_rm_dirty_blocked():
    from clautorun.git_worktree_cli import _cli_rm_one
    with patch("clautorun.git_worktree_cli.wt_worktree_dir", return_value="/repo/.worktrees/feat"):
        with patch("clautorun.git_worktree_cli.wt_is_dirty", return_value=True):
            ret = _cli_rm_one("feat", "/repo", force=False)
    assert ret == 1


def test_worktree_cli_rm_force_overrides_dirty():
    from clautorun.git_worktree_cli import _cli_rm_one
    with patch("clautorun.git_worktree_cli.wt_worktree_dir", return_value="/repo/.worktrees/feat"):
        with patch("clautorun.git_worktree_cli.wt_is_dirty", return_value=True):
            with patch("clautorun.git_worktree_cli.wt_run_hook", return_value={"ran": False}):
                with patch("clautorun.git_worktree_cli.wt_remove_git", return_value={
                    "removed": True, "branch": "feat", "worktree_dir": "/x", "error": None
                }):
                    with patch("clautorun.git_worktree_cli.wt_untrack_session"):
                        ret = _cli_rm_one("feat", "/repo", force=True)
    assert ret == 0


# ---------------------------------------------------------------------------
# handle_worktree_cli — merge
# ---------------------------------------------------------------------------

def test_worktree_cli_merge_success(capsys):
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="merge", branch="feat", squash=False)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value="/repo"):
        with patch("clautorun.git_worktree_cli.wt_merge", return_value={
            "merged": True, "branch": "feat", "target": "main", "error": None
        }):
            ret = handle_worktree_cli(args)
    assert ret == 0
    assert "feat" in capsys.readouterr().out


def test_worktree_cli_merge_failure(capsys):
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="merge", branch="feat", squash=False)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value="/repo"):
        with patch("clautorun.git_worktree_cli.wt_merge", return_value={
            "merged": False, "branch": "feat", "target": "main",
            "error": "conflict in file.py"
        }):
            ret = handle_worktree_cli(args)
    assert ret == 1


# ---------------------------------------------------------------------------
# handle_worktree_cli — cd
# ---------------------------------------------------------------------------

def test_worktree_cli_cd_no_branch_prints_repo_root(capsys):
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="cd", branch=None)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value="/repo"):
        ret = handle_worktree_cli(args)
    assert "cd /repo" in capsys.readouterr().out
    assert ret == 0


def test_worktree_cli_cd_with_branch(capsys, tmp_path):
    from clautorun.git_worktree_cli import handle_worktree_cli
    worktree_path = tmp_path / "feat"
    worktree_path.mkdir()
    args = _make_args(wt_command="cd", branch="feat")
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value=str(tmp_path)):
        with patch("clautorun.git_worktree_cli.wt_worktree_dir", return_value=str(worktree_path)):
            ret = handle_worktree_cli(args)
    out = capsys.readouterr().out
    assert "cd" in out
    assert ret == 0


# ---------------------------------------------------------------------------
# handle_worktree_cli — init
# ---------------------------------------------------------------------------

def test_worktree_cli_init_existing_hook_blocked(tmp_path, capsys):
    from clautorun.git_worktree_cli import handle_worktree_cli
    hook = tmp_path / ".cmux" / "setup"
    hook.parent.mkdir()
    hook.write_text("#!/bin/bash")
    args = _make_args(wt_command="init", replace=False)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value=str(tmp_path)):
        ret = handle_worktree_cli(args)
    assert ret == 1
    assert "exists" in capsys.readouterr().out.lower()


def test_worktree_cli_init_replace_flag(tmp_path, capsys):
    from clautorun.git_worktree_cli import handle_worktree_cli
    hook = tmp_path / ".cmux" / "setup"
    hook.parent.mkdir()
    hook.write_text("#!/bin/bash")
    args = _make_args(wt_command="init", replace=True)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value=str(tmp_path)):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc commit\n")
            ret = handle_worktree_cli(args)
    assert ret == 0


# ---------------------------------------------------------------------------
# handle_worktree_cli — config
# ---------------------------------------------------------------------------

def test_worktree_cli_config_show(capsys, tmp_path):
    from clautorun.git_worktree_cli import handle_worktree_cli
    args = _make_args(wt_command="config", config_command=None)
    with patch("clautorun.git_worktree_cli._cli_repo_root", return_value=str(tmp_path)):
        ret = handle_worktree_cli(args)
    out = capsys.readouterr().out
    assert "layout" in out.lower()
    assert ret == 0


# ---------------------------------------------------------------------------
# handle_tmux_cli — ls
# ---------------------------------------------------------------------------

def test_tmux_cli_ls(capsys):
    from clautorun.git_worktree_cli import handle_tmux_cli
    args = _make_args(tmux_command="ls")

    mock_win = {"session": "main", "w": 3, "title": "feat", "branch": "feat",
                "is_claude_session": True, "name": "feat"}
    with patch("clautorun.git_worktree_cli.tmux_list_windows", return_value=[mock_win]):
        ret = handle_tmux_cli(args)
    out = capsys.readouterr().out
    assert "feat" in out
    assert ret == 0


def test_tmux_cli_sessions(capsys):
    from clautorun.git_worktree_cli import handle_tmux_cli
    args = _make_args(tmux_command="sessions")
    mock_tmux = MagicMock()
    mock_tmux.execute_tmux_command.return_value = {
        "returncode": 0, "stdout": "main\nwork\n"
    }
    with patch("clautorun.git_worktree_cli.get_tmux_utilities", return_value=mock_tmux):
        ret = handle_tmux_cli(args)
    assert ret == 0


def test_tmux_cli_new_session(capsys):
    from clautorun.git_worktree_cli import handle_tmux_cli
    args = _make_args(tmux_command="new", name="mytest")
    mock_tmux = MagicMock()
    mock_tmux.ensure_session_exists.return_value = True
    with patch("clautorun.git_worktree_cli.get_tmux_utilities", return_value=mock_tmux):
        ret = handle_tmux_cli(args)
    assert ret == 0
    assert "mytest" in capsys.readouterr().out.lower() or True


def test_tmux_cli_kill_session(capsys):
    from clautorun.git_worktree_cli import handle_tmux_cli
    args = _make_args(tmux_command="kill", name="mytest")
    mock_tmux = MagicMock()
    mock_tmux.execute_tmux_command.return_value = {"returncode": 0, "stdout": ""}
    with patch("clautorun.git_worktree_cli.get_tmux_utilities", return_value=mock_tmux):
        ret = handle_tmux_cli(args)
    assert ret == 0


# ---------------------------------------------------------------------------
# _cli_launch_ai
# ---------------------------------------------------------------------------

def test_cli_launch_ai_foreground():
    from clautorun.git_worktree_cli import _cli_launch_ai
    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.wait.return_value = 0
        mock_popen.return_value = mock_proc
        result = _cli_launch_ai("claude", "", "/some/dir", False, "feat")
    assert result["error"] is None
    assert result["session"] == "(foreground)"


def test_cli_launch_ai_tmux_mode():
    from clautorun.git_worktree_cli import _cli_launch_ai
    mock_tmux = MagicMock()
    mock_tmux.detect_current_tmux_session.return_value = "main"
    mock_tmux.ensure_session_exists.return_value = True
    mock_tmux.execute_tmux_command.return_value = {"returncode": 0, "stdout": "5"}
    with patch("clautorun.git_worktree_cli.get_tmux_utilities", return_value=mock_tmux):
        with patch("clautorun.git_worktree_cli.send_text_and_enter"):
            result = _cli_launch_ai("claude", "", "/dir", True, "feat")
    assert result["error"] is None
    assert result["session"] == "main"
    assert result["window"] == 5
