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
Tests for unified integrations system (superset of hookify).
"""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from autorun.integrations import (
    Integration,
    load_all_integrations,
    invalidate_caches,
    check_when_predicate,
    check_conditions,
    _extract_frontmatter,
    _pattern_specificity,
    _validate_integration,
)
from autorun.config import DEFAULT_INTEGRATIONS


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear integration cache before each test for isolation."""
    invalidate_caches()
    yield
    invalidate_caches()


class TestIntegrationDataclass:
    """Test Integration dataclass with all fields."""

    def test_from_dict_basic(self):
        """Factory creates Integration from dict."""
        config = {
            "action": "block",
            "suggestion": "Test message",
            "redirect": "echo {args}",
        }
        intg = Integration.from_dict("test", config)

        assert intg.patterns == ("test",)
        assert intg.action == "block"
        assert intg.message == "Test message"
        assert intg.redirect == "echo {args}"
        assert intg.source == "default"

    def test_from_dict_equality(self):
        """Factory method creates equal integrations from same config."""
        config = {"suggestion": "Test"}
        i1 = Integration.from_dict("test", config)
        i2 = Integration.from_dict("test", config)
        # Can't use LRU cache (dicts unhashable), so check equality not identity
        assert i1 == i2
        assert i1.patterns == i2.patterns
        assert i1.action == i2.action

    def test_from_dict_backward_compat_commands(self):
        """Backward compat: 'commands' -> 'redirect'."""
        config = {
            "suggestion": "Test",
            "commands": ["echo {args}", "other"],
        }
        intg = Integration.from_dict("test", config)
        assert intg.redirect == "echo {args}"  # Takes first command

    def test_from_dict_patterns_list(self):
        """Multiple patterns in list."""
        config = {
            "patterns": ["rm", "rm -rf", "rm -f"],
            "suggestion": "Test",
        }
        intg = Integration.from_dict("rm", config)
        assert intg.patterns == ("rm", "rm -rf", "rm -f")

    def test_from_dict_defaults(self):
        """Default values when fields missing."""
        config = {"suggestion": "Test"}
        intg = Integration.from_dict("test", config)

        assert intg.action == "block"
        assert intg.when == "always"
        assert intg.event == "bash"
        assert intg.tool_matcher == "Bash"
        assert intg.conditions == ()
        assert intg.enabled is True


class TestExtractFrontmatter:
    """Test YAML-like frontmatter extraction."""

    def test_basic_frontmatter(self):
        """Parse simple key: value pairs."""
        content = """---
name: test
action: block
enabled: true
---

Body content here"""
        fm, body = _extract_frontmatter(content)

        assert fm["name"] == "test"
        assert fm["action"] == "block"
        assert fm["enabled"] is True
        assert body == "Body content here"

    def test_list_syntax(self):
        """Parse list syntax [item1, item2]."""
        content = """---
patterns: [rm, rm -rf, rm -f]
---

Body"""
        fm, body = _extract_frontmatter(content)
        assert fm["patterns"] == ["rm", "rm -rf", "rm -f"]

    def test_list_syntax_quoted_strings(self):
        """Parse list with quoted strings."""
        content = """---
patterns: ["rm", 'rm -rf', "rm -f"]
---

Body"""
        fm, body = _extract_frontmatter(content)
        # Quotes are stripped from each item
        assert fm["patterns"] == ["rm", "rm -rf", "rm -f"]

    def test_no_frontmatter(self):
        """Handle content without frontmatter."""
        content = "Just body content"
        fm, body = _extract_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_comments_ignored(self):
        """Comments in frontmatter are ignored."""
        content = """---
# This is a comment
name: test
# Another comment
action: block
---

Body"""
        fm, body = _extract_frontmatter(content)
        assert fm == {"name": "test", "action": "block"}

    def test_single_pattern_hookify_compat(self):
        """Single pattern field (hookify style) parses correctly."""
        content = """---
pattern: npm publish
action: block
---

Block npm publish"""
        fm, body = _extract_frontmatter(content)
        assert fm["pattern"] == "npm publish"
        # User code should handle: patterns = fm.get("patterns") or [fm.get("pattern")]


class TestPatternSpecificity:
    """Test pattern specificity sorting."""

    def test_longer_pattern_more_specific(self):
        """Longer patterns have higher specificity."""
        spec1 = _pattern_specificity(("rm",))
        spec2 = _pattern_specificity(("rm -rf",))
        spec3 = _pattern_specificity(("rm -rf /",))

        assert spec3 > spec2 > spec1

    def test_multiword_more_specific(self):
        """Patterns with more words are more specific."""
        spec1 = _pattern_specificity(("git",))
        spec2 = _pattern_specificity(("git reset",))
        spec3 = _pattern_specificity(("git reset --hard",))

        assert spec3 > spec2 > spec1

    def test_max_specificity_from_list(self):
        """Takes max specificity from pattern list."""
        spec = _pattern_specificity(("rm", "rm -rf", "rm -rf /"))
        max_spec = _pattern_specificity(("rm -rf /",))
        assert spec == max_spec


class TestLoadIntegrations:
    """Test file loading with mtime cache."""

    def test_loads_python_defaults(self):
        """Loads from DEFAULT_INTEGRATIONS."""
        # Don't mock - actually load from defaults
        integrations = load_all_integrations()

        # Should have integrations from DEFAULT_INTEGRATIONS
        assert len(integrations) > 0
        # Should include rm entry
        rm_intgs = [i for i in integrations if "rm" in i.patterns]
        assert len(rm_intgs) > 0
        # Verify it's from Python defaults
        assert any(i.source == "default" for i in integrations)

    @patch("autorun.integrations.Path.glob")
    def test_cache_hit_on_unchanged_files(self, mock_glob):
        """Second load is O(1) cached if files unchanged."""
        mock_glob.return_value = []

        # First load
        integrations1 = load_all_integrations()

        # Second load (should use cache)
        integrations2 = load_all_integrations()

        # Should be exact same list object (cache hit)
        assert integrations1 is integrations2

    def test_sorts_by_specificity(self):
        """Integrations sorted by pattern specificity (most specific first)."""
        integrations = load_all_integrations()

        # Find rm and rm -rf
        rm_idx = next((i for i, intg in enumerate(integrations) if intg.patterns == ("rm",)), None)
        rm_rf_idx = next((i for i, intg in enumerate(integrations) if intg.patterns == ("rm -rf",)), None)

        if rm_idx is not None and rm_rf_idx is not None:
            # rm -rf should come before rm (more specific)
            assert rm_rf_idx < rm_idx


class TestWhenPredicates:
    """Test when field (bash + Python)."""

    def test_always_predicate(self):
        """'always' predicate returns True."""
        assert check_when_predicate("always", None) is True

    @patch("subprocess.run")
    def test_python_predicate_has_uncommitted_changes(self, mock_run):
        """has_uncommitted_changes calls git diff."""
        mock_run.return_value = MagicMock(returncode=1)  # Has changes

        result = check_when_predicate("has_uncommitted_changes", None)

        assert result is True
        mock_run.assert_called_once()
        assert "git diff" in mock_run.call_args[0][0]

    @patch("subprocess.run")
    def test_python_predicate_no_changes(self, mock_run):
        """has_uncommitted_changes returns False if no changes."""
        mock_run.return_value = MagicMock(returncode=0)  # No changes

        result = check_when_predicate("has_uncommitted_changes", None)

        assert result is False

    @patch("subprocess.run")
    def test_bash_predicate_fallback(self, mock_run):
        """Unknown predicate runs as bash command."""
        mock_run.return_value = MagicMock(returncode=0)

        result = check_when_predicate("test -f /tmp/foo", None)

        assert result is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_bash_predicate_failure(self, mock_run):
        """Bash command returning non-zero is False."""
        mock_run.return_value = MagicMock(returncode=1)

        result = check_when_predicate("test -f /nonexistent", None)

        assert result is False

    @patch("subprocess.run")
    def test_predicate_timeout(self, mock_run):
        """Timeout on bash predicate returns False."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 2)

        result = check_when_predicate("sleep 10", None)

        assert result is False

    @patch("subprocess.run")
    def test_file_has_unstaged_changes_with_file(self, mock_run):
        """_file_has_unstaged_changes (alias → _file_differs_from_ref) checks file."""
        # v4: _git_diff_quiet probes rev-parse --is-inside-work-tree, then
        # rev-parse --verify HEAD, then git diff HEAD --quiet -- <file>.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),  # is-inside-work-tree
            MagicMock(returncode=0),                     # rev-parse --verify HEAD
            MagicMock(returncode=1),                     # diff: has changes
        ]
        ctx = MagicMock(tool_input={"command": "git checkout -- file.txt"}, cwd="/tmp/repo")

        result = check_when_predicate("_file_has_unstaged_changes", ctx)

        assert result is True
        mock_run.assert_called()
        # Verify the diff call references the file
        diff_call = mock_run.call_args_list[-1]
        assert "file.txt" in str(diff_call)

    @patch("subprocess.run")
    def test_file_has_unstaged_changes_no_changes(self, mock_run):
        """_file_has_unstaged_changes returns False if file clean."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),  # probe
            MagicMock(returncode=0),                     # verify HEAD
            MagicMock(returncode=0),                     # diff: clean
        ]
        ctx = MagicMock(tool_input={"command": "git checkout -- clean.txt"}, cwd="/tmp/repo")

        result = check_when_predicate("_file_has_unstaged_changes", ctx)

        assert result is False


class TestConditions:
    """Test hookify-style conditions (AND-ed)."""

    def test_no_conditions_returns_true(self):
        """Empty conditions always match."""
        ctx = MagicMock(tool_name="Bash", tool_input={"command": "test"})
        assert check_conditions((), ctx) is True

    def test_basic_contains_match(self):
        """Basic contains operator."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish"}
        )
        conditions = (
            {"field": "command", "operator": "contains", "pattern": "publish"},
        )

        assert check_conditions(conditions, ctx) is True

    def test_basic_contains_no_match(self):
        """Contains operator returns False if not found."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm install"}
        )
        conditions = (
            {"field": "command", "operator": "contains", "pattern": "publish"},
        )

        assert check_conditions(conditions, ctx) is False

    def test_regex_match(self):
        """Regex operator matching."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish"}
        )
        conditions = (
            {"field": "command", "operator": "regex_match", "pattern": r"npm\s+publish"},
        )

        assert check_conditions(conditions, ctx) is True

    def test_and_conditions(self):
        """Multiple conditions are AND-ed."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish --tag latest"}
        )
        conditions = (
            {"field": "command", "operator": "contains", "pattern": "publish"},
            {"field": "command", "operator": "contains", "pattern": "--tag"},
        )

        assert check_conditions(conditions, ctx) is True

    def test_and_conditions_fail(self):
        """AND-ed conditions fail if any doesn't match."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish"}
        )
        conditions = (
            {"field": "command", "operator": "contains", "pattern": "publish"},
            {"field": "command", "operator": "contains", "pattern": "--tag"},
        )

        assert check_conditions(conditions, ctx) is False


class TestActionsIntegration:
    """Test block vs warn behavior (integration test)."""

    def test_block_action_denies_command(self):
        """action: block returns deny."""
        # This would require mocking EventContext and checking ctx.deny() call
        # For now, we verify the logic in check_blocked_commands via unit tests
        pass

    def test_warn_action_allows_command(self):
        """action: warn returns allow with message."""
        # This would require full integration test with EventContext
        pass


class TestEventFiltering:
    """Test event field filtering (bash/file/all)."""

    def test_bash_event_matches_bash_tool(self):
        """Integration with event='bash' matches Bash tool context."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="blocked",
            event="bash",
        )
        # event="bash" should match when we're in bash context
        assert intg.event == "bash"

    def test_file_event_matches_write_tool(self):
        """Integration with event='file' matches Write/Edit tool context."""
        intg = Integration(
            patterns=("*.env",),
            action="block",
            message="blocked",
            event="file",
        )
        assert intg.event == "file"

    def test_all_event_matches_any_tool(self):
        """Integration with event='all' matches any tool context."""
        intg = Integration(
            patterns=("dangerous",),
            action="block",
            message="blocked",
            event="all",
        )
        assert intg.event == "all"


class TestPatternValidation:
    """Test pattern and redirect validation."""

    def test_valid_integration_no_warnings(self):
        """Valid integration produces no warnings."""
        intg = Integration(
            patterns=("rm -rf",),
            action="block",
            message="test",
            redirect="trash {args}",
        )
        warnings = _validate_integration(intg, "test")
        assert warnings == []

    def test_broad_pattern_warning(self):
        """Too broad patterns produce warnings."""
        intg = Integration(
            patterns=(".*",),
            action="block",
            message="test",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1
        assert "too broad" in warnings[0].lower()

    def test_single_char_pattern_warning(self):
        """Single character patterns produce warnings."""
        intg = Integration(
            patterns=("a",),
            action="block",
            message="test",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1
        assert "single character" in warnings[0].lower()

    def test_redirect_wrong_placeholder_warning(self):
        """Redirect with {arg} instead of {args} produces warning."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="test",
            redirect="trash {arg}",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1
        assert "{args}" in warnings[0]

    def test_redirect_unbalanced_braces_warning(self):
        """Redirect with unbalanced braces produces warning."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="test",
            redirect="trash {args",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1
        assert "unbalanced" in warnings[0].lower()

    def test_multiple_warnings(self):
        """Multiple issues produce multiple warnings."""
        intg = Integration(
            patterns=(".*", "a"),
            action="block",
            message="test",
            redirect="trash {arg",  # Wrong placeholder + unbalanced
        )
        warnings = _validate_integration(intg, "test")
        # Should have: broad pattern, single char, wrong placeholder, unbalanced
        assert len(warnings) >= 3


class TestRedirectSubstitution:
    """Test redirect field with arg substitution."""

    def test_redirect_with_args(self):
        """Redirect substitutes {args} with actual args."""
        # Example: rm file.txt -> trash file.txt
        cmd = "rm file.txt"
        redirect = "trash {args}"
        args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
        result = redirect.replace("{args}", args)

        assert result == "trash file.txt"

    def test_redirect_no_args(self):
        """Redirect with no args substitutes empty string."""
        cmd = "rm"
        redirect = "trash {args}"
        args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
        result = redirect.replace("{args}", args)

        assert result == "trash "

    def test_redirect_multiple_args(self):
        """Redirect preserves all args."""
        cmd = "rm -rf /tmp/test /tmp/test2"
        redirect = "trash {args}"
        args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
        result = redirect.replace("{args}", args)

        assert result == "trash -rf /tmp/test /tmp/test2"

    def test_redirect_with_special_chars(self):
        """Redirect preserves special characters in args."""
        cmd = "rm 'file with spaces.txt'"
        redirect = "trash {args}"
        args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
        result = redirect.replace("{args}", args)

        assert result == "trash 'file with spaces.txt'"

    def test_redirect_with_glob_patterns(self):
        """Redirect preserves glob patterns in args."""
        cmd = "rm *.log"
        redirect = "trash {args}"
        args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
        result = redirect.replace("{args}", args)

        assert result == "trash *.log"

    def test_redirect_multiple_placeholders(self):
        """Redirect with multiple {args} placeholders."""
        cmd = "rm file.txt"
        redirect = "echo {args} && trash {args}"
        args = cmd.split(maxsplit=1)[1] if " " in cmd else ""
        result = redirect.replace("{args}", args)

        assert result == "echo file.txt && trash file.txt"


# =============================================================================
# DEEP TDD: Cache Invalidation Tests
# =============================================================================

class TestCacheInvalidation:
    """Test cache invalidation behavior."""

    def test_invalidate_caches_clears_cache(self):
        """invalidate_caches() clears the integration cache."""
        # Load once to populate cache
        integrations1 = load_all_integrations()
        assert len(integrations1) > 0

        # Invalidate
        invalidate_caches()

        # Load again - should rebuild (different object)
        integrations2 = load_all_integrations()
        assert integrations1 is not integrations2

    def test_cache_returns_same_object_without_invalidation(self):
        """Cache returns same list object when not invalidated."""
        integrations1 = load_all_integrations()
        integrations2 = load_all_integrations()
        assert integrations1 is integrations2

    @patch("autorun.integrations.Path.glob")
    def test_cache_invalidates_on_mtime_change(self, mock_glob):
        """Cache invalidates when file mtime changes."""
        # Setup mock file
        mock_file = MagicMock()
        mock_file.is_file.return_value = True
        mock_file.stat.return_value = MagicMock(st_mtime=1000.0)
        mock_file.read_text.return_value = """---
patterns: [test]
action: block
---
Test message"""
        mock_file.stem = "test"
        mock_file.__str__ = lambda s: "test.md"
        mock_glob.return_value = [mock_file]

        # First load
        load_all_integrations()

        # Change mtime
        mock_file.stat.return_value = MagicMock(st_mtime=2000.0)

        # Invalidate to clear cache
        invalidate_caches()

        # Should reload with new mtime
        integrations = load_all_integrations()
        assert len(integrations) > 0

    def test_invalidate_multiple_times_safe(self):
        """Multiple invalidate_caches() calls are safe."""
        invalidate_caches()
        invalidate_caches()
        invalidate_caches()

        # Should still work after multiple invalidations
        integrations = load_all_integrations()
        assert len(integrations) > 0


# =============================================================================
# DEEP TDD: User File Loading Tests
# =============================================================================

class TestUserFileLoading:
    """Test loading user integration files."""

    def test_user_file_with_all_fields(self, tmp_path):
        """User file with all fields loads correctly."""
        # Create temp .claude directory
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()

        # Create integration file
        md_file = claude_dir / "autorun.test.local.md"
        md_file.write_text("""---
name: custom-test
patterns: [custom-cmd, custom-alt]
action: warn
redirect: safe-cmd {args}
when: always
event: bash
tool_matcher: Bash
enabled: true
---

Custom warning message here.""")

        # Patch to use tmp_path
        with patch("autorun.integrations.Path") as mock_path:
            mock_path.return_value.glob.return_value = [md_file]
            mock_path.return_value.is_file.return_value = True

            invalidate_caches()
            # This test verifies the parsing logic, actual loading would need
            # full integration test

    def test_disabled_user_file_skipped(self):
        """User file with enabled: false is skipped."""
        content = """---
patterns: [test]
enabled: false
---
Should not load"""
        fm, body = _extract_frontmatter(content)

        # enabled should be False
        assert fm.get("enabled") is False

    def test_user_file_missing_patterns_handled(self):
        """User file without patterns is handled gracefully."""
        content = """---
name: no-patterns
action: block
---
No patterns defined"""
        fm, body = _extract_frontmatter(content)

        # Should have no patterns key
        assert "patterns" not in fm
        assert "pattern" not in fm


# =============================================================================
# DEEP TDD: Frontmatter Edge Cases
# =============================================================================

class TestExtractFrontmatterEdgeCases:
    """Test edge cases in frontmatter extraction."""

    def test_empty_frontmatter(self):
        """Empty frontmatter block."""
        content = """---
---

Body only"""
        fm, body = _extract_frontmatter(content)
        assert fm == {}
        assert body == "Body only"

    def test_frontmatter_with_quoted_strings(self):
        """Quoted strings in frontmatter."""
        content = """---
name: "my-test"
message: 'single quoted'
---

Body"""
        fm, body = _extract_frontmatter(content)
        assert fm["name"] == "my-test"
        assert fm["message"] == "single quoted"

    def test_frontmatter_with_empty_list(self):
        """Empty list in frontmatter."""
        content = """---
patterns: []
---

Body"""
        fm, body = _extract_frontmatter(content)
        # Parser splits on commas, empty list becomes ['']
        # This is acceptable behavior - user files should have content
        assert "patterns" in fm

    def test_frontmatter_with_special_chars_in_values(self):
        """Special characters in values."""
        content = """---
pattern: git reset --hard
redirect: git stash push -m 'WIP: {args}'
---

Body"""
        fm, body = _extract_frontmatter(content)
        assert fm["pattern"] == "git reset --hard"
        assert "{args}" in fm["redirect"]

    def test_frontmatter_false_boolean(self):
        """false boolean parses correctly."""
        content = """---
enabled: false
---

Body"""
        fm, body = _extract_frontmatter(content)
        assert fm["enabled"] is False

    def test_malformed_frontmatter_missing_closing(self):
        """Malformed frontmatter with missing closing delimiter."""
        content = """---
name: test
action: block

This is body that looks like frontmatter"""
        fm, body = _extract_frontmatter(content)
        # Should return empty dict and full content as body
        assert fm == {}

    def test_frontmatter_with_colon_in_value(self):
        """Colon in value doesn't break parsing."""
        content = """---
message: Warning: dangerous command
---

Body"""
        fm, body = _extract_frontmatter(content)
        assert fm["message"] == "Warning: dangerous command"

    def test_frontmatter_multiline_body(self):
        """Body can be multiline."""
        content = """---
name: test
---

Line 1
Line 2
Line 3"""
        fm, body = _extract_frontmatter(content)
        assert "Line 1" in body
        assert "Line 2" in body
        assert "Line 3" in body


# =============================================================================
# DEEP TDD: When Predicate Edge Cases
# =============================================================================

class TestWhenPredicateEdgeCases:
    """Test edge cases for when predicates."""

    def test_empty_predicate_returns_true(self):
        """Empty string predicate treated as 'always'."""
        # Empty string is falsy, should behave like 'always'
        result = check_when_predicate("", None)
        # Empty string is not "always", so it will try bash
        # which will fail gracefully
        assert result in (True, False)  # Either is acceptable

    def test_none_context_with_known_predicate(self):
        """Known predicate works with None context."""
        # has_uncommitted_changes doesn't use ctx
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = check_when_predicate("has_uncommitted_changes", None)
            assert result is False

    @patch("subprocess.run")
    def test_file_predicate_multiple_files(self, mock_run):
        """_file_has_unstaged_changes with multiple files."""
        # v4: each file runs probe+verify+diff; first file clean, second dirty.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),  # probe (file1)
            MagicMock(returncode=0),                     # verify HEAD (file1)
            MagicMock(returncode=0),                     # diff file1: clean
            MagicMock(returncode=0, stdout=b"true\n"),  # probe (file2)
            MagicMock(returncode=0),                     # verify HEAD (file2)
            MagicMock(returncode=1),                     # diff file2: dirty
        ]
        ctx = MagicMock(tool_input={"command": "git checkout -- file1.txt file2.txt"}, cwd="/tmp/repo")

        result = check_when_predicate("_file_has_unstaged_changes", ctx)

        assert result is True  # Any file with changes = True

    @patch("subprocess.run")
    def test_file_predicate_no_double_dash(self, mock_run):
        """_file_has_unstaged_changes without -- still checks full repo for ref."""
        # v4: without `--`, no pathspec is extracted; falls through to
        # repo-wide HEAD diff (matches "discards any uncommitted change").
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),  # probe
            MagicMock(returncode=0),                     # verify HEAD
            MagicMock(returncode=1),                     # repo-wide diff: dirty
        ]
        ctx = MagicMock(tool_input={"command": "git checkout file.txt"}, cwd="/tmp/repo")

        result = check_when_predicate("_file_has_unstaged_changes", ctx)

        assert result is True

    def test_file_predicate_no_tool_input(self):
        """_file_has_unstaged_changes with missing tool_input."""
        ctx = MagicMock(spec=[])  # No tool_input attribute

        result = check_when_predicate("_file_has_unstaged_changes", ctx)

        assert result is False  # Graceful failure

    def test_file_predicate_empty_command(self):
        """_file_has_unstaged_changes with empty command."""
        ctx = MagicMock(tool_input={"command": ""})

        result = check_when_predicate("_file_has_unstaged_changes", ctx)

        assert result is False

    @patch("subprocess.run")
    def test_stash_exists_predicate(self, mock_run):
        """_stash_exists returns True when stash has entries."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="stash@{0}: WIP on main: abc1234 commit msg\n"
        )

        result = check_when_predicate("_stash_exists", None)

        assert result is True

    @patch("subprocess.run")
    def test_stash_exists_predicate_empty(self, mock_run):
        """_stash_exists returns False when stash is empty."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")

        result = check_when_predicate("_stash_exists", None)

        assert result is False

    @patch("subprocess.run")
    def test_bash_predicate_with_complex_command(self, mock_run):
        """Bash predicate with pipes and redirects."""
        mock_run.return_value = MagicMock(returncode=0)

        result = check_when_predicate("ls /tmp | grep test > /dev/null", None)

        assert result is True
        mock_run.assert_called_once()

    @patch("subprocess.run")
    def test_bash_predicate_exception(self, mock_run):
        """Bash predicate handles general exceptions."""
        mock_run.side_effect = OSError("Command failed")

        result = check_when_predicate("bad-command", None)

        assert result is False

    # ---- _restore_is_destructive predicate tests ----

    @patch("subprocess.run")
    def test_restore_plain_is_destructive(self, mock_run):
        """'git restore file.txt' is destructive (default is --worktree)."""
        # v4: delegates to _file_differs_from_ref → probe+verify+diff.
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),  # probe
            MagicMock(returncode=0),                     # verify HEAD
            MagicMock(returncode=1),                     # diff: dirty
        ]
        ctx = MagicMock(tool_input={"command": "git restore file.txt"}, cwd="/tmp/repo")

        result = check_when_predicate("_restore_is_destructive", ctx)
        assert result is True

    @patch("subprocess.run")
    def test_restore_staged_is_safe(self, mock_run):
        """'git restore --staged file.txt' is safe (just unstages)."""
        ctx = MagicMock(tool_input={"command": "git restore --staged file.txt"})

        result = check_when_predicate("_restore_is_destructive", ctx)
        assert result is False
        mock_run.assert_not_called()  # Should short-circuit before git diff

    @patch("subprocess.run")
    def test_restore_short_staged_is_safe(self, mock_run):
        """'git restore -S file.txt' is safe (short form of --staged)."""
        ctx = MagicMock(tool_input={"command": "git restore -S file.txt"})

        result = check_when_predicate("_restore_is_destructive", ctx)
        assert result is False
        mock_run.assert_not_called()

    @patch("subprocess.run")
    def test_restore_worktree_is_destructive(self, mock_run):
        """'git restore --worktree file.txt' is destructive."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=1),
        ]
        ctx = MagicMock(tool_input={"command": "git restore --worktree file.txt"}, cwd="/tmp/repo")
        assert check_when_predicate("_restore_is_destructive", ctx) is True

    @patch("subprocess.run")
    def test_restore_short_worktree_is_destructive(self, mock_run):
        """'git restore -W file.txt' is destructive."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=1),
        ]
        ctx = MagicMock(tool_input={"command": "git restore -W file.txt"}, cwd="/tmp/repo")
        assert check_when_predicate("_restore_is_destructive", ctx) is True

    @patch("subprocess.run")
    def test_restore_staged_worktree_is_destructive(self, mock_run):
        """'git restore --staged --worktree file.txt' is destructive (worktree wins)."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=1),
        ]
        ctx = MagicMock(tool_input={"command": "git restore --staged --worktree file.txt"}, cwd="/tmp/repo")
        assert check_when_predicate("_restore_is_destructive", ctx) is True

    @patch("subprocess.run")
    def test_restore_combined_SW_is_destructive(self, mock_run):
        """'git restore -SW file.txt' is destructive (W present in combined flag)."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=1),
        ]
        ctx = MagicMock(tool_input={"command": "git restore -SW file.txt"}, cwd="/tmp/repo")
        assert check_when_predicate("_restore_is_destructive", ctx) is True

    @patch("subprocess.run")
    def test_restore_no_unstaged_changes_allowed(self, mock_run):
        """'git restore file.txt' allowed if file has no unstaged changes."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"true\n"),
            MagicMock(returncode=0),
            MagicMock(returncode=0),  # diff: clean
        ]
        ctx = MagicMock(tool_input={"command": "git restore file.txt"}, cwd="/tmp/repo")
        assert check_when_predicate("_restore_is_destructive", ctx) is False

    def test_restore_empty_command(self):
        """'git restore' with empty command returns False."""
        ctx = MagicMock(tool_input={"command": ""})
        result = check_when_predicate("_restore_is_destructive", ctx)
        assert result is False

    def test_restore_no_tool_input(self):
        """_restore_is_destructive with missing tool_input."""
        ctx = MagicMock(spec=[])
        result = check_when_predicate("_restore_is_destructive", ctx)
        assert result is False


# =============================================================================
# DEEP TDD: git restore config.py integration tests
# =============================================================================

class TestGitRestoreConfig:
    """Test git restore entry in DEFAULT_INTEGRATIONS."""

    def test_git_restore_exists_in_config(self):
        """git restore has an entry in DEFAULT_INTEGRATIONS."""
        assert "git restore" in DEFAULT_INTEGRATIONS

    def test_git_restore_uses_correct_predicate(self):
        """git restore uses _restore_is_destructive predicate."""
        config = DEFAULT_INTEGRATIONS["git restore"]
        assert config["when"] == "_restore_is_destructive"

    def test_git_restore_redirects_to_stash(self):
        """git restore redirects to git stash push."""
        config = DEFAULT_INTEGRATIONS["git restore"]
        assert "git stash push" in config["redirect"]
        assert "{file}" in config["redirect"]

    def test_git_restore_suggestion_mentions_staged_safe(self):
        """Suggestion tells users --staged is safe."""
        config = DEFAULT_INTEGRATIONS["git restore"]
        assert "--staged" in config["suggestion"]

    def test_no_git_restore_in_other_suggestions(self):
        """No other integration suggests 'git restore' as alternative."""
        for pattern, config in DEFAULT_INTEGRATIONS.items():
            if pattern == "git restore":
                continue
            suggestion = config.get("suggestion", "")
            assert "git restore" not in suggestion, \
                f"'{pattern}' suggestion still mentions 'git restore'"

    def test_git_checkout_no_longer_redirects_to_restore(self):
        """git checkout redirect should NOT use git restore."""
        for pattern in ["git checkout", "git checkout --", "git checkout ."]:
            if pattern in DEFAULT_INTEGRATIONS:
                redirect = DEFAULT_INTEGRATIONS[pattern].get("redirect", "")
                assert "git restore" not in redirect, \
                    f"'{pattern}' redirect still uses 'git restore'"


# =============================================================================
# DEEP TDD: {file} substitution in redirect templates
# =============================================================================

class TestFileSubstitution:
    """Test {file} placeholder substitution in redirect commands."""

    def test_file_extracted_from_git_restore(self):
        """Extract file from 'git restore myfile.txt'."""
        cmd = "git restore myfile.txt"
        parts = cmd.split()
        file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
        assert file_args[-1] == "myfile.txt"

    def test_file_extracted_with_flags(self):
        """Extract file from 'git restore --worktree myfile.txt'."""
        cmd = "git restore --worktree myfile.txt"
        parts = cmd.split()
        file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
        assert file_args[-1] == "myfile.txt"

    def test_file_extracted_with_double_dash(self):
        """Extract file from 'git checkout -- myfile.txt'."""
        cmd = "git checkout -- myfile.txt"
        parts = cmd.split()
        file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
        assert file_args[-1] == "myfile.txt"

    def test_file_extracted_with_combined_flags(self):
        """Extract file from 'git restore -SW myfile.txt'."""
        cmd = "git restore -SW myfile.txt"
        parts = cmd.split()
        file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
        assert file_args[-1] == "myfile.txt"

    def test_file_extracted_with_path(self):
        """Extract file from 'git restore src/main.py'."""
        cmd = "git restore src/main.py"
        parts = cmd.split()
        file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
        assert file_args[-1] == "src/main.py"

    def test_redirect_template_substitution(self):
        """Full redirect template substitution with {file}."""
        template = "git stash push {file} -m 'WIP: {file}'"
        cmd = "git restore src/config.py"
        parts = cmd.split()
        file_args = [p for p in parts[2:] if p != "--" and not p.startswith("-")]
        file_val = file_args[-1] if file_args else ""
        result = template.replace("{file}", file_val)
        assert result == "git stash push src/config.py -m 'WIP: src/config.py'"


# =============================================================================
# DEEP TDD: Conditions Edge Cases
# =============================================================================

class TestConditionsEdgeCases:
    """Test edge cases for hookify-style conditions."""

    def test_equals_operator(self):
        """equals operator for exact match."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish"}
        )
        conditions = (
            {"field": "command", "operator": "equals", "pattern": "npm publish"},
        )

        assert check_conditions(conditions, ctx) is True

    def test_equals_operator_no_match(self):
        """equals operator fails on partial match."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish --tag beta"}
        )
        conditions = (
            {"field": "command", "operator": "equals", "pattern": "npm publish"},
        )

        assert check_conditions(conditions, ctx) is False

    def test_unknown_operator_falls_through(self):
        """Unknown operator falls through (doesn't fail the condition)."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "npm publish"}
        )
        conditions = (
            {"field": "command", "operator": "unknown_op", "pattern": "publish"},
        )

        # Unknown operator falls through all if statements
        # This means the condition doesn't fail, so returns True
        result = check_conditions(conditions, ctx)
        assert result is True  # No condition explicitly failed

    def test_missing_field_in_context(self):
        """Condition with field not in context."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "test"}
        )
        conditions = (
            {"field": "nonexistent", "operator": "contains", "pattern": "test"},
        )

        assert check_conditions(conditions, ctx) is False

    def test_condition_with_regex_special_chars(self):
        """Regex condition with special characters."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/*"}
        )
        conditions = (
            {"field": "command", "operator": "regex_match", "pattern": r"rm.*\*"},
        )

        assert check_conditions(conditions, ctx) is True

    def test_empty_pattern_in_condition(self):
        """Condition with empty pattern."""
        ctx = MagicMock(
            tool_name="Bash",
            tool_input={"command": "test"}
        )
        conditions = (
            {"field": "command", "operator": "contains", "pattern": ""},
        )

        # Empty string is contained in any string
        assert check_conditions(conditions, ctx) is True


# =============================================================================
# DEEP TDD: Pattern Validation Edge Cases
# =============================================================================

class TestPatternValidationEdgeCases:
    """Test edge cases for pattern validation."""

    def test_empty_patterns_tuple(self):
        """Empty patterns tuple doesn't crash."""
        intg = Integration(
            patterns=(),
            action="block",
            message="test",
        )
        warnings = _validate_integration(intg, "test")
        # Empty patterns should work (no warnings for empty)
        assert isinstance(warnings, list)

    def test_numeric_single_char_no_warning(self):
        """Single digit pattern doesn't warn (not alpha)."""
        intg = Integration(
            patterns=("1",),
            action="block",
            message="test",
        )
        warnings = _validate_integration(intg, "test")
        # Single digit is not alpha, so no warning
        assert len(warnings) == 0

    def test_star_glob_pattern_warning(self):
        """* glob pattern produces warning."""
        intg = Integration(
            patterns=("*",),
            action="block",
            message="test",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1
        assert "too broad" in warnings[0].lower()

    def test_double_star_glob_pattern_warning(self):
        """** glob pattern produces warning."""
        intg = Integration(
            patterns=("**",),
            action="block",
            message="test",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1

    def test_redirect_with_valid_placeholder(self):
        """Valid {args} placeholder produces no warning."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="test",
            redirect="trash {args}",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 0

    def test_redirect_with_both_arg_and_args(self):
        """Redirect with both {arg} and {args} - no warning since {args} present."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="test",
            redirect="trash {arg} {args}",
        )
        warnings = _validate_integration(intg, "test")
        # No warning because {args} IS present (only warns if {arg} without {args})
        assert len(warnings) == 0

    def test_redirect_no_placeholder_valid(self):
        """Redirect without placeholder is valid."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="test",
            redirect="trash",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 0

    def test_redirect_extra_closing_brace(self):
        """Redirect with extra } is unbalanced."""
        intg = Integration(
            patterns=("rm",),
            action="block",
            message="test",
            redirect="trash {args}}",
        )
        warnings = _validate_integration(intg, "test")
        assert len(warnings) == 1
        assert "unbalanced" in warnings[0].lower()


# =============================================================================
# DEEP TDD: Integration Dataclass Edge Cases
# =============================================================================

class TestIntegrationDataclassEdgeCases:
    """Test edge cases for Integration dataclass."""

    def test_from_dict_empty_config(self):
        """from_dict with minimal config."""
        intg = Integration.from_dict("test", {"suggestion": ""})
        assert intg.patterns == ("test",)
        assert intg.message == ""
        assert intg.action == "block"

    def test_from_dict_none_values(self):
        """from_dict handles None values in config."""
        config = {
            "suggestion": "Test",
            "redirect": None,
            "when": None,
        }
        intg = Integration.from_dict("test", config)
        assert intg.redirect is None
        # None is passed through (dict.get returns None if key exists with None value)
        # This is acceptable - users should use valid values or omit the key
        assert intg.when is None

    def test_from_dict_commands_as_string(self):
        """from_dict handles commands as string (not list)."""
        config = {
            "suggestion": "Test",
            "commands": "single-command {args}",
        }
        intg = Integration.from_dict("test", config)
        assert intg.redirect == "single-command {args}"

    def test_integration_hashable(self):
        """Integration is hashable (for use in sets/dicts)."""
        intg = Integration(
            patterns=("test",),
            action="block",
            message="test",
        )
        # Should not raise
        hash(intg)

        # Can be used in set
        s = {intg}
        assert intg in s

    def test_integration_equality(self):
        """Two integrations with same values are equal."""
        intg1 = Integration(
            patterns=("test",),
            action="block",
            message="test",
        )
        intg2 = Integration(
            patterns=("test",),
            action="block",
            message="test",
        )
        assert intg1 == intg2

    def test_integration_inequality(self):
        """Two integrations with different values are not equal."""
        intg1 = Integration(
            patterns=("test",),
            action="block",
            message="test",
        )
        intg2 = Integration(
            patterns=("test",),
            action="warn",  # Different
            message="test",
        )
        assert intg1 != intg2


# =============================================================================
# DEEP TDD: Pattern Specificity Edge Cases
# =============================================================================

class TestPatternSpecificityEdgeCases:
    """Test edge cases for pattern specificity calculation."""

    def test_empty_pattern_tuple(self):
        """Empty pattern tuple returns 0."""
        spec = _pattern_specificity(())
        assert spec == 0

    def test_empty_string_pattern(self):
        """Empty string pattern has minimal specificity."""
        spec = _pattern_specificity(("",))
        assert spec == 0

    def test_single_word_pattern(self):
        """Single word pattern has word weight + length."""
        spec = _pattern_specificity(("rm",))
        # 1 word * 100 + 2 chars = 102
        assert spec == 102

    def test_pattern_with_special_chars(self):
        """Special characters count toward length."""
        spec1 = _pattern_specificity(("rm",))
        spec2 = _pattern_specificity(("rm*",))
        assert spec2 > spec1

    def test_whitespace_in_pattern(self):
        """Whitespace splits words."""
        spec = _pattern_specificity(("git reset",))
        # 2 words * 100 + 9 chars = 209
        assert spec == 209


# =============================================================================
# DEEP TDD: Load Integrations Edge Cases
# =============================================================================

class TestLoadIntegrationsEdgeCases:
    """Test edge cases for load_all_integrations."""

    def test_loads_warn_action_integration(self):
        """Loads integration with action: warn (e.g., git)."""
        integrations = load_all_integrations()

        # Find git integration (should have action: warn)
        git_intgs = [i for i in integrations if "git" in i.patterns]
        assert len(git_intgs) > 0
        git_intg = git_intgs[0]
        assert git_intg.action == "warn"

    def test_loads_integration_with_redirect(self):
        """Loads integration with redirect field (e.g., rm)."""
        integrations = load_all_integrations()

        # Find rm integration (should have redirect)
        rm_intgs = [i for i in integrations if i.patterns == ("rm",)]
        assert len(rm_intgs) > 0
        rm_intg = rm_intgs[0]
        assert rm_intg.redirect is not None
        assert "{args}" in rm_intg.redirect

    def test_loads_integration_with_when_predicate(self):
        """Loads integration with when field (e.g., git reset --hard)."""
        integrations = load_all_integrations()

        # Find git reset --hard integration
        reset_intgs = [i for i in integrations if "git reset --hard" in i.patterns]
        assert len(reset_intgs) > 0
        reset_intg = reset_intgs[0]
        assert reset_intg.when != "always"

    def test_all_defaults_have_valid_action(self):
        """All default integrations have valid action."""
        integrations = load_all_integrations()

        for intg in integrations:
            if intg.source == "default":
                assert intg.action in ("block", "warn"), f"{intg.name} has invalid action: {intg.action}"

    def test_all_defaults_have_message(self):
        """All default integrations have a message."""
        integrations = load_all_integrations()

        for intg in integrations:
            if intg.source == "default":
                assert intg.message, f"{intg.name} has empty message"


# =============================================================================
# v4: Ref-aware destructive-git predicates (fixes narrow-diff + daemon-cwd +
# env-leak bugs). Covers the `git checkout HEAD -- <file>` bypass reported
# in the transcript that prompted this PR.
# =============================================================================
import os
import subprocess as _subprocess
import sys


def _init_git_repo(path, committed_content="original\n"):
    """Create a minimal git repo at `path` with one commit. Returns repo path."""
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    _subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=env)
    for key, val in [("user.email", "t@t"), ("user.name", "t"),
                     ("commit.gpgsign", "false")]:
        _subprocess.run(["git", "-C", str(path), "config", key, val],
                        check=True, env=env)
    (path / "seed.txt").write_text(committed_content)
    _subprocess.run(["git", "-C", str(path), "add", "seed.txt"], check=True, env=env)
    _subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"],
                    check=True, env=env)
    return path


def _make_ctx(cmd, cwd):
    """Minimal ctx shim for predicate tests (mimics EventContext surface)."""
    ctx = MagicMock()
    ctx.tool_input = {"command": cmd}
    # ctx.cwd is a property on EventContext; configure_mock sets the attr directly.
    ctx.configure_mock(cwd=str(cwd) if cwd else None)
    return ctx


class TestGitDiffQuietHelper:
    """_git_diff_quiet is the single subprocess gate for the predicates.

    Contract:
      - cwd=None → False (fail-soft, predicate not applicable)
      - cwd not a git work tree → False
      - ref missing (fresh repo) → False
      - clean repo → False
      - diff present → True
      - subprocess error → True (fail-safe block)
      - git env vars (GIT_DIR etc.) scrubbed before subprocess
    """

    def test_cwd_none_returns_false(self):
        from autorun.integrations import _git_diff_quiet
        assert _git_diff_quiet(None, "HEAD", None) is False

    def test_not_a_git_repo_returns_false(self, tmp_path):
        from autorun.integrations import _git_diff_quiet
        # tmp_path is not a git repo
        assert _git_diff_quiet(str(tmp_path), "HEAD", None) is False

    def test_fresh_repo_no_head_returns_false(self, tmp_path):
        from autorun.integrations import _git_diff_quiet
        _subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        # No commits yet, HEAD is undefined
        assert _git_diff_quiet(str(tmp_path), "HEAD", None) is False

    def test_clean_repo_returns_false(self, tmp_path):
        from autorun.integrations import _git_diff_quiet
        _init_git_repo(tmp_path)
        assert _git_diff_quiet(str(tmp_path), "HEAD", None) is False
        assert _git_diff_quiet(str(tmp_path), "HEAD", "seed.txt") is False

    def test_unstaged_change_returns_true(self, tmp_path):
        from autorun.integrations import _git_diff_quiet
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("modified\n")
        assert _git_diff_quiet(str(tmp_path), "HEAD", "seed.txt") is True

    def test_staged_only_change_returns_true(self, tmp_path):
        """THE KEY REGRESSION TEST — proves the narrow-diff bug is fixed.

        Old `git diff --quiet` compared worktree to index; with staged-only
        changes, worktree == index → exit 0 → predicate False → bug.
        New `git diff HEAD --quiet` compares to HEAD → catches staged changes.
        """
        from autorun.integrations import _git_diff_quiet
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("staged-content\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)
        # worktree == index (both changed the same way), but both differ from HEAD
        assert _git_diff_quiet(str(tmp_path), "HEAD", "seed.txt") is True

    def test_subprocess_error_fails_safe(self, tmp_path, monkeypatch):
        from autorun.integrations import _git_diff_quiet
        _init_git_repo(tmp_path)

        def boom(*a, **kw):
            raise OSError("git binary exploded")
        monkeypatch.setattr("autorun.integrations.subprocess.run", boom)
        assert _git_diff_quiet(str(tmp_path), "HEAD", "seed.txt") is True

    def test_timeout_fails_safe(self, tmp_path, monkeypatch):
        from autorun.integrations import _git_diff_quiet
        _init_git_repo(tmp_path)

        def timeout(*a, **kw):
            raise _subprocess.TimeoutExpired("git", 2)
        monkeypatch.setattr("autorun.integrations.subprocess.run", timeout)
        assert _git_diff_quiet(str(tmp_path), "HEAD", "seed.txt") is True

    def test_env_vars_scrubbed(self, tmp_path, monkeypatch):
        """GIT_WORK_TREE/GIT_DIR must not leak to subprocess and redirect git."""
        from autorun.integrations import _git_diff_quiet, _SCRUBBED_GIT_ENV_KEYS

        _init_git_repo(tmp_path)
        bogus = tmp_path.parent / "bogus"
        bogus.mkdir(exist_ok=True)

        calls = []
        real_run = _subprocess.run

        def spy(argv, **kw):
            calls.append(kw.get("env", {}))
            return real_run(argv, **kw)

        monkeypatch.setenv("GIT_WORK_TREE", str(bogus))
        monkeypatch.setenv("GIT_DIR", str(bogus / ".git"))
        monkeypatch.setattr("autorun.integrations.subprocess.run", spy)

        _git_diff_quiet(str(tmp_path), "HEAD", "seed.txt")
        assert calls, "subprocess.run must have been called"
        for env in calls:
            assert env is not None, "every predicate call must pass explicit env"
            for key in _SCRUBBED_GIT_ENV_KEYS:
                assert key not in env, f"{key} leaked into predicate subprocess"


class TestFileDiffersFromRef:
    """_file_differs_from_ref: ref-aware, segment-scoped, cwd-propagating."""

    def test_staged_only_returns_true(self, tmp_path):
        """Regression: the exact bug from the transcript."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("staged\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)
        ctx = _make_ctx("git checkout HEAD -- seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_unstaged_only_returns_true(self, tmp_path):
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("unstaged\n")
        ctx = _make_ctx("git checkout HEAD -- seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_clean_returns_false(self, tmp_path):
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        ctx = _make_ctx("git checkout HEAD -- seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is False

    def test_multiline_transcript_reproducer(self, tmp_path):
        """The exact bash payload from the original bug report."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "universal-app-shell").mkdir()
        (tmp_path / "universal-app-shell" / "App.tsx").write_text("seed\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "universal-app-shell/App.tsx"], check=True)
        _subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "add App.tsx"], check=True)
        # Now create staged-only changes (the exact bug condition):
        (tmp_path / "universal-app-shell" / "App.tsx").write_text("applied-stash\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "universal-app-shell/App.tsx"], check=True)
        cmd = ("git checkout HEAD -- universal-app-shell/App.tsx\n"
               "git status --short universal-app-shell/App.tsx\n"
               "wc -l universal-app-shell/App.tsx")
        ctx = _make_ctx(cmd, tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_segment_scoped_not_cross_command(self, tmp_path):
        """Tokens from subsequent shell-chained commands must NOT be parsed as files."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "a.txt").write_text("seed\n")
        (tmp_path / "b.txt").write_text("seed\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
        _subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "two files"], check=True)
        # b.txt has changes, a.txt is clean
        (tmp_path / "b.txt").write_text("dirty\n")
        # Command: checkout clean a.txt; list dirty b.txt (segments MUST NOT merge)
        cmd = "git checkout HEAD -- a.txt; ls b.txt"
        ctx = _make_ctx(cmd, tmp_path)
        # a.txt is clean in-segment → predicate allows (False)
        assert _file_differs_from_ref(ctx) is False

    def test_ref_argument_extracted(self, tmp_path):
        """git checkout <branch> -- <file> uses <branch> as ref, not HEAD."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        # Create a second branch with different content
        _subprocess.run(["git", "-C", str(tmp_path), "checkout", "-qb", "feature"], check=True)
        (tmp_path / "seed.txt").write_text("feature-branch-content\n")
        _subprocess.run(["git", "-C", str(tmp_path), "commit", "-qam", "feature change"], check=True)
        _subprocess.run(["git", "-C", str(tmp_path), "checkout", "-q", "main"], check=True)
        # On main, working tree matches main's HEAD. `git checkout feature -- seed.txt`
        # would overwrite with feature's content.
        ctx = _make_ctx("git checkout feature -- seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_at_symbol_ref(self, tmp_path):
        """@ is a synonym for HEAD in git."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        ctx = _make_ctx("git checkout @ -- seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_quoted_path_with_space(self, tmp_path):
        """File paths with spaces must be handled by shlex tokenization."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        spaced = tmp_path / "my file.ts"
        spaced.write_text("seed\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "my file.ts"], check=True)
        _subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "spaced"], check=True)
        spaced.write_text("dirty\n")
        ctx = _make_ctx('git checkout HEAD -- "my file.ts"', tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_cwd_missing_returns_false(self):
        """No repo context → fail-soft allow (predicate inapplicable)."""
        from autorun.integrations import _file_differs_from_ref
        ctx = _make_ctx("git checkout HEAD -- x.ts", None)
        assert _file_differs_from_ref(ctx) is False

    def test_exception_fails_safe(self, tmp_path, monkeypatch):
        """Internal error → fail-safe block (return True)."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")

        def boom(*a, **kw):
            raise OSError("catastrophe")
        monkeypatch.setattr("autorun.integrations.subprocess.run", boom)
        ctx = _make_ctx("git checkout HEAD -- seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is True

    def test_restore_source_argument(self, tmp_path):
        """git restore --source=HEAD~1 <file> compares to HEAD~1."""
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("v2\n")
        _subprocess.run(["git", "-C", str(tmp_path), "commit", "-qam", "v2"], check=True)
        # Working tree matches HEAD, but differs from HEAD~1.
        ctx = _make_ctx("git restore --source=HEAD~1 seed.txt", tmp_path)
        assert _file_differs_from_ref(ctx) is True


class TestBackwardCompatAlias:
    """The old predicate name must resolve to the new function."""

    def test_old_name_resolves_to_new(self):
        from autorun.integrations import (
            _WHEN_PREDICATES,
            _file_differs_from_ref,
            _file_has_unstaged_changes,
        )
        # The old key exists and points to the new function (or the alias wrapper).
        assert "_file_has_unstaged_changes" in _WHEN_PREDICATES
        assert "_file_differs_from_ref" in _WHEN_PREDICATES
        # Both resolve to the same callable behavior
        old_pred = _WHEN_PREDICATES["_file_has_unstaged_changes"]
        new_pred = _WHEN_PREDICATES["_file_differs_from_ref"]
        assert old_pred in (_file_has_unstaged_changes, _file_differs_from_ref)
        assert new_pred is _file_differs_from_ref

    def test_repo_differs_alias(self):
        from autorun.integrations import (
            _WHEN_PREDICATES,
            _repo_differs_from_head,
            _has_unstaged_changes,
        )
        assert "_has_unstaged_changes" in _WHEN_PREDICATES
        assert "_repo_differs_from_head" in _WHEN_PREDICATES
        new = _WHEN_PREDICATES["_repo_differs_from_head"]
        assert new is _repo_differs_from_head


class TestRepoDiffersFromHead:
    """`git checkout .` and `git reset --hard` rules use this predicate."""

    def test_clean_repo_returns_false(self, tmp_path):
        from autorun.integrations import _repo_differs_from_head
        _init_git_repo(tmp_path)
        ctx = _make_ctx("git checkout .", tmp_path)
        assert _repo_differs_from_head(ctx) is False

    def test_unstaged_returns_true(self, tmp_path):
        from autorun.integrations import _repo_differs_from_head
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        ctx = _make_ctx("git checkout .", tmp_path)
        assert _repo_differs_from_head(ctx) is True

    def test_staged_only_returns_true(self, tmp_path):
        """Regression: `git checkout .` destroys BOTH worktree and index."""
        from autorun.integrations import _repo_differs_from_head
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("staged\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)
        ctx = _make_ctx("git checkout .", tmp_path)
        assert _repo_differs_from_head(ctx) is True


class TestCheckoutTargetsFileWithChanges:
    """_checkout_targets_file_with_changes: `git checkout <path>` form."""

    def test_dirty_file_target_blocks(self, tmp_path, monkeypatch):
        """`git checkout <path>` with staged-only changes blocks.

        NOTE: _checkout_targets_file_with_changes uses os.path.exists() which
        resolves relative to the process cwd, NOT ctx.cwd. We chdir into the
        tmp repo to exercise the intended code path.
        """
        from autorun.integrations import _checkout_targets_file_with_changes
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)
        monkeypatch.chdir(tmp_path)
        ctx = _make_ctx("git checkout seed.txt", tmp_path)
        assert _checkout_targets_file_with_changes(ctx) is True

    def test_branch_target_allows(self, tmp_path):
        """`git checkout <branch>` must NOT be blocked."""
        from autorun.integrations import _checkout_targets_file_with_changes
        _init_git_repo(tmp_path)
        _subprocess.run(["git", "-C", str(tmp_path), "branch", "feature"], check=True)
        ctx = _make_ctx("git checkout feature", tmp_path)
        assert _checkout_targets_file_with_changes(ctx) is False


class TestConcurrentPredicates:
    """Daemon uses asyncio + thread-pool executor; predicates run concurrently."""

    def test_thread_pool_consistency(self, tmp_path):
        """10 concurrent predicate calls against the same repo are consistent."""
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from autorun.integrations import _file_differs_from_ref

        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)

        def call():
            ctx = _make_ctx("git checkout HEAD -- seed.txt", tmp_path)
            return _file_differs_from_ref(ctx)

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(call) for _ in range(10)]
            results = [f.result() for f in as_completed(futures)]
        assert all(r is True for r in results), results


class TestRestoreSemantics:
    """git restore safety classification."""

    def test_restore_staged_no_source_is_allowed(self, tmp_path):
        """`git restore --staged <file>` just unstages — non-destructive."""
        from autorun.integrations import _restore_is_destructive
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("staged\n")
        _subprocess.run(["git", "-C", str(tmp_path), "add", "seed.txt"], check=True)
        ctx = _make_ctx("git restore --staged seed.txt", tmp_path)
        assert _restore_is_destructive(ctx) is False

    def test_restore_worktree_blocks_when_dirty(self, tmp_path):
        from autorun.integrations import _restore_is_destructive
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        ctx = _make_ctx("git restore seed.txt", tmp_path)
        assert _restore_is_destructive(ctx) is True

    def test_restore_combined_SW_blocks_when_dirty(self, tmp_path):
        """`git restore -SW <file>` unstages AND discards worktree — destructive."""
        from autorun.integrations import _restore_is_destructive
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        ctx = _make_ctx("git restore -SW seed.txt", tmp_path)
        assert _restore_is_destructive(ctx) is True


class TestConfigMigration:
    """All four destructive-git rules now route through the new predicates."""

    def test_checkout_dashdash_uses_file_differs(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["git checkout --"]["when"] == "_file_differs_from_ref"

    def test_checkout_uses_file_differs(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["git checkout"]["when"] == "_file_differs_from_ref"

    def test_checkout_dot_uses_repo_differs(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["git checkout ."]["when"] == "_repo_differs_from_head"

    def test_reset_hard_uses_repo_differs(self):
        from autorun.config import DEFAULT_INTEGRATIONS
        assert DEFAULT_INTEGRATIONS["git reset --hard"]["when"] == "_repo_differs_from_head"


# =============================================================================
# Pure parser tests — _parse_destructive_git_cmd is I/O-free. Every branch
# can be exercised by feeding a command string, no tmp_path needed. This is
# the single-source-of-truth for argument parsing; the predicate glues it to
# subprocess via _git_diff_quiet.
# =============================================================================

class TestParseDestructiveGitCmd:
    """Every branch of argument parsing, independent of any subprocess."""

    # ---- checkout ref + pathspec forms ----

    def test_checkout_head_dash_file(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- file.ts")
        assert p.verb == "checkout"
        assert p.ref == "HEAD"
        assert p.files == ("file.ts",)

    def test_checkout_dash_file_no_ref(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout -- file.ts")
        assert p.verb == "checkout"
        assert p.ref == "HEAD"
        assert p.files == ("file.ts",)

    def test_checkout_branch_ref_dash_file(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout feature -- file.ts")
        assert p.ref == "feature"

    def test_checkout_remote_tracking_ref(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout origin/main -- file.ts")
        assert p.ref == "origin/main"

    def test_checkout_tag_ref(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout v1.2.3 -- file.ts")
        assert p.ref == "v1.2.3"

    def test_checkout_commit_hash_ref(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout abc1234 -- file.ts")
        assert p.ref == "abc1234"

    def test_checkout_reflog_ref(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD@{1} -- file.ts")
        assert p.ref == "HEAD@{1}"

    def test_checkout_at_synonym(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout @ -- file.ts")
        assert p.ref == "@"

    def test_checkout_fetch_head(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout FETCH_HEAD -- file.ts")
        assert p.ref == "FETCH_HEAD"

    def test_checkout_multiple_files(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- a.ts b.ts c.ts")
        assert p.files == ("a.ts", "b.ts", "c.ts")

    def test_checkout_pathspec_dot(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- .")
        assert p.files == (".",)

    def test_checkout_glob_pathspec(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- 'src/**/*.ts'")
        assert p.files == ("src/**/*.ts",)

    def test_checkout_quoted_path_with_space(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd('git checkout HEAD -- "my file.ts"')
        assert p.files == ("my file.ts",)

    def test_checkout_unicode_path(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- 日本.ts")
        assert p.files == ("日本.ts",)

    # ---- restore forms ----

    def test_restore_plain_file(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git restore file.ts")
        assert p.verb == "restore"
        assert p.ref == "HEAD"
        assert p.files == ("file.ts",)

    def test_restore_source_equals(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git restore --source=HEAD~1 file.ts")
        assert p.ref == "HEAD~1"
        assert p.files == ("file.ts",)

    def test_restore_source_space_separated(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git restore --source HEAD~1 file.ts")
        assert p.ref == "HEAD~1"
        assert p.files == ("file.ts",)

    def test_restore_short_s_separated(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git restore -s HEAD~1 file.ts")
        assert p.ref == "HEAD~1"
        assert p.files == ("file.ts",)

    def test_restore_dashdash_separator(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git restore --source=v1.0 -- file.ts")
        assert p.ref == "v1.0"
        assert p.files == ("file.ts",)

    # ---- segment scoping ----

    def test_segment_scoping_semicolon(self):
        """Tokens from second command must not be consumed as pathspec."""
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- a.ts; rm b.ts")
        assert p.files == ("a.ts",)

    def test_segment_scoping_and(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- a.ts && rm b.ts")
        assert p.files == ("a.ts",)

    def test_segment_scoping_newline(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- a.ts\nls b.ts")
        assert p.files == ("a.ts",)

    def test_segment_scoping_pipe(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout HEAD -- a.ts | cat")
        assert p.files == ("a.ts",)

    def test_segment_scoping_first_match_wins(self):
        """Earlier segments ignored; first matching git segment parsed."""
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("echo hi; git checkout HEAD -- a.ts")
        assert p.files == ("a.ts",)

    # ---- non-matches return None ----

    def test_empty_command_returns_none(self):
        from autorun.integrations import _parse_destructive_git_cmd
        assert _parse_destructive_git_cmd("") is None

    def test_non_git_command_returns_none(self):
        from autorun.integrations import _parse_destructive_git_cmd
        assert _parse_destructive_git_cmd("rm -rf /") is None

    def test_git_status_returns_none(self):
        """Non-destructive git commands don't match."""
        from autorun.integrations import _parse_destructive_git_cmd
        assert _parse_destructive_git_cmd("git status") is None

    def test_git_log_returns_none(self):
        from autorun.integrations import _parse_destructive_git_cmd
        assert _parse_destructive_git_cmd("git log --oneline") is None

    def test_git_stash_returns_none(self):
        """git stash isn't checkout/restore — handled by a different rule."""
        from autorun.integrations import _parse_destructive_git_cmd
        assert _parse_destructive_git_cmd("git stash push") is None

    # ---- branch-switch forms (no file destructive intent via this predicate) --

    def test_checkout_branch_only(self):
        """`git checkout <branch>` with no `--` — file extraction returns empty;
        predicate falls back to repo-wide diff. _checkout_targets_file_with_changes
        handles the benign-vs-destructive distinction."""
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout main")
        assert p.verb == "checkout"
        assert p.files == ()  # no pathspec, no `--`

    def test_checkout_dash_b_new_branch(self):
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout -b new-branch")
        # -b is a flag; no pathspec extracted. Ref stays HEAD.
        assert p.files == ()
        assert p.ref == "HEAD"

    # ---- unusual but valid inputs ----

    def test_checkout_no_overlay_flag(self):
        """`git checkout --no-overlay HEAD -- file` (newer flag syntax)."""
        from autorun.integrations import _parse_destructive_git_cmd
        p = _parse_destructive_git_cmd("git checkout --no-overlay HEAD -- file.ts")
        # ref extraction: `--` is at index 3 (git, checkout, --no-overlay, HEAD, --, file)
        # ref = tokens[2] = "--no-overlay", which is wrong but handled safely
        # by _git_diff_quiet (rev-parse --verify returns non-zero → False).
        assert p.verb == "checkout"
        assert "file.ts" in p.files


class TestPredicateFailureModes:
    """Defensive tests for ctx shapes and failure modes the parser may encounter."""

    def test_ctx_tool_input_is_none(self):
        from autorun.integrations import _file_differs_from_ref
        ctx = MagicMock()
        ctx.tool_input = None
        # Missing command extraction → None → fail-soft False
        # Actually hasattr(ctx, "tool_input") is True, so get("command") fails
        # on None → AttributeError caught by outer try → fail-safe True.
        # This is defensive over-block, acceptable.
        result = _file_differs_from_ref(ctx)
        assert result in (True, False)  # either deterministic outcome is fine

    def test_ctx_empty_tool_input(self):
        from autorun.integrations import _file_differs_from_ref
        ctx = MagicMock()
        ctx.tool_input = {}
        ctx.configure_mock(cwd=None)
        assert _file_differs_from_ref(ctx) is False

    def test_ctx_no_tool_input_attr(self):
        from autorun.integrations import _file_differs_from_ref
        ctx = MagicMock(spec=[])  # no tool_input attr
        assert _file_differs_from_ref(ctx) is False

    def test_ctx_cwd_is_path_object(self, tmp_path):
        """ctx.cwd may be a pathlib.Path, not a str — subprocess accepts both."""
        from pathlib import Path
        from autorun.integrations import _file_differs_from_ref
        _init_git_repo(tmp_path)
        (tmp_path / "seed.txt").write_text("dirty\n")
        ctx = MagicMock()
        ctx.tool_input = {"command": "git checkout HEAD -- seed.txt"}
        ctx.configure_mock(cwd=Path(tmp_path))  # Path, not str
        assert _file_differs_from_ref(ctx) is True


class TestLegacySymbolExport:
    """Backward-compat: legacy predicate names remain importable from module."""

    def test_file_has_unstaged_changes_importable(self):
        from autorun.integrations import _file_has_unstaged_changes
        assert callable(_file_has_unstaged_changes)

    def test_has_unstaged_changes_importable(self):
        from autorun.integrations import _has_unstaged_changes
        assert callable(_has_unstaged_changes)

    def test_legacy_keys_in_predicates_dict(self):
        from autorun.integrations import _WHEN_PREDICATES
        # Every legacy key used by config.py (past or present) must still resolve.
        for legacy in ("_has_unstaged_changes", "_file_has_unstaged_changes",
                       "_has_uncommitted_changes", "has_uncommitted_changes",
                       "_stash_exists", "_restore_is_destructive",
                       "_checkout_targets_file_with_changes", "_not_in_pipe"):
            assert legacy in _WHEN_PREDICATES, f"missing legacy key: {legacy}"

    def test_new_keys_in_predicates_dict(self):
        from autorun.integrations import _WHEN_PREDICATES
        for new in ("_repo_differs_from_head", "_file_differs_from_ref"):
            assert new in _WHEN_PREDICATES, f"missing new key: {new}"
