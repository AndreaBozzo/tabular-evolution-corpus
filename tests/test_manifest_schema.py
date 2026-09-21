"""Manifests are well-formed, complete, fact-only and portable."""

from __future__ import annotations

import json
import re

import pytest
from jsonschema import Draft202012Validator

from conftest import FIXTURES, ROOT, SCENARIO_IDS, load
from tabular_evolution.models import CATEGORIES, RELATIONS
from tabular_evolution.scenarios import all_scenarios
from tabular_evolution.validate import check_manifest, load_json

# Words that would turn a fact into a compatibility verdict. The corpus records
# facts only (DESIGN.md section 2); verdicts belong to adapters.
POLICY_WORDS = re.compile(r"compatib|breaking|\bsafe\b|\bunsafe\b|\bvalid\b|\binvalid\b|\bshould\b|\bmust\b", re.I)


def test_schema_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(load_json(ROOT / "schema" / "scenario.schema.json"))


def test_schema_enums_match_the_code() -> None:
    schema = load_json(ROOT / "schema" / "scenario.schema.json")
    assert tuple(schema["properties"]["category"]["enum"]) == CATEGORIES
    relation = schema["$defs"]["invariants"]["properties"]["value_changes"]["items"]["properties"]["relation"]
    assert tuple(relation["enum"]) == RELATIONS


def test_corpus_size_and_unique_ids() -> None:
    assert 20 <= len(SCENARIO_IDS) <= 35
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


def test_paths_are_portable() -> None:
    for path in FIXTURES.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        assert re.fullmatch(r"[a-z0-9_./]+", relative), relative
        assert len(relative) < 100, relative
    for scenario_id in SCENARIO_IDS:
        for version in load(scenario_id).manifest["versions"]:
            assert "/" not in version["file"] and "\\" not in version["file"]
