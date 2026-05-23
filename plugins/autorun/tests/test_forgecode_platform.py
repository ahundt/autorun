"""Tests for ForgeCode platform support (v0.11.0 / C5).

ForgeCode has no external hook system — integration is via:
  - markdown commands at ~/.forge/commands/*.md (YAML frontmatter:
    name + description only; body is the prompt)
  - AGENTS.md at ~/.forge/AGENTS.md (plain text, injected as custom
    instructions into agent context)

The Platform abstraction marks ForgeCode has_hooks=False and
schema_type="none" so the rest of the codebase treats it as
template-only without dispatching hook validation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autorun.platforms import PLATFORMS, get_platform


# ─── Registry presence ────────────────────────────────────────────────────────

def test_forgecode_platform_registered():
    p = get_platform("forgecode")
    assert p is not None
    assert p.name == "forgecode"
    assert p.display_name == "ForgeCode"
    assert p.binary == "forge"


def test_forgecode_has_no_hooks():
    assert PLATFORMS["forgecode"].has_hooks is False


def test_forgecode_schema_type_is_none():
    assert PLATFORMS["forgecode"].schema_type == "none"


def test_forgecode_template_dir_is_forgecode_template():
    assert PLATFORMS["forgecode"].template_dir == "forgecode_template"


def test_forgecode_install_fn_name():
    assert PLATFORMS["forgecode"].install_fn_name == "_install_for_forgecode"


# ─── Detection ────────────────────────────────────────────────────────────────

def test_forgecode_detected_via_explicit_cli_type():
    from autorun.config import detect_cli_type
    assert detect_cli_type({"cli_type": "forgecode"}) == "forgecode"


def test_forgecode_detected_via_FORGE_CONFIG_env(monkeypatch):
    for k in ("GEMINI_SESSION_ID", "GEMINI_PROJECT_DIR", "GEMINI_CLI",
              "CODEX_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FORGE_CONFIG", "/tmp/forge")
    from autorun.config import detect_cli_type
    assert detect_cli_type() == "forgecode"


def test_forgecode_detected_via_transcript_path():
    from autorun.config import detect_cli_type
    assert detect_cli_type({"transcript_path": "/home/u/.forge/log.md"}) == "forgecode"


# ─── Template directory exists with expected commands ────────────────────────

def _template_root() -> Path:
    return (
        Path(__file__).parent.parent
        / "src" / "autorun" / "forgecode_template"
    )


def test_forgecode_template_directory_exists():
    root = _template_root()
    assert root.is_dir(), f"ForgeCode template missing at {root}"
    assert (root / "commands").is_dir()
    assert (root / "AGENTS.md").is_file()


@pytest.mark.parametrize("cmd_name", [
    "ar-go", "ar-st", "ar-allow", "ar-find", "ar-commit", "ar-ph",
])
def test_forgecode_command_files_exist(cmd_name):
    root = _template_root()
    cmd_file = root / "commands" / f"{cmd_name}.md"
    assert cmd_file.is_file(), f"missing command template {cmd_file}"


@pytest.mark.parametrize("cmd_name", [
    "ar-go", "ar-st", "ar-allow", "ar-find", "ar-commit", "ar-ph",
])
def test_forgecode_command_has_required_frontmatter(cmd_name):
    """Per forge's crates/forge_domain/src/command.rs:11-21 the YAML
    frontmatter must contain `name` and `description` (extras silently
    dropped)."""
    root = _template_root()
    cmd_file = root / "commands" / f"{cmd_name}.md"
    text = cmd_file.read_text(encoding="utf-8")
    assert text.startswith("---"), f"{cmd_name}: missing YAML frontmatter"
    # Split on the second `---` separator
    parts = text.split("---", 2)
    assert len(parts) >= 3, f"{cmd_name}: incomplete frontmatter block"
    frontmatter = parts[1]
    assert "name:" in frontmatter, f"{cmd_name}: missing 'name:' field"
    assert "description:" in frontmatter, f"{cmd_name}: missing 'description:' field"


def test_forgecode_agents_md_mentions_safety_guidance():
    agents = (_template_root() / "AGENTS.md").read_text(encoding="utf-8")
    # ForgeCode lacks external hook enforcement, so AGENTS.md provides
    # advisory safety guidance to the agent. Look for the key advisories.
    assert "safety" in agents.lower() or "guard" in agents.lower() or "/ar:" in agents
