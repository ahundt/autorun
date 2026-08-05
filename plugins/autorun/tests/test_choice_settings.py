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


# --------------------------------------------------------------------------
# Skill placement — one route per harness, alternate layouts explicit
# --------------------------------------------------------------------------


def test_skill_placement_setting_matches_documented_contract():
    from autorun.install import _SKILL_PLACEMENT_SETTING

    assert _SKILL_PLACEMENT_SETTING.env_var == "AUTORUN_SKILL_PLACEMENT"
    assert _SKILL_PLACEMENT_SETTING.default == "auto"
    assert _SKILL_PLACEMENT_SETTING.config_key == "skill_placement"
    assert set(_SKILL_PLACEMENT_SETTING.choices) == {"auto", "native", "both"}


def test_skill_placement_precedence_is_cli_env_config_default(monkeypatch):
    from autorun.install import _SKILL_PLACEMENT_SETTING

    monkeypatch.setenv("AUTORUN_SKILL_PLACEMENT", "native")
    assert (
        resolve_choice_setting(
            _SKILL_PLACEMENT_SETTING, cli_value="both", config={"skill_placement": "native"}
        ).value
        == "both"
    )
    assert (
        resolve_choice_setting(
            _SKILL_PLACEMENT_SETTING, cli_value=None, config={"skill_placement": "both"}
        ).value
        == "native"
    )

    monkeypatch.delenv("AUTORUN_SKILL_PLACEMENT")
    assert (
        resolve_choice_setting(
            _SKILL_PLACEMENT_SETTING, cli_value=None, config={"skill_placement": "both"}
        ).value
        == "both"
    )
    assert (
        resolve_choice_setting(
            _SKILL_PLACEMENT_SETTING, cli_value=None, config={}
        ).value
        == "auto"
    )


def test_absent_skill_placement_flag_reaches_install_plugins_as_none():
    """argparse must forward "unspecified" so the env var can still win."""
    from unittest.mock import patch

    from autorun.__main__ import main

    with patch("autorun.install.install_plugins", return_value=0) as install:
        main(["--install", "--codex"])

    assert install.call_args.kwargs["skill_placement"] is None


def test_explicit_skill_placement_flag_reaches_install_plugins():
    from unittest.mock import patch

    from autorun.__main__ import main

    with patch("autorun.install.install_plugins", return_value=0) as install:
        main(["--install", "--codex", "--skill-placement", "both"])

    assert install.call_args.kwargs["skill_placement"] == ["both"]


def test_repeated_skill_placement_flags_all_reach_install_plugins():
    """A multi-harness install states several routes in one command."""
    from unittest.mock import patch

    from autorun.__main__ import main

    with patch("autorun.install.install_plugins", return_value=0) as install:
        main(
            [
                "--install",
                "--skill-placement",
                "native",
                "--skill-placement",
                "codex=both",
            ]
        )

    assert install.call_args.kwargs["skill_placement"] == ["native", "codex=both"]


def test_skill_placement_argparse_rejects_an_invalid_cli_value(capsys):
    """A CLI typo is caught at parse time, before any install work; env and
    config keep the fail-open contract so a stale export cannot abort a run."""
    from autorun.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--install", "--skill-placement", "shared"])

    assert exc.value.code != 0
    assert "shared" in capsys.readouterr().err


def test_skill_placement_argparse_rejects_an_unknown_harness(capsys):
    from autorun.__main__ import main

    with pytest.raises(SystemExit) as exc:
        main(["--install", "--skill-placement", "codx=native"])

    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "codx" in err and "codex" in err


def test_both_parsers_offer_the_same_skill_placement_choices():
    """install.py and __main__.py must not drift into two grammars."""
    from autorun.__main__ import _skill_placement_choices
    from autorun.install import _SKILL_PLACEMENT_SETTING

    assert _skill_placement_choices() == _SKILL_PLACEMENT_SETTING.choices


def test_describe_skill_routes_names_mode_and_exact_paths_per_harness():
    """"harness -> resolved mode -> exact paths" is what makes `auto` auditable;
    a mode name alone does not tell a user which directory gets written."""
    from autorun.install import describe_skill_routes, shared_agents_skills_dir

    lines = describe_skill_routes("auto", ["codex", "claude"])
    rendered = "\n".join(lines)

    assert "codex" in rendered and "claude" in rendered
    assert str(shared_agents_skills_dir()) in rendered
    # Claude cannot read the shared root, so `auto` must not point it there.
    claude_line = next(line for line in lines if "claude" in line)
    assert str(shared_agents_skills_dir()) not in claude_line
    assert "native" in claude_line


def test_describe_skill_routes_flags_the_duplicate_risk_of_both():
    from autorun.install import describe_skill_routes

    rendered = "\n".join(describe_skill_routes("both", ["codex"]))

    assert "shared" in rendered and "native" in rendered
    assert "duplicate" in rendered.lower()


def test_dry_run_reports_the_resolved_skill_placement_and_its_source(
    tmp_path, monkeypatch, capsys
):
    """The user must be able to see which tier decided the layout before any
    directory is written."""
    from autorun.install import install_plugins

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AUTORUN_SKILL_PLACEMENT", "native")

    install_plugins("ar", dry_run=True)

    out = capsys.readouterr().out
    assert "Skill placement: native" in out
    assert "AUTORUN_SKILL_PLACEMENT" in out


# --------------------------------------------------------------------------
# Multi-harness placement: one install writes several harnesses at once
# --------------------------------------------------------------------------


def test_bare_mode_applies_to_every_harness():
    """The zero-configuration form must keep working unchanged."""
    from autorun.install import parse_skill_placement

    placement = parse_skill_placement(["native"])

    assert placement.for_harness("codex") == "native"
    assert placement.for_harness("claude") == "native"


def test_per_harness_value_overrides_the_global_mode():
    """A multi-harness install must be able to route harnesses differently
    without running the installer once per harness."""
    from autorun.install import parse_skill_placement

    placement = parse_skill_placement(["native", "codex=both"])

    assert placement.for_harness("codex") == "both"
    assert placement.for_harness("gemini") == "native"


def test_per_harness_value_without_a_global_mode_falls_back_to_the_default():
    from autorun.install import parse_skill_placement

    placement = parse_skill_placement(["codex=native"], default="auto")

    assert placement.for_harness("codex") == "native"
    assert placement.for_harness("claude") == "auto"


def test_last_value_wins_for_a_repeated_key():
    from autorun.install import parse_skill_placement

    placement = parse_skill_placement(["auto", "codex=native", "codex=both", "native"])

    assert placement.for_harness("codex") == "both"
    assert placement.for_harness("claude") == "native"


def test_unknown_harness_is_rejected_with_the_valid_names():
    """Silently ignoring a typo would install a layout the user did not ask
    for and give no signal that the override was dropped."""
    from autorun.install import parse_skill_placement

    with pytest.raises(ValueError) as exc:
        parse_skill_placement(["codx=native"])

    message = str(exc.value)
    assert "codx" in message
    assert "codex" in message


def test_unknown_mode_is_rejected_with_the_valid_modes():
    from autorun.install import parse_skill_placement

    with pytest.raises(ValueError) as exc:
        parse_skill_placement(["codex=shared"])

    message = str(exc.value)
    assert "shared" in message
    assert "auto" in message and "native" in message and "both" in message


@pytest.mark.parametrize("raw", ["native codex=both", "native,codex=both"])
def test_env_var_accepts_the_same_grammar_space_or_comma_separated(raw, monkeypatch):
    from autorun.install import resolve_skill_placement

    monkeypatch.setenv("AUTORUN_SKILL_PLACEMENT", raw)
    placement = resolve_skill_placement(cli_values=None, config={})

    assert placement.for_harness("codex") == "both"
    assert placement.for_harness("claude") == "native"


def test_config_accepts_a_per_harness_mapping():
    """A mapping is the natural config shape; a string must keep working too."""
    from autorun.install import resolve_skill_placement

    mapping = resolve_skill_placement(
        cli_values=None,
        config={"skill_placement": {"default": "native", "codex": "both"}},
        env={},
    )
    assert mapping.for_harness("codex") == "both"
    assert mapping.for_harness("claude") == "native"

    plain = resolve_skill_placement(
        cli_values=None, config={"skill_placement": "native"}, env={}
    )
    assert plain.for_harness("codex") == "native"


def test_cli_values_beat_the_env_var():
    from autorun.install import resolve_skill_placement

    placement = resolve_skill_placement(
        cli_values=["both"], config={}, env={"AUTORUN_SKILL_PLACEMENT": "native"}
    )

    assert placement.for_harness("codex") == "both"


def test_invalid_env_value_falls_open_to_the_default_without_aborting():
    """CLAUDE.md lesson 7: a stale export must not abort an install. A CLI
    typo still fails hard, because the user is right there to fix it."""
    from autorun.install import resolve_skill_placement

    placement = resolve_skill_placement(
        cli_values=None, config={}, env={"AUTORUN_SKILL_PLACEMENT": "codx=native"}
    )

    assert placement.for_harness("codex") == "auto"


def test_route_report_lists_every_destination_directory():
    """A harness with more than one skill root must not have the second one
    silently dropped by an `or`."""
    import dataclasses

    from autorun.install import describe_skill_routes, platform_config_dir
    from autorun.platforms import PLATFORMS

    from autorun.platforms import CombinedSkillRoutes, ConfigDirSkills, ExtensionSkills

    multi = dataclasses.replace(
        PLATFORMS["claude"],
        name="multiroot",
        extensions_subdir="exts",
        native_skills=CombinedSkillRoutes(
            (ConfigDirSkills("skills"), ExtensionSkills("exts"))
        ),
    )
    lines = describe_skill_routes(
        "native", ["multiroot"], platforms={"multiroot": multi}
    )
    rendered = "\n".join(lines)

    config_dir = platform_config_dir(multi)
    assert str(config_dir / "skills") in rendered
    assert str(config_dir / "exts") in rendered


def test_route_report_uses_the_per_harness_mode():
    from autorun.install import describe_skill_routes, parse_skill_placement

    lines = describe_skill_routes(
        parse_skill_placement(["native", "codex=both"]), ["codex", "claude"]
    )

    codex_line = next(line for line in lines if line.startswith("codex"))
    claude_line = next(line for line in lines if line.startswith("claude"))
    assert "both" in codex_line
    assert "native" in claude_line
