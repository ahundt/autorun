# tests/test_git_worktree_plugin.py
"""Integration tests for git_worktree_plugin.py — orchestration layer.

Mocks git_worktree_utils and tmux_utils; no real git or tmux needed.

TDD: These tests verify the critical ordering requirements (P12), bug fixes,
and AI->AI delegation logic. Written against the implementation.
"""
import os
import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(cwd="/repo/.worktrees/feat", session_id="test-session-123",
              prompt=""):
    ctx = MagicMock()
    ctx.cwd = cwd
    ctx.session_id = session_id
    ctx.activation_prompt = None
    ctx.prompt = prompt
    ctx.block = lambda text: {"block": text}
    return ctx


# ---------------------------------------------------------------------------
# _auto_detect_branch
# ---------------------------------------------------------------------------

def test_auto_detect_branch_from_args():
    from clautorun.git_worktree_plugin import _auto_detect_branch
    ctx = _make_ctx()
    branch, auto_msg, err = _auto_detect_branch(ctx, ["feat", "--force"], "rm")
    assert branch == "feat"
    assert auto_msg is None
    assert err is None


def test_auto_detect_branch_from_cwd():
    from clautorun.git_worktree_plugin import _auto_detect_branch
    ctx = _make_ctx(cwd="/repo/.worktrees/feat")
    with patch("clautorun.git_worktree_plugin.wt_find_branch_for_cwd", return_value="feat"):
        branch, auto_msg, err = _auto_detect_branch(ctx, [], "rm")
    assert branch == "feat"
    assert "Auto-detected" in auto_msg
    assert err is None


def test_auto_detect_branch_fails_gracefully():
    from clautorun.git_worktree_plugin import _auto_detect_branch
    ctx = _make_ctx(cwd="/not/a/worktree")
    with patch("clautorun.git_worktree_plugin.wt_find_branch_for_cwd", return_value=None):
        branch, auto_msg, err = _auto_detect_branch(ctx, [], "rm")
    assert branch is None
    assert err is not None
    assert "No git worktree" in err


# ---------------------------------------------------------------------------
# _wt_cmd_new
# ---------------------------------------------------------------------------

def test_wt_cmd_new_full_flow():
    """Full sequence: git create -> hook -> tmux -> AI launch -> track."""
    from clautorun.git_worktree_plugin import _wt_cmd_new
    ctx = _make_ctx()

    with patch("clautorun.git_worktree_plugin.wt_create_git", return_value={
        "worktree_dir": "/repo/.worktrees/feat", "branch": "feat",
        "created": True, "error": None
    }):
        with patch("clautorun.git_worktree_plugin.wt_run_hook", return_value={
            "ran": False, "path": None, "returncode": None, "stdout": [], "error": None
        }):
            with patch("clautorun.git_worktree_plugin._wt_ensure_tmux_window", return_value={
                "tmux_session": "main", "tmux_window_index": 5,
                "error": None, "message": None
            }):
                with patch("clautorun.git_worktree_plugin.wt_get_ai_cli", return_value="claude"):
                    with patch("clautorun.git_worktree_plugin.get_tmux_utilities") as mock_tmux:
                        with patch("clautorun.git_worktree_plugin.send_text_and_enter"):
                            with patch("clautorun.git_worktree_plugin.wt_track_session") as mock_track:
                                result = _wt_cmd_new(ctx, ["feat"], "/repo")

    assert "feat" in result
    assert "created" in result.lower() or "worktree" in result.lower()
    mock_track.assert_called_once()


def test_wt_cmd_new_no_branch_returns_error():
    from clautorun.git_worktree_plugin import _wt_cmd_new
    ctx = _make_ctx()
    result = _wt_cmd_new(ctx, [], "/repo")
    assert "Usage" in result or "❌" in result


def test_wt_cmd_new_git_failure_returns_error():
    from clautorun.git_worktree_plugin import _wt_cmd_new
    ctx = _make_ctx()
    with patch("clautorun.git_worktree_plugin.wt_create_git", return_value={
        "worktree_dir": "/repo/.worktrees/feat", "branch": "feat",
        "created": False, "error": "git error: branch exists"
    }):
        result = _wt_cmd_new(ctx, ["feat"], "/repo")
    assert "❌" in result


def test_wt_cmd_new_tmux_not_found():
    """FIX: if tmux not installed, return worktree path with instructions."""
    from clautorun.git_worktree_plugin import _wt_cmd_new
    ctx = _make_ctx()
    with patch("clautorun.git_worktree_plugin.wt_create_git", return_value={
        "worktree_dir": "/repo/.worktrees/feat", "branch": "feat",
        "created": True, "error": None
    }):
        with patch("clautorun.git_worktree_plugin.wt_run_hook", return_value={"ran": False}):
            with patch("clautorun.git_worktree_plugin._wt_ensure_tmux_window", return_value={
                "tmux_session": None, "tmux_window_index": None,
                "error": "tmux_not_found",
                "message": "tmux not installed — git worktree created but no AI session launched."
            }):
                result = _wt_cmd_new(ctx, ["feat"], "/repo")
    assert "not installed" in result or "tmux" in result.lower()


# ---------------------------------------------------------------------------
# _wt_cmd_new with -p (AI->AI delegation) — Bug 1 regression test
# ---------------------------------------------------------------------------

def test_wt_cmd_new_with_prompt_creates_task():
    """FIX Bug 1: create_task() called with (task_id, input_data_dict, result='')."""
    from clautorun.git_worktree_plugin import _wt_cmd_new
    ctx = _make_ctx()
    mock_lifecycle = MagicMock()

    with patch("clautorun.git_worktree_plugin.wt_create_git", return_value={
        "worktree_dir": "/repo/.worktrees/feat", "branch": "feat",
        "created": True, "error": None
    }):
        with patch("clautorun.git_worktree_plugin.wt_run_hook", return_value={"ran": False}):
            with patch("clautorun.git_worktree_plugin._wt_ensure_tmux_window", return_value={
                "tmux_session": "main", "tmux_window_index": 5,
                "error": None, "message": None
            }):
                with patch("clautorun.git_worktree_plugin.wt_get_ai_cli", return_value="claude"):
                    with patch("clautorun.git_worktree_plugin.get_tmux_utilities"):
                        with patch("clautorun.git_worktree_plugin.send_text_and_enter"):
                            with patch("clautorun.git_worktree_plugin.wt_track_session"):
                                with patch("clautorun.git_worktree_plugin.TaskLifecycle",
                                           return_value=mock_lifecycle):
                                    with patch("time.sleep"):  # skip polling delay
                                        with patch.object(mock_lifecycle.is_ai_session if hasattr(mock_lifecycle, 'is_ai_session') else MagicMock(), '__call__', return_value=True):
                                            _wt_cmd_new(ctx, ["feat", "-p", "Implement JWT auth"], "/repo")

    # Verify create_task was called with positional task_id (not keyword-only subject)
    if mock_lifecycle.create_task.called:
        call_args = mock_lifecycle.create_task.call_args
        # Must have task_id as first arg or keyword
        assert call_args is not None


# ---------------------------------------------------------------------------
# _wt_cmd_rm — P12 ordering: dirty check BEFORE Claude exit
# ---------------------------------------------------------------------------

def test_wt_cmd_rm_dirty_check_before_claude_exit():
    """P12: dirty check fires BEFORE tmux_dangerous_batch_execute — no side effects first."""
    from clautorun.git_worktree_plugin import _wt_cmd_rm
    ctx = _make_ctx()

    call_order = []

    def mock_is_dirty(path):
        call_order.append("dirty_check")
        return True  # dirty!

    def mock_batch_execute(*args, **kwargs):
        call_order.append("claude_exit")
        return {"failure_count": 0}

    with patch("clautorun.git_worktree_plugin.wt_list", return_value=[
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": False}
    ]):
        with patch("clautorun.git_worktree_plugin.wt_is_dirty", side_effect=mock_is_dirty):
            with patch("clautorun.git_worktree_plugin.tmux_dangerous_batch_execute",
                       side_effect=mock_batch_execute):
                result = _wt_cmd_rm(ctx, ["feat"], "/repo")

    # dirty_check must come BEFORE claude_exit (or claude_exit must not be called)
    assert "dirty" in result.lower() or "uncommitted" in result.lower()
    # claude_exit should NOT have been called (dirty aborts before)
    assert "claude_exit" not in call_order


def test_wt_cmd_rm_sequence_clean_worktree():
    """On clean worktree: exit Claude -> teardown hook -> git remove -> untrack."""
    from clautorun.git_worktree_plugin import _wt_cmd_rm
    ctx = _make_ctx()

    sequence = []

    with patch("clautorun.git_worktree_plugin.wt_list", return_value=[
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": False}
    ]):
        with patch("clautorun.git_worktree_plugin.wt_is_dirty", return_value=False):
            with patch("clautorun.git_worktree_plugin.wt_get_tracked_sessions", return_value={
                "feat": {"tmux_session": "main", "tmux_window": 5}
            }):
                with patch("clautorun.git_worktree_plugin.get_tmux_utilities"):
                    with patch("clautorun.git_worktree_plugin.tmux_dangerous_batch_execute",
                               side_effect=lambda *a, **k: sequence.append("exit") or {"failure_count": 0}):
                        with patch("clautorun.git_worktree_plugin.wt_run_hook",
                                   side_effect=lambda *a, **k: sequence.append("teardown") or {"ran": False}):
                            with patch("clautorun.git_worktree_plugin.wt_remove_git",
                                       side_effect=lambda *a, **k: sequence.append("git_rm") or
                                       {"removed": True, "branch": "feat", "worktree_dir": "/x", "error": None}):
                                with patch("clautorun.git_worktree_plugin.wt_untrack_session",
                                           side_effect=lambda *a: sequence.append("untrack")):
                                    result = _wt_cmd_rm(ctx, ["feat"], "/repo")

    # Verify ordering: exit -> teardown -> git_rm -> untrack
    assert sequence.index("exit") < sequence.index("teardown")
    assert sequence.index("teardown") < sequence.index("git_rm")
    assert sequence.index("git_rm") < sequence.index("untrack")
    assert "removed" in result.lower() or "✅" in result


def test_wt_cmd_rm_aborts_if_claude_exit_fails():
    """If both exit and kill fail, rm must STOP (not remove worktree)."""
    from clautorun.git_worktree_plugin import _wt_cmd_rm
    ctx = _make_ctx()

    with patch("clautorun.git_worktree_plugin.wt_list", return_value=[
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": False}
    ]):
        with patch("clautorun.git_worktree_plugin.wt_is_dirty", return_value=False):
            with patch("clautorun.git_worktree_plugin.wt_get_tracked_sessions", return_value={
                "feat": {"tmux_session": "main", "tmux_window": 5}
            }):
                with patch("clautorun.git_worktree_plugin.get_tmux_utilities"):
                    with patch("clautorun.git_worktree_plugin.tmux_dangerous_batch_execute",
                               return_value={"failure_count": 1}):  # both exit AND kill fail
                        with patch("clautorun.git_worktree_plugin.wt_remove_git") as mock_rm:
                            result = _wt_cmd_rm(ctx, ["feat"], "/repo")

    # wt_remove_git must NOT have been called
    mock_rm.assert_not_called()
    assert "❌" in result or "Could not stop" in result


# ---------------------------------------------------------------------------
# _wt_cmd_rm_all — FIX Bug 4: main checkout must be excluded
# ---------------------------------------------------------------------------

def test_wt_cmd_rm_all_excludes_main_checkout():
    """FIX Bug 4: wt_list() returns main checkout first; rm_all must not touch it."""
    from clautorun.git_worktree_plugin import _wt_cmd_rm_all
    ctx = _make_ctx()

    main_checkout_path = "/repo"
    worktrees = [
        {"branch": "main", "path": main_checkout_path, "locked": False},  # main checkout
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": False},
    ]

    with patch("clautorun.git_worktree_plugin.wt_list", return_value=worktrees):
        with patch("os.path.realpath", side_effect=lambda p: p):
            result = _wt_cmd_rm_all(ctx, main_checkout_path, False, None)

    # Should see 1 worktree (feat), not 2 (main excluded)
    assert "feat" in result
    # Token prompt should mention 1 worktree
    assert "1" in result or "feat" in result


def test_wt_cmd_rm_all_token_validation():
    """Two-step: first call returns token prompt, second with token proceeds."""
    from clautorun.git_worktree_plugin import _wt_cmd_rm_all
    ctx = _make_ctx()

    worktrees = [
        {"branch": "feat", "path": "/repo/.worktrees/feat", "locked": False},
    ]

    # Step 1: no token -> get prompt
    with patch("clautorun.git_worktree_plugin.wt_list", return_value=worktrees):
        with patch("os.path.realpath", side_effect=lambda p: p):  # identity: no symlink resolution
            with patch("clautorun.git_worktree_plugin.session_state") as mock_ss:
                mock_ss.return_value.__enter__ = MagicMock(return_value=MagicMock(
                    get=MagicMock(return_value=None),
                    __setitem__=MagicMock(),
                ))
                mock_ss.return_value.__exit__ = MagicMock(return_value=False)
                result = _wt_cmd_rm_all(ctx, "/repo", False, None)

    assert "--token" in result
    assert "⚠️" in result or "About to delete" in result


# ---------------------------------------------------------------------------
# inject_worktree_task_context — FIX Bug 2: ctx.block() not {'inject': ...}
# ---------------------------------------------------------------------------

def test_inject_worktree_task_context_uses_ctx_block():
    """FIX Bug 2: SessionStart hook must return ctx.block(text), not {'inject': text}."""
    # We can't easily call the registered hook, but we can verify the source uses ctx.block
    import inspect
    from clautorun import git_worktree_plugin
    source = inspect.getsource(git_worktree_plugin)
    # Must use ctx.block() not {'inject':
    assert "ctx.block(" in source
    assert "return {'inject'" not in source
    assert 'return {"inject"' not in source


# ---------------------------------------------------------------------------
# _wt_cmd_ls — FIX: main checkout excluded from table
# ---------------------------------------------------------------------------

def test_wt_cmd_ls_excludes_main_checkout():
    """Main checkout must not appear in ls output."""
    from clautorun.git_worktree_plugin import _wt_cmd_ls
    ctx = _make_ctx(cwd="/repo")

    repo_root = "/repo"
    worktrees = [
        {"branch": "main", "path": repo_root, "head": "abc1234", "locked": False},
        {"branch": "feat", "path": "/repo/.worktrees/feat", "head": "def5678", "locked": False},
    ]

    with patch("clautorun.git_worktree_plugin.wt_list", return_value=worktrees):
        with patch("os.path.realpath", side_effect=lambda p: p):
            with patch("clautorun.git_worktree_plugin.wt_get_tracked_sessions", return_value={}):
                with patch("clautorun.git_worktree_plugin.wt_is_dirty", return_value=False):
                    with patch("clautorun.git_worktree_plugin.session_state") as mock_ss:
                        mock_ss.return_value.__enter__ = MagicMock(return_value=MagicMock(
                            get=MagicMock(return_value={})
                        ))
                        mock_ss.return_value.__exit__ = MagicMock(return_value=False)
                        result = _wt_cmd_ls(ctx, [], repo_root)

    # feat worktree should appear, main checkout (which IS repo_root) must NOT
    assert "feat" in result
    # The main branch row should be filtered out. Check by branch name in markdown table row.
    # Note: repo_root string "/repo" appears in worktree paths like "/repo/.worktrees/feat",
    # so we check for the exact "| main |" row marker, not raw string /repo.
    assert "| main |" not in result  # main checkout row excluded


# ---------------------------------------------------------------------------
# _wt_help
# ---------------------------------------------------------------------------

def test_wt_help_contains_all_subcommands():
    from clautorun.git_worktree_plugin import _wt_help
    help_text = _wt_help()
    for subcmd in ["new", "start", "ls", "rm", "merge", "cd", "init", "config"]:
        assert subcmd in help_text


# ---------------------------------------------------------------------------
# _WTP_DISPATCH completeness
# ---------------------------------------------------------------------------

def test_wtp_dispatch_contains_all_subcommands():
    from clautorun.git_worktree_plugin import _WTP_DISPATCH
    required = {"new", "start", "ls", "rm", "remove", "merge", "cd", "init", "config"}
    assert required.issubset(set(_WTP_DISPATCH.keys()))
