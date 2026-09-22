"""The adapter result record: well-formed, observation-only, separate from manifests."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from conftest import ROOT
from tabular_evolution.validate import load_json
from test_manifest_schema import POLICY_WORDS

SCHEMA = load_json(ROOT / "schema" / "result.schema.json")
VALIDATOR = Draft202012Validator(SCHEMA)

SUCCESS: dict[str, Any] = {
    "schema_version": "1.1",
    "corpus_version": "0.1.0",
    "corpus_revision": "6d928a8c2b0b91adc8d8896c8cc6b7cc7488f800",
    "scenario_id": "timestamp_naive_to_utc",
    "from": "v0",
    "to": "v1",
    "inputs": ["v0", "v1"],
    "adapter": "duckdb",
    "adapter_version": "1.5.5",
    "platform": "linux-x86_64",
    "operation": "read_versions_together",
    "mode": "union_by_name",
    "status": "success",
    "result_schema": [
        {"name": "id", "type": "int64", "native_type": "BIGINT", "nullable": None},
        {"name": "event_time", "type": None, "native_type": "TIMESTAMP WITH TIME ZONE", "nullable": None},
    ],
    "row_count": 10,
    "error_class": None,
    "notes": [],
}

ERROR: dict[str, Any] = {
    **SUCCESS,
    "adapter": "polars",
    "adapter_version": "1.44.2",
    "mode": "diagonal_relaxed",
    "status": "error",
    "result_schema": None,
    "row_count": None,
    "error_class": "polars.exceptions.SchemaError",
    "notes": ["failed to determine supertype of datetime[us] and datetime[us, UTC]"],
}


def with_changes(record: dict[str, Any], **changes: Any) -> dict[str, Any]:
    out = copy.deepcopy(record)
    out.update(changes)
    return out


def test_schema_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("record", [SUCCESS, ERROR], ids=["success", "error"])
def test_examples_validate(record: dict[str, Any]) -> None:
    assert list(VALIDATOR.iter_errors(record)) == []


def test_read_each_version_reads_one_input() -> None:
    one = with_changes(SUCCESS, operation="read_each_version", mode="default", inputs=["v1"])
    assert list(VALIDATOR.iter_errors(one)) == []


@pytest.mark.parametrize(
    "record",
    [
        with_changes(SUCCESS, error_class="duckdb.ConversionException"),
        with_changes(SUCCESS, row_count=None),
        with_changes(SUCCESS, result_schema=None),
        with_changes(ERROR, result_schema=[]),
        with_changes(ERROR, row_count=0),
        with_changes(ERROR, error_class=None),
        with_changes(SUCCESS, compatible=True),
        with_changes(SUCCESS, inputs=["v0"]),
        with_changes(SUCCESS, inputs=["v0", "v1", "v1"]),
        with_changes(SUCCESS, operation="read_each_version", inputs=["v0", "v1"]),
        with_changes(SUCCESS, operation="read_union"),
        with_changes(SUCCESS, mode="union-by-name"),
        with_changes(SUCCESS, corpus_revision="6d928a8"),
        with_changes(SUCCESS, corpus_version="v0.1.0"),
        with_changes(SUCCESS, schema_version="1.0"),
        with_changes(SUCCESS, platform="Linux x86_64"),
        with_changes(SUCCESS, result_schema=[{"name": "id", "type": "int64", "nullable": True}]),
        with_changes(SUCCESS, notes=None),
    ],
    ids=[
        "success-with-error-class",
        "success-without-row-count",
        "success-without-schema",
        "error-with-schema",
        "error-with-row-count",
        "error-without-class",
        "verdict-field",
        "together-with-one-input",
        "together-with-three-inputs",
        "each-with-two-inputs",
        "unknown-operation",
        "mode-not-an-identifier",
        "short-revision",
        "version-with-prefix",
        "earlier-record-format",
        "platform-not-os-arch",
        "field-without-native-type",
        "notes-not-a-list",
    ],
)
def test_tampered_records_are_rejected(record: dict[str, Any]) -> None:
    assert list(VALIDATOR.iter_errors(record)) != []


def test_records_have_no_policy_vocabulary() -> None:
    """Result field names and closed values are observations, like manifest
    keys (DESIGN.md section 2). Engine error text quoted in `notes` is the
    engine's wording, not the corpus's, and is not checked."""
    names: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key in ("properties", "$defs"):
                names.extend(node.get(key, {}))
            names.extend(v for v in node.get("enum", []) if isinstance(v, str))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(SCHEMA)
    assert "status" in names and "success" in names
    assert [n for n in names if POLICY_WORDS.search(n)] == []
