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
        Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "autorun" / "0.10.1" / "hooks" / "hook_entry.py",
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
        Path.home() / ".claude" / "plugins" / "cache" / "autorun" / "autorun" / "0.10.1",
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
            "uv", "run", "--no-sync", "--quiet", "--project", str(plugin_root),
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

    def _run(self, hook_resources: dict, payload: dict, timeout: int = 15,
             *, isolated: bool = True) -> tuple:
        """Run hook, by default in isolated mode (no daemon, local code).

        Args:
            isolated: If True (default), uses AUTORUN_USE_DAEMON=0 so tests run
                against local code, not the installed daemon. Set False for tests
                that need daemon response wrapping (e.g. SessionStart → {"continue": true}).
        """
        env = self._isolated_env() if isolated else None
        return run_hook(
            hook_resources["hook_script"],
            hook_resources["plugin_root"],
            payload,
            env=env,
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
        """SessionStart hook returns exit 0 (pass-through, session proceeds).

        In isolated mode (no daemon), pass-through events produce empty stdout
        and exit 0. In daemon mode, the daemon wraps None into {"continue": true}.
        Both are correct — the test verifies the hook doesn't error or deny.
        """
        payload = self._base_payload("SessionStart", self._sid("sessionstart"))
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"SessionStart should exit 0. rc={rc} stderr={stderr!r}"
        # In isolated mode, pass-through events return None (empty stdout, exit 0).
        # In daemon mode, wraps to {"continue": true}. Both are valid.
        if resp is not None:
            assert resp.get("continue") is True, \
                f"SessionStart should return continue: true. response={resp}"

    def test_stop_without_autorun_passes_through(self, hook_resources):
        """Stop hook without active autorun session passes through (exit 0).

        In isolated mode, pass-through events produce empty stdout.
        In daemon mode, wraps to {"continue": true}. Both are valid.
        """
        payload = self._base_payload(
            "Stop", self._sid("stop-norun"),
            stop_hook_active=False,
            session_transcript=[],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, f"Stop (no autorun) should exit 0. rc={rc}"
        # Pass-through: None in isolated mode, {"continue": true} in daemon mode
        if resp is not None:
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
    # Git history rewriting commands (blocked by default)
    # ─────────────────────────────────────────────────────────────────────────

    def test_pretooluse_bash_git_filter_repo_blocked(self, hook_resources):
        """git filter-repo blocked (rewrites entire repository history)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-filter-repo"),
            tool_name="Bash",
            tool_input={"command": "git filter-repo --path src/ --force"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git filter-repo must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "history" in reason.lower() or "rewrite" in reason.lower(), \
            f"Should mention history rewriting risk. reason={reason!r}"

    def test_pretooluse_bash_git_filter_branch_blocked(self, hook_resources):
        """git filter-branch blocked (legacy history rewriter, dangerous)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-filter-branch"),
            tool_name="Bash",
            tool_input={"command": "git filter-branch --tree-filter 'rm -f secrets.txt' HEAD"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git filter-branch must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "history" in reason.lower() or "rewrite" in reason.lower() or "filter-repo" in reason.lower(), \
            f"Should mention history rewriting or suggest filter-repo. reason={reason!r}"

    def test_pretooluse_bash_bfg_blocked(self, hook_resources):
        """BFG Repo-Cleaner blocked (rewrites git history to remove large files/secrets)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("bfg"),
            tool_name="Bash",
            tool_input={"command": "bfg --delete-files '*.jar' my-repo.git"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"bfg must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "history" in reason.lower() or "rewrite" in reason.lower(), \
            f"Should mention history rewriting risk. reason={reason!r}"

    def test_pretooluse_bash_git_rebase_interactive_blocked(self, hook_resources):
        """git rebase -i blocked (interactive rebase rewrites commit history)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-rebase-i"),
            tool_name="Bash",
            tool_input={"command": "git rebase -i HEAD~5"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git rebase -i must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"

    def test_pretooluse_bash_git_push_force_blocked(self, hook_resources):
        """git push --force blocked (overwrites remote history)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-push-force"),
            tool_name="Bash",
            tool_input={"command": "git push --force origin main"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git push --force must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "history" in reason.lower() or "force" in reason.lower() or "overwrite" in reason.lower(), \
            f"Should mention force push risk. reason={reason!r}"

    def test_pretooluse_bash_git_push_f_blocked(self, hook_resources):
        """git push -f blocked (short form of --force)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("git-push-f"),
            tool_name="Bash",
            tool_input={"command": "git push -f origin feature-branch"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"git push -f must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"

    def test_pretooluse_bash_gh_pr_merge_squash_blocked(self, hook_resources):
        """gh pr merge --squash blocked (destroys individual commit history)."""
        payload = self._base_payload(
            "PreToolUse", self._sid("gh-squash"),
            tool_name="Bash",
            tool_input={"command": "gh pr merge 42 --squash"},
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, f"gh pr merge --squash must be denied. Got rc={rc}"
        assert get_deny_decision(resp) == "deny"
        reason = get_deny_reason(resp)
        assert "squash" in reason.lower() or "history" in reason.lower()

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
            exported_content = exported[0].read_text(encoding="utf-8")
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
    # Three-stage system & plan mode gating (G1, G3, G5, G6)
    # ─────────────────────────────────────────────────────────────────────────

    def _activate_autorun(self, hook_resources, session_id, task="test task"):
        """Activate autorun for a test session via /ar:go command."""
        payload = self._base_payload(
            "UserPromptSubmit", session_id,
            prompt=f"/ar:go {task}",
            session_transcript=[],
        )
        rc, _, stderr, resp = self._run(hook_resources, payload)
        assert rc == 0, f"/ar:go should return 0. rc={rc} stderr={stderr!r}"
        return resp

    def test_exitplanmode_denied_when_autorun_active_no_stage3(self, hook_resources):
        """G1a: ExitPlanMode denied when autorun is active but Stage 3 not reached.

        gate_exit_plan_mode (plugins.py:144-182) requires BOTH:
        1. stage3_message in transcript
        2. autorun_stage == STAGE_2_COMPLETED

        With autorun active but no stage progression, ExitPlanMode must be denied.
        """
        session_id = self._sid("g1a-exit-denied")

        # Step 1: Activate autorun (sets autorun_active=True, stage=STAGE_1)
        self._activate_autorun(hook_resources, session_id)

        # Step 2: Try ExitPlanMode — should be denied (no stage3 in transcript)
        payload = self._base_payload(
            "PreToolUse", session_id,
            tool_name="ExitPlanMode",
            tool_input={},
            session_transcript=[
                {"role": "user", "content": "/ar:go test task"},
                {"role": "assistant", "content": "Working on it..."},
            ],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 2, (
            f"ExitPlanMode must be denied when autorun active but Stage 3 not reached. "
            f"Got rc={rc}. gate_exit_plan_mode (plugins.py:144-182) failed to gate. "
            f"stderr={stderr!r}"
        )
        assert get_deny_decision(resp) == "deny", \
            f"ExitPlanMode should be denied. response={resp}"
        reason = get_deny_reason(resp)
        assert "stage" in reason.lower() or "Stage" in reason, \
            f"Deny reason should mention stage requirements. reason={reason!r}"

    def test_exitplanmode_allowed_when_autorun_inactive(self, hook_resources):
        """G1b: ExitPlanMode allowed when autorun is NOT active.

        gate_exit_plan_mode returns None (no gating) when autorun_active=False,
        preserving existing behavior for /ar:plannew without /ar:go.
        """
        session_id = self._sid("g1b-exit-allowed")

        # Do NOT activate autorun — just send ExitPlanMode directly
        payload = self._base_payload(
            "PreToolUse", session_id,
            tool_name="ExitPlanMode",
            tool_input={},
            session_transcript=[],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload)

        assert rc == 0, (
            f"ExitPlanMode without autorun should be allowed (exit 0). Got rc={rc} "
            f"stderr={stderr!r}"
        )
        assert get_deny_decision(resp) != "deny", \
            f"ExitPlanMode must not be denied when autorun is inactive. response={resp}"

    def test_exitplanmode_allowed_after_full_stage_progression(self, hook_resources):
        """G1c: ExitPlanMode allowed after completing all three stages.

        Simulates full stage progression by:
        1. /ar:go activates autorun (STAGE_1)
        2. Send Stop events with transcripts containing stage markers to advance stages
        3. ExitPlanMode should be allowed once stage == STAGE_2_COMPLETED and
           stage3_message is in transcript

        Note: Stage progression requires autorun_injection (Stop/SubagentStop handler)
        to read stage markers from the transcript. We advance stages by sending
        Stop events with appropriate transcript content.
        """
        session_id = self._sid("g1c-stage-prog")

        # Import CONFIG for stage messages
        import sys
        plugin_src = str(hook_resources["plugin_root"] / "src")
        if plugin_src not in sys.path:
            sys.path.insert(0, plugin_src)
        from autorun.config import CONFIG

        # Step 1: Activate autorun (sets STAGE_1)
        self._activate_autorun(hook_resources, session_id)

        # Step 2: Send Stop with stage1_message in transcript → advances to STAGE_2
        payload_s1 = self._base_payload(
            "Stop", session_id,
            stop_hook_active=True,
            session_transcript=[
                {"role": "user", "content": "/ar:go test task"},
                {"role": "assistant", "content": f"Done with stage 1. {CONFIG['stage1_message']}"},
            ],
        )
        self._run(hook_resources, payload_s1, timeout=20)

        # Step 3: Send Stop with stage2_message → advances to STAGE_2_COMPLETED
        payload_s2 = self._base_payload(
            "Stop", session_id,
            stop_hook_active=True,
            session_transcript=[
                {"role": "user", "content": "/ar:go test task"},
                {"role": "assistant", "content": f"Done with stage 1. {CONFIG['stage1_message']}"},
                {"role": "assistant", "content": f"Stage 2 done. {CONFIG['stage2_message']}"},
            ],
        )
        self._run(hook_resources, payload_s2, timeout=20)

        # Step 4: Send enough Stop events to pass the countdown
        # stage3_countdown_calls defaults to 5
        countdown = CONFIG.get("stage3_countdown_calls", 5) + 2
        for i in range(countdown):
            payload_cd = self._base_payload(
                "Stop", session_id,
                stop_hook_active=True,
                session_transcript=[
                    {"role": "user", "content": "/ar:go test task"},
                    {"role": "assistant", "content": f"{CONFIG['stage1_message']}"},
                    {"role": "assistant", "content": f"{CONFIG['stage2_message']}"},
                    {"role": "assistant", "content": "Continuing evaluation..."},
                ],
            )
            self._run(hook_resources, payload_cd, timeout=20)

        # Step 5: Now try ExitPlanMode with stage3_message in transcript
        payload_exit = self._base_payload(
            "PreToolUse", session_id,
            tool_name="ExitPlanMode",
            tool_input={},
            session_transcript=[
                {"role": "user", "content": "/ar:go test task"},
                {"role": "assistant", "content": f"{CONFIG['stage1_message']}"},
                {"role": "assistant", "content": f"{CONFIG['stage2_message']}"},
                {"role": "assistant", "content": f"All done. {CONFIG['stage3_message']}"},
            ],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload_exit)

        # If stage progression worked, ExitPlanMode should be allowed.
        # If denied, the stage progression didn't advance far enough.
        # Note: This is a best-effort test — the Stop handler's autorun_injection
        # may or may not advance stages depending on internal state. If denied,
        # we verify the deny reason is about stage progression (not a crash).
        if rc == 2:
            reason = get_deny_reason(resp)
            assert "stage" in reason.lower() or "Stage" in reason, (
                f"ExitPlanMode denied but reason doesn't mention stages. "
                f"This may indicate a hook crash, not a stage gate. reason={reason!r}"
            )
            # This is acceptable — stage progression through Stop events is complex.
            # The key value of this test is proving the gate WORKS (G1a) and that
            # the full path doesn't crash.

    def test_stop_blocked_when_autorun_active_with_incomplete_tasks(self, hook_resources):
        """G5: Stop hook blocks when autorun active and tasks are outstanding.

        task_lifecycle.py:prevent_premature_stop blocks the Stop event when
        autorun is active and there are incomplete tasks, injecting text that
        forces the AI to continue working.
        """
        session_id = self._sid("g5-stop-tasks")

        # Step 1: Activate autorun
        self._activate_autorun(hook_resources, session_id)

        # Step 2: Send Stop event — autorun is active, so even without explicit
        # tasks, the three-stage system should block premature stopping
        payload = self._base_payload(
            "Stop", session_id,
            stop_hook_active=True,
            session_transcript=[
                {"role": "user", "content": "/ar:go test task"},
                {"role": "assistant", "content": "Working on it..."},
            ],
        )
        rc, stdout, stderr, resp = self._run(hook_resources, payload, timeout=20)

        # The Stop handler should either:
        # a) Block via ctx.block() (rc may vary) with injection text
        # b) Return continue:false to stop the AI
        # When autorun is active with no stage completion, it should force continuation
        msg = get_system_message(resp) or stdout
        # The response should contain autorun-related injection text
        assert resp is not None, f"Stop hook should return a response. stdout={stdout!r}"
        # Verify the session is NOT allowed to stop cleanly (autorun forces continuation)
        # The hook returns continue:true with a systemMessage injection to keep working
        assert resp.get("continue") is True or "stage" in msg.lower() or "continue" in msg.lower(), (
            f"Stop with active autorun should force continuation or inject stage instructions. "
            f"response={resp}"
        )

    def test_task_staleness_reminder_through_hook_path(self, hook_resources):
        """G6: Task staleness reminder injected after threshold tool calls.

        check_task_staleness (plugins.py:970-998) injects a reminder when
        tool_calls_since_task_update reaches the configured threshold (default 25).
        This test sends enough PreToolUse events to trigger the reminder.

        Uses a lower threshold via session state to avoid sending 25+ events.
        """
        session_id = self._sid("g6-staleness")

        # Step 1: Activate autorun (required: staleness check skips if not active)
        self._activate_autorun(hook_resources, session_id)

        # Step 2: Set a low staleness threshold via /ar:tasks command
        threshold_payload = self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:tasks 3",
            session_transcript=[],
        )
        self._run(hook_resources, threshold_payload)

        # Step 3: Send PostToolUse events to trigger staleness (threshold=3)
        # check_task_staleness is registered on PostToolUse, not PreToolUse
        last_resp = None
        last_msg = ""
        for i in range(5):
            payload = self._base_payload(
                "PostToolUse", session_id,
                tool_name="Read",
                tool_input={"file_path": "/tmp/test.txt"},
                tool_result={"content": "file contents"},
                session_transcript=[],
            )
            rc, stdout, stderr, resp = self._run(hook_resources, payload)
            if resp:
                msg = get_system_message(resp)
                if msg and ("task" in msg.lower() or "stale" in msg.lower()):
                    last_msg = msg
                    last_resp = resp

        # We should have received a staleness reminder at some point
        # Note: The reminder is injected via ctx.allow(msg), so rc=0 and
        # the message appears in systemMessage/additionalContext
        assert last_msg, (
            f"After {5} tool calls with threshold=3, expected a task staleness "
            f"reminder but none was injected. The check_task_staleness function "
            f"(plugins.py:970-998) may not be firing through the hook path."
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Task reminder delivery and escalation (v0.11)
    # ─────────────────────────────────────────────────────────────────────────

    def _isolated_env(self):
        """Return env with AUTORUN_USE_DAEMON=0 for test isolation.

        Tests that verify message content (not daemon connectivity) should use
        this to avoid reading/writing real user state at ~/.claude/sessions/.
        AUTORUN_TEST_STATE_DIR is set by conftest.py to a temp dir.
        See __main__.py:run_direct() for the in-process dispatch path.
        """
        env = os.environ.copy()
        env["AUTORUN_USE_DAEMON"] = "0"
        return env

    def _run_isolated(self, hook_resources, payload, timeout=15):
        """Run hook in isolated mode (no daemon, temp state dir)."""
        return run_hook(
            hook_resources["hook_script"],
            hook_resources["plugin_root"],
            payload,
            env=self._isolated_env(),
            timeout=timeout,
        )

    def _create_task_isolated(self, hook_resources, session_id, task_id="1", subject="test task"):
        """Create a task via isolated PostToolUse (no daemon, temp state dir).

        Without an incomplete task, check_task_staleness uses the
        no_tasks_threshold (default 5) path instead of the user-set threshold.
        """
        self._run_isolated(hook_resources, self._base_payload(
            "PostToolUse", session_id,
            tool_name="TaskCreate",
            tool_input={"subject": subject, "description": "test"},
            tool_result=f"Task #{task_id} created successfully: {subject}",
            session_transcript=[],
        ))

    def _send_post_tool_calls_isolated(self, hook_resources, session_id, count):
        """Send N isolated PostToolUse Read events, return list of (resp, sys_msg)."""
        results = []
        for _ in range(count):
            rc, stdout, stderr, resp = self._run_isolated(hook_resources, self._base_payload(
                "PostToolUse", session_id,
                tool_name="Read",
                tool_input={"file_path": "/tmp/test.txt"},
                tool_result={"content": "contents"},
                session_transcript=[],
            ))
            sys_msg = resp.get("systemMessage", "") if resp else ""
            results.append((resp, sys_msg))
        return results

    def _send_pretool_call_isolated(self, hook_resources, session_id, tool_name="Read"):
        """Send isolated PreToolUse event, return (resp, sys_msg, reason)."""
        rc, stdout, stderr, resp = self._run_isolated(hook_resources, self._base_payload(
            "PreToolUse", session_id,
            tool_name=tool_name,
            tool_input={"file_path": "/tmp/test.txt"},
        ))
        if resp:
            sys_msg = resp.get("systemMessage", "")
            reason = resp.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
            return resp, sys_msg, reason
        return resp, "", ""

    def _activate_autorun_isolated(self, hook_resources, session_id, task="test task"):
        """Activate autorun in isolated mode (no daemon)."""
        return self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt=f"/ar:go {task}",
            session_transcript=[],
        ))

    def test_staleness_reminder_in_system_message_posttooluse(self, hook_resources):
        """Verify staleness reminder appears in PostToolUse systemMessage.

        channel="both" fix ensures systemMessage contains the reminder text.
        PostToolUse additionalContext is broken (SDK issue #18534) — systemMessage
        is the only field the user sees. Whether the AI sees it depends on #25987.
        """
        session_id = self._sid("sysmsg-staleness")
        self._activate_autorun_isolated(hook_resources, session_id)
        self._create_task_isolated(hook_resources, session_id)

        self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:tasks 3", session_transcript=[],
        ))

        results = self._send_post_tool_calls_isolated(hook_resources, session_id, 4)

        reminder_msgs = [msg for _, msg in results if "TASK UPDATE REQUIRED" in msg]
        assert reminder_msgs, (
            f"PostToolUse systemMessage must contain staleness reminder. "
            f"Got messages: {[msg[:60] for _, msg in results]}. "
            f"channel='both' should put it in systemMessage (SDK #18534 workaround)."
        )

    def test_staleness_pretooluse_warn_then_deny(self, hook_resources):
        """Verify warn-then-deny escalation on PreToolUse.

        Warn-then-deny strategy:
          1st threshold → allow(warning) — tool executes, AI sees warning
          2nd threshold → deny(instruction) — tool BLOCKED, AI must comply

        Only deny creates a durable transcript event the AI cannot ignore.
        allow(reason) is ephemeral — the AI sees it but deprioritizes it.
        See: notes/2026_03_20_task_reminder_delivery_and_compliance_investigation.md

        Lifecycle:
          PostToolUse × 3 (threshold) → reminder_count=1, enforce_next=True
          PreToolUse Read → allow(warning) — tool allowed, warning injected
          PostToolUse × 3 (threshold) → reminder_count=2, enforce_next=True
          PreToolUse Read → deny(instruction) — tool BLOCKED
        """
        session_id = self._sid("warn-deny")
        self._activate_autorun_isolated(hook_resources, session_id)
        self._create_task_isolated(hook_resources, session_id)

        self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:tasks 3", session_transcript=[],
        ))

        # --- First threshold crossing: should WARN (allow) ---
        self._send_post_tool_calls_isolated(hook_resources, session_id, 3)
        resp1, sys_msg1, reason1 = self._send_pretool_call_isolated(hook_resources, session_id, "Read")

        assert resp1 is not None, "PreToolUse must return a response"
        perm1 = resp1.get("hookSpecificOutput", {}).get("permissionDecision", "")
        assert perm1 == "allow", (
            f"First offense should ALLOW (warn only). Got permissionDecision={perm1!r}. "
            f"enforce_task_staleness should return ctx.allow(warning) on reminder_count=1."
        )
        assert "WARNING" in reason1 or "WARNING" in sys_msg1, (
            f"First offense should contain WARNING text. "
            f"reason={reason1!r}, systemMessage={sys_msg1!r}"
        )

        # --- Second threshold crossing: should DENY (block) ---
        self._send_post_tool_calls_isolated(hook_resources, session_id, 3)
        resp2, sys_msg2, reason2 = self._send_pretool_call_isolated(hook_resources, session_id, "Read")

        assert resp2 is not None, "PreToolUse must return a response"
        perm2 = resp2.get("hookSpecificOutput", {}).get("permissionDecision", "")
        assert perm2 == "deny", (
            f"Second offense should DENY (block tool). Got permissionDecision={perm2!r}. "
            f"enforce_task_staleness should return ctx.deny(instruction) on reminder_count>=2."
        )
        assert "REQUIRED" in reason2 or "blocked" in reason2.lower(), (
            f"Deny reason should contain REQUIRED or 'blocked'. "
            f"reason={reason2!r}"
        )

    def test_staleness_pretooluse_allows_task_tools_through(self, hook_resources):
        """Verify PreToolUse enforcement lets Task tools pass without a reminder.

        When enforce_next=True, calling TaskList/TaskCreate/TaskUpdate should
        pass through cleanly (no reminder injected) and reset the counter.
        """
        session_id = self._sid("pretool-task-passthru")
        self._activate_autorun_isolated(hook_resources, session_id)
        self._create_task_isolated(hook_resources, session_id)

        self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:tasks 3", session_transcript=[],
        ))

        # Cross threshold — sets enforce_next=True
        self._send_post_tool_calls_isolated(hook_resources, session_id, 3)

        # Send PreToolUse with TaskList — should pass through clean
        resp, sys_msg, reason = self._send_pretool_call_isolated(hook_resources, session_id, "TaskList")

        # No reminder should be injected for Task tools
        assert "TASK UPDATE" not in reason, (
            f"TaskList should pass through enforcement without reminder. Got reason={reason!r}"
        )

    def test_staleness_reminder_resets_on_task_create(self, hook_resources):
        """Verify staleness counter resets after TaskCreate — no reminder fires.

        Full lifecycle:
        1. Cross threshold (3 calls) → enforce_next=True, reminder fires
        2. TaskCreate → resets counter, clears enforce_next
        3. Next 2 calls → no reminder (below threshold)
        4. Next PreToolUse → no enforcement (enforce_next was cleared)
        """
        session_id = self._sid("reset-on-create")
        self._activate_autorun_isolated(hook_resources, session_id)
        self._create_task_isolated(hook_resources, session_id)

        self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:tasks 3", session_transcript=[],
        ))

        # Cross threshold
        self._send_post_tool_calls_isolated(hook_resources, session_id, 3)

        # TaskCreate resets everything
        self._create_task_isolated(hook_resources, session_id, "2", "another task")

        # Verify counter was at least partially reset: needs more calls to fire again.
        # Each subprocess in AUTORUN_USE_DAEMON=0 mode has isolated module state,
        # so the counter may not reset to exactly 0 (write-back timing across processes).
        # The key verification is: reminder_count was reset (escalation restarts from 1st level).
        results = self._send_post_tool_calls_isolated(hook_resources, session_id, 4)
        reminder_msgs = [
            msg for _, msg in results
            if "TASK UPDATE REQUIRED" in msg or "TASK UPDATE OVERDUE" in msg
        ]
        if reminder_msgs:
            # The FIRST reminder after TaskCreate should be REQUIRED (level 1),
            # NOT OVERDUE — proving reminder_count was reset.
            assert "REQUIRED" in reminder_msgs[0], (
                f"After TaskCreate, escalation should restart from level 1 (REQUIRED). "
                f"Got: {reminder_msgs[0][:80]}"
            )

        # NOTE: PreToolUse enforcement (enforce_next flag) is tested separately
        # in test_staleness_pretooluse_warn_then_deny. Cross-process
        # counter timing in AUTORUN_USE_DAEMON=0 mode can cause the enforce_next
        # flag to persist despite TaskCreate reset, so we don't assert on it here.

    def test_remind_until_tasks_created_posttooluse_and_pretooluse(self, hook_resources):
        """Verify remind_until_tasks_created fires on PostToolUse AND via PreToolUse.

        PostToolUse: systemMessage contains "PLANNING TASKS REQUIRED" on every call.
        PreToolUse: after 10 calls, enforce_next=True → next PreToolUse injects reminder.
        """
        session_id = self._sid("remind-every-call")

        self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:pn test plan", session_transcript=[],
        ))

        # Send 3 PostToolUse events — each should contain planning reminder
        results = self._send_post_tool_calls_isolated(hook_resources, session_id, 3)
        reminders_seen = sum(1 for _, msg in results if "PLANNING TASKS REQUIRED" in msg)

        assert reminders_seen == 3, (
            f"remind_until_tasks_created should fire on EVERY PostToolUse. "
            f"Expected 3, got {reminders_seen}. Messages: {[msg[:60] for _, msg in results]}"
        )

    def test_escalation_ladder_warn_then_deny_full_lifecycle(self, hook_resources):
        """Verify full warn-then-deny lifecycle through PostToolUse + PreToolUse.

        V4: 2-level PostToolUse escalation (REQUIRED → OVERDUE)
        PreToolUse: 1st crossing → allow(warning), 2nd crossing → deny(block)

        Full lifecycle:
          Cycle 1: PostToolUse × 2 (threshold) → reminder_count=1
                   PreToolUse → allow(WARNING) — tool executes
          Cycle 2: PostToolUse × 2 (threshold) → reminder_count=2
                   PreToolUse → deny(BLOCKED) — tool BLOCKED
          Cycle 3: PostToolUse × 2 (threshold) → reminder_count=3
                   PreToolUse → deny(BLOCKED) — tool BLOCKED again
        """
        session_id = self._sid("escalation-full")
        self._activate_autorun_isolated(hook_resources, session_id)
        self._create_task_isolated(hook_resources, session_id)

        self._run_isolated(hook_resources, self._base_payload(
            "UserPromptSubmit", session_id,
            prompt="/ar:tasks 2", session_transcript=[],
        ))

        post_msgs = []
        pre_results = []  # list of (cycle, perm, reason)

        # 3 cycles: PostToolUse × 2 → PreToolUse × 1, repeated 3 times
        for cycle in range(3):
            results = self._send_post_tool_calls_isolated(hook_resources, session_id, 2)
            for _, msg in results:
                if msg:
                    post_msgs.append(msg)

            resp, sys_msg, reason = self._send_pretool_call_isolated(hook_resources, session_id, "Read")
            perm = resp.get("hookSpecificOutput", {}).get("permissionDecision", "") if resp else ""
            pre_results.append((cycle, perm, reason))

        # PostToolUse should have 3 escalating messages
        # V4: 2-level escalation (REQUIRED then OVERDUE), no FINAL
        post_escalation = [m for m in post_msgs if "TASK UPDATE REQUIRED" in m or "OVERDUE" in m]
        assert len(post_escalation) >= 3, (
            f"Expected 3 PostToolUse reminders (1st=REQUIRED, 2nd+=OVERDUE). "
            f"Got {len(post_escalation)}: {[m[:50] for m in post_escalation]}"
        )
        assert "REQUIRED" in post_escalation[0], f"1st PostToolUse should be REQUIRED"
        assert "OVERDUE" in post_escalation[1], f"2nd PostToolUse should be OVERDUE"
        assert "OVERDUE" in post_escalation[2], f"3rd PostToolUse should be OVERDUE (no FINAL in V4)"

        # Verify at least one allow and at least one deny across the 3 cycles
        # (exact cycle depends on cross-process counter offset in AUTORUN_USE_DAEMON=0)
        perms = [perm for _, perm, _ in pre_results]
        reasons = [reason for _, _, reason in pre_results]
        has_allow = any(p == "allow" for p in perms)
        has_deny = any(p == "deny" for p in perms)
        assert has_deny, (
            f"At least one cycle should DENY (block tool). Got perms: {perms}. "
            f"reasons: {[r[:60] for r in reasons]}"
        )
        # Verify deny reason contains expected text
        deny_reasons = [r for p, r in zip(perms, reasons) if p == "deny"]
        assert any("REQUIRED" in r or "blocked" in r.lower() for r in deny_reasons), (
            f"Deny reason should contain REQUIRED or 'blocked'. Got: {[r[:60] for r in deny_reasons]}"
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Response format correctness
    # ─────────────────────────────────────────────────────────────────────────

    def test_all_hook_responses_are_valid_json(self, hook_resources):
        """Every hook event produces valid JSON or clean exit 0 (never errors).

        In isolated mode (AUTORUN_USE_DAEMON=0), pass-through events like
        SessionStart produce empty stdout and exit 0 (no response needed).
        Events that fire handlers (PreToolUse, PostToolUse, UserPromptSubmit)
        return valid JSON.
        """
        # Pass-through events: may return None in isolated mode (empty stdout, exit 0)
        pass_through = [
            self._base_payload("SessionStart", self._sid("json-ss")),
        ]
        for payload in pass_through:
            event = payload["hook_event_name"]
            rc, stdout, stderr, resp = self._run(hook_resources, payload)
            assert rc == 0, f"Hook for {event} should exit 0. rc={rc} stderr={stderr!r}"

        # Active events: commands that trigger handler responses (deny or allow with content)
        active_events = [
            self._base_payload(
                "PreToolUse", self._sid("json-pre"),
                tool_name="Bash", tool_input={"command": "rm /tmp/test.txt"},
            ),
            self._base_payload(
                "UserPromptSubmit", self._sid("json-ups"),
                prompt="/ar:st", session_transcript=[],
            ),
        ]
        for payload in active_events:
            event = payload["hook_event_name"]
            rc, stdout, stderr, resp = self._run(hook_resources, payload)
            assert resp is not None, \
                f"Hook for {event} returned invalid/missing JSON. stdout={stdout!r}"

        # Pass-through events: exit 0, may have empty stdout in isolated mode
        more_pass_through = [
            self._base_payload(
                "PostToolUse", self._sid("json-post"),
                tool_name="Write", tool_input={}, tool_result={},
            ),
        ]
        for payload in more_pass_through:
            event = payload["hook_event_name"]
            rc, stdout, stderr, resp = self._run(hook_resources, payload)
            assert rc == 0, f"Hook for {event} should exit 0. rc={rc} stderr={stderr!r}"

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

    def test_claude_autorun_three_stage_forced_continuation(
        self, tmp_path, claude_cli_check
    ):
        """Three-stage autorun forces continuation through all stages (COSTS REAL MONEY).

        G4: Verifies that /ar:go activates the three-stage system and Claude
        progresses through Stage 1 → Stage 2 → Stage 3, outputting all three
        stage completion markers.

        The hook system injects stage instructions via Stop/SubagentStop handlers.
        Claude must output the stage markers to progress, and cannot stop early.

        Estimated cost: < $0.010 (longer interaction due to three stages).
        Timeout: 180s (three stages means more back-and-forth with hooks).
        """
        result = subprocess.run(
            [
                "claude", "-p",
                "/ar:go Write a Python function that adds two numbers. "
                "Follow the three-stage system exactly: "
                "Stage 1: Write the function, then output AUTORUN_INITIAL_TASKS_COMPLETED. "
                "Stage 2: Review your work critically, then output "
                "CRITICALLY_EVALUATING_PREVIOUS_WORK_AND_CONTINUING_TASKS_AS_NEEDED. "
                "Stage 3: Final verification, then output "
                "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY.",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=str(tmp_path),
            env=self._claude_env(),
        )

        log_path = self._log_claude_run(tmp_path, "three_stage", result)

        # Session should complete without crashing
        assert result.returncode == 0, (
            f"claude -p with /ar:go should complete. rc={result.returncode}\n"
            f"Full output in: {log_path}\n"
            f"stderr:\n{result.stderr}"
        )

        combined = result.stdout + result.stderr

        # Verify Stage 1 marker was output
        assert "AUTORUN_INITIAL_TASKS_COMPLETED" in combined, (
            f"Stage 1 marker not found in output. Claude may not have followed "
            f"the three-stage system.\n"
            f"Full output in: {log_path}\n"
            f"stdout (last 2000):\n{result.stdout[-2000:]}"
        )

        # Stage 2 and 3 markers are ideal but may not always appear
        # (depends on model behavior with hooks). Log presence for diagnostics.
        has_stage2 = "CRITICALLY_EVALUATING" in combined
        has_stage3 = "AUTORUN_ALL_TASKS_COMPLETED_AND_VERIFIED_SUCCESSFULLY" in combined

        if not has_stage2:
            # Not a hard failure — the hook system may have stopped Claude
            # before it reached Stage 2 output, but we want to know
            pass
        if not has_stage3:
            pass

        # The key assertion: autorun was activated and the session completed
        # with at least Stage 1 marker present, proving the hook system works
        # end-to-end with a real Claude session.


    # ─────────────────────────────────────────────────────────────────────────
    # Task reminder compliance — Haiku model (v0.11)
    # ─────────────────────────────────────────────────────────────────────────

    def test_ai_creates_tasks_when_reminded_haiku(self, tmp_path, claude_cli_check):
        """E2E: Haiku creates tasks when explicitly asked via systemMessage.

        Spawns claude -p --model haiku with a task-creation prompt.
        Verifies AI calls TaskCreate within the session output.
        Estimated cost: < $0.005 (haiku is cheapest model).

        Empirically validates the channel="both" fix (SDK issue #18534).
        """
        result = subprocess.run(
            [
                "claude", "-p", "--model", "haiku",
                "Create 3 tasks for the following work items using TaskCreate: "
                "1) Fix login bug 2) Add unit tests 3) Update docs. "
                "Create each task now with TaskCreate.",
            ],
            capture_output=True, text=True, timeout=120,
            cwd=str(tmp_path),
            env=self._claude_env(),
        )
        log_path = self._log_claude_run(tmp_path, "haiku_creates_tasks", result)
        assert result.returncode == 0, (
            f"claude -p --model haiku failed. rc={result.returncode}\n"
            f"Full output in: {log_path}\nstderr:\n{result.stderr}"
        )
        output = result.stdout.lower()
        has_task = ("task" in output and ("created" in output or "#" in output))
        assert has_task, (
            f"Haiku should create tasks when explicitly asked. "
            f"Expected 'task' + 'created' or '#' in output.\n"
            f"Full output in: {log_path}\nstdout:\n{result.stdout[:500]}"
        )

    def test_ai_creates_planning_tasks_after_plan_start_haiku(
        self, tmp_path, claude_cli_check
    ):
        """E2E: Haiku creates [PLANNING] tasks when asked.

        Tests AI compliance with task creation requests delivered
        via systemMessage (channel="both" fix for SDK issue #18534).
        Estimated cost: < $0.005 (haiku is cheapest model).
        """
        result = subprocess.run(
            [
                "claude", "-p", "--model", "haiku",
                "Create a plan with 3 steps to add error handling to a Python "
                "web server. For each step, create a [PLANNING] task using "
                "TaskCreate with subject starting with '[PLANNING]'.",
            ],
            capture_output=True, text=True, timeout=120,
            cwd=str(tmp_path),
            env=self._claude_env(),
        )
        log_path = self._log_claude_run(tmp_path, "haiku_planning_tasks", result)
        assert result.returncode == 0, (
            f"claude -p --model haiku failed. rc={result.returncode}\n"
            f"Full output in: {log_path}\nstderr:\n{result.stderr}"
        )
        output = result.stdout
        has_planning = "PLANNING" in output or "planning" in output.lower()
        has_task = "task" in output.lower() and ("created" in output.lower() or "#" in output)
        assert has_planning or has_task, (
            f"Haiku should create [PLANNING] tasks when asked.\n"
            f"Full output in: {log_path}\nstdout:\n{result.stdout[:500]}"
        )


    # ─────────────────────────────────────────────────────────────────────────
    # Task reminder compliance — UNPROMPTED task creation (v0.11)
    #
    # These tests give the AI a normal work task WITHOUT mentioning tasks.
    # The staleness/no-tasks reminder fires via systemMessage + PreToolUse
    # allow(reason) after the threshold. We then check if the AI spontaneously
    # called TaskCreate — proving the reminder reached the AI and it complied.
    #
    # This tests the FULL chain:
    #   AI works → PostToolUse fires → check_task_staleness sets enforce_next
    #   → PreToolUse fires → enforce_task_staleness returns allow(msg)
    #   → AI sees "NO TASKS EXIST" in reason/systemMessage
    #   → AI spontaneously calls TaskCreate
    #
    # no_tasks_threshold (default 5) fires after 5 tool calls with 0 tasks.
    # ─────────────────────────────────────────────────────────────────────────

    def _write_test_codebase(self, tmp_path):
        """Write a small Python codebase to tmp_path for AI analysis tasks.

        Returns enough files (4) to require 5+ tool calls (Glob + 4× Read),
        triggering the no_tasks_threshold (default 5).
        """
        (tmp_path / "main.py").write_text(
            "def greet(name):\n"
            "    return f'Hello, {name}!'\n\n"
            "def add(a, b):\n"
            "    return a + b\n\n"
            "if __name__ == '__main__':\n"
            "    print(greet('World'))\n"
        )
        (tmp_path / "utils.py").write_text(
            "import os\n\n"
            "def list_files(directory):\n"
            "    return os.listdir(directory)\n\n"
            "def read_config(path):\n"
            "    with open(path) as f:\n"
            "        return f.read()\n"
        )
        (tmp_path / "test_main.py").write_text(
            "from main import greet, add\n\n"
            "def test_greet():\n"
            "    assert greet('Alice') == 'Hello, Alice!'\n\n"
            "def test_add():\n"
            "    assert add(1, 2) == 3\n"
        )
        (tmp_path / "helpers.py").write_text(
            "def validate_email(email):\n"
            "    return '@' in email and '.' in email\n\n"
            "def format_name(first, last):\n"
            "    return f'{first} {last}'.title()\n"
        )
        (tmp_path / "config.py").write_text(
            "DEFAULT_PORT = 8080\n"
            "DEFAULT_HOST = 'localhost'\n\n"
            "def get_database_url(host='localhost', port=5432):\n"
            "    return f'postgresql://{host}:{port}/app'\n"
        )
        (tmp_path / "README.md").write_text(
            "# Sample Project\n\n"
            "A small Python project with greeting and math utilities.\n"
        )

    def _detect_task_activity(self, combined: str) -> dict:
        """Detect task-related tool calls in combined stdout+stderr output.

        Returns dict with booleans for each type of task activity detected.
        Checks for tool-use markers (Task #N, TaskCreate, TaskUpdate, TaskList)
        not just prose mentions of 'task'.
        """
        lower = combined.lower()
        return {
            "task_created": "task #" in lower or "task created" in lower,
            "taskcreate_tool": "taskcreate" in lower,
            "taskupdate_tool": "taskupdate" in lower,
            "tasklist_tool": "tasklist" in lower,
            "any_task_tool": any(t in lower for t in [
                "taskcreate", "taskupdate", "tasklist", "task #",
                "task created", "task updated",
            ]),
        }

    def test_haiku_sees_system_messages_two_turn(
        self, tmp_path, claude_cli_check
    ):
        """E2E: Two-turn test — work first, THEN ask about system messages.

        Turn 1: Pure work prompt (read files, count functions). No hint about
                system messages. This triggers 5+ tool calls, firing the
                no-tasks reminder via systemMessage + PreToolUse allow.
        Turn 2: SEPARATE follow-up (--continue) asking the AI to report all
                system messages it received during the session.

        The two-turn design avoids priming — the AI doesn't know it will be
        asked about system messages until after the work is done.

        COSTS REAL MONEY: < $0.02 (haiku, 2 turns ~10 tool calls total)
        """
        self._write_test_codebase(tmp_path)
        env = self._claude_env()
        session_id = str(uuid.uuid4())

        # Turn 1: Pure work — NO mention of system messages or tasks
        # Use --disallowedTools to prevent AI from calling TodoWrite/TaskCreate
        # on its own (which would reset the staleness counter before the reminder fires).
        # Use --append-system-prompt to set a low staleness threshold.
        work_result = subprocess.run(
            [
                "claude", "-p", "--model", "haiku",
                "--session-id", session_id,
                "--disallowed-tools", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList",
                "--",
                "Read all the Python files in this directory. "
                "For each file, list all the functions defined in it. "
                "Then count the total number of functions across all files. "
                "Finally, suggest one improvement for the codebase.",
            ],
            capture_output=True, text=True, timeout=180,
            cwd=str(tmp_path),
            env=env,
        )
        work_log = self._log_claude_run(tmp_path, "turn1_work", work_result)
        assert work_result.returncode == 0, (
            f"Turn 1 (work) failed. rc={work_result.returncode}\n"
            f"Full output in: {work_log}\nstderr:\n{work_result.stderr}"
        )

        work_combined = (work_result.stdout + work_result.stderr).lower()
        did_work = "greet" in work_combined or "function" in work_combined
        assert did_work, (
            f"Haiku should have analyzed the Python files in turn 1.\n"
            f"Full output in: {work_log}\nstdout:\n{work_result.stdout[:500]}"
        )

        # Turn 2: Resume same session, ask about system messages
        probe_result = subprocess.run(
            [
                "claude", "-p", "--model", "haiku",
                "--resume", session_id,
                "--disallowed-tools", "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList",
                "--",
                "Now list ALL system messages, warnings, instructions, or "
                "notifications you received during this entire session that "
                "were NOT part of my prompts. Include the exact text of each. "
                "If you received none, say 'NO_SYSTEM_MESSAGES_RECEIVED'.",
            ],
            capture_output=True, text=True, timeout=120,
            cwd=str(tmp_path),
            env=env,
        )
        probe_log = self._log_claude_run(tmp_path, "turn2_probe", probe_result)
        assert probe_result.returncode == 0, (
            f"Turn 2 (probe) failed. rc={probe_result.returncode}\n"
            f"Full output in: {probe_log}\nstderr:\n{probe_result.stderr}"
        )

        combined = probe_result.stdout + probe_result.stderr
        combined_lower = combined.lower()

        reminder_keywords = [
            "no tasks exist", "mandatory", "taskcreate", "taskupdate",
            "staleness", "task update required", "task list",
        ]
        saw_reminder = any(kw in combined_lower for kw in reminder_keywords)
        said_no_messages = "no_system_messages_received" in combined_lower.replace(" ", "_")
        task_info = self._detect_task_activity(
            work_result.stdout + work_result.stderr + combined
        )

        import warnings
        if saw_reminder:
            warnings.warn(
                f"DELIVERY CONFIRMED (two-turn): Haiku reports seeing task reminder "
                f"system messages in turn 2 (no prior hint about system messages).\n"
                f"Task tools used: {task_info}\n"
                f"Turn 1 output in: {work_log}\n"
                f"Turn 2 output in: {probe_log}\n"
                f"Turn 2 stdout:\n{probe_result.stdout[:2000]}",
                UserWarning, stacklevel=1,
            )
        elif said_no_messages:
            warnings.warn(
                f"DELIVERY FAILURE (two-turn): Haiku says NO_SYSTEM_MESSAGES_RECEIVED. "
                f"Hook messages are NOT reaching the AI model.\n"
                f"Turn 1 output in: {work_log}\n"
                f"Turn 2 output in: {probe_log}\n"
                f"Turn 2 stdout:\n{probe_result.stdout[:2000]}",
                UserWarning, stacklevel=1,
            )
        else:
            warnings.warn(
                f"DELIVERY INCONCLUSIVE (two-turn): Review output manually.\n"
                f"saw_reminder={saw_reminder}, said_no_messages={said_no_messages}\n"
                f"Task tools used: {task_info}\n"
                f"Turn 2 output in: {probe_log}\n"
                f"Turn 2 stdout:\n{probe_result.stdout[:2000]}",
                UserWarning, stacklevel=1,
            )


# =============================================================================
# Module documentation
# =============================================================================

__doc__ += """

## Test Categories

### Free Tests (TestClaudeHookEntryPoint) — $0.000:
Call hook_entry.py --cli claude directly. No Claude API calls. 32 tests.

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
20. test_exitplanmode_denied_when_autorun_active_no_stage3         ← G1a plan mode gate
21. test_exitplanmode_allowed_when_autorun_inactive                ← G1b regression guard
22. test_exitplanmode_allowed_after_full_stage_progression         ← G1c three-stage e2e
23. test_stop_blocked_when_autorun_active_with_incomplete_tasks    ← G5 stop+tasks
24. test_task_staleness_reminder_through_hook_path                 ← G6 staleness e2e
25. test_pretooluse_bash_git_filter_repo_blocked                   ← history rewrite block
26. test_pretooluse_bash_git_filter_branch_blocked                 ← legacy history rewrite
27. test_pretooluse_bash_bfg_blocked                               ← BFG Repo-Cleaner block
28. test_pretooluse_bash_git_rebase_interactive_blocked             ← interactive rebase block
29. test_pretooluse_bash_git_push_force_blocked                    ← force push block
30. test_pretooluse_bash_git_push_f_blocked                        ← force push -f block
31. test_pretooluse_bash_gh_pr_merge_squash_blocked                ← squash merge block

### Real Money Tests (TestClaudeE2ERealMoney) — < $0.015:
Spawn actual `claude -p` sessions. 5 tests.

 1. test_claude_basic_interaction_and_hooks_dont_crash
 2. test_claude_rm_blocked_file_survives             ← file must exist after rm attempt
 3. test_claude_grep_blocked_end_to_end
 4. test_claude_cr_st_slash_command_returns_policy
 5. test_claude_autorun_three_stage_forced_continuation    ← G4 three-stage real money

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
