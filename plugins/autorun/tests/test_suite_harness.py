#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Invariants of the test harness itself.

`conftest.py` isolates the suite (runtime root, daemon socket, tmux server,
harness environment) and serializes the tests that share a process-wide
resource. Those mechanisms are only worth what enforces them, and each one
here failed silently before it was pinned: a broken isolation seam does not
raise, it produces a green run whose results describe the wrong machine.
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest


def test_a_parallel_run_honours_the_xdist_groups_conftest_assigns(request):
    """`pytest -n 8` alone runs `--dist load`, which ignores `xdist_group`.

    `pytest_collection_modifyitems` marks the shelve, daemon and tmux modules
    with `xdist_group` so each resource class lands on one worker and runs
    serially there. pytest-xdist promotes `--dist` from its `"no"` default to
    `"load"` when `-n` is given (`xdist/plugin.py:pytest_cmdline_main`), and
    `load` schedules by test, not by group — so without `--dist=loadgroup` the
    grouping is inert and the marks are decoration.

    That is not a theoretical loss. Under `-n 8` without the mode,
    `test_environment_variable_handling` and two other tmux tests failed on
    `send_keys('C-m')` returning False, because several workers were driving
    the one shared private tmux server at once. They pass serially, so the
    failure reads as flake rather than as a disarmed guard.

    Asserted on `addopts` rather than on the effective mode because a test
    cannot see the effective mode: inside an xdist worker `config.option.dist`
    is `"no"` and `numprocesses` is `None` no matter what the controller was
    given, so an assertion on those passes vacuously in exactly the parallel
    run it is meant to police. `addopts` reads the same in every process.
    """
    addopts = list(request.config.getini("addopts"))

    assert "--dist=loadgroup" in addopts, (
        "addopts does not pin --dist=loadgroup, so `pytest -n N` selects "
        "--dist=load and the xdist_group marks conftest.py assigns are "
        f"ignored; the shelve, daemon and tmux tests then run concurrently. "
        f"addopts is {addopts}."
    )


@pytest.mark.skipif(os.name != "posix", reason="the tmux wrapper is a POSIX shell script")
def test_a_bare_tmux_on_path_reaches_the_suites_private_server():
    """`subprocess.run(["tmux", ...])` in a test must not reach the real server.

    Two holes met here. The wrapper that adds `-S <private socket>` was only
    written when pytest itself ran inside tmux, so a run started from an
    ordinary shell — CI, or any terminal — drove the user's default server.
    And 43 call sites across four test modules build `["tmux", ...]` by hand
    instead of going through `tmux_argv`, so even with the wrapper published as
    `AUTORUN_TMUX_BIN` they resolved plain `tmux` from `PATH`.

    Putting the wrapper first on `PATH` closes both at once and keeps closing
    them: a call site written tomorrow is isolated without knowing it exists.
    That is why this asserts on what `PATH` resolves rather than on the call
    sites — the call sites are the symptom, and there is no end to them.
    """
    import shutil

    resolved = shutil.which("tmux")
    if resolved is None:
        pytest.skip("tmux is not installed on this machine")

    runtime = Path(os.environ["AUTORUN_TEST_RUNTIME_DIR"]).resolve()

    assert Path(resolved).resolve().is_relative_to(runtime), (
        f"a bare `tmux` resolves to {resolved}, outside the suite's runtime "
        f"root {runtime}, so raw ['tmux', ...] subprocess calls reach the "
        f"real tmux server instead of the private one"
    )


def test_the_suite_never_resolves_state_to_the_developers_home():
    """Every runtime root the suite writes to is redirected by `conftest.py`.

    The three variables are set before any autorun import because module-level
    code resolves these paths once; a test process that reaches the real
    `~/.autorun` shares the developer's live daemon, socket, locks and history
    with whatever else is running on the machine.
    """
    for variable in (
        "AUTORUN_TEST_RUNTIME_DIR",
        "AUTORUN_TEST_STATE_DIR",
        "AUTORUN_HOME",
    ):
        value = os.environ.get(variable)
        assert value, f"{variable} is unset, so the suite would use real state"
        resolved = Path(value).resolve()
        assert resolved != (Path.home() / ".autorun").resolve(), (
            f"{variable} points at the live installation: {resolved}"
        )
        assert not str(resolved).startswith(str(Path.home() / ".claude")), (
            f"{variable} points inside the live Claude tree: {resolved}"
        )


def test_the_group_marks_are_applied_before_xdist_reads_them():
    """The marks have to exist by the time xdist's own collection hook runs.

    `xdist/remote.py:pytest_collection_modifyitems` is what turns an
    `xdist_group` mark into scheduling: it appends `@<group>` to each nodeid,
    and the controller's `LoadGroupScheduling` groups by that suffix. Nothing
    reads the mark afterwards.

    Without `tryfirst` on conftest's hook, xdist's ran first, saw no mark, and
    appended nothing — the marks were applied a moment later to items whose
    nodeids were already fixed. Both hooks ran, both "worked", and the tests
    still spread across every worker. Pinned here because the symptom is a
    scheduling decision no assertion in an ordinary test can see.
    """
    import conftest

    options = getattr(conftest.pytest_collection_modifyitems, "pytest_impl", {})

    assert options.get("tryfirst") is True, (
        "conftest.pytest_collection_modifyitems must be tryfirst so the "
        "xdist_group marks exist before xdist/remote.py appends the group "
        f"suffix to each nodeid; hookimpl options are {options}."
    )


def test_no_unlisted_session_id_is_shared_across_test_modules():
    """A literal session id used by two modules belongs to neither of them.

    `session_id="test"` appears in 137 contexts across six modules. Whatever
    one of them writes there — a session block, an allow, a policy — the next
    one reads, and the failure surfaces somewhere else entirely: a test
    asserting `rm` is blocked *with* its trash redirect got a bare block from a
    leftover session block written by a different file.

    `conftest.SHARED_TEST_SESSIONS` resets the ids that are shared on purpose.
    This fails when a new one appears so it is either given a unique id or
    added there, rather than becoming the next silent cross-module channel.
    """
    import ast
    import collections

    import conftest

    owners = collections.defaultdict(set)
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.keyword) or node.arg != "session_id":
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                owners[node.value.value].add(path.stem)

    shared = {
        session: sorted(modules)
        for session, modules in owners.items()
        if len(modules) > 1 and session not in conftest.SHARED_TEST_SESSIONS
    }

    assert shared == {}, (
        "these fixed session ids are written by more than one test module and "
        "are not reset between tests; give each test its own id or add the id "
        f"to conftest.SHARED_TEST_SESSIONS: {shared}"
    )


@pytest.mark.parametrize("group", ["shelve", "daemon", "tmux"])
def test_each_serialized_resource_class_names_modules_that_exist(group):
    """A renamed or deleted test module silently drops out of its group.

    The tables hold bare module stems, so a stale entry matches nothing and the
    tests that needed serializing go back to running concurrently — the same
    end state as the missing `--dist=loadgroup` above, reached a different way.
    """
    import conftest

    tables = {
        "shelve": conftest._SERIAL_SHELVE_TESTS,
        "daemon": conftest._SERIAL_DAEMON_TESTS,
        "tmux": conftest._SERIAL_TMUX_TESTS,
    }
    tests_dir = Path(__file__).parent
    missing = sorted(
        stem for stem in tables[group] if not (tests_dir / f"{stem}.py").is_file()
    )

    assert missing == [], f"{group} group names modules that no longer exist: {missing}"


@pytest.mark.subprocess
# 300s, against the module-wide `timeout = 30` in pyproject.toml: this test
# runs a whole nested pytest session, which loads the same conftest and builds
# its own runtime root.
@pytest.mark.timeout(300)
def test_a_nested_pytest_run_does_not_kill_the_outer_private_tmux_server(request):
    """`pytest_sessionfinish` kills the server whose socket it *inherited*.

    `pytest_configure` reads `AUTORUN_TEST_TMUX_SOCKET` from the environment
    and only invents a socket when it is absent, so a pytest process spawned by
    a test — `test_release_artifacts`, and this one — reuses its parent's
    private server, which is what keeps the isolation from multiplying.
    `pytest_sessionfinish` then ran `kill-server` on that socket
    unconditionally, so the child's exit destroyed the server the parent was
    still using.

    That is the whole of the tmux flake: the outer run's next tmux command
    reports `server exited unexpectedly` or `no server running on <socket>`,
    windows created moments earlier are gone, and `send_keys` returns False.
    It is invisible when the tmux tests run alone, because nothing else is
    spawning pytest at the same time, and invisible to a standalone tmux probe,
    because the killer is pytest rather than tmux.

    Ownership is the fix: the process that invented the socket tears the server
    down, and every process that inherited one leaves it alone.

    The server here is this test's own, not the suite's. Standing up a session
    on the shared one made a module outside `conftest._SERIAL_TMUX_TESTS` drive
    it concurrently with the tests that are serialized against each other, and
    a macOS runner hung `new-session` for the full sixty seconds. A private
    socket reproduces the inheritance exactly — the child is handed this path
    in its environment, which is the same way an xdist worker or a nested run
    receives one — while touching nothing another test is using.
    """
    import subprocess

    real_tmux = getattr(request.config, "_autorun_real_tmux_bin", None)
    if real_tmux is None or os.name != "posix":
        pytest.skip("no private tmux server on this platform")

    # Not `tmp_path`: `sun_path` is 104 bytes on macOS and pytest's per-test
    # directory alone exceeds it — tmux answers `File name too long`. The
    # suite's own socket is short for the same reason.
    own_root = tempfile.mkdtemp(prefix="arown", dir="/tmp" if os.path.isdir("/tmp") else None)
    socket_path = os.path.join(own_root, "s")
    argv = [real_tmux, "-f", "/dev/null", "-S", socket_path]
    env = {key: value for key, value in os.environ.items() if key != "TMUX"}
    env["AUTORUN_TEST_TMUX_SOCKET"] = socket_path

    session = "outer"
    started = subprocess.run(
        [*argv, "new-session", "-d", "-s", session],
        capture_output=True, text=True, timeout=60, env=env,
    )
    assert started.returncode == 0, started.stderr

    try:
        # A node inside this directory, so the nested run loads the same
        # `conftest.py`. A throwaway file in `tmp_path` would not, and the
        # teardown under test lives in that conftest.
        nested_target = (
            f"{Path(__file__).name}::test_the_suite_never_resolves_state_to_the_developers_home"
        )
        child = subprocess.run(
            [sys.executable, "-m", "pytest", nested_target, "-q", "-p", "no:cacheprovider"],
            capture_output=True, text=True, timeout=240,
            cwd=str(Path(__file__).parent), env=env,
        )
        assert child.returncode == 0, f"{child.returncode}\n{child.stdout}\n{child.stderr}"

        alive = subprocess.run(
            [*argv, "has-session", "-t", session],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert alive.returncode == 0, (
            "the nested pytest run tore down the tmux server whose socket it "
            f"inherited: {alive.stderr.strip()!r}"
        )
    finally:
        subprocess.run(
            [*argv, "kill-server"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        shutil.rmtree(own_root, ignore_errors=True)


@pytest.mark.parametrize(
    "kind, exists, wrapped",
    [
        ("absolute", True, True),
        ("absolute", False, False),
        ("bare", True, False),
        ("bare", False, False),
        ("empty", False, False),
    ],
)
def test_the_private_tmux_wrapper_is_only_written_for_a_real_binary(
    kind, exists, wrapped, tmp_path, monkeypatch
):
    """A wrapper named `tmux` that execs `tmux` execs itself, forever.

    `conftest.pytest_configure` writes a `tmux` shell script that adds
    `-S <private socket>` and puts its directory FIRST on `PATH`, so every
    `tmux` in the run is isolated. Its own comment records the hazard — resolve
    the real client before the wrapper shadows it — but
    `tmux_utils._candidate_tmux_binaries` ends with `or ["tmux"]`, a bare name,
    when the machine has no tmux at all. The wrapper then execs `tmux`, `PATH`
    resolves that to the wrapper, and the exec loop never terminates. A wrapper
    with that exact body had to be SIGKILLed after fifteen seconds.

    That is not hypothetical. `ci (macos-latest, 3.13)` installs no tmux — only
    the `tmux-integration` job runs `apt-get install tmux` — and the first
    non-tmux-marked test to call tmux there hung until its 60-second subprocess
    timeout, reporting `Command '['tmux', '-f', '/dev/null', '-S', ...,
    'new-session', ...]' timed out`.

    The decision belongs to one function so it can be checked without standing
    up a pytest session: a wrapper is written only for a resolved path that is
    absolute and executable. Everything else means "no private server", tests
    that need one skip, and `PATH` is left alone.

    The absolute case is built from `tmp_path` rather than written out, because
    what counts as absolute is the running platform's answer: Python 3.13's
    `ntpath.isabs` stopped treating a single leading slash as absolute, so a
    hardcoded POSIX path made this fail on Windows for a reason that had
    nothing to do with the rule under test.
    """
    import conftest

    resolved = {"absolute": str(tmp_path / "tmux"), "bare": "tmux", "empty": ""}[kind]
    monkeypatch.setattr(conftest.os, "access", lambda path, mode: exists)

    assert conftest.private_tmux_binary(resolved) == (resolved if wrapped else None)


# Modules that entered the standard library after this project's floor,
# `requires-python = ">=3.10"`. Importing one unguarded costs the whole run:
# a collection error stops pytest before any test executes, so the job reports
# an exit code and no failing test name.
STDLIB_AFTER_THE_PYTHON_FLOOR = {"tomllib": "3.11"}


def test_no_test_module_imports_a_stdlib_module_newer_than_the_python_floor():
    """A `import tomllib` that works on 3.12 is a collection error on 3.10.

    This is not a hypothetical either, and the repository had already written
    the warning down twice — `test_documentation_consistency.py:514` and `:557`
    both explain that `tomllib` is 3.11+ while autorun supports 3.10, and both
    that module and `test_hook_entry.py:1411` fall back to `tomli`. A new
    module still imported it bare, passed on this machine's 3.12, and took
    `ci (ubuntu-latest, 3.10)` down at collection: `ModuleNotFoundError: No
    module named 'tomllib'`, `1 skipped, 129 deselected, 1 error`, exit 2 —
    6,161 selected tests, none of them run.

    A comment cannot fail a build, so this does. Guard a late-stdlib import
    with `try: import tomllib / except ImportError: import tomli as tomllib`,
    or ask for the value some other way — the marker check in
    `test_real_money_gate.py` reads pytest's own config instead of parsing
    `pyproject.toml`, which is both version-proof and closer to the property
    under test.
    """
    tests_dir = Path(__file__).resolve().parent
    offenders = {}
    # Every .py here, not just `test_*.py`: `conftest.py` and `e2e_support.py`
    # are imported during collection too, and take the run down the same way.
    for module in sorted(tests_dir.rglob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only; a guarded import is nested
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in STDLIB_AFTER_THE_PYTHON_FLOOR:
                    offenders.setdefault(
                        module.relative_to(tests_dir).as_posix(), []
                    ).append((name, node.lineno))

    assert not offenders, (
        "These modules import a standard-library module that does not exist on "
        "the oldest supported Python, at module scope where the failure is a "
        "collection error rather than a test failure. Versions: "
        f"{STDLIB_AFTER_THE_PYTHON_FLOOR}. Offenders: {offenders}"
    )


@pytest.fixture
def daemon_sweep(monkeypatch, tmp_path):
    """A `DaemonManager` whose process discovery and kills are observable."""
    import conftest

    manager = conftest.DaemonManager
    mine = tmp_path / "worker-a"
    theirs = tmp_path / "worker-b"
    (mine / "autorun-home").mkdir(parents=True)
    (theirs / "autorun-home").mkdir(parents=True)
    monkeypatch.setenv("AUTORUN_TEST_RUNTIME_DIR", str(mine))
    monkeypatch.setattr(manager, "_production_pids", {"100"})
    monkeypatch.setattr(manager, "_test_spawned_pids", set())
    monkeypatch.setattr(manager, "_get_all_daemon_pids", classmethod(lambda cls: []))
    monkeypatch.setattr(manager, "_daemon_home", classmethod(lambda cls, pid: None))

    killed = []
    monkeypatch.setattr(
        manager, "_kill_pid", classmethod(lambda cls, pid: killed.append(pid))
    )

    def arrange(*, running, homes, spawned=()):
        monkeypatch.setattr(
            manager, "_get_all_daemon_pids", classmethod(lambda cls: list(running))
        )
        monkeypatch.setattr(
            manager, "_daemon_home", classmethod(lambda cls, pid: homes.get(pid))
        )
        monkeypatch.setattr(manager, "_test_spawned_pids", set(spawned))
        return manager

    arrange.mine = str(mine / "autorun-home")
    arrange.theirs = str(theirs / "autorun-home")
    arrange.killed = killed
    return arrange


def test_daemon_discovery_ignores_process_table_permission_races(monkeypatch):
    """A cleanup diagnostic must not fail the suite on one unreadable process.

    On macOS psutil can surface ``sysctl(KERN_PROCARGS2)`` permission failures
    while ``process_iter(attrs=...)`` materializes ``proc.info``, before the
    loop body can catch ``AccessDenied``. Treat that process as unobservable.
    """
    import conftest

    def permission_failure(*_args, **_kwargs):
        raise SystemError("proc_cmdline returned a result with an exception set")
        yield  # pragma: no cover - makes this the iteration-time failure seen on macOS

    monkeypatch.setattr(conftest.psutil, "process_iter", permission_failure)

    assert conftest.DaemonManager._get_all_daemon_pids() == []


def test_the_daemon_sweep_spares_a_daemon_another_worker_started(daemon_sweep):
    """`-n 8` means eight snapshots, each blind to the other workers' daemons.

    `pytest_sessionstart` runs once per xdist worker and records the daemon PIDs
    alive *at that moment* as production. Anything appearing later looked like
    "a test daemon" to every worker, so the first worker to reach
    `ensure_single_daemon` terminated daemons belonging to the other seven.

    That is not theoretical. `test_daemon_startup_race.py:472-548` spawns
    `python -c "... from autorun.daemon import main; main()"`, whose cmdline
    matches the `autorun.daemon` sweep, and a captured full-suite failure shows
    the child publishing its socket and being killed 63ms later::

        Daemon started on unix:/tmp/arduz8v7i4g/h/daemon.sock   23:07:57,047
        Received SIGTERM                                        23:07:57,110
        Daemon exited                                           23:07:57,115

    The test polls every 0.2s, so the socket existed and vanished between two
    polls: `contents: ['daemon.log']`, `child exit code: 0`, `assert None`.
    Ownership decides who may kill what — a daemon running in another worker's
    `AUTORUN_TEST_RUNTIME_DIR` is that worker's business.
    """
    manager = daemon_sweep(
        running=["100", "200", "300"],
        homes={"200": daemon_sweep.mine, "300": daemon_sweep.theirs},
    )

    manager.kill_test_daemons()

    assert daemon_sweep.killed == ["200"], (
        "the sweep must kill only this worker's daemon (200): 100 is production "
        "and 300 belongs to another worker's runtime root. "
        f"Killed: {daemon_sweep.killed}"
    )


def test_the_daemon_sweep_still_kills_one_it_spawned_with_no_readable_home(
    daemon_sweep,
):
    """Explicit spawn bookkeeping is the fallback when `environ()` is denied.

    `psutil.Process.environ()` can raise `AccessDenied`, and a daemon this
    worker started is still this worker's to clean up. Without the union a
    single unreadable environment would leak a daemon for the whole session.
    """
    manager = daemon_sweep(running=["100", "400"], homes={}, spawned={"400"})

    manager.kill_test_daemons()

    assert daemon_sweep.killed == ["400"], (
        "a daemon recorded in _test_spawned_pids must be killed even when its "
        f"environment cannot be read. Killed: {daemon_sweep.killed}"
    )
