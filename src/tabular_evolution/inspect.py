"""PyArrow reference inspector: facts about a Parquet file, never judgements.

Everything here reports what a file contains as PyArrow reads it. It does not
score PyArrow, and nothing here decides whether a change is acceptable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.types as pat

from .types import type_string

FIELD_ID_KEY = b"PARQUET:field_id"


def field_id(field: pa.Field) -> int | None:
    meta = field.metadata or {}
    return int(meta[FIELD_ID_KEY]) if FIELD_ID_KEY in meta else None


def describe_schema(schema: pa.Schema) -> list[dict[str, Any]]:
    """The manifest form of an Arrow schema: top-level fields in file order."""
    fields = []
    for f in schema:
        entry: dict[str, Any] = {"name": f.name, "type": type_string(f.type), "nullable": f.nullable}
        fid = field_id(f)
        if fid is not None:
            entry["field_id"] = fid
        fields.append(entry)
    return fields


def read_table(path: Path) -> pa.Table:
    return pq.read_table(path)


def parquet_columns(path: Path) -> list[dict[str, Any]]:
    """Parquet leaf column descriptors, independent of the stored Arrow schema."""
    schema = pq.ParquetFile(path).schema
    columns = []
    for i in range(len(schema)):
        c = schema.column(i)
        columns.append(
            {
                "path": c.path,
                "physical_type": c.physical_type,
                "logical_type": str(c.logical_type),
                "converted_type": c.converted_type,
                "max_definition_level": c.max_definition_level,
                "max_repetition_level": c.max_repetition_level,
                "length": c.length,
                "precision": c.precision,
                "scale": c.scale,
            }
        )
    return columns


def parquet_schema_text(path: Path) -> str:
    """The Parquet schema tree as text: groups, repetition, physical and
    logical types and field ids. This is what `parquet_schema_preserved`
    compares; the stored Arrow schema is deliberately not part of it."""
    lines = str(pq.ParquetFile(path).schema).strip().splitlines()
    # PyArrow prefixes the tree with the Python object's repr, which holds a
    # memory address; it is not part of the schema.
    return "\n".join(line for line in lines if not line.startswith("<pyarrow."))


def column_chunks(path: Path) -> list[dict[str, Any]]:
    """Per column chunk: encodings, dictionary page presence, null count."""
    meta = pq.ParquetFile(path).metadata
    chunks = []
    for rg in range(meta.num_row_groups):
        group = meta.row_group(rg)
        for ci in range(group.num_columns):
            col = group.column(ci)
            stats = col.statistics
            chunks.append(
                {
                    "row_group": rg,
                    "path": col.path_in_schema,
                    "encodings": sorted(col.encodings),
                    "has_dictionary_page": col.has_dictionary_page,
                    "null_count": stats.null_count if stats is not None and stats.has_null_count else None,
                }
            )
    return chunks


def dictionaries(table: pa.Table) -> list[dict[str, Any]]:
    """Stored dictionary contents of top-level dictionary columns, in order."""
    out = []
    for f in table.schema:
        if pat.is_dictionary(f.type):
            column = table.column(f.name)
            if column.num_chunks != 1:
                raise ValueError(f"{f.name}: expected one chunk, got {column.num_chunks}")
            out.append({"path": f.name, "values": column.chunk(0).dictionary.to_pylist()})
    return out


def created_by(path: Path) -> str:
    return pq.ParquetFile(path).metadata.created_by


def inspect_file(path: Path) -> dict[str, Any]:
    """Everything the reference inspector reports about one file."""
    meta = pq.ParquetFile(path).metadata
    table = read_table(path)
    return {
        "row_count": meta.num_rows,
        "row_groups": meta.num_row_groups,
        "created_by": meta.created_by,
        "arrow_schema": describe_schema(table.schema),
        "parquet_schema": parquet_schema_text(path).splitlines(),
        "parquet_columns": parquet_columns(path),
        "column_chunks": column_chunks(path),
        "dictionaries": dictionaries(table),
    }
