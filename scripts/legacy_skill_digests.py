#!/usr/bin/env python3
"""Write the frozen catalog of every skill version committed before receipts.

``installer/provenance.py`` adopts an unmarked tree only when it is byte-
identical to something autorun wrote. Installs from now on leave a receipt in
autorun's state directory; installs before that left only the in-tree marker,
which a user's tooling can lose. This script covers those: it fingerprints
every version of every shipped skill directory in the repository's history,
the way an install wrote it, and writes
``plugins/autorun/src/autorun/installer/_legacy_skill_digests.py``.

Each version is fingerprinted twice, as :func:`provenance.tree_digests` does:
with its symlinks kept (``copytree(symlinks=True)``) and with each link to a
file in the same skill replaced by that file, which is what earlier installs
wrote. Ignored names are skipped by :func:`fs._ignored`, the rule ``scan_tree``
applies. Digests are keyed by the plugin that ships the name today (the heir
for a skill that moved), so ``--uninstall`` of one plugin never adopts
another's copies.

Run from the repository root with full history (not a shallow clone):

    uv run --project plugins/autorun python scripts/legacy_skill_digests.py

The catalog is frozen: rerun it only to cover history that predates receipts.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins" / "autorun" / "src"))

from autorun.installer.fs import _LINK, _ignored  # noqa: E402
from autorun.installer.provenance import _digest  # noqa: E402

OUTPUT = ROOT / "plugins/autorun/src/autorun/installer/_legacy_skill_digests.py"
SKILL_FILE = re.compile(r"^plugins/(?P<plugin>[^/]+)/skills/(?P<name>[^/]+)/(?P<rest>.+)$")
#: Plugin directory -> plugin id. The ``ar`` plugin's directory kept the old name.
PLUGIN_IDS = {"autorun": "ar", "clautorun": "ar"}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


class Blobs:
    """Content hashes and link targets, one ``cat-file --batch`` for all."""

    def __init__(self) -> None:
        self.cache: dict[str, bytes] = {}
        self.proc = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )

    def read(self, sha: str) -> bytes:
        if sha not in self.cache:
            assert self.proc.stdin and self.proc.stdout
            self.proc.stdin.write(f"{sha}\n".encode())
            self.proc.stdin.flush()
            size = int(self.proc.stdout.readline().split()[2])
            data = self.proc.stdout.read(size)
            self.proc.stdout.read(1)
            self.cache[sha] = data
        return self.cache[sha]

    def fingerprint(self, mode: str, sha: str) -> str:
        data = self.read(sha)
        if mode == "120000":
            return _LINK + data.decode("utf-8", "surrogateescape")
        return f"{hashlib.sha256(data).hexdigest()}:{'x' if mode == '100755' else '-'}"


def owners_today() -> dict[str, str]:
    """Skill name -> the plugin id that ships it at HEAD."""
    owners: dict[str, str] = {}
    for path in git("ls-tree", "-r", "--name-only", "HEAD", "--", "plugins").splitlines():
        match = SKILL_FILE.match(path)
        if match and match["rest"] == "SKILL.md":
            owners[match["name"]] = PLUGIN_IDS.get(match["plugin"], match["plugin"])
    return owners


def versions(blobs: Blobs) -> dict[str, dict[str, set[str]]]:
    owners = owners_today()
    catalog: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    # A wildcard pathspec matches whole paths, not a directory prefix, so the
    # pattern must reach the files: "plugins/*/skills" alone matched nothing.
    commits = git("log", "--all", "--format=%H", "--", "plugins/*/skills/*").split()
    for commit in commits:
        trees: dict[tuple[str, str], dict[str, tuple[str, str]]] = defaultdict(dict)
        for line in git("ls-tree", "-r", commit, "--", "plugins").splitlines():
            meta, path = line.split("\t", 1)
            mode, kind, sha = meta.split()
            match = SKILL_FILE.match(path)
            if kind != "blob" or not match:
                continue
            if any(_ignored(part) for part in match["rest"].split("/")):
                continue
            trees[(match["plugin"], match["name"])][match["rest"]] = (mode, sha)
        for (plugin_dir, name), files in trees.items():
            if "SKILL.md" not in files:
                continue
            raw = {rel: blobs.fingerprint(mode, sha) for rel, (mode, sha) in files.items()}
            resolved = dict(raw)
            for rel, (mode, sha) in files.items():
                if mode != "120000":
                    continue
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(rel), raw[rel][len(_LINK):])
                )
                if target in files and files[target][0] != "120000":
                    resolved[rel] = blobs.fingerprint(*files[target])
            owner = owners.get(name, PLUGIN_IDS.get(plugin_dir, plugin_dir))
            catalog[owner][name].update({_digest(raw), _digest(resolved)})
    return catalog


def render(catalog: dict[str, dict[str, set[str]]]) -> str:
    lines = [
        '"""Generated by scripts/legacy_skill_digests.py. Do not edit.',
        "",
        "Digests of every skill version committed before installs left receipts;",
        "see installer/provenance.py.",
        '"""',
        "",
        "DIGESTS: dict[str, dict[str, tuple[str, ...]]] = {",
    ]
    for plugin in sorted(catalog):
        lines.append(f"    {plugin!r}: {{")
        for name in sorted(catalog[plugin]):
            lines.append(f"        {name!r}: (")
            lines.extend(f"            {digest!r}," for digest in sorted(catalog[plugin][name]))
            lines.append("        ),")
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        print("needs full history: git fetch --unshallow", file=sys.stderr)
        return 1
    catalog = versions(Blobs())
    OUTPUT.write_text(render(catalog), encoding="utf-8")
    count = sum(len(d) for names in catalog.values() for d in names.values())
    print(f"wrote {OUTPUT.relative_to(ROOT)}: {count} digests, "
          f"{sum(len(n) for n in catalog.values())} skills")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
