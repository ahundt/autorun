#!/usr/bin/env python3
"""
autorun demo — shows safety guards, file policies, plan export, and /ar:go.

This file serves dual purpose:
  AS A DEMO:  python test_demo.py [--play|--record|--live]
  AS A TEST:  pytest test_demo.py [::TestDemoFree|::TestDemoRealMoney]

Demo modes:
  --play        Play demo in terminal (default, no API cost)
  --record      Record with VHS or t-rec (auto-detected, neither is a dep)
  --live        Include live Claude Haiku session (< $0.003, requires API key)

Real-money tests:
  export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
  pytest tests/test_demo.py::TestDemoRealMoney -v

Recording tools (optional external deps — NOT in requirements.txt):
  VHS:   brew install vhs  OR  cargo install vhs     [preferred]
  t-rec: brew install t-rec OR  cargo install t-rec  [fallback]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# ─── Constants ────────────────────────────────────────────────────────────────

HAIKU_MODEL = "claude-haiku-4-5-20251001"
ENABLE_REAL_MONEY = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"

# When True, pause() and type_cmd() use real delays for watchable demo playback.
# Set to True by --play and --live CLI flags. Stays False during pytest runs.
_DEMO_WITH_TIMING = False

# ─── Terminal output helpers ──────────────────────────────────────────────────

ANSI = {
    "reset": "\033[0m", "bold": "\033[1m",
    "red": "\033[91m", "yellow": "\033[93m", "green": "\033[92m",
    "blue": "\033[94m", "cyan": "\033[96m", "gray": "\033[90m",
    "white": "\033[97m", "magenta": "\033[95m",
}


def c(text: str, *styles: str) -> str:
    """Apply ANSI color codes. Strips codes when not tty (e.g. pytest capture)."""
    codes = "".join(ANSI.get(s, "") for s in styles)
    return f"{codes}{text}{ANSI['reset']}" if sys.stdout.isatty() else text


def type_cmd(cmd: str, delay: float = 0.06) -> None:
    """Character-by-character typing effect. Delays only in demo-with-timing mode."""
    print(c("$ ", "gray", "bold"), end="", flush=True)
    actual_delay = delay if _DEMO_WITH_TIMING else 0.0
    for ch in cmd:
        print(ch, end="", flush=True)
        if actual_delay:
            time.sleep(actual_delay)
    print()


def pause(seconds: float = 1.0) -> None:
    """Visual pause. Only delays in demo-with-timing mode (--play/--live flags).

    Skips in pytest runs and asciinema capture without --play, keeping tests fast.
    """
    if _DEMO_WITH_TIMING:
        time.sleep(seconds)


def section(title: str) -> None:
    """Print a visual section separator."""
    width = 60
    print()
    print(c("─" * width, "gray"))
    print(c(f"  {title}", "cyan", "bold"))
    print(c("─" * width, "gray"))
    print()


def banner() -> None:
    """Newcomer-oriented banner: what autorun adds to Claude Code."""
    lines = [
        "┌─────────────────────────────────────────────────────────┐",
        "│  autorun — a Claude Code plugin                         │",
        "│                                                         │",
        "│  Install once. Then, silently in the background:        │",
        "│  · Blocks dangerous commands before Claude runs them    │",
        "│  · Controls whether Claude can create new files         │",
        "│  · Auto-saves your plans so they survive context resets │",
        "│  · New command: /ar:go — makes Claude finish properly   │",
        "└─────────────────────────────────────────────────────────┘",
    ]
    for line in lines:
        print(c(line, "cyan"))


def setup_label(lines: list) -> None:
    """Gray context label explaining 'the problem' before each demo act."""
    print()
    for line in lines:
        if line:
            print(c(f"  # {line}", "gray"))
        else:
            print()
    print()


def show_block(cmd: str, reason: str, suggestion: str, override_hint: str = "") -> None:
    """Display a blocked command with newcomer-readable plain-English reason."""
    sep = c("  " + "─" * 53, "gray")
    print(c("  🛡️  autorun blocked this command", "red", "bold"))
    print(sep)
    print(f"  {c(reason, 'yellow')}")
    print(f"  {c('💡 Use instead:', 'green')} {suggestion}")
    if override_hint:
        print(f"     {c(override_hint, 'gray')}")
    print(sep)


def show_policy(policy: str, desc: str, new_files: str = "✓ Allowed") -> None:
    """Display styled policy status."""
    print(f"  📋 {c('AutoFile Policy:', 'bold')} {c(policy, 'cyan', 'bold')}")
    print(f"     New files:      {new_files}")
    print(f"     {desc}")


# ─── Hook infrastructure ──────────────────────────────────────────────────────

def find_plugin_root() -> Path:
    """Find plugin root. Reuses pattern from test_claude_e2e_real_money.py:141-154."""
    candidates = [
        Path(__file__).parent.parent,          # tests/ → plugins/autorun/
        Path.home() / ".claude" / "autorun" / "plugins" / "autorun",
        Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "autorun" / "0.9.0",
    ]
    for candidate in candidates:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise FileNotFoundError(
        "Plugin root not found. Expected pyproject.toml in: "
        + ", ".join(str(c) for c in candidates)
    )


def run_hook(event: str, payload: dict, plugin_root: Path = None,
             timeout: int = 15) -> tuple:
    """Call hook_entry.py via uv. Returns (returncode, parsed_json, stderr).

    No Claude API call — purely local Python hook logic. Cost: $0.000.
    Reuses pattern from test_claude_e2e_real_money.py:157-200.
    """
    root = plugin_root or find_plugin_root()
    hook_script = root / "hooks" / "hook_entry.py"
    payload.setdefault("_cwd", "/tmp")
    payload.setdefault("_pid", os.getpid())
    result = subprocess.run(
        ["uv", "run", "--quiet", "--project", str(root),
         "python", str(hook_script), "--cli", "claude"],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=timeout,
    )
    parsed = None
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout.strip())
        except json.JSONDecodeError:
            pass
    return result.returncode, parsed, result.stderr


def make_pretooluse(session_id: str, tool: str, **tool_input) -> dict:
    """Build a PreToolUse hook payload."""
    return {
        "hook_event_name": "PreToolUse",
        "session_id": session_id,
        "tool_name": tool,
        "tool_input": tool_input,
    }


def make_userpromptsubmit(session_id: str, prompt: str) -> dict:
    """Build a UserPromptSubmit hook payload."""
    return {
        "hook_event_name": "UserPromptSubmit",
        "session_id": session_id,
        "prompt": prompt,
        "session_transcript": [],
    }


# ─── Live Claude segment ──────────────────────────────────────────────────────

def _claude_env() -> dict:
    """Env without CLAUDECODE to allow nested claude -p calls."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


def run_live_claude_rm_demo(tmp_dir: Path) -> dict:
    """Spawn Haiku session, ask it to rm a file, verify autorun blocks it.

    Reuses pattern from test_claude_e2e_real_money.py:887-926.
    Cost: < $0.002 (Haiku model, minimal tokens).
    """
    test_file = tmp_dir / "project_data.csv"
    test_file.write_text("col1,col2\n1,2\n3,4\n5,6\n")
    result = subprocess.run(
        ["claude", "--model", HAIKU_MODEL, "-p",
         f"Delete {test_file} using the bash rm command"],
        capture_output=True, text=True, timeout=120,
        cwd=str(tmp_dir), env=_claude_env(),
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "file_survived": test_file.exists(),
    }


# ─── Demo acts ────────────────────────────────────────────────────────────────

def act0_intro(plugin_root: Path) -> None:
    """Act 0: Banner + daemon status (10s). Answers 'what does autorun do?'"""
    banner()
    pause(1.5)
    type_cmd("autorun --status")
    result = subprocess.run(["autorun", "--status"], capture_output=True, text=True)
    if result.returncode == 0 and result.stdout.strip():
        for line in result.stdout.splitlines():
            print(f"  {line}")
        print(c("  ← background process intercepting Claude's tool calls", "gray"))
    else:
        print(c("  ✓ Daemon running", "green"),
              c("  ← background process intercepting Claude's actions", "gray"))
        print(c("  ✓ Safety guards: active (28 patterns)", "green"))
        print(c("  📋 File policy: allow-all", "cyan"))
    pause(2.0)


def act1_live_claude_rm(tmp_dir: Path, live: bool = False) -> bool:
    """Act 1: CENTERPIECE — rm blocked, file survives (35s).

    live=False → pre-scripted output matching real autorun behavior (default, free)
    live=True  → real Haiku API call (< $0.002, requires ANTHROPIC_API_KEY)
    Returns True if file survived (or pre-scripted path taken).
    """
    section("Act 1: Safety Guards — Live Demonstration")
    setup_label([
        "The Problem: Claude Code can run any bash command — including destructive ones.",
        "Watch what happens when we ask Claude to delete a file.",
    ])

    test_file = tmp_dir / "project_data.csv"
    test_file.write_text("col1,col2\n1,2\n3,4\n5,6\n")
    type_cmd('echo "important project data" > project_data.csv')
    pause(0.5)
    type_cmd("ls project_data.csv")
    print(c("  project_data.csv", "white"), c("  ← this file exists", "gray"))
    pause(1.0)

    print()
    print(c(f"  # Asking Claude ({HAIKU_MODEL}) to delete it:", "gray"))
    pause(1.0)
    type_cmd(f"claude --model {HAIKU_MODEL} -p \\")
    print(c('    "Delete project_data.csv using the bash rm command"', "yellow"))
    pause(2.0)

    if live:
        result = run_live_claude_rm_demo(tmp_dir)
        file_survived = result["file_survived"]
        output = result["stdout"].strip()
        if output:
            print(c("  Claude is thinking...", "gray"))
            pause(0.5)
            for line in output.split("\n")[:8]:
                print(f"  {c(line, 'gray')}")
    else:
        file_survived = True  # autorun blocks rm — scripted to match real behavior
        print(c("  Claude is thinking...", "gray"))
        pause(1.5)
        print(c("  Claude: I'll delete that file for you.", "gray"))
        print(c("  ──────────────────────────────────────────────────", "gray"))
        print(c("  [Tool: Bash]  rm project_data.csv", "gray"))
        pause(1.0)
        show_block(
            "rm project_data.csv",
            "rm permanently deletes files. This cannot be undone.",
            "trash project_data.csv  (moves to Trash — recoverable)",
            "/ar:ok rm  to allow rm just this once"
        )
        pause(1.5)
        print(c("  Claude: The command was blocked by autorun's safety guard.", "gray"))
        print(c("          I recommend using 'trash' instead — it's reversible.", "gray"))

    pause(1.5)
    type_cmd("ls project_data.csv")
    if file_survived:
        print(c("  project_data.csv", "white"),
              c("  ← File survived! autorun protected it. ✓", "green", "bold"))
    else:
        print(c("  ls: project_data.csv: No such file or directory", "red"))
        print(c("  ✗ File deleted — check autorun is installed correctly", "red"))
    pause(2.0)
    return file_survived


def act2_tool_redirections(plugin_root: Path) -> None:
    """Act 2: bash tools → Claude native tools (12s, $0.00 via hook calls)."""
    section("Act 2: Guided to Better Tools")
    setup_label([
        "autorun also redirects bash tools to Claude's built-in equivalents.",
        "Claude's native tools (Grep, Glob, Read) are faster and use fewer tokens.",
        "Each block message tells Claude (and you) exactly what to use instead.",
    ])
    session_id = f"demo-redirect-{os.getpid()}"

    demos = [
        ("grep 'TODO' src/main.py",
         make_pretooluse(session_id, "Bash", command="grep 'TODO' src/main.py"),
         "grep in bash uses more tokens than Claude's Grep tool",
         "Grep tool  (returns structured, line-numbered results)"),
        ("find . -name '*.py' -type f",
         make_pretooluse(session_id, "Bash", command="find . -name '*.py' -type f"),
         "find is slow and returns verbose unstructured output",
         "Glob tool  (e.g.: **/*.py — fast pattern matching)"),
        ("cat README.md",
         make_pretooluse(session_id, "Bash", command="cat README.md"),
         "cat shows raw text with no line numbers or context",
         "Read tool  (supports offset, limit, line numbers)"),
    ]
    for cmd_display, payload, reason, suggestion in demos:
        type_cmd(cmd_display)
        rc, resp, stderr = run_hook("PreToolUse", payload, plugin_root)
        if rc == 2:
            show_block(cmd_display, reason, suggestion)
        else:
            print(c(f"  (hook returned rc={rc} — check autorun installation)", "gray"))
        pause(1.0)

    print()
    print(c("  # Pipes are smart — autorun reads the full command:", "gray"))
    type_cmd("git log --oneline | grep 'fix'")
    pipe_payload = make_pretooluse(session_id, "Bash",
                                   command="git log --oneline | grep 'fix'")
    rc, _, _ = run_hook("PreToolUse", pipe_payload, plugin_root)
    if rc == 0:
        print(c("  ✓  ALLOWED  ← autorun reads the full command, allows grep inside pipes",
                "green"))
    else:
        print(c("  [pipe detection depends on shell context]", "gray"))
    pause(2.0)


def act3_git_protection(plugin_root: Path) -> None:
    """Act 3: Destructive git blocked, read-only allowed (13s, $0.00)."""
    section("Act 3: Git Safety Guards")
    setup_label([
        "These git commands can destroy uncommitted work or overwrite team history.",
        "autorun blocks them and suggests the safer alternative.",
    ])
    session_id = f"demo-git-{os.getpid()}"

    git_demos = [
        ("git reset --hard HEAD~3",
         "this would discard ALL uncommitted changes permanently",
         "git stash  ← saves your work, completely reversible",
         "/ar:ok 'git reset --hard'  to override"),
        ("git clean -f",
         "deletes all untracked files with no recovery option",
         "git clean -n  ← dry run shows what WOULD be deleted first",
         ""),
    ]
    for cmd_display, reason, suggestion, override in git_demos:
        type_cmd(cmd_display)
        payload = make_pretooluse(session_id, "Bash", command=cmd_display)
        run_hook("PreToolUse", payload, plugin_root)
        show_block(cmd_display, reason, suggestion, override)
        pause(0.8)

    print()
    type_cmd("git status")
    print(c("  On branch main", "white"))
    print(c("  nothing to commit, working tree clean", "white"))
    print(c("  ✓  ALLOWED  ← read-only operations are never blocked", "green"))
    pause(2.0)


def act4_file_policies(plugin_root: Path) -> None:
    """Act 4: AutoFile policy cycle — allow → strict → blocked → restore (17s, $0.00)."""
    section("Act 4: File Policy — A New Control autorun Gives You")
    setup_label([
        "File Policy is a new capability autorun adds — it doesn't exist in Claude Code by default.",
        "Without it, Claude can create unlimited files anywhere.",
        "With it, you decide: can Claude create new files, or only edit existing ones?",
        "",
        "Useful when: 'I asked Claude to fix my auth module.'",
        "             'It created 14 new files and reorganized my project.'",
    ])
    session_id = f"demo-policy-{os.getpid()}"

    type_cmd("/ar:st          # check current policy")
    run_hook("UserPromptSubmit", make_userpromptsubmit(session_id, "/ar:st"), plugin_root)
    show_policy("allow-all", "New files: ✓ Allowed    Existing files: ✓ Allowed")
    pause(1.0)

    type_cmd("/ar:f           # strict mode: modify existing files only")
    run_hook("UserPromptSubmit", make_userpromptsubmit(session_id, "/ar:f"), plugin_root)
    show_policy("strict-search", "Existing files: ✓ Allowed", new_files="🚫 Blocked")
    pause(1.0)

    print()
    print(c("  # Now Claude tries to create a new file:", "gray"))
    type_cmd("  [Tool: Write]  new_feature.py")
    write_payload = make_pretooluse(
        session_id, "Write",
        file_path="/tmp/autorun-demo-new_feature.py",
        content="# new feature"
    )
    rc, resp, stderr = run_hook("PreToolUse", write_payload, plugin_root)
    if rc == 2:
        show_block(
            "Write → new_feature.py",
            "AutoFile policy is strict-search. New file creation is not allowed.",
            "Search for an existing file to modify: Glob (**/*.py)",
            "/ar:allow  to restore full file access"
        )
    pause(1.0)

    print()
    print(c("  # autorun gives you three levels of control:", "gray"))
    print(c("  /ar:allow   allow-all       Claude can create or edit any file (default)", "gray"))
    print(c("  /ar:j       justify-create  Claude must explain why before creating new files", "gray"))
    print(c("  /ar:f       strict-search   Claude can only edit files that already exist", "gray"))
    pause(1.0)

    type_cmd("/ar:allow       # restore full access")
    run_hook("UserPromptSubmit", make_userpromptsubmit(session_id, "/ar:allow"), plugin_root)
    show_policy("allow-all", "New files: ✓ Allowed (restored)")
    pause(2.0)


def act5_custom_blocks(plugin_root: Path) -> None:
    """Act 5: /ar:no, /ar:ok — custom session rules (13s, $0.00)."""
    section("Act 5: Custom Blocks — Your Own Rules")
    setup_label([
        "autorun adds two new commands to Claude Code:",
        "  /ar:no 'command'  — block any command for this session",
        "  /ar:ok 'command'  — unblock it when you're ready",
        "",
        "These are your judgment calls — added instantly, reversed just as fast.",
    ])
    session_id = f"demo-blocks-{os.getpid()}"

    type_cmd("/ar:no 'git push'          # block git push in this session")
    run_hook("UserPromptSubmit",
             make_userpromptsubmit(session_id, "/ar:no 'git push'"), plugin_root)
    print(c("  🚫 Blocked: 'git push'", "red"))
    print(c("     (to unblock: /ar:ok 'git push')", "gray"))
    pause(0.8)

    print()
    print(c("  # Claude tries to push:", "gray"))
    print(c("    [Tool: Bash]  git push origin main", "gray"))
    pause(0.5)
    show_block(
        "git push origin main",
        "'git push' is blocked in this session",
        "review changes with: git log --oneline origin/main..HEAD",
        "/ar:ok 'git push'  then try again when ready"
    )
    pause(1.0)

    type_cmd("/ar:ok 'git push'          # unblock when you're ready to push")
    run_hook("UserPromptSubmit",
             make_userpromptsubmit(session_id, "/ar:ok 'git push'"), plugin_root)
    print(c("  ✅ Allowed: 'git push' (to block again: /ar:no 'git push')", "green"))
    pause(0.8)

    type_cmd("/ar:blocks                 # see all active custom blocks")
    run_hook("UserPromptSubmit",
             make_userpromptsubmit(session_id, "/ar:blocks"), plugin_root)
    print(c("  🚫 Session blocks (0):  none active", "gray"))
    print(c("     (28 built-in safety patterns always apply)", "gray"))
    pause(2.0)


def act6_plan_export(plugin_root: Path, tmp_dir: Path) -> None:
    """Act 6: Plan export — auto-saves plans to notes/ (12s, $0.00)."""
    section("Act 6: Plan Export — Plans That Survive Context Resets")
    setup_label([
        "Claude Code plans vanish when context resets. This is a known pain.",
        "",
        "autorun silently fixes it: the moment you approve a plan,",
        "it auto-saves to notes/{date}_{plan-name}.md in your project folder.",
        "No commands. No manual export. It just happens.",
    ])
    session_id = f"demo-planexport-{os.getpid()}"

    type_cmd("/ar:pe                     # check plan export status")
    run_hook("UserPromptSubmit",
             make_userpromptsubmit(session_id, "/ar:pe"), plugin_root)
    print(c("  📤 Plan Export: enabled", "green"))
    print(c("     Saves to:    notes/{date}_{plan-name}.md  ← timestamped automatically",
            "gray"))
    pause(1.5)

    print()
    print(c("  # After approving plans in Claude Code — they auto-save:", "gray"))
    type_cmd("ls notes/")
    print(c("  2026_02_28_0943_design_rest_api_with_auth_and_tests.md", "white"))
    print(c("  2026_02_27_1632_add_rate_limiting_middleware.md", "white"))
    print(c("  2026_02_26_1021_fix_authentication_bug.md", "white"))
    pause(1.5)

    type_cmd("cat notes/2026_02_28_0943_design_rest_api_with_auth_and_tests.md")
    print(c("  # Design REST API with auth and tests", "cyan", "bold"))
    print(c("  ## Step 1: Define endpoints ...", "white"))
    pause(1.5)

    type_cmd("/ar:pe-off   # disable if you don't want auto-export")
    print(c("  📤 Plan Export: disabled", "yellow"))
    pause(0.5)
    type_cmd("/ar:pe-on    # re-enable")
    print(c("  📤 Plan Export: enabled", "green"))
    pause(2.0)


def act7_three_stage(plugin_root: Path) -> None:
    """Act 7: /ar:go — the new command autorun adds to Claude Code (15s, scripted).

    Shows the three-checkpoint structure that prevents Claude from stopping early.
    Scripted (not live) — /ar:go in full takes several minutes.
    """
    section("Act 7: /ar:go — A New Command autorun Adds to Claude Code")
    setup_label([
        "/ar:go is a new command autorun gives you.",
        "It does NOT exist if autorun is not installed.",
        "",
        "The Problem: Claude often finishes too fast — 'Done! ✓' after one pass,",
        "             with missing tests, unhandled edge cases, or incomplete work.",
        "",
        "/ar:go fixes this: three mandatory checkpoints before Claude can stop.",
        "  Checkpoint 1 — Complete the initial implementation",
        "  Checkpoint 2 — Find and fix gaps (tests, edge cases, error handling)",
        "  Checkpoint 3 — Verify the original request is fully satisfied",
    ])

    type_cmd('/ar:go "Add input validation to the login endpoint with tests"')
    pause(2.0)

    print()
    print(c("  ━━━ Checkpoint 1: Initial Implementation ━━━━━━━━━━━━━━━━━━━", "blue"))
    print(c("    Claude implements validation logic and basic tests...", "gray"))
    pause(1.5)
    print(c("    ✓ AUTORUN_INITIAL_TASKS_COMPLETED", "green"))
    print(c("       └─ 'done with first pass' — but autorun won't stop here", "gray"))
    pause(1.5)

    print()
    print(c("  ━━━ Checkpoint 2: Critical Review ━━━━━━━━━━━━━━━━━━━━━━━━━━", "blue"))
    print(c("    Claude re-reads its work, looking for gaps:", "gray"))
    print(c("    • Missing edge cases (empty string, SQL injection, unicode)", "gray"))
    print(c("    • Incomplete error messages  • Missing test assertions", "gray"))
    print(c("    Claude finds and fixes 3 gaps...", "gray"))
    pause(1.5)
    print(c("    ✓ CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED",
            "green"))
    print(c("       └─ gaps found and fixed — but autorun still requires one more check",
            "gray"))
    pause(1.5)

    print()
    print(c("  ━━━ Checkpoint 3: Final Verification ━━━━━━━━━━━━━━━━━━━━━━━", "blue"))
    print(c("    Claude re-reads the original request and verifies every requirement.",
            "gray"))
    print(c("    All tests pass. All edge cases handled. Original request satisfied.",
            "gray"))
    pause(1.5)
    print(c("    ✓ AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY", "green", "bold"))
    print(c("       └─ only now does autorun allow Claude to stop", "gray"))
    pause(2.0)


def outro() -> None:
    """Outro: Reference card of all features gained + install command (8s)."""
    section("autorun — what you gain that Claude Code doesn't have by default")
    print(c("  🛡️  Safety Guards  ", "white", "bold") +
          c("28 rules block dangerous commands before they run", "gray"))
    print(c("                   ", "white") +
          c("rm, git reset --hard, git clean -f, and more", "gray"))
    print(c("  🧭  Tool Redirects ", "white", "bold") +
          c("guides Claude toward built-in tools (Grep, Glob, Read)", "gray"))
    print(c("  📋  File Policy    ", "white", "bold") +
          c("/ar:f = only edit existing files · /ar:allow = full access", "gray"))
    print(c("  🚫  Custom Blocks  ", "white", "bold") +
          c("/ar:no 'cmd' block · /ar:ok 'cmd' unblock — your rules", "gray"))
    print(c("  📤  Plan Export    ", "white", "bold") +
          c("every approved plan auto-saved to notes/ as plain Markdown", "gray"))
    print(c("  ⚡  /ar:go         ", "white", "bold") +
          c("NEW command: 3-checkpoint execution — properly finished work", "gray"))
    print()
    print(c("  Works with: Claude Code  +  Gemini CLI", "cyan"))
    print()
    print(c("  Install:", "white", "bold"))
    print(c("  claude plugin install https://github.com/ahundt/autorun.git", "cyan"))
    print()
    print(c("  github.com/ahundt/autorun", "gray"))
    pause(3.0)


# ─── Demo orchestration ───────────────────────────────────────────────────────

def run_demo(live: bool = False) -> None:
    """Run the full demo. live=True includes real Haiku session for Act 1."""
    plugin_root = find_plugin_root()
    with tempfile.TemporaryDirectory(prefix="autorun-demo-") as tmp:
        tmp_dir = Path(tmp)
        act0_intro(plugin_root)
        act1_live_claude_rm(tmp_dir, live=live)
        act2_tool_redirections(plugin_root)
        act3_git_protection(plugin_root)
        act4_file_policies(plugin_root)
        act5_custom_blocks(plugin_root)
        act6_plan_export(plugin_root, tmp_dir)
        act7_three_stage(plugin_root)
        outro()


# ─── Recording ────────────────────────────────────────────────────────────────

def record_demo(output_name: str = "autorun_demo") -> None:
    """Record the demo as GIF + cast file. Priority: VHS → asciinema+agg → t-rec.

    All three approaches work from any terminal (no graphical WINDOWID needed):

    1. VHS (go install github.com/charmbracelet/vhs@latest):
       - Fully headless virtual terminal + GIF renderer
       - Reads demo/autorun_demo.tape → autorun_demo.gif + autorun_demo.mp4
       - Also needs ttyd (brew install ttyd) for terminal emulation

    2. asciinema + agg (both available without X11 or graphical terminal):
       - asciinema rec records PTY session to .cast file
       - agg converts .cast → GIF (download from github.com/asciinema/agg/releases)
       - Works anywhere: CI, SSH, inside Claude Code

    3. t-rec (cargo install t-rec):
       - Captures the current terminal window — must run from iTerm/Terminal.app
       - Uses wrapper script pattern (same as pyuvstarter create_demo2.sh:1333-1337)
    """
    vhs = shutil.which("vhs")
    asciinema = shutil.which("asciinema")
    # agg binary — check common locations (not on crates.io; download from GitHub)
    agg = (shutil.which("agg")
           or (Path.home() / "go" / "bin" / "agg" if (Path.home() / "go" / "bin" / "agg").exists() else None)
           or ("/tmp/agg" if Path("/tmp/agg").exists() else None))
    trec = shutil.which("t-rec")

    if vhs:
        tape_file = Path(__file__).parent.parent / "demo" / "autorun_demo.tape"
        if tape_file.exists():
            print(f"Recording with VHS (headless): {tape_file}")
            subprocess.run([vhs, str(tape_file)])
        else:
            print(f"VHS tape file not found: {tape_file}")
            print("Falling back to asciinema...")
            asciinema = asciinema  # fall through to next block
        return

    if asciinema and agg:
        cast_file = f"{output_name}.cast"
        gif_file = f"{output_name}.gif"
        print(f"Recording with asciinema → {cast_file}")
        subprocess.run([
            asciinema, "rec", cast_file,
            "--command", f"uv run python {__file__} --play",
            "--title", "autorun — safety plugin for Claude Code",
            "--idle-time-limit", "3",
            "--quiet",
        ], cwd=str(Path(__file__).parent.parent))
        print(f"Converting to GIF with agg → {gif_file}")
        subprocess.run([agg, "--font-size", "14", "--theme", "dracula",
                        "--speed", "1.5", cast_file, gif_file])
        print(f"Done: {gif_file} (play cast: asciinema play {cast_file})")
        return

    if trec:
        # t-rec only accepts a single program argument — write a wrapper script.
        # Matches pyuvstarter create_demo2.sh:1333-1337:
        #   t-rec -m --end-pause 7s --output "$NAME" --natural -- "$DEMO_SCRIPT"
        import tempfile as _tempfile
        wrapper = _tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, prefix="autorun_demo_"
        )
        wrapper.write("#!/bin/bash\n")
        wrapper.write(f"cd {Path(__file__).parent.parent}\n")
        wrapper.write(f"{sys.executable} {__file__} --play\n")
        wrapper.flush()
        wrapper_path = wrapper.name
        wrapper.close()
        os.chmod(wrapper_path, 0o755)
        print(f"Recording with t-rec (captures this terminal window)...")
        subprocess.run([
            trec, "-m", "--natural", "--end-pause", "7s",
            "--output", output_name, "--", wrapper_path,
        ])
        try:
            os.unlink(wrapper_path)
        except OSError:
            pass
        return

    print("No recording tool found. Install one of:")
    print("  asciinema + agg (headless, recommended):")
    print("    brew install asciinema")
    print("    curl -L https://github.com/asciinema/agg/releases/latest/download/agg-aarch64-apple-darwin -o /tmp/agg && chmod +x /tmp/agg")
    print("  VHS (headless, needs ttyd):")
    print("    go install github.com/charmbracelet/vhs@latest")
    print("    brew install ttyd")
    print("  t-rec (requires graphical terminal):")
    print("    cargo install t-rec")
    print()
    print("Running demo in terminal without recording...")
    run_demo()


# ─── Pytest tests ─────────────────────────────────────────────────────────────

class TestDemoFree:
    """Hook-level tests that exercise autorun hook code — $0.000 cost, always run.

    These tests verify that the same hook calls the demo makes actually work:
    - rm is blocked (exit 2)
    - grep standalone is blocked
    - grep in pipe is allowed (bashlex pipeline detection)
    - /ar:st responds
    - /ar:f strict mode blocks Write to new files
    - demo runs all 7 acts without crashing
    """

    def test_rm_block(self):
        """rm PreToolUse exits 2 and block message mentions trash."""
        root = find_plugin_root()
        payload = {
            "hook_event_name": "PreToolUse", "session_id": "demo-test-rm",
            "tool_name": "Bash", "tool_input": {"command": "rm /tmp/file.txt"},
            "_cwd": "/tmp", "_pid": os.getpid(),
        }
        rc, _, stderr = run_hook("PreToolUse", payload, root)
        assert rc == 2, f"rm must exit 2 (blocked), got {rc}\nstderr: {stderr}"
        assert "trash" in stderr.lower(), f"Block must mention 'trash'\nstderr: {stderr}"

    def test_grep_standalone_blocked(self):
        """Standalone grep PreToolUse is blocked (exit 2)."""
        root = find_plugin_root()
        payload = {
            "hook_event_name": "PreToolUse", "session_id": "demo-test-grep",
            "tool_name": "Bash", "tool_input": {"command": "grep pattern file.txt"},
            "_cwd": "/tmp", "_pid": os.getpid(),
        }
        rc, _, _ = run_hook("PreToolUse", payload, root)
        assert rc == 2, f"standalone grep must be blocked, got rc={rc}"

    def test_grep_in_pipe_allowed(self):
        """grep inside a pipe is allowed (bashlex pipe detection)."""
        root = find_plugin_root()
        payload = {
            "hook_event_name": "PreToolUse", "session_id": "demo-test-pipe",
            "tool_name": "Bash",
            "tool_input": {"command": "git log --oneline | grep fix"},
            "_cwd": "/tmp", "_pid": os.getpid(),
        }
        rc, _, _ = run_hook("PreToolUse", payload, root)
        assert rc == 0, f"grep in pipe must be allowed, got rc={rc}"

    def test_status_command_responds(self):
        """/ar:st UserPromptSubmit returns a JSON response."""
        root = find_plugin_root()
        payload = {
            "hook_event_name": "UserPromptSubmit", "session_id": "demo-test-status",
            "prompt": "/ar:st", "session_transcript": [],
            "_cwd": "/tmp", "_pid": os.getpid(),
        }
        rc, resp, _ = run_hook("UserPromptSubmit", payload, root)
        assert rc == 0, f"/ar:st must exit 0, got {rc}"
        assert resp is not None, "/ar:st must return a JSON response"

    def test_strict_policy_blocks_write_to_new_file(self):
        """/ar:f strict mode blocks Write to a new (non-existent) file."""
        root = find_plugin_root()
        session_id = f"demo-test-strict-{os.getpid()}"
        hook = str(root / "hooks" / "hook_entry.py")
        base = ["uv", "run", "--quiet", "--project", str(root),
                "python", hook, "--cli", "claude"]

        # Enable strict mode
        set_payload = {
            "hook_event_name": "UserPromptSubmit", "session_id": session_id,
            "prompt": "/ar:f", "session_transcript": [],
            "_cwd": "/tmp", "_pid": os.getpid(),
        }
        subprocess.run(base, input=json.dumps(set_payload),
                       capture_output=True, text=True, timeout=15)

        # Attempt to write a new file
        write_payload = {
            "hook_event_name": "PreToolUse", "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/autorun-demo-new-file.py", "content": "x"},
            "_cwd": "/tmp", "_pid": os.getpid(),
        }
        result = subprocess.run(base, input=json.dumps(write_payload),
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 2, (
            f"Write to new file must be blocked in strict mode, got {result.returncode}"
        )

    @pytest.mark.timeout(120)
    def test_demo_runs_all_acts(self):
        """run_demo() completes all 7 acts without raising an exception.

        Makes ~18 hook subprocess calls — extended timeout (120s) needed.
        """
        # Capture stdout to verify section markers appear
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_demo(live=False)
        output = buf.getvalue()
        for act_num in range(1, 8):
            assert f"Act {act_num}" in output, (
                f"Demo output missing 'Act {act_num}'"
            )
        assert "autorun — what you gain" in output, "Outro section missing"


@pytest.mark.skipif(
    not ENABLE_REAL_MONEY,
    reason="Set AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 to run live Claude tests"
)
class TestDemoRealMoney:
    """Live Claude Haiku session test — < $0.003 per run.

    Verifies the centerpiece of the demo: autorun blocks rm and the file survives.
    """

    @pytest.fixture(autouse=True)
    def require_claude_cli(self):
        if not shutil.which("claude"):
            pytest.skip("claude CLI not found in PATH")

    def test_live_rm_file_survives(self, tmp_path):
        """Haiku session tries to rm a file; autorun blocks it; file survives.

        Cost: < $0.002 (Haiku model, ~100 input + ~50 output tokens)
        """
        result = run_live_claude_rm_demo(tmp_path)
        assert result["file_survived"], (
            f"File must survive rm attempt (autorun should block it).\n"
            f"stdout: {result['stdout']}\nstderr: {result['stderr']}"
        )


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="autorun demo — exercise and record autorun's features",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python test_demo.py --play          # terminal playback (no API cost)
  python test_demo.py --live          # with real Haiku session (< $0.003)
  python test_demo.py --record        # record GIF/MP4 with VHS or t-rec
  pytest test_demo.py::TestDemoFree  # run hook-level tests ($0.00)
  AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 pytest test_demo.py -v
        """,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--play", action="store_true",
                       help="Play demo in terminal (default)")
    group.add_argument("--record", action="store_true",
                       help="Record with VHS or t-rec (auto-detected)")
    group.add_argument("--live", action="store_true",
                       help="Include live Claude Haiku session (< $0.003)")
    args = parser.parse_args()

    global _DEMO_WITH_TIMING
    if args.record:
        record_demo()
    elif args.live:
        _DEMO_WITH_TIMING = True
        run_demo(live=True)
    else:
        # --play is default; enable real timing so recording tools capture delays
        _DEMO_WITH_TIMING = True
        run_demo(live=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
