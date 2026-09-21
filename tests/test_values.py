"""The single definition of value equality and of the logical fingerprint."""

from __future__ import annotations

from decimal import Decimal

import pyarrow as pa
import pytest

from tabular_evolution.inspect import describe_schema
from tabular_evolution.values import Instant, column_values, fingerprint, flatten, same


@pytest.mark.parametrize(
    ("a", "b", "equal"),
    [
        (None, None, True),
        (None, 0, False),
        (None, "", False),
        (float("nan"), float("nan"), True),
        (float("nan"), None, False),
        (-0.0, 0.0, True),
        (-0.0, 0, True),
        (2**53, float(2**53), True),
        (2**53 + 1, float(2**53), False),
        (Decimal("1.20"), Decimal("1.2000"), True),
        (Decimal("0.1"), 0.1, False),  # exact comparison: 0.1 is not one tenth
        (1, True, False),
        (0, False, False),
        ("café", "café", False),  # no Unicode normalization
        ("abc", b"abc", False),
        (Instant(0, None), Instant(0, "UTC"), False),
        (Instant(1_000, "UTC"), Instant(1_000, "UTC"), True),
        ({"a": float("nan"), "b": None}, {"a": float("nan"), "b": None}, True),
        ({"a": 1}, {"a": 1, "b": None}, False),
    ],
)
def test_same(a: object, b: object, equal: bool) -> None:
    assert same(a, b) is equal
    assert same(b, a) is equal


def test_timestamps_compare_by_instant_across_units() -> None:
    ms = column_values(pa.array([1, None], pa.timestamp("ms", tz="UTC")))
    us = column_values(pa.array([1000, None], pa.timestamp("us", tz="UTC")))
    assert all(same(a, b) for a, b in zip(ms, us))


def test_dictionary_values_are_decoded() -> None:
    dictionary = pa.DictionaryArray.from_arrays(pa.array([1, 0, None], pa.int8()), pa.array(["x", "y"]))
    assert column_values(dictionary) == ["y", "x", None]


def _fp(columns: dict[str, pa.Array]) -> str:
    table = pa.table(columns)
    return fingerprint(table, describe_schema(table.schema))


def test_fingerprint_is_exact_and_order_sensitive() -> None:
    base = _fp({"v": pa.array([0.0, 1.0])})
    assert base == _fp({"v": pa.array([0.0, 1.0])})
    assert base != _fp({"v": pa.array([-0.0, 1.0])})  # exact, unlike same()
    assert base != _fp({"v": pa.array([1.0, 0.0])})  # row order
    assert base != _fp({"v": pa.array([0.0, 1.0], pa.float32())})  # schema
    assert _fp({"s": pa.array(["café"])}) != _fp({"s": pa.array(["café"])})


def test_fingerprint_ignores_dictionary_contents_but_not_type() -> None:
    a = pa.DictionaryArray.from_arrays(pa.array([0, 1], pa.int8()), pa.array(["x", "y", "unused"]))
    b = pa.DictionaryArray.from_arrays(pa.array([1, 0], pa.int8()), pa.array(["y", "x"]))
    assert _fp({"d": a}) == _fp({"d": b})
    assert _fp({"d": a}) != _fp({"d": pa.array(["x", "y"])})


def test_flatten_separates_struct_validity_from_children() -> None:
    struct = pa.struct([pa.field("a", pa.int64())])
    table = pa.table({"s": pa.array([{"a": 1}, None, {"a": None}], struct)})
    assert flatten(table) == {"s": [True, None, True], "s.a": [1, None, None]}
