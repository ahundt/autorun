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
- File-based user extensions (.claude/clautorun.*.local.md)
- All hookify features (conditions, event, tool_matcher)
- Multiple patterns per file
- Redirect with arg substitution
- Semantic when predicates (Python or bash)
- O(1) cached file loading (mtime-based)
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Final

from clautorun.config import CONFIG, DEFAULT_INTEGRATIONS

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
    - User runs /cr:reload command
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
    search_paths = CONFIG.get("integration_search_paths", [".claude/clautorun.*.local.md"])

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


def _has_unstaged_changes(ctx: any) -> bool:
    """Check if git has unstaged changes."""
    try:
        result = subprocess.run(
            "git diff --quiet",
            shell=True,
            capture_output=True,
            timeout=2
        )
        return result.returncode != 0
    except Exception:
        return False


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


def _file_has_unstaged_changes(ctx: any) -> bool:
    """
    Check if a specific file has unstaged changes.

    Extracts file path from command (e.g., "git checkout -- file.txt")
    and checks if that file has unstaged changes.

    Args:
        ctx: EventContext with tool_input["command"]

    Returns:
        True if file has unstaged changes, False otherwise
    """
    try:
        # Extract file path from command
        cmd = ctx.tool_input.get("command", "") if hasattr(ctx, "tool_input") else ""
        if not cmd:
            return False

        # Parse: "git checkout -- <file>" or "git checkout -- <file1> <file2>"
        parts = cmd.split()
        if "--" in parts:
            idx = parts.index("--")
            files = parts[idx + 1:]
        else:
            # Fallback: last arg is file
            files = parts[-1:] if len(parts) > 2 else []

        if not files:
            return _has_unstaged_changes(ctx)  # Fallback to any unstaged

        # Check if any specified file has unstaged changes
        for file_path in files:
            result = subprocess.run(
                f"git diff --quiet -- {file_path}",
                shell=True,
                capture_output=True,
                timeout=2
            )
            if result.returncode != 0:
                return True
        return False
    except Exception:
        return False


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

        # It's a file - check if it has unstaged changes
        result = subprocess.run(
            f"git diff --quiet -- {target}",
            shell=True,
            capture_output=True,
            timeout=2
        )
        return result.returncode != 0  # Non-zero = has changes
    except Exception:
        return False


def _not_in_pipe(ctx: any) -> bool:
    """
    Check if command is NOT in a pipe context (should block for direct file operations).

    Uses bashlex for robust pipe detection when available, with simple fallback.

    Returns True when command should be blocked (NOT in pipe).
    Returns False when command should be allowed (in pipe or reading stdin).

    Examples:
        - `head file.txt` → NOT in pipe → return True (block)
        - `git diff | head -50` → in pipe → return False (allow)
        - `head -50` (no file) → NOT in pipe but stdin → return False (allow)
        - `ps aux | grep foo && echo done` → has pipe → return False (allow)

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


# Predicate functions (O(1) lookups) - FIX Bug 1
_WHEN_PREDICATES: Final[dict] = {
    "has_uncommitted_changes": _has_uncommitted_changes,
    "_has_uncommitted_changes": _has_uncommitted_changes,  # Backward compat
    "_has_unstaged_changes": _has_unstaged_changes,
    "_stash_exists": _stash_exists,
    "_file_has_unstaged_changes": _file_has_unstaged_changes,
    "_checkout_targets_file_with_changes": _checkout_targets_file_with_changes,  # v0.8.0: Catch git checkout <file>
    "_not_in_pipe": _not_in_pipe,  # v0.8.0: Context-aware blocking for head/tail/grep/cat
    # Add more as needed
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
