"""Bug #4669 obeys the bug-workaround policy in plugins/autorun/CLAUDE.md.

#4669 is the oldest workaround in the codebase and predates the policy every
later one follows: it had no `AUTORUN_BUG_<NAME>_BUG_<NUMBER>_WORKAROUND_ENABLED`
key, so it could only be turned off through an environment variable, and no
bracketed removable block, so "what do I delete when Anthropic fixes this?" had
no answer in the source.

Claude Code ignores `permissionDecision:"deny"` at exit 0 — the tool runs
anyway — so stderr plus exit code 2 is the only thing that actually blocks.
https://github.com/anthropics/claude-code/issues/4669

Applicability comes from `Platform.has_exit2_workaround`, never from a
hardcoded harness name, so a harness that shares the defect declares it once.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autorun.platforms import PLATFORMS  # noqa: E402

# --- BUG #4669 TESTS START --- DELETE WHEN FIXED ---

_BUG_FLAG = (
    "AUTORUN_BUG_CLAUDE_CODE_DENY_IGNORED_AT_EXIT_ZERO_BUG_4669_WORKAROUND_ENABLED"
)
_LEGACY_FLAG = "AUTORUN_EXIT2_WORKAROUND"


@pytest.fixture
def clean_env(monkeypatch):
    """Neither spelling set, so each test states its own starting point."""
    monkeypatch.delenv(_BUG_FLAG, raising=False)
    monkeypatch.delenv(_LEGACY_FLAG, raising=False)
    return monkeypatch


def _should_use(payload=None) -> bool:
    from autorun.config import should_use_exit2_workaround

    return should_use_exit2_workaround(payload)


def test_flag_names_match_the_policy():
    from autorun.config import BUG_4669_FLAG, BUG_4669_LEGACY_FLAG

    assert BUG_4669_FLAG == _BUG_FLAG
    assert BUG_4669_LEGACY_FLAG == _LEGACY_FLAG


def test_config_declares_the_key_so_it_is_disablable_without_an_env_var():
    from autorun.config import CONFIG

    assert CONFIG[_BUG_FLAG] is True


def test_affected_platform_with_workaround_enabled(clean_env):
    assert _should_use({"cli_type": "claude"}) is True


def test_unaffected_platform_is_untouched(clean_env):
    assert _should_use({"cli_type": "gemini"}) is False


def test_env_never_disables_it_everywhere(clean_env):
    clean_env.setenv(_BUG_FLAG, "never")
    assert _should_use({"cli_type": "claude"}) is False


def test_env_always_enables_it_everywhere(clean_env):
    clean_env.setenv(_BUG_FLAG, "always")
    assert _should_use({"cli_type": "gemini"}) is True


def test_config_false_disables_it_without_any_env_var(clean_env, monkeypatch):
    """The gap this task closed: previously only an env var could turn it off."""
    from autorun.config import CONFIG

    monkeypatch.setitem(CONFIG, _BUG_FLAG, False)
    assert _should_use({"cli_type": "claude"}) is False


def test_legacy_spelling_still_wins(clean_env):
    """`--exit2-mode never` writes the legacy var and must keep working."""
    clean_env.setenv(_LEGACY_FLAG, "never")
    clean_env.setenv(_BUG_FLAG, "always")
    assert _should_use({"cli_type": "claude"}) is False


def test_legacy_auto_defers_to_platform_applicability(clean_env):
    clean_env.setenv(_LEGACY_FLAG, "auto")
    assert _should_use({"cli_type": "claude"}) is True
    assert _should_use({"cli_type": "gemini"}) is False


def test_a_typo_falls_through_rather_than_disabling_blocking(clean_env):
    """Fail open on unknown data: a misspelled value must not stop blocking."""
    clean_env.setenv(_LEGACY_FLAG, "nevr")
    assert _should_use({"cli_type": "claude"}) is True


def test_applicability_is_declared_on_the_platform_not_hardcoded():
    affected = {name for name, p in PLATFORMS.items() if p.has_exit2_workaround}
    assert affected == {"claude"}, (
        "If another harness gains this defect, declare has_exit2_workaround on "
        "it rather than adding a name check in config.py"
    )


def test_the_workaround_block_is_bracketed_for_removal():
    """The policy's removal contract: one delimited unit, with instructions."""
    src = (
        Path(__file__).resolve().parents[1] / "src" / "autorun" / "config.py"
    ).read_text(encoding="utf-8")
    assert "# --- BUG #4669 WORKAROUND START --- DELETE WHEN FIXED ---" in src
    assert "# --- BUG #4669 WORKAROUND END --- DELETE WHEN FIXED ---" in src
    start = src.index("BUG #4669 WORKAROUND START")
    end = src.index("BUG #4669 WORKAROUND END")
    block = src[start:end]
    assert "https://github.com/anthropics/claude-code/issues/4669" in block
    assert _BUG_FLAG in block
    assert "Removal:" in block


# --- BUG #4669 TESTS END --- DELETE WHEN FIXED ---
