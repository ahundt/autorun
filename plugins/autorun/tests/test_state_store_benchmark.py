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
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun.session_manager import (  # noqa: E402
    SQLiteStore,
    TaskRepository,
    session_state,
)

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


def _time_repeatedly(operation, repeats: int = REPEATS) -> dict:
    """Median and worst case of one operation, in milliseconds.

    Median because a single sample on a shared machine is noise; the maximum
    because a hook has a deadline and the worst case is what misses it.
    """
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - start) * 1000.0)
    return {
        "median_ms": statistics.median(samples),
        "p95_ms": sorted(samples)[int(len(samples) * 0.95) - 1],
        "max_ms": max(samples),
    }


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


class TestWriteCostVersusCorpusSize:
    def test_the_json_store_gets_slower_as_unrelated_state_grows(self, tmp_path):
        """Establishes that the thing being fixed is real and measurable."""
        rows = []
        for sessions in (100, 400, 1600):
            state_dir = tmp_path / f"json-{sessions}"
            state_dir.mkdir(parents=True)
            _fill_json(state_dir, sessions)

            def write():
                with session_state("subject", state_dir=str(state_dir)) as state:
                    state["field"] = SMALL_CHANGE + str(time.time())

            rows.append((sessions, _time_repeatedly(write)))

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
        rows = []
        for sessions in (100, 400, 1600):
            store = SQLiteStore(tmp_path / f"sqlite-{sessions}" / "state.sqlite3")
            store.initialize()
            _fill_sqlite(store, sessions)

            def write():
                with store.session("subject") as state:
                    state["field"] = SMALL_CHANGE + str(time.time())

            rows.append((sessions, _time_repeatedly(write)))

        _report("SQLite store: one small write, growing unrelated corpus", rows)
        growth = rows[-1][1]["median_ms"] / max(rows[0][1]["median_ms"], 1e-9)
        assert growth < 2.0, (
            f"A 16x larger corpus made one write {growth:.2f}x slower. The "
            "write is still reading or rewriting unrelated state."
        )

    def test_a_write_stays_inside_the_hook_budget(self, tmp_path):
        """A quarter second is the budget; the worst case is what misses it."""
        store = SQLiteStore(tmp_path / "budget" / "state.sqlite3")
        store.initialize()
        _fill_sqlite(store, 1600)

        def write():
            with store.session("subject") as state:
                state["field"] = SMALL_CHANGE + str(time.time())

        timing = _time_repeatedly(write, repeats=50)
        _report("SQLite store: hook budget", [(1600, timing)])
        assert timing["max_ms"] < 250.0, (
            f"The slowest of 50 writes took {timing['max_ms']:.1f}ms against a "
            "250ms hook budget, leaving no room for the rest of the hook."
        )


class TestTaskCostVersusTaskCount:
    def test_one_task_update_costs_the_same_however_many_tasks_exist(self, tmp_path):
        """The 922 KB value: 647 tasks rewritten to change one status."""
        rows = []
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

            def update():
                repo.mutate_task(session_id, "0",
                                 lambda task: {**task, "status": "completed"})

            rows.append((task_count, _time_repeatedly(update)))

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
                repo.list_incomplete(session_id,
                                     terminal_statuses=("completed", "deleted",
                                                        "ignored"))

            rows.append((task_count, _time_repeatedly(check)))

        _report("SQLite store: incomplete-task check, growing task count", rows)
        growth = rows[-1][1]["median_ms"] / max(rows[0][1]["median_ms"], 1e-9)
        assert growth < 3.0, (
            f"With 100x more finished tasks the check took {growth:.2f}x as "
            "long, so finished work is still being decoded to establish that "
            "it is finished."
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
