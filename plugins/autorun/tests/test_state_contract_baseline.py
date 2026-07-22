"""Observable contract of the session-state layer, checked against both stores.

These tests are deliberately black-box: they exercise only the dict-like
context a store yields and never assert on the storage layout. Most of them
run twice, once against the JSON file store and once against the SQLite
store, so "the replacement behaves the same" is a property the suite checks
rather than a claim in a commit message.

Two behaviors are surprising enough to call out, because a naive
reimplementation gets them backwards:

  1. An exception escaping the context body discards every mutation made in
     that body. Callers that swallow their own errors keep their writes;
     callers that propagate lose them.
  2. A nested context — for the same session or a different one — joins the
     outer one. It shares the outer's uncommitted data, so it observes
     read-your-writes, and its changes are flushed by the outer context, not
     by itself. ``cache_guard._read_toggle`` relies on this to read global
     state from inside a session context without taking a second lock.

The final sections are JSON-only: they assert on the file the JSON store
writes, on its whole-state maintenance view, and on its cross-process
behavior. The SQLite equivalents live in ``test_state_field_scoped_access.py``
and ``test_session_state_sqlite.py``, where they can be stated in terms of
rows and transactions instead of bytes and file locks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun.session_manager import (  # noqa: E402
    SQLiteStore,
    SessionStateError,
    all_session_state,
    session_state,
)


@pytest.fixture
def state_dir(tmp_path):
    """An isolated state directory passed explicitly to every call.

    Passing ``state_dir=`` keeps the module-level singletons untouched, so
    these tests cannot disturb other tests or the live daemon's store.
    """
    d = tmp_path / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


@pytest.fixture(params=["json", "sqlite"])
def open_state(request, tmp_path):
    """Open a session context on whichever store is under test."""
    if request.param == "json":
        directory = tmp_path / "sessions"
        directory.mkdir(parents=True, exist_ok=True)

        def _open(session_id):
            return session_state(session_id, state_dir=str(directory))
    else:
        store = SQLiteStore(tmp_path / "state" / "daemon_state.sqlite3")
        store.initialize()

        def _open(session_id):
            return store.session(session_id)

    _open.backend = request.param
    return _open


# ── Missing versus stored None ───────────────────────────────────────────────

class TestMissingVersusStoredNone:
    def test_absent_field_is_distinguishable_from_a_field_stored_as_none(self, open_state):
        with open_state("s") as state:
            state["explicit"] = None

        with open_state("s") as state:
            assert "explicit" in state
            assert state["explicit"] is None
            assert state.get("explicit", "fallback") is None, (
                "A stored None must not be reported as missing; callers use the "
                "difference to tell 'never set' from 'deliberately cleared'."
            )
            assert "absent" not in state
            assert state.get("absent", "fallback") == "fallback"

    def test_reading_an_absent_field_by_subscript_raises_keyerror(self, open_state):
        with open_state("s") as state:
            with pytest.raises(KeyError):
                state["never-set"]

    def test_deleting_a_field_makes_it_missing_again(self, open_state):
        with open_state("s") as state:
            state["gone"] = 1
        with open_state("s") as state:
            del state["gone"]
        with open_state("s") as state:
            assert "gone" not in state
            assert state.get("gone", "fallback") == "fallback"

    def test_deleting_an_absent_field_raises_keyerror(self, open_state):
        with open_state("s") as state:
            with pytest.raises(KeyError):
                del state["never-set"]


# ── Identifier shapes that must survive a round trip ─────────────────────────

class TestIdentifierRoundTrip:
    @pytest.mark.parametrize(
        "field",
        [
            "plain",
            "with/slash",
            "with/several/slashes",
            "unicode-ключ-中文",
            "emoji-\U0001f600",
            "with space",
            "with.dot",
            "-leading-dash",
        ],
    )
    def test_field_names_round_trip(self, open_state, field):
        with open_state("s") as state:
            state[field] = {"v": 1}
        with open_state("s") as state:
            assert state[field] == {"v": 1}
            assert field in state.keys()

    @pytest.mark.parametrize(
        "session_id",
        [
            "plain-session",
            "__global__",
            "__task_lifecycle__abc-123",
            "session/with/slash",
            "unicode-сессия",
        ],
    )
    def test_session_ids_round_trip_and_stay_separate(self, open_state, session_id):
        with open_state(session_id) as state:
            state["marker"] = session_id
        with open_state("other") as state:
            state["marker"] = "other"
        with open_state(session_id) as state:
            assert state["marker"] == session_id

    @pytest.mark.parametrize("bad", ["", "   ", None, 5])
    def test_empty_or_non_string_session_ids_are_rejected(self, open_state, bad):
        with pytest.raises(SessionStateError):
            with open_state(bad):
                pass


# ── Value shapes ─────────────────────────────────────────────────────────────

class TestValueRoundTrip:
    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            0,
            -1,
            3.5,
            "",
            "text",
            [],
            [1, "two", None],
            {},
            {"nested": {"deep": [1, {"deeper": True}]}},
        ],
    )
    def test_json_compatible_values_round_trip_unchanged(self, open_state, value):
        with open_state("s") as state:
            state["v"] = value
        with open_state("s") as state:
            assert state["v"] == value


# ── Dict-like surface ────────────────────────────────────────────────────────

class TestDictSurface:
    def test_keys_values_items_len_and_iteration_are_scoped_to_one_session(self, open_state):
        with open_state("mine") as state:
            state.update({"a": 1, "b": 2}, c=3)
        with open_state("theirs") as state:
            state["z"] = 99

        with open_state("mine") as state:
            assert sorted(state.keys()) == ["a", "b", "c"]
            assert sorted(state.values()) == [1, 2, 3]
            assert sorted(state.items()) == [("a", 1), ("b", 2), ("c", 3)]
            assert len(state) == 3
            assert sorted(iter(state)) == ["a", "b", "c"]

    def test_clear_removes_only_this_sessions_fields(self, open_state):
        with open_state("mine") as state:
            state["a"] = 1
        with open_state("theirs") as state:
            state["b"] = 2

        with open_state("mine") as state:
            state.clear()

        with open_state("mine") as state:
            assert len(state) == 0
        with open_state("theirs") as state:
            assert state["b"] == 2

    def test_sync_and_close_are_accepted_and_do_not_end_the_context(self, open_state):
        """Legacy shelve-era callers still call these; they must stay harmless."""
        with open_state("s") as state:
            state["a"] = 1
            state.sync()
            state.close()
            state["b"] = 2
        with open_state("s") as state:
            assert state["a"] == 1 and state["b"] == 2


# ── Commit and discard semantics ─────────────────────────────────────────────

class TestCommitSemantics:
    def test_an_exception_escaping_the_body_discards_that_bodys_writes(self, open_state):
        with open_state("s") as state:
            state["committed"] = "before"

        with pytest.raises(RuntimeError):
            with open_state("s") as state:
                state["committed"] = "after"
                state["added"] = "new"
                raise RuntimeError("caller failed mid-update")

        with open_state("s") as state:
            assert state["committed"] == "before", (
                "A failed update must not leave a partially applied field."
            )
            assert "added" not in state

    def test_a_baseexception_escaping_the_body_also_discards_writes(self, open_state):
        with pytest.raises(KeyboardInterrupt):
            with open_state("s") as state:
                state["x"] = 1
                raise KeyboardInterrupt

        with open_state("s") as state:
            assert "x" not in state

    def test_the_store_is_usable_again_after_a_failed_body(self, open_state):
        with pytest.raises(RuntimeError):
            with open_state("s"):
                raise RuntimeError("boom")

        with open_state("s") as state:
            state["reacquired"] = True
        with open_state("s") as state:
            assert state["reacquired"] is True


# ── Nested contexts ──────────────────────────────────────────────────────────

class TestNestedContexts:
    def test_a_nested_same_session_context_sees_the_outer_uncommitted_write(self, open_state):
        with open_state("s") as outer:
            outer["x"] = "outer"
            with open_state("s") as inner:
                assert inner["x"] == "outer"
                inner["y"] = "inner"
            assert outer["y"] == "inner"

        with open_state("s") as state:
            assert state["x"] == "outer" and state["y"] == "inner"

    def test_a_nested_different_session_context_shares_the_same_acquisition(self, open_state):
        """The global-fallback read in ``cache_guard`` depends on this.

        Reading ``__global__`` from inside a per-session context must not
        deadlock and must not require a second acquisition.
        """
        with open_state("__global__") as g:
            g["toggle"] = {"enabled": True}

        with open_state("sess") as state:
            state["own"] = 1
            with open_state("__global__") as g:
                assert g["toggle"] == {"enabled": True}
                g["written_from_nested"] = True

        with open_state("__global__") as g:
            assert g["written_from_nested"] is True, (
                "A nested different-session write must be flushed by the "
                "outer context."
            )
        with open_state("sess") as state:
            assert state["own"] == 1

    def test_an_inner_failure_caught_by_the_outer_still_commits_with_the_outer(self, open_state):
        with open_state("s") as outer:
            outer["outer_field"] = 1
            try:
                with open_state("s") as inner:
                    inner["inner_field"] = 2
                    raise RuntimeError("inner failed")
            except RuntimeError:
                pass

        with open_state("s") as state:
            assert state["outer_field"] == 1
            assert state["inner_field"] == 2, (
                "The inner context does not flush on its own, so its writes "
                "live or die with the outer context that caught the failure."
            )


# =============================================================================
# JSON store specifics
# =============================================================================
#
# Below here the assertions are about the file the JSON store writes and the
# maintenance view it exposes. They stay JSON-only because their SQLite
# counterparts are stated in terms of rows rather than bytes; see
# test_state_field_scoped_access.py.


class TestJsonStoreFileBehavior:
    def test_a_read_only_context_does_not_rewrite_storage(self, state_dir):
        with session_state("s", state_dir=state_dir) as state:
            state["a"] = 1

        path = Path(state_dir) / "daemon_state.json"
        before = (path.stat().st_mtime_ns, path.read_bytes())

        with session_state("s", state_dir=state_dir) as state:
            assert state["a"] == 1
            assert state.get("missing") is None

        after = (path.stat().st_mtime_ns, path.read_bytes())
        assert before == after, (
            "A context that only reads must not republish stored state."
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "The JSON store marks itself dirty only on __setitem__, so a "
            "nested value mutated through __getitem__ reaches the in-memory "
            "dict but is never written back unless some other field in the "
            "same context happens to trigger a save. The SQLite store gets "
            "this right; see test_state_field_scoped_access.py."
        ),
    )
    def test_a_value_mutated_in_place_is_persisted(self, state_dir):
        with session_state("s", state_dir=state_dir) as state:
            state["metadata"] = {"hits": 0}

        with session_state("s", state_dir=state_dir) as state:
            state["metadata"]["hits"] += 1

        with session_state("s", state_dir=state_dir) as state:
            assert state["metadata"]["hits"] == 1, (
                "An in-place mutation of a nested value was not persisted."
            )


class TestAllSessionState:
    def test_the_maintenance_view_exposes_every_session(self, state_dir):
        with session_state("one", state_dir=state_dir) as state:
            state["a"] = 1
        with session_state("two", state_dir=state_dir) as state:
            state["b"] = 2

        with all_session_state(state_dir=state_dir) as everything:
            found = _sessions_present(everything)
        assert {"one", "two"} <= found

    def test_the_maintenance_view_can_remove_a_whole_session(self, state_dir):
        with session_state("doomed", state_dir=state_dir) as state:
            state["a"] = 1
        with session_state("kept", state_dir=state_dir) as state:
            state["a"] = 1

        with all_session_state(state_dir=state_dir, write=True) as everything:
            for key in [k for k in list(everything) if k.startswith("doomed/")]:
                del everything[key]

        with session_state("doomed", state_dir=state_dir) as state:
            assert len(state) == 0
        with session_state("kept", state_dir=state_dir) as state:
            assert state["a"] == 1


def _sessions_present(raw_state: dict) -> set:
    """Session IDs visible in the maintenance view.

    The view is currently a flat ``session/field`` mapping. Only the first
    separator is meaningful, because field names may themselves contain ``/``.
    """
    return {key.split("/", 1)[0] for key in raw_state if "/" in key}


_CHILD_SCRIPT = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, sys.argv[1])
    from autorun.session_manager import session_state

    state_dir, session_id, field, hold = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
    with session_state(session_id, state_dir=state_dir) as state:
        current = state.get("counter", 0)
        time.sleep(hold)
        state["counter"] = current + 1
        state[field] = True
    print("done")
    """
)


def _run_child(state_dir, session_id, field, hold=0.0):
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD_SCRIPT, str(SRC_DIR), state_dir,
         session_id, field, str(hold)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(SRC_DIR)},
    )


@pytest.mark.subprocess
@pytest.mark.serial
class TestCrossProcess:
    def test_concurrent_processes_do_not_lose_a_read_modify_write(self, state_dir):
        """Independent processes must serialize, not interleave.

        Each child reads the counter, sleeps inside its context, then writes
        ``counter + 1``. Without cross-process exclusion the later writer
        overwrites the earlier one and the total comes out short.
        """
        with session_state("shared", state_dir=state_dir) as state:
            state["counter"] = 0

        children = [
            _run_child(state_dir, "shared", f"child_{i}", hold=0.2)
            for i in range(3)
        ]
        for child in children:
            out, err = child.communicate(timeout=60)
            assert child.returncode == 0, f"child failed: {err}"

        with session_state("shared", state_dir=state_dir) as state:
            assert state["counter"] == 3, (
                "A concurrent read-modify-write was lost across processes."
            )
            for i in range(3):
                assert state[f"child_{i}"] is True

    def test_a_write_from_another_process_is_visible_immediately(self, state_dir):
        child = _run_child(state_dir, "fresh", "child_0")
        out, err = child.communicate(timeout=60)
        assert child.returncode == 0, f"child failed: {err}"

        with session_state("fresh", state_dir=state_dir) as state:
            assert state["child_0"] is True, (
                "A durable write committed by another process must be visible "
                "to the next reader without restarting it."
            )
        raw = Path(state_dir) / "daemon_state.json"
        assert json.loads(raw.read_text(encoding="utf-8")), "nothing was persisted"
