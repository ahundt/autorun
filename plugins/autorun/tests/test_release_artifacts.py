"""Clean prospective-release artifact and installation lifecycle checks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.release,
    pytest.mark.slow,
    pytest.mark.subprocess,
    pytest.mark.serial,
    # The module fixture builds every artifact twice and the tests install the
    # wheels, which _run allows 180s apiece. The global 30s per-test timeout
    # (pyproject.toml:94) covers fixture setup too, so without this the first
    # test would be killed mid-build on a cold, slower runner and take the
    # whole session with it. 17.7s locally with a warm uv cache.
    pytest.mark.timeout(600),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_DATE_EPOCH = "1700000000"


def _run(argv, *, cwd: Path, env: dict[str, str] | None = None, timeout=180):
    result = subprocess.run(
        [str(arg) for arg in argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert result.returncode == 0, (
        f"command failed ({result.returncode}): {' '.join(map(str, argv))}\n"
        f"stdout:\n{result.stdout[-6000:]}\nstderr:\n{result.stderr[-6000:]}"
    )
    return result


def _prospective_checkout(destination: Path) -> str:
    """Copy tracked plus intentional new files, excluding ignored local data."""
    listed = _run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=REPO_ROOT,
    ).stdout
    for relative_text in sorted(set(filter(None, listed.split("\0")))):
        relative = Path(relative_text)
        source = REPO_ROOT / relative
        if not source.exists() and not source.is_symlink():
            # A tracked path deleted by the prospective release is absent.
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)

    _run(["git", "init", "-q"], cwd=destination)
    _run(["git", "add", "--all"], cwd=destination)
    _run(
        [
            "git",
            "-c",
            "user.name=autorun release test",
            "-c",
            "user.email=release-test@invalid.example",
            "commit",
            "-qm",
            "release fixture",
        ],
        cwd=destination,
    )
    return _run(["git", "rev-parse", "HEAD"], cwd=destination).stdout.strip()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def release_bundle(tmp_path_factory):
    """Build the release artifacts twice, from one prospective checkout.

    REAL WORK IS THE ASSERTION — do not collapse the two passes to speed this
    up. `test_release_archives_repeat_bytes_in_one_toolchain_and_are_clean`
    compares the two builds digest for digest, so the second pass *is* the
    reproducibility check; building once and comparing the result to itself
    would assert nothing. The 5.7s this costs is the price of that proof.

    It is already module scope, so both passes are paid once for every test in
    the file rather than once per test.
    """
    root = tmp_path_factory.mktemp("release-artifacts")
    checkout = root / "checkout"
    checkout.mkdir()
    commit = _prospective_checkout(checkout)
    env = os.environ.copy()
    env.update(
        {
            "AUTORUN_BUILD_COMMIT": commit,
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
            "UV_CACHE_DIR": str(root / "uv-cache"),
        }
    )
    builds = []
    for pass_number in (1, 2):
        output = root / f"dist-{pass_number}"
        _run(
            [
                sys.executable,
                checkout / "scripts" / "build_release_artifacts.py",
                "--out-dir",
                output,
            ],
            cwd=checkout,
            env=env,
        )
        builds.append(output)
    return root, checkout, commit, env, builds


def test_release_archives_repeat_bytes_in_one_toolchain_and_are_clean(release_bundle):
    _root, _checkout, commit, _env, builds = release_bundle
    first = {path.name: _digest(path) for path in builds[0].iterdir()}
    second = {path.name: _digest(path) for path in builds[1].iterdir()}
    assert first == second

    wheels = sorted(builds[0].glob("*.whl"))
    assert [path.name.split("-")[0] for path in wheels] == ["autorun"], (
        "a second distribution reappeared; pdf_extraction ships inside autorun"
    )
    autorun_wheel = wheels[0]
    with zipfile.ZipFile(autorun_wheel) as archive:
        names = archive.namelist()
        assert any(name.endswith(".dist-info/licenses/LICENSE") for name in names)
        assert not any(name.lower().endswith(".pdf") for name in names)
        assert sum(Path(name).name == "hook_entry.py" for name in names) == 1
        # The pdf plugin has no build of its own, so nothing else notices if
        # setuptools stops collecting it.
        assert "pdf_extraction/cli.py" in names
        entry_points = archive.read(
            next(name for name in names if name.endswith(".dist-info/entry_points.txt"))
        ).decode()
        assert "extract-pdfs = pdf_extraction.cli:main" in entry_points
        metadata = json.loads(archive.read("autorun/metadata.json"))
        assert metadata == {
            "version": "1.0.0rc1",
            "commit": commit,
            "build_time": "2023-11-14T22:13:20Z",
        }
        package_metadata = archive.read(
            next(name for name in names if name.endswith(".dist-info/METADATA"))
        ).decode()
        assert "claude-agent-sdk" not in package_metadata
        assert "Requires-Dist: ruff" not in package_metadata

    for source_archive in builds[0].glob("*.tar.gz"):
        with tarfile.open(source_archive, "r:gz") as archive:
            names = archive.getnames()
            assert any(name.endswith("/LICENSE") for name in names)
            assert not any(name.lower().endswith(".pdf") for name in names)
            assert any(name.endswith("/README.md") for name in names)


def _venv(
    root: Path, wheel: Path, env: dict[str, str], *, label: str
) -> tuple[Path, dict[str, str]]:
    """Install one wheel into an environment named for its caller.

    ``label`` is not decoration. Both callers install the same wheel now that
    there is one distribution, so deriving the directory from the wheel name
    alone made the second `uv venv` fail on a directory the first had created.
    """
    slug = f"{wheel.stem}-{label}"
    target = root / slug
    _run(["uv", "venv", "--python", sys.executable, target], cwd=root, env=env)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run(["uv", "pip", "install", "--python", python, wheel], cwd=root, env=env)
    scripts = python.parent
    fake_claude = scripts / ("claude.cmd" if os.name == "nt" else "claude")
    if os.name == "nt":
        fake_claude.write_text("@exit /b 0\n", encoding="utf-8")
    else:
        fake_claude.symlink_to(shutil.which("true") or "/usr/bin/true")

    home = root / f"home-{slug}"
    isolated = env.copy()
    isolated.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "AUTORUN_HOME": str(root / f"autorun-home-{slug}"),
            "AUTORUN_TEST_STATE_DIR": str(root / f"state-{slug}"),
            "AUTORUN_TEST_RUNTIME_DIR": str(root / f"runtime-{slug}"),
            "AUTORUN_USE_DAEMON": "0",
            "PATH": os.pathsep.join((str(scripts), os.defpath)),
        }
    )
    return scripts, isolated


def test_autorun_wheel_install_status_bootstrap_and_uninstall(release_bundle):
    root, _checkout, _commit, env, builds = release_bundle
    wheel = next(builds[0].glob("autorun-*.whl"))
    scripts, isolated = _venv(root, wheel, env, label="cli")
    autorun = scripts / ("autorun.exe" if os.name == "nt" else "autorun")

    assert "1.0.0rc1" in _run([autorun, "--version"], cwd=root, env=isolated).stdout
    _run([autorun, "--install-dry-run", "--claude"], cwd=root, env=isolated)
    _run([autorun, "--install", "--claude", "--force"], cwd=root, env=isolated)
    status_result = _run([autorun, "--status", "--claude"], cwd=root, env=isolated)
    assert "version=1.0.0rc1" in status_result.stdout
    assert "commit=" in status_result.stdout
    assert "package=" in status_result.stdout
    _run([autorun, "--no-bootstrap"], cwd=root, env=isolated)
    _run([autorun, "--enable-bootstrap"], cwd=root, env=isolated)
    _run([autorun, "--uninstall", "ar", "--claude"], cwd=root, env=isolated)

    assert not (Path(isolated["HOME"]) / ".claude" / "CLAUDE.md").exists()
    assert not any((Path(isolated["HOME"]) / ".claude" / "skills").glob("*/SKILL.md"))

    empty_home = root / "empty-uninstall-home"
    empty_env = isolated | {
        "HOME": str(empty_home),
        "USERPROFILE": str(empty_home),
        "AUTORUN_HOME": str(root / "empty-uninstall-state"),
    }
    _run([autorun, "--uninstall", "ar", "--claude"], cwd=root, env=empty_env)
    assert not any(empty_home.rglob("*"))


def test_documented_vcs_subdirectory_installs_autorun_entrypoint(release_bundle):
    root, checkout, _commit, env, _builds = release_bundle
    target = root / "vcs-venv"
    _run(["uv", "venv", "--python", sys.executable, target], cwd=root, env=env)
    python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            f"git+file://{checkout}#subdirectory=plugins/autorun",
        ],
        cwd=root,
        env=env,
    )
    autorun = python.parent / ("autorun.exe" if os.name == "nt" else "autorun")
    assert "1.0.0rc1" in _run([autorun, "--version"], cwd=root, env=env).stdout


def test_pdf_help_and_backend_inventory_are_lightweight(release_bundle):
    """The PDF CLI must work, and cost nothing, in a plain `autorun` install.

    Every backend is an extra, so the wheel installed here has none of them. The
    CLI still has to run and report what is missing rather than fail on import.
    """
    root, _checkout, _commit, env, builds = release_bundle
    wheel = next(builds[0].glob("autorun-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        metadata_name = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = archive.read(metadata_name).decode("utf-8")
    backends = ("markitdown", "pdfplumber", "pdfminer", "pypdf", "docling", "pymupdf4llm")
    unconditional = [
        line
        for line in metadata.splitlines()
        if line.startswith("Requires-Dist:")
        and line.split(":", 1)[1].strip().lower().startswith(backends)
        and "extra ==" not in line
    ]
    assert not unconditional, (
        "an extraction backend is a required autorun dependency, so every user "
        f"downloads it: {unconditional}"
    )
    for label in ("Homepage", "Repository", "Issues"):
        assert f"Project-URL: {label}, " in metadata

    scripts, isolated = _venv(root, wheel, env, label="pdf")
    cli = scripts / ("extract-pdfs.exe" if os.name == "nt" else "extract-pdfs")
    help_result = _run([cli, "--help"], cwd=root, env=isolated, timeout=10)
    assert "--list-backends" in help_result.stdout
    for backend in (
        "docling", "marker", "markitdown", "pymupdf4llm", "pdfbox",
        "pdfminer", "pypdf2", "pdfplumber", "pdftotext",
    ):
        assert backend in help_result.stdout
    inventory = _run([cli, "--list-backends"], cwd=root, env=isolated, timeout=10)
    assert "Available backends:" in inventory.stdout
    assert "Supported but not installed:" in inventory.stdout
