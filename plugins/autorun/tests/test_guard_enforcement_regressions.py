"""Regressions for the PreToolUse safety-guard enforcement layer.

Every test here drives the real path: ``plugins.check_blocked_commands`` or a
handler through ``plugins.app.dispatch``, with an in-memory ``ThreadSafeDB`` so
nothing reaches the live daemon. None of these commands is ever executed.
"""

import uuid

from autorun import plugins
from autorun.core import EventContext, ThreadSafeDB


def _sid() -> str:
    return f"guard-{uuid.uuid4().hex[:12]}"


def _bash(cmd: str, *, sid: str | None = None, store: ThreadSafeDB | None = None) -> EventContext:
    return EventContext(
        session_id=sid or _sid(),
        event="PreToolUse",
        tool_name="Bash",
        tool_input={"command": cmd},
        store=store if store is not None else ThreadSafeDB(),
    )


def _prompt(prompt: str, *, sid: str, store: ThreadSafeDB) -> dict:
    return plugins.app.dispatch(EventContext(session_id=sid, event="UserPromptSubmit", prompt=prompt, store=store))


def _decision(result) -> str:
    return (result or {}).get("hookSpecificOutput", {}).get("permissionDecision", "")


def _reason(result) -> str:
    return (result or {}).get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


class TestSlashPrefixDoesNotBypassTiers:
    """E1: a Bash command that merely starts with ``/ar:`` was approved whole."""

    def test_ar_prefixed_command_chain_still_hits_the_rm_block(self):
        for cmd in ("/ar:ok rm; rm -rf build", "/ar:x; rm -rf ~/important"):
            result = plugins.check_blocked_commands(_bash(cmd))
            assert _decision(result) == "deny", (cmd, result)
