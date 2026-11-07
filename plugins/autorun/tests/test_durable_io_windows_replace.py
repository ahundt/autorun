"""Atomic publication must survive a reader holding the target open on Windows.

Windows refuses to rename over a file another handle has open without delete
sharing, which is how Python's own open() reads (WinError 5 or 32). The daemon
rewrites its port file while hook clients read it, so a bare os.replace failed
the publication. pip retries the same call for about a second
(src/pip/_internal/utils/filesystem.py: retry(stop_after_delay=1)(os.replace)).
"""

from __future__ import annotations

import os
import threading

import pytest

from autorun import durable_io


def test_a_publication_waits_out_a_reader_holding_the_file(tmp_path):
    """Runs everywhere; on Windows the open handle really blocks the rename."""
    target = tmp_path / "daemon.port"
    target.write_text("1", encoding="utf-8")
    reader = open(target, encoding="utf-8")
    release = threading.Timer(durable_io.REPLACE_RETRY_SECONDS / 4, reader.close)
    release.start()
    try:
        durable_io.atomic_write_text(target, "2")
    finally:
        release.join()
        reader.close()
    assert target.read_text(encoding="utf-8") == "2"


def _flaky_replace(failures: int):
    real = os.replace
    calls = []

    def replace(src, dst):
        calls.append(dst)
        if len(calls) <= failures:
            raise PermissionError(13, "Access is denied")
        real(src, dst)

    return replace, calls


def test_windows_retries_a_denied_rename_until_it_succeeds(tmp_path, monkeypatch):
    replace, calls = _flaky_replace(failures=2)
    monkeypatch.setattr(durable_io, "_RETRY_DENIED_REPLACE", True)
    monkeypatch.setattr(durable_io.os, "replace", replace)
    monkeypatch.setattr(durable_io, "sync_directory", lambda _path: None)
    target = tmp_path / "state.json"

    durable_io.atomic_write_json(target, {"ok": True})

    assert len(calls) == 3
    assert target.read_text(encoding="utf-8") == '{"ok": true}'


def test_windows_gives_up_after_the_retry_budget_and_leaves_no_staged_file(tmp_path, monkeypatch):
    replace, calls = _flaky_replace(failures=10**6)
    monkeypatch.setattr(durable_io, "_RETRY_DENIED_REPLACE", True)
    monkeypatch.setattr(durable_io.os, "replace", replace)
    monkeypatch.setattr(durable_io, "REPLACE_RETRY_SECONDS", 0.2)

    with pytest.raises(PermissionError):
        durable_io.atomic_write_text(tmp_path / "state.json", "x")

    assert len(calls) > 1
    assert list(tmp_path.iterdir()) == []


def test_posix_does_not_retry_a_denied_rename(tmp_path, monkeypatch):
    """On POSIX a PermissionError is a real permission problem, not a reader."""
    replace, calls = _flaky_replace(failures=1)
    monkeypatch.setattr(durable_io, "_RETRY_DENIED_REPLACE", False)
    monkeypatch.setattr(durable_io.os, "replace", replace)

    with pytest.raises(PermissionError):
        durable_io.atomic_write_text(tmp_path / "state.json", "x")

    assert len(calls) == 1


def test_a_config_file_saved_in_another_encoding_falls_back_to_defaults(tmp_path, monkeypatch):
    """Reading as UTF-8 can raise UnicodeDecodeError, which is not a
    JSONDecodeError: a config saved by a cp1252 editor must fall back to the
    defaults, as malformed JSON does, instead of crashing the hook."""
    from autorun import plan_export, task_lifecycle

    saved = tmp_path / "config.json"
    saved.write_bytes('{"note": "café"}'.encode("cp1252"))
    monkeypatch.setattr(plan_export, "CONFIG_PATH", saved)
    monkeypatch.setattr(plan_export, "LEGACY_CONFIG_PATH", tmp_path / "absent.json")
    monkeypatch.setattr(task_lifecycle, "CONFIG_PATH", saved)
    monkeypatch.setattr(task_lifecycle, "PLAN_NOTIFY_CONFIG_PATH", saved)

    assert plan_export.read_config_data() == {}
    assert task_lifecycle.TaskLifecycleConfig.load() == task_lifecycle.TaskLifecycleConfig()
    assert task_lifecycle.PlanNotifyConfig.load() == task_lifecycle.PlanNotifyConfig()
