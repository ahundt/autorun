"""Fixed-choice install settings resolve as CLI > env > config > default.

Two defects motivated this:

1. **Env beat an explicit CLI flag.** `_codex_hook_source_from_env(default)` read
   the env var and fell back to `default` — but callers passed the *CLI value* as
   `default`, so `AUTORUN_CODEX_HOOK_SOURCE` silently overrode
   `--codex-hook-source`. Every other resolver in this module
   (`resolve_runtime_architecture_settings`, install.py) documents CLI > env >
   config > defaults.

2. **The override was applied twice.** `install_plugins` resolved the value, then
   `_install_for_codex` resolved it again from the environment. The custom-harness
   path passes `codex_hook_source="user"` explicitly to force a config-dir-scoped
   install; the second resolution turned that into `plugin` whenever the env var
   was set, tripping the "custom Codex config-dir installs support user hooks
   only" hard error.

Distinguishing "user passed the default value" from "argparse filled in the
default" requires argparse `default=None`, which is why `cli_value=None` means
unspecified here — the same convention `resolve_runtime_architecture_settings`
uses for `cli_python`/`cli_hook_no_sync`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.install import (  # noqa: E402
    _CODEX_HOOK_SOURCE_SETTING,
    _CODEX_PLUGIN_MARKETPLACE_SETTING,
    ChoiceSetting,
    resolve_choice_setting,
)

SETTING = ChoiceSetting(
    name="demo",
    env_var="AUTORUN_DEMO_CHOICE",
    choices=("alpha", "beta", "gamma"),
    default="alpha",
    config_key="demo_choice",
)


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------


def test_cli_wins_over_env_and_config():
    """The bug: an explicit flag must not lose to an environment variable."""
    resolved = resolve_choice_setting(
        SETTING,
        cli_value="beta",
        env={"AUTORUN_DEMO_CHOICE": "gamma"},
        config={"demo_choice": "gamma"},
    )
    assert resolved.value == "beta"
    assert resolved.source == "cli"


def test_env_wins_over_config_when_cli_absent():
    resolved = resolve_choice_setting(
        SETTING,
        cli_value=None,
        env={"AUTORUN_DEMO_CHOICE": "beta"},
        config={"demo_choice": "gamma"},
    )
    assert resolved.value == "beta"
    assert resolved.source == "env AUTORUN_DEMO_CHOICE"


def test_config_wins_over_default_when_cli_and_env_absent():
    resolved = resolve_choice_setting(
        SETTING, cli_value=None, env={}, config={"demo_choice": "gamma"}
    )
    assert resolved.value == "gamma"
    assert resolved.source == "config"


def test_default_when_nothing_is_set():
    resolved = resolve_choice_setting(SETTING, cli_value=None, env={}, config={})
    assert resolved.value == "alpha"
    assert resolved.source == "default"


# --------------------------------------------------------------------------
# Validation — an unrecognized value must never propagate
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bogus", ["delta", "", "   ", "ALPHA BETA"])
def test_invalid_env_value_falls_through_to_the_next_source(bogus):
    resolved = resolve_choice_setting(
        SETTING,
        cli_value=None,
        env={"AUTORUN_DEMO_CHOICE": bogus},
        config={"demo_choice": "gamma"},
    )
    assert resolved.value == "gamma"


def test_invalid_config_value_falls_through_to_default():
    resolved = resolve_choice_setting(
        SETTING, cli_value=None, env={}, config={"demo_choice": "nonsense"}
    )
    assert resolved.value == "alpha"
    assert resolved.source == "default"


def test_invalid_cli_value_falls_through_rather_than_raising():
    """Fail open: an unknown value must not abort an install."""
    resolved = resolve_choice_setting(
        SETTING, cli_value="delta", env={"AUTORUN_DEMO_CHOICE": "beta"}, config={}
    )
    assert resolved.value == "beta"


@pytest.mark.parametrize("raw", ["BETA", "  beta  ", "Beta"])
def test_values_are_case_and_whitespace_normalized(raw):
    resolved = resolve_choice_setting(SETTING, cli_value=None, env={"AUTORUN_DEMO_CHOICE": raw})
    assert resolved.value == "beta"


def test_missing_config_key_is_not_an_error():
    """A setting with no config_key simply skips that tier."""
    setting = ChoiceSetting(
        name="demo", env_var="AUTORUN_DEMO_CHOICE", choices=("a", "b"), default="a"
    )
    resolved = resolve_choice_setting(setting, cli_value=None, env={}, config={"x": "b"})
    assert resolved.value == "a"


def test_resolution_is_pure_with_respect_to_the_real_environment(monkeypatch):
    """Passing env explicitly must not consult os.environ."""
    monkeypatch.setenv("AUTORUN_DEMO_CHOICE", "gamma")
    resolved = resolve_choice_setting(SETTING, cli_value=None, env={})
    assert resolved.value == "alpha"


def test_omitting_env_reads_os_environ(monkeypatch):
    monkeypatch.setenv("AUTORUN_DEMO_CHOICE", "gamma")
    resolved = resolve_choice_setting(SETTING, cli_value=None)
    assert resolved.value == "gamma"


# --------------------------------------------------------------------------
# The two real settings keep their documented defaults
# --------------------------------------------------------------------------


def test_codex_hook_source_setting_matches_documented_contract():
    assert _CODEX_HOOK_SOURCE_SETTING.env_var == "AUTORUN_CODEX_HOOK_SOURCE"
    assert _CODEX_HOOK_SOURCE_SETTING.default == "user"
    assert set(_CODEX_HOOK_SOURCE_SETTING.choices) == {"user", "plugin", "both", "none"}


def test_codex_plugin_marketplace_setting_matches_documented_contract():
    assert (
        _CODEX_PLUGIN_MARKETPLACE_SETTING.env_var == "AUTORUN_CODEX_PLUGIN_MARKETPLACE"
    )
    assert _CODEX_PLUGIN_MARKETPLACE_SETTING.default == "personal"
    assert set(_CODEX_PLUGIN_MARKETPLACE_SETTING.choices) == {"personal", "github"}


# --------------------------------------------------------------------------
# Defect 2 — the double resolution, at the call site that broke
# --------------------------------------------------------------------------


def test_absent_flag_reaches_install_plugins_as_none(monkeypatch):
    """The CLI must forward "unspecified", not its own default.

    Passing argparse's default down would make it indistinguishable from an
    explicit choice, and resolve_choice_setting would then rank it above the
    environment variable.
    """
    from unittest.mock import patch

    from autorun.__main__ import main

    with patch("autorun.install.install_plugins", return_value=0) as install:
        main(["--install", "--codex"])

    kwargs = install.call_args.kwargs
    assert kwargs["codex_hook_source"] is None
    assert kwargs["codex_plugin_marketplace"] is None


def test_env_var_is_honored_when_the_flag_is_absent(monkeypatch, tmp_path):
    """End-to-end: AUTORUN_CODEX_HOOK_SOURCE applies with no flag given."""
    monkeypatch.setenv("AUTORUN_CODEX_HOOK_SOURCE", "none")
    resolved = resolve_choice_setting(_CODEX_HOOK_SOURCE_SETTING, cli_value=None)
    assert resolved.value == "none"


def test_explicit_flag_beats_the_env_var_end_to_end(monkeypatch):
    """The regression this whole change exists to prevent."""
    monkeypatch.setenv("AUTORUN_CODEX_HOOK_SOURCE", "plugin")
    resolved = resolve_choice_setting(_CODEX_HOOK_SOURCE_SETTING, cli_value="user")
    assert resolved.value == "user"
    assert resolved.source == "cli"


def test_install_for_codex_does_not_re_resolve_an_explicit_hook_source(
    tmp_path, monkeypatch
):
    """The custom-harness regression, pinned.

    install_plugins passes codex_hook_source="user" explicitly to force a
    config-dir-scoped install. _install_for_codex used to re-read the env var
    and turn that into "plugin", tripping the hard error below.
    """
    from autorun.install import _install_for_codex

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTORUN_CODEX_HOOK_SOURCE", "plugin")

    ok, message = _install_for_codex(
        tmp_path / "marketplace",
        ["autorun"],
        codex_hook_source="user",
        codex_dir=tmp_path / "custom-codex",
        install_global_assets=False,
    )

    assert "user hooks only" not in message, (
        "env var overrode an explicitly passed codex_hook_source"
    )
