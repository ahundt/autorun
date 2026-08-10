"""Reads touch one field; writes stage one row; neither drags the rest along.

The point of the storage change is that the cost of an operation stops
depending on how much unrelated state exists. Timing cannot prove that
reliably on a shared machine, so the tests here prove it structurally
instead: a session is filled with rows whose stored values are deliberately
undecodable, and a field-scoped read of the one valid row is required to
succeed. It can only succeed if the other rows were never fetched or decoded.

The same idea covers writes. A context that changes one field must leave
every other row's stored bytes and timestamp untouched, and a context that
changes nothing must not write at all.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun.session_manager import (  # noqa: E402
    MISSING,
    SessionBackendError,
    SQLiteStore,
)

UNDECODABLE = "this is not json"
DECOY_FIELDS = 2000


@pytest.fixture
def store(tmp_path):
    store = SQLiteStore(tmp_path / "state" / "daemon_state.sqlite3")
    store.initialize()
    return store


@pytest.fixture
def crowded_session(store):
    """One readable field among many rows that would fail if decoded.

    Written through raw SQL because the store's own writer would refuse to
    store a value it could not encode.
    """
    now = time.time()
    with store.operation_scope(30.0) as owner:
        with store.write_transaction(owner) as conn:
            conn.execute(
                "INSERT INTO sessions (session, namespace, last_modified) VALUES (?,?,?)",
                ("crowded", "session", now),
            )
            conn.execute(
                "INSERT INTO state (session, field, value_json, updated_at) VALUES (?,?,?,?)",
                ("crowded", "readable", json.dumps({"ok": True}), now),
            )
            conn.executemany(
                "INSERT INTO state (session, field, value_json, updated_at) VALUES (?,?,?,?)",
                [("crowded", f"decoy_{i}", UNDECODABLE, now) for i in range(DECOY_FIELDS)],
            )
    return "crowded"


# ── Reads are scoped to what was asked for ───────────────────────────────────

class TestReadScope:
    def test_reading_one_field_does_not_decode_the_others(self, store, crowded_session):
        assert store.read_field(crowded_session, "readable") == {"ok": True}

    def test_reading_a_named_subset_does_not_decode_the_others(self, store, crowded_session):
        assert store.read_fields(crowded_session, ["readable"]) == {"readable": {"ok": True}}

    def test_asking_for_everything_is_the_explicit_bulk_path(self, store, crowded_session):
        """The whole-session read still exists, and it really does read it all.

        This is the assertion that makes the two tests above meaningful: the
        decoys are reachable, so their absence from a scoped read is scoping
        and not an empty table.
        """
        with pytest.raises(SessionBackendError) as raised:
            store.read_fields(crowded_session)
        assert "decoy_" in str(raised.value), (
            "A decode failure must name the field that failed, or an operator "
            f"cannot find it among {DECOY_FIELDS} rows. Got: {raised.value}"
        )

    def test_a_missing_field_is_distinguishable_from_a_stored_none(self, store):
        with store.session("s") as state:
            state["explicit"] = None

        assert store.read_field("s", "explicit") is None
        assert store.read_field("s", "explicit", default="fallback") is None
        assert store.read_field("s", "absent") is MISSING
        assert store.read_field("s", "absent", default="fallback") == "fallback"

    def test_reading_a_session_that_was_never_written_returns_nothing(self, store):
        assert store.read_fields("never-seen", ["a"]) == {}
        assert store.read_field("never-seen", "a") is MISSING

    def test_a_read_returns_detached_data(self, store):
        with store.session("s") as state:
            state["nested"] = {"a": [1, 2]}

        first = store.read_field("s", "nested")
        first["a"].append(3)

        assert store.read_field("s", "nested") == {"a": [1, 2]}, (
            "Mutating what a read returned changed stored state, so the "
            "caller was handed a live reference into the store."
        )


# ── Reads do not take the writer slot ────────────────────────────────────────

class TestReadsDoNotBlockOnWriters:
    def test_a_read_proceeds_while_another_connection_holds_the_writer(self, store):
        with store.session("s") as state:
            state["value"] = 1

        holder_ready = threading.Event()
        release = threading.Event()
        errors = []

        def hold_writer():
            conn = sqlite3.connect(store.db_path, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT OR REPLACE INTO sessions (session, namespace, last_modified) "
                    "VALUES (?,?,?)",
                    ("other", "session", time.time()),
                )
                holder_ready.set()
                release.wait(timeout=10)
                conn.execute("ROLLBACK")
            except Exception as exc:  # noqa: BLE001 - surfaced by the assertion
                errors.append(exc)
            finally:
                conn.close()

        thread = threading.Thread(target=hold_writer)
        thread.start()
        try:
            assert holder_ready.wait(timeout=10)
            # A short budget: if the read needed the writer slot it would run
            # out of it rather than return.
            assert store.read_field("s", "value", timeout=0.2) == 1
        finally:
            release.set()
            thread.join(timeout=10)

        assert not errors, errors

    def test_a_read_does_not_modify_stored_state(self, store):
        with store.session("s") as state:
            state["value"] = 1

        before = _session_row(store, "s")
        store.read_field("s", "value")
        store.read_fields("s", ["value"])

        assert _session_row(store, "s") == before, (
            "A read advanced the session's last-modified time, which would "
            "make retention treat idle sessions as active forever."
        )


def _session_row(store, session_id):
    with store.operation_scope(30.0) as owner:
        return owner.connection.execute(
            "SELECT namespace, last_modified FROM sessions WHERE session = ?",
            (session_id,),
        ).fetchone()


def _state_row(store, session_id, field):
    with store.operation_scope(30.0) as owner:
        return owner.connection.execute(
            "SELECT value_json, updated_at FROM state WHERE session = ? AND field = ?",
            (session_id, field),
        ).fetchone()


# ── Writes stage only what changed ───────────────────────────────────────────

class TestWriteScope:
    def test_changing_one_field_leaves_the_others_byte_identical(self, store):
        with store.session("s") as state:
            state["a"] = {"v": 1}
            state["b"] = {"v": 2}

        before_b = _state_row(store, "s", "b")
        time.sleep(0.01)

        with store.session("s") as state:
            state["a"] = {"v": 99}

        assert _state_row(store, "s", "b") == before_b, (
            "An unrelated field was rewritten, so write cost still scales "
            "with session size."
        )
        assert store.read_field("s", "a") == {"v": 99}

    def test_a_context_that_changes_nothing_writes_nothing(self, store):
        with store.session("s") as state:
            state["a"] = 1

        before_session = _session_row(store, "s")
        before_field = _state_row(store, "s", "a")
        time.sleep(0.01)

        with store.session("s") as state:
            assert state["a"] == 1
            state["a"] = 1  # assigning the same value is still no change

        assert _session_row(store, "s") == before_session, (
            "A no-op context advanced last_modified, so an idle session would "
            "never become eligible for retention."
        )
        assert _state_row(store, "s", "a") == before_field

    def test_an_in_place_mutation_of_a_nested_value_is_persisted(self, store):
        """The failure mode a shallow-copy diff cannot see.

        Comparing the buffer against a shallow copy compares the nested object
        with itself, reports no change, and drops the write.
        """
        with store.session("s") as state:
            state["metadata"] = {"hits": 0, "nested": {"deep": []}}

        with store.session("s") as state:
            state["metadata"]["hits"] += 1
            state["metadata"]["nested"]["deep"].append("added")

        stored = store.read_field("s", "metadata")
        assert stored == {"hits": 1, "nested": {"deep": ["added"]}}

    def test_deleting_a_field_removes_only_that_row(self, store):
        with store.session("s") as state:
            state["keep"] = 1
            state["drop"] = 2

        with store.session("s") as state:
            del state["drop"]

        assert store.read_field("s", "drop") is MISSING
        assert store.read_field("s", "keep") == 1

    def test_a_durable_write_advances_the_sessions_last_modified_time(self, store):
        with store.session("s") as state:
            state["a"] = 1
        first = _session_row(store, "s")[1]
        time.sleep(0.01)

        with store.session("s") as state:
            state["a"] = 2
        assert _session_row(store, "s")[1] > first

    def test_the_session_row_records_the_namespace_retention_will_use(self, store):
        for session_id, namespace in [
            ("__global__", "global"),
            ("__task_lifecycle__abc", "task_lifecycle"),
            ("ordinary-session", "session"),
        ]:
            with store.session(session_id) as state:
                state["a"] = 1
            assert _session_row(store, session_id)[0] == namespace, (
                f"{session_id} was classified as "
                f"{_session_row(store, session_id)[0]!r}; retention policy is "
                "keyed on this and an unknown namespace must not be guessed."
            )

    def test_a_value_that_cannot_be_encoded_is_rejected_before_anything_is_written(self, store):
        with store.session("s") as state:
            state["good"] = 1

        with pytest.raises(SessionBackendError, match="bad"):
            with store.session("s") as state:
                state["bad"] = {1, 2, 3}  # a set has no JSON representation

        assert store.read_field("s", "good") == 1
        assert store.read_field("s", "bad") is MISSING


# ── Nesting and read-your-writes ─────────────────────────────────────────────

class TestNestedAccess:
    def test_a_read_inside_an_open_write_sees_the_uncommitted_change(self, store):
        with store.session("s") as state:
            state["v"] = "original"

        with store.session("s") as state:
            state["v"] = "pending"
            assert store.read_field("s", "v") == "pending", (
                "A read taken inside the transaction that changed the value "
                "must see the change, or a helper called mid-update silently "
                "acts on stale data."
            )

    def test_a_read_inside_an_open_write_sees_a_pending_deletion(self, store):
        with store.session("s") as state:
            state["v"] = "original"

        with store.session("s") as state:
            del state["v"]
            assert store.read_field("s", "v") is MISSING

    def test_a_read_inside_an_open_write_sees_a_pending_none(self, store):
        with store.session("s") as state:
            state["v"] = "original"

        with store.session("s") as state:
            state["v"] = None
            assert store.read_field("s", "v") is None

    def test_a_nested_context_for_another_session_commits_with_the_outer(self, store):
        with store.session("outer") as outer:
            outer["a"] = 1
            with store.session("__global__") as inner:
                inner["b"] = 2

        assert store.read_field("outer", "a") == 1
        assert store.read_field("__global__", "b") == 2

    def test_a_failure_after_a_nested_write_discards_both_sessions(self, store):
        with pytest.raises(RuntimeError):
            with store.session("outer") as outer:
                outer["a"] = 1
                with store.session("__global__") as inner:
                    inner["b"] = 2
                raise RuntimeError("caller failed")

        assert store.read_field("outer", "a") is MISSING
        assert store.read_field("__global__", "b") is MISSING, (
            "The nested session was committed independently, so a later "
            "failure left the two out of step."
        )

    def test_a_nested_context_for_the_same_session_shares_one_buffer(self, store):
        with store.session("s") as outer:
            outer["a"] = 1
            with store.session("s") as inner:
                assert inner["a"] == 1
                inner["b"] = 2
            assert outer["b"] == 2

        assert store.read_fields("s", ["a", "b"]) == {"a": 1, "b": 2}

    def test_a_nested_context_reopened_after_closing_does_not_rewrite_its_fields(self, store):
        """Reopening within one operation must not restage unchanged rows.

        The buffer adopts what it wrote as its new starting point; without
        that, the second context would see its own writes as changes and
        rewrite them, turning one update into N.
        """
        with store.session("outer") as outer:
            outer["a"] = 1
            with store.session("inner") as first:
                first["b"] = 2
            row_after_first = _state_row(store, "inner", "b")
            with store.session("inner") as second:
                assert second["b"] == 2

        assert _state_row(store, "inner", "b") == row_after_first
        assert store.read_field("inner", "b") == 2


# ── Values and identifiers at the edges ──────────────────────────────────────

class TestBoundaryValues:
    @pytest.mark.parametrize(
        "field",
        [
            "a" * 4096,
            "control\x01chars",
            "tab\tand\nnewline",
            "quote\"and'apostrophe",
            "percent%and_underscore",  # LIKE wildcards, if anyone ever uses LIKE
            "semicolon; DROP TABLE state; --",
            "​ zero width",
            "🙂",
        ],
    )
    def test_awkward_field_names_round_trip(self, store, field):
        """Identifiers are bound as data, so none of these are special."""
        with store.session("s") as state:
            state[field] = "value"
        assert store.read_field("s", field) == "value"

    @pytest.mark.parametrize(
        "session_id",
        [
            "semicolon; DROP TABLE sessions; --",
            "percent%wild",
            "newline\nin\tid",
            "🙂-session",
        ],
    )
    def test_awkward_session_ids_round_trip_and_stay_separate(self, store, session_id):
        with store.session(session_id) as state:
            state["marker"] = session_id
        with store.session("neighbour") as state:
            state["marker"] = "neighbour"

        assert store.read_field(session_id, "marker") == session_id
        assert store.read_field("neighbour", "marker") == "neighbour"

    def test_a_null_byte_survives_in_a_field_name_and_a_value(self, store):
        """A NUL truncates C strings; JSON escapes it, so it must survive."""
        with store.session("s") as state:
            state["with\x00nul"] = "value\x00inside"

        assert store.read_field("s", "with\x00nul") == "value\x00inside"

    def test_a_large_value_round_trips(self, store):
        payload = {"blob": "x" * (2 * 1024 * 1024)}
        with store.session("s") as state:
            state["big"] = payload
        assert store.read_field("s", "big") == payload

    def test_a_deeply_nested_value_names_the_field_it_could_not_store(
        self, store, monkeypatch
    ):
        """A bare RecursionError from inside json says nothing about the cause.

        The RecursionError is injected rather than provoked by nesting. The C
        json encoder guards on remaining C stack, which neither
        sys.setrecursionlimit nor a fixed nesting depth controls: the previous
        20000-deep value stopped raising on CPython 3.14, and any replacement
        constant would only move the version where it stops raising. What has
        to hold is the wrapping — that a RecursionError out of json is reported
        as a state error naming the field, and that the field stays unstored.
        """
        value = {"nested": "too deep to encode"}
        real_dumps = json.dumps

        def dumps(obj, **kwargs):
            if obj is value:
                raise RecursionError("maximum recursion depth exceeded")
            return real_dumps(obj, **kwargs)

        monkeypatch.setattr(json, "dumps", dumps)

        with pytest.raises(SessionBackendError, match="deep"):
            with store.session("s") as state:
                state["deep"] = value

        monkeypatch.undo()
        assert store.read_field("s", "deep") is MISSING

    def test_an_unpaired_surrogate_is_rejected_and_the_field_is_named(self, store):
        """These have no UTF-8 form, so storing one would corrupt the row.

        The driver's own message says only that encoding failed, which is not
        enough to find the offending value in a session of many fields.
        """
        with pytest.raises(SessionBackendError, match="bad"):
            with store.session("s") as state:
                state["bad"] = "\ud800"

        assert store.read_field("s", "bad") is MISSING

    def test_a_rejected_write_leaves_earlier_fields_in_the_same_context_untouched(
        self, store
    ):
        """One unstorable field must not take the whole context down with it."""
        with store.session("s") as state:
            state["established"] = "before"

        with pytest.raises(SessionBackendError):
            with store.session("s") as state:
                state["established"] = "after"
                state["bad"] = "\ud800"

        assert store.read_field("s", "established") == "before", (
            "A rejected value left the context half-applied."
        )

    def test_a_float_that_json_cannot_represent_is_handled_consistently(self, store):
        """NaN and infinity have no JSON form; whatever happens must round trip."""
        for value in (float("inf"), float("-inf")):
            with store.session("s") as state:
                state["number"] = value
            assert store.read_field("s", "number") == value


class TestClockAnomalies:
    def test_a_backward_system_clock_does_not_make_state_look_older(self, store, monkeypatch):
        """Retention reads last_modified; a clock step must not age live state.

        A session written now and rewritten after the clock jumps backwards is
        still in use. Recording the earlier stamp would make retention treat
        it as abandoned.
        """
        from autorun import session_manager as sm

        with store.session("s") as state:
            state["a"] = 1
        first = _session_row(store, "s")[1]

        monkeypatch.setattr(sm.time, "time", lambda: first - 3600.0)
        with store.session("s") as state:
            state["a"] = 2

        assert _session_row(store, "s")[1] >= first, (
            "The session's modification time moved backwards with the clock, "
            "so retention would see active state as an hour stale."
        )
        assert store.read_field("s", "a") == 2, "the write itself must still land"
