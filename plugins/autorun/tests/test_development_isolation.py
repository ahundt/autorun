"""Exercise isolation with a disposable home standing in for the live install."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
from unittest import mock

import pytest


@pytest.mark.subprocess
def test_pytest_redirects_home_before_any_package_import(tmp_path):
    """Runtime-only isolation leaves installer and harness discovery in live HOME."""
    live_home = tmp_path / "live"
    live_home.mkdir()
    parent_conftest = Path(__file__).parents[1] / "conftest.py"
    env = dict(os.environ, HOME=str(live_home), USERPROFILE=str(live_home))
    controls = {
        "AUTORUN_ALLOW_RETIRED_GEMINI_CLI_BACKEND_TESTS": "1",
        "AUTORUN_CLAUDE_E2E_MODEL": "explicit-test-model",
        "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY": "0",
    }
    env.update(controls)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import json, os, runpy, shutil, sys
            runpy.run_path(sys.argv[1])
            runtime = os.environ["AUTORUN_TEST_RUNTIME_DIR"]
            try:
                paths = {key: os.environ.get(key) for key in (
                    "HOME", "USERPROFILE", "PI_CODING_AGENT_DIR", "AUTORUN_HOME",
                    "AUTORUN_TEST_RUNTIME_DIR", "AUTORUN_TEST_STATE_DIR",
                )}
                controls = {key: os.environ.get(key) for key in json.loads(sys.argv[2])}
                print(json.dumps({"paths": paths, "controls": controls}))
            finally:
                shutil.rmtree(runtime)
        """), str(parent_conftest), json.dumps(list(controls))],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    paths = observed["paths"]
    runtime = Path(paths["AUTORUN_TEST_RUNTIME_DIR"])
    for key, path in paths.items():
        assert path and Path(path).is_relative_to(runtime), (key, path, runtime)
    assert paths["HOME"] == paths["USERPROFILE"]
    assert Path(paths["PI_CODING_AGENT_DIR"]) == Path(paths["HOME"]) / ".pi" / "agent"
    assert observed["controls"] == controls


def test_concurrent_pytest_sessions_get_private_non_pruning_basetemp_roots(tmp_path):
    import types
    import conftest

    first = tmp_path / "session-one"
    second = tmp_path / "session-two"
    first.mkdir()
    second.mkdir()
    marker = first / "survivor.txt"
    marker.write_text("owned by session one\n", encoding="utf-8")

    first_config = types.SimpleNamespace(option=types.SimpleNamespace())
    second_config = types.SimpleNamespace(option=types.SimpleNamespace())
    first_root = conftest._configure_pytest_basetemp(first_config, first)
    second_root = conftest._configure_pytest_basetemp(second_config, second)

    assert first_root != second_root
    assert first_root.parent == second_root.parent
    assert first_root.name.startswith("ar_pytest_")
    assert second_root.name.startswith("ar_pytest_")
    assert marker.read_text(encoding="utf-8") == "owned by session one\n"
    assert first_config.option.basetemp == str(first_root)
    assert second_config.option.basetemp == str(second_root)


def test_clearing_environment_keeps_home_isolation():
    from isolated_environ import isolation_only

    preserved = isolation_only()
    for key in ("HOME", "USERPROFILE", "PI_CODING_AGENT_DIR"):
        assert preserved.get(key) == os.environ.get(key) and preserved.get(key), key


@pytest.mark.subprocess
def test_pytest_ignores_inherited_harness_and_runtime_overrides(tmp_path):
    from autorun.platforms import CLAUDE_PROJECT_DIR_ENV, PLATFORMS

    names = {name for platform in PLATFORMS.values() for name in (
        *platform.config_dir_env_vars, *platform.detect_env_vars,
        *platform.standalone_session_env_vars,
        *platform.version_env_vars,
        *(platform.task_tool_switch.forwarded_env() if platform.task_tool_switch else ()),
    )}
    names.update((CLAUDE_PROJECT_DIR_ENV, "CLAUDE_PLUGIN_ROOT", "AUTORUN_PLUGIN_ROOT", "AUTORUN_DISABLE",
                  "AUTORUN_USE_DAEMON", "UV_PROJECT", "UV_PROJECT_ENVIRONMENT",
                  "UV_WORKING_DIR", "UV_CONFIG_FILE", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR",
                  "UV_PYTHON_INSTALL_DIR", "XDG_DATA_HOME"))
    # PI's relocation variable is deliberately rehomed rather than removed.
    names.discard("PI_CODING_AGENT_DIR")
    poison = str(tmp_path / "live")
    env = dict(os.environ, **dict.fromkeys(names, poison))
    plugin = Path(__file__).parents[1]
    script = textwrap.dedent("""
        import json, os, runpy, shutil, sys
        from types import SimpleNamespace
        runpy.run_path(sys.argv[1] + "/conftest.py")
        runtime = os.environ["AUTORUN_TEST_RUNTIME_DIR"]
        try:
            config = SimpleNamespace(addinivalue_line=lambda *args: None)
            runpy.run_path(sys.argv[1] + "/tests/conftest.py")["pytest_configure"](config)
            print(json.dumps({key: os.environ.get(key) for key in json.loads(sys.argv[2])}))
        finally:
            shutil.rmtree(runtime)
    """)
    result = subprocess.run([sys.executable, "-c", script, str(plugin), json.dumps(sorted(names))],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    inherited = json.loads(result.stdout)
    assert all(value != poison for value in inherited.values()), inherited


@pytest.mark.skipif(os.name != "posix", reason="the documented recipe uses a POSIX shell")
@pytest.mark.parametrize("document", ["AGENTS.md", "plugins/autorun/docs/RUNTIME_STATE_ISOLATION.md"])
def test_documented_sandbox_recipe_drops_live_environment(tmp_path, document):
    from autorun.platforms import PLATFORMS

    repo = Path(__file__).parents[3]
    text = (repo / document).read_text(encoding="utf-8")
    recipe = next(block.split("\n", 1)[1] for block in text.split("```")[1::2]
                  if "SB=$(mktemp" in block and "--install --force" in block)
    command = "uv run --project plugins/autorun python -m autorun --install --force"
    probe = 'import json, os; print(json.dumps(dict(os.environ)))'
    recipe = recipe.replace(command, f"{shlex.quote(sys.executable)} -c {shlex.quote(probe)}")
    # Print the exact directory we own even if the probe fails, for cleanup.
    recipe = recipe.replace('mkdir -p', 'printf "%s\\n" "$SB"; mkdir -p', 1)
    names = {key for platform in PLATFORMS.values() for key in platform.config_dir_env_vars}
    names.update(("AUTORUN_PLUGIN_ROOT", "AUTORUN_DISABLE", "CLAUDE_PLUGIN_ROOT", "UV_PYTHON"))
    names.discard("PI_CODING_AGENT_DIR")  # the recipe explicitly redirects this one
    live = tmp_path / "live"
    live.mkdir()
    env = dict(os.environ, **dict.fromkeys(names, str(live)))
    result = subprocess.run(["/bin/sh", "-c", recipe], cwd=repo, env=env,
                            capture_output=True, text=True, timeout=30)
    lines = result.stdout.splitlines()
    sandbox = Path(lines[0])
    assert sandbox.name.startswith("arsb.") and sandbox.parent == Path("/tmp")
    try:
        assert result.returncode == 0, result.stderr
        child = json.loads(lines[-1])
        assert all(child.get(name) != str(live) for name in names), child.keys()
        for name in ("HOME", "USERPROFILE", "AUTORUN_HOME", "AUTORUN_TEST_STATE_DIR"):
            assert Path(child[name]).is_relative_to(sandbox), (name, child[name])
        assert list(live.iterdir()) == []
    finally:
        shutil.rmtree(sandbox)


def test_daemon_spawn_tracks_only_the_workers_pid_record(monkeypatch, tmp_path):
    import conftest
    from autorun import ipc

    manager = conftest.DaemonManager
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
    monkeypatch.setattr(manager, "_test_spawned_pids", set())
    # Both workers launch between the scans. Only this worker's record is ours.
    snapshots = iter([[], ["123", "456"]])
    monkeypatch.setattr(manager, "_get_all_daemon_pids", lambda: next(snapshots))
    monkeypatch.setattr(conftest.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(conftest.time, "sleep", lambda *args: None)
    records = []

    def own_record(path):
        records.append(path)
        return 123

    monkeypatch.setattr(ipc, "daemon_record_pid", own_record)
    assert manager.spawn_test_daemon() == "123"
    assert manager._test_spawned_pids == {"123"}
    assert records == [tmp_path / "daemon.lock"]


def test_recorded_pid_with_another_runtime_is_not_ours(monkeypatch, tmp_path):
    import conftest

    manager = conftest.DaemonManager
    monkeypatch.setattr(manager, "_test_spawned_pids", {"456"})
    monkeypatch.setattr(manager, "_daemon_home", lambda pid: str(tmp_path / "other-worker"))
    assert not manager._is_ours("456")


def test_unreadable_recorded_pid_requires_a_current_birth_validated_record(monkeypatch):
    import conftest
    from autorun import ipc

    manager = conftest.DaemonManager
    monkeypatch.setattr(manager, "_test_spawned_pids", {"456"})
    monkeypatch.setattr(manager, "_daemon_home", lambda pid: None)
    monkeypatch.setattr(ipc, "daemon_record_pid", lambda path: None)
    assert not manager._is_ours("456")


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS's 104-byte sun_path boundary")
def test_socket_path_guard_counts_encoded_bytes(monkeypatch):
    import runpy

    # Short in characters, over sun_path in UTF-8 bytes. This cannot fit under
    # a long caller TMPDIR, so only the socket-bearing root uses /tmp.
    with tempfile.TemporaryDirectory(prefix="arsb." + "é" * 30, dir="/tmp") as runtime:
        monkeypatch.setattr(tempfile, "mkdtemp", lambda *args, **kwargs: runtime)
        conftest_path = Path(__file__).parents[1] / "conftest.py"
        socket_path = str((Path(runtime) / "autorun-home" / "daemon.sock").resolve())
        assert len(socket_path) < 104 <= len(os.fsencode(socket_path))
        with mock.patch.dict(os.environ, dict(os.environ), clear=True):
            with pytest.raises(RuntimeError, match="socket path is .* bytes"):
                runpy.run_path(str(conftest_path))


@pytest.mark.parametrize("case", ["late-cleanup", "startup-loser"])
def test_lifecycle_unit_probes_leave_another_runtimes_files_alone(case, tmp_path, monkeypatch):
    import inspect

    from autorun import core, ipc
    from test_core import TestAutorunDaemon
    from test_daemon_restart_safety import TestDaemonMainLifecycleCleanup

    live = tmp_path / "stand-in"
    live.mkdir()
    files = {name: live / name for name in ("daemon.lock", "daemon.sock", "daemon.port", "daemon.token")}
    for path in files.values():
        path.write_text("other runtime", encoding="utf-8")
    monkeypatch.setattr(core, "LOCK_PATH", files["daemon.lock"])
    for name, filename in (("SOCKET_PATH", "daemon.sock"), ("PORT_FILE", "daemon.port"),
                           ("TOKEN_FILE", "daemon.token")):
        monkeypatch.setattr(ipc, name, files[filename])
    method = (
        TestAutorunDaemon().test_late_cleanup_spares_a_replacement_daemons_files
        if case == "late-cleanup"
        else TestDaemonMainLifecycleCleanup().test_start_failure_without_owned_lock_preserves_live_daemon_files
    )
    # Invoke both the old probe and its fixture-scoped replacement.
    arguments = {"monkeypatch": monkeypatch, "tmp_path": tmp_path / "probe"}
    arguments["tmp_path"].mkdir()
    method(**{name: arguments[name] for name in inspect.signature(method).parameters})
    assert {name: path.read_text(encoding="utf-8") if path.exists() else None
            for name, path in files.items()} == dict.fromkeys(files, "other runtime")
