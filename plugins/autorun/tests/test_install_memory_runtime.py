"""Autorun's region inside a user's memory file, and how it invokes uv.

Two capabilities that share one property: both write something a harness reads
once and never re-validates, so a malformed result does not raise — it silently
changes behaviour. A doubled memory block quietly grows the user's file every
install, and a mis-rendered uv command produces stderr, which Claude Code treats
as a hook failure and uses to disable every hook for the session while
everything still looks healthy.
"""
from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.memory import Block, bounds, foreign_slugs, splice, strip  # noqa: E402
from autorun.installer.runtime import Probe, UvCommand, has_uv, probe_runtime  # noqa: E402

BLOCK = Block("guidance")
USER_TEXT = "# My notes\n\nKeep this paragraph.\n"


@pytest.fixture
def memory(tmp_path: Path) -> Path:
    target = tmp_path / "AGENTS.md"
    target.write_text(USER_TEXT, encoding="utf-8")
    return target


# ─── The memory block never disturbs the user's file ─────────────────────────


def test_the_block_is_added_beside_the_users_text(memory):
    assert splice(memory, "autorun guidance", BLOCK) is True

    text = memory.read_text()
    assert "Keep this paragraph." in text
    assert text.count(BLOCK.start) == 1 and text.count(BLOCK.end) == 1


def test_updating_replaces_in_place_and_never_appends_a_second_block(memory):
    """The failure this prevents is an install that locates the region
    differently from the code that wrote it, appending a new block every run
    until the user's file is mostly autorun."""
    splice(memory, "first", BLOCK)
    splice(memory, "second", BLOCK)

    text = memory.read_text()
    assert text.count(BLOCK.start) == 1
    assert "second" in text and "first" not in text


def test_an_unchanged_block_is_not_rewritten(memory):
    splice(memory, "same", BLOCK)

    assert splice(memory, "same", BLOCK) is False, "no mtime churn on a no-op install"


def test_a_body_quoting_the_sentinels_cannot_terminate_its_own_region(memory):
    """Guidance text that documents the markers would otherwise close the
    region early and leave the remainder loose in the user's file."""
    splice(memory, f"the markers are {BLOCK.start} and {BLOCK.end}", BLOCK)

    assert memory.read_text().count(BLOCK.start) == 1


def test_stripping_restores_the_file_exactly(memory):
    splice(memory, "autorun guidance", BLOCK)

    assert strip(memory, BLOCK) is True
    assert memory.read_text() == USER_TEXT
    assert strip(memory, BLOCK) is False


def test_a_file_holding_only_our_block_is_removed(tmp_path):
    """A file autorun created and then emptied is litter; one the user wrote
    in stays."""
    ours = tmp_path / "CLAUDE.md"
    splice(ours, "just us", BLOCK)

    assert strip(ours, BLOCK) is True
    assert not ours.exists()


@pytest.mark.parametrize(
    "content",
    [
        "user text\n{end}\nmore user text\n{start}\n",   # inverted
        "{start}\nno end marker\n",                       # half a region
        "{end}\nonly a close marker\n",                   # the other half
    ],
)
def test_a_malformed_region_is_treated_as_absent(tmp_path, content):
    """Rewriting a guessed range is how a user's own paragraphs get swallowed
    into an autorun block."""
    target = tmp_path / "broken.md"
    target.write_text(content.format(start=BLOCK.start, end=BLOCK.end), encoding="utf-8")

    assert bounds(target.read_text(), BLOCK) is None

    splice(target, "ours", BLOCK)

    assert "user text" in target.read_text() or "marker" in target.read_text()


def test_two_blocks_coexist_without_disturbing_each_other(memory):
    other = Block("cache")
    splice(memory, "first", BLOCK)
    splice(memory, "second", other)

    strip(memory, BLOCK)

    text = memory.read_text()
    assert "second" in text and "first" not in text
    assert "Keep this paragraph." in text


def test_a_block_from_an_unknown_version_is_reported_not_ignored(memory):
    """Uninstall only removes slugs it knows, so an unrecognised one is
    invisible litter in the user's file unless something names it."""
    splice(memory, "ours", BLOCK)
    splice(memory, "theirs", Block("retired-feature"))

    assert foreign_slugs(memory, known=["guidance"]) == ("retired-feature",)
    assert foreign_slugs(memory, known=["guidance", "retired-feature"]) == ()


# ─── One uv command, two renderings that cannot disagree ─────────────────────


def test_argv_and_shell_are_the_same_command():
    command = UvCommand(
        project=Path("/tmp/proj"), script=Path("/tmp/proj/hooks/hook_entry.py"),
        args=("--cli", "claude"),
    )

    assert shlex.split(command.shell()) == list(command.argv())


def test_a_project_path_with_a_space_survives_the_shell_rendering():
    """Hand-rolled quoting truncated the command at the space, and the hook
    then failed in the one way that disables every hook without an error."""
    command = UvCommand(project=Path("/tmp/a project"), script=Path("/tmp/a project/h.py"))

    assert shlex.split(command.shell()) == list(command.argv())
    assert str(Path("/tmp/a project")) in shlex.split(command.shell())


@pytest.mark.parametrize("no_sync, quiet", [(True, True), (False, False), (True, False)])
def test_flags_are_described_once_so_both_forms_change_together(no_sync, quiet):
    command = UvCommand(project=Path("/p"), no_sync=no_sync, quiet=quiet)

    assert ("--no-sync" in command.argv()) is no_sync
    assert ("--quiet" in command.argv()) is quiet
    assert shlex.split(command.shell()) == list(command.argv())


def test_environment_assignments_prefix_only_the_shell_form():
    command = UvCommand(project=Path("/p"), env={"AUTORUN_CLI": "codex"})

    assert command.shell().startswith("AUTORUN_CLI=codex uv run")
    assert "AUTORUN_CLI=codex" not in command.argv()


# ─── The probe reports rather than raises ────────────────────────────────────


def test_the_probe_always_explains_itself(tmp_path):
    """An arm64 host resolving an x86_64 interpreter is invisible until a
    native dependency fails to import inside a hook."""
    result = probe_runtime(tmp_path / "no-such-project", timeout=5)

    assert isinstance(result, Probe)
    assert result.describe()
    if not result.ok:
        assert result.reason, "a failed probe names the reason"


def test_has_uv_agrees_with_path():
    import shutil

    assert has_uv() == (shutil.which("uv") is not None)


# ─── Bootstrap, with the subprocess boundary replaced ───────────────────────


def _recorder(returncode: int = 0, stderr: str = ""):
    """A fake Runner. Real tests, no spawned uv, no touched daemon."""
    calls: list[tuple[str, ...]] = []

    def run(argv):
        import subprocess

        calls.append(tuple(argv))
        return subprocess.CompletedProcess(list(argv), returncode, "", stderr)

    return run, calls


def test_bootstrap_syncs_then_installs_the_cli():
    from autorun.installer.runtime import bootstrap

    run, calls = _recorder()

    outcomes = bootstrap(Path("/p/plugins/autorun"), run=run)

    assert [o.step for o in outcomes] == ["dependencies", "autorun CLI"]
    assert all(o.ok for o in outcomes)
    assert calls[0][:2] == ("uv", "sync")
    assert calls[1][:4] == ("uv", "tool", "install", "--force")


def test_installed_distribution_needs_no_source_project_bootstrap(tmp_path):
    from autorun.installer.runtime import bootstrap

    package = tmp_path / "site-packages" / "autorun"
    package.mkdir(parents=True)
    (package / "__main__.py").write_text("# installed package")
    run, calls = _recorder()

    outcomes = bootstrap(package, run=run)

    assert calls == []
    assert len(outcomes) == 1 and outcomes[0].ok
    assert outcomes[0].step == "installed distribution"


def test_hook_command_uses_installed_python_without_a_wheel_pyproject(tmp_path):
    import shlex
    import sys

    from autorun.installer.runtime import hook_command

    package = tmp_path / "site packages" / "autorun"
    script = package / "hooks" / "hook_entry.py"
    script.parent.mkdir(parents=True)
    script.write_text("# hook")

    command = hook_command(package, cli="codex")

    assert command.argv() == (sys.executable, str(script), "--cli", "codex")
    assert shlex.split(command.shell()) == list(command.argv())


def test_hook_command_keeps_uv_project_for_a_source_checkout(tmp_path):
    from autorun.installer.runtime import UvCommand, hook_command

    plugin = tmp_path / "plugins" / "autorun"
    plugin.mkdir(parents=True)
    (plugin / "pyproject.toml").write_text("[project]\n")

    command = hook_command(plugin, cli="opencode", python="/python", no_sync=False)

    assert isinstance(command, UvCommand)
    assert command.project == plugin
    assert command.args == ("--cli", "opencode")
    assert command.python == "/python"
    assert command.no_sync is False


def test_installed_distribution_probe_reports_the_running_interpreter(tmp_path):
    import platform
    import sys

    from autorun.installer.runtime import probe_hook_runtime

    package = tmp_path / "site-packages" / "autorun"
    package.mkdir(parents=True)

    probe = probe_hook_runtime(package)

    assert probe.ok
    assert probe.uv_path == ""
    assert probe.executable == sys.executable
    assert probe.machine == platform.machine()
    assert probe.system == platform.system()


def test_a_failed_sync_stops_before_installing_the_cli():
    """Installing the tool from a project whose dependencies did not resolve
    produces a CLI that imports nothing, and the failure then surfaces inside a
    hook rather than during the install that caused it."""
    from autorun.installer.runtime import bootstrap

    run, calls = _recorder(returncode=1, stderr="could not resolve bashlex")

    outcomes = bootstrap(Path("/p"), run=run)

    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "bashlex" in outcomes[0].detail
    assert len(calls) == 1, "the CLI install never ran"


def test_a_uv_tool_environment_has_no_project_to_sync():
    from autorun.installer.runtime import bootstrap

    run, calls = _recorder()

    bootstrap(Path("/p"), uv_tool_env=True, run=run)

    assert calls[0][:3] == ("uv", "pip", "install")


def test_a_missing_uv_binary_is_reported_never_raised():
    from autorun.installer.runtime import bootstrap

    def explodes(argv):
        raise FileNotFoundError("uv")

    outcomes = bootstrap(Path("/p"), run=explodes)

    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "FileNotFoundError" in outcomes[0].detail


def test_the_interpreter_is_pinned_rather_than_left_to_path_order():
    """PATH order otherwise silently selects a Python for the wrong CPU
    architecture — Intel Homebrew under Rosetta on Apple Silicon is the case
    that shipped."""
    from autorun.installer.runtime import uv_tool_install_argv

    argv = uv_tool_install_argv(Path("/p"), python="/usr/bin/python3.12")

    assert "--python" in argv
    assert argv[argv.index("--python") + 1] == "/usr/bin/python3.12"


# ─── Self-update ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "current, latest, expected",
    [
        ("1.0.0", "1.0.1", True),
        ("1.0.1", "1.0.1", False),
        ("1.0.2", "1.0.1", False),
        ("1.0.9", "1.0.10", True),
        ("1.0.10", "1.0.9", False),
        ("v1.0.0", "v1.2.0", True),
        # The version actually installed right now is a prerelease. The
        # comparison this replaces is tuple(int(x) for x in v.split(".")),
        # which raises ValueError on it, so self-update could not compare
        # anything at all on a prerelease build.
        ("1.0.0rc1", "1.0.0", True),
        ("1.0.0", "1.0.0rc1", False),
        ("1.0.0rc1", "1.0.1", True),
        ("1.0.0rc1", "1.0.0rc2", True),
        ("1.0.0rc2", "1.0.0rc1", False),
        ("1.0.0rc9", "1.0.0rc10", True),
        ("1.0.0rc10", "1.0.0rc2", False),
        ("1.0.0rc1", "1.0.0rc1", False),
    ],
)
def test_version_comparison(current, latest, expected):
    from autorun.installer.runtime import Version

    assert Version(current, latest).update_available is expected


def test_the_currently_installed_version_can_be_compared():
    """A regression guard tied to reality: whatever this machine has installed
    must be comparable, not raise."""
    from importlib.metadata import PackageNotFoundError, version as installed_version

    from autorun.installer.runtime import Version

    try:
        current = installed_version("autorun")
    except PackageNotFoundError:
        pytest.fail("uv did not install the autorun workspace package metadata")

    assert Version(current, "99.0.0").update_available is True
    assert Version(current, current).update_available is False


@pytest.mark.parametrize("unknown", [("unknown", "1.0.1"), ("1.0.0", "unknown")])
def test_an_unknown_version_is_not_an_update(unknown):
    """The common cause of `unknown` is being offline, not being stale.
    Prompting an upgrade that cannot be verified is worse than saying nothing."""
    from autorun.installer.runtime import Version

    assert Version(*unknown).update_available is False


@pytest.mark.parametrize("unknown", [("unknown", "1.0.1"), ("1.0.0", "unknown")])
def test_explicit_update_fails_when_version_check_is_inconclusive(unknown):
    from autorun.installer.runtime import Version, self_update

    run, calls = _recorder()

    outcome = self_update(Version(*unknown), run=run)

    assert not outcome.ok
    assert calls == []


def test_an_up_to_date_install_runs_no_subprocess_at_all():
    """An upgrade command that reinstalls the same version still restarts the
    daemon and invalidates every harness's plugin cache."""
    from autorun.installer.runtime import Version, self_update

    run, calls = _recorder()

    assert self_update(Version("1.0.0", "1.0.0"), run=run).ok
    assert calls == []


@pytest.mark.parametrize(
    "method, expected_head",
    [("uv", ("uv", "pip", "install", "--python")),
     ("claude", ("claude", "plugin", "update", "ar@autorun")),
     ("gemini", ("gemini", "extensions", "update"))],
)
def test_each_method_runs_its_own_command(method, expected_head):
    from autorun.installer.runtime import Version, self_update

    run, calls = _recorder()

    self_update(Version("1.0.0", "1.0.1"), method=method, run=run)

    assert calls[0][:len(expected_head)] == expected_head


def test_an_unknown_method_is_reported_not_raised():
    from autorun.installer.runtime import Version, self_update

    run, _ = _recorder()

    outcome = self_update(Version("1.0.0", "1.0.1"), method="nonsense", run=run)

    assert not outcome.ok and "nonsense" in outcome.detail


def test_an_older_installation_still_answers_to_its_old_extension_name(tmp_path):
    """Updating the wrong name reports success while leaving the real install
    untouched."""
    from autorun.installer.runtime import installed_extension_name

    assert installed_extension_name(tmp_path) == "ar"
    (tmp_path / "autorun-workspace").mkdir()
    assert installed_extension_name(tmp_path) == "autorun-workspace"
    (tmp_path / "ar").mkdir()
    assert installed_extension_name(tmp_path) == "ar", "newest spelling wins"


def test_update_detection_uses_install_provenance_not_unrelated_binaries(tmp_path):
    """A binary on PATH does not prove that binary owns this installation."""
    from autorun.installer.runtime import detect_update_method

    home = tmp_path / "home"
    claude = home / ".claude" / "plugins" / "cache" / "autorun" / "ar" / "1.0.0" / "runtime.py"
    gemini = home / ".gemini" / "extensions" / "ar" / "runtime.py"
    package = tmp_path / "venv" / "site-packages" / "autorun" / "runtime.py"

    def every_cli(_binary):
        return True

    assert detect_update_method(origin=claude, home=home, available=every_cli) == "claude"
    assert detect_update_method(origin=gemini, home=home, available=every_cli) == "gemini"
    assert detect_update_method(origin=package, home=home, available=every_cli) == "uv"
    assert detect_update_method(origin=package, home=home, available=lambda _b: False) == "pip"


def test_a_language_package_upgrade_reregisters_afterwards():
    """Upgrading the Python package replaces the source but not what any
    harness loads: each keeps serving its own cached copy of the previous
    version, so the user sees no change and nothing reports why."""
    from autorun.installer.runtime import REPOSITORY, Version, self_update

    run, calls = _recorder()

    assert self_update(Version("1.0.0", "1.0.1"), method="uv", run=run).ok

    assert calls[0] == (
        "uv", "pip", "install", "--python", sys.executable, "--upgrade", REPOSITORY
    )
    assert calls[1][-2:] == ("--install", "--force"), calls
    assert len(calls) == 2


def test_a_harness_cli_update_needs_no_second_step():
    from autorun.installer.runtime import Version, self_update

    run, calls = _recorder()

    self_update(Version("1.0.0", "1.0.1"), method="claude", run=run)

    assert calls == [("claude", "plugin", "update", "ar@autorun")]


def test_a_failed_upgrade_does_not_go_on_to_reregister():
    """Re-registering after a failed upgrade reports success for a version that
    never landed."""
    from autorun.installer.runtime import Version, self_update

    def fails(argv):
        calls.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 1, "", "network unreachable")

    calls: list[tuple[str, ...]] = []

    outcome = self_update(Version("1.0.0", "1.0.1"), method="pip", run=fails)

    assert not outcome.ok
    assert "network unreachable" in outcome.detail
    assert len(calls) == 1, "stopped at the failure"


@pytest.mark.parametrize(
    "present, expected",
    [({"claude"}, "claude"), ({"gemini"}, "gemini"), (set(), "claude")],
    ids=["claude", "gemini", "neither-installed"],
)
def test_the_retired_plugin_method_still_resolves(present, expected):
    """`plugin` predates telling the two harness CLIs apart. A user with it in
    a script would otherwise be told to pick from methods they never chose."""
    from autorun.installer.runtime import Version, self_update

    run, calls = _recorder()

    self_update(
        Version("1.0.0", "1.0.1"), method="plugin", run=run,
        available=lambda name: name in present,
    )

    assert calls[0][0] == expected


def test_the_disable_switch_for_the_context_guidance_block_has_a_reader(monkeypatch):
    """A documented env var that nothing consults is worse than no switch: the
    user sets it and the workaround keeps running with nothing to say why."""
    from autorun.installer.memory import CONTEXT_GUIDANCE_FLAG, context_guidance_enabled

    assert context_guidance_enabled() is True, "a workaround is on until turned off"
    for token in ("false", "0", "never"):
        monkeypatch.setenv(CONTEXT_GUIDANCE_FLAG, token)
        assert context_guidance_enabled() is False, token
    for token in ("true", "1", "auto", "always"):
        monkeypatch.setenv(CONTEXT_GUIDANCE_FLAG, token)
        assert context_guidance_enabled() is True, token


def test_the_daemon_restart_goes_through_the_cli():
    """The daemon's socket and PID live under AUTORUN_HOME. Signalling a PID
    directly would reach the developer's live daemon from a test that
    redirected that variable; going through the CLI honours it for free."""
    from autorun.installer.runtime import restart_daemon

    run, calls = _recorder()

    assert restart_daemon(run=run).ok
    assert calls == [("autorun", "--restart-daemon")]
