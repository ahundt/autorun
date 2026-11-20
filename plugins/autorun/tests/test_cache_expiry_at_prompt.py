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

import importlib.util
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
    def __init__(self, cli_type: str = "claude", transcript: Path | None = None, store: ThreadSafeDB | None = None):
        self.id = f"k2-{cli_type}-{uuid.uuid4().hex[:10]}"
        self.cli_type = cli_type
        self.transcript = str(transcript) if transcript else None
        self.store = store or ThreadSafeDB()  # sessions sharing one store share the daemon's cache

    def prompt(self, text: str, **fields) -> dict:
        fields.setdefault("harness_entrypoint", "cli" if self.cli_type == "claude" else None)
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


def test_next_prompt_reads_new_usage_inside_the_memo_window(clock, tmp_path):
    transcript = tmp_path / "same-session.jsonl"
    shutil.copyfile(EXPIRED_1H, transcript)
    session = Session("claude", transcript)
    assert outcome(session.prompt("First prompt.")) == "hold"
    with transcript.open("a") as stream:
        stream.write(FRESH_1H.read_text())
    # The clock has not advanced; the old reading is still within the memo TTL.
    assert outcome(session.prompt("Different prompt after a response.")) == "silent"


@pytest.mark.parametrize(("read", "written", "expected"), [(0, 0, "silent"), (1, 0, "hold"), (0, 1, "hold")])
def test_expiry_requires_cached_tokens(clock, tmp_path, read, written, expected):
    transcript = tmp_path / "usage.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant", "timestamp": clock.now - 2,
        "message": {"usage": {"input_tokens": 250_000, "cache_read_input_tokens": read,
                              "cache_creation_input_tokens": written}},
    }) + "\n")
    session = Session("claude", transcript)
    session.command("ar:cache set age 1s")
    assert outcome(session.prompt("Continue.")) == expected


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
    twin = session.prompt("Continue with the refactor.")  # a second hook for the same prompt sees the same pass
    assert not blocked(twin), f"two hooks for one resent prompt disagreed: {twin}"
    other = session.prompt("Now update the changelog.")
    assert blocked(other), f"the resend pass became a pass for every prompt: {other}"
    next_expiry(session, clock)
    later = session.prompt("Continue with the refactor.")
    assert blocked(later), f"the resend pass outlived its expiry: {later}"


# Every case below drives the one fake clock (`clock`), so a one-hour or ten-day
# duration is a number assigned to `clock.now`, never a wait.
SCOPES = {"session": ("", ""), "global": ("global ", "--global ")}  # (`/ar:cache` word, `/ar:ok` flag)


def next_expiry(session: Session, clock: _FrozenClock) -> None:
    """Move to a later expired cache: another response was written, so this is another expiry."""
    other = EXPIRED_5M if session.transcript == str(EXPIRED_1H) else EXPIRED_1H
    session.transcript = str(other)
    clock.now += 10  # past the few seconds the usage reading is memoised for on this clock


def outcome(response: dict) -> str:
    """What the prompt check did: hold the prompt, tell and send it, or stay silent."""
    if blocked(response):
        return "hold"
    return "notice" if EXPIRED_NOTICE in text_of(response) else "silent"


def observer(scope: str, setter: Session) -> Session:
    """The session whose prompts are checked: the setter, or another one for a global setting."""
    return setter if scope == "session" else Session("claude", EXPIRED_1H)


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize(
    ("command", "during"), [("on", "hold"), ("warn", "notice"), ("off", "silent")], ids=["on", "warn", "off"]
)
@pytest.mark.parametrize(
    ("duration", "after_an_hour", "after_ten_days"),
    [("", None, None), ("1h", "hold", "hold"), ("perm", None, None)],
    ids=["standing", "1h", "perm"],
)
def test_on_warn_off_apply_to_a_session_or_to_every_session_and_timed_ones_lapse(
    clock, scope, command, during, duration, after_an_hour, after_ten_days
):
    """`on|warn|off [5m|1h|perm]`, with or without `global`, as for the other switches.

    A timed setting returns to what was there before (here the default, hold);
    a standing or `perm` one stays.
    """
    setter = Session("claude", EXPIRED_1H)
    setter.command(f"ar:cache {SCOPES[scope][0]}{command} {duration}".strip())
    watched = observer(scope, setter)
    # Each prompt has its own text: resending a held prompt's text is its one-time pass.
    assert outcome(watched.prompt("Now.")) == during
    clock.now = NOW + 3601
    assert outcome(watched.prompt("After an hour.")) == (after_an_hour or during)
    clock.now = NOW + 10 * 86400
    assert outcome(watched.prompt("After ten days.")) == (after_ten_days or during)


def test_a_session_setting_overrides_the_global_one(clock):
    setter = Session("claude", EXPIRED_1H)
    setter.command("ar:cache global off")
    other = Session("claude", EXPIRED_1H)
    assert outcome(other.prompt("Continue.")) == "silent"
    other.command("ar:cache on")
    assert outcome(other.prompt("Continue.")) == "hold"
    assert outcome(setter.prompt("Continue.")) == "silent", "the session setting leaked into another session"


def test_on_warn_off_take_a_duration_or_perm_and_refuse_a_count(clock):
    """A count means uses; a mode switch has none. `ok` is the setting that counts."""
    session = Session("claude", EXPIRED_1H)
    for command in ("on", "warn", "off"):
        reply = session.command(f"ar:cache {command} 3")
        assert "not a count" in text_of(reply), f"{command} accepted a count: {reply}"
    assert outcome(session.prompt("Continue.")) == "hold", "a refused count still changed the setting"


# `/ar:ok cache [words]` and `/ar:cache ok [words]` are one grant, spelled six ways.
OK_SPELLINGS = [
    ("session", "ar:ok cache {w}"),
    ("session", "ar ok cache {w}"),
    ("session", "ar:ok {w} cache"),
    ("session", "ar:cache ok {w}"),
    ("global", "ar:ok --global cache {w}"),
    ("global", "ar ok --global cache {w}"),
    ("global", "ar:cache global ok {w}"),
    ("global", "ar:cache --global ok {w}"),
]


def run_prompts(session: Session, how_many: int, start: int = 0) -> list[str]:
    return [outcome(session.prompt(f"Prompt {start + i}.")) for i in range(how_many)]


@pytest.mark.parametrize(("scope", "spelling"), OK_SPELLINGS, ids=[f"{s}:{t}" for s, t in OK_SPELLINGS])
@pytest.mark.parametrize(
    ("words", "plan"),
    [
        ("", [("pass", 1), ("hold", 1)]),
        ("1", [("pass", 1), ("hold", 1)]),
        ("2", [("pass", 2), ("hold", 1)]),
        ("5m", [("pass", 3), ("at", 299), ("pass", 1), ("at", 302), ("hold", 1)]),
        ("2 5m", [("pass", 2), ("hold", 1)]),
        ("perm", [("pass", 3), ("at", 10 * 86400), ("pass", 1)]),
    ],
    ids=["default", "1", "2", "5m", "2+5m", "perm"],
)
def test_ok_for_the_cache_follows_the_count_and_time_rules_of_ar_ok(clock, scope, spelling, words, plan):
    """Count, duration, both, and `perm`, in every spelling, per session and global."""
    if scope == "session" and "{w} cache" in spelling and not words:
        pytest.skip("the scope-before-pattern spelling needs a scope word")
    setter = Session("claude", EXPIRED_1H)
    setter.command(spelling.format(w=words).replace("  ", " ").strip())
    watched = observer(scope, setter)
    seen = 0
    for step, amount in plan:
        if step == "at":
            clock.now = NOW + amount
            continue
        got = run_prompts(watched, amount, start=seen)
        seen += amount
        assert got == [step if step == "hold" else "silent"] * amount, f"{words!r} via {spelling!r}: {step} x{amount} gave {got}"


@pytest.mark.parametrize("words", ["0", "-1", "forever", "5x"])
def test_ok_for_the_cache_rejects_a_bad_count_or_duration_and_grants_nothing(clock, words):
    session = Session("claude", EXPIRED_1H)
    reply = session.command(f"ar:ok cache {words}")
    assert "granted" not in text_of(reply), f"{words!r} was accepted: {reply}"
    assert outcome(session.prompt("Continue.")) == "hold", f"{words!r} granted a pass anyway"


def test_a_quoted_cache_is_an_ordinary_allow_pattern_and_global_is_accepted(clock):
    session = armed(EXPIRED_1H)
    reply = session.command("ar:ok 'cache'")
    assert "Allowed" in text_of(reply) and "override granted" not in text_of(reply), reply
    assert blocked(session.prompt("Continue with the refactor.")), "a quoted 'cache' passed the cache hold"

    from autorun.cache_guard import cache_command

    assert "override granted" in cache_command("--global ok", f"k5-{uuid.uuid4().hex[:8]}")


def test_a_resend_and_an_ok_for_the_same_hold_spend_both_passes(clock):
    session = armed(EXPIRED_1H)
    assert outcome(session.prompt("continue")) == "hold"
    session.command("ar:cache ok")
    assert outcome(session.prompt("continue")) == "silent"  # resent: passes, and spends the ok too
    assert outcome(session.prompt("totally different")) == "hold", "an unspent ok passed the next expired cache"


def test_a_hold_that_was_answered_with_other_text_does_not_pass_its_text_later(clock):
    session = armed(EXPIRED_1H)
    assert outcome(session.prompt("continue")) == "hold"
    session.command("ar:cache ok")
    assert outcome(session.prompt("something else")) == "silent"  # the ok passes this one
    next_expiry(session, clock)
    assert outcome(session.prompt("continue")) == "hold", "the old hold's text passed unheld"


def test_a_later_expiry_holds_the_same_text_again_even_without_a_prompt_between(clock):
    session = armed(EXPIRED_1H)
    assert outcome(session.prompt("continue")) == "hold"
    assert outcome(session.prompt("continue")) == "silent"  # resent: passes this expiry
    next_expiry(session, clock)  # a response, then the cache expires again with no prompt in between
    assert outcome(session.prompt("continue")) == "hold", "a hold from an earlier expiry passed"


@pytest.mark.parametrize(
    ("setter", "during"),
    [("ar:cache global off", "silent"), ("ar:cache --global off", "silent"), ("ar:ok --global cache", "silent"),
     ("ar:cache global warn", "notice"), ("ar:cache --global warn", "notice")],
)
def test_a_global_setting_reaches_a_session_the_daemon_already_cached(clock, setter, during):
    """Sessions sharing one store, as in the daemon: a stale cached read must not hide the change."""
    shared = ThreadSafeDB()
    first, second = Session("claude", EXPIRED_1H, shared), Session("claude", EXPIRED_1H, shared)
    assert outcome(first.prompt("warm one")) == "hold" and outcome(second.prompt("warm two")) == "hold"
    first.command(setter)
    assert outcome(second.prompt("after the change")) == during, f"{setter!r} did not reach the other session"


@pytest.mark.parametrize("spelling", ["ar:ok cache", "ar ok cache", "ar:cache ok"])
def test_every_ok_spelling_passes_one_prompt_and_the_next_is_held(clock, spelling):
    session = armed(EXPIRED_1H)
    assert blocked(session.prompt("Continue with the refactor.")), "control: the expired cache did not block"
    session.command(spelling)
    assert not blocked(session.prompt("Now update the changelog.")), f"{spelling!r} did not pass the next prompt"
    assert blocked(session.prompt("Then run the tests.")), f"{spelling!r} passed more than one prompt"


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

    # Warn mode records nothing; a held prompt (the default) stores one digest
    # so that resending its text passes.
    session = Session(cli_type, EXPIRED_1H)
    session.command("ar:cache warn")
    monkeypatch.setattr(store, "_connect", connect)
    response = session.prompt("Continue with the refactor.")
    assert EXPIRED_NOTICE in text_of(response)
    writes = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))
    ]
    assert writes == []


def test_default_check_on_a_live_cache_executes_zero_sql_writes(clock, monkeypatch):
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
    response = Session("claude", FRESH_1H).prompt("Continue with the refactor.")  # nothing stored, cache live
    assert outcome(response) == "silent", response
    writes = [s for s in statements if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE"))]
    assert writes == [], f"an ordinary prompt on a live cache wrote state: {writes}"


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


def test_an_expired_prompt_is_held_when_the_user_has_stored_nothing(clock):
    """A notice recorded before the request goes out cannot stop it, so the
    default holds the prompt (2026-11-18: 741K tokens re-written 341 ms after a notice)."""
    assert CONFIG["cache_guard_expired_prompt_action"] == "block"
    session = Session("claude", EXPIRED_1H)  # no /ar:cache command, here or globally
    response = session.prompt("Continue with the refactor.")
    assert blocked(response), f"an expired 250k-token cache was not held by default: {response}"
    assert RESEND_LINE in text_of(response), response
    assert not blocked(session.prompt("Continue with the refactor.")), "resending the held text was blocked again"


@pytest.mark.parametrize(
    ("cli_type", "entrypoint", "setting", "expected"),
    [
        ("claude", "cli", None, "hold"),
        ("claude", "claude-desktop", None, "hold"),
        ("claude", "t3-code", None, "notice"),
        ("claude", None, None, "notice"),
        ("claude", "t3-code", "on", "hold"),
        ("claude", "t3-code", "global on", "hold"),
        ("codex", "cli", None, "notice"),
        ("pi", "cli", None, "notice"),
    ],
)
def test_default_hold_follows_the_forwarded_entrypoint(clock, monkeypatch, capsys, cli_type, entrypoint, setting, expected):
    from autorun.__main__ import run_direct

    path = FIXTURES.parents[2] / "hooks" / "hook_entry.py"
    spec = importlib.util.spec_from_file_location("hook_entry_cache_expiry", path)
    assert spec and spec.loader
    hook_entry = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, hook_entry)
    spec.loader.exec_module(hook_entry)
    monkeypatch.setattr(hook_entry, "explicit_event_name", lambda: None)
    env_name = "CLAUDE_CODE_ENTRYPOINT"
    if entrypoint is None:
        monkeypatch.delenv(env_name, raising=False)
    else:
        monkeypatch.setenv(env_name, entrypoint)

    transcript = PI_NO_LIFETIME if cli_type == "pi" else EXPIRED_1H
    payload = {
        "session_id": f"cache-entrypoint-{uuid.uuid4().hex}",
        "hook_event_name": "UserPromptSubmit",
        "transcript_path": str(transcript),
        "prompt": "Continue with the refactor.",
    }
    prepared = hook_entry.prepare_hook_payload(payload, cli_type)
    # Only the prepared frame, not the receiving process's environment, is evidence.
    monkeypatch.delenv(env_name, raising=False)
    if setting:
        run_direct({**prepared, "prompt": f"/ar:cache {setting}"})
        assert "enabled" in capsys.readouterr().out
    run_direct(prepared)
    response = json.loads(capsys.readouterr().out)
    assert outcome(response) == expected, response
    if cli_type == "claude" and expected == "notice":
        assert "/ar:cache on" in text_of(response), response


@pytest.mark.parametrize("cli_type", sorted(p.name for p in hook_platforms()))
def test_the_message_names_how_to_turn_it_off_in_the_harnesss_spelling(clock, cli_type):
    from autorun.platforms import command_display_prefix

    session = armed(PI_NO_LIFETIME, cli_type)
    session.command("ar:cache set age 1h")
    text = text_of(session.prompt("Continue with the refactor."))
    prefix = command_display_prefix(cli_type)
    for command in ("cache off", "cache global off"):
        assert f"{prefix}{command}" in text, f"{cli_type}: no {prefix}{command} in {text}"
    assert prefix == "/ar:" or "/ar:" not in text, f"{cli_type} shows the canonical spelling: {text}"
    assert ("/clear" in text) == (cli_type == "claude"), text
    assert "start a new session" in text.lower() and "combine requests" in text, text
    assert "Stop the check in every session:" not in text, text
    assert "prompt caching stays on" in text, f"{cli_type}: 'off' reads as disabling caching: {text}"


def test_the_hold_message_puts_each_command_alone_on_a_line_to_copy(clock):
    reason = Session("claude", EXPIRED_1H).prompt("Continue with the refactor.")["reason"]
    lines = [line.strip() for line in reason.splitlines()]
    for command in ("/ar:cache ok", "/ar:cache off", "/ar:cache global off", "/ar:cache warn", "/clear",
                    "/ar:cache set expired-min-tokens 200k"):
        assert command in lines, f"{command!r} is not alone on a line: {reason}"
    assert next(i for i, line in enumerate(lines) if RESEND_LINE in line) < lines.index("/ar:cache ok"), reason
    assert len(lines) <= 20, f"the hold message grew to {len(lines)} lines: {reason}"


@pytest.mark.parametrize("scope", ["", "global "])
def test_status_does_not_label_explicit_off_as_default(clock, scope):
    session = Session("claude", EXPIRED_1H)
    session.command(f"ar:cache {scope}off")
    status = text_of(session.command(f"ar:cache {scope}status"))
    assert "disabled" in status and "prompt-time expiry check: off" in status, status
    assert "(default)" not in status, status


def test_a_command_typed_while_expired_gets_through(clock):
    session = armed(EXPIRED_1H)
    assert blocked(session.prompt("Continue with the refactor.")), "control: the expired cache did not block"
    reply = session.command("ar:cache off")
    assert "disabled" in text_of(reply), f"/ar:cache off did not run: {reply}"
    assert "prompt caching stays on" in text_of(reply), f"/ar:cache off reads as disabling caching: {reply}"
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
    assert _expired_trip(reading(None, 7200), default), "no lifetime and no age set: the 300 s default applies"
    assert not _expired_trip(reading(None, 200), default)
    assert not _expired_trip(reading(None, 1800), CacheGuardConfig(cache_age_max_seconds=3600)), "the user's age beats the default"
    assert _expired_trip(reading(None, 7200), CacheGuardConfig(cache_age_max_seconds=3600))
    assert not _expired_trip(reading(one_hour, 400), default), "a recorded 1 h lifetime beats the 300 s default"
    assert not _expired_trip(None, default)

    sid = f"k3-{uuid.uuid4().hex[:8]}"
    for value, stored in (("0", 0), ("200k", 200_000)):
        reply = cache_command(f"set expired-min-tokens {value}", sid)
        assert "unknown" not in reply and CacheGuardConfig.load(sid).expired_min_tokens == stored, reply
    assert "cannot parse" in cache_command("set expired-min-tokens lots", sid)


# ─── Pi and Prime Agent: no lifetime, so only a user-set age speaks ───────


@pytest.mark.parametrize("cli_type", ["pi", "prime"])
def test_pi_record_without_lifetime_guesses_five_minutes_until_age_is_set(clock, cli_type):
    guess = armed(PI_NO_LIFETIME, cli_type).prompt("Continue with the refactor.")
    assert not blocked(guess), guess
    assert "assuming a 5 m lifetime" in text_of(guess), f"{cli_type}: a guessed lifetime is not labelled: {guess}"
    assert "cache set age 1h" in text_of(guess), "the guess does not say how to set the real lifetime"

    session = armed(PI_NO_LIFETIME, cli_type)
    session.command("ar:cache set age 1h")
    response = session.prompt("Continue with the refactor.")
    assert not blocked(response), f"{cli_type} cannot block a prompt, but the response blocks: {response}"
    assert EXPIRED_NOTICE in text_of(response) and "assuming" not in text_of(response), response  # a set age is not a guess

    young = armed(PI_NO_LIFETIME, cli_type)
    young.command("ar:cache set age 3h")
    assert outcome(young.prompt("Continue with the refactor.")) == "silent", "the 2 h-old record is inside a 3 h age"


def test_a_guessed_lifetime_never_holds_a_prompt_even_where_holding_works(clock, tmp_path):
    """Claude can hold a prompt, but a row with no written lifetime is no evidence of expiry."""
    stripped = tmp_path / "claude_no_lifetime.jsonl"
    rows = []
    for line in EXPIRED_1H.read_text().splitlines():
        row = json.loads(line)
        ((row.get("message") or {}).get("usage") or {}).pop("cache_creation", None)
        rows.append(json.dumps(row))
    stripped.write_text("\n".join(rows) + "\n")
    session = armed(stripped)
    response = session.prompt("Continue with the refactor.")
    assert outcome(response) == "notice" and "assuming a 5 m lifetime" in text_of(response), response
    assert not blocked(session.prompt("Continue with the refactor.")), "a guess held a prompt"


def test_the_default_lifetime_guess_can_be_switched_off(clock, monkeypatch):
    from autorun.config import CONFIG

    monkeypatch.setitem(CONFIG, "cache_guard_default_lifetime_seconds", 0)
    assert EXPIRED_NOTICE not in text_of(armed(PI_NO_LIFETIME, "pi").prompt("Continue with the refactor."))


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
