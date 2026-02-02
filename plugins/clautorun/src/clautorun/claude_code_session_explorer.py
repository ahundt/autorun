#!/usr/bin/env python3
"""
Session Explorer - General-purpose backend for /cr:claude-code-session-explorer skill
Handles argument-based operations for searching, extracting, and analyzing sessions
Supports custom instructions and flexible content extraction

Operations:
- list <project>: List sessions for a project
- search <pattern>: Search sessions for pattern
- extract <project> <session-id> <type>: Extract content
- analyze <project> <session-id>: Analyze session structure
- find-tool <tool> [pattern]: Find tool usage
- corrections [project]: Find user correction patterns
- find-commands <pattern> [context]: Search for command patterns with context
- timeline <project> <session-id>: Show session timeline
"""

import json
import sys
import os
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from typing import List, Dict, Any, Optional, Iterator, Tuple
import getpass

# Configurable limits via environment variables (default values in parentheses)
PREVIEW_LIMIT = int(os.environ.get('SESSION_EXPLORER_PREVIEW_LIMIT', '100'))
CONTEXT_LIMIT = int(os.environ.get('SESSION_EXPLORER_CONTEXT_LIMIT', '500'))
DISPLAY_LIMIT = int(os.environ.get('SESSION_EXPLORER_DISPLAY_LIMIT', '150'))
EXTRACT_LIMIT = int(os.environ.get('SESSION_EXPLORER_EXTRACT_LIMIT', '300'))
TOOL_CONTENT_LIMIT = int(os.environ.get('SESSION_EXPLORER_TOOL_CONTENT_LIMIT', '200'))

# Get username for path handling
def get_username() -> str:
    """Get current username for path handling."""
    return getpass.getuser()

def extract_project_name(dir_name: str) -> str:
    """Extract human-readable project name from encoded directory name.

    Claude encodes paths like /Users/name/source/project as:
    -Users-name-source-project

    This extracts just the meaningful project part.
    """
    # Remove common prefixes dynamically
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
    """Find the project directory that matches the given project name."""
    username = get_username()

    # Try common patterns
    patterns = [
        f'-Users-{username}-source-{project_name}',
        f'-Users-{username}-{project_name}',
    ]

    for pattern in patterns:
        candidate = projects_dir / pattern
        if candidate.exists():
            return candidate

    # Fallback: search for partial match
    for project_dir in projects_dir.glob('-Users-*'):
        if project_name in project_dir.name:
            return project_dir

    return None


def iter_all_sessions(project_filter: Optional[str] = None) -> Iterator[Tuple[str, Path]]:
    """Iterate over all sessions, yielding (project_name, session_file) tuples.

    This DRY function replaces 4 repeated code blocks that iterate over all
    projects and sessions. Use this instead of manually iterating.

    Args:
        project_filter: Optional string to filter projects by name (case-insensitive substring match)

    Yields:
        Tuple of (project_name, session_file_path) for each session
    """
    projects_dir = Path.home() / '.claude' / 'projects'
    for project_dir in projects_dir.glob('-Users-*'):
        project_name = extract_project_name(project_dir.name)
        if project_filter and project_filter.lower() not in project_name.lower():
            continue
        for session_file in project_dir.glob('*.jsonl'):
            yield project_name, session_file


# Correction indicators - patterns that suggest user is correcting Claude
CORRECTION_PATTERNS = [
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
    r'\bwhat,',          # "what," indicates confusion/correction
    r'\bnono\b',         # NONO (case insensitive via re.IGNORECASE)
]

# Planning commands to search for
PLANNING_COMMANDS = [
    r'/cr:plannew\b', r'/cr:pn\b',
    r'/cr:planrefine\b', r'/cr:pr\b',
    r'/cr:planupdate\b', r'/cr:pu\b',
    r'/cr:planprocess\b', r'/cr:pp\b',
    r'/plannew\b', r'/planrefine\b', r'/planupdate\b', r'/planprocess\b',
]

def extract_tool_results(session_file, tool_type=None, pattern=None):
    """Extract tool usage and results from session"""
    results = []
    with open(session_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                obj = json.loads(line)
                if 'message' in obj and isinstance(obj['message'], dict):
                    content = obj['message'].get('content', [])
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict):
                                item_type = item.get('type')
                                if item_type == 'tool_use':
                                    name = item.get('name')
                                    if tool_type is None or tool_type.lower() == name.lower():
                                        results.append({
                                            'timestamp': obj.get('timestamp'),
                                            'type': 'tool_use',
                                            'tool': name,
                                            'input': item.get('input', {}),
                                            'line': line_num
                                        })
                                elif item_type == 'tool_result':
                                    name = item.get('name')
                                    if tool_type is None or tool_type.lower() == name.lower():
                                        content_text = item.get('content', '')
                                        results.append({
                                            'timestamp': obj.get('timestamp'),
                                            'type': 'tool_result',
                                            'tool': name,
                                            'content': content_text[:TOOL_CONTENT_LIMIT],
                                            'line': line_num
                                        })
            except:
                pass
    return results

def extract_content_type(session_file, content_type):
    """Extract specific types of content from session"""
    results = []
    with open(session_file, 'r') as f:
        for line_num, line in enumerate(f, 1):
            try:
                obj = json.loads(line)
                msg_type = obj.get('type')
                timestamp = obj.get('timestamp')

                if content_type == 'user-prompts' and msg_type == 'user':
                    message = obj.get('message', {})
                    if isinstance(message, dict):
                        content = message.get('content', '')
                        results.append({'timestamp': timestamp, 'content': content, 'type': 'user'})

                elif content_type == 'assistant-responses' and msg_type == 'assistant':
                    message = obj.get('message', {})
                    if isinstance(message, dict):
                        content = message.get('content', [])
                        if isinstance(content, list):
                            text_content = [c for c in content if isinstance(c, dict) and c.get('type') == 'text']
                            results.append({'timestamp': timestamp, 'content': text_content, 'type': 'assistant'})

                elif content_type == 'pbcopy':
                    if msg_type == 'assistant':
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
                                                    results.append({
                                                        'timestamp': timestamp,
                                                        'content': cmd[start:end],
                                                        'type': 'pbcopy'
                                                    })

                elif content_type == 'bash-output':
                    if msg_type == 'assistant':
                        message = obj.get('message', {})
                        if isinstance(message, dict):
                            content = message.get('content', [])
                            if isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict) and item.get('type') == 'tool_result':
                                        if item.get('name') == 'Bash':
                                            results.append({
                                                'timestamp': timestamp,
                                                'content': item.get('content', ''),
                                                'type': 'bash_output'
                                            })

                elif content_type == 'all':
                    results.append({'timestamp': timestamp, 'type': msg_type, 'content': str(obj)[:TOOL_CONTENT_LIMIT]})
            except:
                pass
    return results

def search_sessions(pattern, all_projects=True):
    """Search for pattern across sessions"""
    projects_dir = Path.home() / '.claude' / 'projects'
    results = defaultdict(list)

    for project_dir in projects_dir.glob('-Users-*'):
        project_name = extract_project_name(project_dir.name)
        for session_file in project_dir.glob('*.jsonl'):
            try:
                with open(session_file, 'r') as f:
                    for line_num, line in enumerate(f, 1):
                        if pattern.lower() in line.lower():
                            results[project_name].append({
                                'session': session_file.stem,
                                'line': line_num,
                                'preview': line[:PREVIEW_LIMIT]
                            })
            except:
                pass

    return results

def list_project_sessions(project_name):
    """List all sessions for a project"""
    projects_dir = Path.home() / '.claude' / 'projects'
    project_dir = find_project_dir(projects_dir, project_name)

    if project_dir is None or not project_dir.exists():
        return []

    sessions = []
    for session_file in sorted(project_dir.glob('*.jsonl'), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = session_file.stat()
        sessions.append({
            'name': session_file.stem,
            'size_kb': stat.st_size // 1024,
            'modified': stat.st_mtime
        })

    return sessions

def analyze_session(session_file):
    """Analyze session structure and content"""
    stats = {
        'total_lines': 0,
        'user_prompts': 0,
        'assistant_responses': 0,
        'tool_uses': defaultdict(int),
        'tool_results': defaultdict(int),
        'files_touched': set(),
        'timestamps': []
    }

    try:
        with open(session_file, 'r') as f:
            for line in f:
                stats['total_lines'] += 1
                try:
                    obj = json.loads(line)
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
                except:
                    pass
    except:
        pass

    return stats

def cross_reference_file(session_file, file_path):
    """Cross-reference session content with file"""
    try:
        with open(file_path, 'r') as f:
            file_content = f.read()
    except:
        return None

    analysis = {
        'file': file_path,
        'total_content_lines': len(file_content.split('\n')),
        'session_content_in_file': 0,
        'changes_detected': []
    }

    # Extract key content types from session
    user_prompts = extract_content_type(str(session_file), 'user-prompts')
    pbcopy_items = extract_content_type(str(session_file), 'pbcopy')

    # Check what's in the file
    for item in pbcopy_items:
        content = item.get('content', '')
        lines_in_file = sum(1 for line in content.split('\n') if line and line in file_content)
        if lines_in_file > 0:
            analysis['session_content_in_file'] += lines_in_file
            analysis['changes_detected'].append({
                'timestamp': item.get('timestamp'),
                'type': 'pbcopy',
                'lines_found': lines_in_file
            })

    return analysis


def extract_message_text(msg_data: Dict[str, Any]) -> str:
    """Extract text content from a message object."""
    if 'message' not in msg_data:
        # Check if this is a queue-operation with content
        if msg_data.get('type') == 'queue-operation' and 'content' in msg_data:
            return msg_data['content']
        return ''

    message = msg_data['message']

    # Handle different message content structures
    if isinstance(message.get('content'), str):
        return message['content']
    elif isinstance(message.get('content'), list):
        parts = []
        for item in message['content']:
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
        return '\n'.join(parts)
    return ''


def find_corrections(session_file: str) -> List[Dict[str, Any]]:
    """Find user correction patterns in a session file.

    Returns list of corrections with context, categorized by type:
    - regression: deleted, removed, lost
    - skip_step: forgot, missed, didn't
    - misunderstanding: wrong, incorrect, mistake
    - incomplete: also need, must also, don't forget
    """
    corrections = []
    combined_pattern = re.compile('|'.join(CORRECTION_PATTERNS), re.IGNORECASE)

    try:
        with open(session_file, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    msg = json.loads(line.strip())
                    if msg.get('type') != 'user':
                        continue

                    text = extract_message_text(msg)
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

                        corrections.append({
                            'file': session_file,
                            'line': line_num,
                            'category': category,
                            'text': text[:EXTRACT_LIMIT],
                            'timestamp': msg.get('timestamp')
                        })
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        pass

    return corrections


def search_command_patterns(session_file: str, patterns: List[str], context_after: int = 5) -> List[Dict[str, Any]]:
    """Search for command patterns and extract context.

    Args:
        session_file: Path to session .jsonl file
        patterns: List of regex patterns to search for
        context_after: Number of messages to include after match

    Returns:
        List of matches with context messages
    """
    matches = []
    combined_pattern = re.compile('|'.join(patterns), re.IGNORECASE)

    try:
        with open(session_file, 'r', encoding='utf-8') as f:
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
                                'text': extract_message_text(ctx_msg)[:CONTEXT_LIMIT]
                            })

                    matches.append({
                        'file': session_file,
                        'line': line_num,
                        'pattern': match.group(),
                        'context': context_messages
                    })
    except Exception as e:
        pass

    return matches


def main():
    if len(sys.argv) < 2:
        print("Session Explorer - Use '/cr:claude-code-session-explorer' for full documentation")
        print("\nQuick commands:")
        print("  list <project>                          - List sessions for a project")
        print("  search <pattern>                        - Search sessions for pattern")
        print("  extract <project> <session-id> <type>   - Extract content (pbcopy, user-prompts, etc.)")
        print("  analyze <project> <session-id>          - Analyze session structure")
        print("  find-tool <tool> [pattern]              - Find tool usage")
        print("  corrections [project]                   - Find user correction patterns")
        print("  find-commands <pattern> [context]       - Search for command patterns")
        print("  planning-usage                          - Analyze planning command usage")
        return

    command = sys.argv[1]

    if command == 'search':
        if len(sys.argv) < 3:
            print("Usage: session-explorer search <pattern>")
            return
        pattern = sys.argv[2]
        results = search_sessions(pattern)

        print(f"Search results for '{pattern}':\n")
        total = 0
        for project, matches in results.items():
            print(f"Project: {project} ({len(matches)} matches)")
            for match in matches[:3]:
                print(f"  {match['session']}: Line {match['line']}")
                print(f"    {match['preview'][:70]}...")
            if len(matches) > 3:
                print(f"  ... and {len(matches) - 3} more")
            total += len(matches)
            print()
        print(f"Total: {total} matches")

    elif command == 'list':
        if len(sys.argv) < 3:
            print("Usage: session-explorer list <project>")
            return
        project = sys.argv[2]
        sessions = list_project_sessions(project)

        if not sessions:
            print(f"No sessions found for '{project}'")
            return

        print(f"Sessions for '{project}':\n")
        for session in sessions:
            print(f"  {session['name']} ({session['size_kb']}KB)")
        print(f"\nTotal: {len(sessions)} sessions")

    elif command == 'extract':
        if len(sys.argv) < 5:
            print("Usage: session-explorer extract <project> <session-id> <type>")
            print("Types: pbcopy, bash-output, user-prompts, assistant-responses, all")
            return
        project = sys.argv[2]
        session_id = sys.argv[3]
        content_type = sys.argv[4]

        projects_dir = Path.home() / '.claude' / 'projects'
        project_dir = find_project_dir(projects_dir, project)
        if project_dir is None:
            print(f"Project not found: {project}")
            return
        session_file = project_dir / f'{session_id}.jsonl'

        if not session_file.exists():
            print(f"Session not found: {session_file}")
            return

        items = extract_content_type(str(session_file), content_type)
        print(f"Extracted {len(items)} items of type '{content_type}':\n")

        for i, item in enumerate(items[:5], 1):
            print("=" * 70)
            print(f"Item #{i} [{item.get('timestamp', 'unknown')}]")
            print("=" * 70)
            content = item.get('content', '')
            if isinstance(content, str):
                print(content[:EXTRACT_LIMIT])
            else:
                print(str(content)[:EXTRACT_LIMIT])
            if len(str(content)) > EXTRACT_LIMIT:
                print(f"\n... ({len(str(content))} total characters)")
            print()

        if len(items) > 5:
            print(f"... and {len(items) - 5} more items")

    elif command == 'analyze':
        if len(sys.argv) < 4:
            print("Usage: session-explorer analyze <project> <session-id>")
            return
        project = sys.argv[2]
        session_id = sys.argv[3]

        projects_dir = Path.home() / '.claude' / 'projects'
        project_dir = find_project_dir(projects_dir, project)
        if project_dir is None:
            print(f"Project not found: {project}")
            return
        session_file = project_dir / f'{session_id}.jsonl'

        if not session_file.exists():
            print(f"Session not found")
            return

        stats = analyze_session(str(session_file))
        print(f"Session Analysis: {session_id}\n")
        print(f"Total lines: {stats['total_lines']}")
        print(f"User prompts: {stats['user_prompts']}")
        print(f"Assistant responses: {stats['assistant_responses']}")
        print(f"Files touched: {len(stats['files_touched'])}")
        print(f"\nTool usage:")
        for tool, count in stats['tool_uses'].items():
            print(f"  {tool}: {count}")

    elif command == 'find-tool':
        if len(sys.argv) < 3:
            print("Usage: session-explorer find-tool <tool> [pattern]")
            return
        tool = sys.argv[2]
        pattern = sys.argv[3] if len(sys.argv) > 3 else None

        print(f"Searching for {tool} usage" + (f" with pattern '{pattern}'" if pattern else "") + ":\n")

        # Group results by project for organized output
        project_results = defaultdict(list)
        for project_name, session_file in iter_all_sessions():
            results = extract_tool_results(str(session_file), tool)
            if results:
                for result in results[:2]:
                    project_results[project_name].append(
                        f"  {session_file.stem}: {result.get('tool')} at line {result.get('line')}"
                    )

        for project_name, lines in project_results.items():
            print(f"Project: {project_name}")
            for line in lines:
                print(line)

    elif command == 'corrections':
        # Find user correction patterns
        project_filter = sys.argv[2] if len(sys.argv) > 2 else None
        all_corrections = []

        print("Searching for user correction patterns...\n")

        for project_name, session_file in iter_all_sessions(project_filter):
            corrections = find_corrections(str(session_file))
            for c in corrections:
                c['project'] = project_name
            all_corrections.extend(corrections)

        # Group by category
        by_category = defaultdict(list)
        for c in all_corrections:
            by_category[c['category']].append(c)

        print(f"Found {len(all_corrections)} correction instances\n")

        for category in ['regression', 'skip_step', 'misunderstanding', 'incomplete', 'other']:
            items = by_category.get(category, [])
            if items:
                print(f"### {category.upper()} ({len(items)} instances)")
                for i, item in enumerate(items[:5], 1):
                    print(f"\n{i}. {item['project']} - Line {item['line']}")
                    print(f"   {item['text'][:DISPLAY_LIMIT]}...")
                if len(items) > 5:
                    print(f"   ... and {len(items) - 5} more")
                print()

    elif command == 'find-commands':
        if len(sys.argv) < 3:
            print("Usage: session-explorer find-commands <pattern> [context-lines]")
            print("Example: find-commands '/cr:plan' 3")
            return

        pattern = sys.argv[2]
        context = int(sys.argv[3]) if len(sys.argv) > 3 else 5
        all_matches = []

        print(f"Searching for pattern: {pattern}\n")

        for project_name, session_file in iter_all_sessions():
            matches = search_command_patterns(str(session_file), [pattern], context)
            for m in matches:
                m['project'] = project_name
            all_matches.extend(matches)

        print(f"Found {len(all_matches)} matches\n")

        for i, match in enumerate(all_matches[:10], 1):
            print(f"### Match {i}: {match['project']} (line {match['line']})")
            print(f"Pattern: {match['pattern']}")
            for ctx in match['context'][:3]:
                print(f"  [{ctx['type']}] {ctx['text'][:PREVIEW_LIMIT]}...")
            print()

        if len(all_matches) > 10:
            print(f"... and {len(all_matches) - 10} more matches")

    elif command == 'planning-usage':
        # Analyze planning command usage across sessions
        all_matches = []

        print("Analyzing planning command usage...\n")

        for project_name, session_file in iter_all_sessions():
            matches = search_command_patterns(str(session_file), PLANNING_COMMANDS, 5)
            for m in matches:
                m['project'] = project_name
            all_matches.extend(matches)

        # Count by pattern
        pattern_counts = defaultdict(int)
        project_counts = defaultdict(int)
        for m in all_matches:
            pattern_counts[m['pattern']] += 1
            project_counts[m['project']] += 1

        print(f"Total planning command invocations: {len(all_matches)}")
        print(f"Across {len(project_counts)} projects\n")

        print("Command frequency:")
        for pattern, count in sorted(pattern_counts.items(), key=lambda x: x[1], reverse=True):
            print(f"  {pattern}: {count}")

        print("\nTop projects by usage:")
        for project, count in sorted(project_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
            print(f"  {project}: {count}")

    else:
        print(f"Unknown command: {command}")
        print("Run without arguments for help")

if __name__ == '__main__':
    main()
