"""The release body is generated from CHANGELOG.md and reads upgrade-first.

`docs/releases/<version>.md` becomes the tag annotation and the GitHub Release,
and neither can be corrected once published. It used to be a second hand-written
copy of the CHANGELOG section, and the two drifted apart in wording. It also put
upgrade steps after the fixes, used bold-heading bullets, and mixed test names
into user entries. These tests hold the generated file to its source and the
source to a shape a reader can follow.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "release_notes", REPO_ROOT / "scripts" / "release_notes.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclasses resolves annotations through sys.modules, so register first.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release_notes = _load()

GOOD = """\
One sentence on what users get.

### Upgrading from 0.0.1rc1

1. Restart running sessions.

### Fixed

1. A symptom, then what happens now.

### For contributors

1. An internal change.
"""


def _problems(body: str, previous: str = "0.0.1rc1") -> list[str]:
    section = release_notes.Section("0.0.1rc2", "2026-09-27", body)
    return release_notes.structure_problems(section, previous)


def test_the_published_notes_are_the_changelog_section_rendered():
    path, expected, problems = release_notes.build()
    assert not problems, "CHANGELOG.md section is malformed:\n  " + "\n  ".join(problems)
    assert path.read_text(encoding="utf-8") == expected, (
        f"{path.relative_to(REPO_ROOT)} differs from its CHANGELOG section; run "
        "`uv run --project plugins/autorun --locked python scripts/release_notes.py`"
    )


def test_a_well_formed_section_passes():
    assert _problems(GOOD) == []


@pytest.mark.parametrize(
    ("body", "complaint"),
    [
        (GOOD.replace("One sentence on what users get.\n", ""), "no summary"),
        (GOOD.replace("Upgrading from 0.0.1rc1", "Upgrade notes"), "first heading"),
        (GOOD.replace("Upgrading from 0.0.1rc1", "Upgrading from 0.0.1rc3"), "first heading"),
        (GOOD.replace("### Fixed", "### Bug fixes"), "unknown heading"),
        (GOOD.replace("### For contributors", "### Added"), "in this order"),
        (GOOD + "\n### Fixed\n\n1. Again.\n", "in this order"),
        (GOOD.replace("1. A symptom", "- A symptom"), "is a bullet"),
        (GOOD.replace("1. A symptom", "1. **Bold:** A symptom"), "bold heading"),
        (GOOD.replace("### Fixed", "## Fixed"), "heading level"),
    ],
)
def test_each_structure_rule_names_its_failure(body, complaint):
    assert any(complaint in problem for problem in _problems(body)), _problems(body)


def test_fenced_lines_are_not_mistaken_for_list_items_or_headings():
    body = GOOD.replace(
        "1. Restart running sessions.\n",
        "1. Restart running sessions.\n\n```bash\n# a comment\n- not a bullet\n```\n",
    )
    assert _problems(body) == []


def test_the_upgrade_commands_lead_the_upgrade_section():
    section = release_notes.Section("0.0.1rc2", "2026-09-27", GOOD)
    rendered = release_notes.render(section, "0.0.1rc1")
    upgrading = rendered.index("## Upgrading from 0.0.1rc1")
    command = rendered.index("uv tool install --force 'autorun-ai==0.0.1rc2' && autorun --install")
    first_step = rendered.index("1. Restart running sessions.")
    assert upgrading < command < first_step
    assert rendered.index("## Fixed") > first_step
    assert "`autorun --version` prints `autorun 0.0.1rc2`" in _prose(rendered)
    pdf = rendered.index("'autorun-ai[pdf]==0.0.1rc2'")
    assert command < pdf < first_step, "the PDF variant must show before the steps"
    assert "compare/v0.0.1rc1...v0.0.1rc2" in rendered


def _prose(text: str) -> str:
    return " ".join(text.split())


def test_only_a_prerelease_explains_its_version_pin():
    assert "Keep the version pin" in _prose(release_notes.upgrade_commands("0.0.1rc2"))
    assert "Keep the version pin" not in _prose(release_notes.upgrade_commands("0.0.1"))
    for version in ("0.0.1rc2", "0.0.1"):
        text = release_notes.upgrade_commands(version)
        assert f"'autorun-ai[pdf]=={version}'" in text, "the PDF extra must be named again"


def test_wrapping_never_splits_a_code_span():
    text = release_notes.upgrade_commands("0.0.1rc2")
    for span in ("`claude plugin update ar@autorun`", "`uv tool list --show-extras`"):
        assert span in text, f"{span} was split across lines"


def test_a_mistyped_level_two_heading_is_reported_not_dropped():
    changelog = (
        "## [0.0.2] - 2026-01-02\n\nSummary.\n\n### Upgrading from 0.0.1\n\n"
        "Nothing.\n\n## Fixed\n\n1. Kept.\n\n## [0.0.1] - 2026-01-01\n\nOld.\n"
    )
    newest, previous = release_notes.sections(changelog)
    assert "1. Kept." in newest.body
    problems = release_notes.structure_problems(newest, previous.version)
    assert any("heading level" in problem for problem in problems), problems


def test_headings_inside_fences_are_neither_checked_nor_promoted():
    body = GOOD.replace(
        "1. A symptom, then what happens now.\n",
        "1. A symptom, then what happens now.\n\n   ~~~text\n   ### not a heading\n   - x\n   ~~~\n",
    )
    assert _problems(body) == []
    section = release_notes.Section("0.0.1rc2", "2026-09-27", body)
    assert "   ### not a heading" in release_notes.render(section, "0.0.1rc1")


def test_dev_releases_count_as_prereleases():
    assert release_notes.is_prerelease("0.0.1.dev3")
    assert not release_notes.is_prerelease("0.0.1")


def _write_repo(root: Path, changelog: str) -> None:
    (root / "plugins" / "autorun").mkdir(parents=True)
    (root / "plugins" / "autorun" / "pyproject.toml").write_text('version = "0.0.2"\n')
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "CHANGELOG.md").write_text(changelog)


def test_build_refuses_a_missing_or_duplicate_section_or_no_previous(tmp_path):
    for name, changelog in {
        "missing": "## [0.0.1] - 2026-01-01\n\nOld.\n",
        "duplicate": "## [0.0.2] - 2026-01-02\n\nA.\n\n## [0.0.2] - 2026-01-02\n\nB.\n",
        "no-previous": "## [0.0.2] - 2026-01-02\n\nOnly.\n",
    }.items():
        _write_repo(tmp_path / name, changelog)
        with pytest.raises(SystemExit):
            release_notes.build(tmp_path / name)


def test_check_fails_on_a_stale_file_and_passes_once_regenerated(tmp_path, monkeypatch):
    changelog = "## [0.0.2] - 2026-01-02\n\n" + GOOD.replace("0.0.1rc1", "0.0.1") + "\n## [0.0.1] - 2026-01-01\n\nOld.\n"
    _write_repo(tmp_path, changelog)
    monkeypatch.setattr(release_notes, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(release_notes, "build", lambda: _real_build(tmp_path))
    assert release_notes.main(["--check"]) == 1, "a missing notes file must fail"
    assert release_notes.main([]) == 0
    notes = tmp_path / "docs" / "releases" / "0.0.2.md"
    assert b"\r\n" not in notes.read_bytes()
    assert release_notes.main(["--check"]) == 0
    notes.write_text(notes.read_text() + "edited by hand\n")
    assert release_notes.main(["--check"]) == 1


_real_build = release_notes.build


def test_sections_stop_at_an_undated_heading():
    changelog = (
        "# Changelog\n\n## [0.0.3] - 2026-01-02\n\nNew.\n\n"
        "## [0.0.2] - 2026-01-01\n\nOld.\n\n## [0.0.1]\n\nUndated.\n"
    )
    found = release_notes.sections(changelog)
    assert [(s.version, s.body) for s in found] == [("0.0.3", "New."), ("0.0.2", "Old.")]
