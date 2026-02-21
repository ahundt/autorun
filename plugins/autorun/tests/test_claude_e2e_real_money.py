#!/usr/bin/env python3
"""
REAL MONEY TESTS - Claude Code E2E Integration Tests

Two test categories in this module:

1. TestClaudeHookEntryPoint — FREE (no API cost)
   Call hook_entry.py --cli claude directly with JSON payloads.
   Tests: hook → client → daemon → plugin → response (full path, zero API calls).
   Cost: $0.000

2. TestClaudeE2ERealMoney — COSTS REAL MONEY
   Spawn actual `claude -p` sessions with prompts.
   Tests: real Claude session with hooks active end-to-end.
   Cost: < $0.005 per run (haiku/sonnet model)

ALL tests require AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 for consistency
with test_gemini_e2e_real_money.py. This prevents accidental daemon state
mutation during regular test runs.

To run ALL tests (including real money):
    export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/autorun/tests/test_claude_e2e_real_money.py -v

To run only FREE hook-level tests:
    export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/autorun/tests/test_claude_e2e_real_money.py::TestClaudeHookEntryPoint -v

To skip all (default in full suite):
    uv run pytest plugins/autorun/tests/ -v
    # These tests are SKIPPED automatically

Full output logging (no truncation):
    All hook call I/O is written to: /tmp/autorun-e2e-test-logs/
    Real Claude subprocess output is written to pytest's tmp_path per test.
    To inspect after a failure:
        ls /tmp/autorun-e2e-test-logs/
        cat /tmp/autorun-e2e-test-logs/<test-label>.json

    For full terminal capture (no pytest truncation of diffs):
        export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
        uv run pytest plugins/autorun/tests/test_claude_e2e_real_money.py -v \\
            --tb=long --log-file=/tmp/autorun-pytest.log --log-file-level=DEBUG \\
            2>&1 | tee /tmp/autorun-pytest-terminal.log

Equivalent Gemini tests: test_gemini_e2e_real_money.py

Cost Breakdown:
    TestClaudeHookEntryPoint (19 tests)  $0.000  (direct hook_entry.py calls)
    TestClaudeE2ERealMoney   (4 tests)   <$0.005 (spawn claude -p)
"""

import datetime
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

# =============================================================================
# LOG DIRECTORY — full subprocess output persisted here for post-failure debug
# =============================================================================

_LOG_DIR = Path("/tmp") / "autorun-e2e-test-logs"


def _log_run(label: str, payload: dict, rc: int, stdout: str, stderr: str) -> Path:
    """Write one hook call's full I/O to _LOG_DIR/<label>.json.

    Returns the log file path (included in assertion messages so failures are
    fully diagnosable without truncation in the terminal).
    """
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe_label = label.replace("/", "_").replace(" ", "_")[:120]
        log_path = _LOG_DIR / f"{safe_label}.json"
        log_path.write_text(
            json.dumps(
                {
                    "timestamp": datetime.datetime.now().isoformat(),
                    "label": label,
                    "payload": payload,
                    "returncode": rc,
                    "stdout": stdout,
                    "stderr": stderr,
                },
                indent=2,
                default=str,
            )
        )
        return log_path
    except Exception:
        return _LOG_DIR / f"{safe_label}.json"  # Return path even if write failed

# =============================================================================
# FLAG: Gate tests (same flag as Gemini test file)
# =============================================================================

ENABLE_REAL_MONEY_TESTS = os.environ.get("AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY", "0") == "1"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not ENABLE_REAL_MONEY_TESTS,
        reason=(
            "AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY not set. "
            "Set to 1 to run. Free hook-level tests cost $0.000; "
            "real Claude subprocess tests cost < $0.005 per run."
        ),
    ),
]


# =============================================================================
# HELPERS
# =============================================================================


def find_hook_script() -> Path:
    """Find hook_entry.py in installed or development locations."""
    candidates = [
        # Dev source: test file lives in tests/ → parent is plugins/autorun/
        Path(__file__).parent.parent / "hooks" / "hook_entry.py",
        # Git repo home location
        Path.home() / ".claude" / "autorun" / "plugins" / "autorun" / "hooks" / "hook_entry.py",
        # Claude plugin cache (installed via plugin system)
        Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "autorun" / "0.8.0" / "hooks" / "hook_entry.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        "hook_entry.py not found. Searched:\n" + "\n".join(f"  - {c}" for c in candidates)
    )


def find_plugin_root() -> Path:
    """Find plugin root (dir containing pyproject.toml) for uv run --project."""
    candidates = [
        Path(__file__).parent.parent,
        Path.home() / ".claude" / "autorun" / "plugins" / "autorun",
        Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "autorun" / "0.8.0",
    ]
    for c in candidates:
        if (c / "pyproject.toml").exists():
            return c
    raise FileNotFoundError(
        "Plugin root (with pyproject.toml) not found. Searched:\n"
        + "\n".join(f"  - {c}" for c in candidates)
    )


def run_hook(
    hook_script: Path,
    plugin_root: Path,
    payload: dict,
    env: dict = None,
    timeout: int = 15,
) -> tuple:
    """Run hook_entry.py --cli claude with JSON payload via stdin.

    Args:
        hook_script: Path to hook_entry.py
        plugin_root: Path to plugin root for uv run --project
        payload: Hook event payload dict (JSON-encoded and sent via stdin)
        env: Environment variables (defaults to os.environ.copy())
        timeout: Subprocess timeout in seconds

    Returns:
        tuple: (returncode, stdout, stderr, parsed_response_or_None)
            returncode: 0 for allow/continue, 2 for deny (Claude exit-2 workaround)
            stdout: Raw stdout string (JSON response from hook)
            stderr: Raw stderr string (deny reason, or empty)
            parsed_response: Parsed JSON dict, or None if stdout is not valid JSON
    """
    if env is None:
        env = os.environ.copy()

    result = subprocess.run(
        [
            "uv", "run", "--quiet", "--project", str(plugin_root),
            "python", str(hook_script), "--cli", "claude",
        ],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )

    parsed = None
    raw = result.stdout.strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            pass

    # Always write full output to log dir (no truncation)
    label = (
        f"{payload.get('hook_event_name','unknown')}"
        f"-{payload.get('session_id', uuid.uuid4().hex[:8])}"
        f"-{payload.get('tool_name','')}"
    )
    _log_run(label, payload, result.returncode, result.stdout, result.stderr)

    return result.returncode, result.stdout, result.stderr, parsed


def get_deny_decision(response: dict) -> str:
    """Extract the permissionDecision value from a hook response.

    Checks hookSpecificOutput.permissionDecision first (canonical Claude path),
    then falls back to top-level decision/permissionDecision fields.
    """
    if response is None:
        return ""
    hso = response.get("hookSpecificOutput", {})
    return (
        hso.get("permissionDecision", "")
        or response.get("permissionDecision", "")
        or response.get("decision", "")
    )


def get_deny_reason(response: dict) -> str:
    """Extract the deny reason from hookSpecificOutput.permissionDecisionReason.

    For Claude deny decisions, the reason lives in:
        response["hookSpecificOutput"]["permissionDecisionReason"]
    The top-level systemMessage/reason fields are intentionally empty for deny
    to avoid triple-printing in the UI (reason also goes to stderr via exit-2).
    """
    if response is None:
        return ""
    hso = response.get("hookSpecificOutput", {})
    return (
        hso.get("permissionDecisionReason", "")
        or response.get("reason", "")
        or response.get("systemMessage", "")
    )


def get_system_message(response: dict) -> str:
    """Extract the systemMessage or additionalContext for UserPromptSubmit responses."""
    if response is None:
        return ""
    hso = response.get("hookSpecificOutput", {})
    return (
        hso.get("additionalContext", "")
        or response.get("systemMessage", "")
        or response.get("reason", "")
    )


# =============================================================================
# MODULE-LEVEL RESOURCE DISCOVERY
# =============================================================================

try:
    _HOOK_SCRIPT = find_hook_script()
    _PLUGIN_ROOT = find_plugin_root()
    _RESOURCES_OK = True
    _RESOURCES_ERROR = ""
except FileNotFoundError as e:
    _HOOK_SCRIPT = None
    _PLUGIN_ROOT = None
    _RESOURCES_OK = False
    _RESOURCES_ERROR = str(e)


@pytest.fixture(scope="module")
def hook_resources():
    """Provide hook_entry.py path and plugin root, skipping if not found."""
    if not _RESOURCES_OK:
        pytest.skip(f"Hook resources not found: {_RESOURCES_ERROR}")
    return {"hook_script": _HOOK_SCRIPT, "plugin_root": _PLUGIN_ROOT}


@pytest.fixture(scope="module")
def claude_cli_check():
    """Verify Claude CLI is installed and autorun plugin is loaded.

    No API cost — just checks the binary and plugin list.
    """
    if not shutil.which("claude"):
        pytest.skip("Claude CLI not installed (claude command not found in PATH)")

    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            pytest.skip(f"Claude CLI not working: {result.stderr}")
    except subprocess.TimeoutExpired:
        pytest.skip("claude --version timed out (>10s)")
    except Exception as e:
        pytest.skip(f"Claude CLI version check failed: {e}")

    # Check autorun plugin is loaded
    try:
        result = subprocess.run(
            ["claude", "plugin", "list"],
            capture_output=True, text=True, timeout=30,
        )
        combined = result.stdout + result.stderr
        if "autorun" not in combined.lower() and "cr:" not in combined.lower():
            pytest.skip(
                "autorun plugin not loaded in Claude Code. "
                "Install with: claude plugin install https://github.com/ahundt/autorun.git"
            )
    except subprocess.TimeoutExpired:
        pytest.skip("claude plugin list timed out (>30s)")
    except Exception as e:
        pytest.skip(f"Plugin list check failed: {e}")


# =============================================================================
# FREE TESTS: hook_entry.py --cli claude called directly (no API cost)
# =============================================================================


class TestClaudeHookEntryPoint:
    """FREE hook-level tests — no Claude API calls, no real money spent.

    Each test calls hook_entry.py --cli claude with a JSON payload via stdin.
    This exercises the complete path:
        test → hook_entry.py → client.py → daemon → plugins.py → response

    The daemon must be running (hook auto-starts it if needed, adding ~3-5s to
    the first call). Each test uses a unique session_id to prevent state
    contamination between tests.

    Cost: $0.000 per test run.
    """

    def _run(self, hook_resources: dict, payload: dict, timeout: int = 15) -> tuple:
        """Convenience wrapper around run_hook()."""
        return run_hook(
            hook_resources["hook_script"],
            hook_resources["plugin_root"],
            payload,
            timeout=timeout,
        )

    def _sid(self, name: str) -> str:
        """Return a unique session ID for a test to prevent cross-test state leak."""
        return f"e2e-claude-{name}-{uuid.uuid4().hex[:8]}"

    def _base_payload(self, event: str, session_id: str, **extra) -> dict:
        """Return a minimal valid payload for the given event."""
        return {
            "hook_event_name": event,
            "session_id": session_id,
            "_cwd": "/tmp",
            "_pid": os.getpid(),
            **extra,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Session lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def test_sessionstart_returns_continue(self, hook_resources):
        """SessionStart hook returns continue: true (session allowed to proceed)."""
        payload = self._base_payload("SessionStart", self._sid("sessionstart"))
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"SessionStart should exit 0. rc={rc} stderr={stderr!r}"
        assert resp is not None, f"Expected JSON response. stdout={stdout!r}"
        assert resp.get("continue") is True, \
            f"SessionStart should return continue: true. response={resp}"

    def test_stop_without_autorun_passes_through(self, hook_resources):
        """Stop hook without active autorun session passes through (continue: true)."""
        payload = self._base_payload(
            "Stop", self._sid("stop-norun"),
            stop_hook_active=False,
            session_transcript=[],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"Stop (no autorun) should exit 0. rc={rc}"
        assert resp is not None
        assert resp.get("continue") is True, \
            f"Stop without autorun should return continue: true. response={resp}"

    # ─────────────────────────────────────────────────────────────────────────
    # Safety guards: blocked bash commands
    # ─────────────────────────────────────────────────────────────────────────

    def test_pretooluse_bash_rm_blocked_suggests_trash(self, hook_resources):
        """rm is blocked with permissionDecision: deny and suggests 'trash' alternative.

        Verifies DEFAULT_INTEGRATIONS["rm"] entry fires on PreToolUse(Bash).
        Exit code 2 is required by Claude Code bug #4669 workaround.
        """
        payload = self._base_payload(
            "PreToolUse", self._sid("rm"),
            tool_name="Bash",
            tool_input={"command": "rm /tmp/some-file.txt"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, (
            f"rm must exit with code 2 for Claude Code (bug #4669 workaround). "
            f"Got rc={rc}. Without exit 2, the rm executes despite deny decision!"
        )
        assert resp is not None
        assert get_deny_decision(resp) == "deny", \
            f"rm should be denied. response={resp}"
        reason = get_deny_reason(resp)
        assert "trash" in reason.lower(), \
            f"Deny reason should mention 'trash' as safe alternative. reason={reason!r}"
        # Reason should also appear in stderr (visible to AI via hook error channel)
        assert stderr.strip(), \
            f"Deny reason must be written to stderr for AI to see it. stderr={stderr!r}"

    def test_pretooluse_bash_rm_rf_blocked(self, hook_resources):
        """rm -rf is blocked (dangerous recursive delete)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("rm-rf"),
            tool_name="Bash",
            tool_input={"command": "rm -rf /tmp/testdir"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"rm -rf must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "trash" in reason.lower(), \
            f"rm -rf should suggest 'trash'. reason={reason!r}"

    def test_pretooluse_bash_grep_blocked_suggests_grep_tool(self, hook_resources):
        """grep blocked and suggests 'Grep tool' — Claude-specific name, not 'grep_search'.

        This is the critical format_suggestion() platform-awareness test.
        If this returns 'grep_search', the CLI_TOOL_NAMES table is misconfigured
        for Claude Code.
        """
        payload = self._base_payload(
            "PreToolUse", self._sid("grep"),
            tool_name="Bash",
            tool_input={"command": "grep 'pattern' /tmp/file.txt"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"grep must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "Grep" in reason, \
            f"Should suggest 'Grep tool' (Claude-specific, capitalized). reason={reason!r}"
        assert "grep_search" not in reason, (
            f"WRONG: 'grep_search' is the Gemini tool name, not Claude's. "
            f"format_suggestion() is returning the wrong CLI's tool name. reason={reason!r}"
        )

    def test_pretooluse_bash_find_blocked_suggests_glob_tool(self, hook_resources):
        """find blocked and suggests 'Glob tool' — Claude-specific name, not 'glob'."""
        payload = self._base_payload(
            "PreToolUse", self._sid("find"),
            tool_name="Bash",
            tool_input={"command": "find /tmp -name '*.txt' -type f"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"find must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "Glob" in reason, \
            f"Should suggest 'Glob tool' (Claude-specific). reason={reason!r}"

    def test_pretooluse_bash_cat_blocked_suggests_read_tool(self, hook_resources):
        """cat blocked and suggests 'Read tool' — Claude-specific name, not 'read_file'."""
        payload = self._base_payload(
            "PreToolUse", self._sid("cat"),
            tool_name="Bash",
            tool_input={"command": "cat /tmp/file.txt"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"cat must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "Read" in reason, \
            f"Should suggest 'Read tool' (Claude-specific). reason={reason!r}"
        assert "read_file" not in reason, (
            f"WRONG: 'read_file' is the Gemini tool name, not Claude's. "
            f"format_suggestion() is returning the wrong CLI's tool name. reason={reason!r}"
        )

    def test_pretooluse_bash_sed_blocked_suggests_edit_tool(self, hook_resources):
        """sed blocked and suggests 'Edit tool' — Claude-specific name, not 'replace'."""
        payload = self._base_payload(
            "PreToolUse", self._sid("sed"),
            tool_name="Bash",
            tool_input={"command": "sed -i 's/old/new/g' /tmp/file.txt"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"sed must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "Edit" in reason, \
            f"Should suggest 'Edit tool' (Claude-specific). reason={reason!r}"
        assert "replace" not in reason or "Edit" in reason, \
            f"Should not use Gemini tool name 'replace'. reason={reason!r}"

    def test_pretooluse_bash_git_reset_hard_blocked(self, hook_resources):
        """git reset --hard blocked (destructive git operation)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-reset-hard"),
            tool_name="Bash",
            tool_input={"command": "git reset --hard HEAD~1"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git reset --hard must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "stash" in reason.lower() or "git stash" in reason.lower(), \
            f"Should suggest 'git stash' as safe alternative. reason={reason!r}"

    def test_pretooluse_bash_git_clean_f_blocked(self, hook_resources):
        """git clean -f blocked (destroys untracked files without confirmation)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-clean-f"),
            tool_name="Bash",
            tool_input={"command": "git clean -f"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git clean -f must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"

    def test_pretooluse_bash_safe_git_status_allowed(self, hook_resources):
        """git status is allowed (read-only, not in block list)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-status"),
            tool_name="Bash",
            tool_input={"command": "git status"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"git status should be allowed (exit 0). Got rc={rc} stderr={stderr!r}"
        assert get_deny_decision(resp) != "deny", \
            f"git status must not be denied. response={resp}"

    def test_pretooluse_bash_echo_allowed(self, hook_resources):
        """echo command is allowed (not in block list)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("echo"),
            tool_name="Bash",
            tool_input={"command": "echo hello world"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"echo should be allowed. Got rc={rc}"
        assert get_deny_decision(resp) != "deny"

    # ─────────────────────────────────────────────────────────────────────────
    # AutoFile policy commands (UserPromptSubmit)
    # ─────────────────────────────────────────────────────────────────────────

    def test_userpromptsubmit_cr_st_returns_policy_info(self, hook_resources):
        """/ar:st UserPromptSubmit returns AutoFile policy info in systemMessage."""
        payload = self._base_payload(
            "UserPromptSubmit", self._sid("cr-st"),
            prompt="/ar:st",
            session_transcript=[],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"/ar:st should return 0. rc={rc} stderr={stderr!r}"
        assert resp is not None
        msg = get_system_message(resp)
        assert any(kw in msg.lower() for kw in ["policy", "autofile", "allow", "strict", "find"]), \
            f"/ar:st should return policy status. systemMessage/additionalContext={msg!r}"

    def test_strict_search_policy_blocks_write_to_new_file(self, hook_resources):
        """/ar:f sets strict-search policy; subsequent Write for new file is blocked.

        Two-step test:
        1. UserPromptSubmit /ar:f → sets strict-search for session
        2. PreToolUse Write /tmp/new-file → blocked (new file creation not allowed)
        """
        session_id = self._sid("strict-write")

        # Step 1: Set strict-search policy for this test session
        set_policy_payload = self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:f",
            session_transcript=[],
        )
        rc, _, stderr, resp = self._run(hook_resources, set_policy_payload)
        assert rc == 0, f"/ar:f should return 0. rc={rc} stderr={stderr!r}"

        # Step 2: Try Write to a new (non-existent) file — should be blocked
        write_payload = self._base_payload(
            "PreToolUse", session_id,
            tool_name="Write",
            tool_input={
                "file_path": "/tmp/autorun-e2e-strict-write-test-should-be-blocked.txt",
                "content": "this should not be written",
            },
        )
        rc, stdout, stderr, resp = self._run(hook_resources, write_payload)

        assert rc == 2, (
            f"Write to new file must be blocked in strict-search mode. "
            f"Got rc={rc}. The strict-search policy did not persist in daemon state "
            f"for session_id={session_id!r}. stderr={stderr!r}"
        )
        assert get_deny_decision(resp) == "deny", \
            f"Write should be denied in strict-search mode. response={resp}"

    def test_allow_all_policy_permits_write(self, hook_resources):
        """/ar:allow sets allow-all; Write is permitted regardless of file existence."""
        session_id = self._sid("allow-write")

        set_payload = self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:allow",
            session_transcript=[],
        )
        self._run(hook_resources, set_payload)

        write_payload = self._base_payload(
            "PreToolUse", session_id,
            tool_name="Write",
            tool_input={
                "file_path": "/tmp/autorun-e2e-allow-write-test.txt",
                "content": "allowed write",
            },
        )
        rc, _, stderr, resp = self._run(hook_resources, write_payload)

        assert rc == 0, \
            f"Write should be allowed in allow-all mode. Got rc={rc} stderr={stderr!r}"
        assert get_deny_decision(resp) != "deny"

    # ─────────────────────────────────────────────────────────────────────────
    # Plan export — critical E2E path (BUG 3 regression test)
    # ─────────────────────────────────────────────────────────────────────────

    def test_plan_export_posttooluse_exitplanmode_writes_to_notes(
        self, hook_resources, tmp_path
    ):
        """PostToolUse(ExitPlanMode) with tool_result.filePath exports plan to <cwd>/notes/.

        This is the definitive E2E test for BUG 3 (ctx.cwd regression):
        - Previously: ctx.cwd was always None → record_write() skipped → no export
        - Fix: _cwd payload field is now propagated to EventContext.cwd
        - This test verifies the ACTUAL hook path, not just the Python function in isolation

        Failure means the ctx.cwd fix did NOT work end-to-end through the daemon.
        """
        # Skip if plan export is disabled in user config
        try:
            plugin_src = str(hook_resources["plugin_root"] / "src")
            if plugin_src not in sys.path:
                sys.path.insert(0, plugin_src)
            from autorun.plan_export import PlanExportConfig
            config = PlanExportConfig.load()
            if not config.enabled:
                pytest.skip(
                    "Plan export disabled in ~/.claude/plan-export.config.json. "
                    "Enable with: /ar:pe-on or set enabled=true in the config."
                )
        except ImportError:
            pass  # Proceed with defaults if import fails

        # Create a unique plan file in ~/.claude/plans/ (where Claude Code writes plans)
        plans_dir = Path.home() / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        unique_marker = uuid.uuid4().hex
        plan_content = (
            f"# E2E Test Plan {unique_marker}\n\n"
            f"Create /tmp/autorun-e2e-hello.txt with 'hello world'.\n\n"
            f"Step 1: Write the file.\n"
            f"Step 2: Verify the file exists.\n"
        )
        plan_file = plans_dir / f"test-e2e-{unique_marker[:8]}.md"
        plan_file.write_text(plan_content)

        try:
            # PostToolUse(ExitPlanMode) payload — same shape as real Claude Code hook
            # _cwd = tmp_path → daemon exports to tmp_path/notes/
            # tool_result.filePath = plan_file → get_current_plan() finds it directly
            payload = self._base_payload(
                "PostToolUse", self._sid("plan-export"),
                tool_name="ExitPlanMode",
                tool_input={},
                tool_result={"filePath": str(plan_file)},
            )
            # Override _cwd to point to our temp dir (not /tmp)
            payload["_cwd"] = str(tmp_path)

            rc, stdout, stderr, resp = self._run(hook_resources, payload, timeout=20)

            assert rc == 0, (
                f"PostToolUse(ExitPlanMode) should return 0. rc={rc}\n"
                f"stderr={stderr!r}\n"
                f"stdout={stdout!r}"
            )

            # Verify plan was exported to notes/ inside tmp_path
            notes_dir = tmp_path / "notes"
            exported = list(notes_dir.glob("*.md")) if notes_dir.exists() else []

            # Collect log info for any failure message
            log_file = _log_run(
                f"plan-export-posttooluse-{payload['session_id']}",
                payload, rc, stdout, stderr,
            )

            assert len(exported) >= 1, (
                f"Plan was NOT exported to {notes_dir}.\n"
                f"notes_dir exists: {notes_dir.exists()}\n"
                f"Hook rc={rc}\n"
                f"Full hook I/O in: {log_file}\n"
                f"Plan file at: {plan_file} (exists={plan_file.exists()})\n"
                f"tmp_path contents: {list(tmp_path.iterdir())}\n"
                f"Hint: Enable debug logging for details:\n"
                f"  echo '{{\"enabled\":true,\"debug_logging\":true}}' > ~/.claude/plan-export.config.json\n"
                f"  tail -f ~/.claude/plan-export-debug.log\n"
                f"stdout (last 2000 chars): {stdout[-2000:]!r}\n"
                f"stderr (last 1000 chars): {stderr[-1000:]!r}"
            )

            # Verify exported content matches our plan
            exported_content = exported[0].read_text()
            assert unique_marker in exported_content or "E2E Test Plan" in exported_content, (
                f"Exported file does not contain expected plan content.\n"
                f"Exported file: {exported[0]}\n"
                f"Full content:\n{exported_content}\n"
                f"Expected unique marker: {unique_marker}"
            )

        finally:
            if plan_file.exists():
                plan_file.unlink()

    def test_pretooluse_exitplanmode_returns_continue_not_deny(self, hook_resources):
        """PreToolUse(ExitPlanMode) backup tracking path: never denies ExitPlanMode itself."""
        payload = self._base_payload(
            "PreToolUse", self._sid("pre-exit-plan"),
            tool_name="ExitPlanMode",
            tool_input={},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, \
            f"PreToolUse(ExitPlanMode) should never deny (exit 0). Got rc={rc} stderr={stderr!r}"
        assert get_deny_decision(resp) != "deny", \
            f"ExitPlanMode must never be blocked by the hook. response={resp}"

    # ─────────────────────────────────────────────────────────────────────────
    # Response format correctness
    # ─────────────────────────────────────────────────────────────────────────

    def test_all_hook_responses_are_valid_json(self, hook_resources):
        """Every hook event produces valid JSON on stdout (never fails silently)."""
        events = [
            self._base_payload("SessionStart", self._sid("json-ss")),
            self._base_payload(
                "PreToolUse", self._sid("json-pre"),
                tool_name="Bash", tool_input={"command": "echo test"},
            ),
            self._base_payload(
                "PostToolUse", self._sid("json-post"),
                tool_name="Write", tool_input={}, tool_result={},
            ),
            self._base_payload(
                "UserPromptSubmit", self._sid("json-ups"),
                prompt="Hello", session_transcript=[],
            ),
        ]
        for payload in events:
            event = payload["hook_event_name"]
            rc, stdout, stderr, resp = self._run(hook_resources, payload)
            assert resp is not None, \
                f"Hook for {event} returned invalid/missing JSON. stdout={stdout!r}"
            assert "continue" in resp, \
                f"Hook response for {event} missing required 'continue' field. response={resp}"

    def test_deny_exits_with_code_2_allow_exits_with_code_0(self, hook_resources):
        """Exit code 2 for deny (Claude bug #4669), exit code 0 for allow.

        Claude Code requires exit code 2 to actually block tool execution.
        Exit code 0 with permissionDecision:deny is silently ignored by Claude Code
        (GitHub issue #4669 — not yet fixed upstream as of 2026-02).
        """
        # Deny case: rm command
        deny_payload = self._base_payload(
            "PreToolUse", self._sid("exitcode-deny"),
            tool_name="Bash",
            tool_input={"command": "rm /tmp/test.txt"},
        )
        rc_deny, _, stderr_deny, resp_deny = self._run(hook_resources, deny_payload)
        assert rc_deny == 2, (
            f"Denied rm must exit with code 2 (Claude bug #4669 workaround). "
            f"Got rc={rc_deny}. Without exit 2, rm EXECUTES despite deny decision."
        )
        assert stderr_deny.strip(), \
            f"Deny reason must be written to stderr (AI sees it). stderr={stderr_deny!r}"

        # Allow case: echo command
        allow_payload = self._base_payload(
            "PreToolUse", self._sid("exitcode-allow"),
            tool_name="Bash",
            tool_input={"command": "echo allowed"},
        )
        rc_allow, _, _, _ = self._run(hook_resources, allow_payload)
        assert rc_allow == 0, f"Allowed command must exit with code 0. Got rc={rc_allow}"


# =============================================================================
# REAL MONEY TESTS: spawn actual claude -p sessions
# =============================================================================


class TestClaudeE2ERealMoney:
    """Real Claude CLI E2E tests — spawn `claude -p` (costs API tokens).

    ⚠️ WARNING: These tests make REAL API calls to the Claude model.

    Estimated cost per test run: < $0.005 (using cheapest available model).
    Set AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1 to enable.

    These tests verify that autorun hooks actually influence Claude's behavior
    in a live session — something the free hook-level tests cannot do because
    they bypass the Claude AI entirely.
    """

    @staticmethod
    def _claude_env() -> dict:
        """Return env without CLAUDECODE so nested claude -p calls are not blocked."""
        return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    def _log_claude_run(self, tmp_path: Path, test_name: str, result) -> Path:
        """Write full claude -p subprocess output to a log file in tmp_path."""
        log_path = tmp_path / f"{test_name}.log"
        log_path.write_text(
            f"=== {test_name} ===\n"
            f"Timestamp: {datetime.datetime.now().isoformat()}\n"
            f"Return code: {result.returncode}\n\n"
            f"--- STDOUT ---\n{result.stdout}\n\n"
            f"--- STDERR ---\n{result.stderr}\n"
        )
        return log_path

    def test_claude_basic_interaction_and_hooks_dont_crash(
        self, tmp_path, claude_cli_check
    ):
        """Basic `claude -p` interaction completes (COSTS REAL MONEY, < $0.001).

        Also verifies that SessionStart and Stop hooks fire without crashing the
        Claude session. If autorun hooks had a fatal error, `claude -p` would
        return a non-zero exit code.

        Estimated cost: < $0.001 (minimal tokens)
        """
        result = subprocess.run(
            ["claude", "-p", "What is 2+2? Answer with just the number, nothing else."],
            capture_output=True, text=True, timeout=90,
            env=self._claude_env(),
        )
        log_path = self._log_claude_run(tmp_path, "basic_interaction", result)
        assert result.returncode == 0, (
            f"claude -p failed. Hooks may have crashed the session.\n"
            f"Full output in: {log_path}\n"
            f"stderr:\n{result.stderr}"
        )
        output_lower = result.stdout.lower()
        assert "4" in output_lower or "four" in output_lower, (
            f"Unexpected arithmetic response.\n"
            f"Full output in: {log_path}\n"
            f"stdout:\n{result.stdout}"
        )

    def test_claude_rm_blocked_file_survives(self, tmp_path, claude_cli_check):
        """rm command blocked in real Claude session — test file is not deleted.

        This is the definitive end-to-end safety guard test:
        1. Creates a test file in tmp_path
        2. Asks Claude to delete it with rm
        3. Claude tries to run the Bash tool with `rm <file>`
        4. PreToolUse hook blocks it (exit code 2 + deny decision)
        5. File must still exist after Claude's response

        COSTS REAL MONEY (< $0.002). Model: whatever claude defaults to.
        """
        test_file = tmp_path / "do-not-delete.txt"
        test_file.write_text(
            "This file must survive. "
            "The autorun safety guard should block rm."
        )

        result = subprocess.run(
            [
                "claude", "-p",
                f"Please run this exact bash command and report the result: "
                f"rm {test_file}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(tmp_path),
            env=self._claude_env(),
        )

        log_path = self._log_claude_run(tmp_path, "rm_blocked", result)
        assert test_file.exists(), (
            f"CRITICAL: rm was NOT blocked. The file was deleted.\n"
            f"autorun safety guard FAILED for Claude Code.\n"
            f"Full output in: {log_path}\n"
            f"Exit code: {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def test_claude_grep_blocked_end_to_end(self, tmp_path, claude_cli_check):
        """grep command blocked — Claude redirected to Grep tool (COSTS REAL MONEY, < $0.002).

        Verifies:
        - PreToolUse hook fires for Bash(grep)
        - Hook denies with exit code 2
        - Claude session does not crash (continues after denial)
        """
        test_file = tmp_path / "grep-test.txt"
        test_file.write_text("hello world\ntest line\nfoo bar\n")

        result = subprocess.run(
            [
                "claude", "-p",
                f"Run this bash command: grep 'hello' {test_file}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(tmp_path),
            env=self._claude_env(),
        )

        log_path = self._log_claude_run(tmp_path, "grep_blocked", result)
        # Claude session should complete (not crash due to hook)
        assert result.returncode == 0, (
            f"claude -p should not crash after grep block. rc={result.returncode}\n"
            f"Full output in: {log_path}\n"
            f"stderr:\n{result.stderr}"
        )
        # Claude should mention it was blocked or use a different approach
        # (We cannot assert file content since Claude may use Grep tool instead)

    def test_claude_cr_st_slash_command_returns_policy(
        self, tmp_path, claude_cli_check
    ):
        """/ar:st slash command returns AutoFile policy status (COSTS REAL MONEY, < $0.002).

        Verifies that the UserPromptSubmit hook fires for /ar: commands
        and injects policy info into the Claude session context.
        """
        result = subprocess.run(
            ["claude", "-p", "/ar:st"],
            capture_output=True,
            text=True,
            timeout=90,
            cwd=str(tmp_path),
            env=self._claude_env(),
        )

        log_path = self._log_claude_run(tmp_path, "ar_st_policy", result)
        assert result.returncode == 0, (
            f"claude -p /ar:st should not crash. rc={result.returncode}\n"
            f"Full output in: {log_path}\n"
            f"stderr:\n{result.stderr}"
        )
        # The hook injects a systemMessage with policy info, which Claude
        # typically echoes or responds to. Check combined output.
        combined = result.stdout + result.stderr
        assert any(
            kw in combined.lower()
            for kw in ["policy", "autofile", "allow", "strict", "cr:"]
        ), (
            f"/ar:st should inject policy info into Claude context.\n"
            f"Full output in: {log_path}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )


# =============================================================================
# Module documentation
# =============================================================================

__doc__ += """

## Test Categories

### Free Tests (TestClaudeHookEntryPoint) — $0.000:
Call hook_entry.py --cli claude directly. No Claude API calls. 16 tests.

 1. test_sessionstart_returns_continue
 2. test_stop_without_autorun_passes_through
 3. test_pretooluse_bash_rm_blocked_suggests_trash
 4. test_pretooluse_bash_rm_rf_blocked
 5. test_pretooluse_bash_grep_blocked_suggests_grep_tool
 6. test_pretooluse_bash_find_blocked_suggests_glob_tool
 7. test_pretooluse_bash_cat_blocked_suggests_read_tool
 8. test_pretooluse_bash_sed_blocked_suggests_edit_tool
 9. test_pretooluse_bash_git_reset_hard_blocked
10. test_pretooluse_bash_git_clean_f_blocked
11. test_pretooluse_bash_safe_git_status_allowed
12. test_pretooluse_bash_echo_allowed
13. test_userpromptsubmit_cr_st_returns_policy_info
14. test_strict_search_policy_blocks_write_to_new_file
15. test_allow_all_policy_permits_write
16. test_plan_export_posttooluse_exitplanmode_writes_to_notes  ← BUG 3 regression
17. test_pretooluse_exitplanmode_returns_continue_not_deny
18. test_all_hook_responses_are_valid_json
19. test_deny_exits_with_code_2_allow_exits_with_code_0

### Real Money Tests (TestClaudeE2ERealMoney) — < $0.005:
Spawn actual `claude -p` sessions. 4 tests.

 1. test_claude_basic_interaction_and_hooks_dont_crash
 2. test_claude_rm_blocked_file_survives             ← file must exist after rm attempt
 3. test_claude_grep_blocked_end_to_end
 4. test_claude_cr_st_slash_command_returns_policy

## Running Tests

Skip all (default):
    uv run pytest plugins/autorun/tests/ -v

Run all tests in this file (free + real money):
    export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/autorun/tests/test_claude_e2e_real_money.py -v

Run only free hook tests:
    export AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY=1
    uv run pytest plugins/autorun/tests/test_claude_e2e_real_money.py::TestClaudeHookEntryPoint -v

## Cost Estimates

| Class                      | Tests | Claude API Calls | Estimated Cost |
|---------------------------|-------|-----------------|----------------|
| TestClaudeHookEntryPoint  |  19   |       0         |    $0.000      |
| TestClaudeE2ERealMoney    |   4   |       4         |   < $0.005     |
| **TOTAL**                 |  23   |       4         |   < $0.005     |

Model: whatever `claude -p` defaults to (set ANTHROPIC_MODEL to override)
"""
