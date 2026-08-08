#!/usr/bin/env python3
"""What is installed, whether it works, and the metadata that identifies it.

Two reports and one write.

``report``   what the walk found, grouped so a user reads outcomes rather than
             a hundred lines. Status *is* dry run: both are ``Mode.PREVIEW``
             over the same walk, so a status line that says PUBLISH is exactly
             what an install would do next.
``health``   the checks a file listing cannot answer — is the hook command
             runnable, does a memory file have a foreign block in it, is a
             harness offering the same product twice.
``metadata`` the version, commit and build time stamped into the package.

WHY HEALTH IS SEPARATE FROM THE WALK
====================================

The walk answers "is this file where it should be". It cannot answer "does the
thing in it run", and those failures are the ones that look like success:
autorun's whole failure mode is a hook that is installed, is never invoked, and
reports nothing. Every check here is a question the walk cannot ask.

Each check returns a finding rather than printing, so the same data serves
``--status``, an install summary and a test.

Complexity: O(harnesses) stats plus one subprocess per runnable check, and only
when asked. Nothing here walks a tree.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

from . import codex, discovery, memory
from .fs import Verdict, atomic_write
from .runtime import Runner, _spawn

__all__ = [
    "Level", "Finding", "health", "summarize", "report_lines",
    "metadata_document", "write_metadata", "METADATA_SUBPATH",
]

#: Where the package records what it was built from, relative to a plugin.
METADATA_SUBPATH = Path("src") / "autorun" / "metadata.json"


class Level(Enum):
    """How much a finding matters. Three, because a fourth would not change
    what anyone does about it."""

    OK = "ok"
    WARN = "warn"      # works, but something will surprise the user later
    BROKEN = "broken"  # installed and inert, which looks like success


@dataclass(frozen=True, slots=True)
class Finding:
    """One answer, with enough context to act on it."""

    check: str
    level: Level
    detail: str = ""
    fix: str = ""

    def describe(self) -> str:
        mark = {Level.OK: "ok  ", Level.WARN: "warn", Level.BROKEN: "FAIL"}[self.level]
        line = f"{mark} {self.check}"
        if self.detail:
            line += f" — {self.detail}"
        if self.fix and self.level is not Level.OK:
            line += f"\n     fix: {self.fix}"
        return line


# --- the checks a file listing cannot answer --------------------------------


def _hook_runs(command: Sequence[str], run: Runner) -> Finding:
    """Whether the hook command starts at all.

    The check that matters most and the one a listing cannot make: a hook whose
    interpreter cannot resolve is installed, is invoked, fails, and — because
    autorun fails open by design — allows the tool call and says nothing. From
    the outside that is indistinguishable from a hook that ran and approved.
    """
    if not command:
        return Finding("hook command", Level.WARN, "not configured")
    try:
        result = run([*command, "--help"])
    except (OSError, subprocess.SubprocessError) as error:
        return Finding(
            "hook command", Level.BROKEN, f"{type(error).__name__}: {error}",
            "re-run the install so the hook command is rewritten for this machine",
        )
    if result.returncode != 0:
        return Finding(
            "hook command", Level.BROKEN, f"exit {result.returncode}",
            "re-run the install so the hook command is rewritten for this machine",
        )
    return Finding("hook command", Level.OK)


def _foreign_blocks(files: Iterable[Path], known: Sequence[str]) -> Iterator[Finding]:
    """Autorun regions written by a version this one does not know.

    Uninstall removes the slugs it knows, so an unknown one stays in the user's
    memory file forever with nothing reporting it.
    """
    for path in files:
        for slug in memory.foreign_slugs(path, known):
            yield Finding(
                f"memory block {slug}", Level.WARN, str(path),
                "a newer or retired autorun wrote it; remove it by hand if unwanted",
            )


def _duplicate_products(entries: Mapping[str, Sequence[str]]) -> Iterator[Finding]:
    """One product offered under two names in a harness's registry.

    This is the shape that made a Codex tree survive every uninstall: the
    marketplace listed the plugin twice and each install resolved whichever it
    read last.
    """
    for harness, names in entries.items():
        if len(set(names)) < len(names):
            yield Finding(
                f"{harness} registry", Level.WARN, f"duplicate entries: {', '.join(names)}",
                "re-run the install; it withdraws the name it no longer uses",
            )


def _duplicate_skills(routes: Mapping[str, Sequence[Path]]) -> Iterator[Finding]:
    """Distinct copies of one skill visible to the same harness."""
    for harness, roots in routes.items():
        found: dict[str, dict[Path, Path]] = {}
        for root in roots:
            try:
                candidates = tuple(root.iterdir())
            except OSError:
                continue
            for candidate in candidates:
                if not candidate.is_dir() or not (candidate / "SKILL.md").is_file():
                    continue
                found.setdefault(candidate.name.casefold(), {}).setdefault(
                    candidate.resolve(), candidate
                )
        for name, copies in found.items():
            if len(copies) > 1:
                yield Finding(
                    f"{harness} skills", Level.WARN,
                    f"{name} appears at {', '.join(map(str, copies.values()))}",
                    "keep one copy or replace the others with links to it",
                )


def _codex_files(codex_dir: Path, guidance: memory.Block | None) -> Iterator[Finding]:
    """Failures Codex turns into silent absence rather than an error."""
    agents = codex_dir / "AGENTS.md"
    shadow = codex.shadowing_override(codex_dir)
    if guidance is not None and shadow is not None and agents.is_file():
        try:
            installed = memory.bounds(agents.read_text(encoding="utf-8"), guidance)
        except OSError:
            installed = None
        if installed is not None:
            yield Finding(
                "Codex guidance", Level.WARN,
                f"{shadow} shadows autorun's block in {agents}",
                "move the wanted text into AGENTS.override.md or remove the non-blank override",
            )

    hooks = codex_dir / "hooks.json"
    if not hooks.is_file():
        return
    try:
        document = json.loads(hooks.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        yield Finding(
            "Codex hooks", Level.BROKEN, f"cannot parse {hooks}: {error}",
            "repair hooks.json, then re-run the install",
        )
        return
    if not isinstance(document, Mapping):
        yield Finding(
            "Codex hooks", Level.BROKEN, f"{hooks} must contain a JSON object",
            "repair hooks.json, then re-run the install",
        )
        return
    unknown = codex.unknown_top_level(document)
    if unknown:
        yield Finding(
            "Codex hooks", Level.BROKEN,
            f"{hooks} has rejected top-level keys: {', '.join(unknown)}",
            "remove the rejected keys; Codex accepts only description and hooks",
        )


def health(
    *,
    hook_command: Sequence[str] = (),
    memory_files: Iterable[Path] = (),
    known_slugs: Sequence[str] = (),
    registry_entries: Mapping[str, Sequence[str]] | None = None,
    skill_routes: Mapping[str, Sequence[Path]] | None = None,
    codex_dir: Path | None = None,
    codex_guidance: memory.Block | None = None,
    run: Runner = _spawn,
) -> tuple[Finding, ...]:
    """Every check, in one call. Inputs are passed in, never discovered here.

    Discovery belongs to ``discovery``; asking it again here would be a second
    authority for questions that already have one, and the two would disagree
    exactly when it mattered.
    """
    findings = [_hook_runs(hook_command, run)]
    findings.extend(_foreign_blocks(memory_files, known_slugs))
    findings.extend(_duplicate_products(registry_entries or {}))
    findings.extend(_duplicate_skills(skill_routes or {}))
    if codex_dir is not None:
        findings.extend(_codex_files(codex_dir, codex_guidance))
    return tuple(findings)


# --- reporting what the walk found ------------------------------------------


def summarize(decisions: Iterable[object]) -> dict[str, int]:
    """Count decisions by verdict, so a report leads with the outcome."""
    counts: dict[str, int] = {}
    for decision in decisions:
        verdict = getattr(decision, "verdict", None)
        name = verdict.value if isinstance(verdict, Verdict) else str(verdict)
        counts[name] = counts.get(name, 0) + 1
    return counts


def report_lines(
    decisions: Sequence[object], findings: Sequence[Finding] = (), *, verbose: bool = False
) -> Iterator[str]:
    """The user-facing report: totals, then anything that needs attention.

    A hundred ``skip`` lines are noise, and burying one ``keep`` among them is
    how a kept user edit goes unnoticed. Totals lead; only decisions that are
    not routine are listed, unless ``verbose`` asks for all of them.
    """
    counts = summarize(decisions)
    if counts:
        yield " ".join(f"{name}={count}" for name, count in sorted(counts.items()))
    notable = [
        d for d in decisions
        if verbose or getattr(getattr(d, "verdict", None), "value", "") not in ("skip",)
    ]
    for decision in notable:
        describe = getattr(decision, "describe", None)
        yield f"  {describe() if callable(describe) else decision}"
    for finding in findings:
        if verbose or finding.level is not Level.OK:
            yield finding.describe()


# --- the metadata that identifies this build --------------------------------


def metadata_document(
    version: str, *, commit: str = "unknown", env: Mapping[str, str] | None = None
) -> dict:
    """What goes in ``metadata.json``.

    The build time comes from ``discovery.build_timestamp``, which honours
    ``SOURCE_DATE_EPOCH``, so two builds of the same commit produce identical
    bytes. A timestamp read from the clock would make every rebuild differ and
    every diff noisy.
    """
    return {
        "version": version,
        "commit": commit,
        "build_time": discovery.build_timestamp(env if env is not None else os.environ) or "unknown",
    }


def write_metadata(
    plugin_dir: Path, document: Mapping[str, object], *, allowed: bool = False
) -> Path | None:
    """Stamp the metadata, unless doing so would dirty a tracked file.

    A local install must not modify a file the repository tracks: the developer
    then has an unexplained change in ``git status`` caused by running their own
    installer. ``allowed`` is the explicit opt-in for a real release build.

    Returns the path written, or None when skipped or unchanged. Unchanged is
    also None, so a repeated install does not churn an mtime.
    """
    target = plugin_dir / METADATA_SUBPATH
    if target.exists() and not allowed:
        return None
    text = json.dumps(dict(document), indent=2) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == text:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(target, text)
    return target


def demo() -> None:
    """Self-check: an inert hook is reported, and a tracked file is not dirtied."""
    import tempfile

    def ok(argv):
        return subprocess.CompletedProcess(argv, 0, "", "")

    def broken(argv):
        return subprocess.CompletedProcess(argv, 127, "", "command not found")

    def explodes(argv):
        raise FileNotFoundError("uv")

    # The check that matters: a hook that cannot start looks exactly like one
    # that ran and approved, because autorun fails open by design.
    assert health(hook_command=("uv", "run"), run=ok)[0].level is Level.OK
    assert health(hook_command=("uv", "run"), run=broken)[0].level is Level.BROKEN
    assert health(hook_command=("uv", "run"), run=explodes)[0].level is Level.BROKEN
    assert health(run=ok)[0].level is Level.WARN, "not configured is not broken"
    assert "fix:" in health(hook_command=("x",), run=broken)[0].describe()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # A block from a version we no longer ship is reported, not ignored.
        theirs = root / "AGENTS.md"
        memory.splice(theirs, "old guidance", memory.Block("retired-slug"))
        found = health(hook_command=("x",), memory_files=[theirs], known_slugs=["guidance"], run=ok)
        assert any(f.level is Level.WARN and "retired-slug" in f.check for f in found), found
        clean = health(
            hook_command=("x",), memory_files=[theirs], known_slugs=["retired-slug"], run=ok
        )
        assert all(f.level is not Level.WARN or "memory" not in f.check for f in clean)

        # One product under two names is the shape that survived every uninstall.
        duplicated = health(
            hook_command=("x",), registry_entries={"codex": ["ar", "ar"]}, run=ok
        )
        assert any("duplicate" in f.detail for f in duplicated)
        assert not any(
            "duplicate" in f.detail
            for f in health(hook_command=("x",), registry_entries={"codex": ["ar"]}, run=ok)
        )

        # Totals lead, and a kept edit is not buried under a hundred skips.
        class Fake:
            def __init__(self, verdict, text):
                self.verdict, self._text = verdict, text

            def describe(self):
                return self._text

        decisions = [Fake(Verdict.SKIP, "s") for _ in range(100)]
        decisions.append(Fake(Verdict.KEEP, "your edit in skills/commit"))
        lines = list(report_lines(decisions))
        assert lines[0] == "keep=1 skip=100", lines[0]
        assert len(lines) == 2 and "your edit" in lines[1]
        assert len(list(report_lines(decisions, verbose=True))) == 102

        # Metadata is reproducible from SOURCE_DATE_EPOCH, never the clock.
        first = metadata_document("1.0.0", commit="abc", env={"SOURCE_DATE_EPOCH": "1700000000"})
        second = metadata_document("1.0.0", commit="abc", env={"SOURCE_DATE_EPOCH": "1700000000"})
        assert first == second and first["build_time"].startswith("2023-")

        # A tracked file is not dirtied by running the installer locally.
        plugin = root / "plugins" / "autorun"
        (plugin / METADATA_SUBPATH.parent).mkdir(parents=True)
        tracked = plugin / METADATA_SUBPATH
        tracked.write_text('{"version": "old"}\n', encoding="utf-8")
        assert write_metadata(plugin, first) is None, "a local install must not dirty it"
        assert tracked.read_text(encoding="utf-8") == '{"version": "old"}\n'

        # An explicit release build stamps it, and a repeat writes nothing.
        assert write_metadata(plugin, first, allowed=True) == tracked
        assert json.loads(tracked.read_text())["commit"] == "abc"
        assert write_metadata(plugin, first, allowed=True) is None, "no mtime churn"

        # A plugin with no metadata file yet is stamped without the opt-in.
        fresh = root / "plugins" / "other"
        assert write_metadata(fresh, first) == fresh / METADATA_SUBPATH

    print("installer.status: all self-checks passed")


if __name__ == "__main__":
    demo()
