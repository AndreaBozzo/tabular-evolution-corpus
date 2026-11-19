"""The adapter runner: pinned corpus identity, closed (operation, mode) lists,
engine errors recorded, adapter bugs not, records validated before writing."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import ROOT

from adapters import runner
from adapters.base import Adapter, ColumnProfile, Observation, ResultField
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
from tabular_evolution import __version__

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
        return Observation(
            [ResultField("id", "int64", "I64", None), ResultField("x", None, "Weird", None)], len(result)
        )


def test_records_cover_every_declared_operation_and_mode() -> None:
    records = observe(ROOT, FakeAdapter(), IDENTITY, SCENARIOS)
    # per transition: read_each_version reads two versions, read_versions_together runs two modes in both orders
    assert len(records) == 3 * (2 + 2 * 2)
    first = [r for r in records if r["scenario_id"] == "add_all_null_column" and r["from"] == "v0"]
    assert [(r["operation"], r["mode"], r["inputs"]) for r in first] == [
        ("read_each_version", "default", ["v0"]),
        ("read_each_version", "default", ["v1"]),
        ("read_versions_together", "default", ["v0", "v1"]),
        ("read_versions_together", "default", ["v1", "v0"]),
        ("read_versions_together", "strict", ["v0", "v1"]),
        ("read_versions_together", "strict", ["v1", "v0"]),
    ]
    assert {(r["corpus_version"], r["corpus_revision"], r["adapter_version"], r["platform"]) for r in records} == {
        ("0.1.0", IDENTITY.revision, "0.0.1", current_platform())
    }
    assert re.fullmatch(r"(windows|linux|darwin)-(x86_64|arm64)", current_platform())


def test_engine_errors_are_recorded_with_relative_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # engines see the corpus root as their working directory regardless
    records = observe(ROOT, FakeAdapter(), IDENTITY, ["reorder_columns"])
    assert [r["status"] for r in records if r["mode"] != "strict"] == ["success"] * 4
    error, swapped = [r for r in records if r["mode"] == "strict"]
    assert error["status"] == "error"
    assert error["error_class"] == "builtins.ValueError"
    assert error["notes"] == ["strict refuses fixtures/reorder_columns/v0.parquet fixtures/reorder_columns/v1.parquet"]
    assert swapped["notes"] == [
        "strict refuses fixtures/reorder_columns/v1.parquet fixtures/reorder_columns/v0.parquet"
    ]
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


def test_phase_two_operations_keep_modes_and_operation_specific_outcomes() -> None:
    class PhaseTwo(Adapter):
        name = "phase_two"
        version = "0.0.1"
        modes = {
            "append_to_table": ("default", "merge"),
            "check_against_contract": ("default",),
            "profile_each_version": ("default",),
        }

        def read(self, operation: str, mode: str, paths: list[str]) -> Any:
            assert all(Path(p).is_file() for p in paths)
            return operation, mode, paths

        def describe(self, result: Any) -> Observation:
            operation, _, _ = result
            if operation == "append_to_table":
                return Observation([ResultField("id", "int64", "BIGINT", None)], 12)
            if operation == "check_against_contract":
                return Observation(None, None, exit_code=1, findings=["Contract tool says invalid: changed type"])
            return Observation(None, None, column_profiles=[ColumnProfile("id", "int64", "integer", 0.0)])

    records = observe(ROOT, PhaseTwo(), IDENTITY, ["reorder_columns"])
    assert [(r["operation"], r["mode"], r["inputs"]) for r in records] == [
        ("append_to_table", "default", ["v0", "v1"]),
        ("append_to_table", "default", ["v1", "v0"]),
        ("append_to_table", "merge", ["v0", "v1"]),
        ("append_to_table", "merge", ["v1", "v0"]),
        ("check_against_contract", "default", ["v0", "v1"]),
        ("check_against_contract", "default", ["v1", "v0"]),
        ("profile_each_version", "default", ["v0"]),
        ("profile_each_version", "default", ["v1"]),
    ]
    assert {r["schema_version"] for r in records} == {"1.3"}
    assert all(r["engine_schema_hints"] is None for r in records)
    assert records[4]["exit_code"] == 1
    assert records[4]["findings"] == ["Contract tool says invalid: changed type"]
    assert records[4]["status"] == "success"
    assert records[6]["column_profiles"] == [
        {"name": "id", "inferred_type": "int64", "native_type": "integer", "null_ratio": 0.0}
    ]


def test_phase_two_adapter_cannot_report_a_read_shape_for_a_contract_check() -> None:
    class WrongShape(FakeAdapter):
        modes = {"check_against_contract": ("default",)}

    with pytest.raises(ValueError, match="invalid record"):
        observe(ROOT, WrongShape(), IDENTITY, ["reorder_columns"])


def test_inputs_are_the_transition_versions_in_either_order() -> None:
    record = observe(ROOT, FakeAdapter(), IDENTITY, ["reorder_columns"])[2]
    validator = result_validator(ROOT)
    assert check_record(record, validator) == []
    assert check_record({**record, "inputs": ["v1", "v0"]}, validator) == []
    assert check_record({**record, "inputs": ["v1", "v1"]}, validator) != []
    assert check_record({**record, "operation": "read_each_version", "inputs": ["v2"]}, validator) != []


def test_results_round_trip_as_ascii_json_lines(tmp_path: Path) -> None:
    records = observe(ROOT, FakeAdapter(), IDENTITY, SCENARIOS)
    records[0]["notes"] = ["datetime[μs]"]
    path = write_results(tmp_path / "fake.jsonl", records)
    raw = path.read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw and raw.count(b"\n") == len(records)
    assert read_results(path) == records


def test_result_writer_rejects_nonfinite_numbers(tmp_path: Path) -> None:
    records = observe(ROOT, FakeAdapter(), IDENTITY, ["reorder_columns"])
    records[0]["column_profiles"] = [
        {"name": "x", "inferred_type": "double", "native_type": "Float64", "null_ratio": float("nan")}
    ]
    with pytest.raises(ValueError, match="Out of range float values"):
        write_results(tmp_path / "invalid.jsonl", records)


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
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "fixtures").mkdir()
    (repo / "fixtures" / "a.txt").write_bytes(b"a\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "corpus")
    git(repo, "tag", "v9.9.9")
    return repo


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
    with pytest.raises(CorpusError, match="differs from v9.9.9"):
        corpus_identity(repo, "9.9.9", allow_dirty=True)


@pytest.mark.parametrize("change", ["unstaged", "staged", "untracked", "hidden", "ignored"])
def test_dirty_mode_still_refuses_modified_fixtures(repo: Path, change: str) -> None:
    path = repo / "fixtures/a.txt"
    if change == "hidden":
        git(repo, "update-index", "--assume-unchanged", "fixtures/a.txt")
    if change == "ignored":
        (repo / ".gitignore").write_bytes(b"fixtures/extra.parquet\n")
    if change in {"untracked", "ignored"}:
        path = repo / "fixtures/extra.parquet"
    path.write_bytes(b"changed\n")
    if change == "staged":
        git(repo, "add", "fixtures/a.txt")
    with pytest.raises(CorpusError, match="working tree fixtures/ differs"):
        corpus_identity(repo, "9.9.9", allow_dirty=True)


def test_main_refuses_outside_a_checkout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(tmp_path)]) == 2
    assert "refusing to run" in capsys.readouterr().err


@pytest.fixture
def runnable_repo(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (repo / "schema").mkdir()
    shutil.copyfile(ROOT / "schema/result.schema.json", repo / "schema/result.schema.json")
    for scenario in SCENARIOS:
        shutil.copytree(ROOT / "fixtures" / scenario, repo / "fixtures" / scenario)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "add scenarios and result schema")
    git(repo, "tag", f"v{__version__}")
    monkeypatch.setattr(runner, "load_adapter", lambda _: FakeAdapter())
    return repo


def test_output_keeps_runs_side_by_side(runnable_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output_dir = tmp_path / "local-results"
    monkeypatch.chdir(tmp_path)
    old = output_dir / "old.jsonl"
    new = output_dir / "new.jsonl"
    assert main(["fake", "--root", str(runnable_repo), "--output", "local-results/old.jsonl"]) == 0
    old_bytes = old.read_bytes()
    monkeypatch.setattr(FakeAdapter, "version", "0.0.2")
    assert main(["fake", "--root", str(runnable_repo), "--output", str(new)]) == 0
    assert old.read_bytes() == old_bytes
    assert {r["adapter_version"] for r in read_results(old)} == {"0.0.1"}
    assert {r["adapter_version"] for r in read_results(new)} == {"0.0.2"}
    assert not (runnable_repo / "results").exists()


def test_dirty_run_marks_each_record(runnable_repo: Path) -> None:
    (runnable_repo / "local.txt").write_bytes(b"local\n")
    output = runnable_repo / "local.jsonl"
    assert main(["fake", "--root", str(runnable_repo), "--allow-dirty", "--output", str(output)]) == 0
    head = git(runnable_repo, "rev-parse", "HEAD")
    assert {r["corpus_revision"] for r in read_results(output)} == {f"{head}-dirty"}


@pytest.mark.parametrize("explicit", [False, True])
def test_dirty_run_cannot_write_into_results(
    runnable_repo: Path, capsys: pytest.CaptureFixture[str], explicit: bool
) -> None:
    (runnable_repo / "local.txt").write_bytes(b"local\n")
    args = ["fake", "--root", str(runnable_repo), "--allow-dirty"]
    if explicit:
        args += ["--output", str(runnable_repo / "results/local.jsonl")]
    with pytest.raises(SystemExit, match="2"):
        main(args)
    assert "dirty runs require --output outside results/" in capsys.readouterr().err
    assert not (runnable_repo / "results").exists()


def test_scenarios_limits_the_run(runnable_repo: Path) -> None:
    output = runnable_repo / "local.jsonl"
    assert main(["fake", "--root", str(runnable_repo), "--output", str(output), "--scenarios", "reorder_columns"]) == 0
    records = read_results(output)
    assert len(records) == 6
    assert {r["scenario_id"] for r in records} == {"reorder_columns"}


def test_filtered_run_cannot_write_into_results(runnable_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["fake", "--root", str(runnable_repo), "--scenarios", "reorder_columns"])
    assert "filtered runs require --output outside results/" in capsys.readouterr().err
    assert not (runnable_repo / "results").exists()


def test_unknown_scenario_is_an_error(runnable_repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output = runnable_repo / "local.jsonl"
    with pytest.raises(SystemExit, match="2"):
        main(["fake", "--root", str(runnable_repo), "--output", str(output), "--scenarios", "reorder_columns", "typo"])
    assert "unknown scenarios: typo" in capsys.readouterr().err
    assert not output.exists()
