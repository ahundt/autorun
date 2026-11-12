"""What a change costs, measured, and whether it depends on unrelated data.

The claim behind this whole change is asymptotic, not constant-factor: today
a 95-byte edit rewrites 11.1 MB because the store is one file, so the cost of
touching one session grows with everything ever recorded. The rewrite is only
worth doing if that dependency is gone.

Constant factors are not asserted here. They vary by machine, disk, and what
else is running, and a suite that fails because a laptop was busy teaches
nobody anything. What is asserted is shape: doubling the unrelated corpus
must not double the cost of one write. The measured numbers are printed so a
person can read them, and the growth ratio is what fails the test.

Marked ``benchmark`` and skipped by default — timing under a parallel test run
measures the test run.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
from autorun.session_manager import (  # noqa: E402
    SQLiteStore,
    TaskRepository,
    session_state,
)
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig  # noqa: E402

pytestmark = [
    pytest.mark.benchmark,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("AUTORUN_ENABLE_STATE_BENCHMARK") != "1",
        reason=(
            "Set AUTORUN_ENABLE_STATE_BENCHMARK=1 to measure storage cost. "
            "Timing is meaningless while the rest of the suite is running."
        ),
    ),
]

# The observed median change from the live store: 95 bytes.
SMALL_CHANGE = "x" * 95
REPEATS = 25


# A hook's deadline. What a write must fit inside to leave room for the rest.
HOOK_BUDGET_MS = 500.0

# What no amount of scheduling noise explains. A shared CI runner can park a
# process for about a second — `state-benchmark` on run 31973891713 recorded a
# single 1064ms write where this machine's slowest of fifty is 2.8ms and the
# store this replaced peaks at 17.9ms. Above three seconds is the store, not
# the scheduler.
STALL_BACKSTOP_MS = 3000.0


def _summarize(samples: list[float]) -> dict:
    """Median, 95th percentile, and worst case, in milliseconds.

    Separate from the timing loop so the choice of statistic can be tested on
    known numbers instead of on a stopwatch.
    """
    return {
        "median_ms": statistics.median(samples),
        "p95_ms": sorted(samples)[int(len(samples) * 0.95) - 1],
        "max_ms": max(samples),
    }


def _time_repeatedly(operation, repeats: int = REPEATS) -> dict:
    """Median, p95 and worst case of one operation, in milliseconds.

    Median because a single sample on a shared machine is noise. p95 because a
    hook has a deadline and the near-worst case is what misses it, while the
    single maximum on a shared runner reports how long the scheduler looked
    away — the budget check reads p95 for that reason, and keeps the maximum
    against a much coarser backstop.
    """
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000.0)
    return _summarize(samples)


def _time_interleaved(operations: list[tuple[int, object]], repeats: int = REPEATS) -> list:
    """Time several corpus sizes round-robin, one sample each per round.

    A growth ratio compares two medians, and measuring one corpus to
    completion before starting the next puts minutes between them. On a shared
    runner that is long enough for the machine to change underneath the sweep:
    `state-benchmark` reported `updating one took 23.18x as long` from medians
    of 2.605ms, 2.270ms and 60.373ms at 100, 1000 and 10000 tasks — a jump that
    lands entirely on the corpus measured last, while 100 to 1000 stayed flat.
    Cost that actually grows with n shows at *every* step of the sweep, so that
    shape is contention, not scaling.

    Interleaving makes each corpus experience the same runner: a burst raises
    every median together and the ratio survives it, while a real O(n) cost
    still separates them. This changes how the measurement is taken and
    nothing about what is asserted.
    """
    samples: dict[int, list[float]] = {size: [] for size, _ in operations}
    for _ in range(repeats):
        for size, operation in operations:
            start = time.perf_counter()
            operation()
            samples[size].append((time.perf_counter() - start) * 1000.0)
    return [(size, _summarize(samples[size])) for size, _ in operations]


def _fill_json(state_dir: Path, sessions: int, fields_per_session: int = 4) -> None:
    payload = {
        f"filler-{index}/field-{field}": {"blob": "y" * 200}
        for index in range(sessions)
        for field in range(fields_per_session)
    }
    (state_dir / "daemon_state.json").write_text(json.dumps(payload), encoding="utf-8")


def _fill_sqlite(store: SQLiteStore, sessions: int, fields_per_session: int = 4) -> None:
    with store.operation_scope(120.0) as owner:
        with store.write_transaction(owner):
            for index in range(sessions):
                with store.session(f"filler-{index}") as state:
                    for field in range(fields_per_session):
                        state[f"field-{field}"] = {"blob": "y" * 200}


def _report(title: str, rows: list) -> None:
    print(f"\n{title}")
    print(f"{'corpus':>10} {'median ms':>12} {'p95 ms':>10} {'max ms':>10}")
    for size, timing in rows:
        print(f"{size:>10} {timing['median_ms']:>12.3f} "
              f"{timing['p95_ms']:>10.3f} {timing['max_ms']:>10.3f}")


def _peak_bytes(operation) -> int:
    tracemalloc.start()
    try:
        operation()
        _current, peak = tracemalloc.get_traced_memory()
        return peak
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize(
    "name, samples, budget_holds, backstop_holds",
    [
        # This machine, fifty writes against a 1600-session corpus.
        ("healthy", [0.9] * 49 + [2.8], True, True),
        # `state-benchmark` on run 31973891713: one sample parked for a second
        # while the other forty-nine were ordinary. The old assertion read the
        # maximum, so the runner's scheduler failed the build twice in twelve
        # runs and the answer both times was to press rerun.
        ("one stall", [0.9] * 49 + [1064.4], True, True),
        # Noise does not arrive alone; two stalls must still pass.
        ("two stalls", [0.9] * 48 + [980.0, 1064.4], True, True),
        # A store that misses the deadline more often than not is the store.
        # The budget catches it; the backstop is not the assertion that does.
        ("slow store", [700.0] * 50, False, True),
        # Above the backstop is not a scheduling story at any frequency.
        ("pathological", [0.9] * 49 + [4200.0], True, False),
    ],
)
def test_the_budget_reads_a_statistic_a_stalled_runner_cannot_move(
    name, samples, budget_holds, backstop_holds
):
    """Which number the hook-budget check reads, tested on known numbers.

    Written against the sample sets above rather than a stopwatch, because the
    property under test is the choice of statistic, not the speed of a disk.
    """
    timing = _summarize(samples)

    assert (timing["p95_ms"] < HOOK_BUDGET_MS) is budget_holds, (
        f"{name}: p95 {timing['p95_ms']:.1f}ms against {HOOK_BUDGET_MS:.0f}ms"
    )
    assert (timing["max_ms"] < STALL_BACKSTOP_MS) is backstop_holds, (
        f"{name}: max {timing['max_ms']:.1f}ms against {STALL_BACKSTOP_MS:.0f}ms"
    )


class TestWriteCostVersusCorpusSize:
    def test_the_json_store_gets_slower_as_unrelated_state_grows(self, tmp_path):
        """Establishes that the thing being fixed is real and measurable."""
        operations = []
        for sessions in (100, 400, 1600):
            state_dir = tmp_path / f"json-{sessions}"
            state_dir.mkdir(parents=True)
            _fill_json(state_dir, sessions)

            def write(state_dir=state_dir):
                with session_state("subject", state_dir=str(state_dir)) as state:
                    state["field"] = SMALL_CHANGE + str(time.time())

            operations.append((sessions, write))

        rows = _time_interleaved(operations)
        _report("JSON store: one small write, growing unrelated corpus", rows)
        growth = rows[-1][1]["median_ms"] / max(rows[0][1]["median_ms"], 1e-9)
        assert growth > 2.0, (
            "The JSON store did not get measurably slower as the corpus grew "
            f"16x (ratio {growth:.2f}). Either the machine is too noisy to "
            "measure or the premise of this change needs rechecking."
        )

    def test_one_write_costs_the_same_however_much_unrelated_state_exists(
        self, tmp_path
    ):
        """The property the whole change exists to obtain."""
        operations = []
        for sessions in (100, 400, 1600):
            store = SQLiteStore(tmp_path / f"sqlite-{sessions}" / "state.sqlite3")
            store.initialize()
            _fill_sqlite(store, sessions)

            def write(store=store):
                with store.session("subject") as state:
                    state["field"] = SMALL_CHANGE + str(time.time())

            operations.append((sessions, write))

        rows = _time_interleaved(operations)
        _report("SQLite store: one small write, growing unrelated corpus", rows)
        growth = rows[-1][1]["median_ms"] / max(rows[0][1]["median_ms"], 1e-9)
        assert growth < 2.0, (
            f"A 16x larger corpus made one write {growth:.2f}x slower. The "
            "write is still reading or rewriting unrelated state."
        )

    def test_a_write_stays_inside_the_hook_budget(self, tmp_path):
        """A half second is the budget; the near-worst case is what misses it."""
        store = SQLiteStore(tmp_path / "budget" / "state.sqlite3")
        store.initialize()
        _fill_sqlite(store, 1600)

        def write():
            with store.session("subject") as state:
                state["field"] = SMALL_CHANGE + str(time.time())

        timing = _time_repeatedly(write, repeats=50)
        _report("SQLite store: hook budget", [(1600, timing)])
        assert timing["p95_ms"] < HOOK_BUDGET_MS, (
            f"The 95th percentile of 50 writes was {timing['p95_ms']:.1f}ms "
            f"against a {HOOK_BUDGET_MS:.0f}ms hook budget, leaving no room "
            "for the rest of the hook."
        )
        assert timing["max_ms"] < STALL_BACKSTOP_MS, (
            f"The slowest of 50 writes took {timing['max_ms']:.1f}ms. Two or "
            "three samples above the budget would be scheduling noise, but "
            f"{STALL_BACKSTOP_MS:.0f}ms is past what a stall explains."
        )


class TestTaskCostVersusTaskCount:
    def test_one_task_update_costs_the_same_however_many_tasks_exist(self, tmp_path):
        """The 922 KB value: 647 tasks rewritten to change one status."""
        operations = []
        for task_count in (100, 1000, 10000):
            store = SQLiteStore(tmp_path / f"tasks-{task_count}" / "state.sqlite3")
            store.initialize()
            repo = TaskRepository(store)
            session_id = "__task_lifecycle__benchmark"

            with store.operation_scope(300.0) as owner:
                with store.write_transaction(owner):
                    for index in range(task_count):
                        repo.put_task(session_id, str(index), {
                            "id": str(index), "status": "pending",
                            "subject": f"Task {index}",
                            "updated_at": time.time(),
                        })

            def update(repo=repo, session_id=session_id):
                task = repo.get_task(session_id, "0")
                repo.put_task(session_id, "0", {**task, "status": "completed"})

            operations.append((task_count, update))

        rows = _time_interleaved(operations)
        _report("SQLite store: one task update, growing task count", rows)
        growth = rows[-1][1]["median_ms"] / max(rows[0][1]["median_ms"], 1e-9)
        assert growth < 2.0, (
            f"With 100x more tasks, updating one took {growth:.2f}x as long. "
            "The update is still materializing tasks it does not touch."
        )

    def test_asking_whether_anything_is_unfinished_stays_flat(self, tmp_path):
        """What the Stop hook asks, on every stop."""
        rows = []
        for task_count in (100, 1000, 10000):
            store = SQLiteStore(tmp_path / f"stop-{task_count}" / "state.sqlite3")
            store.initialize()
            repo = TaskRepository(store)
            session_id = "__task_lifecycle__benchmark"

            with store.operation_scope(300.0) as owner:
                with store.write_transaction(owner):
                    for index in range(task_count):
                        repo.put_task(session_id, str(index), {
                            "id": str(index), "status": "completed",
                            "subject": f"Task {index}",
                            "updated_at": time.time(),
                        })

            def check():
                repo.list_incomplete(session_id)

            rows.append((task_count, _time_repeatedly(check)))

        _report("SQLite store: incomplete-task check, growing task count", rows)
        growth = rows[-1][1]["median_ms"] / max(rows[0][1]["median_ms"], 1e-9)
        assert growth < 3.0, (
            f"With 100x more finished tasks the check took {growth:.2f}x as "
            "long, so finished work is still being decoded to establish that "
            "it is finished."
        )


class TestLifecycleProductionPath:
    """Measure the TaskLifecycle methods invoked by real hook handlers."""

    @staticmethod
    def _build(tmp_path, monkeypatch, task_count):
        state_dir = tmp_path / f"lifecycle-{task_count}"
        monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
        monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
        sm._reset_for_testing()
        lifecycle = TaskLifecycle(
            session_id="benchmark",
            config=TaskLifecycleConfig(storage_dir=tmp_path / "logs"),
        )
        # Establish the blob-to-row marker before loading the corpus.
        assert lifecycle.tasks == {}
        repository = sm.get_session_manager().task_repository()
        now = time.time()
        with repository._store.operation_scope(300.0) as owner:
            with repository._store.write_transaction(owner):
                for index in range(task_count):
                    repository.put_task(
                        lifecycle.global_key,
                        str(index),
                        {
                            "id": str(index),
                            "status": "completed" if index else "pending",
                            "subject": f"Task {index}",
                            "updated_at": now,
                            "created_at": now,
                            "session_id": lifecycle.session_id,
                            "metadata": {},
                            "blockedBy": [],
                            "blocks": [],
                            "tool_outputs": [],
                        },
                    )
        return lifecycle

    def test_hook_update_and_stop_latency_stay_flat(
        self, tmp_path, monkeypatch
    ):
        rows = []
        for task_count in (100, 1000, 10000):
            lifecycle = self._build(tmp_path, monkeypatch, task_count)

            def operation():
                lifecycle.update_task("0", {"status": "pending"}, "tick")
                lifecycle.get_incomplete_tasks()

            rows.append((task_count, _time_repeatedly(operation)))

        _report("TaskLifecycle: update plus Stop query", rows)
        growth = rows[-1][1]["median_ms"] / max(
            rows[0][1]["median_ms"], 1e-9
        )
        assert growth < 3.0, (
            f"With 100x more tasks the production hook path became "
            f"{growth:.2f}x slower."
        )

    def test_stop_query_peak_memory_does_not_follow_terminal_history(
        self, tmp_path, monkeypatch
    ):
        rows = []
        for task_count in (100, 10000):
            lifecycle = self._build(tmp_path, monkeypatch, task_count)
            rows.append((task_count, _peak_bytes(lifecycle.get_incomplete_tasks)))

        print(f"\nTaskLifecycle Stop peak bytes: {rows}")
        growth = rows[-1][1] / max(rows[0][1], 1)
        assert growth < 3.0, (
            f"With 100x more terminal tasks the Stop query allocated "
            f"{growth:.2f}x more peak memory."
        )


class TestStorageFootprint:
    def test_the_write_ahead_log_does_not_grow_with_the_number_of_writes(
        self, tmp_path
    ):
        store = SQLiteStore(tmp_path / "wal" / "state.sqlite3")
        store.initialize()
        wal = Path(str(store.db_path) + "-wal")

        sizes = []
        for round_index in range(5):
            for _ in range(100):
                with store.session("subject") as state:
                    state["field"] = SMALL_CHANGE + str(time.time())
            sizes.append(wal.stat().st_size if wal.exists() else 0)

        print(f"\nWrite-ahead log after each 100 writes: {sizes}")
        assert max(sizes) < 16 * 1024 * 1024, (
            f"The write-ahead log reached {max(sizes)} bytes over 500 small "
            "writes, so it is being pinned rather than checkpointed."
        )


# The prompt hook's whole budget is 10 s (hooks/hooks.json); the cache check
# on it must stay a small part of that however long the transcript grows.
PROMPT_CACHE_CHECK_P95_MS = 50.0


def test_the_prompt_cache_check_stays_fast_on_a_100_mb_transcript(tmp_path, monkeypatch):
    """Wall-clock companion to test_cache_expiry_at_prompt.py's byte count.

    100 MB of unreadable head, an expired 250k-token record at the end. The
    usage memo is disabled so every repetition reads the file again.
    """
    from autorun import cache_guard
    from autorun.core import EventContext, ThreadSafeDB

    fixture = PLUGIN_ROOT / "tests" / "fixtures" / "cache_expiry" / "claude_1h_lifetime_idle_2h_250k_tokens.jsonl"
    big = tmp_path / "big.jsonl"
    with open(big, "wb") as handle:
        handle.seek(100 * 1_000_000)
        handle.write(b"\n" + fixture.read_bytes())

    state = tmp_path / "sessions"
    state.mkdir()
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state))
    sm._reset_for_testing()

    class Clock:
        @staticmethod
        def time() -> float:
            return 1_794_398_400.0  # the fixture's "now": 2 h after its last record

    monkeypatch.setattr(cache_guard, "time", Clock)
    monkeypatch.setattr(cache_guard, "_MEMO_TTL_SECONDS", 0.0)
    ctx = EventContext(
        session_id="bench-prompt-cache",
        event="UserPromptSubmit",
        prompt="Continue with the refactor.",
        cli_type="claude",
        transcript_path=str(big),
        store=ThreadSafeDB(),
    )
    guard = cache_guard.CacheGuard.from_ctx(ctx)
    assert "cache expired" in json.dumps(guard.check_prompt_after_expiry(ctx))

    timing = _time_repeatedly(lambda: guard.check_prompt_after_expiry(ctx))
    print(f"prompt cache check on 100 MB: {timing}")
    assert timing["p95_ms"] < PROMPT_CACHE_CHECK_P95_MS, timing
    sm._reset_for_testing()


class TestRevision3Upgrade:
    """The upgrade every existing SQLite store meets once, at its first start.

    It runs inside ``initialize``'s write lock, so every other session waits
    for it: its lock time must not grow with the size of the store. Sizes are
    tasks shaped like the live store's (3 KB of payload each), kept small
    enough not to fill a disk.
    """

    @staticmethod
    def _old_store(path: Path, task_count: int) -> Path:
        from test_task_status_not_in_schema import PRE_3472B3EE_SCHEMA

        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(PRE_3472B3EE_SCHEMA)
        conn.execute(f"PRAGMA application_id = {sm.SCHEMA_APPLICATION_ID}")
        conn.execute("PRAGMA user_version = 2")
        conn.execute("BEGIN")
        conn.execute("INSERT INTO sessions VALUES ('__task_lifecycle__bench', 'task_lifecycle', 1.0)")
        payload = json.dumps({"subject": "y" * 3000})
        conn.executemany(
            "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) "
            "VALUES ('__task_lifecycle__bench', ?, ?, 1.0, ?)",
            ((str(index), "pending" if index % 100 == 0 else "completed", payload) for index in range(task_count)),
        )
        conn.execute("COMMIT")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        return path

    def test_revision_3_upgrade_cost(self, tmp_path):
        rows = []
        for task_count in (2_000, 20_000):
            path = self._old_store(tmp_path / f"upgrade-{task_count}" / "state.sqlite3", task_count)
            store = SQLiteStore(path)
            conn = store._connect(30.0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                start = time.perf_counter()
                needs_rebuild = sm._migrate_tasks_revision_3(conn)
                conn.execute("COMMIT")
                lock_ms = (time.perf_counter() - start) * 1000.0
                wal = Path(f"{path}-wal")
                wal_bytes = wal.stat().st_size if wal.exists() else 0
                check = conn.execute("PRAGMA integrity_check(tasks)").fetchall()
            finally:
                conn.close()
            assert needs_rebuild is False, "this SQLite refused the in-place rewrite"
            assert check == [("ok",)], check
            rows.append((task_count, path.stat().st_size, lock_ms, wal_bytes))

        print("\nRevision 3 upgrade: tasks, file bytes, write-lock ms, WAL bytes")
        for row in rows:
            print(f"{row[0]:>10} {row[1]:>12} {row[2]:>10.3f} {row[3]:>10}")
        for task_count, _size, lock_ms, wal_bytes in rows:
            assert lock_ms < 1000.0, f"{task_count} tasks held the write lock {lock_ms:.1f} ms"
            assert wal_bytes < 1024 * 1024, f"{task_count} tasks wrote {wal_bytes} WAL bytes"
        # Floored at 5 ms: both sizes take about 1 ms, where one scheduler
        # pause would decide the ratio. A step that reads tasks takes 100 ms
        # at 20,000 tasks (quick_check(tasks) measured 199.9 ms).
        growth = rows[-1][2] / max(rows[0][2], 5.0)
        assert growth < 3.0, (
            f"With 10x the tasks the upgrade held the write lock {growth:.2f}x as long; "
            "it is touching rows instead of only the table definition."
        )

    def test_stop_query_cost_on_1000_tasks_with_status_parameters(self, tmp_path):
        """Per-Stop cost on a 1,000-task session: this build's query and both alternatives.

        ``list_incomplete`` opens a connection per call, as every hook does, so
        it always starts with SQLite's page cache empty. For the raw queries
        "cold" is a new connection per run and "warm" reuses one; the OS file
        cache stays warm in both, which no unprivileged test can clear.
        """
        path = self._old_store(tmp_path / "stop" / "state.sqlite3", 1_000)
        store = SQLiteStore(path)
        store.initialize()  # revision 3
        repo = TaskRepository(store)
        session_id = "__task_lifecycle__bench"
        non_blocking = sorted(sm.task_status.NON_BLOCKING_TASK_STATUSES)
        queries = {
            "status NOT IN parameters only": (
                "SELECT task_id, status, updated_at, payload_json FROM tasks WHERE session = ? "
                f"AND status NOT IN ({','.join('?' * len(non_blocking))}) ORDER BY task_id",
                (session_id, *non_blocking),
            ),
            "generated-column partial index only": (
                "SELECT task_id, status, updated_at, payload_json FROM tasks "
                "WHERE session = ? AND blocks_stop = 1 ORDER BY task_id",
                (session_id,),
            ),
        }
        assert len(repo.list_incomplete(session_id)) == 10

        def cold(query):
            def run():
                conn = sqlite3.connect(path)
                try:
                    conn.execute(*query).fetchall()
                finally:
                    conn.close()
            return run

        results = {"list_incomplete": _time_repeatedly(lambda: repo.list_incomplete(session_id))["median_ms"]}
        print("\nStop query on a 1,000-task session, median ms")
        print(f"{'list_incomplete (new connection per call)':>45} {results['list_incomplete']:>10.3f}")
        for name, query in queries.items():
            conn = sqlite3.connect(path)
            try:
                warm = _time_repeatedly(lambda: conn.execute(*query).fetchall())["median_ms"]
            finally:
                conn.close()
            results[name] = _time_repeatedly(cold(query))["median_ms"]
            print(f"{name + ' warm':>45} {warm:>10.3f}")
            print(f"{name + ' cold':>45} {results[name]:>10.3f}")
        assert results["list_incomplete"] < results["status NOT IN parameters only"], (
            "the Stop query scans every task of the session instead of using the partial index"
        )
