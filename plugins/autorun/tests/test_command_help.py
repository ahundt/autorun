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
"""`ar:help` — the command list, in the spelling the local harness uses.

Two authorities already exist and this feature adds no third one:

* ``commands/*.md`` frontmatter says what a command does (``description``),
  what it takes (``argument-hint``), and which spellings are the same command
  (``aliases``), read by ``command_docs.py``.
* ``app.command_handlers`` says which spellings actually dispatch, which is
  what separates a control command autorun runs itself from a workflow
  document the model reads.

Help joins them and renders through the display owners in ``core``, so no
harness is ever shown another harness's spelling.
"""

from pathlib import Path

import pytest

from autorun import plugins as _plugins  # noqa: F401  (registers command handlers)
from autorun.command_docs import command_help_inventory, iter_command_docs
from autorun.config import CONFIG
from autorun.core import EventContext, ThreadSafeDB, app
from autorun.platforms import PLATFORMS

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_DIR = PLUGIN_ROOT / "commands"
ALL_PLATFORMS = sorted(PLATFORMS)

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


def _run_help(prompt, cli_type="claude"):
    match = app._find_command(prompt, cli_type)
    assert match is not None, f"{prompt!r} does not dispatch on {cli_type}"
    ctx = EventContext(
        session_id=f"help-{cli_type}-{prompt}",
        event="UserPromptSubmit",
        prompt=prompt,
        cli_type=cli_type,
        store=ThreadSafeDB(),
    )
    ctx.activation_prompt = match.activation_prompt
    return match.handler(ctx)


class TestHelpIsReachableTheWayUsersActuallyAsk:
    """Someone who does not know the commands types the shortest thing they
    can think of. Every one of those lands on the list."""

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    @pytest.mark.parametrize("prefix", ["/ar:", "ar:", "ar ", "/ar-", "ar-"])
    def test_help_dispatches_from_every_spelling(self, platform_name, prefix):
        assert app._find_command(f"{prefix}help", platform_name) is not None

    @pytest.mark.parametrize("prompt", ["ar", "/ar", "ar:"])
    def test_bare_autorun_token_shows_the_list(self, prompt):
        assert "ar:help" in _run_help(prompt).replace("/ar:help", "ar:help")

    @pytest.mark.parametrize("prompt", ["help", "help me refactor this", "ar rahman playlist"])
    def test_ordinary_prose_never_opens_help(self, prompt):
        assert app._find_command(prompt, "claude") is None


class TestTheInventoryHasOneOwnerPerCommand:
    """Every shipped document is either a command or a second spelling of one.
    A document that is neither, or that two commands both claim, means the
    recommended-versus-alias answer has drifted from what ships."""

    def test_every_command_document_is_claimed_exactly_once(self):
        entries = command_help_inventory(COMMANDS_DIR)
        canonical = {entry.name for entry in entries}
        claimed: dict[str, list[str]] = {}
        for entry in entries:
            for alias in entry.aliases:
                claimed.setdefault(alias, []).append(entry.name)

        assert not (canonical & set(claimed)), "a command is also claimed as an alias"
        double_claimed = {name: owners for name, owners in claimed.items() if len(owners) > 1}
        assert double_claimed == {}

        documented = {doc.path.stem for doc in iter_command_docs(COMMANDS_DIR)}
        unclaimed = sorted(documented - canonical - set(claimed))
        assert unclaimed == [], f"documents belonging to no command: {unclaimed}"

    def test_every_declared_alias_ships_its_own_document(self):
        documented = {doc.path.stem for doc in iter_command_docs(COMMANDS_DIR)}
        missing = sorted(
            f"{entry.name} -> {alias}"
            for entry in command_help_inventory(COMMANDS_DIR)
            for alias in entry.aliases
            if alias not in documented
        )
        assert missing == []

    def test_declared_aliases_dispatch_to_their_command_handler(self):
        """For commands autorun runs itself, an advertised second spelling must
        reach the same handler — otherwise help promises a dead spelling."""
        mismatched = []
        for entry in command_help_inventory(COMMANDS_DIR):
            canonical = app._find_command(f"/ar:{entry.name}")
            if canonical is None:
                continue  # workflow document: the model reads it, nothing dispatches
            for alias in entry.aliases:
                spelled = app._find_command(f"/ar:{alias}")
                if spelled is None or spelled.handler is not canonical.handler:
                    mismatched.append(f"/ar:{alias} != /ar:{entry.name}")
        assert mismatched == []

    def test_control_and_workflow_classes_are_both_populated(self):
        entries = command_help_inventory(COMMANDS_DIR)
        control = [entry for entry in entries if entry.dispatches]
        workflow = [entry for entry in entries if not entry.dispatches]
        assert len(control) >= 10, "control commands lost their dispatch classification"
        assert len(workflow) >= 5, "workflow documents lost their classification"

    def test_every_entry_carries_a_description(self):
        assert [entry.name for entry in command_help_inventory(COMMANDS_DIR) if not entry.description] == []


class TestHelpSpeaksEachHarnessOwnLanguage:
    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    def test_output_uses_only_the_native_spelling(self, platform_name):
        native = NATIVE_DISPLAY_PREFIXES[platform_name]
        rendered = _run_help("/ar:help", platform_name)
        for spelling in {"/ar:", "ar:", "/ar-"} - {native}:
            if spelling == "ar:" and native == "/ar:":
                continue  # "/ar:" contains "ar:"; that substring is not a foreign form
            assert spelling not in rendered, f"{platform_name} help leaked {spelling!r}"

    @pytest.mark.parametrize("platform_name", ALL_PLATFORMS)
    def test_output_opens_with_the_local_invocation_rule(self, platform_name):
        """Hints are written once in canonical form and translated on the way
        out, so the display prefix stays the only place a spelling is decided."""
        from autorun.core import format_commands_for_cli

        hint = PLATFORMS[platform_name].command_invocation_hint
        assert hint, f"{platform_name} declares no invocation hint"
        assert format_commands_for_cli(hint, platform_name) in _run_help("/ar:help", platform_name)

    def test_codex_says_slash_commands_do_not_reach_autorun(self):
        """The one difference a Codex user must know before typing anything."""
        assert "slash" in PLATFORMS["codex"].command_invocation_hint.lower()

    def test_long_descriptions_are_shortened_in_the_list_but_whole_on_the_command(self):
        """One command with a paragraph-long description must not push every
        other line off the screen; the full text is one `help <command>` away."""
        entries = command_help_inventory(COMMANDS_DIR)
        longest = max(entries, key=lambda entry: len(entry.description))
        assert len(longest.description) > 120, "no long description left to check"

        listed = _run_help("/ar:help")
        assert longest.description not in listed
        assert max(len(line) for line in listed.splitlines()) <= 160
        assert longest.description in _run_help(f"/ar:help {longest.name}")

    def test_list_names_every_command_and_its_short_spelling(self):
        rendered = _run_help("/ar:help")
        for entry in command_help_inventory(COMMANDS_DIR):
            assert f"/ar:{entry.name}" in rendered, f"{entry.name} missing from help"
        assert "/ar:a" in rendered, "the short spelling of /ar:allow is not advertised"


class TestHelpListsTheSkillsToo:
    """A workflow that becomes an Agent Skill leaves `commands/`, so help has to
    read both surfaces or the conversion makes that workflow undiscoverable."""

    def _skill_names(self):
        from autorun.command_docs import skill_docs_inventory

        return set(skill_docs_inventory(PLUGIN_ROOT / "skills"))

    def test_every_packaged_skill_appears_with_its_description(self):
        from autorun.command_docs import skill_docs_inventory

        rendered = _run_help("/ar:help")
        for name, skill in skill_docs_inventory(PLUGIN_ROOT / "skills").items():
            assert name in rendered, f"{name} skill missing from help"
            assert skill["description"].split(".")[0][:40] in rendered

    @pytest.mark.parametrize(
        "platform_name,expected",
        [("claude", "/philosophy"), ("codex", "$philosophy"), ("forgecode", "the philosophy skill")],
    )
    def test_skills_render_in_the_harness_invocation_form(self, platform_name, expected):
        from autorun.platforms import PLATFORMS

        assert PLATFORMS[platform_name].skill_invocation_format.format(name="philosophy") == expected

    def test_no_description_collapses_to_a_yaml_block_marker(self):
        """`description: |` is a block scalar: the value is the indented body
        below it, not the literal marker. Reading the marker put a bare "|" in
        the catalog every harness shows the model."""
        from autorun.command_docs import skill_docs_inventory

        stunted = {
            name: skill["description"]
            for name, skill in skill_docs_inventory(PLUGIN_ROOT / "skills").items()
            if len(skill["description"].strip()) < 20
        }
        assert stunted == {}

    def test_named_skill_shows_its_full_description(self):
        from autorun.command_docs import skill_docs_inventory

        name, skill = sorted(skill_docs_inventory(PLUGIN_ROOT / "skills").items())[0]
        assert skill["description"][:80] in _run_help(f"/ar:help {name}")


class TestHelpForOneCommand:
    def test_named_command_shows_its_arguments_and_spellings(self):
        rendered = _run_help("/ar:help task")
        assert "pause" in rendered and "resume" in rendered  # from argument-hint
        assert "/ar:tasks" in rendered

    def test_named_command_accepts_the_platform_spelling(self):
        assert "pause" in _run_help("ar help task", "codex")

    def test_unknown_name_says_so_and_still_lists_the_commands(self):
        rendered = _run_help("/ar:help nosuchcommand")
        assert "nosuchcommand" in rendered
        assert "/ar:status" in rendered


class TestHelpIsInEveryHarnessCatalog:
    def test_help_ships_a_command_document(self):
        doc = COMMANDS_DIR / "help.md"
        assert doc.is_file()
        assert "description:" in doc.read_text(encoding="utf-8")

    def test_codex_catalog_teaches_help_through_the_ar_skill(self):
        """codex_canonical_commands is empty on purpose — each migrated
        command document costs always-on catalog tokens every turn — so the
        $ar skill is Codex's catalog surface now, and IT must be what makes
        `ar:help` discoverable there."""
        assert CONFIG["codex_canonical_commands"] == ()
        ar_skill = PLUGIN_ROOT / ".codex-plugin" / "skills" / "ar" / "SKILL.md"
        assert "ar:help" in ar_skill.read_text(encoding="utf-8")
