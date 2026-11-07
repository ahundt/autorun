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
Unified command integrations system (superset of hookify).

Features:
- Non-blocking warnings (action: warn)
- File-based user extensions (.claude/autorun.*.local.md in the session cwd
  and each parent up to the repository root, nearest first, then
  ~/.claude/autorun.*.local.md)
- All hookify features (conditions, event, tool_matcher)
- Multiple patterns per file
- Redirect with arg substitution
- Semantic when predicates (Python or bash)
- O(1) cached file loading (mtime-based)
"""

from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Final

from .command_detection import (
    COMMAND_PREFIXES,
    _get_basename,
    command_matches_pattern,
    command_tokens_for,
    extract_commands,
    git_subcommand_index,
    nested_scripts,
    shell_command_from_tool_input,
    shell_words,
    shells_running_downloads,
    strip_transparent_command_wrappers,
)

from autorun.config import CONFIG, DEFAULT_INTEGRATIONS, guards_disabled_by_env

__all__ = [
    "Integration",
    "IntegrationPlatformOverride",
    "load_all_integrations",
    "invalidate_caches",
    "check_when_predicate",
    "check_conditions",
]

logger = logging.getLogger(__name__)

# A user `when:` command runs inside a hook; past this it counts as not matching.
WHEN_PREDICATE_TIMEOUT_SECONDS = 2


# =============================================================================
# Integration Dataclass (Immutable for efficient caching)
# =============================================================================


@dataclass(frozen=True, slots=True)
class IntegrationPlatformOverride:
    """Per-platform replacement fields for one command integration."""

    action: str | None = None
    message: str | None = None
    redirect: str | None = None


@dataclass(frozen=True, slots=True)
class Integration:
    """
    Immutable integration for efficient caching.

    Supports all hookify features plus new features:
    - action: "block" or "warn" (warn = allow + message)
    - redirect: alternative command with {args} substitution
    - patterns: multiple OR patterns in one file
    - when: semantic predicates (Python function or bash command)
    """

    patterns: tuple[str, ...]  # Tuple for hashability
    action: str  # "block" or "warn"
    message: str
    redirect: str | None = None
    when: str = "always"
    event: str = "bash"
    tool_matcher: str = "Bash"
    conditions: tuple[dict, ...] = ()  # Hookify conditions (AND-ed)
    enabled: bool = True
    name: str = ""
    source: str = "default"  # "default" or "user"
    platform_overrides: tuple[tuple[str, IntegrationPlatformOverride], ...] = ()
    dedup_category: str | None = None
    dedup_window_seconds: float | None = None

    @staticmethod
    def _redirect_from_config(config: dict) -> str | None:
        """Extract redirect with backward compat for the old commands key."""
        redirect = config.get("redirect")
        if redirect:
            return redirect
        commands = config.get("commands")
        if commands:
            return commands[0] if isinstance(commands, list) else commands
        return None

    @staticmethod
    def _dedup_window_from_config(config: dict) -> float | None:
        value = config.get("dedup_window_seconds")
        if value in (None, ""):
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        return parsed if math.isfinite(parsed) and parsed > 0 else 0.0

    @classmethod
    def _platform_overrides_from_config(cls, config: dict) -> tuple[tuple[str, IntegrationPlatformOverride], ...]:
        """Parse platform_overrides without leaking mutable config dicts."""
        raw = config.get("platform_overrides") or {}
        if not isinstance(raw, dict):
            raise TypeError("platform_overrides must be a mapping of cli_type to fields")

        overrides: list[tuple[str, IntegrationPlatformOverride]] = []
        for cli_type, override in sorted(raw.items()):
            if not isinstance(override, dict):
                raise TypeError(f"platform_overrides[{cli_type!r}] must be a mapping")
            overrides.append(
                (
                    str(cli_type),
                    IntegrationPlatformOverride(
                        action=override.get("action"),
                        message=override.get("suggestion", override.get("message")),
                        redirect=cls._redirect_from_config(override),
                    ),
                )
            )
        return tuple(overrides)

    def _override_for_cli(self, cli_type: str | None) -> IntegrationPlatformOverride | None:
        if not cli_type:
            return None
        for override_cli, override in self.platform_overrides:
            if override_cli == cli_type:
                return override
        return None

    def action_for_cli(self, cli_type: str | None) -> str:
        override = self._override_for_cli(cli_type)
        if override and override.action is not None:
            return override.action
        return self.action

    def message_for_cli(self, cli_type: str | None) -> str:
        override = self._override_for_cli(cli_type)
        if override and override.message is not None:
            return override.message
        return self.message

    def redirect_for_cli(self, cli_type: str | None) -> str | None:
        override = self._override_for_cli(cli_type)
        if override and override.redirect is not None:
            return override.redirect
        return self.redirect

    @classmethod
    def from_dict(cls, pattern: str, config: dict) -> Integration:
        """
        Factory for Python defaults.

        Note: No LRU cache since dicts are unhashable. Cache is at load_all_integrations level.

        Args:
            pattern: Pattern string (used as fallback if patterns not in config)
            config: Configuration dict from DEFAULT_INTEGRATIONS

        Returns:
            Integration instance
        """
        # Normalize patterns (single pattern or list)
        patterns = config.get("patterns")
        if not patterns:
            patterns = (pattern,)  # Single pattern from dict key
        elif isinstance(patterns, list):
            patterns = tuple(patterns)

        return cls(
            patterns=patterns,
            action=config.get("action", "block"),
            message=config.get("suggestion", ""),
            redirect=cls._redirect_from_config(config),
            when=config.get("when", "always"),
            event=config.get("event", "bash"),
            tool_matcher=config.get("tool_matcher", "Bash"),
            conditions=tuple(config.get("conditions", [])),
            name=config.get("name", pattern),
            source="default",
            platform_overrides=cls._platform_overrides_from_config(config),
            dedup_category=config.get("dedup_category") or None,
            dedup_window_seconds=cls._dedup_window_from_config(config),
        )


# =============================================================================
# File Loading with Caching
# =============================================================================

# Compiled integrations per session cwd, with the file mtimes they were built
# from (refreshed on file change).
# ponytail: one entry per project a daemon has served, never evicted; add an
# LRU bound if a daemon ever serves thousands of directories.
_integration_cache: dict[str | None, tuple[dict[str, float], list[Integration]]] = {}


def invalidate_caches() -> None:
    """
    Clear integration caches (manual reload or testing).

    Call this when:
    - User runs /ar:reload command
    - Test setup needs clean state
    - User files have been modified
    """
    _integration_cache.clear()
    _rule_file_listing_cache.clear()


def default_integrations() -> dict:
    """The built-in guard table as configured: CONFIG's copy, which carries the
    user's autorun.config.json merge, minus guards an env toggle switched off.

    The DEFAULT_INTEGRATIONS constant is only the shipped half; reading it
    ignored every user-added or user-retuned guard.
    """
    table = CONFIG.get("default_integrations")
    table = dict(table if isinstance(table, dict) else DEFAULT_INTEGRATIONS)
    for name in guards_disabled_by_env():
        table.pop(name, None)
    return table


def _project_dirs(cwd: str) -> list[Path]:
    """The session directory and its parents up to the repository root.

    Nearest first. The root is the nearest directory holding `.git` (a
    directory, or a file in a worktree); the walk never reaches home or the
    filesystem root. Outside a repository only the session directory itself
    counts: a parent such as /tmp is shared, and a rule file's `when:` runs
    shell commands.
    """
    start = Path(cwd)
    home = Path.home()
    walked: list[Path] = []
    for directory in (start, *start.parents):
        if directory == home or directory == directory.parent:
            break
        walked.append(directory)
        if (directory / ".git").exists():
            return walked
    return [start]


def _integration_files(cwd: str | None) -> list[Path]:
    """User rule files: the session's project, nearest directory first, then
    the home directory.

    Relative to the session's cwd, never the daemon's: one daemon serves every
    session, so Path(".") applied the rules of whichever directory the daemon
    started in to all sessions, and a project's own rules to none. With no cwd
    only the home directory is searched.
    """
    roots = _project_dirs(cwd) if cwd else []
    roots.append(Path.home())
    files: list[Path] = []
    for root in dict.fromkeys(roots):
        for glob_pattern in CONFIG.get("integration_search_paths", [".claude/autorun.*.local.md"]):
            try:
                files.extend(_glob_rule_files(root, glob_pattern))
            except Exception as e:
                logger.warning(f"Error globbing pattern {glob_pattern} under {root}: {e}")
    return files


#: FAT records modification times in 2 s steps, the coarsest resolution of the
#: filesystems autorun runs on. A directory listed within this long of its last
#: change can change again without its mtime moving, so such a listing is never
#: reused -- the rule git applies to its own index ("racy git").
DIRECTORY_MTIME_RESOLUTION_NS = 2_000_000_000
_GLOB_MAGIC = frozenset("*?[")
# (directory, file-name pattern) -> (directory mtime_ns, matching files).
# ponytail: one entry per rule directory a daemon has served, never evicted,
# the same bound as _integration_cache above.
_rule_file_listing_cache: dict[tuple[str, str], tuple[int, list[Path]]] = {}


def _glob_rule_files(root: Path, glob_pattern: str) -> list[Path]:
    """Rule files matching ``glob_pattern`` under ``root``, listed only on change.

    Every PreToolUse asks, from every session, and listing each directory from
    the session up to the repository root plus home was 70% of the dispatch
    (3.5 ms of 4.2 ms). Which names a directory holds changes only when the
    directory's own mtime does, so one stat decides whether to list again.
    """
    relative = PurePath(glob_pattern)
    if _GLOB_MAGIC & set(str(relative.parent)):
        return sorted(path for path in root.glob(glob_pattern) if path.is_file())
    directory = root / relative.parent
    key = (str(directory), relative.name)
    try:
        mtime_ns = directory.stat().st_mtime_ns
    except OSError:
        _rule_file_listing_cache.pop(key, None)
        return []
    cached = _rule_file_listing_cache.get(key)
    if cached is not None and cached[0] == mtime_ns:
        return list(cached[1])
    listed_at_ns = time.time_ns()
    found = sorted(path for path in directory.glob(relative.name) if path.is_file())
    if mtime_ns + DIRECTORY_MTIME_RESOLUTION_NS <= listed_at_ns:
        _rule_file_listing_cache[key] = (mtime_ns, found)
    else:
        _rule_file_listing_cache.pop(key, None)
    return list(found)


def load_all_integrations(cwd: str | None = None) -> list[Integration]:
    """
    Load from Python defaults + user files for the session in ``cwd``. O(1) cached.

    Priority: User files (project, then home) > Python defaults

    Returns:
        List of Integration objects sorted by pattern specificity (most specific first)
    """
    files = _integration_files(cwd)
    current_mtimes = {}
    for fpath in files:
        try:
            current_mtimes[str(fpath)] = fpath.stat().st_mtime
        except OSError:
            continue

    # O(1) cache hit if mtimes unchanged and cache exists
    cached = _integration_cache.get(cwd)
    if cached is not None and cached[0] == current_mtimes:
        return cached[1]

    # Rebuild cache
    integrations = []

    # 1. Python defaults, as configured
    for pattern, config in default_integrations().items():
        try:
            intg = Integration.from_dict(pattern, config)
            # Validate and log warnings
            for warn in _validate_integration(intg, f"default:{pattern}"):
                logger.warning(warn)
            integrations.append(intg)
        except Exception as e:
            logger.warning(f"Error loading default integration '{pattern}': {e}")
            continue

    # 2. User markdown files (override defaults by pattern)
    seen_patterns = set()
    for md_file in files:
        try:
            fm, body = _extract_frontmatter(
                md_file.read_text(encoding="utf-8"), source=str(md_file)
            )
        except Exception as e:
            logger.warning(f"Error reading {md_file}: {e}")
            continue

        # Check if enabled
        if not fm.get("enabled", True):
            continue

        # Normalize patterns (hookify compat: pattern or patterns)
        patterns = fm.get("patterns") or [fm.get("pattern")]
        if not patterns or not patterns[0]:
            logger.warning(f"No patterns in {md_file}, skipping")
            continue

        # Skip if already seen (first file wins)
        pattern_key = tuple(sorted(patterns))
        if pattern_key in seen_patterns:
            logger.debug(f"Duplicate pattern {pattern_key} in {md_file}, skipping")
            continue
        seen_patterns.add(pattern_key)

        # Create integration from file; a malformed file is skipped, never
        # allowed to take the defaults down with it.
        try:
            intg = Integration(
                patterns=tuple(patterns),
                action=fm.get("action", "block"),
                message=body.strip(),
                redirect=fm.get("redirect"),
                when=fm.get("when", "always"),
                event=fm.get("event", "bash"),
                tool_matcher=fm.get("tool_matcher", "Bash"),
                conditions=tuple(fm.get("conditions", [])),
                name=fm.get("name", md_file.stem),
                source="user",
                dedup_category=fm.get("dedup_category") or None,
                dedup_window_seconds=Integration._dedup_window_from_config(fm),
            )
            for warn in _validate_integration(intg, str(md_file)):
                logger.warning(warn)
        except Exception as e:
            logger.warning(f"Error loading user integration {md_file}: {e}")
            continue
        integrations.append(intg)

    # Sort by pattern specificity (most specific first) - FIX Bug 2
    integrations.sort(key=lambda intg: _pattern_specificity(intg.patterns), reverse=True)

    _integration_cache[cwd] = (current_mtimes, integrations)
    return integrations


def _extract_frontmatter(content: str, *, source: str = "") -> tuple[dict, str]:
    """
    Extract YAML-like frontmatter from markdown.

    Format:
        ---
        key: value
        list_key: [item1, item2]
        ---

        Markdown body

    Args:
        content: Markdown content with frontmatter

    Returns:
        (frontmatter_dict, body_text)
    """
    from .command_docs import split_frontmatter

    return split_frontmatter(content)


def _pattern_specificity(patterns: tuple[str, ...]) -> int:
    """
    Calculate pattern specificity for sorting.

    More specific patterns have higher scores:
    - Longer patterns are more specific
    - Patterns with more words are more specific

    Args:
        patterns: Tuple of pattern strings

    Returns:
        Specificity score (higher = more specific)
    """
    max_specificity = 0
    for pattern in patterns:
        # Word count + character count
        specificity = len(pattern.split()) * 100 + len(pattern)
        max_specificity = max(max_specificity, specificity)
    return max_specificity


def _validate_integration(intg: Integration, source: str) -> list[str]:
    """
    Validate integration and return list of warnings.

    Checks:
    - Pattern not too broad (would match everything)
    - Redirect template is valid if present

    Args:
        intg: Integration to validate
        source: Source identifier for warnings (e.g., file path)

    Returns:
        List of warning messages (empty if valid)
    """
    warnings = []

    if intg.dedup_category and intg.dedup_window_seconds is not None and intg.dedup_window_seconds <= 0:
        warnings.append(
            f"[{source}] dedup_window_seconds must be a finite number greater "
            "than 0; temporal suppression is disabled for this integration. "
            "Pass positive seconds or omit the field for the configured default."
        )

    # Check for overly broad patterns
    TOO_BROAD_PATTERNS = {".*", ".", "", "*", "**", ".+"}
    for pattern in intg.patterns:
        if pattern in TOO_BROAD_PATTERNS:
            warnings.append(f"[{source}] Pattern '{pattern}' is too broad and may match all commands. Consider using a more specific pattern.")
        elif len(pattern) == 1 and pattern.isalpha():
            warnings.append(f"[{source}] Pattern '{pattern}' is very short (single character). This may cause false positives.")

    # Validate redirect template if present
    redirect_values = [("default", intg.redirect)]
    redirect_values.extend((f"platform:{cli_type}", override.redirect) for cli_type, override in intg.platform_overrides)
    for redirect_source, redirect in redirect_values:
        if not redirect:
            continue
        # Check for common template errors
        if "{arg}" in redirect and "{args}" not in redirect:
            warnings.append(f"[{source}] Redirect '{redirect}' ({redirect_source}) uses {{arg}} but should use {{args}}.")
        # Check for unbalanced braces
        open_braces = redirect.count("{")
        close_braces = redirect.count("}")
        if open_braces != close_braces:
            warnings.append(f"[{source}] Redirect '{redirect}' ({redirect_source}) has unbalanced braces.")

    for cli_type, override in intg.platform_overrides:
        if override.action is not None and override.action not in {"block", "warn"}:
            warnings.append(f"[{source}] platform_overrides[{cli_type!r}] action {override.action!r} is not one of: block, warn.")

    return warnings


# =============================================================================
# When Predicates (Semantic Conditions)
# =============================================================================


_NO_MATCHED_PATTERN: Final[object] = object()
_PIPE_AWARE_READ_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "grep",
        "find",
        "cat",
        "head",
        "tail",
    }
)


def _set_current_pattern(ctx: any, pattern: str | None) -> tuple[dict | None, object]:
    """Store the matched pattern for predicates without persisting session state."""
    if pattern is None:
        return None, _NO_MATCHED_PATTERN
    try:
        state = object.__getattribute__(ctx, "_state")
        previous = state.get("_autorun_current_pattern", _NO_MATCHED_PATTERN)
        state["_autorun_current_pattern"] = pattern
        return state, previous
    except Exception:
        previous = getattr(ctx, "_autorun_current_pattern", _NO_MATCHED_PATTERN)
        try:
            setattr(ctx, "_autorun_current_pattern", pattern)
        except Exception:
            return None, _NO_MATCHED_PATTERN
        return None, previous


def _restore_current_pattern(ctx: any, state: dict | None, previous: object) -> None:
    """Restore the matched-pattern marker after one predicate evaluation."""
    try:
        if state is not None:
            if previous is _NO_MATCHED_PATTERN:
                state.pop("_autorun_current_pattern", None)
            else:
                state["_autorun_current_pattern"] = previous
        elif previous is _NO_MATCHED_PATTERN:
            try:
                delattr(ctx, "_autorun_current_pattern")
            except Exception:
                pass
        else:
            setattr(ctx, "_autorun_current_pattern", previous)
    except Exception:
        pass


class _ScriptView:
    """`ctx` as a predicate sees it when judging one script a command runs."""

    __slots__ = ("_ctx", "tool_input")

    def __init__(self, ctx: any, script: str) -> None:
        self._ctx = ctx
        self.tool_input = {"command": script}

    def __getattr__(self, name: str):
        return getattr(self._ctx, name)


def _predicate_views(ctx: any, pattern: str | None):
    """The line as typed, then each script inside it that `pattern` matches.

    Predicates parse `ctx.tool_input`, and `bash -c 'git restore f'` holds no
    git command at the top level: judged only there, the restore was allowed.
    Each script body is judged as the line it is (its own pipes and segments).
    """
    yield ctx
    cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
    for script in nested_scripts(cmd):
        if pattern is None or command_matches_pattern(script, pattern):
            yield _ScriptView(ctx, script)


def check_when_predicate(when: str, ctx: any, pattern: str | None = None) -> bool:
    """
    Check when predicate. O(1) for Python, O(cmd) for bash.

    Args:
        when: Predicate name or bash command
        ctx: Event context (not used for most predicates)
        pattern: Matched integration pattern, when known. Pattern-aware
            predicates use this to avoid command-wide false positives.

    Returns:
        True if condition met, False otherwise
    """
    state, previous = _set_current_pattern(ctx, pattern)
    try:
        if when == "always":
            return True

        # Try Python predicate first (fastest)
        pred_func = _WHEN_PREDICATES.get(when)
        if pred_func:
            for view in _predicate_views(ctx, pattern):
                try:
                    if pred_func(view):
                        return True
                except Exception as e:
                    logger.warning(f"When predicate '{when}' failed: {e}")
            return False

        # Fallback: run as bash command, in the session's directory. A
        # user-authored `when:` is about the project the session is working in
        # ("test -f package.json"), and the daemon serving that session is
        # running wherever it was started — one directory for every session on
        # the machine. cwd=None keeps the previous behavior when the event
        # carries no directory.
        # Run in bash wherever one exists: shell=True is dash or cmd.exe.
        from .platforms import posix_shell

        bash = posix_shell()
        try:
            result = subprocess.run(
                [bash, "-c", when] if bash else when,
                shell=bash is None,
                capture_output=True,
                timeout=WHEN_PREDICATE_TIMEOUT_SECONDS,
                text=True, encoding="utf-8", errors="replace",
                cwd=getattr(ctx, "cwd", None) or None,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.warning(f"When predicate '{when}' timed out")
            return False
        except Exception as e:
            logger.warning(f"When predicate '{when}' error: {e}")
            return False
    finally:
        _restore_current_pattern(ctx, state, previous)


# v4 single-source-of-truth for destructive-git predicates.
# Fixes four defects at once across all git-diff-based predicates:
#   1. Narrow diff (`git diff` vs index only) → use `git diff <ref>` (HEAD).
#   2. Daemon cwd leakage → pass cwd=ctx.cwd explicitly.
#   3. Shell env leakage (GIT_DIR/GIT_WORK_TREE) → scrub before subprocess.
#   4. Fail-open on error → fail-safe True (block destructive op when hook is broken).
_PREDICATE_TIMEOUT: Final[float] = 2.0
_SCRUBBED_GIT_ENV_KEYS: Final[frozenset] = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_CONFIG",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
    }
)


def _scrubbed_env() -> dict:
    """Return os.environ minus git env vars that could redirect the subprocess."""
    return {k: v for k, v in os.environ.items() if k not in _SCRUBBED_GIT_ENV_KEYS}


def _git_diff_quiet(cwd: str | None, ref: str, file_path: str | None = None) -> bool:
    """Return True iff working tree+index differs from `ref` (optionally for one file).

    Contract:
      * cwd=None or not a git work tree → False (fail-soft: predicate inapplicable)
      * ref unresolvable (fresh repo, no HEAD) → False (nothing to protect)
      * clean → False
      * any diff → True
      * subprocess error (git missing, timeout, permission) → True (fail-safe block)
    """
    if not cwd:
        return False
    env = _scrubbed_env()
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            timeout=_PREDICATE_TIMEOUT,
            cwd=cwd,
            env=env,
        )
        if probe.returncode != 0 or probe.stdout.strip() != b"true":
            return False
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            capture_output=True,
            timeout=_PREDICATE_TIMEOUT,
            cwd=cwd,
            env=env,
        )
        if verify.returncode != 0:
            return False
        argv = ["git", "diff", ref, "--quiet"]
        if file_path is not None:
            argv += ["--", file_path]
        r = subprocess.run(
            argv,
            capture_output=True,
            timeout=_PREDICATE_TIMEOUT,
            cwd=cwd,
            env=env,
        )
        return r.returncode != 0
    except Exception as e:
        logger.warning(
            "_git_diff_quiet fail-safe block cwd=%r ref=%r file=%r err=%s",
            cwd,
            ref,
            file_path,
            e,
        )
        return True


def _repo_differs_from_head(ctx: any) -> bool:
    """Return True iff the repo at ctx.cwd differs anywhere from HEAD.

    Used by `git checkout .` and `git reset --hard` rules — both destroy all
    tracked changes (worktree AND index), so both need a HEAD-relative diff.
    """
    return _git_diff_quiet(getattr(ctx, "cwd", None), "HEAD", None)


# Backward-compat aliases — preserve any user hookify files or config entries
# still referencing the legacy names. New code should call
# `_repo_differs_from_head` directly.
#
# `_has_uncommitted_changes` kept its own pre-v4 body long after this one became
# an alias: `git diff --quiet` with no cwd, no scrubbed environment, and every
# error swallowed into False. It therefore answered about the daemon's own
# directory rather than the session's, and a staged-only edit — uncommitted work
# like any other — did not count as a change.
def _has_unstaged_changes(ctx: any) -> bool:
    return _repo_differs_from_head(ctx)


def _has_uncommitted_changes(ctx: any) -> bool:
    return _repo_differs_from_head(ctx)


def _stash_exists(ctx: any) -> bool:
    """Return whether the session's repository holds a stash entry.

    Contract, matching `_git_diff_quiet`:
      * cwd unknown or not a git work tree → False (predicate inapplicable;
        `git stash drop` there fails on its own)
      * a stash entry exists → True
      * none → False
      * subprocess error or timeout → True (fail-safe block)

    `ctx.cwd`, not the process's own directory: the daemon serves every session
    on this machine from one process, whose working directory is wherever it
    was started. Running `git stash list` there answered for a repository the
    user was not in, both ways — permitting a drop in a session whose stash was
    full, and blocking one in a session with nothing to lose. The failure mode
    that matters is the first: a dropped stash cannot be recovered.
    """
    cwd = getattr(ctx, "cwd", None)
    if not cwd:
        return False
    env = _scrubbed_env()
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            timeout=_PREDICATE_TIMEOUT,
            cwd=cwd,
            env=env,
        )
        if probe.returncode != 0 or probe.stdout.strip() != b"true":
            return False
        listing = subprocess.run(
            ["git", "stash", "list"],
            capture_output=True,
            timeout=_PREDICATE_TIMEOUT,
            cwd=cwd,
            env=env,
        )
        if listing.returncode != 0:
            return True
        return bool(listing.stdout.strip())
    except Exception as e:
        logger.warning("_stash_exists fail-safe block: %s", e)
        return True


# ---- Pure parser for destructive-git commands (no I/O, fully unit-testable) --


@dataclass(frozen=True, slots=True)
class _DestructiveGitCommand:
    """Parsed representation of a git checkout/restore invocation.

    Pure data — produced by `_parse_destructive_git_cmd` and consumed by
    `_file_differs_from_ref`. Keeping the parser pure means every branch in the
    predicate logic can be exercised by feeding a string, no subprocess needed.

    Fields:
      verb:  "checkout" or "restore" (the destructive operation)
      ref:   the ref to diff against (default "HEAD"; overridden by explicit
             `<ref>` in `git checkout <ref> -- ...` or `--source=<ref>` in
             `git restore`)
      files: pathspecs scoped to the matched shell segment only. May be empty,
             in which case the predicate falls back to a repo-wide diff.
    """

    verb: str
    ref: str
    files: tuple[str, ...]


def _destructive_segments(cmd: str) -> list[list[str]]:
    """Return tokens of every shell segment whose git subcommand is
    `checkout` or `restore`, in order; [] if none.

    Segment-scoped: splits on `;`, `&&`, `||`, `|`, `&`, `\\n` so tokens
    from one chained command never leak into another's parsing.

    All of them, not the first: `git checkout -- clean.py && git checkout --
    dirty.py` used to be judged entirely by `clean.py`, which loses nothing, so
    the write that took `dirty.py`'s uncommitted work ran unchallenged. The
    same shape hid behind an opening `git checkout -b`, an opening
    `git restore --staged`, and an `echo` that merely mentioned a flag.

    Normalizes git global options away so downstream callers see
    `["git", <verb>, ...subcmd_args]` regardless of whether the source
    command had `git -C <path> checkout ...` or `git checkout ...` form.
    """
    from autorun.command_detection import _SHELL_OPERATORS, _shlex_split_safe

    found: list[list[str]] = []
    for segment in _SHELL_OPERATORS.split(cmd):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = _shlex_split_safe(segment)
        except Exception:
            tokens = segment.split()
        tokens = strip_transparent_command_wrappers(tokens)
        # One basename authority, shared with the pattern layer: it already
        # matched `/usr/bin/git checkout` this way, so a raw-token comparison
        # here let the predicate answer "nothing would be lost" for exactly the
        # commands the pattern had just flagged. `os.path.basename` was not that
        # authority either — it splits backslashes only when run on Windows.
        if not tokens or _get_basename(tokens[0]) != "git":
            continue
        sub_idx = git_subcommand_index(tokens)
        if sub_idx >= len(tokens):
            continue
        if tokens[sub_idx] in ("checkout", "restore"):
            # Normalized form so _extract_* helpers can rely on
            # tokens[0] == "git" and tokens[1] == <verb>.
            found.append(["git"] + tokens[sub_idx:])
    return found


def _find_destructive_segment(cmd: str) -> list[str]:
    """Return the first segment `_destructive_segments` finds; [] if none."""
    segments = _destructive_segments(cmd)
    return segments[0] if segments else []


def _extract_checkout_ref(tokens: list[str]) -> str:
    """For `git checkout [<flags>] [<ref>] -- <file>`, return <ref> or "HEAD".

    Flags may appear between `checkout` and `--` (e.g. `-q`, `--force`,
    `--no-overlay`, `--recurse-submodules`). They must be skipped so the
    actual ref is extracted. Otherwise the flag string ends up in
    `_DestructiveGitCommand.ref`, `git rev-parse --verify <flag>` fails,
    and `_git_diff_quiet` returns False — bypassing the block rule.
    """
    if "--" not in tokens:
        return "HEAD"
    dd = tokens.index("--")
    for t in tokens[2:dd]:
        if not t.startswith("-"):
            return t
    return "HEAD"


def _extract_restore_ref(tokens: list[str]) -> str:
    """For `git restore [--source=<ref> | --source <ref> | -s <ref>] <file>`, return <ref>."""
    i = 2
    while i < len(tokens):
        t = tokens[i]
        if t.startswith("--source="):
            return t.split("=", 1)[1]
        if t in ("--source", "-s") and i + 1 < len(tokens):
            return tokens[i + 1]
        i += 1
    return "HEAD"


def _extract_pathspecs(tokens: list[str], verb: str) -> tuple[str, ...]:
    """Return the positional pathspec tokens from a destructive git segment.

    For `git checkout ... -- <paths>`: tokens after the first `--`.
    For `git restore <paths>` (no `--`): positional args, skipping flags and
    the `--source <ref>` / `-s <ref>` two-arg flag.
    """
    if "--" in tokens:
        dd = tokens.index("--")
        return tuple(t for t in tokens[dd + 1 :] if t and not t.startswith("-"))
    if verb != "restore":
        return ()
    # `git restore` without `--`: consume positional args, skipping flags.
    files: list[str] = []
    skip_next = False
    for t in tokens[2:]:
        if skip_next:
            skip_next = False
            continue
        if t in ("--source", "-s"):
            skip_next = True
            continue
        if t.startswith("-"):
            continue
        files.append(t)
    return tuple(files)


def _creates_a_branch(tokens: list[str]) -> bool:
    """Return whether this checkout only creates a branch at the current commit.

    `-b <name>` and `--orphan <name>` cannot discard working-tree content: git
    carries uncommitted changes across, and aborts with "Your local changes to
    the following files would be overwritten by checkout" rather than writing
    over them. Without this the `git reset --hard` block recommended
    `git checkout -b backup/...-wip` and then blocked it, because a dirty repo
    is exactly the state that produced the first block.

    `-B` is deliberately absent: it moves an existing branch ref and can leave
    commits unreachable. `-f`/`--force` is the spelling that really does
    overwrite local changes, so a forced checkout is never exempt.
    """
    if any(t in ("-f", "--force") for t in tokens[2:]):
        return False
    return any(t in ("-b", "--orphan") for t in tokens[2:])


def _parse_destructive_git_cmds(cmd: str) -> list[_DestructiveGitCommand]:
    """Parse every destructive checkout/restore in a shell command, in order.

    Pure function. No subprocess, no I/O. Unit-testable in isolation. Segments
    that only create a branch are dropped, so a leading `git checkout -b` never
    speaks for the segments after it.
    """
    if not cmd:
        return []
    parsed: list[_DestructiveGitCommand] = []
    for tokens in _destructive_segments(cmd):
        verb = tokens[1]  # "checkout" or "restore"
        if verb == "checkout" and _creates_a_branch(tokens):
            continue
        ref = (
            _extract_checkout_ref(tokens)
            if verb == "checkout"
            else _extract_restore_ref(tokens)
        )
        parsed.append(
            _DestructiveGitCommand(
                verb=verb, ref=ref, files=_extract_pathspecs(tokens, verb)
            )
        )
    return parsed


def _parse_destructive_git_cmd(cmd: str) -> _DestructiveGitCommand | None:
    """Return the first command `_parse_destructive_git_cmds` finds, or None."""
    parsed = _parse_destructive_git_cmds(cmd)
    return parsed[0] if parsed else None


# ---- Predicate using the pure parser + hardened subprocess helper ------------


def _file_differs_from_ref(ctx: any) -> bool:
    """Return True iff any git checkout/restore in ctx would alter tracked content.

    Uses `_parse_destructive_git_cmds` (pure) to extract every (verb, ref,
    files), then `_git_diff_quiet` (hardened I/O) to check each. Fail-safe:
    block on internal error. Fail-soft: allow when no repo context is available
    (predicate inapplicable).
    """
    try:
        cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
        cwd = getattr(ctx, "cwd", None)
        for parsed in _parse_destructive_git_cmds(cmd):
            if not parsed.files:
                if _git_diff_quiet(cwd, parsed.ref, None):
                    return True
            elif any(_git_diff_quiet(cwd, parsed.ref, f) for f in parsed.files):
                return True
        return False
    except Exception as e:
        logger.warning("_file_differs_from_ref fail-safe block: %s", e)
        return True


# Backward-compat aliases. `config.py` no longer names either, but a user's own
# integration file may, and a rule that keeps working has to keep meaning the
# same thing. New code calls _file_differs_from_ref.
#
# `_checkout_targets_file_with_changes` was the last separate implementation of
# "would this checkout discard work", and it was weaker than the shared one on
# three axes, each of which allowed a destructive command. It split the raw
# string, so it read only the first command on a line; it compared `parts[0]`
# against the literal "git", letting `/usr/bin/git` and `git.exe` past a pattern
# layer that had already matched them through `_get_basename`; and it asked
# `os.path.exists(target)`, which resolves against the *daemon's* process
# directory rather than `ctx.cwd`. One daemon serves every session on the
# machine, so that last one answered about a directory the user is not in, and
# answered "allow".
def _file_has_unstaged_changes(ctx: any) -> bool:
    return _file_differs_from_ref(ctx)


def _checkout_targets_file_with_changes(ctx: any) -> bool:
    return _file_differs_from_ref(ctx)


def _command_has_file_args(tokens: list[str], pattern: str | None = None) -> bool:
    """Return whether a read-like shell command has non-flag arguments."""
    tokens = _tokens_from_read_command(tokens, pattern)
    if not tokens:
        return False
    return any(t for t in tokens[1:] if t != "--" and not t.startswith("-"))


def _tokens_from_read_command(tokens: list[str], pattern: str | None) -> list[str]:
    """Return tokens starting at the matched read command, after safe wrappers."""
    stripped = strip_transparent_command_wrappers(tokens)
    if not stripped:
        return []
    first = _get_basename(stripped[0])
    if pattern is None:
        if first in _PIPE_AWARE_READ_COMMANDS:
            return stripped
        if first in COMMAND_PREFIXES:
            for i, token in enumerate(stripped[1:], start=1):
                if _get_basename(token) in _PIPE_AWARE_READ_COMMANDS:
                    return stripped[i:]
        return []
    if first == pattern:
        return stripped
    if first in COMMAND_PREFIXES:
        for i, token in enumerate(stripped[1:], start=1):
            if _get_basename(token) == pattern:
                return stripped[i:]
    return []


def _not_in_pipe(ctx: any) -> bool:
    """
    Check if command is NOT in a pipe context (should block for direct file operations).

    Uses bashlex for robust pipe detection when available, with simple fallback.

    Returns True when command should be blocked (NOT in pipe).
    Returns False when command should be allowed (in pipe, heredoc, or reading stdin).

    Examples:
        - `head file.txt` → NOT in pipe → return True (block)
        - `git diff | head -50` → in pipe → return False (allow)
        - `head -50` (no file) → NOT in pipe but stdin → return False (allow)
        - `ps aux | grep foo && echo done` → has pipe → return False (allow)
        - `git commit -m "$(cat <<'EOF' ... EOF)"` → heredoc → return False (allow)

    Args:
        ctx: EventContext with tool_input["command"]

    Returns:
        True if command should be blocked (not in pipe), False otherwise
    """
    try:
        # Extract command from context
        cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
        if not cmd:
            return False  # No command, allow

        current_pattern = getattr(ctx, "_autorun_current_pattern", None)
        if not isinstance(current_pattern, str) or not current_pattern:
            current_pattern = None

        # Try bashlex for robust pipe detection (handles quotes, complex syntax)
        try:
            import bashlex

            parts = bashlex.parse(cmd)

            matched_any = False

            def command_words(node) -> list[str]:
                words = []
                for child in getattr(node, "parts", []) or []:
                    if getattr(child, "kind", None) == "word":
                        words.append(getattr(child, "word", ""))
                return words

            def child_nodes(node):
                for attr in ("parts", "list", "command"):
                    value = getattr(node, attr, None)
                    if not value:
                        continue
                    if isinstance(value, (list, tuple)):
                        yield from value
                    else:
                        yield value

            def direct_matched_file_read(node, in_pipeline: bool = False) -> bool:
                nonlocal matched_any
                kind = getattr(node, "kind", None)
                child_in_pipeline = in_pipeline or kind == "pipeline"

                if kind == "command":
                    tokens = command_words(node)
                    stripped = _tokens_from_read_command(tokens, current_pattern)
                    if stripped:
                        cmd_name = _get_basename(stripped[0])
                        if (current_pattern is None and cmd_name in _PIPE_AWARE_READ_COMMANDS) or cmd_name == current_pattern:
                            matched_any = True
                            if not child_in_pipeline and _command_has_file_args(stripped, current_pattern):
                                return True

                # Recursively check bashlex child containers. Pipelines inside
                # compound loops live under `list`, not `parts`.
                for child in child_nodes(node):
                    if direct_matched_file_read(child, child_in_pipeline):
                        return True
                return False

            # Block only when the matched read-like command occurs outside a pipe.
            for part in parts:
                if direct_matched_file_read(part):
                    return True
            if matched_any:
                return False

        except (ImportError, Exception) as e:
            # Bashlex not available or parse error - fall back to simple check
            logger.debug(f"Bashlex parse failed, using simple pipe check: {e}")

            # Simple fallback: check if command contains pipe operator |
            # This works for most cases but may have edge cases with quoted strings
            if "|" in cmd:
                # Command likely has a pipe - allow (return False to not block)
                return False

        # Not in pipe - but check if reading from stdin (no file args)
        # Commands like `head -50` with no file argument read from stdin
        # We should allow these (they're not direct file operations)

        # Split command to check for file-like arguments
        import shlex

        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()

        # Check for heredoc: if any token contains << then the command
        # is reading from a heredoc (multi-line string), not a file.
        # e.g., cat <<'EOF', cat << EOF, cat <<-EOF,
        # or $(cat <<'EOF'...) inside a larger command.
        for t in tokens:
            if "<<" in t:
                return False  # Heredoc - allow

        # Skip command name and flags
        # Any non-flag argument is potentially a file argument
        if current_pattern is None:
            direct_tokens = strip_transparent_command_wrappers(tokens)
            has_file_args = any(t for t in direct_tokens[1:] if t != "--" and not t.startswith("-"))
        else:
            has_file_args = _command_has_file_args(tokens, current_pattern)

        if has_file_args:
            # Has file arguments - block (return True)
            return True
        else:
            # No file arguments (reading stdin) - allow (return False)
            return False

    except Exception as e:
        logger.warning(f"_not_in_pipe predicate error: {e}")
        # On error, default to allowing (return False to not block)
        return False


def _sed_tokens_edit_in_place(tokens: tuple[str, ...] | list[str]) -> bool:
    """Return whether one sed invocation's argv edits its file in place.

    Every option is read, wherever it sits: GNU sed permutes arguments, so
    `sed 's/a/b/' -i f` edits too, and `-E -i` has the edit second. A short
    option cluster ends at an option that takes the rest as its argument.
    """
    skip_next = False
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token == "--":
            return False
        if token in {"-e", "-f", "-l", "--expression", "--file", "--line-length"}:
            skip_next = True
            continue
        if token == "--in-place" or token.startswith("--in-place="):
            return True
        if token.startswith("--") or not token.startswith("-") or token == "-":
            continue
        for char in token[1:]:
            if char == "i":
                return True
            if char in "efl":
                break  # the rest of the cluster is this option's argument
    return False


def _sed_modifies_files(ctx: any) -> bool:
    """Return True only for sed invocations that edit files in place.

    Plain `sed -n ... file` and `sed 's/a/b/' file` are read/transform
    operations. The file-mutating forms are the `-i` family and GNU
    `--in-place` variants; those should use the platform edit path instead.
    """
    try:
        cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
        if not cmd:
            return False
        # Every sed on the line, the way `_tmutil_mutates_backup_safety` reads
        # every tmutil: `command_tokens_for` answers with one segment, so a
        # read-only `sed -n` ahead of an in-place edit spoke for both, and a
        # `sh -c "sed -i ..."` body — whose argv[0] is the shell — was not seen
        # at all. `extract_commands` splits the segments and recurses into
        # shell bodies.
        _names, command_strings = extract_commands(cmd)
        return any(
            _sed_tokens_edit_in_place(command_tokens_for(one, "sed"))
            for one in command_strings
        )
    except Exception as e:
        logger.warning("_sed_modifies_files predicate error: %s", e)
        return False


_SHELL_COMMAND_SEPARATORS: Final = frozenset({";", "&&", "||", "|", "|&", "&", "(", ")", "\n"})
#: Names awk runs under. gawk, mawk and nawk run the same programs.
_AWK_NAMES: Final = frozenset({"awk", "gawk", "mawk", "nawk"})
_AWK_FIELD = r"\$(?:\d+|NF)"
#: The awk programs allowed on a pipe, as an allowlist. awk can run commands
#: (system, | getline, print | "cmd"), write files (print > f), load code
#: (@load, @include), call functions by name (@f()), and read files through
#: ARGV; a check that looks for those missed spellings of each, so only
#: these plain filters pass and everything else is blocked:
#:   {print}  {print $1, $NF}  NR>1{print $5}  {s+=$2} END{print s+0}  !seen[$0]++
#: One print/printf operand: a field, or a string with no quote and no
#: backslash other than \n or \t, so it cannot end early. A `>` or `|`
#: inside one is text: the whole program must match, and nothing in this
#: grammar is a redirection.
_AWK_STRING = r'"(?:[^"\\]|\\[nt])*"'
_AWK_OPERAND = rf"(?:{_AWK_FIELD}|{_AWK_STRING})"
_AWK_PRINT = rf"printf?(?:\s+{_AWK_OPERAND}(?:\s*,?\s*{_AWK_OPERAND})*)?"
_AWK_CONDITION = rf"(?:NR|FNR|NF|{_AWK_FIELD})\s*(?:[<>]=?|==|!=)\s*(?:\d+|{_AWK_STRING})"
_AWK_ALLOWED_PROGRAMS: Final = (
    re.compile(rf"\s*(?:{_AWK_CONDITION}\s*)?\{{\s*{_AWK_PRINT}\s*;?\s*\}}\s*"),
    re.compile(rf"\s*{_AWK_CONDITION}\s*"),
    re.compile(
        rf"\s*\{{\s*([A-Za-z_]\w*)\s*\+=\s*{_AWK_FIELD}\s*;?\s*\}}\s*"
        rf"END\s*\{{\s*print\s+\1(?:\s*\+\s*0)?\s*;?\s*\}}\s*"
    ),
    re.compile(r"\s*!\s*([A-Za-z_]\w*)\[\$0\]\+\+\s*"),
)
#: Shell words that start a command, so an awk right after one runs.
_PIPE_WORDS: Final = frozenset({"|", "|&"})
#: Redirections an allowed filter may carry: errors discarded or merged.
_AWK_HARMLESS_REDIRECTS: Final = frozenset({("2>", "/dev/null"), ("2>&", "1"), (">&", "2")})


def _awk_invocation_only_filters(words: list[str], at: int) -> bool:
    """Whether the awk at ``words[at]`` is an allowed filter reading its pipe."""
    if at == 0 or words[at - 1] not in _PIPE_WORDS:
        return False  # not fed by a pipe, or behind a wrapper/keyword/`{`
    i = at + 1
    if i < len(words) and words[i].startswith("-F"):
        separator = words[i][2:] if len(words[i]) > 2 else (words[i + 1] if i + 1 < len(words) else "")
        if not separator or "$" in separator or "`" in separator:
            return False
        i += 1 if len(words[i]) > 2 else 2
    if i >= len(words) or not any(p.fullmatch(words[i]) for p in _AWK_ALLOWED_PROGRAMS):
        return False
    i += 1
    while i < len(words) and words[i] not in _SHELL_COMMAND_SEPARATORS:
        op = words[i]
        if op.isdigit() and i + 1 < len(words):
            op, i = op + words[i + 1], i + 1
        target = words[i + 1] if i + 1 < len(words) else ""
        if (op, target) not in _AWK_HARMLESS_REDIRECTS:
            return False
        i += 2
    return True


def _awk_does_more_than_filter_stdin(ctx: any) -> bool:
    """Block awk unless every awk on the line only filters its input stream.

    `ls | awk '{print $5}'` reads a pipe like `ls | grep x`, so it is allowed,
    as cat/grep/find in a pipe are. Being in a pipe is not enough: awk can
    edit (`-i inplace`), write (`print > f`, `> out`), run commands
    (`system()`), read named files, or run as root, wherever it sits.

    Judged on quote-aware shell words, and closed whenever it cannot see:
    a line that does not parse, a command substitution that may hold an
    awk, or a matched awk it cannot find all block.
    """
    try:
        cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
        if not cmd:
            return False
        if any(_awk_does_more_in(script) for script in nested_scripts(cmd)):
            return True
        return _awk_does_more_in(cmd)
    except Exception as e:
        logger.warning("_awk_does_more_than_filter_stdin predicate error: %s", e)
        return True


def _awk_does_more_in(script: str) -> bool:
    words = shell_words(script)
    if words is None:
        return True
    outside_single_quotes = re.sub(r"'[^']*'", "", script)
    if "$(" in outside_single_quotes or "`" in outside_single_quotes:
        return True  # a substitution's command is not a word here
    awk_at = [i for i, word in enumerate(words) if _get_basename(word) in _AWK_NAMES]
    if not awk_at:
        return True  # the pattern matched an awk this reading cannot place
    return not all(_awk_invocation_only_filters(words, i) for i in awk_at)


def _restore_touches_worktree(tokens: list[str]) -> bool:
    """Return whether one `git restore` segment writes the working tree.

    `--worktree`/`-W` always does, even alongside `--staged`. `--staged`/`-S`
    alone only unstages. With neither, git defaults to `--worktree`.

    Short flags bundle (`-SW`, `-WS`), and case carries meaning: `-S` is
    `--staged` while `-s` is `--source`. Only the segment's own tokens are
    read — scanning the whole command string meant a `--staged` anywhere,
    including inside an unrelated `echo`, spoke for a destructive restore
    further along the line.
    """
    flags = [t for t in tokens[2:] if t.startswith("-") and t != "--"]
    short = [f for f in flags if not f.startswith("--")]
    if "--worktree" in flags or any("W" in f for f in short):
        return True
    if "--staged" in flags or any("S" in f for f in short):
        return False
    return True


def _restore_is_destructive(ctx: any) -> bool:
    """
    Check if 'git restore' is destructive (discards working tree changes).

    Safe (unstage only):
        git restore --staged <file>
        git restore -S <file>

    Destructive (discards working tree changes permanently):
        git restore <file>              (default is --worktree)
        git restore --worktree <file>
        git restore -W <file>
        git restore -SW <file>          (both staged + worktree)
        git restore --staged --worktree <file>

    Returns:
        True if destructive (should block), False if safe (staged-only)
    """
    try:
        cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
        if not cmd:
            return False

        restores = [t for t in _destructive_segments(cmd) if t[1] == "restore"]
        if restores and not any(_restore_touches_worktree(t) for t in restores):
            # Every restore here is --staged only: unstages without discarding
            # the worktree. (With --source=<ref> this restores the INDEX from
            # <ref> — no worktree data loss; still classified safe.)
            return False

        return _file_differs_from_ref(ctx)
    except Exception as e:
        logger.warning("_restore_is_destructive fail-safe block: %s", e)
        return True


_TMUTIL_CONSENT_REQUIRED_VERBS: Final[frozenset[str]] = frozenset(
    {
        "addexclusion",
        "associatedisk",
        "delete",
        "deleteinprogress",
        "deletelocalsnapshots",
        "disable",
        "inheritbackup",
        "removedestination",
        "setdestination",
        "setquota",
        "stopbackup",
        "thinlocalsnapshots",
    }
)


def _tmutil_mutates_backup_safety(ctx: any) -> bool:
    """Require consent for Time Machine history or protection mutations."""
    cmd = shell_command_from_tool_input(getattr(ctx, "tool_input", {}))
    if not cmd:
        return False

    try:
        _names, command_strings = extract_commands(cmd)
        for command_string in command_strings:
            tokens = command_tokens_for(command_string, "tmutil")
            if len(tokens) > 1 and tokens[1] in _TMUTIL_CONSENT_REQUIRED_VERBS:
                return True
        return False
    except Exception as e:
        # Matching `tmutil` reached this predicate; parser failures must not
        # silently permit a potentially destructive backup operation.
        logger.warning("_tmutil_mutates_backup_safety fail-safe block: %s", e)
        return True


def _shell_runs_downloaded_script(ctx: any) -> bool:
    """Whether the matched shell runs a script straight from curl/wget.

    Judged per shell: in `sh build.sh && curl ... | bash` only `bash` is, so an
    allow for `sh` cannot carry the download.
    """
    shells = shells_running_downloads(shell_command_from_tool_input(getattr(ctx, "tool_input", {})))
    pattern = getattr(ctx, "_autorun_current_pattern", None)
    return pattern in shells if isinstance(pattern, str) and pattern else bool(shells)


# Predicate functions (O(1) lookups)
# v4: new primary names `_repo_differs_from_head` and `_file_differs_from_ref`
# are ref-aware and cover staged-only changes that the legacy narrow-diff
# predicates missed. Legacy names kept as aliases for config.py and user
# hookify files written against prior versions.
_WHEN_PREDICATES: Final[dict] = {
    "has_uncommitted_changes": _has_uncommitted_changes,
    "_has_uncommitted_changes": _has_uncommitted_changes,
    # v4 primary: repo-wide HEAD comparison (catches staged-only changes).
    "_repo_differs_from_head": _repo_differs_from_head,
    # Legacy alias → delegates to _repo_differs_from_head.
    "_has_unstaged_changes": _has_unstaged_changes,
    "_stash_exists": _stash_exists,
    # v4 primary: file-scoped HEAD/ref comparison.
    "_file_differs_from_ref": _file_differs_from_ref,
    # Legacy alias → delegates to _file_differs_from_ref.
    "_file_has_unstaged_changes": _file_has_unstaged_changes,
    "_checkout_targets_file_with_changes": _checkout_targets_file_with_changes,
    "_not_in_pipe": _not_in_pipe,
    "_sed_modifies_files": _sed_modifies_files,
    "_awk_does_more_than_filter_stdin": _awk_does_more_than_filter_stdin,
    "_restore_is_destructive": _restore_is_destructive,
    "_tmutil_mutates_backup_safety": _tmutil_mutates_backup_safety,
    "_shell_runs_downloaded_script": _shell_runs_downloaded_script,
}


# =============================================================================
# Conditions (Hookify Compatibility)
# =============================================================================


def check_conditions(conditions: tuple[dict, ...], ctx: any) -> bool:
    """
    Check hookify-style conditions (AND-ed).

    Tries to reuse hookify's logic if available, falls back to basic matching.

    Args:
        conditions: Tuple of condition dicts
        ctx: Event context with tool_name and tool_input

    Returns:
        True if all conditions met, False otherwise
    """
    if not conditions:
        return True

    # Try hookify import
    try:
        from hookify.core.rule_engine import RuleEngine, Condition

        engine = RuleEngine()

        for cond_dict in conditions:
            cond = Condition.from_dict(cond_dict)
            if not engine._check_condition(cond, ctx.tool_name, ctx.tool_input, {}):
                return False
        return True
    except ImportError:
        # Fallback: basic regex matching
        for cond_dict in conditions:
            field = cond_dict.get("field", "")
            pattern = cond_dict.get("pattern", "")
            operator = cond_dict.get("operator", "contains")

            # Get field value from context
            field_val = str(ctx.tool_input.get(field, ""))

            # Apply operator
            if operator == "regex_match":
                if not re.search(pattern, field_val):
                    return False
            elif operator == "contains":
                if pattern not in field_val:
                    return False
            elif operator == "equals":
                if field_val != pattern:
                    return False
        return True
    except Exception as e:
        logger.warning(f"Error checking conditions: {e}")
        return False
