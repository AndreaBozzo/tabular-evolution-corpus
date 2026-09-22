"""Committed observations are valid, reproducible with the pinned engines, and
the README tables are generated from them."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any

import pytest

from adapters import REGISTRY, load_adapter
from adapters.report import _relative, latest_results, main
from adapters.runner import CorpusIdentity, check_record, current_platform, observe, read_results, result_validator
from conftest import ROOT
from tabular_evolution import __version__
from test_adapters import ENGINE_MODULE

RESULTS = latest_results(ROOT)
FILES = sorted(RESULTS.glob("*.jsonl")) if RESULTS else []


def test_there_are_results_for_every_adapter() -> None:
    assert RESULTS is not None
    assert [p.stem for p in FILES] == sorted(REGISTRY)


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_committed_records_are_valid_and_from_one_run(path: Path) -> None:
    records = read_results(path)
    validator = result_validator(ROOT)
    assert records and all(check_record(r, validator) == [] for r in records)
    run = {(r["corpus_version"], r["corpus_revision"], r["adapter"], r["adapter_version"], r["platform"]) for r in records}
    assert len(run) == 1
    assert records[0]["corpus_version"] == path.parent.name
    assert records[0]["adapter"] == path.stem


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_committed_records_reproduce_with_the_installed_engine(path: Path) -> None:
    records = read_results(path)
    first = records[0]
    if first["corpus_version"] != __version__:
        pytest.skip(f"results for corpus {first['corpus_version']}, checkout is {__version__}")
    if importlib.util.find_spec(ENGINE_MODULE[path.stem]) is None:
        pytest.skip(f"{ENGINE_MODULE[path.stem]} is not installed")
    adapter = load_adapter(path.stem)
    if adapter.version != first["adapter_version"]:
        pytest.skip(f"recorded with {first['adapter_version']}, installed {adapter.version}")
    # Compared across operating systems but not across CPU architectures:
    # Linux and Windows x86_64 reproduce each other's records exactly, while
    # on macOS arm64 PyArrow reads int64_to_float64 without the error it
    # raises on x86_64 (see README, Observations).
    here = current_platform()
    if here.split("-")[1] != first["platform"].split("-")[1]:
        pytest.skip(f"recorded on {first['platform']}, running on {here}")
    identity = CorpusIdentity(first["corpus_version"], first["corpus_revision"])
    fresh = [{**r, "platform": first["platform"]} for r in observe(ROOT, adapter, identity)]
    differing = [
        (old["scenario_id"], old["from"], old["to"], old["operation"], old["mode"], old["inputs"])
        for old, new in zip(records, fresh)
        if old != new
    ]
    assert len(fresh) == len(records)
    assert differing == []


def test_readme_tables_are_generated_from_the_results() -> None:
    assert main(["--root", str(ROOT), "--check"]) == 0


def test_readme_check_detects_a_stale_table(tmp_path: Path) -> None:
    for name in ("fixtures", "results"):
        shutil.copytree(ROOT / name, tmp_path / name)
    original = (ROOT / "README.md").read_bytes()
    stale = original.replace(b"| `add_nested_field` v0", b"| `add_nested_field` v9")
    assert stale != original
    (tmp_path / "README.md").write_bytes(stale)
    assert main(["--root", str(tmp_path), "--check"]) == 1
    assert main(["--root", str(tmp_path)]) == 0
    assert (tmp_path / "README.md").read_bytes() == original


def record(schema: list[tuple[str, str]] | None, inputs: list[str]) -> dict[str, Any]:
    if schema is None:
        return {"status": "error", "inputs": inputs}
    return {
        "status": "success",
        "inputs": inputs,
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
    alone = {"v0": record([("id", "int64"), ("x", "int32")], ["v0"]), "v1": record([("id", "int64"), ("x", "int64")], ["v1"])}
    assert _relative(record(combined, ["v0", "v1"]), alone) == expected


def test_a_combined_read_like_two_identical_versions_is_both() -> None:
    same = [("id", "int64"), ("s", "struct<a: int32 not null>")]
    alone = {"v0": record(same, ["v0"]), "v1": record([("id", "int64"), ("s", "struct<a: int32>")], ["v1"])}
    assert _relative(record(same, ["v0", "v1"]), alone) == "both"  # nullability is not compared
