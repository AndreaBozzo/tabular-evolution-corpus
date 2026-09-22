"""What an adapter is and what it reports.

An adapter drives one engine. It declares, per operation, the closed list of
modes it runs, and splits each call in two:

- `read` makes the engine call. An exception raised here is the engine's
  answer and becomes an `error` record.
- `describe` turns a successful result into an `Observation`. An exception
  raised here is a bug in the adapter and stops the run, so it can never be
  recorded as something the engine did.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

OPERATIONS = ("read_each_version", "read_versions_together")


@dataclass(frozen=True)
class ResultField:
    name: str
    type: str | None  # corpus type vocabulary; None when there is no clear equivalent
    native_type: str  # the engine's own spelling, verbatim
    nullable: bool | None  # None when the engine records no nullability

    def to_record(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "native_type": self.native_type, "nullable": self.nullable}


@dataclass(frozen=True)
class Observation:
    fields: list[ResultField]
    row_count: int
    notes: list[str] = field(default_factory=list)


class Adapter(ABC):
    name: ClassVar[str]
    modes: ClassVar[dict[str, tuple[str, ...]]]

    @property
    @abstractmethod
    def version(self) -> str:
        """The engine version, as the engine reports it."""

    @abstractmethod
    def read(self, operation: str, mode: str, paths: list[str]) -> Any:
        """Run the engine. `paths` are relative to the corpus root, which is
        the working directory during the call."""

    @abstractmethod
    def describe(self, result: Any) -> Observation:
        """Describe what `read` returned, without converting values to Python."""
