"""The corpus type vocabulary is fixed here, independent of Arrow's ToString(),
and the structural diff descends into structs, lists and maps."""

from __future__ import annotations

import pyarrow as pa
import pytest

from tabular_evolution.diff import diff_schemas
from tabular_evolution.inspect import describe_schema
from tabular_evolution.types import type_string
from tabular_evolution.validate import row_keys
from tabular_evolution.values import fingerprint, same

# Golden strings. If one of these changes, every manifest and fingerprint that
# uses the type changes with it: that is a schema_version bump, not a refactor.
GOLDEN = [
    (pa.null(), "null"),
    (pa.bool_(), "bool"),
    (pa.int32(), "int32"),
    (pa.float32(), "float"),
    (pa.float64(), "double"),
    (pa.string(), "string"),
    (pa.large_string(), "large_string"),
    (pa.binary(), "binary"),
    (pa.decimal128(9, 2), "decimal128(9, 2)"),
    (pa.timestamp("us"), "timestamp[us]"),
    (pa.timestamp("us", tz="UTC"), "timestamp[us, tz=UTC]"),
    (pa.dictionary(pa.int8(), pa.string()), "dictionary<values=string, indices=int8, ordered=0>"),
    (pa.struct([pa.field("a", pa.int64(), nullable=False), pa.field("b", pa.string())]), "struct<a: int64 not null, b: string>"),
    (pa.list_(pa.int32()), "list<int32>"),
    (pa.list_(pa.field("element", pa.int32())), "list<int32>"),
    (pa.list_(pa.field("item", pa.int32(), nullable=False)), "list<int32 not null>"),
    (pa.large_list(pa.string()), "large_list<string>"),
    (pa.map_(pa.string(), pa.int32()), "map<string, int32>"),
    (pa.list_(pa.struct([pa.field("sku", pa.string())])), "list<struct<sku: string>>"),
]


@pytest.mark.parametrize(("arrow_type", "expected"), GOLDEN)
def test_type_string(arrow_type: pa.DataType, expected: str) -> None:
    assert type_string(arrow_type) == expected


def test_map_type_does_not_carry_the_column_name() -> None:
    # Arrow renders this as "map<string, int32 ('attrs')>" after a round trip
    # through a table with that column; the corpus spelling must not.
    table = pa.table({"attrs": pa.array([[("a", 1)]], pa.map_(pa.string(), pa.int32()))})
    assert describe_schema(table.schema)[0]["type"] == "map<string, int32>"


def test_unknown_types_are_rejected() -> None:
    with pytest.raises(NotImplementedError):
        type_string(pa.month_day_nano_interval())


def test_fingerprint_ignores_list_element_field_name() -> None:
    def fp(element: pa.Field) -> str:
        table = pa.table({"l": pa.array([[1, None], None], pa.list_(element))})
        return fingerprint(table, describe_schema(table.schema))

    assert fp(pa.field("item", pa.int32())) == fp(pa.field("element", pa.int32()))


def _schema(**fields: pa.DataType) -> pa.Schema:
    return pa.schema([pa.field(name, t) for name, t in fields.items()])


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        (
            _schema(l=pa.list_(pa.int32())),
            _schema(l=pa.list_(pa.field("element", pa.int64()))),
            [{"kind": "change_type", "path": "l[]", "old_type": "int32", "new_type": "int64"}],
        ),
        (
            _schema(l=pa.list_(pa.int32())),
            _schema(l=pa.list_(pa.field("item", pa.int32(), nullable=False))),
            [{"kind": "change_nullability", "path": "l[]", "old_nullable": True, "new_nullable": False}],
        ),
        (
            _schema(l=pa.list_(pa.int32())),
            _schema(l=pa.large_list(pa.int32())),
            [{"kind": "change_type", "path": "l", "old_type": "list<int32>", "new_type": "large_list<int32>"}],
        ),
        (
            _schema(l=pa.list_(pa.list_(pa.int32()))),
            _schema(l=pa.list_(pa.list_(pa.int64()))),
            [{"kind": "change_type", "path": "l[][]", "old_type": "int32", "new_type": "int64"}],
        ),
        (
            _schema(m=pa.map_(pa.string(), pa.int32())),
            _schema(m=pa.map_(pa.string(), pa.int64())),
            [{"kind": "change_type", "path": "m[].value", "old_type": "int32", "new_type": "int64"}],
        ),
        (
            _schema(l=pa.list_(pa.struct([pa.field("a", pa.int64())]))),
            _schema(l=pa.list_(pa.struct([pa.field("a", pa.int64()), pa.field("b", pa.string())]))),
            [{"kind": "add_field", "path": "l[].b", "type": "string", "nullable": True}],
        ),
        (_schema(l=pa.list_(pa.int32())), _schema(l=pa.list_(pa.int32())), []),
    ],
)
def test_diff_descends_into_containers(old: pa.Schema, new: pa.Schema, expected: list) -> None:
    assert diff_schemas(old, new) == expected


def test_list_values_distinguish_null_empty_and_null_element() -> None:
    assert not same(None, [])
    assert not same([], [None])
    assert same([1, None], [1.0, None])
    assert not same([1, 2], [2, 1])
    # Where the corpus definition differs from Python's list ==:
    assert same([float("nan")], [float("nan")])
    assert not same([True], [1])


def test_empty_identity_means_position() -> None:
    table = pa.table({"x": ["a", "a", "b"]})
    keys = row_keys(table, [])
    assert len(set(keys)) == 3
    assert keys == row_keys(pa.table({"y": [1, 2, 3]}), [])


def test_renamed_list_children_are_compared_under_the_new_name(tmp_path) -> None:
    """A value change inside a renamed list is found at the renamed path; if
    renamed descendants were not mapped, it would silently drop out of the
    comparison instead."""
    from tabular_evolution.generate import write_parquet
    from tabular_evolution.scenarios._build import field, table
    from tabular_evolution.validate import LoadedScenario, observe_transition

    def build(name: str, values: list) -> pa.Table:
        return table(
            [field("id", pa.int64(), nullable=False, field_id=1), field(name, pa.list_(pa.int32()), field_id=2)],
            [[1, 2], values],
        )

    write_parquet(build("tags", [[1, 2], None]), tmp_path / "v0.parquet")
    write_parquet(build("labels", [[1, 3], None]), tmp_path / "v1.parquet")
    manifest = {
        "row_identity": ["id"],
        "versions": [{"id": "v0", "file": "v0.parquet"}, {"id": "v1", "file": "v1.parquet"}],
    }
    seen = observe_transition(LoadedScenario(tmp_path, manifest), "v0", "v1")
    assert {"kind": "rename_field", "path": "labels", "old_path": "tags", "field_id": 2} in seen["mutations"]
    assert set(seen["value_changes"]) == {"labels[]"}


def test_rename_inside_a_renamed_struct(tmp_path) -> None:
    """Both the struct and its child are renamed (matched by field id); the
    child's values are compared under its full new path."""
    from tabular_evolution.generate import write_parquet
    from tabular_evolution.scenarios._build import field, table
    from tabular_evolution.validate import LoadedScenario, observe_transition

    def build(outer: str, inner: str, value: int) -> pa.Table:
        child = pa.field(inner, pa.int64(), metadata={b"PARQUET:field_id": b"3"})
        return table(
            [field("id", pa.int64(), nullable=False, field_id=1), field(outer, pa.struct([child]), field_id=2)],
            [[1], [{inner: value}]],
        )

    write_parquet(build("a", "x", 1), tmp_path / "v0.parquet")
    write_parquet(build("b", "y", 2), tmp_path / "v1.parquet")
    manifest = {"row_identity": ["id"], "versions": [{"id": "v0", "file": "v0.parquet"}, {"id": "v1", "file": "v1.parquet"}]}
    seen = observe_transition(LoadedScenario(tmp_path, manifest), "v0", "v1")
    assert [m["kind"] for m in seen["mutations"]] == ["rename_field", "rename_field"]
    assert set(seen["value_changes"]) == {"b.y"}
