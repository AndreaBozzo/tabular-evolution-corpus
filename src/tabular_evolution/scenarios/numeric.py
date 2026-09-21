"""Numeric type evolution."""

from __future__ import annotations

from decimal import Decimal

import pyarrow as pa

from ..models import Scenario, Transition, Version, change_type, col, same_rows
from ._build import ID, field, ids, table

D = Decimal


def _numeric(
    scenario_id: str,
    title: str,
    description: str,
    tags: list[str],
    name: str,
    old: tuple[pa.DataType, str, list],
    new: tuple[pa.DataType, str, list],
    value_changes: tuple[tuple[str, str], ...] = (),
    notes: list[str] | None = None,
) -> Scenario:
    """One non-key column changes type; nothing else changes."""
    old_type, old_name, old_values = old
    new_type, new_name, new_values = new
    n = len(old_values)
    return Scenario(
        scenario_id=scenario_id,
        title=title,
        category="numeric",
        description=description,
        notes=notes or [],
        tags=tags,
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field(name, old_type)], [ids(n), old_values]),
                [col("id", "int64", False), col(name, old_name)],
            ),
            Version(
                "v1",
                lambda: table([ID, field(name, new_type)], [ids(n), new_values]),
                [col("id", "int64", False), col(name, new_name)],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type(name, old_name, new_name)],
                same_rows(parquet_schema_preserved=False, value_changes=value_changes),
            )
        ],
    )


def widen_int32_to_int64() -> Scenario:
    values = [-2147483648, -1, 0, 1, 2147483647, None]
    return _numeric(
        "widen_int32_to_int64",
        "Widen int32 to int64",
        "An int32 column becomes int64. Values, including both int32 extremes, zero and a null, are unchanged.",
        ["change-type", "integer", "widening", "numeric-extremes"],
        "quantity",
        (pa.int32(), "int32", values),
        (pa.int64(), "int64", values),
    )


def int64_to_float64() -> Scenario:
    return _numeric(
        "int64_to_float64",
        "int64 to float64 with values beyond 2^53",
        (
            "An int64 column becomes float64. Each v1 value is the nearest float64 to the v0 value. "
            "2^53 and -2^63 are exactly representable and unchanged; 2^53 + 1 and the int64 maximum are not, "
            "so those two values change."
        ),
        ["change-type", "integer", "floating-point", "precision-loss", "numeric-extremes"],
        "measurement",
        (pa.int64(), "int64", [0, -1, 9007199254740992, 9007199254740993, 9223372036854775807, -9223372036854775808, None]),
        (
            pa.float64(),
            "double",
            [0.0, -1.0, 9007199254740992.0, 9007199254740992.0, 9223372036854775808.0, -9223372036854775808.0, None],
        ),
        value_changes=(("measurement", "nearest_float64"),),
        notes=[
            "The v1 value for the int64 maximum is 2^63, which is outside the int64 range: "
            "casting it back to int64 overflows.",
        ],
    )


def float64_to_int64_integral() -> Scenario:
    return _numeric(
        "float64_to_int64_integral",
        "float64 to int64, integral values only",
        (
            "A float64 column becomes int64. Every non-null v0 value is integral and within range, so every "
            "value is unchanged. v0 contains -0.0, which equals 0 as a number."
        ),
        ["change-type", "floating-point", "integer", "narrowing"],
        "amount",
        (pa.float64(), "double", [0.0, -0.0, 1.0, -3.0, 9007199254740992.0, -1e15, None]),
        (pa.int64(), "int64", [0, 0, 1, -3, 9007199254740992, -1000000000000000, None]),
    )


def float64_to_int64_fractional() -> Scenario:
    return _numeric(
        "float64_to_int64_fractional",
        "float64 to int64 with fractional values",
        (
            "A float64 column becomes int64. v0 holds fractional values; each v1 value is the v0 value "
            "truncated toward zero. The values distinguish truncation from rounding: 1.5 -> 1, 2.999 -> 2, "
            "-1.5 -> -1."
        ),
        ["change-type", "floating-point", "integer", "narrowing", "precision-loss"],
        "amount",
        (pa.float64(), "double", [1.5, -1.5, 2.999, -0.5, 0.0, 7.0, None]),
        (pa.int64(), "int64", [1, -1, 2, 0, 0, 7, None]),
        value_changes=(("amount", "truncated_toward_zero"),),
        notes=[
            "Truncation is what this producer did. It is not a statement of how a cast ought to behave; "
            "rounding, rejection and nulling are all observed in practice.",
            "NaN and infinities are deliberately absent: they have no int64 value, and including them would "
            "mix a second change into this scenario.",
        ],
    )


def decimal_precision_increase() -> Scenario:
    values = [D("0.00"), D("-0.01"), D("1.20"), D("9999999.99"), D("-9999999.99"), None]
    return _numeric(
        "decimal_precision_increase",
        "Increase decimal precision",
        (
            "A decimal128(9, 2) column becomes decimal128(18, 2). Scale is unchanged. Values include both "
            "extremes of the old precision and are unchanged."
        ),
        ["change-type", "decimal", "widening", "numeric-extremes"],
        "price",
        (pa.decimal128(9, 2), "decimal128(9, 2)", values),
        (pa.decimal128(18, 2), "decimal128(18, 2)", values),
        notes=["The Parquet fixed_len_byte_array width changes with the precision (4 to 8 bytes)."],
    )


def decimal_scale_increase() -> Scenario:
    return _numeric(
        "decimal_scale_increase",
        "Increase decimal scale at fixed precision",
        (
            "A decimal128(9, 2) column becomes decimal128(9, 4). Precision is unchanged, so the largest "
            "representable magnitude shrinks from 9999999.99 to 99999.9999. Every v0 value still fits and "
            "every value is numerically unchanged (1.20 and 1.2000 are the same number)."
        ),
        ["change-type", "decimal", "scale"],
        "price",
        (pa.decimal128(9, 2), "decimal128(9, 2)", [D("0.00"), D("-0.01"), D("1.20"), D("12345.67"), D("-99999.99"), None]),
        (
            pa.decimal128(9, 4),
            "decimal128(9, 4)",
            [D("0.0000"), D("-0.0100"), D("1.2000"), D("12345.6700"), D("-99999.9900"), None],
        ),
        notes=[
            "The type change narrows the value range while widening the scale; a v0 value such as "
            "100000.00 would not fit. None of the fixture values are affected.",
        ],
    )


SCENARIOS = [
    widen_int32_to_int64,
    int64_to_float64,
    float64_to_int64_integral,
    float64_to_int64_fractional,
    decimal_precision_increase,
    decimal_scale_increase,
]
