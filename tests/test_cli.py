from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from conftest import ROOT, SCENARIO_IDS

from tabular_evolution.cli import main
from tabular_evolution.release import sha256


def test_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(ROOT), "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [line.split()[0] for line in lines] == SCENARIO_IDS


def test_validate(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(ROOT), "validate"]) == 0
    assert f"{len(SCENARIO_IDS)} scenarios checked, 0 with problems" in capsys.readouterr().out


def test_inspect_reports_observed_facts(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--root", str(ROOT), "inspect", "string_to_binary"]) == 0
    out = capsys.readouterr().out
    out.encode("ascii")
    report = json.loads(out)
    observed = report["transitions"][0]["observed"]
    assert observed["mutations"] == [
        {"kind": "change_type", "path": "payload", "old_type": "string", "new_type": "binary"}
    ]
    assert ["str", "café"] in [pair[0] for pair in observed["value_changes"]["payload"]]


def test_inspect_unknown_scenario() -> None:
    assert main(["--root", str(ROOT), "inspect", "no_such_scenario"]) == 2


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


@pytest.mark.parametrize("scenario_ids", [[], ["string_to_binary", "add_all_null_column"]])
def test_export_copies_fixtures_and_checksums(tmp_path: Path, scenario_ids: list[str]) -> None:
    dest = tmp_path / "vendored"
    argv = ["--root", str(ROOT), "export", str(dest), *scenario_ids]
    assert main(argv) == 0
    assert main(argv) == 0

    expected_scenarios = scenario_ids or SCENARIO_IDS
    expected_files = {
        path.relative_to(ROOT).as_posix()
        for name in expected_scenarios
        for path in (ROOT / "fixtures" / name).iterdir()
    }
    assert {path.relative_to(dest).as_posix() for path in (dest / "fixtures").glob("*/*")} == expected_files
    sums = (dest / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    assert len(sums) == len(expected_files)
    assert set(sums) == {f"{sha256(dest / name)}  {name}" for name in expected_files}
    assert (dest / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
    assert f"Git revision: {_git(ROOT, 'rev-parse', 'HEAD').decode().strip()}" in (dest / "README.txt").read_text()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert (dest / "README.txt").read_text().startswith(f"tabular-evolution-corpus {version}")


def test_export_preserves_committed_parquet_under_text_rule(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    (tmp_path / ".gitattributes").write_bytes(b"* text eol=lf\n")
    path = "fixtures/string_to_binary/v1.parquet"
    expected = (ROOT / path).read_bytes()
    dest = tmp_path / "vendored"
    assert main(["--root", str(ROOT), "export", str(dest), "string_to_binary"]) == 0
    _git(tmp_path, "add", ".")
    _git(
        tmp_path,
        "-c",
        "user.name=Corpus Test",
        "-c",
        "user.email=corpus@example.com",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "Vendor fixtures",
    )
    assert _git(tmp_path, "show", f"HEAD:vendored/{path}") == expected
    # The current fixtures have no CRLF bytes. Check the effective rule too.
    assert _git(tmp_path, "check-attr", "text", "--", f"vendored/{path}").strip().endswith(b": text: unset")


def test_export_rejects_unknown_scenario(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dest = tmp_path / "vendored"
    assert main(["--root", str(ROOT), "export", str(dest), "string_to_binary", "no_such_scenario"]) == 2
    assert "unknown scenario: no_such_scenario" in capsys.readouterr().err
    assert not dest.exists()


@pytest.mark.parametrize("entry", ["LICENSE", "unrelated.txt"])
def test_export_rejects_conflicts_before_writing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    entry: str,
) -> None:
    (tmp_path / entry).write_bytes(b"local content")
    assert main(["--root", str(ROOT), "export", str(tmp_path), "string_to_binary"]) == 2
    assert "destination contains a different entry" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == [tmp_path / entry]
    assert (tmp_path / entry).read_bytes() == b"local content"
