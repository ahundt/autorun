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
- File-based user extensions (.claude/autorun.*.local.md)
- All hookify features (conditions, event, tool_matcher)
- Multiple patterns per file
- Redirect with arg substitution
- Semantic when predicates (Python or bash)
- O(1) cached file loading (mtime-based)
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from .command_detection import command_tokens_for

from autorun.config import CONFIG, DEFAULT_INTEGRATIONS

__all__ = [
    "Integration",
    "load_all_integrations",
    "invalidate_caches",
    "check_when_predicate",
    "check_conditions",
]

logger = logging.getLogger(__name__)


# =============================================================================
# Integration Dataclass (Immutable for efficient caching)
# =============================================================================


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
    patterns: tuple[str, ...]              # Tuple for hashability
    action: str                            # "block" or "warn"
    message: str
    redirect: str | None = None
    when: str = "always"
    event: str = "bash"
    tool_matcher: str = "Bash"
    conditions: tuple[dict, ...] = ()      # Hookify conditions (AND-ed)
    enabled: bool = True
    name: str = ""
    source: str = "default"                # "default" or "user"

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

        # Extract redirect (backward compat: "commands" -> "redirect")
        redirect = config.get("redirect")
        if not redirect:
            commands = config.get("commands")
            if commands:
                redirect = commands[0] if isinstance(commands, list) else commands

        return cls(
            patterns=patterns,
            action=config.get("action", "block"),
            message=config.get("suggestion", ""),
            redirect=redirect,
            when=config.get("when", "always"),
            event=config.get("event", "bash"),
            tool_matcher=config.get("tool_matcher", "Bash"),
            conditions=tuple(config.get("conditions", [])),
            name=config.get("name", pattern),
            source="default"
        )


# =============================================================================
# File Loading with Caching
# =============================================================================

# Cache compiled integrations (refreshed on file change)
_integration_cache: list[Integration] | None = None
_cache_mtime: dict[str, float] = {}


def invalidate_caches() -> None:
    """
    Clear integration caches (manual reload or testing).

    Call this when:
    - User runs /ar:reload command
    - Test setup needs clean state
    - User files have been modified
    """
    global _integration_cache, _cache_mtime
    _integration_cache = None
    _cache_mtime = {}


def load_all_integrations() -> list[Integration]:
    """
    Load from Python defaults + user files. O(1) cached.

    Priority: User files > Python defaults

    Returns:
        List of Integration objects sorted by pattern specificity (most specific first)
    """
    global _integration_cache, _cache_mtime

    # Check if cache is stale (file mtimes changed)
    current_mtimes = {}
    search_paths = CONFIG.get("integration_search_paths", [".claude/autorun.*.local.md"])

    for glob_pattern in search_paths:
        try:
            for fpath in Path(".").glob(glob_pattern):
                if fpath.is_file():
                    current_mtimes[str(fpath)] = fpath.stat().st_mtime
        except Exception as e:
            logger.warning(f"Error globbing pattern {glob_pattern}: {e}")
            continue

    # O(1) cache hit if mtimes unchanged and cache exists
    if _integration_cache is not None and current_mtimes == _cache_mtime:
        return _integration_cache

    # Rebuild cache
    integrations = []

    # 1. Python defaults
    for pattern, config in DEFAULT_INTEGRATIONS.items():
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
    for glob_pattern in search_paths:
        try:
            for md_file in Path(".").glob(glob_pattern):
                if not md_file.is_file():
                    continue

                try:
                    fm, body = _extract_frontmatter(md_file.read_text(encoding="utf-8"))
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

                # Create integration from file
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
                    source="user"
                )
                # Validate and log warnings
                for warn in _validate_integration(intg, str(md_file)):
                    logger.warning(warn)
                integrations.append(intg)
        except Exception as e:
            logger.warning(f"Error loading user files from {glob_pattern}: {e}")
            continue

    # Sort by pattern specificity (most specific first) - FIX Bug 2
    integrations.sort(key=lambda intg: _pattern_specificity(intg.patterns), reverse=True)

    _integration_cache = integrations
    _cache_mtime = current_mtimes
    return integrations


def _extract_frontmatter(content: str) -> tuple[dict, str]:
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
    if not content.startswith("---"):
        return {}, content

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content

    # Parse simple YAML (key: value pairs, lists)
    fm_text = parts[1].strip()
    frontmatter = {}

    for line in fm_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()

            # Parse list syntax: [item1, item2]
            if value.startswith("[") and value.endswith("]"):
                value = [item.strip().strip("\"'") for item in value[1:-1].split(",")]
            # Parse boolean
            elif value.lower() in ("true", "false"):
                value = value.lower() == "true"
            # Parse quoted string
            elif value.startswith(("\"", "'")) and value.endswith(value[0]):
                value = value[1:-1]

            frontmatter[key] = value

    body = parts[2].strip()
    return frontmatter, body


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

    # Check for overly broad patterns
    TOO_BROAD_PATTERNS = {".*", ".", "", "*", "**", ".+"}
    for pattern in intg.patterns:
        if pattern in TOO_BROAD_PATTERNS:
            warnings.append(
                f"[{source}] Pattern '{pattern}' is too broad and may match all commands. "
                "Consider using a more specific pattern."
            )
        elif len(pattern) == 1 and pattern.isalpha():
            warnings.append(
                f"[{source}] Pattern '{pattern}' is very short (single character). "
                "This may cause false positives."
            )

    # Validate redirect template if present
    if intg.redirect:
        # Check for common template errors
        if "{arg}" in intg.redirect and "{args}" not in intg.redirect:
            warnings.append(
                f"[{source}] Redirect '{intg.redirect}' uses {{arg}} but should use {{args}}."
            )
        # Check for unbalanced braces
        open_braces = intg.redirect.count("{")
        close_braces = intg.redirect.count("}")
        if open_braces != close_braces:
            warnings.append(
                f"[{source}] Redirect '{intg.redirect}' has unbalanced braces."
            )

    return warnings


# =============================================================================
# When Predicates (Semantic Conditions)
# =============================================================================


def check_when_predicate(when: str, ctx: any) -> bool:
    """
    Check when predicate. O(1) for Python, O(cmd) for bash.

    Args:
        when: Predicate name or bash command
        ctx: Event context (not used for most predicates)

    Returns:
        True if condition met, False otherwise
    """
    if when == "always":
        return True

    # Try Python predicate first (fastest)
    pred_func = _WHEN_PREDICATES.get(when)
    if pred_func:
        try:
            return pred_func(ctx)
        except Exception as e:
            logger.warning(f"When predicate '{when}' failed: {e}")
            return False

    # Fallback: run as bash command
    try:
        result = subprocess.run(
            when,
            shell=True,
            capture_output=True,
            timeout=2,
            text=True
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        logger.warning(f"When predicate '{when}' timed out")
        return False
    except Exception as e:
        logger.warning(f"When predicate '{when}' error: {e}")
        return False


def _has_uncommitted_changes(ctx: any) -> bool:
    """Check if git has uncommitted changes."""
    try:
        result = subprocess.run(
            "git diff --quiet --exit-code",
            shell=True,
            capture_output=True,
            timeout=2
        )
        return result.returncode != 0
    except Exception:
        return False


# v4 single-source-of-truth for destructive-git predicates.
# Fixes four defects at once across all git-diff-based predicates:
#   1. Narrow diff (`git diff` vs index only) → use `git diff <ref>` (HEAD).
#   2. Daemon cwd leakage → pass cwd=ctx.cwd explicitly.
#   3. Shell env leakage (GIT_DIR/GIT_WORK_TREE) → scrub before subprocess.
#   4. Fail-open on error → fail-safe True (block destructive op when hook is broken).
_PREDICATE_TIMEOUT: Final[float] = 2.0
_SCRUBBED_GIT_ENV_KEYS: Final[frozenset] = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG",
    "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM",
})


def _scrubbed_env() -> dict:
    """Return os.environ minus git env vars that could redirect the subprocess."""
    return {k: v for k, v in os.environ.items() if k not in _SCRUBBED_GIT_ENV_KEYS}


def _git_diff_quiet(
    cwd: str | None, ref: str, file_path: str | None = None
) -> bool:
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
            capture_output=True, timeout=_PREDICATE_TIMEOUT, cwd=cwd, env=env,
        )
        if probe.returncode != 0 or probe.stdout.strip() != b"true":
            return False
        verify = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            capture_output=True, timeout=_PREDICATE_TIMEOUT, cwd=cwd, env=env,
        )
        if verify.returncode != 0:
            return False
        argv = ["git", "diff", ref, "--quiet"]
        if file_path is not None:
            argv += ["--", file_path]
        r = subprocess.run(
            argv, capture_output=True, timeout=_PREDICATE_TIMEOUT, cwd=cwd, env=env,
        )
        return r.returncode != 0
    except Exception as e:
        logger.warning(
            "_git_diff_quiet fail-safe block cwd=%r ref=%r file=%r err=%s",
            cwd, ref, file_path, e,
        )
        return True


def _repo_differs_from_head(ctx: any) -> bool:
    """Return True iff the repo at ctx.cwd differs anywhere from HEAD.

    Used by `git checkout .` and `git reset --hard` rules — both destroy all
    tracked changes (worktree AND index), so both need a HEAD-relative diff.
    """
    return _git_diff_quiet(getattr(ctx, "cwd", None), "HEAD", None)


# Backward-compat alias — preserves any user hookify files or config entries
# still referencing the legacy `_has_unstaged_changes` name. New code should
# call `_repo_differs_from_head` directly.
def _has_unstaged_changes(ctx: any) -> bool:
    return _repo_differs_from_head(ctx)


def _stash_exists(ctx: any) -> bool:
    """Check if git stash exists."""
    try:
        result = subprocess.run(
            "git stash list",
            shell=True,
            capture_output=True,
            timeout=2,
            text=True
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


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


# Git global options that consume the NEXT token as their value. These may
# appear between `git` and the subcommand (e.g. `git -C /repo checkout ...`)
# and must be skipped by `_find_destructive_segment` or the parser misses the
# subcommand entirely — allowing a destructive checkout/restore to bypass the
# block rule. The attached-value forms (`--git-dir=<path>`, `-c key=val`
# combined as `--git-dir=<path>`) consume a single token and are handled
# separately below.
_GIT_GLOBAL_OPTS_WITH_ARG: Final[frozenset] = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--git-common-dir", "--super-prefix", "--exec-path",
    "--attr-source", "--list-cmds", "--config-env",
})


def _skip_git_global_opts(tokens: list[str]) -> int:
    """Return index of the first non-option token after `git`.

    Walks `tokens[1:]` consuming git's global options (both the bare flags
    like `--paginate` and the ones that take an argument like `-C <path>` or
    `--git-dir=<path>`). The returned index points at whatever comes next —
    the subcommand (checkout/restore/status/...) or end-of-tokens.

    Precondition: tokens[0] == "git". Caller must check.
    """
    i = 1
    while i < len(tokens):
        t = tokens[i]
        # Flag with separate arg: `-C /path`, `-c key=val`, `--git-dir <path>`.
        if t in _GIT_GLOBAL_OPTS_WITH_ARG:
            i += 2
            continue
        # Attached-value form: `--git-dir=<path>`, `-c=key=val` (rare but valid).
        if "=" in t:
            head = t.split("=", 1)[0]
            if head in _GIT_GLOBAL_OPTS_WITH_ARG:
                i += 1
                continue
        # Bare flag: `-p`, `--paginate`, `--no-pager`, `--bare`, `--html-path`, etc.
        if t.startswith("-"):
            i += 1
            continue
        # First non-option token — this is the subcommand (or a pathspec if
        # there's no subcommand at all, but then we won't match below).
        return i
    return i


def _find_destructive_segment(cmd: str) -> list[str]:
    """Return tokens of the first shell segment whose git subcommand is
    `checkout` or `restore`; [] if none.

    Segment-scoped: splits on `;`, `&&`, `||`, `|`, `&`, `\\n` so tokens
    from subsequent chained commands never leak into pathspec parsing.

    Normalizes git global options away so downstream callers see
    `["git", <verb>, ...subcmd_args]` regardless of whether the source
    command had `git -C <path> checkout ...` or `git checkout ...` form.
    """
    from autorun.command_detection import _SHELL_OPERATORS, _shlex_split_safe
    for segment in _SHELL_OPERATORS.split(cmd):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = _shlex_split_safe(segment)
        except Exception:
            tokens = segment.split()
        if not tokens or tokens[0] != "git":
            continue
        sub_idx = _skip_git_global_opts(tokens)
        if sub_idx >= len(tokens):
            continue
        if tokens[sub_idx] in ("checkout", "restore"):
            # Return normalized form so _extract_* helpers can rely on
            # tokens[0] == "git" and tokens[1] == <verb>.
            return ["git"] + tokens[sub_idx:]
    return []


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
        return tuple(t for t in tokens[dd + 1:] if t and not t.startswith("-"))
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


def _parse_destructive_git_cmd(cmd: str) -> _DestructiveGitCommand | None:
    """Parse a shell command into a _DestructiveGitCommand, or None if no match.

    Pure function. No subprocess, no I/O. Unit-testable in isolation.
    """
    if not cmd:
        return None
    tokens = _find_destructive_segment(cmd)
    if not tokens:
        return None
    verb = tokens[1]   # "checkout" or "restore"
    ref = _extract_checkout_ref(tokens) if verb == "checkout" else _extract_restore_ref(tokens)
    files = _extract_pathspecs(tokens, verb)
    return _DestructiveGitCommand(verb=verb, ref=ref, files=files)


# ---- Predicate using the pure parser + hardened subprocess helper ------------

def _file_differs_from_ref(ctx: any) -> bool:
    """Return True iff the git checkout/restore in ctx would alter tracked content.

    Uses `_parse_destructive_git_cmd` (pure) to extract (verb, ref, files), then
    `_git_diff_quiet` (hardened I/O) to check. Fail-safe: block on internal error.
    Fail-soft: allow when no repo context is available (predicate inapplicable).
    """
    try:
        cmd = ctx.tool_input.get("command", "") if hasattr(ctx, "tool_input") else ""
        parsed = _parse_destructive_git_cmd(cmd)
        if parsed is None:
            return False
        cwd = getattr(ctx, "cwd", None)
        if not parsed.files:
            return _git_diff_quiet(cwd, parsed.ref, None)
        return any(_git_diff_quiet(cwd, parsed.ref, f) for f in parsed.files)
    except Exception as e:
        logger.warning("_file_differs_from_ref fail-safe block: %s", e)
        return True


# Backward-compat alias — preserves config.py and user hookify files that
# still reference the legacy name. New code should call _file_differs_from_ref.
def _file_has_unstaged_changes(ctx: any) -> bool:
    return _file_differs_from_ref(ctx)


def _checkout_targets_file_with_changes(ctx: any) -> bool:
    """
    Check if 'git checkout <target>' is targeting an existing file with unstaged changes.

    Distinguishes between:
    - git checkout branch-name (safe, returns False to allow)
    - git checkout path/to/file (destructive if file has changes, returns True to block)

    Args:
        ctx: EventContext with tool_input["command"]

    Returns:
        True if checkout targets a file with unstaged changes, False otherwise
    """
    try:
        import os

        cmd = ctx.tool_input.get("command", "") if hasattr(ctx, "tool_input") else ""
        if not cmd:
            return False

        # Parse command to get target
        parts = cmd.split()
        if len(parts) < 3 or parts[0] != "git" or parts[1] != "checkout":
            return False

        # Skip if it has "--" separator (handled by different pattern)
        if "--" in parts:
            return False

        # Get the target (could be branch or file path)
        target = parts[2]

        # Special cases that are always safe
        if target in ("-b", "-B", "--track", "--orphan"):
            return False  # Branch creation flags

        # Check if target is an existing file path
        if not os.path.exists(target):
            # Not a file, probably a branch name - allow
            return False

        # It's a file — check via the hardened helper (cwd-aware, env-scrubbed,
        # ref-aware, fail-safe). HEAD-relative diff catches staged + unstaged.
        return _git_diff_quiet(getattr(ctx, "cwd", None), "HEAD", target)
    except Exception as e:
        logger.warning("_checkout_targets_file_with_changes fail-safe block: %s", e)
        return True


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
        cmd = ctx.tool_input.get("command", "") if hasattr(ctx, "tool_input") else ""
        if not cmd:
            return False  # No command, allow

        # Try bashlex for robust pipe detection (handles quotes, complex syntax)
        try:
            import bashlex
            parts = bashlex.parse(cmd)

            # Check if any part is a pipeline
            def has_pipeline(node):
                if node.kind == 'pipeline':
                    return True
                # Recursively check children
                for child in getattr(node, 'parts', []):
                    if has_pipeline(child):
                        return True
                return False

            # If any part has a pipeline, allow the command
            for part in parts:
                if has_pipeline(part):
                    return False  # In pipe - allow

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
        file_args = [
            t for t in tokens[1:]
            if not t.startswith("-")
        ]

        if file_args:
            # Has file arguments - block (return True)
            return True
        else:
            # No file arguments (reading stdin) - allow (return False)
            return False

    except Exception as e:
        logger.warning(f"_not_in_pipe predicate error: {e}")
        # On error, default to allowing (return False to not block)
        return False


def _sed_modifies_files(ctx: any) -> bool:
    """Return True only for sed invocations that edit files in place.

    Plain `sed -n ... file` and `sed 's/a/b/' file` are read/transform
    operations. The file-mutating forms are the `-i` family and GNU
    `--in-place` variants; those should use the platform edit path instead.
    """
    try:
        cmd = ctx.tool_input.get("command", "") if hasattr(ctx, "tool_input") else ""
        tokens = command_tokens_for(cmd, "sed")
        if not tokens:
            return False

        skip_next_script_arg = False
        for token in tokens[1:]:
            if skip_next_script_arg:
                skip_next_script_arg = False
                continue
            if token == "--":
                return False
            if token in {"-e", "-f", "--expression", "--file"}:
                skip_next_script_arg = True
                continue
            if token.startswith("--expression=") or token.startswith("--file="):
                continue
            if token == "--in-place" or token.startswith("--in-place="):
                return True
            if token.startswith("--"):
                continue
            if token.startswith("-") and token != "-":
                # BSD/GNU sed accept `-i`, `-i.bak`, and combined flags like `-Ei`.
                return "i" in token[1:]
            # First non-option is the script; later `-i` text is not an option.
            return False
        return False
    except Exception as e:
        logger.warning("_sed_modifies_files predicate error: %s", e)
        return False


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
        cmd = ctx.tool_input.get("command", "") if hasattr(ctx, "tool_input") else ""
        if not cmd:
            return False

        parts = cmd.split()

        # Check for --worktree or -W (explicitly destructive even with --staged)
        has_worktree = "--worktree" in parts or "-W" in parts
        for p in parts:
            if p.startswith("-") and not p.startswith("--") and "W" in p:
                has_worktree = True  # catches -SW, -WS, etc.

        # Check for --staged or -S (safe if alone, destructive if combined with -W)
        has_staged = "--staged" in parts or "-S" in parts
        for p in parts:
            if p.startswith("-") and not p.startswith("--") and "S" in p:
                has_staged = True  # catches -SW, -WS, etc.

        if has_worktree:
            # --worktree is always destructive, even with --staged
            return _file_differs_from_ref(ctx)
        if has_staged:
            # --staged only: unstages without discarding worktree → safe.
            # (With --source=<ref>, this restores the INDEX from <ref> — no
            # worktree data loss; still classified safe.)
            return False

        # Default (no flags) is --worktree, which is destructive
        return _file_differs_from_ref(ctx)
    except Exception as e:
        logger.warning("_restore_is_destructive fail-safe block: %s", e)
        return True


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
    "_restore_is_destructive": _restore_is_destructive,
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
