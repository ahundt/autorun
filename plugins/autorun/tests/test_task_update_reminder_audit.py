"""Regression tests for the longitudinal task-reminder audit."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parents[3]
AUDIT_PATH = REPO_ROOT / "scripts" / "audit_task_update_reminders_longitudinal.py"
SPEC = importlib.util.spec_from_file_location("task_reminder_audit", AUDIT_PATH)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


def codex_row(source: str, *, tool_name: str = "exec") -> dict[str, str]:
    return {
        "provider": "codex",
        "kind": "tool_call",
        "role": "assistant",
        "tool_name": tool_name,
        "content": json.dumps({"args": source}),
    }


def test_nested_codex_update_plan_is_progress_in_python_and_sql() -> None:
    direct = codex_row('const result = await tools.update_plan({plan:[{step:"audit",status:"in_progress"}]});')
    parallel = codex_row(
        'const [plan, status] = await Promise.all([\n  tools.update_plan({plan:[{step:"audit",status:"completed"}]}),\n  tools.get_goal({})\n]);'
    )
    quoted_search = codex_row('const result = await tools.exec_command({cmd:"rg \\"await tools.update_plan(\\" scripts"});')
    direct_tool = codex_row("", tool_name="update_plan")

    assert AUDIT.is_progress_tool(direct)
    assert AUDIT.is_progress_tool(parallel)
    assert AUDIT.is_progress_tool(direct_tool)
    assert not AUDIT.is_progress_tool(quoted_search)
    assert AUDIT.classify_progress(direct) == "plan_snapshot"

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE messages (provider TEXT, tool_name TEXT, content TEXT)")
    rows = [direct, parallel, quoted_search, direct_tool]
    connection.executemany(
        "INSERT INTO messages(provider, tool_name, content) VALUES (?, ?, ?)",
        [(row["provider"], row["tool_name"], row["content"]) for row in rows],
    )
    detected = connection.execute(f"SELECT CASE WHEN {AUDIT.progress_tool_sql('m')} THEN 1 ELSE 0 END FROM messages AS m ORDER BY rowid").fetchall()
    assert detected == [(1,), (1,), (0,), (1,)]


def test_help_explains_frozen_index_and_reproducible_cutoff() -> None:
    completed = subprocess.run(
        [sys.executable, str(AUDIT_PATH), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "existing-only" in completed.stdout
    assert "reproducible" in completed.stdout
    assert "25 initial / 50 subsequent" in completed.stdout
