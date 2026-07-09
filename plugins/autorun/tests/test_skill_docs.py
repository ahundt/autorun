from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = PLUGIN_ROOT / "skills"


def _skill_entrypoints() -> list[Path]:
    """Return installed skill entrypoint docs, excluding reference material."""
    return sorted(SKILLS_ROOT.glob("*/SKILL.md"))


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
