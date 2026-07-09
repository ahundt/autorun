"""Tests for Codex install pathway (v0.11.0 / C4 + post-0.11.0 hot-fix).

Codex's user-level hooks live at ~/.codex/hooks.json (always active,
no marketplace trust required). The autorun installer:
  - creates/merges ~/.codex/hooks.json with autorun's hook commands
  - uses ABSOLUTE paths resolved at install time. ${PLUGIN_ROOT} is
    set ONLY for plugin-bundled Codex hooks; user-level hooks receive
    no autorun-relevant env vars per
    https://developers.openai.com/codex/hooks
  - uses the canonical {hooks: [{type, command, timeout}]} wrapper for
    EVERY event (PreToolUse, PostToolUse, UserPromptSubmit,
    SessionStart, Stop, SubagentStop) — bare {type, command} dicts are
    silently dropped by Codex's schema
  - prints a user-facing trust-prompt message (Codex requires /hooks
    approval for new hook hashes per HookStateToml verification)
"""
from __future__ import annotations

import json
from pathlib import Path

from autorun.install import (
    CmdResult,
    _install_for_antigravity,
    _install_for_qwen,
    _install_codex_plugin_with_cli,
    _install_for_codex,
    detect_available_clis,
    determine_target_clis,
    install_plugins,
    show_status,
)


def _read_codex_hooks(home: Path) -> dict:
    p = home / ".codex" / "hooks.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ─── detect_available_clis includes all PLATFORMS ────────────────────────────

def test_detect_available_clis_includes_codex_qwen_and_forgecode(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}")
    avail = detect_available_clis()
    assert "claude" in avail
    assert "gemini" in avail
    assert "antigravity" in avail
    assert "qwen" in avail
    assert "codex" in avail
    assert "forgecode" in avail


def test_determine_target_clis_default_returns_all_available():
    available = {
        "claude": True,
        "gemini": True,
        "antigravity": True,
        "qwen": True,
        "codex": True,
        "forgecode": False,
    }
    targets = determine_target_clis(False, False, available)
    assert "codex" in targets
    assert "antigravity" in targets
    assert "qwen" in targets
    assert "forgecode" not in targets


def test_determine_target_clis_codex_only():
    available = {"claude": True, "gemini": True, "antigravity": True, "qwen": True, "codex": True, "forgecode": True}
    assert determine_target_clis(False, False, available, codex_only=True) == ["codex"]


def test_determine_target_clis_antigravity_only():
    available = {"claude": True, "gemini": True, "antigravity": True, "qwen": True, "codex": True, "forgecode": True}
    assert determine_target_clis(False, False, available, antigravity_only=True) == ["antigravity"]


def test_determine_target_clis_qwen_only():
    available = {"claude": True, "gemini": True, "antigravity": True, "qwen": True, "codex": True, "forgecode": True}
    assert determine_target_clis(False, False, available, qwen_only=True) == ["qwen"]


def test_determine_target_clis_multiple_selected_platforms():
    available = {"claude": True, "gemini": True, "antigravity": True, "qwen": True, "codex": True, "forgecode": True}
    assert determine_target_clis(
        True,
        False,
        available,
        qwen_only=True,
        codex_only=True,
        antigravity_only=True,
    ) == ["claude", "antigravity", "qwen", "codex"]


def test_install_for_antigravity_imports_gemini_plugins(monkeypatch, tmp_path):
    """Antigravity install preserves Gemini ar hooks through agy importer."""
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/agy" if binary == "agy" else None)

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append(tuple(cmd))
        if cmd == ["agy", "plugin", "import", "gemini"]:
            return CmdResult(True, "[ok] ar\nhooks : 2 processed\n")
        if cmd == ["agy", "plugin", "list"]:
            return CmdResult(True, '{"imports":[{"name":"ar","components":["skills","commands","hooks"]}]}')
        return CmdResult(False, f"unexpected command: {cmd!r}")

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    ok, msg = _install_for_antigravity(tmp_path, ["autorun"], force=True)

    assert ok, msg
    assert ("agy", "plugin", "import", "gemini") in calls
    assert ("agy", "plugin", "list") in calls


def test_install_for_qwen_rewrites_gemini_template_hook_cli(monkeypatch, tmp_path):
    """Qwen install must materialize Gemini-family hooks with --cli qwen."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda binary: "/usr/bin/qwen" if binary == "qwen" else None)

    qwen_home = tmp_path / ".qwen"
    installed = qwen_home / "extensions" / "ar"
    installed.mkdir(parents=True)

    marketplace = tmp_path / "marketplace"
    plugin_dir = marketplace / "plugins" / "autorun"
    template = plugin_dir / "src" / "autorun" / "gemini_template"
    hooks_dir = template / "hooks"
    hooks_dir.mkdir(parents=True)
    (template / "gemini-extension.json").write_text('{"name": "ar"}', encoding="utf-8")
    (hooks_dir / "hooks.json").write_text(
        json.dumps({
            "hooks": {
                "BeforeTool": [
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "uv run python ${extensionPath}/hooks/hook_entry.py --cli gemini",
                            }
                        ],
                    }
                ]
            }
        }),
        encoding="utf-8",
    )
    shared_hooks = plugin_dir / "hooks"
    shared_hooks.mkdir(parents=True)
    (shared_hooks / "hook_entry.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    calls: list[tuple[str, ...]] = []

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append(tuple(cmd))
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    ok, msg = _install_for_qwen(marketplace, ["autorun"], force=True)

    assert ok, msg
    assert ("qwen", "extensions", "uninstall", "ar") in calls
    assert any(call[:3] == ("qwen", "extensions", "install") for call in calls)
    hooks = json.loads((installed / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert "--cli qwen" in json.dumps(hooks)
    assert "--cli gemini" not in json.dumps(hooks)


def test_install_plugins_runs_direct_platform_installers(monkeypatch, tmp_path):
    """Default install must register hook/app-specific direct installers."""
    calls: list[str] = []

    monkeypatch.setattr("autorun.install.find_marketplace_root", lambda: tmp_path)
    monkeypatch.setattr("autorun.install._update_package_metadata", lambda _plugin_root: None)
    monkeypatch.setattr("autorun.install._parse_selection", lambda _selection: ["ar"])
    monkeypatch.setattr(
        "autorun.install.detect_available_clis",
        lambda: {
            "claude": True,
            "gemini": True,
            "antigravity": True,
            "qwen": True,
            "codex": True,
            "forgecode": True,
        },
    )
    monkeypatch.setattr("autorun.install._check_uv_env", lambda _plugin_dir: CmdResult(True, "OK"))
    monkeypatch.setattr("autorun.install._sync_dependencies", lambda: CmdResult(True, "synced"))
    monkeypatch.setattr("autorun.install._install_pdf_deps", lambda: CmdResult(True, "skipping"))
    monkeypatch.setattr("autorun.install._check_hook_conflicts", lambda: None)
    monkeypatch.setattr("autorun.install._restart_daemon_if_running", lambda: None)
    monkeypatch.setattr("shutil.which", lambda binary: f"/usr/bin/{binary}")

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append(" ".join(cmd))
        return CmdResult(True, "")

    def mark(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            return (True, "success")

        return _inner

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)
    monkeypatch.setattr("autorun.install._install_for_gemini", mark("gemini"))
    monkeypatch.setattr("autorun.install._install_for_antigravity", mark("antigravity"))
    monkeypatch.setattr("autorun.install._install_for_qwen", mark("qwen"))
    monkeypatch.setattr("autorun.install._install_for_codex", mark("codex"))
    monkeypatch.setattr("autorun.install._install_for_forgecode", mark("forgecode"))
    monkeypatch.setattr(
        "autorun.install._install_codex_plugin_with_cli",
        lambda force=False, **_kwargs: CmdResult(True, "ok"),
    )

    assert install_plugins("all", force=True) == 0

    assert any(call.startswith("claude plugin marketplace add ") for call in calls)
    assert "gemini" in calls
    assert "antigravity" in calls
    assert "qwen" in calls
    assert "codex" in calls
    assert "forgecode" in calls


# ─── _install_for_codex installation ─────────────────────────────────────────

def test_install_for_codex_creates_user_hooks_json(tmp_path, monkeypatch):
    """First-time install must create ~/.codex/hooks.json with autorun's hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Re-import Path-using helpers after env change (Path.home() reads HOME)
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    ok, msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok, f"install failed: {msg}"

    hooks = _read_codex_hooks(tmp_path)
    assert "hooks" in hooks
    # Must contain at least PreToolUse, PostToolUse, Stop, SessionStart
    event_names = set(hooks["hooks"].keys())
    for ev in ("PreToolUse", "PostToolUse", "Stop", "SessionStart"):
        assert ev in event_names, f"~/.codex/hooks.json missing {ev}"
    # Hook command must reference --cli codex
    pretool_hooks = hooks["hooks"]["PreToolUse"]
    cmd_text = json.dumps(pretool_hooks)
    assert "--cli codex" in cmd_text or '"--cli", "codex"' in cmd_text
    assert "hook_entry.py" in cmd_text


def test_install_for_codex_preserves_user_hooks(tmp_path, monkeypatch):
    """Re-install must NOT clobber a user's existing custom hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    existing = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "user-pre.sh"}]}
            ],
            "UserCustomEvent": [{"type": "command", "command": "user-custom.sh"}],
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(existing, indent=2))

    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    merged = _read_codex_hooks(tmp_path)
    # User's custom event preserved
    assert "UserCustomEvent" in merged["hooks"]
    # User's PreToolUse hook preserved AND autorun's added
    pretool = merged["hooks"]["PreToolUse"]
    cmd_text = json.dumps(pretool)
    assert "user-pre.sh" in cmd_text
    assert "hook_entry.py" in cmd_text


def test_install_for_codex_idempotent(tmp_path, monkeypatch):
    """Running install twice must not duplicate autorun hook entries."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    snapshot = json.loads(((tmp_path / ".codex" / "hooks.json").read_text()))
    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    after = json.loads(((tmp_path / ".codex" / "hooks.json").read_text()))
    assert snapshot == after, "Re-install must be idempotent"


def test_install_for_codex_prints_trust_reminder(tmp_path, monkeypatch, capsys):
    """User must be told to run /hooks in Codex CLI to trust the new hashes."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    captured = capsys.readouterr().out
    assert "/hooks" in captured, (
        "install output must remind user that Codex needs /hooks approval "
        "for new hook hashes (Codex HookStateToml trust model)"
    )


def test_show_status_reports_codex_and_forgecode_install_artifacts(tmp_path, monkeypatch, capsys):
    """Status must expose deployment health for every supported platform."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("autorun.install.find_marketplace_root", lambda: tmp_path)
    monkeypatch.setattr("autorun.install._check_uv_env", lambda _p: CmdResult(True, "OK"))

    def fake_which(binary: str):
        return (
            f"/usr/bin/{binary}"
            if binary in {"claude", "gemini", "agy", "aix", "qwen", "codex", "autorun", "aise"}
            else None
        )

    def fake_run_cmd(cmd, *args, **kwargs):
        if cmd[:3] == ["claude", "plugin", "list"]:
            return CmdResult(True, "autorun enabled\npdf-extractor enabled\n")
        if cmd[:3] == ["gemini", "extensions", "list"]:
            return CmdResult(True, "ar\npdf-extractor\nconductor\n")
        if cmd[:3] == ["agy", "plugin", "list"]:
            return CmdResult(True, "ar enabled\n")
        if cmd[:3] == ["qwen", "extensions", "list"]:
            return CmdResult(True, "ar\npdf-extractor\n")
        assert cmd[0] != "aix", f"status should not query AIX: {cmd!r}"
        return CmdResult(True, "")

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    codex_hooks = {
        "hooks": {
            ev: [{"hooks": [{"type": "command", "command": "uv run python /x/hooks/hook_entry.py --cli codex"}]}]
            for ev in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "Stop", "SubagentStop")
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(codex_hooks))
    (codex_dir / "AGENTS.md").write_text("autorun safety guidance")
    (codex_dir / "config.toml").write_text(
        '[plugins."autorun@personal"]\n'
        'enabled = true\n'
    )
    skills_root = tmp_path / ".agents" / "skills"
    for name in ("mermaid-diagrams", "parallel-subagent"):
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: installed skill\n---\n",
            encoding="utf-8",
        )
        (skill_dir / ".autorun-owned").write_text("", encoding="utf-8")
    marketplace_dir = tmp_path / ".agents" / "plugins"
    marketplace_dir.mkdir(parents=True)
    (marketplace_dir / "marketplace.json").write_text(json.dumps({
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [
            {
                "name": "autorun",
                "source": {"source": "local", "path": "./plugins/autorun"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }))
    plugin_source_root = tmp_path / "plugins" / "autorun"
    plugin_source = plugin_source_root / ".codex-plugin"
    plugin_source.mkdir(parents=True)
    (plugin_source / "plugin.json").write_text('{"name":"autorun","skills":"./skills/"}')
    plugin_cache_root = tmp_path / ".codex" / "plugins" / "cache" / "personal" / "autorun" / "0.11.0"
    plugin_cache = plugin_cache_root / ".codex-plugin"
    plugin_cache.mkdir(parents=True)
    (plugin_cache / "plugin.json").write_text('{"name":"autorun","skills":"./skills/"}')
    for root in (plugin_source_root, plugin_cache_root):
        for name in ("mermaid-diagrams", "parallel-subagent"):
            skill_dir = root / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: bundled skill\n---\n",
                encoding="utf-8",
            )

    forge = tmp_path / ".forge"
    (forge / "commands").mkdir(parents=True)
    (forge / "commands" / "ar-st.md").write_text("---\ndescription: status\n---\n")
    (forge / "AGENTS.md").write_text("autorun advisory")

    assert show_status() == 0
    out = capsys.readouterr().out
    assert "Codex CLI:" in out
    assert "hooks.json: ✓ installed" in out
    assert "skills: ✓ 2 user, 2 plugin source, 2 plugin cache" in out
    assert "plugin marketplace: ✓ installed, enabled" in out
    assert "Google Antigravity:" in out
    assert "agy CLI: found" in out
    assert "ar plugin: ✓ imported" in out
    assert "Qwen Code:" in out
    assert "qwen CLI: found" in out
    assert "ar: ✓ installed" in out
    assert "AIX:" not in out
    assert "ForgeCode:" in out
    assert "hooks: advisory only" in out


# ─── Hot-fix regression tests: schema correctness + path resolution ──────────

def _iter_command_strings(hooks_json: dict):
    """Yield every command string under any event in hooks.json."""
    events = hooks_json.get("hooks", {})
    for _event, entries in events.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            inner = entry.get("hooks", []) if isinstance(entry, dict) else []
            for h in inner if isinstance(inner, list) else []:
                if isinstance(h, dict) and h.get("type") == "command":
                    yield h.get("command", "")


def _install_into_tmp(tmp_path, monkeypatch) -> Path:
    """Run _install_for_codex against a fake marketplace rooted at tmp_path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")
    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    return tmp_path


def test_codex_hooks_use_absolute_paths(tmp_path, monkeypatch):
    """Every command in ~/.codex/hooks.json must be an ABSOLUTE path.

    The earlier implementation emitted ${PLUGIN_ROOT} placeholders that
    Codex does not expand for user-level hooks, producing the runtime
    error 'Failed to spawn: /hooks/hook_entry.py — No such file or directory'.
    """
    home = _install_into_tmp(tmp_path, monkeypatch)
    hooks = _read_codex_hooks(home)
    assert hooks, "hooks.json missing"

    for cmd in _iter_command_strings(hooks):
        assert "${PLUGIN_ROOT}" not in cmd, (
            f"command still contains unexpanded ${{PLUGIN_ROOT}}: {cmd!r}\n"
            "Codex sets PLUGIN_ROOT only for plugin-bundled hooks; "
            "user-level hooks must use absolute paths resolved at install time."
        )
        assert "${CLAUDE_PLUGIN_ROOT}" not in cmd, (
            f"command still contains unexpanded ${{CLAUDE_PLUGIN_ROOT}}: {cmd!r}"
        )
        # hook_entry.py path must be absolute (starts with /)
        assert "/hooks/hook_entry.py" in cmd
        # The substring just before "/hooks/hook_entry.py" must be an
        # absolute path (starts with "/"), not empty.
        idx = cmd.index("/hooks/hook_entry.py")
        # Walk back from idx to the last whitespace to extract the path
        path_start = cmd.rfind(" ", 0, idx) + 1
        path = cmd[path_start:idx + len("/hooks/hook_entry.py")]
        assert path.startswith("/"), (
            f"hook_entry.py path is not absolute in command: {cmd!r}"
        )


def test_codex_hooks_use_canonical_wrapper_for_every_event(tmp_path, monkeypatch):
    """Every event entry must be a list of {hooks: [...]} dicts.

    Earlier implementation emitted bare {type, command} dicts for
    UserPromptSubmit/SessionStart/Stop/SubagentStop which Codex's
    strict schema silently dropped, producing 0/0 install counts in
    the /hooks TUI view.
    """
    home = _install_into_tmp(tmp_path, monkeypatch)
    hooks = _read_codex_hooks(home)
    events = hooks.get("hooks", {})
    assert events, "hooks.<events> map missing"

    for event_name, entries in events.items():
        assert isinstance(entries, list), (
            f"{event_name}: expected list of matcher-groups, got {type(entries).__name__}"
        )
        for entry in entries:
            assert isinstance(entry, dict), (
                f"{event_name}: each entry must be a dict, got {type(entry).__name__}"
            )
            assert "hooks" in entry, (
                f"{event_name}: each entry must have a 'hooks' list. "
                f"Bare {{type, command}} entries are silently dropped by Codex. "
                f"Got: {entry!r}"
            )
            assert isinstance(entry["hooks"], list)
            for h in entry["hooks"]:
                assert h.get("type") == "command"
                assert isinstance(h.get("command"), str) and h["command"]


def test_codex_hooks_no_sessionend_event(tmp_path, monkeypatch):
    """SessionEnd is NOT a valid Codex hook event — must not appear."""
    home = _install_into_tmp(tmp_path, monkeypatch)
    hooks = _read_codex_hooks(home)
    assert "SessionEnd" not in hooks.get("hooks", {}), (
        "SessionEnd is not in the Codex hook event list "
        "(https://developers.openai.com/codex/hooks) and must not be emitted."
    )


def test_codex_hooks_all_required_events_present(tmp_path, monkeypatch):
    """All six events autorun currently uses must be installed."""
    home = _install_into_tmp(tmp_path, monkeypatch)
    keys = set(_read_codex_hooks(home).get("hooks", {}).keys())
    required = {"PreToolUse", "PostToolUse", "UserPromptSubmit",
                "SessionStart", "Stop", "SubagentStop"}
    missing = required - keys
    assert not missing, f"Codex hooks.json missing events: {sorted(missing)}"


# ─── AGENTS.md install pathway (Codex reads ~/.codex/AGENTS.md globally) ─────
#
# Codex injects ~/.codex/AGENTS.md into every session per
# https://developers.openai.com/codex/guides/agents-md (32 KiB limit, merged
# with project-level AGENTS.md). Autorun ships advisory safety guidance there
# for the same reason it ships forgecode_template/AGENTS.md: hooks cover the
# enforcement path, but the model also benefits from knowing the override
# commands (/ar:sos, /ar:task-ignore) and the philosophy behind the guards.

def test_install_for_codex_writes_agents_md(tmp_path, monkeypatch):
    """Install must write ~/.codex/AGENTS.md with autorun safety guidance."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")
    template = fake_plugin / "src" / "autorun" / "codex_template"
    template.mkdir(parents=True)
    (template / "AGENTS.md").write_text(
        "# autorun safety guidance (Codex)\n\n"
        "Override commands: /ar:sos, /ar:task-ignore <id>.\n"
    )

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    agents = tmp_path / ".codex" / "AGENTS.md"
    assert agents.is_file(), "~/.codex/AGENTS.md must be created"
    text = agents.read_text()
    # Core override-action mentions must be present so the model can see them.
    assert "/ar:sos" in text
    assert "/ar:task-ignore" in text


def test_install_for_codex_preserves_existing_user_agents_md(tmp_path, monkeypatch):
    """A pre-existing user AGENTS.md must not be clobbered.

    Codex users may already have ~/.codex/AGENTS.md with their own
    instructions. Autorun must append/merge, never overwrite.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "AGENTS.md").write_text(
        "# my custom rules\nalways use snake_case in python\n"
    )

    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")
    template = fake_plugin / "src" / "autorun" / "codex_template"
    template.mkdir(parents=True)
    (template / "AGENTS.md").write_text(
        "# autorun safety guidance (Codex)\n\n"
        "Override commands: /ar:sos, /ar:task-ignore <id>.\n"
    )

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    merged = (codex_dir / "AGENTS.md").read_text()
    # User content preserved
    assert "always use snake_case in python" in merged
    # Autorun guidance also present
    assert "/ar:sos" in merged
    assert "/ar:task-ignore" in merged


# ─── Global skills install (~/.agents/skills) ────────────────────────────────
#
# Per https://developers.openai.com/codex/skills, Codex scans
# $HOME/.agents/skills/ for user-level skills (NOT ~/.codex/skills/, which
# is unused by Codex per the same docs). Autorun ships skill files in
# plugins/autorun/skills/<name>/SKILL.md; the install copies each into
# ~/.agents/skills/<name>/ with a .autorun-owned marker so re-installs
# replace ours without clobbering a user-authored skill that happens to
# share the same kebab-case name.

def _make_fake_plugin_with_skills(tmp_path, skill_names):
    """Build a fake plugin tree with the given skill directory names."""
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / ".codex-plugin").mkdir()
    (fake_plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps({
        "name": "autorun",
        "version": "0.11.0",
        "description": "test fixture autorun plugin",
        "skills": "./skills/",
    }))
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")
    template = fake_plugin / "src" / "autorun" / "codex_template"
    template.mkdir(parents=True)
    (template / "AGENTS.md").write_text("# autorun\n/ar:sos /ar:task-ignore\n")
    skills_root = fake_plugin / "skills"
    skills_root.mkdir()
    for name in skill_names:
        skill_dir = skills_root / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test fixture skill\n---\n# {name}\n"
        )
    return fake_marketplace


def test_install_for_codex_installs_skills_globally(tmp_path, monkeypatch):
    """Install copies autorun skills into ~/.agents/skills/<name>/."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(
        tmp_path, ["ai-session-tools", "mermaid-diagrams", "cache"]
    )

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    skills_root = tmp_path / ".agents" / "skills"
    assert skills_root.is_dir(), "~/.agents/skills/ must be created"
    for name in ("ai-session-tools", "mermaid-diagrams", "cache"):
        skill_md = skills_root / name / "SKILL.md"
        assert skill_md.is_file(), f"skill {name} missing"
        marker = skills_root / name / ".autorun-owned"
        assert marker.is_file(), f"autorun-owned marker missing for {name}"


def test_install_for_codex_skills_preserves_user_authored(tmp_path, monkeypatch):
    """A user-authored skill with the same name must NOT be clobbered.

    A user may already have ~/.agents/skills/cache/ with their own content.
    Autorun must detect the absence of the .autorun-owned marker and leave
    that skill alone.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    # Pre-existing user skill (no .autorun-owned marker)
    user_skill = tmp_path / ".agents" / "skills" / "cache"
    user_skill.mkdir(parents=True)
    user_skill_md = user_skill / "SKILL.md"
    user_skill_md.write_text("---\nname: cache\ndescription: USER OWNED\n---\n")

    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache", "mermaid-diagrams"])

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    # User's cache skill content is intact
    assert "USER OWNED" in user_skill_md.read_text()
    assert not (user_skill / ".autorun-owned").exists(), (
        "autorun must not claim ownership of a user-authored skill"
    )
    # Other autorun skills still installed normally
    other = tmp_path / ".agents" / "skills" / "mermaid-diagrams"
    assert (other / "SKILL.md").is_file()
    assert (other / ".autorun-owned").is_file()


def test_install_for_codex_skills_idempotent(tmp_path, monkeypatch):
    """Re-installing replaces autorun-owned skills with fresh copies; no growth."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["mermaid-diagrams", "cache"])

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    first_listing = sorted((tmp_path / ".agents" / "skills").rglob("*"))

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    second_listing = sorted((tmp_path / ".agents" / "skills").rglob("*"))

    assert first_listing == second_listing, (
        "Re-install must not create or delete files in ~/.agents/skills/"
    )


def test_install_for_codex_skills_replaces_stale_autorun_owned(tmp_path, monkeypatch):
    """If an old autorun-owned SKILL.md exists, the new install overwrites it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])

    # Pre-stage a stale autorun-owned copy
    skill_dir = tmp_path / ".agents" / "skills" / "cache"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("STALE CONTENT\n")
    (skill_dir / ".autorun-owned").write_text("")

    _install_for_codex(fake_marketplace, ["autorun"], force=False)

    text = (skill_dir / "SKILL.md").read_text()
    assert "STALE CONTENT" not in text
    assert "test fixture skill" in text


# ─── Codex plugin marketplace packaging ─────────────────────────────────────
#
# Codex can read direct user skills from ~/.agents/skills, but OpenAI's Codex
# plugin docs and the Codex CLI source both treat plugins as the installable
# distribution unit. Autorun should therefore publish its skill bundle as a
# home marketplace plugin in addition to copying global skills for immediate
# local availability.

def _read_personal_marketplace(home: Path) -> dict:
    return json.loads((home / ".agents" / "plugins" / "marketplace.json").read_text())


def _autorun_marketplace_entry(marketplace: dict) -> dict:
    entries = [p for p in marketplace.get("plugins", []) if p.get("name") == "autorun"]
    assert len(entries) == 1
    return entries[0]


def test_install_for_codex_creates_personal_plugin_marketplace(tmp_path, monkeypatch):
    """Default install exposes a skills plugin without plugin-bundled hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["autorun-maintainer", "cache"])
    fake_hooks = fake_marketplace / "plugins" / "autorun" / "hooks" / "hooks.json"
    fake_hooks.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py",
                        }
                    ]
                }
            ]
        }
    }))

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    marketplace = _read_personal_marketplace(tmp_path)
    assert marketplace["name"] == "personal"
    assert marketplace["interface"]["displayName"] == "Personal"
    entry = _autorun_marketplace_entry(marketplace)
    assert entry["source"] == {"source": "local", "path": "./plugins/autorun"}
    assert entry["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entry["category"] == "Productivity"

    plugin_source = tmp_path / "plugins" / "autorun"
    assert plugin_source.exists()
    assert (plugin_source / ".codex-plugin" / "plugin.json").is_file()
    assert (plugin_source / "skills" / "cache" / "SKILL.md").is_file()
    assert not (plugin_source / "hooks" / "hooks.json").exists(), (
        "Codex plugin packaging must not copy hooks/hooks.json. Codex loads "
        "plugin-bundled hooks alongside ~/.codex/hooks.json, so copying the "
        "Claude plugin hook file creates duplicate PreToolUse/PostToolUse "
        "policy hooks."
    )
    assert not (plugin_source / "hooks" / "hook_entry.py").exists(), (
        "The Codex plugin package is skills-only; hook_entry.py runs from "
        "~/.codex/hooks.json's absolute user-level command."
    )


def test_install_for_codex_plugin_hook_source_packages_codex_hooks_only(tmp_path, monkeypatch):
    """Plugin hook mode packages Codex hooks and removes user-level autorun hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run python /old/hooks/hook_entry.py --cli codex",
                        }
                    ]
                }
            ]
        }
    }))

    ok, _msg = _install_for_codex(
        fake_marketplace,
        ["autorun"],
        force=False,
        codex_hook_source="plugin",
    )
    assert ok

    user_hooks = _read_codex_hooks(tmp_path)
    assert "hook_entry.py --cli codex" not in json.dumps(user_hooks)
    plugin_source = tmp_path / "plugins" / "autorun"
    plugin_hooks = plugin_source / "hooks" / "hooks.json"
    assert plugin_hooks.is_file()
    assert (plugin_source / "hooks" / "hook_entry.py").is_file()
    plugin_text = plugin_hooks.read_text(encoding="utf-8")
    assert "--cli codex" in plugin_text
    assert "CLAUDE_PLUGIN_ROOT" in plugin_text
    assert "PreToolUse" in plugin_text
    assert "PostToolUse" in plugin_text


def test_install_for_codex_both_hook_source_installs_user_and_plugin_hooks(tmp_path, monkeypatch):
    """Both mode intentionally installs user-level and plugin-bundled hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])

    ok, _msg = _install_for_codex(
        fake_marketplace,
        ["autorun"],
        force=False,
        codex_hook_source="both",
    )
    assert ok

    user_hooks = _read_codex_hooks(tmp_path)
    assert "hook_entry.py --cli codex" in json.dumps(user_hooks)
    plugin_hooks = tmp_path / "plugins" / "autorun" / "hooks" / "hooks.json"
    assert plugin_hooks.is_file()
    assert "--cli codex" in plugin_hooks.read_text(encoding="utf-8")
    marker = tmp_path / "plugins" / "autorun" / ".autorun-owned"
    assert "codex_hook_source=both" in marker.read_text(encoding="utf-8")


def test_install_for_codex_user_mode_removes_owned_plugin_hooks_and_keeps_user_hooks(tmp_path, monkeypatch):
    """Switching plugin/both -> user removes only autorun-owned plugin hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text(json.dumps({
        "hooks": {
            "PostToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "user-post-tool-use.sh",
                        }
                    ]
                }
            ],
        }
    }))

    ok, _msg = _install_for_codex(
        fake_marketplace,
        ["autorun"],
        force=False,
        codex_hook_source="both",
    )
    assert ok
    assert (tmp_path / "plugins" / "autorun" / "hooks" / "hooks.json").is_file()

    ok, _msg = _install_for_codex(
        fake_marketplace,
        ["autorun"],
        force=False,
        codex_hook_source="user",
    )
    assert ok

    user_hooks = _read_codex_hooks(tmp_path)
    serialized = json.dumps(user_hooks)
    assert "hook_entry.py --cli codex" in serialized
    assert "user-post-tool-use.sh" in serialized
    plugin_source = tmp_path / "plugins" / "autorun"
    assert not (plugin_source / "hooks" / "hooks.json").exists()
    assert "codex_hook_source=user" in (plugin_source / ".autorun-owned").read_text(encoding="utf-8")


def test_install_for_codex_none_hook_source_removes_user_and_plugin_hooks(tmp_path, monkeypatch):
    """None mode leaves skills/plugin assets installed without Codex hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "hooks.json").write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run python /old/hooks/hook_entry.py --cli codex",
                        }
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "user-post-tool-use.sh",
                        }
                    ]
                }
            ],
        }
    }))

    ok, _msg = _install_for_codex(
        fake_marketplace,
        ["autorun"],
        force=False,
        codex_hook_source="none",
    )
    assert ok

    hooks = _read_codex_hooks(tmp_path)
    serialized = json.dumps(hooks)
    assert "hook_entry.py --cli codex" not in serialized
    assert "user-post-tool-use.sh" in serialized
    assert not (tmp_path / "plugins" / "autorun" / "hooks" / "hooks.json").exists()


def test_install_for_codex_rejects_github_plugin_hook_source(tmp_path, monkeypatch):
    """GitHub marketplace mode cannot package locally generated plugin hooks."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])

    ok, msg = _install_for_codex(
        fake_marketplace,
        ["autorun"],
        force=False,
        codex_hook_source="plugin",
        codex_plugin_marketplace="github",
    )

    assert not ok
    assert "cannot package runtime-generated plugin hooks" in msg


def test_install_for_codex_materializes_linked_skill_entrypoints(tmp_path, monkeypatch):
    """Codex plugin source must contain real SKILL.md files for link-backed skills.

    Codex's plugin cache copier copies regular files and directories, but not
    symbolic links. Autorun keeps a few skill entrypoints as meaningful
    Markdown filenames with SKILL.md symlinks for cross-harness compatibility,
    so the Codex plugin source copy must dereference those symlinks before
    Codex installs the plugin.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["parallel-subagent"])
    linked_skill = fake_marketplace / "plugins" / "autorun" / "skills" / "parallel-subagent"
    skill_text = linked_skill / "FixStubbornBugsFastByParallelizing.md"
    skill_text.write_text(
        "---\nname: parallel-subagent\ndescription: link-backed skill\n---\n",
        encoding="utf-8",
    )
    (linked_skill / "SKILL.md").unlink()
    (linked_skill / "SKILL.md").symlink_to(skill_text.name)

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    copied_entrypoint = tmp_path / "plugins" / "autorun" / "skills" / "parallel-subagent" / "SKILL.md"
    assert copied_entrypoint.is_file()
    assert not copied_entrypoint.is_symlink()
    assert "link-backed skill" in copied_entrypoint.read_text(encoding="utf-8")


def test_install_for_codex_preserves_existing_personal_marketplace_entries(tmp_path, monkeypatch):
    """Adding autorun must not remove unrelated home marketplace plugins."""
    monkeypatch.setenv("HOME", str(tmp_path))
    personal_dir = tmp_path / ".agents" / "plugins"
    personal_dir.mkdir(parents=True)
    (personal_dir / "marketplace.json").write_text(json.dumps({
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [
            {
                "name": "existing",
                "source": {"source": "local", "path": "./plugins/existing"},
                "category": "Productivity",
            }
        ],
    }, indent=2))
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    marketplace = _read_personal_marketplace(tmp_path)
    names = [entry["name"] for entry in marketplace["plugins"]]
    assert names == ["existing", "autorun"]
    assert _autorun_marketplace_entry(marketplace)["source"]["path"] == "./plugins/autorun"


def test_install_for_codex_does_not_clobber_user_owned_personal_plugin_dir(tmp_path, monkeypatch):
    """A user-authored ~/plugins/autorun directory must be left untouched."""
    monkeypatch.setenv("HOME", str(tmp_path))
    user_plugin = tmp_path / "plugins" / "autorun"
    (user_plugin / ".codex-plugin").mkdir(parents=True)
    user_manifest = user_plugin / ".codex-plugin" / "plugin.json"
    user_manifest.write_text('{"name":"autorun","description":"USER OWNED"}\n')
    fake_marketplace = _make_fake_plugin_with_skills(tmp_path, ["cache"])

    ok, _msg = _install_for_codex(fake_marketplace, ["autorun"], force=False)
    assert ok

    assert "USER OWNED" in user_manifest.read_text()
    marketplace_path = tmp_path / ".agents" / "plugins" / "marketplace.json"
    assert not marketplace_path.exists(), (
        "autorun must not add a marketplace entry that points at a user-owned "
        "~/plugins/autorun directory"
    )


def test_codex_plugin_manifest_exists_for_packaged_skills():
    """The source plugin must include Codex's required manifest file."""
    manifest = Path(__file__).parents[1] / ".codex-plugin" / "plugin.json"
    data = json.loads(manifest.read_text())
    assert data["name"] == "autorun"
    assert data["skills"] == "./skills/"
    assert "hooks" not in data, (
        "Codex plugin packaging should not duplicate user-level hooks from "
        "~/.codex/hooks.json"
    )


def test_repo_codex_marketplace_targets_autorun_plugin():
    """GitHub marketplace installs need a repo-scoped Codex marketplace file."""
    marketplace = Path(__file__).parents[3] / ".agents" / "plugins" / "marketplace.json"
    data = json.loads(marketplace.read_text(encoding="utf-8"))
    assert data["name"] == "autorun"
    assert data["interface"]["displayName"] == "Autorun"
    entries = [plugin for plugin in data["plugins"] if plugin["name"] == "autorun"]
    assert len(entries) == 1
    assert entries[0]["source"] == {"source": "local", "path": "./plugins/autorun"}


def test_install_codex_plugin_with_cli_runs_codex_plugin_add(monkeypatch):
    """The real installer must refresh and install the local Codex plugin."""
    calls = []

    monkeypatch.setattr(
        "shutil.which",
        lambda binary: f"/usr/bin/{binary}" if binary == "codex" else None,
    )

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append((cmd, kwargs.get("timeout")))
        return CmdResult(True, "Added plugin `autorun` from marketplace `personal`.")

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    result = _install_codex_plugin_with_cli()

    assert result.ok
    assert calls == [
        (["codex", "plugin", "remove", "autorun@personal"], 120),
        (["codex", "plugin", "add", "autorun@personal"], 120),
    ]


def test_install_codex_plugin_with_cli_uses_github_marketplace(monkeypatch):
    """GitHub mode must add ahundt/autorun then install autorun@autorun."""
    calls = []

    monkeypatch.setattr(
        "shutil.which",
        lambda binary: f"/usr/bin/{binary}" if binary == "codex" else None,
    )

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append((cmd, kwargs.get("timeout")))
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    result = _install_codex_plugin_with_cli(
        marketplace_name="autorun",
        marketplace_source="ahundt/autorun",
    )

    assert result.ok
    assert calls == [
        (["codex", "plugin", "marketplace", "add", "ahundt/autorun"], 120),
        (["codex", "plugin", "remove", "autorun@autorun"], 120),
        (["codex", "plugin", "add", "autorun@autorun"], 120),
    ]


def test_install_codex_plugin_with_cli_force_refreshes_cache(monkeypatch):
    """Force mode must refresh Codex's plugin cache copy before add."""
    calls = []

    monkeypatch.setattr(
        "shutil.which",
        lambda binary: f"/usr/bin/{binary}" if binary == "codex" else None,
    )

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append((cmd, kwargs.get("timeout")))
        return CmdResult(True, "ok")

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    result = _install_codex_plugin_with_cli(force=True)

    assert result.ok
    assert calls == [
        (["codex", "plugin", "remove", "autorun@personal"], 120),
        (["codex", "plugin", "add", "autorun@personal"], 120),
    ]


def test_install_codex_plugin_with_cli_refreshes_cache_by_default(monkeypatch):
    """Normal installs must refresh Codex's cache so removed hooks disappear."""
    calls = []

    monkeypatch.setattr(
        "shutil.which",
        lambda binary: f"/usr/bin/{binary}" if binary == "codex" else None,
    )

    def fake_run_cmd(cmd, *args, **kwargs):
        calls.append((cmd, kwargs.get("timeout")))
        if cmd[:3] == ["codex", "plugin", "remove"]:
            return CmdResult(True, "Removed plugin `autorun`.")
        if cmd[:3] == ["codex", "plugin", "add"]:
            return CmdResult(True, "Added plugin `autorun` from marketplace `personal`.")
        return CmdResult(False, "unexpected")

    monkeypatch.setattr("autorun.install.run_cmd", fake_run_cmd)

    result = _install_codex_plugin_with_cli(force=False)

    assert result.ok
    assert calls == [
        (["codex", "plugin", "remove", "autorun@personal"], 120),
        (["codex", "plugin", "add", "autorun@personal"], 120),
    ]


def test_codex_plugin_marketplace_status_flags_cached_plugin_hooks(tmp_path, monkeypatch):
    """Status must flag simultaneous user and plugin autorun hooks."""
    from autorun.install import _codex_plugin_marketplace_status

    monkeypatch.setenv("HOME", str(tmp_path))
    marketplace_dir = tmp_path / ".agents" / "plugins"
    marketplace_dir.mkdir(parents=True)
    (marketplace_dir / "marketplace.json").write_text(json.dumps({
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [
            {
                "name": "autorun",
                "source": {"source": "local", "path": "./plugins/autorun"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }))
    source_manifest = tmp_path / "plugins" / "autorun" / ".codex-plugin" / "plugin.json"
    source_manifest.parent.mkdir(parents=True)
    source_manifest.write_text('{"name":"autorun","skills":"./skills/"}')
    codex_hooks = tmp_path / ".codex" / "hooks.json"
    codex_hooks.parent.mkdir(parents=True)
    codex_hooks.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run python /x/hooks/hook_entry.py --cli codex",
                        }
                    ]
                }
            ]
        }
    }))
    cache_manifest = (
        tmp_path
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / "autorun"
        / "0.12.0"
        / ".codex-plugin"
        / "plugin.json"
    )
    cache_manifest.parent.mkdir(parents=True)
    cache_manifest.write_text('{"name":"autorun","skills":"./skills/"}')
    cache_hooks = cache_manifest.parents[1] / "hooks" / "hooks.json"
    cache_hooks.parent.mkdir()
    cache_hooks.write_text('{"hooks":{"PreToolUse":[{"hooks":[]}]}}')

    ok, status = _codex_plugin_marketplace_status()

    assert not ok
    assert "duplicate user and plugin hooks" in status
    assert "0.12.0" in status


def test_codex_plugin_marketplace_status_allows_explicit_both_hook_source(tmp_path, monkeypatch):
    """Status must not flag duplicates when installer marker says both was selected."""
    from autorun.install import _codex_plugin_marketplace_status

    monkeypatch.setenv("HOME", str(tmp_path))
    marketplace_dir = tmp_path / ".agents" / "plugins"
    marketplace_dir.mkdir(parents=True)
    (marketplace_dir / "marketplace.json").write_text(json.dumps({
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [
            {
                "name": "autorun",
                "source": {"source": "local", "path": "./plugins/autorun"},
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }))
    source_dir = tmp_path / "plugins" / "autorun"
    source_manifest = source_dir / ".codex-plugin" / "plugin.json"
    source_manifest.parent.mkdir(parents=True)
    source_manifest.write_text('{"name":"autorun","skills":"./skills/"}')
    (source_dir / ".autorun-owned").write_text(
        "Autorun-owned Codex plugin source copy.\n"
        "codex_hook_source=both\n",
        encoding="utf-8",
    )
    codex_hooks = tmp_path / ".codex" / "hooks.json"
    codex_hooks.parent.mkdir(parents=True)
    codex_hooks.write_text(json.dumps({
        "hooks": {
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": "uv run python /x/hooks/hook_entry.py --cli codex",
                        }
                    ]
                }
            ]
        }
    }))
    cache_manifest = (
        tmp_path
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / "autorun"
        / "0.12.0"
        / ".codex-plugin"
        / "plugin.json"
    )
    cache_manifest.parent.mkdir(parents=True)
    cache_manifest.write_text('{"name":"autorun","skills":"./skills/"}')
    cache_hooks = cache_manifest.parents[1] / "hooks" / "hooks.json"
    cache_hooks.parent.mkdir()
    cache_hooks.write_text('{"hooks":{"PreToolUse":[{"hooks":[]}]}}')

    ok, status = _codex_plugin_marketplace_status()

    assert ok
    assert "explicit both" in status


def test_install_for_codex_agents_md_idempotent(tmp_path, monkeypatch):
    """Re-running install must not duplicate autorun's AGENTS.md block."""
    monkeypatch.setenv("HOME", str(tmp_path))
    fake_marketplace = tmp_path / "marketplace"
    fake_plugin = fake_marketplace / "plugins" / "autorun"
    fake_plugin.mkdir(parents=True)
    (fake_plugin / "hooks").mkdir()
    (fake_plugin / "hooks" / "hook_entry.py").write_text("#!/usr/bin/env python3\n")
    template = fake_plugin / "src" / "autorun" / "codex_template"
    template.mkdir(parents=True)
    (template / "AGENTS.md").write_text(
        "# autorun safety guidance (Codex)\n\n"
        "Override commands: /ar:sos, /ar:task-ignore <id>.\n"
    )

    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    first = (tmp_path / ".codex" / "AGENTS.md").read_text()
    _install_for_codex(fake_marketplace, ["autorun"], force=False)
    second = (tmp_path / ".codex" / "AGENTS.md").read_text()
    assert first == second, "AGENTS.md content must be stable across re-installs"
    # Hard upper bound: 2× template length leaves no room for double-appending.
    assert second.count("/ar:sos") == 1
    assert second.count("/ar:task-ignore") == 1
