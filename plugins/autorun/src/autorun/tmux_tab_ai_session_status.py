"""`autorun tmux list` and `autorun tmux send`: AI sessions in tmux panes.

Analyzes captured pane content to report what each AI session is doing and
whether it is waiting for input, formats the table `list` prints, and runs
`send`, which types text into the panes the user names.

Architecture (three layers):
  tmux_utils.py                  — low-level: list windows, send keys, discover sessions
  tmux_tab_ai_session_status.py  — this file: status analysis, formatting, list and send
  __main__.py `tmux` subparser   — `autorun tmux list|send`; commands/tmux-exec runs
                                   the same thing where `autorun` is not on PATH

The Anthropic SDK is an OPTIONAL dependency (graceful fallback to heuristics when
unavailable or when ANTHROPIC_API_KEY is not set).
"""
import json
import os
import re
import shutil
import sys
from typing import Dict, List

from .tmux_utils import (
    PROMPT_TYPE_INPUT,
    PROMPT_TYPE_UNKNOWN,
    discover_claude_sessions,
    execute_session_selections,
    get_tmux_utilities,
    tmux_detect_claude_active,
    tmux_detect_prompt_type,
)

# Optional Anthropic SDK — falls back to heuristic analysis when unavailable
try:
    from anthropic import Anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False


# ---------------------------------------------------------------------------
# Content-based status analysis
# ---------------------------------------------------------------------------

def extract_directory(content: str) -> str:
    """Extract working directory from captured terminal content.

    Tries several regex patterns in order: pwd output, explicit labels,
    shell prompt prefixes. Returns 'unknown' if no match found.
    """
    patterns = [
        r'(?:cd|pwd|cwd)[:\s]+([~/][^\s\n]+)',
        r'(?:directory|dir)[:\s]+([~/][^\s\n]+)',
        r'Working directory[:\s]+([~/][^\s\n]+)',
        r'^([~/][^\s]+)\s*[\$>]',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
        if match:
            path = match.group(1)
            if path.startswith(('~', '/')):
                return path
    return 'unknown'


def extract_purpose(content: str) -> str:
    """Extract a brief purpose description from captured terminal content.

    Scans the first 15 lines and returns the first meaningful line (> 10 chars)
    that does not look like a shell prompt or separator. Truncation to column
    width is handled by format_output(), not here.
    """
    lines = [line.strip() for line in content.split('\n') if line.strip()]
    skip_prefixes = ['$', '>', '#', 'last login', '───', '═══']
    for line in lines[:15]:
        line_lower = line.lower()
        if not any(line_lower.startswith(p.lower()) for p in skip_prefixes):
            if len(line) > 10:
                return line
    return 'unknown'


def get_default_actions(status: str) -> List[str]:
    """Return 2-3 recommended actions for a session given its detected status."""
    return {
        'active':    ['let it work', 'status'],
        'idle':      ['continue', 'new task'],
        'error':     ['interrupt', 'retry'],
        'completed': ['new task', 'close'],
        'blocked':   ['unblock', 'skip'],
        'dialog':    ['answer it in the pane', 'status'],
        'unknown':   ['let it work', 'status'],
    }.get(status, ['continue', 'status'])


def analyze_sessions_heuristic(sessions: List[Dict]) -> List[Dict]:
    """Analyze session state from captured terminal content using keyword heuristics.

    Adds 'directory', 'status', 'awaiting', 'purpose', 'actions' keys to each
    session dict in-place. Uses pre-populated tmux_list_windows() fields (path,
    title) when available, falling back to regex extraction from content.

    'awaiting' is tmux_utils' own answer: the plain input prompt
    (prompt_type 'input') while tmux_detect_claude_active sees no generation.
    A permission dialog, plan approval or question menu is not awaiting
    input: Enter there answers the dialog, so it needs the person. The
    prompt_type and is_active from discover_claude_sessions are used when
    present, otherwise detected from content.

    Status detection (in priority order):
      'unknown'   — the harness's prompts cannot be read (prompt_type 'unknown')
      'active'    — Claude is generating
      'dialog'    — any other prompt it recognises: answer it in the pane
      'error'     — 'error', 'failed', or 'exception' in content
      'idle'      — awaiting input
      'completed' — 'completed' or 'done' in content
      'active'    — default
    """
    for session in sessions:
        content = session.get('content', '')
        content_lower = content.lower()

        session['directory'] = session.get('path') or extract_directory(content)

        is_active = (session['is_active'] if 'is_active' in session
                     else tmux_detect_claude_active(content))
        prompt_type = (session['prompt_type'] if 'prompt_type' in session
                       else tmux_detect_prompt_type(content))
        session['awaiting'] = prompt_type == PROMPT_TYPE_INPUT and not is_active

        if prompt_type == PROMPT_TYPE_UNKNOWN:
            # autorun cannot read this harness's prompts: neither idle nor busy.
            session['status'] = 'unknown'
        elif is_active:
            session['status'] = 'active'
        elif prompt_type not in (None, PROMPT_TYPE_INPUT):
            session['status'] = 'dialog'
        elif 'error' in content_lower or 'failed' in content_lower or 'exception' in content_lower:
            session['status'] = 'error'
        elif session['awaiting']:
            session['status'] = 'idle'
        elif 'completed' in content_lower or 'done' in content_lower:
            session['status'] = 'completed'
        else:
            session['status'] = 'active'

        session['purpose'] = session.get('title') or extract_purpose(content)
        session['actions'] = get_default_actions(session['status'])

    return sessions


def analyze_sessions_with_claude(sessions: List[Dict]) -> List[Dict]:
    """Analyze session state using the Anthropic SDK for intelligent inference.

    Requires: anthropic package installed AND ANTHROPIC_API_KEY env var set.
    Falls back to analyze_sessions_heuristic() on any SDK error.

    Adds 'directory', 'purpose', 'status', 'awaiting', 'actions' keys.
    """
    client = Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
    sessions_data = [
        {'id': i, 'target': s['tmux_target'], 'content': s['content_preview']}
        for i, s in enumerate(sessions)
    ]
    prompt = (
        f'Analyze these {len(sessions)} tmux Claude sessions. For each, determine:\n\n'
        '1. directory: Working directory (extract from paths in content, or "unknown")\n'
        '2. purpose: Brief description of what\'s being worked on (10 words max)\n'
        '3. status: One of: active, idle, error, completed, blocked\n'
        '4. awaiting: Boolean - is the session waiting for user input?\n'
        '5. actions: List of 2-3 recommended actions (e.g., "continue", "review error", "git status")\n\n'
        f'Sessions (first 500 chars each):\n{json.dumps(sessions_data, indent=2)}\n\n'
        'Respond with ONLY a JSON array matching session order:\n'
        '[{"directory": "/path", "purpose": "description", "status": "active", '
        '"awaiting": false, "actions": ["continue", "status"]}, ...]'
    )
    response = client.messages.create(
        model='claude-sonnet-4-6',
        max_tokens=2000,
        messages=[{'role': 'user', 'content': prompt}],
    )
    text = response.content[0].text
    start = text.find('[')
    end = text.rfind(']') + 1
    if start >= 0 and end > start:
        try:
            analyses = json.loads(text[start:end])
            for session, analysis in zip(sessions, analyses):
                session.update(analysis)
            return sessions
        except (json.JSONDecodeError, Exception):
            pass
    return analyze_sessions_heuristic(sessions)


def analyze_sessions(sessions: List[Dict]) -> List[Dict]:
    """Analyze session state — uses Anthropic SDK if available, heuristics otherwise.

    Attempts SDK analysis first when anthropic is installed and ANTHROPIC_API_KEY
    is set; falls back to heuristic analysis on ImportError or any SDK exception.
    """
    if _ANTHROPIC_AVAILABLE and os.getenv('ANTHROPIC_API_KEY'):
        try:
            return analyze_sessions_with_claude(sessions)
        except Exception as e:
            print(f'SDK analysis failed, using heuristics: {e}')
    return analyze_sessions_heuristic(sessions)


# ---------------------------------------------------------------------------
# Presentation layer
# ---------------------------------------------------------------------------

def format_output(sessions: List[Dict]) -> str:
    """Format analyzed sessions as a human-readable table with dynamic column widths.

    Column widths adapt to terminal width (default 120). Session letter IDs
    (A, B, C, ...) enable the selection syntax shown in the footer.
    Truncation uses '~' suffix to signal cut content.
    """
    term_width = shutil.get_terminal_size((120, 24)).columns
    # Fixed: ID(3) + Pane(5) + Target(14) + Harness(11) + Status(10) + Awaiting(8) + Actions(18)
    # + separators(8) = 77
    fixed_width = 77
    remaining = max(term_width - fixed_width, 45)
    dir_width = max(remaining * 2 // 5, 15)
    purpose_width = max(remaining - dir_width, 20)

    lines = [
        f"\n{'=' * term_width}",
        f' AI sessions in tmux ({len(sessions)} found)',
        f"{'=' * term_width}\n",
        f"{'ID':<3} {'Pane':<5} {'Target':<14} {'Harness':<11} {'Directory':<{dir_width}} "
        f"{'Purpose':<{purpose_width}} {'Status':<10} {'Awaiting':<8} Actions",
        f"{'-' * 3} {'-' * 5} {'-' * 14} {'-' * 11} {'-' * dir_width} {'-' * purpose_width} "
        f"{'-' * 10} {'-' * 8} {'-' * 18}",
    ]
    for i, s in enumerate(sessions):
        letter = chr(65 + i)
        awaiting = 'yes' if s.get('awaiting') else 'no'
        actions = ', '.join(s.get('actions', [])[:2])
        directory = s.get('directory', 'unknown')
        if len(directory) > dir_width:
            directory = directory[:dir_width - 1] + '~'
        purpose = s.get('purpose', 'unknown')
        if len(purpose) > purpose_width:
            purpose = purpose[:purpose_width - 1] + '~'
        status = s.get('status', 'unknown')[:9]
        harness = (s.get('harness') or 'unknown')[:11]
        lines.append(
            f"{letter:<3} {s.get('pane_id') or '?':<5} {s['tmux_target']:<14} {harness:<11} "
            f"{directory:<{dir_width}} "
            f"{purpose:<{purpose_width}} {status:<10} {awaiting:<8} {actions}"
        )

    lines += [
        f"\n{'-' * term_width}",
        'Selection syntax (types the text, then Enter). Name panes by the Pane column,',
        'which stays put when windows open or close; the letters do not:',
        "  autorun tmux send --to %3 -- <text>              into the pane with id %3",
        "  autorun tmux send --to %3,%7 -- <text>           into %3 and %7",
        "  autorun tmux send --to all -- <text>             into every session listed",
        "  autorun tmux send --to awaiting-input -- <text>  into sessions at their plain input prompt",
        "Nothing is typed into a session showing a dialog, mid-turn or with text typed;",
        "status 'dialog' (a permission, plan or question prompt) needs you in that pane.",
        f"{'-' * term_width}",
    ]
    return '\n'.join(lines)


def format_execution_results(results: List[Dict]) -> str:
    """Format a list of send_to_session() result dicts as human-readable output."""
    if not results:
        return 'No commands executed.'
    lines = ['\nExecuting...\n']
    success_count = sum(1 for r in results if r['success'])
    refused = sum(1 for r in results if r.get('refused'))
    for r in results:
        who = f"{r['letter']} {r['target']}" if r.get('letter') else r['target']
        if r['success']:
            lines.append(f"  [ok] {who}: '{r['command']}' sent")
        elif r.get('refused'):
            lines.append(f"  [refused] {who}: {r['error']}")
        else:
            lines.append(f"  [FAIL] {who}: {r['error']}")
    lines.append(f'\nDone! {success_count}/{len(results)} commands sent.')
    if refused:
        lines.append(f'{refused} refused: nothing was typed into them.')
    return '\n'.join(lines)


def execute_selections(selection_str: str, sessions: List[Dict], command: str = '') -> str:
    """Execute selections and return formatted result string.

    Convenience wrapper combining execute_session_selections() (tmux_utils) and
    format_execution_results().
    """
    results = execute_session_selections(selection_str, sessions, command=command or None)
    return format_execution_results(results)


# ---------------------------------------------------------------------------
# Command entrypoints: autorun tmux list | send
# ---------------------------------------------------------------------------

SEND_TARGETS = ("pane ids from the Pane column of `autorun tmux list` (--to %3, --to %3,%7), "
                "--to all, or --to awaiting-input (awaiting is accepted)")


def describe_target(session: Dict) -> str:
    """One line naming where a send to this session lands."""
    return (f"  {session['tmux_target']}: session {session.get('session_name', '?')}, "
            f"window {session.get('window_id', '?')}, pane {session.get('pane') or '?'} "
            f"(id {session.get('pane_id') or '?'}), "
            f"directory {session.get('path') or session.get('directory') or 'unknown'}, "
            f"harness {session.get('harness') or 'unknown'}")


def announce_targets(targets: List[Dict]) -> None:
    """Print each target before anything is typed into it."""
    print(f'Typing into {len(targets)} pane(s):')
    for session in targets:
        print(describe_target(session))


def handle_execute_mode(data: Dict = None) -> int:
    """Send the text a selection names; the one sender behind `autorun tmux send`.

    data (read from stdin as JSON when None):
    {
        "selections": "A,B" | "all" | "awaiting-input" | "A:cmd",
        "command": "text to type",
        "sessions": [{"tmux_target": "...", "actions": [...], "awaiting": bool}, ...]
    }
    Returns 0 when every target received the text and Enter, 1 otherwise.
    """
    if data is None:
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as e:
            print(json.dumps({'error': f'Invalid JSON: {e}', 'success': False}))
            return 1

    sessions = data.get('sessions', [])
    selections = data.get('selections', '')
    command = data.get('command', '')

    if not sessions:
        print(json.dumps({'error': 'No sessions provided', 'success': False}))
        return 1

    # The command replaces the default action of the letter selections; the
    # parser validates the letters and turns a bare 'all'/'awaiting' into 'all:<command>'.
    results = execute_session_selections(selections, sessions, command=command or None,
                                         announce=announce_targets)
    print(format_execution_results(results))
    return 0 if results and all(r['success'] for r in results) else 1


def list_sessions(as_json: bool = False) -> int:
    """`autorun tmux list`: print the AI sessions in tmux. Types nothing."""
    sessions = discover_claude_sessions(get_tmux_utilities())
    if not sessions:
        if as_json:
            print(json.dumps({'sessions': [], 'count': 0}, indent=2))
        else:
            print('No AI coding sessions found in tmux.')
        return 0
    analyzed = analyze_sessions(sessions)
    if as_json:
        print(json.dumps({'sessions': analyzed, 'count': len(analyzed)}, indent=2))
    else:
        print(format_output(analyzed))
    return 0


def _letters_for_pane_ids(to: str, sessions: List[Dict]) -> str:
    """The letters, in this discovery, of the pane ids `to` names ('%3,%7'),
    or '' when any entry is not a pane id this discovery found.

    Letters are not accepted here: `send` discovers the panes again, so a
    window opened or closed since `list` would shift every letter after it
    onto another pane. A pane id ('%N') names one pane for the life of the
    tmux server and is never reused, so the pane named is the pane listed.
    """
    letters = {s.get('pane_id'): chr(65 + i) for i, s in enumerate(sessions) if s.get('pane_id')}
    wanted = [part.strip() for part in to.split(',') if part.strip()]
    if not wanted or any(part not in letters for part in wanted):
        return ''
    return ','.join(letters[part] for part in wanted)


def send_text(to: str, text: str) -> int:
    """`autorun tmux send --to <targets> -- <text>`: type text, then Enter, into the targets.

    Targets are pane ids ('%3', as `autorun tmux list` prints them), all, or
    awaiting-input, resolved against the panes discovered here. A letter, a
    pane id no longer present, or anything else is refused with the current
    listing. With no --to or no text, nothing is typed and the result is 1.
    """
    if not to:
        print(f'Nothing sent: name the panes with {SEND_TARGETS}. '
              'Run `autorun tmux list` to see the letters.')
        return 1
    if not text.strip():
        print('Nothing sent: give the text to type after --, e.g. '
              f'autorun tmux send --to {to} -- continue')
        return 1
    sessions = discover_claude_sessions(get_tmux_utilities())
    if not sessions:
        print('Nothing sent: no AI coding sessions found in tmux.')
        return 1
    sessions = analyze_sessions(sessions)
    selection = to.strip()
    if selection.lower() not in ('all', 'awaiting', 'awaiting-input'):
        selection = _letters_for_pane_ids(to, sessions)
        if not selection:
            print(f"Nothing sent: '{to}' is not a pane id in the current listing. Name panes "
                  f"by pane id, the Pane column (--to %3,%7): letters shift when a window "
                  f"opens or closes, a pane id does not. Current listing:")
            print(format_output(sessions))
            return 1
    return handle_execute_mode({'selections': selection, 'command': text, 'sessions': sessions})


def run_tmux_command(args, tmux_parser) -> int:
    """Run parsed `autorun tmux` arguments (see __main__.add_tmux_arguments)."""
    if args.execute:
        return handle_execute_mode()
    if args.tmux_command == 'list':
        return list_sessions(as_json=args.json)
    if args.tmux_command == 'send':
        return send_text(args.to, ' '.join(args.text))
    tmux_parser.print_help()
    return 1


def main(argv: List[str] = None) -> int:
    """`autorun tmux <argv>` for commands/tmux-exec, which runs where `autorun`
    is not on PATH and its dependencies may not be installed."""
    import argparse

    from autorun.__main__ import add_tmux_arguments
    from autorun.logging_utils import use_utf8_stdio

    use_utf8_stdio()
    parser = argparse.ArgumentParser(prog='tmux-exec')
    add_tmux_arguments(parser)
    return run_tmux_command(parser.parse_args(argv), parser)
