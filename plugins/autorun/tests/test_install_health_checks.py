"""Health checks for the failure modes that were silent until diagnosed by hand.

Every check here corresponds to a defect that shipped and produced no signal:

- A guidance block installed into ~/.codex/AGENTS.md that Codex never reads,
  because AGENTS.override.md takes precedence
  (openai/codex codex-home/src/instructions/mod.rs:9-10, :26-62).
- A skill symlink into the shared agents directory whose target is gone, after
  the directory moved or a skill was deleted.
- The same skill reaching one harness by two paths, which double-lists it:
  Claude Code deduplicates by resolved path, not by name.
- A sentinel block in a memory file whose slug no longer matches any platform,
  left behind by a rename — uninstall would never find it.
- A top-level key in ~/.codex/hooks.json that Codex rejects. HooksFile is
  #[serde(deny_unknown_fields)] (hooks/src/engine/hook_config.rs:10-17), so one
  stray key makes Codex drop every hook in the file
  (engine/discovery.rs:327-336).

Findings are advisory: `check_install_health` reports, it does not repair.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    check_install_health,
    install_platform_memory,
)
from autorun.platforms import PLATFORMS  # noqa: E402

REAL_PLUGIN_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    return tmp_path


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def test_a_clean_machine_reports_nothing(home):
    assert check_install_health() == []


# --------------------------------------------------------------------------
# Memory guidance that the harness will never read
# --------------------------------------------------------------------------


def test_reports_guidance_shadowed_by_an_override_file(home):
    codex = home / ".codex"
    codex.mkdir(parents=True)
    install_platform_memory(PLATFORMS["codex"], REAL_PLUGIN_DIR, codex)
    (codex / "AGENTS.override.md").write_text("# mine\n", encoding="utf-8")

    findings = check_install_health()

    assert "memory-shadowed-by-override" in _codes(findings)
    assert any("AGENTS.override.md" in f.detail for f in findings)


def test_a_blank_override_does_not_shadow(home):
    """Codex falls through to AGENTS.md when the override is blank."""
    codex = home / ".codex"
    codex.mkdir(parents=True)
    install_platform_memory(PLATFORMS["codex"], REAL_PLUGIN_DIR, codex)
    (codex / "AGENTS.override.md").write_text("  \n\n", encoding="utf-8")

    assert "memory-shadowed-by-override" not in _codes(check_install_health())


def test_an_override_without_our_block_is_not_reported(home):
    """Nothing of ours is being hidden, so there is nothing to say."""
    codex = home / ".codex"
    codex.mkdir(parents=True)
    (codex / "AGENTS.override.md").write_text("# mine\n", encoding="utf-8")

    assert "memory-shadowed-by-override" not in _codes(check_install_health())


# --------------------------------------------------------------------------
# Orphaned blocks
# --------------------------------------------------------------------------


def test_reports_a_block_whose_slug_matches_no_platform(home):
    """A renamed slug orphans the old block; uninstall would never find it."""
    claude = home / ".claude"
    claude.mkdir(parents=True)
    (claude / "CLAUDE.md").write_text(
        "# mine\n\n<!-- autorun:legacy-slug:start -->\nold\n"
        "<!-- autorun:legacy-slug:end -->\n",
        encoding="utf-8",
    )

    findings = check_install_health()

    assert "memory-orphaned-block" in _codes(findings)
    assert any("legacy-slug" in f.detail for f in findings)


def test_a_current_block_is_not_reported_as_orphaned(home):
    claude = home / ".claude"
    claude.mkdir(parents=True)
    install_platform_memory(PLATFORMS["claude"], REAL_PLUGIN_DIR, claude)

    assert "memory-orphaned-block" not in _codes(check_install_health())


# --------------------------------------------------------------------------
# Skill links
# --------------------------------------------------------------------------


def test_reports_a_broken_skill_link(home):
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "gone").symlink_to(home / ".agents" / "skills" / "gone")

    findings = check_install_health()

    assert "skill-link-broken" in _codes(findings)


def test_a_working_skill_link_is_not_reported(home):
    source = home / ".agents" / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "demo").symlink_to(source)

    assert "skill-link-broken" not in _codes(check_install_health())


def test_a_link_pointing_outside_the_shared_dir_is_not_ours_to_report(home):
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "theirs").symlink_to(home / "somewhere-else")

    assert "skill-link-broken" not in _codes(check_install_health())


# --------------------------------------------------------------------------
# Duplicate skills
# --------------------------------------------------------------------------


def test_reports_a_skill_reaching_the_harness_by_two_paths(home):
    """Dedup is by resolved path, so this costs a second listing entry."""
    shared = home / ".agents" / "skills" / "demo"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")

    own = home / ".claude" / "skills" / "demo"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")

    findings = check_install_health()

    assert "skill-duplicate" in _codes(findings)
    assert any("demo" in f.detail for f in findings)


def test_a_linked_skill_is_not_counted_as_a_duplicate_of_itself(home):
    shared = home / ".agents" / "skills" / "demo"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\ndescription: d\n---\n", encoding="utf-8")
    skills = home / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / "demo").symlink_to(shared)

    assert "skill-duplicate" not in _codes(check_install_health())


# --------------------------------------------------------------------------
# hooks.json keys Codex rejects
# --------------------------------------------------------------------------


def test_reports_a_top_level_key_codex_would_reject(home):
    codex = home / ".codex"
    codex.mkdir(parents=True)
    (codex / "hooks.json").write_text(
        json.dumps({"$schema": "x", "hooks": {}}), encoding="utf-8"
    )

    findings = check_install_health()

    assert "codex-hooks-unknown-top-level-key" in _codes(findings)
    assert any("$schema" in f.detail for f in findings)


@pytest.mark.parametrize("key", ["description", "hooks"])
def test_the_two_allowed_top_level_keys_are_not_reported(home, key):
    codex = home / ".codex"
    codex.mkdir(parents=True)
    (codex / "hooks.json").write_text(
        json.dumps({key: {} if key == "hooks" else "notes"}), encoding="utf-8"
    )

    assert "codex-hooks-unknown-top-level-key" not in _codes(check_install_health())


def test_unparseable_hooks_json_is_reported_not_raised(home):
    codex = home / ".codex"
    codex.mkdir(parents=True)
    (codex / "hooks.json").write_text("{not json", encoding="utf-8")

    findings = check_install_health()

    assert "codex-hooks-unparseable" in _codes(findings)


# --------------------------------------------------------------------------
# Reporting contract
# --------------------------------------------------------------------------


def test_every_finding_carries_a_code_detail_and_remedy(home):
    codex = home / ".codex"
    codex.mkdir(parents=True)
    install_platform_memory(PLATFORMS["codex"], REAL_PLUGIN_DIR, codex)
    (codex / "AGENTS.override.md").write_text("# mine\n", encoding="utf-8")

    for finding in check_install_health():
        assert finding.code
        assert finding.detail
        assert finding.remedy, f"{finding.code} must tell the user what to do"


def test_health_checks_never_raise_on_an_unreadable_tree(home, monkeypatch):
    """Diagnostics must fail open; a status pass should not abort."""
    def _boom(*a, **k):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "iterdir", _boom)
    assert isinstance(check_install_health(), list)
