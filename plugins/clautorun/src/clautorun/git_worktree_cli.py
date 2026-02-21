# File: plugins/clautorun/src/clautorun/git_worktree_cli.py
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
"""Terminal CLI handlers for clautorun worktree/tmux subcommands.

Reuses git_worktree_utils.py functions — no logic duplication.
No EventContext: uses os.getcwd() for repo_root, subprocess for AI launch.
Terminal equivalent of the /cr:wt slash command and /cr:tabs.

Usage via clautorun CLI:
    clautorun worktree new <branch> [-p "prompt"] [--tmux]
    clautorun worktree ls
    clautorun worktree rm <branch> [--force]
    clautorun worktree rm --all
    clautorun worktree merge <branch> [--squash]
    clautorun worktree cd [branch]
    clautorun worktree init [--replace]
    clautorun worktree config [set layout <preset>]
    clautorun tmux ls
    clautorun tmux sessions
    clautorun tmux new [name]
    clautorun tmux kill [name]
    clautorun tmux attach [name]
"""
import os
import subprocess
import sys

from .git_worktree_utils import (
    wt_find_branch_for_cwd,
    wt_get_ai_cli,
    wt_get_layout,
    wt_get_tracked_sessions,
    wt_is_dirty,
    wt_list,
    wt_merge,
    wt_remove_git,
    wt_repo_root,
    wt_run_hook,
    wt_set_layout,
    wt_track_session,
    wt_untrack_session,
    wt_worktree_dir,
    wt_create_git,
)
from .tmux_utils import get_tmux_utilities, tmux_list_windows, send_text_and_enter


def _cli_repo_root() -> 'str | None':
    """Get repo root from current working directory (no EventContext)."""
    try:
        return wt_repo_root(cwd=os.getcwd())
    except subprocess.CalledProcessError:
        return None


def _cli_launch_ai(ai_cmd: str, prompt: str, cwd: str, tmux_flag: bool,
                   branch: str) -> dict:
    """Launch AI session. Two modes:
    - Default (no --tmux): foreground subprocess.Popen, blocking (like cmux)
    - --tmux: new tmux window, non-blocking
    Returns {'session': str, 'window': int, 'error': str|None}
    """
    if tmux_flag:
        tmux = get_tmux_utilities()
        session = tmux.detect_current_tmux_session() or 'clautorun'
        tmux.ensure_session_exists(session)
        # FIX Issue 6: use -P -F to atomically get new window index
        idx_result = tmux.execute_tmux_command(
            ['new-window', '-P', '-F', '#{window_index}', '-n', branch, '-c', cwd],
            session=session,
        )
        if not idx_result or idx_result.get('returncode') != 0:
            return {'session': None, 'window': None, 'error': 'tmux new-window failed'}
        idx_str = idx_result.get('stdout', '').strip()
        window_idx = int(idx_str) if idx_str.isdigit() else None
        cmd_str = ai_cmd if not prompt else f'{ai_cmd} -p {prompt!r}'
        send_text_and_enter(tmux, cmd_str, session=session, window=str(window_idx))
        return {'session': session, 'window': window_idx, 'error': None}
    else:
        # Foreground blocking — replaces current process interaction (like cmux)
        cmd = [ai_cmd] + (['-p', prompt] if prompt else [])
        proc = subprocess.Popen(cmd, cwd=cwd)
        proc.wait()
        return {'session': '(foreground)', 'window': None, 'error': None}


def handle_worktree_cli(args) -> int:
    """Dispatch clautorun worktree <subcmd> to git_worktree_utils functions."""
    repo_root = _cli_repo_root()
    cmd = getattr(args, 'wt_command', None)

    if cmd in ('new', 'n'):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        branch = args.branch
        prompt = getattr(args, 'prompt', '')
        tmux_flag = getattr(args, 'tmux', False)
        ai_cli = 'gemini' if getattr(args, 'gemini', False) else wt_get_ai_cli()

        result = wt_create_git(branch, repo_root)
        if not result['created'] and result.get('error'):
            print(f"Error: {result['error']}"); return 1
        worktree_dir = result['worktree_dir']
        created = result['created']

        hook_result = wt_run_hook('setup', worktree_dir, repo_root)
        if hook_result.get('ran'):
            print(f'Setup hook: exit {hook_result["returncode"]}')

        launch = _cli_launch_ai(ai_cli, prompt, worktree_dir, tmux_flag, branch)
        if launch['error']:
            print(f"Warning: AI launch failed: {launch['error']}")
        else:
            wt_track_session(branch, worktree_dir,
                             launch['session'] or '', launch['window'],
                             orchestrator_session_id=None, task_id=None)
            status = "created" if created else "already exists"
            print(f'OK  Worktree {branch!r} {status} at {worktree_dir}')
        return 0

    elif cmd in ('start', 's'):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        branch = getattr(args, 'branch', '') or wt_find_branch_for_cwd(os.getcwd())
        if not branch:
            print('Error: no branch specified and not inside a worktree'); return 1
        worktree_dir = wt_worktree_dir(repo_root, branch)
        prompt = getattr(args, 'prompt', '')
        tmux_flag = getattr(args, 'tmux', False)
        ai_cli = 'gemini' if getattr(args, 'gemini', False) else wt_get_ai_cli()
        resume_flags = '-c' + (f' -p {prompt}' if prompt else '')
        launch = _cli_launch_ai(ai_cli, resume_flags, worktree_dir, tmux_flag, branch)
        if not launch['error']:
            wt_track_session(branch, worktree_dir,
                             launch['session'] or '', launch['window'])
        return 0

    elif cmd in ('ls', 'list', 'l'):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        # FIX Bug 4: exclude main checkout from listing
        worktrees = [w for w in wt_list(repo_root)
                     if os.path.realpath(w['path']) != os.path.realpath(repo_root)]
        tracked = wt_get_tracked_sessions()
        print(f'{"Branch":<25} {"HEAD":>7}  {"tmux":<20}  Dirty')
        print('-' * 65)
        for wt in worktrees:
            b = wt.get('branch') or '(detached)'
            head = (wt.get('head') or '')[:7]
            dirty = 'X' if wt_is_dirty(wt['path']) else 'OK'
            t = tracked.get(b, {})
            tmux_info = f'{t["tmux_session"]}:{t["tmux_window"]}' if t else '--'
            print(f'{b:<25} {head:>7}  {tmux_info:<20}  {dirty}')
        return 0

    elif cmd in ('rm', 'remove'):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        all_flag = getattr(args, 'all', False)
        force = getattr(args, 'force', False)
        if all_flag:
            # Terminal rm --all uses input() not tokens (tokens are for AI)
            # FIX Bug 4: exclude main checkout
            worktrees = [w for w in wt_list(repo_root)
                         if w.get('branch') and
                         os.path.realpath(w['path']) != os.path.realpath(repo_root)]
            if not worktrees:
                print('No worktrees to remove.'); return 0
            names = ', '.join(w['branch'] for w in worktrees)
            confirm = input(f'Remove {len(worktrees)} worktrees ({names})? [yes/N] ')
            if confirm.strip().lower() != 'yes':
                print('Aborted.'); return 1
            for wt in worktrees:
                _cli_rm_one(wt['branch'], repo_root, force)
            return 0
        branch = getattr(args, 'branch', '') or wt_find_branch_for_cwd(os.getcwd())
        if not branch:
            print('Error: no branch specified'); return 1
        return _cli_rm_one(branch, repo_root, force)

    elif cmd in ('merge', 'm'):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        branch = getattr(args, 'branch', '') or wt_find_branch_for_cwd(os.getcwd())
        if not branch:
            print('Error: no branch specified and not inside a worktree'); return 1
        squash = getattr(args, 'squash', False)
        result = wt_merge(branch, repo_root, squash=squash)
        if result['merged']:
            print(f'OK  Merged {branch!r} into {result["target"]!r}')
            return 0
        print(f'Error: {result["error"]}'); return 1

    elif cmd in ('cd',):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        branch = getattr(args, 'branch', None)
        path = repo_root if not branch else wt_worktree_dir(repo_root, branch)
        if branch and not os.path.isdir(path):
            print(f'Error: worktree not found: {path}'); return 1
        # Print for eval: eval $(clautorun worktree cd [branch])
        print(f'cd {path}')
        return 0

    elif cmd in ('init', 'i'):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        replace = getattr(args, 'replace', False)
        hook_path = os.path.join(repo_root, '.cmux', 'setup')
        if os.path.exists(hook_path) and not replace:
            print(f'Error: {hook_path} exists. Use --replace to overwrite.'); return 1
        # Print context for user/AI to generate the hook
        log = subprocess.run(['git', '-C', repo_root, 'log', '--oneline', '-5'],
                             capture_output=True, text=True)
        print(f'Generate .cmux/setup for {repo_root}')
        if log.returncode == 0:
            print(f'Recent commits:\n{log.stdout.strip()}')
        print(f'Write executable bash script to: {hook_path}')
        return 0

    elif cmd in ('config',):
        if not repo_root:
            print('Error: not in a git repository'); return 1
        sub = getattr(args, 'config_command', None)
        if not sub or sub == 'show':
            print(f'layout: {wt_get_layout(repo_root)}')
            return 0
        layout = getattr(args, 'layout', None)
        global_flag = getattr(args, 'global_config', False)
        if sub == 'set' and layout:
            try:
                wt_set_layout(layout, None if global_flag else repo_root,
                              global_config=global_flag)
            except ValueError as e:
                print(f'Error: {e}'); return 1
            scope = 'globally' if global_flag else f'for {repo_root}'
            print(f'OK  Layout set to {layout!r} {scope}')
            return 0

    parser_ref = getattr(args, '_parser', None)
    if parser_ref:
        parser_ref.print_help()
    return 1


def _cli_rm_one(branch: str, repo_root: str, force: bool) -> int:
    """Remove a single worktree from CLI (no EventContext, no tmux exit)."""
    worktree_dir = wt_worktree_dir(repo_root, branch)
    if wt_is_dirty(worktree_dir) and not force:
        print(f'Error: {branch!r} has uncommitted changes. Use --force to override.')
        return 1
    hook_result = wt_run_hook('teardown', worktree_dir, repo_root)
    result = wt_remove_git(branch, repo_root, force=force)
    if result['removed']:
        wt_untrack_session(branch)
        print(f'OK  Removed worktree {branch!r}')
        return 0
    print(f'Error: {result["error"]}')
    return 1


def handle_tmux_cli(args) -> int:
    """Dispatch clautorun tmux <subcmd> to TmuxUtilities.
    Terminal equivalent of /cr:tabs + /cr:tmux slash commands.
    """
    tmux = get_tmux_utilities()
    cmd = getattr(args, 'tmux_command', None)

    if cmd in ('ls', 'list'):
        windows = tmux_list_windows(include_git=True)
        if not windows:
            print('No tmux windows found.'); return 0
        print(f'{"Session:Window":<25} {"Name":<20} {"Branch":<25} AI')
        print('-' * 80)
        for w in windows:
            target = f'{w.get("session","?")}:{w.get("w","?")}'
            name = w.get('name', w.get('title', ''))[:20]
            branch = (w.get('branch') or '--')[:25]
            ai = 'Y' if w.get('is_claude_session') else '--'
            print(f'{target:<25} {name:<20} {branch:<25} {ai}')
        return 0

    elif cmd == 'sessions':
        result = tmux.execute_tmux_command(['list-sessions', '-F', '#{session_name}'])
        if result and result.get('returncode') == 0:
            print(result['stdout'].strip())
        return 0

    elif cmd == 'new':
        name = getattr(args, 'name', 'clautorun')
        created = tmux.ensure_session_exists(name)
        print(f'{"Created" if created else "Exists"}: session {name!r}')
        return 0

    elif cmd == 'kill':
        name = getattr(args, 'name', None)
        if not name:
            name = tmux.detect_current_tmux_session()
        if not name:
            print('Error: no session name specified and cannot auto-detect'); return 1
        result = tmux.execute_tmux_command(['kill-session', '-t', name])
        if result and result.get('returncode') == 0:
            print(f'Killed session {name!r}')
            return 0
        print(f'Error killing session {name!r}'); return 1

    elif cmd == 'attach':
        name = getattr(args, 'name', None)
        if not name:
            name = tmux.detect_current_tmux_session()
        if not name:
            print('Error: no session to attach to'); return 1
        os.execvp('tmux', ['tmux', 'attach-session', '-t', name])  # replaces process

    return 0
