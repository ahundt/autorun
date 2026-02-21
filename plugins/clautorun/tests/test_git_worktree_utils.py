# tests/test_git_worktree_utils.py
"""Unit tests for git_worktree_utils.py — pure git worktree operations.

All tests mock subprocess.run; no real git repository or tmux daemon needed.
Use @pytest.mark.integration for tests requiring a real git repo.

TDD: These tests were written to verify the implementation in git_worktree_utils.py.
Every function has at least one test. Bug fixes from the plan review have
corresponding regression tests.
"""
import json
import os
import time
import pytest
from unittest.mock import MagicMock, patch, mock_open, call


# ---------------------------------------------------------------------------
# Helper stubs
# ---------------------------------------------------------------------------

def _make_porcelain_output(*entries):
    """Build git worktree list --porcelain output for test entries.
    Each entry is a dict with keys: path, head, branch (optional), locked (bool).
    """
    lines = []
    for e in entries:
        lines.append(f"worktree {e['path']}")
        if e.get("head"):
            lines.append(f"HEAD {e['head']}")
        if e.get("branch"):
            lines.append(f"branch refs/heads/{e['branch']}")
        if e.get("locked"):
            lines.append("locked")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# wt_safe_name
# ---------------------------------------------------------------------------

def test_wt_safe_name_slash():
    from clautorun.git_worktree_utils import wt_safe_name
    assert wt_safe_name("feature/login") == "feature-login"


def test_wt_safe_name_no_slash():
    from clautorun.git_worktree_utils import wt_safe_name
    assert wt_safe_name("my-branch") == "my-branch"


def test_wt_safe_name_multi_slash():
    from clautorun.git_worktree_utils import wt_safe_name
    assert wt_safe_name("a/b/c") == "a-b-c"


# ---------------------------------------------------------------------------
# wt_repo_root
# ---------------------------------------------------------------------------

def test_wt_repo_root_absolute_git_dir():
    from clautorun.git_worktree_utils import wt_repo_root
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0, stdout="/tmp/myrepo/.git\n"
        )
        result = wt_repo_root(cwd="/tmp/myrepo/src")
    # realpath resolves symlinks; on macOS /tmp -> /private/tmp
    assert result == os.path.realpath("/tmp/myrepo")


def test_wt_repo_root_relative_git_dir():
    from clautorun.git_worktree_utils import wt_repo_root
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=".git\n")
        result = wt_repo_root(cwd="/tmp/myrepo")
    # realpath("/tmp/myrepo/.git") -> parent is /tmp/myrepo
    assert "myrepo" in result


def test_wt_repo_root_raises_outside_repo():
    import subprocess
    from clautorun.git_worktree_utils import wt_repo_root
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(128, "git")):
        with pytest.raises(subprocess.CalledProcessError):
            wt_repo_root(cwd="/tmp/notarepo")


# ---------------------------------------------------------------------------
# wt_get_layout / wt_set_layout
# ---------------------------------------------------------------------------

def test_wt_get_layout_returns_nested_default(tmp_path):
    from clautorun.git_worktree_utils import wt_get_layout
    # No config files present — should return "nested"
    result = wt_get_layout(str(tmp_path))
    assert result == "nested"


def test_wt_get_layout_reads_repo_config(tmp_path):
    from clautorun.git_worktree_utils import wt_get_layout
    cmux_dir = tmp_path / ".cmux"
    cmux_dir.mkdir()
    (cmux_dir / "config.json").write_text('{"layout": "sibling"}')
    result = wt_get_layout(str(tmp_path))
    assert result == "sibling"


def test_wt_get_layout_invalid_value_falls_back(tmp_path):
    from clautorun.git_worktree_utils import wt_get_layout
    cmux_dir = tmp_path / ".cmux"
    cmux_dir.mkdir()
    (cmux_dir / "config.json").write_text('{"layout": "bogus"}')
    result = wt_get_layout(str(tmp_path))
    assert result == "nested"


def test_wt_set_layout_writes_config(tmp_path):
    from clautorun.git_worktree_utils import wt_set_layout, wt_get_layout
    wt_set_layout("outer-nested", repo_root=str(tmp_path))
    result = wt_get_layout(str(tmp_path))
    assert result == "outer-nested"


def test_wt_set_layout_invalid_raises():
    from clautorun.git_worktree_utils import wt_set_layout
    with pytest.raises(ValueError):
        wt_set_layout("invalid-layout")


# ---------------------------------------------------------------------------
# wt_worktree_dir — parametrized for all 3 layouts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("layout,expected_suffix", [
    ("nested", ".worktrees/feature-auth"),
    ("outer-nested", "myrepo.worktrees/feature-auth"),
    ("sibling", "myrepo-feature-auth"),
])
def test_wt_worktree_dir_layouts(tmp_path, layout, expected_suffix):
    from clautorun.git_worktree_utils import wt_worktree_dir
    # Create repo dir at tmp_path/myrepo
    repo = tmp_path / "myrepo"
    repo.mkdir()
    cmux_dir = repo / ".cmux"
    cmux_dir.mkdir()
    (cmux_dir / "config.json").write_text(f'{{"layout": "{layout}"}}')
    result = wt_worktree_dir(str(repo), "feature/auth")
    assert result.endswith(expected_suffix)


# ---------------------------------------------------------------------------
# wt_list
# ---------------------------------------------------------------------------

def test_wt_list_parses_porcelain():
    from clautorun.git_worktree_utils import wt_list
    porcelain = _make_porcelain_output(
        {"path": "/repo", "head": "abc1234", "branch": "main"},
        {"path": "/repo/.worktrees/feat", "head": "def5678", "branch": "feature/x"},
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
        result = wt_list("/repo")
    assert len(result) == 2
    assert result[0]["branch"] == "main"
    assert result[1]["branch"] == "feature/x"
    assert result[1]["head"] == "def5678"


def test_wt_list_strips_refs_heads_prefix():
    from clautorun.git_worktree_utils import wt_list
    porcelain = "worktree /repo\nHEAD abc\nbranch refs/heads/my-branch\n\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
        result = wt_list("/repo")
    assert result[0]["branch"] == "my-branch"


def test_wt_list_returns_empty_on_error():
    from clautorun.git_worktree_utils import wt_list
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=128, stdout="")
        result = wt_list("/repo")
    assert result == []


def test_wt_list_returns_empty_without_git():
    from clautorun.git_worktree_utils import wt_list
    with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
        result = wt_list("/repo")
    assert result == []


def test_wt_list_marks_locked():
    from clautorun.git_worktree_utils import wt_list
    porcelain = "worktree /repo/.worktrees/feat\nHEAD abc\nbranch refs/heads/feat\nlocked\n\n"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
        result = wt_list("/repo")
    assert result[0]["locked"] is True


# ---------------------------------------------------------------------------
# wt_detect_current_branch
# ---------------------------------------------------------------------------

def test_wt_detect_current_branch_exact_match():
    """Pass repo_root explicitly to avoid subprocess mock ambiguity with _find_repo_root_from."""
    from clautorun.git_worktree_utils import wt_detect_current_branch
    porcelain = _make_porcelain_output(
        {"path": "/repo", "head": "abc", "branch": "main"},
        {"path": "/repo/.worktrees/feat", "head": "def", "branch": "feat"},
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
        result = wt_detect_current_branch(repo_root="/repo", cwd="/repo/.worktrees/feat")
    assert result == "feat"


def test_wt_detect_current_branch_subdirectory():
    """FIX Issue 5: cwd inside a subdirectory of the worktree should match."""
    from clautorun.git_worktree_utils import wt_detect_current_branch
    porcelain = _make_porcelain_output(
        {"path": "/repo/.worktrees/feat", "head": "abc", "branch": "feat"},
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
        result = wt_detect_current_branch(cwd="/repo/.worktrees/feat/src/subdir")
    assert result == "feat"


def test_wt_detect_current_branch_no_match():
    from clautorun.git_worktree_utils import wt_detect_current_branch
    porcelain = _make_porcelain_output(
        {"path": "/other/repo", "head": "abc", "branch": "main"},
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=porcelain)
        result = wt_detect_current_branch(cwd="/my/unrelated/dir")
    assert result is None


# ---------------------------------------------------------------------------
# wt_find_branch_for_cwd — FIX Issue 5 regression test
# ---------------------------------------------------------------------------

def test_wt_find_branch_for_cwd_uses_tracking_dict_first(tmp_path):
    """Tracking dict lookup should succeed with prefix (subdirectory) match."""
    from clautorun.git_worktree_utils import wt_find_branch_for_cwd
    worktree_dir = str(tmp_path / "feat")
    os.makedirs(worktree_dir, exist_ok=True)

    tracked = {"feat": {"worktree_dir": worktree_dir}}
    with patch("clautorun.git_worktree_utils.wt_get_tracked_sessions", return_value=tracked):
        # Exact match
        result = wt_find_branch_for_cwd(worktree_dir)
    assert result == "feat"


def test_wt_find_branch_for_cwd_prefix_match(tmp_path):
    """FIX Issue 5: subdirectory of worktree should match via startswith."""
    from clautorun.git_worktree_utils import wt_find_branch_for_cwd
    worktree_dir = str(tmp_path / "feat")
    sub_dir = str(tmp_path / "feat" / "src" / "lib")
    os.makedirs(sub_dir, exist_ok=True)

    tracked = {"feat": {"worktree_dir": worktree_dir}}
    with patch("clautorun.git_worktree_utils.wt_get_tracked_sessions", return_value=tracked):
        result = wt_find_branch_for_cwd(sub_dir)
    assert result == "feat"


def test_wt_find_branch_for_cwd_falls_back_to_git_scan(tmp_path):
    """Fallback to wt_detect_current_branch when not in tracking dict."""
    from clautorun.git_worktree_utils import wt_find_branch_for_cwd
    with patch("clautorun.git_worktree_utils.wt_get_tracked_sessions", return_value={}):
        with patch("clautorun.git_worktree_utils.wt_detect_current_branch", return_value="feat") as mock_detect:
            result = wt_find_branch_for_cwd("/some/dir")
    assert result == "feat"
    mock_detect.assert_called_once()


# ---------------------------------------------------------------------------
# wt_run_hook
# ---------------------------------------------------------------------------

def test_wt_run_hook_no_hook_found(tmp_path):
    from clautorun.git_worktree_utils import wt_run_hook
    result = wt_run_hook("setup", str(tmp_path / "wt"), str(tmp_path))
    assert result["ran"] is False
    assert result["path"] is None
    assert result["error"] is None


def test_wt_run_hook_runs_worktree_local_first(tmp_path):
    """Worktree-local hook takes priority over repo-wide hook."""
    from clautorun.git_worktree_utils import wt_run_hook
    wt_dir = tmp_path / "wt"
    (wt_dir / ".cmux").mkdir(parents=True)
    repo_hook = tmp_path / ".cmux" / "setup"
    repo_hook.parent.mkdir(exist_ok=True)
    repo_hook.write_text("#!/bin/bash\necho repo"); repo_hook.chmod(0o755)
    wt_hook = wt_dir / ".cmux" / "setup"
    wt_hook.write_text("#!/bin/bash\necho worktree"); wt_hook.chmod(0o755)

    result = wt_run_hook("setup", str(wt_dir), str(tmp_path))
    assert result["ran"] is True
    assert "wt" in result["path"]  # worktree-local path used


def test_wt_run_hook_falls_back_to_repo_hook(tmp_path):
    """Falls back to repo-wide hook if no worktree-local hook."""
    from clautorun.git_worktree_utils import wt_run_hook
    wt_dir = tmp_path / "wt"
    wt_dir.mkdir()
    repo_hook = tmp_path / ".cmux" / "setup"
    repo_hook.parent.mkdir()
    repo_hook.write_text("#!/bin/bash\necho repo"); repo_hook.chmod(0o755)

    result = wt_run_hook("setup", str(wt_dir), str(tmp_path))
    assert result["ran"] is True
    assert result["returncode"] == 0


# ---------------------------------------------------------------------------
# wt_is_dirty
# ---------------------------------------------------------------------------

def test_wt_is_dirty_returns_true_when_unstaged_changes():
    from clautorun.git_worktree_utils import wt_is_dirty
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        result = wt_is_dirty("/repo")
    assert result is True


def test_wt_is_dirty_returns_false_when_clean():
    from clautorun.git_worktree_utils import wt_is_dirty
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = wt_is_dirty("/repo")
    assert result is False


# ---------------------------------------------------------------------------
# wt_create_git
# ---------------------------------------------------------------------------

def test_wt_create_git_idempotent_if_exists():
    """If worktree already exists, returns created=False without re-running git."""
    from clautorun.git_worktree_utils import wt_create_git
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[
        {"path": "/repo/.worktrees/feat", "branch": "feat", "head": "abc", "locked": False}
    ]):
        result = wt_create_git("feat", "/repo")
    assert result["created"] is False
    assert result["error"] is None
    assert result["worktree_dir"] == "/repo/.worktrees/feat"


def test_wt_create_git_creates_new_worktree():
    from clautorun.git_worktree_utils import wt_create_git
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[]):
        with patch("clautorun.git_worktree_utils.wt_get_layout", return_value="nested"):
            with patch("os.makedirs"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    result = wt_create_git("feat", "/repo")
    assert result["created"] is True
    assert result["error"] is None


def test_wt_create_git_returns_error_on_git_failure():
    from clautorun.git_worktree_utils import wt_create_git
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[]):
        with patch("clautorun.git_worktree_utils.wt_get_layout", return_value="nested"):
            with patch("os.makedirs"):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock(
                        returncode=1, stdout="", stderr="already exists"
                    )
                    with patch("os.path.isdir", return_value=False):
                        result = wt_create_git("feat", "/repo")
    assert result["created"] is False
    assert result["error"] is not None


# ---------------------------------------------------------------------------
# wt_remove_git — FIX Issue 7 (use registered path) + Issue 8 (locked check)
# ---------------------------------------------------------------------------

def test_wt_remove_git_uses_registered_path():
    """FIX Issue 7: wt_remove_git must look up path from wt_list, not compute it."""
    from clautorun.git_worktree_utils import wt_remove_git
    # Simulate layout changed after creation: registered path is old, computed is new
    registered_path = "/old-layout/.worktrees/feat"
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[
        {"branch": "feat", "path": registered_path, "locked": False}
    ]):
        with patch("os.path.isdir", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = wt_remove_git("feat", "/repo")
    # The registered path, not the computed path, was used
    rm_call = mock_run.call_args_list[0]
    assert registered_path in rm_call.args[0]
    assert result["removed"] is True


def test_wt_remove_git_blocks_locked_worktree():
    """FIX Issue 8: locked worktrees return actionable error without removal."""
    from clautorun.git_worktree_utils import wt_remove_git
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": True}
    ]):
        result = wt_remove_git("feat", "/repo", force=False)
    assert result["removed"] is False
    assert "locked" in result["error"].lower()


def test_wt_remove_git_force_removes_locked():
    """With force=True, locked worktrees are removed anyway."""
    from clautorun.git_worktree_utils import wt_remove_git
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": True}
    ]):
        with patch("os.path.isdir", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                result = wt_remove_git("feat", "/repo", force=True)
    assert result["removed"] is True


def test_wt_remove_git_not_found():
    from clautorun.git_worktree_utils import wt_remove_git
    with patch("clautorun.git_worktree_utils.wt_list", return_value=[]):
        with patch("os.path.isdir", return_value=False):
            result = wt_remove_git("feat", "/repo")
    assert result["removed"] is False
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# wt_merge
# ---------------------------------------------------------------------------

def test_wt_merge_succeeds():
    from clautorun.git_worktree_utils import wt_merge
    with patch("clautorun.git_worktree_utils.wt_is_dirty", return_value=False):
        with patch("subprocess.run") as mock_run:
            # First call: rev-parse (get current branch = main)
            # Second call: git merge
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="main\n"),
                MagicMock(returncode=0, stdout="", stderr=""),
            ]
            result = wt_merge("feat", "/repo")
    assert result["merged"] is True
    assert result["target"] == "main"


def test_wt_merge_blocks_dirty_main():
    from clautorun.git_worktree_utils import wt_merge
    with patch("clautorun.git_worktree_utils.wt_is_dirty", return_value=True):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="main\n")
            result = wt_merge("feat", "/repo")
    assert result["merged"] is False
    assert "uncommitted" in result["error"].lower()


def test_wt_merge_blocks_self_merge():
    from clautorun.git_worktree_utils import wt_merge
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="feat\n")
        result = wt_merge("feat", "/repo")
    assert result["merged"] is False
    assert "itself" in result["error"].lower()


# ---------------------------------------------------------------------------
# State tracking
# ---------------------------------------------------------------------------

def test_wt_track_and_get_session():
    """Use dict-backed session_state mock (SessionStore not in public API)."""
    from clautorun.git_worktree_utils import wt_track_session, wt_get_tracked_sessions

    # Simulate session_state("__global__") as a dict context manager
    shared_store = {}

    class _FakeSS:
        def __enter__(self):
            return shared_store
        def __exit__(self, *_):
            return False

    with patch("clautorun.git_worktree_utils.session_state", return_value=_FakeSS()):
        wt_track_session("feat", "/repo/.worktrees/feat", "mysession", 3,
                         orchestrator_session_id="orch-123", task_id="task-456")
        tracked = wt_get_tracked_sessions()

    assert "feat" in tracked
    assert tracked["feat"]["tmux_session"] == "mysession"
    assert tracked["feat"]["tmux_window"] == 3
    assert tracked["feat"]["orchestrator_session_id"] == "orch-123"


def test_wt_untrack_session():
    """Use dict-backed session_state mock."""
    from clautorun.git_worktree_utils import wt_track_session, wt_untrack_session, wt_get_tracked_sessions

    shared_store = {}

    class _FakeSS:
        def __enter__(self):
            return shared_store
        def __exit__(self, *_):
            return False

    with patch("clautorun.git_worktree_utils.session_state", return_value=_FakeSS()):
        wt_track_session("feat", "/repo/.worktrees/feat", "s", 1)
        wt_untrack_session("feat")
        tracked = wt_get_tracked_sessions()
    assert "feat" not in tracked


# ---------------------------------------------------------------------------
# wt_get_ai_cli
# ---------------------------------------------------------------------------

def test_wt_get_ai_cli_reads_config(tmp_path):
    from clautorun.git_worktree_utils import wt_get_ai_cli
    (tmp_path / ".cmux").mkdir()
    (tmp_path / ".cmux" / "config.json").write_text('{"ai_cli": "gemini"}')
    result = wt_get_ai_cli(repo_root=str(tmp_path))
    assert result == "gemini"


def test_wt_get_ai_cli_falls_back_to_claude():
    from clautorun.git_worktree_utils import wt_get_ai_cli
    with patch("clautorun.git_worktree_utils.json.load", side_effect=FileNotFoundError):
        with patch("clautorun.config.detect_cli_type", return_value=None):
            result = wt_get_ai_cli()
    assert result == "claude"
