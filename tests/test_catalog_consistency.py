"""The catalog is a faithful, reproducible, viewer-friendly index of the manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.types as pat
import pytest

from conftest import FIXTURES, ROOT, SCENARIO_IDS, load
from tabular_evolution.catalog import CATALOG_PATH, CATALOG_SCHEMA, build_catalog, catalog_row, read_catalog, write_catalog
from tabular_evolution.release import same_writer


@pytest.fixture(scope="module")
def catalog() -> pa.Table:
    return read_catalog(ROOT / CATALOG_PATH)


def test_catalog_has_the_declared_schema(catalog: pa.Table) -> None:
    assert catalog.schema.equals(CATALOG_SCHEMA)


def test_catalog_rows_match_manifests_one_to_one(catalog: pa.Table) -> None:
    rows = catalog.to_pylist()
    assert [r["scenario_id"] for r in rows] == SCENARIO_IDS
    for row in rows:
        assert row == catalog_row(load(row["scenario_id"]).manifest)


def test_catalog_references_existing_files(catalog: pa.Table) -> None:
    for row in catalog.to_pylist():
        for key in ("base_file", "final_file", "manifest_path"):
            path = ROOT / row[key]
            assert path.is_file(), row[key]
            assert path.resolve().is_relative_to(FIXTURES.resolve())
        assert len(row["version_ids"]) == row["version_count"] == len(row["row_counts"])


def test_catalog_json_columns_parse(catalog: pa.Table) -> None:
    for row in catalog.to_pylist():
        manifest = load(row["scenario_id"]).manifest
        assert json.loads(row["base_schema_json"]) == manifest["versions"][0]["schema"]
        assert json.loads(row["final_schema_json"]) == manifest["versions"][-1]["schema"]
        assert json.loads(row["transitions_json"]) == manifest["transitions"]


def test_catalog_uses_only_widely_supported_types() -> None:
    """Scalars and lists of scalars: rendered natively by the Hugging Face
    viewer, pandas, Polars and DuckDB. No structs, maps, unions or dictionaries."""
    for field in CATALOG_SCHEMA:
        t = field.type
        if pat.is_list(t):
            t = t.value_type
        assert pat.is_string(t) or pat.is_int64(t) or pat.is_boolean(t), field


def test_catalog_rebuild_is_reproducible(tmp_path: Path, catalog: pa.Table) -> None:
    rebuilt = write_catalog(ROOT, tmp_path / "scenarios.parquet")
    assert read_catalog(rebuilt).equals(catalog)
    assert build_catalog(ROOT).equals(catalog)
    if same_writer(ROOT):
        assert rebuilt.read_bytes() == (ROOT / CATALOG_PATH).read_bytes()
