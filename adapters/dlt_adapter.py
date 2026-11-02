"""dlt: append Arrow versions to a fresh local DuckDB destination.

``default`` uses the evolve contract; ``freeze_columns`` and
``freeze_data_type`` set the corresponding schema entity to freeze.
Destination metadata, rather than dlt's inferred schema, describes the table.
"""

from __future__ import annotations

import gc
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import dlt
import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from dlt.common.schema.typing import TSchemaContract
from dlt.load.exceptions import LoadClientJobException
from dlt.pipeline.exceptions import PipelineStepFailed

from .base import Adapter, Observation, ResultField
from .duckdb_adapter import corpus_type


@dataclass(frozen=True)
class DltResult:
    columns: list[tuple[str, str, str]]
    row_count: int


class DltAdapter(Adapter):
    name = "dlt"
    modes = {"append_to_table": ("default", "freeze_columns", "freeze_data_type")}

    @property
    def version(self) -> str:
        return dlt.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> DltResult:
        if operation != "append_to_table" or mode not in self.modes["append_to_table"] or len(paths) != 2:
            raise AssertionError(f"unsupported dlt call: {operation}, {mode}, {paths}")
        # Set this before any config access initializes dlt's runtime.
        os.environ["RUNTIME__DLTHUB_TELEMETRY"] = "false"
        contracts: dict[str, TSchemaContract] = {
            "freeze_columns": {"columns": "freeze"},
            "freeze_data_type": {"data_type": "freeze"},
        }
        with TemporaryDirectory(prefix="tec-dlt-") as temp:
            database = Path(temp) / "destination.duckdb"
            pipeline = dlt.pipeline(
                pipeline_name=f"tec_{uuid4().hex}",
                pipelines_dir=str(Path(temp) / "pipelines"),
                destination=dlt.destinations.duckdb(str(database)),
                dataset_name="corpus",
            )

            # A fixed source name keeps schema names in engine messages
            # independent of the unique pipeline name.
            @dlt.source(name="corpus")
            def source(table: pa.Table):
                return dlt.resource(table, name="versions", write_disposition="append")

            try:
                for path in paths:
                    data = source(pq.read_table(path))
                    if mode == "default":
                        pipeline.run(data)
                    else:
                        pipeline.run(data, schema_contract=contracts[mode])
            except PipelineStepFailed as error:
                # Record the engine's original exception verbatim. The wrappers
                # add random load/job identifiers and pipeline state instructions.
                cause = error.exception
                if isinstance(cause, LoadClientJobException):
                    cause = cause.client_exception
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
                return DltResult([(name, native, null) for name, native, null, *_ in described], counted[0])

    def describe(self, result: DltResult) -> Observation:
        fields = [
            # DuckDB exposes JSON's type id as varchar; JSON has no corpus type.
            ResultField(name, None if native == "JSON" else corpus_type(duckdb.sqltype(native)), native, null == "YES")
            for name, native, null in result.columns
            if name not in {"_dlt_load_id", "_dlt_id"}
        ]
        return Observation(fields, result.row_count)
