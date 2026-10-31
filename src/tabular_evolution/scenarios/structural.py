"""Field order, names and nesting."""

from __future__ import annotations

import pyarrow as pa

from ..models import (
    Scenario,
    Transition,
    Version,
    add_field,
    change_type,
    col,
    remove_field,
    rename_field,
    reorder_fields,
    same_rows,
)
from ._build import ID, f32, field, ids, table


def reorder_columns() -> Scenario:
    region = field("region", pa.string())
    amount = field("amount", pa.int64())
    active = field("active", pa.bool_())
    values = {
        "id": ids(6),
        "region": ["north", "south", None, "east", "west", "north"],
        "amount": [100, -5, 0, None, 7, 100],
        "active": [True, False, None, True, False, True],
    }
    decl = {
        "id": col("id", "int64", False),
        "region": col("region", "string"),
        "amount": col("amount", "int64"),
        "active": col("active", "bool"),
    }
    old = ["id", "region", "amount", "active"]
    new = ["amount", "id", "active", "region"]
    fields = {"id": ID, "region": region, "amount": amount, "active": active}
    return Scenario(
        scenario_id="reorder_columns",
        title="Reorder columns",
        category="structural",
        description=(
            "The same four columns appear in a different order. Names, types, nullability and values are "
            "unchanged. Neither order is alphabetical, so a reader that sorts columns by name disagrees with both."
        ),
        tags=["reorder-fields", "column-order"],
        row_identity=["id"],
        versions=[
            Version("v0", lambda: table([fields[n] for n in old], [values[n] for n in old]), [decl[n] for n in old]),
            Version("v1", lambda: table([fields[n] for n in new], [values[n] for n in new]), [decl[n] for n in new]),
        ],
        transitions=[Transition("v0", "v1", [reorder_fields("", old, new)], same_rows(parquet_schema_preserved=False))],
    )


def rename_column_with_field_id() -> Scenario:
    names = ["José", "José", "Renée", "", None, "Zoë"]
    emails = ["jose@example.com", "jose2@example.com", None, "empty@example.com", "anon@example.com", ""]

    def build(name_column: str) -> pa.Table:
        return table(
            [
                field("id", pa.int64(), nullable=False, field_id=1),
                field(name_column, pa.string(), field_id=2),
                field("email", pa.string(), field_id=3),
            ],
            [ids(6), names, emails],
        )

    return Scenario(
        scenario_id="rename_column_with_field_id",
        title="Rename column, identified by Parquet field id",
        category="structural",
        description=(
            "Column full_name is renamed to display_name. Both files carry Parquet field ids and the renamed "
            "column keeps field id 2, which is the evidence that this is a rename. Values are unchanged; they "
            "include 'Jose' with a composed and with a decomposed accent, which are different values."
        ),
        notes=[
            "Without field ids a rename is physically indistinguishable from removing one column and adding "
            "another with the same values. The corpus only records a rename when the file carries evidence for it.",
        ],
        tags=["rename-field", "field-id", "unicode"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: build("full_name"),
                [col("id", "int64", False, 1), col("full_name", "string", True, 2), col("email", "string", True, 3)],
            ),
            Version(
                "v1",
                lambda: build("display_name"),
                [col("id", "int64", False, 1), col("display_name", "string", True, 2), col("email", "string", True, 3)],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [rename_field("full_name", "display_name", 2)],
                same_rows(paths_retained=False, parquet_schema_preserved=False),
            )
        ],
    )


ADDRESS_2 = pa.struct([pa.field("street", pa.string()), pa.field("city", pa.string())])
ADDRESS_3 = pa.struct(
    [pa.field("street", pa.string()), pa.field("city", pa.string()), pa.field("postcode", pa.string())]
)
ADDRESS_2_TYPE = "struct<street: string, city: string>"
ADDRESS_3_TYPE = "struct<street: string, city: string, postcode: string>"
ADDRESSES_2 = [
    {"street": "1 Main St", "city": "Springfield"},
    None,
    {"street": None, "city": "Zürich"},
    {"street": "", "city": ""},
    {"street": "221B Baker St", "city": "London"},
]
ADDRESSES_3 = [
    {"street": "1 Main St", "city": "Springfield", "postcode": "12345"},
    None,
    {"street": None, "city": "Zürich", "postcode": "8001"},
    {"street": "", "city": "", "postcode": None},
    {"street": "221B Baker St", "city": "London", "postcode": "NW1 6XE"},
]


def _address(scenario_id: str, title: str, description: str, old_first: bool) -> Scenario:
    small = (lambda: table([ID, field("address", ADDRESS_2)], [ids(5), ADDRESSES_2]), ADDRESS_2_TYPE)
    large = (lambda: table([ID, field("address", ADDRESS_3)], [ids(5), ADDRESSES_3]), ADDRESS_3_TYPE)
    (b0, t0), (b1, t1) = (small, large) if old_first else (large, small)
    mutation = (
        add_field("address.postcode", "string", True) if old_first else remove_field("address.postcode", "string", True)
    )
    return Scenario(
        scenario_id=scenario_id,
        title=title,
        category="structural",
        description=description,
        tags=["struct", "nested", "add-field" if old_first else "remove-field", "nested-nulls"],
        row_identity=["id"],
        versions=[
            Version("v0", b0, [col("id", "int64", False), col("address", t0)]),
            Version("v1", b1, [col("id", "int64", False), col("address", t1)]),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [mutation],
                same_rows(paths_retained=old_first, parquet_schema_preserved=False),
            )
        ],
    )


def add_nested_field() -> Scenario:
    return _address(
        "add_nested_field",
        "Add a field inside a struct",
        (
            "A nullable string field postcode is appended to the address struct. Rows include a null struct, "
            "a struct whose street is null, and a struct of empty strings; a null struct stays null. "
            "Existing nested values are unchanged."
        ),
        old_first=True,
    )


def remove_nested_field() -> Scenario:
    return _address(
        "remove_nested_field",
        "Remove a field from inside a struct",
        (
            "The postcode field is removed from the address struct. Rows include a null struct, a struct "
            "whose street is null, and a struct of empty strings. Remaining nested values are unchanged."
        ),
        old_first=False,
    )


def nested_field_type_change() -> Scenario:
    coords = [(0.1, -0.1), None, (45.4642, 9.19), (0.0, -0.0), (-90.0, 180.0)]

    def build(value_type: pa.DataType) -> pa.Table:
        position = pa.struct([pa.field("lat", value_type, nullable=False), pa.field("lon", value_type, nullable=False)])
        rows = [None if c is None else {"lat": f32(c[0]), "lon": f32(c[1])} for c in coords]
        return table([ID, field("position", position)], [ids(5), rows])

    return Scenario(
        scenario_id="nested_field_type_change",
        title="Nested fields widen from float32 to float64",
        category="structural",
        description=(
            "Both non-nullable fields of a nullable position struct change from float32 to float64. Every "
            "float32 value is exactly representable as float64 and is unchanged, which means the v1 value "
            "of 0.1 is 0.10000000149011612, not the float64 nearest to 0.1."
        ),
        notes=[
            "One row has a null struct whose children are declared non-nullable: the children have no value "
            "in that row because their parent is null.",
        ],
        tags=["struct", "nested", "change-type", "floating-point", "widening", "nested-nulls"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: build(pa.float32()),
                [col("id", "int64", False), col("position", "struct<lat: float not null, lon: float not null>")],
            ),
            Version(
                "v1",
                lambda: build(pa.float64()),
                [col("id", "int64", False), col("position", "struct<lat: double not null, lon: double not null>")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type("position.lat", "float", "double"), change_type("position.lon", "float", "double")],
                same_rows(parquet_schema_preserved=False),
            )
        ],
    )


def reorder_nested_fields() -> Scenario:
    reordered = pa.struct(
        [pa.field("postcode", pa.string()), pa.field("street", pa.string()), pa.field("city", pa.string())]
    )
    return Scenario(
        scenario_id="reorder_nested_fields",
        title="Reorder the fields inside a struct",
        category="structural",
        description=(
            "The three fields of the address struct change order from street, city, postcode to postcode, "
            "street, city. Top-level columns, names, types and values are unchanged. Rows include a null "
            "struct, a struct whose street is null, and a struct of empty strings."
        ),
        notes=[
            "A struct's fields are addressed by name, but stored in order: a reader that matches nested "
            "fields by position pairs postcode with street.",
        ],
        tags=["struct", "nested", "reorder-fields", "nested-nulls"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("address", ADDRESS_3)], [ids(5), ADDRESSES_3]),
                [col("id", "int64", False), col("address", ADDRESS_3_TYPE)],
            ),
            Version(
                "v1",
                lambda: table([ID, field("address", reordered)], [ids(5), ADDRESSES_3]),
                [col("id", "int64", False), col("address", "struct<postcode: string, street: string, city: string>")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [reorder_fields("address", ["street", "city", "postcode"], ["postcode", "street", "city"])],
                same_rows(parquet_schema_preserved=False),
            )
        ],
    )


def drop_then_readd_column() -> Scenario:
    names = ["Ada", "Grace", "Linus", "Barbara", "Edsger", "Frances"]
    return Scenario(
        scenario_id="drop_then_readd_column",
        title="Column replaced by a different column of the same name",
        category="structural",
        description=(
            "v0 has an int32 column score with field id 3. v1 has no field id 3; it has a string column, also "
            "named score, with field id 4 and unrelated values (letter grades, a null and an empty string). "
            "By field id this is one column removed and another added. The name and the position are the same."
        ),
        notes=[
            "This is what a table history that drops a column and later adds a new one under the same name "
            "leaves in its data files: a v0 file and a v1 file whose same-named columns are different columns.",
            "Both files carry field ids on every column, so the field id is the evidence that the two score "
            "columns are distinct. A reader that matches columns by name pairs them.",
        ],
        tags=["remove-field", "add-field", "field-id", "name-reuse"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(
                    [
                        field("id", pa.int64(), nullable=False, field_id=1),
                        field("name", pa.string(), field_id=2),
                        field("score", pa.int32(), field_id=3),
                    ],
                    [ids(6), names, [90, 75, None, 0, 100, 42]],
                ),
                [col("id", "int64", False, 1), col("name", "string", True, 2), col("score", "int32", True, 3)],
            ),
            Version(
                "v1",
                lambda: table(
                    [
                        field("id", pa.int64(), nullable=False, field_id=1),
                        field("name", pa.string(), field_id=2),
                        field("score", pa.string(), field_id=4),
                    ],
                    [ids(6), names, ["A", "B", "A", None, "", "C"]],
                ),
                [col("id", "int64", False, 1), col("name", "string", True, 2), col("score", "string", True, 4)],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [remove_field("score", "int32", True), add_field("score", "string", True)],
                same_rows(paths_retained=False, parquet_schema_preserved=False),
            )
        ],
    )


def rename_then_reuse_name() -> Scenario:
    old_status = ["open", "closed", "open", None, "pending", ""]

    def fields(v1: bool) -> list[pa.Field]:
        out = [
            field("id", pa.int64(), nullable=False, field_id=1),
            field("legacy_status" if v1 else "status", pa.string(), field_id=2),
            field("amount", pa.int64(), field_id=3),
        ]
        return out + [field("status", pa.string(), field_id=4)] if v1 else out

    amounts = [10, 20, None, 0, -5, 30]
    return Scenario(
        scenario_id="rename_then_reuse_name",
        title="Rename a column, then add a new column under the old name",
        category="structural",
        description=(
            "Column status (field id 2) is renamed to legacy_status and keeps its values. A new string column "
            "named status, field id 4, is appended with different values. Both status columns are strings, "
            "so a reader that matches by name finds no type difference between them."
        ),
        notes=[
            "Every column carries a field id. By field id, status in v0 is legacy_status in v1, and status in "
            "v1 is a new column; by name, status is present in both versions with different values.",
        ],
        tags=["rename-field", "add-field", "field-id", "name-reuse"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(fields(False), [ids(6), old_status, amounts]),
                [col("id", "int64", False, 1), col("status", "string", True, 2), col("amount", "int64", True, 3)],
            ),
            Version(
                "v1",
                lambda: table(
                    fields(True),
                    [ids(6), old_status, amounts, ["active", "inactive", "active", "active", None, "inactive"]],
                ),
                [
                    col("id", "int64", False, 1),
                    col("legacy_status", "string", True, 2),
                    col("amount", "int64", True, 3),
                    col("status", "string", True, 4),
                ],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [rename_field("status", "legacy_status", 2), add_field("status", "string", True)],
                same_rows(paths_retained=False, parquet_schema_preserved=False),
            )
        ],
    )


def rename_case_only() -> Scenario:
    user_ids = [101, 102, None, 103, 104, 105]
    return Scenario(
        scenario_id="rename_case_only",
        title="Column name changes case only, without field ids",
        category="structural",
        description=(
            "Column UserId becomes userid. The files carry no field ids, so by the corpus rules this is UserId "
            "removed and userid added. The two columns hold the same int64 values, row for row."
        ),
        notes=[
            "Readers that resolve column names case-insensitively see one column in both files; case-sensitive "
            "readers see two different columns.",
            "Without field ids a rename is indistinguishable from a removal and an addition, so no rename_field "
            "is recorded (see rename_column_with_field_id).",
        ],
        tags=["remove-field", "add-field", "case-sensitivity", "no-field-id"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("UserId", pa.int64())], [ids(6), user_ids]),
                [col("id", "int64", False), col("UserId", "int64")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("userid", pa.int64())], [ids(6), user_ids]),
                [col("id", "int64", False), col("userid", "int64")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [remove_field("UserId", "int64", True), add_field("userid", "int64", True)],
                same_rows(paths_retained=False, parquet_schema_preserved=False),
            )
        ],
    )


SCENARIOS = [
    reorder_columns,
    rename_column_with_field_id,
    add_nested_field,
    remove_nested_field,
    nested_field_type_change,
    reorder_nested_fields,
    drop_then_readd_column,
    rename_then_reuse_name,
    rename_case_only,
]
