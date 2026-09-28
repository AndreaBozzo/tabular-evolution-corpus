# Development and releases

Run the commands from the repository root.

## Repository structure

```text
tabular-evolution-corpus/
├── README.md               short project entry point
├── DESIGN.md               boundary, fact-vs-policy principle, data model, reproducibility
├── docs/                   scenarios, usage, observations, and development
├── LICENSE                 Apache-2.0
├── CITATION.cff
├── SHA256SUMS              sha256 of every generated file (sha256sum -c compatible)
├── pyproject.toml / uv.lock
├── .github/workflows/ci.yml  regenerate + verify on Linux, Windows, macOS
├── data/
│   └── scenarios.parquet   catalog: one row per scenario
├── fixtures/               generated; do not edit by hand
│   └── <scenario_id>/
│       ├── v0.parquet
│       ├── v1.parquet      (v2.parquet, ... for longer histories)
│       └── scenario.json
├── schema/
│   ├── scenario.schema.json    JSON Schema 2020-12 for scenario.json
│   └── result.schema.json      JSON Schema 2020-12 for adapter observations
├── hf/
│   └── README.md           Hugging Face dataset card
├── src/tabular_evolution/
│   ├── scenarios/          scenario definitions: the source of truth
│   ├── types.py            the corpus type vocabulary (not Arrow's ToString)
│   ├── models.py           authoring model and manifest rendering
│   ├── generate.py         pinned Parquet writer, fixture generation
│   ├── values.py           value equality, canonical values, fingerprints
│   ├── diff.py             structural schema diff (the independent side of mutations)
│   ├── inspect.py          PyArrow reference inspector (facts only)
│   ├── validate.py         every check of manifests against files
│   ├── catalog.py          data/scenarios.parquet
│   ├── release.py          reproducibility comparison, checksums
│   └── cli.py              `tec list | inspect | validate`
├── adapters/               Phase 1 engine adapters, runner, report (not in the package)
├── results/                observations per corpus version and adapter (not part of the corpus)
├── scripts/
│   ├── generate_fixtures.py
│   ├── build_catalog.py
│   └── verify_release.py
└── tests/
```

## Regenerating and validating

```bash
uv sync
uv run pytest
uv run python scripts/generate_fixtures.py   # rewrite fixtures/, then validate every scenario
uv run python scripts/build_catalog.py       # rewrite data/scenarios.parquet and SHA256SUMS
uv run python scripts/verify_release.py      # release gate
```

Small CLI:

```bash
uv run tec list
uv run tec inspect timestamp_naive_to_utc   # everything the reference inspector observes
uv run tec validate                         # or: tec validate <scenario_id> ...
```

### What "reproducible" means here

- **Logical reproducibility (always required).** Regenerating produces
  byte-identical manifests, and every Parquet file has the same *logical
  fingerprint*: a SHA-256 of the stored Arrow schema plus every value in row
  order, in an exact canonical encoding (floats as `float.hex`, decimals as
  strings, timestamps as epoch integers with unit and zone, dictionaries
  decoded). The fingerprint is stored in each manifest and recomputed from
  the file by the validator.
- **Byte reproducibility (required with the same writer).** Every Parquet file
  records its writer in `created_by` (for example
  `parquet-cpp-arrow version 25.0.1`). If the installed PyArrow matches, the
  regenerated fixtures, the catalog and `SHA256SUMS` must be byte-identical.
  With a different PyArrow version, bytes may legitimately differ (footer
  metadata, statistics). `verify_release.py` then says so and checks the
  logical level only.
- Writer options are pinned explicitly in `generate.py` (uncompressed, format
  2.6, data page v1, one row group, statistics on, page index off), so a change
  of PyArrow defaults cannot silently change the files.
- Manifests are ASCII-only JSON with LF endings, so no editor or checkout
  setting can re-normalize the Unicode they describe.
- Type strings come from the corpus's own vocabulary
  (`src/tabular_evolution/types.py`), not from Arrow's `ToString()`, which
  differs between releases and even between writing and reading one file
  (a list written as `list<item: int32>` reads back as `list<element: int32>`;
  a map column's type string embeds the column name). The spelling matches
  Arrow's for scalar types; golden tests pin it.
- CI (`.github/workflows/ci.yml`) regenerates everything on Linux, Windows
  and macOS with Python 3.11 and 3.13 and fails if a single committed byte
  differs. Linux and Windows output has also been checked locally to be
  byte-identical.

### One writer

Every fixture is written by the PyArrow (Arrow C++) Parquet writer pinned in
`uv.lock`, which is also recorded in each file's `created_by`. This is
deliberate: the scenarios vary the *data*, and a second writer would add a
second variable. It also means that some facts are facts about this writer's
output, and the manifests say which ones:

- distinctions that exist only in the stored Arrow schema (`large_string`,
  dictionary encoding and its index width) are flagged with
  `parquet_schema_preserved: true` and a note, because a reader that ignores
  that schema sees no change;
- Parquet-level choices of this writer (list element group named `element`,
  map entries named `key_value`, decimals as fixed-length byte arrays,
  statistics present, no page index) are visible with `tec inspect`.

Files from other writers (parquet-java, Rust `parquet`, DuckDB, Spark) are
a later item: the same scenarios, rewritten, would test whether a system's
behaviour depends on the writer rather than on the change.

## Versioning

Three things are versioned, independently of each other:

| what | where | changes when |
| --- | --- | --- |
| **Corpus release** (`v0.2.0`) | git tag, GitHub release, Hugging Face tag | any fixture, manifest or catalog change. A release names fixed bytes: its tag is never moved or deleted, and a changed corpus is a new release. |
| **Manifest format** (`schema_version: "1.0"`) | every `scenario.json` | only the manifest format changes: a new field, mutation kind, relation or invariant, or a changed type spelling. A release that only adds scenarios keeps it. |
| **Observation runs** | outside `fixtures/` | never folded back into the corpus. A run is keyed by corpus version, corpus revision and adapter version. |

The Python package version is the corpus version. To cite exact bytes, give
the version and the revision: the git commit, or the Hugging Face commit when
the files were read from the Hub. Each GitHub release has the tagged
`SHA256SUMS` attached, so anyone can check a copy of the files against it
with `sha256sum -c SHA256SUMS`.

## Scope and non-goals

In scope: physical, versioned tabular datasets; structural and value-level
facts about their evolution; deterministic regeneration; self-validation.

Non-goals:

- declaring any change compatible or incompatible;
- engine behaviour as ground truth (that is adapter output);
- volume or performance (fixtures are tiny on purpose);
- file formats other than Parquet in Phase 0;
- reproducing or vendoring the Arrow or Parquet test corpora.

## Contributing a scenario

1. **One change per transition.** A scenario isolates one structural or
   value-level change. Unrelated edge values do not belong in it.
2. **Facts only.** Titles, descriptions and notes state what the files
   contain. No `compatible`, `breaking`, `safe`, `should` or `must`; a test
   rejects them.
3. **Declare independently.** Add a function in
   `src/tabular_evolution/scenarios/<module>.py` and list it in that module's
   `SCENARIOS`. Build the data with explicit Arrow types, and write the
   declared schema, mutations and invariants by hand. Do not derive one from
   the other; the validator's job is to catch their disagreement.
4. **Small and deliberate values.** 0 to 30 rows. Every unusual value should
   be named in the description or notes.
5. **Regenerate and verify:**
   ```bash
   uv run python scripts/generate_fixtures.py
   uv run python scripts/build_catalog.py
   uv run pytest
   uv run python scripts/verify_release.py
   ```
6. **Commit the generated files** (`fixtures/`, `data/`, `SHA256SUMS`) with the
   definition. Never edit generated files by hand.
7. If a change cannot be represented in Parquet without relying on undefined
   or reader-specific behaviour, document it under
   ["Deliberately excluded"](SCENARIOS.md#deliberately-excluded) instead.

New mutation kinds, relations or invariants are schema changes: update
`schema/scenario.schema.json`, `models.py`, `diff.py` / `validate.py`,
[`DESIGN.md`](../DESIGN.md), and add tampering tests showing that the new
check fails when it is violated.

## Roadmap

- **Phase 0 (corpus 0.1.0):** 31 scenarios, formal manifest schema,
  validator, catalog, deterministic regeneration, dataset card.
- **Corpus 0.2.0:** four more scenarios: a non-nullable column added, int32 to
  decimal, a nanosecond to microsecond timestamp, and deleted rows (the first
  fixture with `rows_retained: false`).
- **Phase 1: adapters, outside the core package.** A runner, a result
  record schema and three adapters (PyArrow Dataset, DuckDB, Polars), with
  their observations in [OBSERVATIONS.md](OBSERVATIONS.md).
- **Phase 2: table formats and contract tools.** Delta Lake and Iceberg
  (append under each schema-evolution mode), Spark (`mergeSchema`), dlt, Data
  Contract CLI, Soda, Great Expectations, profilers such as dataprof.
- **Corpus growth:** row reordering, map key changes, renames without field
  ids, millisecond and second timestamp units, fixtures from a second Parquet
  writer (see "One writer"), CSV/JSON renderings of the same scenarios.

## License and citation

Apache-2.0, see [LICENSE](../LICENSE). All data is synthetic and generated by the
code in this repository. Citation metadata is in [CITATION.cff](../CITATION.cff).
