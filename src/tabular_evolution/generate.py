"""Write fixtures and manifests from the scenario definitions."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .inspect import describe_schema
from .models import Scenario
from .scenarios import all_scenarios
from .values import fingerprint

MANIFEST = "scenario.json"

# Every writer option that could vary by default is pinned here, so that a
# change of PyArrow defaults cannot silently change the fixtures.
WRITER_OPTIONS: dict[str, Any] = {
    "compression": "none",
    "version": "2.6",
    "data_page_version": "1.0",
    "write_statistics": True,
    "store_schema": True,
    "write_page_index": False,
    "write_page_checksum": False,
}


def write_parquet(table: pa.Table, path: Path, use_dictionary: bool = True) -> None:
    pq.write_table(
        table,
        path,
        use_dictionary=use_dictionary,
        row_group_size=max(table.num_rows, 1),
        **WRITER_OPTIONS,
    )


def write_json(obj: Any, path: Path) -> None:
    """ASCII-only (so no editor can re-normalize Unicode in it), key order as
    given (manifest order is meaningful), LF line endings."""
    text = json.dumps(obj, indent=2, ensure_ascii=True) + "\n"
    path.write_bytes(text.encode("ascii"))


def _observe(table: pa.Table) -> dict[str, Any]:
    return {"row_count": table.num_rows, "fingerprint": fingerprint(table, describe_schema(table.schema))}


def generate_scenario(scenario: Scenario, fixtures_dir: Path) -> Path:
    directory = fixtures_dir / scenario.scenario_id
    directory.mkdir(parents=True, exist_ok=True)
    observed = {}
    for version in scenario.versions:
        table = version.build()
        write_parquet(table, directory / version.file, version.use_dictionary)
        observed[version.id] = _observe(table)
    write_json(scenario.to_manifest(observed), directory / MANIFEST)
    return directory


def generate_all(fixtures_dir: Path, scenarios: list[Scenario] | None = None) -> list[Path]:
    """Regenerate `fixtures_dir` from scratch. The directory is owned by the
    generator: anything in it that no scenario produces is removed."""
    if fixtures_dir.exists():
        shutil.rmtree(fixtures_dir)
    fixtures_dir.mkdir(parents=True)
    return [generate_scenario(s, fixtures_dir) for s in scenarios or all_scenarios()]
