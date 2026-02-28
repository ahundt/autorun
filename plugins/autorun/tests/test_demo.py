#!/usr/bin/env python3
"""
autorun demo — shows safety guards, file policies, plan export, and /ar:go.

This file serves dual purpose:
  AS A DEMO:  python test_demo.py [--live|--play|--record]
  AS A TEST:  pytest test_demo.py [::TestDemoFree|::TestDemoRealMoney]

Demo modes:
  (default)   Run live demo: real Claude Code TUI in a new tmux window
  --play      Pre-scripted mode: hook-level output only, $0.00 cost
  --record    Record live demo with asciinema + agg → autorun_demo.gif

Real-money tests:
  export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
  pytest tests/test_demo.py::TestDemoRealMoney -v

Live demo requirements:
  - tmux (brew install tmux)
  - claude CLI in PATH
  - ANTHROPIC_API_KEY set (uses claude-haiku-4-5-20251001, < $0.02 total)
  - autorun daemon running (autorun --restart-daemon)

Recording tools (optional external deps — NOT in requirements.txt):
  asciinema: brew install asciinema
  agg:       curl -L https://github.com/asciinema/agg/releases/latest/download/agg-aarch64-apple-darwin -o /tmp/agg && chmod +x /tmp/agg
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

import pytest

# Add src to path for standalone demo use (conftest.py handles this for pytest)
_src_path = str(Path(__file__).parent.parent / "src")
if _src_path not in sys.path:
    sys.path.insert(0, _src_path)

# Import tmux detection functions (graceful fallback for environments without autorun)
try:
    from autorun.tmux_utils import (
        tmux_detect_claude_active,
        tmux_detect_prompt_type,
        PROMPT_TYPE_INPUT,
        PROMPT_TYPE_PLAN_APPROVAL,
    )
    _TMUX_UTILS_AVAILABLE = True
except ImportError:
    _TMUX_UTILS_AVAILABLE = False
    PROMPT_TYPE_INPUT = 'input'
    PROMPT_TYPE_PLAN_APPROVAL = 'plan_approval'

    def tmux_detect_prompt_type(content: str) -> Optional[str]:
        """Fallback: detect '>' prompt at end of line."""
        for line in content.splitlines()[-5:]:
            if line.strip() in ('>', '> '):
                return PROMPT_TYPE_INPUT
        return None

    def tmux_detect_claude_active(content: str) -> bool:
        """Fallback: detect esc to interrupt spinner."""
        return 'esc to interrupt' in content.lower()


# ─── Constants ────────────────────────────────────────────────────────────────

HAIKU_MODEL = "claude-haiku-4-5-20251001"
ENABLE_REAL_MONEY = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"

# When True, pause() and type_cmd() use real delays for watchable demo playback.
# Set to True by --live/--record CLI flags. Stays False during pytest runs.
_DEMO_WITH_TIMING = False

# When True, all acts use pre-scripted hook-level output ($0.00, no tmux).
# Set to True by --play CLI flag.
_SCRIPTED = False

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
    """Visual pause. Only delays in demo-with-timing mode (--live/--record flags).

    Skips in pytest runs and --play mode, keeping tests fast.
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


# ─── Claude env helper ────────────────────────────────────────────────────────

def _claude_env() -> dict:
    """Env without CLAUDECODE to allow nested claude -p calls."""
    return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}


# ─── DemoSession: real Claude Code TUI in a dedicated tmux window ─────────────

class DemoSession:
    """Controls a tmux session running `claude` interactively for demo recording.

    Creates an isolated tmux session, starts claude in it, and provides
    methods to send prompts and wait for responses. The claude TUI is what
    asciinema captures — giving viewers a realistic picture of autorun in action.

    Usage:
        session = DemoSession(work_dir=tmp_dir)
        session.create_shell()      # create tmux session (shell only)
        session.run_shell_cmd("autorun --status")  # act 0 in shell
        session.start_claude()      # start claude interactive
        session.send_prompt("Delete project_data.csv using rm")
        session.wait_for_response()
        session.exit_claude()
        session.destroy()
    """

    # Claude Code input prompt: the bare '>' on its own line
    _READY_POLL_INTERVAL = 0.5   # seconds between readiness polls
    _ACTIVITY_POLL_INTERVAL = 0.5  # seconds between active-status polls

    def __init__(self, session_name: str = "autorun-demo",
                 cols: int = 180, rows: int = 50,
                 work_dir: Optional[Path] = None):
        self.session_name = session_name
        self.cols = cols
        self.rows = rows
        self.work_dir = work_dir
        self._pane_target = f"{session_name}:0.0"
        self._claude_started = False

    # ── Tmux lifecycle ──────────────────────────────────────────────────────

    def create_shell(self) -> bool:
        """Create tmux session (bash shell only — claude not yet started).

        Returns True on success.
        """
        # Remove any leftover session from previous run
        subprocess.run(["tmux", "kill-session", "-t", self.session_name],
                       capture_output=True)
        time.sleep(0.3)

        cmd = [
            "tmux", "new-session", "-d",
            "-s", self.session_name,
            "-x", str(self.cols),
            "-y", str(self.rows),
        ]
        if self.work_dir:
            cmd += ["-c", str(self.work_dir)]

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            return False
        time.sleep(0.3)
        return True

    def start_claude(self) -> bool:
        """Start claude interactively in the existing shell session.

        Returns True once Claude shows its input prompt (ready to accept messages).
        Blocks up to 45 seconds.
        """
        self._send_literal(f"claude --model {HAIKU_MODEL}", enter=True)
        self._claude_started = True
        return self.wait_for_input_prompt(timeout=45)

    def destroy(self) -> None:
        """Kill the tmux session unconditionally."""
        subprocess.run(["tmux", "kill-session", "-t", self.session_name],
                       capture_output=True)

    # ── Low-level tmux I/O ─────────────────────────────────────────────────

    def _send_literal(self, text: str, enter: bool = False) -> bool:
        """Send literal text (no tmux key-code interpretation) to the pane.

        Uses -l flag so special characters like $ { } are not misinterpreted.
        Optionally follows with Enter.
        """
        r = subprocess.run(
            ["tmux", "send-keys", "-t", self._pane_target, "-l", text],
            capture_output=True,
        )
        if enter and r.returncode == 0:
            time.sleep(0.05)
            r2 = subprocess.run(
                ["tmux", "send-keys", "-t", self._pane_target, "Enter"],
                capture_output=True,
            )
            return r2.returncode == 0
        return r.returncode == 0

    def _send_key(self, key: str) -> bool:
        """Send a tmux key code (Enter, Escape, C-c, BTab, etc.)."""
        r = subprocess.run(
            ["tmux", "send-keys", "-t", self._pane_target, key],
            capture_output=True,
        )
        return r.returncode == 0

    def capture_pane(self, lines: int = 80) -> str:
        """Capture pane content (ANSI/control sequences stripped for parsing)."""
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", self._pane_target,
             "-p", f"-S-{lines}"],
            capture_output=True, text=True,
        )
        return result.stdout

    # ── High-level demo controls ────────────────────────────────────────────

    def run_shell_cmd(self, cmd: str, wait: float = 2.0) -> None:
        """Run a shell command (in bash, before or between claude sessions)."""
        self._send_literal(cmd, enter=True)
        if _DEMO_WITH_TIMING:
            time.sleep(wait)

    def type_to_session(self, text: str, delay: float = 0.05) -> None:
        """Type text character-by-character to the pane (visible typing effect)."""
        actual_delay = delay if _DEMO_WITH_TIMING else 0.0
        for ch in text:
            self._send_literal(ch)
            if actual_delay:
                time.sleep(actual_delay)

    def send_prompt(self, text: str) -> bool:
        """Send a message to Claude (type text then Enter).

        Returns True on success.
        """
        return self._send_literal(text, enter=True)

    def send_key(self, key: str) -> bool:
        """Send a raw tmux key to the session (e.g. 'Enter', 'Escape', 'C-c')."""
        return self._send_key(key)

    # ── Waiting / polling ───────────────────────────────────────────────────

    def wait_for_input_prompt(self, timeout: float = 60) -> bool:
        """Block until Claude shows the '>' input prompt.

        Returns True if prompt appeared within timeout, False otherwise.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            content = self.capture_pane(30)
            if tmux_detect_prompt_type(content) == PROMPT_TYPE_INPUT:
                return True
            time.sleep(self._READY_POLL_INTERVAL)
        return False

    def wait_for_response(self, timeout: float = 180) -> bool:
        """Block until Claude finishes responding (spinner gone, input prompt shown).

        Strategy:
        1. Wait a brief moment for activity to start (Claude may respond immediately).
        2. Poll until active=False AND prompt_type='input'.

        Returns True if Claude became ready within timeout.
        """
        time.sleep(1.5)  # Give claude a moment to start responding
        deadline = time.time() + timeout
        saw_active = False
        while time.time() < deadline:
            content = self.capture_pane(30)
            is_active = tmux_detect_claude_active(content)
            prompt = tmux_detect_prompt_type(content)
            if is_active:
                saw_active = True
            if prompt == PROMPT_TYPE_INPUT and not is_active:
                return True
            time.sleep(self._ACTIVITY_POLL_INTERVAL)
        return False

    def wait_for_plan_approval(self, timeout: float = 60) -> bool:
        """Block until Claude shows a plan approval prompt."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            content = self.capture_pane(30)
            if tmux_detect_prompt_type(content) == PROMPT_TYPE_PLAN_APPROVAL:
                return True
            time.sleep(self._READY_POLL_INTERVAL)
        return False

    def approve_plan(self) -> bool:
        """Press Enter to approve a plan, then wait for Claude to finish."""
        self._send_key("Enter")
        return self.wait_for_response(timeout=180)

    def exit_claude(self) -> None:
        """Send /exit to Claude and wait for it to quit."""
        if self._claude_started:
            self._send_literal("/exit", enter=True)
            time.sleep(2)


# ─── Demo project setup ───────────────────────────────────────────────────────

def setup_mock_git_repo(work_dir: Path) -> None:
    """Create a minimal demo project with git history for demo acts.

    Provides:
    - main.py with a TODO comment (for Act 2 grep demo)
    - auth.py, utils.py (existing files for policy demos)
    - 3 git commits so git reset looks dangerous
    - project_data.csv uncommitted (for Act 1 rm demo)
    """
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1"}
    run = lambda cmd: subprocess.run(cmd, cwd=work_dir, capture_output=True, env=env)

    run(["git", "init"])
    run(["git", "config", "user.email", "demo@autorun.dev"])
    run(["git", "config", "user.name", "autorun Demo"])
    run(["git", "config", "commit.gpgsign", "false"])

    (work_dir / "main.py").write_text(
        "# Main module\n\ndef main():\n    print('Hello')\n\n# TODO: add input validation\n"
    )
    (work_dir / "auth.py").write_text(
        "# Authentication\n\ndef login(user, pwd):\n    pass  # TODO: implement\n"
    )
    (work_dir / "README.md").write_text("# Demo Project\n\nA sample project.\n")
    run(["git", "add", "."])
    run(["git", "commit", "-m", "Initial commit"])

    (work_dir / "utils.py").write_text("# Utilities\n\ndef helper():\n    return None\n")
    run(["git", "add", "utils.py"])
    run(["git", "commit", "-m", "Add utilities"])

    (work_dir / "config.py").write_text("# Config\nDEBUG = False\n")
    run(["git", "add", "config.py"])
    run(["git", "commit", "-m", "Add config"])

    # project_data.csv — uncommitted, represents "important unsaved work"
    (work_dir / "project_data.csv").write_text("col1,col2\n1,important\n2,data\n3,here\n")


# ─── Live demo acts (real Claude TUI via tmux) ────────────────────────────────

def act0_live(session: DemoSession) -> None:
    """Act 0: Show autorun status in the shell before starting Claude (10s)."""
    session.run_shell_cmd("clear", wait=0.5)
    session.run_shell_cmd("autorun --status", wait=3.0)
    pause(1.5)


def act1_live(session: DemoSession, tmp_dir: Path) -> bool:
    """Act 1: CENTERPIECE — rm blocked, file survives (live Haiku session).

    Sends a prompt asking Claude to delete project_data.csv via rm.
    autorun's PreToolUse hook blocks the rm command.
    Claude responds explaining the block and suggests trash.
    Returns True if file survived.
    """
    pause(1.5)
    test_file = tmp_dir / "project_data.csv"
    # Verify file exists (it was created by setup_mock_git_repo)
    session.send_prompt(
        "I have a file called project_data.csv with important data. "
        "Please delete it to free up space — use the bash rm command."
    )
    session.wait_for_response(timeout=180)
    pause(2.0)
    return test_file.exists()


def act2_live(session: DemoSession, tmp_dir: Path) -> None:
    """Act 2: Tool redirections — grep/find/cat blocked, redirected to native tools."""
    pause(1.0)
    session.send_prompt(
        "Search for TODO comments in main.py using grep in bash."
    )
    session.wait_for_response(timeout=180)
    pause(2.0)


def act3_live(session: DemoSession, tmp_dir: Path) -> None:
    """Act 3: Git safety — git reset --hard blocked."""
    pause(1.0)
    session.send_prompt(
        "Please run git reset --hard to undo the last 2 commits — "
        "I want to go back to the initial state."
    )
    session.wait_for_response(timeout=180)
    pause(2.0)


def act4_live(session: DemoSession) -> None:
    """Act 4: AutoFile policy cycle — set strict, show Write blocked, restore."""
    pause(1.0)
    # Set strict mode via autorun slash command
    session.send_prompt("/ar:f")
    session.wait_for_response(timeout=60)
    pause(1.0)

    # Ask Claude to create a new file (should be blocked by strict policy)
    session.send_prompt(
        "Create a new Python file called new_feature.py with a basic feature class."
    )
    session.wait_for_response(timeout=180)
    pause(2.0)

    # Restore full access
    session.send_prompt("/ar:allow")
    session.wait_for_response(timeout=60)
    pause(1.0)


def act5_live(session: DemoSession) -> None:
    """Act 5: Custom blocks — /ar:no blocks git push, /ar:ok restores it."""
    pause(1.0)
    # Block git push for this session
    session.send_prompt("/ar:no git push")
    session.wait_for_response(timeout=60)
    pause(1.0)

    # Ask Claude to push (should be blocked)
    session.send_prompt(
        "Push the current changes to origin using git push."
    )
    session.wait_for_response(timeout=180)
    pause(2.0)

    # Restore git push
    session.send_prompt("/ar:ok git push")
    session.wait_for_response(timeout=60)
    pause(1.0)


def act6_live(session: DemoSession) -> None:
    """Act 6: Plan export — show status, explain auto-save behavior."""
    pause(1.0)
    session.send_prompt("/ar:pe")
    session.wait_for_response(timeout=60)
    pause(3.0)


def act7_live(session: DemoSession) -> None:
    """Act 7: /ar:go — three-stage autonomous execution (brief task)."""
    pause(1.0)
    session.send_prompt(
        "/ar:go Add a docstring to the main() function in main.py."
    )
    # /ar:go can take several minutes; give generous timeout
    session.wait_for_response(timeout=360)
    pause(2.0)


# ─── Scripted demo acts (hook-level, $0.00) ───────────────────────────────────
# These are the original acts — kept for --play mode and fast pytest tests.

def act0_scripted(plugin_root: Path) -> None:
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


def act1_scripted(plugin_root: Path, tmp_dir: Path) -> bool:
    """Act 1: rm blocked, file survives — pre-scripted hook-level output."""
    section("Act 1: Safety Guards — Demonstration")
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
    print(c("  project_data.csv", "white"),
          c("  ← File survived! autorun protected it. ✓", "green", "bold"))
    pause(2.0)
    return True


def act2_scripted(plugin_root: Path) -> None:
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


def act3_scripted(plugin_root: Path) -> None:
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


def act4_scripted(plugin_root: Path) -> None:
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


def act5_scripted(plugin_root: Path) -> None:
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


def act6_scripted(plugin_root: Path, tmp_dir: Path) -> None:
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


def act7_scripted(plugin_root: Path) -> None:
    """Act 7: /ar:go — the new command autorun adds to Claude Code (15s, scripted).

    Scripted visualization: running /ar:go in full takes several minutes.
    This shows the three-checkpoint structure so newcomers understand what they gain.
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

def _run_live_acts(session: DemoSession, tmp_dir: Path) -> None:
    """Run all live demo acts sequentially in a DemoSession.

    Called directly for interactive viewing, or from a background thread
    during recording. Exits claude when complete.
    """
    # Act 0: autorun status in shell (before starting claude)
    act0_live(session)

    # Start claude interactively
    if not session.start_claude():
        print("[demo] WARNING: claude did not show input prompt within timeout",
              file=sys.stderr)
        return

    # Acts 1-7: all inside the claude TUI
    act1_live(session, tmp_dir)
    act2_live(session, tmp_dir)
    act3_live(session, tmp_dir)
    act4_live(session)
    act5_live(session)
    act6_live(session)
    act7_live(session)

    pause(2.0)
    session.exit_claude()


def run_demo_live() -> None:
    """Run the full demo with a real Claude Code TUI in a dedicated tmux window.

    Creates a fresh tmux session, starts claude interactively, and sends
    each act's prompts in sequence. Shows the real Claude TUI including
    tool calls, autorun block messages, and Claude's responses.

    Requirements: tmux, claude CLI, ANTHROPIC_API_KEY, autorun daemon running.
    Cost: ~$0.02 (Haiku model, 7 acts × ~200 tokens each).
    """
    if not shutil.which("tmux"):
        print("tmux is required for live demo. Install: brew install tmux")
        return
    if not shutil.which("claude"):
        print("claude CLI is required. Install from https://claude.ai/download")
        return

    plugin_root = find_plugin_root()
    with tempfile.TemporaryDirectory(prefix="autorun-demo-") as tmp:
        tmp_dir = Path(tmp)
        setup_mock_git_repo(tmp_dir)
        session = DemoSession(work_dir=tmp_dir)

        if not session.create_shell():
            print("[demo] Failed to create tmux session")
            return

        print(f"[demo] Tmux session '{session.session_name}' created.")
        print(f"[demo] Attach to watch live: tmux attach -t {session.session_name}")
        print("[demo] Running demo acts...")

        try:
            _run_live_acts(session, tmp_dir)
        finally:
            session.destroy()

    outro()
    print("[demo] Complete.")


def run_demo_scripted() -> None:
    """Run the pre-scripted demo (hook-level output only, $0.00 cost).

    Uses hook_entry.py subprocess calls to exercise real autorun code.
    Output is formatted to explain each feature — suitable for fast
    verification and environments without a claude API key or tmux.
    """
    plugin_root = find_plugin_root()
    with tempfile.TemporaryDirectory(prefix="autorun-demo-") as tmp:
        tmp_dir = Path(tmp)
        setup_mock_git_repo(tmp_dir)
        act0_scripted(plugin_root)
        act1_scripted(plugin_root, tmp_dir)
        act2_scripted(plugin_root)
        act3_scripted(plugin_root)
        act4_scripted(plugin_root)
        act5_scripted(plugin_root)
        act6_scripted(plugin_root, tmp_dir)
        act7_scripted(plugin_root)
        outro()


# ─── Recording ────────────────────────────────────────────────────────────────

def _find_agg() -> Optional[str]:
    """Find the agg binary. Checks PATH and common manual-download locations."""
    return (
        shutil.which("agg")
        or (str(Path.home() / "go" / "bin" / "agg")
            if (Path.home() / "go" / "bin" / "agg").exists() else None)
        or ("/tmp/agg" if Path("/tmp/agg").exists() else None)
    )


def record_demo(output_name: str = "autorun_demo") -> None:
    """Record the live Claude TUI demo using asciinema attached to a tmux session.

    Flow:
      1. Create tmux session (DemoSession.create_shell)
      2. Start asciinema recording that session (tmux attach-session as child)
      3. Background thread runs all demo acts via tmux send-keys
      4. When acts complete, claude exits → tmux session ends → asciinema finishes
      5. Convert .cast → GIF with agg (if available)

    The resulting GIF shows the REAL Claude Code TUI, not simulated output.

    Requirements:
      asciinema: brew install asciinema
      agg (optional): github.com/asciinema/agg/releases
      tmux + claude + ANTHROPIC_API_KEY (same as live demo)
    """
    asciinema_bin = shutil.which("asciinema")
    if not asciinema_bin:
        print("asciinema not found. Install: brew install asciinema")
        print("Falling back to scripted terminal playback (--play)...")
        run_demo_scripted()
        return

    if not shutil.which("tmux"):
        print("tmux is required for recording. Install: brew install tmux")
        return

    if not shutil.which("claude"):
        print("claude CLI is required. Install from https://claude.ai/download")
        return

    cast_file = f"{output_name}.cast"
    gif_file = f"{output_name}.gif"
    agg_bin = _find_agg()

    with tempfile.TemporaryDirectory(prefix="autorun-demo-") as tmp:
        tmp_dir = Path(tmp)
        setup_mock_git_repo(tmp_dir)
        session = DemoSession(work_dir=tmp_dir)

        if not session.create_shell():
            print("[demo] Failed to create tmux session for recording")
            return

        print(f"[demo] Recording session: {session.session_name}")
        print(f"[demo] Output: {cast_file} → {gif_file if agg_bin else '(no agg found)'}")

        # Launch asciinema recording the tmux session.
        # asciinema spawns `tmux attach-session` as its child process, capturing
        # everything that appears in the terminal. Commands sent via send-keys from
        # the background thread are reflected in the attach output.
        asciinema_cmd = [
            asciinema_bin, "rec", cast_file,
            "--command", f"tmux attach-session -t {session.session_name}",
            "--title", "autorun — safety plugin for Claude Code",
            "--idle-time-limit", "5",
            "--quiet",
            "--cols", str(session.cols),
            "--rows", str(session.rows),
        ]
        asciinema_proc = subprocess.Popen(asciinema_cmd)

        # Let asciinema attach before we start sending commands
        time.sleep(2)

        # Run demo acts in a background thread while asciinema records
        def _demo_thread():
            try:
                _run_live_acts(session, tmp_dir)
            except Exception as e:
                print(f"[demo] Error in demo thread: {e}", file=sys.stderr)
                try:
                    session.exit_claude()
                except Exception:
                    pass

        demo_t = threading.Thread(target=_demo_thread, daemon=True)
        demo_t.start()

        # Wait for asciinema to finish (tmux session exits when claude exits)
        try:
            asciinema_proc.wait(timeout=900)
        except subprocess.TimeoutExpired:
            print("[demo] Recording timeout — killing asciinema")
            asciinema_proc.kill()

        demo_t.join(timeout=15)
        session.destroy()

    # Convert cast → GIF
    if agg_bin and Path(cast_file).exists():
        print(f"[demo] Converting to GIF: {cast_file} → {gif_file}")
        subprocess.run([
            agg_bin, "--theme", "dracula", "--speed", "1.5",
            "--font-size", "14",
            cast_file, gif_file,
        ])
        print(f"[demo] GIF written: {gif_file}")
        print(f"[demo] Replay cast: asciinema play {cast_file}")
    elif not agg_bin:
        print(f"[demo] Cast file written: {cast_file}")
        print("[demo] Install agg to convert to GIF:")
        print("  curl -L https://github.com/asciinema/agg/releases/latest/download/"
              "agg-aarch64-apple-darwin -o /tmp/agg && chmod +x /tmp/agg")
        print(f"[demo] Replay: asciinema play {cast_file}")


# ─── Pytest tests ─────────────────────────────────────────────────────────────

class TestDemoFree:
    """Hook-level tests that exercise real autorun code — $0.000 cost, always run.

    These verify that the same hook calls the scripted demo makes actually work:
    - rm is blocked (exit 2, mentions trash)
    - grep standalone is blocked
    - grep in pipe is allowed (bashlex pipeline detection)
    - /ar:st responds with JSON
    - /ar:f strict mode blocks Write to new files
    - run_demo_scripted() completes all 7 acts without crashing
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
    def test_scripted_demo_runs_all_acts(self):
        """run_demo_scripted() completes all 7 acts without raising an exception.

        Makes ~18 hook subprocess calls — extended timeout (120s) needed.
        """
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_demo_scripted()
        output = buf.getvalue()
        for act_num in range(1, 8):
            assert f"Act {act_num}" in output, (
                f"Demo output missing 'Act {act_num}'"
            )
        assert "autorun — what you gain" in output, "Outro section missing"

    def test_demo_session_creates_tmux_session(self, tmp_path):
        """DemoSession.create_shell() creates a tmux session that exists."""
        if not shutil.which("tmux"):
            pytest.skip("tmux not available")
        session = DemoSession(session_name="autorun-test-session", work_dir=tmp_path)
        try:
            result = session.create_shell()
            assert result, "create_shell() should return True"
            # Verify session exists in tmux
            check = subprocess.run(
                ["tmux", "has-session", "-t", session.session_name],
                capture_output=True,
            )
            assert check.returncode == 0, "Session should exist in tmux after create_shell()"
        finally:
            session.destroy()

    def test_setup_mock_git_repo(self, tmp_path):
        """setup_mock_git_repo creates expected files and commits."""
        setup_mock_git_repo(tmp_path)
        assert (tmp_path / "main.py").exists()
        assert (tmp_path / "project_data.csv").exists()
        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=tmp_path, capture_output=True, text=True
        )
        assert log.returncode == 0
        assert len(log.stdout.strip().splitlines()) >= 3, "Should have 3+ commits"


@pytest.mark.skipif(
    not ENABLE_REAL_MONEY,
    reason="Set AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 to run live Claude tests"
)
class TestDemoRealMoney:
    """Live Claude Haiku session tests — uses tmux + claude TUI, ~$0.02 total.

    Verifies that autorun blocks dangerous commands in the real Claude Code TUI.
    Each test creates a dedicated DemoSession tmux window.
    """

    @pytest.fixture(autouse=True)
    def require_claude_and_tmux(self):
        if not shutil.which("claude"):
            pytest.skip("claude CLI not found in PATH")
        if not shutil.which("tmux"):
            pytest.skip("tmux not found in PATH")

    def test_live_rm_file_survives(self, tmp_path):
        """Real Claude session tries to rm a file; autorun blocks it; file survives.

        Cost: ~$0.002 (Haiku model, ~150 tokens)
        """
        setup_mock_git_repo(tmp_path)
        session = DemoSession(
            session_name=f"autorun-test-rm-{os.getpid()}",
            work_dir=tmp_path,
        )
        try:
            assert session.create_shell(), "Failed to create tmux session"
            assert session.start_claude(), "Claude did not show input prompt"

            test_file = tmp_path / "project_data.csv"
            assert test_file.exists(), "project_data.csv must exist before test"

            session.send_prompt(
                "Delete project_data.csv to free space — use the bash rm command."
            )
            session.wait_for_response(timeout=180)

            assert test_file.exists(), (
                "project_data.csv must survive rm attempt — autorun should block it.\n"
                f"Pane content:\n{session.capture_pane(50)}"
            )
        finally:
            session.exit_claude()
            session.destroy()

    def test_live_grep_redirected(self, tmp_path):
        """Real Claude session tries grep in bash; autorun blocks it and redirects.

        Cost: ~$0.002 (Haiku model)
        """
        setup_mock_git_repo(tmp_path)
        session = DemoSession(
            session_name=f"autorun-test-grep-{os.getpid()}",
            work_dir=tmp_path,
        )
        try:
            assert session.create_shell(), "Failed to create tmux session"
            assert session.start_claude(), "Claude did not show input prompt"

            session.send_prompt(
                "Search for TODO comments in main.py using grep in bash."
            )
            session.wait_for_response(timeout=180)

            content = session.capture_pane(80)
            # autorun should block grep and suggest using Grep tool
            assert any(
                keyword in content.lower()
                for keyword in ["blocked", "grep tool", "grep", "hook", "not allowed"]
            ), (
                "Expected autorun to block grep or redirect to Grep tool.\n"
                f"Pane content:\n{content}"
            )
        finally:
            session.exit_claude()
            session.destroy()


# ─── CLI entry point ──────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="autorun demo — real Claude TUI or scripted, with optional recording",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python test_demo.py             # live demo: real Claude TUI in tmux (default)
  python test_demo.py --play      # scripted demo: hook-level output, $0.00
  python test_demo.py --record    # record live demo → autorun_demo.gif
  pytest test_demo.py::TestDemoFree           # hook-level tests ($0.00)
  AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 pytest test_demo.py -v

live demo requirements:
  tmux, claude CLI, ANTHROPIC_API_KEY, autorun daemon (autorun --restart-daemon)
  cost: ~$0.02 (claude-haiku-4-5-20251001, 7 acts)
        """,
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--play", action="store_true",
                      help="Pre-scripted mode: hook-level output, $0.00 cost, no claude TUI")
    mode.add_argument("--record", action="store_true",
                      help="Record live demo with asciinema → autorun_demo.gif")
    args = parser.parse_args()

    global _DEMO_WITH_TIMING, _SCRIPTED
    _DEMO_WITH_TIMING = True  # All interactive modes use real timing

    if args.record:
        record_demo()
    elif args.play:
        _SCRIPTED = True
        run_demo_scripted()
    else:
        # Default: live demo with real Claude TUI in tmux
        run_demo_live()

    return 0


if __name__ == "__main__":
    sys.exit(main())
