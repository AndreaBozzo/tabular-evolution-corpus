"""dataprof: profile each version, and check one version against a baseline
saved from the other.

| operation | mode | call |
| --- | --- | --- |
| profile_each_version | default | ``dataprof.profile(path)`` |
| check_against_contract | closed_schema | ``python -m dataprof.check`` with ``--forbid-removed-columns --forbid-added-columns`` |
| check_against_contract | closed_schema_null_increase | the same, plus ``--max-null-increase *=0`` |

The contract is derived from the first input the way dataprof documents a
baseline: ``dataprof.profile(first).save(path)``. The second input is then
checked by the command-line tool against that report with ``--json``, in a
fresh process, so the exit code is the tool's own. Findings are every check
that did not pass and every baseline diagnostic, in the tool's text layout
(``status: code [column]: message``); a diagnostic that names two types adds
them in its own words.

dataprof infers logical types (``Integer``, ``Float``, ``String``, ``Date``,
``Nested``) rather than physical ones, so no inferred type has a clear
equivalent in the corpus vocabulary and ``inferred_type`` is always null.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import dataprof

from .base import Adapter, ColumnProfile, Observation

CHECK_FLAGS = {
    "closed_schema": ["--forbid-removed-columns", "--forbid-added-columns"],
    "closed_schema_null_increase": ["--forbid-removed-columns", "--forbid-added-columns", "--max-null-increase", "*=0"],
}
#: Exit code 3: an argument, policy, source or baseline could not be read or used.
TOOL_ERROR = 3


class CheckToolError(Exception):
    """The check tool could not run the check (exit code 3); the message is the tool's own."""


@dataclass(frozen=True)
class ProfileResult:
    columns: list[dict[str, Any]]


@dataclass(frozen=True)
class CheckResult:
    exit_code: int
    gate: dict[str, Any]


def _finding(item: dict[str, Any], status: str) -> str:
    column = f" [{item['column']}]" if item.get("column") else ""
    text = f"{status}: {item['code']}{column}: {item['message']}"
    types = item.get("data_type")
    if isinstance(types, dict) and set(types) == {"a", "b"}:
        text += f" (baseline {types['a']}, report {types['b']})"
    return text


class DataprofAdapter(Adapter):
    name = "dataprof"
    modes = {
        "profile_each_version": ("default",),
        "check_against_contract": tuple(CHECK_FLAGS),
    }

    @property
    def version(self) -> str:
        return dataprof.__version__

    def read(self, operation: str, mode: str, paths: list[str]) -> ProfileResult | CheckResult:
        if operation == "profile_each_version" and mode == "default" and len(paths) == 1:
            report = json.loads(dataprof.profile(paths[0]).to_json())
            return ProfileResult(report["column_profiles"])
        if operation == "check_against_contract" and mode in CHECK_FLAGS and len(paths) == 2:
            first, second = paths
            with TemporaryDirectory(prefix="tec-dataprof-") as temp:
                baseline = Path(temp) / "baseline.json"
                dataprof.profile(first).save(str(baseline))
                done = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "dataprof.check",
                        second,
                        "--baseline",
                        str(baseline),
                        "--json",
                        *CHECK_FLAGS[mode],
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                )
            if done.returncode == TOOL_ERROR:
                # With --json the tool writes the error to stdout as {"error": {...}}.
                try:
                    message = json.loads(done.stdout)["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    message = (done.stdout + done.stderr).strip()
                raise CheckToolError(message)
            return CheckResult(done.returncode, json.loads(done.stdout))
        raise AssertionError(f"unsupported dataprof call: {operation}, {mode}, {paths}")

    def describe(self, result: ProfileResult | CheckResult) -> Observation:
        if isinstance(result, ProfileResult):
            profiles = [
                ColumnProfile(
                    column["name"],
                    None,
                    column["data_type"],
                    column["null_count"] / column["total_count"] if column["total_count"] else None,
                )
                for column in result.columns
            ]
            return Observation(None, None, column_profiles=profiles)
        findings = [_finding(check, check["status"]) for check in result.gate["checks"] if check["status"] != "passed"]
        findings += [
            _finding(item, "diagnostic") for item in (result.gate.get("baseline") or {}).get("diagnostics", [])
        ]
        return Observation(None, None, exit_code=result.exit_code, findings=findings)
