"""Materializing a Gemini-family extension, and keeping it fresh.

These harnesses install an extension *directory* rather than loading a plugin in
place, so autorun builds one and publishes it. Two harness bugs shape the layout
and are asserted here rather than described: Gemini hardcodes
``<ext>/hooks/hooks.json`` and ignores the manifest's own ``hooks`` field
(#14449), and Claude's loader scans the marketplace source ``hooks/`` and
rejects Gemini event names there, disabling every hook (#24115).

The staging test also pins the fix for the second skill writer: the installer
this replaces copied the plugin's whole ``skills/`` directory into the extension
as part of materialization, independently of the skill-route system, so a
harness reading the shared root got every skill twice — and those copies carried
no ownership marker, which made them unprunable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.extension import (  # noqa: E402
    Manifest,
    receipt_names_source,
    refreshable,
    stage_extension,
)


@pytest.fixture
def template(tmp_path: Path) -> Path:
    root = tmp_path / "gemini_template"
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (root / "hooks" / "hooks.json").write_text('{"PreToolUse": []}', encoding="utf-8")
    return root


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugins" / "ar"
    (root / "commands").mkdir(parents=True)
    (root / "commands" / "go.md").write_text("go\n", encoding="utf-8")
    for name in ("commit", "philosophy"):
        (root / "skills" / name).mkdir(parents=True)
        (root / "skills" / name / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return root


MANIFEST = Manifest(name="ar", version="1.0.0rc1", description="autorun")


# ─── The manifest shape a real install produced ─────────────────────────────


def test_hooks_is_a_path_string_not_an_object():
    """Gemini ignores this field and reads <ext>/hooks/hooks.json regardless
    (#14449), so it is written for correctness while the files go where the
    harness actually looks."""
    document = MANIFEST.as_document()

    assert document["hooks"] == "./hooks/hooks.json"
    assert isinstance(document["hooks"], str)


def test_the_generated_manifest_matches_the_installed_one():
    """The strongest available check: the extension on this machine was
    materialized by the installer being replaced."""
    live_path = Path.home() / ".qwen" / "extensions" / "ar" / "gemini-extension.json"
    if not live_path.is_file():
        pytest.skip("no materialized qwen extension on this machine")
    live = json.loads(live_path.read_text(encoding="utf-8"))

    ours = Manifest(
        name=live["name"], version=live["version"], description=live.get("description", "")
    ).as_document()

    for field in ("name", "version", "description", "contextFileName",
                  "commands", "skills", "hooks"):
        assert ours[field] == live[field], field


# ─── Staging ────────────────────────────────────────────────────────────────


def test_hook_code_lands_where_the_harness_hardcodes_its_lookup(template, plugin, tmp_path):
    """Bug #14449: the manifest's hooks field is ignored, so
    ${extensionPath}/hooks/hook_entry.py has to resolve."""
    staged = stage_extension(template, plugin, tmp_path / "staged", MANIFEST)

    assert (staged / "hooks" / "hook_entry.py").is_file()
    assert (staged / "hooks" / "hooks.json").is_file()


def test_a_shared_root_harness_gets_no_native_skill_copy(template, plugin, tmp_path):
    """The defect this closes: one collision flipped every plugin to native and
    the extension received all 17 skills as well as the shared root."""
    staged = stage_extension(template, plugin, tmp_path / "staged", MANIFEST)

    assert not (staged / "skills").exists()


def test_a_native_route_receives_exactly_the_named_skills(template, plugin, tmp_path):
    """Passing the skills in, rather than reading the plugin's directory, makes
    the route the single authority over where a skill lands."""
    staged = stage_extension(
        template, plugin, tmp_path / "staged", MANIFEST,
        skills={"commit": plugin / "skills" / "commit"},
    )

    assert (staged / "skills" / "commit" / "SKILL.md").is_file()
    assert not (staged / "skills" / "philosophy").exists(), "only what was asked for"


def test_commands_are_always_staged(template, plugin, tmp_path):
    staged = stage_extension(template, plugin, tmp_path / "staged", MANIFEST)

    assert (staged / "commands" / "go.md").is_file()


def test_the_manifest_filename_is_the_one_the_harness_reads(template, plugin, tmp_path):
    """Fixed, never derived from the plugin name. Every Gemini-family CLI looks
    for `gemini-extension.json` whatever the extension is called, so writing
    `<name>-extension.json` yields a directory the harness loads nothing from —
    no commands, no hooks, no skills — and reports no error, because as far as
    it is concerned there is no extension there.

    This shipped. The document was asserted against the live install while the
    filename was not, so every field matched and the file was unreadable.
    """
    from autorun.installer.extension import MANIFEST_NAME

    staged = stage_extension(template, plugin, tmp_path / "staged", MANIFEST)

    assert MANIFEST_NAME == "gemini-extension.json"
    assert (staged / MANIFEST_NAME).is_file()
    assert not (staged / "ar-extension.json").exists(), "never derived from the name"

    document = json.loads((staged / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert document["name"] == "ar"
    assert document["version"] == "1.0.0rc1"


def test_the_manifest_filename_matches_what_is_installed_on_this_machine():
    """The strongest check available: compare against a real materialized
    extension rather than against our own constant."""
    from autorun.installer.extension import MANIFEST_NAME

    installed = Path.home() / ".qwen" / "extensions" / "ar"
    if not installed.is_dir():
        pytest.skip("no materialized qwen extension on this machine")

    assert (installed / MANIFEST_NAME).is_file(), (
        f"the harness reads {MANIFEST_NAME}; found "
        f"{sorted(p.name for p in installed.glob('*.json'))}"
    )


def test_staging_never_touches_the_destination(template, plugin, tmp_path):
    """Publication is a separate atomic step, so a failure part-way through
    staging leaves the installed extension alone."""
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / "sentinel").write_text("untouched\n", encoding="utf-8")

    stage_extension(template, plugin, tmp_path / "staged", MANIFEST)

    assert (installed / "sentinel").read_text() == "untouched\n"


# ─── Refresh eligibility needs positive evidence ────────────────────────────


def test_an_extension_with_no_marker_and_no_receipt_is_the_users(tmp_path, template):
    installed = tmp_path / "installed"
    installed.mkdir()

    assert refreshable(installed, template) is False


def test_the_harness_receipt_names_our_template(tmp_path, template):
    """An extension keeps running from its files after the CLI is uninstalled,
    so refresh must not depend on the CLI being present."""
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / ".qwen-extension-install.json").write_text(
        json.dumps({"source": str(template), "type": "local"}), encoding="utf-8"
    )

    assert receipt_names_source(installed, template) is True
    assert refreshable(installed, template) is True


def test_a_differently_rooted_checkout_cannot_claim_someone_elses_extension(tmp_path, template):
    other = tmp_path / "other_template"
    other.mkdir()
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / ".qwen-extension-install.json").write_text(
        json.dumps({"source": str(template)}), encoding="utf-8"
    )

    assert refreshable(installed, other) is False


def test_a_path_sharing_a_prefix_is_not_a_match(tmp_path, template):
    """A prefix or name test would let a backup checkout claim the real one."""
    sibling = tmp_path / "gemini_template_backup"
    sibling.mkdir()
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / ".qwen-extension-install.json").write_text(
        json.dumps({"source": str(template)}), encoding="utf-8"
    )

    assert refreshable(installed, sibling) is False


def test_one_unreadable_receipt_does_not_hide_a_good_one(tmp_path, template):
    installed = tmp_path / "installed"
    installed.mkdir()
    (installed / ".broken-extension-install.json").write_text("{not json", encoding="utf-8")
    (installed / ".qwen-extension-install.json").write_text(
        json.dumps({"source": str(template)}), encoding="utf-8"
    )

    assert refreshable(installed, template) is True


def test_our_own_ownership_marker_is_sufficient(tmp_path, template, plugin):
    from autorun.installer.fs import publish_tree

    installed = tmp_path / "installed"
    publish_tree(plugin / "commands", installed, plugin="ar")

    assert refreshable(installed, template, plugin="ar") is True


def test_the_real_installed_extension_is_recognised_as_ours():
    """Validates the receipt logic against a directory a real install made."""
    installed = Path.home() / ".qwen" / "extensions" / "ar"
    template = (Path(__file__).resolve().parents[1] / "src" / "autorun" / "gemini_template")
    if not installed.is_dir() or not template.is_dir():
        pytest.skip("no materialized qwen extension on this machine")

    assert refreshable(installed, template) is True
