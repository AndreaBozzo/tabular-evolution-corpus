"""The catalog loads as documented in the README with each engine.

These engines are not dependencies of the corpus. Install them with
`uv sync --group compat`; each test is skipped when its engine is absent.
"""

from __future__ import annotations

import pytest

from conftest import ROOT, SCENARIO_IDS
from tabular_evolution.catalog import CATALOG_PATH

CATALOG = (ROOT / CATALOG_PATH).as_posix()


def test_pandas() -> None:
    pd = pytest.importorskip("pandas")
    df = pd.read_parquet(CATALOG)
    assert sorted(df["scenario_id"]) == SCENARIO_IDS
    assert list(df.loc[df["scenario_id"] == "add_all_null_column", "version_ids"].iloc[0]) == ["v0", "v1", "v2"]


def test_duckdb() -> None:
    duckdb = pytest.importorskip("duckdb")
    rows = duckdb.sql(
        f"""
        SELECT scenario_id, mutation_kinds
        FROM read_parquet('{CATALOG}')
        WHERE list_contains(mutation_kinds, 'change_type') AND category = 'numeric'
        ORDER BY scenario_id
        """
    ).fetchall()
    assert [r[0] for r in rows] == [
        "decimal_precision_increase",
        "decimal_scale_increase",
        "float64_to_int64_fractional",
        "float64_to_int64_integral",
        "int64_to_float64",
        "widen_int32_to_int64",
    ]
    assert rows[0][1] == ["change_type"]


def test_polars() -> None:
    pl = pytest.importorskip("polars")
    df = pl.read_parquet(CATALOG)
    assert df.height == len(SCENARIO_IDS)
    assert df.schema["tags"] == pl.List(pl.String)
    assert df.schema["row_counts"] == pl.List(pl.Int64)
