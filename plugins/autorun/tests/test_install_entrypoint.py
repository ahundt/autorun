"""Production installer facade: one resolved path into the manifest engine."""

from __future__ import annotations

import json
import os
import subprocess
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[3]


def _absolute_link_extension_runner(installed: Path, calls: list[tuple[str, ...]]):
    """Model the Gemini/Qwen local install contract without either CLI."""
    import shutil

    def run(argv):
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("gemini", "extensions", "uninstall"):
            if installed.is_symlink():
                installed.unlink()
            elif installed.exists():
                shutil.rmtree(installed)
        elif call[:3] == ("gemini", "extensions", "install"):
            source = Path(call[3]).resolve()
            installed.parent.mkdir(parents=True, exist_ok=True)
            installed.symlink_to(source, target_is_directory=True)
            (source / ".gemini-extension-install.json").write_text(
                json.dumps({"source": str(source), "type": "local"}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def _copy_extension_runner(
    installed: Path,
    calls: list[tuple[str, ...]],
    failures: list[bool],
):
    """Model current Gemini/Qwen local installs, which copy with a receipt."""
    import shutil

    from autorun.installer.fs import OWNED_MARKER_NAME

    def run(argv):
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("gemini", "extensions", "uninstall"):
            if installed.exists():
                shutil.rmtree(installed)
        elif call[:3] == ("gemini", "extensions", "install"):
            if failures and failures.pop(0):
                return subprocess.CompletedProcess(argv, 1, "", "registration failed")
            if installed.exists():
                return subprocess.CompletedProcess(argv, 1, "", "already installed")
            source = Path(call[3]).resolve()
            shutil.copytree(
                source,
                installed,
                ignore=lambda _root, names: (
                    [OWNED_MARKER_NAME] if OWNED_MARKER_NAME in names else []
                ),
            )
            (installed / ".gemini-extension-install.json").write_text(
                json.dumps({"source": str(source), "type": "local"}),
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def _agy_copy_runner(
    home: Path,
    calls: list[tuple[str, ...]],
    *,
    reject_native: bool = False,
):
    """Model Agy 1.1.7's copied plugin and shared import manifest."""
    import shutil

    from autorun.installer.fs import OWNED_MARKER_NAME

    installed = home / ".gemini" / "config" / "plugins" / "ar"
    manifest = home / ".gemini" / "config" / "import_manifest.json"

    def imports():
        if not manifest.is_file():
            return []
        return json.loads(manifest.read_text(encoding="utf-8")).get("imports", [])

    def write_imports(rows):
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"imports": rows}), encoding="utf-8")

    def run(argv):
        call = tuple(argv)
        calls.append(call)
        if call[:3] == ("agy", "plugin", "validate") and reject_native:
            return subprocess.CompletedProcess(argv, 1, "", "invalid native bundle")
        if call[:3] in {
            ("agy", "plugin", "install"),
            ("agy", "plugin", "import"),
        }:
            source = Path(call[3]).resolve() if call[2] == "install" else None
            imported = home / ".gemini" / "extensions" / "ar"
            if installed.exists():
                shutil.rmtree(installed)
            if source is not None:
                shutil.copytree(
                    source,
                    installed,
                    ignore=lambda _root, names: (
                        [OWNED_MARKER_NAME] if OWNED_MARKER_NAME in names else []
                    ),
                )
            elif imported.is_dir():
                shutil.copytree(imported, installed, symlinks=True)
            rows = [row for row in imports() if row.get("name") != "ar"]
            rows.append(
                {
                    "name": "ar",
                    "source": "antigravity" if source is not None else "gemini-cli",
                    "components": ["skills", "commands", "hooks"],
                }
            )
            write_imports(rows)
        elif call[:3] == ("agy", "plugin", "uninstall"):
            if installed.exists():
                shutil.rmtree(installed)
            write_imports([row for row in imports() if row.get("name") != "ar"])
        elif call[:3] == ("agy", "plugin", "list"):
            names = "\n".join(str(row.get("name", "")) for row in imports())
            return subprocess.CompletedProcess(argv, 0, names, "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # Both names: Path.home() resolves through os.path.expanduser, which reads
    # USERPROFILE on Windows and HOME elsewhere and never consults the other,
    # so setting one isolates this test on one platform and lets it write the
    # real home on the other.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # Keep the default harness roots inside this fixture.  OpenCode honours an
    # explicit XDG_CONFIG_HOME, which CI may export globally; relocation itself
    # is covered by the discovery tests.
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
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


def test_empty_home_uninstall_is_a_true_no_op_even_when_every_cli_is_on_path(
    monkeypatch, isolated
):
    from autorun.installer import entrypoint

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    before = tuple(isolated.rglob("*"))
    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")

    assert entrypoint.uninstall_plugins("ar") == 0

    assert calls == []
    assert tuple(isolated.rglob("*")) == before


def test_no_uv_pdf_install_and_uninstall_are_symmetric(
    monkeypatch, isolated, tmp_path
):
    from autorun.installer import entrypoint

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    package = tmp_path / "pdf-extractor"
    package.mkdir()
    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)

    installed = entrypoint._editable_package(
        package,
        "pdf-extractor CLI",
        distribution="pdf-extractor",
    )
    assert installed.ok
    receipt = entrypoint._package_receipt("pdf-extractor")
    assert receipt.is_file()

    assert entrypoint.uninstall_plugins("pdf-extractor") == 0
    assert calls[0] == (
        entrypoint.sys.executable,
        "-m",
        "pip",
        "install",
        "--editable",
        str(package),
    )
    assert calls[-1] == (
        entrypoint.sys.executable,
        "-m",
        "pip",
        "uninstall",
        "-y",
        "pdf-extractor",
    )
    assert not receipt.exists()


def test_uninstall_keeps_stable_publication_lock_files(tmp_path):
    from autorun.installer import teardown
    from autorun.installer.fs import INSTALL_LOCK_NAME

    root = tmp_path / "config"
    root.mkdir()
    lock = root / INSTALL_LOCK_NAME
    lock.touch()

    result = teardown.teardown(
        (root,),
        pid=lambda: None,
        stop=lambda _pid: None,
        clean=lambda: None,
    )

    assert lock.is_file()
    assert not any("lock" in line.lower() for line in result.describe())


def test_release_lookup_includes_rcs_only_for_an_installed_prerelease(monkeypatch):
    from autorun.installer import entrypoint

    releases = [
        {"tag_name": "v1.0.0", "draft": False, "prerelease": False},
        {"tag_name": "v1.1.0rc1", "draft": False, "prerelease": True},
        {"tag_name": "v9.0.0", "draft": True, "prerelease": False},
    ]

    class Response(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setattr(
        entrypoint.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(json.dumps(releases).encode()),
    )

    assert entrypoint._latest_version("1.0.0rc1") == "1.1.0rc1"
    assert entrypoint._latest_version("1.0.0") == "1.0.0"


def test_custom_claude_flavor_uses_the_portable_commands_and_agents_layout(monkeypatch, isolated):
    from autorun.installer import entrypoint

    monkeypatch.setattr(
        entrypoint,
        "_run",
        lambda argv: subprocess.CompletedProcess(
            argv,
            0,
            "ar\n" if tuple(argv)[:3] == ("agy", "plugin", "list") else "",
            "",
        ),
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


def test_public_gemini_install_is_noninteractive_without_mutating_parent_env(
    monkeypatch, isolated
):
    """The public entrypoint sends consent and trust to the child only."""
    from autorun.installer import entrypoint

    calls = []

    def record(argv, *, env=None):
        calls.append((tuple(argv), dict(env or {})))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.delenv("GEMINI_CLI_TRUST_WORKSPACE", raising=False)
    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: f"/bin/{name}" if name in {"gemini", "uv"} else None,
    )

    assert (
        entrypoint.install_plugins(
            "ar", gemini_only=True, conductor=False, tool=False
        )
        == 0
    )
    registration_call, child_env = next(
        item
        for item in calls
        if item[0][:3] == ("gemini", "extensions", "install")
    )
    assert registration_call[-1] == "--consent"
    assert child_env == {"GEMINI_CLI_TRUST_WORKSPACE": "true"}
    assert "GEMINI_CLI_TRUST_WORKSPACE" not in os.environ


@pytest.mark.parametrize("legacy_empty_marker", [False, True])
def test_receipt_owned_legacy_gemini_refreshes_without_binary(
    monkeypatch, isolated, legacy_empty_marker
):
    """Port the pre-redesign receipt adoption and missing-CLI refresh contract."""
    from autorun.installer import discovery, entrypoint, fs, steps

    plugin = discovery.plugin_dir(REPO, "ar")
    assert plugin is not None
    template = (
        discovery.plugin_runtime_root(plugin) / steps.GEMINI_TEMPLATE_SUBDIR
    )
    installed = isolated / ".gemini" / "extensions" / "ar"
    installed.mkdir(parents=True)
    (installed / "stale-hook.py").write_text("old\n", encoding="utf-8")
    if legacy_empty_marker:
        (installed / fs.OWNED_MARKER_NAME).write_text(
            json.dumps(
                fs.TreeManifest(plugin="ar", files={}).as_payload()
            ),
            encoding="utf-8",
        )
    (installed / ".gemini-extension-install.json").write_text(
        json.dumps({"source": str(template.resolve()), "type": "local"}),
        encoding="utf-8",
    )
    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/uv" if name == "uv" else None,
    )

    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 0

    marker = fs.read_marker(installed)
    assert marker is not None and marker.plugin == "ar"
    assert not (installed / "stale-hook.py").exists()
    hooks = json.loads((installed / "hooks" / "hooks.json").read_text())
    commands = [
        hook["command"]
        for groups in hooks["hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
    ]
    assert commands
    assert all("uv run" in command for command in commands)
    assert all(str(plugin) in command for command in commands)
    assert all("autorun --cli" not in command for command in commands)
    assert not any(call and call[0] == "gemini" for call in calls)


@pytest.mark.parametrize("uninstall_with_binary", [True, False])
def test_forced_gemini_registration_source_survives_native_absolute_link(
    monkeypatch, isolated, uninstall_with_binary
):
    """Model Gemini 0.28's absolute-link install through the public facade."""
    from autorun.installer import entrypoint

    installed = isolated / ".gemini" / "extensions" / "ar"
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run",
        _absolute_link_extension_runner(installed, calls),
    )
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/gemini" if name == "gemini" else None,
    )

    assert entrypoint.install_plugins(
        "ar",
        gemini_only=True,
        conductor=False,
        tool=False,
        force=True,
    ) == 0

    install_call = next(
        call for call in calls if call[:3] == ("gemini", "extensions", "install")
    )
    registration_source = Path(install_call[3]).resolve()
    assert registration_source.is_dir()
    assert registration_source.is_relative_to(
        Path(os.environ["AUTORUN_HOME"]).resolve()
    )
    assert installed.is_symlink()
    assert (installed / "hooks" / "hooks.json").is_file()
    receipt = json.loads(
        (installed / ".gemini-extension-install.json").read_text(encoding="utf-8")
    )
    assert Path(receipt["source"]).resolve() == registration_source

    calls.clear()
    if not uninstall_with_binary:
        monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)
    assert entrypoint.uninstall_plugins("ar") == 0
    assert any(call and call[0] == "gemini" for call in calls) is uninstall_with_binary
    assert not installed.exists() and not installed.is_symlink()
    assert not registration_source.exists()


def test_native_copy_refresh_is_claimed_and_rolls_back_on_registration_failure(
    monkeypatch, isolated
):
    """A copied extension survives stale sweeping and failed forced refresh."""
    from autorun.installer import entrypoint, extension, fs

    installed = isolated / ".gemini" / "extensions" / "ar"
    calls: list[tuple[str, ...]] = []
    failures: list[bool] = []
    monkeypatch.setattr(
        entrypoint,
        "_run",
        _copy_extension_runner(installed, calls, failures),
    )
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/gemini" if name == "gemini" else None,
    )

    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 0
    original = fs.scan_tree(installed)
    receipt = (installed / ".gemini-extension-install.json").read_bytes()

    calls.clear()
    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 0
    assert not any(call[:3] == ("gemini", "extensions", "uninstall") for call in calls)
    assert fs.scan_tree(installed) == original

    monkeypatch.setattr(
        extension,
        "materialization_matches_source",
        lambda _installed, _source: False,
    )
    failures[:] = [True, True]
    calls.clear()
    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 1
    assert any(call[:3] == ("gemini", "extensions", "uninstall") for call in calls)
    assert fs.scan_tree(installed) == original
    assert (installed / ".gemini-extension-install.json").read_bytes() == receipt

    calls.clear()
    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 0
    assert any(call[:3] == ("gemini", "extensions", "uninstall") for call in calls)
    assert any(call[:3] == ("gemini", "extensions", "install") for call in calls)
    refreshed_receipt = json.loads(
        (installed / ".gemini-extension-install.json").read_text(encoding="utf-8")
    )
    assert Path(refreshed_receipt["source"]).resolve().is_relative_to(
        Path(os.environ["AUTORUN_HOME"]).resolve()
    )


def test_antigravity_uses_native_config_receipt_and_uninstalls_only_its_entry(
    monkeypatch, isolated
):
    """Agy 1.1.7 paths and import-manifest ownership drive public uninstall."""
    from autorun.installer import entrypoint, fs

    manifest = isolated / ".gemini" / "config" / "import_manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "imports": [
                    {
                        "name": "foreign",
                        "source": "antigravity",
                        "components": ["commands"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(entrypoint, "_run", _agy_copy_runner(isolated, calls))
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/agy" if name == "agy" else None,
    )

    assert entrypoint.install_plugins(
        "ar", antigravity_only=True, conductor=False, tool=False
    ) == 0
    installed = isolated / ".gemini" / "config" / "plugins" / "ar"
    assert installed.is_dir()
    assert fs.read_marker(installed) is not None
    assert ("agy", "plugin", "validate", str(Path(os.environ["AUTORUN_HOME"]) / "installer" / "extension-sources" / "antigravity" / "ar")) in calls
    assert ("agy", "plugin", "list") in calls

    calls.clear()
    assert entrypoint.uninstall_plugins("ar") == 0
    assert ("agy", "plugin", "uninstall", "ar") in calls
    assert not installed.exists()
    rows = json.loads(manifest.read_text(encoding="utf-8"))["imports"]
    assert [row["name"] for row in rows] == ["foreign"]


def test_antigravity_import_fallback_requires_and_tracks_owned_gemini(
    monkeypatch, isolated
):
    """Importer compatibility cannot adopt an unrelated same-name extension."""
    from autorun.installer import entrypoint, fs

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        entrypoint,
        "_run",
        _agy_copy_runner(isolated, calls, reject_native=True),
    )
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/agy" if name == "agy" else None,
    )
    assert entrypoint.install_plugins(
        "ar", antigravity_only=True, conductor=False, tool=False
    ) == 1
    assert not any(call[:3] == ("agy", "plugin", "import") for call in calls)

    gemini_target = isolated / ".gemini" / "extensions" / "ar"
    gemini_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        entrypoint,
        "_run",
        _copy_extension_runner(gemini_target, gemini_calls, []),
    )
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/gemini" if name == "gemini" else None,
    )
    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 0
    assert fs.read_marker(gemini_target) is not None

    calls.clear()
    monkeypatch.setattr(
        entrypoint,
        "_run",
        _agy_copy_runner(isolated, calls, reject_native=True),
    )
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/agy" if name == "agy" else None,
    )
    assert entrypoint.install_plugins(
        "ar", antigravity_only=True, conductor=False, tool=False
    ) == 0
    agy_target = isolated / ".gemini" / "config" / "plugins" / "ar"
    assert ("agy", "plugin", "import", "gemini") in calls
    assert fs.read_marker(agy_target) is not None

    calls.clear()
    assert entrypoint.uninstall_plugins("ar") == 0
    assert ("agy", "plugin", "uninstall", "ar") in calls
    assert not agy_target.exists()


@pytest.mark.parametrize("uninstall_with_binary", [True, False])
def test_user_edit_preserves_native_link_and_registration_source(
    monkeypatch, isolated, uninstall_with_binary
):
    """Uninstall refuses an edited source with or without the native CLI."""
    from autorun.installer import entrypoint

    installed = isolated / ".gemini" / "extensions" / "ar"
    calls = []
    monkeypatch.setattr(
        entrypoint,
        "_run",
        _absolute_link_extension_runner(installed, calls),
    )
    monkeypatch.setattr(
        entrypoint.shutil,
        "which",
        lambda name: "/usr/bin/gemini" if name == "gemini" else None,
    )
    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False, force=True
    ) == 0
    registration_source = installed.resolve()
    user_file = installed / "keep-user-edit.txt"
    user_file.write_text("mine\n", encoding="utf-8")

    calls.clear()
    if not uninstall_with_binary:
        monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)
    assert entrypoint.uninstall_plugins("ar") == 0

    assert not any(call and call[0] == "gemini" for call in calls)
    assert installed.is_symlink()
    assert registration_source.is_dir()
    assert user_file.read_text(encoding="utf-8") == "mine\n"


def test_foreign_receipt_cannot_select_or_replace_missing_gemini(
    monkeypatch, isolated, tmp_path
):
    from autorun.installer import entrypoint

    installed = isolated / ".gemini" / "extensions" / "ar"
    installed.mkdir(parents=True)
    sentinel = installed / "keep.txt"
    sentinel.write_text("mine\n", encoding="utf-8")
    (installed / ".gemini-extension-install.json").write_text(
        json.dumps({"source": str(tmp_path / "sibling" / "gemini_template")}),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)

    assert entrypoint.install_plugins(
        "ar", gemini_only=True, conductor=False, tool=False
    ) == 1
    assert sentinel.read_text() == "mine\n"


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


def test_guidance_resolves_from_an_artifact_local_template(tmp_path):
    import json

    from autorun.installer.entrypoint import _guidance

    plugin = tmp_path / "autorun"
    metadata = plugin / ".claude-plugin"
    metadata.mkdir(parents=True)
    (metadata / "plugin.json").write_text(json.dumps({"name": "ar"}))
    template = plugin / "forgecode_template" / "AGENTS.md"
    template.parent.mkdir(parents=True)
    template.write_text("artifact guidance")
    platform = SimpleNamespace(
        name="forgecode", memory_template="forgecode_template/AGENTS.md"
    )

    assert _guidance(plugin, (platform,), ()) == {"forgecode": "artifact guidance"}


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
    monkeypatch.setattr(
        entrypoint,
        "_uv_tool_installed",
        lambda package: package == "pdf-extractor",
    )

    assert entrypoint.uninstall_plugins("pdf-extractor") == 0
    assert agents.is_file() and hooks.is_file() and package.is_dir()
    assert ("uv", "tool", "uninstall", "pdf-extractor") in calls
    assert ("uv", "tool", "uninstall", "autorun") not in calls
