"""DuckDB: `read_parquet` in a fresh in-memory database per call.

| operation | mode | call |
| --- | --- | --- |
| read_each_version | default | `read_parquet(file)` |
| read_versions_together | positional | `read_parquet([from, to])` (DuckDB's default) |
| read_versions_together | union_by_name | `read_parquet([from, to], union_by_name = true)` |

The result is materialized inside DuckDB (`CREATE TABLE ... AS`), so every
row is scanned and conversion errors surface, but no value is fetched into
Python. Result types are what `DESCRIBE` reports. The corpus spelling is
derived from DuckDB's structured type, not by parsing that string:

- one text, one blob and one list type: VARCHAR is `string`, BLOB is
  `binary`, LIST is `list<...>`. The corpus's `large_` variants differ only
  in Arrow offset width, which DuckDB does not expose, so the plain variant
  stands for all of them;
- DECIMAL(p, s) is `decimal128(p, s)`;
- TIMESTAMP[_S|_MS|_NS] are naive timestamps of that unit;
- STRUCT, LIST and MAP map member by member; DuckDB records no nullability,
  so members carry no `not null` and `nullable` is null;
- anything else, including TIMESTAMP WITH TIME ZONE (an instant rendered in
  the session time zone, with no zone of its own), has no clear equivalent.

Out of scope for now: a supplied `schema`, which cannot be combined with
union_by_name.
"""

from __future__ import annotations

import duckdb

from .base import Adapter, Observation, ResultField

_SIMPLE = {
    "boolean": "bool",
    "tinyint": "int8",
    "smallint": "int16",
    "integer": "int32",
    "bigint": "int64",
    "utinyint": "uint8",
    "usmallint": "uint16",
    "uinteger": "uint32",
    "ubigint": "uint64",
    "float": "float",
    "double": "double",
    "varchar": "string",
    "blob": "binary",
    "date": "date32[day]",
    "timestamp_s": "timestamp[s]",
    "timestamp_ms": "timestamp[ms]",
    "timestamp": "timestamp[us]",
    "timestamp_ns": "timestamp[ns]",
}


def corpus_type(t: duckdb.DuckDBPyType) -> str | None:
    if t.id in _SIMPLE:
        return _SIMPLE[t.id]
    if t.id == "decimal":
        params = dict(t.children)
        return f"decimal128({params['precision']}, {params['scale']})"
    if t.id in ("struct", "list", "map"):
        members = [(name, corpus_type(child)) for name, child in t.children]
        if any(m is None for _, m in members):
            return None
        if t.id == "struct":
            return "struct<" + ", ".join(f"{name}: {m}" for name, m in members) + ">"
        if t.id == "list":
            return f"list<{members[0][1]}>"
        return f"map<{members[0][1]}, {members[1][1]}>"
    return None


def _sql_string(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


class DuckDBAdapter(Adapter):
    name = "duckdb"
    modes = {
        "read_each_version": ("default",),
        "read_versions_together": ("positional", "union_by_name"),
    }

    @property
    def version(self) -> str:
        return duckdb.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> duckdb.DuckDBPyConnection:
        files = "[" + ", ".join(_sql_string(p) for p in paths) + "]"
        options = ", union_by_name = true" if mode == "union_by_name" else ""
        # One thread: which file a conversion error is reported for does not
        # depend on scheduling.
        con = duckdb.connect(config={"threads": 1})
        try:
            con.execute(f"CREATE TABLE result AS SELECT * FROM read_parquet({files}{options})")
        except BaseException:
            con.close()
            raise
        return con

    def describe(self, result: duckdb.DuckDBPyConnection) -> Observation:
        described = result.sql("DESCRIBE result").fetchall()  # (name, type, null, key, default, extra)
        types = result.table("result").types
        fields = []
        for (name, native, *_), t in zip(described, types, strict=True):
            if str(t) != native:
                raise AssertionError(f"{name}: DESCRIBE says {native}, the relation says {t}")
            fields.append(ResultField(name, corpus_type(t), native, None))
        (row_count,) = result.sql("SELECT count(*) FROM result").fetchone()
        result.close()
        return Observation(fields, row_count)
