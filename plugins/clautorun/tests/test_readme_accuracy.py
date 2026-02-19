"""
Tests that README.md and CLAUDE.md have correct stage marker strings matching config.py.

These tests prevent documentation drift where wrong stage markers in docs cause users
to output AUTORUN_STAGE[123]_COMPLETE instead of the correct longer strings, preventing
the autorun system from detecting stage completion.

Correct values (from config.py:CONFIG):
  stage1_message: AUTORUN_INITIAL_TASKS_COMPLETED
  stage2_message: CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED
  stage3_message: AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY
  emergency_stop: AUTORUN_STATE_PRESERVATION_EMERGENCY_STOP
"""
from pathlib import Path


def _repo_root() -> Path:
    """Find the repository root (contains README.md and CLAUDE.md)."""
    # This test file is at plugins/clautorun/tests/test_readme_accuracy.py
    # Repo root is 3 levels up
    return Path(__file__).parent.parent.parent.parent


def test_readme_stage_markers_match_config():
    """README.md must not contain wrong AUTORUN_STAGE[123]_COMPLETE markers."""
    readme_path = _repo_root() / "README.md"
    readme = readme_path.read_text()

    # README must NOT have these wrong strings
    assert "AUTORUN_STAGE1_COMPLETE" not in readme, (
        "README.md has wrong stage1 marker 'AUTORUN_STAGE1_COMPLETE' — "
        "should be 'AUTORUN_INITIAL_TASKS_COMPLETED' (from config.py:CONFIG['stage1_message'])"
    )
    assert "AUTORUN_STAGE2_COMPLETE" not in readme, (
        "README.md has wrong stage2 marker 'AUTORUN_STAGE2_COMPLETE' — "
        "should be 'CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED'"
    )
    assert "AUTORUN_STAGE3_COMPLETE" not in readme, (
        "README.md has wrong stage3 marker 'AUTORUN_STAGE3_COMPLETE' — "
        "should be 'AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY'"
    )

    # README MUST have these correct strings
    from clautorun.config import CONFIG

    assert CONFIG["stage1_message"] in readme, (
        f"README.md missing correct stage1_message: '{CONFIG['stage1_message']}'"
    )
    assert CONFIG["stage2_message"] in readme, (
        f"README.md missing correct stage2_message: '{CONFIG['stage2_message']}'"
    )
    assert CONFIG["stage3_message"] in readme, (
        f"README.md missing correct stage3_message: '{CONFIG['stage3_message']}'"
    )


def test_readme_emergency_stop_documented():
    """README.md should document the emergency stop marker."""
    readme_path = _repo_root() / "README.md"
    readme = readme_path.read_text()

    from clautorun.config import CONFIG

    assert CONFIG["emergency_stop"] in readme, (
        f"README.md missing emergency_stop marker: '{CONFIG['emergency_stop']}'"
    )


def test_claude_md_stage_markers_match_config():
    """CLAUDE.md (repo root) must not contain wrong AUTORUN_STAGE[123]_COMPLETE markers."""
    claude_md_path = _repo_root() / "CLAUDE.md"
    claude_md = claude_md_path.read_text()

    assert "AUTORUN_STAGE1_COMPLETE" not in claude_md, (
        "CLAUDE.md has wrong stage1 marker 'AUTORUN_STAGE1_COMPLETE' — "
        "should be 'AUTORUN_INITIAL_TASKS_COMPLETED'"
    )
    assert "AUTORUN_STAGE2_COMPLETE" not in claude_md, (
        "CLAUDE.md has wrong stage2 marker 'AUTORUN_STAGE2_COMPLETE'"
    )
    assert "AUTORUN_STAGE3_COMPLETE" not in claude_md, (
        "CLAUDE.md has wrong stage3 marker 'AUTORUN_STAGE3_COMPLETE'"
    )
