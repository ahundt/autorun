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

import os
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
