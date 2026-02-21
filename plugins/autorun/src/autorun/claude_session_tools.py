#!/usr/bin/env python3
"""
Claude Session Tools - Unified tool for exploring, analyzing, and exporting Claude Code session histories.

This module merges claude-code-session-explorer and export-claude-sessions into a single
DRY implementation using factory patterns from plugins.py.

Operations:
- list <project>: List sessions for a project
- search <pattern>: Search sessions for pattern
- extract <project> <session-id> <type>: Extract content
- analyze <project> <session-id>: Analyze session structure
- timeline <project> <session-id>: Show chronological timeline
- find-tool <tool> [pattern]: Find specific tool usage
- corrections [project]: Find user correction patterns
- find-commands <pattern> [context]: Search for command patterns
- planning-usage: Analyze planning command usage
- cross-ref <project> <session-id> <file>: Cross-reference with file
- export <project> <session-id> [output]: Export session to markdown
- export-recent [days] [output]: Export recent sessions to markdown
"""

import json
import sys
import os
import re
import functools
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Iterator, Tuple, Callable, Union
from dataclasses import dataclass, replace, field
import getpass

# ============================================================================
# CONFIGURATION (Parameterized, no hard-coding, env var overrideable)
# ============================================================================

@dataclass
class SessionToolsConfig:
    """Configuration for session tools (all values parameterized).

    Environment variable override for all values.
    Backward compatible with original SESSION_EXPLORER_* env vars.
    """
    # Display Limits (configurable via environment)
    preview_limit: int = int(os.environ.get('SESSION_TOOLS_PREVIEW_LIMIT',
                                             os.environ.get('SESSION_EXPLORER_PREVIEW_LIMIT', '100')))
    context_limit: int = int(os.environ.get('SESSION_TOOLS_CONTEXT_LIMIT',
                                           os.environ.get('SESSION_EXPLORER_CONTEXT_LIMIT', '500')))
    display_limit: int = int(os.environ.get('SESSION_TOOLS_DISPLAY_LIMIT',
                                           os.environ.get('SESSION_EXPLORER_DISPLAY_LIMIT', '150')))
    extract_limit: int = int(os.environ.get('SESSION_TOOLS_EXTRACT_LIMIT',
                                           os.environ.get('SESSION_EXPLORER_EXTRACT_LIMIT', '300')))
    tool_content_limit: int = int(os.environ.get('SESSION_TOOLS_TOOL_CONTENT_LIMIT',
                                                os.environ.get('SESSION_EXPLORER_TOOL_CONTENT_LIMIT', '200')))

    # Session Discovery (configurable paths)
    projects_base_dir: str = os.environ.get(
        'CLAUDE_PROJECTS_DIR',
        str(Path.home() / '.claude' / 'projects')
    )

    # Content Filtering (configurable patterns)
    system_message_patterns: List[str] = field(default_factory=lambda: os.environ.get(
        'SESSION_TOOLS_SYSTEM_PATTERNS',
        '[Request interrupted,<task-notification>,<system-reminder>'
    ).split(','))

    # Correction Pattern Categories (configurable)
    correction_categories: List[str] = field(default_factory=lambda: [
        'regression', 'skip_step', 'misunderstanding', 'incomplete', 'other'
    ])

    # Result Limits (configurable)
    max_results_per_project: int = int(os.environ.get('SESSION_TOOLS_MAX_RESULTS',
                                                       os.environ.get('SESSION_EXPLORER_MAX_RESULTS', '3')))
    max_results_total: int = int(os.environ.get('SESSION_TOOLS_MAX_RESULTS_TOTAL',
                                                os.environ.get('SESSION_EXPLORER_MAX_RESULTS_TOTAL', '10')))

    # Export Defaults (configurable)
    default_export_filename_template: str = os.environ.get(
        'SESSION_TOOLS_EXPORT_TEMPLATE',
        'session_{session_id}.md'
    )
    default_recent_export_filename: str = os.environ.get(
        'SESSION_TOOLS_RECENT_TEMPLATE',
        'recent_sessions.md'
    )

    # JSON error handling
    max_json_errors: int = int(os.environ.get('SESSION_TOOLS_MAX_JSON_ERRORS', '10'))


# Global config instance (following plugins.py pattern)
CONFIG = SessionToolsConfig()

# ============================================================================
# PATTERN DEFINITIONS (Configurable, not hard-coded in functions)
# ============================================================================

# Correction indicators - patterns that suggest user is correcting Claude
DEFAULT_CORRECTION_PATTERNS = [
    r'\byou deleted\b',
    r'\byou forgot\b',
    r'\byou missed\b',
    r'\bshould have\b',
    r'\byou didn\'t\b',
    r'\bthat\'s not correct\b',
    r'\bwrong\b',
    r'\bmistake\b',
    r'\bactually\b',
    r'\bbut you\b',
    r'\bincorrect\b',
    r'\bremoved\b',
    r'\blost\b',
    r'\bregressed\b',
    r'\bbroke\b',
    r'\balso need\b',
    r'\bmust also\b',
    r'\bdon\'t forget\b',
    r'\bno,\s',
    r'\bwait,?\s',
    r'\bstop\b',
    r'\bwhat,',
    r'\bnono\b',
]

# Planning commands to search for
DEFAULT_PLANNING_COMMANDS = [
    r'/cr:plannew\b', r'/cr:pn\b',
    r'/cr:planrefine\b', r'/cr:pr\b',
    r'/cr:planupdate\b', r'/cr:pu\b',
    r'/cr:planprocess\b', r'/cr:pp\b',
    r'/plannew\b', r'/planrefine\b', r'/planupdate\b', r'/planprocess\b',
]


def _load_patterns_from_env(env_var: str, default_patterns: List[str]) -> List[str]:
    """Load regex patterns from environment variable.

    Following config.py pattern for environment-based configuration.
    Allows runtime customization without code changes.
    """
    if env_var in os.environ:
        return os.environ[env_var].split(',')
    return default_patterns


CORRECTION_PATTERNS = _load_patterns_from_env(
    'SESSION_TOOLS_CORRECTION_PATTERNS',
    DEFAULT_CORRECTION_PATTERNS
)

PLANNING_COMMANDS = _load_patterns_from_env(
    'SESSION_TOOLS_PLANNING_COMMANDS',
    DEFAULT_PLANNING_COMMANDS
)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_username() -> str:
    """Get current username for path handling."""
    return getpass.getuser()


def extract_project_name(dir_name: str) -> str:
    """Extract human-readable project name from encoded directory name.

    Claude encodes paths like /Users/name/source/project as:
    -Users-name-source-project

    This extracts just the meaningful project part.
    """
    username = get_username()
    prefixes = [
        f'-Users-{username}-source-',
        f'-Users-{username}-',
        '-Users-',
    ]
    result = dir_name
    for prefix in prefixes:
        if result.startswith(prefix):
            result = result[len(prefix):]
            break
    return result


def find_project_dir(projects_dir: Path, project_name: str) -> Optional[Path]:
    """Find the project directory that matches the given project name.

    Supports fuzzy matching for common mistakes:
    - Case insensitive matching
    - Partial name matching (substring)
    - Encoded vs decoded path formats
    """
    base_dir = Path(projects_dir)
    username = get_username()

    # Try common patterns
    patterns = [
        f'-Users-{username}-source-{project_name}',
        f'-Users-{username}-{project_name}',
    ]

    for pattern in patterns:
        candidate = base_dir / pattern
        if candidate.exists():
            return candidate

    # Case-insensitive match
    project_lower = project_name.lower()
    for project_dir in base_dir.glob('-Users-*'):
        if extract_project_name(project_dir.name).lower() == project_lower:
            return project_dir

    # Partial match (substring)
    for project_dir in base_dir.glob('-Users-*'):
        if project_lower in extract_project_name(project_dir.name).lower():
            return project_dir

    return None


def encode_project_path(project_path: str) -> str:
    """Convert project path to Claude's encoded directory format.

    Claude encodes project paths by replacing '/' with '-' in directory names.
    This is needed for session discovery and export functionality.

    Args:
        project_path: Absolute or relative path to project

    Returns:
        Encoded path suitable for finding Claude session directories

    Examples:
        /Users/username/project    -> -Users-username-project
        /home/username/source/app  -> -home-username-source-app
    """
    if project_path.startswith('/'):
        project_path = project_path[1:]
    return project_path.replace('/', '-')


# ============================================================================
# SESSION PROCESSOR FACTORY (DRY pattern - eliminates JSONL duplication)
# ============================================================================

def process_session_file(
    session_file: Path,
    processor: Callable[[Dict, int], Optional[Any]],
    config: SessionToolsConfig = CONFIG
) -> Iterator[Any]:
    """Generic session file processor with robust error handling.

    Eliminates repeated JSONL reading across the codebase. All JSONL session file
    parsing should use this factory for consistent error handling and encoding.

    Functions using this factory:
    - extract_tool_results() - Extract tool usage and results
    - extract_content_type() - Extract specific content types
    - search_sessions() - Search for pattern (refactored)
    - analyze_session() - Session statistics (refactored)
    - find_corrections() - Find user correction patterns
    - timeline_session() - Chronological timeline
    - _extract_user_messages_filtered() - Export user messages

    Args:
        session_file: Path to .jsonl file
        processor: Callable that receives (obj, line_num) and returns result or None
        config: Configuration object for limits and settings

    Yields:
        Results from processor for each valid JSON object

    Edge cases handled:
    - Malformed JSON lines (skipped with warning)
    - Permission errors (early return with error)
    - Encoding errors (uses errors='replace')
    - File not found (early return with error)
    """
    line_errors = 0
    max_errors = config.max_json_errors

    try:
        with open(session_file, 'r', encoding='utf-8', errors='replace') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                    result = processor(obj, line_num)
                    if result is not None:
                        yield result

                except json.JSONDecodeError:
                    line_errors += 1
                    if line_errors <= max_errors:
                        print(f"⚠️  Line {line_num}: Invalid JSON (suppressing further errors)")
                    continue

                except Exception:
                    # Silently skip processing errors (maintains original behavior)
                    continue

    except PermissionError:
        print(f"❌ Permission denied: {session_file}")
        return

    except FileNotFoundError:
        print(f"❌ File not found: {session_file}")
        return


# ============================================================================
# CONTENT EXTRACTION FACTORY (Handles 3 different message formats)
# ============================================================================

def extract_message_text(msg_data: Dict[str, Any], max_length: int = None) -> str:
    """Extract text content from message, handling multiple formats.

    This function PRESERVES the complex logic from original explorer.
    Handles three different message content formats:
    1. String format (legacy sessions)
    2. Array format with text items (modern sessions)
    3. Queue-operation format (special case)

    Args:
        msg_data: Message dictionary from session
        max_length: Optional maximum length for truncation

    Returns:
        Extracted text content, empty string if extraction fails

    Edge cases:
    - Non-dict message data (returns "")
    - Missing content field (returns "")
    - Queue-operation messages (extracts status)
    - Tool results (truncates to TOOL_CONTENT_LIMIT)
    """
    # Edge case: Non-dict message (original line 371-372)
    if not isinstance(msg_data, dict):
        return ""

    # Check if this is a queue-operation with content (original lines 372-375)
    if msg_data.get('type') == 'queue-operation' and 'content' in msg_data:
        return msg_data['content']

    message = msg_data.get('message', {})
    if not isinstance(message, dict):
        return ""

    content = message.get('content', '')

    # Case 1: String content (legacy format) - original line 376-377
    if isinstance(content, str):
        text = content
    # Case 2: Array content (modern format) - original lines 378-397
    elif isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get('type') == 'text':
                    parts.append(item.get('text', ''))
                elif item.get('type') == 'thinking':
                    parts.append(f"[THINKING]: {item.get('thinking', '')}")
                elif item.get('type') == 'tool_use':
                    tool_name = item.get('name', 'Unknown')
                    parts.append(f"[TOOL: {tool_name}]")
                elif 'text' in item:
                    parts.append(item['text'])
            elif isinstance(item, str):
                parts.append(item)
        text = '\n'.join(parts)
    else:
        return ""

    # Edge case: Tool result truncation
    if max_length and len(text) > max_length:
        text = text[:max_length]

    return text


# ============================================================================
# PROJECT RESOLUTION HELPER (Centralized error handling)
# ============================================================================

def resolve_session_file(
    project: str,
    session_id: str,
    config: SessionToolsConfig = None
) -> Optional[Path]:
    """Resolve project directory and session file path with robust error handling.

    Centralizes error handling and path resolution.
    Eliminates 5+ duplicated blocks from original:
    - Lines 567-576 (in extract command)
    - Lines 604-613 (in analyze command)
    - Similar patterns in list_project_sessions() and analyze_session()

    Args:
        project: Project name (encoded or human-readable)
        session_id: Session UUID
        config: Configuration object (uses CONFIG if None)

    Returns:
        Path to session file, or None if not found

    Edge cases:
    - Project not found (shows available projects)
    - Session not found (shows available sessions)
    - Permission denied (shows fix command)
    """
    if config is None:
        config = CONFIG

    projects_base = Path(config.projects_base_dir)

    # Edge case: Base directory doesn't exist
    if not projects_base.exists():
        available_base = Path.home() / '.claude' / 'projects'
        print(f"❌ Projects directory not found: {config.projects_base_dir}")
        print(f"💡 Did you mean: {available_base}")
        print(f"💡 Set CLAUDE_PROJECTS_DIR environment variable if using custom location")
        return None

    # Use fuzzy matching for project names
    project_dir = find_project_dir(projects_base, project)

    # Edge case: Project not found - show available projects
    if project_dir is None:
        print(f"❌ Project not found: '{project}'")
        print(f"\n📁 Available projects:")
        for pd in list(projects_base.glob('-Users-*'))[:10]:
            p_name = extract_project_name(pd.name)
            session_count = len(list(pd.glob('*.jsonl')))
            print(f"   - {p_name} ({session_count} sessions)")
        if len(list(projects_base.glob('-Users-*'))) > 10:
            print(f"   ... and {len(list(projects_base.glob('-Users-*'))) - 10} more")
        return None

    session_file = project_dir / f'{session_id}.jsonl'

    # Edge case: Session file doesn't exist - show available sessions
    if not session_file.exists():
        print(f"❌ Session not found: {session_id}")
        print(f"\n📁 Available sessions in '{extract_project_name(project_dir.name)}':")
        for sf in list(project_dir.glob('*.jsonl'))[:10]:
            print(f"   - {sf.stem}")
        if len(list(project_dir.glob('*.jsonl'))) > 10:
            print(f"   ... and {len(list(project_dir.glob('*.jsonl'))) - 10} more")
        return None

    # Edge case: Permission denied
    if not os.access(session_file, os.R_OK):
        print(f"❌ Permission denied: {session_file}")
        print(f"💡 Fix: chmod +r '{session_file}'")
        return None

    return session_file


# ============================================================================
# SESSION ITERATION HELPER (DRY pattern)
# ============================================================================

def iter_all_sessions(
    project_filter: Optional[str] = None,
    config: SessionToolsConfig = None
) -> Iterator[Tuple[str, Path]]:
    """Iterate over all sessions, yielding (project_name, session_file) tuples.

    This DRY function replaces 4 repeated code blocks that iterate over all
    projects and sessions. Use this instead of manually iterating.

    Args:
        project_filter: Optional string to filter projects by name (case-insensitive substring match)
        config: Configuration object (uses CONFIG if None)

    Yields:
        Tuple of (project_name, session_file_path) for each session
    """
    if config is None:
        config = CONFIG

    projects_dir = Path(config.projects_base_dir)
    for project_dir in projects_dir.glob('-Users-*'):
        project_name = extract_project_name(project_dir.name)
        if project_filter and project_filter.lower() not in project_name.lower():
            continue
        for session_file in project_dir.glob('*.jsonl'):
            yield project_name, session_file


# ============================================================================
# CORE EXTRACTION AND ANALYSIS FUNCTIONS (Preserved from original)
# ============================================================================

def extract_tool_results(
    session_file: str,
    tool_type: str = None,
    pattern: str = None,
    config: SessionToolsConfig = None
) -> List[Dict[str, Any]]:
    """Extract tool usage and results from session.

    Preserved from original explorer lines 142-178.
    """
    if config is None:
        config = CONFIG

    results = []
    session_path = Path(session_file)

    def process_tool(obj: Dict, line_num: int) -> Optional[Dict]:
        if 'message' not in obj or not isinstance(obj['message'], dict):
            return None

        content = obj['message'].get('content', [])
        if not isinstance(content, list):
            return None

        for item in content:
            if not isinstance(item, dict):
                continue

            item_type = item.get('type')
            name = item.get('name', '')

            if item_type == 'tool_use':
                if tool_type is None or tool_type.lower() == name.lower():
                    if pattern is None or pattern.lower() in str(item.get('input', {})).lower():
                        return {
                            'timestamp': obj.get('timestamp'),
                            'type': 'tool_use',
                            'tool': name,
                            'input': item.get('input', {}),
                            'line': line_num
                        }

            elif item_type == 'tool_result':
                if tool_type is None or tool_type.lower() == name.lower():
                    if pattern is None or pattern.lower() in str(item.get('content', '')).lower():
                        content_text = item.get('content', '')
                        return {
                            'timestamp': obj.get('timestamp'),
                            'type': 'tool_result',
                            'tool': name,
                            'content': content_text[:config.tool_content_limit],
                            'line': line_num
                        }
        return None

    for result in process_session_file(session_path, process_tool, config):
        if result:
            results.append(result)

    return results


def extract_content_type(
    session_file: str,
    content_type: str,
    config: SessionToolsConfig = None
) -> List[Dict[str, Any]]:
    """Extract specific types of content from session.

    Preserved from original explorer lines 180-243.
    """
    if config is None:
        config = CONFIG

    results = []
    session_path = Path(session_file)

    def process_content(obj: Dict, line_num: int) -> Optional[Dict]:
        msg_type = obj.get('type')
        timestamp = obj.get('timestamp')

        if content_type == 'user-prompts' and msg_type == 'user':
            message = obj.get('message', {})
            if isinstance(message, dict):
                c = message.get('content', '')
                return {'timestamp': timestamp, 'content': c, 'type': 'user'}

        elif content_type == 'assistant-responses' and msg_type == 'assistant':
            message = obj.get('message', {})
            if isinstance(message, dict):
                c = message.get('content', [])
                if isinstance(c, list):
                    text_content = [c_item for c_item in c
                                   if isinstance(c_item, dict) and c_item.get('type') == 'text']
                    return {'timestamp': timestamp, 'content': text_content, 'type': 'assistant'}

        elif content_type == 'pbcopy' and msg_type == 'assistant':
            message = obj.get('message', {})
            if isinstance(message, dict):
                content = message.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'tool_use':
                            if item.get('name') == 'Bash' and 'pbcopy' in str(item.get('input', {})):
                                cmd = item.get('input', {}).get('command', '')
                                if "cat <<'EOF' | pbcopy" in cmd:
                                    start = cmd.find("cat <<'EOF' | pbcopy\n") + len("cat <<'EOF' | pbcopy\n")
                                    end = cmd.rfind("\nEOF")
                                    if start > 0 and end > 0:
                                        return {
                                            'timestamp': timestamp,
                                            'content': cmd[start:end],
                                            'type': 'pbcopy'
                                        }

        elif content_type == 'bash-output' and msg_type == 'assistant':
            message = obj.get('message', {})
            if isinstance(message, dict):
                content = message.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'tool_result':
                            if item.get('name') == 'Bash':
                                return {
                                    'timestamp': timestamp,
                                    'content': item.get('content', ''),
                                    'type': 'bash_output'
                                }

        elif content_type == 'all':
            return {'timestamp': timestamp, 'type': msg_type,
                   'content': str(obj)[:config.tool_content_limit]}

        return None

    for result in process_session_file(session_path, process_content, config):
        if result:
            results.append(result)

    return results


def search_sessions(
    pattern: str,
    config: SessionToolsConfig = None
) -> Dict[str, List[Dict[str, Any]]]:
    """Search for pattern across sessions using process_session_file factory.

    Refactored from original explorer lines 245-265 to use DRY pattern.
    Now uses process_session_file() for consistent JSONL parsing and error handling.
    """
    if config is None:
        config = CONFIG

    results = defaultdict(list)

    for project_name, session_file in iter_all_sessions(config=config):
        # Closure to capture session_file for result construction
        def search_processor(obj: Dict, line_num: int) -> Optional[Dict]:
            # Reconstruct line content for pattern matching
            line_str = json.dumps(obj, separators=(',', ':'))
            if pattern.lower() in line_str.lower():
                return {
                    'session': session_file.stem,
                    'line': line_num,
                    'preview': line_str[:config.preview_limit]
                }
            return None

        for match in process_session_file(session_file, search_processor, config):
            results[project_name].append(match)

    return results


def list_project_sessions(
    project_name: str,
    config: SessionToolsConfig = None
) -> List[Dict[str, Any]]:
    """List all sessions for a project.

    Preserved from original explorer lines 267-284.
    """
    if config is None:
        config = CONFIG

    projects_dir = Path(config.projects_base_dir)
    project_dir = find_project_dir(projects_dir, project_name)

    if project_dir is None or not project_dir.exists():
        return []

    sessions = []
    for session_file in sorted(project_dir.glob('*.jsonl'),
                               key=lambda x: x.stat().st_mtime, reverse=True):
        stat = session_file.stat()
        sessions.append({
            'name': session_file.stem,
            'size_kb': stat.st_size // 1024,
            'modified': stat.st_mtime
        })

    return sessions


def analyze_session(
    session_file: str,
    config: SessionToolsConfig = None
) -> Dict[str, Any]:
    """Analyze session structure and content using process_session_file factory.

    Refactored from original explorer lines 286-333 to use DRY pattern.
    Now uses process_session_file() with accumulator pattern for consistent JSONL parsing.
    """
    if config is None:
        config = CONFIG

    stats = {
        'total_lines': 0,
        'user_prompts': 0,
        'assistant_responses': 0,
        'tool_uses': defaultdict(int),
        'tool_results': defaultdict(int),
        'files_touched': set(),
        'timestamps': []
    }

    session_path = Path(session_file)

    def analyze_processor(obj: Dict, line_num: int) -> Optional[Dict]:
        # Update stats accumulator in-place
        stats['total_lines'] += 1

        msg_type = obj.get('type')
        stats['timestamps'].append(obj.get('timestamp'))

        if msg_type == 'user':
            stats['user_prompts'] += 1
        elif msg_type == 'assistant':
            stats['assistant_responses'] += 1
            message = obj.get('message', {})
            if isinstance(message, dict):
                content = message.get('content', [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            if item.get('type') == 'tool_use':
                                tool = item.get('name', 'unknown')
                                stats['tool_uses'][tool] += 1
                                # Track files
                                if tool in ['Edit', 'Read', 'Write']:
                                    input_data = item.get('input', {})
                                    if 'file_path' in input_data:
                                        stats['files_touched'].add(input_data['file_path'])
                            elif item.get('type') == 'tool_result':
                                tool = item.get('name', 'unknown')
                                stats['tool_results'][tool] += 1
        return None  # Accumulator pattern - return None since stats is updated in-place

    # Process all lines (results ignored since stats accumulator is updated in-place)
    for _ in process_session_file(session_path, analyze_processor, config):
        pass

    return stats


def find_corrections(
    session_file: str,
    config: SessionToolsConfig = None
) -> List[Dict[str, Any]]:
    """Find user correction patterns in a session file.

    Preserved from original explorer lines 401-448.
    """
    if config is None:
        config = CONFIG

    corrections = []
    combined_pattern = re.compile('|'.join(CORRECTION_PATTERNS), re.IGNORECASE)

    session_path = Path(session_file)

    def process_correction(obj: Dict, line_num: int) -> Optional[Dict]:
        if obj.get('type') != 'user':
            return None

        text = extract_message_text(obj)
        if combined_pattern.search(text):
            # Categorize
            text_lower = text.lower()
            if any(w in text_lower for w in ['deleted', 'removed', 'lost', 'regressed']):
                category = 'regression'
            elif any(w in text_lower for w in ['forgot', 'missed', "didn't"]):
                category = 'skip_step'
            elif any(w in text_lower for w in ['wrong', 'incorrect', "that's not", 'mistake']):
                category = 'misunderstanding'
            elif any(w in text_lower for w in ['also need', 'must also', "don't forget"]):
                category = 'incomplete'
            else:
                category = 'other'

            return {
                'file': session_file,
                'line': line_num,
                'category': category,
                'text': text[:config.extract_limit],
                'timestamp': obj.get('timestamp')
            }
        return None

    for result in process_session_file(session_path, process_correction, config):
        if result:
            corrections.append(result)

    return corrections


def search_command_patterns(
    session_file: str,
    patterns: List[str],
    context_after: int = 5,
    config: SessionToolsConfig = None
) -> List[Dict[str, Any]]:
    """Search for command patterns and extract context.

    Preserved from original explorer lines 451-503.
    """
    if config is None:
        config = CONFIG

    matches = []
    combined_pattern = re.compile('|'.join(patterns), re.IGNORECASE)
    session_path = Path(session_file)

    try:
        with open(session_path, 'r', encoding='utf-8') as f:
            messages = []
            for line_num, line in enumerate(f, 1):
                try:
                    msg = json.loads(line.strip())
                    msg_type = msg.get('type', '')
                    if msg_type in ['user', 'assistant', 'queue-operation']:
                        messages.append((line_num, msg))
                except json.JSONDecodeError:
                    continue

            # Search for patterns
            for idx, (line_num, msg) in enumerate(messages):
                msg_text = extract_message_text(msg)

                match = combined_pattern.search(msg_text)
                if match:
                    # Extract this message and context_after more
                    context_messages = []
                    for i in range(context_after + 1):
                        if idx + i < len(messages):
                            ctx_line, ctx_msg = messages[idx + i]
                            context_messages.append({
                                'line': ctx_line,
                                'type': ctx_msg.get('type', 'unknown'),
                                'text': extract_message_text(ctx_msg)[:config.context_limit]
                            })

                    return {
                        'file': session_file,
                        'line': line_num,
                        'pattern': match.group(),
                        'context': context_messages
                    }
    except Exception:
        pass

    return matches


# ============================================================================
# CROSS-REFERENCE FUNCTIONALITY (Preserved from original lines 335-366)
# ============================================================================

def cross_reference_file(
    session_file: str,
    file_path: str,
    config: SessionToolsConfig = None
) -> Optional[Dict[str, Any]]:
    """Cross-reference session content with file.

    PRESERVED from original code lines 335-366.
    Shows what changes were made to a file during a session.
    """
    if config is None:
        config = CONFIG

    try:
        with open(file_path, 'r') as f:
            file_content = f.read()
    except Exception:
        return None

    analysis = {
        'file': file_path,
        'total_content_lines': len(file_content.split('\n')),
        'session_content_in_file': 0,
        'changes_detected': []
    }

    # Extract key content types from session
    user_prompts = extract_content_type(str(session_file), 'user-prompts', config)
    pbcopy_items = extract_content_type(str(session_file), 'pbcopy', config)

    # Check what's in the file
    for item in pbcopy_items:
        content = item.get('content', '')
        if isinstance(content, str):
            lines_in_file = sum(1 for line in content.split('\n') if line and line in file_content)
            if lines_in_file > 0:
                analysis['session_content_in_file'] += lines_in_file
                analysis['changes_detected'].append({
                    'timestamp': item.get('timestamp'),
                    'type': 'pbcopy',
                    'lines_found': lines_in_file
                })

    return analysis


# ============================================================================
# TIMELINE FUNCTIONALITY (NEW - planned but not in original)
# ============================================================================

def timeline_session(
    session_file: str,
    config: SessionToolsConfig = None
) -> List[Dict[str, Any]]:
    """Show chronological timeline of events in a session.

    NEW functionality - provides chronological view of all events.
    """
    if config is None:
        config = CONFIG

    events = []
    session_path = Path(session_file)

    def collect_events(obj: Dict, line_num: int) -> Optional[Dict]:
        msg_type = obj.get('type', '')
        timestamp = obj.get('timestamp', '')

        if msg_type in ['user', 'assistant']:
            message = obj.get('message', {})
            if isinstance(message, dict):
                content = message.get('content', [])

                event = {
                    'type': msg_type,
                    'timestamp': timestamp,
                    'line': line_num,
                }

                if isinstance(content, str):
                    event['content'] = content[:config.preview_limit]
                    event['tool_count'] = 0
                elif isinstance(content, list):
                    tool_count = sum(1 for item in content
                                   if isinstance(item, dict) and item.get('type') == 'tool_use')
                    texts = [item.get('text', '') for item in content
                            if isinstance(item, dict) and item.get('type') == 'text']
                    preview = ' '.join(texts)[:config.preview_limit] if texts else ''

                    event['tool_count'] = tool_count
                    event['content'] = preview
                else:
                    return None

                return event
        return None

    for result in process_session_file(session_path, collect_events, config):
        if result:
            events.append(result)

    return events


# ============================================================================
# EXPORT OPERATIONS (MIGRATED from shell scripts)
# ============================================================================

def _extract_session_metadata(session_file: Path) -> Dict[str, str]:
    """Extract session metadata from first line of JSONL.

    Replaces shell jq pattern at export_claude_session.sh:33-41
    """
    with open(session_file, 'r') as f:
        first_line = f.readline().strip()
        data = json.loads(first_line)
        return {
            'sessionId': data.get('sessionId', ''),
            'timestamp': data.get('timestamp', ''),
            'gitBranch': data.get('gitBranch', 'unknown'),
            'cwd': data.get('cwd', ''),
            'version': data.get('version', '')
        }


def _extract_user_messages_filtered(
    session_file: Path,
    config: SessionToolsConfig = None
) -> List[str]:
    """Extract user messages with system message filtering.

    Replaces shell jq+grep pattern at export_claude_session.sh:54-71
    Handles both string and array content formats.
    """
    if config is None:
        config = CONFIG

    messages = []
    system_patterns = config.system_message_patterns

    def process_user(obj: Dict, line_num: int) -> Optional[str]:
        if obj.get('type') != 'user':
            return None

        message = obj.get('message', {})
        if not isinstance(message, dict):
            return None

        content = message.get('content', '')

        # Handle string format
        if isinstance(content, str):
            text = content
        # Handle array format
        elif isinstance(content, list):
            texts = [item.get('text', '') for item in content
                    if isinstance(item, dict) and item.get('type') == 'text']
            text = '\n'.join(texts)
        else:
            return None

        # Filter system messages (following shell script pattern)
        for pattern in system_patterns:
            if pattern in text:
                return None
        if text.strip() in ['null', '']:
            return None

        return text

    for result in process_session_file(session_file, process_user, config):
        if result:
            messages.append(result)

    return messages


def _generate_markdown_export(
    session_id: str,
    metadata: Dict,
    messages: List[str]
) -> str:
    """Generate markdown export format.

    Following export_claude_session.sh format with proper headers.
    """
    md = f"""# Session Export: {session_id}

**Export Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## Session Metadata

**Session ID:** {metadata['sessionId']}
**Date:** {metadata['timestamp']}
**Git Branch:** {metadata['gitBranch']}
**Working Directory:** {metadata['cwd']}
**Claude Code Version:** {metadata['version']}

## User Messages

The following are all user messages from this conversation session:

"""

    md += '\n'.join(messages)
    md += "\n\n---\n\n**End of session export**\n"
    return md


def _generate_recent_markdown(
    sessions: List[Tuple[str, Path, datetime]],
    days: int,
    config: SessionToolsConfig = None
) -> str:
    """Generate markdown for recent sessions export.

    Following export_recent_claude_sessions.sh format.
    """
    if config is None:
        config = CONFIG

    md = f"""# Recent Sessions Export (Last {days} Days)

**Export Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

**Total Sessions:** {len(sessions)}

---

"""

    for project_name, session_file, mtime in sessions:
        session_id = session_file.stem
        metadata = _extract_session_metadata(session_file)
        messages = _extract_user_messages_filtered(session_file, config)

        md += f"""## Session: {session_id}

**Date:** {metadata['timestamp']}
**Git Branch:** {metadata['gitBranch']}
**Working Directory:** {metadata['cwd']}

### User Messages
**Total user messages:** {len(messages)}

{''.join(messages)}

---

"""

    return md


# ============================================================================
# COMMAND FACTORY with CLI Parameter Support (DRY Pattern from plugins.py)
# ============================================================================

def _make_command_handler(
    name: str,
    handler_func: Callable,
    usage: str,
    required_args: int,
    optional_args: List[str] = None
) -> Callable:
    """Factory: Generate command handler with consistent argument parsing.

    Following plugins.py:46-60 pattern for DRY command generation.
    Eliminates repetitive argument validation in 8+ command blocks.

    NEW: Supports CLI parameter overrides for environment variables.
    All config values can be overridden via CLI flags following typer/click patterns.

    Args:
        name: Command name for help text
        handler_func: Core handler function (receives validated args + config)
        usage: Usage string for help text
        required_args: Minimum number of required positional arguments
        optional_args: List of optional argument names (e.g., ['--output', '--days'])

    Returns:
        Wrapped handler function

    CLI Override Pattern:
        Environment var: SESSION_TOOLS_PREVIEW_LIMIT
        CLI flag: --preview-limit N
        Priority: CLI flag > Environment var > Default
    """
    def wrapper(args: List[str]) -> Optional[str]:
        if len(args) < required_args:
            return f"Usage: claude-session-tools {usage}"

        # Parse CLI flags (format: --key value or --key=value)
        cli_config = {}
        positional_args = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith('--'):
                # CLI override parameter
                if '=' in arg:
                    key, value = arg[2:].split('=', 1)
                elif i + 1 < len(args) and not args[i + 1].startswith('--'):
                    key, value = arg[2:], args[i + 1]
                    i += 1
                else:
                    key, value = arg[2:], 'true'

                # Convert CLI param names to config keys
                # --preview-limit -> preview_limit
                config_key = key.replace('-', '_')

                # Create merged config for this invocation
                # CLI overrides take precedence over environment
                if hasattr(CONFIG, config_key):
                    # Type conversion based on config field type
                    field_type = type(getattr(CONFIG, config_key))
                    if field_type == int:
                        try:
                            cli_config[config_key] = int(value)
                        except ValueError:
                            print(f"⚠️  Invalid value for --{key}: {value} (expected int)")
                            return None
                    elif field_type == bool:
                        cli_config[config_key] = value.lower() in ('true', '1', 'yes')
                    else:
                        cli_config[config_key] = value
            else:
                positional_args.append(arg)
            i += 1

        # Create config override object (following dataclass pattern)
        merged_config = CONFIG
        if cli_config:
            merged_config = replace(CONFIG, **cli_config)

        # Call handler with merged config
        return handler_func(*positional_args[:required_args], config=merged_config)

    wrapper.__name__ = f"handle_{name}"
    return wrapper


# ============================================================================
# COMMAND HANDLERS (Refactored from main() lines 522-738)
# ============================================================================

def _handle_list(project: str, config: SessionToolsConfig = None) -> str:
    """List all sessions for a project."""
    if config is None:
        config = CONFIG

    sessions = list_project_sessions(project, config)
    if not sessions:
        return f"No sessions found for '{project}'"

    output = [f"Sessions for '{project}':\n"]
    for session in sessions:
        output.append(f"  {session['name']} ({session['size_kb']}KB)")
    output.append(f"\nTotal: {len(sessions)} sessions")
    return '\n'.join(output)


def _handle_search(pattern: str, config: SessionToolsConfig = None) -> str:
    """Search sessions for pattern."""
    if config is None:
        config = CONFIG

    results = search_sessions(pattern, config)

    output = [f"Search results for '{pattern}':\n"]
    total = 0
    for proj, matches in results.items():
        output.append(f"Project: {proj} ({len(matches)} matches)")
        for match in matches[:config.max_results_per_project]:
            output.append(f"  {match['session']}: Line {match['line']}")
            output.append(f"    {match['preview'][:config.preview_limit]}...")
        if len(matches) > config.max_results_per_project:
            output.append(f"  ... and {len(matches) - config.max_results_per_project} more")
        total += len(matches)
        output.append("")
    output.append(f"Total: {total} matches")
    return '\n'.join(output)


def _handle_extract(project: str, session_id: str, content_type: str, config: SessionToolsConfig = None) -> str:
    """Extract specific content type from session."""
    if config is None:
        config = CONFIG

    session_file = resolve_session_file(project, session_id, config)
    if not session_file:
        return ""

    items = extract_content_type(str(session_file), content_type, config)
    output = [f"Extracted {len(items)} items of type '{content_type}':\n"]

    for i, item in enumerate(items[:5], 1):
        output.append("=" * 70)
        output.append(f"Item #{i} [{item.get('timestamp', 'unknown')}]")
        output.append("=" * 70)
        content = item.get('content', '')
        if isinstance(content, str):
            output.append(content[:config.extract_limit])
        else:
            output.append(str(content)[:config.extract_limit])
        if len(str(content)) > config.extract_limit:
            output.append(f"\n... ({len(str(content))} total characters)")
        output.append("")

    if len(items) > 5:
        output.append(f"... and {len(items) - 5} more items")

    return '\n'.join(output)


def _handle_analyze(project: str, session_id: str, config: SessionToolsConfig = None) -> str:
    """Analyze session structure and statistics."""
    if config is None:
        config = CONFIG

    session_file = resolve_session_file(project, session_id, config)
    if not session_file:
        return ""

    stats = analyze_session(str(session_file), config)
    output = [
        f"Session Analysis: {session_id}\n",
        f"Total lines: {stats['total_lines']}",
        f"User prompts: {stats['user_prompts']}",
        f"Assistant responses: {stats['assistant_responses']}",
        f"Files touched: {len(stats['files_touched'])}",
        "\nTool usage:"
    ]
    for tool, count in stats['tool_uses'].items():
        output.append(f"  {tool}: {count}")

    return '\n'.join(output)


def _handle_timeline(project: str, session_id: str, config: SessionToolsConfig = None) -> str:
    """Show chronological timeline of events."""
    if config is None:
        config = CONFIG

    session_file = resolve_session_file(project, session_id, config)
    if not session_file:
        return ""

    events = timeline_session(str(session_file), config)

    if not events:
        return f"No events found in session {session_id}"

    output = [f"Timeline: {session_id}", f"Total events: {len(events)}\n"]

    for i, event in enumerate(events[:config.max_results_total], 1):
        timestamp = event.get('timestamp', 'unknown')[:19]
        output.append(f"{i}. [{event['type'].upper()}] {timestamp}")

        if event.get('tool_count', 0) > 0:
            output.append(f"   Tools: {event['tool_count']}")
        if event.get('content'):
            output.append(f"   {event['content']}")
        output.append("")

    if len(events) > config.max_results_total:
        output.append(f"... and {len(events) - config.max_results_total} more events")

    return '\n'.join(output)


def _handle_find_tool(tool: str, pattern: str = None, config: SessionToolsConfig = None) -> str:
    """Find tool usage across sessions."""
    if config is None:
        config = CONFIG

    print(f"Searching for {tool} usage" + (f" with pattern '{pattern}'" if pattern else "") + "\n")

    project_results = defaultdict(list)
    for project_name, session_file in iter_all_sessions(config=config):
        results = extract_tool_results(str(session_file), tool, pattern, config)
        if results:
            for result in results[:2]:
                project_results[project_name].append(
                    f"  {session_file.stem}: {result.get('tool')} at line {result.get('line')}"
                )

    output = []
    for project_name, lines in project_results.items():
        output.append(f"Project: {project_name}")
        output.extend(lines)

    return '\n'.join(output)


def _handle_corrections(project_filter: str = None, config: SessionToolsConfig = None) -> str:
    """Find user correction patterns."""
    if config is None:
        config = CONFIG

    all_corrections = []

    for project_name, session_file in iter_all_sessions(project_filter, config):
        corrections = find_corrections(str(session_file), config)
        for c in corrections:
            c['project'] = project_name
        all_corrections.extend(corrections)

    # Group by category using CONFIG.correction_categories (parameterized)
    by_category = defaultdict(list)
    for c in all_corrections:
        by_category[c['category']].append(c)

    output = [f"Found {len(all_corrections)} correction instances\n"]

    for category in config.correction_categories:
        items = by_category.get(category, [])
        if items:
            output.append(f"### {category.upper()} ({len(items)} instances)")
            for i, item in enumerate(items[:5], 1):
                output.append(f"\n{i}. {item['project']} - Line {item['line']}")
                output.append(f"   {item['text'][:config.display_limit]}...")
            if len(items) > 5:
                output.append(f"   ... and {len(items) - 5} more")
            output.append("")

    return '\n'.join(output)


def _handle_find_commands(pattern: str, context: int = 5, config: SessionToolsConfig = None) -> str:
    """Search for command patterns with context."""
    if config is None:
        config = CONFIG

    all_matches = []

    for project_name, session_file in iter_all_sessions(config=config):
        matches = search_command_patterns(str(session_file), [pattern], context, config)
        for m in matches:
            m['project'] = project_name
        all_matches.extend(matches)

    output = [f"Found {len(all_matches)} matches\n"]

    for i, match in enumerate(all_matches[:config.max_results_total], 1):
        output.append(f"### Match {i}: {match['project']} (line {match['line']})")
        output.append(f"Pattern: {match['pattern']}")
        for ctx in match['context'][:3]:
            output.append(f"  [{ctx['type']}] {ctx['text'][:config.preview_limit]}...")
        output.append("")

    if len(all_matches) > config.max_results_total:
        output.append(f"... and {len(all_matches) - config.max_results_total} more matches")

    return '\n'.join(output)


def _handle_planning_usage(config: SessionToolsConfig = None) -> str:
    """Analyze planning command usage across sessions."""
    if config is None:
        config = CONFIG

    all_matches = []

    for project_name, session_file in iter_all_sessions(config=config):
        matches = search_command_patterns(str(session_file), PLANNING_COMMANDS, 5, config)
        for m in matches:
            m['project'] = project_name
        all_matches.extend(matches)

    # Count by pattern
    pattern_counts = defaultdict(int)
    project_counts = defaultdict(int)
    for m in all_matches:
        pattern_counts[m['pattern']] += 1
        project_counts[m['project']] += 1

    output = [
        f"Total planning command invocations: {len(all_matches)}",
        f"Across {len(project_counts)} projects\n",
        "Command frequency:"
    ]
    for pat, count in sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True):
        output.append(f"  {pat}: {count}")

    output.append("\nTop projects by usage:")
    for proj, count in sorted(project_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
        output.append(f"  {proj}: {count}")

    return '\n'.join(output)


def _handle_cross_ref(project: str, session_id: str, file_path: str, config: SessionToolsConfig = None) -> str:
    """Cross-reference session content with actual file state."""
    if config is None:
        config = CONFIG

    session_file = resolve_session_file(project, session_id, config)
    if not session_file:
        return ""

    file_obj = Path(file_path)
    if not file_obj.exists():
        return f"❌ File not found: {file_path}"

    analysis = cross_reference_file(str(session_file), file_path, config)
    if not analysis:
        return f"No references to {file_path} found in session {session_id}"

    output = [
        f"Cross-Reference: {file_path}",
        f"Session: {session_id}",
        f"Found {len(analysis['changes_detected'])} references:\n"
    ]

    for i, change in enumerate(analysis['changes_detected'][:config.max_results_per_project], 1):
        output.append(f"{i}. [{change['timestamp']}] {change['type']}: {change['lines_found']} lines found")

    if len(analysis['changes_detected']) > config.max_results_per_project:
        output.append(f"... and {len(analysis['changes_detected']) - config.max_results_per_project} more")

    return '\n'.join(output)


# ============================================================================
# EXPORT HANDLERS (NEW - from shell scripts)
# ============================================================================

def _handle_export(project: str, session_id: str, output: str = None, config: SessionToolsConfig = None) -> str:
    """Export single session to markdown."""
    if config is None:
        config = CONFIG

    session_file = resolve_session_file(project, session_id, config)
    if not session_file:
        return ""

    if not output:
        output = config.default_export_filename_template.format(session_id=session_id)

    # Extract metadata and user messages
    metadata = _extract_session_metadata(session_file)
    messages = _extract_user_messages_filtered(session_file, config)

    # Generate markdown
    md = _generate_markdown_export(session_id, metadata, messages)

    # Write output
    with open(output, 'w') as f:
        f.write(md)

    return f"✅ Session exported to: {output}"


def _handle_export_recent(days: int = 2, output: str = None, config: SessionToolsConfig = None) -> str:
    """Export sessions from last N days."""
    if config is None:
        config = CONFIG

    if not output:
        output = config.default_recent_export_filename

    cutoff_date = datetime.now() - timedelta(days=days)

    # Find recent sessions using iter_all_sessions()
    sessions = []
    for project_name, session_file in iter_all_sessions(config=config):
        mtime = datetime.fromtimestamp(session_file.stat().st_mtime)
        if mtime >= cutoff_date:
            sessions.append((project_name, session_file, mtime))

    # Sort by modification time
    sessions.sort(key=lambda x: x[2], reverse=True)

    # Generate markdown
    md = _generate_recent_markdown(sessions, days, config)

    with open(output, 'w') as f:
        f.write(md)

    return f"✅ Recent sessions exported to: {output}"


# ============================================================================
# DATA-DRIVEN COMMAND REGISTRATION (Following plugins.py:64-72)
# ============================================================================

# Command specifications: (name, short, handler, required_args, usage)
_SESSION_COMMANDS = [
    # Session Discovery
    ("list", None, _handle_list, 1, "list <project>"),

    # Content Extraction
    ("extract", None, _handle_extract, 3, "extract <project> <session-id> <type>"),

    # Analysis Operations
    ("search", None, _handle_search, 1, "search <pattern>"),
    ("analyze", None, _handle_analyze, 2, "analyze <project> <session-id>"),
    ("timeline", None, _handle_timeline, 2, "timeline <project> <session-id>"),

    # Advanced Analysis
    ("find-tool", "ft", _handle_find_tool, 1, "find-tool <tool> [pattern]"),
    ("corrections", None, _handle_corrections, 0, "corrections [project]"),
    ("find-commands", "fc", _handle_find_commands, 1, "find-commands <pattern> [context]"),
    ("planning-usage", "pu", _handle_planning_usage, 0, "planning-usage"),

    # Cross-Reference (PRESERVED from original lines 335-366)
    ("cross-ref", None, _handle_cross_ref, 3, "cross-ref <project> <session-id> <file>"),

    # Export Operations (NEW from shell scripts)
    ("export", None, _handle_export, 2, "export <project> <session-id> [output]"),
    ("export-recent", "er", _handle_export_recent, 0, "export-recent [days] [output]"),
]

# Build command registry (following plugins.py pattern)
_COMMAND_REGISTRY = {}
for name, short, handler, required_args, usage in _SESSION_COMMANDS:
    wrapper = _make_command_handler(name, handler, usage, required_args)
    _COMMAND_REGISTRY[name] = wrapper
    if short:
        _COMMAND_REGISTRY[short] = wrapper


# ============================================================================
# MAIN CLI (Refactored from lines 506-745 - 239 LOC → ~50 LOC)
# ============================================================================

def _print_help():
    """Print help message."""
    print("Claude Session Tools - General-purpose tool for exploring, analyzing, and exporting sessions")
    print("\nCommands:")
    for name, short, _, _, usage in _SESSION_COMMANDS:
        alias_str = f", {short}" if short else ""
        print(f"  {name}{alias_str}: {usage}")


def main():
    """Main CLI entry point.

    Refactored to use command registry instead of if/elif chain.
    Reduces main() from 239 LOC to ~50 LOC (79% reduction).
    """
    if len(sys.argv) < 2:
        _print_help()
        return

    command = sys.argv[1]
    args = sys.argv[2:]

    handler = _COMMAND_REGISTRY.get(command)
    if handler:
        result = handler(args)
        if result:
            print(result)
    else:
        print(f"Unknown command: {command}")
        _print_help()


if __name__ == '__main__':
    main()
