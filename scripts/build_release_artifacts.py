#!/usr/bin/env python3
"""Build both distributions with byte-stable metadata for one pinned toolchain."""

from __future__ import annotations

import argparse
import gzip
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (Path("plugins/autorun"), Path("plugins/pdf-extractor"))


def _source_date_epoch(env: dict[str, str]) -> int:
    raw = env.get("SOURCE_DATE_EPOCH", "").strip()
    if raw:
        return int(raw)
    result = subprocess.run(
        ["git", "show", "-s", "--format=%ct", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def _normalize_sdist(path: Path, epoch: int) -> None:
    """Rewrite one generated tarball with stable ownership and timestamps."""
    with tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        with tarfile.open(path, "r:gz") as source:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=temporary,
                compresslevel=9,
                mtime=epoch,
            ) as compressed:
                with tarfile.open(
                    fileobj=compressed,
                    mode="w",
                    format=tarfile.PAX_FORMAT,
                ) as target:
                    for member in source.getmembers():
                        payload = source.extractfile(member) if member.isfile() else None
                        member.uid = 0
                        member.gid = 0
                        member.uname = ""
                        member.gname = ""
                        member.mtime = epoch
                        member.pax_headers = {}
                        target.addfile(member, payload)
    os.replace(temporary_path, path)


def build(output: Path) -> tuple[Path, ...]:
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    epoch = _source_date_epoch(env)
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    for package in PACKAGES:
        subprocess.run(
            ["uv", "build", "--out-dir", str(output), str(REPO_ROOT / package)],
            cwd=REPO_ROOT,
            env=env,
            check=True,
        )
    for archive in output.glob("*.tar.gz"):
        _normalize_sdist(archive, epoch)
    return tuple(sorted(path for path in output.iterdir() if path.is_file()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    for artifact in build(args.out_dir.resolve()):
        print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
