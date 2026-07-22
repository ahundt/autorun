"""Canonical shape and transition rules of a stored task record.

Existing task-lifecycle tests cover ghost-task protection, dependency fields,
and stop-block accounting. This file covers what none of them pin down: the
exact record a task operation leaves behind, field by field, including the
defaults nobody sets explicitly.

That snapshot is the reference a storage change has to reproduce. Splitting a
task record across typed columns and a payload, for example, is only correct
if reading it back yields precisely these keys and values — no field dropped,
none invented, none reordered where order carries meaning.

Time-varying fields (``created_at``, ``updated_at``) are compared by type and
ordering rather than value, since they are wall-clock stamps.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as _sm_module  # noqa: E402
from autorun.session_manager import (  # noqa: E402
    SessionStateManager,
    _reset_for_testing,
    session_state,
)
from autorun.task_lifecycle import TaskLifecycle, TaskLifecycleConfig  # noqa: E402

# The full set of keys a task record carries. A backend that loses one of
# these, or adds one, has changed the contract even if every test that reads
# a specific field still passes.
TASK_RECORD_KEYS = {
    "id",
    "subject",
    "description",
    "activeForm",
    "status",
    "created_at",
    "updated_at",
    "session_id",
    "owner",
    "blockedBy",
    "blocks",
    "metadata",
    "tool_outputs",
}


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Give one test its own state directory.

    ``TaskLifecycle`` reaches the store through the module-level
    ``session_state()``, which resolves its directory from
    ``AUTORUN_TEST_STATE_DIR`` and caches one manager per resolved directory.
    Redirecting the variable and clearing those caches is therefore the only
    redirection the whole call chain honors; assigning the module singletons
    alone leaves ``session_state()`` pointed at the shared suite directory,
    where records from earlier tests are still present.
    """
    state_dir = tmp_path / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state_dir))
    _reset_for_testing()
    manager = SessionStateManager(state_dir=state_dir)
    _sm_module._manager = manager
    _sm_module._store = manager._store
    yield state_dir
    _reset_for_testing()


@pytest.fixture
def cfg(tmp_path):
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
    )


@pytest.fixture
def lifecycle(isolated_state, cfg):
    return TaskLifecycle(config=cfg, session_id="contract-session")


# ── The record a create leaves behind ────────────────────────────────────────

class TestCreatedRecordShape:
    def test_a_created_task_has_exactly_the_documented_fields(self, lifecycle):
        lifecycle.create_task(
            "1",
            {"subject": "Write docs", "description": "d", "activeForm": "Writing docs"},
            "tool said ok",
        )
        task = lifecycle.tasks["1"]

        assert set(task) == TASK_RECORD_KEYS, (
            "The stored task record gained or lost a field. Callers and the "
            f"resume path read these by name. Difference: "
            f"{set(task) ^ TASK_RECORD_KEYS}"
        )
        assert task["id"] == "1"
        assert task["subject"] == "Write docs"
        assert task["description"] == "d"
        assert task["activeForm"] == "Writing docs"
        assert task["status"] == "pending"
        assert task["session_id"] == "contract-session"
        assert task["owner"] is None
        assert task["blockedBy"] == []
        assert task["blocks"] == []
        assert task["metadata"] == {}
        assert task["tool_outputs"] == ["tool said ok"]
        assert isinstance(task["created_at"], float)
        assert isinstance(task["updated_at"], float)

    def test_omitted_input_fields_fall_back_to_empty_strings_not_none(self, lifecycle):
        """Downstream formatting concatenates these; None would raise."""
        lifecycle.create_task("1", {}, "result")
        task = lifecycle.tasks["1"]
        assert task["subject"] == ""
        assert task["description"] == ""
        assert task["activeForm"] == ""

    def test_a_gemini_title_is_stored_as_subject_on_create(self, lifecycle):
        lifecycle.create_task("1", {"title": "From Gemini"}, "result")
        assert lifecycle.tasks["1"]["subject"] == "From Gemini"

    def test_supplied_metadata_is_kept_verbatim(self, lifecycle):
        lifecycle.create_task("1", {"subject": "s", "metadata": {"k": [1, 2]}}, "r")
        assert lifecycle.tasks["1"]["metadata"] == {"k": [1, 2]}

    def test_creating_the_same_id_twice_leaves_the_first_record_untouched(self, lifecycle):
        lifecycle.create_task("1", {"subject": "original"}, "first")
        first = dict(lifecycle.tasks["1"])

        lifecycle.create_task("1", {"subject": "replacement"}, "second")
        second = lifecycle.tasks["1"]

        assert second == first, (
            "A repeated create must be ignored. Overwriting would discard the "
            "status and output history the first record accumulated."
        )


# ── The record an update leaves behind ───────────────────────────────────────

class TestUpdatedRecordShape:
    def test_a_gemini_title_update_writes_subject_and_adds_no_title_field(self, lifecycle):
        lifecycle.create_task("1", {"subject": "before"}, "r")
        lifecycle.update_task("1", {"title": "after"}, "r2")

        task = lifecycle.tasks["1"]
        assert task["subject"] == "after"
        assert "title" not in task, "The alias must be mapped, not stored alongside."
        assert set(task) == TASK_RECORD_KEYS

    def test_dependency_lists_extend_rather_than_replace(self, lifecycle):
        lifecycle.create_task("1", {"subject": "s"}, "r")
        lifecycle.update_task("1", {"addBlockedBy": ["a"], "addBlocks": ["x"]}, "r")
        lifecycle.update_task("1", {"addBlockedBy": ["b"], "addBlocks": ["y"]}, "r")

        task = lifecycle.tasks["1"]
        assert task["blockedBy"] == ["a", "b"]
        assert task["blocks"] == ["x", "y"]

    def test_metadata_merges_and_a_null_value_deletes_its_key(self, lifecycle):
        lifecycle.create_task("1", {"subject": "s", "metadata": {"keep": 1, "drop": 2}}, "r")
        lifecycle.update_task("1", {"metadata": {"added": 3, "drop": None}}, "r")

        assert lifecycle.tasks["1"]["metadata"] == {"keep": 1, "added": 3}

    def test_each_update_appends_its_result_in_order(self, lifecycle):
        lifecycle.create_task("1", {"subject": "s"}, "created")
        lifecycle.update_task("1", {"status": "in_progress"}, "started")
        lifecycle.update_task("1", {"status": "completed"}, "finished")

        assert lifecycle.tasks["1"]["tool_outputs"] == ["created", "started", "finished"], (
            "Tool output is an ordered audit trail; order and completeness are "
            "part of the contract."
        )

    def test_updated_at_advances_and_created_at_does_not(self, lifecycle):
        lifecycle.create_task("1", {"subject": "s"}, "r")
        before = dict(lifecycle.tasks["1"])
        lifecycle.update_task("1", {"status": "in_progress"}, "r")
        after = lifecycle.tasks["1"]

        assert after["created_at"] == before["created_at"]
        assert after["updated_at"] >= before["updated_at"]

    def test_updating_an_unknown_id_creates_a_non_blocking_placeholder(self, lifecycle):
        """The harness may complete a task the daemon never saw created.

        Recording it as a blocking task would strand Stop on work that is
        already finished, so it is recorded as ignored and flagged.
        """
        result = lifecycle.update_task("99", {"status": "completed"}, "done")

        task = lifecycle.tasks["99"]
        assert set(task) == TASK_RECORD_KEYS
        assert task["subject"] == "(unknown - created before tracking)"
        assert task["metadata"]["ghost_task"] is True
        assert task["status"] == "completed", "A terminal status is allowed through."
        assert result is None


# ── Ignoring ─────────────────────────────────────────────────────────────────

class TestIgnore:
    def test_ignoring_records_the_reason_and_reports_success(self, lifecycle):
        lifecycle.create_task("1", {"subject": "stuck"}, "r")
        assert lifecycle.ignore_task("1", "no longer relevant") is True

        task = lifecycle.tasks["1"]
        assert task["status"] == "ignored"
        assert task["metadata"]["ignore_reason"] == "no longer relevant"
        assert task["tool_outputs"][-1] == "User ignored task: no longer relevant"

    def test_ignoring_an_unknown_id_reports_failure_and_creates_nothing(self, lifecycle):
        assert lifecycle.ignore_task("missing") is False
        assert "missing" not in lifecycle.tasks


# ── Coupled session-level state ──────────────────────────────────────────────

class TestCoupledSessionState:
    def test_the_plan_to_task_mapping_updates_atomically(self, lifecycle):
        lifecycle.atomic_update_plan_tasks_map(
            lambda m: m.setdefault("plan-a", []).append("1")
        )
        lifecycle.atomic_update_plan_tasks_map(
            lambda m: m.setdefault("plan-a", []).append("2")
        )
        assert lifecycle.plan_tasks_map == {"plan-a": ["1", "2"]}

    def test_session_metadata_is_created_on_first_read_with_known_defaults(self, lifecycle):
        metadata = lifecycle.session_metadata
        assert metadata["session_id"] == "contract-session"
        assert metadata["stop_block_count"] == 0
        assert isinstance(metadata["created_at"], float)
        assert isinstance(metadata["last_activity"], float)

    def test_a_metadata_update_that_changes_nothing_is_not_written_back(self, lifecycle):
        lifecycle.atomic_update_metadata(lambda m: m.__setitem__("hits", 1))
        with session_state(lifecycle.global_key) as state:
            before = dict(state["session_metadata"])

        lifecycle.atomic_update_metadata(lambda m: m.__setitem__("hits", 1))

        with session_state(lifecycle.global_key) as state:
            assert dict(state["session_metadata"]) == before

    def test_the_schema_version_is_recorded_alongside_the_tasks(self, lifecycle):
        lifecycle.create_task("1", {"subject": "s"}, "r")
        with session_state(lifecycle.global_key) as state:
            assert state["schema_version"] == TaskLifecycle.SCHEMA_VERSION, (
                "Stored data must carry the version that wrote it, or a later "
                "migration cannot tell what it is looking at."
            )

    def test_task_state_lives_under_its_own_session_key(self, lifecycle):
        """Task data must not collide with the ordinary session it belongs to."""
        assert lifecycle.global_key == "__task_lifecycle__contract-session"
        lifecycle.create_task("1", {"subject": "s"}, "r")
        with session_state("contract-session") as ordinary:
            assert "tasks" not in ordinary
