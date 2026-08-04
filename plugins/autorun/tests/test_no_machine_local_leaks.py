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


def test_tracked_files_contain_no_real_ip_addresses():
    offenders = []
    for name, text in _tracked_text_files():
        for match in _IPV4.finditer(text):
            ip = match.group(1)
            if any(int(octet) > 255 for octet in ip.split(".")):
                continue  # version-like number, not an address
            if ip.startswith(_IP_ALLOWLIST_PREFIXES):
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
