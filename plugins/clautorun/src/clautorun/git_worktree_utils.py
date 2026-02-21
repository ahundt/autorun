# File: plugins/clautorun/src/clautorun/git_worktree_utils.py
# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Pure git worktree operations and cross-session state tracking for clautorun.

This module contains ONLY git worktree logic — no tmux, no Claude session management.
The `wt_` function prefix stands for "git worktree" throughout this module.

Separation rationale: Pure git operations can be tested without a tmux daemon.
All functions here mock only subprocess.run; no tmux fixtures needed.

State tracking uses session_state("__global__") with key "git_worktrees" so
worktree→tmux mappings survive across Claude Code session restarts.

Reference implementation: /Users/athundt/.claude/cmux/cmux.sh (1099 lines, pure bash)
"""
import json
import os
import shutil
import subprocess
import time
from .session_manager import session_state


# ---------------------------------------------------------------------------
# Layout and path helpers
# ---------------------------------------------------------------------------

def wt_safe_name(branch: str) -> str:
    """Git worktree operation: convert git branch name to safe filesystem name.
    Replaces '/' with '-' so 'feature/login' becomes 'feature-login'.
    Used for git worktree directory names only — never used for display.
    Source: cmux.sh:76-78
    """
    return branch.replace('/', '-')


def wt_repo_root(cwd: str = None) -> str:
    """Git worktree operation: find the root of the main git repository.
    Uses 'git rev-parse --git-common-dir' which returns the same .git dir whether
    called from the main repo or any git worktree checkout. Resolves the parent of
    that .git dir as the repo root. Handles relative paths by joining with cwd.
    Raises subprocess.CalledProcessError if cwd is not inside a git repository.
    Source: cmux.sh:68-73
    """
    effective_cwd = cwd or os.getcwd()
    result = subprocess.run(
        ['git', 'rev-parse', '--git-common-dir'],
        cwd=effective_cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    git_common_dir = result.stdout.strip()
    # git-common-dir may be relative (e.g. ".git"); resolve to absolute
    if not os.path.isabs(git_common_dir):
        git_common_dir = os.path.join(effective_cwd, git_common_dir)
    # The parent of the .git dir is the repo root
    return os.path.realpath(os.path.dirname(git_common_dir))


def _find_repo_root_from(cwd: str = None) -> 'str | None':
    """Internal: find repo root from cwd without raising on error."""
    try:
        return wt_repo_root(cwd)
    except subprocess.CalledProcessError:
        return None


def wt_get_layout(repo_root: str) -> str:
    """Git worktree operation: read preferred git worktree directory layout.
    Search order: {repo_root}/.cmux/config.json → ~/.cmux/config.json → "nested" default.
    Valid layouts: "nested", "outer-nested", "sibling".
    Graceful on missing or malformed JSON (returns default).
    Config format: {"layout": "nested"}  (same as cmux)
    Source: cmux.sh:81-94
    """
    for config_path in [
        os.path.join(repo_root, '.cmux', 'config.json'),
        os.path.expanduser('~/.cmux/config.json'),
    ]:
        try:
            with open(config_path) as f:
                data = json.load(f)
            layout = data.get('layout', '')
            if layout in ('nested', 'outer-nested', 'sibling'):
                return layout
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return 'nested'


def wt_set_layout(layout: str, repo_root: str = None, global_config: bool = False) -> None:
    """Git worktree operation: set preferred git worktree directory layout.
    Raises ValueError if layout not in ("nested", "outer-nested", "sibling").
    Writes to {repo_root}/.cmux/config.json (repo-local) or ~/.cmux/config.json (global).
    Creates parent directories as needed. Preserves other keys in existing config.
    """
    if layout not in ('nested', 'outer-nested', 'sibling'):
        raise ValueError(
            f"Invalid layout {layout!r}. Choose: nested, outer-nested, sibling"
        )
    if global_config or not repo_root:
        config_path = os.path.expanduser('~/.cmux/config.json')
    else:
        config_path = os.path.join(repo_root, '.cmux', 'config.json')
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    try:
        with open(config_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data['layout'] = layout
    with open(config_path, 'w') as f:
        json.dump(data, f, indent=2)


def wt_worktree_dir(repo_root: str, branch: str) -> str:
    """Git worktree operation: compute the filesystem path for a git worktree.
    Layout-aware — reads wt_get_layout() to determine directory structure:
      nested (default): {repo_root}/.worktrees/{safe_name}
      outer-nested:     {parent_of_repo}/{repo_name}.worktrees/{safe_name}
      sibling:          {parent_of_repo}/{repo_name}-{safe_name}
    Returns the path string; does NOT create the directory.
    Source: cmux.sh:109-119
    """
    safe_name = wt_safe_name(branch)
    layout = wt_get_layout(repo_root)
    repo_name = os.path.basename(repo_root)
    parent = os.path.dirname(repo_root)
    if layout == 'outer-nested':
        return os.path.join(parent, f'{repo_name}.worktrees', safe_name)
    elif layout == 'sibling':
        return os.path.join(parent, f'{repo_name}-{safe_name}')
    else:  # nested (default)
        return os.path.join(repo_root, '.worktrees', safe_name)


# ---------------------------------------------------------------------------
# Git worktree list and detection
# ---------------------------------------------------------------------------

def wt_list(repo_root: str = None) -> 'list[dict]':
    """Git worktree operation: list all registered git worktrees for this repository.
    Runs 'git -C repo_root worktree list --porcelain'.
    Returns list of dicts: [{path: str, branch: str|None, head: str, locked: bool}].
    Branch field has 'refs/heads/' stripped (e.g. 'feature/login' not 'refs/heads/feature/login').
    Branch is None for detached HEAD worktrees.
    Returns [] gracefully if not in a git repository (no exception raised).
    Source: cmux.sh:387-406
    """
    effective_root = repo_root or _find_repo_root_from()
    if not effective_root:
        return []
    try:
        result = subprocess.run(
            ['git', '-C', effective_root, 'worktree', 'list', '--porcelain'],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    worktrees = []
    entry: dict = {}
    for line in result.stdout.splitlines():
        line = line.rstrip()
        if line.startswith('worktree '):
            if entry:
                worktrees.append(entry)
            entry = {'path': line[len('worktree '):], 'branch': None,
                     'head': None, 'locked': False}
        elif line.startswith('HEAD '):
            entry['head'] = line[len('HEAD '):]
        elif line.startswith('branch '):
            raw = line[len('branch '):]
            # Strip refs/heads/ prefix; detached HEADs have no branch line
            prefix = 'refs/heads/'
            entry['branch'] = raw[len(prefix):] if raw.startswith(prefix) else raw
        elif line == 'locked':
            entry['locked'] = True
        elif line == '' and entry:
            worktrees.append(entry)
            entry = {}
    if entry:
        worktrees.append(entry)
    return worktrees


def wt_detect_current_branch(repo_root: str = None, cwd: str = None) -> 'str | None':
    """Git worktree operation: detect which git branch the given cwd belongs to.
    Uses wt_list() to avoid duplicate subprocess calls.
    Matches os.path.realpath(cwd or os.getcwd()) against each worktree path
    using both exact match and startswith for subdirectory support.
    Returns branch name (without 'refs/heads/' prefix), or None if cwd is not
    inside any registered git worktree.
    NOTE: prefer wt_find_branch_for_cwd() which checks tracking dict first.
    Source: cmux.sh:122-171 (cmux uses PWD pattern; we use porcelain for robustness)
    """
    real_cwd = os.path.realpath(cwd or os.getcwd())
    worktrees = wt_list(repo_root or _find_repo_root_from(cwd))
    # Sort by path length descending: more specific (longer) paths match before
    # the main checkout root. Without sorting, "/repo" would wrongly match
    # "/repo/.worktrees/feat" because it starts with "/repo/".
    worktrees_sorted = sorted(worktrees, key=lambda w: len(w.get('path', '')), reverse=True)
    for wt in worktrees_sorted:
        wt_real = os.path.realpath(wt['path'])
        if real_cwd == wt_real or real_cwd.startswith(wt_real + os.sep):
            return wt.get('branch')  # None if detached HEAD
    return None


def wt_find_branch_for_cwd(cwd: str) -> 'str | None':
    """Git worktree operation: find which git branch the given directory belongs to.
    Single entry point for "what git worktree is my cwd in?" — used by all
    command handlers that auto-detect branch from ctx.cwd.

    Search order:
      1. Tracking dict (wt_get_tracked_sessions) — fast, works after git worktree repair
      2. Git scan (wt_detect_current_branch) — authoritative fallback

    Returns branch name string or None if cwd is not in any git worktree.
    """
    real_cwd = os.path.realpath(cwd)
    tracked = wt_get_tracked_sessions()
    for branch, info in tracked.items():
        wt_real = os.path.realpath(info.get('worktree_dir', ''))
        # FIX Issue 5: use startswith for subdirectory match, not just exact
        if real_cwd == wt_real or real_cwd.startswith(wt_real + os.sep):
            return branch
    return wt_detect_current_branch(cwd=cwd)


# ---------------------------------------------------------------------------
# Hook execution
# ---------------------------------------------------------------------------

def wt_run_hook(hook_name: str, worktree_dir: str, repo_root: str) -> dict:
    """Git worktree operation: run a lifecycle hook script for a git worktree.
    Searches for executable hook in order:
      1. {worktree_dir}/.cmux/{hook_name}  (worktree-local hook — preferred)
      2. {repo_root}/.cmux/{hook_name}     (repo-wide hook — fallback)
    If neither exists, returns {ran: False} without error.
    Uses subprocess.Popen for line-by-line output collection to avoid
    silently blocking for the full 120s timeout.
    Returns: {ran: bool, path: str|None, returncode: int|None,
              stdout: list[str], error: str|None}
    Hook scripts receive cwd=worktree_dir.
    Source: cmux.sh:283-301 (setup hook), cmux.sh:541-548 (teardown hook)
    """
    hook_path = None
    for candidate in [
        os.path.join(worktree_dir, '.cmux', hook_name),
        os.path.join(repo_root, '.cmux', hook_name),
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            hook_path = candidate
            break

    if hook_path is None:
        return {'ran': False, 'path': None, 'returncode': None,
                'stdout': [], 'error': None}

    lines: list = []
    try:
        proc = subprocess.Popen(
            [hook_path], cwd=worktree_dir,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        for line in proc.stdout:
            lines.append(line.rstrip())
            if len(lines) > 500:  # prevent runaway output accumulation
                break
        proc.wait(timeout=120)
        return {'ran': True, 'path': hook_path, 'returncode': proc.returncode,
                'stdout': lines, 'error': None}
    except subprocess.TimeoutExpired:
        proc.kill()
        return {'ran': True, 'path': hook_path, 'returncode': None,
                'stdout': lines, 'error': 'Hook timed out after 120s'}
    except Exception as e:
        return {'ran': True, 'path': hook_path, 'returncode': None,
                'stdout': lines, 'error': str(e)}


# ---------------------------------------------------------------------------
# Git worktree create / remove / merge
# ---------------------------------------------------------------------------

def wt_is_dirty(worktree_dir: str) -> bool:
    """Git worktree operation: check if a git worktree has uncommitted changes.
    Checks both unstaged (diff) and staged (diff --cached) changes.
    Returns True if dirty, False if clean. Returns False on error (fail-safe).
    Source: cmux.sh:521-527
    """
    for extra in [[], ['--cached']]:
        try:
            r = subprocess.run(
                ['git', '-C', worktree_dir, 'diff', '--quiet'] + extra,
                capture_output=True, timeout=10,
            )
            if r.returncode != 0:
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False
    return False


def wt_create_git(branch: str, repo_root: str, existing_branch: bool = False) -> dict:
    """Git worktree operation: create a new git worktree for a branch (git layer only).
    Idempotent: checks wt_list() first; if git worktree already exists for branch,
    returns {created: False, worktree_dir: <existing_path>, error: None}.
    Git command (from cmux.sh:269): git -C repo_root worktree add worktree_dir -b branch
    Returns: {worktree_dir: str, branch: str, created: bool, error: str|None}
    NO tmux operations. NO hook execution. NO Claude session management.
    Caller (git_worktree_plugin.py) handles those after this returns.
    """
    # Idempotency check
    for existing in wt_list(repo_root):
        if existing.get('branch') == branch:
            return {'worktree_dir': existing['path'], 'branch': branch,
                    'created': False, 'error': None}

    worktree_dir = wt_worktree_dir(repo_root, branch)

    # For nested/outer-nested layouts, ensure the base directory exists
    layout = wt_get_layout(repo_root)
    if layout != 'sibling':
        os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)

    # git worktree add <dir> -b <branch>  (dir comes first per git convention)
    cmd = ['git', '-C', repo_root, 'worktree', 'add', worktree_dir]
    if not existing_branch:
        cmd.extend(['-b', branch])
    else:
        cmd.append(branch)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            err = (result.stderr or result.stdout).strip()
            if os.path.isdir(worktree_dir):
                err += (f'\nStale directory exists at {worktree_dir}. '
                        f'Use --clean to remove it and retry.')
            return {'worktree_dir': worktree_dir, 'branch': branch,
                    'created': False, 'error': err}
    except subprocess.TimeoutExpired:
        return {'worktree_dir': worktree_dir, 'branch': branch, 'created': False,
                'error': 'git worktree add timed out after 30s'}

    return {'worktree_dir': worktree_dir, 'branch': branch,
            'created': True, 'error': None}


def wt_remove_git(branch: str, repo_root: str, force: bool = False) -> dict:
    """Git worktree operation: remove a git worktree and delete its branch (git layer only).
    Does NOT check dirty state — caller must call wt_is_dirty() BEFORE calling this.
    FIX Issue 7: uses registered path from wt_list(), not layout-computed path.
    Layout may have changed since worktree was created; wt_list() has ground truth.
    FIX Issue 8: checks locked worktrees before attempting removal.
    Runs: git worktree remove [--force] {worktree_dir}
    Then: git branch -d {branch} (soft; -D if force=True, per cmux.sh:556-559)
    Returns: {removed: bool, branch: str, worktree_dir: str, error: str|None}
    NO tmux operations. Caller must exit Claude BEFORE calling this.
    Source: cmux.sh:549-563
    """
    # FIX Issue 7: use registered path from wt_list(), not layout-computed path
    registered = {w['branch']: w for w in wt_list(repo_root)}
    wt_entry = registered.get(branch)
    if wt_entry:
        worktree_dir = wt_entry['path']
        # FIX Issue 8: check for locked worktrees
        if wt_entry.get('locked') and not force:
            return {'removed': False, 'branch': branch, 'worktree_dir': worktree_dir,
                    'error': (f'Git worktree {branch!r} is locked. '
                              f'Unlock first: git worktree unlock {worktree_dir}\n'
                              f'Then retry, or use --force.')}
    else:
        worktree_dir = wt_worktree_dir(repo_root, branch)

    if not os.path.isdir(worktree_dir):
        return {'removed': False, 'branch': branch, 'worktree_dir': worktree_dir,
                'error': f'Git worktree directory not found: {worktree_dir}'}

    rm_cmd = ['git', '-C', repo_root, 'worktree', 'remove']
    if force:
        rm_cmd.append('--force')
    rm_cmd.append(worktree_dir)

    try:
        r = subprocess.run(rm_cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            err = (r.stderr or r.stdout).strip()
            if not force:
                err += f'\nUse --force to remove a worktree with uncommitted changes.'
            return {'removed': False, 'branch': branch, 'worktree_dir': worktree_dir,
                    'error': err}
    except subprocess.TimeoutExpired:
        return {'removed': False, 'branch': branch, 'worktree_dir': worktree_dir,
                'error': 'git worktree remove timed out after 30s'}

    # Delete the branch (soft -d, hard -D if force)
    bd_flag = '-D' if force else '-d'
    try:
        subprocess.run(
            ['git', '-C', repo_root, 'branch', bd_flag, branch],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        pass  # Branch delete failure is non-fatal; worktree already removed

    return {'removed': True, 'branch': branch, 'worktree_dir': worktree_dir,
            'error': None}


def wt_merge(branch: str = None, repo_root: str = None, squash: bool = False) -> dict:
    """Git worktree operation: merge a git worktree branch into the current branch.
    Validates: not merging into self, target checkout is clean.
    Runs: git merge [--squash] {branch} from repo_root.
    Returns: {merged: bool, branch: str, target: str, error: str|None}
    Source: cmux.sh:408-484
    """
    if not repo_root:
        repo_root = _find_repo_root_from()
    if not repo_root:
        return {'merged': False, 'branch': branch, 'target': None,
                'error': 'Not in a git repository'}

    try:
        r = subprocess.run(
            ['git', '-C', repo_root, 'rev-parse', '--abbrev-ref', 'HEAD'],
            capture_output=True, text=True, timeout=10, check=True,
        )
        target_branch = r.stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {'merged': False, 'branch': branch, 'target': None,
                'error': 'Could not determine current branch in main checkout'}

    if branch == target_branch:
        return {'merged': False, 'branch': branch, 'target': target_branch,
                'error': f"Cannot merge '{branch}' into itself"}

    if wt_is_dirty(repo_root):
        return {'merged': False, 'branch': branch, 'target': target_branch,
                'error': 'Main checkout has uncommitted changes. Commit or stash first.'}

    merge_cmd = ['git', '-C', repo_root, 'merge']
    if squash:
        merge_cmd.append('--squash')
    merge_cmd.append(branch)

    try:
        r = subprocess.run(merge_cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            return {'merged': False, 'branch': branch, 'target': target_branch,
                    'error': (r.stderr or r.stdout).strip()}
    except subprocess.TimeoutExpired:
        return {'merged': False, 'branch': branch, 'target': target_branch,
                'error': 'git merge timed out after 60s'}

    return {'merged': True, 'branch': branch, 'target': target_branch, 'error': None}


# ---------------------------------------------------------------------------
# Global state tracking — persists git worktree → tmux window mappings
# ---------------------------------------------------------------------------

def wt_track_session(branch: str, worktree_dir: str,
                     tmux_session: str, tmux_window: int,
                     orchestrator_session_id: 'str | None' = None,
                     task_id: 'str | None' = None) -> None:
    """Git worktree tracking: record branch→tmux window mapping in persistent state.
    orchestrator_session_id: session_id of the Claude/Gemini session that created this
      worktree. Worker AI reads this on SessionStart to find its delegated tasks via
      TaskLifecycle(session_id=orchestrator_session_id).
    task_id: TaskLifecycle task ID representing the delegated work (if -p was used).
    Uses session_state("__global__") key "git_worktrees".
    """
    with session_state('__global__') as st:
        wts = dict(st.get('git_worktrees') or {})
        wts[branch] = {
            'worktree_dir': worktree_dir,
            'tmux_session': tmux_session,
            'tmux_window': tmux_window,
            'orchestrator_session_id': orchestrator_session_id,
            'task_id': task_id,
            'created_at': time.time(),
        }
        st['git_worktrees'] = wts


def wt_get_tracked_sessions() -> dict:
    """Git worktree tracking: retrieve all recorded branch→tmux window mappings.
    Returns: {branch: {worktree_dir, tmux_session, tmux_window, created_at, ...}}
    """
    with session_state('__global__') as st:
        return dict(st.get('git_worktrees') or {})


def wt_untrack_session(branch: str) -> None:
    """Git worktree tracking: remove branch→tmux window mapping from persistent state."""
    with session_state('__global__') as st:
        wts = dict(st.get('git_worktrees') or {})
        wts.pop(branch, None)
        st['git_worktrees'] = wts


def wt_update_task_status(branch: str, status: str, notes: str = '') -> None:
    """Git worktree tracking: update task status from worker session.

    Called by worker AI when it completes or pauses work on a delegated task.
    Uses session_state('__global__') — the shared coordination space — NOT the
    orchestrator's TaskLifecycle namespace, which would be a cross-session write.

    status: 'running', 'completed', 'paused', 'failed'
    notes:  brief human-readable summary of work done (shown in /cr:wt ls)
    """
    with session_state('__global__') as st:
        wts = dict(st.get('git_worktrees') or {})
        if branch in wts:
            wts[branch]['task_status'] = status
            wts[branch]['task_notes'] = notes
            wts[branch]['task_updated_at'] = time.time()
            st['git_worktrees'] = wts


# ---------------------------------------------------------------------------
# AI CLI abstraction — Gemini and Claude first-class support
# ---------------------------------------------------------------------------

def wt_get_ai_cli(repo_root: str = None) -> str:
    """Git worktree operation: return the AI CLI start command for new sessions.

    Reading order:
      1. {repo_root}/.cmux/config.json "ai_cli" key (repo-local override)
      2. ~/.cmux/config.json "ai_cli" key (user-global override)
      3. detect_cli_type() — infers from GEMINI_SESSION_ID / env vars (config.py)
      4. "claude" (default fallback)

    Returns "gemini" or "claude" (always the CLI binary name).
    Used for fresh session launch: send_text_and_enter(tmux, wt_get_ai_cli(repo_root))
    Enables Gemini and Claude as first-class citizens without hardcoding.
    """
    from .config import detect_cli_type  # lazy import — avoid circular at module level
    config_paths = []
    if repo_root:
        config_paths.append(os.path.join(repo_root, '.cmux', 'config.json'))
    config_paths.append(os.path.expanduser('~/.cmux/config.json'))

    for config_path in config_paths:
        try:
            with open(config_path) as f:
                data = json.load(f)
            cli = data.get('ai_cli', '').strip()
            if cli in ('claude', 'gemini'):
                return cli
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    # Fall back to env-var-based detection (works transparently in hooks context)
    return detect_cli_type() or 'claude'


def wt_get_ai_resume_cmd(repo_root: str = None) -> str:
    """Git worktree operation: return the AI CLI command to resume a previous session.

    Returns:
      "claude -c"  — Claude Code resumes most recent conversation (cmux.sh:355)
      "gemini"     — Gemini CLI has no separate resume flag; same binary re-opens context
    Source: cmux.sh:355 confirms 'claude -c' for resume.
    """
    cli = wt_get_ai_cli(repo_root)
    if cli == 'gemini':
        return 'gemini'   # Gemini re-opens context automatically on restart
    return 'claude -c'
