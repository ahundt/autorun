"""The daemon serves many concurrent sessions without serialising on durable writes.

Sixty to a hundred harness sessions share one daemon. Every tool call sends a
PreToolUse and a PostToolUse, and every state transaction that call takes is
serialised behind every other session's: ``ThreadSafeDB`` holds one lock across
the durable write, and SQLite admits one writer at a time. A read that goes
through a write transaction therefore costs every other session time, and the
2026-11-06 outage was ~5 such transactions per tool call across ~60 sessions.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

import pytest

from autorun import core
from autorun import ipc
from autorun import session_manager
from autorun.core import EventContext, ThreadSafeDB
from autorun.task_pause import (
    activate_task_pause,
    task_enforcement_is_paused,
    task_pause_guidance,
)
from autorun.scoped_allow import ScopeSpec

BACKENDS = ("json", "sqlite")


@pytest.fixture(params=BACKENDS)
def backend(request, tmp_path, monkeypatch):
    # A private state directory per test: SQLite refuses to start beside JSON
    # state another parametrisation left behind.
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setitem(session_manager._CONFIG, "state_backend", request.param)
    session_manager._reset_for_testing()
    yield request.param
    session_manager._reset_for_testing()


@pytest.fixture
def transactions(monkeypatch):
    """Count durable state transactions the daemon store opens."""
    opened: list[str] = []
    real = core.session_state

    @contextlib.contextmanager
    def counting(session_id, *args, **kwargs):
        opened.append(session_id)
        with real(session_id, *args, **kwargs) as state:
            yield state

    monkeypatch.setattr(core, "session_state", counting)
    return opened


def _ctx(store: ThreadSafeDB, session_id: str) -> EventContext:
    return EventContext(
        session_id=session_id,
        event="PreToolUse",
        prompt="",
        tool_name="Bash",
        store=store,
        session_identity_authority="payload",
        cli_type="codex",
    )


def test_empty_antigravity_notice_check_takes_no_write(backend, transactions):
    from autorun import plugins  # noqa: F401

    store = ThreadSafeDB()
    ctx = EventContext(session_id="agy-empty", event="BeforeModel",
                       cli_type="antigravity", store=store)
    assert core.app.dispatch(ctx) is None
    assert transactions == []
    # A fresh read must also notice a queue written outside the daemon cache.
    other = EventContext(session_id="agy-empty", event="Stop", cli_type="antigravity")
    other.queue_deferred_stop_notice("pending notice")
    transactions.clear()
    assert "pending notice" in str(core.app.dispatch(ctx))
    assert transactions == ["agy-empty"]
    transactions.clear()
    assert core.app.dispatch(ctx) is None
    assert transactions == []


def test_stop_without_a_pause_takes_no_write(backend, transactions):
    from autorun.task_pause import task_pause_allows_stop

    ctx = EventContext(session_id="unpaused-stop", event="Stop", cli_type="codex",
                       store=ThreadSafeDB(), last_assistant_message="Work completed.")
    assert not task_pause_allows_stop(ctx)
    assert transactions == []


def test_concurrent_notice_checks_consume_once(backend, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    store = ThreadSafeDB()
    contexts = [EventContext(session_id="notice-race", event="BeforeModel",
                             cli_type="antigravity", store=store) for _ in range(2)]
    contexts[0].queue_deferred_stop_notice("once")
    barrier = threading.Barrier(2)
    real = EventContext.state_get_stored

    def read_before_either_pop(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(EventContext, "state_get_stored", read_before_either_pop)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda ctx: ctx.pop_deferred_stop_notice(), contexts))
    assert results.count("once") == 1
    assert results.count(None) == 1


def test_get_returns_a_stored_field_after_update_touched_a_sibling(backend):
    """update() may mark a session loaded only once every field is cached.

    It used to cache the one field it wrote yet record the whole session as
    hydrated, so the next get() of any other stored field answered the
    default: a fresh daemon that first ticked a counter then read the pause or
    an /ar:ok grant saw neither.
    """
    ThreadSafeDB().set("sess:stored", "value")
    fresh_daemon = ThreadSafeDB()
    fresh_daemon.update("sess:counter", lambda current: (current or 0) + 1)
    assert fresh_daemon.get("sess:stored", "DEFAULT") == "value"


def test_pause_checks_take_no_transaction_once_the_session_is_hydrated(
    backend, transactions
):
    """Reading whether tasks are paused is a read, so it costs no write slot."""
    store = ThreadSafeDB()
    ctx = _ctx(store, "pause-read")
    task_enforcement_is_paused(ctx)  # first touch hydrates the session
    transactions.clear()

    for _ in range(3):
        assert not task_enforcement_is_paused(ctx)
        assert task_pause_guidance(ctx) is None
    assert transactions == []


def test_a_cold_session_still_persists_a_blind_set(backend, transactions):
    """Without a hydrated cache there is nothing to compare against."""
    ThreadSafeDB().set("cold:flag", True)
    transactions.clear()

    ThreadSafeDB().set("cold:flag", False)
    assert transactions == ["cold"]
    assert ThreadSafeDB().get("cold:flag") is False


def test_a_volatile_value_is_still_made_durable(backend, transactions):
    """The cache holds advisory values too; equality with one proves nothing."""
    store = ThreadSafeDB()
    store.set("vol:other", "hydrate")
    store.set_volatile("vol:counter", 3)
    transactions.clear()

    store.set("vol:counter", 3)
    assert transactions == ["vol"]
    assert ThreadSafeDB().get("vol:counter") == 3


def test_a_set_after_another_writer_changed_storage_still_lands(backend):
    """The cache cannot see writes made outside it, so it cannot prove a no-op.

    Another process (the CLI, the hook's own fallback, an old daemon still
    draining during a restart) or a direct ``session_state()`` caller may
    change storage. A daemon set that matched its stale cache was skipped,
    leaving the other writer's value in place.
    """
    daemon = ThreadSafeDB()
    daemon.set("sess:flag", False)
    with session_manager.session_state("sess") as state:
        state["flag"] = True

    daemon.set("sess:flag", False)
    with daemon.batch_writes():
        daemon.set("sess:flag", False)
    assert ThreadSafeDB().get("sess:flag") is False


@pytest.mark.parametrize("first_read", ["update", "get"])
def test_hydrating_a_session_keeps_a_newer_volatile_value(backend, first_read):
    """A volatile value is newer than any checkpoint of it on disk; loading
    the session's other fields must not put the older stored one back."""
    with session_manager.session_state("vol") as state:
        state["counter"] = 3
    daemon = ThreadSafeDB()
    daemon.set_volatile("vol:counter", 7)
    if first_read == "update":
        daemon.update("vol:other", lambda _current: 1)
    else:
        daemon.get("vol:other")
    assert daemon.get("vol:counter") == 7


def test_an_expired_volatile_value_falls_back_to_storage(backend):
    """Expiry forgets the advisory value, not the stored one beneath it."""
    import time

    with session_manager.session_state("vol") as state:
        state["counter"] = 3
    daemon = ThreadSafeDB(volatile_max_age_seconds=0.01)
    daemon.get("vol:other")  # hydrates the session
    daemon.set_volatile("vol:counter", 7)
    time.sleep(0.05)
    assert daemon.get("vol:counter", "DEFAULT") == 3


def test_a_full_disk_while_pruning_a_pause_enforces_tasks_normally(backend, monkeypatch):
    """Pruning an expired pause writes; on the JSON backend a full disk raised
    a bare OSError out of the pause check instead of the state error every
    reader handles, so the tool call failed instead of enforcing normally."""
    import errno
    import time

    ctx = _ctx(ThreadSafeDB(), "full-disk")
    activate_task_pause(ctx, scope=ScopeSpec(ttl_seconds=1), reason="x", now=time.time() - 10)

    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(session_manager, "atomic_write_json", full)
    assert task_enforcement_is_paused(ctx) is False


@pytest.fixture
def failing_storage(monkeypatch):
    """Make the next durable transactions fail the way a full disk or a locked
    SQLite database does: with a raw error from inside the transaction."""
    import sqlite3

    real = core.session_state
    state = {"fail": False}

    @contextlib.contextmanager
    def maybe_failing(session_id, *args, **kwargs):
        with real(session_id, *args, **kwargs) as stored:
            yield stored
            if state["fail"]:
                raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(core, "session_state", maybe_failing)
    return state


def test_a_failed_save_of_a_volatile_key_leaves_the_stored_value_readable(backend, failing_storage):
    """The failed write dropped the cached value but left the key marked
    volatile, so hydration skipped the stored value and reads returned the
    default for good (tool_calls_since_task_update is written both ways)."""
    with session_manager.session_state("orph") as state:
        state["n"] = 7
    daemon = ThreadSafeDB()
    daemon.update_volatile("orph:n", lambda current: (current or 0) + 1)
    failing_storage["fail"] = True
    with pytest.raises(session_manager.SessionStateError):
        daemon.set("orph:n", 0)
    failing_storage["fail"] = False
    assert daemon.get("orph:n", "DEFAULT") == 7


def test_a_raw_storage_error_inside_update_is_reported_as_a_state_error(backend, failing_storage):
    """set() and get() report storage failures as state errors; update() let
    a raw sqlite3/OSError escape and fail the whole dispatch."""
    daemon = ThreadSafeDB()
    failing_storage["fail"] = True
    with pytest.raises(session_manager.SessionPersistenceError):
        daemon.update("raw:n", lambda current: (current or 0) + 1)


def test_evicting_a_volatile_key_that_hides_nothing_keeps_the_session_cached(backend, transactions):
    """Only a volatile value that shadows a stored one needs a re-read after
    it goes; dropping the cached flag for every eviction turned the volatile
    limit into a storage read per call once the working set exceeded it."""
    daemon = ThreadSafeDB(volatile_max_entries=1)
    daemon.get("cliff:stored")  # hydrate
    daemon.set_volatile("cliff:a", 1)
    daemon.set_volatile("cliff:b", 2)  # evicts cliff:a
    transactions.clear()
    assert daemon.get("cliff:missing", "DEFAULT") == "DEFAULT"
    assert transactions == []


def test_a_steady_tool_call_takes_no_write_transaction(backend, transactions):
    """With no task to track, PreToolUse and PostToolUse rewrote
    task_staleness_enforce_next=False on every call: one write transaction
    per tool call, serialised behind every other session's."""
    from autorun import plugins  # noqa: F401  registers handlers
    from autorun.core import app

    store = ThreadSafeDB()

    def call(event, i):
        return EventContext(
            session_id="steady", event=event, prompt="", tool_name="Bash",
            tool_input={"command": f"ls /tmp/{i}"},
            tool_result={"stdout": "ok"} if event == "PostToolUse" else None,
            store=store, session_identity_authority="payload", cli_type="claude",
            cwd="/tmp", session_transcript=[],
        )

    for event in ("PreToolUse", "PostToolUse"):
        app.dispatch(call(event, 0))  # first call hydrates and records state
    transactions.clear()
    for i in range(1, 4):
        for event in ("PreToolUse", "PostToolUse"):
            app.dispatch(call(event, i))
    assert transactions == []


def test_a_pause_change_made_by_another_process_is_seen(backend):
    """The hook's in-process fallback and an overlapping daemon write state
    outside this daemon's cache. A pause read from the cache kept enforcement
    off after the user resumed, for the life of the daemon."""
    import time

    from autorun.task_pause import resume_task_pause

    daemon = _ctx(ThreadSafeDB(), "two-proc")
    other_process = _ctx(ThreadSafeDB(), "two-proc")
    assert task_enforcement_is_paused(daemon) is False  # hydrated, not paused

    activate_task_pause(other_process, scope=ScopeSpec(ttl_seconds=600), reason="x", now=time.time())
    assert task_enforcement_is_paused(daemon) is True

    resume_task_pause(other_process)
    assert task_enforcement_is_paused(daemon) is False


def test_daemon_startup_builds_the_bash_parser_before_serving(monkeypatch):
    """The first Bash check must not pay the parser build inside its budget.

    bashlex builds its LALR tables on import, measured at 0.58-0.74 s. Lazily
    that landed inside the first PreToolUse after every daemon start, so a
    restart under load timed out the first wave of calls and opened the
    dispatch circuit for every session.
    """
    from autorun import command_detection, core, daemon

    # The parser is built only by the process that wins the daemon lock, so
    # this start needs a lock of its own: another test's daemon may hold the
    # worker's.
    home = Path(tempfile.mkdtemp(prefix="w", dir=os.environ["AUTORUN_TEST_RUNTIME_DIR"]))
    monkeypatch.setattr(core, "LOCK_PATH", home / "daemon.lock")
    for name, filename in (("SOCKET_PATH", "daemon.sock"), ("PORT_FILE", "daemon.port"),
                           ("TOKEN_FILE", "daemon.token")):
        monkeypatch.setattr(ipc, name, home / filename)
    command_detection._bashlex.cache_clear()
    async def before_serving(*args, **kwargs):
        assert command_detection._bashlex.cache_info().currsize == 1
        raise RuntimeError("warmup checked before serving")

    monkeypatch.setattr(ipc, "start_server", before_serving)
    monkeypatch.setattr(daemon, "configure_file_logging", lambda name: None)
    with pytest.raises(SystemExit):
        daemon.main()
    assert command_detection._bashlex.cache_info().currsize == 1


@pytest.mark.asyncio
async def test_one_sessions_timeout_does_not_contain_other_sessions(monkeypatch):
    """A slow handler backs off its own session, not the whole fleet.

    The circuit used to be keyed by event alone, so one PreToolUse timeout in
    any session denied PreToolUse to all sixty for the cooldown. Each denied
    agent then read "repair with autorun --restart-daemon" and ran it.
    """
    import asyncio
    import threading
    from unittest.mock import Mock

    from autorun.core import AutorunDaemon

    release_slow = threading.Event()
    timeout_seconds = 0.05

    def dispatch(ctx):
        if ctx.session_id == "slow":
            release_slow.wait(timeout_seconds * 20)
        return None

    app = Mock()
    app.dispatch.side_effect = dispatch
    daemon = AutorunDaemon(app)
    monkeypatch.setattr(core, "dispatch_timeout_for_event", lambda _event: timeout_seconds)
    try:
        slow = await daemon._dispatch_with_timeout(
            EventContext(session_id="slow", event="PreToolUse"), "codex"
        )
        assert "daemon_dispatch_timeout" in str(slow)

        other = await daemon._dispatch_with_timeout(
            EventContext(session_id="other", event="PreToolUse"), "codex"
        )
        assert other is None

        again = await daemon._dispatch_with_timeout(
            EventContext(session_id="slow", event="PreToolUse"), "codex"
        )
        assert "daemon_dispatch_contained" in str(again)
    finally:
        release_slow.set()
        await asyncio.sleep(0)


LOAD_EVENT_CODES = ("daemon_dispatch_timeout", "daemon_dispatch_contained")


@pytest.mark.parametrize("event_code", LOAD_EVENT_CODES)
def test_a_load_denial_tells_the_agent_to_wait_not_to_restart(event_code):
    """Agents act on this text. On 2026-11-06 about fifteen of them ran the
    restart it recommended, twenty restarts in thirty minutes, and each
    restart deleted the socket every other session was using.

    A load denial clears by itself once the cooldown passes, so it must say
    so, still block (the gate fails closed), and never recommend a restart.
    """
    from autorun.client import build_daemon_failure_response

    response = build_daemon_failure_response(
        "PreToolUse", "codex", "Daemon handler is busy", event_code=event_code
    )
    text = str(response)
    cooldown = core.CONFIG["daemon_dispatch_timeout_cooldown_seconds"]

    # The decision itself, not the word anywhere: an allow that says
    # "blocked" in its message passed the old check.
    assert response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "--restart-daemon" not in text.replace("Do not run `autorun --restart-daemon`", "")
    assert "Retrying will not help" not in text
    assert f"{cooldown:g}" in text
    assert "autorun --status" in text


def test_an_unreachable_daemon_still_names_the_restart():
    """The restart advice stays where a restart is the repair."""
    from autorun.client import build_daemon_failure_response

    response = build_daemon_failure_response(
        "PreToolUse", "codex", "Daemon unavailable", event_code="daemon_unavailable_or_timeout"
    )
    assert "autorun --restart-daemon" in str(response)


def _restart_with_daemon_aged(
    monkeypatch,
    age_seconds,
    src_dir=None,
    answers=True,
    daemon_birth_units=None,
    daemon_records=None,
    **kwargs,
):
    """Run restart_daemon() against a daemon of the given age.

    ``answers`` is whether it answers a round-trip probe; it always accepts
    a connect(), as a wedged daemon's listen backlog does.
    """
    from contextlib import contextmanager
    from pathlib import Path
    from unittest import mock

    import autorun.restart_daemon as restart_mod

    src_dir = src_dir or Path("/nonexistent-autorun-src")

    @contextmanager
    def acquired():
        yield True

    stop = mock.Mock()
    start = mock.Mock(side_effect=AssertionError("must not start a daemon"))
    monkeypatch.setattr(restart_mod, "restart_lock", acquired)
    monkeypatch.setattr(restart_mod, "_resolve_src_dir", lambda: src_dir)
    if daemon_records is None:
        daemon_records = ((4242, daemon_birth_units), (4242, daemon_birth_units))
    records = iter(daemon_records)
    monkeypatch.setattr(
        restart_mod.ipc, "daemon_record", lambda _path: next(records), raising=False
    )
    after_record = daemon_records[-1]
    monkeypatch.setattr(
        restart_mod, "get_daemon_pid", lambda **_: after_record[0] if after_record else None
    )
    monkeypatch.setattr(restart_mod, "is_daemon_responding", lambda: True)
    monkeypatch.setattr(restart_mod.ipc, "answers_round_trip", lambda *a, **k: answers)
    monkeypatch.setattr(restart_mod, "_daemon_age_seconds", lambda pid: age_seconds)
    monkeypatch.setattr(restart_mod.time, "time", lambda: 100.0)
    if daemon_birth_units is None:
        daemon_birth_units = round(
            (100.0 - age_seconds) * restart_mod.ipc.PROCESS_BIRTH_UNITS_PER_SECOND
        )
    monkeypatch.setattr(
        restart_mod.ipc,
        "daemon_record_birth_units",
        lambda path, expected_pid=None: daemon_birth_units,
        raising=False,
    )
    monkeypatch.setattr(restart_mod, "_stop_daemon", stop)
    monkeypatch.setattr(restart_mod, "_start_daemon", start)
    try:
        code = restart_mod.restart_daemon(**kwargs)
    except AssertionError:
        code = "started"
    return code, stop


def test_a_restart_of_a_fresh_healthy_daemon_is_a_no_op(monkeypatch):
    """Twenty restarts in thirty minutes came from agents each fixing the gap
    the previous restart opened. A daemon that started moments ago and answers
    is the restart the caller wanted, so stopping it again only opens a gap.
    """
    window = core.CONFIG["daemon_restart_coalesce_seconds"]
    code, stop = _restart_with_daemon_aged(monkeypatch, window / 2)
    assert code == 0
    stop.assert_not_called()


def test_an_older_daemon_is_still_restarted(monkeypatch):
    window = core.CONFIG["daemon_restart_coalesce_seconds"]
    code, stop = _restart_with_daemon_aged(monkeypatch, window * 2)
    stop.assert_called_once()


def test_a_restart_after_editing_source_is_never_coalesced(monkeypatch, tmp_path):
    """The documented developer loop is edit, restart, edit, restart. A second
    restart within the window must still load the new code."""
    import os
    import time

    window = core.CONFIG["daemon_restart_coalesce_seconds"]
    src = tmp_path / "src" / "autorun"
    src.mkdir(parents=True)
    edited = src / "core.py"
    edited.write_text("# edited after the daemon started\n")
    now = time.time()
    os.utime(edited, (now, now))  # newer than a daemon started window/2 ago

    code, stop = _restart_with_daemon_aged(monkeypatch, window / 2, src_dir=tmp_path / "src")
    stop.assert_called_once()


def test_an_install_handoff_always_replaces_the_daemon(monkeypatch):
    """An install must load its build even when the existing daemon is young."""
    window = core.CONFIG["daemon_restart_coalesce_seconds"]
    code, stop = _restart_with_daemon_aged(
        monkeypatch, window / 2, replace_runtime_daemon=True
    )
    stop.assert_called_once()


def test_an_install_handoff_restarts_after_a_backward_clock_step(monkeypatch):
    """An old daemon must not look concurrent when the wall clock moves backward."""
    units = ipc.PROCESS_BIRTH_UNITS_PER_SECOND
    same = (4242, round(101.0 * units))
    code, stop = _restart_with_daemon_aged(
        monkeypatch,
        0,
        daemon_birth_units=same[1],
        daemon_records=(same, same),
        replace_runtime_daemon=True,
    )
    stop.assert_called_once()


def test_an_install_handoff_accepts_a_changed_daemon_record(monkeypatch):
    """A different validated record proves a concurrent installer replaced it."""
    code, stop = _restart_with_daemon_aged(
        monkeypatch,
        0,
        daemon_records=((4242, 90_000_000), (4343, 91_000_000)),
        replace_runtime_daemon=True,
    )
    assert code == 0
    stop.assert_not_called()


def test_an_install_handoff_accepts_a_new_record_after_none(monkeypatch):
    """A daemon created while this install waited also satisfies the handoff."""
    code, stop = _restart_with_daemon_aged(
        monkeypatch,
        0,
        daemon_records=(None, (4242, 91_000_000)),
        replace_runtime_daemon=True,
    )
    assert code == 0
    stop.assert_not_called()


def _age_directory(path, seconds_ago):
    import os
    import time

    stamp = time.time() - seconds_ago
    os.utime(path, (stamp, stamp))


def test_rule_discovery_does_not_glob_an_unchanged_directory(tmp_path, monkeypatch):
    """Discovering rule files was 70% of a PreToolUse dispatch (3.5 ms of
    4.2 ms): every call re-globbed every directory from the session up to the
    repository root, plus home, to decide whether its cache was still valid.
    """
    from pathlib import Path

    from autorun import integrations

    project = tmp_path / "project"
    rules = project / ".claude"
    rules.mkdir(parents=True)
    (project / ".git").mkdir()
    (rules / "autorun.one.local.md").write_text("rule", encoding="utf-8")
    old_enough = integrations.DIRECTORY_MTIME_RESOLUTION_NS / 1e9 * 2
    _age_directory(rules, old_enough)
    # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(tmp_path / "home"))
    integrations._rule_file_listing_cache.clear()

    first = integrations._integration_files(str(project))
    globbed = []
    real_glob = Path.glob
    monkeypatch.setattr(
        Path, "glob", lambda self, pattern: globbed.append(self) or real_glob(self, pattern)
    )
    assert integrations._integration_files(str(project)) == first
    assert globbed == []

    (rules / "autorun.two.local.md").write_text("rule", encoding="utf-8")
    _age_directory(rules, old_enough / 2)
    assert len(integrations._integration_files(str(project))) == len(first) + 1


def test_a_just_changed_directory_is_listed_again(tmp_path, monkeypatch):
    """Within the filesystem's timestamp resolution a second change can leave
    the mtime unchanged, so a listing that recent is never trusted."""
    from autorun import integrations

    project = tmp_path / "project"
    rules = project / ".claude"
    rules.mkdir(parents=True)
    (project / ".git").mkdir()
    # Path.home() reads USERPROFILE on Windows and HOME elsewhere.
    for variable in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(variable, str(tmp_path / "home"))
    integrations._rule_file_listing_cache.clear()

    assert integrations._integration_files(str(project)) == []
    (rules / "autorun.new.local.md").write_text("rule", encoding="utf-8")
    assert len(integrations._integration_files(str(project))) == 1


class _Writer:
    def __init__(self):
        self.data = b""

    def write(self, chunk):
        self.data += chunk

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None

    def is_closing(self):
        return False

    def get_extra_info(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_info_log_names_each_session_once_not_each_call(caplog):
    """Codex launches every hook from a fresh process, so the old per-PID line
    fired on every tool call ("active: 184" for 60 sessions) and the two INFO
    lines per call rotated the 5 MB daemon log every hour or two, which left
    thirty minutes of history when an outage needed diagnosing.
    """
    import asyncio
    import json
    import logging

    from autorun.core import AutorunDaemon, app

    daemon = AutorunDaemon(app)
    calls_per_session = 3
    sessions = ("log-a", "log-b")
    caplog.set_level(logging.INFO, logger="autorun")
    for session in sessions:
        for call in range(calls_per_session):
            reader = asyncio.StreamReader()
            payload = {
                "hook_event_name": "PostToolUse", "session_id": session,
                "tool_name": "Read", "tool_input": {}, "_cli_type": "codex",
                "_pid": hash((session, call)) & 0xFFFF,  # a fresh process per call
            }
            reader.feed_data((json.dumps(payload) + "\n").encode())
            reader.feed_eof()
            await daemon.handle_client(reader, _Writer())

    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert len([m for m in info if "New session" in m]) == len(sessions), info
    assert not [m for m in info if "handle_client" in m], info


@pytest.mark.asyncio
@pytest.mark.parametrize("unix_sockets", [True, False])
async def test_server_queues_as_many_connects_as_the_os_allows(monkeypatch, unix_sockets):
    """asyncio's default backlog is 100; with a hundred sessions firing at once
    the 101st connect was refused and had to wait out a client retry."""
    import socket

    from autorun import ipc

    seen = {}

    async def fake_start(*_args, **kwargs):
        seen.update(kwargs)

        class _Server:
            sockets = [type("S", (), {"getsockname": lambda self: ("127.0.0.1", 1)})()]

        return _Server()

    monkeypatch.setattr(ipc, "HAS_UNIX_SOCKETS", unix_sockets)
    monkeypatch.setattr(ipc.asyncio, "start_unix_server", fake_start, raising=False)
    monkeypatch.setattr(ipc.asyncio, "start_server", fake_start)
    monkeypatch.setattr(ipc, "atomic_write_text", lambda *_a, **_k: None)
    await ipc.start_server(lambda *_: None)
    assert seen.get("backlog") == socket.SOMAXCONN


def test_a_missing_dependency_is_reported_with_its_repair(monkeypatch, capsys):
    """`autorun --status` ended in a ModuleNotFoundError traceback for tomlkit
    when an editable install predated that dependency. The command must name
    the module and the reinstall that restores it, and exit 1."""
    from autorun import __main__ as cli
    from autorun import install

    def broken(**_kwargs):
        raise ModuleNotFoundError("No module named 'tomlkit'", name="tomlkit")

    monkeypatch.setattr(install, "show_status", broken)
    assert cli.main(["--status"]) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert "tomlkit" in text
    assert "uv tool install --force" in text
    assert "Traceback" not in text


def test_a_failed_save_leaves_no_unsaved_value_in_the_cache(tmp_path, monkeypatch):
    """update() hydrated the cache inside the transaction, before the save, so
    a save failing on a full disk left the daemon serving a value storage
    never accepted. The failure now surfaces as the state error callers handle.
    """
    import errno

    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "sessions"))
    monkeypatch.setitem(session_manager._CONFIG, "state_backend", "json")
    session_manager._reset_for_testing()
    try:
        ThreadSafeDB().set("disk:flag", False)
        daemon = ThreadSafeDB()

        def disk_full(*_args, **_kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        with monkeypatch.context() as patch:
            patch.setattr(session_manager, "atomic_write_json", disk_full)
            with pytest.raises(session_manager.SessionPersistenceError):
                daemon.update("disk:flag", lambda _current: True)

        assert daemon.get("disk:flag") is False, "served a value that was never saved"
        daemon.set("disk:flag", True)
        assert ThreadSafeDB().get("disk:flag") is True, "a real write was skipped"
    finally:
        session_manager._reset_for_testing()


def _blocking_daemon(monkeypatch, timeout_seconds, slots):
    import threading
    from unittest.mock import Mock

    from autorun.core import AutorunDaemon

    release = threading.Event()

    def dispatch(ctx):
        if ctx.session_id.startswith("hang"):
            release.wait(timeout_seconds * 40)
        return None

    app = Mock()
    app.dispatch.side_effect = dispatch
    monkeypatch.setattr(core, "dispatch_timeout_for_event", lambda _event: timeout_seconds)
    monkeypatch.setitem(core.CONFIG, "daemon_dispatch_max_concurrent_per_event", slots)
    return AutorunDaemon(app), release


@pytest.mark.asyncio
async def test_slots_held_by_handlers_past_their_deadline_report_a_stall(monkeypatch):
    """Handlers that time out keep their slot until their thread ends. When
    every slot is held that way the daemon will not recover by waiting, so the
    agent must be told so -- not "this clears in a few seconds"."""
    import asyncio

    timeout_seconds = 0.05
    daemon, release = _blocking_daemon(monkeypatch, timeout_seconds, slots=1)
    monkeypatch.setitem(core.CONFIG, "daemon_dispatch_stall_grace_seconds", 0.0)
    try:
        hung = await daemon._dispatch_with_timeout(
            EventContext(session_id="hang-1", event="PreToolUse"), "codex"
        )
        assert "daemon_dispatch_timeout" in str(hung)
        blocked = await daemon._dispatch_with_timeout(
            EventContext(session_id="other", event="PreToolUse"), "codex"
        )
        text = str(blocked)
        assert "daemon_dispatch_stalled" in text
        assert "autorun --restart-daemon" in text
    finally:
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_slots_held_by_handlers_still_within_budget_report_load(monkeypatch):
    import asyncio
    import time

    timeout_seconds = 1.0
    daemon, release = _blocking_daemon(monkeypatch, timeout_seconds, slots=1)
    try:
        busy = asyncio.ensure_future(daemon._dispatch_with_timeout(
            EventContext(session_id="hang-1", event="PreToolUse"), "codex"
        ))
        await asyncio.sleep(0)
        impatient = EventContext(
            session_id="other", event="PreToolUse",
            deadline_monotonic=time.monotonic() + timeout_seconds / 20,
        )
        blocked = await daemon._dispatch_with_timeout(impatient, "codex")
        assert "daemon_dispatch_contained" in str(blocked)
        assert "Do not run `autorun --restart-daemon`" in str(blocked)
    finally:
        release.set()
        await busy


@pytest.mark.asyncio
async def test_a_queue_of_handlers_each_within_budget_is_never_a_stall(monkeypatch):
    """A slot holder's budget starts when it gets the slot, not when it arrived.

    Measured from arrival, every holder in a queue is "overdue" by the time a
    later request gives up waiting, so a healthy daemon whose handlers each
    finish well inside their budget told agents to restart it.
    """
    import asyncio
    import time
    from unittest.mock import Mock

    from autorun.core import AutorunDaemon

    budget = 0.4
    handler_seconds = budget * 0.6
    slots = 2

    def dispatch(ctx):
        time.sleep(handler_seconds)
        return None

    app = Mock()
    app.dispatch.side_effect = dispatch
    monkeypatch.setattr(core, "dispatch_timeout_for_event", lambda _event: budget)
    monkeypatch.setitem(core.CONFIG, "daemon_dispatch_max_concurrent_per_event", slots)
    daemon = AutorunDaemon(app)
    results = await asyncio.gather(*(
        daemon._dispatch_with_timeout(
            EventContext(session_id=f"s{i}", event="PreToolUse"), "codex"
        )
        for i in range(slots * 3)
    ))
    assert not any("daemon_dispatch_stalled" in str(r) for r in results)
    await asyncio.sleep(handler_seconds * 2)  # let the running threads end


@pytest.mark.asyncio
async def test_handlers_that_overrun_their_budget_but_finish_are_not_a_stall(monkeypatch):
    """Overdue is not stuck. Handlers that each take about twice their budget
    on a loaded machine still free their slots; reporting that as a stall
    told every waiting session to restart a daemon that was making progress."""
    import asyncio
    import time
    from unittest.mock import Mock

    from autorun.core import AutorunDaemon

    budget = 0.1
    handler_seconds = budget * 2.5
    slots = 2

    def dispatch(ctx):
        time.sleep(handler_seconds)
        return None

    app = Mock()
    app.dispatch.side_effect = dispatch
    monkeypatch.setattr(core, "dispatch_timeout_for_event", lambda _event: budget)
    monkeypatch.setitem(core.CONFIG, "daemon_dispatch_max_concurrent_per_event", slots)
    daemon = AutorunDaemon(app)
    results = await asyncio.gather(*(
        daemon._dispatch_with_timeout(EventContext(session_id=f"slow{i}", event="PreToolUse"), "codex")
        for i in range(slots * 3)
    ))
    assert not any("daemon_dispatch_stalled" in str(r) for r in results)
    await asyncio.sleep(handler_seconds * 2)


@pytest.mark.asyncio
async def test_a_session_with_an_overdue_handler_does_not_take_a_second_slot(monkeypatch):
    """One session whose handler hangs must not fill every slot by retrying
    once its cooldown lapses; the next session still gets a slot."""
    import asyncio

    timeout_seconds = 0.05
    daemon, release = _blocking_daemon(monkeypatch, timeout_seconds, slots=2)
    monkeypatch.setitem(core.CONFIG, "daemon_dispatch_timeout_cooldown_seconds", 0.0)
    try:
        first = await daemon._dispatch_with_timeout(EventContext(session_id="hang-1", event="PreToolUse"), "codex")
        assert "daemon_dispatch_timeout" in str(first)
        again = await daemon._dispatch_with_timeout(EventContext(session_id="hang-1", event="PreToolUse"), "codex")
        assert "daemon_dispatch_contained" in str(again)
        assert daemon.app.dispatch.call_count == 1
        other = await daemon._dispatch_with_timeout(EventContext(session_id="other", event="PreToolUse"), "codex")
        assert other is None
    finally:
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_handler_is_overdue_at_the_deadline_its_caller_gave_up_on(monkeypatch):
    """The slot's recorded deadline is the one the request timed out on, not a
    later one taken after the handler was scheduled; otherwise the session's
    next request arrives before that later deadline and takes a second slot."""
    import asyncio
    import time

    timeout_seconds = 0.05
    daemon, release = _blocking_daemon(monkeypatch, timeout_seconds, slots=2)
    monkeypatch.setitem(core.CONFIG, "daemon_dispatch_timeout_cooldown_seconds", 0.0)
    loop = asyncio.get_running_loop()
    schedule = loop.run_in_executor

    def slow_schedule(executor, func, *args):
        future = schedule(executor, func, *args)
        time.sleep(timeout_seconds / 2)  # a loaded machine: scheduling takes a while
        return future

    monkeypatch.setattr(loop, "run_in_executor", slow_schedule)
    try:
        first = await daemon._dispatch_with_timeout(EventContext(session_id="hang-1", event="PreToolUse"), "codex")
        assert "daemon_dispatch_timeout" in str(first)
        again = await daemon._dispatch_with_timeout(EventContext(session_id="hang-1", event="PreToolUse"), "codex")
        assert "daemon_dispatch_contained" in str(again)
        assert daemon.app.dispatch.call_count == 1
    finally:
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_a_request_whose_hook_already_gave_up_is_not_dispatched(monkeypatch):
    """The hook stops waiting at its deadline. Running the handler after that
    spends a slot on an answer nobody reads and can apply side effects (a
    counted allow, a task update) the agent was told did not happen."""
    import time

    daemon, release = _blocking_daemon(monkeypatch, 1.0, slots=1)
    try:
        late = EventContext(session_id="late", event="PreToolUse", deadline_monotonic=time.monotonic() - 0.01)
        result = await daemon._dispatch_with_timeout(late, "codex")
        assert "daemon_dispatch_contained" in str(result)
        assert daemon.app.dispatch.call_count == 0
    finally:
        release.set()


def test_an_unanswered_request_says_retry_once_then_restart():
    """A request the daemon never answered is ambiguous: a loaded daemon and a
    wedged one look the same from the client, since the kernel queues the
    connection either way. Restart advice on the first one sent fifteen agents
    restarting a loaded daemon (2026-11-06); load advice on every one leaves a
    wedged daemon wedged. So: retry once, and restart only if it repeats --
    a restart against a fresh daemon that passes a health check does nothing.
    """
    from autorun import client

    accepted = client.DaemonDidNotAnswer("Daemon accepted the request but did not answer")
    code = client.failure_event_code(accepted)
    assert code == "daemon_response_timeout"
    assert code not in client.LOAD_EVENT_CODES
    reason = client.build_daemon_failure_response(
        "PreToolUse", "codex", str(accepted), event_code=code
    )["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Retry the same tool call once" in reason
    assert "autorun --restart-daemon" in reason
    assert "Do not run" not in reason


def test_the_js_bridge_gives_the_same_advice_for_an_unanswered_request():
    """Pi, Prime and OpenCode reach the daemon through the JS bridge; it said
    "Retrying will not help" where the Python client said "do not restart"."""
    from pathlib import Path

    from autorun import client

    bridge = (Path(client.__file__).parent / "bridge_template" / "daemon-client.mjs").read_text()
    assert f"const RESPONSE_TIMEOUT_GUIDANCE = {json.dumps(client.RESPONSE_TIMEOUT_GUIDANCE)};" in bridge
    assert "AR_EVENT_V1:daemon_response_timeout" in bridge


def test_a_response_timeout_on_python_3_10_is_still_an_unanswered_request(monkeypatch):
    """asyncio.TimeoutError is an OSError only from 3.11; on 3.10 it skipped
    the handler that recognises an accepted request and read as daemon-down."""
    import asyncio

    from autorun import client

    assert asyncio.TimeoutError in client.UNANSWERED_REQUEST_ERRORS
    assert OSError in client.UNANSWERED_REQUEST_ERRORS


@pytest.mark.asyncio
async def test_the_daemon_answers_a_round_trip_probe_without_dispatching(monkeypatch):
    """A connect() succeeds against a wedged daemon (the kernel queues it), so
    health needs an answer from the event loop itself."""
    import asyncio
    from unittest.mock import Mock

    from autorun import ipc
    from autorun.core import AutorunDaemon

    app = Mock()
    daemon = AutorunDaemon(app)
    reader = asyncio.StreamReader()
    reader.feed_data(json.dumps({ipc.ROUND_TRIP_PROBE_KEY: True}).encode() + b"\n")
    reader.feed_eof()
    written = []
    writer = Mock()
    writer.write.side_effect = written.append
    writer.drain = Mock(side_effect=lambda: asyncio.sleep(0))
    writer.wait_closed = Mock(side_effect=lambda: asyncio.sleep(0))

    await daemon.handle_client(reader, writer)
    assert json.loads(b"".join(written)) == {ipc.ROUND_TRIP_PROBE_KEY: True}
    app.dispatch.assert_not_called()


def test_a_young_daemon_that_fails_the_round_trip_is_replaced(monkeypatch):
    """Coalescing skipped any daemon under a minute old that accepted a
    connect(), so a wedged one could not be replaced at all."""
    window = core.CONFIG["daemon_restart_coalesce_seconds"]
    code, stop = _restart_with_daemon_aged(monkeypatch, window / 2, answers=False)
    stop.assert_called_once()


def test_restart_in_a_read_only_sandbox_reports_instead_of_crashing(monkeypatch, capsys):
    """Codex's workspace sandbox cannot write ~/.autorun; 44 restart attempts
    ended in a filelock PermissionError traceback and each was retried with
    escalation. Say what failed and who can do it instead, and exit 1."""
    import errno

    import autorun.restart_daemon as restart_mod

    class DeniedLock:
        def __init__(self, *_args, **_kwargs):
            pass

        def acquire(self, *_args, **_kwargs):
            raise PermissionError(errno.EPERM, "Operation not permitted")

        def release(self):
            raise AssertionError("released a lock that was never acquired")

    monkeypatch.setattr(restart_mod, "FileLock", DeniedLock)
    assert restart_mod.restart_daemon() == 1
    out = capsys.readouterr().out
    assert str(restart_mod.RESTART_LOCK_PATH) in out
    assert "Operation not permitted" in out
    assert "terminal" in out


def test_stand_down_guidance_says_where_the_variable_must_be_set():
    """1,055 commands in 49 sessions were prefixed `AUTORUN_DISABLE=1 ...`,
    which changes nothing: the hook runs in the harness's environment, not
    the command's. The guidance must say where it takes effect."""
    from autorun.client import UNRECOVERABLE_GUIDANCE

    assert "AUTORUN_DISABLE=1" in UNRECOVERABLE_GUIDANCE
    assert "launch" in UNRECOVERABLE_GUIDANCE
    assert "prefix" in UNRECOVERABLE_GUIDANCE
    assert UNRECOVERABLE_GUIDANCE.isascii()


@pytest.mark.asyncio
async def test_a_client_that_hangs_up_mid_send_is_a_disconnect_not_an_error(caplog):
    """Nine 'Handler error: 8192 bytes read on a total of undefined expected
    bytes' tracebacks at ERROR were clients giving up while sending a large
    payload. That is a disconnect: one warning, no traceback, no reply."""
    import asyncio
    import logging

    from autorun.core import AutorunDaemon, app

    reader = asyncio.StreamReader()
    reader.feed_data(b'{"hook_event_name": "PreToolUse", "tool_input": {"command": "' + b"x" * 8192)
    reader.feed_eof()
    writer = _Writer()
    caplog.set_level(logging.DEBUG, logger="autorun")
    await AutorunDaemon(app).handle_client(reader, writer)

    assert writer.data == b""
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not [r for r in caplog.records if r.exc_info]
    assert any("disconnected" in r.getMessage() for r in caplog.records)


PAUSE_STARTED_AT = 100.0
PAUSE_TTL_SECONDS = 600.0
WHILE_PAUSED = PAUSE_STARTED_AT + PAUSE_TTL_SECONDS / 2
AFTER_PAUSE_EXPIRED = PAUSE_STARTED_AT + PAUSE_TTL_SECONDS * 2
PAUSE_STATE_FIELD = "task_enforcement_pause"  # task_pause._STATE_KEY


def _pause(ctx: EventContext) -> None:
    activate_task_pause(
        ctx,
        scope=ScopeSpec(ttl_seconds=PAUSE_TTL_SECONDS),
        reason="throughput test",
        now=PAUSE_STARTED_AT,
    )


def test_an_active_pause_is_read_without_a_transaction(backend, transactions):
    ctx = _ctx(ThreadSafeDB(), "pause-active")
    _pause(ctx)
    transactions.clear()

    assert task_enforcement_is_paused(ctx, now=WHILE_PAUSED)
    assert task_pause_guidance(ctx, now=WHILE_PAUSED) is not None
    assert transactions == []


def test_an_expired_pause_is_pruned_once_then_read_for_free(backend, transactions):
    """Lazy pruning still happens, in exactly one transaction."""
    ctx = _ctx(ThreadSafeDB(), "pause-expired")
    _pause(ctx)
    transactions.clear()

    assert not task_enforcement_is_paused(ctx, now=AFTER_PAUSE_EXPIRED)
    assert len(transactions) == 1
    assert ThreadSafeDB().get(f"pause-expired:{PAUSE_STATE_FIELD}") is None

    transactions.clear()
    assert not task_enforcement_is_paused(ctx, now=AFTER_PAUSE_EXPIRED)
    assert task_pause_guidance(ctx, now=AFTER_PAUSE_EXPIRED) is None
    assert transactions == []


# --- Thread stress: exact invariants under the shared daemon store ----------

STRESS_THREADS = 8        # the daemon's executor runs a few handlers per event
STRESS_ROUNDS = 40        # enough interleavings to lose an update if one can be lost
STRESS_SESSIONS = 3       # cross-session and same-session contention at once


def test_concurrent_updates_and_unchanged_sets_lose_nothing(backend):
    """Every increment lands, and an unchanged-value set never resurrects an old value.

    The unchanged-write skip compares against the cache; if the cache could
    lag storage, a thread setting the old value would be skipped while storage
    held another, or an increment would be overwritten. Exact counts prove
    neither happens across threads sharing one store, on both backends.
    """
    import threading

    store = ThreadSafeDB()
    for s in range(STRESS_SESSIONS):
        store.set(f"st{s}:flag", False)
    start = threading.Barrier(STRESS_THREADS)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            start.wait()
            for r in range(STRESS_ROUNDS):
                session = f"st{(n + r) % STRESS_SESSIONS}"
                store.update(f"{session}:count", lambda current: (current or 0) + 1)
                store.set(f"{session}:flag", False)  # the blind reset PostToolUse does
        except BaseException as exc:  # surfaced below, never swallowed
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(STRESS_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []

    fresh = ThreadSafeDB()  # reads storage, not this store's cache
    total = sum(fresh.get(f"st{s}:count", 0) for s in range(STRESS_SESSIONS))
    assert total == STRESS_THREADS * STRESS_ROUNDS
    assert all(fresh.get(f"st{s}:flag") is False for s in range(STRESS_SESSIONS))


def test_a_direct_write_inside_synchronized_session_is_never_skipped_back(backend):
    """A legacy direct write refreshes the cache, so setting the old value again persists."""
    store = ThreadSafeDB()
    store.set("dw:flag", False)

    def direct_write() -> None:
        with core.session_state("dw") as state:
            state["flag"] = True

    with store.synchronized_session("dw"):
        direct_write()
    store.set("dw:flag", False)
    assert ThreadSafeDB().get("dw:flag") is False
