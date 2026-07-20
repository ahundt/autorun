"""End-to-end plan-export delivery through a real hook process.

These exercise the boundary the in-process tests in test_plan_export_class.py
cannot: a genuine `hook_entry.py` subprocess, isolated from the shared daemon
(AUTORUN_USE_DAEMON=0), writing to a temporary project directory.

What they pin, per harness:
  1. Success — the archive destination reaches the AI's own context
     (hookSpecificOutput.additionalContext), not only the human terminal.
     Without this the AI keeps editing the original working copy, unaware a
     snapshot exists.
  2. Mistakes — a plan that cannot be exported must produce a warning the AI
     can also see. A silently-dropped failure is worse than a duplicate
     message: the AI proceeds believing the plan was archived.

Free to run: no model calls, so these are not gated behind
AUTORUN_ENABLE_TESTS_THAT_COST_REAL_MONEY.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from e2e_support import run_isolated_hook

PLUGIN_ROOT = Path(__file__).parent.parent


def _hook_script() -> Path:
    script = PLUGIN_ROOT / "hooks" / "hook_entry.py"
    if not script.exists():
        pytest.skip(f"hook_entry.py not found at {script}")
    return script


def _post_tool_use_event(cli: str) -> str:
    """Native PostToolUse event name for the harness.

    Gemini renames the tool-lifecycle hooks (PostToolUse -> AfterTool);
    Claude and Qwen keep the Claude-style names.
    """
    return "AfterTool" if cli == "gemini" else "PostToolUse"


def _plan_tool_name(cli: str) -> str:
    """Both spellings are in PLAN_TOOLS (config.py); each harness has its own.

    Gemini CLI and Qwen Code both expose a native snake_case `exit_plan_mode`
    tool; Claude Code uses `ExitPlanMode`.
    """
    return "ExitPlanMode" if cli == "claude" else "exit_plan_mode"


def _run_exit_plan_mode(cli, project_dir, plan_path, session_id):
    payload = {
        "hook_event_name": _post_tool_use_event(cli),
        "session_id": session_id,
        "cwd": str(project_dir),
        "tool_name": _plan_tool_name(cli),
        "tool_input": {"cwd": str(project_dir)},
        "tool_response": {"filePath": str(plan_path)} if plan_path else {},
    }
    result = run_isolated_hook(
        plugin_root=PLUGIN_ROOT,
        hook_script=_hook_script(),
        cli=cli,
        payload=payload,
    )
    assert result.returncode == 0, f"hook failed: {result.stderr}"
    if not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def _ai_visible_text(response: dict) -> str:
    return response.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen"])
def test_export_destination_reaches_ai_context(cli, tmp_path):
    """The AI must be able to answer "where was this plan archived?" from context."""
    project_dir = tmp_path / "project"
    (project_dir / "notes").mkdir(parents=True)
    # Plan lives outside the project dir, standing in for a customized Claude
    # plansDirectory or a Gemini/Conductor project-relative plans location.
    plan_path = tmp_path / "elsewhere" / "my-plan.md"
    plan_path.parent.mkdir(parents=True)
    # Content must be unique per harness: export() dedups on content hash
    # across the whole tracking store, so a shared body would make the second
    # and third parametrized runs take the "Already exported" branch and
    # archive nothing.
    body = f"# E2E Plan for {cli}\n\nPlan body {uuid.uuid4().hex}.\n"
    plan_path.write_text(body)

    response = _run_exit_plan_mode(
        cli, project_dir, plan_path, f"pe-e2e-{cli}-{uuid.uuid4().hex[:8]}"
    )

    ai_text = _ai_visible_text(response)
    assert ai_text, (
        f"[{cli}] export produced no AI-visible additionalContext; the AI "
        f"cannot learn where the plan was archived. Response: {response!r}"
    )
    assert str(plan_path) in ai_text, (
        f"[{cli}] AI-visible text must carry the exact source path. Got: {ai_text!r}"
    )
    assert "notes" in ai_text, (
        f"[{cli}] AI-visible text must carry the archive destination. Got: {ai_text!r}"
    )

    # The archive must actually exist on disk, inside the payload's project dir
    # — not the hook process's own cwd.
    archived = list((project_dir / "notes").glob("*.md"))
    if not archived:
        # Report where it actually landed: a mismatch means project_dir was
        # resolved from something other than the payload's cwd.
        strays = [
            str(p) for p in Path(tmp_path).rglob("*.md") if p != plan_path
        ] + [str(p) for p in Path.cwd().glob("notes/*.md")][:5]
        pytest.fail(
            f"[{cli}] message claimed an export but {project_dir / 'notes'} is "
            f"empty. Hook response: {response!r}. Other .md files found: {strays}"
        )
    # The archive prepends YAML frontmatter (session_id, original_path,
    # export_timestamp, export_destination) ahead of the verbatim plan body.
    archived_text = archived[0].read_text()
    assert body in archived_text, (
        f"[{cli}] archived copy must contain the source plan verbatim. "
        f"Got: {archived_text[:300]!r}"
    )
    assert str(plan_path) in archived_text, (
        f"[{cli}] archive frontmatter must record the original plan path so the "
        f"snapshot can be traced back to its source"
    )


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen"])
def test_missing_plan_warning_reaches_ai_context(cli, tmp_path):
    """A mistake path: no resolvable plan must warn the AI, not fail silently."""
    project_dir = tmp_path / "project"
    (project_dir / "notes").mkdir(parents=True)

    response = _run_exit_plan_mode(
        cli, project_dir, None, f"pe-e2e-miss-{cli}-{uuid.uuid4().hex[:8]}"
    )

    ai_text = _ai_visible_text(response)
    assert "no plan content found" in ai_text.lower(), (
        f"[{cli}] the AI must be told the export did not happen, or it will "
        f"assume the plan was archived. Got: {ai_text!r}"
    )
    assert not list((project_dir / "notes").glob("*.md")), (
        f"[{cli}] nothing should be archived when no plan was found"
    )


@pytest.mark.parametrize("cli", ["claude", "gemini", "qwen"])
def test_deleted_plan_file_does_not_claim_success(cli, tmp_path):
    """A mistake path: plan referenced but deleted before the hook runs.

    The hook must not report a successful archive for a file it never copied.
    """
    project_dir = tmp_path / "project"
    (project_dir / "notes").mkdir(parents=True)
    plan_path = tmp_path / "gone" / "vanished-plan.md"
    plan_path.parent.mkdir(parents=True)
    # Referenced in the payload but never created on disk.

    response = _run_exit_plan_mode(
        cli, project_dir, plan_path, f"pe-e2e-gone-{cli}-{uuid.uuid4().hex[:8]}"
    )

    ai_text = _ai_visible_text(response)
    archived = list((project_dir / "notes").glob("*.md"))
    if archived:
        pytest.fail(
            f"[{cli}] archived {archived} for a plan file that does not exist"
        )
    assert "exported from" not in ai_text.lower(), (
        f"[{cli}] must not report a successful export for a missing plan file. "
        f"Got: {ai_text!r}"
    )
