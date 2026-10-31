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


def test_a_pip_installed_retired_pdf_distribution_is_kept_and_reported(monkeypatch, isolated):
    """An older autorun pip-installed a separate pdf package into *this*
    interpreter; removing it would remove our ``extract-pdfs``.

    The receipt only counts when its python is ``sys.executable``, so the
    retired package shares this environment's bin directory with
    ``autorun-ai``, whose ``pdf`` extra provides ``extract-pdfs`` too. pip
    removes every file in the retired package's RECORD, the shared script
    included, so ``pip uninstall -y pdf-extractor`` here costs the user a
    working command exactly as ``uv tool uninstall`` does. Keep it and say how
    to reclaim the disk.
    """
    from autorun.installer import entrypoint

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)

    receipt = entrypoint._package_receipt("pdf-extractor")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "distribution": "pdf-extractor",
                "installer": "pip",
                "python": str(Path(entrypoint.sys.executable).resolve()),
            }
        ),
        encoding="utf-8",
    )

    retired = entrypoint._retire_legacy_distributions("ar")

    assert calls == [], "pip uninstall here would delete this env's extract-pdfs"
    assert receipt.exists()
    assert [outcome.ok for outcome in retired] == [True]
    detail = retired[0].detail
    assert "extract-pdfs" in detail and "pip uninstall -y pdf-extractor" in detail
    assert "uv tool" not in detail, "a pip install is reclaimed with pip"


def test_removing_the_pdf_plugin_never_reaches_a_python_distribution(isolated):
    """``pdf-extractor`` is a harness plugin, not a package, and must stay one.

    Listing it in ``_PLUGIN_DISTRIBUTIONS`` would make ``--uninstall
    pdf-extractor`` remove a distribution that also carries autorun's own CLI.
    """
    from autorun.installer import entrypoint

    assert "pdf-extractor" not in entrypoint._PLUGIN_DISTRIBUTIONS
    # The distribution is "autorun-ai": PyPI prohibits the bare name. The
    # console script, the import package and the plugin id are unchanged.
    assert entrypoint._PLUGIN_DISTRIBUTIONS["ar"][0] == "autorun-ai"
    # Listing a name here means "retire it if that is safe". `autorun` shares
    # all three console scripts with the current distribution, so the uv-tool
    # sweep keeps it and says so; see
    # test_the_walk_never_uninstalls_a_distribution_sharing_one_of_our_scripts.
    assert set(entrypoint._PLUGIN_DISTRIBUTIONS["ar"][1:]) == {
        "autorun",
        "autorun-pdf-extractor",
        "pdf-extractor",
    }

    # Every distribution named here is fed to `_uv_tool_installed`, which
    # indexes `_UV_TOOL_SCRIPTS` directly. A missing key raises KeyError instead
    # of answering "this home does not own it" -- which is exactly what the
    # rename caused until "autorun-ai" was added. Retired names stay mapped too,
    # because a pre-rename install must remain classifiable.
    for distribution in entrypoint._PLUGIN_DISTRIBUTIONS["ar"]:
        assert distribution in entrypoint._UV_TOOL_SCRIPTS, (
            f"{distribution!r} is swept but has no console script mapped, so "
            f"_uv_tool_installed({distribution!r}) raises KeyError"
        )


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


def test_uninstall_force_reaches_the_walk(monkeypatch, isolated):
    """``--uninstall --force`` widens removal the way ``--install --force``
    widens publication; without the flag the walk runs unforced."""
    from autorun.installer import entrypoint, orchestrate
    from autorun.installer.traversal import Mode

    seen: list[bool] = []

    def fake_uninstall(**kwargs):
        seen.append(bool(kwargs.get("force")))
        return orchestrate.Result(Mode.UNINSTALL)

    monkeypatch.setattr(orchestrate, "uninstall", fake_uninstall)
    monkeypatch.setattr(entrypoint, "_runtime_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)

    assert entrypoint.uninstall_plugins("ar") == 0
    assert entrypoint.uninstall_plugins("ar", force=True) == 0
    assert seen == [False, True]


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


@pytest.mark.parametrize(
    "direct_url, expected",
    [
        (None, "autorun-ai==1.0.1"),
        (
            {"url": "https://github.com/ahundt/autorun.git", "vcs_info": {"vcs": "git", "commit_id": "abc"}},
            "git+https://github.com/ahundt/autorun.git@v1.0.1#subdirectory=plugins/autorun",
        ),
    ],
    ids=["index", "git"],
)
def test_update_installs_the_release_it_reported(monkeypatch, isolated, capsys, direct_url, expected):
    """``autorun --update`` compares against the newest GitHub release, so it
    must install that release: pinned on the index for an index install, the
    release tag for a git install (PEP 610 direct_url.json says which). It used
    to install the git repository with no ref, i.e. main's HEAD, and moved
    PyPI installs onto git."""
    from autorun import __main__ as entry
    from autorun.installer import entrypoint

    class Distribution:
        def read_text(self, name):
            assert name == "direct_url.json"
            return None if direct_url is None else json.dumps(direct_url)

    calls: list[tuple[str, ...]] = []

    def record(argv, **_kwargs):
        calls.append(tuple(str(part) for part in argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(entrypoint, "version", lambda _name: "1.0.0")
    monkeypatch.setattr(entrypoint, "distribution", lambda _name: Distribution(), raising=False)
    monkeypatch.setattr(entrypoint, "_latest_version", lambda _current: "1.0.1")
    monkeypatch.setattr(entrypoint, "_run", record)

    assert entry.main(["--update", "--update-method", "pip"]) == 0
    assert calls[0][-1] == expected, calls
    assert "update available: 1.0.0 -> 1.0.1" in capsys.readouterr().out


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


def test_uninstall_with_a_custom_harness_removes_what_install_put_there(monkeypatch, isolated):
    """``--uninstall --custom-harness SPEC`` used to drop the spec, exit 0, and
    leave every file the matching install had published under its root."""
    from autorun import __main__ as entry
    from autorun.installer import entrypoint

    monkeypatch.setattr(
        entrypoint,
        "_run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv, 0, "ar\n" if tuple(argv)[:3] == ("agy", "plugin", "list") else "", ""
        ),
    )
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")
    spec = "mine=claude:mine:~/.mine::Mine"
    root = isolated / ".mine"

    assert entry.main(["--install", "ar", "--custom-harness", spec, "--no-conductor"]) == 0
    assert list((root / "commands").glob("ar-*.md"))

    assert entry.main(["--uninstall", "ar", "--custom-harness", spec]) == 0
    assert not list((root / "commands").glob("ar-*.md"))
    assert "Mine" not in ((root / "AGENTS.md").read_text() if (root / "AGENTS.md").exists() else "")


def test_a_custom_harness_named_like_any_registered_harness_is_refused(monkeypatch, isolated, capsys):
    """``gemini=qwen:...`` used to install whenever legacy gemini was not
    selected; every uninstall selects all registered harnesses, so each one
    then raised and removed nothing. Refuse the name before writing."""
    from autorun.installer import entrypoint

    monkeypatch.setattr(entrypoint, "_run", lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""))
    monkeypatch.setattr(
        entrypoint.shutil, "which", lambda name: f"/bin/{name}" if name in {"qwen-lab", "uv"} else None
    )

    assert (
        entrypoint.install_plugins(
            "ar", custom_harnesses=("gemini=qwen:qwen-lab:~/.qwen-lab",), conductor=False, tool=False
        )
        == 1
    )
    assert "duplicates a registered harness" in capsys.readouterr().out
    assert not (isolated / ".qwen-lab").exists()


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


def _antigravity_installed_before_stamping(monkeypatch, isolated, calls):
    """Install into Agy, then model a copy made before autorun stamped it.

    Agy copies the bundle and records only ``{"name": "ar", "source":
    "antigravity"}`` in its import manifest, so an older install left a tree
    with no root marker whose contents no longer match today's source.
    """
    from autorun.installer import entrypoint
    from autorun.installer.fs import OWNED_MARKER_NAME

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
    (installed / OWNED_MARKER_NAME).unlink()
    (installed / "commands" / "retired-by-a-later-release.md").write_text(
        "old command\n", encoding="utf-8"
    )
    calls.clear()
    return installed


def test_antigravity_copy_made_before_stamping_is_refreshed_when_its_hooks_are_ours(
    monkeypatch, isolated
):
    """A stale, unmarked Agy copy of our bundle must not freeze that harness forever.

    Before this pin the only proof accepted for such a tree was an exact
    content match with today's source, which no copy of an older bundle can
    pass, so every later ``--install`` skipped Antigravity in silence and it
    kept running the first bundle it ever imported.
    """
    from autorun.installer import entrypoint, fs

    calls: list[tuple[str, ...]] = []
    installed = _antigravity_installed_before_stamping(monkeypatch, isolated, calls)
    source = (
        Path(os.environ["AUTORUN_HOME"]) / "installer" / "extension-sources"
        / "antigravity" / "ar"
    )
    assert fs.scan_tree(installed) != fs.scan_tree(source)

    assert entrypoint.install_plugins(
        "ar", antigravity_only=True, conductor=False, tool=False
    ) == 0
    assert ("agy", "plugin", "uninstall", "ar") in calls
    assert any(call[:3] == ("agy", "plugin", "install") for call in calls)
    assert fs.scan_tree(installed) == fs.scan_tree(source)
    assert not (installed / "commands" / "retired-by-a-later-release.md").exists()
    marker = fs.read_marker(installed)
    assert marker is not None and marker.files


def test_antigravity_same_name_plugin_with_foreign_hooks_is_left_alone_and_reported(
    monkeypatch, isolated, capsys
):
    """The receipt alone proves nothing: a user's own ``ar`` plugin stays theirs."""
    from autorun.installer import entrypoint, fs

    calls: list[tuple[str, ...]] = []
    installed = _antigravity_installed_before_stamping(monkeypatch, isolated, calls)
    (installed / "hooks.json").write_text(
        json.dumps({"PreToolUse": [{"type": "command", "command": "python mine.py"}]}),
        encoding="utf-8",
    )
    theirs = fs.scan_tree(installed)

    assert entrypoint.install_plugins(
        "ar", antigravity_only=True, conductor=False, tool=False
    ) == 1
    assert not any(call[:3] == ("agy", "plugin", "uninstall") for call in calls)
    assert fs.scan_tree(installed) == theirs
    assert fs.read_marker(installed) is None
    out = capsys.readouterr().out
    assert str(installed) in out and "not autorun's" in out


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


def test_status_reports_and_fails_for_the_hook_kill_switch(
    monkeypatch, isolated, capsys
):
    from autorun.installer import entrypoint, orchestrate
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
        lambda **kwargs: orchestrate.Result(Mode.PREVIEW, decisions=()),
    )
    monkeypatch.setenv("AUTORUN_DISABLE", "1")

    assert entrypoint.show_status() == 1
    assert "BROKEN AUTORUN_DISABLE=1" in capsys.readouterr().out


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
    monkeypatch.setattr(entrypoint, "_uv_tool_installed", lambda _package: True)

    assert entrypoint.uninstall_plugins("pdf-extractor") == 0
    assert agents.is_file() and hooks.is_file() and package.is_dir()
    # Removing the pdf plugin moves harness files only. Its code ships inside
    # the `autorun-ai` distribution, which also carries autorun's own CLI, so
    # reaching any package here would uninstall the tool the user kept.
    assert not [call for call in calls if "uninstall" in call and "tool" in call], calls


def test_the_walk_never_uninstalls_a_distribution_sharing_one_of_our_scripts(
    monkeypatch, isolated, capsys
):
    """Stated as the violation: an upgrade leaves the user with no command.

    ``uv tool uninstall`` deletes console scripts by *name*, not by owner.
    Measured twice, in uv's own words, with ``autorun-ai`` installed and
    providing all three scripts::

        $ uv tool uninstall autorun
        Uninstalled 3 executables: autorun, autorun-install, extract-pdfs

        $ uv tool uninstall autorun-pdf-extractor
        Uninstalled 1 executable: extract-pdfs

    The second already shipped, so this is a defect in released behaviour and
    not a consequence of the rename. Restoring the scripts afterwards is not
    available: the repair would reinstall the current distribution, and from a
    ``uv tool`` install the only local path is ``.../site-packages/autorun``,
    the import package, which ``uv tool install`` refuses.

    So the assertion is on the subprocess layer, which is where the damage is
    done -- not on a flag, a comment, or an ordering. No ``uv tool uninstall``
    of a name whose script is one of ours may be issued at all.
    """
    from autorun.installer import entrypoint

    calls: list[tuple[str, ...]] = []

    def record(argv):
        calls.append(tuple(str(part) for part in argv))
        return subprocess.CompletedProcess(
            argv, 0, "ar\n" if tuple(argv)[:3] == ("agy", "plugin", "list") else "", ""
        )

    # Claim every legacy distribution is present, so nothing is skipped merely
    # for being absent -- the skip under test must be the shared-script rule.
    monkeypatch.setattr(entrypoint, "_uv_tool_installed", lambda _package: True)
    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")

    assert entrypoint.install_plugins("ar", conductor=False) == 0

    uninstalls = [call for call in calls if call[:3] == ("uv", "tool", "uninstall")]
    assert uninstalls == [], (
        f"these would delete console scripts autorun-ai provides: {uninstalls}"
    )

    # Principle 4: when we cannot fix it for the user, say so and name the exact
    # next action. A silent skip would leave the orphan invisible forever.
    printed = capsys.readouterr().out
    for package in entrypoint._PLUGIN_DISTRIBUTIONS["ar"][1:]:
        assert package in printed, f"{package} orphan not reported"
    assert "uv tool install --force autorun-ai" in printed, printed


def test_every_legacy_distribution_shares_a_script_with_the_current_one():
    """The rule is a predicate, not a hardcoded list of exceptions.

    Every name autorun has retired happens to provide one of its own scripts
    today, so the uv-tool sweep currently removes nothing. That is the correct
    outcome, and deriving it keeps a future non-overlapping legacy name
    sweepable without editing this test. The pip route is unaffected: a
    pip-installed retired package owns its own environment, so removing it
    takes only its own copy of the script.
    """
    from autorun.installer import entrypoint, runtime

    legacy = entrypoint._PLUGIN_DISTRIBUTIONS["ar"][1:]
    assert legacy, "there is nothing to retire, so the rule is untested"
    shared = {
        package
        for package in legacy
        if entrypoint._UV_TOOL_SCRIPTS[package] in runtime.CLI_SCRIPTS
    }
    assert shared == set(legacy), sorted(set(legacy) - shared)


def _bootstrap_only(monkeypatch, entrypoint, plugin: Path, python: Path, calls: list):
    """Run install_plugins up to and through bootstrap with a recorded runner.

    The walk and the daemon restart are stood in for: the subject is which
    commands bootstrap issues for this interpreter and plugin layout.
    """
    from autorun.installer import orchestrate
    from autorun.installer.traversal import Mode

    def record(argv, **_kwargs):
        call = tuple(str(part) for part in argv)
        calls.append(call)
        editable_wheel = call[:3] == ("uv", "tool", "install") and not (plugin / "pyproject.toml").is_file()
        return subprocess.CompletedProcess(
            argv, 2 if editable_wheel else 0, "", "does not appear to be a Python project" if editable_wheel else ""
        )

    python.parent.mkdir(parents=True, exist_ok=True)
    python.touch()
    monkeypatch.setattr(entrypoint.sys, "executable", str(python))
    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(entrypoint, "_uv_tool_installed", lambda _package: False)
    monkeypatch.setattr(entrypoint, "_runtime_settings", lambda *_args, **_kwargs: plugin)
    monkeypatch.setattr(orchestrate, "install", lambda **_kwargs: orchestrate.Result(Mode.INSTALL))
    monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")


def test_tool_install_from_a_wheel_in_a_uv_tool_env_reaches_the_walk(monkeypatch, isolated, tmp_path, capsys):
    """``autorun --install --tool`` from a wheel installed with ``uv tool``.

    The plugin directory is ``site-packages/autorun``: no pyproject.toml, so
    ``uv tool install --editable`` on it fails, and that failure used to end
    the install with FAIL before the walk ran. The package installer already
    supplied the CLI; there is nothing for --tool to add.
    """
    from autorun.installer import entrypoint

    wheel = tmp_path / "site-packages" / "autorun"
    wheel.mkdir(parents=True)
    (wheel / "__main__.py").touch()
    monkeypatch.delenv("UV_TOOL_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    calls: list[tuple[str, ...]] = []
    _bootstrap_only(
        monkeypatch, entrypoint, wheel, tmp_path / "data" / "uv" / "tools" / "autorun-ai" / "bin" / "python", calls
    )

    assert entrypoint.install_plugins("ar", tool=True, conductor=False) == 0
    assert not [call for call in calls if call[:3] == ("uv", "tool", "install")], calls
    assert "supplied by the package installer" in capsys.readouterr().out


def test_a_custom_uv_tool_dir_is_recognised_as_a_uv_tool_env(monkeypatch, isolated, tmp_path):
    """One answer to "where do uv tools live": UV_TOOL_DIR counts for the
    bootstrap choice exactly as it does for ownership, so a checkout installed
    as a uv tool under a custom root gets ``uv pip install``, not ``uv sync``."""
    from autorun.installer import entrypoint

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "pyproject.toml").touch()
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    calls: list[tuple[str, ...]] = []
    _bootstrap_only(monkeypatch, entrypoint, checkout, tmp_path / "tools" / "autorun-ai" / "bin" / "python", calls)

    assert entrypoint.install_plugins("ar", tool=False, conductor=False) == 0
    assert calls[0][:3] == ("uv", "pip", "install"), calls
