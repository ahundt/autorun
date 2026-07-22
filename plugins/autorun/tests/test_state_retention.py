"""Deleting old state, conservatively, and never by accident.

The measured store holds 8,106 sessions because collection is manual and
gated behind a confirmation, so in practice it never runs. Automating it is
the point — but automated deletion of state that decides what the assistant
is allowed to do is worth being careful about, so the rules here are shaped
around what must *not* happen:

  * an unrecognized session is protected, not swept. A namespace nobody has
    classified is exactly the one whose deletion consequences are unknown.
  * unfinished work is protected regardless of age. A paused task is work
    someone intends to resume; pruning it discards that intent.
  * nothing is deleted before it is archived and the archive is on disk.
  * a session is expired as a whole or not at all. Removing individual old
    fields would leave a session that half exists.

Deletion is off by default and reports what it would do. Turning it on is a
decision, not a default.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun.session_manager import (  # noqa: E402
    MISSING,
    RetentionPolicy,
    SQLiteStore,
    SessionBackendError,
    StateRetention,
    TaskRepository,
)
import autorun.session_manager as session_manager_module  # noqa: E402


def test_state_maintenance_cli_reports_the_existing_database(
    tmp_path, monkeypatch, capsys
):
    from autorun.__main__ import main

    state_dir = tmp_path / "cli-state"
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    store = SQLiteStore(state_dir / "daemon_state.sqlite3")
    store.initialize()

    assert main(["--state-maintenance"]) == 0
    output = capsys.readouterr().out
    assert "database bytes" in output
    assert "wal bytes" in output
    assert "reclaimable bytes" in output

DAY = 86400.0


@pytest.fixture
def store(tmp_path):
    store = SQLiteStore(tmp_path / "state" / "daemon_state.sqlite3")
    store.initialize()
    return store


@pytest.fixture
def repo(store):
    return TaskRepository(store)


@pytest.fixture
def archive_dir(tmp_path):
    path = tmp_path / "archive"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _age_session(store, session_id, seconds_ago):
    """Backdate a session, since last_modified only ever moves forward."""
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as conn:
            conn.execute(
                "UPDATE sessions SET last_modified = ? WHERE session = ?",
                (time.time() - seconds_ago, session_id),
            )


def _seed(store, session_id, seconds_ago=0.0):
    with store.session(session_id) as state:
        state["marker"] = session_id
    if seconds_ago:
        _age_session(store, session_id, seconds_ago)


@pytest.fixture
def retention(store, archive_dir):
    return StateRetention(
        store,
        RetentionPolicy(session_max_age_seconds=7 * DAY, archive_dir=archive_dir),
    )


class TestNothingIsDeletedByDefault:
    def test_a_sweep_reports_instead_of_deleting(self, store, retention):
        _seed(store, "ancient-session", seconds_ago=30 * DAY)

        report = retention.sweep_sessions()

        assert report["eligible"] == ["ancient-session"]
        assert report["deleted"] == []
        assert report["report_only"] is True
        assert store.read_field("ancient-session", "marker") == "ancient-session", (
            "A report-only sweep deleted state."
        )

    def test_deleting_requires_saying_so(self, store, archive_dir):
        _seed(store, "ancient-session", seconds_ago=30 * DAY)
        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )

        report = retention.sweep_sessions()

        assert report["deleted"] == ["ancient-session"]
        assert store.read_field("ancient-session", "marker") is MISSING


class TestWhatIsProtected:
    @pytest.mark.parametrize(
        "session_id",
        ["__global__", "__plan_export__"],
    )
    def test_permanently_protected_namespaces_are_never_eligible(
        self, store, retention, session_id
    ):
        _seed(store, session_id, seconds_ago=365 * DAY)
        assert retention.sweep_sessions()["eligible"] == []

    def test_an_unrecognized_namespace_is_protected(self, store, archive_dir):
        """Protection is the safe default for something nobody classified."""
        _seed(store, "monitor_something", seconds_ago=365 * DAY)
        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )

        report = retention.sweep_sessions()

        assert report["eligible"] == []
        assert "monitor_something" in report["protected"]
        assert store.read_field("monitor_something", "marker") is not MISSING

    def test_a_recent_session_is_not_eligible(self, store, retention):
        _seed(store, "recent-session", seconds_ago=DAY)
        assert retention.sweep_sessions()["eligible"] == []

    def test_the_current_session_is_protected_however_old_it_looks(
        self, store, archive_dir
    ):
        """A clock anomaly must not expire the session that is running."""
        _seed(store, "live-session", seconds_ago=365 * DAY)
        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True,
                            protected_sessions=("live-session",)),
        )

        report = retention.sweep_sessions()

        assert report["eligible"] == []
        assert store.read_field("live-session", "marker") is not MISSING

    def test_a_session_with_unfinished_tasks_is_protected(
        self, store, repo, archive_dir
    ):
        session_id = "__task_lifecycle__busy"
        repo.put_task(session_id, "1", {"id": "1", "status": "in_progress",
                                        "updated_at": time.time()})
        _age_session(store, session_id, 365 * DAY)
        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )

        report = retention.sweep_sessions()

        assert session_id not in report["eligible"]
        assert repo.get_task(session_id, "1") is not MISSING

    def test_a_future_timestamp_is_treated_as_live(self, store, archive_dir):
        _seed(store, "future-session")
        with store.operation_scope(30.0) as owner:
            with store.write_transaction(owner) as conn:
                conn.execute(
                    "UPDATE sessions SET last_modified = ? WHERE session = ?",
                    (time.time() + 365 * DAY, "future-session"),
                )
        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )

        report = retention.sweep_sessions()

        assert report["eligible"] == []
        assert "future-session" in report["anomalies"], (
            "A timestamp in the future is a clock problem worth reporting, "
            "not something to silently ignore."
        )

    def test_a_session_resumed_after_archiving_is_not_deleted(
        self, store, repo, archive_dir, monkeypatch
    ):
        session_id = "__task_lifecycle__resumed-during-sweep"
        _seed(store, session_id, seconds_ago=30 * DAY)
        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        archive_sessions = retention._archive_sessions

        def archive_then_resume(session_ids):
            archive = archive_sessions(session_ids)
            repo.put_task(
                session_id, "resumed",
                {"id": "resumed", "status": "in_progress",
                 "updated_at": time.time()},
            )
            return archive

        monkeypatch.setattr(retention, "_archive_sessions", archive_then_resume)

        report = retention.sweep_sessions()

        assert report["deleted"] == []
        assert report["changed_after_archive"] == [session_id]
        assert repo.get_task(session_id, "resumed") is not MISSING


class TestExpiryIsWholeSessions:
    def test_expiring_a_session_removes_all_of_its_rows(
        self, store, repo, archive_dir
    ):
        session_id = "__task_lifecycle__finished"
        repo.put_task(session_id, "1", {"id": "1", "status": "completed",
                                        "updated_at": time.time() - 30 * DAY})
        repo.append_event(session_id, "1", event_id="e1", idempotency_key="k1",
                          event_type="output", payload={"n": 1})
        with store.session(session_id) as state:
            state["session_metadata"] = {"stop_block_count": 0}
        _age_session(store, session_id, 30 * DAY)

        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        assert retention.sweep_sessions()["deleted"] == [session_id]

        assert store.read_field(session_id, "session_metadata") is MISSING
        assert repo.get_task(session_id, "1") is MISSING
        assert repo.events(session_id) == []

    def test_one_session_expiring_leaves_its_neighbours_alone(
        self, store, archive_dir
    ):
        _seed(store, "old-session", seconds_ago=30 * DAY)
        _seed(store, "young-session", seconds_ago=DAY)

        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        retention.sweep_sessions()

        assert store.read_field("young-session", "marker") == "young-session"


class TestArchiveBeforeDelete:
    def test_the_archive_is_written_before_anything_is_removed(
        self, store, retention, archive_dir
    ):
        _seed(store, "ancient-session", seconds_ago=30 * DAY)
        retention.policy.delete = True

        report = retention.sweep_sessions()

        archives = list(archive_dir.glob("*.json"))
        assert archives, "nothing was archived before deletion"
        archived = json.loads(archives[0].read_text(encoding="utf-8"))
        assert archived["sessions"]["ancient-session"]["state"]["marker"] == \
            "ancient-session"
        assert report["archive"] == str(archives[0])

    def test_a_failed_archive_stops_the_deletion(
        self, store, retention, archive_dir, monkeypatch
    ):
        _seed(store, "ancient-session", seconds_ago=30 * DAY)
        retention.policy.delete = True

        def refuse(*args, **kwargs):
            raise OSError("No space left on device")

        monkeypatch.setattr(type(retention), "_publish_archive", refuse)

        with pytest.raises(OSError):
            retention.sweep_sessions()
        monkeypatch.undo()

        assert store.read_field("ancient-session", "marker") is not MISSING, (
            "State was deleted although its archive was never written."
        )

    def test_a_failed_archive_removes_its_exclusive_reservation(
        self, retention, archive_dir, monkeypatch
    ):
        def refuse(*args, **kwargs):
            raise OSError("No space left on device")

        monkeypatch.setattr(session_manager_module, "atomic_write_json", refuse)

        with pytest.raises(OSError, match="No space left on device"):
            retention._publish_archive({"state": "not durable"}, "failed")

        assert list(archive_dir.iterdir()) == []

    def test_the_archive_name_carries_a_sortable_timestamp(
        self, store, retention, archive_dir
    ):
        import re

        _seed(store, "ancient-session", seconds_ago=30 * DAY)
        retention.policy.delete = True
        retention.sweep_sessions()

        name = next(iter(archive_dir.glob("*.json"))).name
        assert re.search(r"\d{4}-\d{2}-\d{2}-\d{4}", name), name


class TestTaskPruning:
    def test_only_finished_tasks_past_the_cutoff_are_pruned(
        self, store, repo, archive_dir
    ):
        session_id = "__task_lifecycle__mixed"
        old = time.time() - 30 * DAY
        repo.put_task(session_id, "done", {"id": "done", "status": "completed",
                                           "updated_at": old})
        repo.put_task(session_id, "paused", {"id": "paused", "status": "paused",
                                             "updated_at": old})
        repo.put_task(session_id, "open", {"id": "open", "status": "in_progress",
                                           "updated_at": old})

        retention = StateRetention(
            store,
            RetentionPolicy(task_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        report = retention.prune_tasks(session_id)

        assert report["deleted"] == ["done"]
        assert repo.get_task(session_id, "paused") is not MISSING, (
            "A paused task was pruned. Pausing is an intention to resume."
        )
        assert repo.get_task(session_id, "open") is not MISSING

    def test_pruning_a_task_does_not_remove_its_session(
        self, store, repo, archive_dir
    ):
        session_id = "__task_lifecycle__keeps-living"
        repo.put_task(session_id, "done", {"id": "done", "status": "completed",
                                           "updated_at": time.time() - 30 * DAY})
        with store.session(session_id) as state:
            state["session_metadata"] = {"stop_block_count": 2}

        retention = StateRetention(
            store,
            RetentionPolicy(task_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        retention.prune_tasks(session_id)

        assert store.read_field(session_id, "session_metadata") == \
            {"stop_block_count": 2}

    def test_pruning_reports_without_deleting_by_default(
        self, store, repo, archive_dir
    ):
        session_id = "__task_lifecycle__report-only"
        repo.put_task(session_id, "done", {"id": "done", "status": "completed",
                                           "updated_at": time.time() - 30 * DAY})
        retention = StateRetention(
            store, RetentionPolicy(task_max_age_seconds=7 * DAY,
                                   archive_dir=archive_dir),
        )

        report = retention.prune_tasks(session_id)

        assert report["eligible"] == ["done"]
        assert report["deleted"] == []
        assert repo.get_task(session_id, "done") is not MISSING

    def test_a_task_resumed_after_archiving_is_not_deleted(
        self, store, repo, archive_dir, monkeypatch
    ):
        session_id = "__task_lifecycle__resumed-during-prune"
        old = time.time() - 30 * DAY
        repo.put_task(
            session_id, "done",
            {"id": "done", "status": "completed", "updated_at": old},
        )
        retention = StateRetention(
            store,
            RetentionPolicy(task_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        archive_tasks = retention._archive_tasks

        def archive_then_resume(archived_session, tasks):
            archive = archive_tasks(archived_session, tasks)
            repo.put_task(
                session_id, "done",
                {"id": "done", "status": "in_progress",
                 "updated_at": time.time()},
            )
            return archive

        monkeypatch.setattr(retention, "_archive_tasks", archive_then_resume)

        report = retention.prune_tasks(session_id)

        assert report["deleted"] == []
        assert report["changed_after_archive"] == ["done"]
        assert repo.get_task(session_id, "done")["status"] == "in_progress"

    def test_an_event_appended_after_archiving_prevents_task_deletion(
        self, store, repo, archive_dir, monkeypatch
    ):
        session_id = "__task_lifecycle__event-during-prune"
        old = time.time() - 30 * DAY
        repo.put_task(
            session_id, "done",
            {"id": "done", "status": "completed", "updated_at": old},
        )
        retention = StateRetention(
            store,
            RetentionPolicy(task_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        archive_tasks = retention._archive_tasks

        def archive_then_append_event(archived_session, tasks):
            archive = archive_tasks(archived_session, tasks)
            repo.append_event(
                session_id, "done",
                event_id="after-archive",
                idempotency_key="after-archive",
                event_type="OUTPUT",
                payload={"text": "new durable output"},
            )
            return archive

        monkeypatch.setattr(retention, "_archive_tasks", archive_then_append_event)

        report = retention.prune_tasks(session_id)

        assert report["deleted"] == []
        assert report["changed_after_archive"] == ["done"]
        assert [event["event_id"]
                for event in repo.events(session_id, "done")] == ["after-archive"]


class TestMaintenance:
    def test_concurrent_archives_reserve_distinct_destinations(
        self, store, archive_dir, monkeypatch
    ):
        retention = StateRetention(
            store, RetentionPolicy(archive_dir=archive_dir, delete=True)
        )
        real_atomic_write = session_manager_module.atomic_write_json
        both_ready = threading.Barrier(2)

        def synchronized_write(path, payload, **kwargs):
            both_ready.wait(timeout=5)
            real_atomic_write(path, payload, **kwargs)

        monkeypatch.setattr(
            session_manager_module, "atomic_write_json", synchronized_write
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(retention._publish_archive,
                            {"writer": writer}, "same-prefix")
                for writer in ("one", "two")
            ]
            destinations = [future.result(timeout=10) for future in futures]

        assert len(set(destinations)) == 2
        assert {
            json.loads(destination.read_text(encoding="utf-8"))["writer"]
            for destination in destinations
        } == {"one", "two"}

    def test_a_backup_is_a_usable_database(self, store, tmp_path):
        with store.session("s") as state:
            state["a"] = 1

        destination = tmp_path / "backup" / "daemon_state.sqlite3"
        result = StateRetention(store, RetentionPolicy()).backup(destination)

        assert result["destination"] == str(destination)
        restored = SQLiteStore(destination)
        restored.initialize()
        assert restored.read_field("s", "a") == 1, (
            "The backup does not contain committed state. Copying the main "
            "file while a write-ahead log is active loses whatever is in it."
        )

    def test_a_backup_includes_writes_made_just_before_it(self, store, tmp_path):
        with store.session("s") as state:
            state["a"] = 1
        with store.session("s") as state:
            state["b"] = 2

        destination = tmp_path / "backup" / "daemon_state.sqlite3"
        StateRetention(store, RetentionPolicy()).backup(destination)

        restored = SQLiteStore(destination)
        restored.initialize()
        assert restored.read_fields("s", ["a", "b"]) == {"a": 1, "b": 2}

    def test_a_backup_does_not_overwrite_an_existing_file(self, store, tmp_path):
        destination = tmp_path / "backup" / "daemon_state.sqlite3"
        destination.parent.mkdir(parents=True)
        destination.write_text("something else lives here", encoding="utf-8")

        with pytest.raises(SessionBackendError):
            StateRetention(store, RetentionPolicy()).backup(destination)

        assert destination.read_text(encoding="utf-8") == "something else lives here"

    def test_maintenance_reports_what_space_could_be_reclaimed(self, store):
        for index in range(50):
            with store.session(f"session-{index}") as state:
                state["blob"] = "x" * 1024

        report = StateRetention(store, RetentionPolicy()).maintenance()

        assert report["page_size"] > 0
        assert report["page_count"] > 0
        assert report["freelist_count"] >= 0
        assert report["wal_bytes"] >= 0

    def test_maintenance_reclaims_pages_after_a_deletion(self, store, archive_dir):
        for index in range(50):
            _seed(store, f"session-{index}", seconds_ago=30 * DAY)

        retention = StateRetention(
            store,
            RetentionPolicy(session_max_age_seconds=7 * DAY,
                            archive_dir=archive_dir, delete=True),
        )
        retention.sweep_sessions()

        before = retention.maintenance()
        after = retention.maintenance(reclaim=True)

        assert after["freelist_count"] <= before["freelist_count"], (
            "Reclaiming did not reduce the free pages, so deleted space is "
            "never returned and the file only grows."
        )
