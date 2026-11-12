"""Older builds open a database this build migrated, through their own store code.

A rollback is ``uv tool install autorun-ai==1.0.0rc3`` or a marketplace
downgrade: the older build's code meets whatever this build left on disk, and
nothing of this build runs first. So each older build's own ``session_manager``
is extracted from git (``git archive``) and run in a subprocess against a
database this build migrated, through that build's ``_build_store`` with a
COMPLETE receipt, the daemon start path.

1.0.0rc2 (``3032036b``) never reached PyPI; its session_manager.py is
byte-identical to rc1's and rc3's, so rc1 and rc3 cover it.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest

from autorun import session_manager as sm
from autorun import task_status
from test_state_schema_revision import _read, _receipt_backed
from test_task_status_not_in_schema import SESSION, TASK_ROWS, _create_pre_3472b3ee_database

REPO_ROOT = Path(sm.__file__).resolve().parents[4]
OLDER_BUILDS = ["v1.0.0rc1", "v1.0.0rc3", "bdcdb9ac"]

# Statuses a migrated database can hold: two every build knows, one added
# after the oldest databases were created, and one no build knows yet.
SEEDED = {
    "t-pending": "pending",
    "t-returned": "delegation-returned",
    "t-done": "completed",
    "t-future": "zz-future-status",
}

_OLDER_BUILD_PROBE = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, sys.argv[1])
    from autorun import session_manager as sm
    assert sm.__file__.startswith(sys.argv[1]), sm.__file__
    result = {}
    try:
        store = sm._build_store(sys.argv[2])
    except Exception as error:
        print(json.dumps({"refused": str(error)}))
        raise SystemExit(0)
    repo = sm.TaskRepository(store)
    session = sys.argv[3]
    result["incomplete"] = sorted(task["id"] for task in repo.list_incomplete(session))
    result["listed"] = sorted(repo.list_tasks(session))
    record = repo.get_task(session, "t-pending")
    repo.put_task(session, "t-pending", dict(record, status="in_progress"))
    result["put_task"] = repo.get_task(session, "t-pending")["status"]
    try:
        repo.mutate_task(session, "t-future", lambda task: task)
        result["mutate_unknown"] = "kept"
    except Exception as error:
        result["mutate_unknown"] = str(error)
    repo.mutate_task(session, "t-future", lambda task: dict(task, status="completed"))
    result["mutate_to_known"] = repo.get_task(session, "t-future")["status"]
    retention = sm.StateRetention(store, sm.RetentionPolicy())
    result["has_unfinished"] = retention._has_unfinished_tasks(session)
    print(json.dumps(result))
""")


def _extract(ref: str, destination: Path) -> Path:
    archive = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "archive", "--format=tar", ref, "plugins/autorun/src"],
        capture_output=True, check=False,
    )
    assert archive.returncode == 0, (
        f"git archive {ref} failed ({archive.stderr.decode(errors='replace').strip()}). "
        "The checkout needs the release tags and history: fetch with depth 0."
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(destination, filter="data")
        else:  # Python releases before the extraction filters
            tar.extractall(destination)
    return destination / "plugins" / "autorun" / "src"


def _migrated_database(state_dir: Path, *, user_version: int = 2) -> Path:
    db_path = _receipt_backed(state_dir, _create_pre_3472b3ee_database)
    sm._reset_for_testing()
    try:
        store = sm._build_store(str(state_dir))  # this build migrates it
    finally:
        sm._reset_for_testing()
    repository = sm.TaskRepository(store)
    for task_id, status in SEEDED.items():
        record = {"id": task_id, "subject": f"Task {task_id}", "status": status}
        if status in task_status.STATUS_POLICY:
            repository.put_task(SESSION, task_id, record)
        else:  # a newer build wrote it; this one refuses to
            conn = sqlite3.connect(db_path)
            conn.execute(
                "INSERT INTO tasks (session, task_id, status, updated_at, payload_json) VALUES (?, ?, ?, 1.0, ?)",
                (SESSION, task_id, status, json.dumps({"id": task_id, "subject": f"Task {task_id}"})),
            )
            conn.commit()
            conn.close()
    if user_version != 2:
        conn = sqlite3.connect(db_path)
        conn.execute(f"PRAGMA user_version = {user_version}")
        conn.commit()
        conn.close()
    return db_path


def _run_older_build(ref: str, tmp_path: Path, state_dir: Path) -> dict:
    src = _extract(ref, tmp_path / "older")
    home = tmp_path / "home"
    home.mkdir()
    env = dict(
        os.environ,
        HOME=str(home),
        USERPROFILE=str(home),
        AUTORUN_HOME=str(tmp_path / "ar-home"),
        AUTORUN_TEST_STATE_DIR=str(state_dir),
    )
    completed = subprocess.run(
        [sys.executable, "-c", _OLDER_BUILD_PROBE, str(src), str(state_dir), SESSION],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("ref", OLDER_BUILDS)
def test_older_builds_open_and_gate_a_migrated_database(ref, tmp_path):
    state_dir = tmp_path / "sessions"
    db_path = _migrated_database(state_dir)

    result = _run_older_build(ref, tmp_path, state_dir)

    assert "refused" not in result, f"{ref} refused the migrated database: {result}"
    stored = {**SEEDED, **{task_id: status for task_id, status, _payload in TASK_ROWS}}
    blocking = sorted(
        task_id for task_id, status in stored.items()
        if status not in task_status.NON_BLOCKING_TASK_STATUSES
    )
    assert result["incomplete"] == blocking, (
        f"{ref}'s Stop gate does not see what this build's policy blocks on: {result['incomplete']}"
    )
    assert result["listed"] == sorted(stored)
    assert result["put_task"] == "in_progress"
    assert result["mutate_unknown"] == "Unknown task status 'zz-future-status'"
    assert result["mutate_to_known"] == "completed"
    assert result["has_unfinished"] is True
    assert _read(db_path, "PRAGMA integrity_check") == [("ok",)]


@pytest.mark.parametrize("ref", OLDER_BUILDS)
def test_older_builds_refuse_a_bumped_user_version(ref, tmp_path):
    """Why user_version stays 2: every older build refuses anything else at start.

    A change that raises it must delete this test, ship a reverse step, and
    say so in its release notes (plugins/autorun/AGENTS.md).
    """
    state_dir = tmp_path / "sessions"
    db_path = _migrated_database(state_dir, user_version=3)

    result = _run_older_build(ref, tmp_path, state_dir)

    assert result == {"refused": f"Database {db_path} is not an autorun schema generation."}
