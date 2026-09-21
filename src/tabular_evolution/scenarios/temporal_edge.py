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


SCENARIOS = [timestamp_naive_to_utc, empty_then_populated]
