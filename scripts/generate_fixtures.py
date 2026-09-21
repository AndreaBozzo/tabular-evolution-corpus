"""Regenerate every fixture and manifest under fixtures/, then validate them.

Exits non-zero if any generated fixture disagrees with its manifest.
"""

from __future__ import annotations

import sys
from pathlib import Path

from tabular_evolution.generate import generate_all
from tabular_evolution.validate import validate_corpus

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    directories = generate_all(ROOT / "fixtures")
    print(f"generated {len(directories)} scenarios in fixtures/")
    failures = {k: v for k, v in validate_corpus(ROOT).items() if v}
    for scenario_id, problems in failures.items():
        for problem in problems:
            print(f"FAIL {scenario_id}: {problem}", file=sys.stderr)
    if failures:
        return 1
    print("all scenarios validate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
