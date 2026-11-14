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


def test_degraded_allow_is_not_a_load_failure():
    import json
    notice = "[AR_EVENT_V1:daemon_response_timeout] [AR_EVENT_V1:degraded_decision] degraded"
    for response in ({"hookSpecificOutput": {"permissionDecision": "allow", "permissionDecisionReason": notice}},
                     {"decision": "allow", "reason": notice},
                     {"hookSpecificOutput": {"additionalContext": notice}}):
        assert gate.classify(json.dumps(response), "", 0, False) == "degraded_allow"
        assert gate.classify(json.dumps(response), "", 0, True) == "missed_guard"
    assert "degraded_allow" in gate.OUTCOMES
    assert "degraded_allow" not in gate.BAD_OUTCOMES


def test_degraded_exit2_denial_is_intended_not_hook_stderr():
    import json
    reason = "[autorun] [AR_EVENT_V1:daemon_response_timeout] [AR_EVENT_V1:degraded_decision] guarded"
    response = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": reason}}
    assert gate.classify(json.dumps(response), reason + "\n", 2, True) == "intended_denial"
    assert gate.classify(json.dumps(response), reason + "\n", 2, False) == "unexpected_denial"


def test_degraded_exit_zero_denial_cannot_hide_accidental_stderr():
    import json
    response = {"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason":
        "[autorun] [AR_EVENT_V1:daemon_response_timeout] [AR_EVENT_V1:degraded_decision] guarded"}}
    assert gate.classify(json.dumps(response), "unexpected hook warning", 0, True) == "hook_stderr"


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_cleanup_signals_only_the_started_daemon_and_its_descendants(tmp_path, monkeypatch, platform):
    (tmp_path / "ar").mkdir()
    (tmp_path / "ar/daemon.lock").write_text("11", encoding="utf-8")
    held = {11, 12, 13}
    signaled, waited = [], []
    # Windows open_files can omit even the daemon: ownership comes from its
    # sandbox PID record and descendants, never from a foreign file holder.
    monkeypatch.setattr(gate, "holders", lambda _: [13] if platform == "nt" else sorted(held))
    monkeypatch.setattr(gate, "checked", lambda *a, **kw: SimpleNamespace(stdout="daemon"))

    def terminate(pid, sig=None):
        signaled.append(pid)
        if platform == "posix":
            held.remove(pid)

    def wait(pid, timeout):
        assert pid in signaled and timeout == gate.STOP_TIMEOUT_SECONDS
        waited.append(pid)
        held.remove(pid)

    monkeypatch.setattr(gate.psutil, "Process", lambda pid: SimpleNamespace(
        children=lambda recursive: [SimpleNamespace(pid=12)],
        cmdline=lambda: ["daemon"], environ=lambda: {"AUTORUN_HOME": str(tmp_path / "ar")},
        terminate=lambda: terminate(pid), wait=lambda timeout: wait(pid, timeout),
    ))
    monkeypatch.setattr(gate, "os", SimpleNamespace(name=platform, getpid=os.getpid, kill=terminate))
    _, remaining = gate.stop_owned(tmp_path, None)
    assert signaled == [11, 12]
    assert waited == ([11, 12] if platform == "nt" else [])
    assert held == {13}
    assert remaining == []


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_cleanup_treats_permission_denied_as_not_owned(tmp_path, monkeypatch, platform):
    (tmp_path / "ar").mkdir()
    (tmp_path / "ar/daemon.lock").write_text("11", encoding="utf-8")
    monkeypatch.setattr(gate, "holders", lambda _: [11])
    monkeypatch.setattr(gate, "checked", lambda *a, **kw: SimpleNamespace(stdout="daemon"))

    def denied(*_):
        raise PermissionError("not ours")

    monkeypatch.setattr(gate.psutil, "Process", lambda _: SimpleNamespace(
        children=lambda recursive: [], cmdline=lambda: ["daemon"],
        environ=lambda: {"AUTORUN_HOME": str(tmp_path / "ar")}, terminate=denied))
    monkeypatch.setattr(gate, "os", SimpleNamespace(name=platform, getpid=os.getpid, kill=denied))
    assert gate.stop_owned(tmp_path, None)[1] == []


def test_windows_cleanup_retains_sandbox_if_termination_does_not_finish(tmp_path, monkeypatch):
    (tmp_path / "ar").mkdir()
    (tmp_path / "ar/daemon.lock").write_text("11", encoding="utf-8")
    monkeypatch.setattr(gate, "holders", lambda _: [])
    monkeypatch.setattr(gate, "checked", lambda *a, **kw: SimpleNamespace(stdout="daemon"))
    monkeypatch.setattr(gate, "os", SimpleNamespace(name="nt", getpid=os.getpid))

    def wait(timeout):
        raise gate.psutil.TimeoutExpired(timeout, pid=11)

    monkeypatch.setattr(gate.psutil, "Process", lambda _: SimpleNamespace(
        children=lambda recursive: [], cmdline=lambda: ["daemon"],
        environ=lambda: {"AUTORUN_HOME": str(tmp_path / "ar")}, terminate=lambda: None, wait=wait))
    with pytest.raises(RuntimeError, match="retained"):
        gate.stop_owned(tmp_path, None)
    assert (tmp_path / "ar/daemon.lock").exists()


def test_windows_cleanup_refuses_a_recorded_pid_from_another_runtime(tmp_path, monkeypatch):
    (tmp_path / "ar").mkdir()
    (tmp_path / "ar/daemon.lock").write_text("11", encoding="utf-8")
    monkeypatch.setattr(gate, "holders", lambda _: [11])
    monkeypatch.setattr(gate, "checked", lambda *a, **kw: SimpleNamespace(stdout="daemon"))
    monkeypatch.setattr(gate, "os", SimpleNamespace(name="nt", getpid=os.getpid))

    def unexpected():
        pytest.fail("a foreign runtime's PID was terminated")

    monkeypatch.setattr(gate.psutil, "Process", lambda _: SimpleNamespace(
        children=lambda recursive: [], cmdline=lambda: ["daemon"],
        environ=lambda: {"AUTORUN_HOME": str(tmp_path / "foreign")}, terminate=unexpected))
    assert gate.stop_owned(tmp_path, None)[1] == []


@pytest.fixture(autouse=True)
def isolated_trash(tmp_path, monkeypatch):
    """Keep benchmark cleanup in the test sandbox instead of the desktop Trash."""
    checked = gate.checked
    def run(argv, **kwargs):
        if Path(argv[0]).name == "trash":
            destination = tmp_path / "trash"
            destination.mkdir(exist_ok=True)
            for path in argv[1:]:
                gate.shutil.move(str(path), str(destination))
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return checked(argv, **kwargs)
    monkeypatch.setattr(gate, "checked", run)


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
    rows = gate.run_paired(baseline, ROOT, sessions=gate.RELEASE_SESSIONS, repetitions=5,
                           rounds=1, cli_type="claude", output_dir=tmp_path)
    assert all(not gate.failures(row) for row in rows)
    for sessions in gate.RELEASE_SESSIONS:
        good = [row["latency_ms"]["all"]["p50"] for row in rows
                if row["label"] == "known_good" and row["sessions"] == sessions]
        candidate = [row["latency_ms"]["all"]["p50"] for row in rows
                     if row["label"] == "candidate" and row["sessions"] == sessions]
        assert min(good) <= statistics.median(candidate) <= max(good)
