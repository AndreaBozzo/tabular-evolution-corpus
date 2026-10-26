"""The dataprof adapter reports the check tool's exit code and findings, and
each version's profile, without reading a verdict into either."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

dataprof = pytest.importorskip("dataprof")

from adapters import dataprof_adapter
from adapters.runner import CorpusIdentity, observe
from conftest import ROOT

IDENTITY = CorpusIdentity("0.2.0", "0" * 40)


def records(scenario_id: str) -> list[dict]:
    return observe(ROOT, dataprof_adapter.DataprofAdapter(), IDENTITY, [scenario_id])


def check(scenario_id: str, mode: str, inputs: list[str]) -> dict:
    return next(r for r in records(scenario_id) if r["mode"] == mode and r["inputs"] == inputs)


def test_closed_schema_reports_the_added_column_in_one_order_and_the_removed_one_in_the_other() -> None:
    forward = check("add_nullable_int64_column", "closed_schema", ["v0", "v1"])
    reversed_ = check("add_nullable_int64_column", "closed_schema", ["v1", "v0"])
    assert (forward["status"], forward["exit_code"]) == ("success", 1)
    assert forward["findings"] == [
        "failed: forbid_added_column [loyalty_points]: the report profiled a column the baseline's source did not have",
        "diagnostic: column_added [loyalty_points]: the report profiled a column the baseline did not find in its "
        "source, so the baseline has nothing to measure its change from",
    ]
    assert reversed_["exit_code"] == 1
    assert [f.split(":")[1].strip() for f in reversed_["findings"] if f.startswith("failed")] == [
        "forbid_removed_column [loyalty_points]"
    ]


def test_a_type_change_is_a_diagnostic_beside_a_zero_exit_code() -> None:
    record = check("int64_to_float64", "closed_schema", ["v0", "v1"])
    assert record["exit_code"] == 0
    assert record["findings"] == [
        "diagnostic: column_type_changed [measurement]: this column holds values on both sides, and its inferred "
        "type differs (baseline integer, report float)"
    ]


def test_a_baseline_without_rows_leaves_the_null_increase_unevaluated() -> None:
    record = check("empty_then_populated", "closed_schema_null_increase", ["v0", "v1"])
    assert record["exit_code"] == 2
    assert record["findings"] and all(f.startswith("not_evaluated: max_null_percentage_increase") for f in record["findings"])


def test_profiles_record_native_types_and_null_ratios() -> None:
    profiles = {
        r["inputs"][0]: r["column_profiles"]
        for r in records("empty_then_populated")
        if r["operation"] == "profile_each_version"
    }
    assert {p["null_ratio"] for p in profiles["v0"]} == {None}
    label = next(p for p in profiles["v1"] if p["name"] == "label")
    assert label == {"name": "label", "inferred_type": None, "native_type": "String", "null_ratio": 0.8}


def test_a_check_the_tool_cannot_run_is_an_error(tmp_path: Path) -> None:
    adapter = dataprof_adapter.DataprofAdapter()
    with pytest.raises(dataprof_adapter.CheckToolError, match="missing.parquet"):
        adapter.read("check_against_contract", "closed_schema", [str(ROOT / "fixtures/reorder_columns/v0.parquet"), str(tmp_path / "missing.parquet")])


def test_each_check_cleans_up_its_baseline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def temporary(*args: object, **kwargs: object) -> TemporaryDirectory[str]:
        return TemporaryDirectory(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(dataprof_adapter, "TemporaryDirectory", temporary)
    records("reorder_columns")
    assert list(tmp_path.iterdir()) == []
