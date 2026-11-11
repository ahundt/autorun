"""Tasks as rows: one task changes without the others being touched.

Task state is 87.3 percent of the measured store, and the largest single
session holds 647 tasks in one 922 KB value. Every status change rewrites
that whole value today, so the cost of finishing one task grows with how many
tasks the session has ever had and how much output they accumulated.

Storing each task as its own row is only worth anything if reads and writes
are genuinely scoped to it, so the tests below prove that structurally rather
than by timing: a session is filled with task rows whose payloads cannot be
decoded, and operations on the one valid task are required to succeed. They
can only succeed if the others were never fetched.

Tool output and audit records move out of the task itself into append-only
event rows. That is what stops an active task from growing without bound,
and it is why the event identity rules — one row per logical event, a repeat
of the same event changing nothing — matter as much as the task rows do.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun.session_manager import (  # noqa: E402
    MISSING,
    SQLiteStore,
    SessionBackendError,
    TaskRepository,
)

SESSION = "__task_lifecycle__contract"
UNDECODABLE = "this is not json"
DECOY_TASKS = 1000

TASK_RECORD_KEYS = {
    "id", "subject", "description", "activeForm", "status",
    "created_at", "updated_at", "session_id", "owner",
    "blockedBy", "blocks", "metadata",
}


def _record(task_id="1", status="pending", **overrides):
    now = time.time()
    record = {
        "id": task_id,
        "subject": f"Task {task_id}",
        "description": "",
        "activeForm": "",
        "status": status,
        "created_at": now,
        "updated_at": now,
        "session_id": SESSION,
        "owner": None,
        "blockedBy": [],
        "blocks": [],
        "metadata": {},
    }
    record.update(overrides)
    return record


@pytest.fixture
def store(tmp_path):
    store = SQLiteStore(tmp_path / "state" / "daemon_state.sqlite3")
    store.initialize()
    return store


@pytest.fixture
def repo(store):
    return TaskRepository(store)


@pytest.fixture
def crowded(repo, store):
    """One readable task among many rows that would fail if decoded."""
    repo.put_task(SESSION, "readable", _record("readable"))
    now = time.time()
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as conn:
            conn.executemany(
                "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) "
                "VALUES (?,?,?,?,?)",
                [(SESSION, f"decoy_{i}", "pending", now, UNDECODABLE)
                 for i in range(DECOY_TASKS)],
            )
    return repo


# ── The record survives the column/payload split ─────────────────────────────

class TestRecordRoundTrip:
    def test_a_task_round_trips_field_for_field(self, repo):
        record = _record("1", subject="Write docs", metadata={"k": [1, 2]},
                         blockedBy=["2"], owner="someone")
        repo.put_task(SESSION, "1", record)

        assert repo.get_task(SESSION, "1") == record, (
            "Splitting a task across columns and a payload changed it. The "
            "resume path and every status report read these by name."
        )

    def test_an_unknown_task_is_reported_as_missing_not_empty(self, repo):
        assert repo.get_task(SESSION, "nope") is MISSING

    def test_the_status_column_tracks_the_record(self, repo, store):
        """Indexed queries read the column, so it cannot drift from the record."""
        repo.put_task(SESSION, "1", _record("1", status="pending"))
        repo.put_task(SESSION, "1", _record("1", status="completed"))

        with store.operation_scope(30.0) as owner:
            stored = owner.connection.execute(
                "SELECT status FROM tasks WHERE session = ? AND task_id = ?",
                (SESSION, "1"),
            ).fetchone()[0]
        assert stored == "completed"
        assert repo.get_task(SESSION, "1")["status"] == "completed"

    def test_a_task_may_be_stored_for_a_session_that_has_no_other_state(self, repo):
        """The owning session row is created by the write, not by a prior one."""
        repo.put_task("__task_lifecycle__brand-new", "1", _record("1"))
        assert repo.get_task("__task_lifecycle__brand-new", "1")["id"] == "1"

    def test_a_record_that_cannot_be_encoded_names_the_task(self, repo):
        with pytest.raises(SessionBackendError, match="1"):
            repo.put_task(SESSION, "1", _record("1", metadata={"bad": {1, 2}}))
        assert repo.get_task(SESSION, "1") is MISSING


# ── Reads and writes are scoped to one task ──────────────────────────────────

class TestTaskScope:
    def test_reading_one_task_does_not_decode_the_others(self, crowded):
        assert crowded.get_task(SESSION, "readable")["id"] == "readable"

    def test_listing_incomplete_tasks_does_not_decode_terminal_ones(self, repo, store):
        """Stop asks only whether anything is unfinished.

        Decoding finished tasks to answer that is exactly the cost this
        change exists to remove.
        """
        repo.put_task(SESSION, "open", _record("open", status="in_progress"))
        now = time.time()
        with store.operation_scope(30.0) as owner:
            with store.write_transaction(owner) as conn:
                conn.executemany(
                    "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) "
                    "VALUES (?,?,?,?,?)",
                    [(SESSION, f"done_{i}", "completed", now, UNDECODABLE)
                     for i in range(500)],
                )

        incomplete = repo.list_incomplete(SESSION)
        assert [task["id"] for task in incomplete] == ["open"]

    def test_stop_query_uses_the_partial_blocking_index(self, repo, store):
        repo.put_task(SESSION, "open", _record("open", status="in_progress"))
        repo.put_task(SESSION, "paused", _record("paused", status="paused"))
        with store.operation_scope(30.0) as owner:
            plan = owner.connection.execute(
                "EXPLAIN QUERY PLAN SELECT task_id FROM tasks "
                "WHERE session = ? AND blocks_stop = 1 ORDER BY task_id",
                (SESSION,),
            ).fetchall()
        assert any("tasks_incomplete" in str(row) for row in plan), plan

    @pytest.mark.parametrize(
        "status, blocks_stop, prunable",
        [
            ("pending", 1, 0),
            ("in_progress", 1, 0),
            ("paused", 0, 0),
            ("delegated", 0, 0),
            ("completed", 0, 1),
            ("deleted", 0, 1),
            ("ignored", 0, 1),
        ],
    )
    def test_generated_policy_columns_match_the_canonical_status_policy(
        self, repo, store, status, blocks_stop, prunable
    ):
        repo.put_task(SESSION, status, _record(status, status=status))
        with store.operation_scope(30.0) as owner:
            row = owner.connection.execute(
                "SELECT blocks_stop, prunable FROM tasks "
                "WHERE session = ? AND task_id = ?",
                (SESSION, status),
            ).fetchone()
        assert row == (blocks_stop, prunable)

    def test_unknown_status_is_rejected_before_it_can_bypass_stop(self, repo):
        with pytest.raises(SessionBackendError, match="Unknown task status"):
            repo.put_task(SESSION, "mystery", _record("mystery", status="mystery"))

    def test_the_bulk_listing_is_the_explicit_whole_session_path(self, crowded):
        """It really does read everything, which is what makes the rest scoping."""
        with pytest.raises(SessionBackendError) as raised:
            crowded.list_tasks(SESSION)
        assert "decoy_" in str(raised.value)

    def test_deleting_one_task_leaves_the_others(self, repo):
        repo.put_task(SESSION, "keep", _record("keep"))
        repo.put_task(SESSION, "drop", _record("drop"))

        repo.delete_task(SESSION, "drop")

        assert repo.get_task(SESSION, "drop") is MISSING
        assert repo.get_task(SESSION, "keep")["id"] == "keep"


# ── Retention selection ──────────────────────────────────────────────────────

class TestRetentionSelection:
    def test_only_terminal_tasks_past_the_cutoff_are_eligible(self, repo):
        old = time.time() - 86400
        recent = time.time()
        repo.put_task(SESSION, "old-done", _record("old-done", status="completed",
                                                   updated_at=old))
        repo.put_task(SESSION, "new-done", _record("new-done", status="completed",
                                                   updated_at=recent))
        repo.put_task(SESSION, "old-open", _record("old-open", status="in_progress",
                                                   updated_at=old))
        repo.put_task(SESSION, "old-paused", _record("old-paused", status="paused",
                                                     updated_at=old))

        eligible = repo.list_terminal_before(
            SESSION, statuses=("completed", "deleted", "ignored"),
            cutoff=time.time() - 3600,
        )
        assert [task["id"] for task in eligible] == ["old-done"], (
            "Retention selected something it must not touch. A paused task is "
            "work the user intends to resume, and an unfinished one is still "
            "live."
        )

    def test_a_future_timestamp_is_treated_as_live(self, repo):
        """A clock anomaly must not make a task look ancient and prunable."""
        repo.put_task(SESSION, "future", _record("future", status="completed",
                                                 updated_at=time.time() + 86400))
        eligible = repo.list_terminal_before(
            SESSION, statuses=("completed",), cutoff=time.time() - 3600)
        assert eligible == []


# ── Events replace the growing output list ───────────────────────────────────

class TestEvents:
    def test_an_event_is_stored_and_read_back(self, repo):
        repo.put_task(SESSION, "1", _record("1"))
        repo.append_event(SESSION, "1", event_id="e1", idempotency_key="k1",
                          event_type="created", payload={"output": "ok"})

        events = repo.events(SESSION, task_id="1")
        assert [(e["event_id"], e["payload"]) for e in events] == [
            ("e1", {"output": "ok"})
        ]

    def test_events_come_back_in_the_order_they_happened(self, repo):
        repo.put_task(SESSION, "1", _record("1"))
        base = time.time()
        for index in range(5):
            repo.append_event(SESSION, "1", event_id=f"e{index}",
                              idempotency_key=f"k{index}", event_type="output",
                              payload={"n": index}, created_at=base + index)

        assert [e["payload"]["n"] for e in repo.events(SESSION, task_id="1")] == [
            0, 1, 2, 3, 4
        ]

    def test_events_written_in_the_same_instant_still_have_one_order(self, repo):
        """A clock with coarse resolution must not make ordering ambiguous."""
        repo.put_task(SESSION, "1", _record("1"))
        stamp = time.time()
        for index in range(5):
            repo.append_event(SESSION, "1", event_id=f"e{index}",
                              idempotency_key=f"k{index}", event_type="output",
                              payload={"n": index}, created_at=stamp)

        ordered = [e["event_id"] for e in repo.events(SESSION, task_id="1")]
        assert ordered == sorted(ordered)
        assert len(ordered) == 5

    def test_repeating_an_event_changes_nothing(self, repo):
        """A hook that retries must not append the same output twice."""
        repo.put_task(SESSION, "1", _record("1"))
        payload = {"output": "ok"}
        repo.append_event(SESSION, "1", event_id="e1", idempotency_key="k1",
                          event_type="output", payload=payload)
        repo.append_event(SESSION, "1", event_id="e1", idempotency_key="k1",
                          event_type="output", payload=payload)

        assert len(repo.events(SESSION, task_id="1")) == 1

    def test_reusing_a_key_for_different_content_is_refused(self, repo):
        """Two different events sharing a key means the key is wrong.

        Accepting either one silently would drop a real event or record a
        false one, so it has to be visible.
        """
        repo.put_task(SESSION, "1", _record("1"))
        repo.append_event(SESSION, "1", event_id="e1", idempotency_key="k1",
                          event_type="output", payload={"output": "first"})

        with pytest.raises(SessionBackendError, match="k1"):
            repo.append_event(SESSION, "1", event_id="e2", idempotency_key="k1",
                              event_type="output", payload={"output": "second"})

    def test_events_are_paged_rather_than_materialized(self, repo):
        repo.put_task(SESSION, "1", _record("1"))
        base = time.time()
        for index in range(50):
            repo.append_event(SESSION, "1", event_id=f"e{index:03d}",
                              idempotency_key=f"k{index}", event_type="output",
                              payload={"n": index}, created_at=base + index)

        first = repo.events(SESSION, task_id="1", limit=10)
        assert len(first) == 10
        second = repo.events(SESSION, task_id="1", limit=10, after=first[-1])
        assert len(second) == 10
        assert {e["event_id"] for e in first}.isdisjoint(e["event_id"] for e in second)
        assert [e["payload"]["n"] for e in second] == list(range(10, 20))

    def test_a_session_level_event_needs_no_task(self, repo):
        """Migration and pruning records belong to the session, not a task."""
        repo.append_event(SESSION, None, event_id="m1", idempotency_key="mk1",
                          event_type="migration", payload={"from": 1, "to": 3})
        assert len(repo.events(SESSION)) == 1

    def test_deleting_a_task_removes_its_events(self, repo):
        repo.put_task(SESSION, "1", _record("1"))
        repo.append_event(SESSION, "1", event_id="e1", idempotency_key="k1",
                          event_type="output", payload={})
        repo.delete_task(SESSION, "1")

        assert repo.events(SESSION, task_id="1") == []


# ── Atomicity ────────────────────────────────────────────────────────────────

class TestAtomicity:
    def test_a_task_its_event_and_a_session_field_commit_together(self, repo, store):
        """A finished task and the counter it resets must not diverge."""
        repo.put_task(SESSION, "1", _record("1", status="in_progress"))

        with store.operation_scope(30.0) as owner:
            with store.write_transaction(owner):
                repo.put_task(SESSION, "1", _record("1", status="completed"))
                repo.append_event(SESSION, "1", event_id="e1", idempotency_key="k1",
                                  event_type="completed", payload={})
                with store.session(SESSION) as state:
                    state["session_metadata"] = {"stop_block_count": 0}

        assert repo.get_task(SESSION, "1")["status"] == "completed"
        assert len(repo.events(SESSION, task_id="1")) == 1
        assert store.read_field(SESSION, "session_metadata") == {"stop_block_count": 0}

    def test_a_failure_discards_the_task_the_event_and_the_field_alike(
        self, repo, store
    ):
        repo.put_task(SESSION, "1", _record("1", status="in_progress"))

        with pytest.raises(RuntimeError):
            with store.operation_scope(30.0) as owner:
                with store.write_transaction(owner):
                    repo.put_task(SESSION, "1", _record("1", status="completed"))
                    repo.append_event(SESSION, "1", event_id="e1",
                                      idempotency_key="k1", event_type="completed",
                                      payload={})
                    with store.session(SESSION) as state:
                        state["session_metadata"] = {"stop_block_count": 0}
                    raise RuntimeError("failed after staging everything")

        assert repo.get_task(SESSION, "1")["status"] == "in_progress", (
            "The task moved while its coupled records did not."
        )
        assert repo.events(SESSION, task_id="1") == []
        assert store.read_field(SESSION, "session_metadata") is MISSING
