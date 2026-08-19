#!/usr/bin/env python3
"""Where a harness's version comes from, and what happens when it does not.

Version-ranged workarounds need a version per invocation, because different
harness builds run concurrently on one machine. Measured on a live machine
rather than assumed:

* there is no ``CLAUDE_CODE_VERSION`` in the environment;
* the only version-bearing variable present is ``CLAUDE_AGENT_SDK_VERSION``,
  which is the SDK's version, not the CLI's;
* ``docs/claude-code-hooks-api.md`` documents no version field in the hook
  payload, and no captured payload carries one.

The SDK value shares its trailing component with the CLI build, which suggests
they track each other -- and a guessed correspondence is not something to gate
a permission workaround on. So auto-detection is declared per harness in the
registry and stays empty where no reliable source is known, "unknown" is a
first-class answer, and an explicit override exists for an operator who does
know.
"""

import pytest

from autorun.config import harness_version, HARNESS_VERSION_ENV_VAR
from autorun.platforms import hook_platforms, platform_for


def test_the_explicit_override_wins(monkeypatch):
    """An operator who knows the version can always say so."""
    monkeypatch.setenv(HARNESS_VERSION_ENV_VAR, "9.9.9")
    assert harness_version("claude") == "9.9.9"


def test_an_unknown_version_is_none_not_a_guess(monkeypatch):
    """None is a real answer and resolves to the pre-range behavior.

    Inventing a version here would silently change whether a workaround
    engages, which is worse than not knowing.
    """
    monkeypatch.delenv(HARNESS_VERSION_ENV_VAR, raising=False)
    for platform in hook_platforms():
        for name in platform.version_env_vars:
            monkeypatch.delenv(name, raising=False)
        assert harness_version(platform.name) is None


def test_a_registered_source_is_read_when_present(monkeypatch):
    """A harness that declares a source has it consulted."""
    monkeypatch.delenv(HARNESS_VERSION_ENV_VAR, raising=False)
    platform = next(
        (p for p in hook_platforms() if p.version_env_vars), None
    )
    if platform is None:
        pytest.skip("no harness declares a version source yet")
    monkeypatch.setenv(platform.version_env_vars[0], "1.2.3")
    assert harness_version(platform.name) == "1.2.3"


def test_no_platform_claims_a_source_it_cannot_justify():
    """Spec check: a declared source must be a real version of that harness.

    CLAUDE_AGENT_SDK_VERSION is deliberately NOT registered for Claude. It is
    present in the environment and looks usable, which is exactly why the
    temptation needs a written refusal: it reports the Agent SDK's version, and
    gating a workaround described in terms of Claude Code CLI builds on it
    would compare two different numbering schemes that merely appear related.

    REQUIREMENT: register a variable only when it is documented to carry that
    harness's own version. Leaving it empty costs nothing -- unknown already
    resolves to today's behavior.
    """
    for platform in hook_platforms():
        for name in platform.version_env_vars:
            assert "SDK" not in name.upper(), (
                f"{platform.name} registers {name}, which reports an SDK "
                "version rather than the harness's own"
            )


def test_an_unknown_harness_name_does_not_raise(monkeypatch):
    monkeypatch.delenv(HARNESS_VERSION_ENV_VAR, raising=False)
    assert harness_version("not-a-harness") is None


def test_the_override_reaches_a_version_ranged_workaround(monkeypatch):
    """End to end: the override is what makes ranges usable today."""
    from autorun.config import workaround_applies

    flag = "AUTORUN_BUG_CLAUDE_CODE_TASK_TOOLS_GATED_OFF_BUG_80305_WORKAROUND_ENABLED"
    monkeypatch.setenv(flag, ">=2.1.233")
    monkeypatch.setenv(HARNESS_VERSION_ENV_VAR, "2.1.100")

    affected = platform_for("claude").gates_mutable_task_tools
    assert workaround_applies(
        flag, affected=affected, version=harness_version("claude")
    ) is False

    monkeypatch.setenv(HARNESS_VERSION_ENV_VAR, "2.1.240")
    assert workaround_applies(
        flag, affected=affected, version=harness_version("claude")
    ) is True
