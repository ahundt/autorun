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
    assert "pkill -f" not in text
    assert "0.11.0" not in text
