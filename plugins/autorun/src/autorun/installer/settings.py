#!/usr/bin/env python3
"""One Setting declaration drives resolution, validation, help, and the parser.

Replaces 437 lines across ``ChoiceSetting`` + ``resolve_choice_setting`` +
``resolve_runtime_architecture_settings`` (which hand-rolls the same ladder
twice) + ``_truthy_env`` + the five-function skill-placement trio + the
110-line ``_create_install_module_parser``.

The compression is not terseness, it is removing a repetition: every setting
already answered the same four questions — what values are legal, where it may
be set, what to call it, and how to explain it — and each one answered them in
its own code. Answer them once, in a declaration, and the resolver, the
validator, the ``--help`` text and the argparse flag all fall out of it.

Two divergences this closes, both found by reading the originals:

- ``resolve_choice_setting`` falls through an unrecognised value to the next
  tier; ``_truthy_env`` returns True for anything unrecognised, so
  ``UV_NO_SYNC=garbage`` silently enabled it. One resolver, one rule.
- ``skill_placement_help`` exists because two argparse parsers declare the same
  flag and their prose could drift. Generating both parsers from one tuple
  removes the reason for the workaround rather than maintaining it.

Complexity: resolution is O(tiers) per setting; parser construction is O(S)
for S declarations, once per process.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from typing import Callable, Generic, Iterable, Mapping, Sequence, TypeVar

T = TypeVar("T")

__all__ = [
    "Setting", "Resolved", "one_of", "truthy", "csv_of", "into_tuple", "mapping_of",
    "autorun_config", "workaround_enabled",
    "build_parser", "resolve_all", "INSTALL_SETTINGS", "harness_names",
    "CustomHarness", "parse_custom_harness", "synthesize", "steps_for_custom", "flavors",
]


def autorun_config() -> Mapping[str, object]:
    """autorun's ``CONFIG``, the one place a persisted setting may live.

    Imported inside the call because ``config.py`` pulls in the rest of the
    package; the installer must stay importable on its own.
    """
    from ..config import CONFIG

    return CONFIG


def workaround_enabled(key: str) -> bool:
    """Whether one ``AUTORUN_BUG_*_WORKAROUND_ENABLED`` gate is on. Default on.

    One resolver for every bug gate, so the accepted tokens cannot drift between
    them: ``false``/``0``/``never`` disable, ``true``/``1``/``auto``/``always``
    enable, and anything else falls through to CONFIG. Kept separate from
    :func:`truthy` because the vocabularies differ and the default is the
    opposite: a workaround is on until someone turns it off.

    A key nothing reads is the failure this exists to prevent. The documented
    disable switch silently stops working, and the workaround it names runs
    forever with no way to turn it off.
    """
    value = os.environ.get(key, "").strip().lower()
    if value in ("false", "0", "never"):
        return False
    if value in ("true", "1", "auto", "always"):
        return True
    return bool(autorun_config().get(key, True))


@dataclass(frozen=True, slots=True)
class Resolved(Generic[T]):
    """A value and the tier that supplied it, so ``--status`` can say why."""

    value: T
    source: str


@dataclass(frozen=True, slots=True)
class Setting(Generic[T]):
    """One install setting: legal values, where it may be set, how to explain it.

    ``parse`` returns None for a value this setting does not accept, which is
    what makes an unrecognised entry fall through to the next tier instead of
    aborting an install — the rule the two hand-rolled ladders disagreed on.
    ``aliases`` carry retired spellings so a rename never silently ignores the
    value a user already has exported.
    """

    name: str
    parse: Callable[[str], T | None]
    default: T
    help: str
    flag: str = ""                       # defaults to --name-with-dashes
    short: str = ""                      # e.g. -t, kept because people type it
    choices: tuple[str, ...] = ()        # argparse validation + help rendering
    repeatable: bool = False
    aliases: tuple[str, ...] = ()        # retired env vars and config keys

    @property
    def option(self) -> str:
        return self.flag or "--" + self.name.replace("_", "-")

    @property
    def options(self) -> tuple[str, ...]:
        """Every spelling argparse should accept, long form first.

        ``short`` exists because ``--tool`` has always also been ``-t``, and a
        generated parser that emits only the long form silently breaks the
        shorter one people actually type.
        """
        return (self.option, *((self.short,) if self.short else ()))

    def checked(self, raw: str) -> str:
        """Validate one CLI token, for argparse's ``type=``.

        Returns the token unchanged rather than the parsed value: resolution
        happens once, later, in :meth:`resolve`, and a value parsed here would
        arrive there already transformed. This exists only to move the failure
        from a traceback to argparse's own error path.
        """
        if self.parse(raw) is None:
            raise argparse.ArgumentTypeError(
                f"{raw!r} is not valid for {self.option}. {self.rendered_help()}"
            )
        return raw

    @property
    def env(self) -> str:
        return "AUTORUN_" + self.name.upper()

    def resolve(
        self,
        cli: object = None,
        *,
        env: Mapping[str, str] | None = None,
        config: Mapping[str, object] | None = None,
    ) -> Resolved[T]:
        """CLI > env (then aliases) > config (then aliases) > default.

        Both lookups default to the live source rather than to empty. The
        installer being replaced made ``config`` default to ``{}`` and required
        all four call sites to pass ``CONFIG`` by hand; every setting a caller
        forgot lost its config tier silently, and a user's ``CONFIG`` entry did
        nothing with nothing to say so. The config key is the setting's own
        ``name``, so there is no second spelling to keep in step either.
        """
        env = os.environ if env is None else env
        config = autorun_config() if config is None else config
        tiers: list[tuple[str, object]] = [("cli", cli)]
        tiers += [(f"env {key}", env.get(key)) for key in (self.env, *self.aliases)]
        tiers += [("config", config.get(key)) for key in (self.name, *self.aliases)]
        for source, raw in tiers:
            if raw is None:
                continue
            parsed = self._interpret(raw)
            if parsed is not None:
                return Resolved(parsed, source)
            if source == "cli":
                # Falling through is right for the environment and CONFIG: a
                # stale export should not abort an install. It is wrong for a
                # flag the user just typed, which would install a layout they
                # did not ask for and say nothing. Same split install.py made
                # with `type=skill_placement_token` on the parser only.
                shown = " ".join(str(t) for t in raw) if isinstance(raw, (list, tuple)) else raw
                raise ValueError(
                    f"{self.option}: {shown!r} is not a valid value."
                    + (f" One of: {', '.join(self.choices)}." if self.choices else "")
                )
        return Resolved(self.default, "default")

    def _interpret(self, raw: object) -> T | None:
        """Parse one tier's value, which for a repeatable setting is a sequence.

        ``argparse``'s ``append`` hands back a list, and a config file may hold
        a list or a mapping for the same setting. Stringifying those produced
        ``"['native']"``, which no parser accepts: ``--skill-placement native``
        resolved to the default with nothing said, and
        ``--custom-harness mine=claude:bin:~/.mine`` parsed into a harness named
        ``['mine`` writing to ``~/.mine']``.

        Each element is parsed whole — never re-split — so a value containing a
        space survives, and the results are merged by their own natural
        combination: mappings update left to right, tuples concatenate.
        """
        if isinstance(raw, Mapping):
            # The config form of a per-key setting: {"claude": "native"}, with
            # "default" spelling the bare mode install.py's config accepted.
            raw = [f"{v}" if k == "default" else f"{k}={v}" for k, v in raw.items()]
        if isinstance(raw, (list, tuple)):
            parts = [self.parse(str(item)) for item in raw]
            if not parts or any(part is None for part in parts):
                return None
            return _combine(parts)
        return self.parse(raw if isinstance(raw, str) else str(raw))

    def rendered_help(self) -> str:
        """Help text with the machine-checkable parts filled in, not retyped."""
        parts = [self.help.rstrip(".")]
        if self.choices:
            parts.append(f"One of: {', '.join(self.choices)}")
        parts.append(f"Default: {self.default}")
        parts.append(f"{self.env} also sets this; the flag wins")
        if self.aliases:
            parts.append(f"Retired spellings still accepted: {', '.join(self.aliases)}")
        return ". ".join(parts) + "."


def _combine(parts: Sequence[object]) -> object:
    """Fold repeated values of one setting into a single answer.

    By the parsed type rather than by a per-setting rule: a mapping setting
    (`--skill-placement codex=both --skill-placement claude=native`) merges left
    to right so the later flag wins one key without discarding the others, and a
    tuple setting (`--custom-harness` twice) accumulates both.
    """
    if isinstance(parts[0], dict):
        merged: dict = {}
        for part in parts:
            merged.update(part)  # type: ignore[arg-type]
        return merged
    if isinstance(parts[0], tuple):
        return tuple(item for part in parts for item in part)  # type: ignore[union-attr]
    return parts[-1]


# --- parsers: small, total functions, composed rather than branched -----------


def one_of(*allowed: str) -> Callable[[str], str | None]:
    """Accept a fixed vocabulary, case- and space-insensitively."""
    permitted = frozenset(allowed)

    def parse(raw: str) -> str | None:
        value = raw.strip().lower()
        return value if value in permitted else None

    return parse


def truthy(raw: str) -> bool | None:
    """Accept documented boolean spellings only.

    Returning None for anything else is the whole point: ``_truthy_env``
    returned True for unrecognised input, so a typo enabled the flag it was
    meant to configure.
    """
    return {"1": True, "true": True, "yes": True, "on": True,
            "0": False, "false": False, "no": False, "off": False}.get(raw.strip().lower())


def into_tuple(inner: Callable[[str], T | None]) -> Callable[[str], tuple[T, ...] | None]:
    """Lift a single-value parser so a repeated flag accumulates.

    Without this a repeatable setting whose parser returns one object can only
    ever hold the last one, because ``_combine`` has nothing to concatenate.
    Unlike :func:`csv_of` this never splits the token, so a value containing a
    space or comma (a config directory, say) survives intact.
    """
    def parse(raw: str) -> tuple[T, ...] | None:
        value = inner(raw)
        return None if value is None else (value,)
    return parse


def csv_of(inner: Callable[[str], T | None]) -> Callable[[str], tuple[T, ...] | None]:
    """Lift a value parser to a comma- or whitespace-separated list."""
    def parse(raw: str) -> tuple[T, ...] | None:
        items = [inner(token) for token in re.split(r"[,\s]+", raw) if token]
        return tuple(i for i in items if i is not None) if items and all(i is not None for i in items) else None
    return parse


def mapping_of(
    inner: Callable[[str], T | None], keys: Callable[[], Iterable[str]]
) -> Callable[[str], dict[str, T] | None]:
    """Parse ``VALUE`` and ``KEY=VALUE`` tokens into one decision.

    This is the skill-placement grammar. ``keys`` is a callable so the legal
    harness names come from the registry at parse time rather than being
    duplicated into this module.
    """
    def parse(raw: str) -> dict[str, T] | None:
        known, out = set(keys()), {}
        for token in (t for t in re.split(r"[,\s]+", raw) if t):
            key, sep, value = token.partition("=")
            if not sep:
                key, value = "", token
            parsed = inner(value)
            if parsed is None or (key and key not in known):
                return None
            out[key] = parsed
        return out or None
    return parse


# --- the parser is generated, not written ------------------------------------


def build_parser(
    settings: Sequence[Setting],
    *,
    prog: str,
    description: str,
    flags: Mapping[str, str] = {},
    targets: Iterable[str] = (),
    selections: Iterable[str] = (),
) -> argparse.ArgumentParser:
    """Build an argparse parser from declarations.

    Every ``--flag`` here exists because a Setting declared it, so the two
    parsers this replaces cannot drift — which is the only reason
    ``skill_placement_help()`` was written.

    ``default=None`` on every setting-backed option is load-bearing: an
    argparse-supplied default is indistinguishable from an explicit choice and
    would outrank the environment.

    ``selections`` are the installable plugin names. Passing them is what makes
    a stray word an error: ``--conductor false`` is a boolean flag followed by a
    token, and with an unconstrained positional argparse silently accepted
    ``false`` as the plugin to install while leaving ``--conductor`` on.
    """
    parser = argparse.ArgumentParser(
        prog=prog, description=description, formatter_class=argparse.RawTextHelpFormatter
    )
    installable = sorted({*selections, "all"}) if selections else None
    parser.add_argument(
        "selection", nargs="?", default="all", choices=installable, metavar="PLUGIN",
        help="Plugin to install"
             + (f". One of: {', '.join(installable)}" if installable else "")
             + ". Default: all.",
    )
    for flag, help_text in flags.items():
        parser.add_argument(flag, action="store_true", help=help_text)
    if names := sorted(targets):
        parser.add_argument(
            "--only",
            action="append",
            choices=names,
            metavar="HARNESS",
            help="Install for this harness only; repeat to select several. "
                 f"One of: {', '.join(names)}. Default: every available harness.",
        )
    for setting in settings:
        if setting.parse is truthy:
            # A boolean setting is a flag pair, not a flag taking a value.
            # `store_const` rather than `store_true` keeps "unset" as None, so
            # the environment and CONFIG still get their turn, and both halves
            # route through the same `truthy` parser as every other tier.
            parser.add_argument(
                *setting.options, dest=setting.name, action="store_const", const="true",
                default=None, help=setting.rendered_help(),
            )
            parser.add_argument(
                f"--no-{setting.name.replace('_', '-')}", dest=setting.name,
                action="store_const", const="false", default=None,
                help=f"Opposite of {setting.option}.",
            )
            continue
        parser.add_argument(
            *setting.options,
            dest=setting.name,
            action="append" if setting.repeatable else "store",
            choices=list(setting.choices) or None,
            # A setting with a fixed vocabulary is validated by `choices`.
            # One without still has a parser that can reject, and routing that
            # through argparse turns a typo into the usual `usage: … error: …`
            # and exit 2 rather than a traceback out of `resolve_all`.
            type=None if setting.choices else setting.checked,
            default=None,
            help=setting.rendered_help(),
        )
    return parser


def resolve_all(
    settings: Sequence[Setting],
    namespace: argparse.Namespace,
    *,
    config: Mapping[str, object] | None = None,
) -> dict[str, Resolved]:
    """Resolve every declaration once, at the entry point, and pass values down.

    Re-resolving in a callee re-applies the environment to an already-decided
    value and discards the caller's explicit intent — the bug that made the
    custom-harness path fail under ``AUTORUN_CODEX_HOOK_SOURCE=plugin``.

    ``config`` is for tests that need an isolated mapping. Leave it unset in
    production so every setting reads the live ``CONFIG``.
    """
    return {
        s.name: s.resolve(getattr(namespace, s.name, None), config=config)
        for s in settings
    }


# --- autorun's actual settings, declared once -------------------------------
#
# Everything below is data. The resolver, the validator, the --help text and
# both argparse parsers are generated from it, which is what removes the reason
# `skill_placement_help()` had to exist: two parsers declaring the same flag
# could word it differently, so the prose was factored out while the flags
# stayed duplicated. Generating both from one declaration removes the
# duplication instead of maintaining a workaround for it.


def harness_names() -> tuple[str, ...]:
    """Legal harness names, read from the registry at parse time.

    A callable rather than a literal so the vocabulary cannot drift from the
    registry — the failure being a `--skill-placement qwen=native` that parses
    here and names a harness nothing installs.
    """
    from ..platforms import PLATFORMS

    return tuple(sorted(PLATFORMS))


SKILL_PLACEMENT = Setting(
    name="skill_placement",
    parse=mapping_of(one_of("auto", "native", "both"), harness_names),
    default={"": "auto"},
    help="Where installed skills are written, globally or per harness "
         "(MODE or HARNESS=MODE, repeatable)",
    repeatable=True,
)

SHARED_SKILLS_BRIDGE = Setting(
    name="shared_skills_bridge",
    parse=one_of("link", "copy", "none"),
    default="none",
    help="Mirror the shared skills root into a harness that cannot read it",
    choices=("link", "copy", "none"),
    # Named for Claude when Claude was the only harness the bridge could reach.
    # Antigravity also cannot read ~/.agents/skills, so the capability is not
    # Claude's; the old spellings keep working rather than silently doing nothing.
    aliases=("AUTORUN_CLAUDE_AGENTS_SKILLS", "claude_agents_skills"),
)

#: All four values are load-bearing. ``both`` installs user *and* plugin hooks
#: and is read back from the ownership marker so status can tell a deliberate
#: `both` from an accidental duplicate; ``none`` installs neither. Narrowing
#: this to ("user", "plugin") does not error on the dropped values — an
#: unrecognised value falls through to the next tier by design — so a user
#: running `both` would silently lose their plugin hooks with no message.
CODEX_HOOK_SOURCE = Setting(
    name="codex_hook_source",
    parse=one_of("user", "plugin", "both", "none"),
    default="user",
    help="Where Codex hooks are installed: the user file, the plugin package, both, or neither",
    choices=("user", "plugin", "both", "none"),
)

#: ``github`` publishes against the upstream repository rather than the local
#: personal marketplace. It is a real installation mode with a real source
#: constant behind it, not a synonym for ``personal``.
CODEX_PLUGIN_MARKETPLACE = Setting(
    name="codex_plugin_marketplace",
    parse=one_of("personal", "github"),
    default="personal",
    help="Which Codex plugin marketplace autorun publishes itself to",
    choices=("personal", "github"),
)

HOOK_NO_SYNC = Setting(
    name="hook_no_sync",
    parse=truthy,
    default=True,
    help="Pass --no-sync to uv in hook commands so hooks stay fast after install",
    # uv's own spelling, which users already have exported. Deriving the env
    # name from `name` alone silently ignores it.
    aliases=("UV_NO_SYNC",),
)

#: Pinning the hook interpreter is the only defence against PATH order picking
#: a Python for the wrong CPU architecture — Intel Homebrew under Rosetta on an
#: Apple Silicon host. `UvCommand.python` and `probe_runtime(python=...)` accept
#: the value, so without this declaration the capability exists but is
#: unreachable from the CLI, the environment, or config.
HOOK_PYTHON = Setting(
    name="hook_python",
    parse=lambda raw: raw.strip() or None,
    default="",
    help="Interpreter uv must use for hooks; pins the architecture",
    aliases=("UV_PYTHON", "python"),
)

WRITE_SOURCE_METADATA = Setting(
    name="write_source_metadata",
    parse=truthy,
    default=False,
    help="Regenerate tracked source metadata during install",
)

# --- custom harnesses -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CustomHarness:
    """A harness autorun was told about rather than one it ships support for.

    ``flavor`` is the load-bearing field: it names which known harness's wire
    protocol this target speaks, and it becomes ``hook_entry.py --cli <flavor>``.
    Getting it wrong does not fail loudly — it sends one harness's response
    schema to another, which the receiving CLI may accept and misread.
    """

    name: str
    flavor: str
    binary: str
    config_dir: str
    display_name: str = ""


def parse_custom_harness(spec: str) -> CustomHarness | None:
    """Parse ``name=flavor:binary:config_dir[::display]``.

    ``::`` separates the display name so a ``config_dir`` containing a literal
    colon stays unambiguous — which is why it is not simply split on ``:``.

    Returns None for anything malformed, because that is the contract every
    :class:`Setting` parser follows: an unusable value is not a value, so the
    next tier answers rather than the install aborting.
    """
    name, sep, rest = spec.strip().partition("=")
    if not sep:
        return None
    parts = rest.split(":", 2)
    if len(parts) != 3:
        return None
    flavor, binary, tail = (part.strip() for part in parts)
    config_dir, _, display = tail.partition("::")
    name, config_dir = name.strip(), config_dir.strip()
    from ..platforms import CUSTOM_HARNESS_FLAVOR_ALIASES

    if not (name and binary and config_dir) or flavor not in flavors():
        return None
    return CustomHarness(
        name,
        CUSTOM_HARNESS_FLAVOR_ALIASES[flavor],
        binary,
        config_dir,
        display.strip() or name,
    )


def flavors() -> tuple[str, ...]:
    """Which known harnesses a custom target may impersonate, from the registry."""
    from ..platforms import CUSTOM_HARNESS_FLAVOR_ALIASES

    return tuple(sorted(CUSTOM_HARNESS_FLAVOR_ALIASES))


def synthesize(custom: CustomHarness) -> object:
    """Turn a spec into a real ``Platform``, cloned from the flavor it names.

    This is why the new design needs no branch for custom harnesses. The
    installer it replaces dispatches on flavor with a three-way if/elif —
    ``claude`` to the markdown-commands path, ``codex`` to the Codex installer,
    everything else to the Gemini-family path — which is precisely the
    per-harness branching this package exists to remove.

    A custom harness *is* its flavor with different paths, so cloning the
    flavor's registry entry gives it the right hook protocol, skill routes and
    event map for free. Pair it with the flavor's step tuple and it walks the
    same traversal as every built-in harness, with nothing testing for it.
    """
    from dataclasses import replace

    from ..platforms import PLATFORMS

    # A custom ``claude`` flavor is the documented portable, no-hook layout:
    # Claude-format markdown commands plus AGENTS.md. ForgeCode is the existing
    # registry entry with exactly that install shape.
    base = PLATFORMS["forgecode" if custom.flavor == "claude" else custom.flavor]
    return replace(
        base,
        name=custom.name,
        display_name=custom.display_name or custom.name,
        binary=custom.binary,
        install_flavor=custom.flavor,
        config_dir=custom.config_dir,
        config_dir_env_vars=(),
    )


def steps_for_custom(
    custom: CustomHarness, steps: Mapping[str, tuple]
) -> Mapping[str, tuple]:
    """Register the custom name against its flavor's steps.

    One entry, so ``targets()`` finds the synthesized platform without learning
    that custom harnesses exist.
    """
    flavor = "forgecode" if custom.flavor == "claude" else custom.flavor
    return {**steps, custom.name: steps.get(flavor, ())}


#: Installing the `autorun` and `autorun-install` executables as a uv tool, so
#: the CLI works outside a project directory. Off by default because it writes
#: outside every harness config dir this package otherwise confines itself to.
INSTALL_UV_TOOL = Setting(
    name="tool",
    parse=truthy,
    default=False,
    short="-t",
    help="Also run 'uv tool install' for global CLI availability",
)

#: Conductor is a separate Gemini extension autorun installs alongside its own.
#: On by default, and turned off with `--no-conductor`. Not `--conductor false`:
#: a boolean setting generates a flag pair, so a value after the flag is a
#: separate token, and the one declaration covering both halves is what stops
#: them drifting.
CONDUCTOR = Setting(
    name="conductor",
    parse=truthy,
    default=True,
    help="Install the Conductor extension for the Gemini family",
)

#: How a self-update reaches this installation. `auto` detects it, and the
#: detection order matters: a plugin installation upgraded with pip leaves the
#: harness still loading the old copy from its own cache.
#: `plugin` is the retired spelling for "whichever harness CLI is present",
#: from before claude and gemini were told apart. It stays in the vocabulary
#: because `aliases` carries retired *key* names, not retired *values* — a user
#: with `plugin` in a script would otherwise be told to pick from methods they
#: never chose. `runtime.update_argv` resolves it to whichever CLI is installed.
UPDATE_METHOD = Setting(
    name="update_method",
    parse=one_of("auto", "claude", "gemini", "plugin", "uv", "pip"),
    default="auto",
    help="Force a specific self-update method rather than detecting one",
    choices=("auto", "claude", "gemini", "plugin", "uv", "pip"),
)

#: Repeatable, so the parser must return a tuple: `_combine` accumulates tuples
#: and a bare object would leave only the last `--custom-harness` standing.
CUSTOM_HARNESS = Setting(
    name="custom_harness",
    parse=into_tuple(parse_custom_harness),
    default=(),
    help="Install for an unlisted harness: name=flavor:binary:config_dir[::display]",
    repeatable=True,
    aliases=("custom_harnesses",),
)


#: Every install setting. Adding one here gives it resolution, validation, help
#: text and a flag on both parsers, with no other edit anywhere.
INSTALL_SETTINGS: tuple[Setting, ...] = (
    SKILL_PLACEMENT,
    SHARED_SKILLS_BRIDGE,
    CODEX_HOOK_SOURCE,
    CODEX_PLUGIN_MARKETPLACE,
    HOOK_NO_SYNC,
    HOOK_PYTHON,
    WRITE_SOURCE_METADATA,
    INSTALL_UV_TOOL,
    CONDUCTOR,
    UPDATE_METHOD,
    CUSTOM_HARNESS,
)


def demo() -> None:
    """Self-check: precedence, aliases, fall-through, and a generated parser."""
    # A stand-in for SKILL_PLACEMENT with a fixed harness vocabulary, so the
    # grammar checks below do not change meaning when a harness is registered.
    placement = Setting(
        name="skill_placement",
        parse=mapping_of(one_of("auto", "native", "both"), lambda: ("claude", "codex")),
        default={"": "auto"},
        help="Where installed skills are written",
        choices=(),
        repeatable=True,
    )
    # The shipped declarations, not copies of them: a re-typed Setting here
    # would keep passing after the real one changed.
    bridge, no_sync = SHARED_SKILLS_BRIDGE, HOOK_NO_SYNC

    # Precedence, and the retired spelling still resolving.
    assert bridge.resolve(env={}, config={}).source == "default"
    assert bridge.resolve(env={"AUTORUN_CLAUDE_AGENTS_SKILLS": "link"}, config={}) == Resolved(
        "link", "env AUTORUN_CLAUDE_AGENTS_SKILLS"
    )
    assert bridge.resolve(
        env={"AUTORUN_SHARED_SKILLS_BRIDGE": "copy", "AUTORUN_CLAUDE_AGENTS_SKILLS": "link"},
        config={},
    ).value == "copy", "the current spelling outranks its alias"
    assert bridge.resolve("none", env={"AUTORUN_SHARED_SKILLS_BRIDGE": "link"}, config={}).value == "none"

    # An unrecognised value falls through instead of aborting or being coerced.
    assert bridge.resolve(env={"AUTORUN_SHARED_SKILLS_BRIDGE": "typo"}, config={}).source == "default"
    assert no_sync.resolve(env={"AUTORUN_HOOK_NO_SYNC": "garbage"}, config={}).value is True
    assert no_sync.resolve(env={"AUTORUN_HOOK_NO_SYNC": "off"}, config={}).value is False

    # The per-harness grammar, rejecting an unknown harness rather than dropping it.
    assert placement.parse("native") == {"": "native"}
    assert placement.parse("codex=both claude=native") == {"codex": "both", "claude": "native"}
    assert placement.parse("nosuch=both") is None

    # The parser is generated; help carries the machine-checkable parts.
    parser = build_parser(
        [bridge, placement],
        prog="autorun",
        description="install",
        flags={"--force": "Reinstall"},
        targets=("claude", "codex"),
    )
    args = parser.parse_args(["--shared-skills-bridge", "link", "--only", "codex", "--force"])
    assert (args.shared_skills_bridge, args.only, args.force) == ("link", ["codex"], True)
    assert parser.parse_args([]).shared_skills_bridge is None, "no CLI value must stay None"
    assert "Retired spellings" in bridge.rendered_help()
    assert resolve_all([bridge], args, config={})["shared_skills_bridge"].value == "link"

    # The real declarations resolve, and every one is reachable from a parser.
    real = build_parser(INSTALL_SETTINGS, prog="autorun", description="install",
                        flags={"--force": "Reinstall"}, targets=harness_names())
    parsed = real.parse_args(["--shared-skills-bridge", "link", "--codex-hook-source", "plugin"])
    resolved = resolve_all(INSTALL_SETTINGS, parsed, config={})
    assert resolved["shared_skills_bridge"].value == "link"
    assert resolved["codex_hook_source"].value == "plugin"
    assert resolved["skill_placement"].value == {"": "auto"}, "untouched settings keep defaults"
    assert resolved["hook_no_sync"].value is True

    # The config tier reads CONFIG with nobody passing it. The installer being
    # replaced defaulted this to {} and made four call sites remember; a
    # setting one of them missed lost the tier with nothing to say so.
    live = autorun_config()
    absent = object()
    previous = live.get(bridge.name, absent)
    live[bridge.name] = "copy"
    try:
        assert bridge.resolve(env={}) == Resolved("copy", "config")
        assert bridge.resolve("link", env={}).value == "link", "the flag still wins"
    finally:
        if previous is absent:
            live.pop(bridge.name, None)
        else:
            live[bridge.name] = previous

    # Every declaration has a distinct name, flag and env var, or one silently
    # shadows another at whichever tier they collide on.
    for attribute in ("name", "option", "env"):
        seen = [getattr(s, attribute) for s in INSTALL_SETTINGS]
        assert len(seen) == len(set(seen)), f"duplicate {attribute}: {seen}"

    # Per-harness placement only accepts registered harnesses.
    assert SKILL_PLACEMENT.parse("codex=native")["codex"] == "native"
    assert SKILL_PLACEMENT.parse("nosuchharness=native") is None

    print("installer.settings: all self-checks passed")


if __name__ == "__main__":
    demo()
