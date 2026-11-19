"""`tec`: list, inspect, validate and export scenarios in a corpus checkout."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .export import export_fixtures
from .inspect import inspect_file
from .models import ARROW_FEATURES
from .validate import LoadedScenario, load_json, observe_transition, scenario_dirs, validate_corpus
from .values import encode


def _list(root: Path, include: set[str], exclude: set[str]) -> int:
    for d in scenario_dirs(root / "fixtures"):
        m = load_json(d / "scenario.json")
        if include or exclude:
            if "arrow_features" not in m:
                print(f"{d.name}: feature filters require a manifest with arrow_features", file=sys.stderr)
                return 2
            features = set(m["arrow_features"])
            if not include <= features or features & exclude:
                continue
        kinds = sorted({mu["kind"] for t in m["transitions"] for mu in t["mutations"]}) or ["-"]
        print(f"{m['scenario_id']:<32} {m['category']:<15} {len(m['versions'])}v  {','.join(kinds)}")
    return 0


def _inspect(root: Path, scenario_id: str) -> int:
    directory = root / "fixtures" / scenario_id
    if not directory.is_dir():
        print(f"no such scenario: {scenario_id}", file=sys.stderr)
        return 2
    scenario = LoadedScenario.load(directory)
    report = {
        "scenario_id": scenario_id,
        "versions": {vid: inspect_file(scenario.path(vid)) for vid in scenario.versions},
        "transitions": [],
    }
    for t in scenario.manifest["transitions"]:
        seen = observe_transition(scenario, t["from"], t["to"])
        seen["value_changes"] = {
            path: [[encode(a), encode(b)] for a, b in pairs] for path, pairs in seen["value_changes"].items()
        }
        report["transitions"].append({"from": t["from"], "to": t["to"], "observed": seen})
    # ASCII escapes: printable on any console, and composed vs decomposed
    # Unicode stays visible instead of rendering identically.
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


def _validate(root: Path, only: list[str]) -> int:
    results = validate_corpus(root, only or None)
    failed = 0
    for scenario_id, problems in results.items():
        for p in problems:
            print(f"FAIL {scenario_id}: {p}")
        failed += bool(problems)
    print(f"{len(results) - 1} scenarios checked, {failed} with problems")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tec", description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="corpus checkout (default: .)")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="one line per scenario")
    listing.add_argument("--feature", action="append", choices=ARROW_FEATURES, default=[], help="require this feature")
    listing.add_argument(
        "--exclude-feature",
        action="append",
        choices=ARROW_FEATURES,
        default=[],
        help="omit scenarios with this feature",
    )
    inspect = sub.add_parser("inspect", help="facts the PyArrow reference inspector reads from a scenario's files")
    inspect.add_argument("scenario_id")
    validate = sub.add_parser("validate", help="check manifests against their files")
    validate.add_argument("scenario_ids", nargs="*")
    export = sub.add_parser("export", help="copy fixtures and their provenance to a directory")
    export.add_argument("dest", type=Path)
    export.add_argument("scenario_ids", nargs="*")
    args = parser.parse_args(argv)

    if args.command == "list":
        return _list(args.root, set(args.feature), set(args.exclude_feature))
    if args.command == "inspect":
        return _inspect(args.root, args.scenario_id)
    if args.command == "export":
        try:
            export_fixtures(args.root, args.dest, args.scenario_ids)
        except (OSError, ValueError) as error:
            print(f"export: {error}", file=sys.stderr)
            return 2
        return 0
    return _validate(args.root, args.scenario_ids)


if __name__ == "__main__":
    sys.exit(main())
