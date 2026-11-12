"""Cheap process-level load regression; the release-sized matrix is opt-in."""
import importlib.util
import os
import statistics
from pathlib import Path
from types import SimpleNamespace

import pytest
from autorun.platforms import hook_platforms

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "load_gate", ROOT / "plugins/autorun/scripts/load_gate.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
LOAD_BENCHMARK = pytest.mark.skipif(
    os.environ.get("AUTORUN_ENABLE_LOAD_BENCHMARK") != "1",
    reason="Set AUTORUN_ENABLE_LOAD_BENCHMARK=1 to measure concurrent hook load",
)


def test_cleanup_signals_only_the_started_daemon_and_its_descendants(tmp_path, monkeypatch):
    (tmp_path / "ar").mkdir()
    (tmp_path / "ar/daemon.lock").write_text("11")
    held = {11, 12, 13}
    signaled = []
    monkeypatch.setattr(gate, "holders", lambda _: sorted(held))
    monkeypatch.setattr(gate.psutil, "Process", lambda _: SimpleNamespace(
        children=lambda recursive: [SimpleNamespace(pid=12)]))
    monkeypatch.setattr(gate, "checked", lambda *a, **kw: SimpleNamespace(stdout="daemon"))

    def terminate(pid, sig):
        signaled.append(pid)
        held.remove(pid)

    monkeypatch.setattr(gate.os, "kill", terminate)
    _, remaining = gate.stop_owned(tmp_path, None)
    assert signaled == [11, 12]
    assert held == {13}
    assert remaining == []


def test_cleanup_treats_permission_denied_as_not_owned(tmp_path, monkeypatch):
    (tmp_path / "ar").mkdir()
    (tmp_path / "ar/daemon.lock").write_text("11")
    monkeypatch.setattr(gate, "holders", lambda _: [11])
    monkeypatch.setattr(gate.psutil, "Process", lambda _: SimpleNamespace(children=lambda recursive: []))
    monkeypatch.setattr(gate, "checked", lambda *a, **kw: SimpleNamespace(stdout="daemon"))

    def denied(*_):
        raise PermissionError("not ours")

    monkeypatch.setattr(gate.os, "kill", denied)
    assert gate.stop_owned(tmp_path, None)[1] == []


def test_load_gate_protects_the_configured_outer_daemon(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    monkeypatch.setenv("AUTORUN_HOME", str(outer))
    paths = []
    monkeypatch.setattr(gate, "record_pid", lambda path: paths.append(path))
    monkeypatch.setattr(gate, "holders", lambda _: [])

    def fail(*_):
        raise RuntimeError("before startup")

    monkeypatch.setattr(gate, "registry", fail)
    with pytest.raises(RuntimeError, match="before startup"):
        gate.run_load(ROOT, sandbox_parent=tmp_path)
    assert paths[0] == outer / "daemon.lock"
    assert not list(tmp_path.glob("arlg.*"))


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_load_failure_stops_its_daemon_and_removes_its_sandbox(monkeypatch, error):
    observed = {}

    async def fail(python, tree, env, sandbox, platform, sessions, rounds, cli_type,
                   call_timeout_seconds, sample_interval_seconds, daemon_pid):
        observed.update(sandbox=sandbox, daemon=gate.psutil.Process(daemon_pid))
        assert observed["daemon"].is_running()
        raise error("mid-run")

    monkeypatch.setattr(gate, "exercise", fail)
    with pytest.raises(error, match="mid-run"):
        gate.run_load(ROOT, sessions=1, sandbox_parent=os.environ["AUTORUN_TEST_RUNTIME_DIR"])
    assert not observed["sandbox"].exists()
    assert not observed["daemon"].is_running()


@pytest.mark.parametrize("sessions", [
    1,
    pytest.param(20, marks=(pytest.mark.benchmark, pytest.mark.slow, LOAD_BENCHMARK)),
])
def test_small_hook_process_load(sessions):
    # Inside the worker's runtime root: conftest refuses to signal a daemon
    # whose AUTORUN_HOME is anywhere else, and run_load stops its own daemon.
    result = gate.run_load(ROOT, sessions=sessions, rounds=1, cli_type="claude",
                           sandbox_parent=os.environ["AUTORUN_TEST_RUNTIME_DIR"])
    assert len(result["calls"]) <= 20 * sessions
    assert result["calls"]
    assert not gate.failures(result), result["outcomes"]
    assert result["outcomes"]["intended_denial"] > 0
    assert result["daemon_peak_rss_bytes"] > 0
    assert result["daemon_peak_open_files"] > 0
    assert result["cleanup_remaining_pids"] == []
    assert result["task_status_counts"] == {"completed": len(range(0, sessions, gate.TASK_SESSION_MODULUS))}


@pytest.mark.parametrize("stdout,stderr,code,expected,outcome", [
    ('{"decision":"deny"}', "policy", 2, True, "intended_denial"),
    ('{"decision":"deny"}', "Blocking tool use to avoid fail-open", 2, True, "fail_closed"),
    ('{}', "daemon_dispatch_stalled", 2, True, "refused_or_overloaded"),
    ('{}', "CLI timed out", 0, False, "timed_out"),
    ('{}', "warning", 0, False, "hook_stderr"),
    ('{"decision":"deny"}', "", 3, True, "nonzero_exit"),
    ('garbled', "", 0, False, "invalid_response"),
    ('{"hookSpecificOutput":null}', "", 0, False, "invalid_response"),
    ('{}', "", 0, True, "missed_guard"),
])
def test_outcome_classification(stdout, stderr, code, expected, outcome):
    assert gate.classify(stdout, stderr, code, expected) == outcome


@pytest.mark.parametrize("platform", hook_platforms(), ids=lambda p: p.name)
def test_task_workload_uses_registered_tools(platform):
    data = {"tools": dict(platform.tool_names), "task_style": platform.task_management_style,
            **{key: sorted(getattr(platform, key + "_tools"))
               for key in ("task_create", "task_update", "task_bulk", "task_plan")}}
    registered = (platform.task_create_tools | platform.task_update_tools
                  | platform.task_bulk_tools | platform.task_plan_tools)
    calls = list(gate.task_calls(data))
    assert all(name in registered for name, inputs, response in calls)
    assert bool(calls) == bool(registered)


@pytest.mark.benchmark
@pytest.mark.slow
@LOAD_BENCHMARK
def test_paired_release_load(tmp_path):
    baseline = Path(os.environ["AUTORUN_TEST_LOAD_BASELINE_TREE"])
    rows = gate.run_paired(baseline, ROOT, sessions=(60, 100, 200), repetitions=5,
                           rounds=1, cli_type="claude", output_dir=tmp_path)
    assert all(not gate.failures(row) for row in rows)
    for sessions in (60, 100, 200):
        good = [row["latency_ms"]["all"]["p50"] for row in rows
                if row["label"] == "known_good" and row["sessions"] == sessions]
        candidate = [row["latency_ms"]["all"]["p50"] for row in rows
                     if row["label"] == "candidate" and row["sessions"] == sessions]
        assert min(good) <= statistics.median(candidate) <= max(good)
