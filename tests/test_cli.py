from __future__ import annotations

import json

import pytest

from conftest import ROOT, SCENARIO_IDS
from tabular_evolution.cli import main


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
