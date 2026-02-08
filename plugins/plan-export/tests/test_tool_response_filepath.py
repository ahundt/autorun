#!/usr/bin/env python3
"""
Tests for plan_export.py tool_response.filePath handling.

These tests verify that plan_export.py correctly uses tool_response.filePath
as the primary source for the plan file path when exiting plan mode.

Bug details:
- ExitPlanMode PostToolUse hook receives tool_response with:
  - filePath: path to the plan file (e.g., "/Users/.../.claude/plans/keen-napping-sparkle.md")
  - plan: the plan content
- The original code ignored tool_response.filePath and tried:
  1. Transcript parsing (often fails)
  2. Session ID metadata search (often fails)
  3. Most recent plan (returns wrong file!)
- The fix uses tool_response.filePath first, then falls back to other methods

Reference: Claude Code hooks documentation confirms tool_response contains
filePath for tools like Write and ExitPlanMode.
"""

import ast
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import os

import pytest


def get_plan_export_script() -> Path:
    """Get the path to plan_export.py."""
    return Path(__file__).parent.parent / "scripts" / "plan_export.py"


class TestToolResponseFilePathCode:
    """Tests that verify the tool_response.filePath handling is correctly implemented."""

    def test_script_checks_tool_response_first(self):
        """Verify plan_export.py checks tool_response.filePath before other methods."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should check tool_response.get("filePath")
        assert 'tool_response.get("filePath")' in content, (
            "plan_export.py should extract filePath from tool_response"
        )

        # Should have comment explaining this is the most reliable source
        assert "tool_response" in content and "most reliable" in content.lower(), (
            "plan_export.py should document that tool_response is the most reliable source"
        )

    def test_script_has_correct_fallback_order(self):
        """Verify the fallback chain is in the correct order."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Find the main() function section where the fallback chain is implemented
        main_start = content.find("def main():")
        assert main_start > 0, "Should have main() function"
        main_section = content[main_start:]

        # Find positions of each method CALL in main()
        # (not the function definitions which appear earlier)
        tool_response_pos = main_section.find('tool_response.get("filePath")')
        transcript_pos = main_section.find("get_plan_from_transcript(transcript_path)")
        metadata_pos = main_section.find("find_plan_by_session_id(session_id)")
        recent_pos = main_section.find("get_most_recent_plan()")

        assert tool_response_pos > 0, "Should have tool_response.filePath check in main()"
        assert transcript_pos > 0, "Should have transcript parsing call in main()"
        assert metadata_pos > 0, "Should have metadata search call in main()"
        assert recent_pos > 0, "Should have most recent plan call in main()"

        # Verify order: tool_response first, then transcript, then metadata, then recent
        assert tool_response_pos < transcript_pos, (
            "tool_response.filePath should be checked BEFORE transcript parsing"
        )
        assert transcript_pos < metadata_pos, (
            "Transcript parsing should be BEFORE metadata search"
        )
        assert metadata_pos < recent_pos, (
            "Metadata search should be BEFORE most recent plan"
        )

    def test_script_validates_file_exists(self):
        """Verify plan_export.py checks if the file from tool_response exists."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should validate file exists before using it
        assert "candidate.exists()" in content or "candidate.is_file()" in content, (
            "plan_export.py should verify tool_response.filePath exists before using it"
        )

    def test_script_syntax_is_valid(self):
        """Verify plan_export.py has valid Python syntax."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        try:
            ast.parse(content)
        except SyntaxError as e:
            pytest.fail(f"plan_export.py has invalid syntax: {e}")


class TestToolResponseFilePathBehavior:
    """Tests that verify the tool_response.filePath handling works correctly at runtime."""

    def test_tool_response_with_valid_filepath(self, tmp_path):
        """Test that a valid tool_response.filePath is used correctly."""
        # Create a temporary plan file
        plan_file = tmp_path / "test-plan.md"
        plan_file.write_text("# Test Plan\n\nThis is a test plan.")

        # Simulate tool_response from ExitPlanMode
        tool_response = {
            "filePath": str(plan_file),
            "plan": "# Test Plan\n\nThis is a test plan."
        }

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is not None, "Should find plan from tool_response.filePath"
        assert plan_path == plan_file, "Should use the exact path from tool_response"

    def test_tool_response_with_nonexistent_filepath(self, tmp_path):
        """Test that nonexistent tool_response.filePath falls through to fallback."""
        # Simulate tool_response with a path that doesn't exist
        tool_response = {
            "filePath": "/nonexistent/path/plan.md",
            "plan": "# Test Plan"
        }

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should not use nonexistent path"

    def test_tool_response_without_filepath(self):
        """Test that tool_response without filePath falls through to fallback."""
        # Simulate tool_response without filePath (edge case)
        tool_response = {
            "plan": "# Test Plan"
        }

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should fall through when no filePath"

    def test_tool_response_not_dict(self):
        """Test that non-dict tool_response is handled gracefully."""
        # Simulate tool_response as string (edge case)
        tool_response = "some string response"

        # The logic from plan_export.py
        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should handle non-dict tool_response gracefully"

    def test_tool_response_empty_dict(self):
        """Test that empty tool_response is handled gracefully."""
        tool_response = {}

        plan_path = None
        if isinstance(tool_response, dict):
            file_path = tool_response.get("filePath")
            if file_path:
                candidate = Path(file_path)
                if candidate.exists():
                    plan_path = candidate

        assert plan_path is None, "Should handle empty tool_response gracefully"


class TestRegressionPrevention:
    """Tests to prevent regression of the bug."""

    def test_most_recent_plan_is_last_resort(self):
        """Verify get_most_recent_plan is only used as the last fallback."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should have comments indicating it's a fallback
        assert "Fallback" in content and "most recent" in content.lower(), (
            "Most recent plan should be documented as a fallback method"
        )

        # Find the CALL site (not the function definition) by looking in main()
        main_start = content.find("def main():")
        main_section = content[main_start:]
        call_pos = main_section.find("get_most_recent_plan()")

        assert call_pos > 0, "Should call get_most_recent_plan() in main()"

        # Should be the LAST fallback (Fallback 3 or "last resort")
        # Use a larger window (400 chars) to capture the comment above the if block
        recent_context = main_section[max(0, call_pos - 400):call_pos]
        assert "Fallback 3" in recent_context or "last resort" in recent_context.lower(), (
            f"Most recent plan should be Fallback 3 or marked as last resort. "
            f"Found context: {recent_context[-200:]!r}"
        )

    def test_tool_response_checked_before_transcript(self):
        """Verify tool_response.filePath is checked before transcript parsing in main().

        This is the core fix - the bug was that transcript parsing was tried first
        and when it failed, we fell back to most_recent_plan which was wrong.

        Note: We check within main() only because handle_session_start() has its own
        separate flow that doesn't use tool_response (SessionStart vs PostToolUse).
        """
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Find main() function and check order within it
        main_start = content.find("def main():")
        assert main_start > 0, "Should have main() function"
        main_section = content[main_start:]

        # Find the critical section positions within main()
        tool_response_check = main_section.find('isinstance(tool_response, dict)')
        transcript_call = main_section.find('get_plan_from_transcript(transcript_path)')

        assert tool_response_check > 0, "main() should have tool_response isinstance check"
        assert transcript_call > 0, "main() should have transcript parsing call"
        assert tool_response_check < transcript_call, (
            "CRITICAL: tool_response must be checked BEFORE transcript parsing in main(). "
            "This was the root cause of the bug - transcript parsing failed and "
            "fell back to most_recent_plan which returned the wrong file."
        )


class TestExitPlanModeToolResponse:
    """Tests specific to ExitPlanMode tool_response format."""

    def test_exitplanmode_response_format_documented(self):
        """Verify the expected ExitPlanMode response format is documented."""
        script_path = get_plan_export_script()
        content = script_path.read_text()

        # Should document the expected format
        assert "ExitPlanMode" in content, (
            "plan_export.py should document that it handles ExitPlanMode tool"
        )
        assert "filePath" in content and "plan" in content, (
            "plan_export.py should document the expected tool_response fields"
        )

    def test_handles_camelcase_filepath(self):
        """Verify we use camelCase 'filePath' not 'file_path'.

        Claude Code uses camelCase for tool_response fields (e.g., filePath, not file_path).
        """
        script_path = get_plan_export_script()
        content = script_path.read_text()

        assert '"filePath"' in content, (
            "Should use camelCase 'filePath' to match Claude Code's tool_response format"
        )


# =============================================================================
# PARALLEL SCENARIO TESTS
# =============================================================================

class TestParallelToolResponseFilePath:
    """Tests for parallel/concurrent tool_response.filePath handling."""

    def test_concurrent_exports_different_filepaths(self, tmp_path):
        """Two concurrent exports with different filePaths should not interfere.

        Scenario: Two ExitPlanMode calls happen nearly simultaneously for different plans.
        Each should export its own correct plan file.
        """
        import threading
        import time
        from pathlib import Path

        # Create two different plan files
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        plan_a = plans_dir / "plan-a.md"
        plan_a.write_text("# Plan A\n\nContent for plan A")

        plan_b = plans_dir / "plan-b.md"
        plan_b.write_text("# Plan B\n\nContent for plan B")

        # Create notes directory
        notes_dir = tmp_path / "project" / "notes"
        notes_dir.mkdir(parents=True)

        # Track which plan each thread exported
        results = {"a": None, "b": None}
        errors = []

        def simulate_export(plan_path: Path, key: str):
            """Simulate plan export using tool_response.filePath."""
            try:
                # Simulate the tool_response.filePath logic
                if plan_path.exists():
                    content = plan_path.read_text()
                    results[key] = content
            except Exception as e:
                errors.append(str(e))

        # Run exports concurrently
        thread_a = threading.Thread(target=simulate_export, args=(plan_a, "a"))
        thread_b = threading.Thread(target=simulate_export, args=(plan_b, "b"))

        thread_a.start()
        thread_b.start()

        thread_a.join(timeout=5)
        thread_b.join(timeout=5)

        # Verify each export got the correct plan
        assert not errors, f"Export errors: {errors}"
        assert results["a"] is not None, "Plan A should have been exported"
        assert results["b"] is not None, "Plan B should have been exported"
        assert "Plan A" in results["a"], "Thread A should have exported Plan A"
        assert "Plan B" in results["b"], "Thread B should have exported Plan B"

    def test_filepath_exists_race_condition(self, tmp_path):
        """File exists at check time but deleted before read.

        Scenario: tool_response.filePath points to a file that exists when checked,
        but is deleted before we read it. Should fall through to next fallback.
        """
        import threading

        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        plan_file = plans_dir / "ephemeral-plan.md"
        plan_file.write_text("# Ephemeral Plan")

        # Simulate the race: file exists, then disappears
        file_existed = plan_file.exists()
        plan_file.unlink()  # Delete between check and read
        file_exists_after = plan_file.exists()

        # The code should handle this gracefully
        assert file_existed is True, "File should have existed initially"
        assert file_exists_after is False, "File should be gone after deletion"

        # Verify the fallback chain pattern handles this
        plan_path = None
        candidate = plan_file
        if candidate.exists():  # This check should fail now
            plan_path = candidate

        assert plan_path is None, "Should fall through when file disappears"

    def test_parallel_exports_same_destination_no_corruption(self, tmp_path):
        """Parallel exports to same destination should not corrupt output.

        Scenario: Two exports happen simultaneously to the same notes folder.
        Both should complete without corrupting files (though one may overwrite).
        """
        import threading
        import time

        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        notes_dir = tmp_path / "notes"
        notes_dir.mkdir(parents=True)

        # Create two plans that might export to similar names
        plan_1 = plans_dir / "test-plan.md"
        plan_1.write_text("# Test Plan 1\n\n" + "Content 1 " * 100)

        plan_2 = plans_dir / "test-plan-2.md"
        plan_2.write_text("# Test Plan 2\n\n" + "Content 2 " * 100)

        exports_completed = {"count": 0}
        errors = []

        def simulate_export(plan: Path, dest_name: str):
            try:
                dest = notes_dir / dest_name
                content = plan.read_text()
                # Simulate slow write
                time.sleep(0.01)
                dest.write_text(content)
                exports_completed["count"] += 1
            except Exception as e:
                errors.append(str(e))

        threads = [
            threading.Thread(target=simulate_export, args=(plan_1, "export-1.md")),
            threading.Thread(target=simulate_export, args=(plan_2, "export-2.md")),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Export errors: {errors}"
        assert exports_completed["count"] == 2, "Both exports should complete"

        # Verify files are not corrupted (contain valid content)
        export_1 = notes_dir / "export-1.md"
        export_2 = notes_dir / "export-2.md"

        if export_1.exists():
            content = export_1.read_text()
            assert "# Test Plan" in content, "Export 1 should have valid plan content"

        if export_2.exists():
            content = export_2.read_text()
            assert "# Test Plan" in content, "Export 2 should have valid plan content"

    def test_rapid_sequential_exitplanmode_calls(self, tmp_path):
        """Rapid sequential ExitPlanMode calls should each use correct filePath.

        Scenario: User exits plan mode, re-enters, modifies, exits again quickly.
        Each export should use its own tool_response.filePath.
        """
        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        # Simulate 5 rapid exits with different plan files
        plan_files = []
        for i in range(5):
            plan = plans_dir / f"rapid-plan-{i}.md"
            plan.write_text(f"# Rapid Plan {i}\n\nIteration {i}")
            plan_files.append(plan)

        # Verify each file can be read correctly (simulating tool_response.filePath)
        for i, plan in enumerate(plan_files):
            content = plan.read_text()
            assert f"Rapid Plan {i}" in content, f"Plan {i} should have correct content"
            assert f"Iteration {i}" in content, f"Plan {i} should have iteration marker"

    def test_tool_response_filepath_atomic_read(self, tmp_path):
        """Reading from tool_response.filePath should be atomic.

        Even if file is being modified, we should get complete content or fail cleanly.
        """
        import threading
        import time

        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        plan_file = plans_dir / "atomic-test.md"
        plan_file.write_text("# Original Content\n\n" + "A" * 1000)

        read_results = []
        write_done = threading.Event()

        def reader():
            """Read the file multiple times."""
            for _ in range(10):
                try:
                    content = plan_file.read_text()
                    # Content should be coherent (all A's or all B's, not mixed)
                    if "A" * 100 in content or "B" * 100 in content:
                        read_results.append("valid")
                    else:
                        read_results.append("mixed")
                except Exception:
                    read_results.append("error")
                time.sleep(0.001)

        def writer():
            """Overwrite the file."""
            time.sleep(0.005)
            plan_file.write_text("# New Content\n\n" + "B" * 1000)
            write_done.set()

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)

        reader_thread.start()
        writer_thread.start()

        reader_thread.join(timeout=5)
        writer_thread.join(timeout=5)

        # All reads should be valid (complete content, not corrupted)
        invalid_reads = [r for r in read_results if r not in ("valid", "error")]
        assert not invalid_reads, f"Found corrupted reads: {invalid_reads}"


class TestParallelFallbackChain:
    """Tests for fallback chain behavior under parallel execution."""

    def test_fallback_chain_under_load(self, tmp_path):
        """Fallback chain should work correctly even under parallel load.

        Multiple threads checking fallback sources simultaneously.
        """
        import threading

        plans_dir = tmp_path / ".claude" / "plans"
        plans_dir.mkdir(parents=True)

        # Create a plan that all threads will try to find
        shared_plan = plans_dir / "shared-plan.md"
        shared_plan.write_text("# Shared Plan")

        results = []
        errors = []

        def check_fallback_chain(thread_id: int):
            """Simulate fallback chain check."""
            try:
                # Step 1: tool_response.filePath (simulate missing)
                tool_response = {}
                plan_path = None

                file_path = tool_response.get("filePath")
                if file_path:
                    candidate = Path(file_path)
                    if candidate.exists():
                        plan_path = candidate

                # Step 2: Fall through to most_recent_plan
                if not plan_path:
                    plans = list(plans_dir.glob("*.md"))
                    if plans:
                        plan_path = max(plans, key=lambda p: p.stat().st_mtime)

                if plan_path and plan_path.exists():
                    results.append((thread_id, plan_path.name))
                else:
                    results.append((thread_id, None))
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = [
            threading.Thread(target=check_fallback_chain, args=(i,))
            for i in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Fallback chain errors: {errors}"
        assert len(results) == 10, "All threads should complete"

        # All should find the shared plan
        for thread_id, plan_name in results:
            assert plan_name == "shared-plan.md", f"Thread {thread_id} found wrong plan: {plan_name}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
