# Scenario inventory

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

## Deliberately excluded

| candidate | reason |
| --- | --- |
| Rename **without** field ids | Physically identical to remove + add; recording it as a rename would be a claim the files cannot support. A future scenario can model it as remove + add with a value-level link. |
| Dictionary **index width** change (int8 -> int32) | Exists only in the stored Arrow schema; a reader that ignores it sees an identical Parquet column. Recording it would mostly test one writer's metadata round trip. |
| `string_view` / `binary_view` | Written to Parquet as plain strings; the view type survives only through the stored Arrow schema, so it would duplicate `string_to_large_string` at the Parquet level. |
| float64 NaN / infinity -> int64 | No integer value exists; any v1 content would encode a policy (null, error, sentinel). |
| Other timestamp units, dates beyond the nanosecond range | `timestamp_ns_to_us` covers one unit change, inside the nanosecond range. Millisecond and second units, and dates only a coarser unit can hold, are good candidates left for later. |
| Map key changes, duplicate map keys | A map is modelled as a list of key/value entries, so these are representable; they are left for a scenario of their own rather than mixed into `map_value_widen`. |
