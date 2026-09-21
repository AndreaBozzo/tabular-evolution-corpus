from __future__ import annotations

import socket
from pathlib import Path

import pytest

from tabular_evolution.validate import LoadedScenario, manifest_validator, scenario_dirs

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SCENARIO_IDS = [d.name for d in scenario_dirs(FIXTURES)]


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The corpus is self-contained: any attempt to open a connection fails."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def validator():
    return manifest_validator(ROOT)


def load(scenario_id: str) -> LoadedScenario:
    return LoadedScenario.load(FIXTURES / scenario_id)
