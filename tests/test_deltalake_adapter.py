"""The Delta adapter reports three real write modes and leaves no table files."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow.parquet as pq
import pytest

from adapters import deltalake_adapter
from adapters.runner import CorpusIdentity, observe
from conftest import ROOT

deltalake = pytest.importorskip("deltalake")


def test_additive_change_distinguishes_append_merge_and_overwrite() -> None:
    records = observe(ROOT, deltalake_adapter.DeltaLakeAdapter(), CorpusIdentity("0.2.0", "0" * 40), ["add_nullable_int64_column"])
    by_mode = {record["mode"]: record for record in records}
    assert by_mode["default"]["status"] == "error"
    assert by_mode["merge"]["status"] == "success"
    assert by_mode["merge"]["row_count"] == 12
    assert {field["name"] for field in by_mode["merge"]["result_schema"]} == {"id", "name", "loyalty_points"}
    assert by_mode["overwrite"]["status"] == "success"
    assert by_mode["overwrite"]["row_count"] == 6
    assert by_mode["merge"]["result_schema"][0]["native_type"] == 'PrimitiveType("long")'


def test_each_call_cleans_up_its_temporary_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def temporary_table(*args: object, **kwargs: object) -> TemporaryDirectory[str]:
        return TemporaryDirectory(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(deltalake_adapter, "TemporaryDirectory", temporary_table)
    observe(ROOT, deltalake_adapter.DeltaLakeAdapter(), CorpusIdentity("0.2.0", "0" * 40), ["add_nullable_int64_column"])
    assert list(tmp_path.iterdir()) == []


def test_builtin_cast_error_is_raised_by_delta_rs(tmp_path: Path) -> None:
    from deltalake import write_deltalake

    location = tmp_path / "table"
    write_deltalake(location, pq.read_table(ROOT / "fixtures/int64_to_float64/v0.parquet"))
    with pytest.raises(Exception, match="Can't cast value") as error:
        write_deltalake(location, pq.read_table(ROOT / "fixtures/int64_to_float64/v1.parquet"), mode="append")
    assert type(error.value).__module__ == "builtins"
