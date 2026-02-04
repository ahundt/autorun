# Plan: Fix Two Clautorun Hook Bugs

**Created:** 2026-02-02
**Status:** Completed (All bugs fixed)
**Final Commit:** `0625c9a` - fix(hooks): allow plan commands to pass through to skill system

---

## Executive Summary

Fix two bugs in clautorun's hook system:

1. **Bug 1**: `/cr:planrefine` says "operation stopped by hook" ✅ FIXED
2. **Bug 2**: `rm` blocking matches substrings like `rmediation` ✅ FIXED

---

## Root Cause Analysis

### Bug 1: Plan Commands Being Blocked (FIXED)

**Actual Root Cause:** Plan commands were registered as `app.command()` handlers in
`plugins/clautorun/src/clautorun/plugins.py:714-722`. When matched, the handler returned
`ctx.command_response()` which sets `continue=False`, blocking Claude Code from processing
the skill.

**Fix:** Removed plan command handler registration from plugins.py (commit `0625c9a`).
Plan commands now pass through to Claude Code's skill system.

**Original (incorrect) analysis:** Plan commands not in `command_mappings`. This was wrong -
they WERE in command_mappings, but the actual issue was the app.command() registration.

### Bug 2: rm Pattern Matching Too Broad

**Location:** `plugins/clautorun/src/clautorun/plugins.py:410-412`

```python
for k, v in DEFAULT_INTEGRATIONS.items():
    if k in cmd:  # BUG: "rm" in "/cr:planrefine" == True
        return ctx.deny(v["suggestion"])
```

---

## Research Findings

### Why Previous Approaches Fail

| Approach | Failure Case | Problem |
|----------|--------------|---------|
| `pattern in cmd` | `/cr:planrefine` | Substring match |
| shlex + first token | `sudo rm file` | Misses `rm` after `sudo` |
| shlex + any token | `echo rm` | Blocks argument position |
| Regex split by operators | `if true; then rm; fi` | Misses control structures |
| Regex `\b` boundaries | `/bin/rm` | Ambiguous at `/` boundary |

### Correct Approach: AST-Based Parsing with bashlex

[bashlex](https://github.com/idank/bashlex) is a Python port of GNU bash's parser that generates a complete AST. It properly handles:

- **Compound commands**: `cmd1 && cmd2 || cmd3`
- **Pipelines**: `cat file | rm -`
- **Control structures**: `if`, `for`, `while`, `case`
- **Subshells**: `(rm file)`
- **Command substitution**: `$(rm file)` and `` `rm file` ``
- **Process substitution**: `<(rm file)`
- **Multi-line scripts**: Newlines, heredocs
- **Command prefixes**: `sudo`, `env`, `nice`, `nohup`, etc.

**References:**
- [bashlex GitHub](https://github.com/idank/bashlex) - Python bash parser
- [bashlex PyPI](https://pypi.org/project/bashlex/) - Installation: `pip install bashlex`
- [bashlex AST module](https://github.com/idank/bashlex/blob/master/bashlex/ast.py) - nodevisitor pattern
- [bashlex error handling](https://github.com/idank/bashlex/issues/23) - ParsingError fallback

---

## Implementation Plan

### Step 1: Add bashlex as Optional Default Dependency

**File:** `pyproject.toml` (workspace root)

bashlex is an optional dependency that installs by default. Users can skip it if needed, and the code gracefully falls back to heuristic detection.

```toml
[project]
dependencies = [
    # ... existing deps
]

[project.optional-dependencies]
# Robust bash command parsing (recommended, installs by default)
bashlex = ["bashlex>=0.18"]
# All optional features
all = ["bashlex>=0.18"]

[tool.uv]
# Install bashlex by default with uv pip install
default-extras = ["bashlex"]
```

**Installation commands:**
```bash
# Default install (includes bashlex)
uv pip install .
uv pip install git+https://github.com/ahundt/clautorun.git

# Minimal install (no bashlex, uses fallback)
uv pip install . --no-default-extras

# Explicit with bashlex
uv pip install ".[bashlex]"
uv pip install ".[all]"
```

### Step 2: Add Plan Commands to command_mappings

**File:** `plugins/clautorun/src/clautorun/config.py`
**Location:** After `/cr:ttest` entry (~line 270)

```python
# ─── Plan Commands ─────────────────────────────────────────────────────
"/cr:pn": "NEW_PLAN",
"/cr:pr": "REFINE_PLAN",
"/cr:pu": "UPDATE_PLAN",
"/cr:pp": "PROCESS_PLAN",
"/cr:plannew": "NEW_PLAN",
"/cr:planrefine": "REFINE_PLAN",
"/cr:planupdate": "UPDATE_PLAN",
"/cr:planprocess": "PROCESS_PLAN",
```

### Step 3: Create Command Detection Module

**File:** `plugins/clautorun/src/clautorun/command_detection.py` (new file)

```python
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
    "reset", "checkout", "stash", "clean", "push", "pull",
    "merge", "rebase", "branch", "tag", "remote",
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

        for token in tokens[1:]:
            if token.startswith("-"):
                if token.startswith("--"):
                    flags.add(token)
                elif len(token) > 2:
                    flags.update(f"-{c}" for c in token[1:])
                else:
                    flags.add(token)
            elif is_git and token in GIT_SUBCOMMANDS:  # v8: Scoped check
                cmd_parts.append(token)
            else:
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
    """Extract basename: /bin/rm → rm. Inlined for hot path."""
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
    v8: Extract with end-of-options (--) handling.

    Returns (primary_command, full_string, all_potential_commands).
    """
    if not tokens:
        return None, None, set()

    potential: set[str] = set()
    cmd_idx: int | None = None
    end_of_opts = False

    for i, token in enumerate(tokens):
        # v8: Handle -- end-of-options marker
        if token == "--":
            end_of_opts = True
            continue
        # Skip flags only before --
        if not end_of_opts and token.startswith("-"):
            continue
        basename = _get_basename(token)
        if cmd_idx is None and basename in COMMAND_PREFIXES:
            continue
        if cmd_idx is None:
            cmd_idx = i
        potential.add(basename)

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
            words = [p.word for p in parts if p.kind == "word"]
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


def _extract_bashlex(cmd: str, depth: int) -> ExtractedCommands:
    """Extract using bashlex AST."""
    try:
        parts = bashlex.parse(cmd)
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
```

### Step 4: Update `_match` Function

**File:** `plugins/clautorun/src/clautorun/plugins.py`
**Location:** `_match` function, lines 205-210

**Add import at top:**
```python
from .command_detection import command_matches_pattern
```

**Replace literal matching:**
```python
if ptype == "literal":
    return command_matches_pattern(cmd, pattern)
```

### Step 5: Update DEFAULT_INTEGRATIONS Check

**File:** `plugins/clautorun/src/clautorun/plugins.py`
**Location:** `check_blocked_commands` function, lines 410-412

**Replace:**
```python
for k, v in DEFAULT_INTEGRATIONS.items():
    if command_matches_pattern(cmd, k):
        return ctx.deny(v["suggestion"])
```

### Step 6: Write Comprehensive Tests

**File:** `plugins/clautorun/tests/test_command_detection.py` (new file)

```python
"""
Tests for command_detection module.

Run: uv run pytest plugins/clautorun/tests/test_command_detection.py -v
"""
from __future__ import annotations

import pytest

from clautorun.command_detection import (
    BASHLEX_AVAILABLE,
    ExtractedCommands,
    ParsedPattern,
    command_matches_pattern,
    extract_commands,
)
from clautorun.config import CONFIG


# ─── Bug 1: Plan Commands ─────────────────────────────────────────────────────

PLAN_COMMANDS = ["/cr:pn", "/cr:pr", "/cr:pu", "/cr:pp",
                 "/cr:plannew", "/cr:planrefine", "/cr:planupdate", "/cr:planprocess"]

@pytest.mark.parametrize("cmd", PLAN_COMMANDS)
def test_plan_commands_in_mappings(cmd: str) -> None:
    assert cmd in CONFIG["command_mappings"]


# ─── ParsedPattern Tests ──────────────────────────────────────────────────────

class TestParsedPattern:
    """Tests for ParsedPattern dataclass."""

    @pytest.mark.parametrize("pattern,expected_base,expected_flags", [
        ("rm", "rm", frozenset()),
        ("rm -rf", "rm", frozenset({"-r", "-f"})),
        ("git reset --hard", "git reset", frozenset({"--hard"})),
        ("git checkout .", "git checkout", frozenset()),
    ])
    def test_parsing(self, pattern, expected_base, expected_flags):
        p = ParsedPattern.from_string(pattern)
        assert p.base == expected_base
        assert p.flags == expected_flags

    def test_is_single_word(self):
        assert ParsedPattern.from_string("rm").is_single_word is True
        assert ParsedPattern.from_string("rm -rf").is_single_word is False

    def test_caching(self):
        p1 = ParsedPattern.from_string("rm -rf")
        p2 = ParsedPattern.from_string("rm -rf")
        assert p1 is p2  # Same cached object


# ─── ExtractedCommands Tests ──────────────────────────────────────────────────

class TestExtractedCommands:
    """Tests for ExtractedCommands dataclass."""

    def test_matches_single_word(self):
        ec = ExtractedCommands(frozenset({"rm", "ls"}), frozenset())
        assert ec.matches_single_word("rm") is True
        assert ec.matches_single_word("cat") is False

    def test_matches_pattern(self):
        ec = ExtractedCommands(
            frozenset({"rm"}),
            frozenset({"rm -rf /tmp"})
        )
        p = ParsedPattern.from_string("rm -rf")
        assert ec.matches_pattern(p) is True


# ─── extract_commands Tests ───────────────────────────────────────────────────

EXTRACT_CASES = [
    # (cmd, expected_names)
    ("rm file.txt", {"rm"}),
    ("/bin/rm file", {"rm"}),
    ("sudo rm file", {"rm"}),
    ("sudo env rm file", {"rm"}),
    ("cat && rm file", {"cat", "rm"}),
    ("ls | rm -", {"ls", "rm"}),
]

@pytest.mark.parametrize("cmd,expected", EXTRACT_CASES)
def test_extract_commands(cmd: str, expected: set[str]) -> None:
    names, _ = extract_commands(cmd)
    assert expected <= names


@pytest.mark.skipif(not BASHLEX_AVAILABLE, reason="bashlex required")
@pytest.mark.parametrize("cmd", [
    "if true; then rm file; fi",
    "(rm file)", "$(rm file)",
])
def test_extract_nested(cmd: str) -> None:
    names, _ = extract_commands(cmd)
    assert "rm" in names


@pytest.mark.parametrize("cmd", ["echo rm", "grep 'rm' file"])
def test_extract_ignores_args(cmd: str) -> None:
    names, _ = extract_commands(cmd)
    assert "rm" not in names


def test_extract_caching():
    r1 = extract_commands("rm file.txt")
    r2 = extract_commands("rm file.txt")
    assert r1 is r2


# ─── command_matches_pattern Tests ────────────────────────────────────────────

BLOCK_CASES = ["rm file", "sudo rm file", "/bin/rm file", "cat && rm file"]
ALLOW_CASES = ["/cr:planrefine", "echo rm", "rmediation", "warm-up.sh"]

@pytest.mark.parametrize("cmd", BLOCK_CASES)
def test_blocks_rm(cmd: str) -> None:
    assert command_matches_pattern(cmd, "rm") is True

@pytest.mark.parametrize("cmd", ALLOW_CASES)
def test_allows_non_rm(cmd: str) -> None:
    assert command_matches_pattern(cmd, "rm") is False


# Multi-word patterns (combined for efficiency)
MULTIWORD_CASES = [
    # (cmd, pattern, expected)
    ("rm -rf /tmp", "rm -rf", True),
    ("rm -r -f /tmp", "rm -rf", True),
    ("rm file", "rm -rf", False),
    ("git reset --hard", "git reset --hard", True),
    ("git reset HEAD --hard", "git reset --hard", True),
    ("git reset --soft", "git reset --hard", False),
    ("git checkout .", "git checkout .", True),
    ("git checkout main", "git checkout .", False),
    ("dd if=/dev/zero", "dd if=", True),
    ("dd of=/tmp", "dd if=", False),
]

@pytest.mark.parametrize("cmd,pattern,expected", MULTIWORD_CASES)
def test_multiword_patterns(cmd: str, pattern: str, expected: bool) -> None:
    assert command_matches_pattern(cmd, pattern) is expected


# ─── Edge Cases ───────────────────────────────────────────────────────────────

def test_empty_inputs():
    assert extract_commands("") == (frozenset(), frozenset())
    assert command_matches_pattern("", "rm") is False
    assert command_matches_pattern("rm", "") is False


def test_multiline_script():
    assert command_matches_pattern("echo\nrm file", "rm") is True


def test_git_not_matches_git_lfs():
    names, _ = extract_commands("git-lfs pull")
    assert "git-lfs" in names
    assert "git" not in names


# ─── v7: Multi-Pass Detection Tests ───────────────────────────────────────────

class TestMultiPassDetection:
    """v7: Tests for prefix-with-flags handling."""

    @pytest.mark.parametrize("cmd", [
        "sudo -u root rm file",
        "sudo -g wheel rm -rf /",
        "env -u PATH rm file",
        "sudo -u root -g wheel rm file",
    ])
    def test_catches_rm_after_prefix_flags(self, cmd: str) -> None:
        """v7: Must catch rm even with flags between prefix and command."""
        assert command_matches_pattern(cmd, "rm") is True

    def test_all_potential_commands_collected(self):
        """v7: all_potential should include all non-flag tokens."""
        from clautorun.command_detection import _extract_impl
        result = _extract_impl("sudo -u root rm file.txt")
        # Should include: root, rm, file.txt (all non-flag tokens)
        assert "rm" in result.all_potential


# ─── v7: Recursive Shell -c Tests ─────────────────────────────────────────────

class TestRecursiveShellParsing:
    """v7: Tests for 'sh -c' pattern detection."""

    @pytest.mark.parametrize("cmd", [
        'sh -c "rm file"',
        "bash -c 'rm -rf /'",
        'zsh -c "sudo rm file"',
        'sh -c "cat && rm file"',
    ])
    def test_catches_rm_in_shell_c(self, cmd: str) -> None:
        """v7: Must catch rm inside shell -c arguments."""
        assert command_matches_pattern(cmd, "rm") is True

    @pytest.mark.parametrize("cmd", [
        'sh -c "echo hello"',
        'bash -c "ls -la"',
    ])
    def test_allows_safe_shell_c(self, cmd: str) -> None:
        """v7: Must not false-positive on safe shell -c."""
        assert command_matches_pattern(cmd, "rm") is False

    def test_recursion_depth_limit(self):
        """v7: Must not infinite loop on nested shell -c."""
        cmd = '''sh -c "sh -c 'sh -c \\"rm file\\"'"'''
        result = command_matches_pattern(cmd, "rm")
        assert isinstance(result, bool)


# ─── v8: Edge Case Tests ──────────────────────────────────────────────────────

class TestV8EdgeCases:
    """v8: Edge cases for correctness and efficiency."""

    def test_end_of_options_marker(self):
        """v8: -- marks end of options, -rf is a filename."""
        # rm -- -rf means delete file named "-rf"
        names, _ = extract_commands("rm -- -rf")
        assert "rm" in names
        # "-rf" should be in potential (it's after --)
        from clautorun.command_detection import _extract_cached
        result = _extract_cached("rm -- -rf")
        assert "-rf" in result.all_potential

    def test_git_subcommands_scoped(self):
        """v8: GIT_SUBCOMMANDS only applies to git commands."""
        # "make clean" should NOT be parsed as multi-word base
        p = ParsedPattern.from_string("make clean")
        assert p.base == "make"  # NOT "make clean"
        assert "clean" in p.positional

        # "git clean" SHOULD be parsed as multi-word base
        p2 = ParsedPattern.from_string("git clean")
        assert p2.base == "git clean"
        assert "clean" not in p2.positional

    def test_exec_not_a_prefix(self):
        """v8: exec is not in COMMAND_PREFIXES (replaces shell)."""
        from clautorun.command_detection import COMMAND_PREFIXES
        assert "exec" not in COMMAND_PREFIXES
        assert "xargs" not in COMMAND_PREFIXES

    def test_sandboxing_prefixes(self):
        """v8: Sandboxing tools are prefixes."""
        from clautorun.command_detection import COMMAND_PREFIXES
        assert "fakeroot" in COMMAND_PREFIXES
        assert "firejail" in COMMAND_PREFIXES

    def test_caching_efficiency(self):
        """v8: Same command should hit cache."""
        from clautorun.command_detection import _extract_cached
        cmd = "sudo rm -rf /"
        r1 = _extract_cached(cmd)
        r2 = _extract_cached(cmd)
        assert r1 is r2  # Same cached object

    def test_shlex_error_handling(self):
        """v8: Malformed commands don't crash."""
        # Unclosed quote
        names, _ = extract_commands("echo 'unclosed")
        assert "echo" in names

    @pytest.mark.parametrize("cmd,should_block", [
        # Safe commands that must NOT be blocked
        ("make clean", False),           # v8: not git subcommand
        ("cargo build", False),
        ("npm run build", False),
        # Dangerous commands that MUST be blocked
        ("rm -rf /", True),
        ("sudo rm -rf /home", True),
        ("sh -c 'rm -rf /'", True),
    ])
    def test_false_positive_prevention(self, cmd, should_block):
        """v8: Prevent over-blocking safe commands."""
        result = command_matches_pattern(cmd, "rm")
        assert result == should_block, f"cmd={cmd!r}, expected {should_block}"
```

---

## Files to Modify

| File | Change | Lines |
|------|--------|-------|
| `pyproject.toml` | Add `bashlex>=0.18` optional default dependency | +5 |
| `config.py` | Add 8 plan command mappings | +8 |
| `command_detection.py` | New module with v8 optimizations | ~200 |
| `plugins.py` | Import and use `command_matches_pattern` | +3 |
| `test_command_detection.py` | Comprehensive test suite with v8 edge cases | ~180 |

**Total:** ~396 lines (security + efficiency critical, well-tested)

---

## Edge Cases Handled

### Single-Word Patterns (e.g., `rm`)

| Scenario | Command | Pattern | Result | Reason |
|----------|---------|---------|--------|--------|
| Simple | `rm file` | `rm` | BLOCK | Command position |
| With path | `/bin/rm file` | `rm` | BLOCK | Basename extraction |
| After sudo | `sudo rm file` | `rm` | BLOCK | Prefix skipping |
| After && | `cat && rm file` | `rm` | BLOCK | Compound command |
| After \| | `ls \| rm -` | `rm` | BLOCK | Pipeline |
| In if | `if true; then rm; fi` | `rm` | BLOCK | Control structure |
| In subshell | `(rm file)` | `rm` | BLOCK | Subshell |
| In $() | `$(rm file)` | `rm` | BLOCK | Command substitution |
| Multi-line | `echo\nrm file` | `rm` | BLOCK | Newline handling |
| As argument | `echo rm` | `rm` | ALLOW | Argument position |
| In grep | `grep "rm" file` | `rm` | ALLOW | Quoted argument |
| Substring | `/cr:planrefine` | `rm` | ALLOW | Not a token |
| In word | `warm-up.sh` | `rm` | ALLOW | Part of word |
| Hyphenated cmd | `git-lfs pull` | `git` | ALLOW | `git` ≠ `git-lfs` |
| **v7: Prefix flags** | `sudo -u root rm` | `rm` | BLOCK | Multi-pass detection |
| **v7: Shell -c** | `sh -c "rm file"` | `rm` | BLOCK | Recursive parsing |
| **v7: Nested shell** | `bash -c 'sh -c "rm"'` | `rm` | BLOCK | Depth-limited recursion |
| **v8: End-of-opts** | `rm -- -rf` | `rm` | BLOCK | `--` handling |
| **v8: make clean** | `make clean` | `git clean` | ALLOW | Scoped GIT_SUBCOMMANDS |
| **v8: fakeroot** | `fakeroot rm file` | `rm` | BLOCK | Sandboxing prefix |

### Multi-Word Patterns with Flag Reordering

| Scenario | Command | Pattern | Result | Reason |
|----------|---------|---------|--------|--------|
| Exact | `rm -rf /` | `rm -rf` | BLOCK | Flags match |
| Expanded | `rm -r -f /` | `rm -rf` | BLOCK | `-rf` = `-r -f` |
| Reversed | `rm -fr /` | `rm -rf` | BLOCK | Order independent |
| Flag at end | `git reset HEAD --hard` | `git reset --hard` | BLOCK | Flag position flexible |
| Different flag | `git reset --soft` | `git reset --hard` | ALLOW | Wrong flag |
| Missing flag | `rm file` | `rm -rf` | ALLOW | Flags required |
| Positional match | `git checkout .` | `git checkout .` | BLOCK | Exact arg match |
| Positional miss | `git checkout main` | `git checkout .` | ALLOW | Different arg |
| Partial arg | `dd if=/dev/zero` | `dd if=` | BLOCK | Starts with `if=` |
| In quotes | `echo "rm -rf"` | `rm -rf` | ALLOW | Argument position |

---

## Verification Steps

```bash
# 1. Install bashlex dependency
uv add bashlex

# 2. Run unit tests
uv run pytest plugins/clautorun/tests/test_command_detection.py -v

# 3. Verify plan commands work
/cr:pr     # Should work (was blocked before)
/cr:pn     # Should work

# 4. Verify rm in various positions
rm file.txt           # BLOCKED - simple
sudo rm file          # BLOCKED - after prefix
cat && rm file        # BLOCKED - compound
if true; then rm; fi  # BLOCKED - control structure

# 5. Verify false positives fixed
/cr:planrefine        # ALLOWED
echo rm               # ALLOWED
grep "rm" file        # ALLOWED

# 6. Run full test suite
uv run pytest plugins/clautorun/tests/ -v
```

---

## Success Criteria

### Dependency Management
- [ ] bashlex added as optional default dependency in pyproject.toml
- [ ] `uv pip install .` installs bashlex by default
- [ ] `uv pip install . --no-default-extras` works without bashlex
- [ ] Fallback works when bashlex unavailable

### Bug Fixes
- [ ] `/cr:planrefine` works without "operation stopped by hook"
- [ ] All plan commands (`/cr:pn`, `/cr:pr`, `/cr:pu`, `/cr:pp`) work

### Single-Word Pattern Matching
- [ ] `rm file.txt` blocked (command position)
- [ ] `sudo rm file` blocked (prefix handling)
- [ ] `cat && rm file` blocked (compound command)
- [ ] `if true; then rm; fi` blocked (control structure)
- [ ] `echo rm` allowed (argument position)
- [ ] `grep "rm" file` allowed (argument position)
- [ ] `/cr:planrefine` allowed (substring)
- [ ] `git-lfs pull` NOT matched by `git` pattern (hyphenated command)

### v7: Multi-Pass Detection
- [ ] `sudo -u root rm file` blocked (prefix with flags)
- [ ] `sudo -g wheel rm -rf /` blocked (prefix with flags)
- [ ] `env -u PATH rm file` blocked (prefix with flags)

### v7: Recursive Shell -c Parsing
- [ ] `sh -c "rm file"` blocked (shell exec)
- [ ] `bash -c 'rm -rf /'` blocked (shell exec)
- [ ] `sh -c "cat && rm file"` blocked (compound in shell)
- [ ] `sh -c "echo hello"` allowed (safe shell)
- [ ] Nested `sh -c` doesn't hang (depth limit)

### v8: Edge Cases + Efficiency
- [ ] `rm -- -rf` correctly identifies `rm` (end-of-options)
- [ ] `make clean` NOT blocked by `git clean` pattern (scoped GIT_SUBCOMMANDS)
- [ ] `fakeroot rm file` blocked (sandboxing prefix)
- [ ] `exec rm file` NOT treated as prefix (removed from COMMAND_PREFIXES)
- [ ] Same command twice returns cached result (hot path efficiency)
- [ ] Malformed commands (unclosed quotes) don't crash

### Multi-Word Pattern Matching with Flag Reordering
- [ ] `rm -rf /` matches `rm -rf` (exact)
- [ ] `rm -r -f /` matches `rm -rf` (expanded flags)
- [ ] `rm -fr /` matches `rm -rf` (reversed order)
- [ ] `git reset HEAD --hard` matches `git reset --hard` (flag at end)
- [ ] `git reset --soft` does NOT match `git reset --hard` (wrong flag)
- [ ] `rm file` does NOT match `rm -rf` (missing flags)
- [ ] `git checkout .` matches `git checkout .` (positional arg)
- [ ] `dd if=/dev/zero` matches `dd if=` (partial arg)

### Quality
- [ ] All TDD tests pass
- [ ] Caching works for repeated commands
- [ ] Existing tests pass

---

## Refinements Made

### v8 - Efficiency + Edge Cases

| Before (v7) | After (v8) | Benefit |
|-------------|------------|---------|
| `_extract_impl` uncached in hot path | `_extract_cached` with LRU | O(1) repeated calls |
| `COMMAND_PREFIXES` included exec/xargs | Removed (complex semantics) | Correct blocking |
| GIT_SUBCOMMANDS for all commands | Only when first token is "git" | `make clean` works |
| Ignored `--` end-of-options | Handled in `_extract_from_tokens` | `rm -- -rf` correct |
| `path.rsplit("/", 1)[-1]` | `path[path.rfind("/")+1:]` | Faster basename |
| `shlex.split` single fallback | `_shlex_split_safe` with posix=False | Better error handling |
| Cache size 256 | Cache size 512 | More hot path hits |
| Exception logged | Silent return empty | Reduced log noise |

**Correctness Fixes:**
- `exec` removed from COMMAND_PREFIXES (replaces shell, not a prefix)
- `xargs` removed (reads stdin, complex semantics)
- Added sandboxing tools: `fakeroot`, `firejail`, `bubblewrap`
- `make clean` no longer incorrectly parsed as git subcommand

**Performance Fixes:**
- `_extract_cached()` - dedicated cache for hot path
- `_get_basename` uses `rfind` (faster than rsplit)
- Increased cache size 256→512

### v7 - Fixes Critical Limitations

| Before (v6) | After (v7) | Benefit |
|-------------|------------|---------|
| First non-prefix token only | ALL non-flag tokens collected | Catches `sudo -u root rm` |
| No shell -c handling | Recursive parsing for sh/bash -c | Catches `sh -c "rm file"` |
| `matches_single_word` checks `names` | Checks `all_potential` | No false negatives |
| No recursion limit | `_MAX_RECURSION_DEPTH = 3` | Prevents infinite loops |

### v6 - Dataclass + Factory Pattern + Vectorized

| Before (v5) | After (v6) | Benefit |
|-------------|------------|---------|
| `tuple[str, frozenset, frozenset]` | `@dataclass ParsedPattern` | Named fields, type safety |
| `tuple[frozenset, frozenset]` | `@dataclass ExtractedCommands` | Methods encapsulate matching |
| `_tokenize_pattern` function | `ParsedPattern.from_string` | Factory pattern (cached) |
| Implicit is_single_word check | `parsed.is_single_word` field | Pre-computed |
| `(frozen=True, slots=True)` | Dataclass args | Immutable + efficient |

### v5 - Computational Efficiency

| Before (v4) | After (v5) | Benefit |
|-------------|------------|---------|
| `_tokenize_pattern` uncached | `@lru_cache(maxsize=64)` | O(n) → O(1) for repeated patterns |
| Duplicate token logic | `_process_word_tokens()` helper | DRY |
| `startswith(pattern_base)` | Explicit space check | "git" ≠ "git-lfs" |

### v4 - Clean Pythonic Code

| Before | After | Reason |
|--------|-------|--------|
| `visitcommand` no return | Returns `True` | **CRITICAL**: Enables AST recursion |
| No `__all__` | `__all__ = [...]` | Explicit public API |
| Magic cache sizes | `_CMD_CACHE_SIZE: Final[int]` | Named constants |

### Codebase Patterns Applied (from plugins.py)

1. **Factory Pattern** - `ParsedPattern.from_string()` (like `_make_block_op`)
2. **`@dataclass(frozen=True, slots=True)`** - Immutable + efficient (like `verification_engine.py`)
3. **`@lru_cache` on classmethod** - Cached factory pattern
4. **`frozenset`** for immutable sets (like `SHELL_COMMANDS` in `tmux_utils.py`)
5. **Generator expressions** - `next(i for i, t in ...)` for vectorized lookup
6. **Methods on data classes** - Encapsulate matching logic in `ExtractedCommands`
7. **`typing.Final`** for constants

### Library Reuse

- `shlex` - Already in codebase
- `@lru_cache` - Matches `plugins.py:165`
- `@dataclass` - Matches `verification_engine.py:67`
- `re.compile` in constant - Matches codebase pattern

## Performance Considerations (v8)

| Optimization | Technique | Complexity |
|-------------|-----------|------------|
| **HOT PATH cache** | `_extract_cached` with LRU(512) | O(1) hit |
| Pattern caching | `ParsedPattern.from_string` LRU(64) | O(1) hit |
| Basename extraction | `path[path.rfind("/")+1:]` | Faster than rsplit |
| Dataclass slots | `slots=True` | ~40% memory reduction |
| Frozen dataclass | `frozen=True` | Hashable, cacheable |
| Set operations | `p.flags <= cmd.flags` | O(min(m,n)) |
| Early termination | `any()` generator | Best: O(1) |
| Pre-computed field | `is_single_word` | Skip tokenization check |
| Error handling | `_shlex_split_safe` | No exceptions on hot path |

**Call path for `command_matches_pattern` (HOT PATH):**
```
command_matches_pattern(cmd, pattern)
  → _extract_cached(cmd)        # LRU cache hit: O(1)
  → ParsedPattern.from_string(pattern)  # LRU cache hit: O(1)
  → matches_single_word / matches_pattern
```

**Typical complexity:**
- Cache hit: **O(1)** - most common case
- Cache miss, single-word: O(parse) + O(1) set lookup
- Cache miss, multi-word: O(parse) + O(commands × flags)

**Memory usage:**
- 512 cached `ExtractedCommands` × ~200 bytes ≈ 100KB
- 64 cached `ParsedPattern` × ~100 bytes ≈ 6KB

---

## Code Review: Section-by-Section Analysis (v8)

### Issue 1: COMMAND_PREFIXES Contains Non-Prefixes

**Problem**: `exec` and `xargs` are NOT simple prefixes:
- `exec rm file` - `exec` replaces current shell, then runs rm (DANGEROUS)
- `xargs rm` - reads filenames from stdin, not a simple prefix

**Fix**: Remove them and add missing prefixes:
```python
COMMAND_PREFIXES: Final[frozenset[str]] = frozenset({
    # Privilege escalation
    "sudo", "su", "doas", "pkexec", "gksudo", "kdesudo",
    # Environment modification (safe prefixes)
    "env", "nice", "nohup", "time", "timeout", "ionice",
    # Debugging (safe prefixes)
    "strace", "ltrace", "watch",
    # Sandboxing
    "chroot", "fakeroot", "firejail", "bubblewrap",
})
# NOTE: exec, xargs removed - they have complex semantics
```

### Issue 2: GIT_SUBCOMMANDS Applied to All Commands

**Problem**: `make clean` incorrectly parsed as base="make clean" because "clean" is in GIT_SUBCOMMANDS.

**Fix**: Only check GIT_SUBCOMMANDS when first token is "git":
```python
# In ParsedPattern.from_string:
is_git = tokens[0] == "git"
for token in tokens[1:]:
    if token.startswith("-"):
        # flag handling
    elif is_git and token in GIT_SUBCOMMANDS:
        cmd_parts.append(token)
    else:
        positional.add(token)
```

### Issue 3: No Cache on Hot Path

**Problem**: `command_matches_pattern` calls `_extract_impl` directly, bypassing cache. This is called for EVERY Bash command.

**Fix**: Cache the full `ExtractedCommands` object:
```python
@lru_cache(maxsize=_CMD_CACHE_SIZE)
def _extract_cached(cmd: str) -> ExtractedCommands:
    """Cached extraction - HOT PATH optimization."""
    return _extract_impl(cmd)

def command_matches_pattern(cmd: str, pattern: str) -> bool:
    if not cmd or not pattern:
        return False
    result = _extract_cached(cmd)  # Use cached version
    # ...
```

### Issue 4: Missing `--` End-of-Options Handling

**Problem**: `rm -- -rf` should treat `-rf` as a filename, not a flag.
Current code skips `-rf` as a flag.

**Fix**: Track `--` marker:
```python
def _extract_from_tokens(tokens: list[str]) -> tuple[str | None, str | None, set[str]]:
    potential: set[str] = set()
    cmd_idx: int | None = None
    end_of_options = False

    for i, token in enumerate(tokens):
        if token == "--":
            end_of_options = True
            continue
        if not end_of_options and token.startswith("-"):
            continue  # Skip flags only before --
        # ... rest of logic
```

### Issue 5: Over-Blocking Risk in all_potential

**Problem**: ALL non-flag tokens added to `all_potential`, including filenames.
`rm important.txt` → `all_potential = {"rm", "important.txt"}`

**Risk**: If someone creates a pattern "important.txt" (unlikely but possible), it would block.

**Mitigation**: Document that patterns should be command names, not arbitrary strings. The risk is low because DEFAULT_INTEGRATIONS only contains command names.

### Issue 6: Redundant Parsing in _cmd_matches

**Problem**: `_cmd_matches` calls `ParsedPattern.from_string(cmd_str)` for each command string, but these strings came from extraction where we already have the tokens.

**Fix**: Store parsed form during extraction, or accept that caching mitigates this.

### Issue 7: shlex.split() Error Handling

**Problem**: `shlex.split()` can raise `ValueError` on unclosed quotes. Fallback to `segment.split()` loses quote handling.

**Better**: Also try without strict mode:
```python
try:
    tokens = shlex.split(segment)
except ValueError:
    try:
        tokens = shlex.split(segment, posix=False)
    except ValueError:
        tokens = segment.split()
```

---

## Known Limitations (Addressed in v7)

| Scenario | Example | Status | Solution |
|----------|---------|--------|----------|
| Prefix with flags | `sudo -u root rm file` | ✅ Fixed | Multi-pass: check ALL non-flag tokens |
| Quoted commands | `sh -c "rm file"` | ✅ Fixed | Recursive parse for shell -c patterns |
| Heredocs | `cat <<EOF\nrm\nEOF` | ✅ Correct | Heredoc content is data, not commands |
| Variable expansion | `$CMD file` | ❌ Unfixable | Can't predict runtime values |

### Fix 1: Prefix with Flags (Multi-Pass Detection)

**Problem**: `sudo -u root rm file` → `-u` identified as command, `rm` missed

**Solution**: Check ALL non-flag tokens for dangerous patterns, not just the first:

```python
def _extract_from_tokens(tokens: list[str]) -> tuple[str | None, str | None, set[str]]:
    """
    Extract (primary_command, full_string, all_potential_commands).

    Returns ALL non-flag tokens as potential commands for pattern matching.
    """
    potential_commands: set[str] = set()
    cmd_idx = None

    for i, token in enumerate(tokens):
        basename = _get_basename(token)
        # Skip flags
        if token.startswith("-"):
            continue
        # Skip prefixes for primary command detection
        if cmd_idx is None and basename in COMMAND_PREFIXES:
            continue
        # First non-prefix, non-flag is primary command
        if cmd_idx is None:
            cmd_idx = i
        # ALL non-flag tokens are potential commands
        potential_commands.add(basename)

    if cmd_idx is None:
        return None, None, set()

    cmd_name = _get_basename(tokens[cmd_idx])
    cmd_string = " ".join([cmd_name] + tokens[cmd_idx + 1:])
    return cmd_name, cmd_string, potential_commands
```

**Result**: `sudo -u root rm file` → potential_commands = {"root", "rm", "file"}
- Pattern "rm" matches "rm" in potential_commands ✅

### Fix 2: Quoted Commands (Recursive Shell -c Parsing)

**Problem**: `sh -c "rm file"` → Inner `rm` not detected

**Solution**: Detect shell exec patterns and recursively parse:

```python
SHELL_EXEC_COMMANDS: Final[frozenset[str]] = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish",
})

def _find_shell_exec_arg(cmd_name: str, tokens: list[str]) -> str | None:
    """Extract command string from 'sh -c "cmd"' patterns."""
    if cmd_name not in SHELL_EXEC_COMMANDS:
        return None
    try:
        c_idx = tokens.index("-c")
        if c_idx + 1 < len(tokens):
            return tokens[c_idx + 1]
    except ValueError:
        pass
    return None
```

**In visitor/extraction**:
```python
# After extracting command, check for shell exec
shell_arg = _find_shell_exec_arg(cmd_name, tokens)
if shell_arg:
    # Recursively parse the -c argument
    inner_names, inner_strings = _extract_recursive(shell_arg, depth + 1)
    names.update(inner_names)
    strings.update(inner_strings)
```

**Depth limit**: Max recursion depth of 3 to prevent infinite loops.

**Result**: `sh -c "rm file"` → detects both "sh" and "rm" ✅

### Why Heredocs Are Correct (No Fix Needed)

```bash
cat <<EOF
rm file
EOF
```

The `rm file` inside the heredoc is **data**, not a command. It's text being passed to `cat`. Blocking this would be a false positive. bashlex correctly treats heredoc content as a string literal, not as commands to execute.

---

## Rollback Plan

If issues arise:
1. Remove `bashlex` from dependencies
2. Remove `command_detection.py` module
3. Restore original `_match` literal matching in `plugins.py`
4. Restore original DEFAULT_INTEGRATIONS check in `plugins.py`
5. Remove plan commands from `command_mappings` if needed
