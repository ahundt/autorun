#!/usr/bin/env python3
"""Settings resolve CLI parameter > environment > config file > default.

Three tiers existed. The file tier did not: three per-feature config files had
three separate loaders (`plan-export.config.json`, `plan-notify.config.json`,
`task-lifecycle.config.json`) and none of them backed the CONFIG dict, so a
user could tune those three features from a file and nothing else.

The file is overlaid onto CONFIG at import rather than consulted at each of the
fifty-odd `CONFIG.get` call sites. That is what makes the tier arrive
everywhere at once instead of wherever someone remembered to add it, and it is
why an absent file changes nothing: with no file, CONFIG holds exactly the
values its source declares.

Environment variables still win, because the resolvers that read them consult
the environment before CONFIG. That ordering is the point of the chain: a
setting exported for one session must not be overridden by a file written for
the machine.
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from autorun import config as config_module

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PLUGIN_ROOT / "src"

# The probe puts SRC_DIR first on sys.path instead of relying on the working
# directory. `python -c` puts the *caller's* cwd at sys.path[0], and CI runs
# pytest from PLUGIN_ROOT, where an autorun.py launcher used to sit.
# A module shadows a package of the same name, so an inherited-cwd import
# resolves to the launcher, fails on `autorun.python_check`, and exits 1 with
# its diagnostic on stdout. Keep the explicit path; do not "simplify" this to a
# bare `from autorun...`, and pass every process's returncode/stdout/stderr into
# the assertion so the next failure names itself.
_IMPORT_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])
    from autorun.config import CONFIG
    print(CONFIG["log_file_backup_count"])
    """
)


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORUN_HOME", str(tmp_path))
    # Tests here overlay a file onto the process-wide CONFIG that every other
    # module imported, so the overlay has to be undone or it leaks out of this
    # module. It did: a file setting only gemini's timeout left CONFIG holding
    # 3.25, and test_hook_entry's stdlib-mirror spec test then compared its
    # hardcoded 4.0 against that, failing several hundred tests later in
    # whichever order xdist happened to pick. The restore belongs to the
    # fixture rather than to each test, so a new test cannot forget it.
    saved = dict(config_module.CONFIG)
    yield tmp_path
    config_module.CONFIG.clear()
    config_module.CONFIG.update(saved)


def _write(config_home, payload):
    path = config_home / config_module.USER_CONFIG_FILENAME
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_an_absent_file_changes_nothing(config_home):
    """The common case: no file, no difference.

    This is what makes the tier safe to add to a release candidate.
    """
    defaults = config_module.default_config()
    assert config_module.apply_user_config(dict(defaults)) == defaults


@pytest.mark.parametrize("value,expected", [("critical-guards", "critical-guards"), ("block", "block"), ("allow", "allow"), ("unknown", "critical-guards")])
def test_overload_policy_file_and_environment_tiers(config_home, monkeypatch, value, expected):
    _write(config_home, {"overload_policy": value})
    config_module.apply_user_config(config_module.CONFIG)
    assert config_module.overload_policy() == expected
    assert ("unknown value" in config_module.overload_policy_status()) == (value == "unknown")
    monkeypatch.setenv("AUTORUN_OVERLOAD_POLICY", "allow")
    assert config_module.overload_policy() == "allow"
    monkeypatch.setenv("AUTORUN_OVERLOAD_POLICY", "misspelled")
    assert config_module.overload_policy() == "critical-guards"
    assert "unknown value 'misspelled'" in config_module.overload_policy_status()


def test_status_reports_unknown_overload_policy_without_daemon(config_home, monkeypatch, capsys):
    from autorun.installer import entrypoint
    monkeypatch.setenv("AUTORUN_OVERLOAD_POLICY", "typo")
    monkeypatch.setattr(entrypoint, "_marketplace_root", lambda: config_home)
    monkeypatch.setattr(entrypoint, "parse_selection", lambda *args: ())
    monkeypatch.setattr(entrypoint, "_resolved_settings", lambda **kwargs: ({}, ()))
    monkeypatch.setattr(entrypoint, "_harnesses", lambda *args: ((), {}, (), ()))
    assert entrypoint.show_status() == 1
    assert "overload_policy=critical-guards (unknown value 'typo'" in capsys.readouterr().out


@pytest.mark.parametrize("value", [123, {}, None, True])
def test_invalid_overload_policy_file_type_is_reported(config_home, value):
    _write(config_home, {"overload_policy": value})
    config_module.apply_user_config(config_module.CONFIG)
    assert config_module.overload_policy() == "critical-guards"
    assert "unknown value" in config_module.overload_policy_status()


def test_a_file_value_overrides_the_default(config_home):
    _write(config_home, {"log_file_backup_count": 9})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert merged["log_file_backup_count"] == 9


def test_an_unknown_key_is_ignored_rather_than_accepted(config_home):
    """A typo must not silently create a setting nothing reads.

    Accepting it would make `autorun --status` report a value that has no
    effect, which is worse than declining it.
    """
    _write(config_home, {"log_file_backup_kount": 9, "log_file_backup_count": 4})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert "log_file_backup_kount" not in merged
    assert merged["log_file_backup_count"] == 4


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("stage1_completion", "x"),
        ("stage2_completion", "x"),
        ("stage3_completion", "x"),
        ("recheck_template", "x"),
        ("plan_accepted_marker", "x"),
        ("monitor_stop_delay_seconds", 60),
        ("task_staleness_threshold", 30),
        ("task_staleness_no_tasks_threshold", 30),
        ("shared_agents_plugins_subdir", "x"),
    ],
)
def test_a_key_nothing_reads_is_not_a_setting(config_home, key, value):
    """These were declared, accepted from the file, and read only by tests.

    `autorun --status` reported the file's value while nothing outside
    config.py consulted it (task_staleness_threshold only when
    task_staleness_subsequent_threshold was itself invalid).
    """
    _write(config_home, {key: value})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert key not in merged


def test_a_wrong_type_is_declined(config_home):
    """A string where an int belongs must not reach arithmetic later."""
    _write(config_home, {"log_file_max_bytes": "quite large"})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert merged["log_file_max_bytes"] == config_module.default_config()["log_file_max_bytes"]


@pytest.mark.parametrize("content", ["{not json", "[]", "null", '"text"'])
def test_an_unreadable_file_leaves_the_defaults_alone(config_home, content):
    """A broken config file must not stop autorun starting.

    These settings gate command blocking and file policies. Refusing to load is
    a safer failure than refusing to run.
    """
    (config_home / config_module.USER_CONFIG_FILENAME).write_text(content, encoding="utf-8")
    assert config_module.apply_user_config(dict(config_module.default_config())) == (
        config_module.default_config()
    )


def test_the_environment_still_outranks_the_file(config_home, monkeypatch):
    """The ordering that gives the chain its meaning.

    A file is written for a machine; an environment variable is set for one
    session, and the narrower scope wins.
    """
    flag = "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED"
    _write(config_home, {flag: "never"})
    config_module.apply_user_config(config_module.CONFIG)
    assert config_module.CONFIG[flag] == "never", "the file must reach CONFIG"
    monkeypatch.setenv(flag, "always")

    assert config_module.workaround_applies(flag, affected=False) is True


@pytest.mark.parametrize(
    ("mode", "cli_type", "expected"),
    [("never", "claude", False), ("always", "gemini", True), (">=0", "claude", True)],
)
def test_a_file_sets_a_workaround_with_the_documented_words(
    config_home, monkeypatch, mode, cli_type, expected
):
    """The file tier speaks the same grammar as the environment tier.

    These keys are declared ``True``, and the type check used to accept only a
    JSON bool for them, so ``"never"`` -- the word every comment and AGENTS.md
    documents -- was dropped without a sign and exit 2 stayed on.
    """
    flag = config_module.BUG_4669_FLAG
    for name in (flag, config_module.BUG_4669_LEGACY_FLAG):
        monkeypatch.delenv(name, raising=False)
    _write(config_home, {flag: mode})
    config_module.apply_user_config(config_module.CONFIG)

    assert config_module.CONFIG[flag] == mode
    assert config_module.should_use_exit2_workaround({"cli_type": cli_type}) is expected


@pytest.mark.parametrize("mode", ["nevr", ">=not a version", 3])
def test_a_workaround_value_outside_the_grammar_is_declined(config_home, mode):
    """A typo must not reach CONFIG and be reported as a setting."""
    flag = config_module.BUG_4669_FLAG
    _write(config_home, {flag: mode})
    merged = config_module.apply_user_config(dict(config_module.default_config()))
    assert merged[flag] is True


def test_the_live_config_reflects_the_file_at_import(config_home):
    """Wired, not merely available -- the entry point has to apply it.

    A loader nothing calls is the failure this project has hit before: the
    check exists, the caller never passes it, and every unit test still passes.

    Runs in a subprocess rather than reloading the module. `importlib.reload`
    rebinds `config.CONFIG` to a new dict while every module that already did
    `from .config import CONFIG` keeps the old one, so the reload silently
    splits the process into two configurations and breaks later tests -- which
    is exactly what it did when this test first used it.
    """
    _write(config_home, {"log_file_backup_count": 7})
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, str(SRC_DIR)],
        capture_output=True, text=True, timeout=120,
    )
    detail = f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 0, detail
    assert result.stdout.strip() == "7", f"the file tier is not applied at import: {detail}"


_NO_DEPENDENCIES_PROBE = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, sys.argv[1])

    class _Missing:
        def find_spec(self, name, path=None, target=None):
            if name.split(".")[0] in {"filelock", "psutil", "bashlex", "yaml",
                                      "packaging", "tomlkit"}:
                raise ImportError("blocked: " + name)
            return None

    sys.meta_path.insert(0, _Missing())
    import autorun
    from autorun import config
    print(autorun.CONFIG is config.CONFIG)
    """
)


def test_the_package_config_is_the_config_module_without_dependencies(config_home):
    """``autorun.CONFIG`` is config.CONFIG even when a dependency is missing.

    __init__.py fell back to a hand-copied CONFIG whenever `.main` failed to
    import (it needs filelock), and that copy had drifted: a different
    stage2_completion, no /ar: command_mappings, older SEARCH policy text.
    config.py imports only the stdlib at module level, so it needs no fallback.
    """
    result = subprocess.run(
        [sys.executable, "-c", _NO_DEPENDENCIES_PROBE, str(SRC_DIR)],
        capture_output=True, text=True, timeout=120,
    )
    detail = f"rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    assert result.returncode == 0, detail
    assert result.stdout.strip() == "True", detail


@pytest.mark.parametrize(
    "setting",
    ["daemon_client_response_timeouts_seconds", "hook_wrapper_timeouts_seconds"],
)
def test_a_partial_harness_dict_does_not_break_every_harness(config_home, setting):
    """A file may name one harness without disabling the gate for all of them.

    The tier replaces a dict setting rather than merging it, so CONFIG's copy
    is exactly what the file said. Both per-harness lookups in client.py used
    to spell their fallback ``timeouts["claude"]`` -- an index into that same
    replaced dict -- so a file naming only "gemini" raised KeyError for every
    harness, including "gemini" itself. The outer handler turns that into a
    daemon-failure response, and for a tool-gate event that response denies,
    so a plausible config file blocked every tool everywhere.

    The value the user did supply must win, and the harnesses they left out
    must fall back to the declared default rather than to whatever the file
    happens to contain.
    """
    from autorun import client

    _write(config_home, {setting: {"gemini": 3.25}})
    config_module.apply_user_config(config_module.CONFIG)

    lookup = (
        client.daemon_response_timeout_for_cli
        if setting == "daemon_client_response_timeouts_seconds"
        else client.client_total_budget
    )
    declared = config_module.default_config()[setting]

    assert lookup("gemini") == pytest.approx(
        3.25 if setting == "daemon_client_response_timeouts_seconds"
        else max(3.25 - client.CLIENT_BUDGET_MARGIN_SECONDS, 0.1)
    ), "the harness the file names must get the value it was given"

    for harness in ("claude", "codex"):
        # Not an exact-value assertion: the point is that an omitted harness
        # resolves to *something* from the declared defaults instead of raising.
        assert lookup(harness) > 0, (
            f"{harness!r} was absent from the file and must fall back to the "
            f"declared default {declared.get(harness)!r}, not raise"
        )


def test_naming_one_integration_does_not_disarm_the_other_guards(config_home):
    """A dict setting is merged onto its default, never substituted for it.

    ``default_integrations`` is the safety-guard table: ``rm``, ``dd if=``,
    ``fdisk``, ``git push`` and 44 more. Overlaying a file value by assignment
    means a file that names one command *replaces* all 48, so a user adding a
    single integration of their own silently turns every guard off and nothing
    reports it. Guards are the feature; losing them by omission is the worst
    failure this tier can have, and it is indistinguishable from working.

    Merging keeps omission meaning "no opinion" for every dict setting rather
    than "delete". Naming a key still overrides that key outright, which is how
    a user deliberately retunes one guard or one harness's timeout.
    """
    declared = config_module.default_config()["default_integrations"]
    assert "rm" in declared, "fixture assumes rm ships as a default guard"

    _write(config_home, {"default_integrations": {"mycmd": {"action": "block"}}})
    resolved = config_module.apply_user_config(dict(config_module._DEFAULT_CONFIG))
    integrations = resolved["default_integrations"]

    assert "mycmd" in integrations, "the user's own integration must be added"
    missing = sorted(set(declared) - set(integrations))
    assert not missing, (
        f"a one-entry config file disarmed {len(missing)} shipped guards: {missing[:8]}"
    )


def test_naming_a_key_overrides_exactly_that_key(config_home):
    """Merge must not degrade into "the file cannot change anything"."""
    _write(config_home, {"daemon_client_response_timeouts_seconds": {"gemini": 9.5}})
    resolved = config_module.apply_user_config(dict(config_module._DEFAULT_CONFIG))
    timeouts = resolved["daemon_client_response_timeouts_seconds"]

    assert timeouts["gemini"] == 9.5, "the named key must take the file's value"
    declared = config_module.default_config()["daemon_client_response_timeouts_seconds"]
    assert timeouts["claude"] == declared["claude"], "an unnamed key keeps its default"
