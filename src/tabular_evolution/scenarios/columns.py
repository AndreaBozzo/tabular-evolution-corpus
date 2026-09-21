"""Columns appear, or disappear."""

from __future__ import annotations

import pyarrow as pa

from ..models import Scenario, Transition, Version, add_field, col, remove_field, same_rows
from ._build import ID, field, ids, table

NAME = field("name", pa.string())
NAMES = ["Ada", "Grace", "Linus", "Barbara", "Edsger", "Frances"]


def add_nullable_string_column() -> Scenario:
    segment = field("customer_segment", pa.string())
    return Scenario(
        scenario_id="add_nullable_string_column",
        title="Add nullable string column",
        category="additive",
        description=(
            "A nullable string column is appended. Every existing column, value and row is unchanged. "
            "The new column holds both nulls and empty strings, which are different values."
        ),
        tags=["add-field", "string", "nullable"],
        row_identity=["id"],
        versions=[
            Version("v0", lambda: table([ID, NAME], [ids(6), NAMES]), [col("id", "int64", False), col("name", "string")]),
            Version(
                "v1",
                lambda: table(
                    [ID, NAME, segment],
                    [ids(6), NAMES, ["retail", None, "", "wholesale", "retail", None]],
                ),
                [col("id", "int64", False), col("name", "string"), col("customer_segment", "string")],
            ),
        ],
        transitions=[
            Transition(
                "v0", "v1", [add_field("customer_segment", "string", True)], same_rows(parquet_schema_preserved=False)
            )
        ],
    )


def add_nullable_int64_column() -> Scenario:
    points = field("loyalty_points", pa.int64())
    return Scenario(
        scenario_id="add_nullable_int64_column",
        title="Add nullable int64 column",
        category="additive",
        description=(
            "A nullable int64 column is appended. Its values include zero, a negative value, nulls and "
            "both int64 extremes. Existing columns and rows are unchanged."
        ),
        tags=["add-field", "integer", "nullable", "numeric-extremes"],
        row_identity=["id"],
        versions=[
            Version("v0", lambda: table([ID, NAME], [ids(6), NAMES]), [col("id", "int64", False), col("name", "string")]),
            Version(
                "v1",
                lambda: table(
                    [ID, NAME, points],
                    [ids(6), NAMES, [0, -1, None, 9223372036854775807, -9223372036854775808, 42]],
                ),
                [col("id", "int64", False), col("name", "string"), col("loyalty_points", "int64")],
            ),
        ],
        transitions=[
            Transition("v0", "v1", [add_field("loyalty_points", "int64", True)], same_rows(parquet_schema_preserved=False))
        ],
    )


def add_all_null_column() -> Scenario:
    referrer = field("referrer", pa.string())
    schema_v1 = [col("id", "int64", False), col("name", "string"), col("referrer", "string")]
    return Scenario(
        scenario_id="add_all_null_column",
        title="Add a typed all-null column, then backfill it",
        category="additive",
        description=(
            "v1 adds a string column in which every value is null. v2 backfills it without any schema "
            "change. The column is typed string in both v1 and v2; only its values differ."
        ),
        notes=[
            "The v1 column is declared string, not the Arrow null type. A column that is inferred as the "
            "null type from all-null data is a different case: see null_type_to_string.",
        ],
        tags=["add-field", "all-null", "backfill", "multi-version"],
        row_identity=["id"],
        versions=[
            Version("v0", lambda: table([ID, NAME], [ids(6), NAMES]), [col("id", "int64", False), col("name", "string")]),
            Version("v1", lambda: table([ID, NAME, referrer], [ids(6), NAMES, [None] * 6]), schema_v1),
            Version(
                "v2",
                lambda: table(
                    [ID, NAME, referrer],
                    [ids(6), NAMES, ["newsletter", "search", "", "partner", "search", "direct"]],
                ),
                schema_v1,
            ),
        ],
        transitions=[
            Transition(
                "v0", "v1", [add_field("referrer", "string", True)], same_rows(parquet_schema_preserved=False)
            ),
            Transition(
                "v1",
                "v2",
                [],
                same_rows(parquet_schema_preserved=True, value_changes=(("referrer", "nulls_filled"),)),
            ),
        ],
    )


def remove_nullable_column() -> Scenario:
    middle = field("middle_name", pa.string())
    return Scenario(
        scenario_id="remove_nullable_column",
        title="Remove nullable column",
        category="subtractive",
        description=(
            "A nullable string column that held nulls, an empty string and values is removed. "
            "The remaining columns and all rows are unchanged."
        ),
        tags=["remove-field", "string", "nullable"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, NAME, middle], [ids(6), NAMES, [None, "Marie", None, "", "Wybe", "Elizabeth"]]),
                [col("id", "int64", False), col("name", "string"), col("middle_name", "string")],
            ),
            Version("v1", lambda: table([ID, NAME], [ids(6), NAMES]), [col("id", "int64", False), col("name", "string")]),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [remove_field("middle_name", "string", True)],
                same_rows(paths_retained=False, parquet_schema_preserved=False),
            )
        ],
    )


def remove_required_column() -> Scenario:
    country = field("country_code", pa.string(), nullable=False)
    return Scenario(
        scenario_id="remove_required_column",
        title="Remove non-nullable populated column",
        category="subtractive",
        description=(
            "A non-nullable string column with a value in every row is removed. "
            "The remaining columns and all rows are unchanged."
        ),
        tags=["remove-field", "string", "required"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, NAME, country], [ids(6), NAMES, ["GB", "US", "FI", "US", "NL", "US"]]),
                [col("id", "int64", False), col("name", "string"), col("country_code", "string", False)],
            ),
            Version("v1", lambda: table([ID, NAME], [ids(6), NAMES]), [col("id", "int64", False), col("name", "string")]),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [remove_field("country_code", "string", False)],
                same_rows(paths_retained=False, parquet_schema_preserved=False),
            )
        ],
    )


SCENARIOS = [
    add_nullable_string_column,
    add_nullable_int64_column,
    add_all_null_column,
    remove_nullable_column,
    remove_required_column,
]
