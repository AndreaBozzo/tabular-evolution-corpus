"""Run adapters over every transition of a corpus checkout and record what
each engine did, one JSON Lines file per adapter and run.

    uv run python -m adapters.runner                 # every registered adapter
    uv run python -m adapters.runner duckdb polars   # some of them

The run is pinned to exact corpus bytes: the checkout is clean, the corpus
version is tagged, and `fixtures/` is identical to the tagged tree. Records
are validated against `schema/result.schema.json` before anything is written.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from tabular_evolution import __version__
from tabular_evolution.generate import MANIFEST
from tabular_evolution.validate import load_json, scenario_dirs

from . import REGISTRY, load_adapter
from .base import OPERATIONS, Adapter

RESULT_SCHEMA = Path("schema") / "result.schema.json"
RESULTS_DIR = Path("results")
RECORD_VERSION = "1.0"


class CorpusError(RuntimeError):
    """The checkout cannot be pinned to a corpus release."""


@dataclass(frozen=True)
class CorpusIdentity:
    version: str
    revision: str


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if done.returncode != 0:
        raise CorpusError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def corpus_identity(root: Path, version: str = __version__) -> CorpusIdentity:
    """The corpus version and the exact commit the fixtures are read from."""
    if _git(root, "status", "--porcelain"):
        raise CorpusError("the checkout has uncommitted or untracked changes; results must name exact bytes")
    tag = f"v{version}"
    try:
        _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    except CorpusError:
        raise CorpusError(f"corpus version {version} is not tagged ({tag}); tag the release first") from None
    if _git(root, "rev-parse", f"{tag}^{{commit}}:fixtures") != _git(root, "rev-parse", "HEAD:fixtures"):
        raise CorpusError(f"fixtures/ differs from {tag}; a changed corpus is a new release")
    return CorpusIdentity(version, _git(root, "rev-parse", "HEAD"))


def _inputs(operation: str, from_: str, to: str) -> list[list[str]]:
    return [[from_], [to]] if operation == "read_each_version" else [[from_, to]]


def _outcome(adapter: Adapter, operation: str, mode: str, paths: list[str]) -> dict[str, Any]:
    try:
        result = adapter.read(operation, mode, paths)
    # The engine's answer, recorded as such. BaseException because Rust
    # engines surface a panic as a BaseException subclass (pyo3).
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        kind = type(e)
        message = str(e).strip()
        return {
            "status": "error",
            "result_schema": None,
            "row_count": None,
            "error_class": f"{kind.__module__}.{kind.__qualname__}",
            "notes": [message] if message else [],
        }
    seen = adapter.describe(result)  # an exception here is an adapter bug: let it stop the run
    unmapped = [f"{f.name}: {f.native_type} has no equivalent in the corpus type vocabulary" for f in seen.fields if f.type is None]
    return {
        "status": "success",
        "result_schema": [f.to_record() for f in seen.fields],
        "row_count": seen.row_count,
        "error_class": None,
        "notes": unmapped + seen.notes,
    }


def check_record(record: dict[str, Any], validator: Draft202012Validator) -> list[str]:
    """Schema conformance plus the rule a schema cannot express: the inputs
    are the transition's own versions, in order."""
    problems = [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(record)]
    if not problems:
        expected = _inputs(record["operation"], record["from"], record["to"])
        if record["inputs"] not in expected:
            problems.append(f"inputs {record['inputs']} are not one of {expected}")
    return problems


def result_validator(root: Path) -> Draft202012Validator:
    schema = load_json(root / RESULT_SCHEMA)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def observe(root: Path, adapter: Adapter, identity: CorpusIdentity, only: list[str] | None = None) -> list[dict[str, Any]]:
    """Every declared (operation, mode) of `adapter` on every transition, in
    corpus order. Each record is validated; an invalid one is an adapter bug."""
    unknown = set(adapter.modes) - set(OPERATIONS)
    if unknown:
        raise ValueError(f"{adapter.name}: unknown operations {sorted(unknown)}")
    validator = result_validator(root)
    engine_version = adapter.version
    records = []
    # Engines see corpus-relative paths, so that paths quoted in their error
    # messages do not depend on where the checkout lives.
    with contextlib.chdir(root):
        for directory in scenario_dirs(Path("fixtures")):
            manifest = load_json(directory / MANIFEST)
            if only is not None and manifest["scenario_id"] not in only:
                continue
            files = {v["id"]: (directory / v["file"]).as_posix() for v in manifest["versions"]}
            for t in manifest["transitions"]:
                for operation in OPERATIONS:
                    for mode in adapter.modes.get(operation, ()):
                        for inputs in _inputs(operation, t["from"], t["to"]):
                            record = {
                                "schema_version": RECORD_VERSION,
                                "corpus_version": identity.version,
                                "corpus_revision": identity.revision,
                                "scenario_id": manifest["scenario_id"],
                                "from": t["from"],
                                "to": t["to"],
                                "inputs": inputs,
                                "adapter": adapter.name,
                                "adapter_version": engine_version,
                                "operation": operation,
                                "mode": mode,
                                **_outcome(adapter, operation, mode, [files[i] for i in inputs]),
                            }
                            problems = check_record(record, validator)
                            if problems:
                                raise ValueError(f"{adapter.name} produced an invalid record: {problems}")
                            records.append(record)
    return records


def results_path(root: Path, adapter_name: str, corpus_version: str) -> Path:
    return root / RESULTS_DIR / corpus_version / f"{adapter_name}.jsonl"


def write_results(path: Path, records: list[dict[str, Any]]) -> Path:
    """One record per line, ASCII-only, LF endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in records)
    path.write_bytes(text.encode("ascii"))
    return path


def read_results(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().decode("ascii").splitlines()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m adapters.runner", description=__doc__.split("\n\n")[0])
    parser.add_argument("adapters", nargs="*", help=f"default: all ({', '.join(sorted(REGISTRY))})")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="corpus checkout")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    try:
        identity = corpus_identity(root)
    except CorpusError as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2
    print(f"corpus {identity.version} at {identity.revision}")
    for name in args.adapters or sorted(REGISTRY):
        adapter = load_adapter(name)
        records = observe(root, adapter, identity)
        path = write_results(results_path(root, adapter.name, identity.version), records)
        errors = sum(r["status"] == "error" for r in records)
        print(f"{name} {adapter.version}: {len(records)} records ({errors} errors) -> {path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
