#!/usr/bin/env python3
"""
Session Explorer - General-purpose backend for /session-explorer slash command
Handles argument-based operations for searching, extracting, and analyzing sessions
Supports custom instructions and flexible content extraction
"""

import json
import sys
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime

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
                                            'content': content_text[:200],
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
                    results.append({'timestamp': timestamp, 'type': msg_type, 'content': str(obj)[:200]})
            except:
                pass
    return results

def search_sessions(pattern, all_projects=True):
    """Search for pattern across sessions"""
    projects_dir = Path.home() / '.claude' / 'projects'
    results = defaultdict(list)

    for project_dir in projects_dir.glob('-Users-*'):
        project_name = project_dir.name.replace('-Users-athundt-source-', '')
        for session_file in project_dir.glob('*.jsonl'):
            try:
                with open(session_file, 'r') as f:
                    for line_num, line in enumerate(f, 1):
                        if pattern.lower() in line.lower():
                            results[project_name].append({
                                'session': session_file.stem,
                                'line': line_num,
                                'preview': line[:100]
                            })
            except:
                pass

    return results

def list_project_sessions(project_name):
    """List all sessions for a project"""
    projects_dir = Path.home() / '.claude' / 'projects'
    project_dir = projects_dir / f'-Users-athundt-source-{project_name}'

    if not project_dir.exists():
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

def main():
    if len(sys.argv) < 2:
        print("Session Explorer - Use '/session-explorer' for full documentation")
        print("\nQuick commands:")
        print("  list <project>")
        print("  search <pattern>")
        print("  extract <project> <session-id> <type>")
        print("  analyze <project> <session-id>")
        print("  find-tool <tool> [pattern]")
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
        session_file = projects_dir / f'-Users-athundt-source-{project}' / f'{session_id}.jsonl'

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
                print(content[:300])
            else:
                print(str(content)[:300])
            if len(str(content)) > 300:
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
        session_file = projects_dir / f'-Users-athundt-source-{project}' / f'{session_id}.jsonl'

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

        projects_dir = Path.home() / '.claude' / 'projects'
        print(f"Searching for {tool} usage" + (f" with pattern '{pattern}'" if pattern else "") + ":\n")

        for project_dir in projects_dir.glob('-Users-*'):
            project_name = project_dir.name.replace('-Users-athundt-source-', '')
            found = 0
            for session_file in project_dir.glob('*.jsonl'):
                results = extract_tool_results(str(session_file), tool)
                if results:
                    if not found:
                        print(f"Project: {project_name}")
                        found += 1
                    for result in results[:2]:
                        print(f"  {session_file.stem}: {result.get('tool')} at line {result.get('line')}")

if __name__ == '__main__':
    main()
