"""Capacity regression: 60–300 coroutines, one real daemon, no AI calls.

The burst count is the smaller of two measurements, each keeping deadline
headroom on slower hosts: in-process SessionStart CPU cost, and the slowest
answer to a 60-session start burst sent to the real daemon. The second one
sees what CPU time cannot: transport, thread scheduling and disk sync, which
on a shared Windows runner cost more than the handlers do. The CPU test still
requires all 300 starts to fit the full deadline on every host.

Requests use client.prepare_payload_for_daemon and forward_prepared_payload,
including TCP authentication, warm connection retries and fail-closed handling.
Only stdin, hook-process startup and final stdout printing are omitted. Test-only
instrumentation adds a durable counter and observations after real dispatch;
it never replaces guards, task handlers, dispatch limits or persistence.
"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

from filelock import FileLock

import psutil
import pytest

from autorun import client, core, ipc
from autorun.platforms import PLATFORMS
from autorun.config import harness_setting

SPEC = importlib.util.spec_from_file_location(
    "capacity_load_gate", Path(__file__).parents[1] / "scripts/load_gate.py"
)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
# Match the existing load gate: this capacity claim is for SQLite.
DAEMON_CONFIG = {"state_backend": "sqlite"}
SESSIONS = 300
PROBE_SESSIONS = 60  # also the smallest burst the test ever runs
INCREMENTS = 3
FD_MARGIN = 4
THREAD_MARGIN = 2
# Two transport runs measured <18 MiB growth. Four times that rounded up
# to the next power of two leaves platform/allocator slack without hiding leaks.
RSS_GROWTH_BYTES = 128 * 1024 * 1024


def install_probe():
    """Run only in the child; use actual EventContext state and task owners."""
    from autorun.task_lifecycle import TaskLifecycle

    original = core.app._dispatch_unbatched

    def dispatch(ctx):
        if ctx.tool_input.get("_capacity_restart"):
            (ipc.AUTORUN_CONFIG_DIR / "restart-in-flight").touch()
            time.sleep(0.02)  # Keep a real dispatch in flight at shutdown.
        response = original(ctx)
        if ctx.tool_name == "Bash" and ctx.event == "PostToolUse":
            ctx.state_update("capacity_counter", lambda n: n + 1, 0)
            ctx.state_set("capacity_owner", ctx.session_id)
        response = dict(response or {})
        response["_capacity"] = {"session": ctx.session_id, "cwd": ctx.cwd,
                                 "pid": ctx.harness_pid}
        if ctx.prompt == "capacity-inspect":
            store = object.__getattribute__(ctx, "_store")
            response["_capacity"].update(
                counter=ctx.state_get("capacity_counter", 0),
                owner=ctx.state_get("capacity_owner"),
                tasks=TaskLifecycle(ctx=ctx).tasks,
                volatile_entries=len(store._volatile),
                volatile_bytes=store._volatile_bytes,
                loaded_sessions=len(store._loaded_sessions),
            )
        return response

    core.app._dispatch_unbatched = dispatch
    original_start = core.AutorunDaemon.start

    async def start(self):
        # Executor threads persist for the daemon lifetime. Warm the real pool
        # before the resource baseline so first-use allocation is not a leak.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: None)
        workers = loop._default_executor._max_workers
        barrier = threading.Barrier(workers + 1, timeout=10)
        futures = [loop.run_in_executor(None, barrier.wait) for _ in range(workers)]
        barrier.wait()
        await asyncio.gather(*futures)
        await original_start(self)

    core.AutorunDaemon.start = start


DAEMON_SETUP = (
    "import runpy; "
    f"runpy.run_path({str(Path(__file__).resolve())!r})['install_probe']()"
)


def resources(process):
    return {"fds": process.num_handles() if os.name == "nt" else process.num_fds(),
            "threads": process.num_threads(), "rss": process.memory_info().rss}


def platform():
    p = PLATFORMS["claude"]
    return dict(tools=dict(p.tool_names), task_style=p.task_management_style,
                task_create=sorted(p.task_create_tools), task_update=sorted(p.task_update_tools))


def wall_clock_is_unreliable():
    """Why the production deadlines cannot be measured here, or None.

    The deadlines are wall-clock. Sharing cores with other xdist workers, or
    with a host whose runnable processes outnumber its cores, measures the
    host instead of the daemon. CI runs this module serially on a quiet
    runner and always measures.
    """
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return "running beside other xdist workers; run this module alone"
    if os.environ.get("CI") or not hasattr(os, "getloadavg"):
        return None
    load, cores = os.getloadavg()[0], os.cpu_count()
    if load > cores:
        return f"load average {load:.0f} on {cores} cores; rerun on a quieter host"
    return None


@pytest.mark.parametrize("spent, expected", [(0.5, 300), (0.8, 300), (1.6, 150), (8, 60)])
def test_burst_session_count_preserves_start_margin(spent, expected):
    assert burst_session_count(spent, 2) == expected


# Three bursts and a restart: each wait inside is bounded (20 s per burst, 10 s
# for the old daemon to exit), so the whole test needs more than the suite's 30 s.
@pytest.mark.timeout(150)
def test_two_bursts_and_restart(running_daemon, record_property, monkeypatch, sqlite_manager):
    if reason := wall_clock_is_unreliable():
        pytest.skip(reason)
    start_deadline = core.CONFIG["daemon_dispatch_timeouts_seconds"]["SessionStart"]
    start_cpu_seconds = measure_session_start_cpu(enough=0.4 * start_deadline)
    sessions = burst_session_count(start_cpu_seconds, start_deadline)
    _, home, proc = running_daemon
    process = psutil.Process(proc.daemon_pid)
    baseline = resources(process)
    samples = [baseline]
    after_bursts = []
    latencies = {}  # slowest request per event, and during the restart
    spawn_attempts = []

    def forbidden_spawn(*args, **kwargs):
        spawn_attempts.append(args)
        raise AssertionError("client attempted to spawn another daemon")

    monkeypatch.setattr(client, "subprocess", SimpleNamespace(
        Popen=forbidden_spawn, DEVNULL=client.subprocess.DEVNULL))
    monkeypatch.setattr(client, "output_hook_response", lambda response, **kwargs: response)
    started = time.monotonic()

    def daemon_log_tail(lines=40):
        logs = [home / "daemon.log", home / "output.log"]
        return "\n".join(
            "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
            for log in logs if log.exists())

    def identity(index):
        return {"session_id": f"capacity-{index}", "cwd": str(home / f"project-{index}"),
                "_pid": 1_000_000 + index, "_pid_started_at_units": index + 1,
                "cli_type": "claude"}

    async def request(index, event, fields=None, deny=False, restarting=False):
        begin = time.monotonic()
        payload, cli = client.prepare_payload_for_daemon(
            dict(identity(index), hook_event_name=event, **(fields or {})))
        response = await client.forward_prepared_payload(payload, cli)
        elapsed = time.monotonic() - begin
        latencies["restart" if restarting else event] = max(
            elapsed, latencies.get("restart" if restarting else event, 0.0))
        assert elapsed < float(harness_setting("hook_wrapper_timeouts_seconds", cli)), f"{index} {event}: hook budget {elapsed}"
        assert isinstance(response, dict), f"{index} {event}: malformed {response!r}"
        outcome = gate.classify(json.dumps(response), "", 0, deny)
        if restarting and outcome in {"refused_or_overloaded", "fail_closed"}:
            specific = response.get("hookSpecificOutput", {})
            assert specific.get("permissionDecision") == "deny", f"{index}: restart failed open {response}"
            assert any(guidance in json.dumps(response) for guidance in (
                client.load_guidance(), client.RESPONSE_TIMEOUT_GUIDANCE,
            )), f"{index}: no wait/retry guidance {response}"
            return response
        expected = "intended_denial" if deny else "allowed"
        assert outcome == expected, f"{index} {event} {fields}: {outcome}, expected {expected}: {response}"
        probe = response.get("_capacity", {})
        assert probe == dict(probe, session=f"capacity-{index}", cwd=identity(index)["cwd"],
                             pid=identity(index)["_pid"]), f"{index}: wrong-session reply {probe}"
        return response

    async def inspect(index, expected_count=INCREMENTS):
        response = await request(index, "UserPromptSubmit", {"prompt": "capacity-inspect"})
        probe = response["_capacity"]
        assert probe["counter"] == expected_count, f"{index}: lost counter update {probe}"
        assert probe["owner"] == f"capacity-{index}", f"{index}: cross-session owner {probe}"
        tasks = probe["tasks"]
        assert set(tasks) == ({"1"} if index % gate.TASK_SESSION_MODULUS == 0 else set()), f"{index}: task isolation {tasks}"
        assert all(t["status"] == "completed" and t["subject"] == f"capacity-{index}" for t in tasks.values()), f"{index}: lost task update {tasks}"
        assert probe["volatile_entries"] <= core.VOLATILE_MAX_ENTRIES
        assert probe["volatile_bytes"] <= core.VOLATILE_MAX_BYTES
        # Durable hydration is documented as daemon-lifetime caching, not
        # Stop-time eviction. Only session/global namespaces may be retained.
        assert probe["loaded_sessions"] <= 2 * (2 * sessions + PROBE_SESSIONS) + 16

    async def session(index, barrier, first_turn):
        await barrier.wait()
        for event, fields, deny in gate.workload(index, 1, platform(), home / f"project-{index}"):
            if event != "SessionStart":
                # A harness sends nothing after SessionStart until its model
                # answers, which takes longer than the whole start wave. With
                # no pause at all, the earliest sessions' tool traffic would
                # queue ahead of the latest sessions' starts.
                await first_turn.wait()
            if fields.get("tool_name") == "TaskCreate":
                fields["tool_input"]["subject"] = f"capacity-{index}"
                if "tool_response" in fields:
                    fields["tool_response"]["task"]["subject"] = f"capacity-{index}"
            # Keep the required shell/task sequence from the shared workload;
            # file-policy probes belong to load_gate's process benchmark.
            if fields.get("tool_name") in {"Read", "Write"} or deny:
                continue
            if event == "Stop":
                for _ in range(INCREMENTS - 1):
                    await request(index, "PostToolUse", {"tool_name": "Bash", "tool_input": {"command": "echo capacity-tick"}})
                await inspect(index)
            try:
                await request(index, event, fields, deny)
            finally:
                if event == "SessionStart":
                    first_turn.started += 1
                    if first_turn.started == sessions:
                        first_turn.set()
            if event == "PreToolUse" and fields.get("tool_name") == "Bash":
                await request(index, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}, True)

    async def burst(offset):
        barrier, first_turn = asyncio.Event(), asyncio.Event()
        first_turn.started = 0
        tasks = [asyncio.create_task(session(offset + i, barrier, first_turn)) for i in range(sessions)]
        await asyncio.sleep(0)
        assert sum(not task.done() for task in tasks) == sessions
        barrier.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=20)
        errors = [result for result in results if isinstance(result, BaseException)]
        assert not errors, (
            f"{len(errors)} session failures; first: {errors[:3]}\n"
            f"slowest so far: {latencies}\ndaemon log tail:\n{daemon_log_tail()}")

    calibration = {}

    async def run():
        nonlocal sessions
        await asyncio.gather(*(request(3 * SESSIONS + i, "SessionStart") for i in range(PROBE_SESSIONS)))
        probe_seconds = latencies.pop("SessionStart")
        sessions = min(sessions, wall_clock_session_count(probe_seconds, start_deadline))
        calibration.update(
            sessions=sessions, start_cpu_seconds=start_cpu_seconds,
            probe_sessions=PROBE_SESSIONS, probe_slowest_seconds=round(probe_seconds, 3),
            start_deadline_seconds=start_deadline, start_deadline_fraction=0.4)
        print("CAPACITY_CALIBRATION " + json.dumps(calibration))
        sampling = True

        async def sample():
            while sampling:
                try:
                    samples.append(resources(process))
                except psutil.Error:
                    # The daemon exits during the restart. Linux then answers
                    # AccessDenied for /proc/<pid>/fd, not NoSuchProcess.
                    return
                await asyncio.sleep(0.01)

        sampler = asyncio.create_task(sample())
        try:
            # Catch even a small per-request leak before a large burst can
            # fail for a secondary reason such as descriptor exhaustion.
            for _ in range(FD_MARGIN + 2):
                await request(2 * sessions + 1, "UserPromptSubmit", {"prompt": "connection canary"})
            await asyncio.sleep(0.05)
            canary = resources(process)
            assert canary["fds"] <= baseline["fds"] + FD_MARGIN, f"leaked descriptors: {baseline} -> {canary}"
            for offset in (0, sessions):
                await burst(offset)
                await asyncio.sleep(0.1)
                after = resources(process)
                after_bursts.append(after)
                assert after["fds"] <= baseline["fds"] + FD_MARGIN, f"leaked descriptors: {baseline} -> {after}"
                assert after["threads"] <= baseline["threads"] + THREAD_MARGIN, f"leaked threads: {baseline} -> {after}"
            # Launch guarded requests behind the same barrier, then request
            # authenticated graceful shutdown while connections are in flight.
            barrier = asyncio.Event()

            async def during_restart(i):
                await barrier.wait()
                return await request(i, "PreToolUse", {"tool_name": "Bash", "tool_input": {"command": "rm -rf x", "_capacity_restart": True}}, True, restarting=True)

            pending = [asyncio.create_task(during_restart(i)) for i in range(sessions)]
            barrier.set()
            deadline = time.monotonic() + 2
            while not (home / "restart-in-flight").exists():
                assert time.monotonic() < deadline, "restart missed real daemon dispatch"
                await asyncio.sleep(0.002)
            assert any(not task.done() for task in pending), "restart missed the burst"
            with FileLock(str(ipc.daemon_restart_lock_path()), timeout=1):
                assert await asyncio.to_thread(ipc.request_shutdown, proc.daemon_pid)
                await asyncio.to_thread(proc.wait, timeout=10)
                replacement = await asyncio.to_thread(proc.start_replacement)
                assert replacement.daemon_pid != proc.daemon_pid
                await asyncio.gather(*pending)
        finally:
            sampling = False
            await sampler
        for offset in (0, sessions):
            await asyncio.gather(*(inspect(i) for i in range(offset, offset + sessions)))

    asyncio.run(run())
    assert not spawn_attempts, "more than one daemon process was requested"
    peak = max(sample["rss"] for sample in samples)
    assert peak - baseline["rss"] < RSS_GROWTH_BYTES, f"RSS growth {peak - baseline['rss']} >= {RSS_GROWTH_BYTES}"
    metrics = dict(calibration,
                   after_bursts=after_bursts, seconds=round(time.monotonic() - started, 3), baseline=baseline,
                   peak_rss=peak, peak_fds=max(s["fds"] for s in samples),
                   peak_threads=max(s["threads"] for s in samples),
                   slowest_seconds={k: round(v, 3) for k, v in sorted(latencies.items())})
    record_property("capacity_metrics", json.dumps(metrics))
    print("CAPACITY_METRICS " + json.dumps(metrics))


def measure_session_start_cpu(enough=0.0):
    """CPU seconds for 300 real starts, excluding imports and one-time setup.

    Repeats up to three times and stops at the first measurement no greater
    than ``enough``: a slow host pays for the repeats only when it needs them.
    """
    from autorun import plugins  # noqa: F401  registers the real handlers

    store = core.ThreadSafeDB()

    def start(index):
        core.app.dispatch(core.EventContext(
            session_id=f"cpu-capacity-{index}", event="SessionStart", prompt="",
            store=store, cli_type="claude", source="startup"))

    start(-1)  # imports and one-time setup are not per-session cost
    spent = []
    for attempt in range(3):  # other processes only add time; the least is the cost
        begin = time.process_time()
        for index in range(SESSIONS):
            start(f"{attempt}-{index}")
        spent.append(time.process_time() - begin)
        if spent[-1] <= enough:
            break
    return min(spent)


def burst_session_count(spent, deadline):
    # Mac passes with ~2.5x headroom; the 3–4x slower runner misses deadlines.
    return max(60, min(SESSIONS, int(SESSIONS * (0.4 * deadline) / spent)))


def wall_clock_session_count(probe_slowest, deadline):
    """Sessions whose slowest start should answer in half the deadline.

    Scales the measured probe linearly: N simultaneous starts queue behind
    one another, so the slowest waits about N times one start's service time.
    """
    return max(PROBE_SESSIONS, min(SESSIONS, int(PROBE_SESSIONS * (0.5 * deadline) / probe_slowest)))


@pytest.mark.parametrize("slowest, expected", [(0.1, 300), (0.2, 300), (0.4, 150), (0.9, 66), (3, 60)])
def test_wall_clock_session_count_scales_with_the_probe(slowest, expected):
    assert wall_clock_session_count(slowest, 2) == expected


# Up to three passes of 300 starts; on a slow runner each takes several
# seconds of wall time, which together exceed the suite's 30 s default.
@pytest.mark.timeout(150)
def test_300_session_starts_need_less_cpu_than_the_start_deadline(sqlite_manager):
    """300 starts must fit the full deadline on every host (rc3 needed ~3.2s)."""
    deadline = core.CONFIG["daemon_dispatch_timeouts_seconds"]["SessionStart"]
    spent = measure_session_start_cpu(enough=deadline)
    assert spent < deadline, (
        f"{SESSIONS} session starts cost {spent:.2f}s of CPU; they cannot all "
        f"finish inside the {deadline:g}s SessionStart deadline")


@pytest.fixture
def sqlite_manager(tmp_path, monkeypatch):
    from autorun import session_manager

    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setitem(session_manager._CONFIG, "state_backend", "sqlite")
    session_manager._reset_for_testing()
    try:
        yield session_manager.get_session_manager()
    finally:
        session_manager._reset_for_testing()


def test_dispatch_reuses_and_closes_one_state_connection(sqlite_manager, monkeypatch):
    """Regression: every legacy state scope used to reopen/configure SQLite."""

    manager = sqlite_manager
    store = manager._store
    connect = store._connect
    opened = []

    def counted(timeout):
        opened.append(timeout)
        return connect(timeout)

    monkeypatch.setattr(store, "_connect", counted)
    app = core.AutorunApp()

    @app.on("PreToolUse")
    def reads_and_write(ctx):
        manager.read_field(ctx.session_id, "first")
        manager.read_field(ctx.session_id, "second")
        ctx.state_update("count", lambda n: n + 1, 0)
        return ctx.allow()

    ctx = core.EventContext(session_id="connection-scope", event="PreToolUse",
                            prompt="", store=core.ThreadSafeDB(), cli_type="claude")
    app.dispatch(ctx)
    assert len(opened) == 1, f"one dispatch opened {len(opened)} SQLite connections"
    assert store.open_connection_count() == 0, "dispatch retained its connection"
    assert manager.read_field(ctx.session_id, "count") == 1


def test_supplied_launcher_identity_does_not_scan_parent_processes(monkeypatch):
    def unnecessary_scan():
        raise AssertionError("supplied launcher identity still scanned parent processes")

    monkeypatch.setattr(client, "get_stable_process_identity", unnecessary_scan)
    payload, _ = client.prepare_payload_for_daemon(
        {"_pid": 12345, "_pid_started_at_units": 67890, "cli_type": "claude"})
    assert (payload["_pid"], payload["_pid_started_at_units"]) == (12345, 67890)


def test_empty_startup_reads_do_not_take_the_writer_slot(sqlite_manager, monkeypatch):
    import contextlib
    from autorun import cache_guard, plugins
    from autorun.plan_export import PlanExport, PlanExportConfig

    manager = sqlite_manager
    ctx = core.EventContext(session_id="startup-read", event="SessionStart",
                            prompt="", store=core.ThreadSafeDB(), cli_type="claude", source="startup")
    cache_guard.CacheGuard.from_ctx(ctx)  # Hydrate the session and global cache.
    transactions = []
    real = manager._store.write_transaction

    @contextlib.contextmanager
    def counted(owner):
        transactions.append(owner)
        with real(owner) as connection:
            yield connection

    monkeypatch.setattr(manager._store, "write_transaction", counted)
    plugins.initialize_task_staleness_cadence(ctx)
    plugins.cleanup_expired_allows(ctx)
    plugins.cache_guard_on_sessionstart(ctx)
    resume = next(handler for handler in core.app.chains["SessionStart"]
                  if handler.__name__ == "resume_incomplete_tasks")
    resume(ctx)
    assert PlanExport(ctx, PlanExportConfig()).active_plans == {}
    assert not transactions, f"empty startup reads opened {len(transactions)} writer transactions"


def test_startup_reset_cannot_hide_an_old_checkpoint_in_volatile_state(sqlite_manager):
    from autorun import plugins

    manager = sqlite_manager
    ctx = core.EventContext(session_id="old-checkpoint", event="SessionStart",
                            prompt="", store=core.ThreadSafeDB(), cli_type="claude", source="startup")
    name = "task_staleness_initial_checkpoint_complete"
    ctx.state_set(name, True)
    ctx.state_set_volatile(name, False)
    plugins.initialize_task_staleness_cadence(ctx)
    assert manager.read_field(ctx.session_id, name) is False


@pytest.mark.asyncio
async def test_connect_cannot_outlive_the_prepared_hook_deadline(tmp_path, monkeypatch):
    async def stalled_connect(**kwargs):
        await asyncio.sleep(0.8)
        raise OSError("connection never became ready")

    def forbidden_spawn(*args, **kwargs):
        raise AssertionError("deadline test attempted a daemon spawn")

    restart_lock = tmp_path / "restart.lock"
    monkeypatch.setattr(ipc, "connect", stalled_connect)
    monkeypatch.setattr(ipc, "daemon_restart_lock_path", lambda: restart_lock)
    monkeypatch.setattr(client, "output_hook_response", lambda response, **kwargs: response)
    monkeypatch.setattr(client, "subprocess", SimpleNamespace(
        Popen=forbidden_spawn, DEVNULL=client.subprocess.DEVNULL))
    payload, cli = client.prepare_payload_for_daemon(
        {"hook_event_name": "PreToolUse", "cli_type": "claude", "_pid": 12345})
    payload[client.DEADLINE_PAYLOAD_KEY] = time.time() + 0.1
    started = time.monotonic()
    with FileLock(str(restart_lock)):
        response = await client.forward_prepared_payload(payload, cli)
    elapsed = time.monotonic() - started
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert elapsed < 0.5, f"connect ignored the 0.1s hook deadline: {elapsed:.3f}s"
    assert client.RESPONSE_TIMEOUT_GUIDANCE in json.dumps(response), "active restart got unrecoverable guidance"


def test_context_gc_preserves_live_grants_and_refreshes_cached_state(sqlite_manager):
    from autorun import cache_guard

    ctx = core.EventContext(session_id="grant-gc", event="SessionStart",
                            prompt="", store=core.ThreadSafeDB(), cli_type="claude")
    cache_guard.grant_override(ctx.session_id, ttl_seconds=-1)
    cache_guard.grant_override(ctx.session_id, permanent=True)
    key = cache_guard._GRANT_KEY
    assert len(ctx.state_get(key)) == 2
    cache_guard.purge_stale_overrides(ctx.session_id, ctx=ctx)
    assert len(ctx.state_get(key)) == 1
    assert ctx.state_get(key) == sqlite_manager.read_field(ctx.session_id, key)
