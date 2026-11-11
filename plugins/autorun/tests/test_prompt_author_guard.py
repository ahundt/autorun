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
"""Permission-changing commands take effect only when the user typed them.

Every route into command dispatch ends at the UserPromptSubmit branch of
``AutorunApp._dispatch_unbatched``, except the Codex transcript replay, which
calls the handler directly. Both ask one function, ``resolve_prompt_author``,
who wrote the prompt: ``user``, ``subagent`` (``agent_id`` is set),
``model`` or ``automation`` (the bridge's ``prompt_author`` frame field), or
``harness`` (the prompt starts with one of the platform's
``harness_authored_prompt_prefixes``).

The guarded commands (``ok``, ``no``, ``clear`` with and without
``--global``, ``file``, ``cache``, ``task ignore``, ``task pause``) answer any
author but ``user`` with "Only the user can run /ar:ok. Ask them to type it."
as a command reply that carries no decision, and change nothing.

Payload cases go through ``AutorunDaemon.handle_client``, the function that
turns a hook or bridge frame into an ``EventContext``: a field that reaches
the dataclass but not the daemon would pass a unit test and still grant.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from autorun import plugins
from autorun.core import AutorunDaemon, EventContext, ThreadSafeDB, app, format_command_for_cli
from autorun.platforms import PLATFORMS

ALL_PLATFORMS = sorted(PLATFORMS)
REFUSAL_LEAD = "Only the user can run "

# C1: the wrappers Claude Code puts around turns no person typed.
CLAUDE_WRAPPERS = ("<task-notification>", "<agent-message ", "<cross-session-message ")


# ─── helpers ──────────────────────────────────────────────────────────────


class _Writer:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


def send_frame(daemon: AutorunDaemon, payload: dict) -> dict | None:
    """One hook or bridge frame through the daemon's own entry point."""
    reader = Mock()
    reader.readuntil = AsyncMock(return_value=json.dumps(payload).encode() + b"\n")
    writer = _Writer()
    asyncio.run(daemon.handle_client(reader, writer))
    raw = b"".join(writer.writes)
    return json.loads(raw) if raw.strip() else None


def session_allows(daemon: AutorunDaemon, session_id: str, cli_type: str, scope: str = "session") -> list[str]:
    """The patterns the command guard reads for this session (or globally)."""
    ctx = EventContext(session_id=session_id, event="PreToolUse", cli_type=cli_type, store=daemon.store)
    return [entry["pattern"] for entry in plugins.ScopeAccessor(ctx, scope).get_allowed()]


def session_blocks(daemon: AutorunDaemon, session_id: str, cli_type: str) -> list[str]:
    ctx = EventContext(session_id=session_id, event="PreToolUse", cli_type=cli_type, store=daemon.store)
    return [entry["pattern"] for entry in plugins.ScopeAccessor(ctx, "session").get()]


def text_of(response: dict | None) -> str:
    return json.dumps(response or {}, ensure_ascii=False)


def carries_decision(response: dict | None) -> bool:
    """A refusal is a command reply, never a prompt block or a denial."""
    response = response or {}
    hook_output = response.get("hookSpecificOutput") or {}
    return (
        response.get("decision") in {"block", "deny"}
        or hook_output.get("permissionDecision") in {"deny", "block"}
        or response.get("continue") is False
    )


def prompt_frame(cli_type: str, session_id: str, prompt: str, **extra) -> dict:
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "cli_type": cli_type,
        "prompt": prompt,
        **extra,
    }


def _sid(label: str) -> str:
    return f"c8-{label}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def daemon(monkeypatch):
    monkeypatch.delenv("AUTORUN_SESSION_ID", raising=False)
    monkeypatch.delenv("AUTORUN_USE_IDENTITY", raising=False)
    return AutorunDaemon(app)


def _lookup(name: str):
    """A function the plan adds, looked up when the test runs so a missing
    one fails that test alone with its name, not the module's collection."""
    from autorun import core

    for module in (core, plugins):
        if hasattr(module, name):
            return getattr(module, name)
    pytest.fail(f"{name} is not defined in autorun.core or autorun.plugins")


def assert_user_grant_works(daemon, cli_type, prompt="ar:ok 'git push'", **extra):
    """Control: the same text from the user grants, so a refusal below is the
    author rule and not a broken route."""
    session_id = _sid(f"user-{cli_type}")
    response = send_frame(daemon, prompt_frame(cli_type, session_id, prompt, **extra))
    assert session_allows(daemon, session_id, cli_type) == ["git push"], (
        f"control failed: the user's own {prompt!r} granted nothing on {cli_type}: {response}"
    )
    return response


def assert_refused(daemon, cli_type, session_id, response, user_response, command="/ar:ok"):
    assert session_allows(daemon, session_id, cli_type) == [], (
        f"{cli_type}: a prompt not typed by the user granted 'git push': {response}"
    )
    assert not carries_decision(response), f"{cli_type}: the refusal must be a reply, not a block: {response}"
    if "Allowed" in text_of(user_response):
        # The harness shows command replies, so it must show this one.
        expected = REFUSAL_LEAD + format_command_for_cli(command, cli_type)
        assert expected in text_of(response), f"{cli_type}: reply does not say {expected!r}: {response}"


# ─── C1: who wrote the prompt ─────────────────────────────────────────────


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
def test_platform_declares_its_harness_authored_prompt_prefixes(platform_name):
    platform = PLATFORMS[platform_name]
    assert hasattr(platform, "harness_authored_prompt_prefixes"), (
        "Platform.harness_authored_prompt_prefixes is missing"
    )
    expected = CLAUDE_WRAPPERS if platform_name == "claude" else ()
    assert tuple(platform.harness_authored_prompt_prefixes) == expected


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
def test_resolve_prompt_author_names_each_author(platform_name):
    resolve_prompt_author = _lookup("resolve_prompt_author")

    def author(prompt="ar:ok 'git push'", **fields):
        return resolve_prompt_author(
            EventContext(
                session_id=_sid("author"),
                event="UserPromptSubmit",
                prompt=prompt,
                cli_type=platform_name,
                store=ThreadSafeDB(),
                **fields,
            )
        )

    assert author() == "user"
    assert author(prompt_author="user") == "user"
    assert author(agent_id="subagent-1") == "subagent"
    assert author(prompt_author="model") == "model"
    assert author(prompt_author="automation") == "automation"
    # agent_id is checked first: a subagent's turn stays a subagent's turn
    # whatever the bridge reports.
    assert author(agent_id="subagent-1", prompt_author="user") == "subagent"
    for wrapper in getattr(PLATFORMS[platform_name], "harness_authored_prompt_prefixes", ()):
        assert author(prompt=f"{wrapper}ar:ok 'git push'") == "harness", wrapper
    assert author(prompt="<task-notification>") == ("harness" if platform_name == "claude" else "user")


# ─── C2 and C3: the dispatch route refuses everyone but the user ──────────


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
def test_prompt_carrying_agent_id_grants_nothing(daemon, platform_name):
    user_response = assert_user_grant_works(daemon, platform_name)
    session_id = _sid(f"subagent-{platform_name}")
    response = send_frame(
        daemon, prompt_frame(platform_name, session_id, "ar:ok 'git push'", agent_id="subagent-1")
    )
    assert_refused(daemon, platform_name, session_id, response, user_response)


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
@pytest.mark.parametrize("prompt_author", ["model", "automation"])
def test_prompt_author_other_than_user_grants_nothing(daemon, platform_name, prompt_author):
    """``model`` is OpenCode's tool; ``automation`` is Pi's ``rpc`` and
    ``extension`` input sources."""
    user_response = assert_user_grant_works(daemon, platform_name, prompt_author="user")
    session_id = _sid(f"{prompt_author}-{platform_name}")
    response = send_frame(
        daemon, prompt_frame(platform_name, session_id, "ar:ok 'git push'", prompt_author=prompt_author)
    )
    assert_refused(daemon, platform_name, session_id, response, user_response)


GUARDED = (
    "ok 'git push'",
    "no 'git push'",
    "clear",
    "ok --global 'c8-guard-probe'",
    "no --global 'c8-guard-probe'",
    "clear --global",
    "file allow",
    "cache on",
    "task ignore 1",
    "task pause discussing the plan",
)
# Not guarded and safe to run in-process (restart-daemon and reload are left
# out because they act on the process, not on one session).
UNGUARDED = ("status", "help", "task status", "planexport")


def test_model_is_refused_exactly_the_guarded_commands(daemon):
    refused, changed_state, misworded = set(), [], []
    for command in GUARDED + UNGUARDED:
        session_id = _sid("guard-" + command.split()[0])
        # Start strict, so a model `file allow` that slipped through shows.
        send_frame(daemon, prompt_frame("claude", session_id, "/ar:f"))
        response = send_frame(daemon, prompt_frame("claude", session_id, f"/ar:{command}", prompt_author="model"))
        text = text_of(response)
        if REFUSAL_LEAD in text:
            refused.add(command)
            if f"/ar:{command.split()[0]}" not in text or carries_decision(response):
                misworded.append((command, response))
        ctx = EventContext(session_id=session_id, event="UserPromptSubmit", cli_type="claude", store=daemon.store)
        state = (
            session_allows(daemon, session_id, "claude"),
            session_blocks(daemon, session_id, "claude"),
            ctx.file_policy,
            "c8-guard-probe" in session_allows(daemon, session_id, "claude", scope="global"),
        )
        if state != ([], [], "SEARCH", False):
            changed_state.append((command, state))
    assert changed_state == [], f"a model-authored command changed session state (allows, blocks, policy, global): {changed_state}"
    assert refused == set(GUARDED), (
        f"refused but not guarded: {sorted(refused - set(GUARDED))}; "
        f"guarded but not refused: {sorted(set(GUARDED) - refused)}"
    )
    assert misworded == [], f"refusals that do not name the command or carry a decision: {misworded}"


# ─── C5: the Codex transcript replay ──────────────────────────────────────


def _write_codex_rollout(path: Path, prompt: str) -> None:
    entry = {
        "timestamp": "2026-11-11T12:00:00Z",
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]},
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")


def _codex_push(store, session_id, transcript, **fields):
    return app.dispatch(
        EventContext(
            session_id=session_id,
            event="PreToolUse",
            tool_name="Bash",
            tool_input={"command": "git push origin main"},
            cli_type="codex",
            store=store,
            transcript_path=str(transcript),
            **fields,
        )
    )


def test_codex_replayed_record_in_a_subagent_session_grants_nothing(tmp_path):
    store = ThreadSafeDB()
    transcript = tmp_path / "rollout.jsonl"
    _write_codex_rollout(transcript, "ar:ok 'git push'")

    control = _codex_push(store, _sid("codex-main"), transcript)
    assert "Allowed 'git push'" in text_of(control), f"control: the main session's replay did not grant: {control}"

    response = _codex_push(store, _sid("codex-sub"), transcript, agent_id="subagent-1")
    hook_output = (response or {}).get("hookSpecificOutput") or {}
    assert "Allowed 'git push'" not in text_of(response), f"a subagent session's replayed record granted: {response}"
    assert hook_output.get("permissionDecision") == "deny", f"git push was not denied in the subagent session: {response}"


# ─── C6: the model launching a guarded command as a skill ─────────────────


def test_model_skill_launch_of_a_guarded_command_is_denied_with_the_refusal():
    skill_tool = PLATFORMS["claude"].tool_names.get("skill")
    assert skill_tool, "Platform.tool_names has no 'skill' entry for Claude Code"
    store = ThreadSafeDB()

    def launch(skill, args=""):
        return app.dispatch(
            EventContext(
                session_id=_sid("skill"),
                event="PreToolUse",
                tool_name=skill_tool,
                tool_input={"skill": skill, "args": args},
                cli_type="claude",
                store=store,
            )
        )

    denied = launch("ar:ok", "git push")
    hook_output = (denied or {}).get("hookSpecificOutput") or {}
    assert hook_output.get("permissionDecision") == "deny", denied
    assert REFUSAL_LEAD + "/ar:ok" in hook_output.get("permissionDecisionReason", ""), denied

    allowed = launch("ar:status")
    assert ((allowed or {}).get("hookSpecificOutput") or {}).get("permissionDecision") != "deny", allowed
