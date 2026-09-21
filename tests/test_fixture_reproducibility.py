"""Regeneration reproduces the committed fixtures (DESIGN.md section 4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ROOT
from tabular_evolution.generate import generate_all
from tabular_evolution.release import SUMS_PATH, compare_trees, installed_writer, render_sums, same_writer


@pytest.fixture(scope="module")
def regenerated(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("regenerated")
    generate_all(root / "fixtures")
    return root


def test_regeneration_is_logically_identical(regenerated: Path) -> None:
    assert compare_trees(ROOT, regenerated, require_bytes=False) == []


def test_regeneration_is_byte_identical_with_the_same_writer(regenerated: Path) -> None:
    if not same_writer(ROOT):
        pytest.skip(f"committed fixtures were not written by {installed_writer()}")
    assert compare_trees(ROOT, regenerated, require_bytes=True) == []


def test_generation_is_deterministic_within_a_process(regenerated: Path, tmp_path: Path) -> None:
    generate_all(tmp_path / "fixtures")
    assert compare_trees(regenerated, tmp_path, require_bytes=True) == []


def test_compare_trees_reports_differences(regenerated: Path, tmp_path: Path) -> None:
    generate_all(tmp_path / "fixtures")
    manifest = tmp_path / "fixtures" / "reorder_columns" / "scenario.json"
    manifest.write_bytes(manifest.read_bytes().replace(b"Reorder columns", b"Reorder the columns"))
    (tmp_path / "fixtures" / "reorder_columns" / "v1.parquet").write_bytes(
        (tmp_path / "fixtures" / "reorder_columns" / "v0.parquet").read_bytes()
    )
    (tmp_path / "fixtures" / "widen_int32_to_int64" / "v0.parquet").unlink()
    problems = compare_trees(regenerated, tmp_path, require_bytes=True)
    assert "content difference: fixtures/reorder_columns/scenario.json" in problems
    assert any(p.startswith("logical difference: fixtures/reorder_columns/v1.parquet") for p in problems)
    assert "not regenerated: fixtures/widen_int32_to_int64/v0.parquet" in problems


def test_checksums_are_current() -> None:
    if not same_writer(ROOT):
        pytest.skip(f"committed fixtures were not written by {installed_writer()}")
    assert (ROOT / SUMS_PATH).read_bytes().decode("ascii") == render_sums(ROOT)
