#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
"""
Centralized configuration for autorun plugin.

This module provides the single source of truth for all configuration
constants, following DRY (Don't Repeat Yourself) principles.

Usage:
    from autorun.config import CONFIG
    # or
    from autorun import CONFIG
"""

# Tool name sets for different CLIs (Claude Code vs Gemini CLI) - v0.8.0
BASH_TOOLS = {"Bash", "bash_command", "run_shell_command"}
WRITE_TOOLS = {"Write", "write_file"}
EDIT_TOOLS = {"Edit", "edit_file", "replace"}
FILE_TOOLS = WRITE_TOOLS | EDIT_TOOLS
PLAN_TOOLS = {"ExitPlanMode", "exit_plan_mode"}

# Task Lifecycle Tools
TASK_CREATE_TOOLS = {"TaskCreate", "task_create", "tracker_create_task"}
TASK_UPDATE_TOOLS = {"TaskUpdate", "task_update", "tracker_update_task"}
TASK_LIST_TOOLS = {"TaskList", "task_list", "tracker_list_tasks"}
TASK_GET_TOOLS = {"TaskGet", "task_get", "tracker_get_task"}
# Gemini CLI uses "write_todos" for ALL task operations (create, update, list).
# Routing is handled in track_task_operations by inspecting tool_input.
TASK_COMBINED_TOOLS = {"write_todos"}
ALL_TASK_TOOLS = TASK_CREATE_TOOLS | TASK_UPDATE_TOOLS | TASK_LIST_TOOLS | TASK_GET_TOOLS | TASK_COMBINED_TOOLS

# Truncation limits for log/debug output (avoid magic numbers across codebase)
LOG_SNIPPET_MAX_LEN = 120     # tool results, error messages, evidence in log output
PATTERN_DISPLAY_MAX_LEN = 50  # regex/command patterns in error messages (security)


# =============================================================================
# Unified Command Integrations System v0.8.0 (superset of hookify)
# =============================================================================
# Fields:
#   action: "block" (deny) or "warn" (allow + message) - defaults to "block"
#   suggestion: Message shown to AI when command matches
#   redirect: Alternative command template ({args} = all args, {file} = last non-flag arg)
#   when: Predicate name or bash command (defaults to "always")
#   patterns: List of patterns (OR-ed) - alternative to using dict key
#   event: Event type - "bash", "file", "stop", "all" (defaults to "bash")
#   tool_matcher: Tool name(s) - "Bash", "Edit", "Write", "*" (defaults to "Bash")
#   conditions: List of hookify-style conditions (AND-ed)
#   enabled: Enable/disable integration (defaults to true)
#   name: Debug identifier (defaults to pattern)
# =============================================================================
DEFAULT_INTEGRATIONS = {
    "rm": {
        "action": "block",
        "suggestion": "Use the 'trash' CLI command instead for safe file deletion.\n\nExample:\n  Instead of: rm /path/to/file\n  Use: trash /path/to/file\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or go install github.com/andraschume/trash-cli@latest (Linux)\n\nTo allow (default 1 use): /ar:ok rm\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "trash {args}",
    },
    "rm -rf": {
        "action": "block",
        "suggestion": "Use the 'trash' CLI command instead - rm -rf is permanently destructive.\n\nExample:\n  Instead of: rm -rf /path/to/dir\n  Use: trash /path/to/dir\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or go install github.com/andraschume/trash-cli@latest (Linux)\n\nTo allow (default 1 use): /ar:ok 'rm -rf'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "trash {args}",
    },
    "git reset --hard": {
        "action": "block",
        "suggestion": "DANGEROUS: 'git reset --hard' permanently discards all uncommitted changes.\n\n**SAFER ALTERNATIVES (in order of preference):**\n\n1. **Stash changes** (RECOMMENDED - preserves work, easily recoverable):\n   git stash push -m \"WIP: brief description of changes\"\n   # Later: git stash list, git stash pop, or git stash apply\n\n2. **Create backup branch** (if stash isn't suitable):\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint before reset\"\n   git checkout -  # return to original branch\n\n3. **Selective stash** (to save specific files only):\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git status && git diff\n\nTo allow (default 1 use): /ar:ok 'git reset --hard'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: {args}'",
        "when": "_repo_differs_from_head",
    },
    "git checkout .": {
        "action": "block",
        "suggestion": "DANGEROUS: 'git checkout .' discards ALL uncommitted changes in working directory.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push -m \"WIP: saving changes before checkout\"\n\n2. **Create backup branch**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint\"\n   git checkout -\n\n3. **Selective stash** (save specific files only):\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff\n\nTo allow (default 1 use): /ar:ok 'git checkout .'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: {args}'",
        "when": "_repo_differs_from_head",
    },
    "git checkout --": {
        "action": "block",
        "suggestion": "CAUTION: 'git checkout -- <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVE:**\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow (default 1 use): /ar:ok 'git checkout --'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push {file} -m 'WIP: {file}'",
        "when": "_file_differs_from_ref",
    },
    "git checkout": {  # Catch modern syntax: git checkout path/to/file (without --)
        "action": "block",
        "suggestion": "CAUTION: 'git checkout <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push <file> -m 'WIP: <file>'\n\n2. **Switch branches** (if not targeting a file):\n   git switch <branch>  # switch branches (Git 2.23+)\n\n**View what would be lost:**\n   git diff <file>\n\nNote: 'git checkout <branch>' to switch branches is allowed when no files would be affected.\n\nTo allow (default 1 use): /ar:ok 'git checkout'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push {file} -m 'WIP: {file}'",
        "when": "_file_differs_from_ref",
    },
    "git restore": {
        "action": "block",
        "suggestion": "CAUTION: 'git restore <file>' permanently discards unstaged changes with no recovery.\n\n**SAFER ALTERNATIVE (RECOMMENDED):**\n   git stash push <file> -m 'WIP: <file>'\n\nNote: 'git restore --staged <file>' (unstage only) is safe and allowed.\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow (default 1 use): /ar:ok 'git restore'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push {file} -m 'WIP: {file}'",
        "when": "_restore_is_destructive",
    },
    "git stash drop": {
        "action": "block",
        "suggestion": "CAUTION: 'git stash drop' permanently deletes stashed changes.\n\n**SAFER ALTERNATIVES:**\n\n1. **Apply stash instead** (keeps changes):\n   git stash pop    # apply and remove from stash\n   git stash apply  # apply and keep in stash\n\n2. **View stash contents first**:\n   git stash show -p  # see what's in the stash\n\nTo allow (default 1 use): /ar:ok 'git stash drop'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash pop",
        "when": "_stash_exists",
    },
    "git clean -f": {
        "action": "block",
        "suggestion": "DANGEROUS: 'git clean -f' permanently deletes untracked files.\n\n**SAFER ALTERNATIVES:**\n\n1. **Preview first** (ALWAYS do this):\n   git clean -n   # dry-run, shows what would be deleted\n\n2. **Stash untracked files**:\n   git stash push -u -m \"WIP: stashing untracked files\"\n\n3. **Move to backup** (manual safety):\n   mkdir -p ../backup-untracked && git clean -n | xargs -I{} mv {} ../backup-untracked/\n\n4. **Interactive mode**:\n   git clean -i  # prompts for each file\n\nTo allow (default 1 use): /ar:ok 'git clean -f'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git clean -n",
    },
    "git reset HEAD~": {
        "action": "block",
        "suggestion": "CAUTION: 'git reset HEAD~' undoes commits (mixed reset by default).\n\n**SAFER ALTERNATIVES:**\n\n1. **Soft reset** (keeps all changes staged):\n   git reset --soft HEAD~1\n\n2. **Create backup branch first**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-before-reset\n   git checkout -\n   git reset HEAD~1\n\n3. **Revert instead** (creates new commit, preserves history):\n   git revert HEAD\n\n**Recovery if you already reset:**\n   git reflog  # find the commit hash\n   git reset --hard <hash>  # restore to that point\n\nTo allow (default 1 use): /ar:ok 'git reset HEAD~'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "git add -A": {
        "action": "block",
        "suggestion": "CAUTION: 'git add -A' stages ALL changes including untracked files, which may accidentally include sensitive files (.env, credentials) or large binaries.\n\n**SAFER ALTERNATIVE:**\n   git add <file1> <file2> ...  # stage specific files by name\n\n**Preview what would be staged:**\n   git status  # review untracked and modified files first\n\nTo allow (default 1 use): /ar:ok 'git add -A'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "git add .": {
        "action": "block",
        "suggestion": "CAUTION: 'git add .' stages ALL changes in the current directory, which may accidentally include sensitive files (.env, credentials) or large binaries.\n\n**SAFER ALTERNATIVE:**\n   git add <file1> <file2> ...  # stage specific files by name\n\n**Preview what would be staged:**\n   git status  # review untracked and modified files first\n\nTo allow (default 1 use): /ar:ok 'git add .'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "dd if=": {
        "action": "block",
        "suggestion": "Avoid direct disk writes - use proper backup tools. Consider rsync, ddrescue, or backup utilities instead.\n\nTo allow (default 1 use): /ar:ok 'dd if='\nScope: [N|5m|permanent] (default 1 use)",
    },
    "mkfs": {
        "action": "block",
        "suggestion": "Filesystem creation is dangerous - backup data first and use partition managers like GNOME Disks or gparted.\n\nTo allow (default 1 use): /ar:ok mkfs\nScope: [N|5m|permanent] (default 1 use)",
    },
    "fdisk": {
        "action": "block",
        "suggestion": "Partition modification is dangerous - backup data first. Use GUI tools like GNOME Disks or gparted for safer operations.\n\nTo allow (default 1 use): /ar:ok fdisk\nScope: [N|5m|permanent] (default 1 use)",
    },
    # Command-line tools that should use dedicated AI tools instead (v0.8.0)
    # These block the BASH COMMAND (e.g. "grep") and suggest the AI TOOL (e.g. {grep}).
    # These are distinct namespaces — bash "grep" ≠ AI tool "Grep"/"grep_search".
    #
    # Suggestion strings use {tool_key} format variables resolved by format_suggestion()
    # in core.py to the correct API tool_name for the active CLI:
    #
    #   Claude Code CLI v2.1.47  — PascalCase API names (Grep, Glob, Read, Write, Edit)
    #                               Terminal renders Glob→"Search" but API name is "Glob"
    #   Gemini CLI               — snake_case API names (grep_search, glob, read_file, ...)
    #                               Confirmed by hooks.json BeforeTool matcher:
    #                               "write_file|run_shell_command|replace|read_file|glob|grep_search"
    "sed": {
        "action": "block",
        "suggestion": "Use the {edit} tool instead of sed for file modifications.\n\n**Why:**\n- {edit} tool is safer (validates exact string matches)\n- Better error messages\n- Integrates with your AI coding assistant's file tracking\n\n**Example:**\nInstead of: sed -i 's/old/new/g' file.txt\nUse: {edit} tool with old_string='old' and new_string='new'\n\n**Commands:**\n- Allow (default 1 use): /ar:ok sed\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno sed",
    },
    "awk": {
        "action": "block",
        "suggestion": "Use Python or the {read} tool instead of awk for text processing.\n\n**Why:**\n- {read} tool loads file contents directly\n- Python provides more robust text processing\n- Better error handling and debugging\n\n**Example:**\nInstead of: awk '{print $1}' file.txt\nUse: {read} tool + Python string processing\n\n**Commands:**\n- Allow (default 1 use): /ar:ok awk\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno awk",
    },
    "grep": {
        "action": "block",
        "suggestion": "Command blocked: grep\nUse the {grep} tool instead of bash grep command.\n\n**Why:**\n- {grep} tool is optimized for your AI coding assistant\n- Better output formatting and context\n- Supports multiple output modes (content, files, count)\n- Built-in ripgrep integration\n\n**Example:**\nInstead of: grep -r 'pattern' .\nUse: {grep} tool with pattern='pattern'\n\n**Note:** grep in pipes IS allowed (e.g., `ps aux | grep python`, `git log | grep fix`)\n\n**Commands:**\n- Allow (default 1 use): /ar:ok grep\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno grep",
        "when": "_not_in_pipe",
    },
    "find": {
        "action": "block",
        "suggestion": "Use the {glob} tool instead of find command.\n\n**Why:**\n- {glob} tool is faster for file pattern matching\n- Works with any codebase size\n- Simpler glob syntax vs find expressions\n- Returns results sorted by modification time\n\n**Example:**\nInstead of: find . -name '*.py'\nUse: {glob} tool with pattern='**/*.py'\n\nInstead of: find . -type f -name '*test*'\nUse: {glob} tool with pattern='**/*test*'\n\n**Note:** find in pipes IS allowed (e.g., `find . -name '*.py' | head -10`)\n\n**Commands:**\n- Allow (default 1 use): /ar:ok find\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno find",
        "when": "_not_in_pipe",
    },
    "cat": {
        "action": "block",
        "suggestion": "Command blocked: cat\nUse the {read} tool instead of cat command.\n\n**Why:**\n- {read} tool handles large files better (pagination with offset/limit)\n- Shows line numbers automatically (cat -n format)\n- Better error handling for binary files\n- Can read images, PDFs, and Jupyter notebooks\n\n**Example:**\nInstead of: cat file.txt\nUse: {read} tool with file_path='file.txt'\n\nInstead of: cat file.txt | head -20\nUse: {read} tool with file_path='file.txt' and limit=20\n\n**Note:** cat in pipes IS allowed (e.g., `cat file.txt | grep pattern`)\n\n**Commands:**\n- Allow (default 1 use): /ar:ok cat\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno cat",
        "when": "_not_in_pipe",
    },
    "head": {
        "action": "block",
        "suggestion": "Command blocked: head\nUse the {read} tool with limit parameter instead of head.\n\n**Why:**\n- {read} tool shows line numbers\n- Better error handling\n- More flexible (can combine with offset)\n\n**Example:**\nInstead of: head -20 file.txt\nUse: {read} tool with file_path='file.txt' and limit=20\n\n**Note:** head in pipes IS allowed (e.g., `git diff | head -50`, `ls -la | head -20`)\n\n**Commands:**\n- Allow (default 1 use): /ar:ok head\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno head",
        "when": "_not_in_pipe",
    },
    "tail": {
        "action": "block",
        "suggestion": "Command blocked: tail\nUse the {read} tool with offset parameter instead of tail.\n\n**Why:**\n- {read} tool shows line numbers\n- Better error handling\n- Can specify exact line range\n\n**Example:**\nInstead of: tail -20 file.txt\nUse: {read} tool - first get total lines, then read with offset\n\n**Note:** tail in pipes IS allowed (e.g., `git log | tail -20`, `cargo test 2>&1 | tail -100`)\n\n**Commands:**\n- Allow (default 1 use): /ar:ok tail\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno tail",
        "when": "_not_in_pipe",
    },
    "echo >": {
        "action": "block",
        "suggestion": "Use the {write} tool instead of echo redirection.\n\n**Why:**\n- {write} tool validates file paths\n- Better error handling\n- Integrates with your AI coding assistant's file tracking\n- Prevents accidental overwrites\n\n**Example:**\nInstead of: echo 'content' > file.txt\nUse: {write} tool with content='content' and file_path='file.txt'\n\n**Commands:**\n- Allow (default 1 use): /ar:ok 'echo >'\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:globalno 'echo >'",
    },
    # Git history rewriting tools — permanently alter commit history (v0.10)
    # These require explicit /ar:ok permission since history rewriting is irreversible
    # and affects all collaborators when pushed.
    "git filter-repo": {
        "action": "block",
        "suggestion": "BLOCKED: 'git filter-repo' permanently rewrites repository history. User permission required.\n\nAll commit hashes change — collaborators must re-clone after rewrite.\n\nBackup first: git clone --mirror . ../backup-$(date +%Y%m%d).git\n\nTo allow (default 1 use): /ar:ok 'git filter-repo'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "git filter-branch": {
        "action": "block",
        "suggestion": "BLOCKED: 'git filter-branch' is deprecated. Use git-filter-repo instead:\n  pip install git-filter-repo\n\ngit filter-branch is slow, error-prone, and creates backup refs.\n\nTo allow (default 1 use): /ar:ok 'git filter-branch'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "bfg": {
        "action": "block",
        "suggestion": "BLOCKED: BFG Repo-Cleaner permanently rewrites git history.\n\nConsider git-filter-repo instead (Python, no Java dependency):\n  pip install git-filter-repo\n\nAll collaborators must re-clone after any history rewrite.\n\nTo allow (default 1 use): /ar:ok bfg\nScope: [N|5m|permanent] (default 1 use)",
    },
    "git rebase -i": {
        "action": "block",
        "suggestion": "BLOCKED: 'git rebase -i' rewrites commit history and requires an interactive terminal.\n\nAlternatives: git commit --fixup <hash>, git rebase main (non-interactive)\n\nTo allow (default 1 use): /ar:ok 'git rebase -i'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "git rebase --interactive": {
        "action": "block",
        "suggestion": "BLOCKED: 'git rebase --interactive' rewrites commit history. See 'git rebase -i' for alternatives.\n\nTo allow (default 1 use): /ar:ok 'git rebase --interactive'\nScope: [N|5m|permanent] (default 1 use)",
    },
    # Force push — more specific than generic "git push", must be defined first
    "git push --force": {
        "action": "block",
        "suggestion": "BLOCKED: 'git push --force' overwrites remote history. Use --force-with-lease instead.\n\nTo allow (default 1 use): /ar:ok 'git push --force'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "git push -f": {
        "action": "block",
        "suggestion": "BLOCKED: 'git push -f' overwrites remote history. Use --force-with-lease instead.\n\nTo allow (default 1 use): /ar:ok 'git push -f'\nScope: [N|5m|permanent] (default 1 use)",
    },
    # Remote write operations require explicit user permission
    "git push": {
        "action": "block",
        "suggestion": "Blocked: git push requires explicit user permission.\nContinue with local tasks. Ask the user when ready to push.\n\nTo allow (default 1 use): /ar:ok 'git push'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh pr create": {
        "action": "block",
        "suggestion": "Blocked: gh pr create requires explicit user permission.\nContinue with local tasks. Ask the user when ready to create PR.\n\nTo allow (default 1 use): /ar:ok 'gh pr create'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh pr merge --squash": {
        "action": "block",
        "suggestion": "BLOCKED: '--squash' destroys individual commit history by combining all commits into one.\n\nUse a regular merge to preserve commit history: gh pr merge\n\nTo allow (default 1 use): /ar:ok 'gh pr merge --squash'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh pr merge": {
        "action": "block",
        "suggestion": "BLOCKED: User permission required before merging pull requests.\n\nInform the user the PR is ready to merge and ask for permission.\n\nTo allow (default 1 use): /ar:ok 'gh pr merge'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh release create": {
        "action": "block",
        "suggestion": "Command blocked: gh release create\n\nThe user requires explicit permission before creating releases.\n\nInform the user the release is ready and ask for permission.\n\nTo allow (default 1 use): /ar:ok 'gh release create'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh repo create": {
        "action": "block",
        "suggestion": "Command blocked: gh repo create\n\nThe user requires explicit permission before creating remote repositories.\n\nAsk the user for permission before proceeding.\n\nTo allow (default 1 use): /ar:ok 'gh repo create'\nScope: [N|5m|permanent] (default 1 use)",
    },
    # GitHub edit commands — modify public/shared resources (v0.10)
    "gh issue edit": {
        "action": "block",
        "suggestion": "BLOCKED: 'gh issue edit' modifies a public GitHub issue (title, body, labels, assignees).\n\nUser permission required before editing shared resources.\n\nTo allow (default 1 use): /ar:ok 'gh issue edit'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh pr edit": {
        "action": "block",
        "suggestion": "BLOCKED: 'gh pr edit' modifies a public pull request (title, body, labels, reviewers).\n\nUser permission required before editing shared resources.\n\nTo allow (default 1 use): /ar:ok 'gh pr edit'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh repo edit": {
        "action": "block",
        "suggestion": "BLOCKED: 'gh repo edit' modifies repository settings (description, visibility, homepage).\n\nUser permission required before editing shared resources.\n\nTo allow (default 1 use): /ar:ok 'gh repo edit'\nScope: [N|5m|permanent] (default 1 use)",
    },
    # GitHub comment/create commands — post publicly visible content (v0.10)
    "gh pr comment": {
        "action": "block",
        "suggestion": "BLOCKED: 'gh pr comment' posts a publicly visible comment on a pull request.\n\nUser permission required before posting public comments.\n\nTo allow (default 1 use): /ar:ok 'gh pr comment'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh issue comment": {
        "action": "block",
        "suggestion": "BLOCKED: 'gh issue comment' posts a publicly visible comment on a GitHub issue.\n\nUser permission required before posting public comments.\n\nTo allow (default 1 use): /ar:ok 'gh issue comment'\nScope: [N|5m|permanent] (default 1 use)",
    },
    "gh issue create": {
        "action": "block",
        "suggestion": "BLOCKED: 'gh issue create' creates a new public GitHub issue.\n\nUser permission required before creating public issues.\n\nTo allow (default 1 use): /ar:ok 'gh issue create'\nScope: [N|5m|permanent] (default 1 use)",
    },
    # NEW v0.7: Warning example (action: warn = allow + message)
    "git": {
        "action": "warn",
        "suggestion": "Git commit rules: 1) Concrete terms (specific file paths, exact error messages) 2) No vague language ('improve', 'enhance', 'update') 3) Include technical details (line numbers, function names, test results) 4) Reference specific sources when making claims",
    },
}


# Configuration - Three-stage completion system with clear instruction/confirmation naming
CONFIG = {
    # ─── Stage 1: Initial Work ────────────────────────────────────────────────
    # What we inject to AI (descriptive text explaining what Stage 1 is)
    "stage1_completion": "starting tasks, analyzing user requirements, and developing comprehensive plan",
    # What AI outputs when Stage 1 complete (ALL-CAPS confirmation)
    "stage1_message": "AUTORUN_INITIAL_TASKS_COMPLETED",

    # What we inject to guide AI through Stage 1 (detailed methodology)
    "stage1_instruction": """
1. Read through ENTIRE task description carefully
2. Identify all requirements, constraints, and success criteria
3. List any ambiguities requiring clarification
4. Create task checkbox structure with concrete outcomes
5. Verify bias mitigation: not skipping steps, checking own work
6. Execute the task with full tool permissions (Bash, Edit, Write, etc.)
7. After EVERY step, say "Wait," and execute the Wait Process""",

    # ─── Stage 2: Critical Evaluation ─────────────────────────────────────────
    # What we inject to AI (descriptive text - same as output for Stage 2)
    "stage2_completion": "critically evaluating previous work and continuing tasks as needed",
    # What AI outputs when Stage 2 complete (same as completion for Stage 2)
    "stage2_message": "CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED",

    # What we inject to guide AI through Stage 2 (detailed methodology)
    "stage2_instruction": """
1. Critique work overall and line-by-line against best practices
2. Pre-mortem analysis: identify potential failure modes and weaknesses
3. Propose ≥3 concrete solutions to each identified issue
4. Synthesize insights from all critiques and solutions
5. Choose optimal solution with compelling justification
6. If errors found, execute corrective steps immediately""",

    # ─── Stage 3: Final Verification ──────────────────────────────────────────
    # What we inject to AI (compound descriptive text explaining Stage 3)
    "stage3_completion": "starting tasks, analyzing user requirements, and developing comprehensive plan AND critically evaluated own work and verified all tasks are completed",
    # What AI outputs when Stage 3 complete (ALL-CAPS confirmation)
    "stage3_message": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",

    # What we inject to guide AI through Stage 3 (detailed methodology)
    "stage3_instruction": """
1. Verify ALL requirements from original request are met
2. Confirm no tasks silently dropped or skipped
3. Double-check (AI is often overconfident)
4. Verify all file references match actual codebase
5. Confirm code examples are syntactically correct
6. If ANY requirement missing, return to relevant stage""",

    # ─── Descriptive Completion Markers ──────────────────────────────────────
    # NOTE: These are DESCRIPTIVE strings the AI outputs to communicate what it accomplished.
    # The hook system recognizes BOTH the short stage markers AND these descriptive versions.
    # Markdown command files use these descriptive strings for clarity.
    "completion_marker": "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY",

    # ─── Emergency Stop ───────────────────────────────────────────────────────
    # NOTE: This is a DESCRIPTIVE string that the AI outputs to communicate its action.
    # It should describe WHAT the AI is doing, not just be a state variable name.
    "emergency_stop": "AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP",

    # ─── Task Staleness Reminder (v0.9) ───────────────────────────────────────
    # Tool calls without TaskCreate/TaskUpdate before injecting reminder.
    "task_staleness_threshold": 25,
    # Lower threshold when zero tasks exist (AI should create tasks quickly).
    "task_staleness_no_tasks_threshold": 5,
    # Injected when threshold crossed. {threshold} replaced at runtime.
    # V4 strings: no emoji, complete tool syntax, dependency wiring, disable instruction.
    # Warn-then-deny enforcement: 2 PostToolUse levels only (1st + 2nd).
    "task_staleness_message": (
        "\nTASK UPDATE REQUIRED: {threshold} tool calls without {{task_create}} or {{task_update}}. "
        "Your next action must be one of these Task tools: "
        "1. {{task_list}}: review current tasks and their status "
        "2. {{task_update}}({{task_id_param}}=N, status=\"in_progress\"|\"completed\"): update status "
        "3. {{task_create}}({{task_title}}=\"...\", description=\"...\"): one per specific newly discovered step "
        "4. {{task_update}}({{task_id_param}}=N, addBlockedBy=[M]): update dependencies if order changed. "
        "Do not call any tool except these until you have updated your task list. "
        "Your next non-Task tool call will be blocked. Disable: /ar:tasks off"
    ),
    "task_staleness_message_2nd": (
        "\nTASK UPDATE OVERDUE: {threshold} more tool calls without a Task tool. "
        "Your next action must be one of these Task tools: "
        "1. {{task_list}}: review current tasks "
        "2. {{task_update}}({{task_id_param}}=N, status=\"in_progress\"|\"completed\"): update status "
        "3. {{task_create}}({{task_title}}=\"...\", description=\"...\"): one per specific newly discovered step. "
        "Do not call any other tool. Your next non-Task tool call will be blocked. "
        "Disable: /ar:tasks off"
    ),
    # Injected when zero tasks exist and no_tasks_threshold crossed.
    "task_staleness_no_tasks_message": (
        "\nNO TASKS EXIST: {threshold} tool calls with zero tasks tracking your work. "
        "Your next action must be {{task_create}}: "
        "1. {{task_create}}({{task_title}}=\"[step]: [action]\", description=\"...\"): one per specific step, NOT one broad task "
        "2. {{task_update}}({{task_id_param}}=N, status=\"in_progress\"): mark the task you are starting "
        "3. {{task_update}}({{task_id_param}}=N, addBlockedBy=[M]): wire dependencies if tasks have order. "
        "Do not call any other tool until you have created at least one task. "
        "Disable: /ar:tasks off"
    ),
    # Appended to stop-block injection when Stage 3 attempted with outstanding tasks.
    "task_outstanding_stage3_message": (
        "\n⚠️ STAGE 3 RESET: {count} outstanding task(s): {names}. "
        "Complete or discard them (see actions above), Stage 2 continues."
    ),

    # ─── Ghost-Task / Stale-Ref Workaround (v0.11) ────────────────────────────
    # SINGLE SOURCE OF TRUTH for the marker literal. Both the injection builder
    # (uses .format(id=…)) and the detection regex (uses .split("{id}") +
    # re.escape) derive from this one string.
    "ghost_clear_marker_template": "AUTORUN_TASKS_CLEAR_STALE_TASK({id})",

    "ghost_clear_reason": (
        "stale ref: marker emitted after "
        "ghost_clear_min_consecutive_blocks identical stop blocks"
    ),

    "ghost_clear_injection_template": (
        "\n"
        "⚠  STALE-TASK ESCAPE HATCH — this same set of ids has blocked Stop "
        "{threshold} times in a row with no tool call in between. If a task "
        "above is a stale reference (TaskList does not show it, or TaskUpdate "
        "returns \"Task not found\"), print one of the following on its own "
        "line, exactly, for each stale id you want to clear:\n"
        "{marker_lines}\n"
        "These will be detected and marked `ignored` (non-blocking). Do NOT "
        "emit them for real, in-progress tasks — only for ones Claude's own "
        "Task DB no longer knows about. Disable: /ar:tasks stale off\n"
    ),

    # ─── Plan Acceptance ───────────────────────────────────────────────────
    # v0.7: Plan approval detected via PostToolUse hook on ExitPlanMode tool
    # Legacy "PLAN ACCEPTED" text marker kept for backward compatibility with main.py
    "plan_accepted_marker": "PLAN ACCEPTED",

    # --- Plan Acceptance Notification ---
    "plan_acceptance_notify": {
        "tdd_scaffolding": True,
        "task_update_enforcement": True,
        "dependency_wiring": True,
    },
    "tdd_scaffolding_message": (
        "\nTDD SCAFFOLDING REQUIRED: you must create TDD and EXEC tasks before writing ANY implementation code: "
        "1. {{task_create}}({{task_title}}=\"[TDD] Step N: [test description]\"): one per plan step "
        "2. {{task_create}}({{task_title}}=\"[EXEC] Step N: [impl description]\"): one per plan step "
        "3. {{task_update}}({{task_id_param}}=[EXEC_ID], addBlockedBy=[[TDD_ID]]): wire each EXEC blocked by TDD "
        "4. {{task_list}}: verify all tasks visible. "
        "Do not write implementation code until TDD tasks are created and wired."
    ),
    # --- Task Creation Reminder Messages (v0.10) ---
    "plan_planning_task_reminder": (
        "\nPLANNING TASKS REQUIRED: a plan is active with no tasks tracking it. "
        "Your next action must be {{task_create}}: "
        "1. {{task_create}}({{task_title}}=\"[PLANNING] Step N: [name]\") "
        "2. {{task_update}}({{task_id_param}}=N, addBlockedBy=[N-1]): wire sequential dependencies "
        "3. {{task_list}}: verify all tasks visible. "
        "Do not call any other tool until planning tasks exist."
    ),
    "plan_execution_task_reminder": (
        "\nEXECUTION TASKS REQUIRED: plan accepted, no implementation tasks created. "
        "Your next action must be {{task_create}}: "
        "1. {{task_create}}({{task_title}}=\"[TDD] Step N: Write tests for [step]\") "
        "2. {{task_create}}({{task_title}}=\"[EXEC] Step N: [step description]\") "
        "3. Wire dependencies: each [EXEC] addBlockedBy its [TDD] task "
        "4. {{task_list}}: verify all tasks visible. "
        "Do not write code until execution tasks are created and wired."
    ),

    # ─── Bug Workarounds ──────────────────────────────────────────────────────
    # BUG #18534: Claude Code PostToolUse additionalContext broken.
    # PostToolUse hookSpecificOutput.additionalContext is documented but silently
    # dropped by Claude Code SDK. Messages sent via channel="ai" (which targets
    # only additionalContext) never reach the AI on Claude Code.
    # https://github.com/anthropics/claude-code/issues/18534
    # https://github.com/anthropics/claude-code/issues/18427
    # Workaround: respond() PATHWAY 2 in core.py internally upgrades "ai" → "both"
    # on Claude so messages also go to systemMessage (visible to user + AI same-turn).
    # On Gemini CLI, additionalContext works correctly — no workaround needed.
    # Evidence: notes/2026_03_20_task_reminder_delivery_and_compliance_investigation.md
    # Override: set as env var with same name (true|false|always|never) — env var takes precedence.
    # Set to False to disable workaround when Anthropic fixes SDK #18534.
    "AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED": True,

    # BUG #24115: Claude Code plugin loader reads hooks/ from the marketplace
    # source dir in addition to the installed cache. Any Gemini event name in
    # `plugins/autorun/hooks/*.json` fails Claude's strict Zod schema with
    # `invalid_key` errors, silently disabling ALL plugin hooks.
    # https://github.com/anthropics/claude-code/issues/24115
    # Workaround: keep plugins/autorun/hooks/ populated with Claude events only.
    # Gemini assets live under plugins/autorun/src/autorun/gemini_template/
    # (outside Claude's scan surface). The installer materializes
    # ~/.gemini/extensions/<name>/ from the template at install time.
    # Evidence: notes/2026_04_19_1627_fix_arautorun_failed_to_load_across_all_install_pathways.md
    # Override: set as env var with same name (true|false|always|never). Env var wins.
    # Set to False ONLY when #24115 is fixed AND Claude stops scanning the
    # marketplace source — then this whole split-layout refactor can be reverted
    # (keep a single hooks/hooks.json with both Claude & Gemini events separated
    # by CLI type at runtime).
    "AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED": True,

    # BUG #14449: Gemini CLI hardcodes extension hooks at
    # `<extension_root>/hooks/hooks.json`; the `hooks` field in
    # `gemini-extension.json` is ignored at runtime.
    # https://github.com/google-gemini/gemini-cli/issues/14449
    # https://github.com/google-gemini/gemini-cli/pull/14460
    # Workaround: install the Gemini extension from the template dir which has
    # the hardcoded layout (`hooks/hooks.json` at root). The installer copies
    # `hook_entry.py` into `<ext>/hooks/` so `${extensionPath}/hooks/hook_entry.py`
    # resolves at runtime (Gemini substitutes ${extensionPath} natively).
    # Evidence: google-gemini/gemini-cli#14449 confirms hardcoded path.
    # Override: set as env var with same name (true|false|always|never). Env var wins.
    # Set to False when PR #14460 ships in a Gemini CLI release AND the
    # manifest's `hooks` field is honored — then install.py can point Gemini
    # directly at the plugin root and skip the template materialization.
    "AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED": True,

    # ─── Timing ───────────────────────────────────────────────────────────────
    "max_recheck_count": 3,
    "monitor_stop_delay_seconds": 300,
    "stage3_countdown_calls": 5,

    # ─── Injection Template ───────────────────────────────────────────────────
    "injection_template": """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

**THREE-STAGE COMPLETION SYSTEM:**
This system ensures thorough, high-quality work through a structured three-stage process. Each stage builds upon the previous one, with explicit requirements for advancement.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
    * **Permissions Granted:** You have full permission to use all tools (Bash, Edit, Write, etc.) without requiring any further permission prompts.
2.  **SYSTEM STOP SIGNAL RULE:** The exact strings **{emergency_stop}**, **{stage1_message}**, **{stage2_message}**, and **{stage3_message}** are **SYSTEM STOP SIGNALS**. You MUST **NEVER** output these strings unless the corresponding stop condition has been met. Accidental output will immediately halt the entire system.
3.  **Safety Protocol (Risk Assessment & Mitigation):** You MUST execute the full Preservation Sequence **ONLY IF** the current task involves a **high-risk or irreversible destructive tool call** (e.g., initial modification, database interaction, or action following a prior failure).
    * **Assess Risk:** Implicitly evaluate the potential for irreversible state misalignment or system integrity breach.
    * **Mitigation Action (CONDITIONAL):** If a **high-risk condition is met**, you **MUST immediately execute the following Preservation Sequence** and explicitly state your actions:
        1.  **INITIATE SAFETY PROTOCOL:** Announce 'INITIATE SAFETY PROTOCOL' to begin structured assessment.
        2.  **Secure State:** Execute the recovery command(s) to create an **environment backup** or **state checkpoint** (using available systems and tools) *before* proceeding.
        3.  **Verify Integrity:** Run a quick non-destructive check to ensure the state checkpoint was successful.
        4.  **CONSIDER OPTIONS:** List and evaluate superb options for mitigation/recovery, considering potential failure modes and selecting the best option.
    * **CRITICAL ESCAPE PRE-CHECK:** If, after executing the Mitigation Action, the risk remains irreversible, proceed directly to **Step 4: CRITICAL ESCAPE TO STOP SYSTEM**.
4.  **CRITICAL ESCAPE TO STOP SYSTEM (Final Decision):** Only if the risk is irreversible, catastrophic, or cannot be fully mitigated, you **MUST initiate the Preservation Protocol** by immediately outputting the following exact string to immediately halt all actions: **{emergency_stop}**
5.  **STAGE 1 - INITIAL IMPLEMENTATION:** {stage1_instruction}
    * When Stage 1 is complete, output **{stage1_message}** to advance to Stage 2
6.  **STAGE 2 - CRITICAL EVALUATION:** {stage2_instruction}
    * When Stage 2 is complete, output **{stage2_message}** to advance to Stage 3
7.  **STAGE 3 - FINAL VERIFICATION:** {stage3_instruction}
    * Stage 3 instructions: {stage3_instructions}
    * When Stage 3 is complete, output **{stage3_message}** for final completion
8.  **FINAL OUTPUT ON SUCCESS TO STOP SYSTEM:** Only when all three stages are complete and verified, output **{stage3_message}** to stop the system
9.  **FILE CREATION POLICY:** {policy_instructions}""",

    # ─── Recheck Template ─────────────────────────────────────────────────────
    "recheck_template": """AUTORUN TASK VERIFICATION: The task appears complete but requires careful verification before final confirmation.

Original Task: {activation_prompt}

CRITICAL VERIFICATION INSTRUCTIONS:
1. Carefully review ALL aspects of the original task above
2. Verify EVERY requirement has been fully met and tested
3. Check for any incomplete, partial, or missed elements
4. Test any implemented functionality thoroughly
5. Double-check your work against the original requirements
6. Verify all files are in their correct final state
7. Ensure no temporary or incomplete work remains
{verification_requirements}

Only if you are ABSOLUTELY CERTAIN everything is complete, tested, and meets all requirements, output: {stage3_message}

If ANY aspect is incomplete, uncertain, or needs additional work, continue until truly finished.

This is verification attempt #{recheck_count} of {max_recheck_count}.""",

    # ─── Forced Compliance Template ───────────────────────────────────────────
    "forced_compliance_template": """AUTORUN FORCED COMPLIANCE OVERRIDE: System has detected prolonged verification cycles.

Original Task: {activation_prompt}

FORCED COMPLIANCE PROTOCOL ACTIVATED:
Due to extended verification duration, the system is forcing task completion with the following requirements:

{verification_requirements}

SYSTEM OVERRIDE INSTRUCTIONS:
1. Complete any remaining critical requirements immediately
2. Ensure basic functionality is implemented and working
3. Add any missing documentation or comments
4. Perform final validation and cleanup

After completing the above forced requirements, output: {stage3_message}

NOTE: This is a forced compliance override to prevent infinite verification loops.
Ensure core functionality is working before final completion.""",

    # ─── Procedural Injection Template (Wait Process Methodology) ─────────────
    "procedural_injection_template": """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals using the **Sequential Improvement Methodology**.

**WAIT PROCESS (Execute after every step and substep):**
After every step and substep you must say "Wait," and execute this sequential thinking process:

1. **Elaborate and Refine Best Practices**: Elaborate and refine best practices lists based on current context
2. **Comprehensive Critique**: Harshly and constructively critique your work overall and line by line against every single best practice and criteria
3. **Pre-mortem Analysis**: Identify potential failure modes and weaknesses
4. **Multiple Solution Generation**: Propose multiple concrete solutions to each identified issue
5. **Synthesized Solution Building**: Synthesize insights from all previous critiques and solutions
6. **Sequential Quality Enhancement**: Each proposal must be superb quality, building on previous iterations
7. **Best Solution Selection**: Choose the optimal solution from all proposals with compelling justification
8. **Error Correction Protocol**: If errors are found, immediately insert and execute corrective steps

**THREE-STAGE COMPLETION SYSTEM:**
1. **STAGE 1 - INITIAL IMPLEMENTATION:** {stage1_instruction}
   * When Stage 1 is complete, output **{stage1_message}** to advance to Stage 2
2. **STAGE 2 - CRITICAL EVALUATION:** {stage2_instruction}
   * When Stage 2 is complete, output **{stage2_message}** to advance to Stage 3
3. **STAGE 3 - FINAL VERIFICATION:** {stage3_instruction}
   * Stage 3 instructions: {stage3_instructions}
   * When Stage 3 is complete, output **{stage3_message}** for final completion

**SYSTEM STOP SIGNALS:** The exact strings **{emergency_stop}**, **{stage1_message}**, **{stage2_message}**, and **{stage3_message}** are SYSTEM STOP SIGNALS. NEVER output these unless the corresponding stop condition has been met.

**CRITICAL ESCAPE TO STOP SYSTEM:** Only if risk is irreversible, output: **{emergency_stop}**

**FILE CREATION POLICY:** {policy_instructions}""",

    # ─── Policies ─────────────────────────────────────────────────────────────
    "policies": {
        "ALLOW": ("allow-all", "ALLOW ALL: Full permission to create/modify files."),
        "JUSTIFY": ("justify-create", "JUSTIFIED: Search existing first. Include <AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> for new files."),
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Use {glob} and {grep} tools. NO new files.")
    },

    # ─── Policy Blocked Messages ──────────────────────────────────────────────
    "policy_blocked": {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search for existing files handling similar functionality using the {glob} tool with patterns like "*related-topic*", 3) Use the {grep} tool to find files with relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search for existing files using the {glob} tool and {grep} tool related to your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation."
    },

    # ─── Command Mappings ─────────────────────────────────────────────────────
    # Values must match keys in COMMAND_HANDLERS (case-sensitive)
    # Commands support /ar: prefix with short and long forms
    "command_mappings": {
        # ─── New Short Forms (/ar: prefix) ────────────────────────────────────
        "/ar:a": "ALLOW",           # Allow all file creation
        "/ar:j": "JUSTIFY",         # Justify new files
        "/ar:f": "SEARCH",          # Find existing files only
        "/ar:st": "STATUS",         # Show status
        "/ar:go": "activate",       # Start autorun
        "/ar:gp": "activate",       # Start autoproc (procedural)
        "/ar:x": "stop",            # Graceful stop
        "/ar:sos": "emergency_stop", # Emergency stop
        "/ar:tm": "tmux_session",   # Tmux session management
        "/ar:tt": "tmux_test",      # Tmux test workflow

        # ─── New Long Forms (/ar: prefix) ─────────────────────────────────────
        "/ar:allow": "ALLOW",       # Allow all file creation
        "/ar:justify": "JUSTIFY",   # Justify new files
        "/ar:find": "SEARCH",       # Find existing files only
        "/ar:status": "STATUS",     # Show status
        "/ar:run": "activate",      # Start autorun
        "/ar:proc": "activate",     # Start autoproc (procedural)
        "/ar:stop": "stop",         # Graceful stop
        "/ar:estop": "emergency_stop", # Emergency stop
        "/ar:tmux": "tmux_session", # Tmux session management
        "/ar:ttest": "tmux_test",   # Tmux test workflow (ttest to avoid collision with test.md)

        # ─── Plan Commands ─────────────────────────────────────────────────────
        "/ar:pn": "NEW_PLAN",
        "/ar:pr": "REFINE_PLAN",
        "/ar:pu": "UPDATE_PLAN",
        "/ar:pp": "PROCESS_PLAN",
        "/ar:plannew": "NEW_PLAN",
        "/ar:planrefine": "REFINE_PLAN",
        "/ar:planupdate": "UPDATE_PLAN",
        "/ar:planprocess": "PROCESS_PLAN",

        # ─── Legacy Commands (backward compatibility) ─────────────────────────
        "/autorun": "activate",
        "/autoproc": "activate",
        "/autostop": "stop",
        "/estop": "emergency_stop",
        "/afs": "SEARCH",
        "/afa": "ALLOW",
        "/afj": "JUSTIFY",
        "/afst": "STATUS",

        # ─── Command Blocking (NEW in v0.6.0) ───────────────────────────────────────
        "/ar:no": "BLOCK_PATTERN",
        "/ar:ok": "ALLOW_PATTERN",
        "/ar:clear": "CLEAR_PATTERN",
        "/ar:globalno": "GLOBAL_BLOCK_PATTERN",
        "/ar:globalok": "GLOBAL_ALLOW_PATTERN",
        "/ar:globalstatus": "GLOBAL_BLOCK_STATUS"
    },

    # Built-in command integrations (suggestions for dangerous commands)
    "default_integrations": DEFAULT_INTEGRATIONS,

    # ─── Integration Search Paths (File-based Extensions) ─────────────────────
    # User can create .md files matching these patterns to add custom integrations
    # Format: .claude/autorun.{name}.local.md (same pattern as hookify)
    "integration_search_paths": [
        ".claude/autorun.*.local.md",   # Default pattern (like hookify)
    ],
}


# =============================================================================
# CLI Detection and Bug #4669 Workaround (v0.8.0+)
# =============================================================================


# Gemini-only event names (pre-normalization)
_GEMINI_EVENTS = frozenset({"BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
                             "BeforeModel", "AfterModel", "BeforeToolSelection"})


def detect_cli_type(payload: dict = None) -> str:
    """Determine the active CLI type (Claude Code vs Gemini CLI).

    Priority:
    1. Explicit 'cli_type' parameter in payload (set by hook_entry.py --cli)
    2. Explicit 'source' parameter in payload
    3. Gemini-specific identifiers (GEMINI_SESSION_ID, sessionId, session_id)
    4. Gemini-specific event names (BeforeTool, AfterTool, etc.)
    5. Environment variables (GEMINI_SESSION_ID, GEMINI_PROJECT_DIR)
    6. Default: "claude"

    Returns:
        str: "claude" or "gemini"
    """
    import os

    # 1 & 2: Explicit parameters from payload (highest priority)
    if payload:
        explicit = payload.get("cli_type") or payload.get("source")
        if explicit in ("gemini", "claude"):
            return explicit

        # 3: Gemini-specific session identifiers
        if any(payload.get(k) for k in ("GEMINI_SESSION_ID", "sessionId", "session_id")):
            return "gemini"

        # 4: Gemini-specific event names
        if payload.get("hook_event_name") in _GEMINI_EVENTS:
            return "gemini"

        # 4b: File path hints
        if ".gemini" in str(payload.get("transcript_path", "")):
            return "gemini"

    # 5: Environment variables (fallback for direct CLI calls or initialization)
    if any(os.environ.get(k) for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI")):
        return "gemini"

    # 6: Default fallback
    return "claude"


def should_use_exit2_workaround(payload: dict = None) -> bool:
    """Check if exit-2 workaround should be applied for bug #4669.

    SINGLE FLAG CHECK for pathway selection.

    Modes (AUTORUN_EXIT2_WORKAROUND env var):
    - "auto" (default): Use workaround ONLY for Claude Code
    - "always": Force workaround for all CLIs (testing)
    - "never": Disable workaround for all CLIs (testing/future)

    Returns:
        bool: True → Pathway A (exit 2 + stderr)
              False → Pathway B (exit 0 only)

    Reference: notes/hooks_api_reference.md lines 326-440
    """
    import os
    mode = os.environ.get('AUTORUN_EXIT2_WORKAROUND', 'auto').lower()
    if mode == "always":
        return True
    if mode == "never":
        return False
    return detect_cli_type(payload) == "claude"
