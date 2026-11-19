"""Committed observations are valid, reproducible with the pinned engines, and
the observations doc is generated from them."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT
from test_adapters import ENGINE_MODULE

from adapters import REGISTRY, load_adapter
from adapters.report import _order_dependent, _relative, latest_results, main, render
from adapters.runner import (
    RECORD_VERSION,
    CorpusError,
    CorpusIdentity,
    check_record,
    current_platform,
    observe,
    read_recorded_results,
    read_results,
    record_key,
    result_validator,
    write_results,
)
from tabular_evolution import __version__

RESULTS = latest_results(ROOT)
FILES = sorted(RESULTS.glob("*.jsonl")) if RESULTS else []


def test_there_are_results_for_every_adapter() -> None:
    assert RESULTS is not None
    assert [p.stem for p in FILES] == sorted(REGISTRY)


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_committed_records_are_valid_and_from_one_run(path: Path) -> None:
    records = read_recorded_results(path)
    validator = result_validator(ROOT)
    assert records and all(check_record(r, validator) == [] for r in records)
    run = {
        (r["corpus_version"], r["corpus_revision"], r["adapter"], r["adapter_version"], r["platform"]) for r in records
    }
    assert len(run) == 1
    assert records[0]["corpus_version"] == path.parent.name
    assert records[0]["adapter"] == path.stem


@pytest.mark.parametrize("path", sorted((ROOT / "results").glob("*/*.jsonl")))
def test_recorded_files_only_contain_clean_records(path: Path) -> None:
    assert read_recorded_results(path)


def test_recorded_reader_rejects_dirty_file(tmp_path: Path) -> None:
    records = read_results(FILES[0])
    records[-1]["corpus_revision"] += "-dirty"
    path = write_results(tmp_path / "dirty.jsonl", records)
    with pytest.raises(CorpusError, match="records from a dirty checkout"):
        read_recorded_results(path)


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_committed_records_reproduce_with_the_installed_engine(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    records = read_results(path)
    first = records[0]
    if first["corpus_version"] != __version__:
        pytest.skip(f"results use fixtures from tag v{first['corpus_version']}; checkout is v{__version__}")
    if importlib.util.find_spec(ENGINE_MODULE[path.stem]) is None:
        pytest.skip(f"{ENGINE_MODULE[path.stem]} is not installed")
    adapter = load_adapter(path.stem)
    if adapter.version != first["adapter_version"]:
        pytest.skip(f"recorded with {first['adapter_version']}, installed {adapter.version}")
    # Compared across operating systems but not across CPU architectures:
    # Linux and Windows x86_64 reproduce each other's records exactly, while
    # on macOS arm64 PyArrow reads int64_to_float64 without the error it
    # raises on x86_64 (see docs/OBSERVATIONS.md).
    here = current_platform()
    if here.split("-")[1] != first["platform"].split("-")[1]:
        pytest.skip(f"recorded on {first['platform']}, running on {here}")
    identity = CorpusIdentity(first["corpus_version"], first["corpus_revision"])
    legacy = first["schema_version"] == "1.2" and first["schema_version"] != RECORD_VERSION
    if legacy:
        # Replay the operation and mode matrix that was published with format 1.2.
        recorded_modes = {(r["operation"], r["mode"]) for r in records}
        monkeypatch.setattr(
            adapter,
            "modes",
            {
                operation: tuple(mode for mode in modes if (operation, mode) in recorded_modes)
                for operation, modes in adapter.modes.items()
            },
        )
    fresh = [{**r, "platform": first["platform"]} for r in observe(ROOT, adapter, identity)]
    if legacy:
        for record in fresh:
            record.pop("engine_schema_hints")
            record["schema_version"] = first["schema_version"]
    differing = [record_key(old) for old, new in zip(records, fresh, strict=False) if old != new]
    assert len(fresh) == len(records)
    assert differing == []


def test_observation_tables_are_generated_from_the_results() -> None:
    assert main(["--root", str(ROOT), "--check"]) == 0


def test_observation_tables_include_replace_and_merge(tmp_path: Path) -> None:
    source = next(p for p in FILES if p.stem == "dlt")
    sid = "add_nullable_int64_column"
    shutil.copytree(ROOT / "fixtures" / sid, tmp_path / "fixtures" / sid)
    append = [
        r
        for r in read_results(source)
        if r["scenario_id"] == sid and r["mode"] == "default" and r["operation"] == "append_to_table"
    ]
    assert append
    records = [
        {
            **record,
            "operation": operation,
            "schema_version": "1.3",
            "engine_schema_hints": {field["name"]: {} for field in record["result_schema"]},
        }
        for operation in ("append_to_table", "replace_table", "merge_into_table")
        for record in append
    ]
    assert all(check_record(record, result_validator(ROOT)) == [] for record in records)
    results_dir = tmp_path / "results"
    write_results(results_dir / "dlt.jsonl", records)
    report = render(tmp_path, results_dir)
    assert "**dlt replace table.**" in report
    assert "**dlt merge into table.**" in report


def test_observation_check_detects_a_stale_table(tmp_path: Path) -> None:
    for name in ("fixtures", "results", "docs"):
        shutil.copytree(ROOT / name, tmp_path / name)
    original = (ROOT / "docs" / "OBSERVATIONS.md").read_bytes()
    stale = original.replace(b"| `add_nested_field` v0", b"| `add_nested_field` v9")
    assert stale != original
    (tmp_path / "docs" / "OBSERVATIONS.md").write_bytes(stale)
    assert main(["--root", str(tmp_path), "--check"]) == 1
    assert main(["--root", str(tmp_path)]) == 0
    assert (tmp_path / "docs" / "OBSERVATIONS.md").read_bytes() == original


def test_report_refuses_dirty_records(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("fixtures", "results", "docs"):
        shutil.copytree(ROOT / name, tmp_path / name)
    (source, *_) = FILES
    path = tmp_path / source.relative_to(ROOT)
    records = read_results(path)
    records[-1]["corpus_revision"] += "-dirty"
    write_results(path, records)
    doc = tmp_path / "docs/OBSERVATIONS.md"
    original = doc.read_bytes()
    assert main(["--root", str(tmp_path)]) == 2
    assert "records from a dirty checkout" in capsys.readouterr().err
    assert doc.read_bytes() == original


def record(schema: list[tuple[str, str]] | None, inputs: list[str], rows: int = 2) -> dict[str, Any]:
    if schema is None:
        return {"status": "error", "inputs": inputs, "row_count": None}
    return {
        "status": "success",
        "inputs": inputs,
        "row_count": rows,
        "result_schema": [{"name": n, "type": t, "native_type": t.upper(), "nullable": None} for n, t in schema],
    }


@pytest.mark.parametrize(
    ("combined", "expected"),
    [
        ([("id", "int64"), ("x", "int32")], "v0"),
        ([("x", "int64"), ("id", "int64")], "v1"),  # column order is not compared
        ([("id", "int64"), ("x", "double")], "neither"),
        (None, "error"),
    ],
)
def test_combined_reads_are_classified_against_the_same_engine(combined: Any, expected: str) -> None:
    alone = {
        "v0": record([("id", "int64"), ("x", "int32")], ["v0"]),
        "v1": record([("id", "int64"), ("x", "int64")], ["v1"]),
    }
    assert _relative(record(combined, ["v0", "v1"]), alone) == expected


def test_a_combined_read_like_two_identical_versions_is_both() -> None:
    same = [("id", "int64"), ("s", "struct<a: int32 not null>")]
    alone = {"v0": record(same, ["v0"]), "v1": record([("id", "int64"), ("s", "struct<a: int32>")], ["v1"])}
    assert _relative(record(same, ["v0", "v1"]), alone) == "both"  # nullability is not compared


ALONE = {
    "v0": record([("id", "int64"), ("x", "int32")], ["v0"]),
    "v1": record([("id", "int64"), ("x", "int64")], ["v1"]),
}
V0, V1 = [("id", "int64"), ("x", "int32")], [("id", "int64"), ("x", "int64")]


@pytest.mark.parametrize(
    ("forward", "reversed_"),
    [
        (record(V0, ["v0", "v1"]), record(V1, ["v1", "v0"])),  # the first file wins in both orders
        (record(V1, ["v0", "v1"]), record(V1, ["v1", "v0"])),  # the same version wins in both orders
        (record([("x", "double")], ["v0", "v1"]), record([("x", "double")], ["v1", "v0"])),  # unified alike
        (record(None, ["v0", "v1"]), record(None, ["v1", "v0"])),  # fails in both orders
    ],
)
def test_a_result_that_follows_one_rule_does_not_depend_on_order(forward: Any, reversed_: Any) -> None:
    assert not _order_dependent(forward, reversed_, ALONE)


@pytest.mark.parametrize(
    ("forward", "reversed_"),
    [
        (record(V0, ["v0", "v1"]), record(None, ["v1", "v0"])),  # fails in one order only
        (record(V0, ["v0", "v1"], rows=2), record(V1, ["v1", "v0"], rows=1)),  # rows lost in one order
        (record([("x", "double")], ["v0", "v1"]), record([("x", "string")], ["v1", "v0"])),  # unified differently
    ],
)
def test_a_result_that_changes_with_the_order_is_detected(forward: Any, reversed_: Any) -> None:
    assert _order_dependent(forward, reversed_, ALONE)
