#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for command_detection module.

Run: uv run pytest plugins/autorun/tests/test_command_detection.py -v
"""
from __future__ import annotations

import pytest

from autorun.command_detection import (
    BASHLEX_AVAILABLE,
    ExtractedCommands,
    ParsedPattern,
    command_matches_pattern,
    extract_commands,
)
from autorun.config import CONFIG


# ─── Bug 1: Plan Commands ─────────────────────────────────────────────────────

PLAN_COMMANDS = ["/ar:pn", "/ar:pr", "/ar:pu", "/ar:pp",
                 "/ar:plannew", "/ar:planrefine", "/ar:planupdate", "/ar:planprocess"]

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
        ec = ExtractedCommands(
            frozenset({"rm", "ls"}),
            frozenset({"rm file.txt", "ls -la"}),
            frozenset({"rm", "ls", "file.txt"})
        )
        assert ec.matches_single_word("rm") is True
        assert ec.matches_single_word("cat") is False

    def test_matches_pattern(self):
        ec = ExtractedCommands(
            frozenset({"rm"}),
            frozenset({"rm -rf /tmp"}),
            frozenset({"rm", "/tmp"})
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
ALLOW_CASES = ["/ar:planrefine", "echo rm", "rmediation", "warm-up.sh"]

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
        """v7: all_potential should include commands found after prefixes.

        Multi-pass detection for prefix commands (sudo, env, etc.) includes
        ALL subsequent non-flag tokens as potential commands. This catches
        cases like "sudo -u root rm file" where rm is the actual command.

        Trade-off: We also include arguments like "file.txt" but that's
        acceptable because they won't match any dangerous pattern.
        """
        from autorun.command_detection import _extract_impl
        result = _extract_impl("sudo -u root rm file.txt")
        # Must include rm (the actual dangerous command)
        assert "rm" in result.all_potential
        # Multi-pass mode includes all tokens after prefix (root, rm, file.txt)
        # This is by design - rm MUST be caught even with args between sudo and rm
        assert "root" in result.all_potential  # sudo's -u argument
        assert "file.txt" in result.all_potential  # rm's argument (harmless)


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
        # rm is the command, and after --, -rf is treated as a potential command/file
        from autorun.command_detection import _extract_cached
        result = _extract_cached("rm -- -rf")
        # The primary command (rm) should be in potential
        assert "rm" in result.all_potential
        # After --, tokens are included in potential (since they could be files
        # that happen to match command names in edge cases)
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
        from autorun.command_detection import COMMAND_PREFIXES
        assert "exec" not in COMMAND_PREFIXES
        assert "xargs" not in COMMAND_PREFIXES

    def test_sandboxing_prefixes(self):
        """v8: Sandboxing tools are prefixes."""
        from autorun.command_detection import COMMAND_PREFIXES
        assert "fakeroot" in COMMAND_PREFIXES
        assert "firejail" in COMMAND_PREFIXES

    def test_caching_efficiency(self):
        """v8: Same command should hit cache."""
        from autorun.command_detection import _extract_cached
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


# ─── Integration Test: Bug 2 Fix ──────────────────────────────────────────────

class TestBug2SubstringFix:
    """Verify Bug 2 is fixed: rm doesn't match substrings."""

    @pytest.mark.parametrize("safe_cmd", [
        "/ar:planrefine",
        "/ar:pr",
        "rmediation",
        "warm-up.sh",
        "perform",
        "reformatting",
        "rm_backup.txt",  # filename starting with rm
    ])
    def test_safe_commands_not_blocked(self, safe_cmd: str) -> None:
        """Commands containing 'rm' as substring should NOT be blocked."""
        assert command_matches_pattern(safe_cmd, "rm") is False

    @pytest.mark.parametrize("dangerous_cmd", [
        "rm file.txt",
        "rm -rf /",
        "/usr/bin/rm file",
        "sudo rm important.txt",
        "rm",  # bare rm
    ])
    def test_dangerous_commands_blocked(self, dangerous_cmd: str) -> None:
        """Actual rm commands SHOULD be blocked."""
        assert command_matches_pattern(dangerous_cmd, "rm") is True
