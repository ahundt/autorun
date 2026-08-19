#!/usr/bin/env python3
"""Every bug workaround resolves its flag through one grammar.

`plugins/autorun/AGENTS.md` specifies one grammar for every SDK bug workaround:
a single key that is both env var and CONFIG entry, values `true`/`1`/`auto`
(affected platform only), `always` (every platform), `false`/`0`/`never` (off),
resolved env -> CONFIG -> whether the platform is affected.

It was implemented four times, and two copies had drifted:

* `task_lifecycle.py` decided applicability with a hardcoded
  `detect_cli_type(...) == "claude"`, although `config.py` states
  "Applicability is Platform.has_exit2_workaround, never a hardcoded name" and
  `core.py` says it "replaces hard-coded cli_type == 'claude' checks with a
  registry query". A new harness in the same family would silently miss the
  workaround.
* `core.py` called `.lower()` without `.strip()`, so ` always ` worked for two
  flags and not the third.

REQUIREMENT for future workarounds: call `config.workaround_applies` and pass
applicability from a `Platform` field. Do not re-implement the value set.
"""

import re
from pathlib import Path

import pytest

from autorun.config import CONFIG, workaround_applies
from autorun.platforms import hook_platforms, platform_for

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "src"

#: Every flag that uses this grammar. A new workaround belongs here too.
GRAMMAR_FLAGS = [
    "AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED",
    "AUTORUN_BUG_CLAUDE_CODE_IGNORES_ADDITIONAL_CONTEXT_JSON_ENTRY_BUG_18534_WORKAROUND_ENABLED",
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED",
    "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_VANISH_MID_SESSION_BUG_80401_WORKAROUND_ENABLED",
]


@pytest.mark.parametrize("flag", GRAMMAR_FLAGS)
@pytest.mark.parametrize(
    "value,affected,expected",
    [
        ("always", False, True),
        ("always", True, True),
        ("never", True, False),
        ("false", True, False),
        ("0", True, False),
        ("true", True, True),
        ("1", True, True),
        ("auto", True, True),
        ("true", False, False),
        ("auto", False, False),
        # Whitespace and case: the exact drift that made ` always ` work for
        # two flags and not the third.
        ("  always  ", False, True),
        ("ALWAYS", False, True),
        ("  NeVeR ", True, False),
    ],
)
def test_the_grammar_is_identical_for_every_flag(monkeypatch, flag, value, affected, expected):
    monkeypatch.setenv(flag, value)
    assert workaround_applies(flag, affected=affected) is expected


@pytest.mark.parametrize("flag", GRAMMAR_FLAGS)
def test_an_unrecognized_spelling_falls_through_instead_of_disabling(monkeypatch, flag):
    """A typo must not silently switch a safety workaround off.

    Documented in config.py: "An unrecognized spelling falls through to the
    next tier rather than aborting: a typo in an env var must not silently
    disable blocking."
    """
    monkeypatch.setenv(flag, "yes-please")
    assert workaround_applies(flag, affected=True) is True


@pytest.mark.parametrize("flag", GRAMMAR_FLAGS)
def test_config_false_disables_when_no_env_var_is_set(monkeypatch, flag):
    monkeypatch.delenv(flag, raising=False)
    monkeypatch.setitem(CONFIG, flag, False)
    assert workaround_applies(flag, affected=True) is False


@pytest.mark.parametrize("flag", GRAMMAR_FLAGS)
def test_the_default_follows_the_platform(monkeypatch, flag):
    monkeypatch.delenv(flag, raising=False)
    assert workaround_applies(flag, affected=True) is True
    assert workaround_applies(flag, affected=False) is False


def test_a_legacy_spelling_outranks_the_current_key(monkeypatch):
    """`--exit2-mode never` must still win over the newer flag name."""
    legacy = "AUTORUN_EXIT2_WORKAROUND"
    flag = GRAMMAR_FLAGS[0]
    monkeypatch.setenv(legacy, "never")
    monkeypatch.setenv(flag, "always")
    assert workaround_applies(flag, affected=True, legacy_flags=(legacy,)) is False


def test_task_tool_applicability_comes_from_the_registry_not_a_name():
    """The #80305/#80401 predicate must be a Platform field.

    A hardcoded ``== "claude"`` is what this project already fixed once for
    #18534 and forbids in config.py's own comment. Pi, Prime and the
    Gemini-family harnesses are added as registry rows, so a name comparison
    silently excludes every future member of an affected family.
    """
    assert platform_for("claude").gates_mutable_task_tools is True
    affected = [p.name for p in hook_platforms() if p.gates_mutable_task_tools]
    assert affected == ["claude"], (
        "only Claude Code gates the four mutable Task tools today; if that "
        f"changed, update this expectation deliberately: {affected}"
    )


# --- version ranges -----------------------------------------------------------
#
# Harness versions differ between concurrent sessions on one machine, so a
# workaround's applicability is per invocation, not per install. #80305 is
# already version-scoped in prose ("Claude Code 2.1.233+") with no way to say so.


@pytest.mark.parametrize(
    "spec,version,expected",
    [
        # Open ranges.
        (">=2.1.233", "2.1.234", True),
        (">=2.1.233", "2.1.233", True),
        (">=2.1.233", "2.1.232", False),
        ("<2.2", "2.1.999", True),
        ("<2.2", "2.2.0", False),
        # Closed range.
        (">=2.1.233,<2.2", "2.1.240", True),
        (">=2.1.233,<2.2", "2.2.1", False),
        (">=2.1.233,<2.2", "2.1.1", False),
        # Exact and exclusion.
        ("==2.1.234", "2.1.234", True),
        ("==2.1.234", "2.1.235", False),
        ("!=2.1.234", "2.1.235", True),
        ("!=2.1.234", "2.1.234", False),
        # Uneven component counts compare as if zero-padded.
        (">=2.1", "2.1.0", True),
        (">=2.1.1", "2.1", False),
        # A build suffix does not defeat the leading numeric comparison.
        (">=2.1.233", "2.1.240-beta.1", True),
    ],
)
def test_a_version_range_decides_an_affected_platform(monkeypatch, spec, version, expected):
    flag = GRAMMAR_FLAGS[2]
    monkeypatch.setenv(flag, spec)
    assert workaround_applies(flag, affected=True, version=version) is expected


def test_a_range_never_applies_to_an_unaffected_platform(monkeypatch):
    """The range narrows an affected platform; it does not widen to others.

    #80305 is a Claude bug. A Gemini session reporting 2.1.240 must not inherit
    the workaround just because its version string satisfies the range.
    """
    flag = GRAMMAR_FLAGS[2]
    monkeypatch.setenv(flag, ">=2.1.233")
    assert workaround_applies(flag, affected=False, version="2.1.240") is False


@pytest.mark.parametrize("version", [None, "", "unknown", "dev"])
def test_an_unknown_version_keeps_todays_behavior(monkeypatch, version):
    """Fail open to the pre-range behavior rather than guessing.

    A harness that does not report its version must not silently lose a
    workaround it needs; that would be a regression disguised as precision.
    """
    flag = GRAMMAR_FLAGS[2]
    monkeypatch.setenv(flag, ">=2.1.233")
    assert workaround_applies(flag, affected=True, version=version) is True


@pytest.mark.parametrize("spec", [">=", ">=abc", ">2.1,garbage", "=>2.1"])
def test_a_malformed_range_falls_through_instead_of_disabling(monkeypatch, spec):
    """Same rule as a misspelled word: a typo must not disable a safety gate."""
    flag = GRAMMAR_FLAGS[2]
    monkeypatch.setenv(flag, spec)
    assert workaround_applies(flag, affected=True, version="2.1.240") is True


def test_ranges_do_not_disturb_the_word_grammar(monkeypatch):
    """`always`/`never` still win, and are not mistaken for version specs."""
    flag = GRAMMAR_FLAGS[2]
    monkeypatch.setenv(flag, "always")
    assert workaround_applies(flag, affected=False, version="1.0.0") is True
    monkeypatch.setenv(flag, "never")
    assert workaround_applies(flag, affected=True, version="2.1.240") is False


def test_only_one_module_implements_the_value_grammar():
    """Spec check: constrain the regression class.

    Four copies existed and two had drifted. A fifth would drift too, and
    nothing would fail until a user set a value that one copy understood and
    another did not.
    """
    disable_set = re.compile(r"""\{\s*["']false["']\s*,\s*["']0["']\s*,\s*["']never["']""")
    implementers = sorted(
        path.relative_to(PACKAGE_SRC).as_posix()
        for path in PACKAGE_SRC.rglob("*.py")
        if disable_set.search(path.read_text(encoding="utf-8"))
    )
    assert implementers == ["autorun/config.py"], (
        "the workaround value grammar must have exactly one implementation; "
        "call config.workaround_applies instead of re-parsing the values: "
        f"{implementers}"
    )
