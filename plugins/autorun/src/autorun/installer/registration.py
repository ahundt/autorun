#!/usr/bin/env python3
"""Telling a harness its plugin exists, as data rather than seven procedures.

The walk in ``traversal`` puts files where a harness will find them. Four
harnesses additionally want to be *told*, through their own CLI, and that is a
different kind of work: it spawns processes, it can fail for reasons no file
system check predicts, and its result is a sentence rather than a decision.

Each harness declares a sequence of commands with placeholders. Nothing here
branches on a harness name — the differences that used to be seven procedures
are five tuples, and adding a harness is a row.

WHY UPDATE BEFORE INSTALL
=========================

``update`` preserves settings and is faster; ``install`` is the fresh path. A
harness that already has the plugin and is sent ``install`` may report an error
that means "already there", which reads as a failure. Trying update first and
treating "already" as success on either makes the sequence idempotent, which is
what lets an install run on every change without asking whether it is the first.

WHY "already" IS SUCCESS
========================

Every one of these CLIs signals an existing installation by exit status plus a
message, and the messages differ. Treating a non-zero exit as fatal made a
second install fail; matching the word is what the installer being replaced
did, and the behaviour is preserved because it is what these CLIs were tested
against.

Complexity: O(commands) subprocesses, each bounded by ``timeout``. A forced
extension registration gets at most one install retry; a hung CLI still costs
one timeout rather than several.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from . import codex
from .runtime import Outcome, Runner, _spawn

__all__ = [
    "Registration", "REGISTRATIONS", "register", "withdraw", "substitute",
    "register_entry", "withdraw_entry", "COMPANIONS", "companions",
    "ALREADY", "with_binary",
]

#: Words these CLIs use for "it is already there", which is success, not
#: failure. Matched case-insensitively against both streams because they do not
#: agree on which one carries it.
ALREADY = ("already", "up to date", "up-to-date")
ABSENT = ("not found", "not installed", "does not exist", "isn't installed")

_CODEX_REMOVE = (
    ("codex", "plugin", "remove", "{name}@personal"),
    ("codex", "plugin", "remove", "autorun@personal"),
    ("codex", "plugin", "remove", "{name}@autorun"),
    ("codex", "plugin", "remove", "autorun@autorun"),
)


@dataclass(frozen=True, slots=True)
class Registration:
    """How one harness is told about a plugin, and how it is told to forget.

    ``install`` runs in order and stops at the first genuine failure.
    ``remove`` is the uninstall counterpart and never stops: withdrawing from a
    harness that no longer has the plugin is not an error, and stopping there
    would leave the remaining harnesses registered.
    """

    install: tuple[tuple[str, ...], ...] = ()
    remove: tuple[tuple[str, ...], ...] = ()
    #: Tried before ``install``; success skips the rest of ``install``.
    refresh: tuple[tuple[str, ...], ...] = ()
    binary: str = ""
    #: A forced extension uninstall can leave the harness registered but empty.
    #: Retry one failed install without repeating the destructive uninstall.
    retry_after_force: bool = False


def with_binary(entry: Registration, binary: str) -> Registration:
    """Clone one flavor's registration commands for a custom binary."""
    def commands(rows: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
        return tuple((binary, *row[1:]) for row in rows)

    return Registration(
        install=commands(entry.install),
        remove=commands(entry.remove),
        refresh=commands(entry.refresh),
        binary=binary,
        retry_after_force=entry.retry_after_force,
    )


def substitute(argv: Sequence[str], values: Mapping[str, str]) -> tuple[str, ...]:
    """Fill ``{name}``-style placeholders in one command.

    A missing key leaves the placeholder in place rather than raising: the
    command then fails visibly with the literal text in it, which is a better
    report than a KeyError from inside the installer.
    """
    filled = []
    for token in argv:
        for key, value in values.items():
            token = token.replace("{" + key + "}", value)
        filled.append(token)
    return tuple(filled)


#: The four harnesses with a plugin CLI. The other three read files directly, so
#: the walk is the whole install for them and they have no entry here — which is
#: the same "absent means nothing to do" rule the step table uses.
#:
#: Claude takes the marketplace directory itself; the Gemini family takes a
#: directory to install from; Codex takes `<plugin>@<market>`.
REGISTRATIONS: Mapping[str, Registration] = {
    "claude": Registration(
        binary="claude",
        install=(
            ("claude", "plugin", "marketplace", "add", "{root}"),
            ("claude", "plugin", "install", "{name}@{market}"),
            ("claude", "plugin", "enable", "{name}@{market}"),
        ),
        refresh=(
            ("claude", "plugin", "update", "{name}@{market}"),
            ("claude", "plugin", "enable", "{name}@{market}"),
        ),
        remove=(("claude", "plugin", "uninstall", "{name}@{market}"),),
    ),
    "codex": Registration(
        binary="codex",
        install=(("codex", "plugin", "add", "{name}@{market}"),),
        remove=_CODEX_REMOVE,
    ),
    "codex:personal": Registration(
        binary="codex",
        install=(("codex", "plugin", "add", "{name}@{market}"),),
        remove=_CODEX_REMOVE,
    ),
    "codex:github": Registration(
        binary="codex",
        install=(
            (
                "codex", "plugin", "marketplace", "add",
                codex.GITHUB_MARKETPLACE_SOURCE,
            ),
            ("codex", "plugin", "add", "{name}@{market}"),
        ),
        remove=_CODEX_REMOVE,
    ),
    "gemini": Registration(
        binary="gemini",
        install=(("gemini", "extensions", "install", "{extension}"),),
        remove=(("gemini", "extensions", "uninstall", "{name}"),),
        retry_after_force=True,
    ),
    "qwen": Registration(
        binary="qwen",
        install=(("qwen", "extensions", "install", "{extension}"),),
        remove=(("qwen", "extensions", "uninstall", "{name}"),),
        retry_after_force=True,
    ),
    "antigravity": Registration(
        binary="agy",
        install=(("agy", "plugin", "install", "{extension}"),),
        remove=(("agy", "plugin", "uninstall", "{name}"),),
        retry_after_force=True,
    ),
}


#: Extensions autorun offers to install *alongside* itself, keyed by the
#: harness that hosts them. Not autorun's own artifacts: nothing here is
#: removed on uninstall unless the user asked for it, because a tool that
#: uninstalls a second product as a side effect is not one to trust.
#:
#: Conductor is a Gemini-family planning extension (Context → Spec → Plan →
#: Implement). It is opt-out rather than opt-in because it was installed by
#: default before the setting existed, and quietly dropping it on upgrade would
#: take away something users already have.
COMPANIONS: Mapping[str, Mapping[str, Registration]] = {
    "gemini": {
        "conductor": Registration(
            binary="gemini",
            install=(
                (
                    "gemini", "extensions", "install",
                    "https://github.com/gemini-cli-extensions/conductor",
                    # --auto-update keeps it current without autorun tracking a
                    # second product's versions; --consent because the install
                    # is non-interactive and would otherwise block on a prompt.
                    "--auto-update", "--consent",
                ),
            ),
            remove=(("gemini", "extensions", "uninstall", "conductor"),),
        ),
    },
}


def companions(
    harness: str,
    wanted: Iterable[str],
    values: Mapping[str, str],
    *,
    removing: bool = False,
    run: Runner = _spawn,
    available: Iterable[str] | None = None,
) -> Mapping[str, tuple[Outcome, ...]]:
    """Install or remove the extensions this harness hosts alongside autorun.

    Keyed by companion name so a caller reports which one failed. A companion
    that fails never fails the install: it is an optional second product, and
    autorun works without it.
    """
    asked = set(wanted)
    done = {
        name: (
            withdraw_entry(entry, values, run=run, available=available, label=name)
            if removing
            else register_entry(entry, values, run=run, available=available, label=name)
        )
        for name, entry in COMPANIONS.get(harness, {}).items()
        if name in asked
    }
    # A companion whose host CLI is absent ran nothing, so it is left out
    # entirely rather than reported as an empty result a caller must interpret.
    return {name: outcomes for name, outcomes in done.items() if outcomes}


def _ran(
    result: subprocess.CompletedProcess,
    *,
    absent: bool = False,
    already: bool = True,
) -> bool:
    """Whether a command achieved its goal, including "it was already done"."""
    if result.returncode == 0:
        return True
    text = f"{result.stdout or ''}{result.stderr or ''}".lower()
    accepted = (ALREADY if already else ()) + (ABSENT if absent else ())
    return _contains_any(text, accepted)


def _contains_any(text: str, words: Iterable[str]) -> bool:
    """Match CLI status vocabulary without coupling callers to one spelling."""
    return any(word in text.lower() for word in words)


def _first_line(text: str) -> str:
    return next((line.strip() for line in (text or "").splitlines() if line.strip()), "")


def _perform(
    argv: Sequence[str], values: Mapping[str, str], run: Runner, step: str,
    *, absent: bool = False, already: bool = True,
) -> Outcome:
    filled = substitute(argv, values)
    try:
        result = run(filled)
    except (OSError, subprocess.SubprocessError) as error:
        return Outcome(step, False, f"{type(error).__name__}: {error}")
    if _ran(result, absent=absent, already=already):
        return Outcome(step, True, "")
    return Outcome(step, False, _first_line(result.stderr or result.stdout))


def _sequence(
    commands: Iterable[Sequence[str]],
    values: Mapping[str, str],
    run: Runner,
    harness: str,
    *,
    stop: bool,
    absent: bool = False,
    already: bool = True,
) -> tuple[Outcome, ...]:
    """Run commands in order, optionally stopping at the first failure.

    ``stop`` is the difference between the two directions: `enable` after a
    failed `install` is pointless, while withdrawing from one harness must not
    prevent withdrawing from the next.
    """
    done: list[Outcome] = []
    for argv in commands:
        outcome = _perform(
            argv, values, run, f"{harness}: {' '.join(argv[:3])}",
            absent=absent, already=already,
        )
        done.append(outcome)
        if stop and not outcome.ok:
            break
    return tuple(done)


def register(
    harness: str,
    values: Mapping[str, str],
    *,
    run: Runner = _spawn,
    available: Iterable[str] | None = None,
    force: bool = False,
) -> tuple[Outcome, ...]:
    """Tell one harness about the plugin. Returns one outcome per command run.

    An empty result means this harness has nothing to register, which is the
    answer for the three that only read files. A harness whose binary is not
    installed also returns empty rather than a failure: not having Codex is not
    a broken autorun install.

    ``refresh`` is tried first and, when it succeeds, ``install`` is skipped.
    """
    entry = REGISTRATIONS.get(harness)
    return () if entry is None else register_entry(
        entry, values, run=run, available=available, label=harness, force=force
    )


def register_entry(
    entry: Registration,
    values: Mapping[str, str],
    *,
    run: Runner = _spawn,
    available: Iterable[str] | None = None,
    label: str = "",
    force: bool = False,
) -> tuple[Outcome, ...]:
    """Run one registration's install path. The shared half of the two tables.

    Written once because ``REGISTRATIONS`` and ``COMPANIONS`` hold the same
    kind of value and must behave identically; a second copy would be where
    "already installed is success" quietly stopped applying to companions.
    """
    if available is not None and entry.binary not in available:
        return ()
    name = label or entry.binary
    refreshed = _sequence(entry.refresh, values, run, name, stop=True)
    if refreshed and all(outcome.ok for outcome in refreshed):
        return refreshed
    first = _sequence(
        entry.install,
        values,
        run,
        name,
        stop=True,
        already=not (force and entry.retry_after_force),
    )
    if not (force and entry.retry_after_force and first and any(not outcome.ok for outcome in first)):
        return first
    # A forced uninstall followed by "already installed" means the CLI kept a
    # registration but lost its files; retrying cannot repair that state and
    # would only hide the actionable failure. Other failures get one retry.
    if any(
        _contains_any(outcome.detail, ALREADY)
        for outcome in first
        if not outcome.ok
    ):
        return first
    return _sequence(entry.install, values, run, name, stop=True, already=True)


def withdraw(
    harness: str,
    values: Mapping[str, str],
    *,
    run: Runner = _spawn,
    available: Iterable[str] | None = None,
) -> tuple[Outcome, ...]:
    """Tell one harness to forget the plugin.

    Never stops at a failure: withdrawing from a harness that no longer has the
    plugin is not an error, and stopping would leave later commands unrun.
    """
    entry = REGISTRATIONS.get(harness)
    return () if entry is None else withdraw_entry(
        entry, values, run=run, available=available, label=harness
    )


def withdraw_entry(
    entry: Registration,
    values: Mapping[str, str],
    *,
    run: Runner = _spawn,
    available: Iterable[str] | None = None,
    label: str = "",
) -> tuple[Outcome, ...]:
    """Run one registration's removal path, never stopping at a failure."""
    if available is not None and entry.binary not in available:
        return ()
    return _sequence(
        entry.remove, values, run, label or entry.binary, stop=False, absent=True
    )


def demo() -> None:
    """Self-check: placeholders, idempotence, ordering, and absence."""
    calls: list[tuple[str, ...]] = []

    def ok(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    def already(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 1, "", "Plugin already installed")

    def fails(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 1, "", "network unreachable")

    values = {"name": "ar", "market": "autorun", "root": "/repo", "extension": "/staged"}

    # Placeholders are filled, and a missing key stays visible rather than raising.
    assert substitute(("a", "{name}@{market}"), values) == ("a", "ar@autorun")
    assert substitute(("{nope}",), values) == ("{nope}",)

    # Claude refreshes first, and a successful refresh skips the install path.
    calls.clear()
    outcomes = register("claude", values, run=ok)
    assert all(o.ok for o in outcomes)
    assert calls == [
        ("claude", "plugin", "update", "ar@autorun"),
        ("claude", "plugin", "enable", "ar@autorun"),
    ], calls

    # A failed refresh falls through to the full install sequence.
    calls.clear()

    def refresh_then_install(argv):
        # Only `update` fails, which is the real case: nothing to update yet.
        return (fails if "update" in argv else ok)(argv)

    register("claude", values, run=refresh_then_install)
    assert calls[0][:3] == ("claude", "plugin", "update")
    assert calls[1][:4] == ("claude", "plugin", "marketplace", "add"), calls

    # "already installed" is success, not failure: a second install must not
    # report an error for a state that is exactly what was asked for.
    calls.clear()
    assert all(o.ok for o in register("codex", values, run=already))

    # A genuine failure stops the sequence, because enable depends on install.
    calls.clear()
    outcomes = register("codex", values, run=fails)
    assert outcomes and not outcomes[0].ok
    assert "network unreachable" in outcomes[0].detail

    # Withdrawal never stops early, or a later harness stays registered.
    calls.clear()
    assert len(withdraw("claude", values, run=fails)) == len(
        REGISTRATIONS["claude"].remove
    )

    # A harness with no CLI, and one whose binary is absent, both do nothing.
    assert register("forgecode", values, run=ok) == ()
    assert register("codex", values, run=ok, available=()) == ()
    assert withdraw("codex", values, run=ok, available=()) == ()

    # A missing binary is reported, never raised into the install.
    def explodes(argv):
        raise FileNotFoundError("claude")

    crashed = register("codex", values, run=explodes)
    assert crashed and not crashed[0].ok and "FileNotFoundError" in crashed[0].detail

    # A companion is a second product: it installs alongside, is keyed by name
    # so a caller can say which one failed, and is only touched when asked for.
    calls.clear()
    done = companions("gemini", ["conductor"], values, run=ok)
    assert set(done) == {"conductor"} and all(o.ok for o in done["conductor"])
    assert calls[0][:3] == ("gemini", "extensions", "install"), calls
    assert "--consent" in calls[0], "a non-interactive install would block on a prompt"

    assert companions("gemini", [], values, run=ok) == {}, "only what was asked for"
    assert companions("codex", ["conductor"], values, run=ok) == {}
    assert companions("gemini", ["conductor"], values, run=ok, available=()) == {}

    calls.clear()
    companions("gemini", ["conductor"], values, removing=True, run=ok)
    assert calls == [("gemini", "extensions", "uninstall", "conductor")], calls

    # A failing companion is reported, never fatal: autorun works without it.
    failed = companions("gemini", ["conductor"], values, run=fails)
    assert not failed["conductor"][0].ok

    # Every registration names a harness the registry knows.
    from ..platforms import PLATFORMS

    assert not set(REGISTRATIONS) - set(PLATFORMS)
    assert not set(COMPANIONS) - set(PLATFORMS)

    print("installer.registration: all self-checks passed")


if __name__ == "__main__":
    demo()
