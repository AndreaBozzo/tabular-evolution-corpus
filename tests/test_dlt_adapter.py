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
    assert dlt_adapter.DltAdapter.modes == {"append_to_table": ("default", "freeze_columns", "freeze_data_type")}


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


def test_records_follow_the_result_schema() -> None:
    validator = result_validator(ROOT)
    for record in additive_records():
        assert check_record(record, validator) == []


def test_bookkeeping_columns_are_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_ID", "true")
    monkeypatch.setenv("NORMALIZE__PARQUET_NORMALIZER__ADD_DLT_LOAD_ID", "true")
    paths = [str(ROOT / "fixtures/add_nullable_int64_column" / f"{version}.parquet") for version in ("v0", "v1")]
    adapter = dlt_adapter.DltAdapter()
    result = adapter.read("append_to_table", "default", paths)
    assert {name for name, *_ in result.columns} >= {"_dlt_id", "_dlt_load_id"}
    observation = adapter.describe(result)
    assert observation.fields is not None
    assert {field.name for field in observation.fields} == {"id", "name", "loyalty_points"}
    assert observation.row_count == 12


def test_telemetry_is_disabled() -> None:
    from dlt.common.runtime.run_context import active

    additive_records()
    assert active().runtime_config.dlthub_telemetry is False


@pytest.mark.parametrize("scenario", ["nullable_to_required", "required_to_nullable"])
@pytest.mark.parametrize("versions", [("v0", "v1"), ("v1", "v0")])
def test_data_type_freeze_covers_nullability_in_both_orders(scenario: str, versions: tuple[str, str]) -> None:
    from dlt.common.schema.exceptions import DataValidationError

    paths = [str(ROOT / "fixtures" / scenario / f"{version}.parquet") for version in versions]
    with pytest.raises(DataValidationError, match="Can't evolve column type"):
        dlt_adapter.DltAdapter().read("append_to_table", "freeze_data_type", paths)


@pytest.mark.parametrize(
    ("scenario", "mode", "versions", "exception"),
    [
        ("add_nullable_int64_column", "default", ("v0", "v1"), None),
        ("add_nullable_int64_column", "freeze_columns", ("v0", "v1"), "DataValidationError"),
        ("add_required_column", "default", ("v1", "v0"), "ArrowInvalid"),
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
