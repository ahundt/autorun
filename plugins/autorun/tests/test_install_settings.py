"""One declaration drives resolution, validation, help text and both parsers.

The installer this replaces resolves settings three ways: `resolve_choice_setting`
for fixed vocabularies, a hand-rolled ladder for runtime architecture, and
`_truthy_env` for booleans. The first two agree; the third does not, and the
disagreement is a live defect — `_truthy_env` returns True for *anything* it
does not recognise, so a typo silently enables the flag it was meant to set.

These tests pin the shared precedence rule, the fall-through the third resolver
got wrong, and the property that makes the two parsers unable to drift.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.installer.traversal import Context  # noqa: E402
from autorun.installer.settings import (  # noqa: E402
    CODEX_HOOK_SOURCE,
    CODEX_PLUGIN_MARKETPLACE,
    CONDUCTOR,
    HOOK_NO_SYNC,
    INSTALL_SETTINGS,
    Resolved,
    SHARED_SKILLS_BRIDGE,
    SKILL_PLACEMENT,
    WRITE_SOURCE_METADATA,
    build_parser,
    harness_names,
    resolve_all,
)


# ─── One precedence rule, shared by every setting ────────────────────────────


def _home_context(tmp_path, monkeypatch, marketplace_root):
    """A Context whose `home` agrees with `$HOME`, which is the only seam.

    Setting the field alone moves some routes and not others: anything anchored
    at `Path.home()` reads `$HOME` directly. `Context` refuses the mismatch, so
    a test that wants an isolated home redirects the variable.
    """
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # Both names: Path.home() resolves through os.path.expanduser, which reads
    # USERPROFILE on Windows and HOME elsewhere and never consults the other,
    # so setting one isolates this test on one platform and lets it write the
    # real home on the other.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return Context(marketplace_root=marketplace_root, home=home)


def test_cli_outranks_env_outranks_default():
    env = {"AUTORUN_SHARED_SKILLS_BRIDGE": "copy"}

    assert SHARED_SKILLS_BRIDGE.resolve("link", env=env, config={}).value == "link"
    assert SHARED_SKILLS_BRIDGE.resolve(None, env=env, config={}).value == "copy"
    assert SHARED_SKILLS_BRIDGE.resolve(None, env={}, config={}).value == "none"


def test_config_is_consulted_after_env_and_before_the_default():
    assert SHARED_SKILLS_BRIDGE.resolve(
        None, env={}, config={"shared_skills_bridge": "copy"}
    ).value == "copy"
    assert SHARED_SKILLS_BRIDGE.resolve(
        None, env={"AUTORUN_SHARED_SKILLS_BRIDGE": "link"},
        config={"shared_skills_bridge": "copy"},
    ).value == "link"


def test_the_resolved_source_says_where_a_value_came_from():
    """--status has to explain why a setting has the value it has; a bare
    value cannot."""
    resolved = SHARED_SKILLS_BRIDGE.resolve(None, env={"AUTORUN_SHARED_SKILLS_BRIDGE": "link"}, config={})

    assert resolved.value == "link"
    assert "AUTORUN_SHARED_SKILLS_BRIDGE" in resolved.source


# ─── Retired spellings keep working ──────────────────────────────────────────


def test_a_retired_env_var_still_resolves():
    """The bridge was named for Claude when Claude was the only harness it
    could reach. Ignoring the old spelling would silently drop a setting a user
    already has exported."""
    assert SHARED_SKILLS_BRIDGE.resolve(
        None, env={"AUTORUN_CLAUDE_AGENTS_SKILLS": "link"}, config={}
    ).value == "link"


def test_the_current_spelling_outranks_its_alias():
    assert SHARED_SKILLS_BRIDGE.resolve(
        None,
        env={"AUTORUN_SHARED_SKILLS_BRIDGE": "copy", "AUTORUN_CLAUDE_AGENTS_SKILLS": "link"},
        config={},
    ).value == "copy"


def test_a_retired_config_key_still_resolves():
    assert SHARED_SKILLS_BRIDGE.resolve(
        None, env={}, config={"claude_agents_skills": "copy"}
    ).value == "copy"


# ─── The divergence this closes ──────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["garbage", "typo", "maybe", "TRUE-ish", "2"])
def test_an_unrecognised_boolean_falls_through_instead_of_enabling(raw):
    """`_truthy_env` returns True for anything it does not recognise, so
    `AUTORUN_WRITE_SOURCE_METADATA=typo` enables a flag whose default is False.
    One rule for every tier: a value this setting does not accept is not a
    value, so the next tier answers."""
    resolved = WRITE_SOURCE_METADATA.resolve(
        None, env={"AUTORUN_WRITE_SOURCE_METADATA": raw}, config={}
    )

    assert resolved.value is False
    assert resolved.source == "default"


@pytest.mark.parametrize(
    "raw, expected",
    [("1", True), ("true", True), ("yes", True), ("on", True),
     ("0", False), ("false", False), ("no", False), ("off", False),
     ("  TRUE  ", True)],
)
def test_documented_boolean_spellings_are_accepted(raw, expected):
    resolved = HOOK_NO_SYNC.resolve(None, env={"AUTORUN_HOOK_NO_SYNC": raw}, config={})

    assert resolved.value is expected
    assert resolved.source != "default"


@pytest.mark.parametrize("raw", ["typo", "", "  "])
def test_an_unrecognised_choice_falls_through_rather_than_aborting(raw):
    """A bad environment value must not abort an install that a flag or the
    default could still satisfy."""
    assert SHARED_SKILLS_BRIDGE.resolve(None, env={"AUTORUN_SHARED_SKILLS_BRIDGE": raw},
                                        config={}).source == "default"


# ─── Per-harness placement ───────────────────────────────────────────────────


def test_placement_accepts_a_bare_mode_and_per_harness_overrides():
    assert SKILL_PLACEMENT.parse("native") == {"": "native"}
    assert SKILL_PLACEMENT.parse("codex=both claude=native") == {
        "codex": "both", "claude": "native"
    }


def test_placement_rejects_an_unregistered_harness():
    """The vocabulary is read from the registry at parse time, so a name that
    parses cannot be one nothing installs."""
    assert SKILL_PLACEMENT.parse("nosuchharness=native") is None


def test_the_harness_vocabulary_comes_from_the_registry():
    from autorun.platforms import PLATFORMS

    assert set(harness_names()) == set(PLATFORMS)


# ─── A repeated flag is a list, and stringifying one destroys it ─────────────


def _parsed(argv: list[str], name: str):
    parser = build_parser(INSTALL_SETTINGS, prog="autorun", description="install",
                          targets=harness_names())
    return resolve_all(INSTALL_SETTINGS, parser.parse_args(argv), config={})[name].value


def test_a_placement_flag_survives_the_parser():
    """argparse `append` hands resolve() a list. `str(["native"])` is
    "['native']", which no parser accepts, so the flag resolved to the default
    and the install proceeded on a layout the user did not ask for."""
    assert _parsed(["--skill-placement", "native"], "skill_placement") == {"": "native"}
    assert _parsed(["--skill-placement", "codex=both"], "skill_placement") == {"codex": "both"}


def test_repeating_the_placement_flag_merges_rather_than_replaces():
    value = _parsed(
        ["--skill-placement", "codex=both", "--skill-placement", "claude=native"],
        "skill_placement",
    )

    assert value == {"codex": "both", "claude": "native"}


def test_a_custom_harness_spec_is_not_mangled_into_a_directory_it_would_create():
    """The stringified-list form parsed, which is worse than failing: the name
    became "['mine" and config_dir "~/.mine']", a path starting with ~/ that the
    installer would expand and write a harness configuration into."""
    harnesses = _parsed(["--custom-harness", "mine=claude:mybin:~/.mine"], "custom_harness")

    assert len(harnesses) == 1
    assert (harnesses[0].name, harnesses[0].config_dir) == ("mine", "~/.mine")


def test_repeating_the_custom_harness_flag_keeps_both():
    harnesses = _parsed(
        ["--custom-harness", "a=claude:x:~/.a", "--custom-harness", "b=codex:y:~/.b"],
        "custom_harness",
    )

    assert [h.name for h in harnesses] == ["a", "b"]


def test_a_boolean_setting_is_a_flag_pair_not_a_flag_taking_a_value():
    """`--tool` and `--no-conductor` are the spellings the CLI documents. With
    action="store" both exited 2 at argparse."""
    assert _parsed(["--tool"], "tool") is True
    assert _parsed(["--conductor"], "conductor") is True
    assert _parsed(["--no-conductor"], "conductor") is False
    assert _parsed([], "conductor") is CONDUCTOR.default


def test_an_unset_boolean_flag_leaves_the_other_tiers_their_turn():
    """store_const rather than store_true: store_true would supply False for an
    absent flag, which outranks the environment and CONFIG."""
    parser = build_parser(INSTALL_SETTINGS, prog="autorun", description="install",
                          targets=harness_names())

    assert parser.parse_args([]).conductor is None


def _cli(argv: list[str]):
    """Parse with the plugin vocabulary supplied, as the entry point does."""
    parser = build_parser(INSTALL_SETTINGS, prog="autorun", description="install",
                          targets=harness_names(), selections=("ar", "pdf-extractor"))
    return parser.parse_args(argv)


def test_a_value_after_a_boolean_flag_is_an_error_not_a_plugin_name():
    """`--conductor false` reads as "install Conductor, and install the plugin
    called false". With an unconstrained positional argparse accepted it in
    silence: Conductor stayed on and the install proceeded on a bogus
    selection. The spelling that turns it off is `--no-conductor`."""
    with pytest.raises(SystemExit):
        _cli(["--conductor", "false"])

    assert _cli(["--no-conductor"]).conductor == "false"
    assert _cli(["--conductor"]).conductor == "true"


def test_an_unknown_plugin_selection_is_rejected():
    with pytest.raises(SystemExit):
        _cli(["nosuchplugin"])

    assert _cli(["ar"]).selection == "ar"
    assert _cli([]).selection == "all"


def test_the_short_form_of_tool_still_works():
    """`--tool` has always also been `-t`. A generated parser emitting only the
    long form silently breaks the one people type."""
    assert _cli(["-t"]).tool == "true"
    assert _cli(["--tool"]).tool == "true"


def test_a_typo_exits_through_argparse_rather_than_a_traceback():
    """A setting with no fixed vocabulary still has a parser that can reject.
    Routing that through argparse gives the usual usage message and exit 2;
    letting it reach resolve_all gives the user a stack trace."""
    with pytest.raises(SystemExit):
        _cli(["--skill-placement", "nosuchharness=both"])

    with pytest.raises(SystemExit):
        _cli(["--hook-python", ""])


def test_the_config_mapping_form_of_placement_resolves():
    """install.py accepted a mapping of harness to mode with an optional
    "default" key. Stringifying it to its repr dropped the whole entry."""
    resolved = SKILL_PLACEMENT.resolve(
        None, env={}, config={"skill_placement": {"default": "native", "codex": "both"}}
    )

    assert resolved.value == {"": "native", "codex": "both"}
    assert resolved.source == "config"


# ─── A typo the user just typed is an error; a stale export is not ───────────


def test_a_typo_in_a_flag_names_the_problem():
    """Silently dropping an unrecognised token installs a layout the caller did
    not ask for and gives no signal that the override was ignored."""
    with pytest.raises(ValueError, match="nosuchharness"):
        SKILL_PLACEMENT.resolve(["nosuchharness=both"], env={}, config={})

    with pytest.raises(ValueError, match="One of"):
        CODEX_HOOK_SOURCE.resolve("typo", env={}, config={})


@pytest.mark.parametrize("tier", ["env", "config"])
def test_a_stale_export_falls_through_rather_than_aborting_the_install(tier):
    """plugins/autorun/AGENTS.md lesson 7: fail open when data is unknown."""
    env = {"AUTORUN_CODEX_HOOK_SOURCE": "typo"} if tier == "env" else {}
    config = {} if tier == "env" else {"codex_hook_source": "typo"}

    assert CODEX_HOOK_SOURCE.resolve(None, env=env, config=config).source == "default"


# ─── The parsers cannot drift ────────────────────────────────────────────────


def test_every_setting_reaches_the_generated_parser():
    """Two hand-written parsers declaring the same flag is why
    `skill_placement_help()` was factored out. Generating both from one tuple
    removes the reason for the workaround rather than maintaining it."""
    parser = build_parser(INSTALL_SETTINGS, prog="autorun", description="install",
                          targets=harness_names())
    namespace = parser.parse_args([])

    for setting in INSTALL_SETTINGS:
        assert hasattr(namespace, setting.name), setting.name
        assert getattr(namespace, setting.name) is None, (
            f"{setting.name}: an argparse default is indistinguishable from an "
            "explicit choice and would outrank the environment"
        )


def test_declarations_have_distinct_names_flags_and_env_vars():
    """A collision on any tier means one setting silently shadows another."""
    for attribute in ("name", "option", "env"):
        seen = [getattr(s, attribute) for s in INSTALL_SETTINGS]
        assert len(seen) == len(set(seen)), f"duplicate {attribute}: {seen}"


def test_help_text_carries_the_machine_checkable_parts():
    """The choices, the default and the env var are generated rather than
    retyped, so help cannot disagree with behaviour."""
    rendered = SHARED_SKILLS_BRIDGE.rendered_help()

    assert "link, copy, none" in rendered
    assert "Default: none" in rendered
    assert "AUTORUN_SHARED_SKILLS_BRIDGE" in rendered
    assert "AUTORUN_CLAUDE_AGENTS_SKILLS" in rendered, "retired spellings are documented"


# ─── Custom harnesses ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("mytool=gemini:mycli:~/.mytool", ("mytool", "gemini", "mycli", "~/.mytool", "mytool")),
        # `::` separates the display name so a config dir containing a literal
        # colon stays unambiguous — which is why this is not a plain split.
        ("t=claude:c:~/.my:tool::My Tool", ("t", "claude", "c", "~/.my:tool", "My Tool")),
    ],
)
def test_a_well_formed_spec_parses(spec, expected):
    from autorun.installer.settings import parse_custom_harness

    custom = parse_custom_harness(spec)

    assert (custom.name, custom.flavor, custom.binary,
            custom.config_dir, custom.display_name) == expected


@pytest.mark.parametrize(
    "spec",
    ["no-equals-sign", "n=nosuchflavor:bin:~/dir", "n=gemini:missing-third-part", "=gemini:b:~/d"],
)
def test_a_malformed_spec_returns_none_rather_than_aborting(spec):
    """Every Setting parser follows the same contract: an unusable value is not
    a value, so the next tier answers instead of the install dying."""
    from autorun.installer.settings import parse_custom_harness

    assert parse_custom_harness(spec) is None


def test_the_flavor_vocabulary_comes_from_the_registry():
    from autorun.installer.settings import flavors
    from autorun.platforms import CUSTOM_HARNESS_FLAVOR_ALIASES

    assert set(flavors()) == set(CUSTOM_HARNESS_FLAVOR_ALIASES)


def test_a_custom_harness_inherits_its_flavors_protocol():
    """The flavor is the hook identity. Getting it wrong does not fail loudly —
    it sends one harness's response schema to another."""
    from autorun.installer.settings import parse_custom_harness, synthesize
    from autorun.platforms import PLATFORMS

    custom = parse_custom_harness("mytool=gemini:mycli:~/.mytool::My Tool")

    platform = synthesize(custom)

    assert platform.name == "mytool"
    assert platform.binary == "mycli"
    assert platform.config_dir == "~/.mytool"
    assert platform.hook_protocol is PLATFORMS["gemini"].hook_protocol
    assert platform.config_dir_env_vars == (), "a custom root is not env-overridable"


def test_a_custom_harness_needs_no_orchestrator_branch(tmp_path, monkeypatch):
    """The installer this replaces dispatches on flavor with a three-way
    if/elif — claude to one path, codex to another, everything else to a third.
    A custom harness *is* its flavor with different paths, so cloning the
    registry entry and reusing the flavor's steps removes the branch entirely."""
    from autorun.installer.settings import (
        parse_custom_harness,
        steps_for_custom,
        synthesize,
    )
    from autorun.installer.traversal import Intent, Mode, run, targets

    source = tmp_path / "skills" / "commit"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("# commit\n", encoding="utf-8")

    def skills_step(harness, ctx):
        base = Path(harness.platform.config_dir.replace("~", str(ctx.home)))
        return [Intent(target=base / "skills" / "commit", source=source, plugin="ar")]

    custom = parse_custom_harness("mytool=gemini:mycli:~/.mytool::My Tool")
    table = steps_for_custom(custom, {"gemini": (skills_step,)})
    paired = targets([synthesize(custom)], table)
    ctx = _home_context(tmp_path, monkeypatch, tmp_path)

    assert [d.verdict.value for d in run(paired, ctx, Mode.INSTALL)] == ["publish"]
    assert (tmp_path / "home" / ".mytool" / "skills" / "commit" / "SKILL.md").is_file()
    assert [d.verdict.value for d in run(paired, ctx, Mode.INSTALL)] == ["skip"]
    assert [d.verdict.value for d in run(paired, ctx, Mode.UNINSTALL)] == ["retire"]
    assert not (tmp_path / "home" / ".mytool" / "skills" / "commit").exists()


def test_resolve_all_resolves_once_at_the_entry_point():
    """Re-resolving in a callee re-applies the environment over the caller's
    explicit intent — the bug that made the custom-harness path fail under
    AUTORUN_CODEX_HOOK_SOURCE=plugin."""
    parser = build_parser(INSTALL_SETTINGS, prog="autorun", description="install",
                          targets=harness_names())
    namespace = parser.parse_args(["--codex-hook-source", "plugin"])

    resolved = resolve_all(INSTALL_SETTINGS, namespace, config={})

    assert resolved["codex_hook_source"].value == "plugin"
    assert resolved["codex_hook_source"].source == "cli"
    assert resolved["codex_plugin_marketplace"].value == CODEX_PLUGIN_MARKETPLACE.default


def test_a_config_entry_decides_without_the_caller_passing_config(monkeypatch):
    """The installer being replaced defaulted the config tier to {} and made
    every call site pass CONFIG by hand. A setting one of them missed lost the
    tier in silence, so a user's CONFIG entry did nothing and nothing said so."""
    from autorun.installer.settings import autorun_config

    monkeypatch.setitem(autorun_config(), CODEX_HOOK_SOURCE.name, "both")

    assert CODEX_HOOK_SOURCE.resolve(env={}) == Resolved("both", "config")


def test_the_flag_and_the_environment_still_outrank_a_config_entry(monkeypatch):
    from autorun.installer.settings import autorun_config

    monkeypatch.setitem(autorun_config(), CODEX_HOOK_SOURCE.name, "both")

    assert CODEX_HOOK_SOURCE.resolve("none", env={}).value == "none"
    assert CODEX_HOOK_SOURCE.resolve(env={"AUTORUN_CODEX_HOOK_SOURCE": "user"}).value == "user"
