"""What the AI is allowed to write inside a marker.

Markers are the only way to change task state that works on every harness,
because they never touch a harness's tool schema. That makes their grammar a
real interface, and it was narrower than the situations it has to cover:
one marker per task, one task per line, no way to say where the work went.

Three tasks handed to a subagent meant three near-identical lines. This
widens the grammar in the one place both markers are parsed:

    AUTORUN_TASKS_CLEAR_STALE_TASK(1, 2, 3)
    AUTORUN_TASK_DELEGATED(77, 78, agent_session_id=agent-abc)

Bare arguments are task ids. ``agent_session_id=`` names where the work went.
The legacy ``session=`` spelling remains accepted. Naming it rather than
relying on position keeps ``(77, 78)`` unambiguous — two tasks, not one task
handed to a session called "78".

The single-argument form is what the guidance text prints today, so every
test here also checks it still means exactly what it did.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import session_manager as sm  # noqa: E402
from autorun.core import EventContext, ThreadSafeDB  # noqa: E402
from autorun.task_lifecycle import (  # noqa: E402
    TaskLifecycle,
    TaskLifecycleConfig,
    extract_delegate_markers,
    extract_delegate_task_ids,
    extract_stale_clear_task_ids,
)


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@pytest.fixture
def cfg(tmp_path):
    return TaskLifecycleConfig(
        enabled=True,
        storage_dir=tmp_path / "task_lifecycle",
        max_resume_tasks=10,
    )


# ── The single-argument form must not change ─────────────────────────────────

class TestTheExistingFormIsUnchanged:
    def test_one_id_still_reads_as_one_id(self):
        assert extract_delegate_task_ids("AUTORUN_TASK_DELEGATED(77)") == ["77"]
        assert extract_stale_clear_task_ids(
            "AUTORUN_TASKS_CLEAR_STALE_TASK(77)") == ["77"]

    def test_several_separate_markers_still_all_count(self):
        text = "AUTORUN_TASK_DELEGATED(77)\nand later\nAUTORUN_TASK_DELEGATED(78)"
        assert extract_delegate_task_ids(text) == ["77", "78"]

    def test_a_repeated_id_is_reported_once(self):
        text = "AUTORUN_TASK_DELEGATED(77) AUTORUN_TASK_DELEGATED(77)"
        assert extract_delegate_task_ids(text) == ["77"]

    def test_non_numeric_ids_still_work(self):
        """Ids are opaque strings; some harnesses do not use numbers."""
        assert extract_delegate_task_ids("AUTORUN_TASK_DELEGATED(task-a.1_x)") == \
            ["task-a.1_x"]

    def test_text_with_no_marker_yields_nothing(self):
        assert extract_delegate_task_ids("just some prose about delegation") == []
        assert extract_delegate_task_ids("") == []
        assert extract_delegate_task_ids(None) == []


# ── Comma-delimited ids ──────────────────────────────────────────────────────

class TestCommaDelimitedIds:
    @pytest.mark.parametrize(
        "inner, expected",
        [
            ("1,2,3", ["1", "2", "3"]),
            ("1, 2, 3", ["1", "2", "3"]),
            ("1 , 2 ,3", ["1", "2", "3"]),
            ("77", ["77"]),
            ("a-1, b_2, c.3", ["a-1", "b_2", "c.3"]),
        ],
    )
    def test_a_list_clears_every_id_in_it(self, inner, expected):
        assert extract_stale_clear_task_ids(
            f"AUTORUN_TASKS_CLEAR_STALE_TASK({inner})") == expected

    @pytest.mark.parametrize(
        "inner, expected",
        [
            ("1,2,3", ["1", "2", "3"]),
            ("1, 2, 3", ["1", "2", "3"]),
            ("77", ["77"]),
        ],
    )
    def test_a_list_delegates_every_id_in_it(self, inner, expected):
        assert extract_delegate_task_ids(
            f"AUTORUN_TASK_DELEGATED({inner})") == expected

    def test_ids_from_a_list_and_from_separate_markers_combine(self):
        text = "AUTORUN_TASK_DELEGATED(1, 2)\nAUTORUN_TASK_DELEGATED(3)"
        assert extract_delegate_task_ids(text) == ["1", "2", "3"]

    def test_duplicates_across_a_list_are_reported_once(self):
        assert extract_delegate_task_ids("AUTORUN_TASK_DELEGATED(1, 1, 2)") == \
            ["1", "2"]

    def test_a_trailing_comma_is_tolerated(self):
        """A trailing comma is a typo, not an instruction to do nothing."""
        assert extract_delegate_task_ids("AUTORUN_TASK_DELEGATED(1, 2,)") == \
            ["1", "2"]

    def test_an_empty_marker_names_nothing(self):
        assert extract_delegate_task_ids("AUTORUN_TASK_DELEGATED()") == []


# ── The optional session ─────────────────────────────────────────────────────

class TestSessionArgument:
    def test_the_session_is_not_mistaken_for_a_task(self):
        assert extract_delegate_task_ids(
            "AUTORUN_TASK_DELEGATED(77, session=agent-abc)") == ["77"]

    def test_the_session_is_reported_alongside_its_tasks(self):
        entries = extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(77, 78, session=agent-abc)")
        assert entries == [(["77", "78"], "agent-abc")]

    def test_a_marker_without_a_session_reports_none(self):
        assert extract_delegate_markers("AUTORUN_TASK_DELEGATED(77)") == \
            [(["77"], None)]

    def test_two_markers_may_name_different_sessions(self):
        entries = extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(1, session=a)\n"
            "AUTORUN_TASK_DELEGATED(2, session=b)"
        )
        assert entries == [(["1"], "a"), (["2"], "b")]

    def test_the_session_may_come_first(self):
        entries = extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(session=a, 1, 2)")
        assert entries == [(["1", "2"], "a")]

    def test_two_bare_ids_are_two_tasks_not_a_task_and_a_session(self):
        """The reason the session is named rather than positional."""
        assert extract_delegate_markers("AUTORUN_TASK_DELEGATED(77, 78)") == \
            [(["77", "78"], None)]

    def test_an_unknown_keyword_is_ignored_rather_than_treated_as_a_task(self):
        """A typo must not silently delegate a task called "priority=high"."""
        entries = extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(77, priority=high)")
        assert entries == [(["77"], None)]

    def test_the_session_keyword_is_case_insensitive(self):
        assert extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(77, SESSION=abc)") == [(["77"], "abc")]

    def test_the_explicit_agent_session_id_keyword_is_reported(self):
        assert extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(77, agent_session_id=agent-abc)"
        ) == [(["77"], "agent-abc")]

    def test_explicit_agent_session_id_takes_precedence_over_legacy_session(self):
        assert extract_delegate_markers(
            "AUTORUN_TASK_DELEGATED(77, session=old, agent_session_id=new)"
        ) == [(["77"], "new")]


# ── What the extended grammar does to real tasks ─────────────────────────────

def _stop_context(session_id, assistant_text):
    ctx = EventContext(
        session_id=session_id,
        event="Stop",
        prompt="",
        tool_name="",
        tool_input={},
        tool_result="",
        session_transcript=[{"role": "assistant", "content": assistant_text}],
        store=ThreadSafeDB(),
        cli_type="claude",
    )
    ctx.autorun_active = True
    ctx.autorun_stage = EventContext.STAGE_1
    return ctx


class TestAppliedToTasks:
    def _seed(self, cfg, session_id, task_ids):
        seed = TaskLifecycle(config=cfg, session_id=session_id)
        for task_id in task_ids:
            seed.create_task(task_id, {"subject": f"Task {task_id}"}, "created")
            seed.update_task(task_id, {"status": "in_progress"}, "started")
        return seed

    def test_one_marker_delegates_several_tasks(self, isolated_state, cfg):
        session_id = "grammar-many"
        self._seed(cfg, session_id, ["1", "2", "3"])

        ctx = _stop_context(session_id, "AUTORUN_TASK_DELEGATED(1, 2, 3)")
        manager = TaskLifecycle(ctx=ctx, config=cfg)

        assert manager.handle_stop(ctx) is None
        for task_id in ("1", "2", "3"):
            assert manager.tasks[task_id]["status"] == "delegated"

    def test_the_session_is_recorded_on_each_delegated_task(
        self, isolated_state, cfg
    ):
        """Where the work went is worth knowing when it does not come back."""
        session_id = "grammar-session"
        self._seed(cfg, session_id, ["1", "2"])

        ctx = _stop_context(
            session_id, "AUTORUN_TASK_DELEGATED(1, 2, session=agent-abc)")
        manager = TaskLifecycle(ctx=ctx, config=cfg)
        manager.handle_stop(ctx)

        for task_id in ("1", "2"):
            assert manager.tasks[task_id]["metadata"]["delegated_to_session"] == \
                "agent-abc"

    def test_no_session_leaves_no_stray_metadata(self, isolated_state, cfg):
        session_id = "grammar-no-session"
        self._seed(cfg, session_id, ["1"])

        ctx = _stop_context(session_id, "AUTORUN_TASK_DELEGATED(1)")
        manager = TaskLifecycle(ctx=ctx, config=cfg)
        manager.handle_stop(ctx)

        assert "delegated_to_session" not in manager.tasks["1"]["metadata"]

    def test_one_marker_clears_several_stale_tasks(self, isolated_state, cfg):
        session_id = "grammar-clear-many"
        cfg.ghost_clear_enabled = True
        self._seed(cfg, session_id, ["1", "2"])

        blocked = None
        for _ in range(5):
            ctx = _stop_context(
                session_id, "AUTORUN_TASKS_CLEAR_STALE_TASK(1, 2)")
            ctx.ghost_clear_min_consecutive_blocks_override = 1
            blocked = TaskLifecycle(ctx=ctx, config=cfg).handle_stop(ctx)
            if blocked is None:
                break

        assert blocked is None, (
            f"A comma-delimited clear marker did not release the gate: {blocked!r}"
        )

    def test_a_list_naming_an_unrelated_task_still_applies_the_rest(
        self, isolated_state, cfg
    ):
        """One bad id must not discard the whole instruction."""
        session_id = "grammar-partial"
        self._seed(cfg, session_id, ["1"])

        ctx = _stop_context(session_id, "AUTORUN_TASK_DELEGATED(1, 999)")
        manager = TaskLifecycle(ctx=ctx, config=cfg)

        assert manager.handle_stop(ctx) is None
        assert manager.tasks["1"]["status"] == "delegated"
        assert "999" not in manager.tasks


# ── Cost ─────────────────────────────────────────────────────────────────────

class TestExtractionCost:
    """Marker scanning runs on every Stop and every PostToolUse.

    Transcripts reach the hook truncated to a 64 KiB cap, so the realistic
    input is bounded — but the scan happens twice per hook, once per marker,
    and almost none of those hooks carry a marker. The cost that matters is
    therefore the cost of finding nothing.

    The oversized input below is deliberate headroom: it is far past the
    ingest cap, so a pattern that degrades on transcript-shaped text shows up
    here rather than in production when the cap is raised or bypassed with
    AUTORUN_NO_TRUNCATE.
    """

    @staticmethod
    def _big_transcript(marker: str = "") -> str:
        # Text shaped like a real transcript: lots of parentheses and
        # capitalized identifiers, so a naive pattern has plenty to chew on.
        filler = (
            "The assistant called SomeTool(argument=value) and then "
            "ANOTHER_THING(1, 2, 3) before continuing.\n"
        ) * 20000
        return filler + marker

    def test_scanning_a_large_transcript_without_a_marker_is_cheap(self):
        import time

        text = self._big_transcript()
        assert len(text) > 1_000_000, "the fixture is not large enough to matter"

        start = time.perf_counter()
        for _ in range(10):
            assert extract_delegate_task_ids(text) == []
        elapsed_ms = (time.perf_counter() - start) * 100  # per iteration

        assert elapsed_ms < 25.0, (
            f"Scanning a {len(text) // 1024} KiB transcript for an absent "
            f"marker took {elapsed_ms:.1f}ms — and that is roughly 20x the "
            "64 KiB a real transcript is capped at. This runs twice per hook "
            "inside a 500ms budget shared with everything else."
        )

    def test_scanning_does_not_copy_its_inputs(self):
        """Joining the texts copied every byte on every call.

        The arguments are scanned where they are, so a large transcript with
        no marker costs one substring search and no allocation.
        """
        import tracemalloc

        text = self._big_transcript()
        extract_delegate_task_ids(text, "")  # warm any lazy setup

        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(5):
            extract_delegate_task_ids(text, "also no marker here")
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()

        assert after - before < len(text), (
            f"Scanning allocated {after - before} bytes against a "
            f"{len(text)} byte input, so the inputs are still being copied."
        )

    def test_a_marker_at_the_very_end_is_still_found(self):
        """The cheap pre-filter must not become a cheap wrong answer."""
        text = self._big_transcript("AUTORUN_TASK_DELEGATED(77)")
        assert extract_delegate_task_ids(text) == ["77"]
