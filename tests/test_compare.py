"""Compare observations independently of run metadata and file order."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT
from test_result_schema import SUCCESS

from adapters.compare import main
from adapters.runner import RECORD_KEY_FIELDS, write_results
from tabular_evolution.validate import load_json


def compare(tmp_path: Path, left: list[dict[str, Any]], right: list[dict[str, Any]]) -> int:
    a = write_results(tmp_path / "old.jsonl", left)
    b = write_results(tmp_path / "new.jsonl", right)
    return main([str(a), str(b)])


def test_metadata_only_changes_are_shown_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = [SUCCESS, {**SUCCESS, "inputs": ["v1", "v0"]}]
    after = [
        {
            **record,
            "adapter_version": "1.6.0",
            "corpus_version": "0.3.0",
            "corpus_revision": "a" * 40 + "-dirty",
            "platform": "windows-x86_64",
        }
        for record in reversed(before)
    ]
    assert compare(tmp_path, before, after) == 0
    output = capsys.readouterr().out
    assert "No observation changes." in output
    assert 'adapter_version: "1.5.5" -> "1.6.0"' in output
    for field in ("adapter_version", "corpus_version", "corpus_revision", "platform"):
        assert output.count(f"{field}:") == 1


def test_changed_outcome(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    error = {
        **SUCCESS,
        "status": "error",
        "result_schema": None,
        "row_count": None,
        "error_class": "_duckdb.ConversionException",
        "notes": ["Conversion Error"],
    }
    assert compare(tmp_path, [SUCCESS], [error]) == 1
    output = capsys.readouterr().out
    assert 'Changed {"adapter": "duckdb", "scenario_id": "timestamp_naive_to_utc"' in output
    assert 'status: "success" -> "error"' in output


def test_changed_result_field(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert compare(tmp_path, [SUCCESS], [{**SUCCESS, "row_count": 11}]) == 1
    output = capsys.readouterr().out
    assert "row_count: 10 -> 11" in output
    assert "status:" not in output


def test_changed_error_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    error = {
        **SUCCESS,
        "status": "error",
        "result_schema": None,
        "row_count": None,
        "error_class": "_duckdb.ConversionException",
        "notes": ["First engine message"],
    }
    assert compare(tmp_path, [error], [{**error, "notes": ["Second engine message"]}]) == 1
    assert 'notes: ["First engine message"] -> ["Second engine message"]' in capsys.readouterr().out


def test_one_sided_records_keep_the_input_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert compare(tmp_path, [SUCCESS], [{**SUCCESS, "inputs": ["v1", "v0"]}]) == 1
    output = capsys.readouterr().out
    assert 'Left only {"adapter":' in output
    assert 'Right only {"adapter":' in output
    assert '"inputs": ["v0", "v1"]' in output
    assert '"inputs": ["v1", "v0"]' in output


def test_duplicate_keys_are_an_input_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert compare(tmp_path, [SUCCESS, {**SUCCESS, "row_count": 11}], [SUCCESS]) == 2
    assert "duplicate record key" in capsys.readouterr().err


def test_record_key_documentation_matches_the_code() -> None:
    key = f"({', '.join(RECORD_KEY_FIELDS)})"
    assert key in load_json(ROOT / "schema/result.schema.json")["description"]
    assert key in (ROOT / "docs/OBSERVATIONS.md").read_text(encoding="utf-8")
