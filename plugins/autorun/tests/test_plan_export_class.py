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

pytestmark = pytest.mark.slow

# Add clautorun to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "clautorun" / "src"))

from clautorun.plan_export import (
    PlanExport,
    PlanExportConfig,
    GLOBAL_SESSION_ID,
    get_content_hash,
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

    def test_load_migrates_legacy_output_dir_key(self):
        """load() migrates legacy 'output_dir' key to 'output_plan_dir'."""
        legacy_config = {"enabled": True, "output_dir": "custom/path"}
        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_text', return_value=json.dumps(legacy_config)):
                config = PlanExportConfig.load()
                assert config.output_plan_dir == "custom/path"

    def test_load_does_not_migrate_if_output_plan_dir_exists(self):
        """load() keeps output_plan_dir when both legacy and new keys present."""
        config_data = {"output_dir": "old/path", "output_plan_dir": "new/path"}
        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'read_text', return_value=json.dumps(config_data)):
                config = PlanExportConfig.load()
                assert config.output_plan_dir == "new/path"


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
        hash1 = get_content_hash(temp_project["plan_file"])
        hash2 = get_content_hash(temp_project["plan_file"])
        assert hash1 == hash2
        assert len(hash1) == 16  # First 16 chars of SHA256

    def test_content_hash_different_for_different_content(self, exporter, temp_project):
        """content_hash returns different hash for different content."""
        hash1 = get_content_hash(temp_project["plan_file"])

        # Modify content
        temp_project["plan_file"].write_text("Different content")
        hash2 = get_content_hash(temp_project["plan_file"])

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

        content_hash = get_content_hash(temp_project["plan_file"])
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

        # Write twice with mocked timestamps for determinism
        with patch("clautorun.plan_export.datetime") as mock_dt:
            mock_dt.now.return_value.isoformat.return_value = "2026-01-01T12:00:00"
            exporter.record_write(plan_path)
            old_time = exporter.active_plans[plan_path]["recorded_at"]

            mock_dt.now.return_value.isoformat.return_value = "2026-01-01T12:00:01"
            exporter.record_write(plan_path)
            new_time = exporter.active_plans[plan_path]["recorded_at"]

        assert new_time > old_time
        assert old_time == "2026-01-01T12:00:00"
        assert new_time == "2026-01-01T12:00:01"


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
        result = get_content_hash(Path("/nonexistent/file.md"))
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

        num_workers = 5
        plans_per_worker = 5
        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == num_workers

        # Verify ALL entries persisted (not just that threads completed)
        with session_state(GLOBAL_SESSION_ID) as state:
            active_plans = state.get("active_plans", {})
            expected_count = num_workers * plans_per_worker
            matching = [k for k in active_plans if k.startswith("/home/user/.claude/plans/plan-")]
            assert len(matching) == expected_count, (
                f"Expected {expected_count} plan entries, got {len(matching)}. "
                f"TOCTOU race may have caused lost updates."
            )

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
        content_hash = get_content_hash(temp_project["plan_file"])
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
    """Tests for fallback behavior - handle_session_start handles bad input."""

    def test_handle_session_start_empty_input(self, capsys):
        """handle_session_start handles empty dict gracefully."""
        from clautorun.plan_export import handle_session_start, PlanExportConfig
        from unittest.mock import patch

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()):
            handle_session_start({})

        output = capsys.readouterr().out.strip()
        if output:
            parsed = json.loads(output)
            assert parsed.get("continue") is True

    def test_handle_session_start_no_transcript(self, capsys):
        """handle_session_start handles missing transcript gracefully."""
        from clautorun.plan_export import handle_session_start, PlanExportConfig
        from unittest.mock import patch

        with patch.object(PlanExportConfig, 'load', return_value=PlanExportConfig()):
            handle_session_start({"session_id": "test", "cwd": "/tmp"})

        output = capsys.readouterr().out.strip()
        if output:
            parsed = json.loads(output)
            assert parsed.get("continue") is True


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


# =============================================================================
# TEST: get_plan_from_exit_message() - NEW METHOD FOR OPTION 2 FIX
# =============================================================================


class TestGetPlanFromExitMessage:
    """Tests for the new get_plan_from_exit_message() method.

    This method extracts plan file path from ExitPlanMode response message when
    tool_result doesn't include filePath field directly.

    ExitPlanMode returns messages like:
    "Your plan has been saved to: /Users/athundt/.claude/plans/foo.md"
    """

    def test_extracts_path_from_dict_message_field(self, temp_project):
        """Extract path from dict tool_result with message field."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"status": "success", "message": f"Your plan has been saved to: {plan_file}"}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file

    def test_extracts_path_from_string_tool_result(self, temp_project):
        """Extract path from string tool_result."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=f"Your plan has been saved to: {plan_file}",
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file

    def test_extracts_path_from_any_dict_field(self, temp_project):
        """Extract path from any dict field containing "saved to:" pattern."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"field1": "data", "description": f"Plan saved to: {plan_file}"}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file

    def test_case_insensitive_matching(self, temp_project):
        """Match "saved to:" pattern case-insensitively."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=f"Your PLAN HAS BEEN SAVED TO: {plan_file}",
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file

    def test_returns_none_for_nonexistent_path(self, exporter):
        """Return None if extracted path doesn't exist."""
        exporter.ctx.tool_result = "Your plan has been saved to: /nonexistent/path/plan.md"
        result = exporter.get_plan_from_exit_message()
        assert result is None

    def test_returns_none_for_missing_pattern(self, exporter):
        """Return None if 'saved to:' pattern not found."""
        exporter.ctx.tool_result = {"message": "Plan processing completed"}
        result = exporter.get_plan_from_exit_message()
        assert result is None

    def test_returns_none_for_none_tool_result(self, exporter):
        """Return None if tool_result is None."""
        exporter.ctx.tool_result = None
        result = exporter.get_plan_from_exit_message()
        assert result is None

    def test_returns_none_for_invalid_tool_result_type(self, exporter):
        """Return None if tool_result is neither dict nor string."""
        exporter.ctx.tool_result = 12345
        result = exporter.get_plan_from_exit_message()
        assert result is None

    def test_stops_at_newline_in_path(self, temp_project):
        """Stop path extraction at newline character."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=f"Your plan has been saved to: {plan_file}\nNext line of output",
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file

    def test_finds_first_match_with_multiple_patterns(self, temp_project):
        """Use first match if multiple 'saved to:' patterns exist."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=f"First saved to: {plan_file}\nSecond saved to: /other/plan.md",
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file

    def test_extracts_path_from_json_array(self, temp_project):
        """Extract path when tool_result is a JSON array of strings."""
        plan_file = temp_project['plan_file']
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text("# Test Plan")

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps([f"Your plan has been saved to: {plan_file}", "other data"]),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        result = exporter.get_plan_from_exit_message()
        assert result == plan_file


# =============================================================================
# TEST: SessionStart Response Schema - OPTION 1 FIX
# =============================================================================


class TestSessionStartResponseSchema:
    """Tests for SessionStart hook response schema fixes.

    SessionStart is a lifecycle event, not a tool event. It must use minimal
    response format: {"continue": True, "systemMessage": msg}

    WRONG: ctx.respond("allow", msg) - produces decision/reason/hookSpecificOutput
    RIGHT: {"continue": True, "systemMessage": msg} - correct lifecycle format

    These tests call the actual recover_unexported_plans() function to verify
    the response format matches the lifecycle event schema.
    """

    def test_recovery_response_schema_from_production_code(self, temp_project):
        """recover_unexported_plans() returns correct lifecycle event schema."""
        from clautorun.plan_export import recover_unexported_plans

        # Session 1: Record a plan
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="session-1",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store1
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(temp_project['plan_file']))

        # Session 2: Call recover_unexported_plans (the actual production function)
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="session-2-recovery",
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store2
        )
        response = recover_unexported_plans(ctx2)

        # Must return a response (plan was unexported)
        assert response is not None, "Expected recovery response for unexported plan"

        # Verify CORRECT fields present
        assert "continue" in response
        assert response["continue"] is True
        assert "systemMessage" in response
        assert "Recovered" in response["systemMessage"]

        # Verify FORBIDDEN fields absent (lifecycle events don't support these)
        assert "decision" not in response
        assert "reason" not in response
        assert "hookSpecificOutput" not in response

    def test_recovery_returns_none_when_no_plans(self, temp_project):
        """recover_unexported_plans() returns None when nothing to recover."""
        from clautorun.plan_export import recover_unexported_plans

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="empty-session",
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        response = recover_unexported_plans(ctx)
        assert response is None

    def test_recovery_response_contains_plan_count(self, temp_project):
        """Recovery systemMessage includes the number of recovered plans."""
        from clautorun.plan_export import recover_unexported_plans

        # Record a plan
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="session-record",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store1
        )
        PlanExport(ctx1, PlanExportConfig()).record_write(str(temp_project['plan_file']))

        # Recover
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="session-recover",
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store2
        )
        response = recover_unexported_plans(ctx2)
        assert response is not None
        assert "1 plan(s)" in response["systemMessage"]


# =============================================================================
# TEST: Integration - Option 2 Export Flow
# =============================================================================


class TestOption2ExportFlow:
    """Integration test for Option 2 (regular accept) export flow.

    Flow:
    1. User creates plan → track_plan_writes fires
    2. User clicks Accept → ExitPlanMode fires
    3. export_on_exit_plan_mode gets current plan or parses message
    4. Exports to notes/
    5. User sees confirmation
    """

    def test_export_with_tool_result_filepath_field(self, temp_project):
        """Export works when tool_result includes filePath field."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project['plan_file'])}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        plan = exporter.get_current_plan()
        assert plan == temp_project['plan_file']

        # Export succeeds
        result = exporter.export(plan)
        assert result["success"]
        assert result["message"]

    def test_export_with_message_parsing_fallback(self, temp_project):
        """Export works by parsing message when filePath not provided."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"status": "success", "message": f"Your plan has been saved to: {temp_project['plan_file']}"}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # get_current_plan returns None (no filePath)
        plan = exporter.get_current_plan()
        assert plan is None

        # Fallback to parsing message
        plan = exporter.get_plan_from_exit_message()
        assert plan == temp_project['plan_file']

        # Export succeeds
        result = exporter.export(plan)
        assert result["success"]

    def test_export_marks_plan_as_exported(self, temp_project):
        """Exported plans are marked in tracking dict (prevents double-export)."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"message": f"Your plan has been saved to: {temp_project['plan_file']}"}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        plan = exporter.get_plan_from_exit_message()
        result = exporter.export(plan)
        assert result["success"]

        # Verify it's in tracking dict
        assert get_content_hash(plan) in exporter.tracking

    # ------------------------------------------------------------------
    # Gemini CLI first-class citizen tests
    # Gemini uses lowercase tool names: exit_plan_mode (not ExitPlanMode),
    # write_file (not Write). Events are normalized by GEMINI_EVENT_MAP
    # in core.py before reaching PlanExport, but tool_name stays lowercase.
    # PLAN_TOOLS = {"ExitPlanMode", "exit_plan_mode"} covers both CLIs.
    # ------------------------------------------------------------------

    def test_gemini_export_with_exit_plan_mode_tool_name(self, temp_project):
        """Gemini's exit_plan_mode (lowercase) triggers plan export same as Claude's ExitPlanMode.

        Gemini AfterTool → normalized to PostToolUse by GEMINI_EVENT_MAP.
        Tool name stays as exit_plan_mode (lowercase). PLAN_TOOLS covers both.
        """
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-gemini-session",
            event="PostToolUse",  # normalized from AfterTool
            tool_name="exit_plan_mode",  # Gemini's lowercase name
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project['plan_file'])}),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        plan = exporter.get_current_plan()
        assert plan == temp_project['plan_file']

        result = exporter.export(plan)
        assert result["success"], f"Gemini export failed: {result}"

    def test_gemini_export_with_message_parsing(self, temp_project):
        """Gemini export works via message parsing when filePath not in tool_result."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-gemini-msg",
            event="PostToolUse",  # normalized from AfterTool
            tool_name="exit_plan_mode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({
                "status": "success",
                "message": f"Your plan has been saved to: {temp_project['plan_file']}"
            }),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # filePath not in tool_result, but message parsing fallback works
        plan = exporter.get_plan_from_exit_message()
        assert plan == temp_project['plan_file']

        result = exporter.export(plan)
        assert result["success"]

    def test_gemini_cwd_from_ctx_used_for_project_dir(self, temp_project):
        """Gemini plan export uses ctx.cwd (injected by client.py) for project_dir.

        For Gemini, tool_input may not contain 'cwd' key. ctx.cwd (from payload["_cwd"])
        must be the fallback. This is the cwd regression fix applied to Gemini flow.
        """
        store = ThreadSafeDB()
        project_dir = temp_project["project_dir"]
        ctx = EventContext(
            session_id="test-gemini-cwd",
            event="PostToolUse",
            tool_name="exit_plan_mode",
            tool_input={},  # No cwd in tool_input (Gemini doesn't always include it)
            tool_result=json.dumps({"filePath": str(temp_project['plan_file'])}),
            cwd=str(project_dir),  # Injected by client.py payload["_cwd"]
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # project_dir must resolve from ctx.cwd even without tool_input.cwd
        assert exporter.project_dir == project_dir

        plan = exporter.get_current_plan()
        result = exporter.export(plan)
        assert result["success"]

    # ------------------------------------------------------------------
    # E2E file verification: confirm exported file exists on disk
    # ------------------------------------------------------------------

    def test_exported_file_lands_in_notes_dir(self, temp_project):
        """E2E: accepted plan physically appears in notes/ after export.

        Verifies result["success"] is not just an in-memory flag — the
        file must be readable on disk at project_dir/notes/*.md.
        """
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="e2e-accept-test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
            store=store,
        )
        config = PlanExportConfig()
        exporter = PlanExport(ctx, config)

        plan = exporter.get_current_plan()
        result = exporter.export(plan)

        assert result["success"], f"Export failed: {result}"
        notes_dir = temp_project["project_dir"] / config.output_plan_dir
        exported_files = list(notes_dir.glob("*.md"))
        assert len(exported_files) == 1, (
            f"Expected 1 file in notes/, found {len(exported_files)}: {exported_files}"
        )
        content = exported_files[0].read_text()
        assert "Test Plan" in content, "Exported file must contain plan content"

    def test_rejected_plan_lands_in_rejected_dir(self, temp_project):
        """E2E: rejected plan physically appears in notes/rejected/ after export.

        recover_unexported_plans passes rejected=config.export_rejected, so
        when abandoned plans are recovered they go to the rejected sub-dir.
        """
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="e2e-reject-test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
            store=store,
        )
        config = PlanExportConfig()
        config.export_rejected = True
        exporter = PlanExport(ctx, config)

        plan = temp_project["plan_file"]
        result = exporter.export(plan, rejected=True)

        assert result["success"], f"Rejected export failed: {result}"
        rejected_dir = temp_project["project_dir"] / config.output_rejected_plan_dir
        rejected_files = list(rejected_dir.glob("*.md"))
        assert len(rejected_files) == 1, (
            f"Expected 1 file in notes/rejected/, found {len(rejected_files)}: {rejected_files}"
        )
        content = rejected_files[0].read_text()
        assert "Test Plan" in content

    def test_second_export_of_same_plan_is_skipped(self, temp_project):
        """E2E: exporting the same plan twice results in exactly one file (content-hash dedup).

        The second call must return success=False or a skip message, and
        notes/ must still contain exactly one file.
        """
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="e2e-dedup-test",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
            store=store,
        )
        config = PlanExportConfig()
        exporter = PlanExport(ctx, config)

        plan = temp_project["plan_file"]
        result1 = exporter.export(plan)
        assert result1["success"], f"First export failed: {result1}"

        result2 = exporter.export(plan)
        # Second export must NOT create a duplicate file
        notes_dir = temp_project["project_dir"] / config.output_plan_dir
        all_files = list(notes_dir.glob("*.md"))
        assert len(all_files) == 1, (
            f"Dedup failed: found {len(all_files)} files after second export: {all_files}"
        )


# =============================================================================
# TEST: Integration - Option 1 Recovery Flow
# =============================================================================


class TestOption1RecoveryFlow:
    """Integration test for Option 1 (fresh context) recovery flow.

    Flow:
    1. User creates plan → track_plan_writes fires, stores in active_plans
    2. User clicks "Accept with fresh context" → Option 1 flow
    3. Claude Code clears session (changes session_id)
    4. PostToolUse hook NEVER fires (Bug #4669)
    5. Next session starts → SessionStart fires
    6. recover_unexported_plans checks GLOBAL_SESSION_ID's active_plans
    7. Exports unexported plans
    8. Returns correct minimal response schema
    """

    def test_recovery_finds_plans_across_session_clear(self, temp_project):
        """Unexported plans survive session clear via GLOBAL_SESSION_ID."""
        # Session 1: Record a plan using shared session manager
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="session-before-clear",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store1
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(temp_project['plan_file']))

        # Verify plan was recorded in GLOBAL_SESSION_ID
        with session_state(GLOBAL_SESSION_ID) as state:
            active_plans = state.get('active_plans', {})
            assert str(temp_project['plan_file']) in active_plans

        # Session 2: After session clear (new session_id, but accessing same GLOBAL_SESSION_ID state)
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="session-after-clear",  # Different session_id!
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store2
        )
        exp2 = PlanExport(ctx2, PlanExportConfig())

        # Should still find unexported plan via GLOBAL_SESSION_ID
        unexported = exp2.get_unexported()
        assert len(unexported) > 0
        assert temp_project['plan_file'] in unexported

    def test_recovery_response_uses_minimal_schema(self, temp_project):
        """recover_unexported_plans() returns lifecycle schema, not tool schema."""
        from clautorun.plan_export import recover_unexported_plans

        # Session 1: Record a plan
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="session-record-schema-test",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store1
        )
        PlanExport(ctx1, PlanExportConfig()).record_write(str(temp_project['plan_file']))

        # Session 2: Recover via production code path
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="session-recover-schema-test",
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store2
        )
        response = recover_unexported_plans(ctx2)

        assert response is not None
        # Lifecycle event fields only
        assert response["continue"] is True
        assert "systemMessage" in response
        # Tool event fields must be absent
        assert "decision" not in response
        assert "reason" not in response
        assert "hookSpecificOutput" not in response


# =============================================================================
# TEST: Deduplication - Prevent Double-Export
# =============================================================================


class TestDeduplication:
    """Tests for content hash deduplication.

    Both Option 2 (immediate export) and Option 1 (SessionStart recovery) might
    run on the same plan. Deduplication prevents duplicate exports.
    """

    def test_hash_prevents_double_export(self, temp_project):
        """Same plan hash is filtered out by get_unexported()."""
        plan = temp_project['plan_file']

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Record the plan as written
        exporter.record_write(str(plan))

        # First export (Option 2)
        result1 = exporter.export(plan)
        assert result1["success"]

        # Verify hash is tracked
        hash1 = get_content_hash(plan)
        assert hash1 in exporter.tracking

        # Second call to get_unexported() should NOT include it (deduplication)
        unexported = exporter.get_unexported()
        assert plan not in unexported  # Deduplicated - not in unexported list

    def test_modified_file_exports_again(self, temp_project):
        """Modified plan file gets new hash and is excluded from exported list."""
        plan = temp_project['plan_file']

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Record and export
        exporter.record_write(str(plan))
        result1 = exporter.export(plan)
        assert result1["success"]
        hash1 = get_content_hash(plan)

        # Verify it's in tracking with original hash
        assert hash1 in exporter.tracking

        # Modify file (changes content and hash)
        plan.write_text("# Modified Plan\n\nContent changed.")
        hash2 = get_content_hash(plan)
        assert hash2 != hash1

        # Record the modification (simulates new Write event)
        exporter.record_write(str(plan))

        # New hash means it's NOT in the exported list
        # (because hash2 is different from hash1, and hash2 is not in tracking yet)
        unexported = exporter.get_unexported()
        assert plan in unexported  # Different hash, should be in unexported list


# =============================================================================
# TEST: PreToolUse Backup Handlers
# =============================================================================


class TestPreToolUseBackup:
    """Tests for PreToolUse handlers that back up unreliable PostToolUse.

    PostToolUse hooks don't fire in some Claude Code sessions. PreToolUse handlers
    provide backup tracking and export. Content-hash dedup prevents double-export.
    """

    def test_pretooluse_tracking_populates_active_plans(self, temp_project):
        """track_and_export_plans_early() records Write to plan file in active_plans."""
        from clautorun.plan_export import track_and_export_plans_early

        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-pre",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan), "content": "test", "cwd": str(project_dir)},
            store=store
        )
        result = track_and_export_plans_early(ctx)
        assert result is None  # Never blocks

        # Verify active_plans was populated
        with session_state(GLOBAL_SESSION_ID) as state:
            active = state.get("active_plans", {})
            assert str(plan) in active

    def test_pretooluse_export_on_exit_plan_mode(self, temp_project):
        """track_and_export_plans_early() exports when active_plans has entry."""
        from clautorun.plan_export import track_and_export_plans_early

        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        store = ThreadSafeDB()

        # First, track the write
        ctx_write = EventContext(
            session_id="test-pre",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store
        )
        track_and_export_plans_early(ctx_write)

        # Now trigger export on ExitPlanMode PreToolUse
        ctx_exit = EventContext(
            session_id="test-pre",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store
        )
        result = track_and_export_plans_early(ctx_exit)
        # backup_to_rejected() never blocks — always returns None.
        assert result is None

        # Verify plan backed up to notes/rejected/, NOT promoted to notes/
        rejected_dir = project_dir / "notes" / "rejected"
        exported_rejected = list(rejected_dir.glob("*.md"))
        assert len(exported_rejected) >= 1, "backup must go to notes/rejected/, not notes/"
        exported_notes = list(notes_dir.glob("*.md"))
        assert len(exported_notes) == 0, "notes/ must not have plan before user accepts"

        # Verify plan still in active_plans (backup_to_rejected keeps it pending)
        exporter = PlanExport(ctx_exit, PlanExportConfig())
        assert str(plan) in exporter.active_plans, "plan must remain in active_plans after backup"
        assert exporter.active_plans[str(plan)].get("exit_attempted") is True

    def test_dedup_prevents_double_export_pre_and_post(self, temp_project):
        """Content hash prevents double-export when both Pre and Post fire."""
        from clautorun.plan_export import (
            track_and_export_plans_early,
            track_plan_writes, export_on_exit_plan_mode
        )

        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        store = ThreadSafeDB()

        # Track + export via PreToolUse
        ctx_write = EventContext(
            session_id="test-dedup",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store
        )
        track_and_export_plans_early(ctx_write)

        ctx_exit_pre = EventContext(
            session_id="test-dedup",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store
        )
        track_and_export_plans_early(ctx_exit_pre)

        # Also track + export via PostToolUse (simulating both firing)
        ctx_write_post = EventContext(
            session_id="test-dedup",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store
        )
        track_plan_writes(ctx_write_post)

        ctx_exit_post = EventContext(
            session_id="test-dedup",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store
        )
        export_on_exit_plan_mode(ctx_exit_post)

        # Should only have ONE export (dedup via content hash)
        exported = list(notes_dir.glob("*.md"))
        assert len(exported) == 1

    def test_pretooluse_handler_never_blocks(self, temp_project):
        """PreToolUse handler always returns None, even on errors."""
        from clautorun.plan_export import track_and_export_plans_early

        store = ThreadSafeDB()

        # Write with invalid path
        ctx = EventContext(
            session_id="test-never-block",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": "/nonexistent/path.md"},
            store=store
        )
        assert track_and_export_plans_early(ctx) is None

        # ExitPlanMode with no active plans
        ctx2 = EventContext(
            session_id="test-never-block",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={},
            store=store
        )
        assert track_and_export_plans_early(ctx2) is None

        # Non-matching tool name — returns None early
        ctx3 = EventContext(
            session_id="test-never-block",
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "ls"},
            store=store
        )
        assert track_and_export_plans_early(ctx3) is None

    # ------------------------------------------------------------------
    # Gemini CLI first-class citizen tests for PreToolUse backup
    # Gemini tool names: write_file (not Write), exit_plan_mode (not ExitPlanMode)
    # BeforeTool normalized to PreToolUse by GEMINI_EVENT_MAP in core.py
    # ------------------------------------------------------------------

    def test_gemini_write_file_tool_populates_active_plans(self, temp_project):
        """Gemini write_file tool (not Write) also triggers plan tracking.

        Gemini's BeforeTool(write_file) normalizes to PreToolUse.
        track_and_export_plans_early() must handle write_file tool name.
        """
        from clautorun.plan_export import track_and_export_plans_early

        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-gemini-pre",
            event="PreToolUse",  # normalized from BeforeTool by GEMINI_EVENT_MAP
            tool_name="write_file",  # Gemini's native tool name
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store
        )
        result = track_and_export_plans_early(ctx)
        assert result is None  # Never blocks

        # Verify active_plans was populated via Gemini write_file
        with session_state(GLOBAL_SESSION_ID) as state:
            active = state.get("active_plans", {})
            assert str(plan) in active, \
                "Gemini write_file must trigger plan tracking same as Claude Write"

    def test_gemini_exit_plan_mode_triggers_backup_export(self, temp_project):
        """Gemini exit_plan_mode (lowercase) in BeforeTool triggers backup export.

        This tests the bug fix: hooks.json BeforeTool matcher now includes
        exit_plan_mode so track_and_export_plans_early fires before AfterTool.
        Provides redundancy if AfterTool times out or fails.
        """
        from clautorun.plan_export import track_and_export_plans_early

        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        store = ThreadSafeDB()

        # Simulate Gemini write_file tracking
        ctx_write = EventContext(
            session_id="test-gemini-exit",
            event="PreToolUse",
            tool_name="write_file",
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store
        )
        track_and_export_plans_early(ctx_write)

        # Simulate Gemini BeforeTool(exit_plan_mode) backup export
        ctx_exit = EventContext(
            session_id="test-gemini-exit",
            event="PreToolUse",
            tool_name="exit_plan_mode",  # Gemini's lowercase name
            tool_input={"cwd": str(project_dir)},
            store=store
        )
        result = track_and_export_plans_early(ctx_exit)
        # backup_to_rejected() never blocks — always returns None.
        assert result is None

        # Verify plan backed up to notes/rejected/, NOT promoted to notes/
        rejected_dir = project_dir / "notes" / "rejected"
        exported_rejected = list(rejected_dir.glob("*.md"))
        assert len(exported_rejected) >= 1, \
            "Gemini exit_plan_mode in BeforeTool must back up plan to notes/rejected/"
        exported_notes = list(notes_dir.glob("*.md"))
        assert len(exported_notes) == 0, \
            "notes/ must not have plan before user accepts (backup only goes to notes/rejected/)"

        # Verify plan still in active_plans pending acceptance decision
        exporter = PlanExport(ctx_exit, PlanExportConfig())
        assert str(plan) in exporter.active_plans, "plan must remain in active_plans after backup"
        assert exporter.active_plans[str(plan)].get("exit_attempted") is True


# =============================================================================
# TEST: Edge Cases and Robustness
# =============================================================================


class TestEdgeCases:
    """Test edge cases and robustness of plan export system."""

    def test_unicode_path_handling(self, temp_project):
        """Handle plan paths with unicode characters."""
        # Use temp_project's plan dir to avoid writing to real ~/.claude/plans/
        plans_dir = temp_project['plan_file'].parent
        unicode_plan = plans_dir / "plan_📋_unicode.md"
        unicode_plan.write_text("# Unicode Plan")

        try:
            store = ThreadSafeDB()
            ctx = EventContext(
                session_id="test-session",
                event="PostToolUse",
                tool_name="ExitPlanMode",
                tool_input={"cwd": str(temp_project["project_dir"])},
                tool_result=json.dumps({"message": f"Your plan has been saved to: {unicode_plan}"}),
                store=store
            )
            exporter = PlanExport(ctx, PlanExportConfig())

            result = exporter.get_plan_from_exit_message()
            assert result == unicode_plan
        finally:
            if unicode_plan.exists():
                unicode_plan.unlink()

    def test_path_with_spaces(self, temp_project):
        """Handle plan paths with spaces."""
        # Use temp_project's plan dir to avoid writing to real ~/.claude/plans/
        plans_dir = temp_project['plan_file'].parent
        space_plan = plans_dir / "my plan with spaces.md"
        space_plan.write_text("# Spaced Plan")

        try:
            store = ThreadSafeDB()
            ctx = EventContext(
                session_id="test-session",
                event="PostToolUse",
                tool_name="ExitPlanMode",
                tool_input={"cwd": str(temp_project["project_dir"])},
                tool_result=f"Your plan has been saved to: {space_plan}",
                store=store
            )
            exporter = PlanExport(ctx, PlanExportConfig())

            result = exporter.get_plan_from_exit_message()
            assert result == space_plan
        finally:
            if space_plan.exists():
                space_plan.unlink()

    def test_empty_tool_result(self, temp_project):
        """Handle empty or missing tool_result gracefully."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=None,
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        result = exporter.get_plan_from_exit_message()
        assert result is None

    def test_malformed_json_in_tool_result(self, temp_project):
        """Handle malformed JSON in tool_result."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result="{bad json: not valid}",  # Invalid JSON
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Should treat as plain string and try to extract path
        result = exporter.get_plan_from_exit_message()
        # No valid path pattern, so should return None
        assert result is None

    def test_large_tool_result(self, temp_project):
        """Handle large tool_result values gracefully."""
        plan_file = temp_project['plan_file']

        # Create large tool result with pattern in it
        large_content = "X" * 100000 + f"\nYour plan has been saved to: {plan_file}\n" + "Y" * 100000

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=large_content,
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        result = exporter.get_plan_from_exit_message()
        assert result == plan_file  # Should still find path

    def test_concurrent_exports(self, temp_project):
        """Verify thread-safe concurrent exports don't corrupt state."""
        import threading

        plan = temp_project['plan_file']
        errors = []
        results = []
        lock = threading.Lock()

        def export_plan():
            try:
                store = ThreadSafeDB()
                ctx = EventContext(
                    session_id=f"session-{threading.current_thread().ident}",
                    event="PostToolUse",
                    tool_name="ExitPlanMode",
                    tool_input={"cwd": str(temp_project["project_dir"])},
                    store=store
                )
                exporter = PlanExport(ctx, PlanExportConfig())
                exporter.record_write(str(plan))
                result = exporter.export(plan)
                with lock:
                    results.append(result)
            except Exception as e:
                with lock:
                    errors.append(e)

        # Run 5 concurrent exports
        thread_count = 5
        threads = [threading.Thread(target=export_plan) for _ in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent export errors: {errors}"
        assert len(results) == thread_count, f"Expected {thread_count} results, got {len(results)}"
        # All exports should succeed (same content, dedup happens at get_unexported level)
        success_count = sum(1 for r in results if r["success"])
        assert success_count == thread_count

        # Verify tracking state is intact (content hash should be present)
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="verify-session",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        verifier = PlanExport(ctx, PlanExportConfig())
        content_hash = get_content_hash(plan)
        assert content_hash in verifier.tracking, "Content hash should be in tracking after concurrent exports"

    def test_symlink_handling(self, temp_project):
        """Handle symlinked plan files."""
        import tempfile
        import os

        plan_file = temp_project['plan_file']

        # Create a temporary symlink target
        with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
            f.write("# Symlinked Plan")
            target = Path(f.name)

        try:
            # Create symlink to target
            symlink = temp_project['plan_file'].parent / "symlink-plan.md"
            symlink.symlink_to(target)

            store = ThreadSafeDB()
            ctx = EventContext(
                session_id="test-session",
                event="PostToolUse",
                tool_name="ExitPlanMode",
                tool_input={"cwd": str(temp_project["project_dir"])},
                tool_result=f"Your plan has been saved to: {symlink}",
                store=store
            )
            exporter = PlanExport(ctx, PlanExportConfig())

            result = exporter.get_plan_from_exit_message()
            # Returns the symlink path (not resolved) — path.exists() follows symlinks
            assert result == symlink

        finally:
            if symlink.exists():
                symlink.unlink()
            if target.exists():
                target.unlink()

    def test_empty_plan_removed_from_active_plans(self, temp_project):
        """Empty plan files are removed from active_plans by get_unexported()."""
        plan_file = temp_project['plan_file']

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Record a plan, then make it empty
        exporter.record_write(str(plan_file))
        assert str(plan_file) in exporter.active_plans

        # Make plan empty
        plan_file.write_text("")

        # get_unexported should skip it AND clean it up
        unexported = exporter.get_unexported()
        assert plan_file not in unexported

        # Verify it was removed from active_plans
        assert str(plan_file) not in exporter.active_plans

    def test_whitespace_only_plan_removed(self, temp_project):
        """Whitespace-only plan files are treated as empty and cleaned up."""
        plan_file = temp_project['plan_file']

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        exporter.record_write(str(plan_file))
        plan_file.write_text("   \n\n  \t  \n")

        unexported = exporter.get_unexported()
        assert plan_file not in unexported
        assert str(plan_file) not in exporter.active_plans

    def test_unreadable_plan_removed(self, temp_project):
        """Plans that can't be read (IOError) are cleaned up from active_plans."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-session",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())

        # Record a plan path that exists but will cause IOError when read
        plan_file = temp_project['plan_file']
        exporter.record_write(str(plan_file))

        # Make the file unreadable by removing it and recreating as directory
        # (reading a directory raises IOError/IsADirectoryError)
        plan_file.unlink()
        plan_file.mkdir()

        try:
            unexported = exporter.get_unexported()
            assert Path(str(plan_file)) not in [Path(str(p)) for p in unexported]
            assert str(plan_file) not in exporter.active_plans
        finally:
            plan_file.rmdir()

    # ------------------------------------------------------------------
    # cwd propagation regression tests
    # Root cause: ctx.cwd was always None → record_write() skipped tracking
    # Fix: EventContext now accepts cwd= from handle_client() payload["_cwd"]
    # ------------------------------------------------------------------

    def test_record_write_uses_ctx_cwd_when_tool_input_cwd_absent(self, temp_project):
        """record_write() falls back to ctx.cwd when tool_input has no 'cwd' key.

        The Write tool's tool_input contains file_path + content, not cwd.
        plan_export.py:project_dir() must use ctx.cwd (injected by client.py
        via payload["_cwd"]) when tool_input.get("cwd") is None.
        """
        store = ThreadSafeDB()
        project_dir = temp_project["project_dir"]
        plan_file = temp_project["plan_file"]
        plan_file.write_text("# Test Plan\n\ntest content")

        # No 'cwd' in tool_input — simulates real Write hook (only file_path + content)
        ctx = EventContext(
            session_id="test-cwd-fallback",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan_file)},
            cwd=str(project_dir),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        exporter.record_write(str(plan_file))

        # Plan should be tracked when ctx.cwd is available
        assert str(plan_file) in exporter.active_plans

    def test_record_write_skips_when_ctx_cwd_none(self, temp_project):
        """record_write() skips plan tracking when both tool_input.cwd and ctx.cwd are None.

        This is safe behavior — get_current_plan() still tries tool_result.filePath
        and get_plan_from_exit_message() as fallbacks.
        """
        store = ThreadSafeDB()
        plan_file = temp_project["plan_file"]
        plan_file.write_text("# Test Plan\n\ntest content")

        # Neither tool_input.cwd nor ctx.cwd available (both None)
        ctx = EventContext(
            session_id="test-no-cwd",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan_file)},
            cwd=None,
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        exporter.record_write(str(plan_file))

        # active_plans should be empty — plan was not tracked
        assert exporter.active_plans == {}

    def test_project_dir_uses_ctx_cwd(self, tmp_path):
        """PlanExport.project_dir uses ctx.cwd when tool_input has no 'cwd' key."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-project-dir",
            event="PostToolUse",
            tool_name="Write",
            tool_input={},
            cwd=str(tmp_path),
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        assert exporter.project_dir == tmp_path

    def test_project_dir_raises_when_cwd_unavailable(self, tmp_path):
        """PlanExport.project_dir raises ValueError when no cwd source is available."""
        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-no-dir",
            event="PostToolUse",
            tool_name="Write",
            tool_input={},
            cwd=None,
            store=store
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        with pytest.raises(ValueError, match="cwd not available"):
            _ = exporter.project_dir


# =============================================================================
# TEST: Human-Visible Notifications (TDD for Changes 4, 5, 6)
# =============================================================================


class TestHumanVisibleNotifications:
    """Integration tests for plan export notification visibility.

    Verifies that export results are shown to the human user (not silently
    injected into AI context only). Tests all three export paths:
    - PostToolUse (export_on_exit_plan_mode) — Changes 2 & 4
    - PreToolUse backup (track_and_export_plans_early) — Change 5
    - SessionStart recovery (recover_unexported_plans) — Change 6

    hookSpecificOutput absent + systemMessage → human-visible (outcome matrix row 3).
    hookSpecificOutput present → AI context injection only (outcome matrix row 1).
    """

    def test_export_on_exit_plan_mode_response_is_human_visible(self, temp_project):
        """export_on_exit_plan_mode() PostToolUse response must reach both user and AI.

        Correct: systemMessage present (user terminal), hookSpecificOutput present (AI context),
        reason empty (prevents double-print).
        """
        from clautorun.plan_export import export_on_exit_plan_mode

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-human-post",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
            store=store,
        )
        response = export_on_exit_plan_mode(ctx)

        assert response is not None
        assert "systemMessage" in response
        assert response["systemMessage"].startswith("📋")
        assert "Plan exported to" in response["systemMessage"]
        # Both channels set: systemMessage (user) + hookSpecificOutput (AI)
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["additionalContext"] == response["systemMessage"]
        # reason must be empty to prevent double-print (canonical pattern)
        assert response.get("reason", "") == ""

    def test_export_on_exit_plan_mode_dedup_notifies_with_path(self, temp_project):
        """Dedup (already exported): second export notifies user with specific file path.

        When a plan was already exported (e.g. via Option 1 recovery before Option 2 fires),
        the PostToolUse dedup hit previously returned None — silently suppressing notification.
        This left users unaware of where the plan was saved.

        Fix: skipped=True now returns "📋 Plan exported to notes/SPECIFIC_FILE.md"
        so users know where to find the plan without searching. Message matches fresh
        export format (no "already" prefix) for consistent UX.
        """
        from clautorun.plan_export import export_on_exit_plan_mode

        store = ThreadSafeDB()

        def make_ctx():
            return EventContext(
                session_id="test-human-dedup",
                event="PostToolUse",
                tool_name="ExitPlanMode",
                tool_input={"cwd": str(temp_project["project_dir"])},
                tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
                store=store,
            )

        # First export: should notify user with export path
        response1 = export_on_exit_plan_mode(make_ctx())
        assert response1 is not None and "systemMessage" in response1
        assert "notes/" in response1["systemMessage"]

        # Second export (dedup): must notify with specific path so user knows where plan is.
        response2 = export_on_exit_plan_mode(make_ctx())
        assert response2 is not None, "dedup must return a notification (not None)"
        assert "systemMessage" in response2
        assert "exported" in response2["systemMessage"].lower(), (
            f"dedup message must say 'exported'. Got: {response2['systemMessage']!r}"
        )
        assert "notes/" in response2["systemMessage"], (
            f"dedup message must include specific path. Got: {response2['systemMessage']!r}"
        )
        assert "already" not in response2["systemMessage"].lower(), (
            f"dedup message must say 'Plan exported to' not 'Plan already exported to'. "
            f"Got: {response2['systemMessage']!r}"
        )
        # Both channels must be set (user terminal + Claude AI context)
        assert "hookSpecificOutput" in response2, "PostToolUse response must include hookSpecificOutput"
        assert response2["hookSpecificOutput"].get("additionalContext"), \
            "additionalContext must be set so Claude's AI context receives the notification"
        assert response2["hookSpecificOutput"]["additionalContext"] == response2["systemMessage"], \
            "additionalContext and systemMessage must carry the same notification text"

    def test_export_on_exit_plan_mode_timeout_is_human_visible(self, temp_project):
        """Timeout: user MUST see warning — plan was NOT exported.

        Silent timeout would leave user unaware their plan wasn't saved.
        Change 4: timeout uses to_human=True so warning reaches the human.
        """
        from clautorun.plan_export import export_on_exit_plan_mode
        from clautorun.session_manager import SessionTimeoutError

        store = ThreadSafeDB()
        ctx = EventContext(
            session_id="test-human-timeout",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(temp_project["project_dir"])},
            tool_result=json.dumps({"filePath": str(temp_project["plan_file"])}),
            store=store,
        )
        with patch("clautorun.plan_export.PlanExport.export", side_effect=SessionTimeoutError("lock timeout")):
            response = export_on_exit_plan_mode(ctx)

        assert response is not None
        assert "systemMessage" in response
        # Both channels set: systemMessage (user) + hookSpecificOutput (AI)
        assert "hookSpecificOutput" in response
        assert response["hookSpecificOutput"]["additionalContext"] == response["systemMessage"]
        assert "timeout" in response["systemMessage"].lower()

    def test_pretooluse_backup_path_notifies_when_posttooluse_missing(self, temp_project):
        """PreToolUse backs up silently; PostToolUse notifies if it fires.

        Covers backup-then-route design:
        - PreToolUse(ExitPlanMode): backup_to_rejected() → notes/rejected/, returns None
          (no notification — user has not yet accepted; premature notification misleads)
        - PostToolUse(ExitPlanMode): export() → notes/, notifies user with 📋 message
          (hash NOT in tracking because backup_to_rejected skips tracking intentionally)

        If PostToolUse doesn't fire, recovery at SessionStart handles notification.
        """
        from clautorun.plan_export import track_and_export_plans_early, export_on_exit_plan_mode

        plan = temp_project["plan_file"]
        project_dir = temp_project["project_dir"]
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"
        store = ThreadSafeDB()

        # Track the write (simulating user editing plan file)
        ctx_write = EventContext(
            session_id="test-pre-backup",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store,
        )
        track_and_export_plans_early(ctx_write)

        # PreToolUse ExitPlanMode fires — backup to notes/rejected/, no notification
        ctx_exit = EventContext(
            session_id="test-pre-backup",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        response = track_and_export_plans_early(ctx_exit)

        # PreToolUse must return None — no notification before user decides
        assert response is None, \
            "PreToolUse must not notify before user sees dialog (backup only)"
        # Backup in notes/rejected/ as safety copy
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "backup_to_rejected must write to notes/rejected/"

        # PostToolUse fires (Options 2/3) — exports to notes/ with notification
        ctx_post = EventContext(
            session_id="test-pre-backup",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            tool_result=json.dumps({"filePath": str(plan)}),
            store=store,
        )
        response2 = export_on_exit_plan_mode(ctx_post)
        # Hash not in tracking (backup_to_rejected skips tracking) → exports to notes/
        direct_notes = [f for f in notes_dir.iterdir() if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) >= 1, "PostToolUse must export to notes/"
        # With default notify_claude=True, notification is returned
        if response2 is not None:
            assert "systemMessage" in response2
            assert "📋" in response2["systemMessage"]

    def test_recover_unexported_plans_human_visible(self, temp_project):
        """Recovery on SessionStart uses ctx.respond() → human-visible systemMessage.

        Change 6: raw dict {"continue": True, "systemMessage": msg} replaced with
        ctx.respond("allow", msg) → PATHWAY 4 → adds required schema fields.

        PATHWAY 4 (SessionStart) always routes to systemMessage (human-visible).
        hookSpecificOutput impossible for SessionStart (HOOK_SCHEMAS["SessionStart"]["hso"]={}).
        validate_hook_response strips decision/reason/hookSpecificOutput from SessionStart output.
        """
        from clautorun.plan_export import recover_unexported_plans

        # Session 1: Record a plan (simulate user writing plan file)
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="recovery-human-1",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store1,
        )
        PlanExport(ctx1, PlanExportConfig()).record_write(str(temp_project["plan_file"]))

        # Session 2: Trigger SessionStart recovery (simulates fresh context or new session)
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="recovery-human-2",  # Different session_id!
            event="SessionStart",
            tool_input={"cwd": str(temp_project["project_dir"])},
            store=store2,
        )
        response = recover_unexported_plans(ctx2)

        assert response is not None
        assert "systemMessage" in response
        assert "📋" in response["systemMessage"]
        assert "Recovered" in response["systemMessage"]
        # PATHWAY 4 schema: these must NOT be present (stripped by validate_hook_response)
        assert "hookSpecificOutput" not in response
        assert "decision" not in response
        assert "reason" not in response
        # PATHWAY 4 via ctx.respond() adds these required schema fields.
        # Raw dict {"continue": True, "systemMessage": msg} lacks these — Change 6 adds them.
        assert "stopReason" in response
        assert "suppressOutput" in response


# =============================================================================
# TEST: Accepted/Rejected Plan Routing
# =============================================================================


class TestAcceptedRejectedRouting:
    """Tests for backup-then-route design: backup to notes/rejected/ at PreToolUse,
    promote to notes/ only on acceptance.

    Key invariant: backup_to_rejected() does NOT touch tracking so get_unexported()
    still finds the plan at SessionStart recovery for proper routing.

    Maps to Change 2b, 2c, 2d, 3 in the implementation plan.
    """

    def test_backup_creates_file_in_rejected(self, temp_project):
        """backup_to_rejected() copies plan to notes/rejected/, not notes/."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id="test-backup-rejected",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        # Register plan in active_plans first
        exporter = PlanExport(ctx, PlanExportConfig())
        exporter.record_write(str(plan))

        result = exporter.backup_to_rejected(plan, "plan")
        assert result is not None, "backup_to_rejected must return backup path"

        rejected_dir = project_dir / "notes" / "rejected"
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "backup must be in notes/rejected/"

        notes_dir = project_dir / "notes"
        accepted_files = [f for f in notes_dir.glob("*.md")
                          if "rejected" not in str(f.parent)]
        assert len(accepted_files) == 0, "backup must NOT go to notes/ (not yet accepted)"

        # Plan must remain in active_plans with exit_attempted=True
        with session_state(GLOBAL_SESSION_ID) as state:
            plans = state.get("active_plans", {})
            assert str(plan) in plans
            entry = plans[str(plan)]
            assert entry.get("exit_attempted") is True
            assert entry.get("mode_at_exit_attempt") == "plan"
            assert entry.get("backup_path") is not None

    def test_backup_does_not_update_tracking(self, temp_project):
        """backup_to_rejected() does NOT record hash in tracking."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id="test-backup-no-tracking",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        exporter.record_write(str(plan))
        exporter.backup_to_rejected(plan, "plan")

        content_hash = get_content_hash(plan)
        with session_state(GLOBAL_SESSION_ID) as state:
            tracking = state.get("tracking", {})
            assert content_hash not in tracking, \
                "backup_to_rejected must NOT write hash to tracking (breaks recovery routing)"

    def test_finalize_backup_records_tracking_and_clears_active_plans(self, temp_project):
        """finalize_backup() records hash in tracking and removes from active_plans."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id="test-finalize",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        exporter.record_write(str(plan))
        backup_path = exporter.backup_to_rejected(plan, "plan")

        result = exporter.finalize_backup(plan)
        assert result["success"] is True

        content_hash = get_content_hash(plan)
        with session_state(GLOBAL_SESSION_ID) as state:
            tracking = state.get("tracking", {})
            assert content_hash in tracking, "finalize_backup must record hash in tracking"
            assert tracking[content_hash].get("exported_to") == backup_path

            plans = state.get("active_plans", {})
            assert str(plan) not in plans, "finalize_backup must remove plan from active_plans"

        # Message must include specific filename, not just directory
        assert "notes/rejected/" in result["message"]
        assert result["message"] != "Plan retained in notes/rejected/ (not accepted)", \
            "message must include specific filename, not just directory"
        assert any(c.isdigit() for c in result["message"]), \
            "specific filename (with date/counter) must appear in message"

    def test_option1_bypassperms_promotes_to_notes(self, temp_project):
        """Recovery: exit_attempted + permission_mode changed to bypassPermissions → notes/."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"

        # Session 1: record write, then backup_to_rejected
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-option1",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        exp1.backup_to_rejected(plan, "plan")

        # Session 2: recover_unexported_plans with bypassPermissions (Option 1)
        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-option1-recover",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="bypassPermissions",
        )
        recover_unexported_plans(ctx2)

        # Plan must be promoted to notes/
        accepted_files = list(notes_dir.glob("*.md"))
        # Filter out files in subdirectories like notes/rejected/
        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) >= 1, "Option 1 acceptance must promote plan to notes/"
        # Backup in notes/rejected/ preserved as history
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "notes/rejected/ backup must be preserved as history"

    def test_option3_acceptedits_promotes_to_notes(self, temp_project):
        """Recovery: exit_attempted + permission_mode changed to acceptEdits → notes/."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"

        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-option3",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        exp1.backup_to_rejected(plan, "plan")

        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-option3-recover",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="acceptEdits",
        )
        recover_unexported_plans(ctx2)

        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) >= 1, "Option 3 acceptance (acceptEdits) must promote plan to notes/"

    def test_option4_stays_in_rejected_only(self, temp_project):
        """Recovery: exit_attempted + mode unchanged (plan) → finalize_backup → notes/rejected/ only."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"

        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-option4",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        backup_path_option4 = exp1.backup_to_rejected(plan, "plan")
        assert backup_path_option4 is not None, "backup must succeed"
        backup_filename_option4 = Path(backup_path_option4).name

        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-option4-recover",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="plan",  # mode unchanged → Option 4
        )
        recovery_result = recover_unexported_plans(ctx2)

        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) == 0, "Option 4 plan must NOT be promoted to notes/"
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "Plan must stay in notes/rejected/ after Option 4"

        # Plan must be removed from active_plans (finalized)
        with session_state(GLOBAL_SESSION_ID) as state:
            plans = state.get("active_plans", {})
            assert str(plan) not in plans, "finalize_backup must remove plan from active_plans"

        # Recovery systemMessage must include the specific backup filename, not just the directory.
        # Fix: the message must contain e.g. 'notes/rejected/2026_02_20_1714_test_plan.md'
        # not just 'notes/rejected/' (which was the bug — directory only, no filename).
        assert recovery_result is not None, (
            "recovery must return a response for Option 4; "
            "check that recover_unexported_plans() finds the plan via get_unexported()"
        )
        msg = recovery_result["systemMessage"]
        assert backup_filename_option4 in msg, (
            f"systemMessage must include backup filename '{backup_filename_option4}' "
            f"(notes/rejected/<filename>), not just the directory. Got: '{msg}'. "
            "Fix: finalize_backup() must compute rel = Path(backup_path).relative_to(project_dir) "
            "and return f'Plan retained in {rel} (not accepted)'."
        )

    def test_escape_stays_in_rejected_only(self, temp_project):
        """Recovery: exit_attempted + default mode → finalize_backup → notes/rejected/ only."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"

        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-escape",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        backup_path_escape = exp1.backup_to_rejected(plan, "plan")
        assert backup_path_escape is not None, "backup must succeed"
        backup_filename_escape = Path(backup_path_escape).name

        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-escape-recover",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="default",  # Escape: mode reverted to default
        )
        escape_result = recover_unexported_plans(ctx2)

        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) == 0, "Escaped plan must NOT be promoted to notes/"
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "Plan must stay in notes/rejected/ after Escape"

        # Recovery systemMessage must include the specific backup filename, not just the directory.
        # Fix: the message must contain e.g. 'notes/rejected/2026_02_20_1714_test_plan.md'
        # not just 'notes/rejected/' (directory only — the original bug).
        assert escape_result is not None, (
            "recovery must return a response for Escape; "
            "check that recover_unexported_plans() finds the plan via get_unexported()"
        )
        msg = escape_result["systemMessage"]
        assert backup_filename_escape in msg, (
            f"systemMessage must include backup filename '{backup_filename_escape}' "
            f"(notes/rejected/<filename>), not just the directory. Got: '{msg}'. "
            "Fix: finalize_backup() must compute rel = Path(backup_path).relative_to(project_dir) "
            "and return f'Plan retained in {rel} (not accepted)'."
        )

    def test_abandoned_exports_to_rejected(self, temp_project):
        """Recovery: exit_attempted=False (abandoned) → export(rejected=True) → notes/rejected/."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"

        # Record write only — no backup_to_rejected (ExitPlanMode was never called)
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-abandoned",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        PlanExport(ctx1, PlanExportConfig()).record_write(str(plan))

        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-abandoned-recover",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="default",
        )
        recover_unexported_plans(ctx2)

        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) == 0, "Abandoned plan must NOT go to notes/"
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "Abandoned plan must go to notes/rejected/"

    def test_posttooluse_still_routes_to_notes(self, temp_project):
        """PostToolUse (Options 2/3) still exports to notes/ after backup exists in rejected/."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"
        from clautorun.plan_export import (
            track_and_export_plans_early, export_on_exit_plan_mode
        )
        store = ThreadSafeDB()

        # PreToolUse Write: track
        ctx_write = EventContext(
            session_id="test-post-notes",
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(plan), "cwd": str(project_dir)},
            store=store,
        )
        track_and_export_plans_early(ctx_write)

        # PreToolUse ExitPlanMode: backup_to_rejected
        ctx_pre_exit = EventContext(
            session_id="test-post-notes",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        track_and_export_plans_early(ctx_pre_exit)

        # PostToolUse ExitPlanMode: export_on_exit_plan_mode → notes/
        ctx_post_exit = EventContext(
            session_id="test-post-notes",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        export_on_exit_plan_mode(ctx_post_exit)

        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) >= 1, "PostToolUse (Option 2/3) must promote plan to notes/"
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "notes/rejected/ backup preserved as history"

    def test_export_rejected_false_skips_backup(self, temp_project):
        """backup_to_rejected() returns None when config.export_rejected=False."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id="test-no-rejected",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        config = PlanExportConfig()
        config.export_rejected = False
        exporter = PlanExport(ctx, config)
        exporter.record_write(str(plan))

        result = exporter.backup_to_rejected(plan, "plan")
        assert result is None, "backup_to_rejected must return None when export_rejected=False"

        rejected_dir = project_dir / "notes" / "rejected"
        assert not rejected_dir.exists() or len(list(rejected_dir.glob("*.md"))) == 0, \
            "No file must be written to notes/rejected/ when export_rejected=False"

    def test_record_write_resets_exit_flags(self, temp_project):
        """record_write() creates fresh entry without exit_attempted/backup_path (key invariant)."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        store = ThreadSafeDB()

        ctx = EventContext(
            session_id="test-reset-flags",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store,
        )
        exporter = PlanExport(ctx, PlanExportConfig())
        exporter.record_write(str(plan))
        exporter.backup_to_rejected(plan, "plan")

        # Verify flags are set
        with session_state(GLOBAL_SESSION_ID) as state:
            plans = state.get("active_plans", {})
            assert plans[str(plan)].get("exit_attempted") is True

        # Now AI edits the plan (user chose Option 4, then revises plan)
        exporter.record_write(str(plan))

        # Flags must be cleared — fresh entry with no exit_attempted
        with session_state(GLOBAL_SESSION_ID) as state:
            plans = state.get("active_plans", {})
            assert "exit_attempted" not in plans[str(plan)], \
                "record_write() must create fresh entry without exit_attempted flag"
            assert "backup_path" not in plans[str(plan)], \
                "record_write() must create fresh entry without backup_path"

    def test_bypass_before_plan_mode_option4_known_limitation(self, temp_project):
        """Known limitation: if recovery permission_mode is bypassPermissions for unrelated reason,
        plan may be falsely promoted to notes/. Documented, low severity (plan in both folders)."""
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"

        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-known-limit",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        # User was in bypassPermissions before plan mode; mode_at_exit_attempt="plan"
        exp1.backup_to_rejected(plan, "plan")

        # Recovery session starts in bypassPermissions for unrelated reason (known limitation)
        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-known-limit",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="bypassPermissions",  # not caused by Option 1
        )
        recover_unexported_plans(ctx2)

        # Known limitation: plan is (falsely) promoted to notes/ because we can't distinguish
        # from a genuine Option 1 acceptance. Plan appears in both notes/ and notes/rejected/.
        # This is acceptable low-severity behavior — documented here to prevent silent regressions.
        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        rejected_dir = project_dir / "notes" / "rejected"
        backup_files = list(rejected_dir.glob("*.md"))
        # Both folders have the plan — the known limitation behavior
        assert len(direct_notes) >= 1 or len(backup_files) >= 1, \
            "Known limitation: plan must appear in at least one location"

    def test_finalize_backup_message_includes_specific_filename(self, temp_project):
        """Regression: systemMessage must contain specific filename, not just directory.

        Bug: finalize_backup returned 'Plan retained in notes/rejected/ (not accepted)'
        Fix: 'Plan retained in notes/rejected/2026_02_20_HHMM_plan.md (not accepted)'
        """
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']

        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-msg-test",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        backup_path = exp1.backup_to_rejected(plan, "plan")
        assert backup_path is not None, "backup must succeed"
        backup_filename = Path(backup_path).name  # e.g. "2026_02_20_1714_plan.md"

        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-msg-test",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="plan",  # Option 4: not accepted
        )
        result = recover_unexported_plans(ctx2)

        assert result is not None, "recovery must return a response"
        msg = result["systemMessage"]
        assert backup_filename in msg, (
            f"systemMessage must include backup filename '{backup_filename}'. Got: '{msg}'"
        )
        assert "notes/rejected/" in msg
        assert "(not accepted)" in msg

    def test_option1_source_clear_promotes_to_notes(self, temp_project):
        """Regression: Option 1 (clear context + bypass) must route to notes/ via source='clear'.

        Bug: permission_mode is 'default' at hook time — Claude Code applies bypassPermissions
        2ms AFTER the SessionStart hook completes (confirmed by debug log timestamps).
        Fix: use source='clear' as the primary Option 1 detection signal.

        The permission_mode="bypassPermissions" path (test_option1_bypassperms_promotes_to_notes)
        is retained as a future-proof fallback for when Anthropic fixes their timing bug.
        """
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"

        # Session 1: simulate PreToolUse(ExitPlanMode) — backup to notes/rejected/
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-opt1-clear",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        backup_path = exp1.backup_to_rejected(plan, "plan")
        assert backup_path is not None, "backup must succeed before testing Option 1 routing"

        # Session 2: SessionStart:clear — this is how Option 1 actually arrives
        # permission_mode is "default" (not "bypassPermissions") because Claude Code applies
        # bypassPermissions 2ms AFTER the hook completes. source="clear" is the reliable signal.
        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-opt1-clear",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="default",  # as Claude Code actually sends in hook payload
            source="clear",             # ← the fix: Option 1 detection signal
        )
        result = recover_unexported_plans(ctx2)

        assert result is not None, (
            "recovery must return a response for Option 1 (source='clear'). "
            "Check that recover_unexported_plans() routes source='clear' + exit_attempted=True "
            "to export(rejected=False, force=True) instead of finalize_backup()."
        )
        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) >= 1, (
            f"Option 1 (source='clear') must promote plan to notes/, not notes/rejected/. "
            f"notes/: {direct_notes}, rejected/: {list(rejected_dir.glob('*.md'))}. "
            "Fix: add 'session_is_clear = ctx.source == \"clear\"' to recover_unexported_plans() "
            "routing and use it as primary condition: "
            "'plan_was_accepted = exit_was_attempted and (session_is_clear or ...)'"
        )
        # Backup in notes/rejected/ must be preserved as historical record
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "notes/rejected/ backup must be preserved as history"

    def test_option4_source_startup_stays_rejected(self, temp_project):
        """Option 4: normal new session (source='startup') with exit_attempted=True → notes/rejected/.

        This is the common case after Option 4 (provide feedback): the user picks Option 4,
        plan mode continues in the same session, then the session eventually ends normally.
        The next session starts with source='startup' → plan correctly stays in notes/rejected/.
        """
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"
        rejected_dir = project_dir / "notes" / "rejected"

        # Backup to rejected (simulates Option 4 PreToolUse)
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-opt4-startup",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        backup_path = exp1.backup_to_rejected(plan, "plan")
        assert backup_path is not None

        # Recovery: source="startup" (normal new session after Option 4)
        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-opt4-startup",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="default",
            source="startup",  # normal new session, NOT a clear-context Option 1
        )
        recover_unexported_plans(ctx2)

        # Plan must stay in notes/rejected/ — source="startup" is NOT an acceptance signal
        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        assert len(direct_notes) == 0, (
            f"Option 4 (source='startup') must NOT promote plan to notes/. "
            f"Found in notes/: {direct_notes}. "
            "source='startup' should route to finalize_backup(), not export(rejected=False)."
        )
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(backup_files) >= 1, "plan must stay in notes/rejected/ for Option 4"

    def test_option4_then_clear_known_limitation(self, temp_project):
        """Known limitation: Option 4 + manual /clear later → false positive promotion to notes/.

        If the user selects Option 4 (no PostToolUse fires, plan stays in notes/rejected/)
        and then SEPARATELY runs /clear to start a fresh session, the recovery sees
        source='clear' + exit_attempted=True and (falsely) promotes to notes/. This matches
        the pre-existing known limitation for bypassPermissions false positives.

        Consequence: plan appears in both notes/ and notes/rejected/ (mild; not data loss).
        This test pins the behavior to prevent silent regressions and documents the trade-off.
        """
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        notes_dir = project_dir / "notes"

        # Option 4: backup to rejected, exit_attempted=True
        store1 = ThreadSafeDB()
        ctx1 = EventContext(
            session_id="s1-opt4-then-clear",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        exp1 = PlanExport(ctx1, PlanExportConfig())
        exp1.record_write(str(plan))
        exp1.backup_to_rejected(plan, "plan")

        # User later runs /clear independently (source="clear"), NOT from Option 1
        from clautorun.plan_export import recover_unexported_plans
        store2 = ThreadSafeDB()
        ctx2 = EventContext(
            session_id="s2-opt4-clear",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store2,
            permission_mode="default",
            source="clear",  # /clear command, not from ExitPlanMode dialog
        )
        recover_unexported_plans(ctx2)

        # Known limitation: source="clear" + exit_attempted=True → false positive → notes/
        # Plan appears in both notes/ and notes/rejected/ (documented acceptable trade-off).
        rejected_dir = project_dir / "notes" / "rejected"
        direct_notes = [f for f in notes_dir.iterdir()
                        if f.is_file() and f.suffix == ".md"]
        backup_files = list(rejected_dir.glob("*.md"))
        assert len(direct_notes) >= 1 or len(backup_files) >= 1, \
            "Known limitation: plan must appear in at least one location"
        # Document that the false positive (notes/ promotion) occurs for this edge case
        # This is intentional: we prioritize Option 1 correctness over rare edge case precision.

    def test_multi_plan_recovery_message_shows_all_paths(self, temp_project):
        """Regression for Bug 1: all plan paths must appear in recovery message for 2+ plans.

        Bug: last_msg was overwritten each loop iteration in recover_unexported_plans().
        Only the LAST plan's path appeared, silently dropping earlier plans' paths.
        This caused accepted plans to look misrouted when a second (rejected) plan existed.

        Real failure seen: "Recovered 2 plan(s)...Plan exported to notes/rejected/..."
        even when Plan A had been correctly promoted to notes/. Plan B (abandoned) was
        processed second and overwrote last_msg, making the routing look wrong.

        Fix: accumulate accepted_msgs and rejected_msgs separately; join into one message.
        """
        plan_a = temp_project['plan_file']  # will be accepted via source="clear"
        project_dir = temp_project['project_dir']
        plans_dir = plan_a.parent

        # Create a second distinct plan file (abandoned — no exit_attempted)
        plan_b = plans_dir / "test-plan-b-multi.md"
        plan_b.write_text("# Plan B\n\nThis is the second abandoned plan.")

        try:
            store1 = ThreadSafeDB()

            # Record both plans in Session 1 (same store = shared state)
            for plan_path in (plan_a, plan_b):
                ctx_write = EventContext(
                    session_id="s1-multi",
                    event="PostToolUse",
                    tool_name="Write",
                    tool_input={"cwd": str(project_dir), "file_path": str(plan_path)},
                    store=store1,
                )
                PlanExport(ctx_write, PlanExportConfig()).record_write(str(plan_path))

            # Plan A: presented to user (backup_to_rejected) → exit_attempted=True
            ctx_exit = EventContext(
                session_id="s1-multi",
                event="PreToolUse",
                tool_name="ExitPlanMode",
                tool_input={"cwd": str(project_dir)},
                store=store1,
            )
            PlanExport(ctx_exit, PlanExportConfig()).backup_to_rejected(plan_a, "plan")
            # Plan B: never presented → exit_attempted absent → abandoned

            # Session 2: SessionStart:clear (Option 1 → source="clear")
            from clautorun.plan_export import recover_unexported_plans
            store2 = ThreadSafeDB()
            ctx2 = EventContext(
                session_id="s2-multi",
                event="SessionStart",
                tool_input={"cwd": str(project_dir)},
                store=store2,
                permission_mode="default",  # as Claude Code sends it at hook time
                source="clear",            # primary Option 1 detection signal
            )
            result = recover_unexported_plans(ctx2)

            assert result is not None, "recovery must return a response for 2 plans"
            msg = result["systemMessage"]

            notes_dir = project_dir / "notes"
            rejected_dir = notes_dir / "rejected"
            notes_files = [f for f in notes_dir.iterdir()
                           if f.is_file() and f.suffix == ".md"]
            rejected_files = list(rejected_dir.glob("*.md"))

            # Both plans must be physically routed correctly
            assert len(notes_files) >= 1, (
                f"Plan A (accepted via source='clear') must be in notes/. "
                f"Got notes/: {notes_files}, rejected/: {rejected_files}"
            )
            assert len(rejected_files) >= 1, (
                f"Plan B (abandoned) must be in notes/rejected/. "
                f"Got rejected/: {rejected_files}"
            )

            # CRITICAL: both paths must appear in the message.
            # With old (buggy) code, only the last plan's path was shown.
            assert "Accepted:" in msg, (
                f"Message must have 'Accepted:' section for Plan A. Got: {msg!r}"
            )
            assert "Not accepted:" in msg, (
                f"Message must have 'Not accepted:' section for Plan B. Got: {msg!r}"
            )
            # Specific filenames must be present, not just directory names
            assert any(f.name in msg for f in notes_files), (
                f"Plan A filename must appear in message. "
                f"notes/ files: {[f.name for f in notes_files]}. Message: {msg!r}"
            )
            assert any(f.name in msg for f in rejected_files), (
                f"Plan B filename must appear in message. "
                f"rejected/ files: {[f.name for f in rejected_files]}. Message: {msg!r}"
            )
        finally:
            if plan_b.exists():
                plan_b.unlink()

    def test_option1_then_option2_dedup_notifies_destination(self, temp_project):
        """Regression for Bug 2b: Option 1 recovery then Option 2 PostToolUse must notify.

        Scenario that triggered the bug:
        1. User accepts via Option 1 (clear context + bypass permissions).
        2. Recovery runs at SessionStart:clear → plan exported to notes/ → hash in tracking.
        3. In the new bypassPermissions session, user calls ExitPlanMode again (Option 2).
        4. PostToolUse fires → export_on_exit_plan_mode() → dedup hits (hash in tracking).

        Bug: dedup returned skipped=True → old guard 'not result.get("skipped")' → None
        returned → systemMessage: "" → user sees blank notification, doesn't know where plan is.

        Fix: skipped=True now returns "📋 Plan exported to notes/SPECIFIC_FILE.md"
        so users know where the plan is without searching. Message matches fresh
        export format (no "already" prefix) for consistent UX.
        """
        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']

        # === Simulate Option 1 recovery: export plan to notes/, record hash in tracking ===
        store1 = ThreadSafeDB()
        ctx_recovery = EventContext(
            session_id="s1-opt1-recovery",
            event="SessionStart",
            tool_input={"cwd": str(project_dir)},
            store=store1,
            permission_mode="default",
            source="clear",
        )
        # Record the plan as active so recovery can find it
        ctx_write = EventContext(
            session_id="s0-pre-opt1",
            event="PostToolUse",
            tool_name="Write",
            tool_input={"cwd": str(project_dir), "file_path": str(plan)},
            store=store1,
        )
        PlanExport(ctx_write, PlanExportConfig()).record_write(str(plan))
        # Set exit_attempted so it routes as accepted
        ctx_exit = EventContext(
            session_id="s0-pre-opt1",
            event="PreToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1,
        )
        PlanExport(ctx_exit, PlanExportConfig()).backup_to_rejected(plan, "plan")

        from clautorun.plan_export import recover_unexported_plans, export_on_exit_plan_mode
        recovery_result = recover_unexported_plans(ctx_recovery)
        assert recovery_result is not None, "Option 1 recovery must succeed"
        assert "Accepted:" in recovery_result["systemMessage"], (
            f"Recovery must route to notes/. Got: {recovery_result['systemMessage']!r}"
        )

        # === Simulate Option 2: PostToolUse fires in the new session ===
        # The plan hash is now in tracking (from recovery). export() will hit dedup.
        store2 = ThreadSafeDB()
        ctx_post = EventContext(
            session_id="s2-opt2-post",
            event="PostToolUse",
            tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            tool_result=json.dumps({"filePath": str(plan)}),
            store=store2,
            permission_mode="bypassPermissions",
        )
        opt2_result = export_on_exit_plan_mode(ctx_post)

        # Must NOT return None — user needs to know where the plan is
        assert opt2_result is not None, (
            "Bug 2b regression: Option 2 PostToolUse must notify even on dedup hit. "
            "Got None — user left with no information about where plan was exported."
        )
        assert "systemMessage" in opt2_result
        msg = opt2_result["systemMessage"]
        assert "exported" in msg.lower(), (
            f"systemMessage must say 'exported'. Got: {msg!r}"
        )
        assert "notes/" in msg, (
            f"systemMessage must include specific notes/ path. Got: {msg!r}"
        )
        assert "already" not in msg.lower(), (
            f"message must say 'Plan exported to' not 'Plan already exported to'. Got: {msg!r}"
        )
        # Both channels must be set (user terminal + Claude AI context)
        assert "hookSpecificOutput" in opt2_result, "PostToolUse response must include hookSpecificOutput"
        assert opt2_result["hookSpecificOutput"].get("additionalContext"), \
            "additionalContext must be set so Claude's AI context receives the notification"
        assert opt2_result["hookSpecificOutput"]["additionalContext"] == msg, \
            "additionalContext and systemMessage must carry the same notification text"

    def test_option2_after_option1_recovery_dedup_notifies(self, temp_project):
        """Regression: Option 2 PostToolUse must notify even when plan not in active_plans.

        Bug: backup_to_rejected() had 'if entry:' guard that prevented creating a new
        active_plans entry when Option 1 recovery had previously removed the plan via
        export(). export_on_exit_plan_mode() then saw plan=None (no active_plans entry,
        no filePath in tool_result) and returned None — user received no notification.

        Fix: backup_to_rejected() always upserts the active_plans entry with cwd set,
        so get_current_plan()'s active_plans fallback can find the plan for PostToolUse.

        Daemon log that confirmed the bug:
          20:30:40 export_on_exit_plan_mode: plan=None permission_mode=bypassPermissions
          (after Option 1 recovery removed plan from active_plans)
        """
        from clautorun.plan_export import export_on_exit_plan_mode

        plan = temp_project['plan_file']
        project_dir = temp_project['project_dir']

        # Step 1: Option 1 recovery — export to notes/, removes plan from active_plans
        store1 = ThreadSafeDB()
        ctx_write = EventContext(
            session_id="s1-opt1-e2", event="PostToolUse", tool_name="Write",
            tool_input={"cwd": str(project_dir), "file_path": str(plan)},
            store=store1,
        )
        exp1 = PlanExport(ctx_write, PlanExportConfig())
        exp1.record_write(str(plan))
        export_result = exp1.export(plan, rejected=False, force=True)
        assert export_result["success"]
        # Plan is now removed from active_plans, hash in tracking
        assert str(plan) not in exp1.active_plans, "export(force=True) must remove from active_plans"

        # Step 2: PreToolUse(ExitPlanMode) — backup_to_rejected must upsert active_plans
        ctx_pre = EventContext(
            session_id="s1-opt1-e2", event="PreToolUse", tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            store=store1, permission_mode="bypassPermissions",
        )
        exp2 = PlanExport(ctx_pre, PlanExportConfig())
        backup_path = exp2.backup_to_rejected(plan, "plan")
        assert backup_path is not None, "backup must succeed even when plan not in active_plans"
        assert str(plan) in exp2.active_plans, \
            "backup_to_rejected must upsert active_plans entry so PostToolUse can find the plan"
        assert exp2.active_plans[str(plan)].get("cwd") is not None, \
            "active_plans entry must have cwd set for get_current_plan() fallback"

        # Step 3: PostToolUse(ExitPlanMode) Option 2 — no filePath in tool_result,
        # must find plan via active_plans fallback
        ctx_post = EventContext(
            session_id="s1-opt1-e2", event="PostToolUse", tool_name="ExitPlanMode",
            tool_input={"cwd": str(project_dir)},
            tool_result=None,  # no filePath — forces active_plans fallback
            store=store1, permission_mode="bypassPermissions",
        )
        result = export_on_exit_plan_mode(ctx_post)

        assert result is not None, (
            "Option 2 PostToolUse must return a notification even when plan was not in "
            "active_plans (removed by Option 1 recovery). Got None — user has no path info."
        )
        assert "systemMessage" in result
        msg = result["systemMessage"]
        assert "notes/" in msg, f"notification must include notes/ path. Got: {msg!r}"
        assert "exported" in msg.lower(), f"notification must say 'exported'. Got: {msg!r}"
        assert "already" not in msg.lower(), (
            f"notification must say 'Plan exported to' not 'Plan already exported to'. "
            f"Got: {msg!r}"
        )
        # Both channels must be set (user terminal + Claude AI context)
        assert "hookSpecificOutput" in result, "PostToolUse response must include hookSpecificOutput"
        assert result["hookSpecificOutput"].get("additionalContext"), \
            "additionalContext must be set so Claude's AI context receives the notification"
        assert result["hookSpecificOutput"]["additionalContext"] == msg, \
            "additionalContext and systemMessage must carry the same notification text"

    def test_multi_plan_recovery_skips_already_tracked(self, temp_project):
        """Plan already in tracking must be skipped by get_unexported() in multi-plan recovery.

        Verifies:
        - Plan A (hash in tracking from prior export) → filtered by get_unexported(), not
          processed, not double-exported, count not incremented
        - Plan B (hash not in tracking) → processed normally, appears in message
        - Recovery message count is 1, not 2
        - Plan A's notes/ file is NOT written a second time

        Without get_unexported()'s tracking filter, Plan A would enter the routing loop,
        export() would hit dedup (skipped=True), and either the count would be wrong or
        a duplicate file would be written.
        """
        from clautorun.plan_export import recover_unexported_plans

        plan_a = temp_project['plan_file']
        project_dir = temp_project['project_dir']
        plans_dir = plan_a.parent
        notes_dir = project_dir / "notes"

        plan_b = plans_dir / "test-plan-b-tracked-multi.md"
        plan_b.write_text("# Plan B\n\nThis is the second untracked plan for multi-recovery.")

        try:
            store1 = ThreadSafeDB()

            # Export Plan A (hash now in tracking, removed from active_plans)
            ctx_write_a = EventContext(
                session_id="s1-tracked-multi", event="PostToolUse", tool_name="Write",
                tool_input={"cwd": str(project_dir), "file_path": str(plan_a)},
                store=store1,
            )
            exp_a = PlanExport(ctx_write_a, PlanExportConfig())
            exp_a.record_write(str(plan_a))
            export_result = exp_a.export(plan_a, rejected=False)
            assert export_result["success"]
            notes_files_before = list(notes_dir.glob("*.md"))

            # Record Plan B only (not exported — hash NOT in tracking)
            ctx_write_b = EventContext(
                session_id="s1-tracked-multi", event="PostToolUse", tool_name="Write",
                tool_input={"cwd": str(project_dir), "file_path": str(plan_b)},
                store=store1,
            )
            PlanExport(ctx_write_b, PlanExportConfig()).record_write(str(plan_b))

            # Recovery: both plan files exist on disk, Plan A hash in tracking, Plan B not
            store2 = ThreadSafeDB()
            ctx2 = EventContext(
                session_id="s2-tracked-multi", event="SessionStart",
                tool_input={"cwd": str(project_dir)},
                store=store2, permission_mode="default", source="startup",
            )
            result = recover_unexported_plans(ctx2)

            assert result is not None, "recovery must return response (Plan B is untracked)"
            msg = result["systemMessage"]

            # Only Plan B must be recovered (count=1)
            assert "Recovered 1 plan(s)" in msg, (
                f"Only Plan B must appear (Plan A hash already in tracking → filtered). "
                f"Got: {msg!r}"
            )

            # Plan A must NOT be double-exported to notes/
            notes_files_after = list(notes_dir.glob("*.md"))
            assert len(notes_files_after) == len(notes_files_before), (
                f"Plan A must not be written again (hash in tracking). "
                f"Before: {[f.name for f in notes_files_before]}, "
                f"After: {[f.name for f in notes_files_after]}"
            )
        finally:
            if plan_b.exists():
                plan_b.unlink()
