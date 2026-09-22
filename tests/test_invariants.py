"""Every declared fact holds in the files, and the validator notices when one does not."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pyarrow as pa
import pytest

from conftest import FIXTURES, SCENARIO_IDS, load
from tabular_evolution.generate import write_parquet
from tabular_evolution.models import RELATIONS
from tabular_evolution.validate import (
    BOOLEAN_INVARIANTS,
    LoadedScenario,
    _check_relation,
    check_layout,
    check_manifest,
    check_transitions,
    check_versions,
)
from tabular_evolution.values import Instant

# ------------------------------------------------------------ the facts hold


def test_no_orphans_in_fixtures() -> None:
    assert check_layout(FIXTURES) == []


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_declared_version_facts_match_files(scenario_id: str) -> None:
    assert check_versions(load(scenario_id)) == []


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_declared_mutations_and_invariants_hold(scenario_id: str) -> None:
    assert check_transitions(load(scenario_id)) == []


def test_every_mutation_kind_and_relation_is_exercised() -> None:
    kinds, relations = set(), set()
    for scenario_id in SCENARIO_IDS:
        for t in load(scenario_id).manifest["transitions"]:
            kinds |= {m["kind"] for m in t["mutations"]}
            relations |= {c["relation"] for c in t["invariants"]["value_changes"]}
    assert kinds == {"add_field", "remove_field", "change_type", "change_nullability", "rename_field", "reorder_fields"}
    assert relations == set(RELATIONS)


def test_boolean_invariants_are_declared_both_ways() -> None:
    """Each invariant is true somewhere and false somewhere, so both directions
    of every check run against real files."""
    seen: dict[str, set[bool]] = {name: set() for name in BOOLEAN_INVARIANTS}
    for scenario_id in SCENARIO_IDS:
        for t in load(scenario_id).manifest["transitions"]:
            for name in BOOLEAN_INVARIANTS:
                seen[name].add(t["invariants"][name])
    assert {name for name, values in seen.items() if values != {True, False}} == set()


# ------------------------------------------------------------ the validator is not a rubber stamp


def _transitions() -> list[tuple[str, int]]:
    return [(s, i) for s in SCENARIO_IDS for i in range(len(load(s).manifest["transitions"]))]


def _with(scenario: LoadedScenario, manifest: dict) -> LoadedScenario:
    return LoadedScenario(scenario.directory, manifest)


@pytest.mark.parametrize(("scenario_id", "index"), _transitions())
@pytest.mark.parametrize("invariant", BOOLEAN_INVARIANTS)
def test_flipping_any_invariant_is_detected(scenario_id: str, index: int, invariant: str) -> None:
    scenario = load(scenario_id)
    tampered = copy.deepcopy(scenario.manifest)
    inv = tampered["transitions"][index]["invariants"]
    inv[invariant] = not inv[invariant]
    problems = check_transitions(_with(scenario, tampered))
    assert any(invariant in p for p in problems), problems


@pytest.mark.parametrize(
    "scenario_id", [s for s in SCENARIO_IDS if any(t["mutations"] for t in load(s).manifest["transitions"])]
)
def test_omitting_a_mutation_is_detected(scenario_id: str) -> None:
    scenario = load(scenario_id)
    tampered = copy.deepcopy(scenario.manifest)
    next(t for t in tampered["transitions"] if t["mutations"])["mutations"].pop()
    problems = check_transitions(_with(scenario, tampered))
    assert any("undeclared mutation" in p for p in problems), problems


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_inventing_a_mutation_is_detected(scenario_id: str) -> None:
    scenario = load(scenario_id)
    tampered = copy.deepcopy(scenario.manifest)
    tampered["transitions"][0]["mutations"].append(
        {"kind": "change_type", "path": "id", "old_type": "int32", "new_type": "int64"}
    )
    problems = check_transitions(_with(scenario, tampered))
    assert any("declared mutation not in the files" in p for p in problems), problems


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_wrong_declared_schema_is_detected(scenario_id: str) -> None:
    scenario = load(scenario_id)
    tampered = copy.deepcopy(scenario.manifest)
    tampered["versions"][-1]["schema"][-1]["nullable"] ^= True
    problems = check_versions(_with(scenario, tampered))
    assert any("declared schema" in p for p in problems), problems


@pytest.mark.parametrize(
    "scenario_id",
    [s for s in SCENARIO_IDS if any(t["invariants"]["value_changes"] for t in load(s).manifest["transitions"])],
)
def test_every_declared_relation_is_discriminating(scenario_id: str) -> None:
    """Swapping a declared relation for any other is detected: no relation in
    the corpus holds vacuously."""
    scenario = load(scenario_id)
    index = next(i for i, t in enumerate(scenario.manifest["transitions"]) if t["invariants"]["value_changes"])
    declared = scenario.manifest["transitions"][index]["invariants"]["value_changes"][0]["relation"]
    for other in RELATIONS:
        if other == declared:
            continue
        tampered = copy.deepcopy(scenario.manifest)
        tampered["transitions"][index]["invariants"]["value_changes"][0]["relation"] = other
        problems = check_transitions(_with(scenario, tampered))
        assert any(f"relation {other!r}" in p for p in problems), (other, problems)


def test_dropping_a_value_change_is_detected(validator) -> None:
    scenario = load("int64_to_float64")
    tampered = copy.deepcopy(scenario.manifest)
    inv = tampered["transitions"][0]["invariants"]
    inv["value_changes"] = []
    # Inconsistent on its face: preserved=false with no changes listed.
    assert check_manifest(_with(scenario, tampered), validator)
    # Consistent on its face, but false against the files.
    inv["common_values_preserved"] = True
    problems = check_transitions(_with(scenario, tampered))
    assert any("common_values_preserved" in p for p in problems), problems
    assert any("value_changes paths" in p for p in problems), problems


def test_wrong_dictionary_or_writer_declaration_is_detected() -> None:
    scenario = load("string_to_dictionary")
    tampered = copy.deepcopy(scenario.manifest)
    tampered["versions"][1]["dictionaries"][0]["values"] = ["inactive", "active", "pending"]
    tampered["versions"][0]["writer"]["use_dictionary"] = True
    tampered["versions"][1]["writer"]["use_dictionary"] = False
    problems = check_versions(_with(scenario, tampered))
    assert any("declared dictionaries" in p for p in problems), problems
    assert any("use_dictionary is true" in p for p in problems), problems
    assert any("use_dictionary is false" in p for p in problems), problems


def _copy_scenario(scenario_id: str, tmp_path: Path) -> Path:
    target = tmp_path / "fixtures" / scenario_id
    shutil.copytree(FIXTURES / scenario_id, target)
    return target


def test_tampered_value_is_detected(tmp_path: Path) -> None:
    """A fixture whose data no longer matches its manifest fails even when its
    schema is unchanged."""
    directory = _copy_scenario("widen_int32_to_int64", tmp_path)
    original = LoadedScenario.load(directory).table("v1")
    values = original.column("quantity").to_pylist()
    values[0] += 1
    write_parquet(original.set_column(1, original.schema.field("quantity"), pa.array(values, pa.int64())), directory / "v1.parquet")

    scenario = LoadedScenario.load(directory)
    assert any("fingerprint" in p for p in check_versions(scenario))
    problems = check_transitions(scenario)
    assert any("common_values_preserved" in p for p in problems), problems


def test_deleted_row_is_detected(tmp_path: Path) -> None:
    """An undeclared deletion is caught (rows_deleted declares one)."""
    directory = _copy_scenario("reorder_columns", tmp_path)
    table = LoadedScenario.load(directory).table("v1")
    write_parquet(table.slice(1), directory / "v1.parquet")
    problems = check_transitions(LoadedScenario.load(directory))
    assert any("rows_retained declared True but is False" in p for p in problems), problems
    assert any("row_count_preserved declared True but is False" in p for p in problems), problems


def test_orphans_are_detected(tmp_path: Path) -> None:
    directory = _copy_scenario("reorder_columns", tmp_path)
    (directory / "v2.parquet").write_bytes((directory / "v1.parquet").read_bytes())
    (tmp_path / "fixtures" / "stray").mkdir()
    (tmp_path / "fixtures" / "README.txt").write_text("x")
    problems = check_layout(tmp_path / "fixtures")
    assert any("orphan v2.parquet" in p for p in problems), problems
    assert any("stray: directory has no scenario.json" in p for p in problems), problems
    assert any("unexpected file" in p for p in problems), problems


def test_missing_file_is_detected(tmp_path: Path) -> None:
    directory = _copy_scenario("reorder_columns", tmp_path)
    (directory / "v1.parquet").unlink()
    scenario = LoadedScenario.load(directory)
    assert any("does not exist" in p for p in check_versions(scenario))
    assert any("missing version file" in p for p in check_transitions(scenario))


# ------------------------------------------------------------ relation rules


@pytest.mark.parametrize(
    ("relation", "holds", "violated"),
    [
        ("nulls_filled", [(None, "a"), ("b", "b")], [(None, "a"), ("b", "c")]),
        ("nulls_filled", [(None, "a")], [(None, None)]),  # nothing was filled
        ("nulled", [("a", None), (None, None)], [("a", None), ("b", "b")]),
        ("nulled", [("a", None)], [(None, None)]),  # nothing was nulled
        ("truncated_toward_zero", [(1.5, 1), (-1.5, -1), (None, None)], [(1.5, 2)]),
        ("truncated_toward_zero", [(-0.5, 0)], [(float("nan"), 0)]),
        ("nearest_float64", [(2**53 + 1, float(2**53))], [(2**53 + 1, float(2**53 + 2))]),
        ("utf8_encoded", [("café", b"caf\xc3\xa9")], [("café", b"caf\xc3\xa9")]),
        ("interpreted_as_utc", [(Instant(5, None), Instant(5, "UTC"))], [(Instant(5, "UTC"), Instant(5, "UTC"))]),
    ],
)
def test_relation_rules(relation: str, holds: list, violated: list) -> None:
    assert _check_relation(relation, holds)
    assert not _check_relation(relation, violated)
