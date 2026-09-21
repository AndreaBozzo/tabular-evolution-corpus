"""Same logical text, different physical representation."""

from __future__ import annotations

import pyarrow as pa

from ..models import Scenario, Transition, Version, change_type, col, same_rows
from ._build import ID, dictionary, field, ids, table

# Composed and decomposed forms are different code point sequences and
# compare unequal; nothing in the corpus normalizes them.
CAFE_NFC = "café"
CAFE_NFD = "café"
TOKYO = "東京"
SMILE = "\U0001f642"

STATUS_DICT = "dictionary<values=string, indices=int8, ordered=0>"
STATUS_VALUES = ["active", "inactive", "active", None, "pending", "active"]


def string_to_large_string() -> Scenario:
    labels = ["", " ", "NULL", TOKYO, CAFE_NFC, CAFE_NFD, SMILE, None]
    return Scenario(
        scenario_id="string_to_large_string",
        title="string to large_string",
        category="representation",
        description=(
            "A string column becomes large_string (64-bit offsets). Values are unchanged; they include an "
            "empty string, whitespace, the literal text NULL, CJK, an emoji, and 'cafe' with the accent both "
            "composed (U+00E9) and decomposed (e + U+0301)."
        ),
        notes=[
            "The difference exists only in the stored Arrow schema. Both files have the same Parquet schema "
            "(BYTE_ARRAY, String), so a reader that ignores the Arrow schema sees no change at all.",
        ],
        tags=["change-type", "string", "offsets", "unicode", "arrow-only"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("label", pa.string())], [ids(8), labels]),
                [col("id", "int64", False), col("label", "string")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("label", pa.large_string())], [ids(8), labels]),
                [col("id", "int64", False), col("label", "large_string")],
            ),
        ],
        transitions=[
            Transition(
                "v0", "v1", [change_type("label", "string", "large_string")], same_rows(parquet_schema_preserved=True)
            )
        ],
    )


def string_to_dictionary() -> Scenario:
    status_dict = field("status", pa.dictionary(pa.int8(), pa.string()))
    return Scenario(
        scenario_id="string_to_dictionary",
        title="string to dictionary-encoded string",
        category="representation",
        description=(
            "A plain string column becomes a dictionary<int8, string> column, as written for a pandas "
            "categorical. v0 is written without dictionary encoding; v1 with it. Decoded values are unchanged."
        ),
        notes=[
            "The Arrow type change is recorded only in the stored Arrow schema. The Parquet schema is "
            "identical; the Parquet difference is in the column chunk encodings (PLAIN vs RLE_DICTIONARY).",
            "Readers that ignore the stored Arrow schema may decode v1 as plain strings, or as a dictionary "
            "with int32 indices. The int8 index width is not representable in the Parquet schema itself.",
        ],
        tags=["change-type", "dictionary", "categorical", "encoding", "arrow-only"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("status", pa.string())], [ids(6), STATUS_VALUES]),
                [col("id", "int64", False), col("status", "string")],
                use_dictionary=False,
            ),
            Version(
                "v1",
                lambda: table(
                    [ID, status_dict],
                    [ids(6), dictionary(["active", "inactive", "pending"], [0, 1, 0, None, 2, 0])],
                ),
                [col("id", "int64", False), col("status", STATUS_DICT)],
                dictionaries={"status": ["active", "inactive", "pending"]},
            ),
        ],
        transitions=[
            Transition("v0", "v1", [change_type("status", "string", STATUS_DICT)], same_rows(parquet_schema_preserved=True))
        ],
    )


def dictionary_reencoded() -> Scenario:
    status_dict = field("status", pa.dictionary(pa.int8(), pa.string()))
    return Scenario(
        scenario_id="dictionary_reencoded",
        title="Dictionary contents change without any value change",
        category="representation",
        description=(
            "Both versions store the same dictionary<int8, string> column with the same decoded values. "
            "The stored dictionaries differ: v0 has a different order and an entry that no row uses; v1 has "
            "exactly the used values. Schema, rows and values are unchanged, so both versions have the "
            "same logical fingerprint."
        ),
        notes=[
            "Tools that compare dictionary indices, category lists or category codes observe a change here. "
            "The decoded values do not change.",
            "The unused dictionary entry survives a PyArrow round trip; other readers may rebuild the "
            "dictionary on read and drop it.",
        ],
        tags=["dictionary", "categorical", "encoding", "no-schema-change", "no-value-change"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(
                    [ID, status_dict],
                    [ids(6), dictionary(["pending", "active", "inactive", "archived"], [1, 2, 1, None, 0, 1])],
                ),
                [col("id", "int64", False), col("status", STATUS_DICT)],
                dictionaries={"status": ["pending", "active", "inactive", "archived"]},
            ),
            Version(
                "v1",
                lambda: table(
                    [ID, status_dict],
                    [ids(6), dictionary(["active", "inactive", "pending"], [0, 1, 0, None, 2, 0])],
                ),
                [col("id", "int64", False), col("status", STATUS_DICT)],
                dictionaries={"status": ["active", "inactive", "pending"]},
            ),
        ],
        transitions=[Transition("v0", "v1", [], same_rows(parquet_schema_preserved=True))],
    )


def string_to_binary() -> Scenario:
    text = ["abc", "", CAFE_NFC, CAFE_NFD, TOKYO, None]
    return Scenario(
        scenario_id="string_to_binary",
        title="string to binary holding the UTF-8 bytes",
        category="representation",
        description=(
            "A string column becomes a binary column. Each v1 value is the UTF-8 encoding of the v0 value. "
            "Text and bytes are different kinds of value, so values are not preserved even though the "
            "stored bytes are identical. The composed and decomposed 'cafe' encode to different bytes."
        ),
        notes=[
            "At the Parquet level both columns are BYTE_ARRAY; only the String logical annotation is removed.",
        ],
        tags=["change-type", "string", "binary", "unicode"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("payload", pa.string())], [ids(6), text]),
                [col("id", "int64", False), col("payload", "string")],
            ),
            Version(
                "v1",
                lambda: table(
                    [ID, field("payload", pa.binary())],
                    [ids(6), [b"abc", b"", b"caf\xc3\xa9", b"cafe\xcc\x81", b"\xe6\x9d\xb1\xe4\xba\xac", None]],
                ),
                [col("id", "int64", False), col("payload", "binary")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type("payload", "string", "binary")],
                same_rows(parquet_schema_preserved=False, value_changes=(("payload", "utf8_encoded"),)),
            )
        ],
    )


SCENARIOS = [string_to_large_string, string_to_dictionary, dictionary_reencoded, string_to_binary]
