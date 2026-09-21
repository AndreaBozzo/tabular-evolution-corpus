"""The scenario registry. A plain list: adding a scenario means adding a
function to one of these modules and to its module's `SCENARIOS`."""

from __future__ import annotations

from ..models import Scenario
from . import columns, containers, nullability, numeric, representation, structural, temporal_edge


def all_scenarios() -> list[Scenario]:
    scenarios = [
        build()
        for module in (columns, numeric, nullability, representation, structural, containers, temporal_edge)
        for build in module.SCENARIOS
    ]
    ids = [s.scenario_id for s in scenarios]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise ValueError(f"duplicate scenario ids: {duplicates}")
    return scenarios
