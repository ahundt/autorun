"""The Claude cache hook runtime is a non-editable, cache-local snapshot."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import autorun
from autorun.installer import claude, runtime


AUTORUN_VERSION = autorun.__version__


def _claude_plugin(root: Path) -> Path:
    plugin = root / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "ar", "version": AUTORUN_VERSION}), encoding="utf-8"
    )
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "uv run --project ${CLAUDE_PLUGIN_ROOT} python ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py --cli claude"}]}]}}),
        encoding="utf-8",
    )
    (plugin / "hooks" / "hook_entry.py").write_text(
        '''import autorun, subprocess, sys
child = subprocess.run(
    [sys.executable, "-c", "import os; print(os.environ.get('PYTHONPATH', '')); print(os.environ.get('PYTHONHOME', ''))"],
    capture_output=True, text=True, check=True,
)
print(autorun.__file__)
print(child.stdout, end="")
''',
        encoding="utf-8",
    )
    (plugin / "pyproject.toml").write_text(
        "[build-system]\nrequires=['setuptools']\nbuild-backend='setuptools.build_meta'\n"
        f"[project]\nname='autorun-ai'\nversion='{AUTORUN_VERSION}'\n", encoding="utf-8"
    )
    (plugin / "src" / "autorun").mkdir(parents=True)
    (plugin / "src" / "autorun" / "__init__.py").write_text(
        "MARKER = 'cache-runtime'\n", encoding="utf-8"
    )
    return plugin


@pytest.mark.parametrize("native", [True, False], ids=["native", "cache-fallback"])
def test_claude_registration_executes_cache_snapshot_after_source_removal(
    tmp_path, monkeypatch, native
):
    """Both native and fallback registration routes run cache-local code."""
    from autorun.installer.orchestrate import _registrations
    from autorun.installer.traversal import Context
    from autorun.platforms import PLATFORMS
    from test_windows_hook_launch import _launch

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    plugin = _claude_plugin(tmp_path)
    ctx = Context(marketplace_root=tmp_path, home=home)
    calls = []

    def run(argv, **_kwargs):
        argv = tuple(str(part) for part in argv)
        calls.append(argv)
        if argv and argv[0] == "claude" and native:
            target = home / ".claude" / "plugins" / "cache" / "autorun" / "ar" / AUTORUN_VERSION
            shutil.copytree(plugin, target, dirs_exist_ok=True)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv and argv[0] == "claude":
            return subprocess.CompletedProcess(argv, 1, "", "offline")
        return runtime._spawn(argv, **_kwargs)

    outcomes = _registrations(
        (PLATFORMS["claude"],), {"ar": plugin}, tmp_path, "autorun", ctx,
        removing=False, run=run, available=("claude",),
    )
    assert outcomes and all(outcome.ok for outcome in outcomes), (outcomes, calls)
    cached = home / ".claude" / "plugins" / "cache" / "autorun" / "ar" / AUTORUN_VERSION
    command = json.loads((cached / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    hook = command["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert str(cached / ".venv") in hook and "-I" in hook
    provenance = subprocess.run(
        (
            str(runtime.cache_venv_python(cached)), "-I", "-c",
            "import importlib.metadata as m, json; d=m.distribution('autorun-ai'); "
            "print(d.locate_file('')); print(d.read_text('direct_url.json') or '')",
        ),
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path / "poison"), "PYTHONHOME": str(tmp_path / "poison")},
    )
    provenance_lines = provenance.stdout.splitlines()
    assert Path(provenance_lines[0]).resolve().is_relative_to((cached / ".venv").resolve())
    metadata = json.loads("\n".join(provenance_lines[1:]) or "{}")
    assert not metadata.get("dir_info", {}).get("editable")

    shutil.rmtree(plugin)
    environment = dict(os.environ, **{
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(tmp_path / "poison"),
        "PYTHONHOME": str(tmp_path / "poison"),
    })
    result = _launch(PLATFORMS["claude"], {"command": hook}, {"cwd": str(home)}, environment)
    assert result.returncode == 0, result.stderr
    result_lines = result.stdout.splitlines()
    imported = Path(result_lines[0]).resolve()
    assert imported.is_relative_to((cached / ".venv").resolve())
    assert result_lines[1:3] == ["", ""]


def test_cache_hook_uses_the_cache_interpreter_and_imports_cache_site_packages(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()

    python = runtime.ensure_cache_venv(cache, python=sys.executable, run=runtime._spawn)
    site_packages = next((cache / ".venv").rglob("site-packages"))
    package = site_packages / "autorun"
    package.mkdir()
    (package / "__init__.py").write_text("MARKER = 'cache'\n", encoding="utf-8")
    hooks = cache / "hooks"
    hooks.mkdir()
    (hooks / "hook_entry.py").write_text(
        "import autorun; print(autorun.__file__)\n", encoding="utf-8"
    )

    result = subprocess.run(
        claude.cache_hook_argv(cache),
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(tmp_path / "shadow")},
    )
    imported = Path(result.stdout.strip()).resolve()
    assert imported.is_relative_to(site_packages.resolve())
    assert Path(python).resolve() == Path(claude.cache_hook_argv(cache)[0]).resolve()
    command = claude.cache_hook_command(cache)
    assert "-I" in command and "PYTHONPATH=''" in command and "PYTHONHOME=''" in command


def test_cache_install_commands_target_cache_python_without_editable(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text("[project]\nname='autorun-ai'\nversion='1'\n", encoding="utf-8")
    calls = []

    def run(argv, **_kwargs):
        calls.append(tuple(argv))
        if "-c" in argv:
            return subprocess.CompletedProcess(argv, 0, str(cache / ".venv"), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    outcome = runtime.install_cache_runtime(cache, source, run=run)
    assert outcome.ok, outcome.detail
    assert calls[0][:2] == ("uv", "venv")
    pip_call = next(call for call in calls if call[:4] == ("uv", "pip", "install", "--python"))
    assert "--editable" not in pip_call
    assert str(runtime.cache_venv_python(cache)) in pip_call


def test_cache_runtime_keeps_the_installer_lock_until_package_install_finishes(tmp_path):
    from filelock import FileLock, Timeout
    from autorun.installer.fs import INSTALL_LOCK_NAME

    cache = tmp_path / "cache"
    cache.mkdir()
    source = _claude_plugin(tmp_path)
    checked = []

    def run(argv, **_kwargs):
        if "-c" in argv:
            return subprocess.CompletedProcess(argv, 0, str(cache / ".venv"), "")
        if argv[:3] == ("uv", "pip", "install"):
            contender = FileLock(str(cache.parent / INSTALL_LOCK_NAME))
            with pytest.raises(Timeout):
                contender.acquire(timeout=0)
            checked.append(True)
        return subprocess.CompletedProcess(argv, 0, "", "")

    outcome = runtime.install_cache_runtime(cache, source, run=run)
    assert outcome.ok and checked, outcome
    with FileLock(str(cache.parent / INSTALL_LOCK_NAME), timeout=0):
        pass


@pytest.mark.parametrize(
    ("runtime_state", "probe_stdout"),
    (
        ("missing-interpreter", ""),
        ("missing-package", ""),
        ("editable-package", "/checkout/src/autorun/__init__.py\n"),
    ),
)
def test_matching_receipt_never_skips_without_a_cache_local_autorun_import(
    tmp_path, monkeypatch, runtime_state, probe_stdout
):
    """Violation excluded: a receipt alone reports a broken runtime current."""
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _claude_plugin(tmp_path)
    fingerprint = runtime._cache_runtime_fingerprint(source)
    venv = cache / ".venv"
    python = runtime.cache_venv_python(cache)
    if runtime_state != "missing-interpreter":
        python.parent.mkdir(parents=True)
        python.write_text("interpreter placeholder\n", encoding="utf-8")
        (venv / "pyvenv.cfg").write_text("home = isolated\n", encoding="utf-8")
    venv.mkdir(exist_ok=True)
    (venv / ".autorun-runtime.json").write_text(
        json.dumps({"source_fingerprint": fingerprint}), encoding="utf-8"
    )
    installs = []

    def run(argv, **_kwargs):
        call = tuple(str(part) for part in argv)
        if call[:2] == ("uv", "venv"):
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("interpreter placeholder\n", encoding="utf-8")
            (venv / "pyvenv.cfg").write_text("home = isolated\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        if len(call) >= 4 and call[1:3] == ("-I", "-c"):
            if "sys.prefix" in call[3] and "import autorun" not in call[3]:
                return subprocess.CompletedProcess(argv, 0, str(venv) + "\n", "")
            return subprocess.CompletedProcess(argv, 0, probe_stdout, "")
        installs.append(call)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(runtime, "has_uv", lambda: True)
    outcome = runtime.install_cache_runtime(cache, source, run=run)

    assert outcome.ok, outcome.detail
    assert len(installs) == 1, f"{runtime_state} receipt skipped reinstall: {installs}"


def test_cache_runtime_reports_the_actual_package_install_error(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    source = _claude_plugin(tmp_path)
    project = source / "pyproject.toml"
    project.write_text(project.read_text().replace(f"version='{AUTORUN_VERSION}'", "version='not-a-version'"))
    outcome = runtime.install_cache_runtime(cache, source, run=runtime._spawn)
    assert not outcome.ok
    assert "error" in outcome.detail.lower(), outcome.detail


def test_cache_venv_creation_preserves_existing_managed_files(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    managed = cache / ".venv" / "managed-by-claude.json"
    managed.parent.mkdir(parents=True)
    managed.write_text('{"keep": true}\n', encoding="utf-8")
    before = managed.stat()
    calls = []
    environments = []

    def run(argv, **_kwargs):
        calls.append(tuple(argv))
        environments.append(_kwargs.get("env", {}))
        return runtime._spawn(argv, **_kwargs)

    monkeypatch.setenv("UV_VENV_CLEAR", "1")
    runtime.ensure_cache_venv(cache, run=run)
    assert managed.read_text(encoding="utf-8") == '{"keep": true}\n'
    after = managed.stat()
    assert (after.st_mtime_ns, after.st_size, after.st_ino) == (
        before.st_mtime_ns, before.st_size, before.st_ino
    )
    assert "--allow-existing" in calls[0]
    assert environments[0]["UV_VENV_CLEAR"] == "false"


def test_cache_venv_rejects_a_foreign_interpreter_symlink(tmp_path):
    cache = tmp_path / "cache"
    expected = runtime.cache_venv_python(cache)
    expected.parent.mkdir(parents=True)
    expected.symlink_to(sys.executable)
    with pytest.raises(RuntimeError, match="symlink outside"):
        runtime.ensure_cache_venv(cache, run=lambda *_args, **_kwargs: subprocess.CompletedProcess((), 0, "", ""))


@pytest.mark.parametrize("redirect", ["cache", "venv"])
def test_cache_runtime_never_installs_through_a_foreign_directory_symlink(tmp_path, redirect):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    runtime.ensure_cache_venv(foreign, run=runtime._spawn)
    package = next((foreign / ".venv").rglob("site-packages")) / "autorun"
    assert not package.exists()
    cache = tmp_path / "cache"
    if redirect == "cache":
        cache.symlink_to(foreign, target_is_directory=True)
    else:
        cache.mkdir()
        (cache / ".venv").symlink_to(foreign / ".venv", target_is_directory=True)
    outcome = runtime.install_cache_runtime(cache, _claude_plugin(tmp_path), run=runtime._spawn)
    assert not package.exists(), "cache installation changed the stand-in foreign environment"
    assert not outcome.ok and "symlink" in outcome.detail, outcome


def test_cache_runtime_rejects_windows_directory_reparse_points_before_spawning(tmp_path, monkeypatch):
    import stat
    from types import SimpleNamespace

    cache = tmp_path / "cache"
    cache.mkdir()
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == cache:
            return SimpleNamespace(st_mode=stat.S_IFDIR,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, str(cache / ".venv") if "-c" in argv else "", "")

    with pytest.raises(RuntimeError, match="reparse"):
        runtime.ensure_cache_venv(cache, run=run)
    assert not calls


def test_owned_source_hook_pin_is_idempotent(tmp_path):
    cache = tmp_path / "cache"
    hooks = cache / "hooks"
    hooks.mkdir(parents=True)
    path = hooks / "hooks.json"
    path.write_text(
        json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": "uv run ${CLAUDE_PLUGIN_ROOT}/hooks/hook_entry.py"},
        ]}]}}),
        encoding="utf-8",
    )
    target = claude.cache_hook_command(cache)
    assert claude.pin_hook_command(cache, target)
    first = path.read_bytes()
    before = path.stat()
    assert not claude.pin_hook_command(cache, target)
    assert path.read_bytes() == first
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    document = json.loads(first)
    commands = [item["command"] for item in document["hooks"]["Stop"][0]["hooks"]]
    assert commands == [f"{target} --event Stop"]


def test_cache_install_builds_outside_the_source_project(tmp_path):
    source = _claude_plugin(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    outcome = runtime.install_cache_runtime(cache, source, run=runtime._spawn)
    assert outcome.ok, outcome.detail
    assert not (source / "build").exists()
    assert not list((source / "src").glob("*.egg-info"))


def test_staging_never_copies_through_a_linked_sibling_package(tmp_path, monkeypatch):
    """Violation: the `src/` copy descends into `src/pdf_extraction`.

    On Windows that link is file-typed when its target did not exist as the
    link was created, and copying it raised "[WinError 5] Access is denied",
    so `claude: cache fallback` failed. The package must arrive only through
    `materialize_sibling_packages`, which reads the link's target itself.
    """
    source = Path(autorun.__file__).resolve().parents[2]
    link = source / "src" / "pdf_extraction"
    assert link.exists(), "this checkout has no linked sibling package to stage"
    copied_through_link = []
    real_copytree = shutil.copytree

    def recording_copytree(src, dst, *args, **kwargs):
        ignore = kwargs.get("ignore")
        if Path(src) == source / "src" and ignore is not None:
            if link.name not in ignore(str(src), os.listdir(src)):
                copied_through_link.append(str(src))
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(runtime.shutil, "copytree", recording_copytree)
    target = tmp_path / "project"
    runtime._stage_cache_project(source, target)

    assert not copied_through_link
    staged = target / "src" / "pdf_extraction"
    assert staged.is_dir() and not staged.is_symlink()
    assert (staged / "__init__.py").is_file()
