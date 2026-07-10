"""Shared readers for autorun command and skill Markdown files."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CommandDoc:
    """Parsed command markdown metadata plus body text."""

    path: Path
    name: str
    aliases: tuple[str, ...]
    description: str
    body: str
    executable: bool


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return simple YAML-style frontmatter and the markdown body."""
    if not text.startswith("---"):
        return {}, text
    try:
        _, raw_frontmatter, body = text.split("---", 2)
    except ValueError:
        return {}, text

    frontmatter: dict[str, str] = {}
    for line in raw_frontmatter.strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        frontmatter[key.strip()] = value.strip().strip("'\"")
    return frontmatter, body.strip()


def _parse_aliases(raw_aliases: str | None) -> tuple[str, ...]:
    """Parse the compact `[a, b]` alias form used in command frontmatter."""
    if not raw_aliases:
        return ()
    value = raw_aliases.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return tuple(part.strip().strip("'\"") for part in value.split(",") if part.strip())


def read_command_doc(path: Path) -> CommandDoc:
    """Parse a single command markdown file without executing embedded snippets."""
    text = path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(text)
    return CommandDoc(
        path=path,
        name=frontmatter.get("name") or path.stem,
        aliases=_parse_aliases(frontmatter.get("aliases")),
        description=frontmatter.get("description", ""),
        body=body,
        executable="!`" in text,
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
        }
    return inventory


def skill_docs_inventory(skills_dir: Path) -> dict[str, dict[str, str]]:
    """Return stable metadata for installed ``skills/*/SKILL.md`` files."""
    inventory: dict[str, dict[str, str]] = {}
    if not skills_dir.is_dir():
        return inventory
    for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
        frontmatter, _ = _split_frontmatter(skill_file.read_text(encoding="utf-8"))
        inventory[skill_file.parent.name] = {
            "file": str(skill_file.relative_to(skills_dir)),
            "name": frontmatter.get("name") or skill_file.parent.name,
            "description": frontmatter.get("description", ""),
        }
    return inventory
