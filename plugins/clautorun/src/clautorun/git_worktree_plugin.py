# File: plugins/clautorun/src/clautorun/git_worktree_plugin.py
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
"""Git worktree lifecycle orchestration plugin for clautorun.

Registers the /cr:wt slash command and coordinates the full lifecycle of
parallel Claude agent sessions in isolated git worktrees:
  - git worktree creation (via git_worktree_utils)
  - tmux window creation (via tmux_utils)
  - Claude/Gemini session launch and task delegation (via tmux_utils)
  - git worktree removal with clean session exit

Follows task_lifecycle.py:1519 register_hooks(app_instance) pattern exactly.
See plugins.py:50-51 for registration (2 lines added after task_lifecycle).

Subcommand dispatch: handle_worktree() -> _WTP_DISPATCH dict -> _wt_cmd_*() helpers.
Each _wt_cmd_* handles one subcommand: new, start, ls, rm, merge, cd, init, config.
_WTP_DISPATCH is defined at module level after all _wt_cmd_* functions (Python convention).
"""
import os
import secrets
import shlex
import shutil
import subprocess
import time

from .git_worktree_utils import (
    wt_create_git,
    wt_find_branch_for_cwd,
    wt_get_ai_cli,
    wt_get_ai_resume_cmd,
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
)
from .session_manager import session_state
from .task_lifecycle import TaskLifecycle  # for TaskCreate on -p delegation; imported at module level for testability
from .tmux_utils import (
    get_tmux_utilities,
    send_text_and_enter,
    tmux_dangerous_batch_execute,
    tmux_list_windows,
)

# Module-level guard — DRY for the repeated "not in git repo" check.
# All _wt_cmd_* handlers return this string when repo_root is None.
_NOT_IN_GIT = '❌ Not in a git repository. Navigate to a git repo first.'


def _auto_detect_branch(ctx, rest: list, cmd_name: str = 'subcommand') ->         'tuple[str | None, str | None, str | None]':
    """DRY helper: extract branch from args, or auto-detect from ctx.cwd.

    Used by _wt_cmd_start, _wt_cmd_rm, _wt_cmd_merge — all three need the same
    "branch from arg or auto-detect from cwd" logic. Returns a 3-tuple:
      (branch, auto_msg, error_msg)
    Caller checks: if error_msg: return error_msg

    auto_msg is non-None when branch was auto-detected; include in response for
    transparency (P17 — all auto-detected values reported).
    error_msg is non-None when branch could not be determined.

    EventContext.__getattr__ (core.py:690) handles None returns for unset attrs,
    so ctx.cwd is safe to access directly — no getattr() needed.
    """
    branch = next((r for r in rest if not r.startswith('-')), None)
    auto_msg = None
    if not branch:
        cwd = ctx.cwd  # EventContext handles None via __getattr__
        branch = wt_find_branch_for_cwd(cwd) if cwd else None
        if branch:
            auto_msg = f'Auto-detected git worktree: {branch} (from {cwd})'
        else:
            cwd_str = f' ({cwd})' if cwd else ''
            return None, None, (
                f'❌ No git worktree detected in current directory{cwd_str}.\n'
                f'Specify branch: /cr:wt {cmd_name} <branch>'
            )
    return branch, auto_msg, None


def register_hooks(app_instance) -> None:
    """Register /cr:wt command and SessionStart recovery for git worktree lifecycle.
    Called from plugins.py after task_lifecycle.register_hooks(app).
    Follows task_lifecycle.py:1519 register_hooks(app_instance) pattern exactly.
    """

    @app_instance.on('SessionStart')
    def inject_worktree_task_context(ctx) -> 'dict | None':
        """AI failure mode recovery: if this session is a worktree worker, surface task.

        On every SessionStart, checks if ctx.cwd is inside a tracked git worktree.
        If it is, reads the orchestrator's TaskLifecycle to find the delegated task
        and injects it as context — so even after context compaction or session restart,
        the worker AI immediately knows its branch, worktree path, and assigned task.

        FIX Bug 2: returns ctx.block(inject_text), NOT {'inject': inject_text}.
        Verified against task_lifecycle.py:735 which uses ctx.block() pattern.
        """
        if not ctx.cwd:
            return None
        branch = wt_find_branch_for_cwd(ctx.cwd)
        if not branch:
            return None
        tracked = wt_get_tracked_sessions()
        info = tracked.get(branch, {})
        orch_session_id = info.get('orchestrator_session_id')
        task_id = info.get('task_id')
        if not orch_session_id:
            return None
        try:
            lifecycle = TaskLifecycle(session_id=orch_session_id)
            my_tasks = [t for t in lifecycle.tasks.values()
                        if t.get('metadata', {}).get('git_branch') == branch
                        and t.get('status') not in ('completed', 'deleted')]
        except Exception:
            return None
        if not my_tasks:
            return None
        task_lines = []
        for t in my_tasks:
            task_lines.append(f'  [{t["status"]}] {t["subject"]}')
            if t.get('description'):
                task_lines.append(f'    {t["description"][:120]}')
        inject_text = (
            f'📋 Git worktree context restored after session start:\n'
            f'  Branch:     {branch}\n'
            f'  Worktree:   {info.get("worktree_dir", ctx.cwd)}\n'
            f'  Delegated tasks from orchestrator session:\n'
            + '\n'.join(task_lines)
        )
        # FIX Bug 2: use ctx.block() not {'inject': ...}
        return ctx.block(inject_text)

    @app_instance.command('/cr:wt', '/cr:worktree')
    def handle_worktree(ctx) -> str:
        """Git worktree command dispatcher. Parses /cr:wt <subcmd> [args].
        Uses shlex.split() to correctly handle multi-word -p arguments (P3).
        Auto-detects current git worktree from ctx.cwd for rm/merge/start.
        Dispatch via _WTP_DISPATCH dict (defined at module end after all handlers).

        ctx.activation_prompt or ctx.prompt: EventContext.__getattr__ (core.py:690)
        returns None for unset attributes — no getattr() wrapper needed.
        """
        # plugins.py:372 pattern: activation_prompt first, prompt fallback
        prompt = ctx.activation_prompt or ctx.prompt or ''
        try:
            parts = shlex.split(prompt)
        except ValueError:
            parts = prompt.split()  # fallback for unbalanced quotes
        args = parts[1:]  # strip "/cr:wt" command name
        subcmd = args[0].lower() if args else ''
        rest = args[1:]

        try:
            repo_root = wt_repo_root(cwd=ctx.cwd)
        except Exception:
            repo_root = None

        # Dict dispatch — cleaner than if/elif, same pattern as plugins.py:58-83
        handler = _WTP_DISPATCH.get(subcmd)
        if handler:
            return handler(ctx, rest, repo_root)
        elif subcmd in ('', '--help', '-h'):
            return _wt_help()
        else:
            return f'❌ Unknown subcommand: {subcmd!r}\n\n{_wt_help()}'


def _wt_help() -> str:
    """Return help text for /cr:wt command."""
    return (
        'Git Worktree Commands (/cr:wt):\n'
        '  /cr:wt new <branch> [-p "prompt"]  Create git worktree + tmux window + AI session\n'
        '  /cr:wt start <branch>              Reopen tmux window (AI resume mode)\n'
        '  /cr:wt ls                          List all git worktrees with status\n'
        '  /cr:wt rm [branch] [--force]       Remove git worktree + branch\n'
        '  /cr:wt rm --all                    Remove all git worktrees (two-step confirmation)\n'
        '  /cr:wt merge [branch] [--squash]   Merge git worktree branch into primary\n'
        '  /cr:wt cd [branch]                 Print cd command (no arg = repo root)\n'
        '  /cr:wt init [--replace]            Generate .cmux/setup hook for this repo\n'
        '  /cr:wt config                      Show current git worktree layout\n'
        '  /cr:wt config set layout <preset>  Set layout: nested | outer-nested | sibling\n'
        '\n'
        'Note: -p sends task to new AI session (AI->AI delegation).\n'
        'Note: rm/merge/start auto-detect branch from current directory.\n'
        'Note: cd prints the path; run the cd command yourself (plugin cannot change shell cwd).'
    )


def _wt_ensure_tmux_window(branch: str, worktree_dir: str,
                            tmux_session: 'str | None' = None) -> dict:
    """Git worktree helper: create a new tmux window for a git worktree.
    DRY helper shared by _wt_cmd_new and _wt_cmd_start.
    Fails fast with actionable message if tmux not installed (P14).
    FIX Issue 6: uses new-window -P -F to atomically capture new window index.
    Returns: {tmux_session: str, tmux_window_index: int, error: str|None, message: str|None}
    error='tmux_not_found' if tmux binary not found (caller skips polling + prompt).
    """
    if not shutil.which('tmux'):
        return {
            'tmux_session': None,
            'tmux_window_index': None,
            'error': 'tmux_not_found',
            'message': (
                f'tmux not installed — git worktree created but no AI session launched.\n'
                f'Install tmux, then: cd {worktree_dir} && claude'
            ),
        }

    try:
        tmux = get_tmux_utilities()
        session = tmux_session or tmux.detect_current_tmux_session() or 'clautorun'
        tmux.ensure_session_exists(session)

        # FIX Issue 6: use new-window -P -F '#{window_index}' to atomically create
        # window AND capture index. Replaces execute_win_op + windows[-1] (fragile
        # if concurrent window creation). tmux prints new window index on stdout.
        idx_result = tmux.execute_tmux_command(
            ['new-window', '-P', '-F', '#{window_index}', '-n', branch, '-c', worktree_dir],
            session=session,
        )
        if not idx_result or idx_result.get('returncode') != 0:
            return {'tmux_session': session, 'tmux_window_index': None,
                    'error': f'Failed to create tmux window for branch {branch!r}',
                    'message': None}
        idx_str = idx_result.get('stdout', '').strip()
        window_idx = int(idx_str) if idx_str.isdigit() else None
        return {'tmux_session': session, 'tmux_window_index': window_idx,
                'error': None, 'message': None}
    except Exception as e:
        return {'tmux_session': None, 'tmux_window_index': None,
                'error': str(e), 'message': None}


def _wt_cmd_new(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: create a new git worktree + tmux window + AI session.
    Sequence (order matters for P12 — all checks before irreversible actions):
      1. Parse <branch> and optional -p <prompt>
      2. wt_create_git() — git only, idempotent
      3. wt_run_hook('setup', ...) — Popen for visible output
      4. _wt_ensure_tmux_window() — create tmux window in worktree_dir
      5. send_text_and_enter(tmux, ai_cli) — launch AI session
      6. Poll is_ai_session() up to 10s; send task prompt if -p specified
      7. wt_track_session() — record git worktree->tmux window mapping
    Source: cmux.sh:233-310
    """
    if not repo_root:
        return _NOT_IN_GIT

    # Parse: /cr:wt new <branch> [-p "task prompt"] [--clean]
    branch = None
    task_prompt = None
    clean = False
    i = 0
    while i < len(rest):
        if rest[i] == '-p' and i + 1 < len(rest):
            task_prompt = rest[i + 1]
            i += 2
        elif rest[i] == '--clean':
            clean = True
            i += 1
        elif not rest[i].startswith('-'):
            branch = rest[i]
            i += 1
        else:
            i += 1

    if not branch:
        return '❌ Usage: /cr:wt new <branch> [-p "task prompt"] [--clean]'

    # --clean: remove stale directory before git worktree add
    if clean:
        target_dir = wt_worktree_dir(repo_root, branch)
        registered = any(w.get('branch') == branch for w in wt_list(repo_root))
        if not registered and os.path.isdir(target_dir):
            shutil.rmtree(target_dir, ignore_errors=True)

    # Step 2: Create git worktree (git only, idempotent)
    result = wt_create_git(branch, repo_root)
    if result['error']:
        return f'❌ Failed to create git worktree:\n  {result["error"]}'
    worktree_dir = result['worktree_dir']
    created = result['created']

    lines = [
        f'{"✅ Git worktree created" if created else "ℹ️  Git worktree already exists"}',
        f'  - Branch:   {branch} {"(new)" if created else "(existing)"}',
        f'  - Path:     {worktree_dir}',
    ]

    # Step 3: Run setup hook (Popen for visible progress, cmux.sh:283-301)
    t0 = time.monotonic()
    hook = wt_run_hook('setup', worktree_dir, repo_root)
    elapsed = time.monotonic() - t0
    if hook['ran']:
        status = 'timed out' if (hook['error'] and 'timed out' in hook['error'])                  else f'exit {hook["returncode"]}'
        lines.append(f'  - Hook:     .cmux/setup ran ({status}, {elapsed:.1f}s)')
        if hook['stdout']:
            for ln in hook['stdout'][-5:]:
                lines.append(f'              {ln}')
    else:
        lines += [
            '  - Hook:     no .cmux/setup found',
            '    💡 Run /cr:wt init to generate a setup hook for this repo.',
        ]

    # Step 4: Ensure tmux window
    win = _wt_ensure_tmux_window(branch, worktree_dir)
    if win['error'] == 'tmux_not_found':
        lines += [f'  - tmux:     not installed',
                  f'  - AI:   not launched — {win["message"]}']
        return '\n'.join(lines)
    if win['error']:
        lines.append(f'  - tmux:     ❌ {win["error"]}')
        return '\n'.join(lines)

    session = win['tmux_session']
    window_idx = win['tmux_window_index']
    lines.append(f'  - tmux:     {session}:{window_idx} (window "{branch}")')

    # Step 5: Launch AI session — Claude or Gemini (not hardcoded)
    ai_cli = wt_get_ai_cli(repo_root)
    tmux = get_tmux_utilities()
    send_text_and_enter(tmux, ai_cli, session=session, window=str(window_idx))
    lines.append(f'  - AI CLI:   {ai_cli}')

    # Step 6: AI->AI delegation — poll until AI session detected, then send prompt
    # is_ai_session() detects both Claude and Gemini processes
    task_id = None
    if task_prompt:
        detected = False
        for _ in range(20):  # 20 x 0.5s = 10s max
            time.sleep(0.5)
            if tmux.is_ai_session(session, str(window_idx)):
                detected = True
                break
        send_text_and_enter(tmux, task_prompt, session=session, window=str(window_idx))
        verdict = 'launched, task delegated ✓' if detected else                   'launched (AI detection timed out, prompt sent anyway)'
        lines.append(f'  - AI:       {verdict}')

        # FIX Bug 1: create_task() signature is (task_id: str, input_data: Dict, result: str) -> None
        # Must generate UUID externally; return value is None (not the task_id).
        # Verified from task_lifecycle.py:345.
        try:
            import uuid as _uuid
            lifecycle = TaskLifecycle(ctx=ctx)
            task_id = str(_uuid.uuid4())
            lifecycle.create_task(
                task_id=task_id,
                input_data={
                    'subject': f'[{branch}] {task_prompt[:60]}{"..." if len(task_prompt) > 60 else ""}',
                    'description': task_prompt,
                    'metadata': {
                        'git_branch': branch,
                        'git_worktree': worktree_dir,
                        'orchestrator_session_id': ctx.session_id,
                        'tmux_target': f'{session}:{window_idx}',
                    },
                },
                result='',
            )
        except Exception:
            task_id = None  # TaskLifecycle failure is non-fatal
    else:
        lines.append(f'  - AI:       launched')

    # Step 7: Record git worktree -> tmux window mapping (includes orchestrator link)
    wt_track_session(branch, worktree_dir, session, window_idx,
                     orchestrator_session_id=ctx.session_id,
                     task_id=task_id)
    return '\n'.join(lines)


def _wt_cmd_start(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: reopen tmux window for an existing git worktree.
    Sends 'claude -c' (or 'gemini') to resume the most recent AI conversation.
    Auto-detects branch from ctx.cwd via wt_find_branch_for_cwd() if not specified.
    Source: cmux.sh:312-356 — uses 'claude -c' (continue flag confirmed line 355)
    """
    if not repo_root:
        return _NOT_IN_GIT

    branch, auto_msg, err = _auto_detect_branch(ctx, rest, 'start')
    if err:
        return err

    worktree_dir = wt_worktree_dir(repo_root, branch)
    if not os.path.isdir(worktree_dir):
        return (f'❌ Git worktree directory not found: {worktree_dir}\n'
                f'Run /cr:wt ls to see available worktrees, '
                f'or /cr:wt new {branch} to create one.')

    win = _wt_ensure_tmux_window(branch, worktree_dir)
    if win['error'] == 'tmux_not_found':
        return f'❌ {win["message"]}'
    if win['error']:
        return f'❌ tmux error: {win["error"]}'

    session = win['tmux_session']
    window_idx = win['tmux_window_index']
    tmux = get_tmux_utilities()
    resume_cmd = wt_get_ai_resume_cmd(repo_root)
    send_text_and_enter(tmux, resume_cmd, session=session, window=str(window_idx))
    wt_track_session(branch, worktree_dir, session, window_idx)

    lines = ['✅ Git worktree session started']
    if auto_msg:
        lines.append(f'  - {auto_msg}')
    lines += [
        f'  - Branch:   {branch}',
        f'  - Path:     {worktree_dir}',
        f'  - tmux:     {session}:{window_idx} (window "{branch}")',
        f'  - AI:       resumed ({resume_cmd})',
    ]
    return '\n'.join(lines)


def _wt_cmd_ls(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: list all git worktrees with tmux and git status.
    Cross-references git worktree list with tracked tmux window assignments.
    Output is a markdown table optimized for AI parsing.
    FIX Bug 4 / regression vs cmux: excludes main checkout from listing.
    FIX Issue 9: prunes stale tracking entries for externally removed worktrees.
    Source: cmux.sh:387-406
    """
    if not repo_root:
        return _NOT_IN_GIT

    # FIX Bug 4 / cmux regression: exclude main checkout from listing.
    # cmux.sh:387-406 only lists worktrees under the base dir, not the main checkout.
    worktrees = [
        w for w in wt_list(repo_root)
        if os.path.realpath(w['path']) != os.path.realpath(repo_root)
    ]
    if not worktrees:
        return 'No git worktrees found. Create one with /cr:wt new <branch>'

    tracked = wt_get_tracked_sessions()

    # FIX Issue 9: prune stale tracking entries atomically
    registered_branches = {w['branch'] for w in worktrees if w.get('branch')}
    stale = [b for b in tracked if b not in registered_branches]
    if stale:
        with session_state('__global__') as st:
            wts = dict(st.get('git_worktrees') or {})
            for b in stale:
                wts.pop(b, None)
            st['git_worktrees'] = wts
        for b in stale:
            tracked.pop(b, None)

    lines = [
        '| Branch | Path | HEAD | tmux | Dirty |',
        '|--------|------|------|------|-------|',
    ]
    for wt in worktrees:
        branch = wt.get('branch') or '(detached)'
        path = wt.get('path', '')
        head = (wt.get('head') or '')[:7]
        dirty = '✗' if wt_is_dirty(path) else '✓'
        tmux_info = '—'
        if branch in tracked:
            t = tracked[branch]
            tmux_info = f'{t["tmux_session"]}:{t["tmux_window"]}'
        lines.append(f'| {branch} | {path} | {head} | {tmux_info} | {dirty} |')

    return '\n'.join(lines)


def _wt_cmd_rm(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: remove a git worktree and its branch.
    CRITICAL ORDERING (P12 — irreversible actions last):
      1. locked check — fail fast with clear message
      2. dirty check — abort if dirty + no --force (no side effects)
      3. exit AI: tmux_dangerous_batch_execute('exit')
      4. retry with 'kill' if 'exit' failed
      5. abort if AI could not be exited
      6. teardown hook: wt_run_hook('teardown', ...)
      7. wt_remove_git() — git worktree remove + git branch -d
      8. wt_untrack_session()
    Source: cmux.sh:486-563
    """
    if not repo_root:
        return _NOT_IN_GIT

    force = '--force' in rest or '-f' in rest
    all_flag = '--all' in rest
    token_val = None
    branch = None
    i = 0
    while i < len(rest):
        if rest[i] in ('--force', '-f', '--all'):
            i += 1
        elif rest[i] == '--token' and i + 1 < len(rest):
            token_val = rest[i + 1]
            i += 2
        elif not rest[i].startswith('-'):
            branch = rest[i]
            i += 1
        else:
            i += 1

    if all_flag:
        return _wt_cmd_rm_all(ctx, repo_root, force, token_val)

    if not branch:
        branch, auto_msg, err = _auto_detect_branch(ctx, rest, 'rm')
        if err:
            return err
    else:
        auto_msg = None

    worktree_dir = wt_worktree_dir(repo_root, branch)

    # Step 1: Locked check — no side effects, fail fast (Issue 8)
    for wt in wt_list(repo_root):
        if wt.get('branch') == branch and wt.get('locked'):
            return (
                f'❌ Git worktree {branch!r} is locked (git worktree lock was set).\n'
                f'Unlock first: git worktree unlock {worktree_dir}\n'
                f'Then retry: /cr:wt rm {branch}'
            )

    # Step 2: Dirty check FIRST — no side effects, abort before anything irreversible
    if wt_is_dirty(worktree_dir) and not force:
        return (
            f'❌ Git worktree {branch!r} has uncommitted changes.\n'
            f'Options:\n'
            f'  - Commit or stash changes, then retry\n'
            f'  - Force delete (loses changes): /cr:wt rm {branch} --force'
        )

    # Step 3+4: Exit AI cleanly (if tracked)
    tracked = wt_get_tracked_sessions()
    tmux_info = tracked.get(branch)
    claude_exit_msg = '—'
    if tmux_info:
        target = {'session': tmux_info['tmux_session'], 'w': tmux_info['tmux_window']}
        tmux = get_tmux_utilities()
        result = tmux_dangerous_batch_execute(tmux, 'exit', [target])
        if result.get('failure_count', 0) > 0:
            # Retry with kill (Ctrl+C twice)
            result2 = tmux_dangerous_batch_execute(tmux, 'kill', [target])
            if result2.get('failure_count', 0) > 0:
                return (
                    f'❌ Could not stop AI session in '
                    f'{tmux_info["tmux_session"]}:{tmux_info["tmux_window"]}.\n'
                    f'Switch to that window and exit the AI manually, then retry:\n'
                    f'  /cr:wt rm {branch}'
                )
            claude_exit_msg = 'killed (Ctrl+C)'
        else:
            claude_exit_msg = 'exited cleanly (/exit)'

    # Step 5: Teardown hook (cmux.sh:541-548)
    hook = wt_run_hook('teardown', worktree_dir, repo_root)
    hook_msg = (f'.cmux/teardown ran (exit {hook["returncode"]})' if hook['ran']
                else 'no teardown hook')

    # Step 6: Remove git worktree + branch (wt_remove_git handles locked + path lookup)
    rm = wt_remove_git(branch, repo_root, force=force)
    if not rm['removed']:
        if 'not merged' in (rm['error'] or '').lower():
            return (
                f'❌ {rm["error"]}\n'
                f'Options:\n'
                f'  - Merge first: /cr:wt merge {branch}\n'
                f'  - Force delete: /cr:wt rm {branch} --force'
            )
        return f'❌ Failed to remove git worktree: {rm["error"]}'

    # Step 7: Untrack
    wt_untrack_session(branch)

    lines = [f'✅ Git worktree {branch!r} removed']
    if auto_msg:
        lines.append(f'  - {auto_msg}')
    lines += [
        f'  - Branch:   {branch} deleted',
        f'  - Path:     {worktree_dir} deleted',
        f'  - AI:       {claude_exit_msg}',
        f'  - Hook:     {hook_msg}',
    ]
    return '\n'.join(lines)


def _wt_cmd_rm_all(ctx, repo_root: str, force: bool,
                   token_val: 'str | None') -> str:
    """Handle /cr:wt rm --all with two-step token confirmation (P3 race-safe).
    Step 1: no --token -> generate random token, store with 5-min TTL, return prompt.
    Step 2: --token <val> -> validate + delete token atomically in single with block.
    FIX Bug 4: excludes main checkout from the list of worktrees to remove.
    """
    # FIX Bug 4: exclude main checkout.
    # cmux.sh guards against this explicitly; we do the same via realpath comparison.
    worktrees = [
        w for w in wt_list(repo_root)
        if w.get('branch') and os.path.realpath(w['path']) != os.path.realpath(repo_root)
    ]
    if not worktrees:
        return 'No git worktrees to remove.'

    if token_val is None:
        # Step 1: Generate token and return confirmation prompt
        token = secrets.token_hex(4).upper()
        names = ', '.join(w['branch'] for w in worktrees)
        with session_state('__global__') as st:
            st['wt_rm_all_token'] = {
                'token': token,
                'expires': time.time() + 300,  # 5-minute TTL
                'count': len(worktrees),
            }
        return (
            f'⚠️  About to delete {len(worktrees)} git worktrees: {names}\n\n'
            f'Confirm: `/cr:wt rm --all --token {token}`\n'
            f'(Token expires in 5 minutes)'
        )

    # Step 2: Validate + consume token atomically in a single with block
    with session_state('__global__') as st:
        stored = st.get('wt_rm_all_token') or {}
        if (not stored
                or stored.get('token') != token_val
                or time.time() > stored.get('expires', 0)):
            return '❌ Token invalid or expired. Run `/cr:wt rm --all` to get a new token.'
        del st['wt_rm_all_token']  # single-use: consumed atomically in this with block

    results = []
    for wt in worktrees:
        sub_rest = [wt['branch']] + (['--force'] if force else [])
        results.append(_wt_cmd_rm(ctx, sub_rest, repo_root))
    return '\n\n'.join(results)


def _wt_cmd_merge(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: merge a git worktree branch into the primary branch.
    Source: cmux.sh:408-484
    """
    if not repo_root:
        return _NOT_IN_GIT

    squash = '--squash' in rest
    branch, auto_msg, err = _auto_detect_branch(ctx, rest, 'merge')
    if err:
        return err

    result = wt_merge(branch, repo_root, squash=squash)
    if not result['merged']:
        return f'❌ Merge failed: {result["error"]}'

    lines = [f'✅ Git worktree {branch!r} merged into {result["target"]!r}']
    if auto_msg:
        lines.append(f'  - {auto_msg}')
    if squash:
        lines.append('  - Squash merge staged. Review and commit: git commit')
    return '\n'.join(lines)


def _wt_cmd_cd(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: print cd command for a git worktree directory.
    No args: print repo root path (matches cmux cd behavior: cmux.sh:358-385).
    With <branch>: print worktree path for that branch.
    Note: plugin cannot change shell cwd — user must run the cd command themselves.
    """
    if not repo_root:
        return _NOT_IN_GIT
    branch = next((r for r in rest if not r.startswith('-')), None)
    if not branch:
        # FIX regression vs cmux: 'cmux cd' with no args -> repo root (cmux.sh:358-372)
        return f'cd {repo_root}'
    path = wt_worktree_dir(repo_root, branch)
    # Validate existence, matching cmux.sh:378-382
    if not os.path.isdir(path):
        return (f'❌ Worktree not found: {path}\n'
                f'Run /cr:wt ls to see available worktrees.')
    return f'cd {path}'


def _wt_cmd_init(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: generate a .cmux/setup hook for this repository.
    Gathers project context (recent commits, package files) and returns an inline
    prompt for Claude to generate the setup hook script.
    --replace: regenerate even if .cmux/setup already exists (matches cmux init --replace)
    Source: cmux.sh _cmux_init (interactive version; ours is AI-assisted)
    """
    if not repo_root:
        return _NOT_IN_GIT

    replace = '--replace' in rest
    hook_path = os.path.join(repo_root, '.cmux', 'setup')
    if os.path.exists(hook_path) and not replace:
        return (
            f'❌ {hook_path} already exists.\n'
            f'Use /cr:wt init --replace to regenerate it.'
        )

    context_parts = []
    try:
        r = subprocess.run(
            ['git', '-C', repo_root, 'log', '--oneline', '-5'],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            context_parts.append(f'Recent commits:\n{r.stdout.strip()}')
    except Exception:
        pass

    pkg_files = [f for f in ['package.json', 'Pipfile', 'pyproject.toml',
                              'Gemfile', 'go.mod', 'Cargo.toml', 'requirements.txt']
                 if os.path.exists(os.path.join(repo_root, f))]
    if pkg_files:
        context_parts.append(f'Package files found: {", ".join(pkg_files)}')

    context = '\n'.join(context_parts) or '(no context gathered)'
    return (
        f'Generating .cmux/setup hook for {repo_root}...\n\n'
        f'Project context:\n{context}\n\n'
        f'Please create an executable script at {hook_path} that:\n'
        f'1. Installs project dependencies (npm install, pip install -e ., uv sync, etc.)\n'
        f'2. Sets up environment variables or .env files needed for development\n'
        f'3. Creates any required directories or config files\n'
        f'4. Is idempotent (safe to run multiple times)\n'
        f'Make it a bash script with #!/bin/bash and chmod +x it.'
    )


def _wt_cmd_config(ctx, rest: list, repo_root: 'str | None') -> str:
    """Git worktree subcommand: show or set git worktree directory layout."""
    if not repo_root:
        return _NOT_IN_GIT
    if not rest:
        layout = wt_get_layout(repo_root)
        return (
            f'Current git worktree layout: {layout}\n'
            f'Valid layouts: nested (default), outer-nested, sibling\n'
            f'Change: /cr:wt config set layout <preset>'
        )
    if rest[0] == 'set' and len(rest) >= 3 and rest[1] == 'layout':
        try:
            wt_set_layout(rest[2], repo_root)
            return f'✅ Git worktree layout set to: {rest[2]}'
        except ValueError as e:
            return f'❌ {e}'
    return '❌ Usage: /cr:wt config set layout <nested|outer-nested|sibling>'


# ---------------------------------------------------------------------------
# Dispatch table — defined at module end after all _wt_cmd_* functions.
# Pattern: plugins.py:58-83 _make_policy_handler / _POLICY_ALIASES.
# Dict is cleaner than if/elif: adding a new subcommand = one line here.
# ---------------------------------------------------------------------------
_WTP_DISPATCH: dict = {
    'new': _wt_cmd_new,
    'start': _wt_cmd_start,
    'ls': _wt_cmd_ls,
    'rm': _wt_cmd_rm,
    'remove': _wt_cmd_rm,    # alias
    'merge': _wt_cmd_merge,
    'cd': _wt_cmd_cd,
    'init': _wt_cmd_init,
    'config': _wt_cmd_config,
}
