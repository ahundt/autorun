"""Regressions for the PreToolUse safety-guard enforcement layer.

Every test here drives the real path: ``plugins.check_blocked_commands`` or a
handler through ``plugins.app.dispatch``, with an in-memory ``ThreadSafeDB`` so
nothing reaches the live daemon. None of these commands is ever executed.
"""

import json
import uuid

import pytest

from autorun import config as config_module
from autorun import integrations, plugins
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


def _claude_transcript(path, *entries) -> str:
    """Write Claude Code-shaped JSONL: ("user"|"assistant"|"tool_result", text)."""
    lines = []
    for kind, text in entries:
        if kind == "user":
            lines.append({"type": "user", "message": {"role": "user", "content": text}})
        elif kind == "tool_result":
            content = [{"type": "tool_result", "tool_use_id": "t1", "content": text}]
            lines.append({"type": "user", "message": {"role": "user", "content": content}})
        else:
            content = [{"type": "text", "text": text}]
            lines.append({"type": "assistant", "message": {"role": "assistant", "content": content}})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return str(path)


class TestJustifyReadsTheHarnessTranscript:
    """F4: /ar:j JUSTIFY only saw Pi's inline transcript, so it denied every
    new-file Write on Claude, Codex and the Gemini family."""

    def _write(self, tmp_path, transcript_path=None, last_assistant_message=""):
        ctx = EventContext(
            session_id=_sid(),
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(tmp_path / "brand_new_module.py"), "content": "x"},
            transcript_path=transcript_path,
            last_assistant_message=last_assistant_message,
            store=ThreadSafeDB(),
        )
        ctx.file_policy = "JUSTIFY"
        return plugins.enforce_file_policy(ctx)

    def test_justification_in_the_current_turn_of_transcript_path_allows(self, tmp_path):
        path = _claude_transcript(
            tmp_path / "t.jsonl",
            ("user", "add a parser"),
            ("assistant", "<AUTOFILE_JUSTIFICATION>no module parses this format yet</AUTOFILE_JUSTIFICATION>"),
            ("tool_result", "ok"),
            ("assistant", "Creating it now."),
        )
        assert self._write(tmp_path, transcript_path=path) is None

    def test_last_assistant_message_justification_allows(self, tmp_path):
        text = "<AUTOFILE_JUSTIFICATION>no module parses this format yet</AUTOFILE_JUSTIFICATION>"
        assert self._write(tmp_path, last_assistant_message=text) is None

    def test_autorun_guidance_echoed_back_is_not_a_justification(self, tmp_path):
        guidance = plugins.CONFIG["policy_blocked"]["JUSTIFY"]
        path = _claude_transcript(
            tmp_path / "t.jsonl",
            ("user", "add a parser"),
            ("tool_result", guidance),
            ("assistant", f"The hook said: {guidance}"),
        )
        assert _decision(self._write(tmp_path, transcript_path=path)) == "deny"

    def test_pi_inline_transcript_holding_only_the_deny_message_still_denies(self, tmp_path):
        guidance = plugins.CONFIG["policy_blocked"]["JUSTIFY"]
        ctx = EventContext(
            session_id=_sid(),
            event="PreToolUse",
            tool_name="Write",
            tool_input={"file_path": str(tmp_path / "brand_new_module.py"), "content": "x"},
            session_transcript=[{"role": "toolResult", "content": guidance}],
            store=ThreadSafeDB(),
        )
        ctx.file_policy = "JUSTIFY"
        assert _decision(plugins.enforce_file_policy(ctx)) == "deny"

    def test_justification_from_an_earlier_turn_does_not_carry_over(self, tmp_path):
        path = _claude_transcript(
            tmp_path / "t.jsonl",
            ("user", "first task"),
            ("assistant", "<AUTOFILE_JUSTIFICATION>needed for the first task</AUTOFILE_JUSTIFICATION>"),
            ("user", "now something else"),
            ("assistant", "Writing a file."),
        )
        assert _decision(self._write(tmp_path, transcript_path=path)) == "deny"

    def test_a_real_justification_after_a_placeholder_still_counts(self, tmp_path):
        text = (
            "<AUTOFILE_JUSTIFICATION>reason</AUTOFILE_JUSTIFICATION> then "
            "<AUTOFILE_JUSTIFICATION>no module parses this format yet</AUTOFILE_JUSTIFICATION>"
        )
        assert self._write(tmp_path, last_assistant_message=text) is None


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    """Overlay an autorun.config.json onto CONFIG for one test, then undo it."""
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
    saved = dict(config_module.CONFIG)

    def apply(payload: dict) -> None:
        (tmp_path / config_module.USER_CONFIG_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
        config_module.apply_user_config(config_module.CONFIG)
        integrations.invalidate_caches()

    yield apply
    config_module.CONFIG.clear()
    config_module.CONFIG.update(saved)
    integrations.invalidate_caches()


class TestUserDefaultIntegrationsAreEnforced:
    """F3: config.py merged the user's default_integrations into CONFIG, but the
    enforcement read the DEFAULT_INTEGRATIONS module constant."""

    def test_user_added_guard_is_loaded_and_blocks(self, user_config):
        user_config({"default_integrations": {"mycmd": {"action": "block", "suggestion": "mycmd is off limits"}}})
        assert any("mycmd" in intg.patterns for intg in integrations.load_all_integrations())
        result = plugins.check_blocked_commands(_bash("mycmd --go"))
        assert _decision(result) == "deny" and "mycmd is off limits" in _reason(result)

    def test_user_override_of_a_shipped_guard_wins(self, user_config):
        user_config({"default_integrations": {"awk": {"action": "warn", "suggestion": "awk is fine here"}}})
        result = plugins.check_blocked_commands(_bash("awk '{print $1}' data.txt"))
        assert _decision(result) != "deny" and "awk is fine here" in str(result)

    def test_env_toggle_still_removes_a_guard_the_user_file_names(self, user_config, monkeypatch):
        monkeypatch.setenv("AUTORUN_PUBLISH_GUARD_ENABLED", "false")
        user_config({"default_integrations": {"publish-command": {"patterns": ["cargo publish"], "suggestion": "x"}}})
        names = {intg.name for intg in integrations.load_all_integrations()}
        assert "publish-command" not in names
