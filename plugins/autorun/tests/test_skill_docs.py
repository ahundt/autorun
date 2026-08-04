import subprocess
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PLUGIN_ROOT / "skills"
AUDIT_SCRIPT = SKILLS_ROOT / "ai-skill-builder" / "scripts" / "audit-skill.sh"


def _skill_entrypoints() -> list[Path]:
    """Return installed skill entrypoint docs, excluding reference material."""
    return sorted(SKILLS_ROOT.glob("*/SKILL.md"))


def _packaged_skill_dirs() -> list[Path]:
    """Return every skill directory the installer can package.

    An empty directory is a working-tree artifact, not a skill, and is skipped
    so a stray mkdir does not read as a broken package.
    """
    return sorted(
        path
        for path in SKILLS_ROOT.iterdir()
        if path.is_dir() and any(path.iterdir())
    )


def _skill_ids() -> list[str]:
    return [path.name for path in _packaged_skill_dirs()]


@pytest.mark.parametrize("skill_dir", _packaged_skill_dirs(), ids=_skill_ids())
def test_packaged_skill_has_a_real_skill_md_not_a_symlink(skill_dir):
    """`shutil.copytree(symlinks=False)` materializes a symlinked SKILL.md as a
    second full copy of its target, so every install ships the same body twice
    under two independently editable names."""
    entrypoint = skill_dir / "SKILL.md"

    assert entrypoint.is_file(), f"{skill_dir.name} has no SKILL.md"
    assert not entrypoint.is_symlink(), (
        f"{skill_dir.name}/SKILL.md is a symlink to {Path(entrypoint).readlink()}; "
        "installs would ship both files"
    )


@pytest.mark.parametrize("skill_dir", _packaged_skill_dirs(), ids=_skill_ids())
def test_packaged_skill_frontmatter_name_matches_its_directory(skill_dir):
    """Hosts key a skill by directory name; a divergent `name` field is the
    identifier users see fail to resolve."""
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    declared = ""
    for line in text.splitlines()[1:]:
        if line.startswith("---"):
            break
        if line.startswith("name:"):
            declared = line.split(":", 1)[1].strip().strip("'\"")
            break

    assert declared in ("", skill_dir.name), (
        f"{skill_dir.name}/SKILL.md declares name {declared!r}"
    )


@pytest.mark.parametrize("skill_dir", _packaged_skill_dirs(), ids=_skill_ids())
def test_packaged_skill_passes_the_structural_audit(skill_dir):
    """Phase 1 release gate: zero structural FAILs for every packaged skill."""
    result = _run_audit(str(skill_dir))

    assert result.returncode == 0, result.stdout


def _run_audit(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(AUDIT_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _write_skill(root: Path, name: str, frontmatter: str, body: str = "") -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
    return skill


def test_audit_script_exits_zero_on_a_structurally_clean_skill(tmp_path):
    """A release gate can only gate if a clean run is distinguishable by status."""
    skill = _write_skill(
        tmp_path,
        "clean-fixture-skill",
        "name: clean-fixture-skill\n"
        'description: Render a fixture report from a directory. Use when asked to'
        ' "render a fixture report" or "audit a fixture skill", for example `pytest`'
        " cases naming *.md inputs.",
        "# Clean Fixture Skill\n\n## Workflow\n\n1. Read the input.\n2. Report.\n",
    )

    result = _run_audit(str(skill))

    assert "❌ FAIL" not in result.stdout, result.stdout
    assert result.returncode == 0, result.stdout


def test_audit_script_exits_nonzero_when_a_structural_check_fails(tmp_path):
    """A FAIL that still exits 0 lets CI and humans read a broken skill as passing."""
    skill = _write_skill(
        tmp_path,
        "Failing_Fixture_Skill",
        "name: totally-different-name\nversion: 9.9.9\ndescription: short",
    )

    result = _run_audit(str(skill))

    assert "❌ FAIL" in result.stdout, result.stdout
    assert result.returncode == 1, result.stdout


def test_audit_script_help_documents_its_exit_codes():
    """The exit contract is only usable if `--help` states it."""
    result = _run_audit("--help")

    assert result.returncode == 0, result.stderr
    assert "zero structural FAILs" in result.stdout
    assert "audit-skill.sh <skill-path>" in result.stdout


def test_audit_script_exits_nonzero_on_missing_directory(tmp_path):
    """An unusable argument must not be reported with the clean-run status."""
    result = _run_audit(str(tmp_path / "no-such-skill"))

    assert result.returncode == 1


def test_skill_entrypoints_do_not_embed_executable_markdown_commands():
    """Skills should guide tool use; they must not run Claude-only !` snippets."""
    offenders = [
        str(path.relative_to(PLUGIN_ROOT))
        for path in _skill_entrypoints()
        if "!`" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_user_invocable_skills_do_not_advertise_slash_as_skill_invocation():
    """Slash commands are harness-specific; skills need skill-native invocation text."""
    offenders = []
    for path in _skill_entrypoints():
        text = path.read_text(encoding="utf-8")
        if "user-invocable: true" in text and "Invoke with:** `/ar:" in text:
            offenders.append(str(path.relative_to(PLUGIN_ROOT)))

    assert offenders == []


def test_autorun_maintainer_skill_covers_current_harnesses_and_scoped_restarts():
    """Maintainer guidance should reflect current multi-harness install safety."""
    text = (SKILLS_ROOT / "autorun-maintainer" / "SKILL.md").read_text(encoding="utf-8")

    for required in [
        "Codex CLI",
        "Google Antigravity",
        "Qwen Code",
        "custom harness",
        "autorun --status --custom-harness SPEC",
        "autorun --install-dry-run",
        "autorun --restart-all-daemons",
    ]:
        assert required in text

    assert "restart-all-daemons` only" in text
    assert "name=flavor:binary:config_dir[::display]" in text
    assert "name=flavor:binary:config_dir[:display]" not in text
    assert "pkill -f" not in text
    assert "0.11.0" not in text
