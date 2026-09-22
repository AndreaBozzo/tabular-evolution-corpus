"""Each adapter runs every declared (operation, mode) on every transition.

These tests check that an adapter reports faithfully what its engine did.
They do not assert what an engine ought to do with a transition: that is the
observation itself. Engines other than PyArrow come from the `adapters`
dependency group; their tests skip when the engine is absent.
"""

from __future__ import annotations

from functools import cache
from typing import Any, Callable

import pytest

from adapters import REGISTRY, load_adapter
from adapters.base import OPERATIONS
from adapters.runner import CorpusIdentity, observe
from conftest import ROOT, SCENARIO_IDS, load

IDENTITY = CorpusIdentity("0.1.0", "0" * 40)
ENGINE_MODULE = {"duckdb": "duckdb", "polars": "polars", "pyarrow_dataset": "pyarrow"}


@cache
def records(name: str) -> list[dict[str, Any]]:
    return observe(ROOT, load_adapter(name), IDENTITY)


def adapter_records(name: str) -> list[dict[str, Any]]:
    pytest.importorskip(ENGINE_MODULE[name])
    return records(name)


def test_every_adapter_names_its_engine_module() -> None:
    assert set(ENGINE_MODULE) == set(REGISTRY)


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_every_declared_operation_and_mode_runs_on_every_transition(name: str) -> None:
    seen = adapter_records(name)
    adapter = load_adapter(name)
    assert adapter.name == name
    assert set(adapter.modes) <= set(OPERATIONS)
    expected = []
    for sid in SCENARIO_IDS:
        for t in load(sid).manifest["transitions"]:
            for operation in OPERATIONS:
                for mode in adapter.modes.get(operation, ()):
                    inputs = [[t["from"]], [t["to"]]] if operation == "read_each_version" else [[t["from"], t["to"]]]
                    expected += [(sid, t["from"], t["to"], operation, mode, i) for i in inputs]
    assert [(r["scenario_id"], r["from"], r["to"], r["operation"], r["mode"], r["inputs"]) for r in seen] == expected


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_error_records_come_from_the_engine(name: str) -> None:
    """An exception from Python itself (TypeError, KeyError, ...) in `read`
    would be an adapter bug recorded as an engine answer."""
    errors = [r["error_class"] for r in adapter_records(name) if r["status"] == "error"]
    assert [e for e in errors if e.startswith("builtins.")] == []


# ---------------------------------------------------------------- PyArrow


def declared(scenario_id: str, version_id: str) -> list[dict[str, Any]]:
    version = load(scenario_id).versions[version_id]
    return [{"name": f["name"], "type": f["type"], "nullable": f["nullable"]} for f in version["schema"]]


def reported(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"name": f["name"], "type": f["type"], "nullable": f["nullable"]} for f in record["result_schema"]]


def test_pyarrow_reads_each_version_as_declared() -> None:
    """PyArrow is also the reference inspector, so a single file must read
    back exactly as its manifest declares it."""
    each = [r for r in adapter_records("pyarrow_dataset") if r["operation"] == "read_each_version"]
    assert each
    for r in each:
        (version_id,) = r["inputs"]
        assert r["status"] == "success", r
        assert reported(r) == declared(r["scenario_id"], version_id), r["scenario_id"]
        assert r["row_count"] == load(r["scenario_id"]).versions[version_id]["row_count"]


def test_pyarrow_default_dataset_takes_the_first_file_schema() -> None:
    together = [
        r
        for r in adapter_records("pyarrow_dataset")
        if r["operation"] == "read_versions_together" and r["mode"] == "default" and r["status"] == "success"
    ]
    assert together
    for r in together:
        assert reported(r) == declared(r["scenario_id"], r["from"]), r["scenario_id"]


# ---------------------------------------------------------------- DuckDB


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("BIGINT", "int64"),
        ("UTINYINT", "uint8"),
        ("VARCHAR", "string"),
        ("BLOB", "binary"),
        ("DECIMAL(11,4)", "decimal128(11, 4)"),
        ("TIMESTAMP", "timestamp[us]"),
        ("TIMESTAMP_NS", "timestamp[ns]"),
        ("INTEGER[]", "list<int32>"),
        ("STRUCT(sku VARCHAR, qty INTEGER)[]", "list<struct<sku: string, qty: int32>>"),
        ("MAP(VARCHAR, BIGINT)", "map<string, int64>"),
        ("TIMESTAMPTZ", None),
        ("STRUCT(a INTEGER, b TIMESTAMPTZ)", None),
        ("HUGEINT", None),
        ("INTEGER[3]", None),
        ('"NULL"', None),
    ],
)
def test_duckdb_type_mapping(sql_type: str, expected: str | None) -> None:
    duckdb = pytest.importorskip("duckdb")
    from adapters.duckdb_adapter import corpus_type

    assert corpus_type(duckdb.sqltype(sql_type)) == expected


@pytest.mark.parametrize("name", ["duckdb", "polars"])
def test_engines_without_nullability_report_names_and_row_counts_of_each_version(name: str) -> None:
    each = [r for r in adapter_records(name) if r["operation"] == "read_each_version"]
    assert each
    for r in each:
        (version_id,) = r["inputs"]
        assert r["status"] == "success", r
        assert [f["name"] for f in r["result_schema"]] == [f["name"] for f in declared(r["scenario_id"], version_id)]
        assert {f["nullable"] for f in r["result_schema"]} == {None}
        assert r["row_count"] == load(r["scenario_id"]).versions[version_id]["row_count"]


def names(fields: list[dict[str, Any]]) -> list[str]:
    return [f["name"] for f in fields]


@pytest.mark.parametrize(
    ("name", "mode"),
    [("duckdb", "union_by_name"), ("polars", "diagonal_relaxed"), ("pyarrow_dataset", "unified_permissive")],
)
def test_by_name_modes_return_the_union_of_columns(name: str, mode: str) -> None:
    """Defines the mode, not the engine's merit: matching by name keeps every
    column of either version."""
    merged = [r for r in adapter_records(name) if r["mode"] == mode and r["status"] == "success"]
    assert merged
    for r in merged:
        old, new = names(declared(r["scenario_id"], r["from"])), names(declared(r["scenario_id"], r["to"]))
        assert sorted(names(r["result_schema"])) == sorted(set(old) | set(new)), r["scenario_id"]


@pytest.mark.parametrize(("name", "mode"), [("duckdb", "positional"), ("polars", "default")])
def test_first_file_modes_return_the_first_file_columns(name: str, mode: str) -> None:
    together = [
        r
        for r in adapter_records(name)
        if r["operation"] == "read_versions_together" and r["mode"] == mode and r["status"] == "success"
    ]
    assert together
    for r in together:
        assert names(r["result_schema"]) == names(declared(r["scenario_id"], r["from"])), r["scenario_id"]


# ---------------------------------------------------------------- Polars


POLARS_TYPES: list[tuple[str, Callable[[Any], Any], str | None]] = [
    ("Int64", lambda pl: pl.Int64, "int64"),
    ("UInt16", lambda pl: pl.UInt16, "uint16"),
    ("Float16", lambda pl: pl.Float16, "halffloat"),
    ("Float32", lambda pl: pl.Float32, "float"),
    ("String", lambda pl: pl.String, "string"),
    ("Binary", lambda pl: pl.Binary, "binary"),
    ("Null", lambda pl: pl.Null, "null"),
    ("Datetime-us", lambda pl: pl.Datetime("us"), "timestamp[us]"),
    ("Datetime-ns-UTC", lambda pl: pl.Datetime("ns", "UTC"), "timestamp[ns, tz=UTC]"),
    ("Decimal", lambda pl: pl.Decimal(9, 4), "decimal128(9, 4)"),
    ("List", lambda pl: pl.List(pl.Int32), "list<int32>"),
    ("Struct", lambda pl: pl.Struct({"key": pl.String, "value": pl.Int64}), "struct<key: string, value: int64>"),
    (
        "List-of-Struct",
        lambda pl: pl.List(pl.Struct({"sku": pl.String, "qty": pl.Int32})),
        "list<struct<sku: string, qty: int32>>",
    ),
    ("Categorical", lambda pl: pl.Categorical(), None),
    ("Array", lambda pl: pl.Array(pl.Int32, 3), None),
    ("Int128", lambda pl: pl.Int128, None),
    ("Struct-with-Categorical", lambda pl: pl.Struct({"a": pl.Int32, "b": pl.Categorical()}), None),
]


@pytest.mark.parametrize(("dtype", "expected"), [t[1:] for t in POLARS_TYPES], ids=[t[0] for t in POLARS_TYPES])
def test_polars_type_mapping(dtype: Callable[[Any], Any], expected: str | None) -> None:
    pl = pytest.importorskip("polars")
    from adapters.polars_adapter import corpus_type

    assert corpus_type(dtype(pl)) == expected
