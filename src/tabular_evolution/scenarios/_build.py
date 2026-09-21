"""Table-building helpers for scenario definitions."""

from __future__ import annotations

from typing import Any

import pyarrow as pa

from ..inspect import FIELD_ID_KEY


def field(name: str, type: pa.DataType, nullable: bool = True, field_id: int | None = None) -> pa.Field:
    metadata = {FIELD_ID_KEY: str(field_id).encode()} if field_id is not None else None
    return pa.field(name, type, nullable=nullable, metadata=metadata)


def table(fields: list[pa.Field], columns: list[Any]) -> pa.Table:
    """A table from explicit fields; each column is a list of Python values or
    a ready-made Arrow array."""
    if len(fields) != len(columns):
        raise ValueError("one column per field")
    arrays = [c if isinstance(c, pa.Array) else pa.array(c, type=f.type) for f, c in zip(fields, columns)]
    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def dictionary(values: list[str], indices: list[int | None], index_type: pa.DataType = pa.int8()) -> pa.Array:
    """A string dictionary array with an exact, caller-chosen dictionary."""
    return pa.DictionaryArray.from_arrays(pa.array(indices, type=index_type), pa.array(values, type=pa.string()))


ID = field("id", pa.int64(), nullable=False)


def ids(n: int) -> list[int]:
    return list(range(1, n + 1))
