"""Authoring model for scenarios.

A scenario definition carries two independent descriptions of each version:
the Arrow table that becomes the file, and the hand-written schema, mutations
and invariants that become the manifest. Neither is derived from the other;
the validator checks that they agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

import pyarrow as pa

SCHEMA_VERSION = "1.0"

CATEGORIES = (
    "additive",
    "subtractive",
    "numeric",
    "nullability",
    "representation",
    "structural",
    "temporal",
    "edge",
)

RELATIONS = (
    "nulls_filled",
    "nulled",
    "truncated_toward_zero",
    "nearest_float64",
    "utf8_encoded",
    "interpreted_as_utc",
)


def col(name: str, type: str, nullable: bool = True, field_id: int | None = None) -> dict[str, Any]:
    """A declared top-level field, in manifest form."""
    entry: dict[str, Any] = {"name": name, "type": type, "nullable": nullable}
    if field_id is not None:
        entry["field_id"] = field_id
    return entry


# Mutation constructors. Each returns the exact manifest form.


def add_field(path: str, type: str, nullable: bool) -> dict[str, Any]:
    return {"kind": "add_field", "path": path, "type": type, "nullable": nullable}


def remove_field(path: str, type: str, nullable: bool) -> dict[str, Any]:
    return {"kind": "remove_field", "path": path, "type": type, "nullable": nullable}


def change_type(path: str, old_type: str, new_type: str) -> dict[str, Any]:
    return {"kind": "change_type", "path": path, "old_type": old_type, "new_type": new_type}


def change_nullability(path: str, old_nullable: bool, new_nullable: bool) -> dict[str, Any]:
    return {"kind": "change_nullability", "path": path, "old_nullable": old_nullable, "new_nullable": new_nullable}


def rename_field(old_path: str, path: str, field_id: int) -> dict[str, Any]:
    return {"kind": "rename_field", "path": path, "old_path": old_path, "field_id": field_id}


def reorder_fields(path: str, old_order: list[str], new_order: list[str]) -> dict[str, Any]:
    return {"kind": "reorder_fields", "path": path, "old_order": old_order, "new_order": new_order}


@dataclass(frozen=True)
class Invariants:
    row_count_preserved: bool
    row_identity_preserved: bool
    rows_retained: bool
    paths_retained: bool
    common_values_preserved: bool
    parquet_schema_preserved: bool
    value_changes: tuple[tuple[str, str], ...] = ()  # (path, relation)

    def to_manifest(self) -> dict[str, Any]:
        return {
            "row_count_preserved": self.row_count_preserved,
            "row_identity_preserved": self.row_identity_preserved,
            "rows_retained": self.rows_retained,
            "paths_retained": self.paths_retained,
            "common_values_preserved": self.common_values_preserved,
            "value_changes": [{"path": p, "relation": r} for p, r in self.value_changes],
            "parquet_schema_preserved": self.parquet_schema_preserved,
        }


def same_rows(
    *,
    paths_retained: bool = True,
    parquet_schema_preserved: bool,
    value_changes: tuple[tuple[str, str], ...] = (),
) -> Invariants:
    """Invariants for the common case: the same rows, in the same order."""
    return Invariants(
        row_count_preserved=True,
        row_identity_preserved=True,
        rows_retained=True,
        paths_retained=paths_retained,
        common_values_preserved=not value_changes,
        parquet_schema_preserved=parquet_schema_preserved,
        value_changes=value_changes,
    )


@dataclass(frozen=True)
class Version:
    id: str
    build: Callable[[], pa.Table]
    schema: list[dict[str, Any]]
    use_dictionary: bool = True
    dictionaries: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def file(self) -> str:
        return f"{self.id}.parquet"


@dataclass(frozen=True)
class Transition:
    from_: str
    to: str
    mutations: list[dict[str, Any]]
    invariants: Invariants


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    category: str
    description: str
    tags: list[str]
    row_identity: list[str]
    versions: list[Version]
    transitions: list[Transition]
    notes: list[str] = field(default_factory=list)

    def to_manifest(self, observed: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """The manifest. `observed[version_id]` supplies the two values that are
        measured rather than declared: `row_count` and `fingerprint`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "scenario_id": self.scenario_id,
            "title": self.title,
            "category": self.category,
            "description": self.description,
            "notes": self.notes,
            "tags": self.tags,
            "row_identity": self.row_identity,
            "versions": [
                {
                    "id": v.id,
                    "file": v.file,
                    "row_count": observed[v.id]["row_count"],
                    "fingerprint": observed[v.id]["fingerprint"],
                    "schema": v.schema,
                    "writer": {"use_dictionary": v.use_dictionary},
                    "dictionaries": [{"path": p, "values": vals} for p, vals in v.dictionaries.items()],
                }
                for v in self.versions
            ],
            "transitions": [
                {
                    "from": t.from_,
                    "to": t.to,
                    "mutations": t.mutations,
                    "invariants": t.invariants.to_manifest(),
                }
                for t in self.transitions
            ],
        }
