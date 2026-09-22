# tabular-evolution-corpus

[![ci](https://github.com/AndreaBozzo/tabular-evolution-corpus/actions/workflows/ci.yml/badge.svg)](https://github.com/AndreaBozzo/tabular-evolution-corpus/actions/workflows/ci.yml)
[![Hugging Face dataset](https://img.shields.io/badge/%F0%9F%A4%97-dataset-yellow)](https://huggingface.co/datasets/AndreaBozzo/tabular-evolution-corpus)

Datasets change shape over time. A column is added, an `int32` becomes an
`int64`, a field that was never null starts being null, a string column comes
back dictionary-encoded, a struct gains a field. Every ingestion framework,
dataframe library, query engine, lakehouse table format, schema registry and
data-contract tool has to decide what to do when it sees two versions of the
same data. Existing test suites tend to encode one engine's semantics or focus
on physical-format conformance. This corpus provides a small, engine-neutral
set of physical files that states what changed and leaves compatibility
decisions to the system under test.

## What this is

A deliberately small corpus of **35 scenarios**. Each scenario is one logical
dataset at two or more points in time:

- **versions**: tiny Parquet files (0 to 8 rows) written by one pinned writer;
- a **manifest** (`scenario.json`) that records, as machine-checkable facts:
  - the stored Arrow schema of every version,
  - the **complete** structural difference between consecutive versions
    (`mutations`),
  - row- and value-level **invariants** that hold or do not hold across each
    transition, including exactly which values changed and how;
- a **catalog** (`data/scenarios.parquet`) with one row per scenario, for
  browsing and filtering.

The corpus distrusts its own manifests. Every declared fact is re-derived from
the Parquet files and compared, in both directions, by the validator. A fixture
that disagrees with its manifest fails generation, the test suite, and the
release check.

## What this is not

- **Not a compatibility verdict.** No manifest says `compatible`, `breaking`,
  `safe` or `valid`; a test enforces that. `int32 -> int64` is a fact. Whether
  that is acceptable depends on the engine, the format, or the contract.
- **Not a Parquet or Arrow conformance suite.** Every file is ordinary, valid
  Parquet. [apache/parquet-testing](https://github.com/apache/parquet-testing)
  covers encodings, compatibility and malformed files;
  [Arrow's integration tests](https://arrow.apache.org/docs/format/Integration.html)
  cover cross-implementation Arrow representation. This corpus covers
  **logical datasets changing over time** and is meant to sit next to those
  projects.
- **Not a dirty-data benchmark.** Values are chosen to expose boundaries (NaN,
  `-0.0`, 2^53 + 1, composed vs decomposed Unicode, empty strings, nested
  nulls), not to simulate data-quality problems.
- **Not a data-contract standard.** Contract tools can use these files as test
  inputs; the corpus does not define contracts.
- **Not a benchmark of any engine**, including PyArrow, which is only used to
  write the files and as a reference inspector of facts.

## A scenario

`fixtures/int64_to_float64/scenario.json` (abridged):

```json
{
  "scenario_id": "int64_to_float64",
  "category": "numeric",
  "description": "An int64 column becomes float64. Each v1 value is the nearest float64 to the v0 value. 2^53 and -2^63 are exactly representable and unchanged; 2^53 + 1 and the int64 maximum are not, so those two values change.",
  "row_identity": ["id"],
  "versions": [
    {"id": "v0", "file": "v0.parquet", "row_count": 7, "fingerprint": "sha256:86f0...",
     "schema": [{"name": "id", "type": "int64", "nullable": false},
                {"name": "measurement", "type": "int64", "nullable": true}]},
    {"id": "v1", "file": "v1.parquet", "row_count": 7, "fingerprint": "sha256:0785...",
     "schema": [{"name": "id", "type": "int64", "nullable": false},
                {"name": "measurement", "type": "double", "nullable": true}]}
  ],
  "transitions": [{
    "from": "v0", "to": "v1",
    "mutations": [{"kind": "change_type", "path": "measurement", "old_type": "int64", "new_type": "double"}],
    "invariants": {
      "row_count_preserved": true,
      "row_identity_preserved": true,
      "rows_retained": true,
      "paths_retained": true,
      "common_values_preserved": false,
      "value_changes": [{"path": "measurement", "relation": "nearest_float64"}],
      "parquet_schema_preserved": false
    }
  }]
}
```

The validator reads both files. It confirms that the stored schemas are
exactly as declared, that the only structural difference is that one type
change, that values differ at `measurement` and nowhere else, and that every
v1 value is exactly `float(v0)`.

## Scenarios

| scenario | category | title | mutations | value relation | rows |
| --- | --- | --- | --- | --- | --- |
| `add_all_null_column` | additive | Add a typed all-null column, then backfill it | add_field | nulls_filled | 6/6/6 |
| `add_nullable_int64_column` | additive | Add nullable int64 column | add_field | - | 6/6 |
| `add_nullable_string_column` | additive | Add nullable string column | add_field | - | 6/6 |
| `add_required_column` | additive | Add non-nullable populated column | add_field | - | 6/6 |
| `remove_nullable_column` | subtractive | Remove nullable column | remove_field | - | 6/6 |
| `remove_required_column` | subtractive | Remove non-nullable populated column | remove_field | - | 6/6 |
| `decimal_precision_increase` | numeric | Increase decimal precision | change_type | - | 6/6 |
| `decimal_scale_increase` | numeric | Increase decimal scale at fixed precision | change_type | - | 6/6 |
| `float64_to_int64_fractional` | numeric | float64 to int64 with fractional values | change_type | truncated_toward_zero | 7/7 |
| `float64_to_int64_integral` | numeric | float64 to int64, integral values only | change_type | - | 7/7 |
| `int32_to_decimal` | numeric | int32 to decimal with every value unchanged | change_type | - | 6/6 |
| `int64_to_float64` | numeric | int64 to float64 with values beyond 2^53 | change_type | nearest_float64 | 7/7 |
| `widen_int32_to_int64` | numeric | Widen int32 to int64 | change_type | - | 6/6 |
| `column_becomes_all_null` | nullability | Populated column becomes entirely null | (none) | nulled | 6/6 |
| `null_type_to_string` | nullability | Null-typed column becomes a string column | change_type | nulls_filled | 5/5 |
| `nullable_column_backfilled` | nullability | Nullable column loses its nulls; schema stays nullable | (none) | nulls_filled | 6/6 |
| `nullable_to_required` | nullability | Nullable column becomes non-nullable | change_nullability | - | 6/6 |
| `required_to_nullable` | nullability | Non-nullable column becomes nullable | change_nullability | - | 6/6 |
| `dictionary_reencoded` | representation | Dictionary contents change without any value change | (none) | - | 6/6 |
| `string_to_binary` | representation | string to binary holding the UTF-8 bytes | change_type | utf8_encoded | 6/6 |
| `string_to_dictionary` | representation | string to dictionary-encoded string | change_type | - | 6/6 |
| `string_to_large_string` | representation | string to large_string | change_type | - | 8/8 |
| `add_nested_field` | structural | Add a field inside a struct | add_field | - | 5/5 |
| `nested_field_type_change` | structural | Nested fields widen from float32 to float64 | change_type | - | 5/5 |
| `remove_nested_field` | structural | Remove a field from inside a struct | remove_field | - | 5/5 |
| `rename_column_with_field_id` | structural | Rename column, identified by Parquet field id | rename_field | - | 6/6 |
| `reorder_columns` | structural | Reorder columns | reorder_fields | - | 6/6 |
| `list_element_widen` | structural | List elements widen from int32 to int64 | change_type | - | 5/5 |
| `add_field_in_list_of_struct` | structural | Add a field to the structs inside a list | add_field | - | 5/5 |
| `map_value_widen` | structural | Map values widen from int32 to int64 | change_type | - | 5/5 |
| `timestamp_naive_to_utc` | temporal | Naive timestamp becomes UTC timestamp | change_type | interpreted_as_utc | 5/5 |
| `timestamp_ns_to_us` | temporal | Timestamp unit changes from nanoseconds to microseconds | change_type | - | 6/6 |
| `empty_then_populated` | edge | Empty typed file followed by a populated file | (none) | - | 0/5 |
| `rows_appended_without_key` | edge | Rows appended to a dataset without a key | (none) | - | 4/6 |
| `rows_deleted` | edge | Rows deleted from a keyed dataset | (none) | - | 6/4 |

Six scenarios have **no schema change at all** (`column_becomes_all_null`,
`nullable_column_backfilled`, `dictionary_reencoded`, `empty_then_populated`,
`rows_appended_without_key`, `rows_deleted`).
In these, values or their encoding change while the stored schema does not,
which matters to any system that infers types, nullability or categories from
the data it sees.

## Repository structure

```text
tabular-evolution-corpus/
├── README.md               this file
├── DESIGN.md               boundary, fact-vs-policy principle, data model, reproducibility
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
│   └── README.md           Hugging Face dataset card (draft)
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

## Loading the catalog

The catalog uses only scalar columns, `list<string>` and `list<int64>`.
Nested manifest structures are JSON strings (`base_schema_json`,
`final_schema_json`, `transitions_json`), because their shape depends on the
mutation kind. These column types load unchanged in every reader tested below
and render as plain columns in the Hugging Face viewer.

PyArrow:

```python
import pyarrow.compute as pc
import pyarrow.parquet as pq

catalog = pq.read_table("data/scenarios.parquet")
numeric = catalog.filter(pc.equal(catalog["category"], "numeric"))
print(numeric.select(["scenario_id", "mutation_kinds", "value_change_relations"]))
```

Pandas:

```python
import json
import pandas as pd

df = pd.read_parquet("data/scenarios.parquet")
both = df[df["values_changed"] & df["schema_changed"]]
for row in both.itertuples():
    for t in json.loads(row.transitions_json):
        print(row.scenario_id, t["from"], t["to"], t["mutations"], t["invariants"]["value_changes"])
```

DuckDB:

```sql
SELECT scenario_id, category, mutation_kinds, row_counts
FROM read_parquet('data/scenarios.parquet')
WHERE list_contains(mutation_kinds, 'change_type')
  AND NOT parquet_schema_changed      -- visible only in the stored Arrow schema
ORDER BY scenario_id;
```

(The pandas, DuckDB and Polars loading paths are exercised by
`tests/test_catalog_compat.py` after `uv sync --group compat`.)

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

## Consuming the fixtures

A downstream test suite needs only the files and the manifests. It does not
need this Python package.

```python
import json, pathlib

for manifest_path in sorted(pathlib.Path("fixtures").glob("*/scenario.json")):
    scenario = json.loads(manifest_path.read_text())
    files = [manifest_path.parent / v["file"] for v in scenario["versions"]]
    for t in scenario["transitions"]:
        result = my_system.evolve(files[int(t["from"][1:])], files[int(t["to"][1:])])
        record(scenario["scenario_id"], t, result)   # what *your* system did
```

Typical uses:

- **Readers and query engines**: read each version, then read all versions as
  one dataset (union by name or by position); record the resulting schema,
  row count and errors.
- **Schema registries and contract tools**: register v0's schema, then check
  v1's; compare what the tool reports with the declared `mutations`.
- **Profilers and type inference**: profile each version; the scenarios
  without schema changes are where inferred and stored types diverge.
- **Table formats**: append v1 to a table created from v0 under each
  schema-evolution mode.

Keep your results separate from the corpus, keyed by `scenario_id` and
transition. The corpus says what changed; your results say what your system
did about it.

Paths in mutations and value changes join field names with `.` and mark list
elements with `[]`: `address.postcode`, `scores[]`, `line_items[].discount`.
Maps are lists of entries, so a map value is `attributes[].value`. A scenario
whose `row_identity` is empty has no key; its rows are identified by position.

The meaning of every invariant, of value equality (NaN equals NaN, `-0.0`
equals `0.0`, no Unicode normalization, text never equals bytes, naive never
equals zoned) and of each value relation is defined in
[DESIGN.md](DESIGN.md#invariants).

## Observations

Phase 1 runs a few engines over every transition and records what each one
did, in `results/<corpus version>/<adapter>.jsonl` (one record per engine
call, schema: `schema/result.schema.json`). These are observations about the
engines, not verdicts on them, and they are not part of the corpus.

| adapter | `read_each_version` | `read_versions_together` modes |
| --- | --- | --- |
| PyArrow | `pq.read_table` | `default`: `ds.dataset([from, to])`, schema from the first file; `unified_permissive`: schema from `pa.unify_schemas(..., promote_options="permissive")` |
| DuckDB | `read_parquet(file)` | `positional`: `read_parquet([from, to])` (DuckDB's default); `union_by_name` |
| Polars | `pl.read_parquet` | `default`: `pl.scan_parquet([from, to]).collect()`; `diagonal_relaxed`: `pl.concat(..., how="diagonal_relaxed")` |

```bash
uv sync --group adapters
uv run python -m adapters.runner    # needs a clean checkout of a tagged corpus release
uv run python -m adapters.report    # regenerates the tables below
```

<!-- observations:begin (generated by `python -m adapters.report`; do not edit) -->

Corpus 0.1.0 at revision `39fec426b6d2`; DuckDB 1.5.5, Polars 1.44.2, PyArrow 25.0.1. Full records: [`duckdb.jsonl`](results/0.1.0/duckdb.jsonl), [`polars.jsonl`](results/0.1.0/polars.jsonl), [`pyarrow_dataset.jsonl`](results/0.1.0/pyarrow_dataset.jsonl).

**Reading two versions as one dataset.** Adapters or modes disagree on 23 of 32 transitions. Each cell shows the result type of the columns the transition touches, in the engine's own spelling, or the class of the error the engine raised. A version in italics (*v0*): the whole result has the schema the same engine reads from that version alone; *neither*: it matches neither (a promoted type, or columns from both); no mark: the engine reads both versions alike and the result matches them. A row count is shown when it is not the sum of both versions.

| transition | DuckDB `positional` | DuckDB `union_by_name` | Polars `default` | Polars `diagonal_relaxed` | PyArrow `default` | PyArrow `unified_permissive` |
| --- | --- | --- | --- | --- | --- | --- |
| `add_all_null_column` v0→v1 | (absent) *v0* | `VARCHAR` *v1* | error: `SchemaError` | `String` *v1* | (absent) *v0* | `string` *v1* |
| `add_field_in_list_of_struct` v0→v1 | `STRUCT(sku VARCHAR, qty INTEGER)[]` *v0* | `STRUCT(sku VARCHAR, qty INTEGER, discount DOUBLE)[]` *v1* | error: `SchemaError` | `List(Struct({'sku': String, 'qty': Int32, 'discount': Float64}))` *v1* | `list<element: struct<sku: string, qty: int32>>` *v0* | `list<element: struct<sku: string, qty: int32, discount: double>>` *v1* |
| `add_nested_field` v0→v1 | `STRUCT(street VARCHAR, city VARCHAR)` *v0* | `STRUCT(street VARCHAR, city VARCHAR, postcode VARCHAR)` *v1* | error: `SchemaError` | `Struct({'street': String, 'city': String, 'postcode': String})` *v1* | `struct<street: string, city: string>` *v0* | `struct<street: string, city: string, postcode: string>` *v1* |
| `add_nullable_int64_column` v0→v1 | (absent) *v0* | `BIGINT` *v1* | error: `SchemaError` | `Int64` *v1* | (absent) *v0* | `int64` *v1* |
| `add_nullable_string_column` v0→v1 | (absent) *v0* | `VARCHAR` *v1* | error: `SchemaError` | `String` *v1* | (absent) *v0* | `string` *v1* |
| `decimal_precision_increase` v0→v1 | `DECIMAL(9,2)` *v0* | `DECIMAL(18,2)` *v1* | error: `SchemaError` | `Decimal(precision=18, scale=2)` *v1* | `decimal128(9, 2)` *v0* | `decimal128(18, 2)` *v1* |
| `decimal_scale_increase` v0→v1 | `DECIMAL(9,2)` *v0* | `DECIMAL(11,4)` *neither* | error: `SchemaError` | `Decimal(precision=9, scale=4)` *v1* | `decimal128(9, 2)` *v0* | `decimal128(11, 4)` *neither* |
| `float64_to_int64_fractional` v0→v1 | `DOUBLE` *v0* | `DOUBLE` *v0* | error: `SchemaError` | `Float64` *v0* | `double` *v0* | `double` *v0* |
| `float64_to_int64_integral` v0→v1 | `DOUBLE` *v0* | `DOUBLE` *v0* | error: `SchemaError` | `Float64` *v0* | `double` *v0* | `double` *v0* |
| `int64_to_float64` v0→v1 | error: `ConversionException` | `DOUBLE` *v1* | error: `SchemaError` | `Float64` *v1* | error: `ArrowInvalid` | error: `ArrowInvalid` |
| `list_element_widen` v0→v1 | `INTEGER[]` *v0* | `BIGINT[]` *v1* | error: `SchemaError` | `List(Int64)` *v1* | `list<element: int32>` *v0* | `list<element: int64>` *v1* |
| `map_value_widen` v0→v1 | `MAP(VARCHAR, INTEGER)` *v0* | `MAP(VARCHAR, BIGINT)` *v1* | error: `SchemaError` | `List(Struct({'key': String, 'value': Int64}))` *v1* | `map<string, int32 ('attributes')>` *v0* | `map<string, int64>` *v1* |
| `nested_field_type_change` v0→v1 | `STRUCT(lat FLOAT, lon FLOAT)` *v0* | `STRUCT(lat DOUBLE, lon DOUBLE)` *v1* | error: `SchemaError` | `Struct({'lat': Float64, 'lon': Float64})` *v1* | `struct<lat: float not null, lon: float not null>` *v0* | `struct<lat: double not null, lon: double not null>` *v1* |
| `null_type_to_string` v0→v1 | error: `ConversionException` | `VARCHAR` *v1* | error: `SchemaError` | `String` *v1* | error: `ArrowNotImplementedError` | `string` *v1* |
| `remove_nested_field` v0→v1 | `STRUCT(street VARCHAR, city VARCHAR, postcode VARCHAR)` *v0* | `STRUCT(street VARCHAR, city VARCHAR, postcode VARCHAR)` *v0* | error: `SchemaError` | `Struct({'street': String, 'city': String, 'postcode': String})` *v0* | `struct<street: string, city: string, postcode: string>` *v0* | `struct<street: string, city: string, postcode: string>` *v0* |
| `remove_nullable_column` v0→v1 | error: `InvalidInputException` | `VARCHAR` *v0* | error: `ColumnNotFoundError` | `String` *v0* | `string` *v0* | `string` *v0* |
| `remove_required_column` v0→v1 | error: `InvalidInputException` | `VARCHAR` *v0* | error: `ColumnNotFoundError` | `String` *v0* | `string` *v0* | `string` *v0* |
| `rename_column_with_field_id` v0→v1 | error: `InvalidInputException` | display_name: `VARCHAR`; full_name: `VARCHAR` *neither* | error: `SchemaError` | display_name: `String`; full_name: `String` *neither* | display_name: (absent); full_name: `string` *v0* | display_name: `string`; full_name: `string` *neither* |
| `string_to_binary` v0→v1 | `VARCHAR` *v0* | error: `ConversionException` | error: `SchemaError` | `Binary` *v1* | `string` *v0* | `binary` *v1* |
| `string_to_dictionary` v0→v1 | `VARCHAR` | `VARCHAR` | error: `SchemaError` | `String` *v0* | `string` *v0* | error: `ArrowTypeError` |
| `string_to_large_string` v0→v1 | `VARCHAR` | `VARCHAR` | `String` | `String` | `string` *v0* | `large_string` *v1* |
| `timestamp_naive_to_utc` v0→v1 | `TIMESTAMP` *v0* | `TIMESTAMP WITH TIME ZONE` *v1* | `Datetime(time_unit='us', time_zone=None)` *v0* | error: `SchemaError` | `timestamp[us]` *v0* | error: `ArrowTypeError` |
| `widen_int32_to_int64` v0→v1 | `INTEGER` *v0* | `BIGINT` *v1* | error: `SchemaError` | `Int64` *v1* | `int32` *v0* | `int64` *v1* |

Every adapter and mode reads the other 9 alike: `add_all_null_column` v1→v2, `column_becomes_all_null` v0→v1, `dictionary_reencoded` v0→v1, `empty_then_populated` v0→v1, `nullable_column_backfilled` v0→v1, `nullable_to_required` v0→v1, `reorder_columns` v0→v1, `required_to_nullable` v0→v1, `rows_appended_without_key` v0→v1.

**Reading each version on its own** (`from` → `to`). Adapters disagree on 7 of 32 transitions; here the differences are in how each engine represents a single file.

| transition | DuckDB | Polars | PyArrow |
| --- | --- | --- | --- |
| `dictionary_reencoded` v0→v1 | `VARCHAR` → `VARCHAR` | `Categorical` → `Categorical` | `dictionary<values=string, indices=int8, ordered=0>` → `dictionary<values=string, indices=int8, ordered=0>` |
| `empty_then_populated` v0→v1 | `TIMESTAMP WITH TIME ZONE` → `TIMESTAMP WITH TIME ZONE` | `Datetime(time_unit='us', time_zone='UTC')` → `Datetime(time_unit='us', time_zone='UTC')` | `timestamp[us, tz=UTC]` → `timestamp[us, tz=UTC]` |
| `map_value_widen` v0→v1 | `MAP(VARCHAR, INTEGER)` → `MAP(VARCHAR, BIGINT)` | `List(Struct({'key': String, 'value': Int32}))` → `List(Struct({'key': String, 'value': Int64}))` | `map<string, int32 ('attributes')>` → `map<string, int64 ('attributes')>` |
| `null_type_to_string` v0→v1 | `INTEGER` → `VARCHAR` | `Null` → `String` | `null` → `string` |
| `string_to_dictionary` v0→v1 | `VARCHAR` → `VARCHAR` | `String` → `Categorical` | `string` → `dictionary<values=string, indices=int8, ordered=0>` |
| `string_to_large_string` v0→v1 | `VARCHAR` → `VARCHAR` | `String` → `String` | `string` → `large_string` |
| `timestamp_naive_to_utc` v0→v1 | `TIMESTAMP` → `TIMESTAMP WITH TIME ZONE` | `Datetime(time_unit='us', time_zone=None)` → `Datetime(time_unit='us', time_zone='UTC')` | `timestamp[us]` → `timestamp[us, tz=UTC]` |

<!-- observations:end -->

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

## Deliberately excluded

| candidate | reason |
| --- | --- |
| Rename **without** field ids | Physically identical to remove + add; recording it as a rename would be a claim the files cannot support. A future scenario can model it as remove + add with a value-level link. |
| Dictionary **index width** change (int8 -> int32) | Exists only in the stored Arrow schema; a reader that ignores it sees an identical Parquet column. Recording it would mostly test one writer's metadata round trip. |
| `string_view` / `binary_view` | Written to Parquet as plain strings; the view type survives only through the stored Arrow schema, so it would duplicate `string_to_large_string` at the Parquet level. |
| float64 NaN / infinity -> int64 | No integer value exists; any v1 content would encode a policy (null, error, sentinel). |
| Other timestamp units, dates beyond the nanosecond range | `timestamp_ns_to_us` covers one unit change, inside the nanosecond range. Millisecond and second units, and dates only a coarser unit can hold, are good candidates left for later. |
| Map key changes, duplicate map keys | A map is modelled as a list of key/value entries, so these are representable; they are left for a scenario of their own rather than mixed into `map_value_widen`. |

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
   or reader-specific behaviour, document it under "Deliberately excluded"
   instead.

New mutation kinds, relations or invariants are schema changes: update
`schema/scenario.schema.json`, `models.py`, `diff.py` / `validate.py`,
`DESIGN.md`, and add tampering tests showing that the new check fails when it
is violated.

## Roadmap

- **Phase 0 (corpus 0.1.0):** 31 scenarios, formal manifest schema,
  validator, catalog, deterministic regeneration, dataset card.
- **Corpus 0.2.0:** four more scenarios: a non-nullable column added, int32 to
  decimal, a nanosecond to microsecond timestamp, and deleted rows (the first
  fixture with `rows_retained: false`).
- **Phase 1: adapters, outside the core package.** A runner, a result
  record schema and three adapters (PyArrow Dataset, DuckDB, Polars), with
  their observations in the README (see [Observations](#observations)).
- **Phase 2: table formats and contract tools.** Delta Lake and Iceberg
  (append under each schema-evolution mode), Spark (`mergeSchema`), dlt, Data
  Contract CLI, Soda, Great Expectations, profilers such as dataprof.
- **Corpus growth:** row reordering, map key changes, renames without field
  ids, millisecond and second timestamp units, fixtures from a second Parquet
  writer (see "One writer"), CSV/JSON renderings of the same scenarios.

## License and citation

Apache-2.0, see [LICENSE](LICENSE). All data is synthetic and generated by the
code in this repository. Citation metadata is in [CITATION.cff](CITATION.cff).
