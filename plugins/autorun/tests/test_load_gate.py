"""Cheap process-level load regression; the release-sized matrix is opt-in."""
import importlib.util
import os
import statistics
from pathlib import Path

import pytest
from autorun.platforms import hook_platforms

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "load_gate", ROOT / "plugins/autorun/scripts/load_gate.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_small_hook_process_load():
    # Inside the worker's runtime root: conftest refuses to signal a daemon
    # whose AUTORUN_HOME is anywhere else, and run_load stops its own daemon.
    result = gate.run_load(ROOT, sessions=20, rounds=1, cli_type="claude",
                           sandbox_parent=os.environ["AUTORUN_TEST_RUNTIME_DIR"])
    assert result["calls"]
    assert not gate.failures(result), result["outcomes"]
    assert result["outcomes"]["intended_denial"] > 0
    assert result["daemon_peak_rss_bytes"] > 0
    assert result["daemon_peak_open_files"] > 0
    assert result["cleanup_remaining_pids"] == []
    assert result["task_status_counts"] == {"completed": 5}


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
def test_paired_release_load(tmp_path):
    # The explicitly requested opt-in follows test_state_store_benchmark.py.
    if os.environ.get("AUTORUN_ENABLE_LOAD_BENCHMARK") != "1":
        pytest.skip("Set AUTORUN_ENABLE_LOAD_BENCHMARK=1 and AUTORUN_TEST_LOAD_BASELINE_TREE")
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
