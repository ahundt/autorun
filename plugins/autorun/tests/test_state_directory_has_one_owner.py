#!/usr/bin/env python3
"""Where session state lives is decided in exactly one place.

Four modules each re-derived it, two of them with character-identical code:

    session_manager._state_dir_key   state_dir or $AUTORUN_TEST_STATE_DIR or ~/.claude/sessions
    ai_monitor.STATE_DIR             $AUTORUN_TEST_STATE_DIR or ~/.claude/sessions
    main.STATE_DIR                   the same three lines again
    plan_export                      reads $AUTORUN_TEST_STATE_DIR directly

Two consequences. The production default was stated in four places, so
answering "should state live under ~/.autorun instead?" was a four-site change
with four chances to miss one. And the isolation the whole test suite depends
on rested on every one of those sites remembering to honor the same variable.

REQUIREMENT: resolve through `session_manager.state_directory`. The default is
deliberately unchanged here -- relocating it is a separate, daemon-quiesced
migration, because a live daemon holds this database open for every attached
session.
"""

from pathlib import Path

import pytest

from autorun import session_manager as sm


def test_the_resolver_prefers_an_explicit_value(tmp_path, monkeypatch):
    """Highest precedence: what the caller passed in."""
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "from-env"))
    assert sm.state_directory(tmp_path / "explicit") == (tmp_path / "explicit")


def test_the_resolver_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTORUN_TEST_STATE_DIR", str(tmp_path / "from-env"))
    assert sm.state_directory() == (tmp_path / "from-env")


def test_the_default_is_unchanged(monkeypatch):
    """Relocating this is a migration, not an edit.

    A live daemon holds the database open on behalf of every attached session,
    so changing the default under them is the class of change the repository
    guidance calls out by name. If this expectation is ever updated, the
    migration has to land with it.
    """
    monkeypatch.delenv("AUTORUN_TEST_STATE_DIR", raising=False)
    assert sm.state_directory() == Path.home() / ".claude" / "sessions"


@pytest.mark.parametrize("module_name", ["autorun.ai_monitor", "autorun.main"])
def test_every_module_agrees_with_the_resolver(module_name, monkeypatch):
    """The modules that keep their own STATE_DIR must not disagree with it."""
    import importlib

    module = importlib.import_module(module_name)
    monkeypatch.delenv("AUTORUN_TEST_STATE_DIR", raising=False)
    importlib.reload(module)
    assert Path(module.STATE_DIR) == sm.state_directory(), (
        f"{module_name} resolved a different state directory than "
        "session_manager.state_directory"
    )


def test_no_module_rederives_the_default_path():
    """Spec check: one owner, enforced against a fifth copy appearing.

    Matches the literal path segments rather than a variable name, because the
    duplicates spelled it three different ways -- ``os.path.expanduser`` with a
    string, ``Path.home() / ".claude" / "sessions"``, and a plain join.
    """
    source_root = Path(sm.__file__).resolve().parent
    offenders = []
    for path in sorted(source_root.rglob("*.py")):
        if path.name == "session_manager.py":
            continue  # the owner
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            joined = '".claude" / "sessions"' in stripped or '".claude", "sessions"' in stripped
            if joined or "~/.claude/sessions" in stripped:
                offenders.append(f"{path.name}:{number}: {stripped}")

    assert not offenders, (
        "the state directory default belongs to "
        "session_manager.state_directory; a second copy is how the production "
        "default and the test isolation drift apart:\n  " + "\n  ".join(offenders)
    )
