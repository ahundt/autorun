"""AI session status analysis and dashboard for Claude/Gemini tmux tabs.

Provides heuristic and SDK-based analysis of captured terminal content to
determine what each Claude/Gemini session is doing, its current status, and
whether it is waiting for user input. Also provides the human-readable table
formatter and the /ar:tabs command entrypoint.

Architecture (three layers):
  tmux_utils.py                  — low-level: list windows, send keys, discover sessions
  tmux_tab_ai_session_status.py  — this file: status analysis, formatting, entrypoint
  commands/tabs-exec             — thin wrapper: `from autorun.tmux_tab_ai_session_status import main; main()`

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
    discover_claude_sessions,
    execute_session_selections,
    get_tmux_utilities,
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
    lines = [l.strip() for l in content.split('\n') if l.strip()]
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
    }.get(status, ['continue', 'status'])


def analyze_sessions_heuristic(sessions: List[Dict]) -> List[Dict]:
    """Analyze session state from captured terminal content using keyword heuristics.

    Adds 'directory', 'status', 'awaiting', 'purpose', 'actions' keys to each
    session dict in-place. Uses pre-populated tmux_list_windows() fields (path,
    title) when available, falling back to regex extraction from content.

    Status detection (in priority order):
      'error'     — 'error', 'failed', or 'exception' in content
      'idle'      — content ends with '>' or contains 'waiting'
      'completed' — 'completed' or 'done' in content
      'active'    — default
    """
    for session in sessions:
        content = session.get('content', '')
        content_lower = content.lower()

        session['directory'] = session.get('path') or extract_directory(content)

        if 'error' in content_lower or 'failed' in content_lower or 'exception' in content_lower:
            session['status'] = 'error'
        elif content.rstrip().endswith('>') or 'waiting' in content_lower:
            session['status'] = 'idle'
        elif 'completed' in content_lower or 'done' in content_lower:
            session['status'] = 'completed'
        else:
            session['status'] = 'active'

        last_lines = content[-200:] if len(content) > 200 else content
        session['awaiting'] = (
            last_lines.rstrip().endswith('>')
            or 'waiting for' in content_lower
            or 'your selection' in content_lower
        )

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
    # Fixed: ID(3) + Target(14) + Status(10) + Awaiting(8) + Actions(18) + separators(6) = 59
    fixed_width = 59
    remaining = max(term_width - fixed_width, 45)
    dir_width = max(remaining * 2 // 5, 15)
    purpose_width = max(remaining - dir_width, 20)

    lines = [
        f"\n{'=' * term_width}",
        f' Claude Sessions ({len(sessions)} found)',
        f"{'=' * term_width}\n",
        f"{'ID':<3} {'Target':<14} {'Directory':<{dir_width}} "
        f"{'Purpose':<{purpose_width}} {'Status':<10} {'Awaiting':<8} Actions",
        f"{'-' * 3} {'-' * 14} {'-' * dir_width} {'-' * purpose_width} "
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
        lines.append(
            f"{letter:<3} {s['tmux_target']:<14} {directory:<{dir_width}} "
            f"{purpose:<{purpose_width}} {status:<10} {awaiting:<8} {actions}"
        )

    lines += [
        f"\n{'-' * term_width}",
        'Selection syntax:',
        "  'A,C' or 'AC'              Execute default action for selected",
        "  'A:git status, B:pwd'      Execute custom commands",
        "  'all:continue'             Execute on all sessions",
        "  'awaiting:continue'        Execute on sessions awaiting input",
        f"{'-' * term_width}",
    ]
    return '\n'.join(lines)


def format_execution_results(results: List[Dict]) -> str:
    """Format a list of send_to_session() result dicts as human-readable output."""
    if not results:
        return 'No commands executed.'
    lines = ['\nExecuting...\n']
    success_count = sum(1 for r in results if r['success'])
    for r in results:
        if r['success']:
            lines.append(f"  [ok] {r['target']}: '{r['command']}' sent")
        else:
            lines.append(f"  [FAIL] {r['target']}: {r['error']}")
    lines.append(f'\nDone! {success_count}/{len(results)} commands sent.')
    return '\n'.join(lines)


def execute_selections(selection_str: str, sessions: List[Dict]) -> str:
    """Execute selections and return formatted result string.

    Convenience wrapper combining execute_session_selections() (tmux_utils) and
    format_execution_results() for the /ar:tabs command interface.
    """
    results = execute_session_selections(selection_str, sessions)
    return format_execution_results(results)


# ---------------------------------------------------------------------------
# Command entrypoint
# ---------------------------------------------------------------------------

def handle_execute_mode() -> None:
    """Handle --execute mode: read JSON action from stdin, run it, print results.

    Expected stdin JSON:
    {
        "action": "execute",
        "selections": "A,B" | "all:cmd" | "awaiting:cmd",
        "command": "optional override command",
        "sessions": [{"tmux_target": "...", "actions": [...], "awaiting": bool}, ...]
    }
    """
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(json.dumps({'error': f'Invalid JSON: {e}', 'success': False}))
        return

    sessions = data.get('sessions', [])
    selections = data.get('selections', '')
    command = data.get('command', '')

    if not sessions:
        print(json.dumps({'error': 'No sessions provided', 'success': False}))
        return

    # If a command override is provided, broadcast it to the letter selections
    if command and not (':' in selections and not selections.startswith(('all:', 'awaiting:'))):
        if not selections.startswith(('all:', 'awaiting:')):
            letters = selections.replace(' ', '').replace(',', '')
            selections = ', '.join(f'{l}:{command}' for l in letters if l.isalpha())

    print(execute_selections(selections, sessions))


def main() -> None:
    """Entry point for /ar:tabs — discover, analyze, and display Claude tmux sessions."""
    import argparse

    parser = argparse.ArgumentParser(description='Claude tmux session manager')
    parser.add_argument('--execute', action='store_true',
                        help='Execute mode: read JSON from stdin with action details')
    parser.add_argument('--json', action='store_true',
                        help='Output session data as JSON instead of table')
    args = parser.parse_args()

    if args.execute:
        handle_execute_mode()
        return

    tmux = get_tmux_utilities()
    sessions = discover_claude_sessions(tmux)

    if not sessions:
        print('No Claude sessions found in tmux.')
        return

    analyzed = analyze_sessions(sessions)

    if args.json:
        print(json.dumps({'sessions': analyzed, 'count': len(analyzed)}, indent=2))
    else:
        print(format_output(analyzed))
