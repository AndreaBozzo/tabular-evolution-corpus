# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A small corpus of versioned Parquet scenarios (one logical dataset at two or more points in time) with machine-checked manifests, plus adapters that record what engines (PyArrow, DuckDB, Polars, delta-rs) do with each transition. `DESIGN.md` is the authority on the data model; when code and `DESIGN.md` disagree, one of them needs an explicit fix.

## Commands

Run everything from the repository root with `uv`.

```bash
uv sync                                         # core: pyarrow + jsonschema only
uv sync --group adapters                        # + deltalake, duckdb, polars for adapters/
uv run pytest                                   # full suite; adapter tests skip if an engine is missing
uv run pytest tests/test_values.py::test_name   # a single test
uv run pytest -k int64_to_float64               # tests parametrized by scenario id

uv run python scripts/generate_fixtures.py      # rewrite fixtures/ from definitions, then validate all
uv run python scripts/build_catalog.py          # rewrite data/scenarios.parquet and SHA256SUMS
uv run python scripts/verify_release.py         # release gate

uv run tec list
uv run tec inspect <scenario_id>                # everything the reference inspector observes
uv run tec validate [<scenario_id> ...]

uv run python -m adapters.runner [adapter ...]  # needs a clean checkout whose fixtures/ match a tagged release
uv run python -m adapters.report [--check]      # regenerate the tables in docs/OBSERVATIONS.md
```

CI (`.github/workflows/ci.yml`) runs pytest, regenerates fixtures and catalog on Linux/Windows/macOS with Python 3.11 and 3.13, and fails on any byte difference (`git status --porcelain` must be empty). Separate jobs require the compat and adapter tests to run without skips.

## Architecture

**Two independent sides that must agree.** A scenario is a hand-written function in `src/tabular_evolution/scenarios/<module>.py`, listed in that module's `SCENARIOS` (the registry is `scenarios/__init__.py`). It declares the data (explicit Arrow types) and, separately and by hand, the schema, mutations and invariants. Never derive the declaration from the data: `diff.py` and `validate.py` recompute everything from the written files and fail on any disagreement, in both directions (a declared `false` is checked as strictly as a `true`).

**Pipeline.** `models.py` (authoring model, manifest rendering) → `generate.py` (the single pinned PyArrow writer with explicit writer options) → `fixtures/<id>/v*.parquet` + `scenario.json` → `validate.py` → `catalog.py` (`data/scenarios.parquet`) and `release.py` (checksums, reproducibility). `fixtures/`, `data/` and `SHA256SUMS` are generated: never edit them by hand, and commit them together with the definition that produced them.

**Vocabularies owned by the corpus.**
- `types.py`: type strings are the corpus's own spelling, not Arrow's `ToString()`. An Arrow type outside the vocabulary raises.
- `values.py`: the single definition of value equality (exact numeric comparison across int/float/decimal, no Unicode normalization, timestamps by instant and zone) and of the logical fingerprint.
- Mutation kinds and value relations are closed sets. Adding one is a manifest-format change: update `schema/scenario.schema.json`, `models.py`, `diff.py`/`validate.py`, `DESIGN.md`, and add tampering tests proving the new check fails when violated.

**Adapters are outside the package** (`adapters/`, importable because pytest sets `pythonpath = ["."]`). Each adapter declares its operations and closed list of modes and splits a call into `read` (an exception is the engine's answer and becomes an `error` record) and `describe` (an exception is an adapter bug and stops the run). The runner validates every record against `schema/result.schema.json` and writes `results/<corpus version>/<adapter>.jsonl`. `docs/OBSERVATIONS.md` has a generated section between `observations:begin/end` markers; regenerate it with `adapters.report`, never edit it by hand. Results are observations about engines and never modify the corpus.

## Rules that tests enforce

- **Facts, not policy.** Manifests, result records and scenario prose never use `compatible`, `breaking`, `safe`, `valid`, `should`, `must` (see `POLICY_WORDS` in `tests/test_manifest_schema.py`). Engine error messages quoted verbatim in `notes` are the engine's words.
- **One change per transition**, 0 to 30 rows, every unusual value named in the description or notes.
- **One writer.** All fixtures come from the PyArrow version pinned in `uv.lock`. A change Parquet cannot represent without writer- or reader-specific behaviour goes under "Deliberately excluded" in `docs/SCENARIOS.md` instead.
- **Releases name fixed bytes.** Any fixture, manifest or catalog change is a new corpus version; tags are never moved. `schema_version` changes only when the manifest format does.
- Tests must not use the network (`tests/conftest.py` blocks sockets).

## Upstream findings

Engine behaviour found through the corpus that could be an upstream bug is tracked in issue #16 (label `upstream`). Before recording or filing one:

1. Reproduce it in a minimal script outside the corpus harness, on the latest engine release.
2. Search the upstream tracker (issues and PRs, open and closed) for duplicates; say whether the behaviour is documented or intended.
3. Record it in #16 with the scenario id, then file upstream using the project's issue template: short, a self-contained repro, a link to this repository and the scenario id, and an offer to help with a fix. Commenting on an existing upstream issue needs the maintainer's (repository owner's) OK first.

Repro pitfalls: DuckDB can skip reading values under projection pushdown (`SELECT typeof(x), count(*)`), hiding conversion errors, so materialize the values. On Windows, PyArrow needs `tzdata` to convert zoned timestamps to Python objects; print epoch integers instead.

## Conventions

- Commit messages: one imperative sentence, no conventional-commit prefix (`Add the Polars adapter`). No AI attribution trailers in commits or PRs.
- Issues and tickets are terse: what was observed, a repro, links.
