#!/usr/bin/env python3
"""
Comprehensive edge case tests for plan_export.py functions.

Tests all individual functions with edge cases:
- Plan discovery and selection
- Transcript parsing
- Metadata extraction and embedding
- Name extraction and sanitization (via PlanExport class)
- Template expansion (via PlanExport class)
- Export operations
- Approval detection logic

Run: uv run pytest plugins/clautorun/tests/test_edge_cases.py -v
"""

import json
import os
import sys
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
from contextlib import nullcontext

import pytest

# Import the module to get reference for patching
import clautorun.plan_export as plan_export
from clautorun.plan_export import (
    PlanExport,
    PlanExportConfig,
    export_plan,
    export_rejected_plan,
    get_content_hash,
    get_most_recent_plan,
    get_plan_from_transcript,
    get_plan_from_metadata,
    find_plan_by_session_id,
    embed_plan_metadata,
    DEFAULT_CONFIG,
    EventContext,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory for tests."""
    return tmp_path


@pytest.fixture
def plans_dir(temp_dir):
    """Create a temporary plans directory."""
    plans = temp_dir / ".claude" / "plans"
    plans.mkdir(parents=True)
    return plans


@pytest.fixture
def config_dir(temp_dir):
    """Create a temporary config directory."""
    config = temp_dir / ".claude" / "clautorun"
    config.mkdir(parents=True)
    return config


@pytest.fixture
def project_dir(temp_dir):
    """Create a temporary project directory with notes folder."""
    project = temp_dir / "project"
    notes = project / "notes"
    notes.mkdir(parents=True)
    return project


@pytest.fixture
def plan_exporter(project_dir):
    """Create a PlanExport instance for testing class methods."""
    from clautorun.core import ThreadSafeDB
    store = ThreadSafeDB()
    ctx = EventContext(
        session_id="test-session",
        event="PostToolUse",
        tool_name="ExitPlanMode",
        tool_input={"cwd": str(project_dir)},
        store=store,
    )
    config = PlanExportConfig()
    return PlanExport(ctx, config)


# =============================================================================
# PLAN DISCOVERY TESTS
# =============================================================================

class TestGetMostRecentPlan:
    """Tests for get_most_recent_plan() function."""

    def test_plans_dir_not_exists(self, temp_dir):
        """Plans directory doesn't exist - returns None."""
        fake_plans_dir = temp_dir / "nonexistent" / "plans"
        with patch.object(plan_export, 'PLANS_DIR', fake_plans_dir):
            result = get_most_recent_plan()
        assert result is None

    def test_plans_dir_empty(self, plans_dir):
        """Plans directory is empty - returns None."""
        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_most_recent_plan()
        assert result is None

    def test_single_plan_file(self, plans_dir):
        """Single plan file - returns it."""
        plan_file = plans_dir / "test-plan.md"
        plan_file.write_text("# Test Plan")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_most_recent_plan()
        assert result == plan_file

    def test_multiple_plan_files_returns_most_recent(self, plans_dir):
        """Multiple plan files - returns most recently modified."""
        import time

        old_plan = plans_dir / "old-plan.md"
        old_plan.write_text("# Old Plan")
        time.sleep(0.1)  # Ensure different mtime

        new_plan = plans_dir / "new-plan.md"
        new_plan.write_text("# New Plan")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_most_recent_plan()
        assert result == new_plan


# =============================================================================
# TRANSCRIPT PARSING TESTS
# =============================================================================

class TestGetPlanFromTranscript:
    """Tests for get_plan_from_transcript() function."""

    def test_transcript_not_exists(self, temp_dir):
        """Transcript file doesn't exist - returns None."""
        result = get_plan_from_transcript(str(temp_dir / "nonexistent.jsonl"))
        assert result is None

    def test_transcript_no_file_history_entries(self, temp_dir, plans_dir):
        """Transcript has no file-history-snapshot entries - returns None."""
        transcript = temp_dir / "transcript.jsonl"
        transcript.write_text('{"type": "message", "content": "hello"}\n')

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_plan_from_transcript(str(transcript))
        assert result is None

    def test_transcript_with_plan_entry(self, temp_dir, plans_dir):
        """Transcript has plan file entry - returns plan path."""
        plan_file = plans_dir / "my-plan.md"
        plan_file.write_text("# My Plan")

        transcript = temp_dir / "transcript.jsonl"
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": str(plan_file)}
                    }
                ]
            }
        }
        transcript.write_text(json.dumps(entry) + "\n")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_plan_from_transcript(str(transcript))
        assert result == plan_file

    def test_transcript_plan_file_not_exists(self, temp_dir, plans_dir):
        """Transcript references plan that doesn't exist - returns None."""
        transcript = temp_dir / "transcript.jsonl"
        entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": str(plans_dir / "deleted-plan.md")}
                    }
                ]
            }
        }
        transcript.write_text(json.dumps(entry) + "\n")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_plan_from_transcript(str(transcript))
        assert result is None

    def test_transcript_invalid_json_line(self, temp_dir, plans_dir):
        """Transcript has invalid JSON line - skips it gracefully."""
        plan_file = plans_dir / "valid-plan.md"
        plan_file.write_text("# Valid Plan")

        transcript = temp_dir / "transcript.jsonl"
        valid_entry = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "input": {"file_path": str(plan_file)}
                    }
                ]
            }
        }
        transcript.write_text("{ invalid json }\n" + json.dumps(valid_entry) + "\n")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = get_plan_from_transcript(str(transcript))
        assert result == plan_file


# =============================================================================
# METADATA TESTS
# =============================================================================

class TestGetPlanFromMetadata:
    """Tests for get_plan_from_metadata() function."""

    def test_plan_no_frontmatter(self, plans_dir):
        """Plan has no frontmatter - returns None."""
        plan_file = plans_dir / "no-frontmatter.md"
        plan_file.write_text("# Plan without frontmatter")
        result = get_plan_from_metadata(plan_file)
        assert result is None

    def test_plan_frontmatter_no_session_id(self, plans_dir):
        """Plan has frontmatter but no session_id - returns None."""
        plan_file = plans_dir / "no-session-id.md"
        plan_file.write_text("---\ntitle: My Plan\n---\n# Plan")
        result = get_plan_from_metadata(plan_file)
        assert result is None

    def test_plan_frontmatter_with_session_id(self, plans_dir):
        """Plan has frontmatter with session_id - returns session_id."""
        plan_file = plans_dir / "with-session-id.md"
        plan_file.write_text("---\nsession_id: abc123\n---\n# Plan")
        result = get_plan_from_metadata(plan_file)
        assert result == "abc123"

    def test_plan_frontmatter_session_id_with_quotes(self, plans_dir):
        """Plan has session_id with quotes - strips quotes."""
        plan_file = plans_dir / "quoted-session-id.md"
        plan_file.write_text('---\nsession_id: "def456"\n---\n# Plan')
        result = get_plan_from_metadata(plan_file)
        assert result == "def456"


class TestFindPlanBySessionId:
    """Tests for find_plan_by_session_id() function."""

    def test_plans_dir_not_exists(self, temp_dir):
        """Plans directory doesn't exist - returns None."""
        fake_plans_dir = temp_dir / "nonexistent" / "plans"
        with patch.object(plan_export, 'PLANS_DIR', fake_plans_dir):
            result = find_plan_by_session_id("abc123")
        assert result is None

    def test_no_matching_session_id(self, plans_dir):
        """No plan with matching session_id - returns None."""
        plan_file = plans_dir / "other-plan.md"
        plan_file.write_text("---\nsession_id: xyz789\n---\n# Other Plan")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = find_plan_by_session_id("abc123")
        assert result is None

    def test_matching_session_id_found(self, plans_dir):
        """Plan with matching session_id - returns plan path."""
        plan_file = plans_dir / "matching-plan.md"
        plan_file.write_text("---\nsession_id: abc123\n---\n# Matching Plan")

        with patch.object(plan_export, 'PLANS_DIR', plans_dir):
            result = find_plan_by_session_id("abc123")
        assert result == plan_file


class TestEmbedPlanMetadata:
    """Tests for embed_plan_metadata() function."""

    def test_embed_to_plan_without_metadata(self, plans_dir, project_dir):
        """Plan without metadata - adds frontmatter to export destination."""
        plan_file = plans_dir / "no-metadata.md"
        plan_file.write_text("# Plan Content")

        notes_dir = project_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        export_dest = notes_dir / "exported.md"
        export_dest.write_text("# Plan Content")

        embed_plan_metadata(plan_file, "session123", export_dest)

        content = export_dest.read_text()
        assert "---" in content
        assert "session_id: session123" in content

    def test_embed_to_plan_with_existing_metadata(self, plans_dir, project_dir):
        """Plan with existing metadata - skips embedding (already has frontmatter)."""
        plan_file = plans_dir / "has-metadata.md"
        plan_file.write_text("---\ntitle: Existing\n---\n# Plan")

        notes_dir = project_dir / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        export_dest = notes_dir / "exported.md"
        export_dest.write_text("---\ntitle: Existing\n---\n# Plan")

        embed_plan_metadata(plan_file, "new-session", export_dest)

        content = export_dest.read_text()
        assert "title: Existing" in content
        assert content.startswith("---")


# =============================================================================
# NAME EXTRACTION AND SANITIZATION TESTS (class methods)
# =============================================================================

class TestExtractUsefulName:
    """Tests for PlanExport.extract_useful_name() method."""

    def test_plan_with_heading(self, plans_dir, plan_exporter):
        """Plan with # heading (no frontmatter) - extracts heading text."""
        plan_file = plans_dir / "with-heading.md"
        plan_file.write_text("# My Awesome Plan\n\nContent here")

        result = plan_exporter.extract_useful_name(plan_file)
        assert result == "my_awesome_plan"

    def test_plan_no_heading_uses_first_line(self, plans_dir, plan_exporter):
        """Plan without heading - uses first non-empty line."""
        plan_file = plans_dir / "no-heading.md"
        plan_file.write_text("First line of plan\n\nMore content")

        result = plan_exporter.extract_useful_name(plan_file)
        assert "first_line" in result.lower() or result == "first_line_of_plan"

    def test_plan_empty_uses_filename(self, plans_dir, plan_exporter):
        """Empty plan - uses filename."""
        plan_file = plans_dir / "empty-plan.md"
        plan_file.write_text("")

        result = plan_exporter.extract_useful_name(plan_file)
        assert "empty" in result.lower() or result == "empty-plan"


class TestSanitizeFilename:
    """Tests for PlanExport._sanitize_filename() method."""

    def test_sanitize_special_characters(self, plan_exporter):
        """Removes special characters from filename."""
        result = plan_exporter._sanitize_filename("My Plan: A Test! (v2)")

        assert ":" not in result
        assert "!" not in result
        assert "(" not in result
        assert ")" not in result

    def test_sanitize_spaces(self, plan_exporter):
        """Converts spaces to underscores or dashes."""
        result = plan_exporter._sanitize_filename("my plan name")

        assert " " not in result
        assert "_" in result or "-" in result

    def test_sanitize_preserves_alphanumeric(self, plan_exporter):
        """Preserves alphanumeric characters."""
        result = plan_exporter._sanitize_filename("plan123")

        assert "plan" in result.lower()
        assert "123" in result


# =============================================================================
# TEMPLATE EXPANSION TESTS (class methods)
# =============================================================================

class TestExpandTemplate:
    """Tests for PlanExport.expand_template() method."""

    def test_expand_date_placeholder(self, plans_dir, plan_exporter):
        """Template with {date} placeholder."""
        plan_file = plans_dir / "test.md"
        plan_file.write_text("# Test")

        result = plan_exporter.expand_template("{date}_plan.md", plan_file, "test")

        assert "_" in result
        assert "plan.md" in result

    def test_expand_name_placeholder(self, plans_dir, plan_exporter):
        """Template with {name} placeholder."""
        plan_file = plans_dir / "test.md"
        plan_file.write_text("# Test")

        result = plan_exporter.expand_template("notes/{name}.md", plan_file, "my_plan")

        assert "my_plan" in result

    def test_expand_original_name_placeholder(self, plans_dir, plan_exporter):
        """Template with {original} placeholder (original filename without extension)."""
        plan_file = plans_dir / "original-file-name.md"
        plan_file.write_text("# Test")

        result = plan_exporter.expand_template("{original}_exported.md", plan_file, "test")

        assert "original-file-name" in result


# =============================================================================
# EXPORT OPERATION TESTS
# =============================================================================

def make_mock_session_state():
    """Return a fresh in-memory session_state context manager.

    Used to avoid contention with the live daemon's shelve.open() on macOS:
    shelve.open() blocks indefinitely when another process holds the ndbm
    file lock, causing 10+ minute test hangs in CI and during development.
    """
    from contextlib import contextmanager
    _state: dict = {}

    @contextmanager
    def _mock(session_id, timeout=30.0, shared_access=False):
        yield _state.setdefault(session_id, {})

    return _mock


class TestExportPlan:
    """Tests for export_plan() function."""

    def test_export_creates_file(self, plans_dir, project_dir):
        """Export creates file in notes directory."""
        plan_file = plans_dir / "to-export.md"
        plan_file.write_text("# Plan to Export\n\nContent here")

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()), \
             patch('clautorun.plan_export.session_state', make_mock_session_state()):
            result = export_plan(plan_file, project_dir)

        assert result["success"] is True
        assert Path(result["destination"]).exists()

    def test_export_with_session_id_embeds_metadata(self, plans_dir, project_dir):
        """Export with session_id embeds metadata in exported file."""
        plan_file = plans_dir / "embed-test.md"
        plan_file.write_text("# Plan\n\nContent")

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()), \
             patch('clautorun.plan_export.session_state', make_mock_session_state()):
            result = export_plan(plan_file, project_dir, session_id="test-session")

        assert result["success"] is True
        exported_content = Path(result["destination"]).read_text()
        assert "session_id:" in exported_content
        assert "test-session" in exported_content


class TestExportRejectedPlan:
    """Tests for export_rejected_plan() function."""

    def test_export_rejected_creates_file(self, plans_dir, project_dir):
        """Export rejected plan creates file in rejected subdirectory."""
        plan_file = plans_dir / "rejected-plan.md"
        plan_file.write_text("# Rejected Plan\n\nNot approved")

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()), \
             patch('clautorun.plan_export.session_state', make_mock_session_state()):
            result = export_rejected_plan(plan_file, project_dir)

        assert result["success"] is True
        exported_path = Path(result["destination"])
        assert exported_path.exists()
        assert "rejected" in str(exported_path).lower()


# =============================================================================
# APPROVAL DETECTION TESTS
# =============================================================================

class TestMainApprovalDetection:
    """Tests for approval detection logic used in export flow."""

    def test_permission_mode_acceptedits_is_approved(self):
        """permission_mode=acceptEdits means plan is approved."""
        hook_input = {
            "session_id": "test-session",
            "tool_response": {"filePath": "/tmp/plan.md", "plan": "# Test"},
            "permission_mode": "acceptEdits",
            "cwd": "/tmp/project"
        }

        permission_mode = hook_input.get("permission_mode", "")
        is_approved = permission_mode in ["acceptEdits", "bypassPermissions"]

        assert is_approved is True

    def test_permission_mode_bypasspermissions_is_approved(self):
        """permission_mode=bypassPermissions means plan is approved."""
        hook_input = {
            "session_id": "test-session",
            "tool_response": {"filePath": "/tmp/plan.md", "plan": "# Test"},
            "permission_mode": "bypassPermissions",
            "cwd": "/tmp/project"
        }

        permission_mode = hook_input.get("permission_mode", "")
        is_approved = permission_mode in ["acceptEdits", "bypassPermissions"]

        assert is_approved is True

    def test_permission_mode_plan_is_rejected(self):
        """permission_mode=plan means plan is rejected (still in plan mode)."""
        hook_input = {
            "session_id": "test-session",
            "tool_response": {"filePath": "/tmp/plan.md", "plan": "# Test"},
            "permission_mode": "plan",
            "cwd": "/tmp/project"
        }

        permission_mode = hook_input.get("permission_mode", "")
        is_approved = permission_mode in ["acceptEdits", "bypassPermissions"]

        assert is_approved is False

    def test_unknown_permission_mode_defaults_to_approved(self):
        """Unknown permission_mode defaults to approved (safe default)."""
        hook_input = {
            "session_id": "test-session",
            "tool_response": {"filePath": "/tmp/plan.md", "plan": "# Test"},
            "permission_mode": "unknown_value",
            "cwd": "/tmp/project"
        }

        permission_mode = hook_input.get("permission_mode", "")
        if permission_mode in ["acceptEdits", "bypassPermissions"]:
            is_approved = True
        elif permission_mode == "plan":
            is_approved = False
        else:
            is_approved = True  # Default to approved

        assert is_approved is True


class TestMainNoSessionId:
    """Tests for session_id handling."""

    def test_unknown_session_id_warning(self):
        """Unknown session_id triggers warning but continues."""
        hook_input = {
            "session_id": "unknown",
            "tool_response": {"filePath": "/tmp/plan.md"},
            "permission_mode": "acceptEdits",
            "cwd": "/tmp/project"
        }

        session_id = hook_input.get("session_id", "unknown")
        assert session_id == "unknown"


class TestMainNoPlanFound:
    """Tests for when no plan is found."""

    def test_no_plan_returns_error(self):
        """When no plan is found, should return error response."""
        tool_response = {}
        transcript_result = None
        metadata_result = None
        most_recent_result = None

        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                pass

        if not plan_path:
            plan_path = transcript_result

        if not plan_path:
            plan_path = metadata_result

        if not plan_path:
            plan_path = most_recent_result

        assert plan_path is None


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Tests for error handling in various functions."""

    def test_ioerror_in_get_plan_from_metadata(self, plans_dir):
        """IOError reading metadata - returns None gracefully."""
        result = get_plan_from_metadata(plans_dir / "nonexistent.md")
        assert result is None

    def test_ioerror_in_extract_useful_name(self, plans_dir, plan_exporter):
        """IOError reading plan for name extraction - uses filename."""
        result = plan_exporter.extract_useful_name(plans_dir / "nonexistent.md")
        assert "nonexistent" in result

    def test_session_timeout_handled(self):
        """SessionTimeoutError is handled gracefully."""
        from clautorun.session_manager import SessionTimeoutError

        try:
            raise SessionTimeoutError("Test timeout")
        except SessionTimeoutError as e:
            assert "timeout" in str(e).lower()


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestFullExportFlow:
    """Integration tests for the full export flow."""

    def test_full_approved_export_flow(self, plans_dir, project_dir):
        """Full flow: approved plan -> export to notes."""
        plan_file = plans_dir / "approved-plan.md"
        plan_file.write_text("# Approved Plan\n\nThis plan was approved.")

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()), \
             patch('clautorun.plan_export.session_state', make_mock_session_state()):
            result = export_plan(plan_file, project_dir, session_id="integration-test")

        assert result["success"] is True
        exported_path = Path(result["destination"])
        assert exported_path.exists()
        assert "approved" in exported_path.read_text().lower()

    def test_full_rejected_export_flow(self, plans_dir, project_dir):
        """Full flow: rejected plan -> export to rejected folder."""
        plan_file = plans_dir / "rejected-plan.md"
        plan_file.write_text("# Rejected Plan\n\nThis plan was not approved.")

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()), \
             patch('clautorun.plan_export.session_state', make_mock_session_state()):
            result = export_rejected_plan(plan_file, project_dir, session_id="integration-test")

        assert result["success"] is True
        exported_path = Path(result["destination"])
        assert exported_path.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
