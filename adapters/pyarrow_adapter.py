"""PyArrow: `pyarrow.parquet` for one file, `pyarrow.dataset` for several.

| operation | mode | call |
| --- | --- | --- |
| read_each_version | default | `pq.read_table(file)` |
| read_versions_together | default | `ds.dataset([from, to])`: the schema is inferred from the first file |
| read_versions_together | unified_permissive | `ds.dataset([from, to], schema=pa.unify_schemas(..., promote_options="permissive"))` |

Neither `read_versions_together` mode is the reference; both are recorded.
Result types are Arrow types, so the corpus spelling comes straight from the
corpus type vocabulary.
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from tabular_evolution.types import type_string

from .base import Adapter, Observation, ResultField


def corpus_type(t: pa.DataType) -> str | None:
    try:
        return type_string(t)
    except NotImplementedError:  # an Arrow type outside the vocabulary
        return None


class PyArrowDatasetAdapter(Adapter):
    name = "pyarrow_dataset"
    modes = {
        "read_each_version": ("default",),
        "read_versions_together": ("default", "unified_permissive"),
    }

    @property
    def version(self) -> str:
        return pa.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> pa.Table:
        if operation == "read_each_version":
            return pq.read_table(paths[0])
        if mode == "default":
            return ds.dataset(paths, format="parquet").to_table()
        unified = pa.unify_schemas([pq.read_schema(p) for p in paths], promote_options="permissive")
        return ds.dataset(paths, schema=unified, format="parquet").to_table()

    def describe(self, result: pa.Table) -> Observation:
        fields = [ResultField(f.name, corpus_type(f.type), str(f.type), f.nullable) for f in result.schema]
        return Observation(fields, result.num_rows)
