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

# Tool name sets for different CLIs (Claude Code, Gemini CLI, Codex CLI,
# OpenCode). OpenCode's model-facing tool ids are lowercase; its plugin
# forwards them verbatim in PreToolUse frames.
BASH_TOOLS = {
    "Bash",
    "bash",
    "bash_command",
    "run_shell_command",
    "run_command",
    "exec_command",
    "functions.exec_command",
    "shell",
}
WRITE_TOOLS = {"Write", "write_file", "write_to_file", "write"}
EDIT_TOOLS = {
    "Edit",
    "edit_file",
    "replace",
    "replace_file_content",
    "multi_replace_file_content",
    "edit",
}
FILE_TOOLS = WRITE_TOOLS | EDIT_TOOLS
PLAN_TOOLS = {"ExitPlanMode", "exit_plan_mode"}


# Truncation limits for log/debug output (avoid magic numbers across codebase)
LOG_SNIPPET_MAX_LEN = 120  # tool results, error messages, evidence in log output
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
#   platform_overrides: Per-cli replacements for action, suggestion, redirect
# =============================================================================
def _codex_suggestion(command: str, body: str) -> str:
    """Codex block text: what to do instead, then the allow/block commands.

    Codex has a shell and apply_patch, no Read/Write/Edit tool with named
    parameters, so the Claude texts below cannot be reworded by {tool} slots.
    """
    return (
        f"Command blocked: {command}\n{body}\n\n"
        "**Commands:**\n"
        f"- Allow (default 1 use): /ar:ok {command}\n"
        "Scope: [N|5m|permanent] (default 1 use)\n"
        f"- Block globally: /ar:no --global {command}"
    )


_CODEX_PLATFORM_SUGGESTIONS = {
    "awk": _codex_suggestion(
        "awk",
        "Use `rg -n '<pattern>' <file>` to find lines, `sed -n '<start>,<end>p' <file>` "
        "to print a range, or a short Python script to split fields.",
    ),
    "sed": _codex_suggestion(
        "sed", "Use apply_patch to change files; `sed -n` that only prints is allowed."
    ),
}


# Detects a package-version assignment in edited file content (used by the
# version-bump consent guard's `file` integrations). Matches the version FIELD
# being set — not a dependency's version constraint. Covers:
#   TOML/cfg/gradle/py:  version = "1.2.3" | version = 1.2.3 | __version__ = "1.2.3"
#   YAML (Chart/pubspec): version: 1.2.3
#   JSON (package.json):  "version": "1.2.3"
#   Maven (pom.xml):      <version>1.2.3</version>
# Anchored at line start so `tokio = { version = "1.40" }`, `min_version`, and
# `api_version` do NOT match (verified). `(?m)` makes ^ match each line.
_VERSION_ASSIGNMENT_RE = (
    r'(?m)^[ \t]*(?:__)?version(?:__)?[ \t]*[:=][ \t]*["\']?\d+\.\d+'
    r'|"version"[ \t]*:[ \t]*"\d+\.\d+'
    r"|<version>\s*\d+\.\d+"
)

# One-shot `/ar:ok` regex that bypasses EVERY version-bump integration in a
# single grant. TIER 1 allows are matched (via _match) against `cmd`, which is
# the shell command for bash events and the file_path for file events — so the
# allow pattern must cover BOTH. `version` (substring) catches `npm version` /
# `cargo set-version`; the manifest alternatives catch the file_path of edits.
# NOTE: a bare literal `/ar:ok 'version'` does NOT work (`version` is not a whole
# word in `set-version` and never appears in a manifest path), and the `regex:`
# prefix must be UNQUOTED — `/ar:ok 'regex:...'` is parsed as a literal whose
# text includes "regex:" (the quote defeats prefix detection). Verified in tests.
_VERSION_BUMP_ALLOW_REGEX = r"version|Cargo.toml|package.json|pyproject.toml"

# Shared message tail for the version-bump consent guard. Tells the AI to keep
# working (autonomous runs are redirected, not halted) and exactly how the user
# can grant permission with autorun's /ar:ok scoped-permission command.
_VERSION_BUMP_ALLOW_HINT = (
    "\n\nKeep going on your other tasks — do NOT stop or get stuck on this. "
    "When a release is actually needed, tell the user the change is ready and "
    "ask which part to bump (major/minor/patch).\n\n"
    "If the user consents, grant permission with /ar:ok, e.g.:\n"
    "  /ar:ok 'cargo set-version'   — allow that exact command (quote commands with spaces)\n"
    "  /ar:ok 'Cargo.toml'          — allow edits to that manifest\n"
    "  /ar:ok regex:" + _VERSION_BUMP_ALLOW_REGEX + " perm   — allow ALL version "
    "bumps this session (leave the regex UNQUOTED)\n"
    "Add a scope: N (uses) | 5m (time window) | permanent. Use /ar:ok --global "
    "to apply the grant to every session.\n"
    "Disable the guard entirely: set AUTORUN_VERSION_BUMP_GUARD_ENABLED=false and "
    "restart the daemon (autorun --restart-daemon)."
)

# One-shot `/ar:ok` regex that bypasses every publish integration in one grant.
# `publish` (substring) covers npm/cargo/poetry/uv/hatch/flit/gradle publish;
# `upload` covers twine; the rest are explicit so `git push` (its own gate) is
# NOT caught. Verified in tests.
_PUBLISH_ALLOW_REGEX = r"publish|upload|gem push|docker push|nuget push|mvn deploy"

_PUBLISH_ALLOW_HINT = (
    "\n\nKeep going on your other tasks — do NOT stop or get stuck on this. "
    "Publishing is public and usually irreversible (a released version cannot be "
    "unpublished). When a release is actually wanted, tell the user it is ready "
    "and ask them to confirm.\n\n"
    "If the user consents, grant permission with /ar:ok, e.g.:\n"
    "  /ar:ok 'cargo publish'   — allow that exact command (quote commands with spaces)\n"
    "  /ar:ok regex:" + _PUBLISH_ALLOW_REGEX + " perm   — allow ALL publishes this "
    "session (leave the regex UNQUOTED)\n"
    "Add a scope: N (uses) | 5m (time window) | permanent. Use /ar:ok --global "
    "to apply the grant to every session.\n"
    "Disable the guard entirely: set AUTORUN_PUBLISH_GUARD_ENABLED=false and "
    "restart the daemon (autorun --restart-daemon)."
)

MESSAGE_DEDUP_DEFAULT_WINDOW_SECONDS = 3.0
MESSAGE_DEDUP_DEFAULT_ENTRY_CAP = 128

#: The instant the hook wrapper will stop waiting, as epoch seconds. The
#: wrapper starts counting before spawning the CLI, so a process cannot derive
#: this locally without overrunning by its own startup cost.
#: ``hooks/hook_entry.py`` declares the same name separately and deliberately:
#: it is stdlib-only so that a missing dependency surfaces as an ImportError it
#: can bootstrap from, which means it cannot import this module.
HOOK_DEADLINE_ENV_VAR = "AUTORUN_HOOK_DEADLINE_EPOCH"

#: JSON field carrying the sender's deadline into the long-lived daemon, as
#: epoch seconds. Senders are Python (hook_entry.py, client.py) and Node (the
#: JS bridge); see monotonic_deadline for why the wire is not monotonic.
HOOK_DEADLINE_PAYLOAD_KEY = "_autorun_hook_deadline_epoch"

#: Beyond this, a deadline is a stale value inherited from an unrelated process
#: rather than ours. The largest configured wrapper timeout is 5s.
MAX_PLAUSIBLE_WRAPPER_SECONDS = 60.0


def monotonic_deadline(epoch_seconds) -> float | None:
    """A deadline received from another process, on this process's monotonic clock.

    Deadlines cross processes as epoch seconds and are converted on arrival,
    the way gRPC converts at each hop (https://grpc.io/docs/guides/deadlines/).
    A monotonic instant is not portable: Node's ``process.hrtime`` reads
    ``mach_continuous_time`` on macOS and Python's ``time.monotonic`` reads
    ``mach_absolute_time``, which drift apart by every sleep since boot. Every
    process on one host reads the same wall clock, so only a clock step during
    the milliseconds in transit can skew the result, and every reader also
    bounds its wait by its own budget.

    None for anything unusable, and for a value more than
    MAX_PLAUSIBLE_WRAPPER_SECONDS from now either way: a stale variable
    inherited from an unrelated process, or a monotonic instant from an older
    sender, must not set the budget. A recent past value is returned as is,
    already expired.
    """
    import math
    import time

    if isinstance(epoch_seconds, bool):
        return None
    try:
        epoch = float(epoch_seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(epoch):
        return None
    remaining = epoch - time.time()
    if abs(remaining) > MAX_PLAUSIBLE_WRAPPER_SECONDS:
        return None
    return time.monotonic() + remaining
SCOPED_ALLOW_DEFAULT_GRACE_SECONDS = 1.0
CODEX_TRANSCRIPT_ALLOW_GRACE_SECONDS = 5.0
TASK_PAUSE_DEFAULT_TTL_SECONDS = 5 * 60
TASK_PAUSE_GENERATION_TOKEN_BYTES = 16


DEFAULT_INTEGRATIONS = {
    "rm": {
        "action": "block",
        "suggestion": "Use the 'trash' CLI command instead for safe file deletion.\n\nExample:\n  Instead of: rm /path/to/file\n  Use: trash /path/to/file\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or uv tool install trash-cli (Linux)\n\nTo allow (default 1 use): /ar:ok rm\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "trash {file_args}",
    },
    "rm -rf": {
        "action": "block",
        "suggestion": "Use the 'trash' CLI command instead - rm -rf is permanently destructive.\n\nExample:\n  Instead of: rm -rf /path/to/dir\n  Use: trash /path/to/dir\n\nThe 'trash' command safely moves files to the trash instead of permanently deleting them.\n\nInstall: brew install trash (macOS) or uv tool install trash-cli (Linux)\n\nTo allow (default 1 use): /ar:ok 'rm -rf'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "trash {file_args}",
    },
    "git reset --hard": {
        "action": "block",
        "suggestion": "DANGEROUS: 'git reset --hard' permanently discards all uncommitted changes.\n\n**SAFER ALTERNATIVES (in order of preference):**\n\n1. **Stash changes** (RECOMMENDED - preserves work, easily recoverable):\n   git stash push -m \"WIP: brief description of changes\"\n   # Later: git stash list, git stash pop, or git stash apply\n\n2. **Create backup branch** (if stash isn't suitable):\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint before reset\"\n   git checkout -  # return to original branch\n\n3. **Selective stash** (to save specific files only):\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git status && git diff\n\nTo allow (default 1 use): /ar:ok 'git reset --hard'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: saved instead of discarding'",
    },
    "git checkout .": {
        "action": "block",
        "suggestion": "DANGEROUS: 'git checkout .' discards ALL uncommitted changes in working directory.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push -m \"WIP: saving changes before checkout\"\n\n2. **Create backup branch**:\n   git checkout -b backup/$(date +%Y%m%d-%H%M)-wip\n   git add -A && git commit -m \"WIP: checkpoint\"\n   git checkout -\n\n3. **Selective stash** (save specific files only):\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff\n\nTo allow (default 1 use): /ar:ok 'git checkout .'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: saved instead of discarding'",
        "when": "_repo_differs_from_head",
    },
    "git checkout --": {
        "action": "block",
        "suggestion": "CAUTION: 'git checkout -- <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVE:**\n   git stash push <file> -m 'WIP: <file>'\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow (default 1 use): /ar:ok 'git checkout --'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: saved instead of discarding' -- {file}",
        "when": "_file_differs_from_ref",
    },
    "git checkout": {  # Catch modern syntax: git checkout path/to/file (without --)
        "action": "block",
        "suggestion": "CAUTION: 'git checkout <file>' discards unstaged changes to specific file.\n\n**SAFER ALTERNATIVES:**\n\n1. **Stash changes** (RECOMMENDED):\n   git stash push <file> -m 'WIP: <file>'\n\n2. **Switch branches** (if not targeting a file):\n   git switch <branch>  # switch branches (Git 2.23+)\n\n**View what would be lost:**\n   git diff <file>\n\nNote: 'git checkout <name>' cannot be told apart from a path without guessing, so it is blocked while the repo has uncommitted changes. Creating a branch ('git checkout -b <new-branch>') and 'git switch <branch>' are always allowed.\n\nTo allow (default 1 use): /ar:ok 'git checkout'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: saved instead of discarding' -- {file}",
        "when": "_file_differs_from_ref",
    },
    "git restore": {
        "action": "block",
        "suggestion": "CAUTION: 'git restore <file>' permanently discards unstaged changes with no recovery.\n\n**SAFER ALTERNATIVE (RECOMMENDED):**\n   git stash push <file> -m 'WIP: <file>'\n\nNote: 'git restore --staged <file>' (unstage only) is safe and allowed.\n\n**View what would be lost:**\n   git diff <file>\n\nTo allow (default 1 use): /ar:ok 'git restore'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash push -m 'WIP: saved instead of discarding' -- {file}",
        "when": "_restore_is_destructive",
    },
    "git stash drop": {
        "action": "block",
        "suggestion": "CAUTION: 'git stash drop' permanently deletes stashed changes.\n\n**SAFER ALTERNATIVES:**\n\n1. **Apply stash instead** (keeps changes):\n   git stash pop    # apply and remove from stash\n   git stash apply  # apply and keep in stash\n\n2. **View stash contents first**:\n   git stash show -p  # see what's in the stash\n\nTo allow (default 1 use): /ar:ok 'git stash drop'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash pop",
        "when": "_stash_exists",
    },
    "git stash clear": {
        "action": "block",
        "suggestion": "DANGEROUS: 'git stash clear' permanently deletes ALL stashed changes at once.\n\n**SAFER ALTERNATIVES:**\n\n1. **Apply the stashes you still need** (keeps changes):\n   git stash list   # see every stash\n   git stash apply stash@{N}\n\n2. **View stash contents first**:\n   git stash show -p stash@{N}\n\nTo allow (default 1 use): /ar:ok 'git stash clear'\nScope: [N|5m|permanent] (default 1 use)",
        "redirect": "git stash list",
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
    "time-machine-safety": {
        "patterns": ["tmutil"],
        "action": "block",
        "when": "_tmutil_mutates_backup_safety",
        "suggestion": (
            "Time Machine history and protection changes require explicit user permission. "
            "This command can permanently remove backup history or weaken backup protection; "
            "tool-host or automatic approval is not user consent.\n\n"
            "Inspect first with read-only commands such as:\n"
            "  tmutil listlocalsnapshots /\n"
            "  tmutil listbackups\n"
            "  tmutil destinationinfo\n\n"
            "Ask the user to approve the exact operation. After approval, allow only that "
            "operation (default 1 use):\n"
            "  /ar:ok 'tmutil thinlocalsnapshots'\n"
            "  Codex: ar:ok 'tmutil thinlocalsnapshots'\n"
            "Scope: [N|5m|permanent] (default 1 use)"
        ),
    },
    # `curl ... | sh`, `bash <(curl ...)`, `bash -c "$(curl ...)"`: the script
    # never passes through the command parser, so no guard sees what it runs.
    # Scripts autorun can read (`bash <<EOF`, `echo ls | sh`) are parsed instead.
    # Patterns: command_detection.SHELL_EXEC_COMMANDS, spelled out because
    # config.py imports only the stdlib (a test pins the two equal).
    "shell-runs-downloaded-script": {
        "patterns": ["bash", "dash", "fish", "ksh", "sh", "su", "zsh"],
        "action": "block",
        "when": "_shell_runs_downloaded_script",
        "suggestion": (
            "Blocked: this shell would run a script straight from a network download "
            "(curl or wget). autorun checks each command before it runs, but it cannot "
            "read the commands inside a script it never sees, so none of its guards can "
            "check what this script does.\n\n"
            "**Instead:** download the file, read it, then run it:\n"
            "  curl -fsSL -o /tmp/install.sh <url>\n"
            "  # read /tmp/install.sh first ({read})\n"
            "  sh /tmp/install.sh\n\n"
            "To allow (default 1 use): /ar:ok <shell>, the shell the download is fed to, e.g. /ar:ok sh or /ar:ok bash\n"
            "Scope: [N|5m|permanent] (default 1 use)"
        ),
    },
    # Command-line tools that should use platform-native AI tools or shell
    # affordances instead (v0.8.0)
    # These block the BASH COMMAND (e.g. "sed -i") and suggest the platform-native
    # route (e.g. {edit}). These are distinct namespaces: bash "sed" is not
    # Claude "Edit", Gemini "replace", or Codex "apply_patch".
    #
    # Suggestion strings use {tool_key} format variables resolved by format_suggestion()
    # in core.py to the correct model-facing guidance for the active CLI:
    #
    #   Claude Code CLI v2.1.47  — PascalCase API names (Grep, Glob, Read, Write, Edit)
    #                               Terminal renders Glob→"Search" but API name is "Glob"
    #   Gemini CLI               — snake_case API names (grep_search, glob, read_file, ...)
    #                               Confirmed by hooks.json BeforeTool matcher:
    #                               "write_file|run_shell_command|replace|read_file|glob|grep_search"
    #   Codex CLI                — shell search/read paths plus apply_patch/update_plan
    #                               where no Claude-style model tool exists
    "sed": {
        "action": "block",
        "platform_overrides": {"codex": {"suggestion": _CODEX_PLATFORM_SUGGESTIONS["sed"]}},
        "suggestion": "Use the {edit} tool instead of sed for file modifications.\n\n**Why:**\n- {edit} tool is safer (validates exact string matches)\n- Better error messages\n- Integrates with your AI coding assistant's file tracking\n\n**Example:**\nInstead of: sed -i 's/old/new/g' file.txt\nUse: {edit} tool with old_string='old' and new_string='new'\n\n**Commands:**\n- Allow (default 1 use): /ar:ok sed\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:no --global sed",
        "when": "_sed_modifies_files",
    },
    "awk": {
        "action": "block",
        "platform_overrides": {"codex": {"suggestion": _CODEX_PLATFORM_SUGGESTIONS["awk"]}},
        # A filter on a pipe is allowed; one that edits,
        # writes, runs a command, reads a file or runs as root is not.
        "when": "_awk_does_more_than_filter_stdin",
        "suggestion": "Use Python or the {read} tool instead of awk for text processing.\n\n**Why:**\n- {read} tool loads file contents directly\n- Python provides more robust text processing\n- Better error handling and debugging\n\n**Example:**\nInstead of: awk '{print $1}' file.txt\nUse: {read} tool + Python string processing\n\n**Commands:**\n- Allow (default 1 use): /ar:ok awk\nScope: [N|5m|permanent] (default 1 use)\n- Block globally: /ar:no --global awk",
    },
    # Listing files with find is allowed, alone or in a pipe; its actions that
    # delete (-delete) or truncate a named file (-fprint, -fprint0, -fprintf,
    # -fls) are not. `find -exec rm` is the rm guard's.
    "find": {
        "action": "block",
        "when": "_find_deletes_or_overwrites",
        "suggestion": (
            "Blocked: this find deletes files (-delete) or overwrites the file it names "
            "(-fprint, -fprint0, -fprintf, -fls), and neither can be undone.\n\n"
            "**Instead:** list what matches first with the same expression, then move it to the trash:\n"
            "  find . -name '*.tmp'                       # check the list\n"
            "  find . -name '*.tmp' -exec trash {} +      # recoverable delete\n"
            "To save find's output, redirect to a new file: find . -name '*.tmp' > list.txt\n\n"
            "To allow (default 1 use): /ar:ok find\n"
            "Scope: [N|5m|permanent] (default 1 use)"
        ),
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
        "dedup_category": "integration_warning",
        "suggestion": "Git commit rules: 1) Concrete terms (specific file paths, exact error messages) 2) No vague language ('improve', 'enhance', 'update') 3) Include technical details (line numbers, function names, test results) 4) Reference specific sources when making claims 5) No transient internal AI session details (plan phases, task IDs, CI run numbers, session references)",
    },
    # ─── Version bumps require explicit user consent ──────────────────────────
    # Cross-backend (claude/codex/gemini). One bash guard for version-bump CLIs
    # plus two file guards for direct manifest edits (Edit vs Write route via the
    # condition field: new_string vs content — hookify is not importable, so
    # conditions read raw tool_input keys). action=block, but the message tells
    # the AI to KEEP WORKING so autonomous runs are redirected, not halted —
    # same consent-gate pattern as `git push` / `gh release create` above.
    # Toggle: AUTORUN_VERSION_BUMP_GUARD_ENABLED env var (see below the dict).
    "version-bump-command": {
        "action": "block",
        "patterns": [
            "npm version",
            "yarn version",
            "pnpm version",
            "cargo set-version",
            "poetry version",
            "uv version",
            "hatch version",
            "bump2version",
            "bumpversion",
            "mvn versions:set",
        ],
        "name": "version-bump-command",
        "suggestion": (
            "Blocked: bumping the package version requires explicit user consent. The user has not asked for a version bump." + _VERSION_BUMP_ALLOW_HINT
        ),
    },
    "version-bump-manifest-edit": {
        "action": "block",
        "event": "file",
        "tool_matcher": "Edit|Write",
        "patterns": [
            "Cargo.toml",
            "package.json",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "build.gradle",
            "build.gradle.kts",
            "pom.xml",
            "composer.json",
            "Chart.yaml",
            "pubspec.yaml",
        ],
        "name": "version-bump-manifest-edit",
        "conditions": [
            {"field": "new_string", "operator": "regex_match", "pattern": _VERSION_ASSIGNMENT_RE},
        ],
        "suggestion": ("Blocked: this edit changes the package version field in a manifest, which requires explicit user consent." + _VERSION_BUMP_ALLOW_HINT),
    },
    "version-bump-manifest-write": {
        "action": "block",
        "event": "file",
        "tool_matcher": "Edit|Write",
        "patterns": [
            "Cargo.toml",
            "package.json",
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
            "build.gradle",
            "build.gradle.kts",
            "pom.xml",
            "composer.json",
            "Chart.yaml",
            "pubspec.yaml",
        ],
        "name": "version-bump-manifest-write",
        "conditions": [
            {"field": "content", "operator": "regex_match", "pattern": _VERSION_ASSIGNMENT_RE},
        ],
        "suggestion": ("Blocked: this write sets the package version field in a manifest, which requires explicit user consent." + _VERSION_BUMP_ALLOW_HINT),
    },
    # ─── Publishing a package/release requires explicit consent ───────────────
    # Publishes are public and usually irreversible (a released version cannot be
    # unpublished). Bash-only and cross-backend. gh release create / gh repo
    # create / git push already have their own consent gates above.
    "publish-command": {
        "action": "block",
        "patterns": [
            "npm publish",
            "yarn publish",
            "pnpm publish",
            "cargo publish",
            "poetry publish",
            "uv publish",
            "hatch publish",
            "flit publish",
            "twine upload",
            "gem push",
            "mvn deploy",
            "gradle publish",
            "dotnet nuget push",
            "docker push",
        ],
        "name": "publish-command",
        "suggestion": (
            "Blocked: publishing a package/release to a registry requires explicit user consent. The user has not asked to publish." + _PUBLISH_ALLOW_HINT
        ),
    },
}


# ─── Env-toggled consent guards ───────────────────────────────────────────────
# Each variable switches off the default guards listed with it. Guards are on
# by default; disable one globally with its variable, then restart the daemon
# (`autorun --restart-daemon`). Allow per use at runtime instead with `/ar:ok`
# (`/ar:ok 'version'`, `/ar:ok 'cargo publish'`) or `/ar:ok --global`. Values:
#   unset|true|1|yes|on|always — guard active
#   false|0|no|off|never       — guard removed
# integrations.default_integrations() applies this table to the configured
# guard list, user autorun.config.json entries included.
ENV_TOGGLED_GUARDS: dict[str, tuple[str, ...]] = {
    "AUTORUN_VERSION_BUMP_GUARD_ENABLED": (
        "version-bump-command",
        "version-bump-manifest-edit",
        "version-bump-manifest-write",
    ),
    "AUTORUN_PUBLISH_GUARD_ENABLED": ("publish-command",),
}


def env_guard_enabled(variable: str) -> bool:
    """Whether the guard switched by environment variable ``variable`` is on."""
    import os

    val = os.environ.get(variable)
    return val is None or val.strip().lower() not in {"false", "0", "no", "off", "never"}


def guards_disabled_by_env() -> set[str]:
    """Names of the default guards an ENV_TOGGLED_GUARDS variable switches off."""
    return {name for variable, names in ENV_TOGGLED_GUARDS.items() if not env_guard_enabled(variable) for name in names}


for _key in guards_disabled_by_env():
    DEFAULT_INTEGRATIONS.pop(_key, None)


# Configuration - Three-stage completion system with clear instruction/confirmation naming
CONFIG = {
    # Temporal suppression is opt-in per message category. It applies only to
    # byte-identical informational output from concurrent hook invocations;
    # safety decisions, state transitions, failures, command replies, and Stop
    # generations are never eligible. Claims use session-state transactions,
    # expire on access, and stay bounded without a cleanup thread.
    "message_dedup_enabled": True,
    "message_dedup_window_seconds": MESSAGE_DEDUP_DEFAULT_WINDOW_SECONDS,
    "message_dedup_max_entries_per_session": MESSAGE_DEDUP_DEFAULT_ENTRY_CAP,
    "message_dedup_categories": {
        "integration_warning": True,
        "informational_notification": True,
    },
    "scoped_allow_default_grace_seconds": SCOPED_ALLOW_DEFAULT_GRACE_SECONDS,
    "codex_transcript_allow_grace_seconds": CODEX_TRANSCRIPT_ALLOW_GRACE_SECONDS,
    "task_pause_default_ttl_seconds": TASK_PAUSE_DEFAULT_TTL_SECONDS,
    "task_pause_generation_token_bytes": TASK_PAUSE_GENERATION_TOKEN_BYTES,
    # ─── Stage 1: Initial Work ────────────────────────────────────────────────
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
    # What AI outputs when Stage 2 complete (ALL-CAPS confirmation)
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
    # First post-start checkpoint, then the steady cadence after either that
    # checkpoint or any provider-native task/plan update.
    "task_staleness_initial_threshold": 25,
    "task_staleness_subsequent_threshold": 50,
    # all | user | subagent. Both Claude Code and Codex identify subagent hook
    # calls with agent_id; agent_type alone is not a reliable discriminator.
    "task_staleness_agent_scope": "all",
    # Injected when threshold crossed. {threshold} replaced at runtime.
    # V4 strings: no emoji, complete tool syntax, dependency wiring, disable instruction.
    # Warn-then-deny enforcement: 2 PostToolUse levels only (1st + 2nd).
    # {{task_dependency}} is resolved by plugins._resolve_task_dependency() to the
    # harness's own dependency syntax, or to nothing where its task tools have no
    # dependency parameter. It always comes last so the numbering never gaps.
    "task_staleness_message": (
        "\nTASK UPDATE REQUIRED: {threshold} tool calls without {{task_create}} or {{task_update}}. "
        "Your next action must be one of these Task tools: "
        "1. {{task_list}}: review current tasks and their status "
        '2. {{task_update}}({{task_id_param}}=N, status="in_progress"|"completed"): update status '
        '3. {{task_create}}({{task_title}}="...", description="..."): break what is left into '
        "fine-grained steps, one task per step, never one broad task "
        "{{task_dependency}}"
        "Do not call any tool except these until you have updated your task list. "
        "Your next non-Task tool call will be blocked. Disable: /ar:tasks off"
    ),
    "task_staleness_message_2nd": (
        "\nTASK UPDATE OVERDUE: {threshold} more tool calls without a Task tool. "
        "Your next action must be one of these Task tools: "
        "1. {{task_list}}: review current tasks "
        '2. {{task_update}}({{task_id_param}}=N, status="in_progress"|"completed"): update status '
        '3. {{task_create}}({{task_title}}="...", description="..."): one per specific newly discovered step. '
        "Do not call any other tool. Your next non-Task tool call will be blocked. "
        "Disable: /ar:tasks off"
    ),
    # Injected when a cadence threshold is crossed with no active task row in
    # autorun's durable mirror. The harness may retain native tasks that its
    # resume hook does not replay, so this must not claim they do not exist.
    "task_staleness_no_tasks_message": (
        "\nAUTORUN HAS NO ACTIVE TASKS RECORDED: {threshold} tool calls with no active task in autorun's durable mirror. "
        "Your next action must be {{task_create}}: "
        '1. {{task_create}}({{task_title}}="[step]: [action]", description="..."): one concrete step per task, NOT one broad task '
        '2. {{task_update}}({{task_id_param}}=N, status="in_progress"): mark the task you are starting '
        "{{task_dependency}}"
        "Do not call any other tool until you have created at least one task. "
        "Disable: /ar:tasks off"
    ),
    # Appended to stop-block injection when Stage 3 attempted with outstanding tasks.
    "task_outstanding_stage3_message": (
        "\n⚠️ STAGE 3 RESET: {count} outstanding task(s): {names}. Complete or discard them (see actions above), Stage 2 continues."
    ),
    # ─── Ghost-Task / Stale-Ref Workaround (v0.10.2) ─────────────────────────
    # SINGLE SOURCE OF TRUTH for the marker literal. Both the injection builder
    # (uses .format(id=…)) and the detection regex (uses .split("{id}") +
    # re.escape) derive from this one string.
    "ghost_clear_marker_template": "AUTORUN_TASKS_CLEAR_STALE_TASK({id})",
    # SINGLE SOURCE OF TRUTH for the delegation marker, same derivation rules as
    # the stale-clear marker above. Exists because "delegated" is an autorun
    # status, not a harness one: no supported harness's task-update tool accepts
    # it (Claude Code's TaskUpdate rejects it with InputValidationError), so the
    # AI needs a way to request delegation that does not depend on the harness's
    # tool schema. Printing this marker is that way.
    "delegate_marker_template": "AUTORUN_TASK_DELEGATED({id})",
    "task_pause_resume_marker_template": "AUTORUN_TASK_RECOVERY({id})",
    # Printed by the AI on its own line, followed by the decision it needs:
    # "AUTORUN_TASK_AWAITING_USER(7): approve the merge". The task becomes
    # awaiting-user (task_status.py) with that decision stored, stops blocking
    # Stop, and is shown to the user; the next prompt the user types, or the
    # agent's own update of the task, restores the status it had.
    "awaiting_user_marker_template": "AUTORUN_TASK_AWAITING_USER({id})",
    "delegate_reason": "delegated to a subagent via marker; non-blocking until it reports back",
    "ghost_clear_reason": "stale ref: the agent printed the stale-clear marker for a task its harness no longer lists",
    # ─── Cache Guard (/ar:cache) ─────────────────────────────────────────────
    # How much of the JSONL transcript to tail-scan on each PreToolUse call.
    # Larger values find usage stats in sessions with large tool_result payloads.
    "cache_guard_jsonl_scan_bytes": 64 * 1024,
    # Retry window when the initial scan finds no assistant entry (4× initial).
    "cache_guard_jsonl_retry_bytes": 256 * 1024,
    # How long (seconds) to trust a memoised usage reading before re-scanning.
    "cache_guard_memo_ttl_seconds": 2.0,
    # Clock-skew tolerance: timestamps this far in the future are treated as
    # "just now" (age=0) instead of unknown (fail-open).
    "cache_guard_clock_skew_tolerance_s": 60.0,
    # The prompt-time expiry check applies only to a context (input + cache
    # read + cache write tokens) at least this large. 0 checks every size.
    # `/ar:cache set expired-min-tokens <tokens>` overrides it per session.
    "cache_guard_expired_min_tokens": 100_000,
    # What the prompt-time expiry check does when nothing is stored: "warn"
    # shows the notice and lets the prompt through; "block" stops it until
    # it is sent again. `/ar:cache on` stores block, `/ar:cache warn` warn,
    # `/ar:cache off` turns the check off. Warn until the wrapper text of
    # turns no person typed has been confirmed from captured payloads.
    "cache_guard_expired_prompt_action": "warn",
    # ─── Hook Performance Budgets ───────────────────────────────────────────
    # Maximum time a hook-path state read/write may wait on the shared JSON
    # state lock. Longer waits cause Claude/Codex hook timeouts under many
    # simultaneous sessions; callers keep daemon-local cache when persistence
    # is briefly contended.
    # Cross-process hook writes include an fsynced JSON publication. A 500ms
    # budget lets the eight-process reminder/concurrency path queue safely on
    # slower filesystems while remaining bounded for interactive hooks.
    "hook_state_lock_timeout_seconds": 0.5,
    # The floor above is a *default*, not a ceiling. When the caller carries a
    # deadline (ctx.deadline_monotonic), session_manager.state_lock_timeout()
    # spends the time that request actually has left instead of a fixed 0.5s:
    # a Windows runner under the full suite queued longer than the flat budget
    # and returned SessionTimeoutError while the request still had seconds of
    # its own dispatch window unused.
    #
    # Reserve: time held back from the deadline so a lock acquired at the last
    # moment still leaves room to do the work and write the response. Without
    # it, winning the lock and then overrunning the harness timeout is worse
    # than failing early, because the harness discards the whole response.
    "state_lock_response_reserve_seconds": 0.25,
    # Ceiling: no single lock wait exceeds this however long the deadline is.
    # A daemon request with a generous budget must not let one contended write
    # occupy an executor thread indefinitely.
    "state_lock_max_wait_seconds": 3.0,
    # ─── State store: which backend holds session state ───
    # "json"   — one file rewritten on every change. The original.
    # "sqlite" — one row per field. Existing JSON must first be converted with
    #            `autorun --state-migrate` while the scoped daemon is stopped.
    #
    # This is the pre-migration/fresh-install default. A COMPLETE migration
    # receipt durably activates SQLite without editing this source setting;
    # `autorun --state-rollback` restores JSON authority. A failed conversion
    # leaves existing state authoritative and refuses to open rather than
    # running two writable stores at once.
    "state_backend": "json",
    # ─── State store: advisory in-memory bounds ───
    # These bound ADVISORY state only — counters and flags the daemon keeps
    # between durable checkpoints. No record, task, event, or exported plan is
    # ever discarded by them; retention below is the only thing that deletes
    # stored data, and it deletes nothing unless configured to.
    #
    # They exist because a daemon can serve thousands of sessions for weeks,
    # and an advisory value has no durable home, so nothing else would ever
    # remove it. At the limit the oldest advisory entries are dropped; the
    # next read of a dropped key falls back to storage.
    "volatile_state_max_entries": 4096,
    "volatile_state_max_bytes": 8 * 1024 * 1024,
    "volatile_state_max_age_seconds": 86400.0,
    # Full task output history lives in append-only SQLite task_events. Keep a
    # bounded tail in each task record for compatibility/status displays.
    "task_output_recent_limit": 64,
    # ─── State store: write-ahead log maintenance ───
    # Bound the SQLite sidecar, not the data. A checkpoint moves committed
    # pages from the log into the database; the limit caps how large the log
    # grows before that happens. Larger values mean fewer checkpoints and a
    # bigger sidecar. Neither discards anything.
    # Provisional: chosen to keep the log reclaimed, not by comparison.
    "state_wal_autocheckpoint_pages": 1000,
    "state_journal_size_limit_bytes": 8 * 1024 * 1024,
    # Bound on parameters per SELECT when reading a named set of fields, kept
    # under SQLite's variable limit. Larger requests are split into several
    # statements rather than refused.
    "state_query_parameter_chunk": 500,
    # How many malformed legacy keys a refused migration lists by name before
    # summarizing the rest as a count. Every bad key is always counted and the
    # migration always refuses; this bounds only how long the message gets, so
    # one corrupt file cannot print megabytes. Raise it to see more names at
    # once. Bounds a message, never the data.
    "state_migration_max_reported_bad_keys": 20,
    # ─── Logging: bound autorun's own log files ───
    # One ceiling for both logging entry points, so `configure_file_logging`
    # and `get_logger` cannot drift; they previously carried separate copies of
    # the same two literals. Rotation bounds the log at
    # max_bytes * (backup_count + 1) — nothing else prunes it.
    #
    # Bounding matters beyond tidiness: an unbounded log is how a full disk
    # stops being rare. On a full disk `logging.Handler.handleError` prints to
    # stderr, and any stderr from a hook makes Claude Code discard that hook's
    # response, silently disabling every protection. `logging_utils` refuses
    # that write; this keeps the situation from arising in the first place.
    # Provisional: chosen to keep the log reclaimed, not by comparison.
    "log_file_max_bytes": 5 * 1024 * 1024,
    "log_file_backup_count": 3,
    # How many suffixed names an export tries before giving up. Reaching this
    # means something is wrong, not that the directory is busy.
    "plan_export_max_destination_attempts": 1000,
    # The cache lock spans a pip reinstall, so allow a slow build but never wait forever.
    "cache_install_lock_timeout_seconds": 600.0,
    # Daemon dispatch budgets must be below client/wrapper timeouts so the
    # daemon can return a platform-correct fail-open/fail-closed response.
    "daemon_dispatch_timeouts_seconds": {
        "PreToolUse": 3.0,
        "BeforeTool": 3.0,
        "PermissionRequest": 3.0,
        "UserPromptSubmit": 3.0,
        "Stop": 3.0,
        "SubagentStop": 3.0,
        "PostToolUse": 2.0,
        "SessionStart": 2.0,
        "SessionEnd": 2.0,
    },
    # Bound executor occupation when one handler becomes unhealthy. Additional requests
    # return schema-correct failure responses during cooldown or while timed-out workers
    # remain alive instead of spawning an unbounded cluster of worker threads.
    "daemon_dispatch_max_concurrent_per_event": 4,
    "daemon_dispatch_timeout_cooldown_seconds": 5.0,
    # How far past its budget a slot holder must be before "every slot is
    # held" is reported as a stall needing a restart rather than as load.
    # Budgets are 2-3 s; under machine load handlers run at a multiple of
    # that and still finish, so the grace is an order of magnitude above them.
    "daemon_dispatch_stall_grace_seconds": 30.0,
    # A --restart-daemon issued while a daemon that started this recently is
    # answering is a duplicate of the restart that produced it, so it does
    # nothing. On 2026-11-06 agents restarted the daemon twenty times in thirty
    # minutes, each fixing the socket gap the previous restart opened; the
    # longest gap between two of those restarts was 58 s, so the window must
    # exceed it. Install handoffs are exempt: they exist to load new code.
    "daemon_restart_coalesce_seconds": 60.0,
    # How long --restart-daemon waits for the daemon's event loop to answer a
    # health probe before treating it as wedged. The loop answers the probe
    # itself, ahead of any handler, so a loaded daemon answers in milliseconds
    # (266 calls/s sandbox load: p99 under 0.6 s); a wedged one never does.
    "daemon_round_trip_probe_seconds": 2.0,
    # Administrative shutdown carries the target PID so a scoped restart
    # cannot stop a different daemon at the published endpoint.
    "daemon_shutdown_request_key": "_autorun_shutdown",
    "daemon_shutdown_timeout_seconds": 5.0,
    # Timeout layering is intentional and must stay ordered:
    # daemon_dispatch_timeouts_seconds < daemon_client_response_timeouts_seconds
    # < hook_wrapper_timeouts_seconds < outer harness hooks.json timeout.
    # If the client waits less than daemon dispatch, it can fail closed before
    # the daemon emits its platform-correct response. If the wrapper waits too
    # near the harness timeout, Claude/Codex/Gemini discard autorun output.
    "daemon_client_response_timeouts_seconds": {
        "gemini": 3.5,
        "antigravity": 3.5,
        "qwen": 3.5,
        "claude": 4.0,
        "codex": 4.0,
        # The OpenCode shim spawns hook_entry only as its daemon-down
        # fallback and bounds the whole call at its own 5s timer.
        "opencode": 4.0,
        "pi": 4.0,
        # Prime Agent is Pi's runtime with a rebranded config dir; same bridge.
        "prime": 4.0,
    },
    "hook_wrapper_timeouts_seconds": {
        # Gemini-family hooks are configured with a 5s outer timeout, so the
        # wrapper keeps one second of margin for JSON output and process cleanup.
        "gemini": 4.0,
        "antigravity": 4.0,
        "qwen": 4.0,
        # Claude/Codex hooks use 10s outer timeouts; keep these short enough to
        # report autorun failures promptly while leaving room for cold starts.
        "claude": 5.0,
        "codex": 5.0,
        # ForgeCode has no active hooks today; retained for hook_entry fallback
        # compatibility if a user invokes the wrapper with --cli forgecode.
        "forgecode": 5.0,
        # OpenCode reaches hook_entry through the shim's fallback spawn; the
        # shim kills the child at 5s, so the wrapper must finish first.
        "opencode": 4.5,
        # Pi uses the same bounded in-process bridge and fallback lifecycle.
        "pi": 4.5,
        # Prime Agent shares Pi's bridge and fallback lifecycle.
        "prime": 4.5,
    },
    # ─── Plan Acceptance ───────────────────────────────────────────────────
    "tdd_scaffolding_message": (
        "\nTDD SCAFFOLDING REQUIRED: you must create TDD and EXEC tasks before writing ANY implementation code: "
        '1. {{task_create}}({{task_title}}="[TDD] Step N: [test description]"): one per plan step '
        '2. {{task_create}}({{task_title}}="[EXEC] Step N: [impl description]"): one per plan step '
        "3. {{task_list}}: verify all tasks visible "
        "{{task_dependency}}"
        "Do not write implementation code until TDD tasks are created."
    ),
    # --- Task Creation Reminder Messages (v0.10) ---
    "plan_planning_task_reminder": (
        "\nPLANNING TASKS REQUIRED: a plan is active with no tasks tracking it. "
        "Your next action must be {{task_create}}: "
        '1. {{task_create}}({{task_title}}="[PLANNING] Step N: [name]"): one per step, not one broad task '
        "2. {{task_list}}: verify all tasks visible "
        "{{task_dependency}}"
        "Do not call any other tool until planning tasks exist."
    ),
    "plan_execution_task_reminder": (
        "\nEXECUTION TASKS REQUIRED: plan accepted, no implementation tasks created. "
        "Your next action must be {{task_create}}: "
        '1. {{task_create}}({{task_title}}="[TDD] Step N: Write tests for [step]") '
        '2. {{task_create}}({{task_title}}="[EXEC] Step N: [step description]") '
        "3. {{task_list}}: verify all tasks visible "
        "{{task_dependency}}"
        "Do not write code until execution tasks are created."
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
    # BUG #4669: Claude Code ignores permissionDecision:"deny" at exit 0. The
    # tool runs anyway despite the JSON deny decision, so the only way blocking
    # works is stderr + exit 2. Gemini CLI honours the JSON decision correctly.
    # https://github.com/anthropics/claude-code/issues/4669
    # Workaround: client.output_hook_response prints the reason to stderr and
    # returns exit code 2 on deny, for affected platforms only.
    # Evidence: the BUG #4669 block below and plugins/autorun/AGENTS.md; applicability
    # is declared per platform as Platform.has_exit2_workaround.
    # Override: env var of the same name (true|false|always|never). The older
    # AUTORUN_EXIT2_WORKAROUND spelling and the `--exit2-mode` flag remain
    # supported and take precedence over this key.
    # Set to False when Anthropic honours deny at exit 0.
    "AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED": True,
    # BUG #54673: Claude Code exposes no token counts to hooks, and Opus 4.7+ /
    # Fable 5 / Mythos 5 receive no API context-awareness tags either, so the
    # model guesses at remaining capacity and defers real work on the guess.
    # https://github.com/anthropics/claude-code/issues/54673
    # Workaround: install a guidance block into ~/.claude/CLAUDE.md supplying the
    # interpretation the measurement would have. Override: env var of the same
    # name (true|false|always|never).
    # Evidence: notes/2026-07-24-2045-claude-code-opus-5-premature-context-exhaustion.md
    # Set to False when Anthropic exposes token counts to hooks.
    "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED": True,
    # BUG #80305: Claude Code 2.1.233+ gates TaskCreate/Get/Update/List off on
    # newer flagship models unless CLAUDE_CODE_ENABLE_TODO_TOOLS=1 is present;
    # 2.1.268 made it documented behavior (an allowlist of older models).
    # `autorun --install` now sets that switch (platforms.TaskToolSwitch), and
    # task gates need evidence of the tools, so this recovery text reaches only
    # sessions that had the tools and lost them.
    # BUG #80401: the same four tools can vanish mid-session after the
    # claude.ai deferred-tool channel disconnects. The runtime workaround tells
    # the model to load once through ToolSearch, then gives the exact next-
    # session env fix rather than looping on tools that do not exist.
    # https://github.com/anthropics/claude-code/issues/80305
    # https://github.com/anthropics/claude-code/issues/80401
    # Override either env var with false|0|never; true|1|auto affects Claude;
    # always forces its message for diagnostics on another platform.
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED": True,
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_VANISH_MID_SESSION_BUG_80401_WORKAROUND_ENABLED": True,
    # ─── Timing ───────────────────────────────────────────────────────────────
    "max_recheck_count": 3,
    "stage3_countdown_calls": 5,
    # ─── Injection Template ───────────────────────────────────────────────────
    "injection_template": """Your primary objective is to continue the **UNINTERRUPTED, FULLY AUTONOMOUS, NONINTERACTIVE, PATIENT, AND SAFE EXECUTION** of your current tasks and goals.

**THREE-STAGE COMPLETION SYSTEM:**
This system ensures thorough, high-quality work through a structured three-stage process. Each stage builds upon the previous one, with explicit requirements for advancement.

1.  **MANDATORY PROCESS TO CONTINUE EXECUTION:** Cautiously and deliberately continue working **carefully, patiently, concretely, and safely**, non-stop, autonomously, and non-interactively per your instructions and CLAUDE.md philosophy and definition of concrete.
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
        "SEARCH": ("strict-search", "STRICT SEARCH: ONLY modify existing files. Search existing file names and contents first. NO new files."),
    },
    # The word `/ar:file <word>` and `autorun file <word>` use for each policy.
    "file_policy_words": {"ALLOW": "allow", "JUSTIFY": "justify", "SEARCH": "existing-only"},
    # ─── Policy Blocked Messages ──────────────────────────────────────────────
    "policy_blocked": {
        "SEARCH": 'Blocked: STRICT SEARCH policy active. To proceed: 1) Identify what functionality this file provides, 2) Search existing file names for similar functionality with patterns like "*related-topic*", 3) Search file contents for relevant classes/functions/imports, 4) Modify the most appropriate existing file. Search examples: "*auth*" for authentication, "*api*" for endpoints, "*config*" for settings, "*model*" for data structures.',
        "JUSTIFY": "Blocked: JUSTIFIED CREATION policy requires justification. To proceed: 1) Search existing file names and contents for your functionality, 2) Evaluate if existing files can be extended, 3) If no existing file works, include <AUTOFILE_JUSTIFICATION>Specific technical reason why existing files cannot accommodate this functionality</AUTOFILE_JUSTIFICATION> in your reasoning during the same prompt where you request the file creation, then retry file creation.",
    },
    # ─── Commands only the user may run ───────────────────────────────────────
    # They change what the agent is allowed to do, so they take effect only
    # when the user typed them (core.resolve_prompt_author). From a subagent,
    # a model tool, automation or the harness they answer with the refusal
    # below and change nothing. "<name> <subcommand>" guards that subcommand
    # only. Prompt dispatch, the Codex transcript replay and the Skill-tool
    # denial all read this one list (core.AutorunApp.user_only_command).
    "user_only_commands": ("ok", "no", "clear", "file", "cache", "task ignore", "task pause"),
    "user_only_command_refusal": "Only the user can run {command}. Ask them to type it.",
    # ─── Command Mappings ─────────────────────────────────────────────────────
    # Values are internal ids in plugins._INTERNAL_COMMANDS (case-sensitive);
    # plugins._register_command_mappings registers each spelling. An id with
    # no handler there is skipped, so its spelling does nothing.
    # Commands support /ar: prefix with short and long forms
    "command_mappings": {
        "/ar:status": "STATUS",  # File policy, session and global rules
        "/ar:go": "activate",  # Start autorun
        "/ar:goprocess": "activate",  # Start autorun with the plan and wait process
        "/ar:stop": "stop",
        "/ar:estop": "emergency_stop",  # Emergency stop
        "/ar:plannew": "NEW_PLAN",
        "/ar:planrefine": "REFINE_PLAN",
        "/ar:planupdate": "UPDATE_PLAN",
        "/ar:planprocess": "PROCESS_PLAN",
    },
    # Built-in command integrations (suggestions for dangerous commands)
    "default_integrations": DEFAULT_INTEGRATIONS,
    # ─── Integration Search Paths (File-based Extensions) ─────────────────────
    # User can create .md files matching these patterns to add custom integrations
    # Format: .claude/autorun.{name}.local.md (same pattern as hookify)
    "integration_search_paths": [
        ".claude/autorun.*.local.md",  # Default pattern (like hookify)
    ],
    # ─── Install / Uninstall Locations ────────────────────────────────────────
    # Where autorun deploys shared, cross-harness assets. Install and uninstall
    # both read these, so relocating one moves both — a literal in either would
    # let uninstall miss what install wrote.
    #
    # Values may be "~"-prefixed or absolute; they are expanded at use, matching
    # integration_search_paths above which stores patterns rather than resolved
    # paths. Per-harness locations are NOT here: those live on
    # autorun.platforms.Platform.config_dir, the single source of truth.
    #
    # Defaults follow the cross-tool convention Codex reads
    # (core-skills/src/loader.rs:334-345 and core-plugins/src/marketplace.rs:20-25
    # in openai/codex). Note Antigravity uses the singular "~/.agent" instead,
    # which is exactly the kind of difference this key exists to absorb.
    "shared_agents_dir": "~/.agents",
    "shared_agents_skills_subdir": "skills",
    # Old skill name -> its replacement. A copy an earlier release installed
    # under the old name is handed to the plugin that ships the new name
    # (installer.skills.heirs), whose install then retires it as no longer
    # shipped; a copy the user edited is kept and reported.
    "skill_name_migrations": {
        "claude-skill-builder": "ai-skill-builder",
        "parallel-subagent": "test-fixes-in-parallel",
    },
    # Parent directory of the local plugin source the personal marketplace
    # references; the plugin name is appended to it.
    "codex_plugin_source_dir": "~/plugins",
    # Per-harness config-root overrides, e.g. {"codex": "~/.codex-work"}.
    # Resolution in install.platform_config_dir(): this mapping, then the
    # harness's own env vars (CODEX_HOME, QWEN_HOME, CLAUDE_CONFIG_DIR,
    # FORGE_CONFIG), then Platform.config_dir. Additional simultaneous
    # installs of one flavor are custom_harnesses entries, not entries here.
    "harness_config_dirs": {},
    # Persistent custom harness targets in the --custom-harness SPEC grammar
    # (name=flavor:binary:config_dir[::display]); merged with CLI flags at
    # install and status, CLI winning by name. Multiple entries may share one
    # flavor with different config dirs, e.g. codex-home + codex-work.
    "custom_harnesses": (),
    # Command documents Codex may migrate into model-visible skill entries.
    #
    # Empty ON PURPOSE — and it must STAY DECLARED. codex-rs core-plugins
    # manifest.rs load_plugin_command_paths returns None when the manifest has
    # NO "commands" key, and None re-enables the whole-directory fallback scan
    # of commands/ (29 always-loaded catalog entries, ~1,021 o200k_base tokens
    # per turn). An explicit empty list returns Some([]) and migrates nothing.
    # `.codex-plugin/plugin.json` mirrors this as `"commands": []`;
    # test_install_codex.py pins the two empty surfaces to each other.
    #
    # The 17 documents previously listed here cost 533 o200k_base tokens of
    # always-on catalog every turn. One Codex-native skill entry replaces
    # them: `.codex-plugin/skills/ar/SKILL.md` (the `$ar` mention, ~82 catalog
    # tokens) teaches the full `ar:<command>` grammar. The daemon dispatches
    # `ar:*` prompt text regardless of the catalog, so no command behavior
    # changed; every document stays in commands/ for human completion and
    # every alias stays registered in plugins.py.
    #
    # Measured against Codex 0.146.0; receipt:
    # ~/.agents/notes/2026-08-03-0335-codex-skills-live-catalog-context-cost-assessment.md
    "codex_canonical_commands": (),
}

# ─── Settings precedence: CLI parameter > environment > config file > default ──
#
# The dict above is the DEFAULT tier. The file tier is overlaid onto it once,
# at the end of this module, rather than consulted at each of the ~50
# `CONFIG.get` call sites: that is what makes the tier arrive everywhere at
# once instead of wherever someone remembered to add it, and it is why an
# absent file changes nothing.
#
# The environment tier stays above the file because the resolvers that read env
# vars consult them before CONFIG, and that ordering is the point: a value
# exported for one session must not be overridden by a file written for the
# whole machine. The CLI tier sits above both, through the explicit parameters
# and flags that already write env vars (`--exit2-mode`, `--cli`).
#
# REQUIREMENT: an unknown key or a wrong type is declined, not accepted. A
# typo that silently became a setting would make `autorun --status` report a
# value nothing reads, and these settings gate command blocking and file
# policies -- declining to load is a safer failure than refusing to start.
USER_CONFIG_FILENAME = "autorun.config.json"

_DEFAULT_CONFIG = dict(CONFIG)


def default_config() -> dict:
    """The declared defaults, before any user config file is applied."""
    return dict(_DEFAULT_CONFIG)


def harness_setting(key: str, cli_type: str, fallback_harness: str = "claude"):
    """One harness's value from a per-harness dict setting, without raising.

    Indexing such a setting directly is what this exists to prevent. Spelling a
    fallback as ``CONFIG[key][fallback_harness]`` raises ``KeyError`` the moment
    that dict lacks the key, and because the resulting failure response denies
    on a tool-gate event, one raise blocks every tool on every harness.
    ``client.py`` did exactly that at two sites.

    ``apply_user_config`` now merges a file's dict onto the declared default, so
    a user file can no longer be the cause. Two others remain, which is why the
    helper stays rather than being inlined back:

    - the declared defaults are themselves partial. ``forgecode`` has a
      ``hook_wrapper_timeouts_seconds`` entry and no
      ``daemon_client_response_timeouts_seconds`` one, so the direct index
      raises for a real harness with no user file present at all;
    - ``cli_type`` arrives from harness detection, so an unrecognised or future
      harness name reaches this lookup and must resolve to something.

    Resolution order:

    1. the harness's own entry in the resolved dict,
    2. its ``fallback_harness`` entry, if present,
    3. the harness's declared default,
    4. the declared ``fallback_harness``, which is always present.
    """
    configured = CONFIG.get(key) or {}
    if cli_type in configured:
        return configured[cli_type]
    if fallback_harness in configured:
        return configured[fallback_harness]
    declared = _DEFAULT_CONFIG[key]
    return declared.get(cli_type, declared[fallback_harness])


def user_config_path():
    """Where the optional user config file lives, under AUTORUN_HOME."""
    from pathlib import Path
    import os

    home = os.environ.get("AUTORUN_HOME")
    base = Path(home) if home else Path.home() / ".autorun"
    return base / USER_CONFIG_FILENAME


def read_user_config() -> dict:
    """The user config file as written, or ``{}`` when absent or unreadable.

    The one reader of the file. Absent is the normal state before a first
    install and on every machine that never wrote one, so it is not an error.
    Each consumer validates the keys it owns: :func:`apply_user_config` the
    runtime CONFIG keys, ``installer.settings`` the install settings.
    """
    import json

    try:
        loaded = json.loads(user_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def apply_user_config(target: dict) -> dict:
    """Overlay the user config file onto ``target`` and return it.

    Only keys already declared in the defaults are accepted, and only when the
    supplied value matches the declared type -- bools are excluded from the
    int check because ``isinstance(True, int)`` is true and a flag written as
    ``1`` should not silently satisfy a numeric setting. Install settings are
    not declared here; ``installer.settings`` reads and validates those.
    """
    for key, value in read_user_config().items():
        if key not in _DEFAULT_CONFIG:
            continue
        declared = _DEFAULT_CONFIG[key]
        if (
            key.startswith("AUTORUN_BUG_")
            and key.endswith("_WORKAROUND_ENABLED")
            and isinstance(value, str)
        ):
            # Bug flags are declared True but documented, at every tier, as
            # the always/never/auto/range grammar workaround_applies resolves.
            if _workaround_mode_verdict(value, affected=True) is not None:
                target[key] = value
            continue
        if isinstance(declared, bool) != isinstance(value, bool):
            continue
        if isinstance(declared, (int, float)) and not isinstance(value, (int, float)):
            continue
        if isinstance(declared, str) and not isinstance(value, str):
            continue
        if isinstance(declared, dict):
            if not isinstance(value, dict):
                continue
            # Merge onto the declared default rather than substituting for it.
            # Assignment makes omission mean "delete", and the dict settings are
            # the ones where that is most dangerous: `default_integrations` is
            # the safety-guard table, so a file naming one command used to strip
            # the other 47 -- rm, dd if=, fdisk among them -- with nothing said
            # about it. The per-harness timeout dicts had the same shape, where
            # a partial dict made every CONFIG[key][harness] lookup raise.
            #
            # Basing the merge on `declared` and not on `target[key]` keeps the
            # result a pure function of (defaults, file): applying this twice,
            # or after another file, cannot accumulate keys from a previous run.
            target[key] = {**declared, **value}
            continue
        if isinstance(declared, (list, tuple)) and not isinstance(value, (list, tuple)):
            continue
        target[key] = value
    return target


# =============================================================================
# CLI Detection and Bug #4669 Workaround (v0.8.0+)
# =============================================================================


# Platform detection metadata derives from the single source of truth in
# autorun.platforms.PLATFORMS — adding a new CLI = adding one Platform()
# definition there. No parallel maintenance here.
from .platforms import PLATFORMS as _PLATFORMS, detection_platforms as _detection_platforms  # noqa: E402
from .platforms import env_var_identifies_harness as _env_var_identifies_harness  # noqa: E402

# Ordered detectors for all non-default CLIs. Each entry is a 5-tuple:
#   (name, session_id_keys, event_names, path_hints, env_vars)
# First match wins; "claude" is always the default fallback (excluded here).
_CLI_DETECTORS = [
    (
        p.name,
        p.detect_session_keys,
        p.detect_event_names,
        p.detect_path_hints,
        p.detect_env_vars,
    )
    for p in _detection_platforms()
]

# Gemini-only event names (pre-normalization) — kept for backward compat callers.
_GEMINI_EVENTS = _PLATFORMS["gemini"].detect_event_names

# Set of all known CLI names (used for explicit-payload validation).
_KNOWN_CLI_NAMES = frozenset(_PLATFORMS.keys())


def detect_cli_type(payload: dict = None) -> str:
    """Determine the active CLI type from payload or environment.

    Priority:
    1. Explicit 'cli_type' or 'source' parameter in payload
    2. Explicit AUTORUN_CLI_TYPE environment variable set by --cli
    3. Platform-specific session-ID keys in payload
    4. Platform-specific event names in payload
    5. Platform-specific path hints in transcript_path
    6. Environment variables (checked in _CLI_DETECTORS order)
    7. Default: "claude"

    Returns:
        str: one of "claude", "gemini", "antigravity", "qwen", "codex", "forgecode"
    """
    import os

    # 1: Explicit parameters from payload (highest priority)
    if payload:
        explicit = payload.get("cli_type") or payload.get("source")
        if explicit in _KNOWN_CLI_NAMES:
            return explicit

    env_explicit = os.environ.get("AUTORUN_CLI_TYPE")
    if env_explicit in _KNOWN_CLI_NAMES:
        return env_explicit

    if payload:
        # 3–5: Check each detector's payload signals in order
        for name, session_keys, event_names, path_hints, _ in _CLI_DETECTORS:
            if session_keys and any(payload.get(k) for k in session_keys):
                return name
            if event_names and payload.get("hook_event_name") in event_names:
                return name
            if path_hints:
                path = str(payload.get("transcript_path", ""))
                if any(h in path for h in path_hints):
                    return name
        # Claude is the default, but its own transcript path outranks a
        # variable inherited from a parent harness.
        path = str(payload.get("transcript_path", ""))
        if any(h in path for h in _PLATFORMS["claude"].detect_path_hints):
            return "claude"

    # 6: Environment variables (see platforms.env_var_identifies_harness)
    for name, _, _, _, env_vars in _CLI_DETECTORS:
        if any(_env_var_identifies_harness(k, os.environ) for k in env_vars):
            return name

    # 7: Default fallback
    return "claude"


# ─── Bug workaround flag grammar (one owner for every workaround) ─────────────
# REQUIREMENT: every AUTORUN_BUG_*_WORKAROUND_ENABLED flag resolves through
# workaround_applies() below, and passes applicability from a Platform field
# rather than comparing a harness name. Four hand-written copies of this
# grammar existed and two had drifted: one decided applicability with
# `detect_cli_type(...) == "claude"` (so a new harness in an affected family
# would silently miss the workaround), and one lowercased without stripping (so
# ` always ` worked for two flags and not the third). Enforced by
# tests/test_workaround_flag_grammar.py.
_WORKAROUND_ALWAYS = "always"
_WORKAROUND_DISABLED = frozenset({"false", "0", "never"})
_WORKAROUND_AUTO = frozenset({"true", "1", "auto"})

# Version-range support. A workaround is often true of a range of harness
# builds rather than of a harness ("Claude Code 2.1.233+ gates the Task tools
# off"), and different builds run concurrently on one machine, so this resolves
# per invocation rather than per install.
#
# Comparison is `packaging`'s, not ours: dotted-integer compares get
# pre-releases, epochs and local versions wrong, and this decides whether a
# safety workaround engages.
#
# REQUIREMENT: keep both imports lazy and inside the range branch. This
# function runs on the PreToolUse path under the daemon's dispatch budget, and
# nearly every invocation uses the word grammar (`always`/`never`/`auto`),
# which must not pay an import for a feature it never touches.
_RANGE_LEADS = ("<", ">", "=", "!")

#: Tells autorun which harness build it is running under when the harness does
#: not say so itself. Highest precedence, because an operator who sets it knows
#: something the process cannot observe.
HARNESS_VERSION_ENV_VAR = "AUTORUN_HARNESS_VERSION"


def harness_version(cli_type: str) -> "str | None":
    """The running harness's version, or None when nothing reliable says.

    Precedence: ``AUTORUN_HARNESS_VERSION``, then whatever the platform
    registry declares in ``version_env_vars``.

    None is a real answer, not a failure. Measured on a live machine: Claude
    Code publishes no ``CLAUDE_CODE_VERSION``, its hook payload carries no
    version field, and the only version-bearing variable present reports the
    Agent SDK rather than the CLI. Guessing from a number that merely looks
    related would silently change whether a permission workaround engages, so
    unknown resolves to the behavior the flag had before ranges existed.
    """
    import os

    override = os.environ.get(HARNESS_VERSION_ENV_VAR, "").strip()
    if override:
        return override

    try:
        from .platforms import get_platform

        platform = get_platform(cli_type)
    except Exception:
        return None
    for name in getattr(platform, "version_env_vars", ()) or ():
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _parse_version_range(spec: str):
    """The specifier for ``spec``, or None when it cannot be used.

    ``prereleases=True`` is deliberate: a beta harness build still carries the
    upstream bug, and ``SpecifierSet`` excludes pre-releases by default, which
    would silently drop the workaround exactly for the builds most likely to
    have it.

    None also covers `packaging` being absent from an older installed venv
    predating this dependency. Both routes land the caller on the pre-range
    behavior rather than failing a permission decision.
    """
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
    except ImportError:
        return None
    try:
        return SpecifierSet(spec, prereleases=True)
    except InvalidSpecifier:
        return None


def _version_satisfies(specifier, version):
    """True/False, or None when the version is missing or unparseable."""
    from packaging.version import InvalidVersion, Version

    try:
        return Version(str(version)) in specifier
    except (InvalidVersion, TypeError, ValueError):
        return None


def workaround_applies(
    flag: str, *, affected: bool, legacy_flags: tuple = (), version=None
) -> bool:
    """Report whether one bug workaround is active for this invocation.

    Args:
        flag: the key that is both env var and CONFIG entry.
        affected: whether the detected platform has the upstream bug, read
            from a ``Platform`` field by the caller. Passed in rather than
            computed here so this module needs no import of ``platforms``.
        legacy_flags: older spellings checked first, highest precedence. Only
            #4669 has one (``AUTORUN_EXIT2_WORKAROUND``, written by
            ``--exit2-mode``), so an explicit ``--exit2-mode never`` still wins
            over the newer key.

    Resolution order: legacy env vars, then the flag's env var, then the CONFIG
    entry, then ``affected``.

    Values at any tier: ``always`` (every platform), ``false``/``0``/``never``
    (off), ``true``/``1``/``auto`` (affected platforms only).

    An unrecognized spelling deliberately falls through to the next tier rather
    than disabling: a typo in an env var must not silently switch a safety
    workaround off.
    """
    import os

    configured = CONFIG.get(flag, True)
    # A CONFIG entry may be a bool or any value from the same grammar. Both
    # tiers are documented as one vocabulary, and a range written in CONFIG was
    # previously only tested for truthiness -- so it stayed on for every
    # version, which is the opposite of what its author asked for and gave no
    # sign of being ignored.
    tiers = [os.environ.get(key, "") for key in (*legacy_flags, flag)]
    tiers.append(configured if isinstance(configured, str) else "")

    for raw in tiers:
        verdict = _workaround_mode_verdict(raw, affected, version)
        if verdict is not None:
            return verdict

    if not configured:
        return False
    return bool(affected)


def _workaround_mode_verdict(raw: str, affected: bool, version=None):
    """One tier's answer, or None when ``raw`` is empty or outside the grammar.

    The single owner of the grammar: ``workaround_applies`` resolves with it and
    ``apply_user_config`` validates file values with it, so a word the resolver
    would ignore cannot enter CONFIG and be reported as a setting.
    """
    mode = raw.strip().lower()
    if not mode:
        return None
    if mode == _WORKAROUND_ALWAYS:
        return True
    if mode in _WORKAROUND_DISABLED:
        return False
    if mode in _WORKAROUND_AUTO:
        return bool(affected)
    if not mode.startswith(_RANGE_LEADS):
        return None
    specifier = _parse_version_range(mode)
    if specifier is None:
        return None  # malformed spec: treat like a typo, try the next tier
    if not affected:
        # A range narrows an affected platform; it never widens the
        # workaround to a harness that does not have the bug.
        return False
    verdict = _version_satisfies(specifier, version)
    if verdict is None:
        # Version unknown or unparseable. Keep the behavior this flag had
        # before ranges existed rather than guessing: silently dropping a
        # workaround a harness needs is a regression wearing precision as a
        # disguise.
        return True
    return verdict


# --- BUG #4669 WORKAROUND START --- DELETE WHEN FIXED ---
# Claude Code ignores permissionDecision:"deny" at exit 0 — the tool runs
# anyway despite the JSON deny decision, so stderr + exit 2 is the only way
# blocking actually works. Gemini CLI honours the JSON decision per spec.
#   https://github.com/anthropics/claude-code/issues/4669
# Disable: AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_
# WORKAROUND_ENABLED=false (env var or CONFIG entry of the same name).
# Applicability is Platform.has_exit2_workaround, never a hardcoded name.
# Removal: delete this block and replace the two call sites in
# client.output_hook_response with False, leaving only Pathway B.
BUG_4669_FLAG = "AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED"

# Predates the bug-workaround policy and is documented in AGENTS.md, the README
# and `--exit2-mode`, which writes it. Kept as an alias and checked first so an
# explicit `--exit2-mode never` still wins over the newer key.
BUG_4669_LEGACY_FLAG = "AUTORUN_EXIT2_WORKAROUND"


def should_use_exit2_workaround(payload: dict = None) -> bool:
    """Report whether a deny must be delivered as stderr + exit 2 (bug #4669).

    Single flag check for pathway selection in
    :func:`client.output_hook_response`.

    Resolution order, matching every other bug workaround in this codebase:
      1. ``AUTORUN_EXIT2_WORKAROUND`` (legacy spelling, also set by
         ``--exit2-mode``)
      2. ``AUTORUN_BUG_..._BUG_4669_WORKAROUND_ENABLED`` env var
      3. the CONFIG entry of that same name
      4. whether the detected platform declares ``has_exit2_workaround``

    Values at any tier: ``true``/``1``/``auto`` (affected platforms only),
    ``always`` (every platform), ``false``/``0``/``never`` (off).

    Returns:
        True  → Pathway A (JSON + stderr + exit 2)
        False → Pathway B (JSON + exit 0)
    """
    platform = _PLATFORMS.get(detect_cli_type(payload))
    return workaround_applies(
        BUG_4669_FLAG,
        affected=bool(platform and platform.has_exit2_workaround),
        legacy_flags=(BUG_4669_LEGACY_FLAG,),
    )


# --- BUG #4669 WORKAROUND END --- DELETE WHEN FIXED ---


# The file tier is applied last so the workaround grammar it validates bug
# flags against (`_workaround_mode_verdict`) is defined. Nothing above reads
# CONFIG at import time.
apply_user_config(CONFIG)
