"""A state-failure notice must fit whichever harness is listening.

When persistence fails at the end of a dispatch, the handlers have already
run and their response is already built, so there is no chain left to add a
notification to. The notice has to be merged into the finished response
instead — and that is where a harness regression is easy to introduce,
because the harnesses do not agree on where text belongs.

Claude and Codex validate strictly and drop unknown fields. Gemini, Qwen, and
Antigravity accept a looser shape. ForgeCode carries no hook response at all.
Writing Claude's field layout by hand would be silently stripped on some of
them and, on ForgeCode, would attach a response where none belongs.

So the notice is serialized by the same per-harness contract every other
response uses, and these tests hold that line for every supported harness.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent
SRC_DIR = PLUGIN_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autorun import core  # noqa: E402
from autorun.core import EventContext, ThreadSafeDB  # noqa: E402
from autorun.platforms import PLATFORMS  # noqa: E402
from autorun.session_manager import SessionTimeoutError  # noqa: E402

# Every harness the plugin claims to support. Taken from the platform
# registry rather than written out, so a newly supported harness fails here
# until its behavior is decided rather than silently inheriting Claude's.
SUPPORTED_CLI_TYPES = sorted(PLATFORMS)

NOTICE_MARKER = "could not save session state"


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    from autorun import session_manager as sm

    directory = tmp_path / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(directory))
    sm._reset_for_testing()
    yield directory
    sm._reset_for_testing()


@contextlib.contextmanager
def failing_persistence():
    """Fail every persistent write, the way sustained contention would."""
    @contextlib.contextmanager
    def failing_session_state(*args, **kwargs):
        raise SessionTimeoutError("Could not acquire state lock for 'x' after 0.25s")
        yield  # pragma: no cover - unreachable, keeps this a generator

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(core, "session_state", failing_session_state)
        yield


def _context(store, cli_type, event="PostToolUse"):
    ctx = EventContext(
        session_id=f"notice-{cli_type}",
        event=event,
        prompt="",
        tool_name="Edit",
        tool_input={},
        tool_result="",
        session_transcript=[],
        store=store,
        cli_type=cli_type,
    )
    ctx.autorun_active = False
    ctx.autorun_stage = EventContext.STAGE_INACTIVE
    return ctx


def _notice_text(response) -> str:
    """Every place a harness could carry the notice, flattened."""
    if not isinstance(response, dict):
        return ""
    parts = [str(response.get("systemMessage") or ""),
             str(response.get("reason") or "")]
    hook_output = response.get("hookSpecificOutput") or {}
    if isinstance(hook_output, dict):
        parts.append(str(hook_output.get("additionalContext") or ""))
    return " ".join(parts)


@pytest.mark.parametrize("cli_type", SUPPORTED_CLI_TYPES)
class TestNoticeShapePerHarness:
    def test_a_failed_flush_never_breaks_the_hook(self, isolated_state, cli_type):
        """A bookkeeping failure must not become a failed hook on any harness."""
        store = ThreadSafeDB()
        ctx = _context(store, cli_type)

        with failing_persistence():
            response = core._attach_state_failure_notice(
                {"continue": True}, ctx,
                "⚠️ autorun could not save session state at the end of this "
                "PostToolUse hook: lock timeout.",
            )

        assert isinstance(response, dict)

    def test_the_response_survives_this_harnesses_own_validation(
        self, isolated_state, cli_type
    ):
        """Whatever is attached must still be a response the harness accepts.

        A field this harness rejects would be stripped at best. At worst the
        harness treats the whole response as malformed and ignores it, which
        would turn off every protection the hook provides.
        """
        store = ThreadSafeDB()
        ctx = _context(store, cli_type)

        # Start from a response this harness already accepts, so the only
        # thing under test is what the notice adds.
        original = core.validate_hook_response(
            "PostToolUse", {"continue": True}, cli_type)
        attached = core._attach_state_failure_notice(
            dict(original), ctx,
            f"⚠️ autorun {NOTICE_MARKER}: lock timeout.",
        )
        revalidated = core.validate_hook_response("PostToolUse", dict(attached), cli_type)

        assert revalidated == attached, (
            f"The notice added fields {cli_type} does not accept; validation "
            f"changed the response. Before: {attached}. After: {revalidated}"
        )

    def test_existing_handler_text_is_kept(self, isolated_state, cli_type):
        """The handlers' own message is still true and must not be replaced."""
        store = ThreadSafeDB()
        ctx = _context(store, cli_type)

        response = core._attach_state_failure_notice(
            {"continue": True, "systemMessage": "handler said something"},
            ctx,
            f"⚠️ autorun {NOTICE_MARKER}: lock timeout.",
        )

        text = _notice_text(response)
        if text:
            assert "handler said something" in text, (
                f"The notice overwrote the handler's message on {cli_type}. "
                f"Got: {response}"
            )


class TestNoticeReachesHarnessesThatCanCarryIt:
    @pytest.mark.parametrize(
        "cli_type",
        [name for name in SUPPORTED_CLI_TYPES
         if PLATFORMS[name].schema_type != "none"],
    )
    def test_the_notice_is_actually_visible(self, isolated_state, cli_type):
        store = ThreadSafeDB()
        ctx = _context(store, cli_type)

        response = core._attach_state_failure_notice(
            {"continue": True}, ctx, f"⚠️ autorun {NOTICE_MARKER}: lock timeout.",
        )

        assert NOTICE_MARKER in _notice_text(response), (
            f"{cli_type} can carry a hook response but the notice did not "
            f"reach any field it reads. Got: {response}"
        )

    @pytest.mark.parametrize(
        "cli_type",
        [name for name in SUPPORTED_CLI_TYPES
         if PLATFORMS[name].schema_type == "none"],
    )
    def test_a_harness_without_hook_responses_gets_nothing_extra(
        self, isolated_state, cli_type
    ):
        """Attaching a response where none belongs is worse than staying quiet.

        The log keeps the record for these; the failure is logged before the
        notice is ever built.
        """
        store = ThreadSafeDB()
        ctx = _context(store, cli_type)
        original = {"continue": True}

        response = core._attach_state_failure_notice(
            dict(original), ctx, f"⚠️ autorun {NOTICE_MARKER}: lock timeout.",
        )

        assert response == original, (
            f"{cli_type} carries no hook response, but fields were added: "
            f"{response}"
        )


class TestDispatchDoesNotLoseTheResponse:
    """The flush happens after the handlers, so it must not undo them.

    A denial the handlers already decided on would become an allow if the
    response were dropped, which is how a bookkeeping failure turns into a
    safety failure. The handlers are stubbed out here so the test is about
    the flush and nothing else.
    """

    @staticmethod
    def _decision_for(cli_type):
        return {
            "continue": True,
            "systemMessage": "handlers denied this",
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "handlers denied this",
            },
        }

    @pytest.mark.parametrize("cli_type", SUPPORTED_CLI_TYPES)
    def test_a_flush_failure_still_returns_the_handlers_decision(
        self, isolated_state, cli_type, monkeypatch
    ):
        from autorun import plugins

        store = ThreadSafeDB()
        ctx = _context(store, cli_type, event="PreToolUse")
        decision = self._decision_for(cli_type)

        def handlers_that_write_state(context):
            context.state_set("some_field", "some value")
            store.set(f"{context.session_id}:another_field", "value")
            return dict(decision)

        monkeypatch.setattr(
            type(plugins.app), "_dispatch_unbatched",
            lambda self, context: handlers_that_write_state(context),
        )

        with failing_persistence():
            response = plugins.app.dispatch(ctx)

        assert response is not None, (
            f"dispatch returned nothing on {cli_type}: the flush failure "
            "discarded a decision the handlers had already made."
        )
        assert response.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" \
            or response.get("systemMessage"), (
            f"The decision was lost on {cli_type}. Got: {response}"
        )

    @pytest.mark.parametrize("cli_type", SUPPORTED_CLI_TYPES)
    def test_a_flush_failure_does_not_escape_dispatch(
        self, isolated_state, cli_type, monkeypatch
    ):
        """An exception here would surface as a hook error on every harness."""
        from autorun import plugins

        store = ThreadSafeDB()
        ctx = _context(store, cli_type, event="PostToolUse")

        monkeypatch.setattr(
            type(plugins.app), "_dispatch_unbatched",
            lambda self, context: store.set(f"{context.session_id}:f", 1) or {},
        )

        with failing_persistence():
            plugins.app.dispatch(ctx)  # must not raise
