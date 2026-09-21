"""Structural schema diff between two Arrow schemas.

This is the independent computation that declared mutations are checked
against. It knows nothing about any manifest.

Paths: struct children are joined with `.`, list elements are `[]`, and map
entries are lists of `key`/`value` pairs, so `tags[]`, `line_items[].sku` and
`attributes[].value` are all paths.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pyarrow.types as pat

from .inspect import field_id
from .types import type_string
from .values import canonical_json


def diff_schemas(old: pa.Schema, new: pa.Schema) -> list[dict[str, Any]]:
    """Every structural difference between `old` and `new`, as mutations."""
    return _diff_level(list(old), list(new), "", "")


def renames(mutations: list[dict[str, Any]]) -> dict[str, str]:
    """`old_path -> path` for every rename."""
    return {m["old_path"]: m["path"] for m in mutations if m["kind"] == "rename_field"}


def _is_list(t: pa.DataType) -> bool:
    return pat.is_list(t) or pat.is_large_list(t)


def _match(old: list[pa.Field], new: list[pa.Field]) -> list[tuple[pa.Field, pa.Field]]:
    """Pair siblings by field id when every sibling on both sides has one,
    otherwise by name."""
    if old and new and all(field_id(f) is not None for f in old + new):
        by_id = {field_id(f): f for f in new}
        return [(f, by_id[field_id(f)]) for f in old if field_id(f) in by_id]
    by_name = {f.name: f for f in new}
    return [(f, by_name[f.name]) for f in old if f.name in by_name]


def _diff_pair(a: pa.Field, b: pa.Field, old_path: str, path: str) -> list[dict[str, Any]]:
    """Differences between two fields already known to be the same field.
    `old_path` locates `a` in the old schema, `path` locates `b` in the new."""
    out: list[dict[str, Any]] = []
    ta, tb = a.type, b.type
    if pat.is_struct(ta) and pat.is_struct(tb):
        out.extend(_diff_level(list(ta), list(tb), old_path + ".", path + "."))
    elif pat.is_map(ta) and pat.is_map(tb):
        out.extend(
            _diff_level([ta.key_field, ta.item_field], [tb.key_field, tb.item_field], old_path + "[].", path + "[].")
        )
    elif _is_list(ta) and _is_list(tb) and pat.is_list(ta) == pat.is_list(tb):
        # The element field's name (item/element) is physical, not structural.
        out.extend(_diff_pair(ta.value_field, tb.value_field, old_path + "[]", path + "[]"))
    elif type_string(ta) != type_string(tb):
        out.append({"kind": "change_type", "path": path, "old_type": type_string(ta), "new_type": type_string(tb)})
    if a.nullable != b.nullable:
        out.append({"kind": "change_nullability", "path": path, "old_nullable": a.nullable, "new_nullable": b.nullable})
    return out


def _diff_level(old: list[pa.Field], new: list[pa.Field], old_prefix: str, prefix: str) -> list[dict[str, Any]]:
    """`old_prefix` and `prefix` differ when an ancestor was renamed: removed
    fields and `old_path` are paths in the old schema, everything else is a
    path in the new one."""
    out: list[dict[str, Any]] = []
    pairs = _match(old, new)
    matched_old = {id(a) for a, _ in pairs}
    matched_new = {id(b) for _, b in pairs}

    for f in old:
        if id(f) not in matched_old:
            out.append(
                {"kind": "remove_field", "path": old_prefix + f.name, "type": type_string(f.type), "nullable": f.nullable}
            )

    for a, b in pairs:
        if a.name != b.name:
            out.append(
                {"kind": "rename_field", "path": prefix + b.name, "old_path": old_prefix + a.name, "field_id": field_id(b)}
            )
        out.extend(_diff_pair(a, b, old_prefix + a.name, prefix + b.name))

    for f in new:
        if id(f) not in matched_new:
            out.append({"kind": "add_field", "path": prefix + f.name, "type": type_string(f.type), "nullable": f.nullable})

    old_order = [a.name for a, _ in pairs]
    new_position = {id(b): i for i, b in enumerate(new)}
    if [a.name for a, _ in sorted(pairs, key=lambda p: new_position[id(p[1])])] != old_order:
        out.append(
            {
                "kind": "reorder_fields",
                "path": prefix.rstrip("."),
                "old_order": [f.name for f in old],
                "new_order": [f.name for f in new],
            }
        )
    return out


def mutation_key(m: dict[str, Any]) -> str:
    """Order-independent identity of a mutation, for set comparison."""
    return canonical_json(m)
