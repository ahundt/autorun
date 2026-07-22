"""One setting turns on the row store, and brings existing state with it.

The row store is worth nothing if reaching it takes a code change. This is
the switch: set the backend to sqlite and the next process converts whatever
JSON state exists and serves from the database, through the same
``session_state()`` every caller already uses.

Conversion is part of flipping the switch rather than a separate step an
operator has to remember. A switch that silently starts from empty state
would look like it worked and lose every policy, task, and claim in the file
it ignored.

The default stays JSON. Changing backends is a deliberate act, and until the
new one has run against real traffic it should not become the default by
omission.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
from autorun.session_manager import (  # noqa: E402
    SQLiteStore,
    _JSONStore,
    all_session_state,
    session_state,
)

LEGACY = {
    "sess-a/file_policy": "SEARCH",
    "sess-a/stored_none": None,
    "sess-a/nested": {"deep": [1, {"deeper": True}]},
    "__global__/toggle": {"enabled": True},
    "__task_lifecycle__x/schema_version": 3,
}


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@pytest.fixture
def use_sqlite(monkeypatch):
    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    yield
    sm._reset_for_testing()


def _seed_legacy(state_dir):
    (state_dir / "daemon_state.json").write_text(json.dumps(LEGACY), encoding="utf-8")


class TestTheDefaultIsUnchanged:
    def test_without_the_setting_the_json_store_is_used(self, state_dir):
        assert isinstance(sm._get_store(), _JSONStore)

    def test_an_unknown_backend_name_is_refused_rather_than_guessed(
        self, state_dir, monkeypatch
    ):
        monkeypatch.setitem(sm._CONFIG, "state_backend", "postgres")
        sm._reset_for_testing()
        with pytest.raises(sm.SessionBackendError, match="postgres"):
            sm._get_store()


class TestFlippingTheSetting:
    def test_the_row_store_is_used(self, state_dir, use_sqlite):
        assert isinstance(sm._get_store(), SQLiteStore)

    def test_existing_state_comes_across(self, state_dir, use_sqlite):
        _seed_legacy(state_dir)

        with session_state("sess-a") as state:
            assert state["file_policy"] == "SEARCH"
            assert state["stored_none"] is None
            assert state["nested"] == {"deep": [1, {"deeper": True}]}
        with session_state("__global__") as state:
            assert state["toggle"] == {"enabled": True}

    def test_conversion_happens_without_a_separate_command(
        self, state_dir, use_sqlite
    ):
        """A switch that quietly started empty would look like it worked."""
        _seed_legacy(state_dir)
        with session_state("sess-a") as state:
            assert len(state) == 3

        assert (state_dir / "daemon_state.sqlite3").exists()

    def test_the_original_file_is_kept(self, state_dir, use_sqlite):
        _seed_legacy(state_dir)
        with session_state("sess-a"):
            pass

        backups = list(state_dir.glob("daemon_state.json.migrated.*"))
        assert backups, "the original state file was not preserved"
        assert json.loads(backups[0].read_text(encoding="utf-8")) == LEGACY

    def test_writes_go_to_the_row_store(self, state_dir, use_sqlite):
        _seed_legacy(state_dir)
        with session_state("sess-a") as state:
            state["file_policy"] = "ALLOW"

        sm._reset_for_testing()
        with session_state("sess-a") as state:
            assert state["file_policy"] == "ALLOW"

    def test_starting_with_no_previous_state_works(self, state_dir, use_sqlite):
        with session_state("brand-new") as state:
            state["a"] = 1
        sm._reset_for_testing()
        with session_state("brand-new") as state:
            assert state["a"] == 1

    def test_converting_twice_does_not_repeat_itself(self, state_dir, use_sqlite):
        _seed_legacy(state_dir)
        with session_state("sess-a") as state:
            state["file_policy"] = "ALLOW"

        sm._reset_for_testing()
        with session_state("sess-a") as state:
            assert state["file_policy"] == "ALLOW", (
                "The conversion ran again and overwrote a value written after "
                "it, so every restart would roll state back to the import."
            )


class TestTheStoreBoundaryIsHonored:
    """Everything SessionStateManager asks of a store has to be there."""

    def test_the_state_directory_is_reported(self, state_dir, use_sqlite):
        assert sm.get_session_manager().state_dir == state_dir

    def test_the_maintenance_view_works(self, state_dir, use_sqlite):
        with session_state("one") as state:
            state["a"] = 1
        with session_state("two") as state:
            state["b"] = 2

        with all_session_state() as everything:
            assert everything.get("one/a") == 1
            assert everything.get("two/b") == 2

    def test_the_maintenance_view_can_remove_a_session(self, state_dir, use_sqlite):
        with session_state("doomed") as state:
            state["a"] = 1
        with session_state("kept") as state:
            state["a"] = 1

        with all_session_state(write=True) as everything:
            for key in [k for k in list(everything) if k.startswith("doomed/")]:
                del everything[key]

        with session_state("doomed") as state:
            assert len(state) == 0
        with session_state("kept") as state:
            assert state["a"] == 1

    def test_clearing_a_test_session_works(self, state_dir, use_sqlite):
        with session_state("scratch") as state:
            state["a"] = 1
        sm.clear_test_session_state("scratch")
        with session_state("scratch") as state:
            assert len(state) == 0


_CHILD = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from autorun import session_manager as sm
    sm._CONFIG["state_backend"] = "sqlite"
    from autorun.session_manager import session_state

    with session_state(sys.argv[2]) as state:
        state["from_child"] = True
        print(state.get("file_policy"))
    """
)


@pytest.mark.subprocess
@pytest.mark.serial
class TestAcrossProcesses:
    def test_a_second_process_sees_the_converted_state(self, state_dir, use_sqlite):
        import os
        import subprocess

        _seed_legacy(state_dir)
        with session_state("sess-a"):
            pass  # convert here

        completed = subprocess.run(
            [sys.executable, "-c", _CHILD, str(SRC_DIR), "sess-a"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "AUTORUN_TEST_STATE_DIR": str(state_dir)},
        )
        assert completed.returncode == 0, completed.stderr
        assert "SEARCH" in completed.stdout

        with session_state("sess-a") as state:
            assert state["from_child"] is True


class TestSwitchingBack:
    """The dangerous direction, and the path provided for it."""

    def test_selecting_json_after_a_conversion_is_refused(
        self, state_dir, monkeypatch
    ):
        """Silently serving empty state is the failure worth preventing.

        The conversion renames the original file, so a JSON store would open
        a path that is not there, find nothing, and report nothing wrong.
        """
        _seed_legacy(state_dir)
        monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
        sm._reset_for_testing()
        with session_state("sess-a"):
            pass

        monkeypatch.setitem(sm._CONFIG, "state_backend", "json")
        sm._reset_for_testing()

        with pytest.raises(sm.SessionBackendError) as raised:
            sm._get_store()

        message = str(raised.value)
        assert "--state-rollback" in message, (
            f"The refusal does not say how to go back. Got: {message}"
        )
        assert "empty state" in message

    def test_rollback_then_json_serves_the_converted_state(
        self, state_dir, monkeypatch
    ):
        """The path out has to actually work, not just be named."""
        _seed_legacy(state_dir)
        monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
        sm._reset_for_testing()
        with session_state("sess-a") as state:
            state["file_policy"] = "ALLOW"

        sm.StateMigrator(
            state_dir / "daemon_state.json",
            state_dir / "daemon_state.sqlite3",
            state_dir / "daemon_state.migration.json",
        ).rollback()

        monkeypatch.setitem(sm._CONFIG, "state_backend", "json")
        sm._reset_for_testing()
        with session_state("sess-a") as state:
            assert state["file_policy"] == "ALLOW", (
                "Rollback did not carry back the value written after the "
                "conversion, so going back loses work."
            )

    def test_json_still_opens_normally_when_nothing_was_converted(self, state_dir):
        _seed_legacy(state_dir)
        with session_state("sess-a") as state:
            assert state["file_policy"] == "SEARCH"


class TestConversionFailure:
    def test_a_failed_conversion_refuses_to_open_and_says_why(
        self, state_dir, monkeypatch
    ):
        """Falling back to JSON would leave two writable stores.

        Which one a given write reached would then be unknowable, so this
        fails instead — recoverable, unlike split state.
        """
        (state_dir / "daemon_state.json").write_text(
            json.dumps({"no-separator-anywhere": "value"}), encoding="utf-8"
        )
        monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
        sm._reset_for_testing()

        with pytest.raises(sm.SessionBackendError) as raised:
            sm._get_store()

        message = str(raised.value)
        assert "--state-status" in message, (
            f"The failure does not say how to inspect it. Got: {message}"
        )
        assert "still authoritative" in message

    def test_the_original_file_survives_a_failed_conversion(
        self, state_dir, monkeypatch
    ):
        (state_dir / "daemon_state.json").write_text(
            json.dumps({"no-separator-anywhere": "value"}), encoding="utf-8"
        )
        monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
        sm._reset_for_testing()
        with pytest.raises(sm.SessionBackendError):
            sm._get_store()

        assert (state_dir / "daemon_state.json").exists()
