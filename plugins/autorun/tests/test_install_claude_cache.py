"""Claude's plugin cache: the fallback that fills it, and the paths inside it.

Two failures these pin, both silent in the installer being replaced. A failed
`claude plugin install` left no files and every hook inert, with the install
reporting the CLI error and moving on. And an unexpanded `${CLAUDE_PLUGIN_ROOT}`
became a path that does not exist, so the hook never ran and nothing said why.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("AUTORUN_HOME", "/tmp/autorun-test-home")
os.environ.setdefault("AUTORUN_TEST_STATE_DIR", "/tmp/autorun-test-state")

from autorun.installer import claude, fs, runtime  # noqa: E402
from autorun.installer.fs import OWNED_MARKER_NAME, read_marker  # noqa: E402


class Harness:
    """A synthetic harness. The real config directory comes from the registry,
    and a literal here would be a second authority for that question."""

    name = "fake-cache-harness"
    config_dir = "~/.fake-cache-harness"
    config_dir_env_vars: tuple[str, ...] = ()


@pytest.fixture
def source(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "hooks").mkdir(parents=True)
    (plugin / ".claude-plugin").mkdir()
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "ar", "version": "1.2.3"}), encoding="utf-8"
    )
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": ["${CLAUDE_PLUGIN_ROOT}/hooks/entry.py"]}}),
        encoding="utf-8",
    )
    return plugin


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


def install(source: Path, home: Path, version: str = "1.2.3") -> Path:
    written = claude.cache_fallback(
        source, Harness(), market="autorun", plugin="ar", version=version, home=home
    )
    assert written is not None
    return written


# ─── The fallback ────────────────────────────────────────────────────────────


def test_the_fallback_writes_the_cache_entry_the_cli_could_not(source, home):
    written = install(source, home)

    assert (written / "hooks" / "hooks.json").is_file()
    assert written.parts[-3:] == ("autorun", "ar", "1.2.3")


def test_the_fallback_records_file_hash_ownership(source, home):
    written = install(source, home)

    marker = read_marker(written)
    assert (written / OWNED_MARKER_NAME).is_file()
    assert marker is not None
    assert marker.plugin == "ar"
    assert "hooks/hooks.json" in marker.files


def test_same_version_cache_mismatch_names_stale_and_changed_files(source, home):
    written = install(source, home)
    (written / "skills" / "stale-skill").mkdir(parents=True)
    (written / "skills" / "stale-skill" / "SKILL.md").write_text("stale\n", encoding="utf-8")
    (written / "hooks" / "hook_entry.py").write_text("old hook\n", encoding="utf-8")

    mismatches = claude.cache_mismatches(source, written)

    assert "skills/stale-skill/SKILL.md" in mismatches
    assert "hooks/hook_entry.py" in mismatches


def test_same_version_refresh_compares_substituted_manifests_to_the_current_build(source, home):
    written = install(source, home)
    old = json.loads((written / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    old["hooks"].pop("PreToolUse")
    (written / "hooks" / "hooks.json").write_text(
        json.dumps(old), encoding="utf-8"
    )
    _record_cache(written)

    assert claude.refresh_cache(source, written) == written

    installed = json.loads(
        (written / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    assert "PreToolUse" in installed["hooks"]


def test_refresh_replaces_plugin_payload_in_place_and_preserves_managed_runtime(source, home):
    (source / "stale.txt").write_text("old payload\n", encoding="utf-8")
    written = install(source, home)
    runtime = written / ".venv" / "lib" / "python" / "site-packages"
    runtime.mkdir(parents=True)
    (runtime / "keep.txt").write_text("managed runtime\n", encoding="utf-8")
    (source / "stale.txt").unlink()
    root_inode = written.stat().st_ino
    venv_inode = (written / ".venv").stat().st_ino

    assert claude.refresh_cache(source, written) == written

    assert written.stat().st_ino == root_inode
    assert (written / ".venv").stat().st_ino == venv_inode
    assert not (written / "stale.txt").exists()
    assert (runtime / "keep.txt").read_text(encoding="utf-8") == "managed runtime\n"
    assert claude.cache_mismatches(source, written) == ()


def _write_round_trip_hook(root: Path, answer: str) -> None:
    from autorun.installer.runtime import cache_venv_python

    hooks = root / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "hook_entry.py").write_text(
        "import json, sys\njson.load(sys.stdin)\nprint(json.dumps({'answer': "
        + repr(answer)
        + "}))\n",
        encoding="utf-8",
    )
    (hooks / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"hooks": [{
            "type": "command",
            "command": (
                f"{cache_venv_python(Path('${CLAUDE_PLUGIN_ROOT}'))} "
                "${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py "
                "--cli claude --event PreToolUse"
            ),
        }]}]}}),
        encoding="utf-8",
    )


def _make_cache_python(root: Path) -> None:
    import shutil

    from autorun.installer.runtime import cache_venv_python

    python = cache_venv_python(root)
    python.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shutil.copy2(sys.executable, python)
        (root / ".venv" / "pyvenv.cfg").write_text(
            f"home = {Path(sys.executable).parent}\n", encoding="utf-8"
        )
    else:
        python.symlink_to(sys.executable)


def _record_cache(root: Path) -> None:
    assert fs.record_tree(root, plugin="ar", ownership_proof=lambda installed: installed == root)


def _round_trip(root: Path) -> str:
    import shlex

    document = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    command = document["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    command = command.replace("${CLAUDE_PLUGIN_ROOT}", str(root))
    result = subprocess.run(
        shlex.split(command),
        input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash"}),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)["answer"]


def test_refresh_crash_after_each_file_keeps_hook_entry_manifest_and_venv_usable(
    source, home
):
    _write_round_trip_hook(source, "new")
    written = install(source, home)
    _write_round_trip_hook(written, "old")
    _make_cache_python(written)
    _record_cache(written)
    venv = written / ".venv"
    steps = []

    def record(step: str) -> None:
        steps.append(step)

    claude.refresh_cache(source, written, after_step=record)
    assert steps

    for crash_after in range(1, len(steps) + 1):
        crashed = install(source, home, version=f"1.2.{crash_after + 3}")
        _write_round_trip_hook(crashed, "old")
        _make_cache_python(crashed)
        _record_cache(crashed)
        seen = 0

        def crash(_step: str) -> None:
            nonlocal seen
            seen += 1
            if seen == crash_after:
                raise RuntimeError("simulated refresh crash")

        with pytest.raises(RuntimeError, match="simulated refresh crash"):
            claude.refresh_cache(source, crashed, after_step=crash)

        assert (crashed / "hooks" / "hook_entry.py").is_file()
        assert (crashed / "hooks" / "hooks.json").is_file()
        assert (crashed / ".venv").is_dir()
        assert _round_trip(crashed) in {"old", "new"}
        interrupted_receipt = json.loads(
            (crashed / OWNED_MARKER_NAME).read_text(encoding="utf-8")
        )
        with claude._expected_cache_tree(
            source,
            crashed,
            hook_command=claude._installed_hook_command(source, crashed),
        ) as expected:
            assert interrupted_receipt["intent"]["files"] == fs.scan_tree(expected)

        assert claude.refresh_cache(source, crashed) == crashed
        assert claude.cache_mismatches(source, crashed) == ()
        with claude._expected_cache_tree(
            source,
            crashed,
            hook_command=claude._installed_hook_command(source, crashed),
        ) as expected:
            assert fs.scan_tree(crashed) == fs.scan_tree(expected)
        assert _round_trip(crashed) == "new"
        final_receipt = json.loads(
            (crashed / OWNED_MARKER_NAME).read_text(encoding="utf-8")
        )
        assert "intent" not in final_receipt


def test_interrupted_refresh_still_keeps_a_later_user_edit(source, home):
    _write_round_trip_hook(source, "new")
    crashed = install(source, home)
    _write_round_trip_hook(crashed, "old")
    _make_cache_python(crashed)
    _record_cache(crashed)

    def crash(_step: str) -> None:
        raise RuntimeError("simulated refresh crash")

    with pytest.raises(RuntimeError, match="simulated refresh crash"):
        claude.refresh_cache(source, crashed, after_step=crash)

    hook = crashed / "hooks" / "hook_entry.py"
    hook.write_text("user edit after interrupted refresh\n", encoding="utf-8")

    kept = []
    assert claude.refresh_cache(source, crashed, kept=kept) is None
    assert kept == ["hooks/hook_entry.py"]
    assert hook.read_text(encoding="utf-8") == "user edit after interrupted refresh\n"


def test_cache_lock_path_survives_install_refresh_and_uninstall(source, home):
    """Violation excluded: a later install unlinks the persistent cache lock."""
    written = install(source, home)
    lock = written.parent / fs.INSTALL_LOCK_NAME

    assert claude.refresh_cache(source, written, plugin="ar") == written
    assert lock.is_file()

    (source / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": ["changed"]}}), encoding="utf-8"
    )
    assert claude.refresh_cache(source, written, plugin="ar") == written
    assert lock.is_file()

    removed, _kept = claude.retire_cache_versions(
        Harness(), market="autorun", plugin="ar", home=home
    )
    assert removed == (written,)
    assert lock.is_file()


def test_retirement_removes_only_hash_proven_unmodified_cache_versions(source, home):
    current = install(source, home, version="1.2.4")
    old = install(source, home, version="1.2.3")
    edited = install(source, home, version="1.2.2")
    for directory in (old, edited):
        (directory / ".orphaned_at").write_text("2026-11-12T00:00:00Z\n", encoding="utf-8")
    (edited / "hooks" / "hooks.json").write_text("user edit\n", encoding="utf-8")
    unproven = claude.cache_dir(Harness(), market="autorun", plugin="ar", version="0.9.0", home=home)
    assert unproven is not None
    unproven.mkdir(parents=True)
    (unproven / "mine.txt").write_text("not proven\n", encoding="utf-8")

    removed, kept = claude.retire_cache_versions(
        Harness(), market="autorun", plugin="ar", keep=("1.2.4",), home=home
    )

    assert removed == (old,)
    lock = old.parent / fs.INSTALL_LOCK_NAME
    assert tuple(item.path for item in kept) == (unproven, edited, lock)
    assert kept[0].files == ("mine.txt",)
    assert kept[1].files == ("hooks/hooks.json",)
    assert kept[2].files == ("installer synchronization lock",)
    assert current.is_dir()
    assert not old.exists()
    assert (edited / "hooks" / "hooks.json").read_text(encoding="utf-8") == "user edit\n"
    assert (unproven / "mine.txt").is_file()


def test_retirement_keeps_a_symlinked_version_without_reading_its_target(
    source, home, tmp_path
):
    """Violation excluded: uninstall follows a version link outside the cache."""
    external = install(source, tmp_path / "external-home", version="9.9.9")
    before = {
        path.relative_to(external).as_posix(): path.read_bytes()
        for path in external.rglob("*")
        if path.is_file()
    }
    root = claude.cache_dir(
        Harness(), market="autorun", plugin="ar", version="linked", home=home
    )
    assert root is not None
    root.parent.mkdir(parents=True, exist_ok=True)
    root.symlink_to(external, target_is_directory=True)

    removed, kept = claude.retire_cache_versions(
        Harness(), market="autorun", plugin="ar", home=home
    )

    after = {
        path.relative_to(external).as_posix(): path.read_bytes()
        for path in external.rglob("*")
        if path.is_file()
    }
    assert removed == ()
    assert tuple(item.path for item in kept) == (root,)
    assert kept[0].files == ("symlinked or reparse-point version directory",)
    assert root.is_symlink()
    assert after == before
    assert (external / OWNED_MARKER_NAME).read_bytes() == before[OWNED_MARKER_NAME]


def test_retirement_removes_proven_files_but_keeps_mixed_cache_user_files(
    source, home
):
    """Violation excluded: one user file strands every proven cache payload file."""
    mixed = install(source, home, version="1.2.2")
    edited = mixed / "hooks" / "hooks.json"
    edited.write_text("user edit\n", encoding="utf-8")
    notes = mixed / "notes.txt"
    notes.write_text("user notes\n", encoding="utf-8")
    venv_file = mixed / ".venv" / "managed-by-claude.json"
    venv_file.parent.mkdir()
    venv_file.write_text("{}\n", encoding="utf-8")
    unchanged = mixed / ".claude-plugin" / "plugin.json"
    lock = mixed.parent / fs.INSTALL_LOCK_NAME
    lock.touch()

    removed, kept = claude.retire_cache_versions(
        Harness(), market="autorun", plugin="ar", home=home
    )

    assert removed == ()
    assert tuple(item.path for item in kept) == (mixed, lock)
    assert kept[0].files == (".venv/", "hooks/hooks.json", "notes.txt")
    assert kept[1].files == ("installer synchronization lock",)
    assert not unchanged.exists()
    assert not (mixed / OWNED_MARKER_NAME).exists()
    assert lock.is_file()
    assert edited.read_text(encoding="utf-8") == "user edit\n"
    assert notes.read_text(encoding="utf-8") == "user notes\n"
    assert venv_file.is_file()


def test_retirement_leaves_the_lock_and_its_cache_parent_directories(source, home):
    """Violation excluded: unlinking the lock splits installer lock authority."""
    only = install(source, home, version="1.2.2")
    plugin_root = only.parent
    market_root = plugin_root.parent
    lock = plugin_root / fs.INSTALL_LOCK_NAME
    lock.touch(exist_ok=True)

    removed, kept = claude.retire_cache_versions(
        Harness(), market="autorun", plugin="ar", home=home
    )

    assert removed == (only,)
    assert tuple(item.path for item in kept) == (lock,)
    assert kept[0].files == ("installer synchronization lock",)
    assert lock.is_file()
    assert plugin_root.is_dir()
    assert market_root.is_dir()


def test_refresh_keeps_an_edited_recorded_file_and_an_unrecorded_file(source, home):
    written = install(source, home)
    hook = written / "hooks" / "hooks.json"
    hook.write_text("user hook edit\n", encoding="utf-8")
    extra = written / "notes.txt"
    extra.write_text("user notes\n", encoding="utf-8")
    (source / "hooks" / "hooks.json").write_text("new shipped hook\n", encoding="utf-8")

    kept = []
    assert claude.refresh_cache(source, written, kept=kept) is None
    assert kept == ["hooks/hooks.json", "notes.txt"]

    assert hook.read_text(encoding="utf-8") == "user hook edit\n"
    assert extra.read_text(encoding="utf-8") == "user notes\n"


def test_unmarked_matching_cache_is_adopted_but_same_named_fork_is_kept(source, home):
    matching = install(source, home, version="1.2.4")
    (matching / OWNED_MARKER_NAME).unlink()
    assert claude.refresh_cache(source, matching) == matching
    assert read_marker(matching) is not None
    assert "${CLAUDE_PLUGIN_ROOT}" not in (
        matching / "hooks" / "hooks.json"
    ).read_text(encoding="utf-8")

    fork = install(source, home, version="1.2.5")
    (fork / OWNED_MARKER_NAME).unlink()
    (fork / "fork.txt").write_text("same manifest name, different payload\n", encoding="utf-8")
    kept = []
    assert claude.refresh_cache(source, fork, kept=kept) is None
    marker = read_marker(fork)
    assert marker is not None
    assert "hooks/hooks.json" in marker.files
    assert "fork.txt" not in marker.files
    assert kept == ["fork.txt"]
    assert (fork / "fork.txt").is_file()


def test_fallback_never_replaces_a_harness_managed_cache(source, home):
    written = install(source, home)
    runtime = written / ".venv" / "lib" / "site-packages" / "autorun"
    runtime.mkdir(parents=True)
    (runtime / "__init__.py").write_text("installed = True\n", encoding="utf-8")
    (written / "left-over.txt").write_text("from the previous copy\n", encoding="utf-8")

    assert claude.cache_fallback(
        source, Harness(), market="autorun", plugin="ar", version="1.2.3", home=home
    ) is None

    assert (written / "left-over.txt").read_text(encoding="utf-8") == "from the previous copy\n"
    assert (runtime / "__init__.py").read_text(encoding="utf-8") == "installed = True\n"


def test_native_cache_creation_wins_a_fallback_publication_race(
    source, home, monkeypatch
):
    target = claude.cache_dir(
        Harness(), market="autorun", plugin="ar", version="1.2.3", home=home
    )
    assert target is not None
    runtime = target / ".venv" / "lib" / "site-packages" / "autorun"
    real_copytree = fs.shutil.copytree

    def copytree(*args, **kwargs):
        result = real_copytree(*args, **kwargs)
        runtime.mkdir(parents=True, exist_ok=True)
        (runtime / "__init__.py").write_text("native = True\n", encoding="utf-8")
        return result

    monkeypatch.setattr(fs.shutil, "copytree", copytree)

    assert claude.cache_fallback(
        source, Harness(), market="autorun", plugin="ar", version="1.2.3", home=home
    ) is None
    assert (runtime / "__init__.py").read_text(encoding="utf-8") == "native = True\n"


def test_forced_refresh_stops_if_a_file_changes_after_its_backup(
    source, home, tmp_path, monkeypatch
):
    written = install(source, home)
    target = written / "hooks" / "hooks.json"
    target.write_text("user edit before backup\n", encoding="utf-8")
    original_park = fs._park_backup

    def park_then_edit(path, backup_root, *, name=""):
        backup = original_park(path, backup_root, name=name)
        if path == target:
            path.write_text("user edit after backup\n", encoding="utf-8")
        return backup

    monkeypatch.setattr(fs, "_park_backup", park_then_edit)

    with pytest.raises(RuntimeError, match="changed after backup"):
        claude.refresh_cache(
            source,
            written,
            force=True,
            backup_root=tmp_path / "backups",
        )

    assert target.read_text(encoding="utf-8") == "user edit after backup\n"
    marker = read_marker(written)
    assert marker is not None and marker.intended_files


def test_replace_retries_a_transient_windows_sharing_violation(
    source, home, monkeypatch
):
    from autorun import durable_io

    written = install(source, home)
    (source / "new.txt").write_text("new\n", encoding="utf-8")
    real_replace = durable_io.os.replace
    calls = 0

    def sharing_violation(src, dst):
        nonlocal calls
        if Path(dst) == written / "new.txt":
            calls += 1
            if calls <= 2:
                raise PermissionError("sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(durable_io, "_RETRY_DENIED_REPLACE", True)
    monkeypatch.setattr(durable_io, "REPLACE_RETRY_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(durable_io.os, "replace", sharing_violation)

    assert claude.refresh_cache(source, written) == written
    assert calls >= 3
    assert (written / "new.txt").read_text(encoding="utf-8") == "new\n"


def test_exhausted_replace_retry_keeps_intent_and_the_next_run_finishes(
    source, home, monkeypatch
):
    from autorun import durable_io

    written = install(source, home)
    (source / "new.txt").write_text("new\n", encoding="utf-8")
    real_replace = durable_io.os.replace

    monkeypatch.setattr(durable_io, "_RETRY_DENIED_REPLACE", True)
    monkeypatch.setattr(durable_io, "REPLACE_RETRY_SECONDS", 0)
    def sharing_violation(src, dst):
        if Path(dst) == written / "new.txt":
            raise PermissionError("sharing violation")
        return real_replace(src, dst)

    monkeypatch.setattr(durable_io.os, "replace", sharing_violation)

    with pytest.raises(PermissionError, match="sharing violation"):
        claude.refresh_cache(source, written)

    marker = read_marker(written)
    assert marker is not None and marker.intended_files
    monkeypatch.setattr(durable_io.os, "replace", real_replace)

    assert claude.refresh_cache(source, written) == written
    assert (written / "new.txt").read_text(encoding="utf-8") == "new\n"
    final = read_marker(written)
    assert final is not None and not final.intended_files


def test_cache_lock_wait_is_bounded_and_names_the_rerun_action(
    source, home, tmp_path
):
    """Violation excluded: a second cache refresh waits forever without a notice."""
    from filelock import FileLock

    written = install(source, home)
    lock_path = written.parent / fs.INSTALL_LOCK_NAME
    notices = []
    with FileLock(str(lock_path)):
        with pytest.raises(RuntimeError) as raised:
            claude.refresh_cache(
                source,
                written,
                lock_timeout_seconds=0.01,
                wait_notice=notices.append,
            )

    message = str(raised.value)
    assert notices == [f"waiting for another autorun install refreshing {written}"]
    assert str(lock_path) in message
    assert str(written) in message
    assert "0.01" in message
    assert "wait and rerun" in message


def test_cache_intent_replacement_runtime_and_receipt_share_one_lock(
    source, home, monkeypatch
):
    written = install(source, home)
    (source / "payload.py").write_text("current = True\n", encoding="utf-8")
    real_lock = runtime.PersistentFileLock
    acquisitions = 0
    active = 0

    class CountingLock:
        def __init__(self, path):
            self.inner = real_lock(path)

        def acquire(self, *args, **kwargs):
            nonlocal acquisitions, active
            acquisitions += 1
            result = self.inner.acquire(*args, **kwargs)
            active += 1
            return result

        def release(self):
            nonlocal active
            active -= 1
            return self.inner.release()

    monkeypatch.setattr(runtime, "PersistentFileLock", CountingLock)
    monkeypatch.setattr(
        fs,
        "FileLock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("nested cache lock acquisition")
        ),
    )
    callback_states = []

    assert claude.refresh_cache(
        source,
        written,
        before_hooks=lambda: callback_states.append(active),
    ) == written

    assert acquisitions == 1
    assert callback_states == [1]
    assert active == 0
    assert not read_marker(written).intended_files


def test_runtime_finishes_before_hooks_json_is_replaced(source, home):
    written = install(source, home)
    (source / "payload.py").write_text("current = True\n", encoding="utf-8")
    source_hook = source / "hooks" / "hooks.json"
    document = json.loads(source_hook.read_text(encoding="utf-8"))
    document["hooks"]["Stop"] = ["${CLAUDE_PLUGIN_ROOT}/hooks/entry.py"]
    source_hook.write_text(json.dumps(document), encoding="utf-8")
    order = []

    claude.refresh_cache(
        source,
        written,
        after_step=order.append,
        before_hooks=lambda: order.append("runtime ready"),
    )

    assert order.index("replace payload.py") < order.index("runtime ready")
    assert order.index("runtime ready") < order.index("replace hooks/hooks.json")


def test_matching_runtime_receipt_skips_a_second_venv_reinstall(
    source, tmp_path, monkeypatch
):
    cache = tmp_path / "cache"
    cache.mkdir()
    venv = cache / ".venv"
    python = runtime.cache_venv_python(cache)
    python.parent.mkdir(parents=True)
    python.write_text("interpreter placeholder\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text("home = isolated\n", encoding="utf-8")
    installs = []

    def run(argv, **_kwargs):
        if tuple(argv[1:3]) == ("-I", "-c"):
            return subprocess.CompletedProcess(argv, 0, str(venv) + "\n", "")
        installs.append(tuple(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runtime, "has_uv", lambda: True)

    first = runtime.install_cache_runtime(cache, source, run=run)
    receipt = json.loads((venv / ".autorun-runtime.json").read_text(encoding="utf-8"))
    second = runtime.install_cache_runtime(cache, source, run=run)

    assert first.ok and second.ok
    assert receipt["schema"] == 1 and receipt["install_mode"] == "non-editable"
    assert len(installs) == 1
    assert "already current" in second.detail


def test_versions_sit_beside_each_other_and_none_is_pruned(source, home):
    install(source, home, version="1.2.3")
    install(source, home, version="1.2.4")

    assert claude.installed_versions(
        Harness(), market="autorun", plugin="ar", home=home
    ) == ("1.2.3", "1.2.4")


def test_a_harness_with_no_config_directory_is_a_real_answer(source, home):
    class Bare:
        name = "bare"
        config_dir = ""
        config_dir_env_vars: tuple[str, ...] = ()

    assert claude.cache_dir(Bare(), market="m", plugin="p", version="1", home=home) is None
    assert claude.cache_fallback(
        source, Bare(), market="m", plugin="p", version="1", home=home
    ) is None
    assert claude.installed_versions(Bare(), market="m", plugin="p", home=home) == ()


# ─── The placeholder ─────────────────────────────────────────────────────────


def test_the_plugin_root_placeholder_is_expanded_in_the_cached_copy(source, home):
    written = install(source, home)

    changed = claude.substitute_root(written)

    assert changed == ("hooks/hooks.json",)
    # As JSON writes it: a Windows path's backslashes arrive escaped.
    assert json.dumps(str(written))[1:-1] in (written / "hooks" / "hooks.json").read_text(
        encoding="utf-8"
    )
    assert "${CLAUDE_PLUGIN_ROOT}" not in (written / "hooks" / "hooks.json").read_text(
        encoding="utf-8"
    )


def test_prose_mentioning_the_placeholder_is_left_alone(source, home):
    """Command documentation names the variable on purpose. Rewriting it would
    replace the documentation with one machine's path."""
    (source / "README.md").write_text("set ${CLAUDE_PLUGIN_ROOT} yourself\n", encoding="utf-8")
    written = install(source, home)

    claude.substitute_root(written)

    assert "${CLAUDE_PLUGIN_ROOT}" in (written / "README.md").read_text(encoding="utf-8")


def test_substitution_is_idempotent(source, home):
    """A second pass must not churn an mtime a harness watches."""
    written = install(source, home)
    claude.substitute_root(written)

    assert claude.substitute_root(written) == ()


def test_substitution_publishes_each_manifest_atomically(source, home, monkeypatch):
    written = install(source, home)
    calls = []
    real_write = claude.atomic_write

    def write(path, text):
        calls.append(path)
        real_write(path, text)

    monkeypatch.setattr(claude, "atomic_write", write)

    claude.substitute_root(written)

    assert calls == [written / "hooks" / "hooks.json"]


def test_the_bare_spelling_is_expanded_too(source, home):
    """A manifest written by hand uses whichever spelling the author
    remembered, and an unexpanded one becomes a path that does not exist."""
    (source / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": ["$CLAUDE_PLUGIN_ROOT/hooks/entry.py"]}}),
        encoding="utf-8",
    )
    written = install(source, home)

    claude.substitute_root(written)

    assert "$CLAUDE_PLUGIN_ROOT" not in (written / "hooks" / "hooks.json").read_text(
        encoding="utf-8"
    )


def test_a_cached_wheel_hook_runs_without_the_tool_directory_on_path(tmp_path):
    """The wheel's hooks ran `autorun --cli claude` through PATH. The desktop
    app starts Claude Code without uv's tool directory there, so every hook
    failed with "command not found" before it could log anything."""
    import json

    from autorun.installer import claude

    hooks = tmp_path / "hooks" / "hooks.json"
    hooks.parent.mkdir()
    mine = {"type": "command", "command": "~/bin/my-hook"}
    hooks.write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command", "command": claude.CONSOLE_HOOK_COMMAND}]}],
        "PreToolUse": [{"hooks": [mine]}],
    }}))
    absolute = "/opt/tool/bin/python /opt/tool/autorun/hooks/hook_entry.py --cli claude"
    assert claude.pin_hook_command(tmp_path, absolute)
    written = json.loads(hooks.read_text())["hooks"]
    assert written["Stop"][0]["hooks"][0]["command"] == f"{absolute} --event Stop"
    assert written["PreToolUse"][0]["hooks"][0] == mine, "other commands stay"
    assert not claude.pin_hook_command(tmp_path, absolute), "already pinned"


def test_pinned_commands_keep_each_manifest_event_for_bootstrap_failure(tmp_path):
    import shlex
    import shutil

    cache = tmp_path / "cache"
    hooks = cache / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[1] / "hooks" / "hook_entry.py", hooks)
    manifest = hooks / "hooks.json"
    manifest.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"hooks": [{"type": "command", "command": claude.CONSOLE_HOOK_COMMAND}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": claude.CONSOLE_HOOK_COMMAND}]}],
    }}), encoding="utf-8")
    # Before Python 3.11 sys._base_executable is the venv's own python on
    # POSIX, which has the real `autorun` beside it: hook_entry then runs that
    # CLI instead of failing to bootstrap, and on Linux it answers an unknown
    # event with silence (exit 0, no output). The shared helper returns an
    # interpreter with no autorun sibling on every version.
    from test_hook_entry import _isolated_interpreter

    interpreter = _isolated_interpreter(tmp_path)
    base = f"{interpreter} -I {hooks / 'hook_entry.py'} --cli claude"

    assert claude.pin_hook_command(cache, base)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    commands = {
        event: matchers[0]["hooks"][0]["command"]
        for event, matchers in document["hooks"].items()
    }
    assert commands["PreToolUse"].endswith("--event PreToolUse")
    assert commands["SessionStart"].endswith("--event SessionStart")

    broken = tmp_path / "broken-plugin"
    package = broken / "src" / "autorun"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "raise ImportError('injected bootstrap failure')\n", encoding="utf-8"
    )
    environment = {
        **os.environ,
        "PATH": os.pathsep.join(("/usr/bin", "/bin")) if os.name != "nt" else "",
        "AUTORUN_PLUGIN_ROOT": str(broken),
        "AUTORUN_NO_BOOTSTRAP": "1",
        "AUTORUN_HOME": str(tmp_path / "autorun-home"),
    }
    payload = json.dumps({"hook_event_name": "FutureUnknownEvent", "tool_name": "Bash"})

    tool = subprocess.run(
        shlex.split(commands["PreToolUse"]), input=payload, text=True,
        capture_output=True, env=environment, check=False,
    )
    lifecycle = subprocess.run(
        shlex.split(commands["SessionStart"]), input=payload, text=True,
        capture_output=True, env=environment, check=True,
    )

    assert tool.stdout.strip(), (
        f"the pinned PreToolUse command printed nothing: rc={tool.returncode} stderr={tool.stderr!r}"
    )
    tool_response = json.loads(tool.stdout)
    assert tool.returncode == 2
    assert tool_response["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert json.loads(lifecycle.stdout)["continue"] is True
    assert lifecycle.stderr == ""


def test_the_pinned_command_is_the_one_the_wheel_build_writes():
    from pathlib import Path

    from autorun.installer import claude

    build = (Path(__file__).resolve().parents[1] / "build_support.py").read_text(encoding="utf-8")
    assert f'hook["command"] = "{claude.CONSOLE_HOOK_COMMAND}"' in build


def test_a_backslash_in_the_plugin_root_keeps_hooks_json_parseable(tmp_path):
    """Windows paths (C:\\Users\\...) were pasted raw into hooks.json, where each
    backslash began an invalid JSON escape and the file stopped parsing."""
    import json

    from autorun.installer import claude

    root = tmp_path / "C:\\Users\\me"  # a literal backslash in one name on POSIX
    (root / "hooks").mkdir(parents=True)
    (root / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py"},
    ]}]}}))
    claude.substitute_root(root)
    command = json.loads((root / "hooks" / "hooks.json").read_text())["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert command == f"python {root}/hooks/hook_entry.py"


# ─── A link that leaves the plugin ───────────────────────────────────────────
# Claude's own `plugin install`/`plugin update` copies the marketplace source
# and writes a link whose target is outside the plugin as that target's
# content (observed on 2026-11-13: `src/pdf_extraction ->
# ../../pdf-extractor/src/pdf_extraction` arrived in the cache as a directory
# of files, while `CLAUDE.md -> AGENTS.md` stayed a link). autorun must expect
# that form, or a forced install fails with "directory blocks file".


@pytest.fixture
def source_with_outside_link(source, tmp_path):
    shared = tmp_path / "sibling" / "pkg"
    shared.mkdir(parents=True)
    (shared / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "src").mkdir()
    (source / "src" / "pkg").symlink_to(Path("..") / ".." / "sibling" / "pkg", target_is_directory=True)
    (source / "AGENTS.md").write_text("guidance\n", encoding="utf-8")
    (source / "CLAUDE.md").symlink_to("AGENTS.md")
    return source


def _copy_as_claude_does(source: Path, home: Path) -> Path:
    import shutil

    cached = claude.cache_dir(Harness(), market="autorun", plugin="ar", version="1.2.3", home=home)
    shutil.copytree(source, cached, symlinks=True)
    link = cached / "src" / "pkg"
    link.unlink()
    shutil.copytree((source / "src" / "pkg").resolve(), link)
    return cached


def test_forced_refresh_accepts_the_directory_claude_wrote_for_an_outside_link(
    source_with_outside_link, home, tmp_path
):
    source = source_with_outside_link
    cached = _copy_as_claude_does(source, home)
    backed_up: list[Path] = []

    claude.refresh_cache(
        source, cached, force=True, backup_root=tmp_path / "backups", backed_up=backed_up
    )

    assert backed_up == []
    assert (cached / "src" / "pkg" / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (cached / "src" / "pkg").is_symlink()
    assert (cached / "CLAUDE.md").is_symlink()
    assert claude.cache_mismatches(source, cached) == ()


def test_plain_refresh_keeps_nothing_in_a_cache_claude_copied(source_with_outside_link, home):
    source = source_with_outside_link
    cached = _copy_as_claude_does(source, home)
    kept: list[str] = []

    claude.refresh_cache(source, cached, kept=kept)

    assert kept == []
    assert claude.cache_mismatches(source, cached) == ()


def test_the_fallback_writes_an_outside_link_as_its_content(source_with_outside_link, home):
    written = install(source_with_outside_link, home)

    assert not (written / "src" / "pkg").is_symlink()
    assert (written / "src" / "pkg" / "__init__.py").is_file()
    assert (written / "CLAUDE.md").is_symlink()
    marker = read_marker(written)
    assert marker is not None and "src/pkg/__init__.py" in marker.files
    assert not [path for path in claude.cache_mismatches(source_with_outside_link, written) if path.startswith("src/")]


def test_refresh_replaces_a_cached_outside_link_with_its_content(source_with_outside_link, home):
    import shutil

    source = source_with_outside_link
    cached = claude.cache_dir(Harness(), market="autorun", plugin="ar", version="1.2.3", home=home)
    shutil.copytree(source, cached, symlinks=True)  # what an earlier autorun build wrote
    assert fs.record_matching_tree_files(
        source, cached, plugin="ar", ignored_globs=claude.CACHE_METADATA_GLOBS
    )
    assert "src/pkg" in read_marker(cached).files

    assert claude.refresh_cache(source, cached) == cached

    assert not (cached / "src" / "pkg").is_symlink()
    assert (cached / "src" / "pkg" / "__init__.py").is_file()
    assert claude.cache_mismatches(source, cached) == ()
