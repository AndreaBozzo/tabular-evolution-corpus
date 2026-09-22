# Design

This document fixes the boundary and the data model of the corpus before any
fixture exists. It is short on purpose. When the code and this document
disagree, the code is wrong or this document needs an explicit revision.

## 1. Corpus boundary

A **scenario** is one logical dataset observed at two or more points in time.
Each observation is a **version**: a small Parquet file. Between consecutive
versions there is a **transition**, described by:

- **mutations**: the complete list of structural schema differences, and
- **invariants**: row- and value-level facts that are true (or false) across
  the transition.

Out of scope:

| Concern | Where it belongs |
| --- | --- |
| Can implementation A read what implementation B wrote? | Apache Arrow integration tests |
| Is this Parquet encoding/page/footer handled correctly? | apache/parquet-testing |
| Is this value dirty, invalid, or an outlier? | data-quality / dirty-data benchmarks |
| Is this change "compatible"? | the engine, format or contract tool being tested |

The corpus sits one layer above physical format correctness. Every file in it
is ordinary, valid Parquet written by one pinned writer. What makes a scenario
interesting is the **relationship between versions**, not any single file.

## 2. Objective facts, not engine policy

The ground truth records only what can be checked against the files:

- `int32 -> int64` is a fact. "Backward compatible" is a policy, and different
  systems (Avro, Protobuf, Delta, Iceberg, Spark, a data contract) answer it
  differently for the same change.
- `nullable: true -> false` is a fact. Whether a reader must reject the old file
  is a policy.
- "Every value in v1 equals the truncation toward zero of the v0 value" is a
  fact. "This cast is acceptable" is a policy.

So the manifest never contains `compatible`, `breaking`, `safe`, `valid` or
similar. Adapter results (section 7) record what a particular engine *did*
with a scenario, in a separate artifact, keyed by `scenario_id`.

Invariants are **two-sided**: a declared `false` is checked as strictly as a
declared `true`. "Values were not preserved" is a claim, and a generator that
accidentally preserved them would be publishing a wrong fact.

## 3. Manifest model

One `scenario.json` per scenario, validated against
`schema/scenario.schema.json` (JSON Schema 2020-12).

```text
scenario
  schema_version, scenario_id, title, category, description, notes[], tags[]
  row_identity[]            top-level columns that identify a row in every version; [] = by position
  versions[]
    id, file, row_count, fingerprint
    schema[]                {name, type, nullable, field_id?}   top level, in file order
    writer                  {use_dictionary}
    dictionaries[]          {path, values[]}   optional: stored dictionary contents
  transitions[]             one per consecutive version pair
    from, to
    mutations[]             exhaustive structural diff
    invariants              row/value facts, all keys required
```

### Types

`type` strings use the **corpus type vocabulary**, rendered by
`src/tabular_evolution/types.py`: `int64`, `double`, `decimal128(9, 2)`,
`timestamp[us, tz=UTC]`, `dictionary<values=string, indices=int8, ordered=0>`,
`struct<lat: double not null, lon: double>`, `list<int32>`,
`list<struct<sku: string>>`, `map<string, int32>`. Arrow's type names are used
because they are the most widely shared descriptive vocabulary across the
target tools, not because Arrow semantics are authoritative. Nested
nullability is part of the type string (`list<int32 not null>`).

The strings are produced by the corpus, not by Arrow C++
`DataType::ToString()`. That rendering is a debugging aid: it changes between
releases, and it is not even stable across one write and read (a list written
as `list<item: int32>` reads back as `list<element: int32>`; a map column's
type renders as `map<string, int32 ('attributes')>`, with the column name
inside). Fingerprints and declared schemas built on it would change with no
change to the data. For scalar types the corpus spelling matches Arrow's
current one; container types omit physical child names. Golden tests pin
every spelling, and a change to one is a `schema_version` change. An Arrow
type outside the vocabulary is an error.

The declared schema is the Arrow schema **stored in the file** (the
`ARROW:schema` key-value metadata, which is what `pyarrow.parquet.read_schema`
returns). Some distinctions exist only there: `string` vs `large_string`, the
dictionary index width, dictionary encoding itself. A reader that ignores that
metadata sees only the Parquet schema, so every transition also states whether
the Parquet schema (physical type, logical annotation, repetition, levels,
field ids) changed: `parquet_schema_preserved`.

### Mutations

The declared mutation list must equal the diff computed from the two files,
exactly. Kinds:

| kind | fields | meaning |
| --- | --- | --- |
| `add_field` | `path, type, nullable` | path exists only in `to` |
| `remove_field` | `path, type, nullable` | path exists only in `from` |
| `change_type` | `path, old_type, new_type` | same path, different type (not struct-to-struct) |
| `change_nullability` | `path, old_nullable, new_nullable` | same path, nullability flag differs |
| `rename_field` | `path, old_path, field_id` | same Parquet field id, different name |
| `reorder_fields` | `path, old_order, new_order` | relative order of retained siblings differs (`path` = parent, `""` for root) |

Paths join field names with `.` and mark the element of a list with `[]`:
`address.postcode`, `scores[]`, `line_items[].sku`, `matrix[][]`. A map is
treated as what Arrow and Parquet store, a list of key/value entries:
`attributes[].key`, `attributes[].value`. Field names therefore never contain
`.`, `[` or `]`. Containers are diffed recursively, so adding a nested field
is `add_field line_items[].discount`, not a type change of `line_items`, and
the element field's physical name (`item`, `element`) is not structure.
`list` and `large_list` are different containers: changing one into the other
is a `change_type` of the whole path.

`path` is a path in the `to` schema, except for `remove_field`, whose path is
in the `from` schema; `old_path` is always a path in the `from` schema. They
differ below a renamed ancestor: renaming struct `a` to `b` and its child `x`
to `y` gives `rename_field a -> b` and `rename_field a.x -> b.y`.

A rename is only a structural fact when the file carries evidence for it. The
only evidence Parquet has is the field id. Without field ids a rename is
physically indistinguishable from remove + add, and the corpus describes it
that way.

### Invariants

Rows are aligned by `row_identity` (keys must be non-null and unique in every
version). An empty `row_identity` means the dataset has no key and rows are
identified by **position**: row *i* of one version is row *i* of the next.
That is a declared fact about the scenario, typical of append-only logs, and
the invariants are then evaluated on positions.

"Common paths" are field paths present in both versions, plus
`old_path -> path` pairs from renames (including their descendants). For a
struct path only its validity is compared, and its children are compared as
their own paths. For a list or map path its length is compared (null, empty
and non-empty are three different values), and its elements are compared as
the path `<path>[]`, element by element.

| invariant | definition |
| --- | --- |
| `row_count_preserved` | equal row counts |
| `row_identity_preserved` | the ordered sequence of identity keys is identical |
| `rows_retained` | every identity key of `from` exists in `to` |
| `paths_retained` | every field path of `from` exists in `to` under the same name |
| `common_values_preserved` | for every aligned row and common path, values are equal |
| `value_changes` | exactly the common paths whose values differ, each with a relation |
| `parquet_schema_preserved` | Parquet column descriptors are identical |

Quantifiers over an empty set are vacuously true (an empty `from` version
"retains" all of its zero rows). This is stated rather than special-cased.

Value equality is defined once, in `values.py`:

- null equals only null; NaN equals NaN; `-0.0` equals `0.0`;
- numbers compare by exact mathematical value across int, float and decimal
  (`2**53 + 1 != 9007199254740992.0`; `Decimal("1.20") == Decimal("1.2")`);
- strings compare by code points, with **no** Unicode normalization;
- text never equals bytes; booleans never equal numbers;
- timestamps compare by instant and time zone (a naive value never equals a
  zoned one), independent of unit;
- dictionary-encoded values compare by their decoded value.

Relations for `value_changes` are a closed vocabulary. Each is checked on
every aligned row (`a` = old value, `b` = new value):

| relation | holds when |
| --- | --- |
| `nulls_filled` | every non-null `a` equals `b`, and at least one null `a` has a non-null `b` |
| `nulled` | every `b` is null, and at least one `a` was not |
| `truncated_toward_zero` | nulls stay null; otherwise `a` is a finite float and `b` is the integer `trunc(a)` |
| `nearest_float64` | nulls stay null; otherwise `a` is an integer and `b` is exactly `float(a)` |
| `utf8_encoded` | nulls stay null; otherwise `b` is exactly the UTF-8 encoding of `a` |
| `interpreted_as_utc` | nulls stay null; otherwise `a` is naive, `b` is zoned `UTC`, same epoch value |

The test suite swaps each declared relation for every other one and requires
the validator to reject the swap, so no relation in the corpus holds vacuously.

## 4. Reproducibility

Three levels, strongest first:

1. **Bytes.** Given the same PyArrow version (recorded in every file's
   `created_by`), regeneration produces byte-identical Parquet, manifests and
   catalog. `SHA256SUMS` records the hashes. Writer options are pinned
   explicitly (uncompressed, format 2.6, data page v1, single row group).
2. **Logical fingerprint.** `sha256` of a canonical JSON encoding of the
   declared-form Arrow schema plus every value in row order (floats via
   `float.hex`, decimals as strings, timestamps as epoch integers with unit and
   zone, bytes as hex, dictionaries decoded). Stored per version in the
   manifest and recomputed from the file by the validator. This must hold
   across PyArrow versions.
3. **Manifests.** Generated as ASCII-only JSON with sorted, stable ordering and
   LF line endings, so editors cannot silently re-normalize Unicode in them.

A different PyArrow version may legitimately change bytes (footer
`created_by`, statistics layout). Verification then falls back to level 2 and
says so. Level 2 depends only on the corpus type vocabulary and the value
encoding, both owned by this repository.

CI regenerates on Linux, Windows and macOS with Python 3.11 and 3.13 and
requires a clean `git status` afterwards, i.e. level 1 on every platform.

### One writer

All fixtures come from one Parquet writer, the PyArrow version pinned in
`uv.lock`. Scenarios vary the data; a second writer would be a second
variable. Facts that belong to this writer rather than to the data are
marked: Arrow-schema-only distinctions carry `parquet_schema_preserved: true`
and a note, and writer-level choices (group names, decimal encoding,
statistics) are left to the inspector rather than declared. Fixtures from
other writers are future work.

## 5. Self-distrust

The scenario definition declares the schema and the facts by hand, and builds
the data separately. The generator writes both without cross-deriving one from
the other. The validator then reads the files back and checks every declared
fact against them: schema, row count, fingerprint, exhaustive mutation list,
every invariant in both directions, every value relation, writer options,
dictionary contents. A fixture that disagrees with its manifest fails
generation, the test suite, and `verify_release.py`.

## 6. Initial taxonomy

| category | scenarios |
| --- | --- |
| additive | add nullable string / int64 column; add all-null column then backfill (3 versions) |
| subtractive | remove nullable column; remove required column |
| numeric | int32->int64; int64->float64 (lossy above 2^53); float64->int64 integral / fractional; decimal precision increase; decimal scale increase at fixed precision |
| nullability | nulls backfilled under a nullable schema; required->nullable; nullable->required; column becomes all-null; `null`-typed column becomes string |
| representation | string->large_string; string->dictionary; dictionary re-encoded without value change; string->binary |
| structural | reorder; rename with field ids; add / remove nested field; nested float32->float64; list element int32->int64; add field inside list<struct>; map value int32->int64 |
| temporal | naive timestamp -> UTC timestamp |
| edge | empty typed file -> populated file; rows appended to a keyless dataset |

## 7. Adapters (not in the core package)

An adapter runs a fixed operation over a transition under one engine-specific
mode and emits one result record per engine call, validated against
`schema/result.schema.json` (JSON Schema 2020-12, versioned separately from
the manifest):

```json
{"schema_version": "1.0", "corpus_version": "0.1.0", "corpus_revision": "<40-hex commit>",
 "scenario_id": "timestamp_naive_to_utc", "from": "v0", "to": "v1", "inputs": ["v0", "v1"],
 "adapter": "duckdb", "adapter_version": "1.5.5",
 "operation": "read_versions_together", "mode": "union_by_name",
 "status": "success",
 "result_schema": [{"name": "event_time", "type": null,
                    "native_type": "TIMESTAMP WITH TIME ZONE", "nullable": null}],
 "row_count": 10, "error_class": null, "notes": []}
```

- **Corpus identity.** `corpus_version` names a release; `corpus_revision`
  is the commit the fixtures were read from, which proves which bytes
  produced the result.
- **Operation and mode.** `operation` is a closed set. `mode` names the
  engine's variant (`union_by_name`, `positional`, `unified_permissive`,
  `diagonal_relaxed`, or `default`). A variant is never encoded in `notes` or
  in a new operation name.
- **Types.** `type` is the corpus type vocabulary where the engine's type has
  a clear equivalent, otherwise null; `native_type` is always the engine's own
  spelling. `nullable` is null for engines that record no nullability.
- **Observations only.** No field says pass, fail or compatible, and the
  policy-word test covers the result schema too. `notes` may quote engine
  error messages verbatim; that wording is the engine's.

Results are observations about an engine, never corrections of the corpus.
They live outside `fixtures/` and are never part of a corpus release.
