"""Production installer facade: one resolved path into the manifest engine."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO = Path(__file__).resolve().parents[3]


def _cache_aware_runner(argv, **_kwargs):
    """Model installer subprocesses, including cache-venv prefix probes."""
    call = tuple(str(part) for part in argv)
    stdout = "ar\n" if call[:3] == ("agy", "plugin", "list") else ""
    if len(call) >= 4 and call[1:3] == ("-I", "-c") and "sys.prefix" in call[3]:
        stdout = f"{Path(call[0]).resolve().parent.parent}\n"
    return subprocess.CompletedProcess(argv, 0, stdout, "")


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


@pytest.fixture(scope="session")
def marketplace_copy(tmp_path_factory):
    """The checkout's tracked files, copied once per worker, outside git.

    An install walk over the live checkout reads its git state (``rev-parse``
    and ``status --porcelain`` for the build commit, ``ls-files`` for the
    asset copy) while parallel workers run in the same tree. A copy with no
    ``.git`` makes the walk depend on nothing another test can change.
    """
    import shutil

    listed = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"],
        capture_output=True, check=True,
    ).stdout.decode("utf-8").split("\0")
    root = tmp_path_factory.mktemp("marketplace")
    for name in filter(None, listed):
        source = REPO / name
        if not (source.exists() or source.is_symlink()):
            continue  # deleted in the working tree
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination, follow_symlinks=False)
    return root


@pytest.fixture
def isolated(monkeypatch, tmp_path, point_daemon_at):
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
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "state"))
    # AUTORUN_HOME and the daemon paths fixed at import, so an uninstall's
    # teardown cannot find and stop this worker's daemon (conftest.py).
    point_daemon_at(tmp_path / "ar")
    return home


def test_isolated_daemon_paths_follow_the_fixtures_autorun_home(isolated):
    """An uninstall's teardown (installer/teardown.stop_daemon) finds the
    daemon through restart_daemon.LOCK_PATH and ipc's socket paths, which are
    fixed when autorun is first imported. Setting AUTORUN_HOME afterwards left
    them on the test worker's runtime, so these tests' uninstalls stopped the
    worker's own daemon instead of finding none."""
    from autorun import ipc, restart_daemon

    home = Path(os.environ["AUTORUN_HOME"])
    assert ipc.AUTORUN_CONFIG_DIR == home
    assert restart_daemon.LOCK_PATH == home / "daemon.lock"
    assert ipc.SOCKET_PATH == home / "daemon.sock"
    assert ipc.PORT_FILE == home / "daemon.port"
    assert ipc.TOKEN_FILE == home / "daemon.token"


def test_main_entrypoint_dispatches_install_force_and_uninstall(monkeypatch):
    """Violation excluded: public CLI modes bypass the installer entry point."""
    from autorun import install as install_api
    from autorun.installer import entrypoint

    calls = []
    monkeypatch.setattr(
        install_api,
        "install_plugins",
        lambda selection, **kwargs: calls.append(("install", selection, kwargs)) or 0,
    )
    monkeypatch.setattr(
        install_api,
        "uninstall_plugins",
        lambda selection, **kwargs: calls.append(("uninstall", selection, kwargs)) or 0,
    )

    assert entrypoint.main(["--install", "ar", "--claude"]) == 0
    assert entrypoint.main(["--install", "ar", "--claude", "--force"]) == 0
    assert entrypoint.main(["--uninstall", "ar", "--claude"]) == 0
    assert calls[0][0:2] == ("install", "ar") and calls[0][2]["force"] is False
    assert calls[1][0:2] == ("install", "ar") and calls[1][2]["force"] is True
    assert calls[2][0:2] == ("uninstall", "ar")


@pytest.mark.parametrize(
    ("runtime_state", "import_result"),
    (
        ("missing-interpreter", None),
        ("missing-package", None),
        ("editable-package", "/checkout/src/autorun/__init__.py"),
    ),
)
def test_main_install_repairs_every_receipt_only_runtime_state(
    monkeypatch, isolated, runtime_state, import_result
):
    """Violation excluded: the CLI trusts a receipt over its cache interpreter."""
    from autorun.installer import claude, entrypoint, runtime, steps
    from autorun.platforms import PLATFORMS

    source = REPO / "plugins" / "autorun"
    version = steps._version(source)
    cache = claude.cache_fallback(
        source,
        PLATFORMS["claude"],
        market="autorun",
        plugin="ar",
        version=version,
        home=isolated,
    )
    assert cache is not None
    venv = cache / ".venv"
    python = runtime.cache_venv_python(cache)
    if runtime_state != "missing-interpreter":
        python.parent.mkdir(parents=True)
        python.write_text("interpreter placeholder\n", encoding="utf-8")
        (venv / "pyvenv.cfg").write_text("home = isolated\n", encoding="utf-8")
    venv.mkdir(exist_ok=True)
    (venv / ".autorun-runtime.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "install_mode": "non-editable",
                "source_fingerprint": runtime._cache_runtime_fingerprint(source),
            }
        ),
        encoding="utf-8",
    )
    pip_calls = []

    def run(argv, **_kwargs):
        call = tuple(str(part) for part in argv)
        if call[:2] == ("uv", "venv"):
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("interpreter placeholder\n", encoding="utf-8")
            (venv / "pyvenv.cfg").write_text("home = isolated\n", encoding="utf-8")
        elif len(call) >= 4 and call[1:3] == ("-I", "-c"):
            if "import autorun" in call[3]:
                return subprocess.CompletedProcess(
                    argv,
                    0 if import_result else 1,
                    (import_result + "\n") if import_result else "",
                    "missing autorun" if not import_result else "",
                )
            return subprocess.CompletedProcess(argv, 0, str(venv) + "\n", "")
        elif call[:3] == ("uv", "pip", "install"):
            pip_calls.append(call)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")
    monkeypatch.setattr(entrypoint, "_run", run)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}" if name == "claude" else None)
    monkeypatch.setattr(runtime, "has_uv", lambda: True)
    monkeypatch.setattr(
        runtime,
        "bootstrap",
        lambda *args, **kwargs: (runtime.Outcome("bootstrap", True, "test"),),
    )

    assert entrypoint.main(["--install", "ar", "--claude"]) == 0
    assert len(pip_calls) == 1, f"{runtime_state} skipped the package reinstall"


def test_second_plain_main_install_reports_current_claude_cache(
    monkeypatch, isolated, capsys
):
    """Violation excluded: an identical plain install says nothing about its cache."""
    from autorun.installer import claude, entrypoint, runtime, steps
    from autorun.platforms import PLATFORMS

    source = REPO / "plugins" / "autorun"
    version = steps._version(source)
    cache = claude.cache_fallback(
        source,
        PLATFORMS["claude"],
        market="autorun",
        plugin="ar",
        version=version,
        home=isolated,
    )
    assert cache is not None
    venv = cache / ".venv"
    python = runtime.cache_venv_python(cache)
    python.parent.mkdir(parents=True)
    python.write_text("interpreter placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = isolated\n", encoding="utf-8")
    imported = venv / "lib" / "site-packages" / "autorun" / "__init__.py"
    (venv / ".autorun-runtime.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "install_mode": "non-editable",
                "source_fingerprint": runtime._cache_runtime_fingerprint(source),
            }
        ),
        encoding="utf-8",
    )

    def run(argv, **_kwargs):
        call = tuple(str(part) for part in argv)
        if len(call) >= 4 and call[1:3] == ("-I", "-c"):
            output = str(imported) if "import autorun" in call[3] else str(venv)
            return subprocess.CompletedProcess(argv, 0, output + "\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")
    monkeypatch.setattr(entrypoint, "_run", run)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}" if name == "claude" else None)
    monkeypatch.setattr(
        runtime,
        "bootstrap",
        lambda *args, **kwargs: (runtime.Outcome("bootstrap", True, "test"),),
    )

    assert entrypoint.main(["--install", "ar", "--claude"]) == 0
    capsys.readouterr()
    assert entrypoint.main(["--install", "ar", "--claude"]) == 0
    output = capsys.readouterr().out
    assert f"ok   claude: cache refresh — already current: {cache}" in output


def test_two_main_processes_bound_cache_lock_contention_through_config(
    monkeypatch, isolated, tmp_path
):
    """Violation excluded: two CLI installs can wait forever on the cache lock."""
    import time

    from autorun.installer import claude, steps
    from autorun.platforms import PLATFORMS

    source = REPO / "plugins" / "autorun"
    version = steps._version(source)
    cache = claude.cache_fallback(
        source,
        PLATFORMS["claude"],
        market="autorun",
        plugin="ar",
        version=version,
        home=isolated,
    )
    assert cache is not None
    signal = tmp_path / "runtime-started"
    script = r'''
import os, subprocess, time
from pathlib import Path
from autorun.installer import entrypoint, runtime

def run(argv, **kwargs):
    return subprocess.CompletedProcess(argv, 0, "", "")

def slow_runtime(*args, **kwargs):
    Path(os.environ["SLOW_RUNTIME_SIGNAL"]).write_text("started\n", encoding="utf-8")
    time.sleep(float(os.environ["SLOW_RUNTIME_SECONDS"]))
    return runtime.Outcome("Claude cache runtime", True, "slow fake completed")

entrypoint._run = run
entrypoint.shutil.which = lambda name: f"/bin/{name}" if name == "claude" else None
runtime.bootstrap = lambda *args, **kwargs: (runtime.Outcome("bootstrap", True, "test"),)
runtime.install_cache_runtime = slow_runtime
raise SystemExit(entrypoint.main(["--install", "ar", "--claude", "--force"]))
'''
    environment = {
        **os.environ,
        "HOME": str(isolated),
        "USERPROFILE": str(isolated),
        "AUTORUN_USE_DAEMON": "0",
        "AUTORUN_CACHE_INSTALL_LOCK_TIMEOUT_SECONDS": "0.05",
        "SLOW_RUNTIME_SIGNAL": str(signal),
        "SLOW_RUNTIME_SECONDS": "0.4",
    }
    command = [sys.executable, "-c", script]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    deadline = time.monotonic() + 10
    while not signal.exists() and first.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert signal.exists(), first.communicate(timeout=5)
    second = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=15)
    first_stdout, first_stderr = first.communicate(timeout=15)

    assert first.returncode == 0, (first_stdout, first_stderr)
    assert second.returncode == 1, (second.stdout, second.stderr)
    assert "waiting for another autorun install refreshing" in second.stdout
    assert "timed out waiting for cache install lock" in second.stdout
    assert "wait and rerun" in second.stdout
    assert first_stderr == second.stderr == ""


def test_two_install_processes_coalesce_daemon_restart(monkeypatch, isolated, tmp_path):
    """Violation excluded: a concurrent install fails only because restart is busy."""
    import time

    signal = tmp_path / "restart-started"
    healthy = tmp_path / "daemon-healthy"
    script = r'''
import os, time
from pathlib import Path
from autorun import restart_daemon as restart_mod
from autorun.installer import entrypoint, orchestrate, runtime
from autorun.installer.traversal import Mode

signal = Path(os.environ["RESTART_SIGNAL"])
healthy = Path(os.environ["RESTART_HEALTHY"])
src = Path(os.environ["RESTART_SRC"])
restart_mod._resolve_src_dir = lambda: src
restart_mod.get_daemon_pid = lambda **kwargs: 4242 if healthy.exists() else None
restart_mod.ipc.answers_round_trip = lambda: healthy.exists()
restart_mod.ipc.daemon_record = lambda *_args, **_kwargs: (
    (4242, int(healthy.read_text(encoding="utf-8"))) if healthy.exists() else None
)
restart_mod._daemon_age_seconds = lambda _pid: 0.0
restart_mod._source_newer_than = lambda *_args: False
restart_mod._daemon_processes_for_src = lambda _src: []
restart_mod._all_daemon_processes = lambda: []
restart_mod.cleanup_stale_files = lambda: None
restart_mod._clear_pycache = lambda _src: None
restart_mod._check_conflicting_packages = lambda: None
restart_mod.is_daemon_responding = lambda: healthy.exists()
restart_mod._display_daemon_diagnostics = lambda _src: True
restart_mod.verify_bashlex = lambda: True

def start(_src):
    signal.write_text("started\n", encoding="utf-8")
    time.sleep(0.4)
    healthy.write_text(
        str(round(time.time() * restart_mod.ipc.PROCESS_BIRTH_UNITS_PER_SECOND)),
        encoding="utf-8",
    )
    return True
restart_mod._start_daemon = start

entrypoint.shutil.which = lambda name: f"/bin/{name}" if name == "claude" else None
runtime.bootstrap = lambda *args, **kwargs: (runtime.Outcome("bootstrap", True, "test"),)
orchestrate.install = lambda **kwargs: orchestrate.Result(Mode.INSTALL)
def restart(**kwargs):
    rc = restart_mod.restart_daemon(replace_runtime_daemon=True)
    return runtime.Outcome("daemon restart", rc == 0, "wait and rerun" if rc else "")
runtime.restart_daemon = restart
raise SystemExit(entrypoint.main(["--install", "ar", "--claude"]))
'''
    environment = {
        **os.environ,
        "HOME": str(isolated),
        "USERPROFILE": str(isolated),
        "AUTORUN_USE_DAEMON": "1",
        "RESTART_SIGNAL": str(signal),
        "RESTART_HEALTHY": str(healthy),
        "RESTART_SRC": str(REPO / "plugins" / "autorun" / "src"),
    }
    command = [sys.executable, "-c", script]
    first = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    deadline = time.monotonic() + 10
    while not signal.exists() and first.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert signal.exists(), first.communicate(timeout=5)
    second = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=15)
    first_stdout, first_stderr = first.communicate(timeout=15)

    assert first.returncode == second.returncode == 0, (
        first_stdout,
        first_stderr,
        second.stdout,
        second.stderr,
    )
    assert "not restarting it again" in second.stdout
    assert first_stderr == second.stderr == ""


def test_selection_comes_from_the_manifest_and_accepts_the_retired_name():
    from autorun.installer.entrypoint import parse_selection

    listed = tuple(
        entry["name"]
        for entry in json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["plugins"]
    )
    assert parse_selection(REPO, "all") == listed
    assert "autorun-skills" in listed
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


def test_install_all_for_codex_registers_only_the_codex_capable_plugins(monkeypatch, isolated):
    """Codex gets exactly the marketplace plugins that ship a Codex manifest."""
    from autorun.installer import entrypoint

    calls = []

    def record(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    from autorun.installer.discovery import plugin_dir

    plugins = _marketplace_plugins()
    capable = {
        name for name in plugins
        if (plugin_dir(REPO, name) / ".codex-plugin" / "plugin.json").is_file()
    }
    assert "ar" in capable, "the Codex manifest check matches nothing"

    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")
    assert entrypoint.install_plugins("all", codex_only=True, conductor=False, tool=False) == 0
    for name in plugins:
        packaged = (isolated / "plugins" / name / ".codex-plugin" / "plugin.json").is_file()
        registered = ("codex", "plugin", "add", f"{name}@personal") in calls
        assert packaged is (name in capable), name
        assert registered is (name in capable), name


def _marketplace_plugins() -> tuple[str, ...]:
    manifest = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    return tuple(entry["name"] for entry in manifest["plugins"])


@pytest.mark.parametrize("harness", sorted(__import__("autorun.platforms", fromlist=["PLATFORMS"]).PLATFORMS))
def test_every_marketplace_plugin_reaches_every_harness(harness, tmp_path, monkeypatch):
    """Stated as the violation: a plugin the marketplace lists never arrives.

    A plugin reaches a harness when the install either publishes a tree it
    owns (its ownership marker names the plugin) or tells the harness's own
    plugin CLI about it (an argv naming the plugin, its ``name@market`` or its
    staged directory).
    """
    from autorun.installer import fs, orchestrate
    from autorun.platforms import PLATFORMS

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path / "ar"))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    plugins = _marketplace_plugins()
    calls: list[tuple[str, ...]] = []

    def run(argv, **kwargs):
        calls.append(tuple(str(part) for part in argv))
        result = _cache_aware_runner(argv, **kwargs)
        if tuple(argv)[:3] == ("agy", "plugin", "list"):
            result = subprocess.CompletedProcess(argv, 0, "\n".join(plugins) + "\n", "")
        return result

    platform = PLATFORMS[harness]
    result = orchestrate.install(
        marketplace_root=REPO, plugins=plugins, settings={}, home=home,
        harnesses=(platform,), available=(platform.binary,), run_command=run,
        state_dir=home / ".state",
    )
    assert result.ok, result.lines()

    def reached(plugin: str) -> bool:
        owned = any(
            decision.target.is_dir() and fs.owns(fs.read_marker(decision.target), plugin)
            for decision in result.decisions
        )
        told = any(
            token == plugin or token.startswith(f"{plugin}@") or Path(token).name == plugin
            for call in calls for token in call
        )
        return owned or told

    missing = [plugin for plugin in plugins if not reached(plugin)]
    assert not missing, f"{harness} never received {missing}"


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


def test_uninstall_keeps_unproven_cache_files_without_failing(
    monkeypatch, isolated, capsys
):
    """Violation excluded: an unproven sibling cache makes uninstall exit 1."""
    from autorun.installer import entrypoint, fs

    root = isolated / ".claude" / "plugins" / "cache" / "autorun" / "ar"
    owned = root / "1.0.0"
    (owned / "hooks").mkdir(parents=True)
    (owned / "hooks" / "hook.py").write_text("owned\n", encoding="utf-8")
    (owned / fs.OWNED_MARKER_NAME).write_text(
        json.dumps(fs.TreeManifest.of(owned, plugin="ar").as_payload()),
        encoding="utf-8",
    )
    unproven = root / "0.8.8"
    unproven.mkdir()
    (unproven / "notes.txt").write_text("user notes\n", encoding="utf-8")
    (unproven / ".orphaned_at").write_text("2026-11-12T00:00:00Z\n", encoding="utf-8")
    lock = root / fs.INSTALL_LOCK_NAME
    lock.touch()
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        entrypoint,
        "_run",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 0, "", ""),
    )

    assert entrypoint.uninstall_plugins("ar") == 0

    output = capsys.readouterr().out
    assert not owned.exists()
    assert (unproven / "notes.txt").is_file()
    assert "FAIL" not in output
    assert f"KEEP {unproven}: notes.txt" in output
    assert "Delete this folder by hand if you no longer want these files." in output
    assert lock.is_file()
    assert f"KEEP {lock}: installer synchronization lock." in output
    assert "It remains in place so concurrent installers keep one lock authority." in output


def test_uninstall_cache_io_error_still_fails(monkeypatch, isolated, capsys):
    """Violation excluded: an actual cache retirement I/O error reports success."""
    from autorun.installer import claude, entrypoint

    root = isolated / ".claude" / "plugins" / "cache" / "autorun" / "ar" / "1.0.0"
    root.mkdir(parents=True)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda _name: None)
    monkeypatch.setattr(
        claude,
        "retire_cache_versions",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("injected I/O failure")),
    )

    assert entrypoint.uninstall_plugins("ar") == 1
    output = capsys.readouterr().out
    assert "FAIL claude: ar cache retirement" in output
    assert "injected I/O failure" in output


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


def test_uninstall_removes_each_owned_distribution_by_its_own_route(monkeypatch, isolated):
    """Uninstall removes what this home installed: the uv tool with uv, a
    pip-receipted one with pip (and its receipt), and nothing it does not own."""
    from autorun.installer import entrypoint

    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        entrypoint, "_run", lambda argv, **_k: calls.append(tuple(argv)) or subprocess.CompletedProcess(argv, 0, "", "")
    )
    monkeypatch.setattr(entrypoint, "_runtime_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: "/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(entrypoint, "_uv_tool_installed", lambda package: package == "autorun-ai")
    receipt = entrypoint._package_receipt("pdf-extractor")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps({"distribution": "pdf-extractor", "installer": "pip", "python": str(Path(entrypoint.sys.executable).resolve())}),
        encoding="utf-8",
    )

    assert entrypoint.uninstall_plugins("ar") == 0

    assert [call for call in calls if "uninstall" in call] == [
        ("uv", "tool", "uninstall", "autorun-ai"),
        (entrypoint.sys.executable, "-m", "pip", "uninstall", "-y", "pdf-extractor"),
    ]
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
        _cache_aware_runner,
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
        _cache_aware_runner,
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
@pytest.mark.parametrize("shell", [None, "posix", "powershell"])
def test_receipt_owned_legacy_gemini_refreshes_without_binary(
    monkeypatch, isolated, legacy_empty_marker, shell
):
    """Port the pre-redesign receipt adoption and missing-CLI refresh contract."""
    from autorun.installer import discovery, entrypoint, fs, runtime, steps
    from autorun.platforms import PLATFORMS

    if shell is not None:
        monkeypatch.setattr(runtime, "hook_command_shell", lambda _harness: shell)
    # setup-uv exports UV_PYTHON on CI, an alias of the hook_python setting,
    # which adds --python to every rendered command.
    monkeypatch.delenv("UV_PYTHON", raising=False)

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
        (event, hook["command"])
        for event, groups in hooks["hooks"].items()
        for group in groups
        for hook in group.get("hooks", [])
    ]
    assert commands
    for event, command in commands:
        expected = runtime.UvCommand(
            project=plugin,
            script=plugin / "hooks" / "hook_entry.py",
            args=("--cli", "gemini"),
        ).shell(runtime.hook_command_shell(PLATFORMS["gemini"]))
        assert command == f"{expected} --event {event}"
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
    monkeypatch, isolated, capsys, marketplace_copy
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

    def record(argv, **_kwargs):
        calls.append(tuple(str(part) for part in argv))
        return _cache_aware_runner(argv, **_kwargs)

    # Claim every legacy distribution is present, so nothing is skipped merely
    # for being absent -- the skip under test must be the shared-script rule.
    monkeypatch.setattr(entrypoint, "_uv_tool_installed", lambda _package: True)
    monkeypatch.setattr(entrypoint, "_run", record)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")
    # Hermetic: no git calls against the shared checkout, no shared tree.
    monkeypatch.setenv("AUTORUN_BUILD_COMMIT", "test-commit")
    monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: marketplace_copy)

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


@pytest.mark.parametrize(
    "listing, registered",
    [
        ("No plugins are installed.\n", False),
        ("autorun-tools\nradar\n", False),
        ("ar\n", True),
        ("Installed plugins:\n  ar (1.0.0)\n", True),
    ],
)
def test_antigravity_verification_matches_the_plugin_name_as_a_token(listing, registered):
    """registration.register verifies Agy by ``agy plugin list`` output. A
    substring match on "ar" passed for "No plugins are installed." ("are")."""
    from autorun.installer import registration

    def run(argv, **_kwargs):
        if tuple(argv)[:3] == ("agy", "plugin", "validate"):
            return subprocess.CompletedProcess(argv, 1, "", "invalid native bundle")
        stdout = listing if tuple(argv)[:3] == ("agy", "plugin", "list") else ""
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    outcomes = registration.register(
        "antigravity",
        {"name": "ar", "market": "autorun", "root": "/repo", "extension": "/durable/ar"},
        run=run,
        available=("agy",),
    )

    assert all(outcome.ok for outcome in outcomes) is registered


@pytest.mark.parametrize("layout", ["checkout", "wheel"])
def test_codex_plugin_hook_command_follows_the_hook_command_layout(monkeypatch, isolated, tmp_path, layout):
    """A wheel's plugin root has no pyproject.toml, so ``uv run --project
    ${CLAUDE_PLUGIN_ROOT}`` there runs a bare interpreter without autorun's
    dependencies. The bundled Codex hook must make the choice
    runtime.hook_command makes for every other hook."""
    import shlex
    import sys

    from autorun.installer import discovery, entrypoint

    plugin = tmp_path / layout
    (plugin / "hooks").mkdir(parents=True)
    (plugin / ("pyproject.toml" if layout == "checkout" else "__main__.py")).touch()
    monkeypatch.setattr(discovery, "plugin_dir", lambda _root, _name: plugin)
    resolved: dict = {}

    entrypoint._runtime_settings(tmp_path, resolved, (), ())

    command = resolved["_codex_plugin_hook_command"]["command"]
    if layout == "checkout":
        assert command.startswith("uv run") and '--project "$CLAUDE_PLUGIN_ROOT"' in command, command
    else:
        assert "uv run" not in command, command
        assert shlex.split(command)[:2] == [sys.executable, str(plugin / "hooks" / "hook_entry.py")]
        assert command == resolved["_codex_hook_command"]["command"]


@pytest.mark.parametrize("layout", ["checkout", "wheel"])
def test_codex_writes_windows_override_in_user_and_plugin_hooks(monkeypatch, isolated, tmp_path, layout):
    """Both install routes need the override Codex selects before COMSPEC /C."""
    import base64
    from dataclasses import replace

    from autorun.installer import codex, discovery, entrypoint, steps
    from autorun.installer.traversal import Context, Mode
    from autorun.platforms import CODEX

    plugin = tmp_path / "plugin with space & apostrophe's"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "hooks" / "hook_entry.py").touch()
    (plugin / ".codex-plugin").mkdir()
    (plugin / ".codex-plugin" / "plugin.json").write_text('{"name":"ar"}', encoding="utf-8")
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"ar"}', encoding="utf-8")
    if layout == "checkout":
        (plugin / "pyproject.toml").touch()
    monkeypatch.setattr(discovery, "plugin_dir", lambda _root, _name: plugin)
    platform = replace(CODEX, name="custom-codex", install_flavor="codex")
    resolved = {"codex_hook_source": "both"}
    entrypoint._runtime_settings(tmp_path, resolved, (platform,), (), require_guidance=False)
    ctx = Context(tmp_path, plugin_dirs=(plugin,), settings=resolved)
    entries = steps.hooks_for(platform, ctx)
    steps.apply_hooks(entries, Mode.INSTALL)
    assert steps.apply_hooks(entries, Mode.PREVIEW) == [f"current {entries[0].describe()}"]
    staged = codex.stage_plugin(
        plugin, tmp_path / "staged", include_hooks=True, include_skills=False,
        hook_command=resolved["_codex_plugin_hook_command"], hook_events=("PreToolUse",),
    )
    for path in (entries[0].path, staged / "hooks" / "hooks.json"):
        hook = json.loads(path.read_text())["hooks"]["PreToolUse"][0]["hooks"][0]
        assert hook["command"]
        windows = hook["commandWindows"]
        prefix = "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
        assert windows.startswith(prefix)
        script = base64.b64decode(windows[len(prefix):]).decode("utf-16-le")
        assert "& " in script and "--cli" in script and "codex" in script
        assert str(plugin).replace("'", "''") in script
        assert "${CLAUDE_PLUGIN_ROOT}" not in script


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


def test_source_metadata_is_stamped_once_where_the_package_reads_it(monkeypatch, isolated, tmp_path):
    """AUTORUN_WRITE_SOURCE_METADATA=1 stamped ``<plugin>/src/autorun/metadata.json``
    for every selected plugin: pdf-extractor got an autorun metadata file, and
    a wheel got ``site-packages/autorun/src/autorun/metadata.json``, which
    nothing reads. Only ar has the file, at its runtime root."""
    from autorun.installer import discovery, entrypoint

    wheel = tmp_path / "site-packages" / "autorun"
    wheel.mkdir(parents=True)
    (wheel / "__main__.py").touch()
    pdf = tmp_path / "plugins" / "pdf-extractor"
    pdf.mkdir(parents=True)
    calls: list[tuple[str, ...]] = []
    _bootstrap_only(monkeypatch, entrypoint, wheel, tmp_path / "venv" / "bin" / "python", calls)
    monkeypatch.setattr(discovery, "resolve_plugins", lambda _root, _names: ((wheel, pdf), ()))
    monkeypatch.setenv("AUTORUN_WRITE_SOURCE_METADATA", "1")

    assert entrypoint.install_plugins("all", tool=False, conductor=False) == 0
    assert json.loads((wheel / "metadata.json").read_text(encoding="utf-8"))["version"]
    assert not (wheel / "src").exists()
    assert not (pdf / "src").exists()


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
    bootstrap = next(call for call in calls if call[:3] == ("uv", "pip", "install"))
    assert bootstrap[:3] == ("uv", "pip", "install"), calls


@pytest.mark.parametrize("layout", [(".forge",), ("forge",), (".forge", "forge")])
def test_forgecode_install_writes_under_forgecodes_base_and_creates_no_new_base(
    monkeypatch, isolated, layout
):
    """ForgeCode 2.13.21 reads skills, commands and AGENTS.md from one base:
    ~/forge when it exists, else ~/.forge (`forge list skill` and `forge list
    cmd` in a temporary HOME showed only the ~/forge markers when both held
    one). An install that creates ~/forge on a machine with only ~/.forge moves
    ForgeCode's base away from the user's configuration, so it must not.
    """
    from autorun.installer import entrypoint, memory
    from autorun.platforms import PLATFORMS

    monkeypatch.setattr(entrypoint, "_run", _cache_aware_runner)
    monkeypatch.setattr(
        entrypoint.shutil, "which", lambda name: f"/bin/{name}" if name in ("forge", "uv") else None
    )
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")
    for name in layout:
        (isolated / name / "skills" / "mine").mkdir(parents=True)
        (isolated / name / "skills" / "mine" / "SKILL.md").write_text(f"mine in {name}\n", encoding="utf-8")
        (isolated / name / "commands").mkdir()
        (isolated / name / "commands" / "mine.md").write_text(f"mine in {name}\n", encoding="utf-8")
        (isolated / name / "AGENTS.md").write_text(f"my rules in {name}\n", encoding="utf-8")
    before = {path.name for path in isolated.iterdir()}

    assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0

    created = {path.name for path in isolated.iterdir()} - before
    assert "forge" not in created, f"install created ~/forge beside {layout}: {sorted(created)}"
    base = isolated / ("forge" if "forge" in layout else ".forge")
    assert (base / "skills" / "commit" / "SKILL.md").is_file()
    assert (base / "commands" / "ar-status.md").is_file()
    agents = (base / "AGENTS.md").read_text(encoding="utf-8")
    assert memory.bounds(agents, memory.Block(PLATFORMS["forgecode"].memory_sentinel_slug)) is not None
    for name in layout:
        assert (isolated / name / "skills" / "mine" / "SKILL.md").read_text(encoding="utf-8") == f"mine in {name}\n"
        assert (isolated / name / "commands" / "mine.md").read_text(encoding="utf-8") == f"mine in {name}\n"
        assert (isolated / name / "AGENTS.md").read_text(encoding="utf-8").startswith(f"my rules in {name}\n")


def test_forgecode_memory_block_retires_from_the_directory_that_stopped_being_the_base(
    monkeypatch, isolated
):
    """An earlier install wrote autorun's block into ~/.forge/AGENTS.md. Once
    ~/forge exists ForgeCode reads ~/forge/AGENTS.md instead, so the old block
    is stale: the install moves it, keeping the user's text in ~/.forge, and an
    uninstall removes the block wherever it is."""
    from autorun.installer import entrypoint, memory
    from autorun.platforms import PLATFORMS

    monkeypatch.setattr(entrypoint, "_run", _cache_aware_runner)
    monkeypatch.setattr(
        entrypoint.shutil, "which", lambda name: f"/bin/{name}" if name in ("forge", "uv") else None
    )
    monkeypatch.delenv("FORGE_CONFIG", raising=False)
    monkeypatch.setenv("AUTORUN_USE_DAEMON", "0")
    block = memory.Block(PLATFORMS["forgecode"].memory_sentinel_slug)
    old = isolated / ".forge" / "AGENTS.md"
    old.parent.mkdir()
    old.write_text("my rules in .forge\n", encoding="utf-8")
    assert memory.splice(old, "guidance from an earlier release", block)
    (isolated / "forge").mkdir()
    new = isolated / "forge" / "AGENTS.md"
    new.write_text("my rules in forge\n", encoding="utf-8")

    assert entrypoint.install_plugins("ar", conductor=False, tool=False) == 0
    assert memory.bounds(new.read_text(encoding="utf-8"), block) is not None
    assert old.read_text(encoding="utf-8") == "my rules in .forge\n", "stale block left in ~/.forge"

    assert memory.splice(old, "guidance from an earlier release", block)
    assert entrypoint.uninstall_plugins("ar") == 0
    assert old.read_text(encoding="utf-8") == "my rules in .forge\n"
    assert new.read_text(encoding="utf-8") == "my rules in forge\n"


def test_codex_upgrade_retires_the_command_files_and_keeps_the_users_own(
    monkeypatch, isolated
):
    """Codex reads neither ~/.codex/commands nor ~/.codex/prompts, and its
    plugin manifest declares `"commands": []`, so autorun stops writing there.

    An earlier release did write the template commands; the upgrade must retire
    exactly those owned files and leave anything else in the directory alone.
    """
    from autorun.installer import entrypoint, steps

    monkeypatch.setattr(entrypoint, "_run", _cache_aware_runner)
    monkeypatch.setattr(entrypoint.shutil, "which", lambda name: f"/bin/{name}")
    commands = isolated / ".codex" / "commands"
    commands.mkdir(parents=True)
    (commands / "mine.md").write_text("my own command\n", encoding="utf-8")
    (commands / "ar-mine.md").write_text("mine too, despite the prefix\n", encoding="utf-8")
    forge_dirs = (isolated / "forge" / "commands", isolated / ".forge" / "commands")
    for directory in forge_dirs:
        directory.mkdir(parents=True)
        (directory / "mine.md").write_text("forge command\n", encoding="utf-8")

    current = steps.STEPS["codex"]
    earlier = tuple(step for step in current if step is not steps.commands_step) + (steps.commands_step,)
    monkeypatch.setitem(steps.STEPS, "codex", earlier)
    assert entrypoint.install_plugins("ar", codex_only=True, conductor=False, tool=False) == 0
    published = sorted(p.name for p in commands.glob("ar-*.md") if p.name != "ar-mine.md")
    assert published, "the earlier-release layout did not publish any command file"

    monkeypatch.setitem(steps.STEPS, "codex", current)
    assert entrypoint.install_plugins("ar", codex_only=True, conductor=False, tool=False) == 0
    assert sorted(p.name for p in commands.iterdir()) == ["ar-mine.md", "mine.md"]
    assert (commands / "mine.md").read_text(encoding="utf-8") == "my own command\n"
    assert (commands / "ar-mine.md").read_text(encoding="utf-8") == "mine too, despite the prefix\n"
    for directory in forge_dirs:
        assert sorted(p.name for p in directory.iterdir()) == ["mine.md"]
