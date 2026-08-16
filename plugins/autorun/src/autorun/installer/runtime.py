#!/usr/bin/env python3
"""How autorun invokes uv, and what it learns about the runtime it selected.

One command builder. The code this replaces has two: ``_render_uv_hook_command``
assembles a *shell string* for the hook manifest, and
``probe_hook_python_architecture`` assembles an *argv list* to probe the same
interpreter. They repeat the same flags — ``run --quiet``, ``--no-sync``,
``--project``, ``--python`` — and nothing keeps them in step, so a flag added
for the hook is missing from the probe that is supposed to verify the hook.

Here a command is built once as argv and *rendered* to a shell string only where
a manifest demands a string. Building the two forms from one description is what
makes them incapable of disagreeing.

Why this matters more than it looks: a hook manifest is read once at session
start, and a wrong flag there does not raise — the hook simply produces stderr,
which Claude Code treats as a hook failure and silently disables every hook for
the session while everything still looks healthy.

Complexity: building is O(flags). The probe runs one subprocess with a timeout
and is called by install and status only, never on a hook path.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping, Sequence

__all__ = [
    "DirectCommand", "UvCommand", "hook_command", "has_uv", "python_runner", "Probe", "probe_runtime", "probe_hook_runtime",
    "Outcome", "Runner", "bootstrap", "restart_daemon",
    "sync_dependencies_argv", "uv_tool_install_argv",
    "Version", "self_update", "update_argv", "detect_update_method",
    "installed_extension_name", "REPOSITORY", "EXTENSION_NAMES",
]


@lru_cache(maxsize=1)
def has_uv() -> bool:
    """Whether uv is on PATH. Cached: install and status ask repeatedly."""
    return shutil.which("uv") is not None


def python_runner() -> tuple[str, ...]:
    """How to run Python for user-facing instructions, uv first, pip fallback."""
    return ("uv", "run", "python") if has_uv() else ("python",)


@dataclass(frozen=True, slots=True)
class DirectCommand:
    """An argv command for an already-installed Python distribution."""

    command: tuple[str, ...]

    def argv(self) -> tuple[str, ...]:
        return self.command

    def shell(self) -> str:
        return shlex.join(self.command)

    def run(self, *, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )


@dataclass(frozen=True, slots=True)
class UvCommand:
    """One `uv run` invocation, renderable as argv or as a shell string.

    ``no_sync`` defaults True because a hook subprocess must stay fast after
    install and status have already validated the environment; uv documents
    ``--no-sync`` as the standard no-environment-update switch.

    ``env`` is carried rather than applied so the shell rendering can prefix
    ``KEY=value`` assignments, which is the only form a hook manifest accepts.
    """

    project: Path
    script: Path | None = None
    args: tuple[str, ...] = ()
    python: str = ""
    no_sync: bool = True
    quiet: bool = True
    env: Mapping[str, str] = field(default_factory=dict)

    def argv(self) -> tuple[str, ...]:
        """The single source of truth for what uv is asked to do."""
        return (
            "uv",
            "run",
            *(("--quiet",) if self.quiet else ()),
            *(("--no-sync",) if self.no_sync else ()),
            "--project",
            str(self.project),
            *(("--python", self.python) if self.python else ()),
            "python",
            *((str(self.script),) if self.script is not None else ()),
            *self.args,
        )

    def shell(self) -> str:
        """The same command as one shell string, for a manifest that needs one.

        Quoting is ``shlex.quote`` throughout rather than by hand: a project path
        containing a space silently truncated the command, and the hook then
        failed in the one way that disables every hook without an error.
        """
        assignments = [f"{key}={shlex.quote(value)}" for key, value in self.env.items()]
        return " ".join([*assignments, *(shlex.quote(part) for part in self.argv())])

    def run(self, *, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            self.argv(),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **self.env} if self.env else None,
        )


def hook_command(
    plugin_dir: Path,
    *,
    cli: str,
    python: str = "",
    no_sync: bool = True,
) -> UvCommand | DirectCommand:
    """Build the hook command for a checkout or installed distribution.

    A checkout owns a ``pyproject.toml`` and deliberately uses uv's project
    environment.  A wheel has already installed its dependencies and contains
    no project file, so running ``uv --project site-packages/autorun`` can never
    work; invoke its packaged hook with the current interpreter instead.
    """
    script = plugin_dir / "hooks" / "hook_entry.py"
    if (plugin_dir / "pyproject.toml").is_file():
        return UvCommand(
            project=plugin_dir,
            script=script,
            args=("--cli", cli),
            python=python,
            no_sync=no_sync,
        )
    return DirectCommand((sys.executable, str(script), "--cli", cli))


@dataclass(frozen=True, slots=True)
class Probe:
    """What uv actually selected, for install and status diagnostics."""

    ok: bool
    uv_path: str = ""
    executable: str = ""
    machine: str = ""
    system: str = ""
    reason: str = ""

    def describe(self) -> str:
        if not self.ok:
            return f"hook runtime: unavailable — {self.reason}"
        prefix = f"uv={self.uv_path}, " if self.uv_path else ""
        return f"{prefix}python={self.executable}, arch={self.machine}, os={self.system}"


#: Printed by the probed interpreter. One line, JSON, so a warning on stderr
#: cannot corrupt the answer the way a bare print would.
_PROBE = (
    "import json,platform,sys;"
    "print(json.dumps({'executable':sys.executable,"
    "'machine':platform.machine(),'system':platform.system()}))"
)


def probe_runtime(project: Path, *, python: str = "", no_sync: bool = True,
                  timeout: int = 10) -> Probe:
    """Ask uv which interpreter it would use, and on which architecture.

    Diagnostic only — install and status call it, hooks never do. An arm64 host
    resolving an x86_64 interpreter is the failure this catches, and it is
    otherwise invisible until a native dependency fails to import inside a hook.
    """
    if not (uv_path := shutil.which("uv")):
        return Probe(False, reason="uv not found on PATH")
    command = UvCommand(project=project, args=("-c", _PROBE), python=python, no_sync=no_sync)
    try:
        result = command.run(timeout=timeout)
    except (OSError, subprocess.SubprocessError) as error:
        return Probe(False, uv_path=uv_path, reason=f"{type(error).__name__}: {error}")
    if result.returncode != 0:
        return Probe(False, uv_path=uv_path, reason=_first_line(result.stderr) or "uv run failed")
    try:
        # Last line, not the whole output: uv may print progress before it.
        data = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Probe(False, uv_path=uv_path, reason="probe produced no JSON")
    return Probe(
        True,
        uv_path=uv_path,
        executable=str(data.get("executable", "")),
        machine=str(data.get("machine", "")),
        system=str(data.get("system", "")),
    )


def probe_hook_runtime(
    plugin_dir: Path,
    *,
    python: str = "",
    no_sync: bool = True,
    timeout: int = 10,
) -> Probe:
    """Report the interpreter used by source or installed hook commands."""
    if (plugin_dir / "pyproject.toml").is_file():
        return probe_runtime(
            plugin_dir,
            python=python,
            no_sync=no_sync,
            timeout=timeout,
        )
    return Probe(
        True,
        executable=sys.executable,
        machine=platform.machine(),
        system=platform.system(),
    )


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


# --- bootstrap: what has to exist before a hook can run ---------------------


@dataclass(frozen=True, slots=True)
class Outcome:
    """What one bootstrap step did, in a form both status and install print."""

    step: str
    ok: bool
    detail: str = ""

    def describe(self) -> str:
        return f"{'ok  ' if self.ok else 'FAIL'} {self.step}{f' — {self.detail}' if self.detail else ''}"


#: The subprocess boundary, injectable so a test never spawns uv, never installs
#: a tool into the developer's home, and never signals the live daemon. Passing a
#: fake here is the only way those tests can be both real and safe.
Runner = Callable[..., subprocess.CompletedProcess]


def _spawn(
    argv: Sequence[str],
    *,
    timeout: int = 120,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, **env} if env else None,
    )


def sync_dependencies_argv(plugin_dir: Path, *, uv_tool_env: bool = False) -> tuple[str, ...]:
    """The command that makes the hook runtime importable.

    A uv *tool* environment cannot be `uv sync`ed — it has no project — so the
    one dependency hooks genuinely need is installed into the running
    interpreter instead. Choosing by environment rather than by trying and
    catching keeps the failure of a real sync visible.
    """
    if uv_tool_env:
        return ("uv", "pip", "install", "--python", sys.executable, "-q", "bashlex")
    return ("uv", "sync", "--project", str(plugin_dir))


def uv_tool_install_argv(package_dir: Path, *, python: str = "") -> tuple[str, ...]:
    """Install the entry-point-owning package as an editable uv tool.

    The interpreter is pinned rather than left to PATH order, which otherwise
    silently selects a Python for the wrong CPU architecture — Intel Homebrew
    under Rosetta on an Apple Silicon host is the case that shipped.
    """
    return (
        "uv", "tool", "install", "--force",
        "--python", python or sys.executable,
        "--editable", str(package_dir),
    )


def bootstrap(
    plugin_dir: Path,
    *,
    uv_tool_env: bool = False,
    install_tool: bool = True,
    run: Runner = _spawn,
) -> tuple[Outcome, ...]:
    """Sync dependencies, then install the CLI, reporting each step.

    Sequential and short-circuiting: installing the tool from a project whose
    dependencies did not resolve produces a CLI that imports nothing, and the
    failure then surfaces inside a hook rather than during the install that
    caused it.
    """
    if (
        not uv_tool_env
        and not (plugin_dir / "pyproject.toml").is_file()
        and (plugin_dir / "__main__.py").is_file()
    ):
        return (
            Outcome(
                "installed distribution",
                True,
                "dependencies and CLI are supplied by the package installer",
            ),
        )
    steps = (
        ("dependencies", sync_dependencies_argv(plugin_dir, uv_tool_env=uv_tool_env)),
    ) + (
        (("autorun CLI", uv_tool_install_argv(plugin_dir)),)
        if install_tool
        else ()
    )
    outcomes: list[Outcome] = []
    for name, argv in steps:
        try:
            result = run(argv)
        except (OSError, subprocess.SubprocessError) as error:
            outcomes.append(Outcome(name, False, f"{type(error).__name__}: {error}"))
            break
        ok = result.returncode == 0
        outcomes.append(Outcome(name, ok, "" if ok else _first_line(result.stderr or result.stdout)))
        if not ok:
            break
    return tuple(outcomes)


def restart_daemon(*, run: Runner = _spawn) -> Outcome:
    """Ask this installer runtime to restart its own daemon.

    Delegated to the CLI rather than signalling a PID directly: the daemon's
    socket and PID file live under ``AUTORUN_HOME``, so a test with that
    redirected must not have its restart reach the developer's live daemon.
    Going through this interpreter's CLI means the redirection is honoured
    without selecting a stale global ``autorun`` executable from ``PATH``.
    """
    try:
        result = run(
            (sys.executable, "-m", "autorun", "--restart-daemon-after-install")
        )
    except (OSError, subprocess.SubprocessError) as error:
        return Outcome("daemon restart", False, f"{type(error).__name__}: {error}")
    return Outcome(
        "daemon restart",
        result.returncode == 0,
        "" if result.returncode == 0 else _first_line(result.stderr or result.stdout),
    )


# --- self-update ------------------------------------------------------------

#: Where an upgrade comes from when autorun was installed from source control.
#: The repository root is a workspace-only distribution with no ``autorun``
#: entry point, so every Python installer must select the plugin subproject.
REPOSITORY = (
    "git+https://github.com/ahundt/autorun.git"
    "#subdirectory=plugins/autorun"
)

#: Extension names autorun has shipped under, newest first. An installation made
#: by an older version still answers to its old name, and updating the wrong one
#: reports success while leaving the real install untouched.
EXTENSION_NAMES = ("ar", "autorun-workspace", "autorun")


#: Re-registration after a language-package upgrade. Upgrading the Python
#: package replaces the source but not what any harness loads: each keeps
#: serving its own cached copy of the previous version, so the user sees no
#: change and nothing reports why. Only the uv and pip routes need this — the
#: harness CLIs update their own caches as part of their update command.
_REREGISTER = (sys.executable, "-m", "autorun", "--install", "--force")


def update_argv(
    method: str, *, extension: str = EXTENSION_NAMES[0],
    available: Callable[[str], bool] = shutil.which,
) -> tuple[tuple[str, ...], ...]:
    """The commands that upgrade an installation made the given way, in order.

    A table rather than a branch chain: each method is one row, so adding a
    packaging route is a row and not a new `elif` in a function that already
    decides three other things. A row is a *sequence* of commands because the
    language-package routes are not done when the upgrade returns.

    ``plugin`` is the retired spelling for "whichever harness CLI is here",
    from before the two were told apart. It still resolves, because a user with
    it in a script or a config file would otherwise get an error naming methods
    they never chose.
    """
    if method == "plugin":
        method = next(
            (name for name in ("claude", "gemini") if available(name)), "claude"
        )
    return {
        "claude": ((("claude", "plugin", "update", "ar@autorun")),),
        "gemini": ((("gemini", "extensions", "update", extension)),),
        "uv": (
            (
                "uv", "pip", "install", "--python", sys.executable,
                "--upgrade", REPOSITORY,
            ),
            _REREGISTER,
        ),
        "pip": ((sys.executable, "-m", "pip", "install", "--upgrade", REPOSITORY), _REREGISTER),
    }[method]


def installed_extension_name(extensions_dir: Path) -> str:
    """The name this machine's extension actually uses, newest spelling first."""
    return next(
        (name for name in EXTENSION_NAMES if (extensions_dir / name).is_dir()),
        EXTENSION_NAMES[0],
    )


def _is_within(path: Path, parent: Path) -> bool:
    """Whether ``path`` is below ``parent``, without string-prefix mistakes."""
    try:
        path.resolve().relative_to(parent.resolve())
    except (OSError, ValueError):
        return False
    return True


def detect_update_method(
    *,
    origin: Path | None = None,
    home: Path | None = None,
    available: Callable[[str], bool] = shutil.which,
) -> str:
    """Infer the owning install route from this module's location.

    A harness executable on ``PATH`` proves only that the user installed the
    harness, not that it owns this copy of autorun.  Cache/extension locations
    are positive provenance.  Everything else is a Python distribution and is
    upgraded in the current interpreter (uv is only the transport when present).
    """
    source = (origin or Path(__file__)).resolve()
    base = (home or Path(os.environ.get("HOME", str(Path.home())))).resolve()
    from ..platforms import PLATFORMS
    from . import discovery

    claude_config = discovery.config_dir(PLATFORMS["claude"], home=base)
    gemini_config = discovery.config_dir(PLATFORMS["gemini"], home=base)
    claude_roots = (
        claude_config / "plugins" / "cache",
        claude_config / "plugins" / "marketplaces",
    )
    if any(_is_within(source, root) for root in claude_roots):
        return "claude"
    if _is_within(source, gemini_config / "extensions"):
        return "gemini"
    return "uv" if available("uv") else "pip"


@dataclass(frozen=True, slots=True)
class Version:
    """What is installed and what is published."""

    current: str = "unknown"
    latest: str = "unknown"

    @property
    def update_available(self) -> bool:
        """Unknown on either side is not an update.

        Reporting one would prompt an upgrade that cannot be verified, and the
        common cause of `unknown` is being offline rather than being stale.
        """
        return (
            "unknown" not in (self.current, self.latest)
            and _as_tuple(self.latest) > _as_tuple(self.current)
        )

    def describe(self) -> str:
        if self.latest == "unknown":
            return f"version check unavailable (current={self.current})"
        if self.current == "unknown":
            return f"installed version unknown (latest={self.latest})"
        if self.update_available:
            return f"update available: {self.current} -> {self.latest}"
        return f"up to date ({self.current})"


def _as_tuple(version: str) -> tuple:
    """A comparable key: numeric parts, then release-beats-prerelease.

    Two failures this avoids, both live:

    The comparison it replaces is ``tuple(int(x) for x in v.split("."))``, which
    raises ``ValueError`` on ``1.0.0rc1`` — the version actually installed right
    now — so self-update cannot compare anything on a prerelease build.

    Mixing ``int`` and ``str`` in one tuple is the other trap: ``(1, 0, "0rc1")``
    against ``(1, 0, 1)`` raises ``TypeError`` at the first differing position.
    Numbers and their suffixes are therefore split into separate slots, and the
    suffix slot sorts a release above any prerelease of the same number.
    """
    normalized = version.lstrip("vV").split("+", 1)[0]
    match = re.fullmatch(
        r"(?P<release>\d+(?:\.\d+)*)(?:[-_.]?(?P<phase>[A-Za-z]+)(?P<number>\d*)(?P<tail>.*))?",
        normalized,
    )
    if match is None:
        return ((), (0, normalized.casefold(), 0, ""))
    numbers = [int(part) for part in match.group("release").split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    phase = match.group("phase")
    if phase is None:
        prerelease = (1, "", 0, "")
    else:
        prerelease = (
            0,
            phase.casefold(),
            int(match.group("number") or 0),
            (match.group("tail") or "").casefold(),
        )
    return (tuple(numbers), prerelease)


def self_update(
    version: Version,
    *,
    method: str = "auto",
    extension: str = EXTENSION_NAMES[0],
    run: Runner = _spawn,
    available: Callable[[str], bool] = shutil.which,
) -> Outcome:
    """Upgrade this installation, or say why it did not.

    The version check happens first so an up-to-date install runs no
    subprocess at all — an upgrade command that reinstalls the same version
    still restarts the daemon and invalidates every harness's plugin cache.
    """
    if "unknown" in (version.current, version.latest):
        return Outcome("self-update", False, version.describe())
    if not version.update_available:
        return Outcome("self-update", True, version.describe())
    resolved = detect_update_method(available=available) if method == "auto" else method
    try:
        steps = update_argv(resolved, extension=extension, available=available)
    except KeyError:
        return Outcome("self-update", False, f"unknown update method {resolved!r}")
    for argv in steps:
        try:
            result = run(argv)
        except (OSError, subprocess.SubprocessError) as error:
            return Outcome("self-update", False, f"{type(error).__name__}: {error}")
        # Stop at the first failure rather than re-registering after a failed
        # upgrade, which would report success for a version that never landed.
        if result.returncode != 0:
            return Outcome("self-update", False, _first_line(result.stderr or result.stdout))
    return Outcome("self-update", True, version.describe())


def demo() -> None:
    """Self-check: argv and shell agree, quoting holds, the probe is honest."""
    project = Path("/tmp/a project/with space")

    command = UvCommand(project=project, script=Path("/x/hook_entry.py"), args=("--cli", "claude"))
    argv = command.argv()

    assert argv[:4] == ("uv", "run", "--quiet", "--no-sync"), argv
    assert "--project" in argv and str(project) in argv
    assert argv[-2:] == ("--cli", "claude")

    # The shell form is the SAME command, and survives a space in the path.
    rendered = command.shell()
    assert shlex.split(rendered) == list(argv), (rendered, argv)
    # Quoted from the same value rather than a written-out POSIX spelling:
    # `str(Path("/tmp/a project/with space"))` is a backslash path on Windows,
    # so the literal asserted the separator instead of the quoting.
    assert shlex.quote(str(project)) in rendered, (rendered, project)
    assert shlex.quote(str(project)) != str(project), "the space must force quoting"

    # Flags are described once, so both forms change together.
    loose = UvCommand(project=project, no_sync=False, quiet=False)
    assert "--no-sync" not in loose.argv() and "--quiet" not in loose.argv()
    assert shlex.split(loose.shell()) == list(loose.argv())

    # Environment assignments prefix the shell form only.
    with_env = UvCommand(project=project, env={"AUTORUN_CLI": "codex"})
    assert with_env.shell().startswith("AUTORUN_CLI=codex uv run")
    assert "AUTORUN_CLI=codex" not in with_env.argv()

    # An explicit interpreter is passed through in both forms.
    pinned = UvCommand(project=project, python="/usr/bin/python3.12")
    assert "--python" in pinned.argv() and "/usr/bin/python3.12" in pinned.argv()
    assert shlex.split(pinned.shell()) == list(pinned.argv())

    # The probe reports rather than raises when uv is missing or fails.
    missing = probe_runtime(Path("/nonexistent-project-xyz"), timeout=5)
    assert isinstance(missing, Probe)
    assert missing.ok is False or missing.executable, missing
    assert missing.describe(), "a probe always explains itself"

    if has_uv():
        assert shutil.which("uv"), "has_uv agrees with PATH"

    # --- bootstrap, with the subprocess boundary replaced ------------------
    calls: list[tuple[str, ...]] = []

    def ok(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(list(argv), 0, "", "")

    def fails(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(list(argv), 1, "", "could not resolve bashlex")

    plugin = Path("/p/plugins/autorun")
    done = bootstrap(plugin, run=ok)
    assert [o.step for o in done] == ["dependencies", "autorun CLI"], done
    assert all(o.ok for o in done)
    assert calls[0][:2] == ("uv", "sync"), calls[0]
    assert calls[1][:4] == ("uv", "tool", "install", "--force"), calls[1]
    assert "--python" in calls[1], "the interpreter is pinned, not left to PATH order"

    # A failed sync stops before installing a CLI that would import nothing.
    calls.clear()
    stopped = bootstrap(plugin, run=fails)
    assert len(stopped) == 1 and not stopped[0].ok
    assert "bashlex" in stopped[0].detail
    assert len(calls) == 1, "the CLI install never ran"
    assert "FAIL" in stopped[0].describe()

    # A uv tool environment has no project to sync.
    calls.clear()
    bootstrap(plugin, uv_tool_env=True, run=ok)
    assert calls[0][:3] == ("uv", "pip", "install"), calls[0]

    # A missing binary is reported, never raised into the install.
    def explodes(argv):
        raise FileNotFoundError("uv")

    crashed = bootstrap(plugin, run=explodes)
    assert len(crashed) == 1 and not crashed[0].ok and "FileNotFoundError" in crashed[0].detail

    # The daemon restart goes through this interpreter's CLI so AUTORUN_HOME
    # and the just-installed package version are both honoured.
    calls.clear()
    assert restart_daemon(run=ok).ok
    assert calls == [
        (sys.executable, "-m", "autorun", "--restart-daemon-after-install")
    ], calls

    # --- self-update -------------------------------------------------------
    assert Version("1.0.0", "1.0.1").update_available
    assert not Version("1.0.1", "1.0.1").update_available
    assert not Version("1.0.2", "1.0.1").update_available
    assert not Version("unknown", "1.0.1").update_available, "offline is not stale"
    assert not Version("1.0.0", "unknown").update_available

    # Numeric comparison: string ordering declines every upgrade past .9.
    assert Version("1.0.9", "1.0.10").update_available, "1.0.10 must outrank 1.0.9"
    assert Version("v1.0.0", "v1.2.0").update_available, "a leading v is tolerated"

    # Prereleases: the installed version right now is 1.0.0rc1, and the
    # comparison this replaces raises ValueError on it.
    assert Version("1.0.0rc1", "1.0.0").update_available, "a release beats its rc"
    assert Version("1.0.0rc1", "1.0.1").update_available
    assert not Version("1.0.0", "1.0.0rc1").update_available, "an rc never beats the release"
    assert Version("1.0.0rc1", "1.0.0rc2").update_available
    assert not Version("1.0.0rc2", "1.0.0rc1").update_available
    assert Version("1.0.0rc9", "1.0.0rc10").update_available
    assert not Version("1.0.0rc10", "1.0.0rc2").update_available

    calls.clear()
    assert self_update(Version("1.0.0", "1.0.0"), run=ok).ok
    assert calls == [], "an up-to-date install runs no subprocess at all"

    # A language-package upgrade re-registers afterwards. Without that second
    # step the source is new and every harness still loads its cached copy of
    # the old version, with nothing reporting the mismatch.
    calls.clear()
    assert self_update(Version("1.0.0", "1.0.1"), method="uv", run=ok).ok
    assert calls == [
        (
            "uv", "pip", "install", "--python", sys.executable,
            "--upgrade", REPOSITORY,
        ),
        _REREGISTER,
    ], calls

    calls.clear()
    self_update(Version("1.0.0", "1.0.1"), method="gemini", extension="autorun-workspace", run=ok)
    assert calls == [("gemini", "extensions", "update", "autorun-workspace")], calls
    assert len(calls) == 1, "a harness CLI updates its own cache"

    # The retired `plugin` spelling still resolves, to whichever CLI is here.
    calls.clear()
    self_update(Version("1.0.0", "1.0.1"), method="plugin", run=ok, available=lambda _: False)
    assert calls == [("claude", "plugin", "update", "ar@autorun")], calls
    calls.clear()
    self_update(
        Version("1.0.0", "1.0.1"), method="plugin", run=ok,
        available=lambda name: name == "gemini",
    )
    assert calls[0][0] == "gemini", calls

    # A failed upgrade does not go on to re-register, which would report
    # success for a version that never landed.
    calls.clear()
    assert not self_update(Version("1.0.0", "1.0.1"), method="uv", run=fails).ok
    assert len(calls) == 1, "stopped at the failure"

    failed = self_update(Version("1.0.0", "1.0.1"), method="uv", run=fails)
    assert not failed.ok and "bashlex" in failed.detail

    assert not self_update(Version("1.0.0", "1.0.1"), method="nonsense", run=ok).ok

    # An older installation still answers to its old extension name.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as ext_tmp:
        exts = Path(ext_tmp)
        assert installed_extension_name(exts) == "ar", "default when nothing exists"
        (exts / "autorun-workspace").mkdir()
        assert installed_extension_name(exts) == "autorun-workspace"
        (exts / "ar").mkdir()
        assert installed_extension_name(exts) == "ar", "newest spelling wins"

    # Provenance, not unrelated binaries on PATH, selects a harness updater.
    with _tf.TemporaryDirectory() as provenance_tmp:
        from ..platforms import PLATFORMS as _PLATFORMS
        from . import discovery as _discovery

        home = Path(provenance_tmp)
        claude_config = _discovery.config_dir(_PLATFORMS["claude"], home=home)
        gemini_config = _discovery.config_dir(_PLATFORMS["gemini"], home=home)
        assert claude_config is not None and gemini_config is not None
        claude_origin = (
            claude_config / "plugins" / "cache" / "autorun" / "ar"
            / "1.0.0" / "runtime.py"
        )
        gemini_origin = gemini_config / "extensions" / "ar" / "runtime.py"
        package_origin = home / "venv" / "site-packages" / "autorun" / "runtime.py"
        assert detect_update_method(origin=claude_origin, home=home) == "claude"
        assert detect_update_method(origin=gemini_origin, home=home) == "gemini"
        assert detect_update_method(
            origin=package_origin, home=home, available=lambda b: b == "uv"
        ) == "uv"
        assert detect_update_method(
            origin=package_origin, home=home, available=lambda _b: False
        ) == "pip"

    print("installer.runtime: all self-checks passed")


if __name__ == "__main__":
    demo()
