"""Timestamps, and the empty-then-populated edge case."""

from __future__ import annotations

import pyarrow as pa

from ..models import Invariants, Scenario, Transition, Version, change_type, col, same_rows
from ._build import ID, field, ids, table

US_PER_S = 1_000_000

# Stored microseconds since the epoch. Deliberately written as integers so
# nothing time-zone-dependent is involved in building the fixture.
EVENT_TIMES = [
    -1,  # 1969-12-31T23:59:59.999999, one tick before the epoch
    0,  # 1970-01-01T00:00:00
    1709208000 * US_PER_S,  # 2024-02-29T12:00:00, a leap day
    1711852200 * US_PER_S,  # 2024-03-31T02:30:00, inside the Central European DST gap as a wall clock
    None,
]


def timestamp_naive_to_utc() -> Scenario:
    return Scenario(
        scenario_id="timestamp_naive_to_utc",
        title="Naive timestamp becomes UTC timestamp",
        category="temporal",
        description=(
            "A timestamp column without a time zone becomes timestamp with tz=UTC. The stored integers are "
            "identical, so each wall-clock value in v0 is reinterpreted as the same wall-clock value in UTC. "
            "A naive value and a zoned value are not the same value."
        ),
        notes=[
            "At the Parquet level this is the isAdjustedToUTC flag of the Timestamp logical type changing "
            "from false to true; the physical INT64 values are unchanged.",
            "Values include one microsecond before the epoch, the epoch, a leap day and a wall-clock time that "
            "does not exist in Central European time.",
        ],
        tags=["change-type", "timestamp", "timezone", "utc"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table([ID, field("event_time", pa.timestamp("us"))], [ids(5), EVENT_TIMES]),
                [col("id", "int64", False), col("event_time", "timestamp[us]")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("event_time", pa.timestamp("us", tz="UTC"))], [ids(5), EVENT_TIMES]),
                [col("id", "int64", False), col("event_time", "timestamp[us, tz=UTC]")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type("event_time", "timestamp[us]", "timestamp[us, tz=UTC]")],
                same_rows(parquet_schema_preserved=False, value_changes=(("event_time", "interpreted_as_utc"),)),
            )
        ],
    )


# Stored microseconds; the nanosecond version stores each times 1000. The
# extremes are the int64 nanosecond range rounded inward to whole microseconds.
UNIT_TIMES = [
    -1,  # 1969-12-31T23:59:59.999999
    0,
    1709208000 * US_PER_S,  # 2024-02-29T12:00:00
    9223372036854775,  # 2262-04-11T23:47:16.854775, the last whole microsecond timestamp[ns] holds
    -9223372036854775,  # 1677-09-21T00:12:43.145225, the first
    None,
]


def timestamp_ns_to_us() -> Scenario:
    return Scenario(
        scenario_id="timestamp_ns_to_us",
        title="Timestamp unit changes from nanoseconds to microseconds",
        category="temporal",
        description=(
            "A naive timestamp column changes unit from nanoseconds to microseconds. Every v0 value is a whole "
            "number of microseconds, so every instant is unchanged. Values include both ends of the range "
            "timestamp[ns] can hold, one microsecond before the epoch, and a leap day."
        ),
        notes=[
            "At the Parquet level the Timestamp logical type's unit changes from NANOS to MICROS and every "
            "stored INT64 is divided by 1000.",
            "Timestamps compare by instant, independent of unit, so no value changes. The v0 extremes sit at "
            "the edges of the int64 nanosecond range; the microsecond range is 1000 times wider.",
        ],
        tags=["change-type", "timestamp", "unit", "nanoseconds", "range-extremes"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(
                    [ID, field("event_time", pa.timestamp("ns"))],
                    [ids(6), [None if t is None else t * 1000 for t in UNIT_TIMES]],
                ),
                [col("id", "int64", False), col("event_time", "timestamp[ns]")],
            ),
            Version(
                "v1",
                lambda: table([ID, field("event_time", pa.timestamp("us"))], [ids(6), UNIT_TIMES]),
                [col("id", "int64", False), col("event_time", "timestamp[us]")],
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [change_type("event_time", "timestamp[ns]", "timestamp[us]")],
                same_rows(parquet_schema_preserved=False),
            )
        ],
    )


def empty_then_populated() -> Scenario:
    fields = [
        ID,
        field("label", pa.string()),
        field("amount", pa.float64()),
        field("observed_at", pa.timestamp("us", tz="UTC")),
    ]
    decl = [
        col("id", "int64", False),
        col("label", "string"),
        col("amount", "double"),
        col("observed_at", "timestamp[us, tz=UTC]"),
    ]
    return Scenario(
        scenario_id="empty_then_populated",
        title="Empty typed file followed by a populated file",
        category="edge",
        description=(
            "v0 has a fully typed schema and zero rows, the Parquet counterpart of a header-only CSV. v1 has "
            "the identical schema and five rows whose values include NaN, both infinities, -0.0, an empty "
            "string, whitespace, the literal text NULL and a timestamp before the epoch."
        ),
        notes=[
            "The row and value invariants quantify over the rows of v0, of which there are none, so "
            "rows_retained and common_values_preserved are vacuously true.",
            "v0 carries complete type information in its schema and none in its values, because it has no values.",
        ],
        tags=["empty", "zero-rows", "no-schema-change", "nan", "infinity", "unicode"],
        row_identity=["id"],
        versions=[
            Version("v0", lambda: table(fields, [[], [], [], []]), decl),
            Version(
                "v1",
                lambda: table(
                    fields,
                    [
                        ids(5),
                        ["", "  ", "NULL", "café", None],
                        [float("nan"), float("inf"), float("-inf"), -0.0, None],
                        [0, 1700000000 * US_PER_S, None, -1, 1709208000 * US_PER_S],
                    ],
                ),
                decl,
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [],
                Invariants(
                    row_count_preserved=False,
                    row_identity_preserved=False,
                    rows_retained=True,
                    paths_retained=True,
                    common_values_preserved=True,
                    parquet_schema_preserved=True,
                ),
            )
        ],
    )


def rows_deleted() -> Scenario:
    fields = [ID, field("status", pa.string()), field("amount", pa.int64())]
    decl = [col("id", "int64", False), col("status", "string"), col("amount", "int64")]
    return Scenario(
        scenario_id="rows_deleted",
        title="Rows deleted from a keyed dataset",
        category="edge",
        description=(
            "Two of six rows, ids 2 and 5, are absent from v1. The four remaining rows keep their values and "
            "their relative order. The schema is unchanged."
        ),
        notes=[
            "rows_retained is false. common_values_preserved quantifies over the rows present in both "
            "versions, and those rows are unchanged.",
            "v1 has no rows for ids 2 and 5, rather than rows with nulls or empty values. The deleted rows "
            "held an empty status (id 2) and a null amount (id 5).",
        ],
        tags=["delete", "keyed", "no-schema-change"],
        row_identity=["id"],
        versions=[
            Version(
                "v0",
                lambda: table(
                    fields,
                    [ids(6), ["open", "", "closed", "open", "closed", "open"], [10, 20, 30, 40, None, 0]],
                ),
                decl,
            ),
            Version(
                "v1",
                lambda: table(fields, [[1, 3, 4, 6], ["open", "closed", "open", "open"], [10, 30, 40, 0]]),
                decl,
            ),
        ],
        transitions=[
            Transition(
                "v0",
                "v1",
                [],
                Invariants(
                    row_count_preserved=False,
                    row_identity_preserved=False,
                    rows_retained=False,
                    paths_retained=True,
                    common_values_preserved=True,
                    parquet_schema_preserved=True,
                ),
            )
        ],
    )


SCENARIOS = [timestamp_naive_to_utc, timestamp_ns_to_us, empty_then_populated, rows_deleted]
