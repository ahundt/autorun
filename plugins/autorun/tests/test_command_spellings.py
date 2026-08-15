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
"""The user-facing autorun command spelling contract, in three layers.

1. What you are taught: each harness shows only its own native spelling.
   Claude autocompletes ``/ar:st``; Codex messages say ``ar:st``; ForgeCode and
   OpenCode command files advertise ``/ar-st``. One datum encodes that —
   ``Platform.command_display_prefix``.
2. What you can type: every harness accepts the same superset, so a spelling
   carried over from another harness still works wherever the harness lets the
   text reach autorun. Retired spellings that once dispatched keep dispatching.
3. What cannot work: a prompt that is not an autorun command passes through
   untouched, no matter which prefix it happens to start with.

These tests own that contract. Platform metadata pins live beside their
platform suites (``test_codex_platform.py``); dispatch behavior lives here.
"""

import json

import pytest

from autorun.core import (
    app,
    canonicalize_command_prompt,
    command_display_prefix,
    format_commands_for_cli,
)
from autorun.platforms import PLATFORMS
from autorun.transcript_commands import latest_transcript_command

# Importing plugins is what registers the command handlers on `app`. Without it
# every _find_command lookup returns None and the pass-through assertions below
# would pass against an empty registry.
from autorun import plugins as _plugins  # noqa: F401  (import for registration)

# The shared acceptance superset every platform inherits: slash-colon
# (Claude/Gemini native), bare colon and space (Codex swallows unknown slash
# commands), and the dash form ForgeCode/OpenCode command files advertise.
SUPERSET_PREFIXES = ("/ar:", "ar:", "ar ", "/ar-", "ar-")

ALL_PLATFORMS = sorted(PLATFORMS)

# Display stays native per harness: acceptance is shared, teaching is local.
NATIVE_DISPLAY_PREFIXES = {
    "claude": "/ar:",
    "gemini": "/ar:",
    "qwen": "/ar:",
    "antigravity": "/ar:",
    "codex": "ar:",
    "forgecode": "/ar-",
    "opencode": "/ar-",
    "pi": "/ar ",
    "prime": "/ar ",
}


class TestSpellingSupersetIsSharedByEveryPlatform:
    """One accepted-prefix tuple, inherited everywhere, so dispatch has one
    code path instead of a per-harness prefix policy."""

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    def test_platform_accepts_the_shared_superset(self, platform_name):
        assert PLATFORMS[platform_name].command_prefixes == SUPERSET_PREFIXES

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    @pytest.mark.parametrize("prefix", SUPERSET_PREFIXES)
    def test_every_spelling_dispatches_on_every_platform(self, platform_name, prefix):
        prompt = f"{prefix}st"
        assert app._find_command(prompt, platform_name) is not None, (
            f"{prompt!r} does not dispatch on {platform_name}"
        )

    @pytest.mark.parametrize("prefix", SUPERSET_PREFIXES)
    def test_every_spelling_reaches_the_same_handler_with_subcommand_arguments(self, prefix):
        canonical = app._find_command("/ar:task ignore 7 done", "claude")
        spelled = app._find_command(f"{prefix}task ignore 7 done", "codex")
        assert canonical is not None
        assert spelled is not None, f"{prefix!r} loses the subcommand form"
        assert spelled.handler is canonical.handler

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    def test_unknown_cli_type_still_normalizes_the_superset(self, platform_name):
        """Payloads that arrive before cli_type is known fall back to the union
        of hook-platform prefixes; the superset must survive that path too."""
        assert app._find_command("ar-st") is not None


class TestNonCommandPromptsPassThroughUntouched:
    """Accepting more spellings widens the false-positive surface. A prompt
    that merely starts with ``ar`` is ordinary text: no handler may claim it."""

    # Portuguese "air conditioning is essential", a musician's name, a command
    # that never existed, and prose that starts with the bare word.
    FALSE_POSITIVES = (
        "ar-condicionado é essencial no verão",
        "ar rahman playlist please",
        "ar-taskmaster status",
        "ar:notacommand with arguments",
        "argument parsing is broken",
    )

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    @pytest.mark.parametrize("prompt", FALSE_POSITIVES)
    def test_unmatched_prompts_reach_no_handler(self, platform_name, prompt):
        assert app._find_command(prompt, platform_name) is None, (
            f"{prompt!r} was captured as a command on {platform_name}"
        )

    @pytest.mark.parametrize("prompt", FALSE_POSITIVES)
    def test_pass_through_prompts_keep_their_own_text_when_unprefixed(self, prompt):
        """Canonicalization may rewrite the prefix of a command-shaped prompt,
        but text with no accepted prefix is returned verbatim."""
        if prompt.startswith(("ar-", "ar:", "ar ", "/ar")):
            return
        assert canonicalize_command_prompt(prompt, "claude") == prompt


class TestDisplaySpellingStaysNative:
    """Acceptance is shared; teaching is local. Rendered guidance on a harness
    never shows another harness's spelling."""

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    def test_display_prefix_is_the_harness_native_form(self, platform_name):
        assert command_display_prefix(platform_name) == NATIVE_DISPLAY_PREFIXES[platform_name]

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    def test_rendered_guidance_shows_only_the_local_spelling(self, platform_name):
        native = NATIVE_DISPLAY_PREFIXES[platform_name]
        rendered = format_commands_for_cli("Run /ar:ok git push then /ar:st", platform_name)
        assert rendered == f"Run {native}ok git push then {native}st"
        foreign = {"/ar:", "ar:", "/ar-"} - {native}
        for spelling in foreign:
            if spelling == "ar:" and native == "/ar:":
                continue  # "/ar:" contains "ar:"; the substring is not a foreign form
            assert spelling not in rendered, (
                f"{platform_name} guidance leaked the {spelling!r} spelling: {rendered!r}"
            )


class TestMarkdownCommandHarnessesTeachOnlyWhatTheyShip:
    """ForgeCode and OpenCode have no hook surface: a command exists there only
    because a file with that name was installed. Their memory templates are the
    whole user-facing catalog, so every spelling in one must be the harness's
    own and must name a document the installer actually copies."""

    # `_install_markdown_commands_harness` copies this one shared bundle for
    # every markdown-commands harness (install.py hardcodes forgecode_template).
    PORTABLE_COMMAND_BUNDLE = "forgecode_template/commands"

    @pytest.mark.parametrize("platform_name", ["forgecode", "opencode"])
    def test_memory_template_advertises_only_installed_native_spellings(self, platform_name):
        import re
        from pathlib import Path

        import autorun

        source_root = Path(autorun.__file__).parent
        platform = PLATFORMS[platform_name]
        shipped = {
            path.stem for path in (source_root / self.PORTABLE_COMMAND_BUNDLE).glob("ar-*.md")
        }
        assert shipped, "the portable command bundle is empty"

        text = (source_root / platform.memory_template).read_text(encoding="utf-8")
        advertised = sorted(set(re.findall(r"/ar[:\-][A-Za-z][A-Za-z0-9_-]*", text)))
        assert advertised, f"{platform_name} memory template advertises no commands"

        native = platform.command_display_prefix
        for spelling in advertised:
            assert spelling.startswith(native), (
                f"{platform_name} template teaches {spelling!r}, not its own {native!r} form"
            )
            document = f"ar-{spelling[len(native):]}"
            assert document in shipped, (
                f"{platform_name} template teaches {spelling!r} but no {document}.md is installed"
            )


class TestRetiredSpellingsStillDispatch:
    """``/ar:task-status`` and ``/ar:task-ignore`` were documented spellings
    before the subcommand consolidation. They are no longer advertised anywhere
    — no command document, no catalog entry, no registered alias — but a user
    who remembers yesterday's spelling still gets the command they meant."""

    RETIRED = {"task-status": "task status", "task-ignore": "task ignore"}

    @pytest.mark.parametrize("platform_name", ["claude", "codex", "qwen"])
    @pytest.mark.parametrize("prefix", SUPERSET_PREFIXES)
    @pytest.mark.parametrize("retired", sorted(RETIRED))
    def test_retired_spelling_reaches_the_task_handler(self, platform_name, prefix, retired):
        retired_match = app._find_command(f"{prefix}{retired}", platform_name)
        canonical = app._find_command("/ar:task status", platform_name)
        assert canonical is not None
        assert retired_match is not None, (
            f"{prefix}{retired} does not dispatch on {platform_name}"
        )
        assert retired_match.handler is canonical.handler

    @pytest.mark.parametrize("retired,replacement", sorted(RETIRED.items()))
    def test_retired_spelling_canonicalizes_to_the_subcommand_form(self, retired, replacement):
        assert canonicalize_command_prompt(f"/ar:{retired}", "claude") == f"/ar:{replacement}"

    def test_retired_spelling_keeps_its_arguments(self):
        assert (
            canonicalize_command_prompt("ar-task-ignore 7 superseded", "codex")
            == "/ar:task ignore 7 superseded"
        )

    @pytest.mark.parametrize("prompt", ["/task-status", "/task-ignore"])
    def test_bare_dash_spelling_without_an_ar_prefix_is_not_a_command(self, prompt):
        assert app._find_command(prompt) is None, f"{prompt} still has a handler"

    def test_retired_map_matches_whole_names_only(self):
        assert canonicalize_command_prompt("/ar:task-statuses", "claude") == "/ar:task-statuses"
        assert app._find_command("/ar:task-statuses") is None

    def test_no_command_document_advertises_a_retired_spelling(self):
        """Input tolerance only: the map must never grow a catalog entry back."""
        from pathlib import Path

        commands_dir = Path(__file__).parents[1] / "commands"
        assert sorted(path.name for path in commands_dir.glob("task-*.md")) == []


class TestCodexTranscriptScannerSharesTheSameNormalization:
    """Codex API-backed sessions deliver PreToolUse with a transcript path but
    no UserPromptSubmit, so the transcript scanner is the second consumer of
    the one canonicalizer. It must accept exactly the same spellings."""

    POLICY_COMMANDS = frozenset({"/ar:ok", "/ar:no", "/ar:clear"})

    def _transcript(self, tmp_path, text):
        path = tmp_path / "session.jsonl"
        path.write_text(
            json.dumps({"type": "user_message", "message": text, "timestamp": "2026-08-04T00:00:00Z"})
            + "\n",
            encoding="utf-8",
        )
        return str(path)

    @pytest.mark.parametrize("prefix", SUPERSET_PREFIXES)
    def test_every_spelling_normalizes_identically_in_the_transcript_scan(self, tmp_path, prefix):
        found = latest_transcript_command(
            self._transcript(tmp_path, f"{prefix}ok git push"),
            cli_type="codex",
            command_names=self.POLICY_COMMANDS,
        )
        assert found is not None, f"{prefix!r} was not recovered from the transcript"
        assert found.canonical_prompt == "/ar:ok git push"
        assert found.command == "/ar:ok"

    def test_non_command_transcript_lines_are_ignored(self, tmp_path):
        assert (
            latest_transcript_command(
                self._transcript(tmp_path, "ar-condicionado é essencial no verão"),
                cli_type="codex",
                command_names=self.POLICY_COMMANDS,
            )
            is None
        )
