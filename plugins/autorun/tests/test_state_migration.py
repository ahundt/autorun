"""Moving state to the new store, interruptibly and without losing anything.

Migration spans two artifacts that no single transaction covers: a JSON file
and a database. A crash can land between them, and the recovery has to know
which side is authoritative without guessing. That is what the receipt is
for — it records what was verified and what was published, so a resumed
migration continues rather than starting from an unknown state.

The properties below are what "safe to run" means here:

  * every value survives, including the ones that are easy to lose — a stored
    ``None``, a field whose name contains the separator, a nested structure;
  * a mismatch stops the migration with the JSON still authoritative, rather
    than publishing a database that does not match its source;
  * an interruption at any phase resumes to the same end state;
  * the original file is never deleted, only renamed, so rollback stays
    possible without trusting anything this code wrote.
"""
from __future__ import annotations

import json
import re
import sys
import threading
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
    StateMigrator,
    _JSONStore,
)

TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}-\d{4}")


@pytest.fixture
def paths(tmp_path):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    return {
        "dir": directory,
        "json": directory / "daemon_state.json",
        "db": directory / "daemon_state.sqlite3",
        "receipt": directory / "daemon_state.migration.json",
    }


def _write_legacy(paths, data):
    paths["json"].write_text(json.dumps(data), encoding="utf-8")


def _migrator(paths):
    return StateMigrator(paths["json"], paths["db"], paths["receipt"])


LEGACY = {
    "plain/field": "value",
    "plain/stored_none": None,
    "plain/nested": {"deep": [1, {"deeper": True}]},
    "plain/with/slash": "field name contains the separator",
    "unicode-сессия/ключ": "значение",
    "__global__/toggle": {"enabled": True},
    "__task_lifecycle__abc/schema_version": 3,
    "__task_lifecycle__abc/tasks": {
        "1": {"id": "1", "status": "completed", "subject": "done",
              "tool_outputs": ["created", "finished"]},
        "2": {"id": "2", "status": "pending", "subject": "todo",
              "tool_outputs": []},
    },
}


class TestEverythingSurvives:
    def test_every_session_and_field_is_carried_across(self, paths):
        _write_legacy(paths, LEGACY)
        result = _migrator(paths).migrate()
        assert result["phase"] == "COMPLETE", result

        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("plain", "field") == "value"
        assert store.read_field("plain", "nested") == {"deep": [1, {"deeper": True}]}
        assert store.read_field("__global__", "toggle") == {"enabled": True}
        assert store.read_field("unicode-сессия", "ключ") == "значение"

    def test_a_stored_none_stays_distinguishable_from_absent(self, paths):
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()

        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("plain", "stored_none") is None
        assert store.read_field("plain", "never_existed") is MISSING

    def test_a_field_name_containing_the_separator_survives(self, paths):
        """Only the first separator divides session from field."""
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()

        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("plain", "with/slash") == \
            "field name contains the separator"

    def test_the_counts_are_reported_and_match(self, paths):
        _write_legacy(paths, LEGACY)
        result = _migrator(paths).migrate()
        assert result["fields"] == len(LEGACY)
        assert result["sessions"] == len({k.split("/", 1)[0] for k in LEGACY})

    def test_an_absent_source_produces_an_empty_store_rather_than_failing(self, paths):
        result = _migrator(paths).migrate()
        assert result["phase"] == "COMPLETE"
        assert result["fields"] == 0
        assert paths["db"].exists()

    def test_slash_bearing_session_alias_remains_readable_after_migration(
        self, paths
    ):
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()

        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("plain/with", "slash") == (
            "field name contains the separator"
        )


class TestIdempotence:
    def test_running_it_twice_changes_nothing(self, paths):
        _write_legacy(paths, LEGACY)
        first = _migrator(paths).migrate()
        digest_before = paths["db"].read_bytes()

        second = _migrator(paths).migrate()

        assert second["phase"] == "COMPLETE"
        assert second["already_complete"] is True, (
            "A completed migration ran again. Re-importing a retired source "
            "would resurrect values the daemon has since changed."
        )
        assert paths["db"].read_bytes() == digest_before
        assert first["backup"] == second["backup"]

    def test_a_completed_migration_does_not_touch_a_reappearing_source(self, paths):
        """An old process writing JSON after cutover must not be merged in."""
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()

        paths["json"].write_text(json.dumps({"intruder/field": "from an old writer"}),
                                 encoding="utf-8")
        result = _migrator(paths).migrate()

        assert result["already_complete"] is True
        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("intruder", "field") is MISSING, (
            "State written after cutover was silently merged. That file is "
            "evidence of a second writer, not input."
        )
        assert result.get("unexpected_source") is True, (
            "A source file reappearing after cutover has to be reported; it "
            "means something else is still writing."
        )

    def test_an_existing_destination_must_match_the_prepared_generation(self, paths):
        existing = SQLiteStore(paths["db"])
        existing.initialize()
        with existing.session("sess") as state:
            state["value"] = "database-old"
        _write_legacy(paths, {"sess/value": "json-new"})

        with pytest.raises(SessionBackendError, match="generation|destination|database"):
            _migrator(paths).migrate()

        assert json.loads(paths["json"].read_text(encoding="utf-8")) == {
            "sess/value": "json-new"
        }

    def test_rollback_then_remigration_never_reuses_the_old_database(self, paths):
        _write_legacy(paths, {"sess/value": "v1"})
        _migrator(paths).migrate()
        store = SQLiteStore(paths["db"])
        store.initialize()
        with store.session("sess") as state:
            state["value"] = "v2"

        _migrator(paths).rollback()
        restored = json.loads(paths["json"].read_text(encoding="utf-8"))
        restored["sess/value"] = "v3"
        _write_legacy(paths, restored)
        result = _migrator(paths).migrate()

        assert result["phase"] == "COMPLETE"
        remigrated = SQLiteStore(paths["db"])
        remigrated.initialize()
        assert remigrated.read_field("sess", "value") == "v3"


class TestCutoverCoordination:
    def test_a_json_store_opened_before_cutover_cannot_recreate_the_source(
        self, paths
    ):
        """A daemon from another install must observe the authority handoff."""
        _write_legacy(paths, {"sess/value": "before"})
        stale_store = _JSONStore(
            str(paths["json"]), str(paths["json"]) + ".lock"
        )
        with stale_store.session("sess") as state:
            assert state["value"] == "before"

        _migrator(paths).migrate()

        with pytest.raises(SessionBackendError, match="SQLite.*authoritative"):
            with stale_store.session("sess") as state:
                state["value"] = "stale-writer"

        assert not paths["json"].exists()
        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("sess", "value") == "before"

    def test_a_pre_cutover_json_store_works_again_after_supported_rollback(
        self, paths
    ):
        _write_legacy(paths, {"sess/value": "before"})
        stale_store = _JSONStore(
            str(paths["json"]), str(paths["json"]) + ".lock"
        )
        _migrator(paths).migrate()

        with pytest.raises(SessionBackendError):
            with stale_store.session("sess"):
                pass

        _migrator(paths).rollback()
        with stale_store.session("sess") as state:
            state["value"] = "after-rollback"

        restored = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert restored["sess/value"] == "after-rollback"

    def test_a_write_after_verification_is_included_before_retirement(
        self, paths, monkeypatch
    ):
        _write_legacy(paths, {"sess/original": "kept"})
        migrator = _migrator(paths)
        real_retire = type(migrator)._retire_source

        def write_then_retire(self):
            current = json.loads(paths["json"].read_text(encoding="utf-8"))
            current["sess/late"] = "also kept"
            _write_legacy(paths, current)
            return real_retire(self)

        monkeypatch.setattr(type(migrator), "_retire_source", write_then_retire)
        result = migrator.migrate()
        assert result["phase"] == "COMPLETE"

        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("sess", "late") == "also kept"

    def test_two_migrators_share_one_exclusive_generation(self, paths):
        _write_legacy(paths, LEGACY)
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def run():
            try:
                barrier.wait(timeout=5)
                results.append(_migrator(paths).migrate())
            except BaseException as exc:  # captured for assertion in parent thread
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert not errors, errors
        assert len(results) == 2
        assert {result["phase"] for result in results} == {"COMPLETE"}
        assert sum(bool(result.get("already_complete")) for result in results) == 1


class TestTheOriginalIsKept:
    def test_the_source_is_renamed_not_deleted(self, paths):
        _write_legacy(paths, LEGACY)
        result = _migrator(paths).migrate()

        backup = Path(result["backup"])
        assert backup.exists()
        assert json.loads(backup.read_text(encoding="utf-8")) == LEGACY
        assert not paths["json"].exists()

    def test_the_backup_name_carries_a_sortable_timestamp(self, paths):
        _write_legacy(paths, LEGACY)
        result = _migrator(paths).migrate()
        assert TIMESTAMP.search(Path(result["backup"]).name), (
            f"{Path(result['backup']).name} has no yyyy-mm-dd-hhmm stamp, so "
            "successive migrations cannot be ordered."
        )


class TestRefusalsLeaveJsonAuthoritative:
    def test_a_malformed_key_stops_the_migration(self, paths):
        """A key with no separator has no session; guessing one would misfile it."""
        _write_legacy(paths, {"no-separator-anywhere": "value"})

        with pytest.raises(SessionBackendError, match="no-separator-anywhere"):
            _migrator(paths).migrate()

        assert paths["json"].exists(), "the source was retired despite the failure"
        assert not paths["db"].exists()

    def test_unreadable_json_stops_the_migration(self, paths):
        paths["json"].write_text("{ this is not json", encoding="utf-8")

        with pytest.raises(SessionBackendError):
            _migrator(paths).migrate()

        assert paths["json"].exists()

    def test_a_verification_mismatch_refuses_to_publish(self, paths, monkeypatch):
        """The database must reproduce its source exactly or it is not used."""
        _write_legacy(paths, LEGACY)
        migrator = _migrator(paths)

        monkeypatch.setattr(
            type(migrator), "_reconstruct_legacy_view",
            lambda self, store: {"plain/field": "something else"},
        )

        with pytest.raises(SessionBackendError, match="verif"):
            migrator.migrate()

        assert paths["json"].exists(), (
            "The source was retired even though the copy did not match it."
        )
        assert _migrator(paths).status()["phase"] == "FAILED"


class TestResume:
    @pytest.mark.parametrize(
        "stop_after",
        ["PREPARED", "SOURCE_RETIRED", "DATABASE_PUBLISHED"],
    )
    def test_an_interruption_at_any_phase_resumes_to_the_same_result(
        self, paths, monkeypatch, stop_after
    ):
        _write_legacy(paths, LEGACY)
        migrator = _migrator(paths)

        real_record = type(migrator)._record_phase

        def stop_at(self, phase, **details):
            real_record(self, phase, **details)
            if phase == stop_after:
                raise KeyboardInterrupt(f"interrupted after {phase}")

        monkeypatch.setattr(type(migrator), "_record_phase", stop_at)
        with pytest.raises(KeyboardInterrupt):
            migrator.migrate()
        monkeypatch.undo()

        assert _migrator(paths).status()["phase"] == stop_after

        resumed = _migrator(paths).migrate()
        assert resumed["phase"] == "COMPLETE", resumed

        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("plain", "field") == "value"
        assert store.read_field("__global__", "toggle") == {"enabled": True}
        assert Path(resumed["backup"]).exists()

    def test_status_does_not_start_a_migration(self, paths):
        """Asking what the state is must not change it."""
        _write_legacy(paths, LEGACY)
        status = _migrator(paths).status()

        assert status["phase"] == "NOT_STARTED"
        assert not paths["db"].exists()
        assert paths["json"].exists()

    @pytest.mark.parametrize("action", ["retire", "publish"])
    def test_crash_after_artifact_action_before_receipt_resumes(
        self, paths, monkeypatch, action
    ):
        _write_legacy(paths, LEGACY)
        migrator = _migrator(paths)
        real_replace = __import__("os").replace
        crashed = {"done": False}

        def replace_then_crash(source, destination):
            real_replace(source, destination)
            source = Path(source)
            destination = Path(destination)
            artifact_action = (
                action == "retire" and source == paths["json"]
            ) or (
                action == "publish" and destination == paths["db"]
            )
            if artifact_action and not crashed["done"]:
                crashed["done"] = True
                raise KeyboardInterrupt(f"crashed after {action} rename")

        monkeypatch.setattr("autorun.session_manager.os.replace", replace_then_crash)
        with pytest.raises(KeyboardInterrupt, match="crashed after"):
            migrator.migrate()
        monkeypatch.undo()

        resumed = _migrator(paths).migrate()
        assert resumed["phase"] == "COMPLETE"
        store = SQLiteStore(paths["db"])
        store.initialize()
        assert store.read_field("plain", "field") == "value"

    def test_json_writer_is_refused_after_retire_rename_before_receipt(
        self, paths, monkeypatch
    ):
        _write_legacy(paths, {"sess/value": "before"})
        stale_store = _JSONStore(
            str(paths["json"]), str(paths["json"]) + ".lock"
        )
        migrator = _migrator(paths)
        real_replace = __import__("os").replace

        def retire_then_crash(source, destination):
            real_replace(source, destination)
            if Path(source) == paths["json"]:
                raise KeyboardInterrupt("crashed after source retirement")

        monkeypatch.setattr(
            "autorun.session_manager.os.replace", retire_then_crash
        )
        with pytest.raises(KeyboardInterrupt, match="source retirement"):
            migrator.migrate()
        monkeypatch.undo()

        assert migrator.status()["phase"] == "PREPARED"
        with pytest.raises(SessionBackendError, match="SQLite.*authoritative"):
            with stale_store.session("sess") as state:
                state["value"] = "stale-writer"
        assert not paths["json"].exists()


class TestRollback:
    def test_state_can_be_exported_back_to_the_legacy_format(self, paths):
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()

        result = _migrator(paths).rollback()

        assert result["phase"] == "ROLLED_BACK", result
        restored = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert restored == LEGACY, (
            "The round trip changed the data, so rollback is not a way back."
        )

    def test_rollback_captures_changes_made_after_the_migration(self, paths):
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()

        store = SQLiteStore(paths["db"])
        store.initialize()
        with store.session("plain") as state:
            state["field"] = "changed after migration"

        _migrator(paths).rollback()

        restored = json.loads(paths["json"].read_text(encoding="utf-8"))
        assert restored["plain/field"] == "changed after migration", (
            "Rollback exported the original file rather than current state, "
            "so everything since the migration is lost."
        )

    def test_rollback_does_not_overwrite_an_existing_source(self, paths):
        _write_legacy(paths, LEGACY)
        _migrator(paths).migrate()
        paths["json"].write_text('{"someone/else": "wrote this"}', encoding="utf-8")

        with pytest.raises(SessionBackendError):
            _migrator(paths).rollback()

        assert json.loads(paths["json"].read_text(encoding="utf-8")) == \
            {"someone/else": "wrote this"}

    def test_rollback_before_a_migration_reports_rather_than_guesses(self, paths):
        with pytest.raises(SessionBackendError, match="COMPLETE"):
            _migrator(paths).rollback()


class TestLegacyStateStillReadable:
    def test_the_backup_can_be_read_by_the_old_store(self, paths):
        """The preserved artifact remains valid legacy JSON on its own path."""
        _write_legacy(paths, LEGACY)
        result = _migrator(paths).migrate()

        backup = Path(result["backup"])
        legacy_store = _JSONStore(str(backup), str(backup) + ".lock")
        with legacy_store.session("plain") as state:
            assert state["field"] == "value"
            assert state["stored_none"] is None
            assert state["with/slash"] == "field name contains the separator"
