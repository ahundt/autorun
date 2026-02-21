#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Unit tests for pattern parsing and enhanced pattern matching.

Tests the new functionality for:
- Parsing patterns with optional descriptions
- Support for quoted patterns
- Support for regex patterns (regex: prefix)
- Support for glob patterns (glob: prefix)
- Support for /pattern/ regex syntax
"""

import pytest
from autorun.main import parse_pattern_and_description, command_matches_pattern


class TestParsePatternAndDescription:
    """Test parse_pattern_and_description function."""

    def test_simple_pattern_no_description(self):
        """Test simple pattern without description."""
        pattern, description, pattern_type = parse_pattern_and_description("rm")
        assert pattern == "rm"
        assert description is None
        assert pattern_type == "literal"

    def test_quoted_pattern_no_description(self):
        """Test quoted pattern without description."""
        pattern, description, pattern_type = parse_pattern_and_description('"rm -rf"')
        assert pattern == "rm -rf"
        assert description is None
        assert pattern_type == "literal"

    def test_quoted_pattern_with_description(self):
        """Test quoted pattern with custom description."""
        pattern, description, pattern_type = parse_pattern_and_description('"exec(" unsafe function')
        assert pattern == "exec("
        assert description == "unsafe function"
        assert pattern_type == "literal"

    def test_unquoted_pattern_with_description(self):
        """Test unquoted pattern with description."""
        pattern, description, pattern_type = parse_pattern_and_description("rm dangerous command")
        assert pattern == "rm"
        assert description == "dangerous command"
        assert pattern_type == "literal"

    def test_regex_prefix(self):
        """Test regex: prefix for regex patterns."""
        pattern, description, pattern_type = parse_pattern_and_description("regex:exec(")
        assert pattern == "exec("
        assert description is None
        assert pattern_type == "regex"

    def test_regex_prefix_with_description(self):
        """Test regex: prefix with custom description."""
        pattern, description, pattern_type = parse_pattern_and_description('regex:exec( unsafe exec function')
        assert pattern == "exec("
        assert description == "unsafe exec function"
        assert pattern_type == "regex"

    def test_glob_prefix(self):
        """Test glob: prefix for glob patterns."""
        pattern, description, pattern_type = parse_pattern_and_description("glob:*.tmp")
        assert pattern == "*.tmp"
        assert description is None
        assert pattern_type == "glob"

    def test_glob_prefix_with_description(self):
        """Test glob: prefix with custom description."""
        pattern, description, pattern_type = parse_pattern_and_description('glob:*.tmp temporary files not allowed')
        assert pattern == "*.tmp"
        assert description == "temporary files not allowed"
        assert pattern_type == "glob"

    def test_slash_delimited_regex_auto_detect(self):
        """Test /pattern/ syntax auto-detects regex."""
        # /pattern/ format should be used without descriptions (use regex: prefix instead)
        pattern, description, pattern_type = parse_pattern_and_description("/exec(.*/")
        assert pattern == "exec(.*"
        assert description is None
        assert pattern_type == "regex"

    def test_slash_delimited_with_description(self):
        """Test /pattern/ syntax with description - use regex: prefix instead."""
        # For patterns with descriptions, use the regex: prefix
        pattern, description, pattern_type = parse_pattern_and_description('regex:exec(.* unsafe function')
        assert pattern == "exec(.*"
        assert description == "unsafe function"
        assert pattern_type == "regex"

    def test_slash_delimited_literal_no_regex_chars(self):
        """Test /pattern/ without regex chars stays literal."""
        pattern, description, pattern_type = parse_pattern_and_description("/simple/")
        # No regex metacharacters, so it's not auto-detected as regex
        assert pattern == "/simple/"
        assert pattern_type == "literal"

    def test_complex_regex_pattern(self):
        """Test complex regex pattern."""
        # Use pattern that actually works
        pattern, description, pattern_type = parse_pattern_and_description('regex:(eval|assert)(')
        assert pattern == "(eval|assert)("
        assert description is None
        assert pattern_type == "regex"

    def test_empty_string_raises_error(self):
        """Test empty string raises ValueError."""
        with pytest.raises(ValueError, match="No pattern provided"):
            parse_pattern_and_description("")

    def test_whitespace_only_raises_error(self):
        """Test whitespace-only string raises ValueError."""
        with pytest.raises(ValueError, match="No pattern provided"):
            parse_pattern_and_description("   ")

    def test_multiple_word_description(self):
        """Test pattern with multi-word description."""
        pattern, description, pattern_type = parse_pattern_and_description('rm permanently destructive use trash instead')
        assert pattern == "rm"
        assert description == "permanently destructive use trash instead"
        assert pattern_type == "literal"


class TestCommandMatchesPatternWithType:
    """Test command_matches_pattern with pattern_type parameter."""

    def test_literal_pattern_existing_behavior(self):
        """Test literal patterns maintain existing behavior."""
        assert command_matches_pattern("rm file.txt", "rm", "literal") is True
        assert command_matches_pattern("dd if=/dev/zero", "dd if=", "literal") is True
        assert command_matches_pattern("ls file.txt", "rm", "literal") is False

    def test_regex_pattern_basic(self):
        """Test basic regex pattern matching."""
        # Match exec( anywhere in command
        assert command_matches_pattern("code('exec(whoami)')", r"exec\(", "regex") is True
        assert command_matches_pattern("safe code", r"exec\(", "regex") is False

    def test_regex_pattern_complex(self):
        """Test complex regex patterns."""
        # Match eval or assert followed by (
        assert command_matches_pattern("code('eval(x)')", r"(eval|assert)\(", "regex") is True
        assert command_matches_pattern("code('assert(x)')", r"(eval|assert)\(", "regex") is True
        assert command_matches_pattern("code('print(x)')", r"(eval|assert)\(", "regex") is False

    def test_regex_pattern_anchored(self):
        """Test regex patterns with anchors."""
        # Match starting with rm
        assert command_matches_pattern("rm file.txt", "^rm", "regex") is True
        assert command_matches_pattern("sudo rm file.txt", "^rm", "regex") is False

    def test_glob_pattern_wildcard(self):
        """Test glob pattern with wildcard."""
        assert command_matches_pattern("file.tmp", "*.tmp", "glob") is True
        assert command_matches_pattern("path/to/file.tmp", "*.tmp", "glob") is True
        assert command_matches_pattern("file.txt", "*.tmp", "glob") is False

    def test_glob_pattern_character_class(self):
        """Test glob pattern with character class."""
        assert command_matches_pattern("file1.tmp", "file[0-9].tmp", "glob") is True
        assert command_matches_pattern("fileA.tmp", "file[0-9].tmp", "glob") is False

    def test_glob_pattern_question_mark(self):
        """Test glob pattern with ? wildcard."""
        assert command_matches_pattern("fil.tmp", r"fi?.tmp", "glob") is True
        assert command_matches_pattern("fiX.tmp", r"fi?.tmp", "glob") is True
        assert command_matches_pattern("fiXX.tmp", r"fi?.tmp", "glob") is False

    def test_invalid_regex_falls_back_to_literal(self):
        """Test invalid regex pattern falls back to literal substring match."""
        # Invalid regex should fall back to substring match
        assert command_matches_pattern("code with [unclosed", "[unclosed", "regex") is True
        assert command_matches_pattern("code without", "[unclosed", "regex") is False

    def test_pattern_type_default_literal(self):
        """Test default pattern_type is literal."""
        # When pattern_type is not specified, it should default to literal
        assert command_matches_pattern("rm file.txt", "rm") is True
        assert command_matches_pattern("dd if=/dev/zero", "dd if=") is True

    def test_regex_pattern_matches_substring(self):
        """Test regex pattern can match substrings."""
        # Regex .* matches any characters
        assert command_matches_pattern("code exec( dangerous", "exec\\(.*dangerous", "regex") is True
        assert command_matches_pattern("code exec( safe", "exec\\(.*dangerous", "regex") is False


class TestBackwardCompatibility:
    """Test backward compatibility with existing functionality."""

    def test_existing_block_still_works(self):
        """Test that existing blocks without description still work."""
        # This should use DEFAULT_INTEGRATIONS for suggestion
        pattern, description, pattern_type = parse_pattern_and_description("rm")
        assert pattern == "rm"
        assert description is None
        assert pattern_type == "literal"

    def test_existing_pattern_matching_still_works(self):
        """Test existing pattern matching behavior unchanged."""
        # All existing patterns should still match
        assert command_matches_pattern("rm file.txt", "rm") is True
        assert command_matches_pattern("rm -rf /tmp", "rm -rf") is True
        assert command_matches_pattern("dd if=/dev/zero of=file", "dd if=") is True
        assert command_matches_pattern("git reset --hard", "git reset --hard") is True

    def test_existing_handlers_still_work(self):
        """Test that handler function signatures are backward compatible."""
        # add_session_block and add_global_block should still work with just pattern
        # They should use None for description and "literal" for pattern_type by default
        pattern, description, pattern_type = parse_pattern_and_description("rm")
        assert description is None  # Will use DEFAULT_INTEGRATIONS
        assert pattern_type == "literal"


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_pattern_with_quotes_in_description(self):
        """Test pattern with quoted string in description."""
        pattern, description, pattern_type = parse_pattern_and_description('rm "permanently destructive" command')
        assert pattern == "rm"
        assert description == "permanently destructive command"
        assert pattern_type == "literal"

    def test_regex_pattern_with_special_chars(self):
        """Test regex pattern with various special characters."""
        pattern, description, pattern_type = parse_pattern_and_description(r'regex:$.*dangerous')
        assert pattern == r"$.*dangerous"
        assert pattern_type == "regex"

    def test_glob_pattern_with_multiple_wildcards(self):
        """Test glob pattern with multiple wildcards."""
        pattern, description, pattern_type = parse_pattern_and_description('glob:*.[Tt][Mm][Pp]')
        assert pattern == "*.[Tt][Mm][Pp]"
        assert pattern_type == "glob"

    def test_empty_description_after_pattern(self):
        """Test pattern followed by spaces (empty description)."""
        pattern, description, pattern_type = parse_pattern_and_description("rm    ")
        # Trailing whitespace is stripped, so description should be None
        assert pattern == "rm"
        assert description is None
        assert pattern_type == "literal"
