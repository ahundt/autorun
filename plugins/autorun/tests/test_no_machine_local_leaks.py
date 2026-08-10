"""Tracked files stay self-contained and free of machine-local identity.

The local username is derived at runtime (getpass/Path.home) so this file
never embeds any particular contributor's name; every clone checks for the
identity that could actually leak from that machine. IP addresses are
matched structurally with a small allowlist for loopback, wildcard, and the
RFC 5737 documentation ranges.

autorun creates notes/ and fills it with exported plans and generated
reports. That directory is local working output: it is gitignored, never
tracked, and `.githooks/pre-commit` blocks staging anything under it. These
tests are the cross-machine enforcement; the hook is the fast local
feedback.
"""

import getpass
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
HOOK_PATH = REPO_ROOT / ".githooks" / "pre-commit"

_IPV4 = re.compile(r"(?<![\w.])((?:\d{1,3}\.){3}\d{1,3})(?![\w.])")
_GENERATED_LOCKFILES = {"uv.lock"}
_URL_HOST = re.compile(r"//$")
_IP_ALLOWLIST_PREFIXES = (
    "127.",          # loopback
    "0.0.0.0",       # wildcard bind
    "255.255.255.",  # broadcast/netmask
    "192.0.2.",      # RFC 5737 TEST-NET-1
    "198.51.100.",   # RFC 5737 TEST-NET-2
    "203.0.113.",    # RFC 5737 TEST-NET-3
)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _tracked_text_files():
    for name in _git("ls-files", "-z").split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable — path names are still covered below


def test_notes_directory_is_untracked():
    tracked = _git("ls-files", "notes/").strip()
    assert not tracked, (
        "notes/ is autorun's local plan and report output and is never "
        "tracked. Untrack with: git rm -r --cached notes/  Tracked entries:\n"
        + tracked
    )


def test_tracked_files_do_not_name_the_local_username():
    home = str(Path.home())
    user = getpass.getuser()
    needles = {home, f"/Users/{user}", f"/home/{user}"}
    offenders = [
        f"{name}: {needle}"
        for name, text in _tracked_text_files()
        for needle in needles
        if needle in text
    ]
    assert not offenders, (
        "Tracked files embed this machine's identity; replace with ~ or a "
        "generic path such as /home/user:\n" + "\n".join(offenders)
    )


def test_no_tracked_file_is_binary():
    """Binaries do not belong in this repository.

    Classified by content, not by extension, using git's own detection: a
    binary blob reports ``-`` for both counts against the empty tree. That
    catches an unusual or missing extension, which an extension allowlist
    cannot. The four tracked ``.svg`` diagrams are XML and stay text.
    """
    empty_tree = _git("hash-object", "-t", "tree", "/dev/null").strip()
    offenders = [
        line.split("\t", 2)[2]
        for line in _git("diff", "--numstat", empty_tree, "HEAD").splitlines()
        if line.startswith("-\t-\t")
    ]
    assert not offenders, (
        "Tracked files are binary; build output and archives belong in "
        ".gitignore, and generated assets should be produced at build time:\n"
        + "\n".join(offenders)
    )


# One representative path per artifact family .gitignore must exclude. Each is
# reachable from work this repository actually performs: `uv build --wheel` in
# the packaging tests, `git bundle` recovery artifacts, zip backups, the SQLite
# session store, and native builds on the platforms autorun claims to support.
_MUST_BE_IGNORED = (
    "lib.dylib",
    "lib.so",
    "pkg.whl",
    "archive.zip",
    "archive.7z",
    "archive.tgz",
    "archive.tar.gz",
    "archive.tar.bz2",
    "archive.tar.xz",
    "recovery.bundle",
    "objects.pack",
    "state.sqlite",
    "state.sqlite3",
    "tool.exe",
    "lib.dll",
    "object.o",
    "static.a",
    "crate.rlib",
    "Klass.class",
    "app.jar",
    "module.wasm",
    "native.node",
    "blob.bin",
    "opaque.dat",
    "module.pyc",
    "pkg/__pycache__/mod.pyc",
)


@pytest.mark.parametrize("candidate", _MUST_BE_IGNORED)
def test_gitignore_excludes_binary_and_build_artifacts(candidate):
    """.gitignore must exclude each artifact family before one can be staged.

    ``git check-ignore`` exits 0 only when a rule matches, so this asks git
    itself rather than reading the file, which keeps the check honest about
    negations and ordering.
    """
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--", candidate],
        capture_output=True,
    )
    assert result.returncode == 0, (
        f"{candidate!r} is not ignored, so a stray build artifact of that kind "
        "could be committed. Add the pattern to .gitignore."
    )


def test_tracked_files_contain_no_real_ip_addresses():
    offenders = []
    for name, text in _tracked_text_files():
        for match in _IPV4.finditer(text):
            ip = match.group(1)
            if any(int(octet) > 255 for octet in ip.split(".")):
                continue  # version-like number, not an address
            if ip.startswith(_IP_ALLOWLIST_PREFIXES):
                continue
            if name in _GENERATED_LOCKFILES and not _URL_HOST.search(
                text[max(0, match.start() - 2):match.start()]
            ):
                # uv.lock is generated, and CUDA and OpenCV publish four-part
                # versions that are indistinguishable from an address by shape
                # alone -- both as a version field and inside every wheel
                # filename. Only a match in URL host position is reported here,
                # which is where a private index reached by address would show
                # up; hand-authored files stay fully checked.
                continue
            offenders.append(f"{name}: {ip}")
    assert not offenders, (
        "Tracked files contain IP addresses; use an RFC 5737 documentation "
        "address (192.0.2.x) instead:\n" + "\n".join(offenders)
    )


class TestPreCommitHookBlocksNotes:
    """The tracked hook rejects staged notes/ content with the policy text."""

    @pytest.fixture()
    def scratch_repo(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Test"], check=True
        )
        hooks = repo / ".githooks"
        hooks.mkdir()
        shutil.copy(HOOK_PATH, hooks / "pre-commit")
        (hooks / "pre-commit").chmod(0o755)
        subprocess.run(
            ["git", "-C", str(repo), "config", "core.hooksPath", ".githooks"],
            check=True,
        )
        return repo

    def _commit(self, repo, message):
        return subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", message],
            capture_output=True,
            text=True,
        )

    def test_hook_file_is_tracked_and_executable(self):
        assert HOOK_PATH.is_file(), ".githooks/pre-commit missing"
        mode = _git("ls-files", "-s", ".githooks/pre-commit").split()[0]
        assert mode == "100755", f"hook must be executable in the index, got {mode}"

    def test_staged_notes_file_is_blocked_with_policy(self, scratch_repo):
        notes = scratch_repo / "notes"
        notes.mkdir()
        (notes / "leak.md").write_text("evidence", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(scratch_repo), "add", "-f", "notes/leak.md"],
            check=True,
        )
        result = self._commit(scratch_repo, "should be blocked")
        assert result.returncode != 0
        feedback = result.stderr + result.stdout
        assert "COMMIT BLOCKED" in feedback
        assert "notes/ stays out of version control" in feedback
        assert "git restore --staged" in feedback

    def test_ordinary_file_commits_cleanly(self, scratch_repo):
        (scratch_repo / "code.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(scratch_repo), "add", "code.py"], check=True)
        result = self._commit(scratch_repo, "allowed")
        assert result.returncode == 0, result.stderr

    def test_deleting_a_tracked_notes_file_is_allowed(self, scratch_repo):
        """Removals must pass so untracking notes/ itself is never blocked."""
        notes = scratch_repo / "notes"
        notes.mkdir()
        (notes / "old.md").write_text("evidence", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(scratch_repo), "add", "-f", "notes/old.md"], check=True
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(scratch_repo),
                "-c",
                "core.hooksPath=/dev/null",
                "commit",
                "-q",
                "-m",
                "seed",
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(scratch_repo), "rm", "-q", "--cached", "notes/old.md"],
            check=True,
        )
        result = self._commit(scratch_repo, "untrack notes")
        assert result.returncode == 0, result.stderr
