---
license: apache-2.0
pretty_name: Tabular Evolution Corpus
size_categories:
  - n<1K
tags:
  - schema-evolution
  - parquet
  - arrow
  - data-engineering
  - test-fixtures
  - tabular
configs:
  - config_name: default
    default: true
    data_files:
      - split: scenarios
        path: data/scenarios.parquet
---

# Tabular Evolution Corpus

> Source, generator and validator:
> [github.com/AndreaBozzo/tabular-evolution-corpus](https://github.com/AndreaBozzo/tabular-evolution-corpus).
> This card is the `README.md` of the Hugging Face dataset repository; the
> GitHub repository keeps its own README.

A small, deterministic corpus of Parquet datasets that change over time. Each
**scenario** is one logical dataset at two or more versions, with a manifest
that records **what structurally changed** and **which row and value facts
hold** across each transition. Every fact is verified against the files.

The corpus records facts, not compatibility verdicts. Whether a change is
acceptable is for the engine, table format or data contract under test to
decide.

## What the viewer shows

The viewer shows the **catalog**, `data/scenarios.parquet`, with one row per
scenario (31 rows). The catalog is an index. The versioned fixture files it
points to live in the same repository under `fixtures/<scenario_id>/`
(`v0.parquet`, `v1.parquet`, ..., `scenario.json`).

The `configs` block above deliberately restricts the viewer and
`load_dataset` to the catalog. The fixture Parquet files have a different
schema in every scenario and are **not** rows of one dataset. Without the
explicit config, automatic data-file detection would try to load them as one.

## Catalog columns

| column | type | meaning |
| --- | --- | --- |
| `scenario_id` | string | stable identifier; also the fixture directory name |
| `title`, `description` | string | what changes, stated as facts |
| `category` | string | additive, subtractive, numeric, nullability, representation, structural, temporal, edge |
| `notes` | list<string> | caveats, e.g. differences that exist only in the stored Arrow schema |
| `tags` | list<string> | free-form, kebab-case |
| `row_identity` | list<string> | columns that identify a row in every version; empty means rows are identified by position |
| `version_count`, `version_ids`, `row_counts` | int64, list<string>, list<int64> | versions in order |
| `mutation_count`, `mutation_kinds` | int64, list<string> | structural changes across all transitions |
| `paths` | list<string> | field paths touched by a mutation or a value change |
| `value_change_relations` | list<string> | how changed values relate (`nearest_float64`, `utf8_encoded`, ...) |
| `schema_changed`, `values_changed`, `parquet_schema_changed` | bool | quick filters |
| `base_file`, `final_file`, `manifest_path` | string | repository-relative paths |
| `base_schema_json`, `final_schema_json`, `transitions_json` | string | JSON; full detail is in `scenario.json` |
| `base_fingerprint`, `final_fingerprint` | string | logical fingerprints (schema + values + row order) |

Nested manifest structures are JSON strings because their shape depends on the
mutation kind. Every other column is a scalar or a list of scalars.

## Usage

```python
from datasets import load_dataset

catalog = load_dataset("AndreaBozzo/tabular-evolution-corpus", split="scenarios")
```

To get the fixtures, download the repository files:

```python
from huggingface_hub import snapshot_download

root = snapshot_download("AndreaBozzo/tabular-evolution-corpus", repo_type="dataset")
# root/fixtures/<scenario_id>/{v0.parquet, v1.parquet, scenario.json}
```

```sql
-- DuckDB
SELECT scenario_id, mutation_kinds, row_counts
FROM read_parquet('hf://datasets/AndreaBozzo/tabular-evolution-corpus/data/scenarios.parquet')
WHERE category = 'numeric';
```

## Dataset structure

```text
data/scenarios.parquet          catalog (this viewer)
fixtures/<scenario_id>/         one directory per scenario
    v0.parquet, v1.parquet ...  versions, 0-8 rows each
    scenario.json               manifest (JSON Schema: schema/scenario.schema.json)
schema/scenario.schema.json
SHA256SUMS
```

## Creation

All data is synthetic, written by the generator in the source repository with
a pinned PyArrow Parquet writer (uncompressed, format 2.6, one row group).
Values are chosen to expose boundaries: NaN, infinities, `-0.0`, 2^53 + 1,
int and decimal extremes, composed vs decomposed Unicode, empty strings,
nested nulls, pre-epoch timestamps. Regeneration is logically reproducible,
and byte-reproducible with the same PyArrow version.

## Intended use and limitations

Intended as test input for ingestion frameworks, dataframe libraries, query
engines, lakehouse tooling, schema registries, contract validators and
profilers. It is not training data, not a performance benchmark, and not a
conformance suite for Arrow or Parquet (see apache/parquet-testing and the
Arrow integration tests for those). Phase 0 covers Parquet only and 31
scenarios. Nested evolution covers structs, lists and maps; datasets are
keyed or identified by row position.

**One writer.** Every file is written by a single pinned Parquet writer
(PyArrow / Arrow C++, recorded in each file's `created_by`). The scenarios
vary the data, not the writer. Differences that exist only in the Arrow schema
stored in the file (`large_string`, dictionary encoding) are flagged in each
manifest, because readers that ignore that schema see no change. Fixtures
from other writers are planned but not included.

## License

Apache-2.0. No personal data: every name, address and e-mail in the fixtures
is invented or a well-known public name used as a placeholder.
