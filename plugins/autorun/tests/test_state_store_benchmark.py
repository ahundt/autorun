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
                repo.mutate_task(session_id, "0",
                                 lambda task: {**task, "status": "completed"})

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
