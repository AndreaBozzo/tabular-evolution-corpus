"""The adapter runner: pinned corpus identity, closed (operation, mode) lists,
engine errors recorded, adapter bugs not, records validated before writing."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from adapters.base import Adapter, Observation, ResultField
from adapters.runner import (
    CorpusError,
    CorpusIdentity,
    check_record,
    corpus_identity,
    current_platform,
    main,
    observe,
    read_results,
    result_validator,
    write_results,
)
from conftest import ROOT

IDENTITY = CorpusIdentity("0.1.0", "6d928a8c2b0b91adc8d8896c8cc6b7cc7488f800")
SCENARIOS = ["add_all_null_column", "reorder_columns"]  # three transitions


class FakeAdapter(Adapter):
    name = "fake"
    version = "0.0.1"
    modes = {"read_each_version": ("default",), "read_versions_together": ("default", "strict")}

    def read(self, operation: str, mode: str, paths: list[str]) -> Any:
        if mode == "strict":
            raise ValueError("strict refuses " + " ".join(paths))
        for p in paths:
            assert Path(p).is_file(), p  # relative to the corpus root
        return paths

    def describe(self, result: Any) -> Observation:
        return Observation([ResultField("id", "int64", "I64", None), ResultField("x", None, "Weird", None)], len(result))


def test_records_cover_every_declared_operation_and_mode() -> None:
    records = observe(ROOT, FakeAdapter(), IDENTITY, SCENARIOS)
    # per transition: read_each_version reads two versions, read_versions_together runs two modes
    assert len(records) == 3 * (2 + 2)
    first = [r for r in records if r["scenario_id"] == "add_all_null_column" and r["from"] == "v0"]
    assert [(r["operation"], r["mode"], r["inputs"]) for r in first] == [
        ("read_each_version", "default", ["v0"]),
        ("read_each_version", "default", ["v1"]),
        ("read_versions_together", "default", ["v0", "v1"]),
        ("read_versions_together", "strict", ["v0", "v1"]),
    ]
    assert {(r["corpus_version"], r["corpus_revision"], r["adapter_version"], r["platform"]) for r in records} == {
        ("0.1.0", IDENTITY.revision, "0.0.1", current_platform())
    }
    assert re.fullmatch(r"(windows|linux|darwin)-(x86_64|arm64)", current_platform())


def test_engine_errors_are_recorded_with_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # engines see the corpus root as their working directory regardless
    records = observe(ROOT, FakeAdapter(), IDENTITY, ["reorder_columns"])
    assert [r["status"] for r in records if r["mode"] != "strict"] == ["success"] * 3
    error = next(r for r in records if r["mode"] == "strict")
    assert error["status"] == "error"
    assert error["error_class"] == "builtins.ValueError"
    assert error["notes"] == ["strict refuses fixtures/reorder_columns/v0.parquet fixtures/reorder_columns/v1.parquet"]
    assert (error["result_schema"], error["row_count"]) == (None, None)


def test_success_records_note_types_without_a_corpus_equivalent() -> None:
    record = observe(ROOT, FakeAdapter(), IDENTITY, ["reorder_columns"])[0]
    assert record["status"] == "success"
    assert record["result_schema"][1] == {"name": "x", "type": None, "native_type": "Weird", "nullable": None}
    assert record["notes"] == ["x: Weird has no equivalent in the corpus type vocabulary"]


def test_an_adapter_bug_stops_the_run() -> None:
    class Broken(FakeAdapter):
        def describe(self, result: Any) -> Observation:
            raise NotImplementedError("unmapped type")

    with pytest.raises(NotImplementedError):
        observe(ROOT, Broken(), IDENTITY, ["reorder_columns"])


def test_an_invalid_record_stops_the_run() -> None:
    class BadMode(FakeAdapter):
        modes = {"read_each_version": ("Default",)}

    with pytest.raises(ValueError, match="invalid record"):
        observe(ROOT, BadMode(), IDENTITY, ["reorder_columns"])


def test_operations_are_a_closed_set() -> None:
    class Extra(FakeAdapter):
        modes = {"read_union": ("default",)}

    with pytest.raises(ValueError, match="unknown operations"):
        observe(ROOT, Extra(), IDENTITY, ["reorder_columns"])


def test_inputs_are_the_transition_versions_in_order() -> None:
    record = observe(ROOT, FakeAdapter(), IDENTITY, ["reorder_columns"])[2]
    validator = result_validator(ROOT)
    assert check_record(record, validator) == []
    assert check_record({**record, "inputs": ["v1", "v0"]}, validator) != []
    assert check_record({**record, "operation": "read_each_version", "inputs": ["v2"]}, validator) != []


def test_results_round_trip_as_ascii_json_lines(tmp_path: Path) -> None:
    records = observe(ROOT, FakeAdapter(), IDENTITY, SCENARIOS)
    records[0]["notes"] = ["datetime[μs]"]
    path = write_results(tmp_path / "fake.jsonl", records)
    raw = path.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw and raw.count(b"\n") == len(records)
    assert read_results(path) == records


# ---------------------------------------------------------------- corpus identity


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "core.autocrlf=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "fixtures" / "a.txt").write_bytes(b"a\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "corpus")
    git(tmp_path, "tag", "v9.9.9")
    return tmp_path


def test_identity_of_a_tagged_clean_checkout(repo: Path) -> None:
    assert corpus_identity(repo, "9.9.9") == CorpusIdentity("9.9.9", git(repo, "rev-parse", "HEAD"))


def test_identity_follows_head_while_fixtures_match_the_tag(repo: Path) -> None:
    (repo / "results.txt").write_bytes(b"r\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "results")
    head = git(repo, "rev-parse", "HEAD")
    assert head != git(repo, "rev-parse", "v9.9.9")
    assert corpus_identity(repo, "9.9.9").revision == head


def test_refuses_a_dirty_checkout(repo: Path) -> None:
    (repo / "untracked.txt").write_bytes(b"x\n")
    with pytest.raises(CorpusError, match="uncommitted or untracked"):
        corpus_identity(repo, "9.9.9")


def test_refuses_an_untagged_version(repo: Path) -> None:
    with pytest.raises(CorpusError, match="not tagged"):
        corpus_identity(repo, "9.9.10")


def test_refuses_fixtures_that_differ_from_the_tag(repo: Path) -> None:
    (repo / "fixtures" / "a.txt").write_bytes(b"changed\n")
    git(repo, "commit", "-q", "-am", "change a fixture")
    with pytest.raises(CorpusError, match="differs from v9.9.9"):
        corpus_identity(repo, "9.9.9")


def test_main_refuses_outside_a_checkout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(tmp_path)]) == 2
    assert "refusing to run" in capsys.readouterr().err
