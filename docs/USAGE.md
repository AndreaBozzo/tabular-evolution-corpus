# Using the corpus

Run the examples from the repository root.

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

## Filtering by Arrow feature

Search across the stored Arrow schemas of all versions in each scenario.

```bash
uv run tec list --feature nested
uv run tec list --exclude-feature nested --exclude-feature dictionary
```

Require multiple features by repeating `--feature`. Each
`--exclude-feature` removes any scenario with that feature. The catalog
exposes the same `arrow_features` as a list of strings.

```sql
SELECT scenario_id, arrow_features
FROM read_parquet('data/scenarios.parquet')
WHERE list_contains(arrow_features, 'unsigned')
ORDER BY scenario_id;
```

See [Arrow features](../DESIGN.md#arrow-features) for the vocabulary.

## Vendoring fixtures

From a corpus Git checkout, export the scenarios your test suite needs:

```bash
uv run tec export ../my-project/tests/corpus string_to_binary rename_column_with_field_id
cd ../my-project/tests/corpus
sha256sum -c SHA256SUMS
```

Omit the scenario IDs to copy all scenarios. The export includes the selected
`fixtures/` directories, their checksum entries, the license, and a
`README.txt` with the corpus version and Git revision. A revision ending in
`-dirty` identifies local changes to the exported corpus sources.
The export's `.gitattributes` disables Git line-ending conversion for its
files. You can rerun the same export into an existing directory whose entries
match. An unknown scenario ID or a conflicting destination entry stops the
export.

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
        record(scenario["scenario_id"], t, result)  # what *your* system did
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
[DESIGN.md](../DESIGN.md#invariants).
