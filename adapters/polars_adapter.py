"""Polars.

| operation | mode | call |
| --- | --- | --- |
| read_each_version | default | `pl.read_parquet(file)` |
| read_versions_together | default | `pl.scan_parquet([from, to]).collect()`, default options |
| read_versions_together | diagonal_relaxed | `pl.concat([pl.read_parquet(f) for f in (from, to)], how="diagonal_relaxed")` |

Polars has its own type system. A dtype maps to the corpus vocabulary where
the equivalent is clear:

- integers, floats, Boolean, Date and Null by name; String is `string` and
  Binary is `binary` (Polars has one of each; the corpus's `large_` variants
  differ only in Arrow offset width);
- Datetime(unit, zone) is `timestamp[unit]` or `timestamp[unit, tz=zone]`;
- Decimal(p, s) is `decimal128(p, s)`;
- List and Struct map member by member; Polars records no nullability, so
  members carry no `not null` and `nullable` is null.

Anything else (Categorical, Enum, Array, Int128, ...) has no clear
equivalent; the runner then records the dtype verbatim in `notes`. A Parquet
map reads as a list of key/value structs, and is recorded as such.

Out of scope for now: `scan_parquet`'s explicit schema, missing/extra-column
and cast options.
"""

from __future__ import annotations

import polars as pl

from .base import Adapter, Observation, ResultField

_SIMPLE = {
    pl.Int8: "int8",
    pl.Int16: "int16",
    pl.Int32: "int32",
    pl.Int64: "int64",
    pl.UInt8: "uint8",
    pl.UInt16: "uint16",
    pl.UInt32: "uint32",
    pl.UInt64: "uint64",
    pl.Float16: "halffloat",
    pl.Float32: "float",
    pl.Float64: "double",
    pl.Boolean: "bool",
    pl.String: "string",
    pl.Binary: "binary",
    pl.Date: "date32[day]",
    pl.Null: "null",
}


def corpus_type(t: pl.DataType) -> str | None:
    for simple, name in _SIMPLE.items():
        if t == simple:
            return name
    if isinstance(t, pl.Datetime):
        return f"timestamp[{t.time_unit}]" if t.time_zone is None else f"timestamp[{t.time_unit}, tz={t.time_zone}]"
    if isinstance(t, pl.Decimal):
        return f"decimal128({t.precision}, {t.scale})"
    if isinstance(t, pl.List):
        inner = corpus_type(t.inner)
        return None if inner is None else f"list<{inner}>"
    if isinstance(t, pl.Struct):
        members = [(f.name, corpus_type(f.dtype)) for f in t.fields]
        if any(m is None for _, m in members):
            return None
        return "struct<" + ", ".join(f"{name}: {m}" for name, m in members) + ">"
    return None


class PolarsAdapter(Adapter):
    name = "polars"
    modes = {
        "read_each_version": ("default",),
        "read_versions_together": ("default", "diagonal_relaxed"),
    }

    @property
    def version(self) -> str:
        return pl.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> pl.DataFrame:
        if operation == "read_each_version":
            return pl.read_parquet(paths[0])
        if mode == "default":
            return pl.scan_parquet(paths).collect()
        return pl.concat([pl.read_parquet(p) for p in paths], how="diagonal_relaxed")

    def describe(self, result: pl.DataFrame) -> Observation:
        fields = [ResultField(name, corpus_type(t), str(t), None) for name, t in result.schema.items()]
        return Observation(fields, result.height)
