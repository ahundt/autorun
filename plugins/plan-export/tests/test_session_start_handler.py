#!/usr/bin/env python3
"""
Tests for SessionStart handler - workaround for Claude Code fresh context bug.

CLAUDE CODE BUG WORKAROUND:
    Bug: When a user accepts a plan with Option 1 (fresh context), the PostToolUse
    hook for ExitPlanMode doesn't fire, leaving the plan unexported.

    Workaround: The SessionStart handler catches unexported plans on the next
    session start by checking transcripts for plan activity without corresponding
    export tracking entries.

    Evidence: Confirmed via tmux test - Option 1 (fresh context) does not export,
    Option 2 (regular accept) exports correctly.
"""

import contextlib
import json
import sys
import tempfile
from io import StringIO
from pathlib import Path
from typing import Generator
from unittest.mock import patch, MagicMock

import pytest

# Add scripts to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from plan_export import (
    detect_hook_type,
    handle_session_start,
    main,
    get_content_hash,
    export_plan,
)


# =============================================================================
# FIXTURES - DRY helpers for common test setup
# =============================================================================


@pytest.fixture
def temp_project() -> Generator[dict, None, None]:
    """Fixture that creates temp directory with project structure.

    Yields dict with 'tmpdir', 'project_dir', ready for test use.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        project_dir = tmpdir / "project"
        project_dir.mkdir()
        yield {"tmpdir": tmpdir, "project_dir": project_dir}


@pytest.fixture
def temp_plan_file(temp_project: dict) -> Path:
    """Fixture that creates a test plan file in temp directory."""
    plan_file = temp_project["tmpdir"] / "test_plan.md"
    plan_file.write_text("# Test Plan\n\nTest content for export.")
    return plan_file


def make_hook_input(
    session_id: str = "test123",
    cwd: str = "/tmp",
    transcript_path: str = "/fake/transcript.jsonl",
    tool_name: str = None,
) -> dict:
    """Helper to create hook input dict with sensible defaults."""
    hook = {"session_id": session_id, "cwd": cwd}
    if transcript_path:
        hook["transcript_path"] = transcript_path
    if tool_name:
        hook["tool_name"] = tool_name
    return hook


@contextlib.contextmanager
def mock_session_start_context(
    enabled: bool = True,
    plan_path: Path = None,
    tracking: dict = None,
    config: dict = None,
):
    """Context manager that sets up common mocks for SessionStart handler tests.

    Args:
        enabled: Whether plugin is enabled
        plan_path: Plan file path to return from transcript parsing (None = not found)
        tracking: Tracking dict (default: empty = not yet exported)
        config: Config dict (default: minimal config with notify_claude=True)
    """
    if tracking is None:
        tracking = {}
    if config is None:
        config = {"enabled": True, "notify_claude": True, "output_plan_dir": "notes"}

    with patch("plan_export.is_enabled", return_value=enabled):
        with patch("plan_export.get_plan_from_transcript", return_value=plan_path):
            with patch("plan_export.load_tracking", return_value=tracking):
                with patch("plan_export.load_config", return_value=config):
                    yield


def parse_last_json_output(output: str) -> dict:
    """Parse the last JSON object from output (handles multiple prints)."""
    lines = [line.strip() for line in output.strip().split('\n') if line.strip()]
    if not lines:
        return {}
    # Return the last JSON object (some tests print multiple)
    return json.loads(lines[-1])


# =============================================================================
# HOOK TYPE DETECTION TESTS
# =============================================================================


class TestDetectHookType:
    """Tests for hook type detection dispatch logic."""

    def test_posttooluse_detected_by_tool_name(self):
        """PostToolUse hooks include tool_name in input."""
        hook_input = make_hook_input(tool_name="ExitPlanMode")
        assert detect_hook_type(hook_input) == "PostToolUse"

    def test_sessionstart_detected_by_missing_tool_name(self):
        """SessionStart hooks don't have tool_name."""
        hook_input = make_hook_input()  # No tool_name
        assert detect_hook_type(hook_input) == "SessionStart"

    def test_empty_input_defaults_to_sessionstart(self):
        """Empty input defaults to SessionStart (safe fallback)."""
        assert detect_hook_type({}) == "SessionStart"

    def test_tool_name_takes_priority(self):
        """Even with other fields, tool_name indicates PostToolUse."""
        hook_input = make_hook_input(tool_name="Write")
        assert detect_hook_type(hook_input) == "PostToolUse"


# =============================================================================
# SESSION START HANDLER TESTS
# =============================================================================


class TestHandleSessionStart:
    """Tests for SessionStart handler logic."""

    def test_returns_continue_when_disabled(self, capsys):
        """When plugin is disabled, returns continue=True with suppressOutput."""
        with mock_session_start_context(enabled=False):
            handle_session_start(make_hook_input())

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True
        assert result.get("suppressOutput") is True

    def test_returns_continue_when_no_session_id(self, capsys):
        """Without session_id, cannot use lock - returns continue."""
        with mock_session_start_context():
            handle_session_start({"cwd": "/tmp", "transcript_path": "/fake"})

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_returns_continue_when_no_transcript(self, capsys):
        """Without transcript_path, cannot find plan - returns continue."""
        with mock_session_start_context():
            handle_session_start({"session_id": "test", "cwd": "/tmp"})

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_returns_continue_when_no_plan_found(self, capsys):
        """When transcript doesn't reference a plan, returns continue."""
        with mock_session_start_context(plan_path=None):
            handle_session_start(make_hook_input())

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_skips_already_exported_plan(self, temp_project: dict, temp_plan_file: Path, capsys):
        """Plans with matching content hash in tracking are skipped."""
        content_hash = get_content_hash(temp_plan_file)
        tracking = {content_hash: {"exported_at": "2024-01-01", "destination": "/some/path"}}

        with mock_session_start_context(plan_path=temp_plan_file, tracking=tracking):
            handle_session_start(make_hook_input(cwd=str(temp_project["tmpdir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True
        # No systemMessage means no export happened
        assert "Recovered" not in result.get("systemMessage", "")

    def test_skips_empty_plan(self, temp_project: dict, capsys):
        """Empty plan files are not exported."""
        plan_file = temp_project["tmpdir"] / "empty.md"
        plan_file.write_text("")  # Empty file

        with mock_session_start_context(plan_path=plan_file):
            handle_session_start(make_hook_input(cwd=str(temp_project["tmpdir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_exports_unexported_plan(self, temp_project: dict, temp_plan_file: Path, capsys):
        """Unexported plans are exported to notes/ directory."""
        with mock_session_start_context(plan_path=temp_plan_file):
            handle_session_start(make_hook_input(cwd=str(temp_project["project_dir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

        # Check file was exported
        notes_dir = temp_project["project_dir"] / "notes"
        assert notes_dir.exists(), "Notes directory should be created"
        exported = list(notes_dir.glob("*.md"))
        assert len(exported) >= 1, "Plan should be exported"

    def test_notifies_claude_on_recovery(self, temp_project: dict, capsys):
        """When a plan is recovered, systemMessage is set."""
        plan_file = temp_project["tmpdir"] / "recovered.md"
        plan_file.write_text("# Recovered Plan\n\nContent.")

        with mock_session_start_context(plan_path=plan_file):
            handle_session_start(make_hook_input(cwd=str(temp_project["project_dir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert "systemMessage" in result
        assert "Recovered" in result["systemMessage"]


# =============================================================================
# MAIN DISPATCH TESTS
# =============================================================================


class TestMainDispatch:
    """Tests for main() dispatch to correct handler."""

    def test_main_dispatches_sessionstart(self, capsys):
        """main() routes SessionStart input to handle_session_start."""
        hook_input = json.dumps(make_hook_input())

        with patch("plan_export.is_enabled", return_value=True):
            with patch("sys.stdin", StringIO(hook_input)):
                main()

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_main_dispatches_posttooluse(self, capsys):
        """main() routes PostToolUse input to existing handler."""
        hook_input = json.dumps(make_hook_input(tool_name="ExitPlanMode"))

        with patch("plan_export.is_enabled", return_value=False):
            with patch("sys.stdin", StringIO(hook_input)):
                main()

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True
        assert result.get("suppressOutput") is True

    def test_main_handles_json_decode_error(self, capsys):
        """main() handles invalid JSON input gracefully."""
        with patch("sys.stdin", StringIO("not valid json")):
            main()

        # Should still output valid JSON
        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True


# =============================================================================
# DOUBLE-EXPORT PREVENTION TESTS
# =============================================================================


class TestDoubleExportPrevention:
    """Tests for content-hash based double-export prevention."""

    def test_content_hash_is_consistent(self, temp_project: dict):
        """Same content produces same hash."""
        file1 = temp_project["tmpdir"] / "file1.md"
        file2 = temp_project["tmpdir"] / "file2.md"

        content = "# Same Content\n\nIdentical."
        file1.write_text(content)
        file2.write_text(content)

        hash1 = get_content_hash(file1)
        hash2 = get_content_hash(file2)

        assert hash1 == hash2
        assert len(hash1) == 16  # SHA256 truncated to 16 chars

    def test_content_hash_differs_for_different_content(self, temp_project: dict):
        """Different content produces different hash."""
        file1 = temp_project["tmpdir"] / "file1.md"
        file2 = temp_project["tmpdir"] / "file2.md"

        file1.write_text("# Plan A")
        file2.write_text("# Plan B")

        assert get_content_hash(file1) != get_content_hash(file2)

    def test_content_hash_empty_for_missing_file(self):
        """Missing file returns empty string (safe fallback)."""
        assert get_content_hash(Path("/nonexistent/file.md")) == ""


# =============================================================================
# EXPORT PLAN FUNCTION TESTS
# =============================================================================


class TestExportPlanFunction:
    """Tests for the export_plan function."""

    def test_export_creates_notes_directory(self, temp_project: dict, temp_plan_file: Path):
        """export_plan creates notes/ if it doesn't exist."""
        result = export_plan(temp_plan_file, temp_project["project_dir"])

        assert result["success"] is True
        assert (temp_project["project_dir"] / "notes").exists()

    def test_export_handles_filename_collision(self, temp_project: dict, temp_plan_file: Path):
        """export_plan adds suffix for duplicate filenames."""
        # Export twice
        result1 = export_plan(temp_plan_file, temp_project["project_dir"])
        result2 = export_plan(temp_plan_file, temp_project["project_dir"])

        assert result1["success"] is True
        assert result2["success"] is True
        assert result1["destination"] != result2["destination"]


# =============================================================================
# ATOMIC SAVE TRACKING TESTS
# =============================================================================


class TestAtomicSaveTracking:
    """Tests for atomic save_tracking function."""

    def test_save_tracking_atomic(self, temp_project: dict):
        """save_tracking uses atomic write pattern."""
        from plan_export import save_tracking, load_tracking
        import plan_export

        original_tracking = plan_export.TRACKING_FILE
        plan_export.TRACKING_FILE = temp_project["tmpdir"] / "test-tracking.json"

        try:
            test_data = {"hash123": {"exported_at": "2024-01-01", "destination": "/tmp"}}
            save_tracking(test_data)

            assert plan_export.TRACKING_FILE.exists()
            loaded = load_tracking()
            assert loaded == test_data

            # Verify no temp files left behind
            temp_files = list(temp_project["tmpdir"].glob(".plan-export-tracking-*.tmp"))
            assert len(temp_files) == 0, "Temp files should be cleaned up"
        finally:
            plan_export.TRACKING_FILE = original_tracking

    def test_save_tracking_creates_parent_dir(self, temp_project: dict):
        """save_tracking creates parent directory if needed."""
        from plan_export import save_tracking
        import plan_export

        original_tracking = plan_export.TRACKING_FILE
        plan_export.TRACKING_FILE = temp_project["tmpdir"] / "subdir" / "tracking.json"

        try:
            save_tracking({"test": "data"})
            assert plan_export.TRACKING_FILE.exists()
        finally:
            plan_export.TRACKING_FILE = original_tracking


# =============================================================================
# THREAD SAFETY TESTS
# =============================================================================


class TestThreadSafety:
    """Tests for thread safety in SessionStart handler."""

    def test_handler_uses_session_lock(self):
        """Verify handle_session_start uses SessionLock."""
        import inspect

        source = inspect.getsource(handle_session_start)
        assert "SessionLock" in source, "handle_session_start should use SessionLock"
        assert "lock_context" in source, "Should create lock_context"

    def test_handler_catches_lock_timeout(self):
        """Verify handle_session_start handles SessionTimeoutError."""
        import inspect

        source = inspect.getsource(handle_session_start)
        assert "SessionTimeoutError" in source, "Should catch SessionTimeoutError"


# =============================================================================
# PRE-MORTEM EDGE CASE TESTS
# =============================================================================


class TestPreMortemEdgeCases:
    """Pre-mortem analysis: testing failure modes and edge cases.

    These tests verify graceful degradation when things go wrong.
    All should fail open with continue=True.
    """

    def test_export_plan_failure_handled(
        self, temp_project: dict, temp_plan_file: Path, capsys
    ):
        """When export_plan raises exception, handler fails gracefully."""
        with mock_session_start_context(plan_path=temp_plan_file):
            with patch("plan_export.export_plan", side_effect=PermissionError("Access denied")):
                handle_session_start(make_hook_input(cwd=str(temp_project["tmpdir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_plan_deleted_between_exists_and_read(self, temp_project: dict, capsys):
        """TOCTOU: Plan deleted between exists() check and read_text()."""
        mock_plan = MagicMock()
        mock_plan.exists.return_value = True
        mock_plan.read_text.side_effect = FileNotFoundError("File deleted")

        with mock_session_start_context(plan_path=mock_plan):
            with patch("plan_export.get_content_hash", return_value="somehash"):
                handle_session_start(make_hook_input(cwd=str(temp_project["tmpdir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_unicode_decode_error_handled(self, temp_project: dict, capsys):
        """Plan with invalid encoding doesn't crash handler."""
        plan_file = temp_project["tmpdir"] / "binary.md"
        plan_file.write_bytes(b"\xff\xfe invalid utf-8")

        with mock_session_start_context(plan_path=plan_file):
            with patch("plan_export.get_content_hash", return_value="somehash"):
                handle_session_start(make_hook_input(cwd=str(temp_project["tmpdir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

    def test_whitespace_only_plan_not_exported(self, temp_project: dict, capsys):
        """Plan with only whitespace is treated as empty."""
        plan_file = temp_project["tmpdir"] / "whitespace.md"
        plan_file.write_text("   \n\n   \t\t\n   ")

        with mock_session_start_context(plan_path=plan_file):
            handle_session_start(make_hook_input(cwd=str(temp_project["project_dir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True

        # Should NOT have exported (empty plan)
        notes_dir = temp_project["project_dir"] / "notes"
        if notes_dir.exists():
            assert len(list(notes_dir.glob("*.md"))) == 0

    def test_concurrent_export_same_plan(
        self, temp_project: dict, temp_plan_file: Path, capsys
    ):
        """Two handlers trying to export same plan - only one should succeed."""
        content_hash = get_content_hash(temp_plan_file)

        # First call - should export
        with mock_session_start_context(plan_path=temp_plan_file):
            handle_session_start(make_hook_input(cwd=str(temp_project["project_dir"])))

        # Clear captured output
        capsys.readouterr()

        # Second call - tracking should show already exported
        tracking = {content_hash: {"exported_at": "2024-01-01"}}
        with mock_session_start_context(plan_path=temp_plan_file, tracking=tracking):
            handle_session_start(make_hook_input(cwd=str(temp_project["project_dir"])))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True
        # No systemMessage means skipped (already exported)
        assert "Recovered" not in result.get("systemMessage", "")

    def test_project_dir_not_writable(
        self, temp_project: dict, temp_plan_file: Path, capsys
    ):
        """Handler gracefully fails if project_dir is not writable."""
        with mock_session_start_context(plan_path=temp_plan_file):
            with patch("plan_export.export_plan", side_effect=OSError("Read-only filesystem")):
                handle_session_start(make_hook_input(cwd="/nonexistent"))

        result = parse_last_json_output(capsys.readouterr().out)
        assert result["continue"] is True


# =============================================================================
# CODE QUALITY TESTS
# =============================================================================


class TestCodeQuality:
    """Tests for code quality and consistency."""

    def test_all_returns_have_valid_json(self):
        """All return paths produce valid JSON with continue field."""
        import inspect

        source = inspect.getsource(handle_session_start)

        # Count print(json.dumps(...)) calls
        json_dumps_count = source.count('print(json.dumps(')
        # All should have "continue": True
        continue_true_count = source.count('"continue": True')

        assert continue_true_count >= json_dumps_count - 1, (
            f"Found {json_dumps_count} json.dumps calls but only {continue_true_count} with continue=True"
        )

    def test_exception_handlers_fail_open(self):
        """All exception handlers return continue=True (fail open)."""
        import inspect

        source = inspect.getsource(handle_session_start)

        assert 'except SessionTimeoutError:' in source
        assert 'except Exception as e:' in source

        # After each except, should print continue=True
        lines = source.split('\n')
        in_except = False
        for i, line in enumerate(lines):
            if 'except ' in line:
                in_except = True
            elif in_except and 'print(json.dumps' in line:
                next_line = lines[i + 1] if i + 1 < len(lines) else ""
                assert 'continue' in line or 'continue' in next_line
                in_except = False

    def test_docstring_documents_bug(self):
        """Verify handle_session_start documents the Claude Code bug."""
        import inspect

        docstring = inspect.getdoc(handle_session_start)

        assert "CLAUDE CODE BUG" in docstring
        assert "fresh context" in docstring.lower()
        assert "Option 1" in docstring
        assert "PostToolUse" in docstring


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
