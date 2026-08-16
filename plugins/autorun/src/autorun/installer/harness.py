"""Turning autorun's one command set into each harness's own file format.

Autorun writes commands once, as markdown with frontmatter. Claude reads that
directly; the Gemini family wants TOML under ``commands/<ext>/``; every harness
has its own placeholder spelling. This module holds those translations as pure
functions over text, so they can be tested without a filesystem and reused by
whichever step needs them.

Nothing here writes to disk. Generated content has no source path, so it does
not fit :class:`Intent` directly — a step renders into a staging directory and
publishes that, which keeps the intent layer pure and keeps generation out of
the traversal.

One latent defect closed: the renderer this replaces built TOML with
``f'description = "{doc.description}"'`` and no escaping. A description holding
a quote or backslash emits invalid TOML and Gemini drops that command with no
error. No shipped command trips it today, which is exactly why it would have
shipped — the first description containing an apostrophe-free quotation would
have found it in a user's install rather than in a test.

Complexity: O(n) in the text for every function here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "toml_string",
    "render_toml_command",
    "substitute",
    "CLAUDE_ROOT_PLACEHOLDER",
    "GEMINI_ARGS",
    "CLAUDE_ARGS",
]

#: Claude's placeholder for the directory a plugin was installed into. Other
#: harnesses use their own (`${extensionPath}`), which the registry records.
CLAUDE_ROOT_PLACEHOLDER = "${CLAUDE_PLUGIN_ROOT}"

#: How each family spells "the rest of what the user typed".
CLAUDE_ARGS = "$ARGUMENTS"
GEMINI_ARGS = "{{args}}"

_TOML_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def toml_string(value: str) -> str:
    """Quote a value as a TOML basic string, escaping what TOML requires.

    Backslash first, or every escape this function adds gets escaped again.
    """
    escaped = value
    for raw, replacement in _TOML_ESCAPES.items():
        escaped = escaped.replace(raw, replacement)
    return f'"{escaped}"'


def _toml_multiline(value: str) -> str:
    """A multi-line basic string, for a prompt body that spans lines.

    Only the delimiter and backslashes need escaping inside ``\"\"\"``; leaving
    single quotes alone keeps the body readable, which matters because a human
    debugging a command reads this file.
    """
    escaped = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return f'"""\n{escaped}\n"""'


@dataclass(frozen=True, slots=True)
class Command:
    """The parts of a command document a harness file needs."""

    name: str
    description: str
    body: str


def render_toml_command(command: Command, *, args: str = GEMINI_ARGS) -> str:
    """One ``commands/<ext>/<name>.toml`` file.

    ``$ARGUMENTS`` becomes the family's own spelling, because a command whose
    placeholder never expands silently passes the literal text to the model.
    """
    prompt = command.body.replace(CLAUDE_ARGS, args)
    return (
        f"description = {toml_string(command.description)}\n"
        f"prompt = {_toml_multiline(prompt)}\n"
    )


def substitute(text: str, root: Path, *, placeholder: str = CLAUDE_ROOT_PLACEHOLDER) -> str:
    """Replace a harness's plugin-root placeholder with the real directory.

    Both spellings are handled — ``${CLAUDE_PLUGIN_ROOT}`` and the bare
    ``$CLAUDE_PLUGIN_ROOT`` — because a manifest written by hand uses whichever
    the author remembered, and an unexpanded placeholder becomes a path that
    does not exist rather than an error anyone sees.
    """
    bare = placeholder.replace("${", "$").replace("}", "")
    return text.replace(placeholder, str(root)).replace(bare, str(root))


def demo() -> None:
    """Self-check: escaping that the old renderer would have got wrong."""
    # A quote or backslash in a description produced invalid TOML before.
    assert toml_string('say "hi"') == '"say \\"hi\\""'
    assert toml_string("C:\\path") == '"C:\\\\path"'
    assert toml_string("line\nbreak") == '"line\\nbreak"'
    assert toml_string("plain") == '"plain"'

    # Backslash-first ordering: a naive implementation double-escapes.
    assert toml_string('a\\"b') == '"a\\\\\\"b"'

    command = Command(name="status", description='Show "current" status', body="Report $ARGUMENTS")
    rendered = render_toml_command(command)

    assert rendered.startswith('description = "Show \\"current\\" status"')
    assert "{{args}}" in rendered and "$ARGUMENTS" not in rendered
    assert 'prompt = """' in rendered

    # The rendered file must actually parse as TOML.
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
        tomllib = None
    if tomllib is not None:
        parsed = tomllib.loads(rendered)
        assert parsed["description"] == 'Show "current" status', parsed
        assert parsed["prompt"].strip() == "Report {{args}}", parsed

        # The shape that broke the old renderer, round-tripped.
        nasty = Command("x", 'has "quotes" and \\ backslash', 'body with """ fence')
        assert tomllib.loads(render_toml_command(nasty))["description"] == nasty.description

    # Placeholder substitution accepts both spellings. The expectation is built
    # from `root` rather than written out, because `str(Path("/opt/plugins/ar"))`
    # is a backslash path on Windows and a hardcoded POSIX spelling made this
    # assert what the platform separator is, not what `substitute` does.
    root = Path("/opt/plugins/ar")
    here = str(root)
    assert substitute("run ${CLAUDE_PLUGIN_ROOT}/hooks/x.py", root) == f"run {here}/hooks/x.py"
    assert substitute("run $CLAUDE_PLUGIN_ROOT/x", root) == f"run {here}/x"
    assert substitute("nothing to do", root) == "nothing to do"

    # A different harness placeholder is honoured.
    assert substitute("${extensionPath}/h.py", root, placeholder="${extensionPath}") == \
        f"{here}/h.py"

    print("installer.harness: all self-checks passed")


if __name__ == "__main__":
    demo()
