"""Autorun's one command set, rendered into each harness's own file format.

The renderer this replaces built TOML with an f-string and no escaping, so a
description holding a quote or backslash emitted invalid TOML and Gemini
dropped that command with no error. No shipped command trips it today, which is
precisely why it would have shipped: the first description containing a
quotation mark would have found it in a user's install rather than here.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.harness import (  # noqa: E402
    CLAUDE_ROOT_PLACEHOLDER,
    Command,
    render_toml_command,
    substitute,
    toml_string,
)

tomllib = pytest.importorskip("tomllib")


# ─── TOML escaping ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        'say "hi"',
        "C:\\path\\to\\thing",
        'a\\"b',
        "line\nbreak",
        "tab\there",
        "plain text",
        'both " and \\ together',
    ],
)
def test_any_description_round_trips_through_toml(value):
    """The property, rather than a fixed expected string: whatever we emit must
    parse back to exactly what went in."""
    rendered = f"description = {toml_string(value)}\n"

    assert tomllib.loads(rendered)["description"] == value


def test_backslashes_are_escaped_before_quotes():
    """Escaping quotes first would then escape the backslashes this function
    just added, doubling them."""
    assert toml_string('a\\"b') == '"a\\\\\\"b"'


def test_a_body_containing_a_triple_quote_fence_still_parses():
    """The prompt is a multi-line string, so its own delimiter must be escaped
    or the body terminates early and the rest becomes invalid TOML."""
    command = Command("x", "d", 'body with """ fence inside')

    parsed = tomllib.loads(render_toml_command(command))

    assert '"""' in parsed["prompt"]


# ─── Argument placeholder translation ────────────────────────────────────────


def test_arguments_are_translated_to_the_family_spelling():
    """A command whose placeholder never expands silently passes the literal
    text `$ARGUMENTS` to the model."""
    parsed = tomllib.loads(render_toml_command(Command("s", "d", "Report $ARGUMENTS now")))

    assert "{{args}}" in parsed["prompt"]
    assert "$ARGUMENTS" not in parsed["prompt"]


def test_a_body_without_arguments_is_unchanged():
    parsed = tomllib.loads(render_toml_command(Command("s", "d", "No placeholder here")))

    assert parsed["prompt"].strip() == "No placeholder here"


# ─── Plugin-root substitution ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("run ${CLAUDE_PLUGIN_ROOT}/hooks/x.py", "run /opt/ar/hooks/x.py"),
        ("run $CLAUDE_PLUGIN_ROOT/x", "run /opt/ar/x"),
        ("nothing to substitute", "nothing to substitute"),
        ("${CLAUDE_PLUGIN_ROOT}", "/opt/ar"),
    ],
)
def test_both_placeholder_spellings_are_substituted(text, expected):
    """A manifest written by hand uses whichever spelling the author
    remembered, and an unexpanded placeholder becomes a path that does not
    exist rather than an error anyone sees."""
    assert substitute(text, Path("/opt/ar")) == expected.replace("/", os.sep)


def test_another_harnesss_placeholder_is_honoured():
    assert substitute("${extensionPath}/h.py", Path("/opt/ar"),
                      placeholder="${extensionPath}") == os.path.join("/opt/ar", "h.py")


def test_the_claude_placeholder_constant_matches_what_manifests_use():
    assert CLAUDE_ROOT_PLACEHOLDER == "${CLAUDE_PLUGIN_ROOT}"


# ─── Every real command ──────────────────────────────────────────────────────


def test_every_shipped_command_renders_to_valid_toml():
    """The strongest available check: render the real command set and require
    each file to parse and round-trip."""
    from autorun.command_docs import iter_command_docs

    commands = list(iter_command_docs(Path(__file__).resolve().parents[1] / "commands"))
    assert commands, "no commands found; the fixture path is wrong"

    for doc in commands:
        rendered = render_toml_command(Command(doc.name, doc.description, doc.body))
        parsed = tomllib.loads(rendered)

        assert parsed["description"] == doc.description, doc.name
        assert "$ARGUMENTS" not in parsed["prompt"], doc.name
