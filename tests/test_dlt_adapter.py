"""dlt contracts, destination metadata and isolated local pipeline state."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest

dlt = pytest.importorskip("dlt")
pytest.importorskip("duckdb")

from conftest import ROOT

from adapters import dlt_adapter
from adapters.runner import CorpusIdentity, check_record, observe, result_validator


@cache
def additive_records() -> list[dict[str, Any]]:
    return observe(ROOT, dlt_adapter.DltAdapter(), CorpusIdentity("0.3.0", "0" * 40), ["add_nullable_int64_column"])


def test_declared_modes() -> None:
    contracts = (
        "freeze_columns",
        "freeze_data_type",
        "freeze_columns_and_data_type",
        "evolve_columns_freeze_data_type",
        "evolve_all",
    )
    assert dlt_adapter.DltAdapter.modes == {
        "append_to_table": (
            "default",
            *contracts,
            "filesystem",
            "filesystem_freeze_columns",
            "filesystem_freeze_data_type",
            "filesystem_freeze_columns_and_data_type",
            "filesystem_evolve_columns_freeze_data_type",
            "filesystem_evolve_all",
        ),
        "replace_table": ("default", *contracts),
        "merge_into_table": ("default", *contracts),
    }


def test_evolve_appends_versions_and_reports_destination_metadata() -> None:
    record = next(r for r in additive_records() if r["mode"] == "default" and r["inputs"] == ["v0", "v1"])
    assert record["status"] == "success"
    assert record["row_count"] == 12
    assert record["result_schema"] == [
        {"name": "id", "type": "int64", "native_type": "BIGINT", "nullable": False},
        {"name": "name", "type": "string", "native_type": "VARCHAR", "nullable": True},
        {"name": "loyalty_points", "type": "int64", "native_type": "BIGINT", "nullable": True},
    ]


def test_freeze_columns_records_the_engine_message_verbatim() -> None:
    record = next(r for r in additive_records() if r["mode"] == "freeze_columns" and r["inputs"] == ["v0", "v1"])
    assert record["status"] == "error"
    assert record["error_class"] == "dlt.common.schema.exceptions.DataValidationError"
    assert record["notes"] == [
        "In schema `corpus`: In Table: `versions` Column: `loyalty_points` . "
        "Contract on `columns` with `contract_mode=freeze` is violated. "
        "Can't add table column `loyalty_points` to table `versions` because `columns` are frozen."
    ]
    reversed_ = next(r for r in additive_records() if r["mode"] == "freeze_columns" and r["inputs"] == ["v1", "v0"])
    assert reversed_["status"] == "success"
    assert reversed_["row_count"] == 12


def test_combined_contracts_handle_added_columns() -> None:
    forward = {
        r["mode"]: r for r in additive_records() if r["operation"] == "append_to_table" and r["inputs"] == ["v0", "v1"]
    }
    both_frozen = forward["freeze_columns_and_data_type"]
    assert both_frozen["status"] == "error"
    assert "`columns` are frozen" in both_frozen["notes"][0]
    for mode in ("evolve_columns_freeze_data_type", "evolve_all"):
        assert forward[mode]["status"] == "success"
        assert forward[mode]["row_count"] == 12
        assert forward[mode]["result_schema"][-1]["name"] == "loyalty_points"


@pytest.mark.parametrize(("operation", "rows"), [("replace_table", 4), ("merge_into_table", 6)])
def test_write_disposition_uses_second_input(operation: str, rows: int) -> None:
    paths = [str(ROOT / "fixtures/rows_deleted" / f"{version}.parquet") for version in ("v0", "v1")]
    adapter = dlt_adapter.DltAdapter()
    observation = adapter.describe(adapter.read(operation, "default", paths))
    assert observation.row_count == rows


def test_filesystem_reads_back_parquet() -> None:
    record = next(r for r in additive_records() if r["mode"] == "filesystem" and r["inputs"] == ["v0", "v1"])
    assert record["status"] == "success", record
    assert record["row_count"] == 12
    assert record["result_schema"] == [
        {"name": "id", "type": "int64", "native_type": "int64", "nullable": False},
        {"name": "name", "type": "string", "native_type": "string", "nullable": True},
        {"name": "loyalty_points", "type": "int64", "native_type": "int64", "nullable": True},
    ]


def test_hints_come_from_dlt_schema() -> None:
    paths = [str(ROOT / "fixtures/decimal_precision_increase" / f"{version}.parquet") for version in ("v0", "v1")]
    adapter = dlt_adapter.DltAdapter()
    observation = adapter.describe(adapter.read("append_to_table", "default", paths))
    assert observation.fields is not None
    assert next(field.native_type for field in observation.fields if field.name == "price") == "DECIMAL(9,2)"
    assert observation.engine_schema_hints is not None
    price = observation.engine_schema_hints["price"]
    assert price["data_type"] == "decimal"
    assert price["precision"] == 18
    assert price["scale"] == 2


def test_nested_type_hints_do_not_depend_on_the_operating_system() -> None:
    """dlt gzips a nested column's Arrow schema; the gzip header's OS byte is
    10 on Windows and 3 on Linux. The recorded hint is the decoded type."""
    import base64
    import gzip

    import pyarrow as pa

    field = pa.field("c", pa.list_(pa.struct([("sku", pa.string()), ("qty", pa.int32())])))
    ipc = pa.schema([field]).serialize().to_pybytes()
    encoded = []
    for os_byte in (3, 10):
        raw = bytearray(gzip.compress(ipc, mtime=0))
        raw[9] = os_byte
        encoded.append("arrow-ipc:" + base64.b64encode(bytes(raw)).decode("ascii"))
    assert encoded[0] != encoded[1]
    decoded = {dlt_adapter._hint_value(value) for value in encoded}
    assert decoded == {"arrow-ipc:" + str(field.type)}
    assert dlt_adapter._hint_value("bigint") == "bigint"


def test_records_follow_the_result_schema() -> None:
    validator = result_validator(ROOT)
    for record in additive_records():
        assert check_record(record, validator) == []
        if record["status"] == "success":
            assert record["engine_schema_hints"]["id"]["data_type"] == "bigint"
        else:
            assert record["engine_schema_hints"] is None


def test_nested_arrow_fields_are_stored_as_json() -> None:
    paths = [str(ROOT / "fixtures/add_nested_field" / f"{version}.parquet") for version in ("v0", "v1")]
    adapter = dlt_adapter.DltAdapter()
    observation = adapter.describe(adapter.read("append_to_table", "default", paths))
    assert observation.fields is not None
    address = next(field for field in observation.fields if field.name == "address")
    assert address.native_type == "JSON"
    assert address.type is None
    assert address.nullable is True
    assert observation.row_count == 10


def test_bookkeeping_columns_are_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_ID", "true")
    monkeypatch.setenv("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID", "true")
    paths = [str(ROOT / "fixtures/add_nullable_int64_column" / f"{version}.parquet") for version in ("v0", "v1")]
    adapter = dlt_adapter.DltAdapter()
    result = adapter.read("append_to_table", "default", paths)
    assert isinstance(result.columns, list)
    assert {name for name, *_ in result.columns} >= {"_dlt_id", "_dlt_load_id"}
    observation = adapter.describe(result)
    assert observation.fields is not None
    assert {field.name for field in observation.fields} == {"id", "name", "loyalty_points"}
    assert observation.row_count == 12
    assert observation.engine_schema_hints is not None
    assert set(observation.engine_schema_hints) == {"id", "name", "loyalty_points"}


def test_telemetry_is_disabled() -> None:
    from dlt.common.runtime.run_context import active

    additive_records()
    assert active().runtime_config.dlthub_telemetry is False


@pytest.mark.parametrize(
    "mode", ["freeze_data_type", "freeze_columns_and_data_type", "evolve_columns_freeze_data_type"]
)
@pytest.mark.parametrize("scenario", ["nullable_to_required", "required_to_nullable"])
@pytest.mark.parametrize("versions", [("v0", "v1"), ("v1", "v0")])
def test_data_type_freeze_covers_nullability_in_both_orders(
    mode: str, scenario: str, versions: tuple[str, str]
) -> None:
    """From dlt 1.22, a data_type freeze rejects a change of nullability.

    Before 1.22 a data_type freeze accepts it. If columns are frozen too, the
    columns contract rejects it instead; 1.20 words it as adding the column
    and 1.21 as evolving it. The test asserts each case, so a run on the
    oldest supported dlt checks the adapter there.
    """
    from dlt.common.schema.exceptions import DataValidationError

    paths = [str(ROOT / "fixtures" / scenario / f"{version}.parquet") for version in versions]
    if _dlt_release() >= (1, 22):
        with pytest.raises(DataValidationError, match="Can't evolve column type"):
            dlt_adapter.DltAdapter().read("append_to_table", mode, paths)
    elif mode == "freeze_columns_and_data_type":
        with pytest.raises(DataValidationError, match="Contract on `columns` with `contract_mode=freeze` is violated"):
            dlt_adapter.DltAdapter().read("append_to_table", mode, paths)
    else:
        dlt_adapter.DltAdapter().read("append_to_table", mode, paths)


def _dlt_release() -> tuple[int, int]:
    major, minor = dlt.__version__.split(".")[:2]
    return int(major), int(minor)


@pytest.mark.parametrize(
    ("scenario", "mode", "versions", "exception"),
    [
        ("add_nullable_int64_column", "default", ("v0", "v1"), None),
        ("add_nullable_int64_column", "freeze_columns", ("v0", "v1"), "DataValidationError"),
        ("add_required_column", "default", ("v1", "v0"), "ArrowInvalid"),
        ("add_required_column", "freeze_columns", ("v1", "v0"), "ArrowInvalid"),
        ("add_required_column", "freeze_data_type", ("v1", "v0"), "ArrowInvalid"),
        ("remove_required_column", "default", ("v0", "v1"), "ArrowInvalid"),
        ("remove_required_column", "freeze_columns", ("v0", "v1"), "ArrowInvalid"),
        ("remove_required_column", "freeze_data_type", ("v0", "v1"), "ArrowInvalid"),
    ],
)
def test_each_call_cleans_up_pipeline_and_database(
    monkeypatch: pytest.MonkeyPatch, scenario: str, mode: str, versions: tuple[str, str], exception: str | None
) -> None:
    import pyarrow as pa
    from dlt.common.schema.exceptions import DataValidationError

    # dlt's nested job paths can exceed Windows' path limit under pytest's
    # per-test directory. Keep the parent as short as the adapter's own temp.
    with TemporaryDirectory(prefix="tec-test-") as parent:

        def temporary_pipeline(prefix: str) -> TemporaryDirectory[str]:
            return TemporaryDirectory(prefix=prefix, dir=parent)

        monkeypatch.setattr(dlt_adapter, "TemporaryDirectory", temporary_pipeline)
        paths = [str(ROOT / "fixtures" / scenario / f"{version}.parquet") for version in versions]
        if exception is None:
            dlt_adapter.DltAdapter().read("append_to_table", mode, paths)
        else:
            kind = {"DataValidationError": DataValidationError, "ArrowInvalid": pa.ArrowInvalid}[exception]
            with pytest.raises(kind):
                dlt_adapter.DltAdapter().read("append_to_table", mode, paths)
        assert list(Path(parent).iterdir()) == []


def test_destination_error_omits_job_identifiers() -> None:
    from dlt.destinations.exceptions import DatabaseTerminalException

    paths = [str(ROOT / "fixtures/drop_then_readd_column" / f"{version}.parquet") for version in ("v0", "v1")]
    with pytest.raises(DatabaseTerminalException) as error:
        dlt_adapter.DltAdapter().read("append_to_table", "default", paths)
    assert (
        str(error.value)
        == "Conversion Error: Could not convert string 'A' to INT32 when casting from source column score"
    )


def test_schema_update_error_omits_internal_table_order() -> None:
    from dlt.destinations.exceptions import DatabaseTransientException

    paths = [str(ROOT / "fixtures/add_required_column" / f"{version}.parquet") for version in ("v0", "v1")]
    with pytest.raises(DatabaseTransientException) as error:
        dlt_adapter.DltAdapter().read("append_to_table", "default", paths)
    assert str(error.value) == "Parser Error: Adding columns with constraints not yet supported"


@pytest.mark.parametrize("mode", ["default", "filesystem"])
def test_same_pipeline_name_keeps_calls_isolated(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from dlt.common.configuration.container import Container
    from dlt.common.pipeline import PipelineContext
    from dlt.common.schema.exceptions import DataValidationError

    create_pipeline = dlt.pipeline
    pipelines = []

    def capture_pipeline(**kwargs):
        pipeline = create_pipeline(**kwargs)
        pipelines.append(pipeline)
        return pipeline

    monkeypatch.setattr(dlt, "pipeline", capture_pipeline)
    adapter = dlt_adapter.DltAdapter()

    def read(scenario: str, contract: str):
        paths = [str(ROOT / "fixtures" / scenario / f"{version}.parquet") for version in ("v0", "v1")]
        return adapter.describe(adapter.read("append_to_table", contract, paths))

    first = read("add_nullable_int64_column", mode)
    assert first.row_count == 12
    with pytest.raises(DataValidationError, match="`columns` are frozen"):
        read("add_nullable_int64_column", "freeze_columns")
    assert not Container()[PipelineContext].is_active()
    second = read("rows_deleted", mode)
    assert second.row_count == 10
    assert second.fields is not None
    assert [field.name for field in second.fields] == ["id", "status", "amount"]
    assert [pipeline.pipeline_name for pipeline in pipelines] == ["tec"] * 3
    assert len({pipeline.working_dir for pipeline in pipelines}) == 3
    assert all(not Path(pipeline.working_dir).exists() for pipeline in pipelines)
    assert not Container()[PipelineContext].is_active()


@pytest.mark.parametrize(
    ("operation", "default_mode", "freeze_mode", "rows"),
    [
        ("replace_table", "default", "freeze_columns", 6),
        ("merge_into_table", "default", "freeze_columns", 6),
        ("append_to_table", "filesystem", "filesystem_freeze_columns", 12),
    ],
)
def test_freeze_columns_applies_to_replace_merge_and_filesystem(
    operation: str, default_mode: str, freeze_mode: str, rows: int
) -> None:
    from dlt.common.schema.exceptions import DataValidationError

    paths = [str(ROOT / "fixtures/add_nullable_int64_column" / f"{version}.parquet") for version in ("v0", "v1")]
    adapter = dlt_adapter.DltAdapter()
    default = adapter.describe(adapter.read(operation, default_mode, paths))
    assert default.row_count == rows
    assert default.fields is not None
    assert [field.name for field in default.fields] == ["id", "name", "loyalty_points"]
    with pytest.raises(DataValidationError) as error:
        adapter.read(operation, freeze_mode, paths)
    assert str(error.value) == (
        "In schema `corpus`: In Table: `versions` Column: `loyalty_points` . "
        "Contract on `columns` with `contract_mode=freeze` is violated. "
        "Can't add table column `loyalty_points` to table `versions` because `columns` are frozen."
    )
    reversed_ = adapter.describe(adapter.read(operation, freeze_mode, paths[::-1]))
    assert reversed_.row_count == rows
    assert reversed_.fields is not None
    assert [field.name for field in reversed_.fields] == ["id", "name", "loyalty_points"]


@pytest.mark.parametrize(
    "mode",
    [
        "filesystem_freeze_columns",
        "filesystem_freeze_data_type",
        "filesystem_freeze_columns_and_data_type",
        "filesystem_evolve_columns_freeze_data_type",
        "filesystem_evolve_all",
    ],
)
def test_filesystem_contract_reads_back_parquet(mode: str) -> None:
    paths = [str(ROOT / "fixtures/add_nullable_int64_column" / f"{version}.parquet") for version in ("v1", "v0")]
    adapter = dlt_adapter.DltAdapter()
    observation = adapter.describe(adapter.read("append_to_table", mode, paths))
    assert observation.row_count == 12
    assert observation.fields is not None
    assert [(field.name, field.native_type) for field in observation.fields] == [
        ("id", "int64"),
        ("name", "string"),
        ("loyalty_points", "int64"),
    ]
