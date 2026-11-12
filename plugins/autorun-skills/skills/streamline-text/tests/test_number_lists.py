"""Regression tests for the Markdown list-numbering helper."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "number_lists.py"


def _convert(tmp_path: Path, text: str) -> str:
    source = tmp_path / "example.md"
    source.write_text(text, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(source)],
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return source.read_text(encoding="utf-8")


def test_converts_top_level_and_nested_dashes(tmp_path):
    converted = _convert(
        tmp_path,
        "- first\n"
        "   - nested one\n"
        "   - nested two\n"
        "- second\n",
    )

    assert converted == (
        "1. first\n"
        "   1. nested one\n"
        "   2. nested two\n"
        "2. second\n"
    )


def test_preserves_frontmatter_tables_and_fenced_code(tmp_path):
    source = (
        "---\n"
        "name: example\n"
        "---\n"
        "| value |\n"
        "|---|\n"
        "```text\n"
        "- code\n"
        "```\n"
        "- prose\n"
    )

    converted = _convert(tmp_path, source)

    assert converted == source.replace("- prose\n", "1. prose\n")
