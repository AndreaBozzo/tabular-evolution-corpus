"""Check every declared fact of every scenario against its physical files.

Nothing here trusts a manifest. Each check re-derives the fact from the
Parquet files and compares; each function returns a list of problems, empty
when the scenario is consistent.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
from jsonschema import Draft202012Validator

from .diff import diff_schemas, mutation_key, renames
from .generate import MANIFEST
from .inspect import column_chunks, describe_schema, dictionaries, parquet_schema_text, read_table
from .values import Instant, canonical_json, column_values, encode, fingerprint, flatten, same

SCHEMA_PATH = Path("schema") / "scenario.schema.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_bytes().decode("utf-8"))


def manifest_validator(root: Path) -> Draft202012Validator:
    schema = load_json(root / SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@dataclass
class LoadedScenario:
    directory: Path
    manifest: dict[str, Any]
    _tables: dict[str, pa.Table] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: Path) -> "LoadedScenario":
        return cls(directory, load_json(directory / MANIFEST))

    @property
    def scenario_id(self) -> str:
        return self.manifest["scenario_id"]

    @cached_property
    def versions(self) -> dict[str, dict[str, Any]]:
        return {v["id"]: v for v in self.manifest["versions"]}

    def path(self, version_id: str) -> Path:
        return self.directory / self.versions[version_id]["file"]

    def table(self, version_id: str) -> pa.Table:
        if version_id not in self._tables:
            self._tables[version_id] = read_table(self.path(version_id))
        return self._tables[version_id]


# ---------------------------------------------------------------- manifest


def check_manifest(scenario: LoadedScenario, validator: Draft202012Validator) -> list[str]:
    """JSON Schema conformance plus the cross-field rules a schema cannot express."""
    m = scenario.manifest
    problems = [f"schema: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}" for e in validator.iter_errors(m)]
    if problems:
        return problems

    if m["scenario_id"] != scenario.directory.name:
        problems.append(f"scenario_id {m['scenario_id']!r} does not match directory {scenario.directory.name!r}")

    ids = [v["id"] for v in m["versions"]]
    if ids != [f"v{i}" for i in range(len(ids))]:
        problems.append(f"version ids must be v0..v{len(ids) - 1} in order, got {ids}")
    files = [v["file"] for v in m["versions"]]
    if len(set(files)) != len(files):
        problems.append(f"version files are not unique: {files}")

    pairs = [(t["from"], t["to"]) for t in m["transitions"]]
    if pairs != list(zip(ids, ids[1:])):
        problems.append(f"transitions must cover consecutive versions in order, got {pairs}")

    for v in m["versions"]:
        names = [f["name"] for f in v["schema"]]
        if len(set(names)) != len(names):
            problems.append(f"{v['id']}: duplicate field names {names}")
        missing = [k for k in m["row_identity"] if k not in names]
        if missing:
            problems.append(f"{v['id']}: row identity columns {missing} are not in the schema")

    for t in m["transitions"]:
        inv = t["invariants"]
        paths = [c["path"] for c in inv["value_changes"]]
        if len(set(paths)) != len(paths):
            problems.append(f"{t['from']}->{t['to']}: duplicate value_changes paths {paths}")
        if inv["common_values_preserved"] == bool(paths):
            problems.append(
                f"{t['from']}->{t['to']}: value_changes must be empty exactly when common_values_preserved is true"
            )
    return problems


# ---------------------------------------------------------------- versions


def check_versions(scenario: LoadedScenario) -> list[str]:
    problems: list[str] = []
    identity = scenario.manifest["row_identity"]
    for vid, v in scenario.versions.items():
        path = scenario.path(vid)
        if not path.is_file():
            problems.append(f"{vid}: file {v['file']} does not exist")
            continue
        table = scenario.table(vid)
        actual_schema = describe_schema(table.schema)

        if actual_schema != v["schema"]:
            problems.append(
                f"{vid}: declared schema {canonical_json(v['schema'])} != actual {canonical_json(actual_schema)}"
            )
        if table.num_rows != v["row_count"]:
            problems.append(f"{vid}: declared row_count {v['row_count']} != actual {table.num_rows}")
        actual_fp = fingerprint(table, actual_schema)
        if actual_fp != v["fingerprint"]:
            problems.append(f"{vid}: declared fingerprint {v['fingerprint']} != actual {actual_fp}")

        for f in table.schema:
            if not f.nullable and table.column(f.name).null_count:
                problems.append(f"{vid}: non-nullable column {f.name} contains nulls")

        if identity and all(k in table.column_names for k in identity):
            keys = row_keys(table, identity)
            if any(k is None for k in keys):
                problems.append(f"{vid}: row identity contains nulls")
            if len(set(keys)) != len(keys):
                problems.append(f"{vid}: row identity is not unique")

        chunks = column_chunks(path)
        with_dictionary = [c["path"] for c in chunks if c["has_dictionary_page"]]
        if not v["writer"]["use_dictionary"] and with_dictionary:
            problems.append(f"{vid}: use_dictionary is false but {with_dictionary} have dictionary pages")
        if v["writer"]["use_dictionary"] and table.num_rows and not with_dictionary:
            problems.append(f"{vid}: use_dictionary is true but no column chunk has a dictionary page")

        if dictionaries(table) != v["dictionaries"]:
            problems.append(f"{vid}: declared dictionaries {v['dictionaries']} != actual {dictionaries(table)}")
    return problems


def row_keys(table: pa.Table, identity: list[str]) -> list[str | None]:
    """One hashable key per row; None when any identity value is null.

    An empty identity identifies rows by position: row i of one version is
    row i of the next."""
    if not identity:
        return [canonical_json(["position", i]) for i in range(table.num_rows)]
    columns = [column_values(table.column(k)) for k in identity]
    return [None if any(v is None for v in row) else canonical_json([encode(v) for v in row]) for row in zip(*columns)]


# ---------------------------------------------------------------- transitions


def _check_relation(relation: str, pairs: list[tuple[Any, Any]]) -> bool:
    both_null = lambda a, b: a is None and b is None  # noqa: E731
    rules: dict[str, Callable[[], bool]] = {
        "nulls_filled": lambda: all(a is None or same(a, b) for a, b in pairs)
        and any(a is None and b is not None for a, b in pairs),
        "nulled": lambda: all(b is None for _, b in pairs) and any(a is not None for a, _ in pairs),
        "truncated_toward_zero": lambda: all(
            both_null(a, b)
            or (
                isinstance(a, float)
                and math.isfinite(a)
                and isinstance(b, int)
                and not isinstance(b, bool)
                and b == math.trunc(a)
            )
            for a, b in pairs
        ),
        "nearest_float64": lambda: all(
            both_null(a, b) or (isinstance(a, int) and isinstance(b, float) and b == float(a)) for a, b in pairs
        ),
        "utf8_encoded": lambda: all(
            both_null(a, b) or (isinstance(a, str) and isinstance(b, bytes) and b == a.encode("utf-8"))
            for a, b in pairs
        ),
        "interpreted_as_utc": lambda: all(
            both_null(a, b)
            or (
                isinstance(a, Instant)
                and isinstance(b, Instant)
                and a.tz is None
                and b.tz == "UTC"
                and a.epoch_ns == b.epoch_ns
            )
            for a, b in pairs
        ),
    }
    return rules[relation]()


def observe_transition(scenario: LoadedScenario, from_id: str, to_id: str) -> dict[str, Any]:
    """Everything about a transition, computed from the two files alone."""
    identity = scenario.manifest["row_identity"]
    a, b = scenario.table(from_id), scenario.table(to_id)
    mutations = diff_schemas(a.schema, b.schema)

    keys_a, keys_b = row_keys(a, identity), row_keys(b, identity)
    row_of_b = {k: i for i, k in enumerate(keys_b)}
    aligned = [(i, row_of_b[k]) for i, k in enumerate(keys_a) if k in row_of_b]

    flat_a, flat_b = flatten(a), flatten(b)
    renamed = renames(mutations)
    common: list[tuple[str, str]] = []
    for p in flat_a:
        # The longest renamed prefix wins: a renamed child inside a renamed
        # struct carries its full new path already.
        matches = [old for old in renamed if p == old or p.startswith((old + ".", old + "["))]
        old = max(matches, key=len, default=None)
        target = p if old is None else renamed[old] + p[len(old) :]
        if target in flat_b:
            common.append((p, target))

    changes: dict[str, list[tuple[Any, Any]]] = {}
    for p, q in common:
        pairs = [(flat_a[p][i], flat_b[q][j]) for i, j in aligned]
        if not all(same(x, y) for x, y in pairs):
            changes[q] = pairs

    return {
        "mutations": mutations,
        "row_count_preserved": a.num_rows == b.num_rows,
        "row_identity_preserved": keys_a == keys_b,
        "rows_retained": set(keys_a) <= set(keys_b),
        "paths_retained": set(flat_a) <= set(flat_b),
        "common_values_preserved": not changes,
        "value_changes": changes,
        "parquet_schema_preserved": parquet_schema_text(scenario.path(from_id))
        == parquet_schema_text(scenario.path(to_id)),
    }


BOOLEAN_INVARIANTS = (
    "row_count_preserved",
    "row_identity_preserved",
    "rows_retained",
    "paths_retained",
    "common_values_preserved",
    "parquet_schema_preserved",
)


def check_transitions(scenario: LoadedScenario) -> list[str]:
    problems: list[str] = []
    for t in scenario.manifest["transitions"]:
        label = f"{t['from']}->{t['to']}"
        if not (scenario.path(t["from"]).is_file() and scenario.path(t["to"]).is_file()):
            problems.append(f"{label}: missing version file")
            continue
        seen = observe_transition(scenario, t["from"], t["to"])

        declared = Counter(mutation_key(m) for m in t["mutations"])
        actual = Counter(mutation_key(m) for m in seen["mutations"])
        for key in sorted((declared - actual).elements()):
            problems.append(f"{label}: declared mutation not in the files: {key}")
        for key in sorted((actual - declared).elements()):
            problems.append(f"{label}: undeclared mutation: {key}")

        inv = t["invariants"]
        for name in BOOLEAN_INVARIANTS:
            if inv[name] != seen[name]:
                problems.append(f"{label}: {name} declared {inv[name]} but is {seen[name]}")

        declared_changes = {c["path"]: c["relation"] for c in inv["value_changes"]}
        if set(declared_changes) != set(seen["value_changes"]):
            problems.append(
                f"{label}: value_changes paths declared {sorted(declared_changes)} "
                f"but values differ at {sorted(seen['value_changes'])}"
            )
        for path, relation in declared_changes.items():
            if path in seen["value_changes"] and not _check_relation(relation, seen["value_changes"][path]):
                problems.append(f"{label}: {path}: relation {relation!r} does not hold row by row")
    return problems


# ---------------------------------------------------------------- corpus


def check_layout(fixtures_dir: Path) -> list[str]:
    """No orphans: every directory is a scenario, every file is referenced."""
    problems: list[str] = []
    for entry in sorted(fixtures_dir.iterdir()):
        if not entry.is_dir():
            problems.append(f"unexpected file in fixtures/: {entry.name}")
            continue
        if not (entry / MANIFEST).is_file():
            problems.append(f"{entry.name}: directory has no {MANIFEST}")
            continue
        try:
            referenced = {v["file"] for v in load_json(entry / MANIFEST)["versions"]} | {MANIFEST}
        except (KeyError, TypeError, ValueError) as error:
            problems.append(f"{entry.name}: unreadable manifest: {error}")
            continue
        for item in sorted(entry.iterdir()):
            if item.name not in referenced or not item.is_file():
                problems.append(f"{entry.name}: orphan {item.name}")
    return problems


def scenario_dirs(fixtures_dir: Path) -> list[Path]:
    return sorted(d for d in fixtures_dir.iterdir() if (d / MANIFEST).is_file())


def check_scenario(scenario: LoadedScenario, validator: Draft202012Validator) -> list[str]:
    problems = check_manifest(scenario, validator)
    if problems:
        return problems
    return check_versions(scenario) + check_transitions(scenario)


def validate_corpus(root: Path, only: list[str] | None = None) -> dict[str, list[str]]:
    """Problems keyed by scenario id; the key "<layout>" holds corpus-level ones."""
    fixtures = root / "fixtures"
    validator = manifest_validator(root)
    results: dict[str, list[str]] = {"<layout>": check_layout(fixtures)}
    for directory in scenario_dirs(fixtures):
        if only and directory.name not in only:
            continue
        results[directory.name] = check_scenario(LoadedScenario.load(directory), validator)
    return results
