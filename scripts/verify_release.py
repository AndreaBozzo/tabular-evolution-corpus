"""Release gate: everything committed is valid, complete and reproducible.

1. every scenario validates against its files;
2. regenerating fixtures reproduces the committed ones (logically always,
   byte-for-byte when the installed PyArrow wrote the committed files);
3. rebuilding the catalog reproduces data/scenarios.parquet;
4. SHA256SUMS matches the committed files;
5. the Hugging Face card points its viewer at the catalog only, and no
   published document still contains a placeholder.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

from tabular_evolution.catalog import CATALOG_PATH, read_catalog, write_catalog
from tabular_evolution.generate import generate_all
from tabular_evolution.release import (
    SUMS_PATH,
    compare_trees,
    installed_writer,
    render_sums,
    same_writer,
)
from tabular_evolution.validate import validate_corpus

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    problems: list[str] = []

    results = validate_corpus(ROOT)
    for scenario_id, found in results.items():
        problems += [f"validate {scenario_id}: {p}" for p in found]
    print(f"[1] validated {len(results) - 1} scenarios")

    exact = same_writer(ROOT)
    level = "byte-for-byte" if exact else "logically (committed files were written by another PyArrow version)"
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp)
        generate_all(fresh / "fixtures")
        problems += [f"regenerate: {p}" for p in compare_trees(ROOT, fresh, require_bytes=exact)]
        print(f"[2] regenerated fixtures compared {level} ({installed_writer()})")

        rebuilt = write_catalog(ROOT, fresh / CATALOG_PATH)
        committed = ROOT / CATALOG_PATH
        if not committed.is_file():
            problems.append(f"catalog: {CATALOG_PATH.as_posix()} is missing")
        elif not read_catalog(rebuilt).equals(read_catalog(committed)):
            problems.append("catalog: rebuilt catalog differs from the committed one")
        elif exact and rebuilt.read_bytes() != committed.read_bytes():
            problems.append("catalog: rebuilt catalog differs byte-for-byte")
        print("[3] catalog rebuilt and compared")

    sums = ROOT / SUMS_PATH
    if not sums.is_file():
        problems.append(f"{SUMS_PATH} is missing; run scripts/build_catalog.py")
    elif exact and sums.read_bytes().decode("ascii") != render_sums(ROOT):
        problems.append(f"{SUMS_PATH} is stale; run scripts/build_catalog.py")
    print(f"[4] {SUMS_PATH} checked" + ("" if exact else " (skipped: different writer)"))

    card = ROOT / "hf" / "README.md"
    text = card.read_text(encoding="utf-8") if card.is_file() else ""
    if f"path: {CATALOG_PATH.as_posix()}" not in text:
        problems.append("hf/README.md must declare a config whose data_files path is data/scenarios.parquet")
    for name in ("hf/README.md", "README.md", "CITATION.cff", "schema/scenario.schema.json"):
        if re.search(r"<(owner|user|org|repo|todo)>|TODO|TBD", (ROOT / name).read_text(encoding="utf-8"), re.I):
            problems.append(f"{name} still contains a placeholder")
    print("[5] dataset card checked")

    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    print("release OK" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
