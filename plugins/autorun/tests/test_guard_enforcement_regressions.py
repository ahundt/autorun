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


class TestAllowWaivesOnlyTheSegmentItMatches:
    """E2: one allow used to approve the whole command line it touched."""

    def test_deny_lists_a_hint_for_every_blocked_pattern(self):
        result = plugins.check_blocked_commands(_bash("rm -rf build && git push --force origin main"))
        assert _decision(result) == "deny"
        reason = _reason(result)
        assert "/ar:ok 'git push --force'" in reason, reason
        assert "/ar:ok rm" in reason or "/ar:ok 'rm -rf'" in reason, reason

    def test_rm_allow_does_not_approve_a_force_push_in_the_same_line(self):
        sid, store = _sid(), ThreadSafeDB()
        _prompt("/ar:ok rm", sid=sid, store=store)
        result = plugins.check_blocked_commands(
            _bash("rm -rf build && git push --force origin main", sid=sid, store=store)
        )
        assert _decision(result) == "deny", result
        assert "git push --force" in _reason(result)

    def test_git_push_allow_does_not_approve_rm_in_the_same_line(self):
        sid, store = _sid(), ThreadSafeDB()
        _prompt("/ar:ok 'git push'", sid=sid, store=store)
        result = plugins.check_blocked_commands(
            _bash("git push origin main && rm -rf ~/projects", sid=sid, store=store)
        )
        assert _decision(result) == "deny", result

    def test_allow_still_approves_its_segment_next_to_unblocked_commands(self):
        sid, store = _sid(), ThreadSafeDB()
        _prompt("/ar:ok rm", sid=sid, store=store)
        first = plugins.check_blocked_commands(_bash("cd /tmp/x && rm -rf build", sid=sid, store=store))
        assert _decision(first) != "deny", first
        # The default grant is one use: the next rm is blocked again.
        second = plugins.check_blocked_commands(_bash("cd /tmp/y && rm -rf build", sid=sid, store=store))
        assert _decision(second) == "deny", second

    def test_allows_covering_every_blocked_segment_approve_the_line(self):
        sid, store = _sid(), ThreadSafeDB()
        _prompt("/ar:ok rm", sid=sid, store=store)
        _prompt("/ar:ok 'git push'", sid=sid, store=store)
        result = plugins.check_blocked_commands(_bash("rm -rf build && git push origin main", sid=sid, store=store))
        assert _decision(result) != "deny", result

    def test_denied_line_does_not_spend_the_grant(self):
        sid, store = _sid(), ThreadSafeDB()
        _prompt("/ar:ok rm", sid=sid, store=store)
        denied = plugins.check_blocked_commands(_bash("rm -rf a && git push --force", sid=sid, store=store))
        assert _decision(denied) == "deny"
        allowed = plugins.check_blocked_commands(_bash("rm -rf b", sid=sid, store=store))
        assert _decision(allowed) != "deny", allowed


class TestUseInsteadIsBuiltFromTheMatchedCommand:
    """E4: the redirect substituted words of the whole line, e.g. `trash ~`."""

    def test_trash_suggestion_names_only_the_rm_operands(self):
        cases = {
            "ls ~ ; rm -r old": "trash old",
            "cd /tmp && rm -rf build": "trash build",
            "sudo rm -rf /var/x": "trash /var/x",
            "rm -rf 'my dir'": "trash 'my dir'",
        }
        for cmd, expected in cases.items():
            reason = _reason(plugins.check_blocked_commands(_bash(cmd)))
            suggestions = [line for line in reason.splitlines() if line.startswith("Use instead:")]
            assert suggestions, (cmd, reason)
            assert all(line == f"Use instead: `{expected}`" for line in suggestions), (cmd, suggestions)

    def test_fixed_redirect_is_shown_unchanged(self):
        reason = _reason(plugins.check_blocked_commands(_bash("cd repo && git clean -fd")))
        assert "Use instead: `git clean -n`" in reason, reason
