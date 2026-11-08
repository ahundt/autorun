"""Launch installed hook strings through their harness's process boundary.

These are free process tests, collected by the ordinary Windows CI matrix.
They do not run a model or claim to test a harness's config discovery/trust UI.
Antigravity's Windows executable/argv are unpublished; do not infer them from
its Gemini-shaped manifest (https://antigravity.google/docs/hooks).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autorun.core import validate_hook_response
from autorun.installer import claude, codex, discovery, entrypoint, extension, runtime, steps
from autorun.installer.traversal import Context, Mode
from autorun.platforms import PLATFORMS
from conftest import minimal_env
from test_codex_response_schema import assert_codex_response_valid


PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parents[1]

# (POSIX executable/argv, Windows executable/argv). Resolve executables, never
# shell=True: Node spawn(shell:false) and Rust Command pass these exact args.
# Claude: official Windows Git Bash requirement, confirmed by the maintainer:
# https://github.com/anthropics/claude-code/issues/90495 (2.1.117 process argv)
# https://code.claude.com/docs/en/hooks (sh -c; exported plugin variables).
# Gemini 0.51.0 installed bundle chunk-OW353XI3.js:245754-245781,357002-357080;
# Qwen 0.21.15 chunk-L7RG5PVT.js:45431-45466,129811-129828,130042-130100.
# Codex: https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/hooks/src/engine/command_runner.rs#L417-L490
# and engine/discovery.rs#L616-L629 (commandWindows selection).
LAUNCHERS = {
    # Claude Code 2.1.117 captured argv: https://github.com/anthropics/claude-code/issues/90495
    "claude": (("/bin/sh", "-c"), ("bash.exe", "-c")),
    # https://github.com/google-gemini/gemini-cli/blob/v0.51.0/packages/core/src/utils/shell-utils.ts
    # https://github.com/google-gemini/gemini-cli/blob/v0.51.0/packages/core/src/hooks/hookRunner.ts
    "gemini": (("bash", "-c"), ("pwsh.exe", "-NoProfile", "-Command")),
    # https://github.com/QwenLM/qwen-code/blob/v0.21.15/packages/core/src/hooks/hookRunner.ts#L215-L241
    "qwen": (("bash", "-c"), ("powershell", "-Command")),
    # https://github.com/openai/codex/blob/rust-v0.166.0/codex-rs/hooks/src/engine/command_runner.rs#L417-L490
    "codex": (("/bin/sh", "-lc"), ("cmd.exe", "/C")),
}
UNVERIFIED = {"antigravity": "No published Windows executable/argv contract in official hooks docs or public source"}
HOOK_PLATFORMS = {
    name for name, platform in PLATFORMS.items()
    if platform.has_hooks and platform.windows_hook_shell in {"posix", "powershell"}
}


def test_every_string_hook_runner_is_verified_or_explicitly_unverified():
    assert set(LAUNCHERS).isdisjoint(UNVERIFIED)
    assert set(LAUNCHERS) | set(UNVERIFIED) == HOOK_PLATFORMS


def _written_hook(platform, root, plugin, resolved, route):
    """Use the same writer and renderer as the install route, then read disk."""
    name = platform.name
    event = platform.autorun_to_harness_cli_events.get("UserPromptSubmit", "UserPromptSubmit")
    if name == "codex":
        if route == "plugin":
            staged = codex.stage_plugin(
                plugin, root / "codex-plugin", include_hooks=True, include_skills=False,
                hook_command=resolved["_codex_plugin_hook_command"], hook_events=(event,),
            )
            path = staged / "hooks" / "hooks.json"
        else:
            ctx = Context(REPO, plugin_dirs=(plugin,), settings=resolved)
            entries = steps.hooks_for(platform, ctx)
            steps.apply_hooks(entries, Mode.INSTALL)
            path = entries[0].path
    elif platform.extensions_subdir:
        template = PLUGIN / "src" / "autorun" / "gemini_template"
        translated = extension.translated_hooks(
            template, platform, hook_command=resolved["_extension_hook_commands"][name],
        )
        staged = extension.stage_extension(
            template, plugin, root / name, extension.Manifest("ar", "0"),
            hook_manifest=translated,
        )
        path = staged / "hooks" / "hooks.json"
    else:
        cached = root / "claude-cache"
        (cached / "hooks").mkdir(parents=True)
        path = cached / "hooks" / "hooks.json"
        shutil.copy2(PLUGIN / "hooks" / "hooks.json", path)
        shutil.copy2(PLUGIN / "hooks" / "hook_entry.py", cached / "hooks" / "hook_entry.py")
        # Wheels ship the console command; the cache installer pins it.
        if route == "wheel":
            data = json.loads(path.read_text(encoding="utf-8"))
            for groups in data["hooks"].values():
                for group in groups:
                    for hook in group["hooks"]:
                        hook["command"] = claude.CONSOLE_HOOK_COMMAND
            path.write_text(json.dumps(data), encoding="utf-8")
            assert claude.pin_hook_command(cached, resolved["_claude_hook_command"])
        else:
            shutil.copy2(PLUGIN / "pyproject.toml", cached / "pyproject.toml")
            claude.substitute_root(cached)
            installed = runtime.install_cache_runtime(
                cached, PLUGIN, python=str(resolved["hook_python"]), run=runtime._spawn
            )
            assert installed.ok, installed
            assert claude.pin_hook_command(cached, claude.cache_hook_command(cached))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    groups = manifest[platform.hook_protocol.hook_manifest_container_key][event]
    hooks = [hook for group in groups for hook in group.get("hooks", [group])]
    assert len(hooks) == 1, hooks
    return event, hooks[0]


def _launch(platform, hook, payload, env):
    name = platform.name
    windows = os.name == "nt"
    prefix = list(LAUNCHERS[name][windows])
    command = hook["command"]
    if name == "codex":
        # A controlled session shell/environment, reconstructed by Codex.
        prefix[0] = env["COMSPEC"] if windows else env["SHELL"]
        if windows:
            command = hook["commandWindows"]
            # Rust raw_arg(format!("\"{command_line}\"")), not Python's
            # list2cmdline escaping. /C consumes the outer quote pair.
            return subprocess.run(
                f'"{prefix[0]}" /C "{command}"', executable=prefix[0],
                input=json.dumps(payload), capture_output=True, text=True,
                encoding="utf-8", env=env, cwd=payload["cwd"], timeout=45,
            )
        # Plugin variables are substituted before shell parsing, including
        # inside quotes (discovery.rs at rust-v0.166.0:680-682).
        for key in ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
            if key in env:
                command = command.replace("${" + key + "}", env[key])
    if windows and name == "claude":
        # Avoid the WSL bash alias; Claude resolves Git for Windows' bash.
        git = Path(shutil.which("git.exe") or "")
        candidates = (git.parent.parent / "bin" / "bash.exe", git.parent / "bash.exe")
        prefix[0] = next((str(path) for path in candidates if path.is_file()), "")
        assert prefix[0], "Git for Windows bash.exe is required on the Windows runner"
        env["PATH"] = str(Path(prefix[0]).parent) + os.pathsep + env["PATH"]
    if windows and name == "qwen":
        assert hook["shell"] == "powershell", hook
    if windows and name == "gemini":
        # getShellConfiguration prefers ComSpec only when it is PowerShell;
        # otherwise pwsh.exe on PATH, then Windows PowerShell.
        comspec = env.get("COMSPEC", "")
        if comspec.lower().endswith(("powershell.exe", "pwsh.exe")):
            prefix = [comspec, "-NoProfile", "-NonInteractive", "-Command"]
        elif not shutil.which("pwsh.exe"):
            prefix = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command"]
        command += "; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    env.update(hook.get("env", {}))
    assert prefix[0] and shutil.which(prefix[0]), prefix
    return subprocess.run(
        [*prefix, command], input=json.dumps(payload), capture_output=True,
        text=True, encoding="utf-8", env=env, cwd=payload["cwd"], timeout=45,
    )


@pytest.mark.parametrize("name,route", [
    (name, route) for name in sorted(HOOK_PLATFORMS - set(UNVERIFIED))
    for route in (("checkout", "wheel", "plugin") if name == "codex" else ("checkout", "wheel"))
])
def test_generated_hook_runs_through_harness_launcher(monkeypatch, tmp_path, name, route):
    platform = PLATFORMS[name]
    cache = subprocess.check_output(["uv", "cache", "dir"], text=True).strip()
    home = tmp_path / "home with space & apostrophe's"
    home.mkdir()
    env = minimal_env(
        PATH=os.environ["PATH"], HOME=str(home), USERPROFILE=str(home),
        AUTORUN_HOME=str(home / "ar"), AUTORUN_TEST_STATE_DIR=str(home / "state"),
        AUTORUN_USE_DAEMON="0", AUTORUN_TEST_MODE="1", UV_CACHE_DIR=cache,
        UV_PROJECT_ENVIRONMENT=sys.prefix,
        PYTHONPATH=str(PLUGIN / "src"), PYTHONIOENCODING="utf-8",
        SHELL="/bin/sh", COMSPEC=os.environ.get("COMSPEC", "cmd.exe"),
        CLAUDE_PLUGIN_ROOT=PLUGIN.as_posix(),
    )
    # Start with an allowlisted environment. Harness secret sanitization is
    # therefore the identity operation; no developer credentials are inherited.
    for key in tuple(os.environ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    plugin = PLUGIN
    if route == "wheel":
        plugin = home / "wheel"
        (plugin / "hooks").mkdir(parents=True)
        shutil.copy2(PLUGIN / "hooks" / "hook_entry.py", plugin / "hooks" / "hook_entry.py")
        (plugin / ".codex-plugin").mkdir()
        (plugin / ".codex-plugin" / "plugin.json").write_text('{"name":"ar"}', encoding="utf-8")
        (plugin / ".claude-plugin").mkdir()
        (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"ar"}', encoding="utf-8")
        monkeypatch.setattr(discovery, "plugin_dir", lambda _root, _name: plugin)
    resolved = {"codex_hook_source": "both", "hook_python": sys.executable}
    entrypoint._runtime_settings(REPO, resolved, (platform,), (), require_guidance=False)
    event, hook = _written_hook(platform, home, plugin, resolved, route)
    payload = {
        "hook_event_name": event, "session_id": f"launch-{name}-{route}",
        "cwd": str(home), "prompt": "/ar:st",
    }
    if name == "claude":
        env.update(CLAUDE_PROJECT_DIR=str(home),
                   CLAUDE_PLUGIN_ROOT=(home / "claude-cache").as_posix(),
                   CLAUDE_PLUGIN_DATA=str(home / "plugin-data"))
    if name == "codex" and route == "plugin":
        env.update(PLUGIN_ROOT=str(home / "codex-plugin"),
                   CLAUDE_PLUGIN_ROOT=str(home / "codex-plugin"),
                   PLUGIN_DATA=str(home / "plugin-data"),
                   CLAUDE_PLUGIN_DATA=str(home / "plugin-data"))
    if name in {"gemini", "qwen"}:
        env.update(GEMINI_PROJECT_DIR=str(home), CLAUDE_PROJECT_DIR=str(home))
    if name == "gemini":
        env.update(GEMINI_CWD=str(home), GEMINI_SESSION_ID=payload["session_id"],
                   GEMINI_PLANS_DIR=str(home / ".gemini" / "plans"))
    if name == "qwen":
        env.update(QWEN_PROJECT_DIR=str(home), QWEN_CODE_MODEL_IDENTITY="",
                   QWEN_CODE_AGENT_ID="", QWEN_CODE_PROMPT_ID="")
    result = _launch(platform, hook, payload, env)
    assert result.returncode == 0, (result.stdout, result.stderr, hook)
    assert not result.stderr, result.stderr
    response = json.loads(result.stdout)
    assert isinstance(response, dict) and response, result.stdout
    assert validate_hook_response("UserPromptSubmit", response, cli_type=name) == response
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == event
    assert output["additionalContext"], response  # Fail-open/printed commands cannot pass.
    if name == "codex":
        assert_codex_response_valid(event, response)
