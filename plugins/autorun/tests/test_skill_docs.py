import subprocess
import sys
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PLUGIN_ROOT / "skills"
AUDIT_SCRIPT = SKILLS_ROOT / "ai-skill-builder" / "scripts" / "audit-skill.sh"

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_SKILLS_ROOT = REPO_ROOT / ".agents" / "skills"
CLAUDE_SKILLS_ROOT = REPO_ROOT / ".claude" / "skills"

# Skills that document how to maintain autorun itself. They read the git
# checkout - plugins/autorun/src/, the test layout, this repository's install
# flow - none of which exists where the plugin is installed, so shipping them
# gives users a skill whose every instruction points at absent paths. Users get
# plugins/autorun/TROUBLESHOOTING.md, which is written for an installed copy.
REPO_INTERNAL_SKILLS = ("autorun-maintainer",)
POSIX_AUDIT_AVAILABLE = sys.platform != "win32"


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
@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
def test_packaged_skill_passes_the_structural_audit(skill_dir):
    """Phase 1 release gate: zero structural FAILs for every packaged skill."""
    result = _run_audit(str(skill_dir))

    assert result.returncode == 0, result.stdout


# ─── Semantic XML regions ───────────────────────────────────────────────────
#
# Methodology rule SKILL-REQ004 (plugins/autorun/skills/ai-skill-builder/SKILL.md):
# every major operational region of a SKILL.md body sits inside a balanced,
# descriptive XML tag on its own line, Markdown inside, code in fences. It is a
# quality policy for separating instructions, context, and examples, not a
# parser requirement of the portable Agent Skills specification. audit-skill.sh
# section 4 enforces the same rules in bash; this Python mirror runs where the
# bash audit is skipped (Windows CI) so the gate has no platform hole.

_PRESENTATIONAL_TAGS = frozenset(
    "a b big br center code div em font hr i img li ol p pre small span strong "
    "table td th tr u ul".split()
)


def _semantic_regions(text: str) -> tuple[list[str], list[str]]:
    """Return (region names in order, defects) for one SKILL.md text.

    Frontmatter and fenced code are excluded. Defects are unbalanced or
    mis-nested tags, presentational tag names, and `## ` headings outside every
    region; an empty region list is itself a defect.
    """
    import re

    lines = text.splitlines()
    start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                start = i + 1
                break
    open_re = re.compile(r"^<([a-z][a-z0-9_-]*)>\s*$")
    close_re = re.compile(r"^</([a-z][a-z0-9_-]*)>\s*$")
    fence_re = re.compile(r"^(`{3,})(\s*\S+)?\s*$")
    stack: list[tuple[str, int]] = []
    regions: list[str] = []
    defects: list[str] = []
    in_fence, opener = False, 0
    for n in range(start, len(lines)):
        line = lines[n]
        m = fence_re.match(line)
        if m:
            ticks, info = len(m.group(1)), (m.group(2) or "").strip()
            if not in_fence:
                in_fence, opener = True, ticks
            elif not info and ticks >= opener:
                in_fence = False
            continue
        if in_fence:
            continue
        if m := open_re.match(line):
            name = m.group(1)
            if name in _PRESENTATIONAL_TAGS or len(name) < 2:
                defects.append(f"line {n + 1}: <{name}> is not a descriptive region name")
            stack.append((name, n + 1))
            regions.append(name)
            continue
        if m := close_re.match(line):
            name = m.group(1)
            if not stack:
                defects.append(f"line {n + 1}: </{name}> closes nothing")
            elif stack[-1][0] != name:
                defects.append(f"line {n + 1}: </{name}> closes <{stack[-1][0]}>")
                stack.pop()
            else:
                stack.pop()
            continue
        if not stack and line.startswith("## "):
            defects.append(f"line {n + 1}: H2 outside every region: {line.strip()[:50]}")
    defects.extend(f"<{name}> opened at line {ln} never closed" for name, ln in stack)
    if not regions:
        defects.append("no semantic XML regions")
    return regions, defects


# Every SKILL.md this repository ships or links, not only plugins/autorun/skills:
# the Codex `$ar` catalog skill, the pdf-extractor plugin skill, and the
# repo-internal maintainer skill are loaded by the same harnesses.
OTHER_SHIPPED_SKILLS = (
    PLUGIN_ROOT / ".codex-plugin" / "skills" / "ar",
    REPO_ROOT / "plugins" / "pdf-extractor" / "skills" / "pdf-extractor",
    AGENTS_SKILLS_ROOT / "autorun-maintainer",
)


def _every_shipped_skill_dir() -> list[Path]:
    return _packaged_skill_dirs() + list(OTHER_SHIPPED_SKILLS)


@pytest.mark.parametrize(
    "skill_dir",
    _every_shipped_skill_dir(),
    ids=lambda p: p.name if p.name != "ar" else "codex-ar",
)
def test_shipped_skill_body_uses_balanced_semantic_xml_regions(skill_dir):
    """SKILL-REQ004: regions present, balanced, descriptive, and every H2 inside one."""
    regions, defects = _semantic_regions((skill_dir / "SKILL.md").read_text(encoding="utf-8"))

    assert not defects, f"{skill_dir.name}/SKILL.md: " + "; ".join(defects)
    assert regions


def test_semantic_region_checker_reports_each_defect_kind():
    """The mirror must fail for the same reasons audit-skill.sh section 4 fails."""
    body = "---\nname: x\ndescription: y\n---\n# T\n\n"
    _, ok = _semantic_regions(body + "<purpose>\n## A\n```markdown\n<unclosed>\n## fenced\n```\n</purpose>\n")
    assert ok == []
    _, none = _semantic_regions(body + "## A\ntext\n")
    assert none == ["line 7: H2 outside every region: ## A", "no semantic XML regions"]
    _, nested = _semantic_regions(body + "<purpose>\n<workflow>\n</purpose>\n</workflow>\n")
    assert any("closes <workflow>" in d for d in nested)
    _, html = _semantic_regions(body + "<div>\n## A\n</div>\n")
    assert html == ["line 7: <div> is not a descriptive region name"]
    _, escaped = _semantic_regions(body + "<purpose>\ntext\n</purpose>\n\n## Outside\n")
    assert escaped == ["line 11: H2 outside every region: ## Outside"]


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


@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
def test_audit_script_exits_zero_on_a_structurally_clean_skill(tmp_path):
    """A release gate can only gate if a clean run is distinguishable by status."""
    skill = _write_skill(
        tmp_path,
        "clean-fixture-skill",
        "name: clean-fixture-skill\n"
        'description: Render a fixture report from a directory. Use when asked to'
        ' "render a fixture report" or "audit a fixture skill", for example `pytest`'
        " cases naming *.md inputs.",
        "# Clean Fixture Skill\n\n<workflow>\n\n## Workflow\n\n1. Read the input.\n2. Report.\n\n</workflow>\n",
    )

    result = _run_audit(str(skill))

    assert "❌ FAIL" not in result.stdout, result.stdout
    assert result.returncode == 0, result.stdout


@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
def test_audit_script_fails_a_body_without_semantic_xml_regions(tmp_path):
    """SKILL-REQ004 is a release gate, so a region-less body must be a FAIL, not a note."""
    skill = _write_skill(
        tmp_path,
        "plain-fixture-skill",
        "name: plain-fixture-skill\n"
        'description: Render a fixture report. Use when asked to "render a fixture report".',
        "# Plain Fixture Skill\n\n## Workflow\n\n1. Read the input.\n",
    )

    result = _run_audit(str(skill))

    assert "Semantic XML regions missing or unbalanced" in result.stdout, result.stdout
    assert result.returncode == 1


@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
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


@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
def test_audit_script_help_documents_its_exit_codes():
    """The exit contract is only usable if `--help` states it."""
    result = _run_audit("--help")

    assert result.returncode == 0, result.stderr
    assert "zero structural FAILs" in result.stdout
    assert "audit-skill.sh <skill-path>" in result.stdout


@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
def test_audit_script_exits_nonzero_on_missing_directory(tmp_path):
    """An unusable argument must not be reported with the clean-run status."""
    result = _run_audit(str(tmp_path / "no-such-skill"))

    assert result.returncode == 1


@pytest.mark.skipif(
    not POSIX_AUDIT_AVAILABLE,
    reason="audit-skill.sh requires a POSIX shell; Windows CI has no WSL distribution",
)
def test_audit_resolves_repository_owned_notes_from_git_root(tmp_path):
    """A skill-local relative path may point at a note owned by its repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    notes = repo / "notes"
    notes.mkdir()
    (notes / "actual.md").write_text("repository note", encoding="utf-8")
    (repo / "plugins").mkdir()

    skill = _write_skill(
        repo / "plugins",
        "repo-note-skill",
        "name: repo-note-skill\n"
        'description: Read repository notes. Use when asked to "read repository notes".',
        "# Repo Note Skill\n\n<workflow>\n\nRead `notes/actual.md`.\n\n</workflow>\n",
    )

    result = _run_audit(str(skill))

    assert "SKILL.md names files that do not exist" not in result.stdout
    assert result.returncode == 0, result.stdout


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


@pytest.mark.parametrize("name", REPO_INTERNAL_SKILLS)
def test_repo_internal_skill_is_not_packaged_for_users(name):
    """A repo-internal skill must not sit under the plugin's skills/ directory.

    Both manifests declare `"skills": "./skills/"` as a directory and
    build_support.py maps skills/ into the wheel, so anything placed there
    reaches users through the Claude plugin, the Codex bundle, the
    Gemini-family extensions, and the capability snapshot at once. Location is
    the whole packaging decision; there is no per-skill exclude.
    """
    assert not (SKILLS_ROOT / name).exists(), (
        f"{name} is repo-internal but sits in the packaged skills directory. "
        f"Move it: git mv plugins/autorun/skills/{name} .agents/skills/{name}"
    )
    assert (AGENTS_SKILLS_ROOT / name / "SKILL.md").is_file(), (
        f"{name} must live at .agents/skills/{name}/SKILL.md, the shared root "
        "Codex, OpenCode, and Antigravity read directly."
    )


@pytest.mark.parametrize("name", REPO_INTERNAL_SKILLS)
def test_repo_internal_skill_is_linked_into_claude_skills(name):
    """Claude Code reads .claude/skills/ only, so the shared copy needs a link.

    The link is per skill directory, never the skills/ directory itself:
    Claude Code stops loading skills when that directory is a symlink
    (anthropics/claude-code#38051).
    """
    link = CLAUDE_SKILLS_ROOT / name
    assert link.is_symlink(), (
        f".claude/skills/{name} must be a symlink so one copy serves every "
        f"harness. Create it: ln -s ../../.agents/skills/{name} .claude/skills/{name}"
    )
    assert not CLAUDE_SKILLS_ROOT.is_symlink(), (
        ".claude/skills/ itself must be a real directory; Claude Code stops "
        "loading skills when it is a symlink (anthropics/claude-code#38051)"
    )
    assert link.resolve() == (AGENTS_SKILLS_ROOT / name).resolve(), (
        f".claude/skills/{name} must resolve to .agents/skills/{name}, "
        f"got {link.resolve()}"
    )


def test_autorun_maintainer_skill_covers_current_harnesses_and_scoped_restarts():
    """Maintainer guidance should reflect current multi-harness install safety."""
    text = (AGENTS_SKILLS_ROOT / "autorun-maintainer" / "SKILL.md").read_text(encoding="utf-8")

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


# ─── Explicit-only command metadata pilot ───────────────────────────────────
#
# Claude Code loads every command document's description into model context and
# lets Claude invoke it (https://code.claude.com/docs/en/slash-commands:
# "disable-model-invocation ... Set to true to prevent Claude from
# automatically loading this skill. Use for workflows you want to trigger
# manually with /name. Default: false").
#
# The commands below cross a session boundary or halt work outright. A model
# should not reach for them on its own, and the user should still be able to
# type them. Piloted on this set first; expand only with a completion and
# behavior receipt per harness.

COMMANDS_ROOT = PLUGIN_ROOT / "commands"

MANUAL_ONLY_COMMANDS = (
    "estop",
    "sos",
    "globalno",
    "globalok",
    "globalclear",
)


def _frontmatter(path: Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


@pytest.mark.parametrize("stem", MANUAL_ONLY_COMMANDS)
def test_manual_only_command_is_not_model_invocable(stem):
    path = COMMANDS_ROOT / f"{stem}.md"

    assert path.is_file(), f"{stem}.md is missing"
    assert _frontmatter(path).get("disable-model-invocation") == "true"


@pytest.mark.parametrize("stem", MANUAL_ONLY_COMMANDS)
def test_manual_only_command_stays_visible_in_the_slash_menu(stem):
    """Explicit-only removes model invocation, not human completion. Hiding it
    from the menu too would delete the command from the user's reach."""
    fields = _frontmatter(COMMANDS_ROOT / f"{stem}.md")

    assert fields.get("user-invocable", "true") != "false"
    assert fields.get("description")


def test_the_pilot_stays_small_and_excludes_ordinary_controls():
    """A pilot that quietly grows into "hide everything" removes capability
    instead of removing context. Ordinary policy and status commands must stay
    model-invocable."""
    for stem in ("status", "allow", "find", "justify", "ok", "no", "blocks"):
        fields = _frontmatter(COMMANDS_ROOT / f"{stem}.md")
        assert fields.get("disable-model-invocation") is None, stem

    piloted = [
        path.stem
        for path in sorted(COMMANDS_ROOT.glob("*.md"))
        if _frontmatter(path).get("disable-model-invocation") == "true"
    ]
    assert piloted == sorted(MANUAL_ONLY_COMMANDS)
