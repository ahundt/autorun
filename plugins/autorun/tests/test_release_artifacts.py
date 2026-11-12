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

from skill_routes import reachable_skills

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

#: The distribution. Everything else about this project is spelled `autorun`
#: (console script, import package, marketplace) or `ar` (plugin id, `/ar:`
#: prefix); only PyPI needed a different name, because it prohibits the bare
#: one. Not parsed from pyproject here on purpose: `tomllib` is not stdlib
#: before 3.11 and CI runs 3.10, so a module-scope parse is a collection error
#: on that job. `test_each_autorun_spelling_keeps_its_own_job` pins this
#: constant against the declared name instead, so the two cannot drift.
DISTRIBUTION = "autorun-ai"

#: The wheel filename stem. PEP 427 normalises "-" to "_", so this is derived
#: rather than written out: a literal `autorun_ai` would be a *fourth* spelling
#: to keep in sync, for no gain -- nothing declares it, the build backend just
#: emits it.
WHEEL_STEM = DISTRIBUTION.replace("-", "_")


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
            target.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
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


def test_prospective_checkout_declares_directory_links_before_targets_exist(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    (source / "z-package").mkdir(parents=True)
    (source / "z-package" / "cli.py").write_text("source\n")
    (source / "a-link").symlink_to("z-package", target_is_directory=True)
    destination = tmp_path / "checkout"
    destination.mkdir()
    monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", source)
    monkeypatch.setattr(
        sys.modules[__name__], "_run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            (), 0, "a-link\0z-package/cli.py\0", ""
        ),
    )
    symlink_to = Path.symlink_to

    def windows_symlink_to(path, target, target_is_directory=False):
        assert target_is_directory, "Windows requires a directory link before its target exists"
        return symlink_to(path, target, target_is_directory=target_is_directory)

    monkeypatch.setattr(Path, "symlink_to", windows_symlink_to)
    _prospective_checkout(destination)
    assert (destination / "a-link").is_symlink()
    assert (destination / "a-link" / "cli.py").read_text() == "source\n"


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
    # uv's own cache (or the caller's UV_CACHE_DIR), not a fresh one per run: a
    # new cache downloaded every build and install dependency again, 260 s
    # locally against about 18 s warm, and beside a parallel suite that ran
    # past _run's timeouts. It holds downloads, never autorun state, and uv
    # locks it for concurrent use; reproducibility is the two builds' digests.
    env.update({"AUTORUN_BUILD_COMMIT": commit, "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH})
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
        _run(["git", "diff", "--exit-code", "HEAD"], cwd=checkout)
    return root, checkout, commit, env, builds


def test_release_archives_repeat_bytes_in_one_toolchain_and_are_clean(release_bundle):
    _root, _checkout, commit, _env, builds = release_bundle
    first = {path.name: _digest(path) for path in builds[0].iterdir()}
    second = {path.name: _digest(path) for path in builds[1].iterdir()}
    assert first == second

    wheels = sorted(builds[0].glob("*.whl"))
    assert [path.name.split("-")[0] for path in wheels] == [WHEEL_STEM], (
        f"a second distribution reappeared; pdf_extraction ships inside {DISTRIBUTION}"
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
            "version": "1.0.0rc4",
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
    wheel = next(builds[0].glob(f"{WHEEL_STEM}-*.whl"))
    scripts, isolated = _venv(root, wheel, env, label="cli")
    autorun = scripts / ("autorun.exe" if os.name == "nt" else "autorun")

    assert "1.0.0rc4" in _run([autorun, "--version"], cwd=root, env=isolated).stdout.split()
    _run([autorun, "--install-dry-run", "--claude"], cwd=root, env=isolated)
    _run([autorun, "--install", "--claude", "--force"], cwd=root, env=isolated)
    status_result = _run([autorun, "--status", "--claude"], cwd=root, env=isolated)
    assert "version=1.0.0rc4" in status_result.stdout.split()
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


def _marketplace_plugin_names(checkout: Path) -> tuple[str, ...]:
    document = json.loads(
        (checkout / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    return tuple(entry["name"] for entry in document["plugins"])


def _published_by_plugin(
    env: dict[str, str], log: Path, names: tuple[str, ...]
) -> dict[str, set[str]]:
    """What one install published, per marketplace plugin, independent of where
    it ran from: each owned tree, by the plugin its marker records, and each
    harness command naming the plugin, with this install's own roots replaced
    by placeholders."""
    roots = {"$HOME": Path(env["HOME"]), "$AUTORUN_HOME": Path(env["AUTORUN_HOME"])}
    published: dict[str, set[str]] = {name: set() for name in names}
    for label, root in roots.items():
        for marker in root.rglob(".autorun-owned"):
            plugin = json.loads(marker.read_text(encoding="utf-8")).get("plugin")
            if plugin in published:
                published[plugin].add(f"{label}/{marker.parent.relative_to(root).as_posix()}")
    for line in log.read_text(encoding="utf-8").splitlines():
        for label, root in roots.items():
            line = line.replace(str(root), label)
        words = line.replace("\\", "/").split()
        for name in names:
            if any(
                word == name or word.startswith(f"{name}@") or word.endswith(f"/{name}")
                for word in words
            ):
                published[name].add(f"run: {' '.join(words)}")
    return published


def _install_every_harness(
    autorun: Path, scripts: Path, env: dict[str, str], names: tuple[str, ...]
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """`autorun --install` against a stand-in for every registered harness.

    Each stand-in records its arguments and answers a `list` with every
    marketplace plugin, which is what a harness that accepted the install
    prints. That keeps the run offline and the real home untouched while every
    harness step still runs. `uv tool` and `uv sync` are stood in for too: a
    clone install syncs its project and installs itself as a uv tool, and the
    environment here already holds both. Every other `uv` call is real, since
    Claude's cache fallback builds a working environment with it.
    """
    from autorun.platforms import PLATFORMS

    fakes = scripts.parent / "harness-stand-ins"
    fakes.mkdir(exist_ok=True)
    log = fakes / "invocations.log"
    real_uv = shutil.which("uv")
    assert real_uv, "the release tests build with uv"
    for binary in {platform.binary for platform in PLATFORMS.values()}:
        if os.name == "nt":
            (fakes / f"{binary}.cmd").write_text(
                f'@echo {binary} %*>>"%AUTORUN_FAKE_LOG%"\n@echo %AUTORUN_FAKE_LIST%\n',
                encoding="utf-8",
            )
        else:
            stand_in = fakes / binary
            stand_in.write_text(
                f'#!/bin/sh\necho "{binary} $*" >> "$AUTORUN_FAKE_LOG"\n'
                'echo "$AUTORUN_FAKE_LIST"\n',
                encoding="utf-8",
            )
            stand_in.chmod(0o755)
    if os.name == "nt":
        (fakes / "uv.cmd").write_text(
            '@if "%1"=="tool" exit /b 0\n@if "%1"=="sync" exit /b 0\n'
            f'@"{real_uv}" %*\n',
            encoding="utf-8",
        )
    else:
        stand_in = fakes / "uv"
        stand_in.write_text(
            f'#!/bin/sh\ncase "$1" in tool|sync) exit 0;; esac\nexec "{real_uv}" "$@"\n',
            encoding="utf-8",
        )
        stand_in.chmod(0o755)
    home = Path(env["HOME"])
    # A harness variable inherited from the test process would send that
    # harness's files to another home, where the comparison below cannot see
    # them; every harness here has to resolve from HOME.
    env = {
        key: value
        for key, value in env.items()
        if key not in {
            var
            for platform in PLATFORMS.values()
            for var in (getattr(platform, "config_dir_env_vars", ()) or ())
        }
    } | {
        "PI_CODING_AGENT_DIR": str(home / ".pi" / "agent"),
        "PATH": os.pathsep.join((str(fakes), env["PATH"])),
        "AUTORUN_FAKE_LOG": str(log),
        "AUTORUN_FAKE_LIST": " ".join(names),
    }
    _install_all(autorun, scripts, env)
    return _published_by_plugin(env, log, names), env


def _install_all(autorun: Path, cwd: Path, env: dict[str, str], *args: str) -> None:
    # Gemini CLI is opt-in (`install_by_default` is false), so it gets its own run.
    for selection in ((), ("--gemini",)):
        _run([autorun, "--install", *args, "--force", *selection], cwd=cwd, env=env)


def _owned_trees(env: dict[str, str], plugin: str) -> list[Path]:
    """Every tree autorun wrote and recorded as ``plugin``'s.

    Claude's plugin cache is never among them: Claude's own CLI copies a
    plugin's whole source directory into it, and autorun's fallback that fills
    it when that CLI fails mirrors the same copy and deliberately records no
    marker (fs.fill_tree). Measured with Claude Code: a `"source": "./"`
    plugin's cache kept a .gitignored directory, and a sibling entry
    `"../side/q"` was refused with `source: Invalid string: must start with
    "./"`. In a wheel, ar's source is the marketplace root itself, so the
    other bundled plugins land in ar's Claude cache whatever autorun does;
    test (c) below pins why that copy is inert there.
    """
    return [
        marker.parent
        for root in (Path(env["HOME"]), Path(env["AUTORUN_HOME"]))
        for marker in root.rglob(".autorun-owned")
        if json.loads(marker.read_text(encoding="utf-8")).get("plugin") == plugin
    ]


def test_wheel_install_publishes_every_marketplace_plugin_a_clone_install_does(
    release_bundle,
):
    """`uv tool install autorun-ai && autorun --install` must reach every plugin
    the marketplace lists, on every harness a clone install reaches it on.

    The wheel once carried only `ar`: its marketplace listed nothing else and
    no sibling plugin's manifests, commands or skills were packaged, so a PyPI
    or git-subdirectory install published pdf-extractor nowhere while a clone
    published it to all nine harnesses. A clone install is the reference here,
    so a plugin added to the marketplace is checked without editing this test.
    """
    root, checkout, _commit, env, builds = release_bundle
    names = _marketplace_plugin_names(checkout)
    wheel = next(builds[0].glob(f"{WHEEL_STEM}-*.whl"))
    scripts, isolated = _venv(root, wheel, env, label="harnesses")
    (scripts / ("claude.cmd" if os.name == "nt" else "claude")).unlink()
    wheel_autorun = scripts / ("autorun.exe" if os.name == "nt" else "autorun")
    from_wheel, wheel_env = _install_every_harness(wheel_autorun, scripts, isolated, names)

    clone = root / "clone-harnesses"
    _run(["uv", "venv", "--python", sys.executable, clone], cwd=root, env=env)
    clone_python = clone / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _run(
        ["uv", "pip", "install", "--python", clone_python, "-e", checkout / "plugins" / "autorun"],
        cwd=root, env=env,
    )
    clone_env = isolated | {
        "HOME": str(root / "home-clone-harnesses"),
        "USERPROFILE": str(root / "home-clone-harnesses"),
        "AUTORUN_HOME": str(root / "autorun-home-clone-harnesses"),
        "AUTORUN_TEST_STATE_DIR": str(root / "state-clone-harnesses"),
        "AUTORUN_TEST_RUNTIME_DIR": str(root / "runtime-clone-harnesses"),
        "PATH": os.pathsep.join((str(clone_python.parent), os.defpath)),
    }
    from_clone, clone_env = _install_every_harness(
        clone_python.parent / ("autorun.exe" if os.name == "nt" else "autorun"),
        clone_python.parent, clone_env, names,
    )

    for name in names:
        assert from_clone[name], f"the clone install published nothing for {name}"
        assert from_wheel[name] == from_clone[name], (
            f"{name}: clone-only {sorted(from_clone[name] - from_wheel[name])}, "
            f"wheel-only {sorted(from_wheel[name] - from_clone[name])}"
        )

    # Each bundled plugin is published as itself, never again inside ar's trees.
    from autorun.resources import BUNDLED_PLUGINS_DIR

    bundled_skills = {
        skill.read_bytes()
        for skill in (checkout / "plugins").glob("*/skills/*/SKILL.md")
        if not skill.is_relative_to(checkout / "plugins" / "autorun")
    }
    assert bundled_skills, "no other marketplace plugin ships a skill to check for"
    for tree in _owned_trees(wheel_env, "ar"):
        nested = [path for path in tree.rglob("*") if BUNDLED_PLUGINS_DIR in path.parts]
        assert not nested, f"{tree} carries the bundled plugins: {nested[:3]}"
        copies = [
            path for path in tree.rglob("SKILL.md") if path.read_bytes() in bundled_skills
        ]
        assert not copies, f"{tree} carries another plugin's skill: {copies}"

    # Every harness finds each skill in as many places from a wheel as from a clone.
    assert reachable_skills(wheel_env, names) == reachable_skills(clone_env, names)

    # What makes the copy in Claude's ar cache inert: ar's manifest points no
    # component into it, and it is not under a directory Claude scans by default.
    package = Path(
        _run(
            [scripts / ("python.exe" if os.name == "nt" else "python"), "-c",
             "import autorun, os; print(os.path.dirname(autorun.__file__))"],
            cwd=root, env=env,
        ).stdout.strip()
    )
    manifest = json.loads((package / ".claude-plugin" / "plugin.json").read_text())
    for key in ("skills", "commands", "agents", "hooks"):
        declared = manifest.get(key, ())
        for value in [declared] if isinstance(declared, str) else declared:
            assert BUNDLED_PLUGINS_DIR not in Path(str(value)).parts, (key, value)
    assert (package / BUNDLED_PLUGINS_DIR).is_dir()
    for scanned in ("skills", "commands", "agents", "hooks"):
        assert not any(
            BUNDLED_PLUGINS_DIR in path.parts for path in (package / scanned).rglob("*")
        ), scanned

    # Removing ar leaves no bundled copy in anything autorun wrote, and the
    # bundled plugins survive it; removing and reinstalling one restores it.
    _run([wheel_autorun, "--uninstall", "ar"], cwd=scripts, env=wheel_env)
    assert not _owned_trees(wheel_env, "ar")
    claude_cache = Path(wheel_env["HOME"]) / ".claude" / "plugins" / "cache"
    for root_dir in (Path(wheel_env["HOME"]), Path(wheel_env["AUTORUN_HOME"])):
        left = [
            path for path in root_dir.rglob(BUNDLED_PLUGINS_DIR)
            if not path.is_relative_to(claude_cache)  # Claude's, see _owned_trees
        ]
        assert not left, left
    for name in names:
        if name == "ar":
            continue
        published = set(_owned_trees(wheel_env, name))
        assert published, f"uninstalling ar removed {name}"
        _run([wheel_autorun, "--uninstall", name], cwd=scripts, env=wheel_env)
        assert not _owned_trees(wheel_env, name)
        _install_all(wheel_autorun, scripts, wheel_env, name)
        assert set(_owned_trees(wheel_env, name)) == published, f"{name} not restored"


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
            # as_uri(): an f-string gives git+file://C:\... on Windows, which
            # uv reads as host "c" and git refuses.
            f"git+{checkout.as_uri()}#subdirectory=plugins/autorun",
        ],
        cwd=root,
        env=env,
    )
    autorun = python.parent / ("autorun.exe" if os.name == "nt" else "autorun")
    assert "1.0.0rc4" in _run([autorun, "--version"], cwd=root, env=env).stdout.split()
    # This route builds the wheel straight from the checkout, never through the
    # sdist, so it needs its own proof that every marketplace plugin came along.
    resolved = _run(
        [
            python,
            "-c",
            "from autorun.installer import discovery, entrypoint\n"
            "root = entrypoint._marketplace_root()\n"
            "for name in entrypoint.parse_selection(root):\n"
            "    print(name, discovery.plugin_dir(root, name))\n",
        ],
        cwd=root,
        env=env,
    ).stdout.split("\n")
    for name in _marketplace_plugin_names(checkout):
        assert any(
            line.startswith(f"{name} ") and not line.endswith(" None") for line in resolved
        ), f"{name} does not resolve in a git-subdirectory install: {resolved}"


def test_wheel_built_directly_from_a_text_link_checkout_carries_pdf_extraction(
    release_bundle,
):
    """A Windows clone with core.symlinks=false stores src/pdf_extraction as a
    text file naming its target. A git or local-path install builds the wheel
    straight from that tree, never through the sdist that materializes it."""
    root, checkout, _commit, env, _builds = release_bundle
    tree = root / "text-link-checkout"
    shutil.copytree(checkout / "plugins", tree / "plugins", symlinks=True)
    link = tree / "plugins" / "autorun" / "src" / "pdf_extraction"
    target = os.readlink(link)
    link.unlink()
    link.write_text(target, encoding="utf-8")
    output = root / "direct-wheel"
    _run(
        ["uv", "build", "--wheel", "--out-dir", output, tree / "plugins" / "autorun"],
        cwd=root,
        env=env,
    )
    wheel, = output.glob("*.whl")
    with zipfile.ZipFile(wheel) as archive:
        assert "pdf_extraction/cli.py" in archive.namelist()
    assert link.read_text(encoding="utf-8") == target, "the build edited the checkout"


def test_pdf_help_and_backend_inventory_are_lightweight(release_bundle):
    """The PDF CLI must work, and cost nothing, in a plain `autorun` install.

    Every backend is an extra, so the wheel installed here has none of them. The
    CLI still has to run and report what is missing rather than fail on import.
    """
    root, _checkout, _commit, env, builds = release_bundle
    wheel = next(builds[0].glob(f"{WHEEL_STEM}-*.whl"))
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
