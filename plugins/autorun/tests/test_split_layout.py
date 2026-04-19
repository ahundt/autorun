"""Tests for the programmatic split-layout introduced to fix claude plugin
bug #24115 and Gemini's hardcoded hooks/hooks.json path.

After the fix (no duplicate folder; installer materializes Gemini extension):
- plugins/autorun/hooks/hooks.json                 → Claude-only events (default path)
- plugins/autorun/src/autorun/gemini_template/hooks.json
                                                    → Gemini-only events (template; under src/
                                                      so Claude's hooks/ scanner never sees it)
- plugins/autorun/src/autorun/gemini_template/gemini-extension.json
                                                    → Gemini manifest template
- plugins/autorun/.claude-plugin/plugin.json has NO "hooks" field
- install.py materializes ~/.gemini/extensions/ar/ from the template at install time,
  including copying hook_entry.py

Source: plan at notes/2026_04_19_1627_fix_arautorun_failed_to_load_across_all_install_pathways.md
Reference bugs:
- anthropics/claude-code#24115 (marketplace-source scan)
- google-gemini/gemini-cli#14449 (hardcoded hooks path)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "autorun"
GEMINI_TEMPLATE_ROOT = PLUGIN_ROOT / "src" / "autorun" / "gemini_template"

CLAUDE_CODE_VALID_EVENTS = {
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PreToolUseFailure",
    "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied",
    "Notification", "SubagentStart", "SubagentStop", "TaskCreated",
    "TaskCompleted", "Stop", "StopFailure", "TeammateIdle",
    "InstructionsLoaded", "ConfigChange", "CwdChanged", "FileChanged",
    "WorktreeCreate", "WorktreeRemove", "PreCompact", "PostCompact",
    "Elicitation", "ElicitationResult", "SessionEnd",
}

GEMINI_CLI_VALID_EVENTS = {
    "BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
    "BeforeModel", "AfterModel", "BeforeToolSelection",
    "SessionStart", "SessionEnd", "Notification", "PreCompress",
}

GEMINI_ONLY = GEMINI_CLI_VALID_EVENTS - CLAUDE_CODE_VALID_EVENTS
CLAUDE_ONLY = CLAUDE_CODE_VALID_EVENTS - GEMINI_CLI_VALID_EVENTS


def test_claude_hooks_at_default_path_and_schema_valid():
    """plugins/autorun/hooks/hooks.json exists, is valid JSON, and uses only
    Claude Code event names.

    Fixes Claude Code bug #24115: the plugin loader scans the marketplace
    source directory in addition to the cache. If any Gemini event name is
    present, strict Zod rejects with `invalid_key` and `ar@autorun` fails
    to load.
    """
    hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
    assert hooks_json.exists(), (
        f"Expected Claude default hooks file at {hooks_json}. "
        "After refactor, claude-hooks.json is renamed to hooks.json."
    )

    data = json.loads(hooks_json.read_text(encoding="utf-8"))
    events = set(data.get("hooks", {}).keys())
    assert events, "hooks.json has no event entries"

    invalid = events - CLAUDE_CODE_VALID_EVENTS
    assert not invalid, (
        f"plugins/autorun/hooks/hooks.json contains non-Claude event names: {invalid}. "
        f"These trigger Zod invalid_key errors via Claude bug #24115."
    )

    gemini_contamination = events & GEMINI_ONLY
    assert not gemini_contamination, (
        f"plugins/autorun/hooks/hooks.json has Gemini-only events: {gemini_contamination}. "
        "Move these to plugins/autorun/src/autorun/gemini_template/hooks/hooks.json."
    )

    # Must use Claude's plugin root variable, not Gemini's.
    text = hooks_json.read_text(encoding="utf-8")
    assert "${CLAUDE_PLUGIN_ROOT}" in text, (
        "Claude hooks.json must reference ${CLAUDE_PLUGIN_ROOT}"
    )
    assert "${extensionPath}" not in text, (
        "Claude hooks.json must not use Gemini's ${extensionPath}"
    )


def test_claude_plugin_json_has_no_explicit_hooks_field():
    """plugin.json no longer specifies a "hooks" path.

    With the default hooks/hooks.json layout, an explicit field is redundant.
    Keeping the legacy "./hooks/claude-hooks.json" override would also be
    problematic because Claude Code still ALSO discovers hooks/hooks.json
    by default. Removing it keeps a single source of truth.
    """
    plugin_json = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    manifest = json.loads(plugin_json.read_text(encoding="utf-8"))
    assert "hooks" not in manifest, (
        f"plugin.json should not declare a 'hooks' field (uses default hooks/hooks.json). "
        f"Current value: {manifest.get('hooks')!r}"
    )


def test_gemini_hooks_template_under_src_not_in_claude_plugin_root():
    """The Gemini extension is assembled from a template under
    plugins/autorun/src/autorun/gemini_template/ so Claude's bug #24115
    marketplace-source scanner (which only walks hooks/) never sees it.
    The Claude plugin's hooks/ directory must not contain any Gemini event
    names anywhere.
    """
    gemini_hooks = GEMINI_TEMPLATE_ROOT / "hooks" / "hooks.json"
    assert gemini_hooks.exists(), (
        f"Expected Gemini hooks template at {gemini_hooks}. "
        "Template must live under src/autorun/gemini_template/ so Claude doesn't scan it."
    )
    data = json.loads(gemini_hooks.read_text(encoding="utf-8"))
    events = set(data.get("hooks", {}).keys())
    assert events, "gemini_template/hooks.json has no event entries"

    invalid = events - GEMINI_CLI_VALID_EVENTS
    assert not invalid, (
        f"gemini_template/hooks.json contains non-Gemini event names: {invalid}"
    )
    claude_contamination = events & CLAUDE_ONLY
    assert not claude_contamination, (
        f"Gemini hooks contain Claude-only events: {claude_contamination}"
    )

    # Claude plugin root must not have any residual Gemini hook files.
    claude_hooks_dir = PLUGIN_ROOT / "hooks"
    for p in claude_hooks_dir.glob("*.json"):
        text = p.read_text(encoding="utf-8")
        for ev in GEMINI_ONLY:
            assert f'"{ev}"' not in text, (
                f"Gemini-only event '{ev}' leaked into Claude hooks file {p.name}. "
                "All Gemini events must live under the gemini_template/."
            )


def test_gemini_extension_manifest_in_template_dir():
    """gemini-extension.json is now under src/autorun/gemini_template/ so
    Claude's scanner doesn't consider it a plugin artifact, and the installer
    materializes ~/.gemini/extensions/ar/gemini-extension.json from it.
    """
    assert (GEMINI_TEMPLATE_ROOT / "gemini-extension.json").exists(), (
        "Expected gemini-extension.json at src/autorun/gemini_template/"
    )
    legacy = PLUGIN_ROOT / "gemini-extension.json"
    assert not legacy.exists(), (
        f"Legacy gemini-extension.json still present at {legacy}. "
        "Move it to src/autorun/gemini_template/gemini-extension.json."
    )


def test_install_copies_hook_entry_to_gemini_extension(tmp_path):
    """After autorun --install runs the Gemini install path, the installed
    extension dir (~/.gemini/extensions/ar/) must contain hook_entry.py as a
    regular file. The Gemini hooks.json references ${extensionPath}/hook_entry.py
    so the file MUST be there for hooks to run.
    """
    from autorun import install as install_mod

    # Stand up a fake Gemini ext dir and a fake plugins/autorun/hooks/hook_entry.py.
    fake_gemini_ext = tmp_path / "gemini_ext"
    (fake_gemini_ext / "hooks").mkdir(parents=True)
    # Gemini install typically lays out hooks/hooks.json inside the ext dir.
    (fake_gemini_ext / "hooks" / "hooks.json").write_text("{}", encoding="utf-8")

    fake_plugin_dir = tmp_path / "plugin_src"
    (fake_plugin_dir / "hooks").mkdir(parents=True)
    source_hook = fake_plugin_dir / "hooks" / "hook_entry.py"
    source_hook.write_text("# marker hook_entry\n", encoding="utf-8")

    # Expected helper API. Implementation task: add a module-level helper
    # _copy_hook_entry_to_gemini_ext(plugin_dir, ext_dir) and call it at the
    # end of each Gemini install step. Keep the helper discoverable so tests
    # and diagnostics can call it directly.
    assert hasattr(install_mod, "_copy_hook_entry_to_gemini_ext"), (
        "install.py must expose _copy_hook_entry_to_gemini_ext(plugin_dir, ext_dir). "
        "See plan: the function copies plugin_dir/hooks/hook_entry.py into "
        "ext_dir/hook_entry.py so ${extensionPath}/hook_entry.py resolves at runtime."
    )

    install_mod._copy_hook_entry_to_gemini_ext(fake_plugin_dir, fake_gemini_ext)
    copied = fake_gemini_ext / "hooks" / "hook_entry.py"
    assert copied.exists() and not copied.is_symlink(), (
        f"hook_entry.py not copied to {copied} (or is a symlink). "
        "Must be a regular file so ${extensionPath}/hooks/hook_entry.py resolves."
    )
    assert copied.read_text(encoding="utf-8") == "# marker hook_entry\n"


def test_migrate_legacy_layout_fail_fast_when_old_manifest_present(tmp_path):
    """install.py must detect a pre-fix working tree and fail fast.

    If a user pulls the refactor but has lingering
    plugins/autorun/gemini-extension.json (e.g., partial git checkout, stash
    conflict), the installer should abort with an actionable message rather
    than silently producing a broken dual-install.
    """
    from autorun import install as install_mod

    assert hasattr(install_mod, "_migrate_legacy_layout"), (
        "install.py must expose _migrate_legacy_layout(plugin_dir). "
        "Called at start of install flows to detect stale layout."
    )

    # Stage a MIGRATED-but-inconsistent layout: has both a template dir
    # (indicating the plugin already migrated) AND a stale legacy manifest.
    fake_plugin = tmp_path / "autorun"
    template = fake_plugin / "src" / "autorun" / "gemini_template"
    template.mkdir(parents=True)
    (template / "gemini-extension.json").write_text("{}", encoding="utf-8")
    (fake_plugin / "gemini-extension.json").write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit) as exc_info:
        install_mod._migrate_legacy_layout(fake_plugin)
    msg = str(exc_info.value)
    assert "gemini_template" in msg or exc_info.value.code not in (None, 0), (
        "_migrate_legacy_layout must exit non-zero and mention gemini_template "
        "in its message."
    )

    # Fresh migrated layout (no legacy manifest) must pass.
    clean_plugin = tmp_path / "clean"
    clean_template = clean_plugin / "src" / "autorun" / "gemini_template"
    clean_template.mkdir(parents=True)
    (clean_template / "gemini-extension.json").write_text("{}", encoding="utf-8")
    install_mod._migrate_legacy_layout(clean_plugin)  # must not raise

    # Legacy-only layout (no template dir, has legacy manifest) must also pass
    # — some plugins legitimately use legacy layout (e.g. pdf-extractor).
    legacy_plugin = tmp_path / "legacy"
    legacy_plugin.mkdir()
    (legacy_plugin / "gemini-extension.json").write_text("{}", encoding="utf-8")
    install_mod._migrate_legacy_layout(legacy_plugin)  # must not raise


def test_root_level_gemini_shim_exists():
    """Repo root must have a gemini-extension.json shim so `gemini extensions
    install <github-url>` (Pathway 2) and `gemini extensions install .` from
    repo root (Pathway 6) both succeed without needing a subdirectory path.

    Gemini CLI does NOT support workspace manifests with an `extensions` array
    (verified via geminicli.com/docs/extensions/reference/). The only way to
    make Pathway 2 work is to put a real manifest at the repo root. We use
    committed symlinks into the template so content is not duplicated.

    Both JSON files must be valid and equivalent to the template content.
    """
    root_manifest = REPO_ROOT / "gemini-extension.json"
    template_manifest = GEMINI_TEMPLATE_ROOT / "gemini-extension.json"

    assert root_manifest.exists(), (
        f"Missing root-level Gemini shim at {root_manifest}. "
        "Required for Pathway 2 (github URL install) and Pathway 6 (local .) install."
    )
    # Content equivalence via symlink or identical bytes.
    root_data = json.loads(root_manifest.read_text(encoding="utf-8"))
    template_data = json.loads(template_manifest.read_text(encoding="utf-8"))
    assert root_data == template_data, (
        "Root-level gemini-extension.json must match template content "
        "(symlink or regenerated copy)."
    )


def test_root_level_hooks_shim_exists():
    """Repo root must have hooks/hooks.json at ./hooks/hooks.json so Gemini
    resolves ${extensionPath}/hooks/hooks.json when the repo root is the
    installed extension (Pathway 2 and 6).
    """
    root_hooks = REPO_ROOT / "hooks" / "hooks.json"
    template_hooks = GEMINI_TEMPLATE_ROOT / "hooks" / "hooks.json"

    assert root_hooks.exists(), (
        f"Missing root-level hooks shim at {root_hooks}. "
        "Required so `gemini extensions install <repo>` can resolve hooks/hooks.json "
        "at extension root (Gemini hardcodes this path)."
    )
    root_data = json.loads(root_hooks.read_text(encoding="utf-8"))
    template_data = json.loads(template_hooks.read_text(encoding="utf-8"))
    assert root_data == template_data, (
        "Root-level hooks/hooks.json must match template content"
    )


def test_root_hook_entry_reachable_from_shim():
    """Root-level hook_entry.py must exist so gemini's ${extensionPath}/hooks/hook_entry.py
    resolves when the repo root is installed as the Gemini extension.
    """
    root_hook_entry = REPO_ROOT / "hooks" / "hook_entry.py"
    plugin_hook_entry = PLUGIN_ROOT / "hooks" / "hook_entry.py"

    assert root_hook_entry.exists(), (
        f"Missing root-level hook_entry.py shim at {root_hook_entry}"
    )
    # Must resolve to the canonical plugin hook_entry (either symlink target
    # or identical content — symlink preferred to avoid divergence).
    if root_hook_entry.is_symlink():
        resolved = root_hook_entry.resolve()
        assert resolved == plugin_hook_entry.resolve(), (
            f"Symlink target {resolved} does not match canonical {plugin_hook_entry}"
        )
    else:
        assert root_hook_entry.read_bytes() == plugin_hook_entry.read_bytes(), (
            "Root hook_entry.py content diverged from plugin's"
        )


# --- BUG #24115 & #14449 TESTS START --- DELETE WHEN BOTH BUGS ARE FIXED ---
# These tests pin the bug-workaround flags and helper behavior so regressions
# in either direction (workaround removed prematurely OR left on after fix)
# surface immediately. Shared flag constants match the CONFIG keys in
# plugins/autorun/src/autorun/config.py.

_BUG_24115_FLAG = "AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED"
_BUG_14449_FLAG = "AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED"


def test_bug_24115_workaround_config_key_exists():
    """Config entry must exist with default True and bug link in docstring."""
    from autorun.config import CONFIG
    assert _BUG_24115_FLAG in CONFIG, (
        f"CONFIG missing {_BUG_24115_FLAG}. See plugins/autorun/CLAUDE.md "
        "'Bug Workaround Policy' for required format."
    )
    assert CONFIG[_BUG_24115_FLAG] is True, (
        f"Default for {_BUG_24115_FLAG} must be True until #24115 is fixed."
    )


def test_bug_14449_workaround_config_key_exists():
    """Gemini hardcoded-hooks-path workaround must be wired."""
    from autorun.config import CONFIG
    assert _BUG_14449_FLAG in CONFIG, f"CONFIG missing {_BUG_14449_FLAG}"
    assert CONFIG[_BUG_14449_FLAG] is True


def test_bug_24115_env_var_overrides_config():
    """Env var value must win over CONFIG value (policy requirement)."""
    import os
    from autorun import install as install_mod

    # With workaround enabled (default), _migrate_legacy_layout should trip
    # on inconsistent layout.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        plugin = tmp_path / "p"
        template = plugin / "src" / "autorun" / "gemini_template"
        template.mkdir(parents=True)
        (plugin / "gemini-extension.json").write_text("{}", encoding="utf-8")

        # Explicitly disable via env var → must skip migration check
        prev_24115 = os.environ.get(_BUG_24115_FLAG)
        prev_14449 = os.environ.get(_BUG_14449_FLAG)
        try:
            os.environ[_BUG_24115_FLAG] = "false"
            os.environ[_BUG_14449_FLAG] = "false"
            # Must NOT raise — workaround disabled
            install_mod._migrate_legacy_layout(plugin)

            os.environ[_BUG_24115_FLAG] = "true"
            os.environ[_BUG_14449_FLAG] = "true"
            with pytest.raises(SystemExit):
                install_mod._migrate_legacy_layout(plugin)
        finally:
            if prev_24115 is None:
                os.environ.pop(_BUG_24115_FLAG, None)
            else:
                os.environ[_BUG_24115_FLAG] = prev_24115
            if prev_14449 is None:
                os.environ.pop(_BUG_14449_FLAG, None)
            else:
                os.environ[_BUG_14449_FLAG] = prev_14449


def test_bug_workaround_helpers_are_bracketed():
    """install.py must mark the workaround block for easy deletion."""
    install_py = (PLUGIN_ROOT / "src" / "autorun" / "install.py").read_text(encoding="utf-8")
    assert "# --- BUG #24115 & #14449 WORKAROUND START" in install_py, (
        "install.py missing BUG #24115 & #14449 WORKAROUND START marker"
    )
    assert "# --- BUG #24115 & #14449 WORKAROUND END" in install_py, (
        "install.py missing BUG #24115 & #14449 WORKAROUND END marker"
    )
    # Deletion instructions must be present
    assert "DELETE WHEN" in install_py, (
        "WORKAROUND header must tell maintainers how/when to delete it"
    )


# --- BUG #24115 & #14449 TESTS END ---


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
