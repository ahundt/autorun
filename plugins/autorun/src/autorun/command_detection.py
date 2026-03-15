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
Robust command detection using bashlex AST parsing.

v8 Features:
- Multi-pass detection: catches rm in "sudo -u root rm file"
- Recursive shell -c parsing: catches rm in "sh -c 'rm file'"
- HOT PATH caching: `_extract_cached` for command_matches_pattern
- End-of-options (--) handling
- Fixed GIT_SUBCOMMANDS scope

References:
- https://github.com/idank/bashlex
- https://github.com/idank/bashlex/blob/master/bashlex/ast.py
"""
from __future__ import annotations

import logging
import re
import shlex
from dataclasses import dataclass
from functools import lru_cache
from typing import Final

__all__ = [
    "command_matches_pattern",
    "extract_commands",
    "ParsedPattern",
    "ExtractedCommands",
    "BASHLEX_AVAILABLE",
]

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

# v8: Removed exec/xargs (complex semantics), added sandboxing tools
COMMAND_PREFIXES: Final[frozenset[str]] = frozenset({
    # Privilege escalation
    "sudo", "su", "doas", "pkexec", "gksudo", "kdesudo",
    # Environment modification
    "env", "nice", "nohup", "time", "timeout", "ionice",
    # Debugging/tracing
    "strace", "ltrace", "watch",
    # Sandboxing
    "chroot", "fakeroot", "firejail", "bubblewrap",
})

GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset({
    "add", "commit", "reset", "checkout", "stash", "clean",
    "push", "pull", "fetch", "merge", "rebase",
    "branch", "tag", "remote", "log", "diff", "status",
})

SHELL_EXEC_COMMANDS: Final[frozenset[str]] = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish",
})

_SHELL_OPERATORS: Final[re.Pattern[str]] = re.compile(r"\s*(?:&&|\|\||[|;&\n])\s*")
_CMD_CACHE_SIZE: Final[int] = 512   # v8: Increased for hot path
_PATTERN_CACHE_SIZE: Final[int] = 64
_MAX_RECURSION_DEPTH: Final[int] = 3


# ─── Optional bashlex ─────────────────────────────────────────────────────────

try:
    import bashlex
    from bashlex import ast as bashlex_ast
    from bashlex.errors import ParsingError
    BASHLEX_AVAILABLE: Final[bool] = True
except ImportError:
    BASHLEX_AVAILABLE: Final[bool] = False
    bashlex = None  # type: ignore[assignment]
    bashlex_ast = None  # type: ignore[assignment]
    ParsingError = Exception  # type: ignore[misc,assignment]


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ParsedPattern:
    """Immutable parsed pattern for efficient matching."""
    base: str
    flags: frozenset[str]
    positional: frozenset[str]
    is_single_word: bool

    @classmethod
    @lru_cache(maxsize=_PATTERN_CACHE_SIZE)
    def from_string(cls, pattern: str) -> "ParsedPattern":
        """Factory: Parse pattern string into structured form (cached)."""
        tokens = pattern.split()
        if not tokens:
            return cls("", frozenset(), frozenset(), True)

        first = tokens[0]
        is_git = first == "git"  # v8: Only apply GIT_SUBCOMMANDS for git
        cmd_parts, flags, positional = [first], set(), set()
        seen_non_flag = False  # Track if we've seen any non-flag token

        for token in tokens[1:]:
            if token.startswith("-"):
                if token.startswith("--"):
                    flags.add(token)
                elif len(token) > 2:
                    flags.update(f"-{c}" for c in token[1:])
                else:
                    flags.add(token)
            elif is_git and not seen_non_flag and token in GIT_SUBCOMMANDS:
                # Only add FIRST non-flag token if it's a git subcommand
                cmd_parts.append(token)
                seen_non_flag = True
            else:
                # Any other non-flag token (including git subcommands after first position)
                seen_non_flag = True
                positional.add(token)

        return cls(
            " ".join(cmd_parts),
            frozenset(flags),
            frozenset(positional),
            len(tokens) == 1
        )


@dataclass(frozen=True, slots=True)
class ExtractedCommands:
    """Immutable extraction result with all potential commands."""
    names: frozenset[str]
    strings: frozenset[str]
    all_potential: frozenset[str]

    def matches_single_word(self, pattern: str) -> bool:
        """O(1) lookup in all potential commands."""
        return pattern in self.all_potential

    def matches_pattern(self, p: ParsedPattern) -> bool:
        """Check if any command string matches parsed pattern."""
        return any(self._cmd_matches(cs, p) for cs in self.strings)

    def _cmd_matches(self, cmd_str: str, p: ParsedPattern) -> bool:
        """Match command string against parsed pattern."""
        cmd = ParsedPattern.from_string(cmd_str)
        # Base: exact or prefix with space
        if cmd.base != p.base and not cmd.base.startswith(p.base + " "):
            return False
        # Flags: all pattern flags must be present
        if not p.flags <= cmd.flags:
            return False
        # Positional: exact or prefix match for "dd if="
        for pos in p.positional:
            if pos.endswith("="):
                if not any(cp.startswith(pos) for cp in cmd.positional):
                    return False
            elif pos not in cmd.positional:
                return False
        return True


# ─── Extraction Helpers ───────────────────────────────────────────────────────

def _get_basename(path: str) -> str:
    """Extract basename: /bin/rm -> rm. Inlined for hot path."""
    idx = path.rfind("/")
    return path[idx + 1:] if idx >= 0 else path


def _find_shell_exec_arg(cmd_name: str, tokens: list[str]) -> str | None:
    """Extract command string from 'sh -c "cmd"' patterns."""
    if cmd_name not in SHELL_EXEC_COMMANDS:
        return None
    try:
        c_idx = tokens.index("-c")
        return tokens[c_idx + 1] if c_idx + 1 < len(tokens) else None
    except (ValueError, IndexError):
        return None


def _shlex_split_safe(segment: str) -> list[str]:
    """v8: Robust shlex.split with fallbacks."""
    try:
        return shlex.split(segment)
    except ValueError:
        try:
            return shlex.split(segment, posix=False)
        except ValueError:
            return segment.split()


def _extract_from_tokens(tokens: list[str]) -> tuple[str | None, str | None, set[str]]:
    """
    v8: Extract with end-of-options (--) handling and multi-pass detection.

    Returns (primary_command, full_string, all_potential_commands).

    Logic for all_potential:
    - If first command is a prefix (sudo, env), ALL subsequent non-flag tokens
      are potential commands (multi-pass detection for "sudo -u root rm")
    - If first command is NOT a prefix, only that command is in potential
      (prevents false positives like "echo rm" blocking rm)
    - Exception: -- marker resets, tokens after it are included

    Examples:
    - "sudo -u root rm file" -> potential={root, rm, file} (multi-pass, rm will match)
    - "echo rm" -> potential={echo} (echo is not a prefix, so rm is just an arg)
    - "cat && rm file" -> handled by _extract_recursive, each segment separately
    """
    if not tokens:
        return None, None, set()

    potential: set[str] = set()
    cmd_idx: int | None = None
    end_of_opts = False
    saw_prefix = False  # Did we see a prefix command? If so, enable multi-pass

    for i, token in enumerate(tokens):
        # v8: Handle -- end-of-options marker
        if token == "--":
            end_of_opts = True
            continue
        # Skip flags only before --
        if not end_of_opts and token.startswith("-"):
            continue

        basename = _get_basename(token)

        if cmd_idx is None:
            # Looking for the primary command
            if basename in COMMAND_PREFIXES:
                saw_prefix = True
                continue
            # Found a non-prefix token - this is a potential command
            cmd_idx = i
            potential.add(basename)
        elif saw_prefix:
            # Multi-pass mode: we saw a prefix, so all subsequent non-flag tokens
            # are potential commands (handles "sudo -u root rm file")
            potential.add(basename)
        elif end_of_opts:
            # After --, tokens could be filenames that match command patterns
            potential.add(basename)
        # else: regular command without prefix, don't add arguments to potential

    if cmd_idx is None:
        return None, None, set()

    cmd_name = _get_basename(tokens[cmd_idx])
    # v8: Build command string more efficiently
    rest = tokens[cmd_idx + 1:]
    cmd_string = f"{cmd_name} {' '.join(rest)}" if rest else cmd_name
    return cmd_name, cmd_string, potential


# ─── Recursive Extraction ─────────────────────────────────────────────────────

def _extract_recursive(cmd: str, depth: int) -> tuple[set[str], set[str], set[str]]:
    """Recursively extract commands, including from 'sh -c' patterns."""
    if depth >= _MAX_RECURSION_DEPTH:
        return set(), set(), set()

    names, strings, potential = set(), set(), set()

    for segment in _SHELL_OPERATORS.split(cmd):
        if not (segment := segment.strip()):
            continue

        tokens = _shlex_split_safe(segment)
        if not tokens:
            continue

        name, string, pot = _extract_from_tokens(tokens)
        if name:
            names.add(name)
            potential.update(pot)

            # Recursively parse shell -c arguments
            shell_arg = _find_shell_exec_arg(name, tokens)
            if shell_arg:
                n, s, p = _extract_recursive(shell_arg, depth + 1)
                names.update(n)
                strings.update(s)
                potential.update(p)

        if string:
            strings.add(string)

    return names, strings, potential


# ─── bashlex Visitor ──────────────────────────────────────────────────────────

if BASHLEX_AVAILABLE:
    class CommandVisitor(bashlex_ast.nodevisitor):
        """AST visitor with multi-pass and recursive shell -c parsing."""
        __slots__ = ("names", "strings", "potential", "depth")

        def __init__(self, depth: int = 0) -> None:
            self.names: set[str] = set()
            self.strings: set[str] = set()
            self.potential: set[str] = set()
            self.depth = depth

        def visitcommand(self, node, parts) -> bool:
            # Filter out words containing newlines — these are heredoc bodies
            # or command substitutions with embedded content, not real arguments.
            # Prevents false positives like "git restore" matching inside a
            # commit message heredoc: git commit -m "$(cat <<EOF\n...restore...\nEOF)"
            words = [p.word for p in parts if p.kind == "word" and "\n" not in p.word]
            name, string, pot = _extract_from_tokens(words)

            if name:
                self.names.add(name)
                self.potential.update(pot)

                if self.depth < _MAX_RECURSION_DEPTH:
                    shell_arg = _find_shell_exec_arg(name, words)
                    if shell_arg:
                        inner = _extract_impl(shell_arg, self.depth + 1)
                        self.names.update(inner.names)
                        self.strings.update(inner.strings)
                        self.potential.update(inner.all_potential)

            if string:
                self.strings.add(string)

            return True


def _normalize_heredoc_delimiters(cmd: str) -> str:
    """
    Remove quotes from heredoc delimiters for bashlex compatibility.

    Bashlex expects unquoted heredoc delimiters, but shell scripts often use
    quoted delimiters like << 'EOF' or << "END". This function normalizes them
    to the unquoted form that bashlex can parse.

    Examples:
        python3 << 'EOF' → python3 << EOF
        cat << "END" → cat << END
        sh << EOF → sh << EOF (no change)

    Args:
        cmd: Shell command string potentially containing heredoc

    Returns:
        Normalized command string with unquoted heredoc delimiters
    """
    # Match << followed by optional whitespace and quoted delimiter
    # Capture the delimiter content (without quotes)
    pattern = r'<<\s*(["\'])(\w+)\1'
    return re.sub(pattern, r'<< \2', cmd)


def _extract_bashlex(cmd: str, depth: int) -> ExtractedCommands:
    """Extract using bashlex AST."""
    try:
        # Normalize heredoc delimiters for bashlex compatibility
        normalized_cmd = _normalize_heredoc_delimiters(cmd)
        parts = bashlex.parse(normalized_cmd)
    except (ParsingError, Exception):
        return ExtractedCommands(frozenset(), frozenset(), frozenset())

    visitor = CommandVisitor(depth)
    for part in parts:
        visitor.visit(part)

    return ExtractedCommands(
        frozenset(visitor.names),
        frozenset(visitor.strings),
        frozenset(visitor.potential),
    )


def _extract_fallback(cmd: str, depth: int) -> ExtractedCommands:
    """Fallback extraction using shlex."""
    n, s, p = _extract_recursive(cmd, depth)
    return ExtractedCommands(frozenset(n), frozenset(s), frozenset(p))


def _extract_impl(cmd: str, depth: int = 0) -> ExtractedCommands:
    """Internal extraction dispatcher."""
    if BASHLEX_AVAILABLE:
        result = _extract_bashlex(cmd, depth)
        if result.names or result.strings:
            return result
    return _extract_fallback(cmd, depth)


# ─── Public API (HOT PATH) ────────────────────────────────────────────────────

@lru_cache(maxsize=_CMD_CACHE_SIZE)
def _extract_cached(cmd: str) -> ExtractedCommands:
    """v8: Cached extraction - HOT PATH for command_matches_pattern."""
    return _extract_impl(cmd)


@lru_cache(maxsize=_CMD_CACHE_SIZE)
def extract_commands(cmd: str) -> tuple[frozenset[str], frozenset[str]]:
    """
    Extract command names and full strings from bash command.

    Returns:
        (command_names, command_strings) as frozensets
    """
    if not cmd or not cmd.strip():
        return frozenset(), frozenset()
    result = _extract_cached(cmd)
    return result.names, result.strings


def command_matches_pattern(cmd: str, pattern: str) -> bool:
    """
    v8: Check if pattern matches a command in the bash string.

    HOT PATH - called for every Bash command. Uses cached extraction.
    """
    if not cmd or not pattern:
        return False

    result = _extract_cached(cmd)  # v8: Use cached version
    parsed = ParsedPattern.from_string(pattern)

    if parsed.is_single_word:
        return result.matches_single_word(parsed.base)
    return result.matches_pattern(parsed)
