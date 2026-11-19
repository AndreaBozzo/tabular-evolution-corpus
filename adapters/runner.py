"""Run adapters over every transition of a corpus checkout and record what
each engine did, one JSON Lines file per adapter and run.

    uv run python -m adapters.runner                 # every registered adapter
    uv run python -m adapters.runner duckdb polars   # some of them

The corpus version is tagged and `fixtures/` matches the tagged tree for
every run. A run from a dirty checkout is marked and written separately.
Records are validated against `schema/result.schema.json` before writing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from tabular_evolution import __version__
from tabular_evolution.generate import MANIFEST
from tabular_evolution.validate import load_json, scenario_dirs

from . import REGISTRY, load_adapter
from .base import OPERATIONS, SINGLE_VERSION_OPERATIONS, Adapter

RESULT_SCHEMA = Path("schema") / "result.schema.json"
RESULTS_DIR = Path("results")
RECORD_VERSION = "1.2"
RECORD_KEY_FIELDS = ("adapter", "scenario_id", "from", "to", "operation", "mode", "inputs")
DIRTY_SUFFIX = "-dirty"
_MACHINES = {"amd64": "x86_64", "aarch64": "arm64"}

RecordKey = tuple[str | tuple[str, ...], ...]


def record_key(record: dict[str, Any]) -> RecordKey:
    """Identify one engine call within a run, including the input order."""
    return tuple(tuple(record[field]) if field == "inputs" else record[field] for field in RECORD_KEY_FIELDS)


def current_platform() -> str:
    """`<os>-<arch>`, e.g. windows-x86_64, darwin-arm64. The same engine
    version can answer differently on another CPU architecture."""
    machine = platform.machine().lower()
    return f"{platform.system().lower()}-{_MACHINES.get(machine, machine)}"


class CorpusError(RuntimeError):
    """The checkout cannot be pinned to a corpus release."""


@dataclass(frozen=True)
class CorpusIdentity:
    version: str
    revision: str

    @property
    def dirty(self) -> bool:
        return self.revision.endswith(DIRTY_SUFFIX)


def _git(root: Path, *args: str, input_: str | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=root, input=input_, capture_output=True, text=True)
    if done.returncode != 0:
        raise CorpusError(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout.strip()


def corpus_identity(root: Path, version: str = __version__, *, allow_dirty: bool = False) -> CorpusIdentity:
    """The corpus release and checkout revision, marked if the checkout is dirty."""
    dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=normal"))
    if dirty and not allow_dirty:
        raise CorpusError("the checkout has uncommitted or untracked changes; results must name exact bytes")
    tag = f"v{version}"
    try:
        _git(root, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    except CorpusError:
        raise CorpusError(f"corpus version {version} is not tagged ({tag}); tag the release first") from None
    if _git(root, "rev-parse", f"{tag}^{{commit}}:fixtures") != _git(root, "rev-parse", "HEAD:fixtures"):
        raise CorpusError(f"fixtures/ differs from {tag}; a changed corpus is a new release")
    if _git(root, "status", "--porcelain", "--untracked-files=all", "--ignored", "--", "fixtures"):
        raise CorpusError(f"working tree fixtures/ differs from {tag}")
    # Git status can hide byte differences through checkout filters or index flags.
    entries = _git(root, "ls-tree", "-rz", "HEAD", "--", "fixtures").rstrip("\0").split("\0")
    blobs_and_paths = [entry.split("\t", 1) for entry in entries]
    paths = "".join(f"{path}\n" for _, path in blobs_and_paths)
    hashes = _git(root, "hash-object", "--no-filters", "--stdin-paths", input_=paths).splitlines()
    if hashes != [blob.split()[2] for blob, _ in blobs_and_paths]:
        raise CorpusError(f"working tree fixtures/ differs from {tag}")
    revision = _git(root, "rev-parse", "HEAD")
    return CorpusIdentity(version, revision + DIRTY_SUFFIX if dirty else revision)


def _inputs(operation: str, from_: str, to: str) -> list[list[str]]:
    """Single-version operations see each version; two-version operations see
    both orders, since engines can answer differently depending on which file
    comes first (and for a table, `[to, from]` is an older schema appended to
    a newer table)."""
    return [[from_], [to]] if operation in SINGLE_VERSION_OPERATIONS else [[from_, to], [to, from_]]


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
            "exit_code": None,
            "findings": [],
            "column_profiles": None,
            "error_class": f"{kind.__module__}.{kind.__qualname__}",
            "notes": [message] if message else [],
        }
    seen = adapter.describe(result)  # an exception here is an adapter bug: let it stop the run
    unmapped = [
        f"{f.name}: {f.native_type} has no equivalent in the corpus type vocabulary"
        for f in seen.fields or []
        if f.type is None
    ]
    unmapped += [
        f"{p.name}: {p.native_type} has no equivalent in the corpus type vocabulary"
        for p in seen.column_profiles or []
        if p.inferred_type is None
    ]
    return {
        "status": "success",
        "result_schema": [f.to_record() for f in seen.fields] if seen.fields is not None else None,
        "row_count": seen.row_count,
        "exit_code": seen.exit_code,
        "findings": seen.findings,
        "column_profiles": [p.to_record() for p in seen.column_profiles] if seen.column_profiles is not None else None,
        "error_class": None,
        "notes": unmapped + seen.notes,
    }


def check_record(record: dict[str, Any], validator: Validator) -> list[str]:
    """Schema conformance plus rules JSON Schema cannot express: inputs are
    the transition's own versions, in one of the orders the runner uses, and
    the record is strict JSON."""
    problems = [
        f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(record)
    ]
    if not problems:
        try:
            json.dumps(record, allow_nan=False)
        except (TypeError, ValueError) as e:
            problems.append(f"record is not strict JSON: {e}")
        expected = _inputs(record["operation"], record["from"], record["to"])
        if record["inputs"] not in expected:
            problems.append(f"inputs {record['inputs']} are not one of {expected}")
    return problems


def result_validator(root: Path) -> Validator:
    schema = load_json(root / RESULT_SCHEMA)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def observe(
    root: Path, adapter: Adapter, identity: CorpusIdentity, only: list[str] | None = None
) -> list[dict[str, Any]]:
    """Every declared (operation, mode) of `adapter` on every transition, in
    corpus order. Each record is validated; an invalid one is an adapter bug."""
    unknown = set(adapter.modes) - set(OPERATIONS)
    if unknown:
        raise ValueError(f"{adapter.name}: unknown operations {sorted(unknown)}")
    validator = result_validator(root)
    engine_version = adapter.version
    run_platform = current_platform()
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
                                "platform": run_platform,
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
    text = "".join(json.dumps(r, ensure_ascii=True, allow_nan=False) + "\n" for r in records)
    path.write_bytes(text.encode("ascii"))
    return path


def read_results(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_bytes().decode("ascii").splitlines()]


def read_recorded_results(path: Path) -> list[dict[str, Any]]:
    records = read_results(path)
    if any(record["corpus_revision"].endswith(DIRTY_SUFFIX) for record in records):
        raise CorpusError(f"{path}: records from a dirty checkout cannot be published")
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m adapters.runner", description=__doc__.split("\n\n")[0])
    parser.add_argument("adapters", nargs="*", help=f"default: all ({', '.join(sorted(REGISTRY))})")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="corpus checkout")
    parser.add_argument("--output", type=Path, help="JSON Lines output path for a single adapter")
    parser.add_argument("--allow-dirty", action="store_true", help="allow local changes outside fixtures/")
    parser.add_argument("--scenarios", nargs="+", metavar="ID", help="run only the named scenarios")
    args = parser.parse_args(argv)

    names = args.adapters or sorted(REGISTRY)
    if args.output is not None and len(names) != 1:
        parser.error("--output requires one adapter")
    root = args.root.resolve()
    output = args.output.resolve() if args.output is not None else None
    if output is not None and (
        output == (root / "SHA256SUMS").resolve()
        or any(output.is_relative_to((root / name).resolve()) for name in ("fixtures", "data"))
    ):
        parser.error("--output cannot name a corpus file")
    try:
        identity = corpus_identity(root, allow_dirty=args.allow_dirty)
    except CorpusError as e:
        print(f"refusing to run: {e}", file=sys.stderr)
        return 2
    recorded_output = output is None or output.is_relative_to((root / RESULTS_DIR).resolve())
    if identity.dirty and recorded_output:
        parser.error("dirty runs require --output outside results/")
    if args.scenarios is not None:
        unknown = set(args.scenarios) - {directory.name for directory in scenario_dirs(root / "fixtures")}
        if unknown:
            parser.error(f"unknown scenarios: {', '.join(sorted(unknown))}")
        if recorded_output:
            parser.error("filtered runs require --output outside results/")
    print(f"corpus {identity.version} at {identity.revision}")
    for name in names:
        adapter = load_adapter(name)
        records = observe(root, adapter, identity, args.scenarios)
        path = write_results(output or results_path(root, adapter.name, identity.version), records)
        errors = sum(r["status"] == "error" for r in records)
        display_path = path.relative_to(root) if path.is_relative_to(root) else path
        print(f"{name} {adapter.version}: {len(records)} records ({errors} errors) -> {display_path.as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
