"""Vendor byte-exact fixtures from a corpus checkout."""

from __future__ import annotations

import hashlib
import subprocess
import tomllib
from pathlib import Path

from .release import SUMS_PATH
from .validate import scenario_dirs


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


def export_fixtures(root: Path, dest: Path, scenario_ids: list[str]) -> None:
    """Copy the selected fixtures and their provenance without overwriting files."""
    directories = {path.name: path for path in scenario_dirs(root / "fixtures")}
    selected = set(scenario_ids) or set(directories)
    unknown = sorted(selected - directories.keys())
    if unknown:
        raise ValueError(f"unknown scenario: {', '.join(unknown)}")

    fixtures = {
        file.relative_to(root).as_posix(): file.read_bytes()
        for name in sorted(selected)
        for file in sorted(directories[name].rglob("*"))
        if file.is_file()
    }
    sums = [
        line
        for line in (root / SUMS_PATH).read_text(encoding="ascii").splitlines()
        if line.partition("  ")[2] in fixtures
    ]
    actual = {f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in fixtures.items()}
    if set(sums) != actual or len(sums) != len(actual):
        raise ValueError("selected fixtures differ from SHA256SUMS")

    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    revision = _git(root, "rev-parse", "HEAD")
    if _git(root, "status", "--porcelain", "--", "fixtures", "SHA256SUMS", "LICENSE", "pyproject.toml"):
        revision += "-dirty"
    files = {
        Path(name): data
        for name, data in {
            **fixtures,
            SUMS_PATH.as_posix(): ("\n".join(sums) + "\n").encode("ascii"),
            "LICENSE": (root / "LICENSE").read_bytes(),
            "README.txt": f"tabular-evolution-corpus {version}\nGit revision: {revision}\n".encode("ascii"),
            ".gitattributes": b"* -text\n",
        }.items()
    }

    if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
        raise ValueError(f"destination is not a directory: {dest}")
    # Preflight the entire destination before adding any missing files.
    expected_dirs = {parent for path in files for parent in path.parents}
    for existing in dest.rglob("*"):
        relative = existing.relative_to(dest)
        if existing.is_symlink() or not (
            (relative in expected_dirs and existing.is_dir())
            or (relative in files and existing.is_file() and existing.read_bytes() == files[relative])
        ):
            raise ValueError(f"destination contains a different entry: {existing}")

    for relative, data in files.items():
        target = dest / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
