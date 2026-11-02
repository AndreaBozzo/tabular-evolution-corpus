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


def test_telemetry_is_disabled() -> None:
    additive_records()
    assert dlt.config["runtime.dlthub_telemetry"] is False


@pytest.mark.parametrize("scenario", ["nullable_to_required", "required_to_nullable"])
@pytest.mark.parametrize("versions", [("v0", "v1"), ("v1", "v0")])
def test_data_type_freeze_covers_nullability_in_both_orders(scenario: str, versions: tuple[str, str]) -> None:
    from dlt.common.schema.exceptions import DataValidationError

    paths = [str(ROOT / "fixtures" / scenario / f"{version}.parquet") for version in versions]
    with pytest.raises(DataValidationError, match="Can't evolve column type"):
        dlt_adapter.DltAdapter().read("append_to_table", "freeze_data_type", paths)


@pytest.mark.parametrize("mode", ["default", "freeze_columns"])
def test_each_call_cleans_up_pipeline_and_database(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # dlt's nested job paths can exceed Windows' path limit under pytest's
    # per-test directory. Keep the parent as short as the adapter's own temp.
    with TemporaryDirectory(prefix="tec-test-") as parent:

        def temporary_pipeline(prefix: str) -> TemporaryDirectory[str]:
            return TemporaryDirectory(prefix=prefix, dir=parent)

        monkeypatch.setattr(dlt_adapter, "TemporaryDirectory", temporary_pipeline)
        paths = [str(ROOT / "fixtures/add_nullable_int64_column" / f"{version}.parquet") for version in ("v0", "v1")]
        if mode == "default":
            dlt_adapter.DltAdapter().read("append_to_table", mode, paths)
        else:
            from dlt.common.schema.exceptions import DataValidationError

            with pytest.raises(DataValidationError):
                dlt_adapter.DltAdapter().read("append_to_table", mode, paths)
        assert list(Path(parent).iterdir()) == []
