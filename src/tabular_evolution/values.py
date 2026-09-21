"""Canonical values, value equality and logical fingerprints.

Values are read out of Arrow arrays into plain Python objects without going
through anything locale- or time-zone-database-dependent: timestamps become
`Instant(epoch_ns, tz)` from their stored integers, so no tzdata is needed and
no conversion can shift them.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pyarrow as pa
import pyarrow.types as pat

_NS_PER_UNIT = {"s": 1_000_000_000, "ms": 1_000_000, "us": 1_000, "ns": 1}


@dataclass(frozen=True)
class Instant:
    """A timestamp value: nanoseconds since the Unix epoch, plus its zone.

    `tz is None` is a naive (wall-clock) timestamp; it never equals a zoned one.
    """

    epoch_ns: int
    tz: str | None


def column_values(array: pa.Array | pa.ChunkedArray) -> list[Any]:
    """Every value of `array` as a canonical Python object, in row order."""
    if isinstance(array, pa.ChunkedArray):
        array = array.combine_chunks() if array.num_chunks else pa.array([], array.type)
    return _values(array)


def _values(array: pa.Array) -> list[Any]:
    t = array.type
    if pat.is_dictionary(t):
        return _values(array.dictionary_decode())
    if pat.is_null(t):
        return [None] * len(array)
    if pat.is_timestamp(t):
        factor = _NS_PER_UNIT[t.unit]
        raw = array.view(pa.int64()).to_pylist()
        return [None if v is None else Instant(v * factor, t.tz) for v in raw]
    if pat.is_list(t) or pat.is_large_list(t) or pat.is_map(t):
        # Offsets index into the unsliced child array; map entries are
        # {"key": ..., "value": ...} structs.
        offsets = array.offsets.to_pylist()
        inner = _values(array.values)
        valid = array.is_valid().to_pylist()
        return [inner[offsets[i] : offsets[i + 1]] if valid[i] else None for i in range(len(array))]
    if pat.is_struct(t):
        children = {t.field(i).name: _values(array.field(i)) for i in range(t.num_fields)}
        return [
            {name: vals[row] for name, vals in children.items()} if array[row].is_valid else None
            for row in range(len(array))
        ]
    if (
        pat.is_integer(t)
        or pat.is_floating(t)
        or pat.is_boolean(t)
        or pat.is_decimal(t)
        or pat.is_string(t)
        or pat.is_large_string(t)
        or pat.is_binary(t)
        or pat.is_large_binary(t)
        or pat.is_date(t)
    ):
        return array.to_pylist()
    raise NotImplementedError(f"no canonical value form for Arrow type {t}")


def same(a: Any, b: Any) -> bool:
    """Value equality as defined in DESIGN.md section 3."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    numeric = (int, float, Decimal)
    if isinstance(a, numeric) and isinstance(b, numeric):
        if _is_nan(a) or _is_nan(b):
            return _is_nan(a) and _is_nan(b)
        return a == b  # Python compares int/float/Decimal by exact value
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if type(a) is not type(b):
        return False
    return a == b


def _is_nan(v: Any) -> bool:
    return (isinstance(v, float) and math.isnan(v)) or (isinstance(v, Decimal) and v.is_nan())


def encode(v: Any) -> Any:
    """A tagged, JSON-serializable and exact encoding of a canonical value."""
    if v is None:
        return None
    if isinstance(v, bool):
        return ["bool", v]
    if isinstance(v, int):
        return ["int", str(v)]
    if isinstance(v, float):
        return ["float", float.hex(v)]
    if isinstance(v, Decimal):
        return ["decimal", str(v)]
    if isinstance(v, str):
        return ["str", v]
    if isinstance(v, bytes):
        return ["bytes", v.hex()]
    if isinstance(v, Instant):
        return ["instant", str(v.epoch_ns), v.tz]
    if isinstance(v, dict):
        return ["struct", [[k, encode(x)] for k, x in v.items()]]
    if isinstance(v, list):
        return ["list", [encode(x) for x in v]]
    # date and date-like objects
    if hasattr(v, "isoformat"):
        return [type(v).__name__, v.isoformat()]
    raise TypeError(f"cannot encode {type(v).__name__}")


def canonical_json(obj: Any) -> str:
    """The one JSON rendering used for hashing and for generated files."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def fingerprint(table: pa.Table, schema_fields: list[dict]) -> str:
    """Logical fingerprint: normalized schema plus every value in row order."""
    payload = {
        "schema": schema_fields,
        "columns": [[name, [encode(v) for v in column_values(table.column(name))]] for name in table.column_names],
    }
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("ascii")).hexdigest()


def _map(f: Any, v: Any, depth: int) -> Any:
    """Apply `f` to the values `depth` list levels inside `v`; nulls stay null."""
    if v is None:
        return None
    if depth == 0:
        return f(v)
    return [_map(f, x, depth - 1) for x in v]


def flatten(table: pa.Table) -> dict[str, list[Any]]:
    """Every field path of `table` mapped to its per-row values.

    - A struct path maps to its validity (`True` or `None`); its children are
      separate paths, and a child of a null struct is `None`.
    - A list or map path maps to its length (`None` when the list is null);
      its elements are the path `<path>[]`, whose per-row value is the list of
      element values (map elements are `<path>[].key` and `<path>[].value`).
    """
    out: dict[str, list[Any]] = {}

    def walk(path: str, t: pa.DataType, values: list[Any], depth: int) -> None:
        if pat.is_struct(t):
            out[path] = [_map(lambda _: True, v, depth) for v in values]
            for child in t:
                walk(f"{path}.{child.name}", child.type, [_map(lambda x, n=child.name: x[n], v, depth) for v in values], depth)
        elif pat.is_map(t):
            out[path] = [_map(len, v, depth) for v in values]
            for key in ("key", "value"):
                child = t.key_type if key == "key" else t.item_type
                walk(f"{path}[].{key}", child, [_map(lambda x, k=key: x[k], v, depth + 1) for v in values], depth + 1)
        elif pat.is_list(t) or pat.is_large_list(t):
            out[path] = [_map(len, v, depth) for v in values]
            walk(f"{path}[]", t.value_type, values, depth + 1)
        else:
            out[path] = values

    for field in table.schema:
        walk(field.name, field.type, column_values(table.column(field.name)), 0)
    return out
