"""delta-rs: create a local Delta table, then bring in the next version.

| operation | mode | second write |
| --- | --- | --- |
| append_to_table | default | append with the default schema rules |
| append_to_table | merge | append with ``schema_mode="merge"`` |
| append_to_table | overwrite | overwrite data and schema |

Every call gets its own temporary table. The resulting schema comes from the
Delta transaction log; its Arrow form is mapped to the corpus type vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import deltalake
import pyarrow as pa
import pyarrow.parquet as pq
from deltalake import DeltaTable, write_deltalake

from .base import Adapter, Observation, ResultField
from .pyarrow_adapter import corpus_type


@dataclass(frozen=True)
class DeltaResult:
    schema: pa.Schema
    native_types: tuple[str, ...]
    row_count: int


class DeltaLakeAdapter(Adapter):
    name = "deltalake"
    modes = {"append_to_table": ("default", "merge", "overwrite")}

    @property
    def version(self) -> str:
        return deltalake.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> DeltaResult:
        if operation != "append_to_table" or mode not in self.modes["append_to_table"] or len(paths) != 2:
            raise AssertionError(f"unsupported Delta call: {operation}, {mode}, {paths}")
        previous, incoming = (pq.read_table(path) for path in paths)
        with TemporaryDirectory(prefix="tec-delta-") as temp:
            location = Path(temp) / "table"
            write_deltalake(location, previous)
            options: dict[str, Any] = {"mode": "append"}
            if mode == "merge":
                options["schema_mode"] = "merge"
            elif mode == "overwrite":
                options = {"mode": "overwrite", "schema_mode": "overwrite"}
            write_deltalake(location, incoming, **options)
            table = DeltaTable(location)
            delta_schema = table.schema()
            schema = pa.schema(delta_schema.to_arrow())
            native_types = tuple(str(field.type) for field in delta_schema.fields)
            row_count = table.to_pyarrow_table().num_rows
            return DeltaResult(schema, native_types, row_count)

    def describe(self, result: DeltaResult) -> Observation:
        if len(result.schema) != len(result.native_types):
            raise AssertionError("Delta and Arrow schema field counts differ")
        fields = [
            ResultField(field.name, corpus_type(field.type), native, field.nullable)
            for field, native in zip(result.schema, result.native_types, strict=True)
        ]
        return Observation(fields, result.row_count)
