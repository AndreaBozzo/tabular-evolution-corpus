"""Nullability: the schema flag and the observed nulls are different facts."""

from __future__ import annotations

import pyarrow as pa

from ..models import Scenario, Transition, Version, change_nullability, change_type, col, same_rows
from ._build import ID, field, ids, table

SKUS = ["A-001", "A-002", "B-100", "B-100", "C-999", "Z-000"]


def nullable_column_backfilled() -> Scenario:
    email = field("email", pa.string())
    return Scenario(
        scenario_id="nullable_column_backfilled",
        title="Nullable column loses its nulls; schema stays nullable",
        category="nullability",
        description=(
            "Every null in a nullable string column is filled in v1. The schema is identical in both "
            "versions: the field is still nullable although v1 contains no nulls. The empty string in v0 "
            "is a value, not a null, and is unchanged."
        ),
        notes=[
            "Observing zero nulls in a batch says nothing about the declared nullability. Both files "
            "declare the column optional at the Arrow and Parquet levels.",
        ],
        tags=["nullable", "backfill", "no-schema-change", "empty-string"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(
                    [ID, email], [ids(6), ["a@example.com", None, "c@example.com", None, "", "f@example.com"]]
                ),
                [col("id", "int64", False), col("email", "string")],
            ),
            Version(
                "v1",
                lambda: table(
                    [ID, email],
                    [ids(6), ["a@example.com", "b@example.com", "c@example.com", "d@example.com", "", "f@example.com"]],
                ),
                [col("id", "int64", False), col("email", "string")],
            ),
        ],
        transitions=[
            Transition(
                "v0", "v1", [], same_rows(parquet_schema_preserved=True, value_changes=(("email", "nulls_filled"),))
            )
        ],
    )


def _nullability(scenario_id: str, title: str, description: str, old: bool, new: bool) -> Scenario:
    return Scenario(
        scenario_id=scenario_id,
        title=title,
        category="nullability",
        description=description,
        tags=["change-nullability", "string", "no-value-change"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("sku", pa.string(), nullable=old)], [ids(6), SKUS]),
                [col("id", "int64", False), col("sku", "string", old)],
            ),
            Version(
                "v1",
                lambda: table([ID, field("sku", pa.string(), nullable=new)], [ids(6), SKUS]),
                [col("id", "int64", False), col("sku", "string", new)],
            ),
        ],
        transitions=[
            Transition("v0", "v1", [change_nullability("sku", old, new)], same_rows(parquet_schema_preserved=False))
        ],
    )


def required_to_nullable() -> Scenario:
    return _nullability(
        "required_to_nullable",
        "Non-nullable column becomes nullable",
        (
            "A non-nullable string column is declared nullable in v1. The data is identical and contains no "
            "nulls in either version; only the declaration changes (Parquet repetition required -> optional)."
        ),
        False,
        True,
    )


def nullable_to_required() -> Scenario:
    return _nullability(
        "nullable_to_required",
        "Nullable column becomes non-nullable",
        (
            "A nullable string column that happens to contain no nulls is declared non-nullable in v1. "
            "The data is identical; only the declaration changes (Parquet repetition optional -> required)."
        ),
        True,
        False,
    )


def column_becomes_all_null() -> Scenario:
    discount = field("discount", pa.float64())
    return Scenario(
        scenario_id="column_becomes_all_null",
        title="Populated column becomes entirely null",
        category="nullability",
        description=(
            "Every value of a nullable float64 column is null in v1. The schema is unchanged. v0 contains "
            "NaN, -0.0 and infinity, none of which is a null."
        ),
        notes=[
            "v0 has exactly one null. Systems that count NaN as missing report two; that is a policy of "
            "those systems, not a property of the file.",
        ],
        tags=["nullable", "all-null", "no-schema-change", "nan", "infinity"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, discount], [ids(6), [0.1, float("nan"), None, -0.0, 0.25, float("inf")]]),
                [col("id", "int64", False), col("discount", "double")],
            ),
            Version(
                "v1",
                lambda: table([ID, discount], [ids(6), [None] * 6]),
                [col("id", "int64", False), col("discount", "double")],
            ),
        ],
        transitions=[
            Transition("v0", "v1", [], same_rows(parquet_schema_preserved=True, value_changes=(("discount", "nulled"),)))
        ],
    )


def null_type_to_string() -> Scenario:
    return Scenario(
        scenario_id="null_type_to_string",
        title="Null-typed column becomes a string column",
        category="nullability",
        description=(
            "In v0 a column has the Arrow null type: it carries no values and no value type, which is what "
            "type inference produces from a batch where the column is always null. In v1 the same column "
            "is a string column with values."
        ),
        notes=[
            "Parquet has no null type. The writer stores it as an optional INT32 column with the Null "
            "logical annotation, so the Parquet physical type changes as well (INT32 -> BYTE_ARRAY).",
        ],
        tags=["change-type", "null-type", "type-inference", "all-null"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("comment", pa.null())], [ids(5), pa.nulls(5)]),
                [col("id", "int64", False), col("comment", "null")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("comment", pa.string())], [ids(5), ["ok", "", None, "late", "NULL"]]),
                [col("id", "int64", False), col("comment", "string")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type("comment", "null", "string")],
                same_rows(parquet_schema_preserved=False, value_changes=(("comment", "nulls_filled"),)),
            )
        ],
    )


SCENARIOS = [
    nullable_column_backfilled,
    required_to_nullable,
    nullable_to_required,
    column_becomes_all_null,
    null_type_to_string,
]
