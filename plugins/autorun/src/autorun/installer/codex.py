"""Codex's hooks file, which one stray key disables entirely.

Codex is the harness where being *almost* right is worse than failing. Three of
its rules silently discard work rather than reporting it, so everything here
validates before writing rather than handling errors after:

1. ``HooksFile`` is ``#[serde(deny_unknown_fields)]`` (openai/codex
   ``hooks/src/engine/hook_config.rs:10-17``, reported at
   ``engine/discovery.rs:327-336``). One unrecognised top-level key drops
   **every hook in the file**, including the user's.
2. A bare ``{"type": "command", ...}`` entry is silently dropped. Codex wants
   the wrapper object whose ``hooks`` list holds the commands.
3. ``AGENTS.override.md`` is read before ``AGENTS.md`` and the first non-blank
   one wins, so guidance written to ``AGENTS.md`` can be invisible while the
   install reports success.

The merge is the interesting part: the file belongs to the user, so autorun
replaces only entries it can prove are its own and never rewrites the rest.
Ownership is by command marker rather than by a side-car flag, because a flag
is itself an unknown field under rule 1 — the legacy ``_autorun_owned`` key was
exactly the shape that disables the file it is written into.

Complexity: O(E) in existing entries, one read and one atomic write.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .fs import dereference_links as fs_dereference_links, json_document

__all__ = [
    "ALLOWED_TOP_LEVEL",
    "COMMAND_MARK",
    "HOOK_TIMEOUT_SECONDS",
    "is_ours",
    "without_ours",
    "WHOLLY_OURS",
    "unknown_top_level",
    "validate_hooks",
    "validate_marketplace",
    "wrap",
    "merge_hooks",
    "shadowing_override",
    "marketplace_entry",
    "publish_marketplace",
    "withdraw_from_marketplace",
    "stage_plugin",
    "dereference_links",
    "PLUGIN_NAME", "PERSONAL_MARKETPLACE_NAME", "GITHUB_MARKETPLACE_NAME",
    "GITHUB_MARKETPLACE_SOURCE",
]

PLUGIN_NAME = "ar"
PERSONAL_MARKETPLACE_NAME = "personal"
GITHUB_MARKETPLACE_NAME = "autorun"
GITHUB_MARKETPLACE_SOURCE = "ahundt/autorun"

#: Anything else drops every hook in the file. Not a style rule — a data-loss rule.
ALLOWED_TOP_LEVEL = frozenset({"description", "hooks"})

#: How autorun recognises its own entries: the command it wrote. A side-car key
#: would be an unknown field, which is the failure this constant exists to avoid.
COMMAND_MARK = "hook_entry.py"


def unknown_top_level(document: Mapping[str, object]) -> tuple[str, ...]:
    """Top-level keys Codex would reject, sorted.

    Checked *before* the write. Discovering this afterwards means the user's
    hooks are already disabled and nothing said so.
    """
    return tuple(sorted(set(document) - ALLOWED_TOP_LEVEL))


def _validate_hooks_document(path: Path, document: Mapping[str, object]) -> None:
    if rejected := unknown_top_level(document):
        raise ValueError(
            f"{path} has top-level key(s) Codex rejects: {', '.join(rejected)}. "
            "Codex drops every hook in a file with an unknown key, so autorun "
            "will not add to it. Remove the key, then re-run the install."
        )
    events = document.get("hooks", {})
    if not isinstance(events, dict):
        raise ValueError(
            f"{path}: 'hooks' must be an object, found {type(events).__name__}"
        )
    malformed = sorted(name for name, entries in events.items() if not isinstance(entries, list))
    if malformed:
        raise ValueError(
            f"{path}: hook event(s) must contain lists: {', '.join(malformed)}"
        )


def validate_hooks(path: Path) -> None:
    """Read and validate a user-owned hooks file without writing it."""
    if not path.is_file():
        return
    with json_document(path) as document:
        _validate_hooks_document(path, document)


def is_ours(entry: object, mark: str = COMMAND_MARK) -> bool:
    """Whether this entry is entirely autorun's, so removing it costs nothing.

    Three shapes have shipped: a wrapper whose inner hooks carry our command, a
    bare command entry from before the wrapper was required, and an entry
    tagged with the legacy ``_autorun_owned`` flag. All three must be
    recognised or an upgrade leaves duplicates that fire twice.

    Defined as "stripping ours leaves nothing", so this and :func:`without_ours`
    cannot disagree about a mixed entry.
    """
    return without_ours(entry, mark) is WHOLLY_OURS


#: What :func:`without_ours` returns for an entry with nothing left in it. A
#: sentinel rather than ``None`` because ``None`` is itself a legal list element
#: in a file the user edits, and reading it as "ours" would silently delete it.
WHOLLY_OURS = object()


def without_ours(entry: object, mark: str = COMMAND_MARK) -> object:
    """``entry`` with autorun's hooks removed, or ``WHOLLY_OURS`` if none remain.

    A wrapper holding both our command and one the user added by hand keeps
    theirs. Dropping the whole entry would delete a hook we never wrote, and
    the wrapper is a shape autorun authored, so a user editing inside it is the
    likely case rather than an exotic one.
    """
    if not isinstance(entry, Mapping):
        return entry
    if entry.get("_autorun_owned") or _is_command(entry, mark):
        return WHOLLY_OURS
    inner = entry.get("hooks")
    if not isinstance(inner, list):
        return entry
    kept = [
        hook for hook in inner
        if not (isinstance(hook, Mapping) and (hook.get("_autorun_owned") or _is_command(hook, mark)))
    ]
    if not kept:
        return WHOLLY_OURS
    return entry if len(kept) == len(inner) else {**entry, "hooks": kept}


def _is_command(hook: object, mark: str) -> bool:
    return (
        isinstance(hook, Mapping)
        and hook.get("type") == "command"
        and mark in str(hook.get("command") or "")
    )


#: Seconds a single hook may take before Codex abandons it. Autorun's hook
#: talks to a local daemon and answers in milliseconds, so a wait beyond this is
#: a hang; without it the harness default applies and a stuck daemon stalls the
#: user's session instead of failing open. Every entry autorun writes, in
#: ``~/.codex/hooks.json`` and in the bundled plugin alike, carries this.
HOOK_TIMEOUT_SECONDS = 10


def wrap(commands: Sequence[str], *, timeout: int = HOOK_TIMEOUT_SECONDS) -> dict:
    """Build the entry shape Codex accepts.

    A bare ``{"type": "command"}`` at the top of an event list is silently
    dropped, which reads exactly like a hook that ran and decided to allow.
    """
    return {
        "hooks": [
            {"type": "command", "command": command, "timeout": timeout}
            for command in commands
        ]
    }


def merge_hooks(
    path: Path,
    ours: Mapping[str, Sequence[str]],
    *,
    description: str = "",
    mark: str = COMMAND_MARK,
) -> tuple[str, ...]:
    """Put autorun's hooks into a file the user owns, keeping theirs untouched.

    Returns the events written. Raises before writing if the existing file
    carries a key Codex would reject, rather than adding to a file that is
    already disabling itself — repairing that is the user's call, since the key
    is theirs.

    Removing our entries and re-adding them (rather than editing in place) is
    what makes a rename or a command change converge instead of accumulating a
    second copy alongside the first.

    Every event in the file is swept, not only the ones in ``ours``: an event
    autorun used to register and no longer ships would otherwise keep firing our
    hook forever. ``SessionEnd`` is the case that proved it — Codex never
    accepted the name, and the entry outlived several versions.
    """
    with json_document(path, lambda: {"hooks": {}}) as document:
        _validate_hooks_document(path, document)
        if description and "description" not in document:
            document["description"] = description
        events = document.setdefault("hooks", {})
        for event in [*events, *(e for e in ours if e not in events)]:
            entries = events.get(event, [])
            kept = [e for e in (without_ours(e, mark) for e in entries) if e is not WHOLLY_OURS]
            commands = ours.get(event, ())
            events[event] = [*kept, wrap(commands)] if commands else kept
            if not events[event]:
                del events[event]
    return tuple(ours)


def shadowing_override(codex_dir: Path) -> Path | None:
    """The ``AGENTS.override.md`` hiding our ``AGENTS.md``, if one is doing so.

    Codex reads the override first and uses the first non-blank file, so a
    non-empty override makes our guidance unreachable while the install still
    reports that it wrote it. A blank override shadows nothing and is not
    reported, or every user with an empty placeholder gets a false warning.
    """
    override = codex_dir / "AGENTS.override.md"
    try:
        return override if override.is_file() and override.read_text(encoding="utf-8").strip() else None
    except OSError:
        return None


# --- the plugin package and the marketplace that lists it -------------------


def marketplace_entry(name: str, relative_source: str, *, category: str = "Productivity") -> dict:
    """One plugin entry, in the shape Codex's marketplace file uses."""
    return {
        "name": name,
        "source": {"source": "local", "path": relative_source},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": category,
    }


def _same_marketplace_source(candidate: object, expected: Mapping[str, object]) -> bool:
    """Whether name and source prove this is the entry autorun published."""
    return (
        isinstance(candidate, Mapping)
        and candidate.get("name") == expected.get("name")
        and candidate.get("source") == expected.get("source")
    )


def publish_marketplace(
    path: Path, name: str, entry: Mapping[str, object], *, display: str = ""
) -> bool:
    """Add or update one plugin in a marketplace file the user may also use.

    Name plus exact source establishes ownership. A same-name entry from a
    different source is a user conflict and is left untouched.

    Returns whether anything changed; :func:`json_document` writes nothing when
    the document is unchanged, so a repeated install does not churn the file.
    """
    changed = False
    with json_document(path, lambda: {"name": name, "plugins": []}) as document:
        before = json.dumps(document, sort_keys=True)
        if display and "interface" not in document:
            document["interface"] = {"displayName": display}
        plugins = document.setdefault("plugins", [])
        _validate_marketplace_document(path, document, entry)
        others = [
            p for p in plugins
            if not _same_marketplace_source(p, entry)
        ]
        document["plugins"] = [*others, dict(entry)]
        changed = json.dumps(document, sort_keys=True) != before
    return changed


def _validate_marketplace_document(
    path: Path,
    document: Mapping[str, object],
    entry: Mapping[str, object] | None = None,
) -> None:
    plugins = document.get("plugins", [])
    if not isinstance(plugins, list):
        raise ValueError(
            f"{path}: 'plugins' must be a list, found {type(plugins).__name__}"
        )
    if entry is not None and any(
        isinstance(plugin, Mapping)
        and plugin.get("name") == entry.get("name")
        and not _same_marketplace_source(plugin, entry)
        for plugin in plugins
    ):
        raise ValueError(
            f"{path}: plugin {entry.get('name')!r} already uses a different source"
        )


def validate_marketplace(
    path: Path,
    entry: Mapping[str, object] | None = None,
) -> None:
    """Read and validate a user-owned marketplace without writing it."""
    if not path.is_file():
        return
    with json_document(path) as document:
        _validate_marketplace_document(path, document, entry)


def withdraw_from_marketplace(path: Path, entry: Mapping[str, object]) -> bool:
    """Remove one exactly owned plugin, leaving same-name entries untouched."""
    if not path.is_file():
        return False
    removed = False
    with json_document(path) as document:
        plugins = document.get("plugins")
        if not isinstance(plugins, list):
            return False
        kept = [p for p in plugins if not _same_marketplace_source(p, entry)]
        removed = len(kept) != len(plugins)
        document["plugins"] = kept
    return removed


def stage_plugin(
    plugin_dir: Path,
    staging: Path,
    *,
    include_hooks: bool = False,
    include_skills: bool = True,
    skill_names: Iterable[str] | None = None,
    hook_command: str = "",
) -> Path:
    """Build the local Codex plugin tree without touching its live target."""
    selected_skills = None if skill_names is None else set(skill_names)
    include_skills = include_skills and selected_skills != set()
    ignored = shutil.ignore_patterns(
        ".git", ".venv", ".pytest_cache", ".mypy_cache", ".ruff_cache",
        "__pycache__", "*.pyc", "*.pyo", ".coverage", "htmlcov", "hooks",
    )

    def ignore(directory: str, names: list[str]) -> set[str]:
        source = Path(directory)
        skipped = set(ignored(directory, names))
        if source == plugin_dir and not include_skills:
            skipped.add("skills")
        if source == plugin_dir / "skills":
            skipped.update(
                name for name in names
                if (
                    (source / name).is_dir()
                    and not (source / name / "SKILL.md").is_file()
                )
                or (selected_skills is not None and name not in selected_skills)
            )
        return skipped

    shutil.copytree(plugin_dir, staging, ignore=ignore)
    manifest_path = staging / ".codex-plugin" / "plugin.json"
    if not include_skills and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        declared = manifest.get("skills")
        if isinstance(declared, list):
            kept = [entry for entry in declared if entry != "./skills/"]
            if kept:
                manifest["skills"] = kept
            else:
                manifest.pop("skills", None)
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
    if include_hooks:
        if not hook_command:
            raise ValueError("Codex plugin hooks require a command")
        hooks = staging / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        shutil.copy2(plugin_dir / "hooks" / "hook_entry.py", hooks / "hook_entry.py")
        events = (
            "PreToolUse", "PostToolUse", "UserPromptSubmit",
            "SessionStart", "Stop", "SubagentStop",
        )
        (hooks / "hooks.json").write_text(
            json.dumps(
                {"hooks": {event: [wrap((hook_command,))] for event in events}},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    dereference_links(staging)
    return staging


def dereference_links(root: Path) -> tuple[str, ...]:
    """Flatten a staged tree for the Codex plugin cache, naming broken links.

    The cache ignores symlinks, so a staged ``SKILL.md`` that is a link ships
    with no content and nothing reports it. The flattening itself is
    harness-neutral and lives in :func:`fs.dereference_links`, the only module
    allowed to mutate a tree; Codex keeps just the knowledge that it needs it.
    """
    _replaced, broken = fs_dereference_links(root)
    return broken


def demo() -> None:
    """Self-check: the three silent-discard rules, and a user's hooks surviving."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        codex = Path(tmp)
        hooks = codex / "hooks.json"

        # A user's own hook, in the shape Codex accepts.
        theirs = {"hooks": [{"type": "command", "command": "/usr/local/bin/mine.sh"}]}
        hooks.write_text(json.dumps({"hooks": {"PreToolUse": [theirs]}}), encoding="utf-8")

        written = merge_hooks(hooks, {"PreToolUse": ["uv run hook_entry.py --cli codex"]})
        document = json.loads(hooks.read_text())
        entries = document["hooks"]["PreToolUse"]

        assert written == ("PreToolUse",)
        assert theirs in entries, "the user's hook is never touched"
        assert len(entries) == 2
        assert not unknown_top_level(document), "we never introduce a rejected key"

        # Rule 2: our entry is wrapped, not bare.
        ours = next(e for e in entries if is_ours(e))
        assert "hooks" in ours and ours["hooks"][0]["type"] == "command"

        # Re-merging converges instead of accumulating a second copy.
        merge_hooks(hooks, {"PreToolUse": ["uv run hook_entry.py --cli codex --v2"]})
        entries = json.loads(hooks.read_text())["hooks"]["PreToolUse"]
        assert len([e for e in entries if is_ours(e)]) == 1, "exactly one of ours, always"
        assert theirs in entries
        assert "--v2" in json.dumps(entries)

        # Every legacy spelling is still recognised, or upgrades double-fire.
        assert is_ours({"_autorun_owned": True})
        assert is_ours({"type": "command", "command": "python hook_entry.py"})
        assert is_ours({"hooks": [{"type": "command", "command": "x hook_entry.py y"}]})
        assert not is_ours({"hooks": [{"type": "command", "command": "/bin/theirs"}]})
        assert not is_ours("not even a dict")

        # Every entry we write carries a timeout, so a hung daemon cannot stall
        # the user's session waiting on the harness default.
        assert all(h["timeout"] == HOOK_TIMEOUT_SECONDS for h in wrap(["a hook_entry.py"])["hooks"])

        # An event autorun no longer ships is swept, not left firing forever.
        merge_hooks(hooks, {"SessionEnd": ["old hook_entry.py"]})
        assert "SessionEnd" in json.loads(hooks.read_text())["hooks"]
        merge_hooks(hooks, {"PreToolUse": ["uv run hook_entry.py --cli codex"]})
        assert "SessionEnd" not in json.loads(hooks.read_text())["hooks"], "retired event swept"

        # A command the user added inside our wrapper survives its removal.
        mixed = {"hooks": [
            {"type": "command", "command": "uv run hook_entry.py --cli codex"},
            {"type": "command", "command": "/usr/local/bin/also-mine.sh"},
        ]}
        shared = codex / "mixed.json"
        shared.write_text(json.dumps({"hooks": {"Stop": [mixed]}}), encoding="utf-8")
        merge_hooks(shared, {"Stop": []})
        kept = json.loads(shared.read_text())["hooks"]["Stop"]
        assert kept == [{"hooks": [{"type": "command", "command": "/usr/local/bin/also-mine.sh"}]}], kept

        # An empty command list removes our entries and leaves the user's.
        merge_hooks(hooks, {"PreToolUse": []})
        entries = json.loads(hooks.read_text())["hooks"]["PreToolUse"]
        assert entries == [theirs], entries

        # Rule 1: a file already carrying a rejected key is refused, not extended.
        poisoned = codex / "poisoned.json"
        poisoned.write_text(json.dumps({"hooks": {}, "version": 2}), encoding="utf-8")
        assert unknown_top_level({"hooks": {}, "version": 2}) == ("version",)
        try:
            merge_hooks(poisoned, {"Stop": ["cmd hook_entry.py"]})
        except ValueError as error:
            assert "version" in str(error) and "re-run" in str(error), error
        else:  # pragma: no cover - the assertion is the point
            raise AssertionError("a rejected key must stop the write")
        assert json.loads(poisoned.read_text()) == {"hooks": {}, "version": 2}, "left as found"

        # Rule 3: a non-blank override shadows our guidance; a blank one does not.
        assert shadowing_override(codex) is None
        (codex / "AGENTS.override.md").write_text("   \n", encoding="utf-8")
        assert shadowing_override(codex) is None, "a blank override shadows nothing"
        (codex / "AGENTS.override.md").write_text("my rules\n", encoding="utf-8")
        assert shadowing_override(codex) == codex / "AGENTS.override.md"

        # --- the marketplace, and the package it points at -------------------
        market = codex / "marketplace.json"
        entry = marketplace_entry("autorun", "./plugins/autorun")

        assert publish_marketplace(market, "personal", entry, display="Personal") is True
        document = json.loads(market.read_text())
        assert document["name"] == "personal"
        assert document["interface"]["displayName"] == "Personal"
        assert [p["name"] for p in document["plugins"]] == ["autorun"]
        assert document["plugins"][0]["source"] == {"source": "local", "path": "./plugins/autorun"}

        # Republishing the same entry changes nothing and rewrites nothing.
        assert publish_marketplace(market, "personal", entry) is False

        # A same-name entry from another source is not ours to replace.
        moved = marketplace_entry("autorun", "./elsewhere/autorun")
        try:
            publish_marketplace(market, "personal", moved)
        except ValueError as error:
            assert "different source" in str(error)
        else:  # pragma: no cover - the assertion is the point
            raise AssertionError("a name collision must not replace another source")

        # Another plugin, including one the user added, is never disturbed.
        theirs = marketplace_entry("their-tool", "./their-tool")
        publish_marketplace(market, "personal", theirs)
        assert withdraw_from_marketplace(market, entry) is True
        remaining = json.loads(market.read_text())["plugins"]
        assert [p["name"] for p in remaining] == ["their-tool"]
        assert withdraw_from_marketplace(market, entry) is False, "already gone"

        # --- staging must flatten links the Codex cache cannot follow --------
        real = codex / "real"
        real.mkdir()
        (real / "SKILL.md").write_text("# real content\n", encoding="utf-8")
        staged = codex / "staged"
        (staged / "skills" / "commit").mkdir(parents=True)
        (staged / "skills" / "commit" / "SKILL.md").symlink_to(real / "SKILL.md")
        (staged / "skills" / "linked-dir").symlink_to(real)
        (staged / "skills" / "dangling").symlink_to(codex / "nowhere")

        broken = dereference_links(staged)

        assert not (staged / "skills" / "commit" / "SKILL.md").is_symlink()
        assert (staged / "skills" / "commit" / "SKILL.md").read_text() == "# real content\n"
        assert (staged / "skills" / "linked-dir" / "SKILL.md").is_file()
        assert not (staged / "skills" / "linked-dir").is_symlink()
        assert broken == ("skills/dangling",), broken
        assert (staged / "skills" / "dangling").is_symlink(), "a broken link is reported, not hidden"

    print("installer.codex: all self-checks passed")


if __name__ == "__main__":
    demo()
