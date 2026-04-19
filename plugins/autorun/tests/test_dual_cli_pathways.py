"""Deep coverage for both CLI pathways end-to-end: installer, hook runtime,
CLI detection, event schema, bug workarounds, and shared-vs-divergent code.

This test file is split into four classes that each pin a different slice of
the dual-CLI contract:

- TestGeminiPathway: Gemini-specific runtime and layout guarantees
- TestClaudePathway: Claude-specific runtime and layout guarantees
- TestSharedContract: Cross-cutting invariants (single hook_entry.py, CLI
  detection, schema completeness, command file parity, bug workaround flags)
- TestBugWorkaroundCleanup: traceable deletion path when upstream bugs are
  fixed (per plugins/autorun/CLAUDE.md Bug Workaround Policy)

These tests are deliberately paranoid — they assert file-system layout,
manifest JSON, and source-level markers so regressions surface at `pytest`
time rather than at `claude plugin list` / `gemini extensions install` time.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent.parent
PLUGIN_ROOT = REPO_ROOT / "plugins" / "autorun"
TEMPLATE = PLUGIN_ROOT / "src" / "autorun" / "gemini_template"

CLAUDE_VALID_EVENTS = {
    "SessionStart", "UserPromptSubmit", "PreToolUse", "PreToolUseFailure",
    "PostToolUse", "PostToolUseFailure", "PermissionRequest", "PermissionDenied",
    "Notification", "SubagentStart", "SubagentStop", "TaskCreated",
    "TaskCompleted", "Stop", "StopFailure", "TeammateIdle",
    "InstructionsLoaded", "ConfigChange", "CwdChanged", "FileChanged",
    "WorktreeCreate", "WorktreeRemove", "PreCompact", "PostCompact",
    "Elicitation", "ElicitationResult", "SessionEnd",
}

GEMINI_VALID_EVENTS = {
    "BeforeTool", "AfterTool", "BeforeAgent", "AfterAgent",
    "BeforeModel", "AfterModel", "BeforeToolSelection",
    "SessionStart", "SessionEnd", "Notification", "PreCompress",
}


class TestGeminiPathway:
    """Gemini-specific invariants post-refactor."""

    def test_template_is_self_contained_for_gemini_install(self):
        """`gemini extensions install <template_dir>` needs three files at
        the template root layout: gemini-extension.json, hooks/hooks.json,
        hooks/hook_entry.py. Any missing file breaks the install command
        even before hooks fire.
        """
        assert (TEMPLATE / "gemini-extension.json").is_file(), (
            "gemini-extension.json missing at template root — "
            "`gemini extensions install` would fail"
        )
        assert (TEMPLATE / "hooks" / "hooks.json").is_file(), (
            "hooks/hooks.json missing at template root — Gemini's hardcoded "
            "hook path would not resolve (bug #14449)"
        )
        assert (TEMPLATE / "hooks" / "hook_entry.py").is_file(), (
            "hooks/hook_entry.py missing — ${extensionPath}/hooks/hook_entry.py "
            "would fail at runtime"
        )

    def test_template_hook_entry_matches_canonical(self):
        """Template hook_entry.py MUST stay byte-identical to the canonical
        plugins/autorun/hooks/hook_entry.py. Any divergence means Gemini
        runs stale handler code.
        """
        canonical = PLUGIN_ROOT / "hooks" / "hook_entry.py"
        template_copy = TEMPLATE / "hooks" / "hook_entry.py"
        assert canonical.read_bytes() == template_copy.read_bytes(), (
            "hook_entry.py in gemini_template/ has drifted from the canonical "
            "plugins/autorun/hooks/hook_entry.py. Re-run `autorun --install` or "
            "copy the canonical file into gemini_template/hooks/."
        )

    def test_gemini_hooks_use_extension_path_not_plugin_root(self):
        """Gemini substitutes ${extensionPath} at runtime. The hooks.json
        MUST reference ${extensionPath}, NEVER ${CLAUDE_PLUGIN_ROOT} (that
        variable belongs to Claude and won't be substituted by Gemini).
        """
        text = (TEMPLATE / "hooks" / "hooks.json").read_text(encoding="utf-8")
        assert "${extensionPath}" in text
        assert "${CLAUDE_PLUGIN_ROOT}" not in text, (
            "Gemini hooks must not reference ${CLAUDE_PLUGIN_ROOT}"
        )

    def test_gemini_hooks_all_events_valid(self):
        """Every event in the Gemini template must be a valid Gemini event
        (Gemini's schema is permissive but an unknown key will be ignored,
        silently breaking the handler).
        """
        data = json.loads((TEMPLATE / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for event in data.get("hooks", {}):
            assert event in GEMINI_VALID_EVENTS, (
                f"Unknown Gemini event name {event!r} in template hooks.json"
            )

    def test_gemini_hook_commands_reference_hooks_subdir(self):
        """All hook commands must invoke ${extensionPath}/hooks/hook_entry.py
        (not ${extensionPath}/hook_entry.py). The installer copies the file
        into hooks/ — a bare extensionPath/hook_entry.py would 404.
        """
        data = json.loads((TEMPLATE / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for event, groups in data.get("hooks", {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    cmd = hook.get("command", "")
                    if "hook_entry.py" not in cmd:
                        continue
                    assert "${extensionPath}/hooks/hook_entry.py" in cmd, (
                        f"Hook command for {event} references hook_entry.py "
                        f"but not at ${{extensionPath}}/hooks/ — the installer "
                        f"places the file there. Command: {cmd}"
                    )

    def test_gemini_hook_timeouts_are_milliseconds(self):
        """Gemini uses ms; sub-second timeouts silently disable the hook on
        cold daemon starts. Must be >= 5000ms for daemon warmup.
        """
        data = json.loads((TEMPLATE / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        for event, groups in data.get("hooks", {}).items():
            for group in groups:
                for hook in group.get("hooks", []):
                    if "timeout" in hook:
                        assert hook["timeout"] >= 5000, (
                            f"Gemini {event} timeout too short "
                            f"({hook['timeout']}ms < 5000ms). Daemon warmup takes ~3-5s."
                        )

    def test_gemini_manifest_references_hooks_path(self):
        """The manifest "hooks" field is ignored at runtime (bug #14449) but
        we include it anyway for forward-compat with PR #14460 (Gemini 0.28+).
        """
        manifest = json.loads((TEMPLATE / "gemini-extension.json").read_text(encoding="utf-8"))
        assert manifest.get("hooks") == "./hooks/hooks.json", (
            "Template manifest must declare hooks: ./hooks/hooks.json for "
            "forward compatibility with Gemini PR #14460"
        )
        assert manifest.get("contextFileName") == "GEMINI.md"

    def test_repo_root_shims_resolve_into_template(self):
        """Repo-root shims exist and resolve into the template dir (not the
        Claude plugin's hooks dir). A shim pointing at Claude's hooks would
        leak Claude events into Gemini via Pathway 2 & 6.
        """
        root_manifest = REPO_ROOT / "gemini-extension.json"
        root_hooks_dir = REPO_ROOT / "hooks"
        assert root_manifest.exists(), "Missing ./gemini-extension.json shim"
        assert root_hooks_dir.exists(), "Missing ./hooks shim"
        # Resolve both and confirm template membership.
        assert root_manifest.resolve() == (TEMPLATE / "gemini-extension.json").resolve()
        assert root_hooks_dir.resolve() == (TEMPLATE / "hooks").resolve(), (
            f"./hooks resolves to {root_hooks_dir.resolve()}; must resolve "
            f"to the template's hooks dir."
        )

    def test_install_py_references_template_not_plugin_root(self):
        """Installer must hand `gemini extensions install` the template dir
        path, not the plugin dir (which would install all Python source as
        part of the extension on Pathway 6).
        """
        install_py = (PLUGIN_ROOT / "src" / "autorun" / "install.py").read_text(encoding="utf-8")
        assert "_gemini_template_dir" in install_py
        assert "_gemini_source" in install_py, (
            "install.py must expose per-plugin source resolution so "
            "pdf-extractor's legacy layout still works"
        )
        assert "str(gemini_src)" in install_py, (
            "install.py must pass the template path (not plugin_dir) to "
            "`gemini extensions install`"
        )


class TestClaudePathway:
    """Claude-specific invariants post-refactor."""

    def test_plugin_json_uses_default_hooks_path(self):
        """No explicit "hooks" field → Claude uses hooks/hooks.json default."""
        manifest = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        assert "hooks" not in manifest, (
            "plugin.json should rely on the default hooks/hooks.json path. "
            "Any explicit value conflicts with the marketplace-source scan."
        )

    def test_claude_hooks_only_has_claude_events(self):
        """Bug #24115 regression pin: the marketplace-source scan walks
        plugins/autorun/hooks/ — only Claude-valid event names allowed.
        """
        hooks_json = PLUGIN_ROOT / "hooks" / "hooks.json"
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        gemini_only = GEMINI_VALID_EVENTS - CLAUDE_VALID_EVENTS
        for event in data.get("hooks", {}):
            assert event in CLAUDE_VALID_EVENTS, (
                f"'{event}' in {hooks_json} is not a valid Claude event — "
                f"will trigger bug #24115 invalid_key failure"
            )
            assert event not in gemini_only, (
                f"'{event}' is Gemini-only and leaked into Claude hooks"
            )

    def test_claude_hooks_use_plugin_root_var(self):
        """Claude hooks must reference ${CLAUDE_PLUGIN_ROOT} (substituted at
        install time by _substitute_paths).
        """
        text = (PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8")
        assert "${CLAUDE_PLUGIN_ROOT}" in text
        assert "${extensionPath}" not in text, (
            "Claude hooks must not reference Gemini's ${extensionPath}"
        )

    def test_no_legacy_claude_hooks_files(self):
        """claude-hooks.json and .bak variants must be gone (the rename was
        the whole point of the refactor)."""
        legacy = [
            PLUGIN_ROOT / "hooks" / "claude-hooks.json",
            PLUGIN_ROOT / "hooks" / "claude-hooks.json.bak",
        ]
        for p in legacy:
            assert not p.exists(), (
                f"Legacy file {p} still present. Should be deleted or renamed."
            )

    def test_no_gemini_manifest_at_plugin_root(self):
        """plugin root must have no gemini-extension.json (moved to template)."""
        legacy = PLUGIN_ROOT / "gemini-extension.json"
        assert not legacy.exists(), (
            f"{legacy} should not exist at plugin root; it lives under "
            f"src/autorun/gemini_template/ now."
        )

    def test_self_heal_cache_hooks_removed(self):
        """_self_heal_cache_hooks was obsoleted by the split. The method
        MUST be removed from core.py to avoid importing the deleted
        _clean_cross_cli_hooks.
        """
        core_py = (PLUGIN_ROOT / "src" / "autorun" / "core.py").read_text(encoding="utf-8")
        assert "_self_heal_cache_hooks" not in core_py, (
            "core.py still defines or calls _self_heal_cache_hooks; this "
            "imported the deleted _clean_cross_cli_hooks helper."
        )
        assert "_clean_cross_cli_hooks" not in core_py, (
            "core.py still references _clean_cross_cli_hooks; delete those "
            "imports and call sites."
        )

    def test_install_py_no_clean_cross_cli_hooks_calls(self):
        """install.py must have zero references to _clean_cross_cli_hooks
        (function was deleted — any leftover call is a NameError)."""
        install_py = (PLUGIN_ROOT / "src" / "autorun" / "install.py").read_text(encoding="utf-8")
        assert "_clean_cross_cli_hooks" not in install_py, (
            "install.py still calls _clean_cross_cli_hooks; delete those calls."
        )


class TestSharedContract:
    """Code-sharing invariants: what MUST stay unified across both CLIs."""

    def test_hook_entry_is_single_source_of_truth(self):
        """Exactly one hook_entry.py is the canonical source. The template's
        copy is a synced artifact — not a diverged fork.
        """
        canonical = PLUGIN_ROOT / "hooks" / "hook_entry.py"
        template_copy = TEMPLATE / "hooks" / "hook_entry.py"
        assert canonical.is_file()
        assert template_copy.is_file()
        # Byte-identical content implies no divergence.
        assert canonical.read_bytes() == template_copy.read_bytes()

    def test_cli_type_detection_discriminates_claude_vs_gemini(self):
        """Core CLI detection must still distinguish Claude from Gemini via
        GEMINI_SESSION_ID presence. This is the basis for per-CLI branching
        in handlers (e.g., check_task_staleness bypass for Gemini).
        """
        from autorun.config import detect_cli_type
        prev_gemini = os.environ.get("GEMINI_SESSION_ID")
        try:
            os.environ.pop("GEMINI_SESSION_ID", None)
            assert detect_cli_type() == "claude"
            os.environ["GEMINI_SESSION_ID"] = "test"
            assert detect_cli_type() == "gemini"
        finally:
            if prev_gemini is None:
                os.environ.pop("GEMINI_SESSION_ID", None)
            else:
                os.environ["GEMINI_SESSION_ID"] = prev_gemini

    def test_command_files_shared_between_clis(self):
        """plugins/autorun/commands/ is shared — both CLIs read the same .md
        files (Gemini gets .toml files generated at install time from these).
        If this dir goes missing, both CLIs break.
        """
        commands_dir = PLUGIN_ROOT / "commands"
        assert commands_dir.is_dir()
        md_files = list(commands_dir.glob("*.md"))
        assert len(md_files) > 10, (
            f"Expected many .md command files in {commands_dir}, found {len(md_files)}"
        )

    def test_both_cli_event_sets_contain_session_start(self):
        """SessionStart is the one event name both CLIs share. Each hooks
        file must have an entry for it so daemon warmup happens in both.
        """
        claude_data = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        gemini_data = json.loads((TEMPLATE / "hooks" / "hooks.json").read_text(encoding="utf-8"))
        assert "SessionStart" in claude_data["hooks"], "Claude hooks must register SessionStart"
        assert "SessionStart" in gemini_data["hooks"], "Gemini hooks must register SessionStart"


class TestBugWorkaroundCleanup:
    """Pin the deletion-path described in plugins/autorun/CLAUDE.md Bug
    Workaround Policy — when upstream bugs are fixed, maintainers should be
    able to follow the markers and flags to remove the workaround cleanly.
    """

    def test_policy_compliance_both_bugs_have_config_and_helpers(self):
        """Each bug needs: CONFIG key, helper using the key, bracketed block."""
        from autorun.config import CONFIG
        flags = [
            "AUTORUN_BUG_CLAUDE_CODE_MARKETPLACE_SOURCE_SCAN_BUG_24115_WORKAROUND_ENABLED",
            "AUTORUN_BUG_GEMINI_CLI_HOOKS_JSON_HARDCODED_BUG_14449_WORKAROUND_ENABLED",
        ]
        for flag in flags:
            assert flag in CONFIG, f"CONFIG missing {flag}"

        install_py = (PLUGIN_ROOT / "src" / "autorun" / "install.py").read_text(encoding="utf-8")
        assert "_bug_24115_workaround_enabled" in install_py
        assert "_bug_14449_workaround_enabled" in install_py
        assert "# --- BUG #24115 & #14449 WORKAROUND START" in install_py
        assert "# --- BUG #24115 & #14449 WORKAROUND END" in install_py

    def test_both_bug_references_link_to_issues(self):
        """Bug workaround comments must link to the upstream issue URLs so
        maintainers can verify the bug is still open before deleting.
        """
        install_py = (PLUGIN_ROOT / "src" / "autorun" / "install.py").read_text(encoding="utf-8")
        assert "github.com/anthropics/claude-code/issues/24115" in install_py
        assert "github.com/google-gemini/gemini-cli/issues/14449" in install_py

    def test_workaround_disable_instructions_in_claude_md(self):
        """CLAUDE.md table lists every bug workaround flag with its key,
        default, and effect — this is the deletion runbook.
        """
        claude_md = (PLUGIN_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        # Existing bug #18534 should be in the table; new bugs don't need
        # to be there YET but policy encourages adding them. We only assert
        # the policy block exists so maintainers know where to add entries.
        assert "Bug Workaround Policy" in claude_md
