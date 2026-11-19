"""Manifests are well-formed, complete, fact-only and portable."""

from __future__ import annotations

import copy
import json
import re

import pyarrow as pa
import pytest
from conftest import FIXTURES, ROOT, SCENARIO_IDS, load
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from tabular_evolution.inspect import FIELD_ID_KEY, arrow_features
from tabular_evolution.models import ARROW_FEATURES, CATEGORIES, RELATIONS, SCHEMA_VERSION
from tabular_evolution.scenarios import all_scenarios
from tabular_evolution.validate import LoadedScenario, check_manifest, check_scenario, check_versions, load_json

# Words that would turn a fact into a compatibility verdict. The corpus records
# facts only (DESIGN.md section 2); verdicts belong to adapters.
POLICY_WORDS = re.compile(r"compatib|breaking|\bsafe\b|\bunsafe\b|\bvalid\b|\binvalid\b|\bshould\b|\bmust\b", re.I)


def test_schema_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(load_json(ROOT / "schema" / "scenario.schema.json"))


def test_schema_enums_match_the_code() -> None:
    schema = load_json(ROOT / "schema" / "scenario.schema.json")
    assert schema["properties"]["schema_version"]["const"] == SCHEMA_VERSION
    assert tuple(schema["properties"]["category"]["enum"]) == CATEGORIES
    assert tuple(schema["properties"]["arrow_features"]["items"]["enum"]) == ARROW_FEATURES
    relation = schema["$defs"]["invariants"]["properties"]["value_changes"]["items"]["properties"]["relation"]
    assert tuple(relation["enum"]) == RELATIONS


def test_corpus_size_and_unique_ids() -> None:
    assert 20 <= len(SCENARIO_IDS) <= 50
    assert sorted(s.scenario_id for s in all_scenarios()) == SCENARIO_IDS
    lowered = [i.lower() for i in SCENARIO_IDS]
    assert len(set(lowered)) == len(lowered), "ids must be unique on case-insensitive filesystems too"


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_manifest_validates(scenario_id: str, validator) -> None:
    assert check_manifest(load(scenario_id), validator) == []


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_manifest_records_no_policy(scenario_id: str) -> None:
    manifest = load(scenario_id).manifest
    keys: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            keys.extend(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(manifest)
    assert not [k for k in keys if POLICY_WORDS.search(k)]
    prose = " ".join([manifest["title"], manifest["description"], *manifest["notes"]])
    assert not POLICY_WORDS.search(prose), POLICY_WORDS.search(prose)


@pytest.mark.parametrize("scenario_id", SCENARIO_IDS)
def test_manifest_is_ascii_with_lf_endings(scenario_id: str) -> None:
    raw = (FIXTURES / scenario_id / "scenario.json").read_bytes()
    raw.decode("ascii")
    assert b"\r" not in raw
    json.loads(raw)


@pytest.mark.parametrize("features", [[], ["dictionary", "large"]], ids=["missing", "extra"])
def test_arrow_features_are_checked_against_files(features: list[str], validator: Validator) -> None:
    scenario = load("string_to_large_string")
    manifest = copy.deepcopy(scenario.manifest)
    manifest["arrow_features"] = features
    tampered = LoadedScenario(scenario.directory, manifest)
    assert check_manifest(tampered, validator) == []
    assert check_scenario(tampered, validator) == [f"declared arrow_features {features} != actual ['large']"]


def test_format_1_0_versions_can_still_be_checked() -> None:
    scenario = load("string_to_large_string")
    scenario.manifest["schema_version"] = "1.0"
    del scenario.manifest["arrow_features"]
    assert check_versions(scenario) == []


@pytest.mark.parametrize("features", [["union"], ["large", "large"]], ids=["unknown", "duplicate"])
def test_arrow_feature_vocabulary_rejects_tampering(features: list[str], validator: Validator) -> None:
    scenario = load("string_to_large_string")
    scenario.manifest["arrow_features"] = features
    assert any(problem.startswith("schema: arrow_features") for problem in check_manifest(scenario, validator))


def test_arrow_feature_scan_descends_into_nested_and_dictionary_types() -> None:
    code = pa.field("code", pa.uint16(), metadata={FIELD_ID_KEY: b"7"})
    label = pa.field("label", pa.dictionary(pa.int8(), pa.large_binary()))
    empty = pa.field("empty", pa.null())
    schema = pa.schema([pa.field("items", pa.map_(pa.string(), pa.large_list(pa.struct([code, label, empty]))))])
    assert arrow_features(schema) == set(ARROW_FEATURES)


def test_paths_are_portable() -> None:
    for path in FIXTURES.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        assert re.fullmatch(r"[a-z0-9_./]+", relative), relative
        assert len(relative) < 100, relative
    for scenario_id in SCENARIO_IDS:
        for version in load(scenario_id).manifest["versions"]:
            assert "/" not in version["file"] and "\\" not in version["file"]
