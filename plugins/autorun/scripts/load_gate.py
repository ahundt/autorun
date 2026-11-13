"""Measure concurrent real hook processes against an isolated checkout daemon.

uv run --project plugins/autorun python plugins/autorun/scripts/load_gate.py \
    --tree . --sessions 60 --rounds 1 --cli-type claude --output /tmp/load.json
Add --baseline-tree /tmp/export --repetitions 5 for alternating 60/100/200 runs.
Latency includes process startup, hook entry, IPC, daemon dispatch and response.
No autorun imports occur in this driver; registry data comes from the target.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import closing
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import statistics
import subprocess
import tempfile
import time

import psutil  # Already a runtime dependency; the measured tree is never imported.

DEFAULT_SESSIONS = 20
DEFAULT_ROUNDS = 1
DEFAULT_REPETITIONS = 5
RELEASE_SESSIONS = (60, 100, 200)
CALL_TIMEOUT_SECONDS = 10.0
START_TIMEOUT_SECONDS = 30.0
SAMPLE_INTERVAL_SECONDS = 0.5
STOP_TIMEOUT_SECONDS = 10.0
TASK_SESSION_MODULUS = 4
DESTRUCTIVE_SESSION_MODULUS = 5
MILLISECONDS_PER_SECOND = 1000
TAIL_PERCENTILES = (90, 99)
OUTCOMES = ("allowed", "intended_denial", "unexpected_denial", "refused_or_overloaded",
            "timed_out", "fail_closed", "hook_stderr", "nonzero_exit", "invalid_response",
            "missed_guard")
BAD_OUTCOMES = OUTCOMES[2:]
FAIL_CLOSED_TEXT = ("fail.closed", "fail-closed", "blocking tool use to avoid fail-open",
                    "daemon_failure", "blocking tool use because autorun could not evaluate",
                    "blocking this tool call because",
                    "cannot bootstrap", "bootstrap in progress", "import error",
                    "runtime error", "could not connect", "unreachable")
LOAD_TEXT = ("daemon_dispatch", "daemon_response_timeout", "connection refused",
             "overloaded", "did not answer", "accepted the request", "daemon unavailable")


def checked(argv, *, env=None, cwd=None, timeout=START_TIMEOUT_SECONDS):
    return subprocess.run(argv, env=env, cwd=cwd, text=True, capture_output=True,
                          check=True, timeout=timeout)


def record_pid(path):
    if not path.exists():
        return None
    return int(path.read_text().split()[0])


def target_python(tree):
    for path in (tree / ".venv/bin/python", tree / "plugins/autorun/.venv/bin/python",
                 tree / ".venv/Scripts/python.exe", tree / "plugins/autorun/.venv/Scripts/python.exe"):
        if path.is_file():
            return path  # Do not resolve the venv symlink to the base interpreter.
    raise ValueError(f"Sync the target first: uv sync --project {tree}/plugins/autorun")


def registry(python, tree, env, cli_type):
    code = (
        "import json; from autorun.platforms import PLATFORMS; "
        f"p=PLATFORMS[{cli_type!r}]; "
        "print(json.dumps(dict(tools=dict(p.tool_names), "
        "events=dict(p.autorun_to_harness_cli_events), has_hooks=p.has_hooks, "
        "task_style=p.task_management_style, task_create=sorted(p.task_create_tools), "
        "task_update=sorted(p.task_update_tools), task_bulk=sorted(p.task_bulk_tools), "
        "task_plan=sorted(p.task_plan_tools))))"
    )
    platform = json.loads(checked([str(python), "-c", code], env=env, cwd=tree).stdout)
    if not platform["has_hooks"]:
        raise ValueError(f"{cli_type} has no hook protocol in the target registry")
    return platform


def task_calls(platform):
    """Use the target registry's actual task API, including bulk checklists."""
    tools = platform["tools"]
    style = platform["task_style"]
    if style == "task_tools":
        yield (platform["task_create"][0], {tools.get("task_title", "subject"): "Inspect project",
               "description": "Load gate task"},
               {"task": {"id": "1", "subject": "Inspect project", "status": "pending"}})
        yield (platform["task_update"][0], {tools.get("task_id_param", "taskId"): "1", "status": "completed"},
               {"task": {"id": "1", "status": "completed"}})
    elif style == "plan_checklist":
        for status in ("in_progress", "completed"):
            yield platform["task_plan"][0], {"plan": [{"step": "Inspect project", "status": status}]}, {"success": True}
    elif style == "bulk_todos":
        for status in ("in_progress", "completed"):
            yield platform["task_bulk"][0], {"todos": [{"id": "1", "content": "Inspect project", "status": status}]}, {"success": True}


def workload(session, rounds, platform, sandbox):
    """Serial harness calls within a session; sessions themselves run concurrently."""
    tools = platform["tools"]
    yield "SessionStart", {}, False
    yield "UserPromptSubmit", {"prompt": "Inspect the project and report the result."}, False
    for index in range(rounds):
        for tool, inputs in (
            (tools["bash"], {"command": "git status --short"}),
            (tools["read"], {"file_path": str(sandbox / "fixture.txt")}),
            (tools["write"], {"file_path": str(sandbox / "result.txt"), "content": "ok"}),
        ):
            fields = {"tool_name": tool, "tool_input": inputs,
                      "tool_use_id": f"tool-{session}-{index}-{tool}"}
            yield "PreToolUse", fields, False
            yield "PostToolUse", dict(fields, tool_response={"stdout": "ok", "exit_code": 0}), False
        if session % TASK_SESSION_MODULUS == 0:
            for name, inputs, response in task_calls(platform):
                fields = {"tool_name": name, "tool_input": inputs}
                yield "PreToolUse", fields, False
                yield "PostToolUse", dict(fields, tool_response=response), False
        if session % DESTRUCTIVE_SESSION_MODULUS == 0:
            yield "PreToolUse", {"tool_name": tools["bash"], "tool_input": {
                "command": f"rm -rf {sandbox}/guard-fixture"}}, True
    yield "Stop", {"stop_hook_active": False}, False


def classify(stdout, stderr, returncode, expected_denial):
    text = (stdout + "\n" + stderr).lower()
    # Infrastructure failure takes precedence over a denial and its exit-2 stderr.
    if any(token in text for token in LOAD_TEXT):
        return "refused_or_overloaded"
    if "timed out" in text or "timeout" in text:
        return "timed_out"
    if any(token in text for token in FAIL_CLOSED_TEXT):
        return "fail_closed"
    try:
        response = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        return "invalid_response"
    if not isinstance(response, dict):
        return "invalid_response"
    specific = response.get("hookSpecificOutput", {})
    if not isinstance(specific, dict):
        return "invalid_response"
    denied = (returncode == 2 or response.get("decision") in ("deny", "block")
              or specific.get("permissionDecision") == "deny"
              or response.get("continue") is False)
    if returncode not in (0, 2):
        return "nonzero_exit"
    if denied:
        return "intended_denial" if expected_denial else "unexpected_denial"
    if stderr:
        return "hook_stderr"
    if returncode:
        return "nonzero_exit"
    # A guard silently allowing its destructive fixture is also a regression.
    return "missed_guard" if expected_denial else "allowed"


def summarize(values):
    ordered = sorted(values)
    return {"p50": statistics.median(ordered),
            **{f"p{percent}": ordered[max(0, math.ceil(percent / 100 * len(ordered)) - 1)]
               for percent in TAIL_PERCENTILES}, "max": ordered[-1], "count": len(ordered)}


def holders(sandbox):
    if shutil.which("lsof") is None:
        root = sandbox.resolve()
        pids = []
        for process in psutil.process_iter():
            try:
                if any(Path(opened.path).is_relative_to(root) for opened in process.open_files()):
                    pids.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return sorted(pids)
    result = subprocess.run(["lsof", "-t", "+D", str(sandbox)], text=True, capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError(f"Cannot establish sandbox process ownership: {result.stderr}")
    return sorted({int(pid) for pid in result.stdout.split()})


def stop_owned(sandbox, live_pid):
    daemon_pid = record_pid(sandbox / "ar/daemon.lock")
    if daemon_pid is None:
        return "", []
    owned = {daemon_pid}
    try:
        owned.update(child.pid for child in psutil.Process(daemon_pid).children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    pids = sorted(owned.intersection(holders(sandbox)))
    if os.getpid() in pids or (live_pid is not None and live_pid in pids):
        raise RuntimeError(f"Refusing cleanup: unexpected file holder {pids}")
    if pids:
        # Kept in the JSON receipt instead of mixing diagnostics into JSON stdout.
        commands = (checked(["ps", "-o", "pid,command", "-p", ",".join(map(str, pids))]).stdout
                    if shutil.which("ps") else "\n".join(
                        f"{pid} {psutil.Process(pid).cmdline()}" for pid in pids))
        for pid in pids:
            try:
                if os.name == "nt":
                    psutil.Process(pid).terminate()
                else:
                    os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess, psutil.AccessDenied):
                owned.discard(pid)
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline and owned.intersection(holders(sandbox)):
            time.sleep(SAMPLE_INTERVAL_SECONDS)
        remaining = sorted(owned.intersection(holders(sandbox)))
        if remaining:
            raise RuntimeError(f"Sandbox processes did not stop; retained {sandbox}: {remaining}")
        return commands, remaining
    return "", []


async def exercise(python, tree, env, sandbox, platform, sessions, rounds, cli_type,
                   call_timeout_seconds, sample_interval_seconds, daemon_pid):
    records = []
    samples = []
    done = asyncio.Event()

    async def sample():
        def snapshot():
            process = psutil.Process(daemon_pid)
            return {"rss_bytes": process.memory_info().rss,
                    "open_files": len(process.open_files())}
        while not done.is_set():
            samples.append(await asyncio.to_thread(snapshot))
            try:
                await asyncio.wait_for(done.wait(), sample_interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def session(number):
        for event, fields, expected_denial in workload(number, rounds, platform, sandbox):
            payload = dict(fields, hook_event_name=platform["events"].get(event, event),
                           session_id=f"load-{number}", cli_type=cli_type,
                           cwd=str(sandbox), transcript_path="")
            started = time.monotonic()
            proc = await asyncio.create_subprocess_exec(str(python),
                str(tree / "plugins/autorun/hooks/hook_entry.py"), "--cli", cli_type,
                env=env, cwd=sandbox, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()),
                                                       call_timeout_seconds)
                stdout, stderr = stdout.decode(errors="replace"), stderr.decode(errors="replace")
                outcome = classify(stdout, stderr, proc.returncode, expected_denial)
            except asyncio.TimeoutError:
                proc.kill()  # This exact hook child was created above, never a daemon pattern.
                stdout_bytes, stderr_bytes = await proc.communicate()
                stdout, stderr = stdout_bytes.decode(errors="replace"), stderr_bytes.decode(errors="replace")
                outcome = "timed_out"
            records.append(dict(session=number, event=event,
                latency_ms=(time.monotonic() - started) * MILLISECONDS_PER_SECOND,
                outcome=outcome, returncode=proc.returncode, stdout=stdout, stderr=stderr))

    monitor = asyncio.create_task(sample())
    started = time.monotonic()
    try:
        await asyncio.gather(*(session(number) for number in range(sessions)))
    finally:
        done.set()
        await monitor
    return records, samples, time.monotonic() - started


def failures(result):
    return {key: result["outcomes"].get(key, 0) for key in BAD_OUTCOMES
            if result["outcomes"].get(key, 0)}


def run_load(tree, *, sessions=DEFAULT_SESSIONS, rounds=DEFAULT_ROUNDS, cli_type="claude",
             output=None, call_timeout_seconds=CALL_TIMEOUT_SECONDS,
             sample_interval_seconds=SAMPLE_INTERVAL_SECONDS, sandbox_parent=None):
    """``sandbox_parent``: directory the run's sandbox is created in; default /tmp.

    A caller that restricts which daemons it may signal (the test suite) passes
    its own runtime root, so the daemon this run starts is provably its own.
    """
    if sessions < 1 or rounds < 1 or call_timeout_seconds <= 0 or sample_interval_seconds <= 0:
        raise ValueError("Session/round counts and time intervals must be positive")
    tree = Path(tree).resolve()
    python = target_python(tree)
    live_pid = record_pid(Path(os.environ.get("AUTORUN_HOME", Path.home() / ".autorun")) / "daemon.lock")
    sandbox = Path(tempfile.mkdtemp(
        prefix="arlg.", dir=sandbox_parent or ("/tmp" if os.name != "nt" else None)))
    result = None
    try:
        env = {"PATH": os.defpath, "HOME": str(sandbox / "home"),
               "USERPROFILE": str(sandbox / "home"), "AUTORUN_HOME": str(sandbox / "ar"),
               "AUTORUN_TEST_STATE_DIR": str(sandbox / "state"),
               "PI_CODING_AGENT_DIR": str(sandbox / "home/.pi/agent"),
               "PYTHONPATH": str(tree / "plugins/autorun/src"),
               "AUTORUN_PLUGIN_ROOT": str(tree / "plugins/autorun"),
               "CLAUDE_PLUGIN_ROOT": str(tree / "plugins/autorun")}
        env["UV_CACHE_DIR"] = str(sandbox / "uv-cache")
        for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            if name in os.environ:
                env[name] = os.environ[name]
        for directory in ("home", "ar", "state"):
            (sandbox / directory).mkdir(mode=0o700)
        (sandbox / "ar/autorun.config.json").write_text(json.dumps({"state_backend": "sqlite"}))
        (sandbox / "fixture.txt").write_text("load gate fixture\n")
        platform = registry(python, tree, env, cli_type)
        load_average_start = os.getloadavg() if hasattr(os, "getloadavg") else None
        # Normal CLI hook dispatch starts this runtime without scanning or stopping
        # other daemons using the same checkout (as --restart-daemon would).
        startup = subprocess.run([shutil.which("uv") or "uv", "run", "--quiet", "--no-sync",
            "--project", str(tree / "plugins/autorun"), "python", "-m", "autorun"], env=env, cwd=sandbox,
            input=json.dumps({"hook_event_name": "SessionStart", "session_id": "warmup",
                              "cli_type": cli_type, "cwd": str(sandbox)}),
            text=True, capture_output=True, check=True, timeout=START_TIMEOUT_SECONDS)
        daemon_pid = record_pid(sandbox / "ar/daemon.lock")
        if daemon_pid is None:
            raise RuntimeError(f"CLI did not publish a daemon lock: {startup.stdout} {startup.stderr}")
        records, samples, wall = asyncio.run(exercise(python, tree, env, sandbox, platform,
            sessions, rounds, cli_type, call_timeout_seconds, sample_interval_seconds, daemon_pid))
        logs = "\n".join(path.read_text(errors="replace") for path in (sandbox / "ar").glob("daemon*.log*"))
        outcomes = Counter(record["outcome"] for record in records)
        with closing(sqlite3.connect(f"file:{sandbox}/state/daemon_state.sqlite3?mode=ro", uri=True)) as db:
            task_status_counts = dict(db.execute("SELECT status, count(*) FROM tasks GROUP BY status"))
        events = sorted({record["event"] for record in records})
        result = dict(tree=str(tree), sessions=sessions, rounds=rounds, cli_type=cli_type,
            load_average_start=load_average_start, wall_seconds=wall,
            outcomes={key: outcomes[key] for key in OUTCOMES},
            latency_ms={event: summarize([r["latency_ms"] for r in records
                        if event == "all" or r["event"] == event]) for event in ["all", *events]},
            daemon_peak_rss_bytes=max(s["rss_bytes"] for s in samples),
            daemon_peak_open_files=max(s["open_files"] for s in samples), resource_samples=samples,
            daemon_log_error_count=logs.count("ERROR"),
            daemon_log_traceback_count=logs.count("Traceback (most recent call last)"),
            task_status_counts=task_status_counts,
            daemon_startup_stdout=startup.stdout, daemon_startup_stderr=startup.stderr,
            calls=records)
    finally:
        commands, remaining = stop_owned(sandbox, live_pid)
        if trash := shutil.which("trash"):
            checked([trash, str(sandbox)])
        else:
            shutil.rmtree(sandbox)  # Only the exact fresh directory created by this call.
        if result is not None:
            result.update(cleanup_commands=commands, cleanup_remaining_pids=remaining)
    if output is not None:
        Path(output).write_text(json.dumps(result, indent=2) + "\n")
    return result


def run_paired(baseline, candidate, *, sessions=RELEASE_SESSIONS,
               repetitions=DEFAULT_REPETITIONS, rounds=DEFAULT_ROUNDS, cli_type="claude",
               output_dir):
    if repetitions < 1 or not sessions:
        raise ValueError("A paired comparison needs positive repetitions and session counts")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for count in sessions:
        for repetition in range(repetitions):
            for label, tree in (("known_good", baseline), ("candidate", candidate)):
                row = run_load(tree, sessions=count, rounds=rounds, cli_type=cli_type)
                row.update(label=label, repetition=repetition + 1)
                path = output_dir / f"{count}-{repetition + 1}-{label}.json"
                path.write_text(json.dumps(row, indent=2) + "\n")
                rows.append(row)
                print(json.dumps({k: v for k, v in row.items() if k not in ("calls", "resource_samples")}), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--cli-type", default="claude")
    parser.add_argument("--output", type=Path, required=True, help="JSON file, or directory for paired runs")
    parser.add_argument("--baseline-tree", type=Path)
    parser.add_argument("--paired-sessions", type=int, nargs="+", default=RELEASE_SESSIONS)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    args = parser.parse_args()
    if args.baseline_tree:
        rows = run_paired(args.baseline_tree, args.tree, sessions=args.paired_sessions,
                          repetitions=args.repetitions, rounds=args.rounds,
                          cli_type=args.cli_type, output_dir=args.output)
    else:
        rows = [run_load(args.tree, sessions=args.sessions, rounds=args.rounds,
                         cli_type=args.cli_type, output=args.output)]
        print(json.dumps(rows[0]))
    return int(any(failures(row) for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
