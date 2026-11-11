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
"""One advertised name per command; every old name still reaches it.

The command surface is ``ok``, ``no``, ``clear`` (each taking ``--global``),
``status``, ``go``, ``goprocess``, ``stop``, ``estop``, ``file`` (with
``allow``, ``justify`` or ``existing-only``), ``planexport``, ``task``,
``tasks``, ``cache``, ``help``, ``reload`` and ``restart-daemon``, plus the
four plan skills. A name autorun used to advertise keeps dispatching through
``core._RETIRED_COMMAND_SPELLINGS`` ("input tolerance only"), so the user who
remembers ``/ar:st`` gets ``/ar:status``, but no text autorun prints or ships
names the old spelling again.

Four properties, each stated as the violation it forbids:

(a) an old name that reaches a different handler, or reaches the same handler
    with a different command line, than its advertised form;
(b) an unprefixed pre-2026 spelling (``/afs``, ``/autorun`` ...) that still
    matches a handler;
(c) a retired or deleted spelling inside runtime text or a shipped document;
(d) ``autorun file <mode>`` failing, or telling the user to type a retired
    slash command.
"""

from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path

import pytest

from autorun import plugins as _plugins  # noqa: F401  (registers command handlers)
from autorun.config import CONFIG
from autorun.core import EventContext, ThreadSafeDB, app, command_display_prefix
from autorun.platforms import PLATFORMS

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SRC = PLUGIN_ROOT / "src" / "autorun"
ALL_PLATFORMS = sorted(PLATFORMS)

# Section 10's last column: each old name and the advertised command line it
# now stands for. A value with a space carries its arguments, which is how
# the retired-spellings table already maps "task-status" to "task status".
OLD_TO_NEW = {
    "st": "status",
    "afst": "status",
    "blocks": "status",
    "globalstatus": "status",
    "a": "file allow",
    "allow": "file allow",
    "afa": "file allow",
    "j": "file justify",
    "justify": "file justify",
    "afj": "file justify",
    "f": "file existing-only",
    "find": "file existing-only",
    "afs": "file existing-only",
    "run": "go",
    "autorun": "go",
    "gp": "goprocess",
    "proc": "goprocess",
    "autoproc": "goprocess",
    "x": "stop",
    "sos": "estop",
    "pn": "plannew",
    "pr": "planrefine",
    "pu": "planupdate",
    "pp": "planprocess",
    "pe": "planexport",
    "globalok": "ok --global",
    "globalno": "no --global",
    "globalclear": "clear --global",
}

# Pattern commands are typed with a pattern; the tail must survive the
# translation unchanged, or `/ar:globalok 'git push'` grants something else.
TAIL = {"globalok": "'git push'", "globalno": "'git push'"}

# Deleted outright (section 10): documents with no handler. `tmux` is left
# out on purpose: section 10.1 gives that name to the tmux skill workstream H
# adds, so forbidding it here would fail the moment H lands.
DELETED = ("gc", "ph", "tm", "tt", "ttest", "test", "gemini", "claude-code-plugin-help")

UNPREFIXED = ("/afs", "/afa", "/afj", "/afst", "/autorun", "/autoproc", "/autostop", "/estop")

_RETIRED_NAMES = "|".join(sorted((re.escape(n) for n in (*OLD_TO_NEW, *DELETED)), key=len, reverse=True))
# Any display spelling of an /ar command: /ar:x (Claude), ar:x (Codex),
# /ar-x and ar-x (ForgeCode, OpenCode, their file names), /ar x (Pi). The
# look-arounds keep /ar:file from matching f and /ar:status from matching st.
_PREFIXED_RE = re.compile(rf"(?<![\w/.~-])(?:/?ar[:-]|/ar )({_RETIRED_NAMES})(?![\w-])")
_UNPREFIXED_RE = re.compile(
    r"(?<![\w./~:-])(" + "|".join(re.escape(n) for n in UNPREFIXED) + r")(?![\w-])"
)

SKILLS_E2_EDITS = ("plannew", "planrefine", "planupdate", "planprocess", "commit", "philosophy")


def _retired_mentions(text: str) -> list[str]:
    return sorted({m.group(0) for m in _PREFIXED_RE.finditer(text)} | {m.group(0) for m in _UNPREFIXED_RE.finditer(text)})


def _match(prompt: str, platform_name: str):
    return app._find_command(prompt, platform_name)


def _typed_prefix(platform_name: str) -> str:
    """The spelling this harness teaches, as it reaches the dispatcher.

    Pi and Prime Agent display ``/ar `` (their registered command), and the
    bridge rewrites it to ``ar:`` before the daemon sees it
    (``daemon-client.mjs`` ``runCommandResponse``); every other harness hands
    its display spelling through unchanged.
    """
    display = command_display_prefix(platform_name)
    return display if display in PLATFORMS[platform_name].command_prefixes else "ar:"


# ─── (a) old names reach the advertised command ───────────────────────────


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
@pytest.mark.parametrize("old", sorted(OLD_TO_NEW))
def test_old_name_reaches_the_same_command_line_as_its_new_form(platform_name, old):
    prefix = _typed_prefix(platform_name)
    tail = TAIL.get(old, "")
    new_prompt = " ".join(filter(None, [f"/ar:{OLD_TO_NEW[old]}", tail]))
    old_prompt = " ".join(filter(None, [f"{prefix}{old}", tail]))

    new = _match(new_prompt, platform_name)
    assert new is not None, f"the advertised form {new_prompt!r} does not dispatch on {platform_name}"
    old_match = _match(old_prompt, platform_name)
    assert old_match is not None, f"{old_prompt!r} stopped dispatching on {platform_name}"
    assert old_match.handler is new.handler, (
        f"{old_prompt!r} reaches {old_match.handler.__name__}, but {new_prompt!r} "
        f"reaches {new.handler.__name__}"
    )
    assert (old_match.activation_prompt, old_match.arguments) == (new.activation_prompt, new.arguments), (
        f"{old_prompt!r} reaches the right handler with a different command line: "
        f"{old_match.activation_prompt!r} / {old_match.arguments!r}, expected "
        f"{new.activation_prompt!r} / {new.arguments!r}"
    )


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
def test_file_existing_only_typed_on_each_harness_sets_the_policy_the_write_gate_reads(platform_name):
    """Entry point: dispatch, then read back the session's file policy."""
    store = ThreadSafeDB()
    session_id = f"file-existing-only-{platform_name}-{uuid.uuid4().hex[:8]}"
    prompt = f"{_typed_prefix(platform_name)}file existing-only"
    app.dispatch(
        EventContext(
            session_id=session_id,
            event="UserPromptSubmit",
            prompt=prompt,
            cli_type=platform_name,
            store=store,
        )
    )
    policy = EventContext(session_id=session_id, event="UserPromptSubmit", cli_type=platform_name, store=store).file_policy
    assert policy == "SEARCH", f"{prompt!r} on {platform_name} left the file policy at {policy!r}"


# ─── (b) unprefixed pre-2026 names are gone ───────────────────────────────


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
@pytest.mark.parametrize("spelling", UNPREFIXED)
def test_unprefixed_pre_2026_name_matches_no_handler(platform_name, spelling):
    match = _match(spelling, platform_name)
    assert match is None, f"{spelling!r} still dispatches to {match.handler.__name__} on {platform_name}"


# ─── (c) nothing autorun prints or ships names a retired spelling ─────────


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(key)
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _walk_strings(item)


def test_config_names_no_retired_spelling():
    found = sorted({hit for text in _walk_strings(CONFIG) for hit in _retired_mentions(text)})
    assert found == [], f"CONFIG still names retired spellings: {found}"


@pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
@pytest.mark.parametrize("reminders_disabled", [False, True])
def test_stop_gate_task_actions_name_no_retired_spelling(platform_name, reminders_disabled):
    from autorun.task_lifecycle import _task_actions_fragment

    text = _task_actions_fragment(platform_name, staleness_reminders_disabled=reminders_disabled)
    assert _retired_mentions(text) == [], f"{platform_name} stop-gate message: {text}"


def test_plan_export_names_no_retired_spelling():
    text = (SRC / "plan_export.py").read_text(encoding="utf-8")
    assert _retired_mentions(text) == []


def _all_help(parser: argparse.ArgumentParser) -> str:
    parts = [parser.format_help()]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in dict.fromkeys(action.choices.values()):
                parts.append(_all_help(sub))
    return "\n".join(parts)


def test_cli_help_names_no_retired_spelling():
    from autorun.__main__ import create_parser

    assert _retired_mentions(_all_help(create_parser())) == []


def _shipped_files() -> list[Path]:
    files = [p for p in (PLUGIN_ROOT / "commands").rglob("*") if p.is_file()]
    for template in sorted(SRC.glob("*_template")):
        files += [p for p in template.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    # The hook manifests and entry script print and match the same names the
    # Gemini template copies (E2 and E7 edit both).
    files += [p for p in (PLUGIN_ROOT / "hooks").iterdir() if p.is_file()]
    files.append(PLUGIN_ROOT / ".codex-plugin" / "skills" / "ar" / "SKILL.md")
    files += [PLUGIN_ROOT / "skills" / name / "SKILL.md" for name in SKILLS_E2_EDITS]
    return files


def test_no_shipped_file_names_a_retired_spelling():
    """Command documents, harness templates (contents and file names), hook
    files, the Codex skill and the six skill bodies E2 edits."""
    offenders = {}
    for path in _shipped_files():
        relative = path.relative_to(PLUGIN_ROOT).as_posix()
        found = set(_retired_mentions(relative)) | set(
            _retired_mentions(path.read_text(encoding="utf-8", errors="replace"))
        )
        if found:
            offenders[relative] = sorted(found)
    assert offenders == {}, "shipped files name retired spellings:\n" + "\n".join(
        f"  {path}: {names}" for path, names in sorted(offenders.items())
    )


# ─── (d) the command line keeps working without the retired mappings ──────


class TestFileCommandLine:
    @pytest.fixture(autouse=True)
    def _direct_mode(self, monkeypatch):
        from autorun import __main__ as entry

        for name in ("CLAUDE_SESSION_ID", "AUTORUN_SESSION_ID", "CODEX_THREAD_ID", "GEMINI_SESSION_ID"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("AUTORUN_CLI_TYPE", "claude")
        monkeypatch.setattr(entry, "USE_DAEMON", False)
        self.entry = entry

    def test_file_allow_with_session_exits_zero_and_names_the_advertised_command(self, capsys):
        session = f"file-cli-{uuid.uuid4().hex[:8]}"
        assert self.entry.main(["file", "allow", "--session", session]) == 0
        out = capsys.readouterr().out
        assert "/ar:file allow" in out, f"slash equivalent is not the advertised name:\n{out}"
        assert _retired_mentions(out) == [], out

    @pytest.mark.parametrize("word", ["existing-only", "search"])
    def test_file_existing_only_and_its_alias_set_the_strict_policy(self, word, capsys):
        session = f"file-cli-{uuid.uuid4().hex[:8]}"
        assert self.entry.main(["file", word, "--session", session]) == 0
        out = capsys.readouterr().out
        assert "strict-search" in out, out
        assert "/ar:file existing-only" in out, f"`file {word}` does not name /ar:file existing-only:\n{out}"

    def test_daemon_mode_refusal_names_the_advertised_command(self, monkeypatch, capsys):
        monkeypatch.setattr(self.entry, "USE_DAEMON", True)
        session = f"file-cli-{uuid.uuid4().hex[:8]}"
        assert self.entry.main(["file", "justify", "--session", session]) == 1
        out = capsys.readouterr().out
        assert "/ar:file justify" in out, out
        assert _retired_mentions(out) == [], out
