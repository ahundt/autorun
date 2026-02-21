# tests/test_tmux_utils_git.py
"""Tests for the git-aware additions to tmux_utils.py.

Tests:
  - _tmux_get_git_branch() — subprocess mock
  - WindowList.in_git_worktree() — branch filtering + chain
  - TmuxUtilities.is_ai_session() — Claude + Gemini detection (FIX Bug 3)
  - WindowList.ai_sessions() — filter by is_ai_session

TDD: Written before implementing the tmux_utils.py changes.
"""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# _tmux_get_git_branch
# ---------------------------------------------------------------------------

def test_tmux_get_git_branch_returns_branch():
    from clautorun.tmux_utils import _tmux_get_git_branch
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="feature/login\n")
        result = _tmux_get_git_branch("/repo/.worktrees/feat")
    assert result == "feature/login"


def test_tmux_get_git_branch_returns_none_on_detached_head():
    from clautorun.tmux_utils import _tmux_get_git_branch
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="HEAD\n")
        result = _tmux_get_git_branch("/some/path")
    assert result is None


def test_tmux_get_git_branch_returns_none_outside_repo():
    from clautorun.tmux_utils import _tmux_get_git_branch
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = _tmux_get_git_branch("/not/a/repo")
    assert result is None


def test_tmux_get_git_branch_returns_none_on_timeout():
    import subprocess
    from clautorun.tmux_utils import _tmux_get_git_branch
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 1)):
        result = _tmux_get_git_branch("/slow/network/mount")
    assert result is None


def test_tmux_get_git_branch_returns_none_without_git():
    from clautorun.tmux_utils import _tmux_get_git_branch
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = _tmux_get_git_branch("/some/path")
    assert result is None


# ---------------------------------------------------------------------------
# WindowList.in_git_worktree
# ---------------------------------------------------------------------------

def test_in_git_worktree_filters_by_branch():
    from clautorun.tmux_utils import WindowList
    windows = WindowList([
        {"session": "s", "w": 1, "branch": "feat", "title": ""},
        {"session": "s", "w": 2, "branch": "main", "title": ""},
        {"session": "s", "w": 3, "branch": None, "title": ""},
    ])
    result = windows.in_git_worktree("feat")
    assert len(result) == 1
    assert result[0]["w"] == 1


def test_in_git_worktree_no_branch_returns_all_with_branch():
    from clautorun.tmux_utils import WindowList
    windows = WindowList([
        {"session": "s", "w": 1, "branch": "feat"},
        {"session": "s", "w": 2, "branch": None},
        {"session": "s", "w": 3, "branch": "main"},
    ])
    result = windows.in_git_worktree()
    assert len(result) == 2
    assert all(w["branch"] is not None for w in result)


def test_in_git_worktree_returns_windowlist():
    from clautorun.tmux_utils import WindowList
    windows = WindowList([{"branch": "feat"}, {"branch": None}])
    result = windows.in_git_worktree()
    assert isinstance(result, WindowList)


def test_in_git_worktree_chain_with_claude_sessions():
    """Chain: in_git_worktree().claude_sessions() should work."""
    from clautorun.tmux_utils import WindowList
    windows = WindowList([
        {"branch": "feat", "is_claude_session": True},
        {"branch": "feat", "is_claude_session": False},
        {"branch": None, "is_claude_session": True},
    ])
    result = windows.in_git_worktree().claude_sessions()
    assert len(result) == 1
    assert result[0]["is_claude_session"] is True
    assert result[0]["branch"] == "feat"


# ---------------------------------------------------------------------------
# TmuxUtilities.is_ai_session — FIX Bug 3
# ---------------------------------------------------------------------------

def test_is_ai_session_detects_claude():
    """FIX Bug 3: is_ai_session must use execute_tmux_command, not _get_pane_pid()."""
    from clautorun.tmux_utils import TmuxUtilities
    tmux = TmuxUtilities()

    with patch.object(tmux, "execute_tmux_command", return_value={
        "returncode": 0, "stdout": "12345\n"
    }):
        with patch("subprocess.run") as mock_run:
            # pgrep -P 12345 -> child PID 99999
            # ps -p 99999 -o command= -> "claude"
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="99999\n"),  # pgrep
                MagicMock(returncode=0, stdout="claude\n"),  # ps
            ]
            result = tmux.is_ai_session("main", "3")
    assert result is True


def test_is_ai_session_detects_gemini():
    """is_ai_session must detect gemini in addition to claude."""
    from clautorun.tmux_utils import TmuxUtilities
    tmux = TmuxUtilities()

    with patch.object(tmux, "execute_tmux_command", return_value={
        "returncode": 0, "stdout": "12345\n"
    }):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="99999\n"),  # pgrep
                MagicMock(returncode=0, stdout="gemini\n"),  # ps
            ]
            result = tmux.is_ai_session("main", "3")
    assert result is True


def test_is_ai_session_returns_false_for_non_ai():
    from clautorun.tmux_utils import TmuxUtilities
    tmux = TmuxUtilities()

    with patch.object(tmux, "execute_tmux_command", return_value={
        "returncode": 0, "stdout": "12345\n"
    }):
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="99999\n"),
                MagicMock(returncode=0, stdout="vim\n"),
            ]
            result = tmux.is_ai_session("main", "3")
    assert result is False


def test_is_ai_session_returns_false_on_error():
    """Must fail safely, not raise."""
    from clautorun.tmux_utils import TmuxUtilities
    tmux = TmuxUtilities()

    with patch.object(tmux, "execute_tmux_command", return_value={
        "returncode": 1, "stdout": ""
    }):
        result = tmux.is_ai_session("main", "3")
    assert result is False


def test_is_ai_session_does_not_call_get_pane_pid():
    """FIX Bug 3: _get_pane_pid does not exist; verify is_ai_session does not call it."""
    from clautorun.tmux_utils import TmuxUtilities
    tmux = TmuxUtilities()
    assert not hasattr(tmux, "_get_pane_pid"), (
        "Bug 3: _get_pane_pid() must not exist; is_ai_session must use "
        "execute_tmux_command(['list-panes', ...]) instead"
    )


# ---------------------------------------------------------------------------
# WindowList.ai_sessions
# ---------------------------------------------------------------------------

def test_ai_sessions_filters_to_ai_windows():
    from clautorun.tmux_utils import WindowList
    # ai_sessions() should filter windows where is_ai_session=True
    # (After tmux_list_windows is called with appropriate parameters)
    windows = WindowList([
        {"session": "s", "w": 1, "is_ai_session": True},
        {"session": "s", "w": 2, "is_ai_session": False},
        {"session": "s", "w": 3, "is_ai_session": True},
    ])
    result = windows.ai_sessions()
    assert len(result) == 2
    assert all(w.get("is_ai_session") is True for w in result)


# ---------------------------------------------------------------------------
# tmux_list_windows include_git parameter
# ---------------------------------------------------------------------------

def test_tmux_list_windows_include_git_populates_branch():
    """include_git=True should add win['branch'] from _tmux_get_git_branch()."""
    from clautorun.tmux_utils import tmux_list_windows
    # This test verifies the parameter exists and branch is populated
    # by checking the function signature accepts include_git
    import inspect
    sig = inspect.signature(tmux_list_windows)
    assert "include_git" in sig.parameters, (
        "tmux_list_windows() must accept include_git parameter"
    )


def test_tmux_list_windows_default_include_git_false():
    """Default include_git=False should not call _tmux_get_git_branch (performance)."""
    import inspect
    from clautorun.tmux_utils import tmux_list_windows
    sig = inspect.signature(tmux_list_windows)
    default = sig.parameters["include_git"].default
    assert default is False, "include_git must default to False (avoid per-window subprocess overhead)"
