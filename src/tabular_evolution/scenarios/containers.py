"""Lists, maps, and datasets without a row key."""

from __future__ import annotations

import pyarrow as pa

from ..models import Invariants, Scenario, Transition, Version, add_field, change_type, col, same_rows
from ._build import ID, field, ids, table


def list_element_widen() -> Scenario:
    scores = [[1, -2147483648, 2147483647], [], None, [None, 0], [7]]
    return Scenario(
        scenario_id="list_element_widen",
        title="List elements widen from int32 to int64",
        category="structural",
        description=(
            "The element type of a list column changes from int32 to int64. Rows include a null list, an "
            "empty list and a list holding a null element, which are three different values. Every element "
            "value, including both int32 extremes, is unchanged."
        ),
        notes=[
            "The element field name is not part of the corpus type: this writer stores a list written as "
            "list<item: ...> under the Parquet name 'element', and reads it back that way.",
        ],
        tags=["list", "nested", "change-type", "integer", "widening", "nested-nulls"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("scores", pa.list_(pa.int32()))], [ids(5), scores]),
                [col("id", "int64", False), col("scores", "list<int32>")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("scores", pa.list_(pa.int64()))], [ids(5), scores]),
                [col("id", "int64", False), col("scores", "list<int64>")],
            ),
        ],
        transitions=[
            Transition(
                "v0", "v1", [change_type("scores[]", "int32", "int64")], same_rows(parquet_schema_preserved=False)
            )
        ],
    )


LINE_ITEM_2 = pa.struct([pa.field("sku", pa.string()), pa.field("qty", pa.int32())])
LINE_ITEM_3 = pa.struct([pa.field("sku", pa.string()), pa.field("qty", pa.int32()), pa.field("discount", pa.float64())])


def add_field_in_list_of_struct() -> Scenario:
    return Scenario(
        scenario_id="add_field_in_list_of_struct",
        title="Add a field to the structs inside a list",
        category="structural",
        description=(
            "Each element of a list<struct> column gains a nullable float64 field discount. Rows include a "
            "null list, an empty list and a list with a null element. Existing element values are unchanged."
        ),
        tags=["list", "struct", "nested", "add-field", "nested-nulls"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(
                    [ID, field("line_items", pa.list_(LINE_ITEM_2))],
                    [
                        ids(5),
                        [
                            [{"sku": "A-1", "qty": 1}, {"sku": "B-2", "qty": 2}],
                            [],
                            None,
                            [None, {"sku": "C-3", "qty": 0}],
                            [{"sku": "", "qty": -1}],
                        ],
                    ],
                ),
                [col("id", "int64", False), col("line_items", "list<struct<sku: string, qty: int32>>")],
            ),
            Version(
                "v1",
                lambda: table(
                    [ID, field("line_items", pa.list_(LINE_ITEM_3))],
                    [
                        ids(5),
                        [
                            [{"sku": "A-1", "qty": 1, "discount": 0.1}, {"sku": "B-2", "qty": 2, "discount": None}],
                            [],
                            None,
                            [None, {"sku": "C-3", "qty": 0, "discount": 0.0}],
                            [{"sku": "", "qty": -1, "discount": None}],
                        ],
                    ],
                ),
                [
                    col("id", "int64", False),
                    col("line_items", "list<struct<sku: string, qty: int32, discount: double>>"),
                ],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [add_field("line_items[].discount", "double", True)],
                same_rows(parquet_schema_preserved=False),
            )
        ],
    )


def map_value_widen() -> Scenario:
    attributes = [[("color", 1), ("size", -1)], [], None, [("", 0)], [("k", 2147483647), ("k2", None)]]
    return Scenario(
        scenario_id="map_value_widen",
        title="Map values widen from int32 to int64",
        category="structural",
        description=(
            "The value type of a map<string, int32> column becomes int64. Rows include a null map, an empty "
            "map, an empty-string key and a null value. Keys, values and entry order are unchanged."
        ),
        notes=[
            "Map paths treat a map as a list of key/value entries, which is how Arrow and Parquet store it: "
            "the changed path is attributes[].value.",
        ],
        tags=["map", "nested", "change-type", "integer", "widening"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("attributes", pa.map_(pa.string(), pa.int32()))], [ids(5), attributes]),
                [col("id", "int64", False), col("attributes", "map<string, int32>")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("attributes", pa.map_(pa.string(), pa.int64()))], [ids(5), attributes]),
                [col("id", "int64", False), col("attributes", "map<string, int64>")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type("attributes[].value", "int32", "int64")],
                same_rows(parquet_schema_preserved=False),
            )
        ],
    )


def rows_appended_without_key() -> Scenario:
    fields = [field("level", pa.string()), field("message", pa.string())]
    decl = [col("level", "string"), col("message", "string")]
    first = [["INFO", "INFO", "WARN", "INFO"], ["started", "started", "disk 91% full", ""]]
    return Scenario(
        scenario_id="rows_appended_without_key",
        title="Rows appended to a dataset without a key",
        category="edge",
        description=(
            "An append-only log with no identifying column gains two rows. The first four rows, which include "
            "two identical rows, are unchanged and in the same order. The schema is unchanged."
        ),
        notes=[
            "row_identity is empty: rows are identified by position, so row i of v0 is row i of v1. The "
            "duplicate rows are why no column set can serve as a key.",
        ],
        tags=["keyless", "append", "duplicate-rows", "no-schema-change"],
        row_identity=[],
        versions=[
            Version("v0", lambda: table(fields, first), decl),
            Version(
                "v1",
                lambda: table(fields, [first[0] + ["ERROR", "INFO"], first[1] + ["disk full", "started"]]),
                decl,
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [],
                Invariants(
                    row_count_preserved=False,
                    row_identity_preserved=False,
                    rows_retained=True,
                    paths_retained=True,
                    common_values_preserved=True,
                    parquet_schema_preserved=True,
                ),
            )
        ],
    )


SCENARIOS = [list_element_widen, add_field_in_list_of_struct, map_value_widen, rows_appended_without_key]
