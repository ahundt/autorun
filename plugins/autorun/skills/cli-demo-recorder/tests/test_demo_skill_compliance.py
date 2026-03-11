"""Validate demo assets across repos meet cli-demo-recorder skill standards.

Tests cast file resolution (cols/rows), MP4 resolution (width >= 1080),
and audit script score. Canal demos are marked xfail until re-recorded
at 160x48.

Run: pytest tests/test_demo_skill_compliance.py -v
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

# --- Cast file resolution tests ---

CAST_FILES = [
    ("~/.claude/ai_session_tools/demo.cast", 160, 48),
    ("~/.claude/autorun/autorun_demo.cast", 160, 48),
    pytest.param(
        "~/source/canal/demos/canal-demo.cast",
        160,
        48,
        marks=pytest.mark.xfail(reason="80x24 — needs re-recording at 160x48"),
    ),
    pytest.param(
        "~/source/canal/demos/canal-interactive.cast",
        160,
        48,
        marks=pytest.mark.xfail(reason="80x24 — needs re-recording at 160x48"),
    ),
]


@pytest.mark.parametrize("cast_file,expected_cols,expected_rows", CAST_FILES)
def test_cast_resolution_minimum(cast_file, expected_cols, expected_rows):
    """Cast files must use at least 160x48 terminal for Full HD output."""
    path = Path(cast_file).expanduser()
    if not path.exists():
        pytest.skip(f"Cast file not found: {path}")
    with open(path) as f:
        header = json.loads(f.readline())
    # asciinema v2 uses flat "width"/"height", v3 nests under "term"
    if "term" in header:
        cols = header["term"].get("cols", 0)
        rows = header["term"].get("rows", 0)
    else:
        cols = header.get("width", 0)
        rows = header.get("height", 0)
    assert cols >= expected_cols, f"Cast cols {cols} < {expected_cols} minimum"
    assert rows >= expected_rows, f"Cast rows {rows} < {expected_rows} minimum"


# --- MP4 resolution tests ---

MP4_FILES = [
    ("~/.claude/ai_session_tools/demo.mp4", 1080),
    ("~/.claude/autorun/autorun_demo.mp4", 1080),
    pytest.param(
        "~/source/canal/demos/canal-demo.mp4",
        1080,
        marks=pytest.mark.xfail(reason="690px — needs re-recording at 160x48"),
    ),
]


@pytest.mark.parametrize("mp4_file,min_width", MP4_FILES)
def test_mp4_resolution_minimum(mp4_file, min_width):
    """MP4 output must be at least 1080px wide (Full HD)."""
    path = Path(mp4_file).expanduser()
    if not path.exists():
        pytest.skip(f"MP4 file not found: {path}")
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"ffprobe not available or failed: {result.stderr}")
    width = int(result.stdout.strip())
    assert width >= min_width, f"MP4 width {width}px < {min_width}px minimum"


# --- Skill audit test ---


def test_skill_audit_no_failures():
    """Audit script must have 0 FAILs (score >= 85%)."""
    audit_script = Path(
        "~/.claude/skills/claude-skill-builder/scripts/audit-skill.sh"
    ).expanduser()
    skill_dir = Path("~/.claude/skills/cli-demo-recorder").expanduser()
    if not audit_script.exists():
        pytest.skip("Audit script not found")
    if not skill_dir.exists():
        pytest.skip("cli-demo-recorder skill not found")
    result = subprocess.run(
        ["bash", str(audit_script), str(skill_dir)],
        capture_output=True,
        text=True,
    )
    # Strip ANSI escape codes before checking
    clean = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    assert "Failed:   0" in clean, f"Audit has failures:\n{clean[-500:]}"


# --- SKILL.md word count test ---


def test_skill_word_count_under_limit():
    """SKILL.md must be under 5000 words (hard limit)."""
    skill_md = Path("~/.claude/skills/cli-demo-recorder/SKILL.md").expanduser()
    if not skill_md.exists():
        pytest.skip("SKILL.md not found")
    text = skill_md.read_text()
    word_count = len(text.split())
    assert word_count < 5000, f"SKILL.md is {word_count} words (limit: 5000)"


# --- Reference files existence test ---

EXPECTED_REFERENCES = [
    "references/hook-based-tools.md",
    "references/common-pitfalls.md",
    "references/prompt-engineering.md",
    "references/rust-demos.md",
    "references/typescript-demos.md",
    "references/tmux-integration.md",
    "references/cast-merging.md",
    "references/alternative-tools.md",
    "references/examples/aise-cli-example.md",
    "references/examples/autorun-tui-example.md",
]


@pytest.mark.parametrize("ref_file", EXPECTED_REFERENCES)
def test_reference_file_exists(ref_file):
    """All reference files listed in SKILL.md must exist and have content."""
    path = Path("~/.claude/skills/cli-demo-recorder").expanduser() / ref_file
    assert path.exists(), f"Reference file missing: {ref_file}"
    assert path.stat().st_size > 100, f"Reference file too small: {ref_file}"


# --- Frontmatter validation ---


def test_skill_frontmatter_has_version():
    """SKILL.md frontmatter must have version field."""
    skill_md = Path("~/.claude/skills/cli-demo-recorder/SKILL.md").expanduser()
    if not skill_md.exists():
        pytest.skip("SKILL.md not found")
    text = skill_md.read_text()
    assert "version:" in text[:500], "Missing version field in frontmatter"


def test_skill_frontmatter_has_trigger_phrases():
    """SKILL.md description must have quoted trigger phrases."""
    skill_md = Path("~/.claude/skills/cli-demo-recorder/SKILL.md").expanduser()
    if not skill_md.exists():
        pytest.skip("SKILL.md not found")
    text = skill_md.read_text()[:500]
    assert '"record a demo"' in text, "Missing trigger phrase in description"
