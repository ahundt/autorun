#!/usr/bin/env python3
"""Autorun's guidance block inside a memory file the user also writes.

``CLAUDE.md``, ``AGENTS.md`` and their per-harness siblings belong to the user.
Autorun contributes one sentinel-delimited region and must be able to update or
remove exactly that region without disturbing a word around it.

Splice and strip are the same operation: replace the region with a new body, or
with nothing. Writing them as one function removes the pair that had to agree
about how a malformed region is handled — the failure mode being an install that
appends a second block every run because it locates the region differently from
the code that wrote it.

Ownership here is by sentinel rather than by the marker used for directories: a
memory file is the user's file, so autorun never claims it, only a range inside
it. That is why this cannot reuse ``install_fs``'s tree transactions.

Complexity: O(n) in the file, two substring searches and one atomic write.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .fs import atomic_write

__all__ = [
    "Block", "bounds", "splice", "strip", "foreign_slugs", "SENTINEL_RE",
    "CONTEXT_GUIDANCE", "CONTEXT_GUIDANCE_FLAG", "context_guidance_enabled",
]

#: Any autorun region, whoever wrote it. Used to find blocks from a version that
#: shipped slugs this one no longer knows, so an upgrade can report them rather
#: than silently leaving them behind forever.
SENTINEL_RE = re.compile(r"<!--\s*autorun:([a-z0-9-]+):start\s*-->")


@dataclass(frozen=True, slots=True)
class Block:
    """One named region autorun owns inside a file it does not own."""

    slug: str

    @property
    def start(self) -> str:
        return f"<!-- autorun:{self.slug}:start -->"

    @property
    def end(self) -> str:
        return f"<!-- autorun:{self.slug}:end -->"


# --- BUG #54673 WORKAROUND START --- DELETE WHEN FIXED ---
# Claude Code exposes no token counts to hooks, and Opus 4.7+ / Fable 5 /
# Mythos 5 receive no API context-awareness tags either:
#   https://platform.claude.com/docs/en/build-with-claude/context-windows#context-awareness
#   https://github.com/anthropics/claude-code/issues/54673
# With no measurement available the model guesses at remaining capacity, states
# the guess as fact — measured claims were wrong by more than 50 percentage
# points — and defers real work on the strength of it. This block supplies the
# interpretation the measurement would have provided.
# Disable: AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED=false
# Removal: delete this constant and the harnesses entry that names it; read the
# harness's own token counts instead.
# Evidence: notes/2026-07-24-2045-claude-code-opus-5-premature-context-exhaustion.md
CONTEXT_GUIDANCE_FLAG = (
    "AUTORUN_BUG_CLAUDE_CODE_NO_TOKEN_COUNT_FOR_HOOKS_BUG_54673_WORKAROUND_ENABLED"
)
CONTEXT_GUIDANCE = Block("context-capacity")


def context_guidance_enabled() -> bool:
    """Whether to write the block. The reader for ``CONTEXT_GUIDANCE_FLAG``.

    A documented disable switch that nothing consults is worse than no switch:
    the env var is published, a user sets it to ``false``, and the workaround
    keeps running with nothing to say why.
    """
    from .settings import workaround_enabled

    return workaround_enabled(CONTEXT_GUIDANCE_FLAG)
# --- BUG #54673 WORKAROUND END ---


def bounds(text: str, block: Block) -> tuple[int, int] | None:
    """Locate one well-formed region, or None when there isn't one.

    Both markers must be present with ``end`` after ``start``. A half-written
    region — one marker, or the markers inverted by a bad merge — reads as
    absent, so callers append rather than rewrite a range whose extent they
    cannot trust. Rewriting a guessed range is how a user's own paragraphs get
    swallowed into an autorun block.
    """
    opened = text.find(block.start)
    closed = text.find(block.end, opened + len(block.start)) if opened != -1 else -1
    return (opened, closed) if opened != -1 and closed != -1 else None


def validate(target: Path, block: Block) -> None:
    """Reject ambiguous ownership markers before an installer writes anything."""
    if not target.is_file():
        return
    text = target.read_text(encoding="utf-8")
    opened = text.count(block.start)
    closed = text.count(block.end)
    if opened == closed == 0:
        return
    if opened != 1 or closed != 1 or text.find(block.end) < text.find(block.start):
        raise ValueError(
            f"{target} has a malformed [{block.slug}] autorun region; "
            "repair its sentinel pair, then retry"
        )


def _rendered(text: str, block: Block, body: str) -> str:
    """The file's new contents with ``body`` in the region, or removed if empty.

    Three cases, in order:

    1. A well-formed region exists, so replace it.
    2. No region, but this exact body is already in the file unsentinelled.
       Wrap that copy where it sits. Autorun once published these files by
       copying the guidance in whole, before sentinels existed, so appending
       would give everyone upgrading from such an install the block twice.
       Adoption also makes the text removable, which a bare copy never was.
    3. Neither, so append.

    Case 2 matches verbatim and therefore cannot adopt a *stale* pre-sentinel
    copy whose text has since changed; that one is appended alongside and left
    for a human, since nothing in the file identifies it as ours.
    """
    region = f"{block.start}\n{body}\n{block.end}" if body else ""
    if (found := bounds(text, block)) is not None:
        opened, closed = found
        prefix, suffix = text[:opened], text[closed + len(block.end):]
    elif body and (at := text.find(body)) != -1:
        prefix, suffix = text[:at], text[at + len(body):]
    else:
        prefix, suffix = text, ""
    parts = [p for p in (prefix.rstrip(), region, suffix.strip()) if p]
    return "\n\n".join(parts) + "\n" if parts else ""


def splice(target: Path, body: str, block: Block) -> bool:
    """Put ``body`` in ``target``'s region, creating or replacing it.

    The body is stripped of the sentinels themselves before insertion: guidance
    text that quotes its own markers would otherwise terminate the region early
    and leave the remainder loose in the user's file.

    Returns False and writes nothing when the file already says exactly this,
    so a repeated install does not churn an mtime a harness watches.
    """
    body = body.replace(block.start, "").replace(block.end, "").strip()
    if not body:
        return False
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    updated = _rendered(existing, block, body)
    if updated == existing:
        return False
    atomic_write(target, updated)
    return True


def strip(target: Path, block: Block) -> bool:
    """Remove autorun's region, leaving the rest of the file exactly as it was.

    Deleting the file when the region was all it held is deliberate: a file
    autorun created and then emptied is litter, but one the user wrote in stays.
    """
    if not target.is_file():
        return False
    existing = target.read_text(encoding="utf-8")
    if bounds(existing, block) is None:
        return False
    updated = _rendered(existing, block, "")
    if updated:
        atomic_write(target, updated)
    else:
        target.unlink()
    return True


def foreign_slugs(target: Path, known: Iterable[str]) -> tuple[str, ...]:
    """Autorun regions in the file that this version does not recognise.

    A block written by a newer or retired version would otherwise stay forever:
    uninstall only removes slugs it knows, so an unknown one is invisible litter
    in the user's memory file. Reporting them lets a human decide.
    """
    if not target.is_file():
        return ()
    found = set(SENTINEL_RE.findall(target.read_text(encoding="utf-8")))
    return tuple(sorted(found - set(known)))


def demo() -> None:
    """Self-check: create, update, remove, and every malformed shape."""
    import tempfile

    block = Block("guidance")
    user_text = "# My notes\n\nKeep this paragraph.\n"

    with tempfile.TemporaryDirectory() as tmp:
        memory = Path(tmp) / "AGENTS.md"

        # Created beside the user's own text, which survives verbatim.
        memory.write_text(user_text, encoding="utf-8")
        assert splice(memory, "autorun says hello", block) is True
        text = memory.read_text()
        assert "Keep this paragraph." in text
        assert text.count(block.start) == 1 and text.count(block.end) == 1

        # Re-splicing the same body is a no-op, so no mtime churn.
        assert splice(memory, "autorun says hello", block) is False

        # Updating replaces the region in place, never appending a second one.
        assert splice(memory, "autorun says goodbye", block) is True
        text = memory.read_text()
        assert text.count(block.start) == 1, "exactly one region, always"
        assert "goodbye" in text and "hello" not in text
        assert "Keep this paragraph." in text

        # A body quoting the sentinels cannot terminate its own region.
        assert splice(memory, f"see {block.start} and {block.end} markers", block) is True
        assert memory.read_text().count(block.start) == 1

        # Stripping leaves the user's file exactly as they wrote it.
        assert strip(memory, block) is True
        assert memory.read_text() == user_text
        assert strip(memory, block) is False, "already gone"

        # A file that held only our block is removed rather than left empty.
        only_ours = Path(tmp) / "CLAUDE.md"
        splice(only_ours, "just us", block)
        assert strip(only_ours, block) is True
        assert not only_ours.exists()

        # A malformed region is treated as absent: append, never guess a range.
        broken = Path(tmp) / "broken.md"
        broken.write_text(f"user text\n{block.end}\nmore user text\n{block.start}\n", encoding="utf-8")
        before = broken.read_text()
        assert bounds(before, block) is None, "inverted markers are not a region"
        splice(broken, "ours", block)
        assert "more user text" in broken.read_text(), "user text is never swallowed"

        # A pre-sentinel copy is adopted in place, never appended beside itself.
        legacy = Path(tmp) / "legacy.md"
        legacy.write_text("# Theirs\n\nautorun guidance body\n", encoding="utf-8")
        assert splice(legacy, "autorun guidance body", block) is True
        text = legacy.read_text()
        assert text.count("autorun guidance body") == 1, "adopted, not duplicated"
        assert text.count(block.start) == 1 and "# Theirs" in text
        assert strip(legacy, block) is True, "adoption makes it removable"
        assert "autorun guidance body" not in legacy.read_text()

        # Half a region is also not a region.
        half = Path(tmp) / "half.md"
        half.write_text(f"{block.start}\nno end marker\n", encoding="utf-8")
        assert bounds(half.read_text(), block) is None

        # Two different blocks coexist in one file without disturbing each other.
        other = Block("cache")
        both = Path(tmp) / "both.md"
        both.write_text(user_text, encoding="utf-8")
        splice(both, "first", block)
        splice(both, "second", other)
        assert strip(both, block) is True
        text = both.read_text()
        assert "second" in text and "first" not in text and "Keep this paragraph." in text

        # The documented disable switch has a reader, and its tokens match the
        # other bug gates. Restored around the check so a real environment is
        # not left changed by a self-check.
        import os

        previous = os.environ.get(CONTEXT_GUIDANCE_FLAG)
        try:
            os.environ[CONTEXT_GUIDANCE_FLAG] = "false"
            assert context_guidance_enabled() is False
            os.environ[CONTEXT_GUIDANCE_FLAG] = "never"
            assert context_guidance_enabled() is False, "never is the documented spelling"
            os.environ[CONTEXT_GUIDANCE_FLAG] = "auto"
            assert context_guidance_enabled() is True
            del os.environ[CONTEXT_GUIDANCE_FLAG]
            assert context_guidance_enabled() is True, "a workaround is on until turned off"
        finally:
            os.environ.pop(CONTEXT_GUIDANCE_FLAG, None)
            if previous is not None:
                os.environ[CONTEXT_GUIDANCE_FLAG] = previous

        # A block from a version we no longer ship is reported, not ignored.
        assert foreign_slugs(both, known=["guidance"]) == ("cache",)
        assert foreign_slugs(both, known=["guidance", "cache"]) == ()

    print("installer.memory: all self-checks passed")


if __name__ == "__main__":
    demo()
