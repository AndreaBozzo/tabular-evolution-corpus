"""The corpus type vocabulary.

Type strings in manifests, mutations and fingerprints are rendered here, not
by Arrow's `DataType::ToString()`. Arrow's rendering is a debugging aid and
changes between releases and even between write and read of the same file: a
list written as `list<item: int32>` reads back as `list<element: int32>`, and
a map column renders with its column name inside the type (`map<string,
int32 ('m')>`). A fingerprint or declared schema built on that would change
without any change to the data.

For scalar types the spelling deliberately matches Arrow's current one, so the
strings stay familiar. Container types omit physical child names. An Arrow
type outside this vocabulary is an error, not a best-effort string.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.types as pat

_SIMPLE = {
    pa.null(): "null",
    pa.bool_(): "bool",
    pa.int8(): "int8",
    pa.int16(): "int16",
    pa.int32(): "int32",
    pa.int64(): "int64",
    pa.uint8(): "uint8",
    pa.uint16(): "uint16",
    pa.uint32(): "uint32",
    pa.uint64(): "uint64",
    pa.float16(): "halffloat",
    pa.float32(): "float",
    pa.float64(): "double",
    pa.string(): "string",
    pa.large_string(): "large_string",
    pa.binary(): "binary",
    pa.large_binary(): "large_binary",
    pa.date32(): "date32[day]",
    pa.date64(): "date64[ms]",
}


def _member(t: pa.DataType, nullable: bool) -> str:
    return type_string(t) + ("" if nullable else " not null")


def type_string(t: pa.DataType) -> str:
    """The corpus spelling of an Arrow type."""
    for simple, name in _SIMPLE.items():
        if t == simple:
            return name
    if pat.is_decimal(t):
        return f"decimal{t.bit_width}({t.precision}, {t.scale})"
    if pat.is_timestamp(t):
        return f"timestamp[{t.unit}]" if t.tz is None else f"timestamp[{t.unit}, tz={t.tz}]"
    if pat.is_dictionary(t):
        return (
            f"dictionary<values={type_string(t.value_type)}, indices={type_string(t.index_type)}, "
            f"ordered={int(t.ordered)}>"
        )
    if pat.is_struct(t):
        return "struct<" + ", ".join(f"{f.name}: {_member(f.type, f.nullable)}" for f in t) + ">"
    if pat.is_map(t):
        return f"map<{type_string(t.key_type)}, {_member(t.item_type, t.item_field.nullable)}>"
    if pat.is_list(t) or pat.is_large_list(t):
        kind = "list" if pat.is_list(t) else "large_list"
        return f"{kind}<{_member(t.value_type, t.value_field.nullable)}>"
    raise NotImplementedError(f"Arrow type {t} is not in the corpus type vocabulary")
