#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Copyright 2025 Andrew Hundt <ATHundt@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""The cache guard checks a prompt before it re-sends an expired cache.

A prompt sent after the last response's cache lifetime has passed re-writes
the whole context at the cache-write price. The check that catches it,
``expired``, trips when the time since the last response exceeds the
lifetime that response wrote (3,600 s for ``ephemeral_1h_input_tokens``, 300 s
for ``ephemeral_5m_input_tokens``) and the context (input + cache read +
cache write) is at or above ``expired_min_tokens`` (default 100,000). At
prompt time it is the only check that runs; ``ratio``, ``read`` and ``age``
stay at tool time. ``/ar:cache on`` blocks, ``/ar:cache warn`` only tells,
``/ar:cache off`` stops both, and resending the same text passes.

Every case dispatches through ``app.dispatch`` with the fixture transcripts
in ``tests/fixtures/cache_expiry/``, whose timestamps are fixed against the
clock below. The clock replaces ``autorun.cache_guard.time``, the same seam
``tests/test_cache_guard.py`` drives, so the state locks underneath keep real
time.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

import pytest

from autorun import plugins as _plugins  # noqa: F401  (registers command handlers and chains)
from autorun.config import CONFIG
from autorun.core import EventContext, ThreadSafeDB, app
from autorun.platforms import PLATFORMS, hook_platforms

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cache_expiry"
NOW = 1_794_398_400.0  # 2026-11-11T12:00:00Z; every fixture timestamp is relative to it

EXPIRED_1H = FIXTURES / "claude_1h_lifetime_idle_2h_250k_tokens.jsonl"
FRESH_1H = FIXTURES / "claude_1h_lifetime_idle_30m_250k_tokens.jsonl"
EXPIRED_5M = FIXTURES / "claude_5m_lifetime_idle_10m_250k_tokens.jsonl"
SMALL_EXPIRED_1H = FIXTURES / "claude_1h_lifetime_idle_2h_20k_tokens.jsonl"
PI_NO_LIFETIME = FIXTURES / "pi_no_lifetime_idle_2h_250k_tokens.jsonl"
CODEX_ROLLOUT = FIXTURES / "codex_rollout_no_usable_record.jsonl"
NO_USAGE = FIXTURES / "claude_no_usage_record.jsonl"
# Built at test time: a repository test forbids tracked binary files.
UNREADABLE_BYTES = (
    b"\xff\xfe\x00\x00\x89PNG\r\n\x1a\n" + bytes(range(256)) + b"\n" + b'{"type": "assistant", "message": {"usage": \n'
)

EXPIRED_NOTICE = "cache expired"  # section 5.4.2 item 4: "this session's prompt cache expired ..."
RESEND_LINE = "Send the same prompt again"
TOOL_TIME_HINTS = ("Tripped axes", "/ar:cache ok 3", "/ar:cache set ratio", "/ar:cache set read", "/ar:cache set age")


class _FrozenClock:
    def __init__(self, now: float):
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(tmp_path, monkeypatch):
    """Per-test session state and a cache-guard clock the test advances."""
    from autorun import cache_guard, session_manager

    state = tmp_path / "sessions"
    state.mkdir()
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(state))
    session_manager._reset_for_testing()
    frozen = _FrozenClock(NOW)
    monkeypatch.setattr(cache_guard, "time", frozen)
    yield frozen
    session_manager._reset_for_testing()


class Session:
    def __init__(self, cli_type: str = "claude", transcript: Path | None = None):
        self.id = f"k2-{cli_type}-{uuid.uuid4().hex[:10]}"
        self.cli_type = cli_type
        self.transcript = str(transcript) if transcript else None
        self.store = ThreadSafeDB()

    def prompt(self, text: str, **fields) -> dict:
        return app.dispatch(
            EventContext(
                session_id=self.id,
                event="UserPromptSubmit",
                prompt=text,
                cli_type=self.cli_type,
                transcript_path=self.transcript,
                store=self.store,
                **fields,
            )
        )

    def command(self, text: str) -> dict:
        """A command the user types; it must never be blocked itself."""
        response = self.prompt(text)
        assert not blocked(response), f"the user's command {text!r} was blocked: {response}"
        return response

    def tool(self, command: str = "git status") -> dict:
        return app.dispatch(
            EventContext(
                session_id=self.id,
                event="PreToolUse",
                tool_name=PLATFORMS[self.cli_type].tool_names["bash"],
                tool_input={"command": command},
                cli_type=self.cli_type,
                transcript_path=self.transcript,
                store=self.store,
            )
        )


def blocked(response: dict | None) -> bool:
    response = response or {}
    hook_output = response.get("hookSpecificOutput") or {}
    return (
        response.get("decision") in {"block", "deny"}
        or hook_output.get("permissionDecision") == "deny"
        or response.get("continue") is False
    )


def text_of(response: dict | None) -> str:
    return json.dumps(response or {}, ensure_ascii=False)


def armed(transcript: Path, cli_type: str = "claude", mode: str = "on") -> Session:
    session = Session(cli_type, transcript)
    session.command(f"ar:cache {mode}")
    return session


# ─── which transcripts block ──────────────────────────────────────────────


def test_prompt_time_blocks_exactly_the_expired_large_contexts(clock, tmp_path):
    runs = {
        "claude 1h lifetime, idle 2h, 250k": ("claude", EXPIRED_1H),
        "claude 1h lifetime, idle 30m, 250k": ("claude", FRESH_1H),
        "claude 5m lifetime, idle 10m, 250k": ("claude", EXPIRED_5M),
        "claude 1h lifetime, idle 2h, 20k": ("claude", SMALL_EXPIRED_1H),
        "pi, no lifetime, no age set": ("pi", PI_NO_LIFETIME),
        "codex rollout, no usable record": ("codex", CODEX_ROLLOUT),
        "claude, no usage record": ("claude", NO_USAGE),
        "claude, unreadable bytes": ("claude", tmp_path / "unreadable_transcript.jsonl"),
    }
    runs["claude, unreadable bytes"][1].write_bytes(UNREADABLE_BYTES)
    if sys.platform != "win32" and os.geteuid() != 0:
        locked = tmp_path / "locked.jsonl"
        shutil.copy2(EXPIRED_1H, locked)
        locked.chmod(0)
        runs["claude, file that cannot be opened"] = ("claude", locked)
    for platform_name in sorted(set(PLATFORMS) - {"claude", "codex", "pi"}):
        runs[f"{platform_name}, no transcript"] = (platform_name, None)

    outcomes = {}
    for label, (cli_type, transcript) in runs.items():
        response = armed(transcript, cli_type).prompt("Continue with the refactor.")
        outcomes[label] = "block" if blocked(response) else "pass"

    expected = {label: "pass" for label in runs}
    expected["claude 1h lifetime, idle 2h, 250k"] = "block"
    expected["claude 5m lifetime, idle 10m, 250k"] = "block"
    assert outcomes == expected


def test_cache_ok_lets_the_next_prompt_through_once(clock):
    session = armed(EXPIRED_1H)
    assert blocked(session.prompt("Continue with the refactor."))
    session.command("ar:cache ok")
    assert not blocked(session.prompt("Now update the changelog.")), "/ar:cache ok did not pass the next prompt"
    assert blocked(session.prompt("Then run the tests.")), "one /ar:cache ok passed more than one prompt"


def test_resending_the_blocked_text_passes_and_other_text_still_blocks(clock):
    session = armed(EXPIRED_1H)
    first = session.prompt("Continue with the refactor.")
    assert blocked(first), f"an expired 250k-token cache did not block: {first}"
    again = session.prompt("Continue with the refactor.")
    assert not blocked(again), f"resending the same text was blocked again: {again}"
    other = session.prompt("Now update the changelog.")
    assert blocked(other), f"the resend pass became a pass for every prompt: {other}"


def test_a_prompt_the_user_did_not_type_is_never_blocked(clock):
    control = armed(EXPIRED_1H).prompt("Continue with the refactor.")
    assert blocked(control), f"control: the user's own prompt was not blocked: {control}"

    cases = {
        "subagent": ({"agent_id": "subagent-1"}, "Continue with the refactor."),
        "task notification": ({}, "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n</task-notification>"),
        "agent message": ({}, '<agent-message from="worker">Finished the review.</agent-message>'),
        "cross-session message": ({}, '<cross-session-message from="other">Rebased.</cross-session-message>'),
        "model": ({"prompt_author": "model"}, "Continue with the refactor."),
        "automation": ({"prompt_author": "automation"}, "Continue with the refactor."),
    }
    passed_through = {}
    for label, (fields, text) in cases.items():
        passed_through[label] = not blocked(armed(EXPIRED_1H).prompt(text, **fields))
    assert passed_through == {label: True for label in cases}


def test_user_thresholds_stay_at_tool_time(clock):
    """``ratio``, ``read`` and ``age`` set by the user never block a prompt."""
    thresholds = ("ar:cache set age 10m", "ar:cache set ratio 0.999", "ar:cache set read 300k")

    control = armed(EXPIRED_1H)
    for command in thresholds:
        control.command(command)
    blocked_by_expiry = control.prompt("Continue with the refactor.")
    assert blocked(blocked_by_expiry), f"control: the expired cache did not block: {blocked_by_expiry}"
    assert "Tripped axes" not in text_of(blocked_by_expiry), "the prompt block reports tool-time axes"

    session = armed(FRESH_1H)
    for command in thresholds:
        session.command(command)
    at_prompt = session.prompt("Continue with the refactor.")
    assert not blocked(at_prompt), f"a user threshold blocked a prompt on an unexpired cache: {at_prompt}"
    at_tool = session.tool()
    assert blocked(at_tool), f"the same thresholds stopped working at tool time: {at_tool}"


# ─── a prompt denial blocks only on a harness that declares it ────────────


@pytest.mark.parametrize("cli_type", sorted(p.name for p in hook_platforms()))
def test_a_prompt_denial_blocks_exactly_where_the_platform_declares_it(clock, cli_type):
    protocol = PLATFORMS[cli_type].hook_protocol
    reason = "k4 denial reason"
    response = EventContext(session_id=f"k4-{cli_type}", event="UserPromptSubmit", prompt="hi", cli_type=cli_type).deny(reason)
    assert blocked(response) == ("UserPromptSubmit" in protocol.context_events_with_block_decision), response
    assert reason in text_of(response), f"the denial text was dropped on {cli_type}: {response}"
    if blocked(response):
        assert response.get("reason") == reason and response.get("systemMessage") == reason, response


def test_claude_and_codex_block_a_denied_prompt():
    blocking = {p.name for p in hook_platforms() if "UserPromptSubmit" in p.hook_protocol.context_events_with_block_decision}
    assert {"claude", "codex"} <= blocking, blocking


@pytest.mark.parametrize("cli_type", sorted(p.name for p in hook_platforms()))
def test_a_typed_permission_command_is_answered_and_never_blocks_the_prompt(clock, cli_type):
    """Every UserPromptSubmit reply that is not the cache check stays a reply.

    `regex:((a+))+b` is an unsafe regex the /ar:ok handler rejects; the
    quoted form without the prefix is stored as a literal. Both are typed on
    a session whose cache has expired with the check set to block, and
    neither reply may carry a decision. A subagent's copy is refused (the
    user-only rule) and that refusal carries no decision either.
    """
    session = armed(EXPIRED_1H, cli_type) if cli_type in {"claude", "codex", "pi", "prime"} else Session(cli_type)
    for text, wanted in (("/ar:ok 'regex:((a+))+b'", "Unsafe regex"), ("/ar:ok '((a+))+b'", "((a+))+b")):
        reply = session.prompt(text)
        assert not blocked(reply) and "decision" not in reply, f"{text!r} on {cli_type}: {reply}"
        assert wanted in text_of(reply), f"{text!r} on {cli_type} was not answered: {reply}"

    refused = session.prompt("/ar:ok 'git push'", agent_id="subagent-1")
    assert not blocked(refused) and "decision" not in refused, f"the user-only refusal blocks on {cli_type}: {refused}"
    from autorun.core import user_only_refusal

    assert user_only_refusal("/ar:ok", cli_type) in text_of(refused), refused


# ─── handler order on UserPromptSubmit ────────────────────────────────────


def test_the_prompt_check_runs_after_every_state_writing_prompt_handler(clock, monkeypatch):
    """The chain stops at the first response, and the check returns one.

    It is registered last, and when it blocks or warns the task-lifecycle
    prompt handler has already run.
    """
    chain = app.chains["UserPromptSubmit"]
    names = [handler.__name__ for handler in chain]
    assert names[-1] == "cache_guard_prompt_after_expiry", names
    assert "reset_stop_sequence_on_user_prompt" in names, names

    ran = []
    spied = []
    for handler in chain:
        if handler.__name__ == "reset_stop_sequence_on_user_prompt":
            def spy(ctx, _inner=handler):
                ran.append(ctx.prompt)
                return _inner(ctx)
            spied.append(spy)
        else:
            spied.append(handler)
    monkeypatch.setitem(app.chains, "UserPromptSubmit", spied)

    for mode in ("on", "warn"):
        ran.clear()
        response = armed(EXPIRED_1H, mode=mode).prompt("Continue with the refactor.")
        assert EXPIRED_NOTICE in text_of(response), f"{mode}: the check did not respond: {response}"
        assert "Continue with the refactor." in ran, f"{mode}: the task-lifecycle prompt handler did not run"


# ─── modes and the message ────────────────────────────────────────────────


@pytest.mark.parametrize("cli_type", ["claude", "codex", "gemini", "qwen"])
def test_ordinary_prompt_cache_check_executes_zero_sql_writes(
    clock, monkeypatch, cli_type
):
    from autorun import session_manager as sm

    monkeypatch.setitem(sm._CONFIG, "state_backend", "sqlite")
    sm._reset_for_testing()
    store = sm.get_session_manager()._store
    statements = []
    real_connect = store._connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(store, "_connect", connect)
    response = Session(cli_type, EXPIRED_1H).prompt("Continue with the refactor.")
    assert EXPIRED_NOTICE in text_of(response)
    writes = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
    ]
    assert writes == []


def test_warn_mode_tells_and_never_blocks(clock):
    response = armed(EXPIRED_1H, mode="warn").prompt("Continue with the refactor.")
    assert not blocked(response), f"warn mode blocked: {response}"
    assert EXPIRED_NOTICE in text_of(response), f"warn mode said nothing: {response}"


def test_the_prompt_time_message_names_its_switches_and_no_tool_time_hint(clock):
    response = armed(EXPIRED_1H).prompt("Continue with the refactor.")
    assert blocked(response), response
    text = text_of(response)
    for wanted in (EXPIRED_NOTICE, "/ar:cache warn", "/ar:cache set expired-min-tokens", RESEND_LINE):
        assert wanted in text, f"the block message lacks {wanted!r}: {text}"
    leaked = [hint for hint in TOOL_TIME_HINTS if hint in text]
    assert leaked == [], f"the prompt-time message carries tool-time hints {leaked}: {text}"


def test_a_command_typed_while_expired_gets_through(clock):
    session = armed(EXPIRED_1H)
    assert blocked(session.prompt("Continue with the refactor.")), "control: the expired cache did not block"
    reply = session.command("ar:cache off")
    assert "disabled" in text_of(reply), f"/ar:cache off did not run: {reply}"
    after = session.prompt("Now update the changelog.")
    assert not blocked(after), f"/ar:cache off did not switch the prompt check off: {after}"


# ─── timed toggles ────────────────────────────────────────────────────────


def _default_action() -> str:
    action = CONFIG.get("cache_guard_expired_prompt_action")
    assert action in {"block", "warn"}, f"CONFIG cache_guard_expired_prompt_action is {action!r}"
    return action


def test_off_for_an_hour_with_nothing_stored_returns_to_the_config_default(clock):
    default = _default_action()
    session = Session("claude", EXPIRED_1H)  # nothing stored, here or globally
    session.command("ar:cache off 1h")
    during = session.prompt("Continue with the refactor.")
    assert not blocked(during) and EXPIRED_NOTICE not in text_of(during), f"off 1h did not silence it: {during}"

    clock.now = NOW + 3601
    after = session.prompt("Now update the changelog.")
    assert blocked(after) == (default == "block"), f"after the hour, default {default!r} gave {after}"
    assert EXPIRED_NOTICE in text_of(after), f"after the hour the check is still silent: {after}"


def test_warn_then_off_for_an_hour_returns_to_warn(clock):
    session = armed(EXPIRED_1H, mode="warn")
    session.command("ar:cache off 1h")
    during = session.prompt("Continue with the refactor.")
    assert EXPIRED_NOTICE not in text_of(during), f"off 1h did not silence warn: {during}"

    clock.now = NOW + 3601
    after = session.prompt("Now update the changelog.")
    assert not blocked(after), f"off 1h expired into block instead of the stored warn: {after}"
    assert EXPIRED_NOTICE in text_of(after), f"off 1h expired into off instead of warn: {after}"


@pytest.mark.parametrize(("stored_enabled", "stored_prior"), [(False, True), (True, False), (True, True), (False, False)])
def test_a_timed_toggle_stored_before_rc4_expires_to_its_bool_prior(clock, stored_enabled, stored_prior):
    """rc3 stored `prior` as a bool. After upgrade it still decides the state.

    A bool prior of True means the guard was on, with no prompt action
    stored, so the prompt check takes the CONFIG default; False means off.
    """
    from autorun.cache_guard import _TOGGLE_KEY, is_cache_enabled
    from autorun.session_manager import session_state

    session = Session("claude", EXPIRED_1H)
    with session_state(session.id) as st:
        st[_TOGGLE_KEY] = {"enabled": stored_enabled, "prior": stored_prior, "expires_at": NOW - 60}

    assert is_cache_enabled(session.id) is stored_prior
    response = session.prompt("Continue with the refactor.")
    if stored_prior:
        assert EXPIRED_NOTICE in text_of(response), response
        assert blocked(response) == (_default_action() == "block"), response
    else:
        assert EXPIRED_NOTICE not in text_of(response) and not blocked(response), response


@pytest.mark.parametrize(
    "stored",
    [
        {"enabled": True, "prior": "yes", "expires_at": NOW - 60},
        {"enabled": True, "prior": {"expires_at": NOW - 120}, "expires_at": NOW - 60},
        {"enabled": True, "expires_at": "tomorrow"},
        {"enabled": True, "expired_prompt_action": "explode"},
        ["not", "a", "dict"],
    ],
)
def test_a_malformed_toggle_never_raises(clock, stored):
    from autorun.cache_guard import _TOGGLE_KEY, cache_command
    from autorun.session_manager import session_state

    session = Session("claude", EXPIRED_1H)
    with session_state(session.id) as st:
        st[_TOGGLE_KEY] = stored
    session.prompt("Continue with the refactor.")
    assert "ar:cache" in cache_command("status", session.id)
    assert "enabled" in cache_command("off 1h", session.id) or "disabled" in cache_command("off 1h", session.id)


def test_on_and_status_name_the_four_tests_that_have_data(clock):
    from autorun.cache_guard import cache_command

    sid = f"k9-{uuid.uuid4().hex[:8]}"
    reply = cache_command("on", sid)
    assert "prompt-time expiry check only" in reply, reply
    cache_command("set ratio 0.3", sid)
    assert "prompt-time expiry check only" not in cache_command("on", sid)
    status = cache_command("status", sid)
    assert "prompt-time expiry check: block" in status and "ratio floor" in status, status
    for retired in ("full", "rate-limit", "window ceiling"):
        assert retired not in status, status


# ─── the context-size floor ───────────────────────────────────────────────


def test_expired_min_tokens_sets_the_smallest_context_checked(clock):
    small = armed(SMALL_EXPIRED_1H)
    reply = small.command("ar:cache set expired-min-tokens 0")
    assert "expired-min-tokens" in text_of(reply) and "unknown" not in text_of(reply), reply
    assert blocked(small.prompt("Continue with the refactor.")), "a floor of 0 did not check a 20k context"

    large = armed(EXPIRED_1H)
    large.command("ar:cache set expired-min-tokens 300k")
    assert not blocked(large.prompt("Continue with the refactor.")), "a 300k floor still checked a 250k context"

    unknown = Session("claude").command("ar:cache set no-such-key 5")
    assert "expired-min-tokens" in text_of(unknown), f"the unknown-key reply does not list the valid keys: {unknown}"


def test_the_expiry_test_reads_the_written_lifetime_and_the_floor(clock, tmp_path):
    from autorun.cache_guard import CacheGuardConfig, _expired_trip, _usage_from_assistant, cache_command

    def reading(written: dict, age: float, total: int = 250_000):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(NOW - age))
        usage = {"input_tokens": 3, "cache_read_input_tokens": total - 1003, "cache_creation_input_tokens": 1000}
        if written is not None:
            usage["cache_creation"] = written
        return _usage_from_assistant({"timestamp": stamp}, usage, cli="claude")

    one_hour = {"ephemeral_1h_input_tokens": 1000, "ephemeral_5m_input_tokens": 0}
    five_min = {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 1000}
    both = {"ephemeral_1h_input_tokens": 500, "ephemeral_5m_input_tokens": 500}
    assert [reading(w, 0).cache_lifetime_seconds for w in (one_hour, five_min, both, None)] == [3600, 300, 3600, None]

    default = CacheGuardConfig()
    assert not _expired_trip(reading(one_hour, 3599), default)
    assert _expired_trip(reading(one_hour, 3601), default)
    assert _expired_trip(reading(five_min, 301), default)
    assert not _expired_trip(reading(one_hour, 7200, total=99_999), default), "below the 100k default floor"
    assert not _expired_trip(reading(None, 7200), default), "no lifetime and no age set"
    assert _expired_trip(reading(None, 7200), CacheGuardConfig(cache_age_max_seconds=3600))
    assert not _expired_trip(None, default)

    sid = f"k3-{uuid.uuid4().hex[:8]}"
    for value, stored in (("0", 0), ("200k", 200_000)):
        reply = cache_command(f"set expired-min-tokens {value}", sid)
        assert "unknown" not in reply and CacheGuardConfig.load(sid).expired_min_tokens == stored, reply
    assert "cannot parse" in cache_command("set expired-min-tokens lots", sid)


# ─── Pi and Prime Agent: no lifetime, so only a user-set age speaks ───────


@pytest.mark.parametrize("cli_type", ["pi", "prime"])
def test_pi_record_without_lifetime_warns_only_when_age_is_set(clock, cli_type):
    silent = armed(PI_NO_LIFETIME, cli_type).prompt("Continue with the refactor.")
    assert EXPIRED_NOTICE not in text_of(silent), f"spoke with no lifetime and no age: {silent}"

    session = armed(PI_NO_LIFETIME, cli_type)
    session.command("ar:cache set age 1h")
    response = session.prompt("Continue with the refactor.")
    assert not blocked(response), f"{cli_type} cannot block a prompt, but the response blocks: {response}"
    assert EXPIRED_NOTICE in text_of(response), f"{cli_type} with age 1h and a 2h-old record said nothing: {response}"


@pytest.mark.parametrize("cli_type", sorted(p.name for p in hook_platforms()))
def test_where_a_prompt_cannot_be_blocked_the_check_warns_in_block_mode(clock, cli_type):
    """Pi and Prime Agent show a message and cannot stop the prompt, as do
    Gemini CLI and Qwen. There `/ar:cache on` warns, and never claims the
    prompt was held back or offers to resend it."""
    can_block = "UserPromptSubmit" in PLATFORMS[cli_type].hook_protocol.context_events_with_block_decision
    session = armed(PI_NO_LIFETIME, cli_type, mode="on")
    session.command("ar:cache set age 1h")
    response = session.prompt("Continue with the refactor.")
    text = text_of(response)
    assert EXPIRED_NOTICE in text, f"{cli_type} said nothing: {response}"
    assert blocked(response) == can_block, f"{cli_type}: {response}"
    assert (RESEND_LINE in text) == can_block, f"{cli_type} offers a resend it cannot honour: {text}"


# ─── cost bound ───────────────────────────────────────────────────────────


def _transcript_with_garbage_head(path: Path, head_bytes: int = 100 * 1_000_000) -> Path:
    """A file of `head_bytes` zero bytes (no line is JSON) ending in EXPIRED_1H.

    Written sparse: seeking past the end costs no disk time on any runner.
    """
    with open(path, "wb") as handle:
        handle.seek(head_bytes)
        handle.write(b"\n" + EXPIRED_1H.read_bytes())
    return path


def _prompt_context(transcript: Path) -> EventContext:
    session = armed(transcript)
    return EventContext(
        session_id=session.id,
        event="UserPromptSubmit",
        prompt="Continue with the refactor.",
        cli_type="claude",
        transcript_path=str(transcript),
        store=session.store,
    )


def test_the_check_reads_only_the_tail_of_a_100_mb_transcript(clock, tmp_path, monkeypatch):
    """The cost bound as a property: bytes read, not wall-clock time.

    The reader seeks from the end, so 100 MB of unreadable head is never
    read. The wall-clock measurement lives in test_state_store_benchmark.py,
    where timing runs alone.
    """
    from autorun import cache_guard
    from autorun.cache_guard import CacheGuard

    big = _transcript_with_garbage_head(tmp_path / "big.jsonl")
    assert big.stat().st_size >= 100 * 1_000_000

    read_bytes = []
    real_open = open

    class CountingFile:
        def __init__(self, inner):
            self._inner = inner

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._inner.close()
            return False

        def seek(self, *args):
            return self._inner.seek(*args)

        def read(self, *args):
            data = self._inner.read(*args)
            read_bytes.append(len(data))
            return data

    def counting_open(path, mode="r", *args, **kwargs):
        return CountingFile(real_open(path, mode, *args, **kwargs))

    monkeypatch.setattr(cache_guard, "open", counting_open, raising=False)
    ctx = _prompt_context(big)
    result = CacheGuard.from_ctx(ctx).check_prompt_after_expiry(ctx)
    assert blocked(result), f"the 100 MB transcript ending in an expired record did not block: {result}"
    bound = CONFIG["cache_guard_jsonl_scan_bytes"] + CONFIG["cache_guard_jsonl_retry_bytes"]
    assert read_bytes and sum(read_bytes) <= bound, f"read {sum(read_bytes):,} bytes of a 100 MB file (bound {bound:,})"
