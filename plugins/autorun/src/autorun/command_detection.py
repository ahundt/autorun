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
"""
Robust command detection using bashlex AST parsing.

v8 Features:
- Wrapper-spec detection: catches child commands in rtk/tsb/sudo/env/timeout
- Recursive shell -c parsing: catches rm in "sh -c 'rm file'"
- HOT PATH caching: `_extract_cached` for command_matches_pattern
- End-of-options (--) handling
- Fixed GIT_SUBCOMMANDS scope

References:
- https://github.com/idank/bashlex
- https://github.com/idank/bashlex/blob/master/bashlex/ast.py
"""
from __future__ import annotations

import importlib.util
import logging
import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import chain
from typing import Final

__all__ = [
    "command_matches_pattern",
    "command_tokens_for",
    "extract_commands",
    "git_subcommand_index",
    "nested_scripts",
    "ParsedPattern",
    "ExtractedCommands",
    "BASHLEX_AVAILABLE",
    "shell_command_from_tool_input",
    "shells_running_downloads",
    "strip_transparent_command_wrappers",
]

logger = logging.getLogger(__name__)


# ─── Constants ────────────────────────────────────────────────────────────────

GIT_SUBCOMMANDS: Final[frozenset[str]] = frozenset({
    "add", "commit", "reset", "checkout", "stash", "clean",
    "push", "pull", "fetch", "merge", "rebase",
    "branch", "tag", "remote", "log", "diff", "status",
})

# Long spellings of a short flag a default pattern names, per subcommand. Both
# the pattern and the command are read through this, so `git clean -f` and
# `git clean --force` block (and `/ar:ok`) the same commands.
_GIT_FLAG_ALIASES: Final[dict[str, dict[str, str]]] = {
    "clean": {"--force": "-f"},
    "add": {"--all": "-A"},
}

GIT_GLOBAL_OPTIONS_WITH_ARG: Final[frozenset[str]] = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--git-common-dir", "--super-prefix", "--exec-path",
    "--attr-source", "--list-cmds", "--config-env",
})

# Commands that run a script given with -c, or read from stdin without it.
# su is here, not among the wrappers: it hands -c to the target user's shell.
SHELL_EXEC_COMMANDS: Final[frozenset[str]] = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "fish", "su",
})

# -c alone or bundled with other single-letter options: -lc, -ec, -xec.
_SHELL_C_OPTION: Final[re.Pattern[str]] = re.compile(r"-[A-Za-z]*c[A-Za-z]*")

# Commands whose output is a network download. A shell that runs that output
# runs commands no guard here ever reads.
# ponytail: curl and wget only; add fetch/aria2c/httpie if they show up.
NETWORK_DOWNLOADERS: Final[frozenset[str]] = frozenset({"curl", "wget"})

# `<(curl` / `$(wget`, for the shlex fallback, which has no substitution nodes.
_SUBSTITUTED_DOWNLOAD: Final[re.Pattern[str]] = re.compile(
    r"[<$]\(\s*(?:\S*/)?(?:" + "|".join(sorted(NETWORK_DOWNLOADERS)) + r")\b"
)

# find actions whose following words, up to `;` or `{} +`, are a command.
_FIND_EXEC_ACTIONS: Final[frozenset[str]] = frozenset({"-exec", "-execdir", "-ok", "-okdir"})

# What Windows will run when the name is typed without one. `.ps1` is absent:
# PowerShell will not run it as a bare command name.
_WINDOWS_EXECUTABLE_SUFFIXES: Final[frozenset[str]] = frozenset({
    "exe", "com", "bat", "cmd",
})

_SHELL_OPERATORS: Final[re.Pattern[str]] = re.compile(r"\s*(?:&&|\|\||[|;&\n])\s*")
# Consume quoted/escaped text before recognizing an unquoted separator.
_QUOTED_SHELL_OPERATORS: Final[re.Pattern[str]] = re.compile(
    r"""'[^']*'|"(?:\\[\s\S]|[^"\\])*"|\\[\s\S]|[<>]&|&>>?|(?P<separator>&&|\|\||[|;&\n])"""
)
# Keep quote provenance until shell syntax is removed; quoted words are data.
_FALLBACK_WORD = r"""(?:'[^']*'|"(?:\\[\s\S]|[^"\\])*"|\\[\s\S]|[^\s'"\\()])+"""
_FALLBACK_PREFIX = re.compile(
    r"(?:do|then|else|elif|if|while|until|coproc)(?=\s|[({!]|$)\s*"
    r"|time(?:\s+-p)?(?=\s|[({!]|$)(?!\s+-)\s*"
    r"|\(|(?:\{|!)(?=\s|$)\s*"
    r"|function\s+\w+(?:\s*\(\s*\))?\s*"
    rf"|case\s+{_FALLBACK_WORD}\s+in\s+"
    rf"|{_FALLBACK_WORD}\)\s*"
)
_CMD_CACHE_SIZE: Final[int] = 512   # v8: Increased for hot path
_PATTERN_CACHE_SIZE: Final[int] = 64
_MAX_RECURSION_DEPTH: Final[int] = 3
_LEADING_REDIRECT = re.compile(
    rf"\s*(?:\d+)?(?:<<<|<<-?|>>|<>|>&|<&|>\||&>>?|[<>])\s*{_FALLBACK_WORD}"
)
_FALLBACK_ARGV = re.compile(rf"(?P<redirect>{_LEADING_REDIRECT.pattern})|{_FALLBACK_WORD}")
_NAMED_DESCRIPTOR = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_SHELL_QUOTES = re.compile(
    r"""\$'(?:\\[\s\S]|[^'\\])*'|\$?"(?:\\[\s\S]|[^"\\])*"|'[^']*'|\\[\s\S]"""
    r"|(?<!\S)\{[A-Za-z0-9_./+-]+(?:,[A-Za-z0-9_./+-]+)+\}(?!\S)"
)
_ANSI_ESCAPE = re.compile(r"\\(x[0-9a-fA-F]{1,2}|u[0-9a-fA-F]{1,4}|U[0-9a-fA-F]{1,8}|[0-7]{1,3}|[\s\S])")


class _UninspectableScript(ValueError):
    """Executable shell text cannot be determined without running the command."""


# ─── Optional bashlex ─────────────────────────────────────────────────────────

# Found here, imported on the first command parse (_bashlex below). Importing
# bashlex rebuilds its whole LALR table -- its bundled PLY never reads the
# parsetab it ships -- which was half of every CLI hook call, and Stop,
# PostToolUse and prompt events never parse a command.
BASHLEX_AVAILABLE: Final[bool] = importlib.util.find_spec("bashlex") is not None


# ─── Data Classes ─────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ParsedPattern:
    """Immutable parsed pattern for efficient matching."""
    base: str
    flags: frozenset[str]
    positional: frozenset[str]
    is_single_word: bool

    @classmethod
    @lru_cache(maxsize=_PATTERN_CACHE_SIZE)
    def from_string(cls, pattern: str) -> "ParsedPattern":
        """Factory: Parse pattern string into structured form (cached)."""
        tokens = pattern.split()
        if not tokens:
            return cls("", frozenset(), frozenset(), True)

        first = tokens[0]
        if first == "git":
            return cls._from_git_tokens(tokens)

        cmd_parts, flags, positional = [first], set(), set()

        for token in tokens[1:]:
            if token.startswith("-"):
                _add_flag_tokens(flags, token)
            else:
                positional.add(token)

        return cls(
            " ".join(cmd_parts),
            frozenset(flags),
            frozenset(positional),
            len(tokens) == 1
        )

    @classmethod
    def _from_git_tokens(cls, tokens: list[str]) -> "ParsedPattern":
        """Parse git commands with global options before the subcommand.

        Git accepts forms such as `git -C /repo -c key=value push`. The
        subcommand is still `push`, so block patterns like `git push` must match
        after those global options are skipped.
        """
        cmd_parts, flags, positional = ["git"], set(), set()

        sub_idx = git_subcommand_index(tokens)
        _collect_git_global_flags(tokens, sub_idx, flags)

        i = sub_idx
        aliases: dict[str, str] = {}
        if i < len(tokens):
            token = tokens[i]
            if token in GIT_SUBCOMMANDS:
                cmd_parts.append(token)
                aliases = _GIT_FLAG_ALIASES.get(token, {})
            else:
                positional.add(token)
            i += 1

        for token in tokens[i:]:
            if token.startswith("-"):
                _add_flag_tokens(flags, aliases.get(token, token))
            else:
                positional.add(token)

        return cls(
            " ".join(cmd_parts),
            frozenset(flags),
            frozenset(positional),
            len(tokens) == 1,
        )


@dataclass(frozen=True, slots=True)
class CommandWrapperSpec:
    """Grammar for wrappers/prefixes whose argv contains another command.

    The parser is intentionally explicit. Arbitrary wrapper inference creates
    safety bugs in both directions: bypasses (`tsb git push`) and false
    positives (`echo rm`). Each spec declares which wrapper options consume
    values and whether fixed positional operands appear before the child
    command.
    """

    flags_with_arg: frozenset[str] = frozenset()
    stop_flags: frozenset[str] = frozenset()
    leading_positional_count: int = 0
    skip_env_assignments: bool = False
    optional_subcommands: frozenset[str] = frozenset()
    # Options that consume more than one word: `bwrap --bind SRC DEST`.
    flag_arity: Mapping[str, int] = field(default_factory=dict)
    # The child argv is joined and run as shell script text (eval, watch), so
    # it is read as `sh -c "<joined>"`.
    args_are_script: bool = False
    script_flags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ExtractedCommands:
    """Immutable extraction result with all potential commands."""
    names: frozenset[str]
    strings: frozenset[str]
    all_potential: frozenset[str]
    # Shells whose script cannot be inspected: a curl/wget download or nesting
    # beyond the scan limit. The latter has inspection_incomplete=True.
    fetched_shells: frozenset[str] = frozenset()
    # Script text the commands run (`bash -c` bodies, eval, here-strings,
    # heredocs and echo piped to a shell, find -exec), nested ones included,
    # in the order found.
    scripts: tuple[str, ...] = ()
    # None marks an unsuccessful parse; otherwise cached (source words, argv) pairs.
    segments: tuple[tuple[str, list[str]], ...] | None = None
    inspection_incomplete: bool = False

    def matches_single_word(self, pattern: str) -> bool:
        """O(1) lookup in all potential commands."""
        return pattern in self.all_potential

    def matches_pattern(self, p: ParsedPattern) -> bool:
        """Check if any command string matches parsed pattern."""
        return any(self.string_matches(cs, p) for cs in self.strings)

    @staticmethod
    def string_matches(cmd_str: str, p: ParsedPattern) -> bool:
        """Match one extracted command string against a parsed pattern."""
        cmd = ParsedPattern.from_string(cmd_str)
        # Base: exact or prefix with space
        if cmd.base != p.base and not cmd.base.startswith(p.base + " "):
            return False
        # Flags: all pattern flags must be present
        if not p.flags <= cmd.flags:
            return False
        # Positional: exact or prefix match for "dd if="
        for pos in p.positional:
            if pos.endswith("="):
                if not any(cp.startswith(pos) for cp in cmd.positional):
                    return False
            elif pos not in cmd.positional:
                return False
        return True


# ─── Extraction Helpers ───────────────────────────────────────────────────────

def _get_basename(path: str) -> str:
    """Extract the command name: `/bin/rm` -> `rm`, `C:\\...\\git.exe` -> `git`.

    Both separators, because the hook parses the same command strings wherever
    it runs and `os.path.basename` splits backslashes only on Windows. The
    Windows executable suffixes come off for the same reason argv[0]'s
    directory does: `rm.exe` is what the file is actually called, so reading it
    literally let every command block be bypassed by spelling it out. A POSIX
    file genuinely named `deploy.bat` is read as `deploy`, which only matters
    if a pattern goes by that name.
    """
    idx = max(path.rfind("/"), path.rfind("\\"))
    name = path[idx + 1:] if idx >= 0 else path
    stem, dot, suffix = name.rpartition(".")
    return stem if dot and suffix.lower() in _WINDOWS_EXECUTABLE_SUFFIXES else name


def _add_flag_tokens(flags: set[str], token: str) -> None:
    """Add a command-line flag, preserving the previous compact-short behavior."""
    if token.startswith("--"):
        flags.add(token)
    elif len(token) > 2:
        flags.update(f"-{c}" for c in token[1:])
    else:
        flags.add(token)


def _git_option_head(token: str) -> str:
    """Return the option name for `--opt=value`; otherwise the token itself."""
    return token.split("=", 1)[0] if "=" in token else token


_RTK_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset(),
    stop_flags=frozenset({"-h", "--help", "-V", "--version"}),
    optional_subcommands=frozenset({"proxy"}),
)

_TSB_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({
        "-s", "--sandbox",
        "-p", "--profile",
        "-e", "--env",
        "--mount",
        "--add-read-path",
        "--add-write-path",
        "--allow-file",
    }),
    stop_flags=frozenset({"-h", "--help", "-V", "--version"}),
)

_SUDO_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({
        "-u", "--user",
        "-g", "--group",
        "-h", "--host",
        "-p", "--prompt",
        "-C", "--close-from",
        "-D", "--chdir",
        "-R", "--chroot",
        "-T", "--command-timeout",
        "-U", "--other-user",
    }),
    # -k is deliberately absent: `sudo -k <command>` runs the command with the
    # cached credentials ignored, so stopping there hid every wrapped command
    # from the guard. -K is the one that "is not possible to use ... in
    # conjunction with a command", and falls through as an ordinary flag.
    stop_flags=frozenset({"-h", "--help", "-V", "--version", "-l", "--list", "-v", "--validate"}),
)

_ENV_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    # --block-signal, --default-signal and --ignore-signal are documented with a
    # bracketed value (`--block-signal[=SIG]`), so GNU env reads a value only in
    # the attached form and the following word is the command. Listing them here
    # ate that command. The attached form needs no entry: an option carrying its
    # own `=value` is skipped whole.
    flags_with_arg=frozenset({
        "-u", "--unset",
        "-C", "--chdir",
        "-S", "--split-string",
        "-a", "--argv0",
    }),
    stop_flags=frozenset({"-h", "--help", "--version"}),
    skip_env_assignments=True,
)

_TIMEOUT_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({"-k", "--kill-after", "-s", "--signal"}),
    stop_flags=frozenset({"--help", "--version"}),
    leading_positional_count=1,
)

_NICE_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({"-n", "--adjustment"}),
    stop_flags=frozenset({"--help", "--version"}),
)

_IONICE_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({"-c", "--class", "-n", "--classdata"}),
    stop_flags=frozenset({"-h", "--help", "-V", "--version"}),
)

_CHROOT_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    # --skip-chdir takes no value; listing it here consumed NEWROOT, which then
    # let the leading positional consume the command itself.
    flags_with_arg=frozenset({
        "--groups",
        "--userspec",
    }),
    stop_flags=frozenset({"--help", "--version"}),
    leading_positional_count=1,
)

# Option lists below are the options each tool's usage documents with a
# required argument (GNU and BSD spellings). An option whose argument is
# optional (`xargs -i[R]`, `watch -d[=x]`) is left out: getopt only accepts
# that argument attached, so the next word is the command.
_HELP_VERSION: Final[frozenset[str]] = frozenset({"--help", "--version"})

_XARGS_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({
        "-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L",
        "-n", "--max-args", "-P", "--max-procs", "-s", "--max-chars",
        "--process-slot-var",
        "-J", "-R", "-S",  # BSD xargs
    }),
    stop_flags=_HELP_VERSION,
)

_STRACE_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({
        "-a", "-b", "-e", "-E", "-I", "-o", "-O", "-p", "-P", "-s", "-S", "-u", "-U", "-X",
        "--columns", "--detach-on", "--env", "--attach", "--user", "--argv0",
        "--interruptible", "--trace", "--signal", "--status", "--trace-path",
        "--abbrev", "--verbose", "--raw", "--read", "--write", "--kvm",
        "--output", "--string-limit", "--const-print-style", "--inject", "--fault",
        "--summary-syscall-overhead", "--summary-sort-by", "--summary-columns",
    }),
    stop_flags=frozenset({"-h", "-V"}) | _HELP_VERSION,
)

_LTRACE_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({
        "-a", "--align", "-A", "-D", "--debug", "-e", "-F", "--config",
        "-l", "--library", "-n", "--indent", "-o", "--output", "-p", "-s",
        "-u", "-w", "--where", "-x",
    }),
    stop_flags=frozenset({"-h", "-V"}) | _HELP_VERSION,
)

_BWRAP_WRAPPER_SPEC: Final[CommandWrapperSpec] = CommandWrapperSpec(
    flags_with_arg=frozenset({
        "--args", "--argv0", "--userns", "--userns2", "--pidns", "--uid", "--gid",
        "--hostname", "--chdir", "--unsetenv", "--lock-file", "--sync-fd",
        "--remount-ro", "--exec-label", "--file-label", "--proc", "--dev",
        "--tmpfs", "--mqueue", "--dir", "--seccomp", "--add-seccomp-fd",
        "--block-fd", "--userns-block-fd", "--info-fd", "--json-status-fd",
        "--cap-add", "--cap-drop", "--perms", "--size", "--overlay-src",
        "--tmp-overlay", "--ro-overlay",
    }),
    flag_arity={
        **dict.fromkeys((
            "--setenv", "--bind", "--bind-try", "--dev-bind", "--dev-bind-try",
            "--ro-bind", "--ro-bind-try", "--bind-fd", "--ro-bind-fd", "--file",
            "--bind-data", "--ro-bind-data", "--symlink", "--chmod",
        ), 2),
        "--overlay": 3,
    },
    stop_flags=_HELP_VERSION,
)

_TRANSPARENT_WRAPPER_SPECS: Final[dict[str, CommandWrapperSpec]] = {
    # RTK wraps command output for token savings while preserving child command
    # semantics from autorun's safety perspective.
    "rtk": _RTK_WRAPPER_SPEC,
    # tsb is Task SandBox: `tsb [options] <command> [args...]`.
    "tsb": _TSB_WRAPPER_SPEC,
    # Common shell prefixes with operands before the child command.
    "sudo": _SUDO_WRAPPER_SPEC,
    "env": _ENV_WRAPPER_SPEC,
    "timeout": _TIMEOUT_WRAPPER_SPEC,
    "nice": _NICE_WRAPPER_SPEC,
    "ionice": _IONICE_WRAPPER_SPEC,
    "chroot": _CHROOT_WRAPPER_SPEC,
    # Privilege, process and sandbox wrappers. Each still runs its child.
    "doas": CommandWrapperSpec(flags_with_arg=frozenset({"-a", "-C", "-u"})),
    "pkexec": CommandWrapperSpec(flags_with_arg=frozenset({"--user"}), stop_flags=_HELP_VERSION),
    "gksudo": CommandWrapperSpec(
        flags_with_arg=frozenset({"-u", "--user", "-D", "--description"}), stop_flags=_HELP_VERSION,
    ),
    "kdesudo": CommandWrapperSpec(flags_with_arg=frozenset({"-u"}), stop_flags=_HELP_VERSION),
    # systemd's sudo replacement, util-linux's root-only su, and setpriv
    # (`--reuid=0` changes the user like sudo does).
    "run0": CommandWrapperSpec(
        flags_with_arg=frozenset({
            "-u", "--user", "-g", "--group", "-D", "--chdir", "--nice", "--setenv",
            "--unit", "--property", "--description", "--slice", "--machine", "--background",
        }),
        stop_flags=_HELP_VERSION,
    ),
    "runuser": CommandWrapperSpec(
        flags_with_arg=frozenset({
            "-u", "--user", "-g", "--group", "-G", "--supp-group", "-s", "--shell",
            "-w", "--whitelist-environment",
        }),
        stop_flags=_HELP_VERSION,
    ),
    "setpriv": CommandWrapperSpec(
        flags_with_arg=frozenset({
            "--reuid", "--regid", "--ruid", "--euid", "--rgid", "--egid", "--groups",
            "--inh-caps", "--ambient-caps", "--bounding-set", "--securebits",
            "--pdeathsig", "--selinux-label", "--apparmor-profile",
            "--landlock-access", "--landlock-rule", "--seccomp-filter",
        }),
        stop_flags=_HELP_VERSION,
    ),
    "nohup": CommandWrapperSpec(stop_flags=_HELP_VERSION),
    "time": CommandWrapperSpec(
        flags_with_arg=frozenset({"-f", "--format", "-o", "--output"}),
        stop_flags=frozenset({"-V"}) | _HELP_VERSION,
    ),
    "stdbuf": CommandWrapperSpec(
        flags_with_arg=frozenset({"-i", "-o", "-e", "--input", "--output", "--error"}),
        stop_flags=_HELP_VERSION,
    ),
    "strace": _STRACE_WRAPPER_SPEC,
    "ltrace": _LTRACE_WRAPPER_SPEC,
    "fakeroot": CommandWrapperSpec(
        flags_with_arg=frozenset({"-l", "--lib", "--faked", "-i", "-s", "-b", "--fd-base"}),
        stop_flags=frozenset({"-h", "-v"}) | _HELP_VERSION,
    ),
    "firejail": CommandWrapperSpec(stop_flags=_HELP_VERSION),
    "bwrap": _BWRAP_WRAPPER_SPEC,
    "bubblewrap": _BWRAP_WRAPPER_SPEC,
    # Shell builtins that run their operand as a command: `exec` replaces the
    # shell with it, `command -v`/`-V` only look it up.
    "exec": CommandWrapperSpec(flags_with_arg=frozenset({"-a"})),
    "command": CommandWrapperSpec(stop_flags=frozenset({"-v", "-V"})),
    "builtin": CommandWrapperSpec(),
    "busybox": CommandWrapperSpec(stop_flags=frozenset({"--help", "--install", "--list", "--list-full"})),
    # xargs runs its operand with stdin appended as arguments.
    "xargs": _XARGS_WRAPPER_SPEC,
    # These join their operands and hand them to a shell.
    "eval": CommandWrapperSpec(args_are_script=True),
    "parallel": CommandWrapperSpec(args_are_script=True),
    "script": CommandWrapperSpec(script_flags=frozenset({"-c", "--command"})),
    "flock": CommandWrapperSpec(
        leading_positional_count=1, script_flags=frozenset({"-c", "--command"}),
    ),
    "watch": CommandWrapperSpec(
        flags_with_arg=frozenset({"-n", "--interval", "-q", "--equexit"}),
        stop_flags=frozenset({"-h", "-v"}) | _HELP_VERSION,
        args_are_script=True,
    ),
}

TRANSPARENT_COMMAND_WRAPPERS: Final[frozenset[str]] = frozenset(_TRANSPARENT_WRAPPER_SPECS)

#: Wrappers that run their child as another (usually privileged) user. A read
#: that is harmless as yourself is not harmless through one of these.
PRIVILEGE_ESCALATING_WRAPPERS: Final[frozenset[str]] = frozenset(
    {"sudo", "doas", "pkexec", "gksudo", "kdesudo", "run0", "runuser", "setpriv", "su"}
)


def shell_words(cmd: str) -> list[str] | None:
    """The line as the shell tokenises it, operators as their own words.

    Quotes and backslashes are resolved (`s''udo` is `sudo`) and `;`, `|`,
    `&`, `(`, `)`, `<`, `>` inside quotes stay inside their word, which a
    split on operator characters gets wrong. None when the line does not
    parse (an unclosed quote): callers that guard must then refuse.
    """
    lexer = shlex.shlex(cmd or "", posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    # shlex ends a line at any `#`; bash only at one that starts a word, so
    # `echo $#; sudo x` hid the sudo. Keeping `#` text costs nothing a guard
    # needs and sees every command bash would run.
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def mentions_privilege_escalation(cmd: str) -> bool:
    """Whether any word on a line names a run-as-another-user wrapper.

    Checked over every shell word, quoted text split too, so `s''udo`,
    `$(sudo ...)`, `` `sudo ...` `` and `su -c '...'` are all seen. A line
    that does not parse counts as mentioning one.
    """
    words = shell_words(cmd)
    if words is None:
        return True
    return any(
        _get_basename(part) in PRIVILEGE_ESCALATING_WRAPPERS
        for word in words
        # `X=`sudo` and "$(sudo" stay one shell word; split at the
        # characters that can sit between an assignment or substitution
        # and the command it runs.
        for part in re.split(r"[\s=`$(){}\"';|&]+", word)
    )

# Derived, not listed: integrations.py reads these as prefixes, and a name it
# knew that the pattern layer did not (nohup, time, doas, ...) let
# `nohup rm -rf x` through the `rm -rf` block.
COMMAND_PREFIXES: Final[frozenset[str]] = TRANSPARENT_COMMAND_WRAPPERS


def git_subcommand_index(tokens: list[str]) -> int:
    """Return the index of git's subcommand after global options.

    Precondition: `tokens[0]` is `git`. If no subcommand exists, returns
    `len(tokens)`.
    """
    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            return i + 1
        if token in GIT_GLOBAL_OPTIONS_WITH_ARG:
            i += 2
            continue
        if _git_option_head(token) in GIT_GLOBAL_OPTIONS_WITH_ARG:
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        return i
    return i


def _collect_git_global_flags(tokens: list[str], sub_idx: int, flags: set[str]) -> None:
    """Collect only the global option names before `sub_idx`, not their values."""
    i = 1
    while i < sub_idx:
        token = tokens[i]
        if token == "--":
            i += 1
            continue
        if token in GIT_GLOBAL_OPTIONS_WITH_ARG:
            flags.add(token)
            i += 2
            continue
        option_head = _git_option_head(token)
        if option_head in GIT_GLOBAL_OPTIONS_WITH_ARG:
            flags.add(option_head)
            i += 1
            continue
        if token.startswith("-"):
            _add_flag_tokens(flags, token)
        i += 1


def _is_env_assignment(token: str) -> bool:
    """Return whether token is a shell-style NAME=value assignment."""
    name, sep, _value = token.partition("=")
    return bool(sep) and bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def _strip_wrapper_once(tokens: list[str], spec: CommandWrapperSpec) -> list[str]:
    """Strip one leading wrapper invocation according to its explicit grammar."""
    rest = tokens[1:]
    i = 0
    leading_positional_remaining = spec.leading_positional_count

    while i < len(rest):
        token = rest[i]
        option_head = _git_option_head(token)

        if option_head in spec.script_flags:
            script = token.partition("=")[2] if "=" in token else next(iter(rest[i + 1:]), "")
            return ["sh", "-c", script]

        if spec is _ENV_WRAPPER_SPEC and (
            token.startswith("-S") or option_head == "--split-string"
        ):
            if token in ("-S", "--split-string"):
                if i + 1 >= len(rest):
                    return []
                value, consumed = rest[i + 1], 2
            else:
                value = token[2:] if token.startswith("-S") else token.partition("=")[2]
                consumed = 1
            rest[i:i + consumed] = _shlex_split_safe(value)
            continue

        if token == "--":
            return rest[i + 1:]

        if token in spec.stop_flags or option_head in spec.stop_flags:
            return []

        if spec.skip_env_assignments and _is_env_assignment(token):
            i += 1
            continue

        if token.startswith("-"):
            if token in spec.flag_arity:
                i += 1 + spec.flag_arity[token]
            elif token in spec.flags_with_arg or option_head in spec.flags_with_arg:
                i += 1
                if "=" not in token and i < len(rest):
                    i += 1
            else:
                i += 1
            continue

        if leading_positional_remaining > 0:
            leading_positional_remaining -= 1
            i += 1
            continue

        if token in spec.optional_subcommands:
            i += 1
            continue

        return rest[i:]

    return []


def strip_transparent_command_wrappers(
    tokens: list[str], *, excluded_wrappers: frozenset[str] = frozenset(),
) -> list[str]:
    """Return tokens with leading transparent wrappers removed.

    Wrappers are stripped by explicit argv grammar. This keeps `rtk echo rm`
    and `tsb echo rm` safe while still recognizing wrapped `git push`.
    `eval rm -rf x` and `watch 'rm -rf x'` come back as `sh -c 'rm -rf x'`,
    which is what they run.
    """
    stripped = list(tokens)
    while stripped:
        if excluded_wrappers and _get_basename(stripped[0]) in excluded_wrappers:
            return []
        # POSIX prefix assignments (`LC_ALL=C tail f`) run the command after
        # them, as `env LC_ALL=C tail f` does. A line of only assignments runs
        # no command, so it is left as written.
        assignments = 0
        while assignments < len(stripped) and _is_env_assignment(stripped[assignments]):
            assignments += 1
        if 0 < assignments < len(stripped):
            stripped = stripped[assignments:]
        spec = _TRANSPARENT_WRAPPER_SPECS.get(_get_basename(stripped[0]))
        if spec is None:
            break
        next_tokens = _strip_wrapper_once(stripped, spec)
        if spec.args_are_script and next_tokens:
            next_tokens = ["sh", "-c", " ".join(next_tokens)]
        if next_tokens == stripped:
            break
        stripped = next_tokens
    return stripped


def shell_command_from_tool_input(tool_input: object) -> str:
    """Return the shell command string from Claude/Gemini/Codex-style inputs."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd", "CommandLine"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def _find_shell_exec_arg(tokens: list[str]) -> str | None:
    """Return the script a shell runs from `-c`, `-lc`, `-c --`, `--command`.

    `tokens` must already be wrapper-stripped: `ionice -c 3 bash -c x` gave
    ionice's `-c` to bash when the raw argv was searched.
    """
    if not tokens or _get_basename(tokens[0]) not in SHELL_EXEC_COMMANDS:
        return None
    for i, token in enumerate(tokens[1:], start=1):
        if token.startswith("--command="):
            return token.partition("=")[2]
        if token == "--command" or _SHELL_C_OPTION.fullmatch(token):
            rest = tokens[i + 1:]
            if rest[:1] == ["--"]:
                rest = rest[1:]
            return rest[0] if rest else None
    return None


def _reads_stdin_script(tokens: list[str]) -> bool:
    """Whether wrapper-stripped `tokens` reads script text, rather than data, on stdin."""
    if _sources_stdin(tokens):
        return True
    if not tokens or _get_basename(tokens[0]) not in SHELL_EXEC_COMMANDS or _find_shell_exec_arg(tokens) is not None:
        return False
    args = iter(tokens[1:])
    for token in args:
        if token == "--":
            return next(args, "/dev/stdin") in ("-", "/dev/stdin")
        if token in ("-o", "+o", "-O", "+O", "--rcfile", "--init-file"):
            next(args, None)
        elif token.startswith(("-", "+")) and token != "-":
            if not token.startswith("--") and "s" in token[1:]:
                return True
        else:
            return token in ("-", "/dev/stdin")
    return True


def _sources_stdin(tokens: list[str]) -> bool:
    return len(tokens) > 1 and tokens[0] in (".", "source") and tokens[1] == "/dev/stdin"


def _is_download(tokens: list[str]) -> bool:
    """Whether wrapper-stripped `tokens` is a curl/wget command."""
    return bool(tokens) and _get_basename(tokens[0]) in NETWORK_DOWNLOADERS


def _herestring(tokens: list[str]) -> str | None:
    """`<<< text` as shlex leaves it in argv (bashlex makes it a redirect)."""
    for i, token in enumerate(tokens):
        if token == "<<<":
            return tokens[i + 1] if i + 1 < len(tokens) else None
        if token.startswith("<<<"):
            return token[3:]
    return None


def _printed_text(tokens: list[str]) -> str | None:
    """What wrapper-stripped `echo`/`printf` writes, as script text, or None.

    ponytail: the arguments joined, plus each on its own line so a printf
    format cannot glue itself onto the command; a real formatter if it
    over-matches.
    """
    if not tokens or _get_basename(tokens[0]) not in ("echo", "printf"):
        return None
    args = tokens[1:]
    return "\n".join([" ".join(args), *args])


def _child_scripts(tokens: list[str]) -> list[str]:
    """Script text that wrapper-stripped `tokens` runs as further commands."""
    script = _find_shell_exec_arg(tokens)
    if script is not None:
        return [script]
    if not tokens or _get_basename(tokens[0]) != "find":
        return []
    children: list[list[str]] = []
    child: list[str] | None = None
    for token in tokens[1:]:
        if child is None:
            if token in _FIND_EXEC_ACTIONS:
                child = []
        elif token == ";" or (token == "+" and child[-1:] == ["{}"]):
            children.append(child)
            child = None
        else:
            child.append(token)
    if child:
        children.append(child)
    return [shlex.join(c) for c in children if c]


def _shell_quote_fragment(match: re.Match) -> str:
    """Translate shell quoting into shlex quoting without evaluating expansions."""
    text = match.group()
    if text.startswith("{"):
        return text[1:-1].replace(",", " ")
    if text.startswith("$'"):
        def unescape(escape):
            value = escape[1]
            if value[0] in "xuU":
                if len(value) == 1:
                    raise _UninspectableScript("invalid ANSI-C escape")
                try:
                    return chr(int(value[1:], 16))
                except ValueError as exc:
                    raise _UninspectableScript("invalid ANSI-C code point") from exc
            if value[0] in "01234567":
                return chr(int(value, 8) % 256)
            escapes = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f",
                       "n": "\n", "r": "\r", "t": "\t", "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}
            if value == "c":
                raise _UninspectableScript("unsupported ANSI-C control escape")
            return escapes.get(value, "\\" + value)
        return shlex.quote(_ANSI_ESCAPE.sub(unescape, text[2:-1]).split("\0", 1)[0])
    if text.startswith('$"'):
        text = text[1:]
    if text.startswith('"'):
        return text.replace("\\\n", "").replace(r"\$", "$").replace(r"\`", "`")
    return "" if text == "\\\n" else text


def _shlex_split_safe(segment: str) -> list[str]:
    """Resolve literal shell word quoting on both parsing paths, without execution."""
    if "$" in segment or "\\\n" in segment or ("{" in segment and "," in segment):
        segment = _SHELL_QUOTES.sub(_shell_quote_fragment, segment)
    try:
        return shlex.split(segment)
    except ValueError:
        try:
            return shlex.split(segment, posix=False)
        except ValueError:
            return segment.split()


def _without_leading_redirects(segment: str) -> str:
    """Strip leading redirections from fallback source before unquoting."""
    pos = 0
    while match := _LEADING_REDIRECT.match(segment, pos):
        pos = match.end()
    return segment[pos:].lstrip() if pos else segment


def _uninspected_shell() -> ExtractedCommands:
    # Reuse the unreadable-script shell guard when the bounded scan stops.
    shell = frozenset({"sh"})
    return ExtractedCommands(shell, shell, shell, shell, segments=(), inspection_incomplete=True)


def _extract_from_tokens(tokens: list[str]) -> tuple[str | None, str | None, set[str]]:
    """
    Extract command identity after explicit wrapper normalization.

    Returns (primary_command, full_string, all_potential_commands).

    Logic for all_potential:
    - Leading transparent wrappers are stripped with explicit argv grammars.
    - Only the resulting command token is potential.
    - Command operands remain operands, including values after `--`.

    Examples:
    - "sudo -u root rm file" -> potential={rm}
    - "echo rm" -> potential={echo} (echo is not a prefix, so rm is just an arg)
    - "cat && rm file" -> handled by _extract_recursive, each segment separately
    """
    if not tokens:
        return None, None, set()

    tokens = strip_transparent_command_wrappers(tokens)
    if not tokens or all(_is_env_assignment(token) for token in tokens):
        return None, None, set()

    cmd_idx = 0
    if not tokens:
        return None, None, set()

    cmd_name = _get_basename(tokens[cmd_idx])
    potential = {cmd_name}
    # v8: Build command string more efficiently
    rest = tokens[cmd_idx + 1:]
    cmd_string = f"{cmd_name} {' '.join(rest)}" if rest else cmd_name
    return cmd_name, cmd_string, potential


# ─── Recursive Extraction ─────────────────────────────────────────────────────

def _substitution_scripts(cmd: str, *, heredoc: bool = False):
    """Find executable substitutions in one O(n) pass, with O(n) stack space.

    Yield outer bodies only; the fallback scans their nested scripts within
    its existing recursion limit. Arithmetic frames balance but are not scripts.
    """
    # Each frame keeps its own quoting and parenthesis balance.
    stack = [["root", 0, "", 0]]
    commands = 0
    i = 0
    while i < len(cmd):
        kind, start, quote, balance = stack[-1]
        char = cmd[i]
        if quote == "'":
            if char == "'":
                stack[-1][2] = ""
        elif char == "\\":
            i += 1
        elif char == "`" and kind == "backtick":
            stack.pop()
            commands -= 1
            if not commands:
                yield cmd[start:i]
        elif char == "`" or cmd.startswith("$(", i) or (
            not heredoc and not quote and char in "<>" and cmd.startswith("(", i + 1)
        ):
            arithmetic = cmd.startswith("$((", i)
            offset = 1 if char == "`" else 3 if arithmetic else 2
            stack.append([
                "arithmetic" if arithmetic else "backtick" if char == "`" else "command",
                i + offset, "", 2 if arithmetic else 1,
            ])
            commands += not arithmetic
            i += offset - 1
        elif char in "\"'" and (not quote or char == quote) and not (heredoc and kind == "root"):
            stack[-1][2] = "" if quote else char
        elif not quote and kind in ("command", "arithmetic") and char in "()":
            stack[-1][3] += 1 if char == "(" else -1
            if not stack[-1][3]:
                stack.pop()
                if kind == "command":
                    commands -= 1
                    if not commands:
                        yield cmd[start:i]
        i += 1


def _fallback_segments(cmd: str, depth: int = 0):
    """Yield simple commands and their preceding operator after a parse failure."""
    if depth > _MAX_RECURSION_DEPTH:
        # An explicit opaque shell segment carries the existing guard signal.
        yield "", ["sh"], ""
        return
    start, separator = 0, ""
    boundaries = (
        (match.start(), match.end(), match.group("separator"))
        for match in _QUOTED_SHELL_OPERATORS.finditer(cmd)
        if match.group("separator")
    )
    for end, next_start, following in chain(boundaries, [(len(cmd), len(cmd), "")]):
        segment = cmd[start:end].strip()
        start = next_start
        pos = 0
        while prefix := _FALLBACK_PREFIX.match(segment, pos):
            pos = prefix.end()
            while pos < len(segment) and segment[pos].isspace():
                pos += 1
        segment = _without_leading_redirects(segment[pos:])
        # Remove syntax before unquoting so an operand like '>' stays data.
        argv = _FALLBACK_ARGV.sub(lambda m: " " if m.group("redirect") else m.group(), segment)
        if segment and (tokens := _shlex_split_safe(argv)):
            yield segment, tokens, separator
        separator = following
    for script in _substitution_scripts(cmd):
        yield from _fallback_segments(script, depth + 1)


def _extract_recursive(cmd: str, depth: int) -> ExtractedCommands:
    """Recursively extract commands, including from 'sh -c' patterns."""
    if depth > _MAX_RECURSION_DEPTH:
        return _uninspected_shell()

    names, strings, potential, fetched = set(), set(), set(), set()
    nested: list[str] = []
    segments: list[tuple[str, list[str]]] = []
    child_segments: list[tuple[str, list[str]]] = []
    incomplete = False

    previous: list[str] = []
    downloading = False  # an earlier stage of this pipeline is curl/wget
    for segment, tokens, separator in _fallback_segments(cmd, depth):
        if segment:
            segments.append((segment, tokens))
        if not segment:
            fetched.add("sh")
            incomplete = True
        piped = separator == "|"
        downloading = downloading and piped

        name, string, pot = _extract_from_tokens(tokens)
        if name:
            names.add(name)
            potential.update(pot)
        if string:
            strings.add(string)

        # Scripts this command runs: shell -c, find -exec, here-string, pipe.
        stripped = strip_transparent_command_wrappers(tokens)
        scripts = _child_scripts(stripped)
        if _reads_stdin_script(stripped):
            scripts += [_herestring(_shlex_split_safe(segment)), _printed_text(previous) if piped else None]
        if stripped and _get_basename(stripped[0]) in SHELL_EXEC_COMMANDS and (
            (downloading and _reads_stdin_script(stripped)) or _SUBSTITUTED_DOWNLOAD.search(segment)
        ):
            fetched.add(_get_basename(stripped[0]))
            scripts = []  # Downloaded script text is unavailable to inspection.
        for script in filter(None, scripts):
            inner = _extract_recursive(script, depth + 1)
            names.update(inner.names)
            strings.update(inner.strings)
            potential.update(inner.all_potential)
            fetched.update(inner.fetched_shells)
            incomplete |= inner.inspection_incomplete
            nested += [script, *inner.scripts]
            child_segments.extend(inner.segments or ())
        previous = stripped
        downloading = downloading or _is_download(stripped)

    return ExtractedCommands(
        frozenset(names), frozenset(strings), frozenset(potential), frozenset(fetched),
        tuple(dict.fromkeys(nested)),
        tuple(segments + child_segments),
        inspection_incomplete=incomplete,
    )


def shell_segments(cmd: str):
    """Yield (segment text, its shlex argv) for each simple command on a line.

    Covers the line itself and then every script it runs (`bash -c '...'`),
    split at shell operators. Quoting survives in the argv, unlike the
    strings `extract_commands` returns.
    """
    yield from _extract_cached(cmd).segments or ()


def command_tokens_for(
    cmd: str,
    pattern: str,
    *,
    allow_prefixes: bool = True,
    excluded_wrappers: frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Argv of the first command in a shell string that `pattern` matches.

    `pattern` is a command name (`sed`) or a full pattern (`rm -rf`); the
    segment returned is one the whole pattern matches, so on `rm a && rm -rf b`
    `rm -rf` answers with `rm -rf b`. Scripts the line runs (`bash -c '...'`)
    are searched after the line itself. The extractor reuses autorun's normal
    prefix-aware command parser, so `sudo sed -i ...` can still be recognized
    as sed when desired. Callers that need only a direct shell command can set
    allow_prefixes=False.
    """
    if not cmd or not pattern or not pattern.split():
        return ()
    wanted = ParsedPattern.from_string(pattern)
    command_name = pattern.split()[0]

    for _segment, tokens in shell_segments(cmd):
        name, string, _potential = _extract_from_tokens(tokens)
        if name != command_name or not ExtractedCommands.string_matches(string, wanted):
            continue
        if not allow_prefixes and _get_basename(tokens[0]) != command_name:
            continue

        for i, token in enumerate(tokens):
            if _get_basename(token) == command_name:
                if excluded_wrappers and any(
                    _get_basename(prefix) in excluded_wrappers for prefix in tokens[:i]
                ):
                    break
                return tuple(tokens[i:])
        else:
            # env -S may keep the child name inside a single source argument.
            stripped = strip_transparent_command_wrappers(tokens, excluded_wrappers=excluded_wrappers)
            if stripped:
                return tuple(stripped)

    return ()


# ─── bashlex Visitor ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _bashlex():
    """bashlex and a visitor over its AST, loaded once, on first use.

    None when bashlex is present but will not import. The failure is cached
    too: lru_cache does not remember an exception, so a broken bashlex was
    re-imported, grammar rebuild and all, on every command parse.
    """
    try:
        import bashlex
        from bashlex import ast as bashlex_ast

        base = bashlex_ast.nodevisitor
    except Exception:
        return None

    class CommandVisitor(base):
        """AST visitor with wrapper-aware and recursive shell -c parsing."""
        __slots__ = (
            "names", "strings", "potential", "fetched", "scripts", "depth",
            "source", "original_source", "segments", "child_segments", "incomplete", "segment_argv",
        )

        def __init__(self, source: str, original_source: str, depth: int = 0) -> None:
            self.names: set[str] = set()
            self.strings: set[str] = set()
            self.potential: set[str] = set()
            self.fetched: set[str] = set()
            self.scripts: list[str] = []
            self.depth = depth
            self.source = source
            self.original_source = original_source
            self.incomplete = False
            self.segments: list[tuple[str, list[str]]] = []
            self.child_segments: list[tuple[str, list[str]]] = []
            self.segment_argv: dict[str, list[str]] = {}

        def visitcommand(self, node, parts) -> bool:
            word_parts = _command_word_parts(parts, self.source)
            segment = " ".join(self.original_source[p.pos[0]:p.pos[1]] for p in word_parts)
            # Repeated commands share argv within this parse.
            tokens = self.segment_argv.get(segment)
            if tokens is None:
                tokens = self.segment_argv[segment] = _shlex_split_safe(segment)
            if tokens:
                self.segments.append((segment, tokens))
            all_words = tokens
            # Filter out words containing newlines — these are heredoc bodies
            # or command substitutions with embedded content, not real arguments.
            # Prevents false positives like "git restore" matching inside a
            # commit message heredoc: git commit -m "$(cat <<EOF\n...restore...\nEOF)"
            words = [w for w in all_words if "\n" not in w]
            name, string, pot = _extract_from_tokens(words)

            if name:
                self.names.add(name)
                self.potential.update(pot)

            if string:
                # Redirect operators ride along so a pattern naming one
                # (`echo >`) can match; bashlex keeps them out of the words.
                # `1>` is `>`; `2>` stays distinct.
                redirects = []
                for p in parts:
                    if p.kind == "redirect":
                        fd = getattr(p, "input", None)
                        redirects.append(f"{'' if fd in (None, 0, 1) else fd}{p.type}")
                self.strings.add(" ".join([string, *redirects]))

            # Scripts this command runs. These use every word: a multi-line
            # `bash -c` body is a script, not a commit message.
            stripped = strip_transparent_command_wrappers(all_words)
            if _reads_stdin_script(stripped):
                script = _stdin_text(parts, self.original_source)
                self._scan(script)
            # `bash <(curl ...)`, `bash -c "$(curl ...)"`, `sh <<< "$(curl ...)"`.
            # ponytail: any download in the shell's words counts, so
            # `bash local.sh "$(curl ...)"` is read as one too; fails closed.
            if (
                stripped
                and _get_basename(stripped[0]) in SHELL_EXEC_COMMANDS
                and any(_downloads(p, self.source) for p in parts)
            ):
                self.fetched.add(_get_basename(stripped[0]))
            else:
                for script in _child_scripts(stripped):
                    self._scan(script)

            return True

        def visitredirect(self, node, input, type, output, heredoc) -> bool:
            if heredoc is not None:
                delimiter = _HEREDOC_DELIMITER.search(self.original_source, node.pos[0])
                if delimiter and not any(c in delimiter.group("delimiter") for c in "'\"\\"):
                    body = _heredoc_text(heredoc, self.original_source)
                    for script in _substitution_scripts(body or "", heredoc=True):
                        self._scan(script)
            return True

        def visitpipeline(self, node, parts) -> bool:
            # `echo 'rm -rf x' | sh` and `cat <<EOF | sh`: the shell's script
            # is what the command before it writes.
            commands = [p for p in parts if p.kind != "pipe"]
            for i, (producer, consumer) in enumerate(zip(commands, commands[1:]), start=1):
                if consumer.kind != "command":
                    continue
                shell = strip_transparent_command_wrappers(_words(consumer, self.original_source))
                if not _reads_stdin_script(shell):
                    continue
                # `curl ... | sh`, `wget -qO- ... | gunzip | bash`: a script
                # that exists only on the wire.
                if any(_downloads(c, self.source) for c in commands[:i]):
                    self.fetched.add(_get_basename(shell[0]))
                if producer.kind != "command":
                    continue
                source = strip_transparent_command_wrappers(_words(producer, self.original_source))
                if source and _get_basename(source[0]) == "cat":
                    self._scan(_stdin_text(producer.parts, self.original_source))
                else:
                    self._scan(_printed_text(source))
            return True

        def _scan(self, script: str | None) -> None:
            if not script:
                return
            inner = _extract_impl(script, self.depth + 1)
            self.names.update(inner.names)
            self.strings.update(inner.strings)
            self.potential.update(inner.all_potential)
            self.fetched.update(inner.fetched_shells)
            self.incomplete |= inner.inspection_incomplete
            self.scripts += [script, *inner.scripts]
            self.child_segments.extend(inner.segments or ())

    def _command_word_parts(parts, source):
        """Keep argv parts; an unquoted, adjacent {name} belongs to its redirect."""
        return [p for i, p in enumerate(parts) if p.kind in ("word", "assignment") and not (
            p.kind == "word" and p.word.startswith("{")
            and i + 1 < len(parts) and parts[i + 1].kind == "redirect"
            and p.pos[1] == parts[i + 1].pos[0]
            and _NAMED_DESCRIPTOR.fullmatch(source[p.pos[0]:p.pos[1]])
        )]

    def _words(node, source) -> list[str]:
        return _shlex_split_safe(" ".join(
            source[p.pos[0]:p.pos[1]] for p in _command_word_parts(node.parts, source)
        ))

    def _downloads(node, source) -> bool:
        """Whether `node` is or contains a curl/wget command."""
        if node.kind == "command" and _is_download(strip_transparent_command_wrappers(_words(node, source))):
            return True
        for attr in ("parts", "list", "command", "output"):
            value = getattr(node, attr, None)
            for child in value if isinstance(value, list) else [value]:
                if hasattr(child, "kind") and _downloads(child, source):
                    return True
        return False

    def _stdin_text(parts, original_source: str) -> str | None:
        """The here-string or heredoc body a command reads as stdin, or None."""
        for p in reversed(parts):
            if p.kind != "redirect" or p.input not in (None, 0):
                continue
            if p.type == "<<<":
                return _shlex_split_safe(original_source[p.output.pos[0]:p.output.pos[1]])[0]
            heredoc = getattr(p, "heredoc", None)
            if heredoc is not None:
                return _heredoc_text(heredoc, original_source)
            if p.type in ("<", "<&", "<>"):
                return None
        return None

    def _heredoc_text(heredoc, source: str) -> str:
        # bashlex keeps the closing delimiter as the body's last line.
        return source[heredoc.pos[0]:heredoc.pos[1]].rpartition("\n")[0]

    return bashlex, CommandVisitor


def warm_parser() -> bool:
    """Build the bash grammar now; whether bashlex is usable.

    The daemon calls this at startup so its first command does not pay for the
    grammar, and the restart diagnostics report its answer. False is not an
    error: every parse then falls back to shlex. BASHLEX_AVAILABLE only says
    bashlex is installed.
    """
    return _bashlex() is not None


def _normalize_heredoc_delimiters(cmd: str) -> str:
    """
    Remove quotes from heredoc delimiters for bashlex compatibility.

    Bashlex expects unquoted heredoc delimiters, but shell scripts often use
    quoted delimiters like << 'EOF' or << "END". This function normalizes them
    to the unquoted form that bashlex can parse.

    Examples:
        python3 << 'EOF' → python3 << EOF
        cat << "END" → cat << END
        cat <<-'END-MSG' → cat <<- END-MSG
        sh << EOF → sh << EOF (no change)
        bash <<< 'x' → bash <<< 'x' (a here-string, no change)

    Args:
        cmd: Shell command string potentially containing heredoc

    Returns:
        Normalized command string with unquoted heredoc delimiters
    """
    def normalize(match):
        delimiter = match.group("delimiter")
        if not any(c in delimiter for c in "'\"\\"):
            return match.group()
        word = _shlex_split_safe(delimiter)[0]
        # Keep offsets valid in the ORIGINAL source, including nested heredocs.
        return ("<<" + match.group("tabs") + " " + word).ljust(len(match.group()))

    return _HEREDOC_DELIMITER.sub(normalize, cmd)


# `<<` or `<<-`, then a quoted delimiter of any non-blank characters. Not a
# `<<<` here-string. A delimiter `\w+` alone missed `<<-'EOF'` and
# `<<'END-MSG'`, bashlex then failed, and the shlex fallback read every body
# line as a command. Linear: no nested quantifiers.
_HEREDOC_DELIMITER: Final[re.Pattern[str]] = re.compile(
    rf"(?<!<)<<(?!<)(?P<tabs>-?)[ \t]*(?P<delimiter>{_FALLBACK_WORD})"
)


def _extract_bashlex(cmd: str, depth: int) -> ExtractedCommands:
    """Extract using bashlex AST."""
    loaded = _bashlex()
    if loaded is None:
        # A bashlex that is present but will not import: the shlex fallback
        # in _extract_impl answers.
        return ExtractedCommands(frozenset(), frozenset(), frozenset())
    bashlex, CommandVisitor = loaded
    try:
        # Normalize heredoc delimiters for bashlex compatibility
        normalized_cmd = _normalize_heredoc_delimiters(cmd)
        parts = bashlex.parse(normalized_cmd)
    except Exception:
        return ExtractedCommands(frozenset(), frozenset(), frozenset())

    visitor = CommandVisitor(normalized_cmd, cmd, depth)
    for part in parts:
        visitor.visit(part)

    return ExtractedCommands(
        frozenset(visitor.names),
        frozenset(visitor.strings),
        frozenset(visitor.potential),
        frozenset(visitor.fetched),
        tuple(dict.fromkeys(visitor.scripts)),
        tuple(visitor.segments + visitor.child_segments),
        visitor.incomplete,
    )


def _extract_fallback(cmd: str, depth: int) -> ExtractedCommands:
    """Fallback extraction using shlex."""
    return _extract_recursive(cmd, depth)


def _extract_impl(cmd: str, depth: int = 0) -> ExtractedCommands:
    """Internal extraction dispatcher."""
    if depth > _MAX_RECURSION_DEPTH:
        return _uninspected_shell()
    try:
        if BASHLEX_AVAILABLE:
            result = _extract_bashlex(cmd, depth)
            if result.segments is not None:
                return result
        return _extract_fallback(cmd, depth)
    except _UninspectableScript:
        shell = frozenset({"sh"})
        return ExtractedCommands(shell, shell, shell, shell, segments=())


# ─── Public API (HOT PATH) ────────────────────────────────────────────────────

@lru_cache(maxsize=_CMD_CACHE_SIZE)
def _extract_cached(cmd: str) -> ExtractedCommands:
    """v8: Cached extraction - HOT PATH for command_matches_pattern."""
    return _extract_impl(cmd)


@lru_cache(maxsize=_CMD_CACHE_SIZE)
def extract_commands(cmd: str) -> tuple[frozenset[str], frozenset[str]]:
    """
    Extract command names and full strings from bash command.

    Returns:
        (command_names, command_strings) as frozensets
    """
    if not cmd or not cmd.strip():
        return frozenset(), frozenset()
    result = _extract_cached(cmd)
    return result.names, result.strings


def shells_running_downloads(cmd: str) -> frozenset[str]:
    """Shells running unreadable scripts (downloads or nesting past the limit).

    Their commands never pass through this parser, so no guard can see them.
    """
    if not cmd or not cmd.strip():
        return frozenset()
    return _extract_cached(cmd).fetched_shells


def nested_scripts(cmd: str) -> tuple[str, ...]:
    """Every script text a command in `cmd` runs, outermost first: what
    `bash -c '...'`, `eval`, a heredoc fed to a shell and the like execute."""
    if not cmd or not cmd.strip():
        return ()
    return _extract_cached(cmd).scripts


def command_matches_pattern(cmd: str, pattern: str) -> bool:
    """
    v8: Check if pattern matches a command in the bash string.

    HOT PATH - called for every Bash command. Uses cached extraction.
    """
    if not cmd or not pattern:
        return False

    result = _extract_cached(cmd)  # v8: Use cached version
    parsed = ParsedPattern.from_string(pattern)

    if parsed.is_single_word:
        return result.matches_single_word(parsed.base)
    return result.matches_pattern(parsed)
