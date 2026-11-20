"""dlt: load Arrow versions into a fresh local DuckDB or Parquet destination.

Supports dlt 1.20.0 and later. CI tests the oldest supported release and the
pinned one on every push, and the newest release weekly. Before 1.20,
``LoadClientJobFailed`` keeps only the destination's message, not its
exception, so records would name a different error class.
"""

from __future__ import annotations

import base64
import gc
import gzip
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dlt
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from dlt.common.schema.typing import TSchemaContract, TWriteDisposition
from dlt.destinations.impl.filesystem.filesystem import FilesystemClient
from dlt.load.exceptions import LoadClientJobException
from dlt.pipeline.exceptions import PipelineStepFailed

from tabular_evolution.generate import MANIFEST
from tabular_evolution.validate import load_json

from .base import Adapter, Observation, ResultField
from .duckdb_adapter import corpus_type as duckdb_type
from .pyarrow_adapter import corpus_type as arrow_type

try:
    from dlt.common.destination.exceptions import SchemaUpdateError
except ImportError:
    SchemaUpdateError = None  # dlt before 1.30 raises the schema error directly.


CONTRACTS: dict[str, TSchemaContract] = {
    "freeze_columns": {"columns": "freeze"},
    "freeze_data_type": {"data_type": "freeze"},
    "freeze_columns_and_data_type": {"columns": "freeze", "data_type": "freeze"},
    "evolve_columns_freeze_data_type": {"columns": "evolve", "data_type": "freeze"},
    "evolve_all": {"tables": "evolve", "columns": "evolve", "data_type": "evolve"},
}
DISPOSITIONS: dict[str, TWriteDisposition] = {
    "append_to_table": "append",
    "replace_table": "replace",
    "merge_into_table": "merge",
}
BOOKKEEPING_COLUMNS = {"_dlt_load_id", "_dlt_id", "_dlt_root_id", "_dlt_parent_id", "_dlt_list_idx"}


ARROW_IPC_PREFIX = "arrow-ipc:"


def _hint_value(value: Any) -> Any:
    """A stored hint as recorded. dlt keeps a nested column's Arrow type as
    ``arrow-ipc:`` + base64 of a gzip-compressed IPC schema. The gzip header
    names the operating system, so the same type gives different bytes on
    Windows and Linux. The record keeps the Arrow type the hint encodes."""
    if not (isinstance(value, str) and value.startswith(ARROW_IPC_PREFIX)):
        return value
    ipc = gzip.decompress(base64.b64decode(value[len(ARROW_IPC_PREFIX) :]))
    schema = pa.ipc.read_schema(pa.py_buffer(ipc))
    return ARROW_IPC_PREFIX + str(schema.field(0).type)


@dataclass(frozen=True)
class DltResult:
    columns: list[tuple[str, str, str]] | pa.Schema
    row_count: int
    schema_hints: dict[str, dict[str, Any]]


class DltAdapter(Adapter):
    name = "dlt"
    modes = {
        "append_to_table": ("default", *CONTRACTS, "filesystem"),
        "replace_table": ("default",),
        "merge_into_table": ("default",),
    }

    @property
    def version(self) -> str:
        return dlt.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> DltResult:
        if mode not in self.modes.get(operation, ()) or len(paths) != 2:
            raise AssertionError(f"unsupported dlt call: {operation}, {mode}, {paths}")
        # Set this before any config access initializes dlt's runtime.
        os.environ["RUNTIME__DLTHUB_TELEMETRY"] = "false"
        contract = CONTRACTS.get(mode)
        disposition = DISPOSITIONS[operation]
        keys = load_json(Path(paths[0]).with_name(MANIFEST))["row_identity"] if disposition == "merge" else []
        with TemporaryDirectory(prefix="tec-dlt-") as temp:
            database = Path(temp) / "destination.duckdb"
            filesystem = mode == "filesystem"
            destination = (
                dlt.destinations.filesystem(bucket_url=(Path(temp) / "bucket").as_uri())
                if filesystem
                else dlt.destinations.duckdb(str(database))
            )
            pipeline = dlt.pipeline(
                # Each call has its own pipelines_dir and destination.
                pipeline_name="tec",
                pipelines_dir=str(Path(temp) / "pipelines"),
                destination=destination,
                dataset_name="corpus",
            )

            # A fixed source name keeps schema names in engine messages
            # independent of the pipeline name.
            @dlt.source(name="corpus")
            def source(table: pa.Table):
                resource = dlt.resource(table, name="versions", write_disposition=disposition)
                if keys:
                    resource.apply_hints(primary_key=keys)
                return resource

            try:
                for path in paths:
                    data = source(pq.read_table(path))
                    if filesystem:
                        pipeline.run(data, loader_file_format="parquet")
                    elif contract is not None:
                        pipeline.run(data, schema_contract=contract)
                    else:
                        pipeline.run(data)
                schema_hints = {
                    name: {key: _hint_value(value) for key, value in column.items() if key != "name"}
                    for name, column in pipeline.default_schema.get_table_columns("versions").items()
                }
                if filesystem:
                    client = pipeline.destination_client()
                    assert isinstance(client, FilesystemClient)
                    files = sorted(client.list_table_files("versions"))
                    table = pa.concat_tables([pq.read_table(file) for file in files], promote_options="permissive")
                    return DltResult(table.schema, table.num_rows, schema_hints)
            except PipelineStepFailed as error:
                # Record the engine's original exception verbatim. The wrappers
                # add random load/job identifiers and pipeline state instructions.
                cause = error.exception
                if isinstance(cause, LoadClientJobException):
                    cause = cause.client_exception
                elif SchemaUpdateError is not None and isinstance(cause, SchemaUpdateError):
                    cause = cause.cause
                # dlt's Arrow writer can remain referenced by failed frames.
                # Release those locals and writer cycles before Windows removes
                # the temp files (dlt-hub/dlt#4633).
                traceback.clear_frames(cause.__traceback__)
                gc.collect()
                raise cause from error
            finally:
                pipeline.deactivate()
            with duckdb.connect(str(database), read_only=True) as con:
                described = con.sql('DESCRIBE "corpus"."versions"').fetchall()
                counted = con.sql('SELECT count(*) FROM "corpus"."versions"').fetchone()
                if counted is None:
                    raise AssertionError("count(*) returned no row")
                return DltResult(
                    [(name, native, null) for name, native, null, *_ in described], counted[0], schema_hints
                )

    def describe(self, result: DltResult) -> Observation:
        if isinstance(result.columns, pa.Schema):
            fields = [
                ResultField(field.name, arrow_type(field.type), str(field.type), field.nullable)
                for field in result.columns
                if field.name not in BOOKKEEPING_COLUMNS
            ]
        else:
            fields = [
                # DuckDB exposes JSON's type id as varchar; JSON has no corpus type.
                ResultField(
                    name, None if native == "JSON" else duckdb_type(duckdb.sqltype(native)), native, null == "YES"
                )
                for name, native, null in result.columns
                if name not in BOOKKEEPING_COLUMNS
            ]
        hints = {field.name: result.schema_hints[field.name] for field in fields}
        return Observation(fields, result.row_count, engine_schema_hints=hints)
