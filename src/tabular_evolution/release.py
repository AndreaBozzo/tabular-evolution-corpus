"""Reproducibility checks shared by the release script and the tests.

Reproducibility is checked at two levels (DESIGN.md section 4):

- logical: manifests are byte-identical, and every Parquet file has the same
  logical fingerprint (schema + values + row order);
- bytes: every generated file is byte-identical. Required only when the
  installed PyArrow is the version that wrote the committed files, because
  another writer version may legitimately produce different bytes.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pyarrow as pa

from .catalog import CATALOG_PATH
from .inspect import created_by, describe_schema, read_table
from .values import fingerprint

SUMS_PATH = Path("SHA256SUMS")


def installed_writer() -> str:
    return f"parquet-cpp-arrow version {pa.__version__}"


def generated_files(root: Path) -> list[Path]:
    """Every generated artifact, relative to `root`, in a stable order."""
    fixtures = sorted(p.relative_to(root) for p in (root / "fixtures").rglob("*") if p.is_file())
    return fixtures + [CATALOG_PATH]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_sums(root: Path) -> str:
    """`sha256sum -c` compatible, POSIX paths, LF endings."""
    return "".join(f"{sha256(root / p)}  {p.as_posix()}\n" for p in generated_files(root))


def write_sums(root: Path) -> Path:
    path = root / SUMS_PATH
    path.write_bytes(render_sums(root).encode("ascii"))
    return path


def same_writer(root: Path) -> bool:
    """True when every committed Parquet file was written by the installed PyArrow."""
    return all(created_by(root / p) == installed_writer() for p in generated_files(root) if p.suffix == ".parquet")


def compare_trees(expected: Path, actual: Path, *, require_bytes: bool) -> list[str]:
    """Differences between two generated trees (fixtures only, relative paths)."""
    problems: list[str] = []
    a = {p.relative_to(expected) for p in (expected / "fixtures").rglob("*") if p.is_file()}
    b = {p.relative_to(actual) for p in (actual / "fixtures").rglob("*") if p.is_file()}
    for p in sorted(a - b):
        problems.append(f"not regenerated: {p.as_posix()}")
    for p in sorted(b - a):
        problems.append(f"regenerated but not committed: {p.as_posix()}")
    for p in sorted(a & b):
        left, right = expected / p, actual / p
        if p.suffix == ".parquet":
            fl, fr = logical_fingerprint(left), logical_fingerprint(right)
            if fl != fr:
                problems.append(f"logical difference: {p.as_posix()} ({fl} != {fr})")
            elif require_bytes and left.read_bytes() != right.read_bytes():
                problems.append(f"byte difference: {p.as_posix()}")
        elif left.read_bytes() != right.read_bytes():
            problems.append(f"content difference: {p.as_posix()}")
    return problems


def logical_fingerprint(path: Path) -> str:
    table = read_table(path)
    return fingerprint(table, describe_schema(table.schema))
