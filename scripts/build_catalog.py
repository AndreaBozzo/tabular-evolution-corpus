"""Build data/scenarios.parquet from the manifests, then refresh SHA256SUMS."""

from __future__ import annotations

import sys
from pathlib import Path

from tabular_evolution.catalog import read_catalog, write_catalog
from tabular_evolution.release import write_sums

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    path = write_catalog(ROOT)
    print(f"wrote {path.relative_to(ROOT).as_posix()} ({read_catalog(path).num_rows} scenarios)")
    print(f"wrote {write_sums(ROOT).relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
