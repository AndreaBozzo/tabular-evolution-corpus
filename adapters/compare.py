"""Compare two JSON Lines files of adapter results by record key."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .runner import RECORD_KEY_FIELDS, RecordKey, read_results, record_key

RUN_FIELDS = ("schema_version", "corpus_version", "corpus_revision", "adapter_version", "platform")
NON_OUTCOME_FIELDS = frozenset((*RECORD_KEY_FIELDS, *RUN_FIELDS))


def _format_key(key: RecordKey) -> str:
    return json.dumps(dict(zip(RECORD_KEY_FIELDS, key, strict=True)))


def _index(path: Path) -> dict[RecordKey, dict[str, Any]]:
    indexed = {}
    for record in read_results(path):
        key = record_key(record)
        if key in indexed:
            raise ValueError(f"{path}: duplicate record key {_format_key(key)}")
        indexed[key] = record
    return indexed


def _format_field(record: dict[str, Any], field: str) -> str:
    return json.dumps(record[field], sort_keys=True) if field in record else "(absent)"


def _metadata(records: dict[RecordKey, dict[str, Any]], field: str) -> str:
    return ", ".join(sorted({_format_field(record, field) for record in records.values()})) or "(no records)"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m adapters.compare", description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    args = parser.parse_args(argv)

    try:
        left, right = _index(args.left), _index(args.right)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"cannot compare results: {error}", file=sys.stderr)
        return 2

    print(f"Left: {args.left}")
    print(f"Right: {args.right}")
    for field in RUN_FIELDS:
        print(f"  {field}: {_metadata(left, field)} -> {_metadata(right, field)}")

    changed = False
    missing = object()
    for key in sorted(left.keys() | right.keys()):
        if key not in left:
            print(f"Right only {_format_key(key)}")
            changed = True
            continue
        if key not in right:
            print(f"Left only {_format_key(key)}")
            changed = True
            continue
        before, after = left[key], right[key]
        fields = (before.keys() | after.keys()) - NON_OUTCOME_FIELDS
        differing = [field for field in sorted(fields) if before.get(field, missing) != after.get(field, missing)]
        if differing:
            print(f"Changed {_format_key(key)}")
            for field in differing:
                print(f"  {field}: {_format_field(before, field)} -> {_format_field(after, field)}")
            changed = True
    if not changed:
        print("No observation changes.")
    return int(changed)


if __name__ == "__main__":
    sys.exit(main())
