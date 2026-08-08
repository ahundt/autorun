"""Production installer facade: one resolved path into the manifest engine."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar"))
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "state"))
    return home


def test_selection_comes_from_the_manifest_and_accepts_the_retired_name():
    from autorun.installer.entrypoint import parse_selection

    assert parse_selection(REPO, "all") == ("ar", "pdf-extractor")
    assert parse_selection(REPO, "autorun") == ("ar",)
    with pytest.raises(ValueError, match="unknown plugin"):
        parse_selection(REPO, "ar,not-a-plugin")


def test_custom_cli_specs_override_same_named_config_without_dropping_others(
    monkeypatch,
):
    from autorun.installer.entrypoint import resolve_custom_harnesses

    monkeypatch.setitem(
        __import__("autorun.config", fromlist=["CONFIG"]).CONFIG,
        "custom_harnesses",
        ("one=codex:one:~/.one", "two=qwen:two:~/.two"),
    )
    resolved = resolve_custom_harnesses(("one=codex:new:~/.new",))

    assert [(item.name, item.binary) for item in resolved] == [
        ("one", "new"),
        ("two", "two"),
    ]


def test_install_all_for_codex_registers_only_the_codex_capable_plugin(monkeypatch, isolated):
    from autorun.installer import entrypoint

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")

    assert entrypoint.install_plugins("all", codex_only=True, conductor=False, tool=False) == 0
    assert (isolated / "plugins" / "ar" / ".codex-plugin" / "plugin.json").is_file()
    assert not (isolated / "plugins" / "pdf-extractor").exists()
    assert ("codex", "plugin", "add", "ar@personal") in calls
    assert not any("pdf-extractor@personal" in part for call in calls for part in call)


def test_source_independent_uninstall_withdraws_hooks_marketplace_and_package(monkeypatch, isolated, tmp_path):
    from autorun.installer import codex, entrypoint, fs, memory

    hooks = isolated / ".codex" / "hooks.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "Stop": [
                        codex.wrap(("uv run /gone/hooks/hook_entry.py --cli codex",)),
                        {"hooks": [{"type": "command", "command": "echo mine"}]},
                    ]
                }
            }
        )
    )
    agents = isolated / ".codex" / "AGENTS.md"
    memory.splice(agents, "old", memory.Block("codex-agents-md"))
    package = isolated / "plugins" / "ar"
    package.mkdir(parents=True)
    (package / fs.OWNED_MARKER_NAME).write_text(json.dumps(fs.TreeManifest.of(package, plugin="ar").as_payload()))
    market = isolated / ".agents" / "plugins" / "marketplace.json"
    market.parent.mkdir(parents=True)
    market.write_text(
        json.dumps(
            {
                "plugins": [
                    codex.marketplace_entry("ar", "./plugins/ar"),
                    codex.marketplace_entry("mine", "./plugins/mine"),
                ]
            }
        )
    )
    missing_root = tmp_path / "source-is-gone"
    monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: missing_root)
    monkeypatch.setattr(entrypoint, "_run", lambda argv: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)

    assert entrypoint.uninstall_plugins("ar") == 0
    assert not package.exists()
    assert "hook_entry.py" not in hooks.read_text()
    assert "echo mine" in hooks.read_text()
    assert not agents.exists() or "codex-agents-md" not in agents.read_text()
    assert [item["name"] for item in json.loads(market.read_text())["plugins"]] == ["mine"]


def test_custom_claude_flavor_uses_the_portable_commands_and_agents_layout(monkeypatch, isolated):
    from autorun.installer import entrypoint

    monkeypatch.setattr(
        entrypoint,
        "_run",
        lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")

    assert (
        entrypoint.install_plugins(
            "ar",
            custom_harnesses=("mine=claude:mine:~/.mine::Mine",),
            conductor=False,
        )
        == 0
    )
    assert list((isolated / ".mine" / "commands").glob("ar-*.md"))
    guidance = (isolated / ".mine" / "AGENTS.md").read_text()
    assert "Mine" in guidance and "ForgeCode" not in guidance


def test_custom_extension_flavor_registers_with_its_declared_binary(monkeypatch, isolated):
    from autorun.installer import entrypoint

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in {"mine", "uv"} else None,
    )

    assert (
        entrypoint.install_plugins(
            "ar",
            custom_harnesses=("mine=qwen:mine:~/.mine::Mine",),
            conductor=False,
        )
        == 0
    )
    assert any(call[:3] == ("mine", "extensions", "install") for call in calls)


def test_status_fails_when_an_owned_artifact_cannot_be_reconciled(monkeypatch, isolated):
    from autorun.installer import entrypoint, orchestrate
    from autorun.installer.fs import Decision, Verdict
    from autorun.installer.traversal import Mode

    monkeypatch.setattr(
        entrypoint,
        "_harnesses",
        lambda *args, **kwargs: ((SimpleNamespace(name="test"),), {}, (), ()),
    )
    monkeypatch.setattr(entrypoint, "_runtime_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        orchestrate,
        "preview",
        lambda **kwargs: orchestrate.Result(
            Mode.PREVIEW,
            decisions=(Decision(Verdict.KEEP, isolated / "skill", "user edit"),),
        ),
    )

    assert entrypoint.show_status() == 1


def test_a_declared_guidance_template_that_is_missing_is_an_install_error(tmp_path):
    from autorun.installer.entrypoint import _guidance

    plugin = tmp_path / "plugins" / "ar" / "src" / "autorun"
    plugin.mkdir(parents=True)
    platform = SimpleNamespace(name="forgecode", memory_template="missing/AGENTS.md")

    with pytest.raises(ValueError, match="guidance template not found"):
        _guidance(tmp_path, (platform,), ())


def test_partial_uninstall_of_pdf_preserves_every_autorun_artifact(monkeypatch, isolated):
    from autorun.installer import codex, entrypoint, memory

    agents = isolated / ".codex" / "AGENTS.md"
    memory.splice(agents, "ours", memory.Block("codex-agents-md"))
    hooks = isolated / ".codex" / "hooks.json"
    hooks.write_text(
        json.dumps({"hooks": {"Stop": [codex.wrap(("hook_entry.py",))]}}),
        encoding="utf-8",
    )
    package = isolated / "plugins" / "ar"
    package.mkdir(parents=True)
    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")

    assert entrypoint.uninstall_plugins("pdf-extractor") == 0
    assert agents.is_file() and hooks.is_file() and package.is_dir()
    assert ("uv", "tool", "uninstall", "pdf-extractor") in calls
    assert ("uv", "tool", "uninstall", "autorun") not in calls
