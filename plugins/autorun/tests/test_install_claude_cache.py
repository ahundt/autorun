"""Claude's plugin cache: the fallback that fills it, and the paths inside it.

Two failures these pin, both silent in the installer being replaced. A failed
`claude plugin install` left no files and every hook inert, with the install
reporting the CLI error and moving on. And an unexpanded `${CLAUDE_PLUGIN_ROOT}`
became a path that does not exist, so the hook never ran and nothing said why.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("AUTORUN_HOME", "/tmp/autorun-test-home")
os.environ.setdefault("AUTORUN_TEST_STATE_DIR", "/tmp/autorun-test-state")

from autorun.installer import claude, fs  # noqa: E402
from autorun.installer.fs import OWNED_MARKER_NAME, read_marker  # noqa: E402


class Harness:
    """A synthetic harness. The real config directory comes from the registry,
    and a literal here would be a second authority for that question."""

    name = "fake-cache-harness"
    config_dir = "~/.fake-cache-harness"
    config_dir_env_vars: tuple[str, ...] = ()


@pytest.fixture
def source(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": ["${CLAUDE_PLUGIN_ROOT}/hooks/entry.py"]}}),
        encoding="utf-8",
    )
    return plugin


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def install(source: Path, home: Path, version: str = "1.2.3") -> Path:
    written = claude.cache_fallback(
        source, Harness(), market="autorun", plugin="ar", version=version, home=home
    )
    assert written is not None
    return written


# ─── The fallback ────────────────────────────────────────────────────────────


def test_the_fallback_writes_the_cache_entry_the_cli_could_not(source, home):
    written = install(source, home)

    assert (written / "hooks" / "hooks.json").is_file()
    assert written.parts[-3:] == ("autorun", "ar", "1.2.3")


def test_the_fallback_leaves_no_ownership_marker(source, home):
    """The cache belongs to Claude. A marker there would offer state Claude
    still tracks to autorun's retirement sweep."""
    written = install(source, home)

    assert not (written / OWNED_MARKER_NAME).exists()
    assert read_marker(written) is None


def test_fallback_never_replaces_a_harness_managed_cache(source, home):
    written = install(source, home)
    runtime = written / ".venv" / "lib" / "site-packages" / "autorun"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("installed = True\n", encoding="utf-8")
    (written / "left-over.txt").write_text("from the previous copy\n", encoding="utf-8")

    assert claude.cache_fallback(
        source, Harness(), market="autorun", plugin="ar", version="1.2.3", home=home
    ) is None

    assert (written / "left-over.txt").read_text(encoding="utf-8") == "from the previous copy\n"
    assert (runtime / "__init__.py").read_text(encoding="utf-8") == "installed = True\n"


def test_native_cache_creation_wins_a_fallback_publication_race(
    source, home, monkeypatch
):
    target = claude.cache_dir(
        Harness(), market="autorun", plugin="ar", version="1.2.3", home=home
    )
    assert target is not None
    runtime = target / ".venv" / "lib" / "site-packages" / "autorun"
    real_copytree = fs.shutil.copytree

    def copytree(*args, **kwargs):
        result = real_copytree(*args, **kwargs)
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "__init__.py").write_text("native = True\n", encoding="utf-8")
        return result

    monkeypatch.setattr(fs.shutil, "copytree", copytree)

    assert claude.cache_fallback(
        source, Harness(), market="autorun", plugin="ar", version="1.2.3", home=home
    ) is None
    assert (runtime / "__init__.py").read_text(encoding="utf-8") == "native = True\n"


def test_versions_sit_beside_each_other_and_none_is_pruned(source, home):
    """Claude loads the newest and manages the rest, so this reports rather
    than deletes."""
    install(source, home, version="1.2.3")
    install(source, home, version="1.2.4")

    assert claude.installed_versions(
        Harness(), market="autorun", plugin="ar", home=home
    ) == ("1.2.3", "1.2.4")


def test_a_harness_with_no_config_directory_is_a_real_answer(source, home):
    class Bare:
        name = "bare"
        config_dir = ""
        config_dir_env_vars: tuple[str, ...] = ()

    assert claude.cache_dir(Bare(), market="m", plugin="p", version="1", home=home) is None
    assert claude.cache_fallback(
        source, Bare(), market="m", plugin="p", version="1", home=home
    ) is None
    assert claude.installed_versions(Bare(), market="m", plugin="p", home=home) == ()


# ─── The placeholder ─────────────────────────────────────────────────────────


def test_the_plugin_root_placeholder_is_expanded_in_the_cached_copy(source, home):
    written = install(source, home)

    changed = claude.substitute_root(written)

    assert changed == ("hooks/hooks.json",)
    assert str(written) in (written / "hooks" / "hooks.json").read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" not in (written / "hooks" / "hooks.json").read_text(
        encoding="utf-8"
    )


def test_prose_mentioning_the_placeholder_is_left_alone(source, home):
    """Command documentation names the variable on purpose. Rewriting it would
    replace the documentation with one machine's path."""
    (source / "README.md").write_text("set ${CLAUDE_PLUGIN_ROOT} yourself\n", encoding="utf-8")
    written = install(source, home)

    claude.substitute_root(written)

    assert "${CLAUDE_PLUGIN_ROOT}" in (written / "README.md").read_text(encoding="utf-8")


def test_substitution_is_idempotent(source, home):
    """A second pass must not churn an mtime a harness watches."""
    written = install(source, home)
    claude.substitute_root(written)

    assert claude.substitute_root(written) == ()


def test_substitution_publishes_each_manifest_atomically(source, home, monkeypatch):
    written = install(source, home)
    calls = []
    real_write = claude.atomic_write

    def write(path, text):
        calls.append(path)
        real_write(path, text)

    monkeypatch.setattr(claude, "atomic_write", write)

    claude.substitute_root(written)

    assert calls == [written / "hooks" / "hooks.json"]


def test_the_bare_spelling_is_expanded_too(source, home):
    """A manifest written by hand uses whichever spelling the author
    remembered, and an unexpanded one becomes a path that does not exist."""
    (source / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": ["$CLAUDE_PLUGIN_ROOT/hooks/entry.py"]}}),
        encoding="utf-8",
    )
    written = install(source, home)

    claude.substitute_root(written)

    assert "$CLAUDE_PLUGIN_ROOT" not in (written / "hooks" / "hooks.json").read_text(
        encoding="utf-8"
    )
