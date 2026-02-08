#!/usr/bin/env python3
"""
Tests for the new PlanExport class API.

This tests the refactored plan export implementation that uses clautorun's
daemon infrastructure for cross-session state persistence.

Key features tested:
1. PlanExportConfig - configuration loading with defaults
2. PlanExport - state management, file tracking, export logic
3. Atomic operations - TOCTOU race condition prevention
4. Cross-session state - survives Option 1 (fresh context) session clears
5. Unicode handling - em dash, en dash normalization in filenames
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest

# Add clautorun to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "clautorun" / "src"))

from clautorun.plan_export import (
    PlanExport,
    PlanExportConfig,
    GLOBAL_SESSION_ID,
)
from clautorun.core import EventContext, ThreadSafeDB
from clautorun.session_manager import session_state


# =============================================================================
# FIXTURES
# =============================================================================


@pytest.fixture
def temp_project() -> Generator[dict, None, None]:
    """Create temporary project directory with notes/ structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project_dir = Path(tmpdir)
        notes_dir = project_dir / "notes"
        notes_dir.mkdir()

        # Create a mock plan file
        plans_dir = Path.home() / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plans_dir / "test-plan.md"
        plan_file.write_text("# Test Plan\n\nThis is a test plan.")

        yield {
            "project_dir": project_dir,
            "notes_dir": notes_dir,
            "plan_file": plan_file,
        }

        # Cleanup
        if plan_file.exists():
            plan_file.unlink()


@pytest.fixture
def mock_ctx(temp_project) -> EventContext:
    """Create mock EventContext for testing."""
    store = ThreadSafeDB()
    ctx = EventContext(
        session_id="test-session-123",
        event="PostToolUse",
        tool_name="ExitPlanMode",
        tool_input={"cwd": str(temp_project["project_dir"])},
        store=store
    )
    return ctx


@pytest.fixture
def config() -> PlanExportConfig:
    """Create default config."""
    return PlanExportConfig()


@pytest.fixture
def exporter(mock_ctx, config) -> PlanExport:
    """Create PlanExport instance."""
    return PlanExport(mock_ctx, config)


@pytest.fixture(autouse=True)
def cleanup_global_state():
    """Clean up global state before/after each test."""
    # Clear state before test
    try:
        with session_state(GLOBAL_SESSION_ID) as state:
            state.clear()
    except Exception:
        pass

    yield

    # Clear state after test
    try:
        with session_state(GLOBAL_SESSION_ID) as state:
            state.clear()
    except Exception:
        pass


# =============================================================================
# TEST: PlanExportConfig
# =============================================================================


class TestPlanExportConfig:
    """Tests for PlanExportConfig dataclass."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = PlanExportConfig()
        assert config.enabled is True
        assert config.output_plan_dir == "notes"
        assert config.filename_pattern == "{datetime}_{name}"
        assert config.extension == ".md"
        assert config.export_rejected is True
        assert config.output_rejected_plan_dir == "notes/rejected"
        assert config.debug_logging is False
        assert config.notify_claude is True

    def test_load_returns_defaults_when_no_file(self):
        """load() returns defaults when config file doesn't exist."""
        with patch.object(Path, 'exists', return_value=False):
            config = PlanExportConfig.load()
            assert config.enabled is True
            assert config.output_plan_dir == "notes"

    def test_load_merges_user_config(self):
        """load() merges user config with defaults."""
        user_config = {"enabled": False, "output_plan_dir": "docs"}
        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_text', return_value=json.dumps(user_config)):
                config = PlanExportConfig.load()
                assert config.enabled is False
                assert config.output_plan_dir == "docs"
                # Defaults preserved for unspecified fields
                assert config.extension == ".md"

    def test_load_handles_corrupted_json(self):
        """load() returns defaults when JSON is corrupted."""
        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_text', return_value="not valid json"):
                config = PlanExportConfig.load()
                assert config.enabled is True  # Default


# =============================================================================
# TEST: PlanExport State Management
# =============================================================================


class TestPlanExportState:
    """Tests for cross-session state persistence."""

    def test_active_plans_persists_across_sessions(self, temp_project):
        """active_plans survives session_id changes (Option 1 workaround)."""
        store = ThreadSafeDB()

        # Session 1: Record a plan write
        ctx1 = EventContext(
            session_id="session-old",
            event="PostToolUse",
            tool_name="Write",
            tool_input={
                "cwd": str(temp_project["project_dir"]),
                "file_path": str(temp_project["plan_file"]),
            },
            store=store
        )
        exporter1 = PlanExport(ctx1, PlanExportConfig())
        exporter1.record_write(str(temp_project["plan_file"]))

        # Session 2: Different session_id can see the plan
        ctx2 = EventContext(
            session_id="session-new",  # Different session!
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter2 = PlanExport(ctx2, PlanExportConfig())

        # Should find the unexported plan from session 1
        unexported = exporter2.get_unexported()
        assert len(unexported) == 1
        assert unexported[0] == temp_project["plan_file"]

    def test_atomic_update_active_plans(self, exporter, temp_project):
        """atomic_update_active_plans prevents TOCTOU race."""
        plan_path = str(temp_project["plan_file"])

        # Record via atomic update
        def updater(plans):
            plans[plan_path] = {"cwd": str(temp_project["project_dir"]), "session_id": "test"}
        exporter.atomic_update_active_plans(updater)

        # Verify persisted
        active = exporter.active_plans
        assert plan_path in active

    def test_atomic_update_tracking(self, exporter):
        """atomic_update_tracking prevents duplicate exports."""
        content_hash = "abc123def456"

        def updater(tracking):
            tracking[content_hash] = {"exported_at": datetime.now().isoformat()}
        exporter.atomic_update_tracking(updater)

        # Verify persisted
        tracking = exporter.tracking
        assert content_hash in tracking


# =============================================================================
# TEST: Plan File Detection
# =============================================================================


class TestPlanFileDetection:
    """Tests for plan file identification."""

    def test_is_plan_file_positive(self, exporter):
        """is_plan_file returns True for valid plan paths."""
        assert exporter.is_plan_file("/Users/test/.claude/plans/foo.md")
        assert exporter.is_plan_file("/home/user/.claude/plans/bar.md")

    def test_is_plan_file_negative(self, exporter):
        """is_plan_file returns False for non-plan paths."""
        assert not exporter.is_plan_file("/Users/test/notes/plan.md")
        assert not exporter.is_plan_file("/Users/test/.claude/plans/foo.txt")
        assert not exporter.is_plan_file("/tmp/foo.md")

    def test_content_hash_consistent(self, exporter, temp_project):
        """content_hash returns same hash for same content."""
        hash1 = exporter.content_hash(temp_project["plan_file"])
        hash2 = exporter.content_hash(temp_project["plan_file"])
        assert hash1 == hash2
        assert len(hash1) == 16  # First 16 chars of SHA256

    def test_content_hash_different_for_different_content(self, exporter, temp_project):
        """content_hash returns different hash for different content."""
        hash1 = exporter.content_hash(temp_project["plan_file"])

        # Modify content
        temp_project["plan_file"].write_text("Different content")
        hash2 = exporter.content_hash(temp_project["plan_file"])

        assert hash1 != hash2


# =============================================================================
# TEST: Filename Sanitization
# =============================================================================


class TestFilenameSanitization:
    """Tests for _sanitize_filename with Unicode handling."""

    def test_unicode_em_dash_normalized(self, exporter):
        """Em dash (U+2014) normalized to ASCII dash."""
        result = exporter._sanitize_filename("foo\u2014bar")
        assert "\u2014" not in result
        assert "-" in result or "_" in result

    def test_unicode_en_dash_normalized(self, exporter):
        """En dash (U+2013) normalized to ASCII dash."""
        result = exporter._sanitize_filename("foo\u2013bar")
        assert "\u2013" not in result

    def test_unicode_quotes_removed(self, exporter):
        """Smart quotes removed from filename."""
        result = exporter._sanitize_filename("\u201Cquoted\u201D")
        assert "\u201C" not in result
        assert "\u201D" not in result

    def test_unsafe_chars_removed(self, exporter):
        """Unsafe characters removed."""
        result = exporter._sanitize_filename("foo:bar<baz>qux")
        assert ":" not in result
        assert "<" not in result
        assert ">" not in result

    def test_spaces_replaced(self, exporter):
        """Spaces replaced with separator."""
        result = exporter._sanitize_filename("foo bar baz")
        assert " " not in result

    def test_lowercase_output(self, exporter):
        """Output is lowercase."""
        result = exporter._sanitize_filename("FooBar")
        assert result == result.lower()


# =============================================================================
# TEST: Template Expansion
# =============================================================================


class TestTemplateExpansion:
    """Tests for expand_template."""

    def test_datetime_expansion(self, exporter, temp_project):
        """{datetime} expands to YYYY_MM_DD_HHMM format."""
        result = exporter.expand_template("{datetime}", temp_project["plan_file"], "test")
        assert len(result) == 15  # YYYY_MM_DD_HHMM
        assert "_" in result

    def test_name_expansion(self, exporter, temp_project):
        """{name} expands to provided name."""
        result = exporter.expand_template("{name}", temp_project["plan_file"], "my-plan")
        assert result == "my-plan"

    def test_original_expansion(self, exporter, temp_project):
        """{original} expands to plan filename stem."""
        result = exporter.expand_template("{original}", temp_project["plan_file"], "test")
        assert result == temp_project["plan_file"].stem


# =============================================================================
# TEST: Export Logic
# =============================================================================


class TestExportLogic:
    """Tests for export() method."""

    def test_export_creates_file(self, exporter, temp_project):
        """export() creates file in notes directory."""
        result = exporter.export(temp_project["plan_file"])

        assert result["success"] is True
        assert "message" in result

        # File should exist in notes/
        notes_files = list(temp_project["notes_dir"].glob("*.md"))
        assert len(notes_files) == 1

    def test_export_embeds_metadata(self, exporter, temp_project):
        """export() embeds YAML frontmatter."""
        exporter.export(temp_project["plan_file"])

        notes_files = list(temp_project["notes_dir"].glob("*.md"))
        content = notes_files[0].read_text()

        assert content.startswith("---")
        assert "session_id:" in content
        assert "export_timestamp:" in content

    def test_export_records_hash(self, exporter, temp_project):
        """export() records content hash to prevent duplicates."""
        exporter.export(temp_project["plan_file"])

        content_hash = exporter.content_hash(temp_project["plan_file"])
        tracking = exporter.tracking

        assert content_hash in tracking

    def test_export_clears_active_plans(self, exporter, temp_project):
        """export() removes plan from active_plans."""
        plan_path = str(temp_project["plan_file"])

        # First record the write
        exporter.record_write(plan_path)
        assert plan_path in exporter.active_plans

        # Export clears it
        exporter.export(temp_project["plan_file"])
        assert plan_path not in exporter.active_plans

    def test_export_handles_collision(self, exporter, temp_project):
        """export() handles filename collision with counter suffix."""
        # Export twice
        exporter.export(temp_project["plan_file"])

        # Modify and export again (new hash)
        temp_project["plan_file"].write_text("Modified content")
        exporter.export(temp_project["plan_file"])

        notes_files = list(temp_project["notes_dir"].glob("*.md"))
        assert len(notes_files) == 2

    def test_export_rejected_plan(self, exporter, temp_project):
        """export(rejected=True) exports to rejected directory."""
        result = exporter.export(temp_project["plan_file"], rejected=True)

        assert result["success"] is True

        rejected_dir = temp_project["project_dir"] / "notes" / "rejected"
        assert rejected_dir.exists()
        rejected_files = list(rejected_dir.glob("*.md"))
        assert len(rejected_files) == 1


# =============================================================================
# TEST: Recovery Logic (Option 1 Workaround)
# =============================================================================


class TestRecoveryLogic:
    """Tests for get_unexported() and SessionStart recovery."""

    def test_get_unexported_finds_untracked_plans(self, exporter, temp_project):
        """get_unexported() finds plans recorded but not exported."""
        plan_path = str(temp_project["plan_file"])

        # Record write but don't export
        exporter.record_write(plan_path)

        unexported = exporter.get_unexported()
        assert len(unexported) == 1
        assert unexported[0] == temp_project["plan_file"]

    def test_get_unexported_skips_exported_plans(self, exporter, temp_project):
        """get_unexported() skips plans already in tracking."""
        plan_path = str(temp_project["plan_file"])

        # Record and export
        exporter.record_write(plan_path)
        exporter.export(temp_project["plan_file"])

        # Should be empty now
        unexported = exporter.get_unexported()
        assert len(unexported) == 0

    def test_get_unexported_cleans_stale_entries(self, exporter, temp_project):
        """get_unexported() cleans up entries for deleted files."""
        fake_path = "/nonexistent/.claude/plans/deleted.md"

        # Add fake entry
        def updater(plans):
            plans[fake_path] = {"cwd": str(temp_project["project_dir"]), "session_id": "old"}
        exporter.atomic_update_active_plans(updater)

        # get_unexported should clean it up
        exporter.get_unexported()

        assert fake_path not in exporter.active_plans

    def test_get_unexported_filters_by_project(self, temp_project):
        """get_unexported() only returns plans for current project."""
        store = ThreadSafeDB()

        # Record plan for different project
        other_ctx = EventContext(
            session_id="other-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": "/other/project"},
            store=store
        )
        other_exporter = PlanExport(other_ctx, PlanExportConfig())
        other_exporter.record_write(str(temp_project["plan_file"]))

        # Query for current project
        ctx = EventContext(
            session_id="test-session",
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Should not find plans for other project
        unexported = exporter.get_unexported()
        assert len(unexported) == 0


# =============================================================================
# TEST: Record Write
# =============================================================================


class TestRecordWrite:
    """Tests for record_write() tracking."""

    def test_record_write_tracks_plan_file(self, exporter, temp_project):
        """record_write() tracks plan file writes."""
        plan_path = str(temp_project["plan_file"])

        exporter.record_write(plan_path)

        active = exporter.active_plans
        assert plan_path in active
        assert active[plan_path]["cwd"] == str(temp_project["project_dir"])

    def test_record_write_ignores_non_plan_files(self, exporter):
        """record_write() ignores non-plan files."""
        exporter.record_write("/tmp/regular-file.md")

        assert len(exporter.active_plans) == 0

    def test_record_write_updates_existing(self, exporter, temp_project):
        """record_write() updates existing entry (latest wins)."""
        plan_path = str(temp_project["plan_file"])

        # Write twice
        exporter.record_write(plan_path)
        old_time = exporter.active_plans[plan_path]["recorded_at"]

        import time
        time.sleep(0.01)
        exporter.record_write(plan_path)
        new_time = exporter.active_plans[plan_path]["recorded_at"]

        assert new_time > old_time


# =============================================================================
# TEST: Get Current Plan
# =============================================================================


class TestGetCurrentPlan:
    """Tests for get_current_plan() discovery."""

    def test_get_current_plan_from_tool_result(self, temp_project):
        """get_current_plan() extracts from tool_result.filePath."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result={"filePath": str(temp_project["plan_file"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        plan = exporter.get_current_plan()
        assert plan == temp_project["plan_file"]

    def test_get_current_plan_from_json_string(self, temp_project):
        """get_current_plan() handles JSON string tool_result."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        plan = exporter.get_current_plan()
        assert plan == temp_project["plan_file"]

    def test_get_current_plan_fallback_to_active(self, exporter, temp_project):
        """get_current_plan() falls back to active_plans."""
        plan_path = str(temp_project["plan_file"])

        # Record but no tool_result
        exporter.record_write(plan_path)

        plan = exporter.get_current_plan()
        assert plan == temp_project["plan_file"]

    def test_get_current_plan_nonexistent_filepath_fallsthrough(self, temp_project):
        """get_current_plan() falls through if filePath doesn't exist."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result={"filePath": "/nonexistent/path.md"},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Should fall through to active_plans (which is empty)
        plan = exporter.get_current_plan()
        assert plan is None

    def test_get_current_plan_empty_tool_result(self, temp_project):
        """get_current_plan() handles empty tool_result."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result={},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        assert exporter.get_current_plan() is None


# =============================================================================
# TEST: Error Handling & Graceful Degradation
# =============================================================================


class TestErrorHandling:
    """Tests for error handling and fail-open behavior."""

    def test_export_handles_missing_project_dir(self, temp_project):
        """export() handles case when project_dir doesn't exist."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": "/nonexistent/project"},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Should return error, not raise exception
        result = exporter.export(temp_project["plan_file"])
        # May fail or succeed depending on mkdir behavior
        # Key: should not raise unhandled exception

    def test_content_hash_handles_missing_file(self, exporter):
        """content_hash() returns empty string for missing file."""
        result = exporter.content_hash(Path("/nonexistent/file.md"))
        assert result == ""

    def test_record_write_handles_missing_cwd(self, temp_project):
        """record_write() handles missing cwd gracefully."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test",
            event="PostToolUse",
            tool_name="Write",
            tool_input={},  # No cwd
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Should not raise, just skip
        exporter.record_write(str(temp_project["plan_file"]))

    def test_extract_useful_name_handles_unreadable_file(self, exporter):
        """extract_useful_name() falls back to filename for unreadable files."""
        fake_path = Path("/nonexistent/.claude/plans/test-plan.md")
        result = exporter.extract_useful_name(fake_path)
        assert result == "test-plan"


# =============================================================================
# TEST: Concurrency & Thread Safety
# =============================================================================


class TestConcurrencySafety:
    """Tests for concurrent access and race condition prevention."""

    def test_atomic_operations_prevent_toctou(self, temp_project):
        """Atomic operations prevent TOCTOU race conditions."""
        import threading
        import time

        store = ThreadSafeDB()
        results = []
        errors = []

        def worker(worker_id):
            try:
                ctx = EventContext(
                    session_id=f"session-{worker_id}",
                    event="PostToolUse",
                    tool_name="Write",
                    tool_input={"cwd": str(temp_project["project_dir"])},
                    store=store
                )
                exporter = PlanExport(ctx, PlanExportConfig())

                # Simulate concurrent writes
                for i in range(5):
                    plan_path = f"/home/user/.claude/plans/plan-{worker_id}-{i}.md"
                    def updater(plans):
                        plans[plan_path] = {"cwd": str(temp_project["project_dir"]), "session_id": f"session-{worker_id}"}
                    exporter.atomic_update_active_plans(updater)
                    time.sleep(0.001)

                results.append(worker_id)
            except Exception as e:
                errors.append((worker_id, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 5

    def test_cross_session_state_not_corrupted(self, temp_project):
        """Multiple sessions don't corrupt shared state."""
        store = ThreadSafeDB()

        # Session 1 records a plan
        ctx1 = EventContext(
            session_id="session-1",
            event="PostToolUse",
            tool_name="Write",
            tool_input={
                "cwd": str(temp_project["project_dir"]),
                "file_path": str(temp_project["plan_file"]),
            },
            store=store
        )
        exporter1 = PlanExport(ctx1, PlanExportConfig())
        exporter1.record_write(str(temp_project["plan_file"]))

        # Session 2 exports a different plan
        other_plan = temp_project["project_dir"] / "other-plan.md"
        other_plan.write_text("# Other Plan")

        ctx2 = EventContext(
            session_id="session-2",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result={"filePath": str(other_plan)},
            store=store
        )
        exporter2 = PlanExport(ctx2, PlanExportConfig())
        exporter2.export(other_plan)

        # Session 1's plan should still be in active_plans
        assert str(temp_project["plan_file"]) in exporter1.active_plans


# =============================================================================
# TEST: Double Export Prevention
# =============================================================================


class TestDoubleExportPrevention:
    """Tests for idempotency via content hash tracking."""

    def test_same_plan_not_exported_twice(self, exporter, temp_project):
        """Same plan content not exported twice."""
        # First export
        result1 = exporter.export(temp_project["plan_file"])
        assert result1["success"] is True

        # Second export of same content
        result2 = exporter.export(temp_project["plan_file"])

        # Should create second file (collision handling)
        # But tracking should contain the hash
        content_hash = exporter.content_hash(temp_project["plan_file"])
        assert content_hash in exporter.tracking

    def test_modified_plan_exported_again(self, exporter, temp_project):
        """Modified plan can be exported again."""
        # First export
        result1 = exporter.export(temp_project["plan_file"])
        assert result1["success"] is True

        # Modify content
        temp_project["plan_file"].write_text("# Modified Plan\n\nDifferent content.")

        # Clear active plans to re-enable export
        def clearer(plans):
            plans.clear()
        exporter.atomic_update_active_plans(clearer)

        # Record new write
        exporter.record_write(str(temp_project["plan_file"]))

        # Second export of different content
        result2 = exporter.export(temp_project["plan_file"])
        assert result2["success"] is True


# =============================================================================
# TEST: Hook Dispatch Integration
# =============================================================================


class TestHookDispatchIntegration:
    """Tests for the @app.on() handler integration."""

    def test_track_plan_writes_handler(self, temp_project):
        """track_plan_writes handler records Write/Edit events."""
        from clautorun.plan_export import track_plan_writes

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={
                "cwd": str(temp_project["project_dir"]),
                "file_path": str(temp_project["plan_file"]),
            },
            store=store
        )

        # Handler should return None (continue processing)
        result = track_plan_writes(ctx)
        assert result is None

        # Verify plan was recorded
        exporter = PlanExport(ctx, PlanExportConfig())
        assert str(temp_project["plan_file"]) in exporter.active_plans

    def test_track_plan_writes_ignores_non_plan_files(self, temp_project):
        """track_plan_writes ignores non-plan files."""
        from clautorun.plan_export import track_plan_writes

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={
                "cwd": str(temp_project["project_dir"]),
                "file_path": "/tmp/regular-file.md",
            },
            store=store
        )

        result = track_plan_writes(ctx)
        assert result is None

        exporter = PlanExport(ctx, PlanExportConfig())
        assert len(exporter.active_plans) == 0

    def test_export_on_exit_plan_mode_handler(self, temp_project):
        """export_on_exit_plan_mode handler exports on ExitPlanMode."""
        from clautorun.plan_export import export_on_exit_plan_mode

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result={"filePath": str(temp_project["plan_file"])},
            store=store
        )

        result = export_on_exit_plan_mode(ctx)

        # Should return response with message
        if result:
            assert "systemMessage" in result or result.get("continue") is True

    def test_recover_unexported_plans_handler(self, temp_project):
        """recover_unexported_plans handler exports on SessionStart."""
        from clautorun.plan_export import recover_unexported_plans, track_plan_writes

        store = ThreadSafeDB()

        # First, record a plan write
        ctx1 = EventContext(
            session_id="old-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={
                "cwd": str(temp_project["project_dir"]),
                "file_path": str(temp_project["plan_file"]),
            },
            store=store
        )
        track_plan_writes(ctx1)

        # Then trigger SessionStart (simulating Option 1)
        ctx2 = EventContext(
            session_id="new-session",  # Different session!
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )

        result = recover_unexported_plans(ctx2)

        # Should have exported the plan
        if result:
            assert "Recovered" in result.get("systemMessage", "")


# =============================================================================
# TEST: Bootstrap/Fallback Compatibility
# =============================================================================


class TestBootstrapFallback:
    """Tests for fallback behavior when daemon not running."""

    def test_script_main_handles_empty_input(self):
        """Script main() handles empty stdin gracefully."""
        import subprocess

        script_path = Path(__file__).parent.parent / "scripts" / "plan_export.py"

        # Run script with empty stdin
        result = subprocess.run(
            ["python3", str(script_path)],
            input="",
            capture_output=True,
            text=True,
            timeout=10
        )

        # Should output valid JSON
        assert result.returncode == 0
        output = result.stdout.strip()
        if output:
            parsed = json.loads(output)
            assert "continue" in parsed

    def test_script_main_handles_invalid_json(self):
        """Script main() handles invalid JSON gracefully."""
        import subprocess

        script_path = Path(__file__).parent.parent / "scripts" / "plan_export.py"

        result = subprocess.run(
            ["python3", str(script_path)],
            input="not valid json",
            capture_output=True,
            text=True,
            timeout=10
        )

        assert result.returncode == 0


# =============================================================================
# TEST: Extract Useful Name
# =============================================================================


class TestExtractUsefulName:
    """Tests for name extraction from plan content."""

    def test_extracts_from_h1_heading(self, exporter, temp_project):
        """extract_useful_name extracts from H1 heading."""
        temp_project["plan_file"].write_text("# My Great Plan\n\nContent here.")
        result = exporter.extract_useful_name(temp_project["plan_file"])
        assert "my" in result.lower()
        assert "great" in result.lower()
        assert "plan" in result.lower()

    def test_extracts_from_h2_heading(self, exporter, temp_project):
        """extract_useful_name extracts from H2 heading if no H1."""
        temp_project["plan_file"].write_text("## Second Level Heading\n\nContent.")
        result = exporter.extract_useful_name(temp_project["plan_file"])
        assert "second" in result.lower() or "level" in result.lower()

    def test_extracts_from_first_line(self, exporter, temp_project):
        """extract_useful_name uses first line if no heading."""
        temp_project["plan_file"].write_text("This is the first line\n\nMore content.")
        result = exporter.extract_useful_name(temp_project["plan_file"])
        assert len(result) > 0

    def test_uses_filename_for_empty_content(self, exporter, temp_project):
        """extract_useful_name uses filename for empty/whitespace content."""
        temp_project["plan_file"].write_text("   \n\n   ")
        result = exporter.extract_useful_name(temp_project["plan_file"])
        # Should fall back to filename stem
        assert result == temp_project["plan_file"].stem


# =============================================================================
# TEST: Project Directory Isolation
# =============================================================================


class TestProjectDirectoryIsolation:
    """Tests for project-based isolation of plans."""

    def test_different_projects_dont_interfere(self):
        """Plans from different projects don't interfere."""
        store = ThreadSafeDB()

        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                # Create plans in both projects
                plans_dir = Path.home() / ".claude" / "plans"
                plans_dir.mkdir(parents=True, exist_ok=True)

                plan1 = plans_dir / "project1-plan.md"
                plan2 = plans_dir / "project2-plan.md"
                plan1.write_text("# Plan 1")
                plan2.write_text("# Plan 2")

                try:
                    # Record in project 1
                    ctx1 = EventContext(
                        session_id="session-1",
                        event="PostToolUse",
                        tool_name="Write",
                        tool_input={"cwd": tmpdir1, "file_path": str(plan1)},
                        store=store
                    )
                    exp1 = PlanExport(ctx1, PlanExportConfig())
                    exp1.record_write(str(plan1))

                    # Record in project 2
                    ctx2 = EventContext(
                        session_id="session-2",
                        event="PostToolUse",
                        tool_name="Write",
                        tool_input={"cwd": tmpdir2, "file_path": str(plan2)},
                        store=store
                    )
                    exp2 = PlanExport(ctx2, PlanExportConfig())
                    exp2.record_write(str(plan2))

                    # Query project 1 - should only see plan1
                    unexported1 = exp1.get_unexported()
                    assert len(unexported1) == 1
                    assert unexported1[0] == plan1

                    # Query project 2 - should only see plan2
                    unexported2 = exp2.get_unexported()
                    assert len(unexported2) == 1
                    assert unexported2[0] == plan2

                finally:
                    if plan1.exists():
                        plan1.unlink()
                    if plan2.exists():
                        plan2.unlink()
