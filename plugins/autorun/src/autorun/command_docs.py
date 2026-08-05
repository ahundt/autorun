"""Shared readers for autorun command and skill Markdown files."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .logging_utils import get_logger


@dataclass(frozen=True)
class CommandDoc:
    """Parsed command markdown metadata plus body text."""

    path: Path
    name: str
    aliases: tuple[str, ...]
    description: str
    body: str
    executable: bool
    argument_hint: str = ""


@dataclass(frozen=True)
class CommandEntry:
    """One user-facing command and every spelling that reaches it.

    ``name`` is the recommended spelling, ``aliases`` are the shorter or
    legacy documents that mean the same thing, and ``dispatches`` says whether
    autorun runs the command itself (a control command) or the harness hands
    the document to the model (a guide).
    """

    name: str
    aliases: tuple[str, ...]
    description: str
    argument_hint: str
    dispatches: bool

    @property
    def spellings(self) -> tuple[str, ...]:
        """Recommended spelling first, then the advertised alternatives."""
        return (self.name, *self.aliases)


class Frontmatter(dict):
    """Parsed YAML frontmatter, read through the two shapes documents use.

    YAML hands back whatever the author wrote — a string, a number, a list, a
    block scalar. Every consumer here wants either one line of text or a list
    of names, so those two reads live on the mapping instead of being
    re-derived at each call site.
    """

    def text(self, key: str, default: str = "") -> str:
        """One frontmatter value as text; a number or date renders, not leaks."""
        value = self.get(key)
        return default if value is None else str(value).strip()

    def names(self, key: str) -> tuple[str, ...]:
        """A list-valued key as names, tolerating the compact `a, b` string."""
        value = self.get(key)
        if isinstance(value, str):
            value = value.split(",")
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(str(item).strip() for item in value if str(item).strip())


def split_frontmatter(text: str, *, source: str = "") -> tuple[Frontmatter, str]:
    """Return the document's YAML frontmatter and its markdown body.

    Frontmatter is YAML, so a YAML parser reads it. Hand-written key-splitting
    readers each understood a different subset and disagreed on block scalars,
    quoting, lists, and comments; a `description: |` block read as the literal
    "|" is how that shows up in a catalog. Malformed frontmatter yields an
    empty mapping rather than raising, because a broken document must not take
    a hook or an install down with it.

    PyYAML is imported here rather than at module scope: no hook path reads a
    command document, so no hook pays for the import.
    """
    if not text.startswith("---"):
        return Frontmatter(), text
    try:
        _, raw_frontmatter, body = text.split("---", 2)
    except ValueError:
        return Frontmatter(), text

    import yaml

    try:
        parsed = yaml.load(raw_frontmatter, Loader=_yaml_loader())
    except yaml.YAMLError as exc:
        parsed = _read_hand_written_pairs(raw_frontmatter)
        # get_logger, not logging.getLogger: autorun's factory attaches a
        # NullHandler so a record can never reach stderr, which Claude Code
        # treats as a hook failure and which silently disables every hook.
        get_logger(__name__).warning(
            "%s: frontmatter is not valid YAML (%s); read it as plain key: value "
            "pairs instead. Quote any value containing a colon to remove this.",
            source or "document",
            str(exc).splitlines()[0],
        )
    return Frontmatter(parsed if isinstance(parsed, dict) else {}), body.strip()


@lru_cache(maxsize=1)
def _yaml_loader():
    """Return the fastest SAFE loader this install has.

    Both candidates are safe loaders: neither constructs Python objects from
    `!!python/...` tags, which is the risk `yaml.load` carries with the default
    loader. CSafeLoader is libyaml's C implementation of exactly SafeLoader's
    behavior and ships in the PyYAML wheels; the Python one is the fallback for
    a source build without libyaml.

    Measured over the 57 shipped command documents, one full parse pass:
    SafeLoader 5.11 ms, CSafeLoader 0.55 ms. `/ar:help` pays that pass per
    invocation, so the C loader is worth selecting.
    """
    import yaml

    return getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _read_hand_written_pairs(raw_frontmatter: str) -> dict[str, object]:
    """Read `key: value` pairs out of frontmatter that strict YAML refuses.

    User-written hookify rules routinely carry an unquoted colon inside a
    message or a redirect (`message: Warning: dangerous command`). YAML reads
    that as a nested mapping and rejects the document, and dropping the
    document would silently disarm the user's own rule, so the pairs are read
    directly. Only this path is lenient; well-formed documents never reach it.
    """
    values: dict[str, object] = {}
    for line in raw_frontmatter.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, _, raw_value = stripped.partition(":")
        value = raw_value.strip()
        if value.startswith("[") and value.endswith("]"):
            values[key.strip()] = [item.strip().strip("\"'") for item in value[1:-1].split(",")]
        elif value.lower() in ("true", "false"):
            values[key.strip()] = value.lower() == "true"
        else:
            values[key.strip()] = value.strip("\"'")
    return values


def read_command_doc(path: Path) -> CommandDoc:
    """Parse a single command markdown file without executing embedded snippets."""
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text, source=str(path))
    return CommandDoc(
        path=path,
        name=frontmatter.text("name") or path.stem,
        aliases=frontmatter.names("aliases"),
        description=frontmatter.text("description"),
        body=body,
        executable="!`" in text,
        argument_hint=frontmatter.text("argument-hint"),
    )


def iter_command_docs(commands_dir: Path):
    """Yield parsed command docs from a commands directory in stable order."""
    if not commands_dir.is_dir():
        return
    for md_file in sorted(commands_dir.glob("*.md")):
        yield read_command_doc(md_file)


def command_docs_inventory(commands_dir: Path) -> dict[str, dict[str, object]]:
    """Return JSON-ready command markdown metadata keyed by filename stem."""
    inventory: dict[str, dict[str, object]] = {}
    for doc in iter_command_docs(commands_dir):
        inventory[doc.path.stem] = {
            "file": doc.path.name,
            "name": doc.name,
            "aliases": list(doc.aliases),
            "description": doc.description,
            "executable": doc.executable,
            "argument_hint": doc.argument_hint,
        }
    return inventory


def _dispatches_as_autorun_command(name: str) -> bool:
    """True when autorun itself answers ``/ar:<name>`` instead of the model.

    Imported lazily: reading command documents must not require the command
    registry, which install-time and snapshot callers do not always load.
    """
    from . import plugins as _plugins  # noqa: F401 - import registers handlers
    from .core import app

    return app._find_command(f"/ar:{name}") is not None


def command_help_inventory(
    commands_dir: Path,
    *,
    dispatches=None,
) -> tuple[CommandEntry, ...]:
    """Return one entry per command, with its alias documents folded in.

    A document is an alias when another document names its stem in
    ``aliases:``; everything else is a command in its own right. Short alias
    documents that are symlinks share the canonical document's frontmatter, so
    a document naming itself is ignored rather than hiding the command.

    Cost: O(D) reads over D command documents plus O(A) alias links, on
    explicit help invocation only — no hook hot path reaches this.
    """
    docs = {doc.path.stem: doc for doc in iter_command_docs(commands_dir)}
    claimed_by: dict[str, str] = {}
    for stem, doc in docs.items():
        for alias in doc.aliases:
            if alias != stem:
                claimed_by.setdefault(alias, stem)

    is_dispatched = dispatches or _dispatches_as_autorun_command
    entries = [
        CommandEntry(
            name=stem,
            aliases=tuple(alias for alias in doc.aliases if alias != stem),
            description=doc.description,
            argument_hint=doc.argument_hint,
            dispatches=is_dispatched(stem),
        )
        for stem, doc in docs.items()
        if stem not in claimed_by
    ]
    return tuple(sorted(entries, key=lambda entry: entry.name))


def skill_docs_inventory(skills_dir: Path) -> dict[str, dict[str, str]]:
    """Return stable metadata for installed ``skills/*/SKILL.md`` files."""
    inventory: dict[str, dict[str, str]] = {}
    if not skills_dir.is_dir():
        return inventory
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        frontmatter, _ = split_frontmatter(
            skill_file.read_text(encoding="utf-8"), source=str(skill_file)
        )
        inventory[skill_file.parent.name] = {
            "file": str(skill_file.relative_to(skills_dir)),
            "name": frontmatter.text("name") or skill_file.parent.name,
            "description": frontmatter.text("description"),
        }
    return inventory


def marketplace_skill_docs_inventory(
    plugins_dir: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    """Return flattened and per-plugin skill metadata for a marketplace."""
    skills: dict[str, dict[str, str]] = {}
    plugin_skills: dict[str, list[str]] = {}
    if not plugins_dir.is_dir():
        return (skills, plugin_skills)

    for plugin_dir in sorted(path for path in plugins_dir.iterdir() if path.is_dir()):
        inventory = skill_docs_inventory(plugin_dir / "skills")
        if not inventory:
            continue
        duplicate_names = sorted(set(skills).intersection(inventory))
        if duplicate_names:
            names = ", ".join(duplicate_names)
            raise ValueError(f"duplicate marketplace skill name(s): {names}")
        skills.update(inventory)
        plugin_skills[plugin_dir.name] = sorted(inventory)
    return (skills, plugin_skills)
