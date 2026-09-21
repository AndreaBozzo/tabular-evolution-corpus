"""The scenario catalog: one row per scenario, an index over `fixtures/`.

Column types are chosen for the Hugging Face dataset viewer and for plain
Parquet readers: scalars, `list<string>` and `list<int64>` render natively
everywhere; nested manifest structures (schemas, transitions) are JSON strings,
because their shape varies by mutation kind and a union struct would be
mostly nulls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .generate import MANIFEST, write_parquet
from .validate import load_json, scenario_dirs
from .values import canonical_json

CATALOG_PATH = Path("data") / "scenarios.parquet"

_strings = pa.list_(pa.string())

CATALOG_SCHEMA = pa.schema(
    [
        pa.field("scenario_id", pa.string(), nullable=False),
        pa.field("title", pa.string(), nullable=False),
        pa.field("category", pa.string(), nullable=False),
        pa.field("description", pa.string(), nullable=False),
        pa.field("notes", _strings, nullable=False),
        pa.field("tags", _strings, nullable=False),
        pa.field("row_identity", _strings, nullable=False),
        pa.field("version_count", pa.int64(), nullable=False),
        pa.field("version_ids", _strings, nullable=False),
        pa.field("row_counts", pa.list_(pa.int64()), nullable=False),
        pa.field("mutation_count", pa.int64(), nullable=False),
        pa.field("mutation_kinds", _strings, nullable=False),
        pa.field("paths", _strings, nullable=False),
        pa.field("value_change_relations", _strings, nullable=False),
        pa.field("schema_changed", pa.bool_(), nullable=False),
        pa.field("values_changed", pa.bool_(), nullable=False),
        pa.field("parquet_schema_changed", pa.bool_(), nullable=False),
        pa.field("base_file", pa.string(), nullable=False),
        pa.field("final_file", pa.string(), nullable=False),
        pa.field("base_schema_json", pa.string(), nullable=False),
        pa.field("final_schema_json", pa.string(), nullable=False),
        pa.field("transitions_json", pa.string(), nullable=False),
        pa.field("base_fingerprint", pa.string(), nullable=False),
        pa.field("final_fingerprint", pa.string(), nullable=False),
        pa.field("manifest_path", pa.string(), nullable=False),
    ]
)


def catalog_row(manifest: dict[str, Any]) -> dict[str, Any]:
    sid = manifest["scenario_id"]
    versions, transitions = manifest["versions"], manifest["transitions"]
    mutations = [m for t in transitions for m in t["mutations"]]
    changes = [c for t in transitions for c in t["invariants"]["value_changes"]]
    paths = {m["path"] for m in mutations if m["path"]} | {m["old_path"] for m in mutations if "old_path" in m}
    paths |= {c["path"] for c in changes}
    base, final = versions[0], versions[-1]
    return {
        "scenario_id": sid,
        "title": manifest["title"],
        "category": manifest["category"],
        "description": manifest["description"],
        "notes": manifest["notes"],
        "tags": manifest["tags"],
        "row_identity": manifest["row_identity"],
        "version_count": len(versions),
        "version_ids": [v["id"] for v in versions],
        "row_counts": [v["row_count"] for v in versions],
        "mutation_count": len(mutations),
        "mutation_kinds": sorted({m["kind"] for m in mutations}),
        "paths": sorted(paths),
        "value_change_relations": sorted({c["relation"] for c in changes}),
        "schema_changed": bool(mutations),
        "values_changed": bool(changes),
        "parquet_schema_changed": not all(t["invariants"]["parquet_schema_preserved"] for t in transitions),
        "base_file": f"fixtures/{sid}/{base['file']}",
        "final_file": f"fixtures/{sid}/{final['file']}",
        "base_schema_json": canonical_json(base["schema"]),
        "final_schema_json": canonical_json(final["schema"]),
        "transitions_json": canonical_json(transitions),
        "base_fingerprint": base["fingerprint"],
        "final_fingerprint": final["fingerprint"],
        "manifest_path": f"fixtures/{sid}/{MANIFEST}",
    }


def build_catalog(root: Path) -> pa.Table:
    rows = [catalog_row(load_json(d / MANIFEST)) for d in scenario_dirs(root / "fixtures")]
    rows.sort(key=lambda r: r["scenario_id"])
    return pa.Table.from_pylist(rows, schema=CATALOG_SCHEMA)


def write_catalog(root: Path, destination: Path | None = None) -> Path:
    path = destination or root / CATALOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    write_parquet(build_catalog(root), path)
    return path


def read_catalog(path: Path) -> pa.Table:
    return pq.read_table(path)
