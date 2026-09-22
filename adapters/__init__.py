"""Adapters: what engines do with the corpus's transitions.

Not part of the corpus package. `tabular_evolution` depends on PyArrow and
jsonschema only; the engines driven here come from the `adapters` dependency
group (`uv sync --group adapters`). Results are observations about engines and
never modify the corpus.
"""

from __future__ import annotations

from importlib import import_module

from .base import Adapter

# name -> "module:class", imported on demand so that running one adapter does
# not require every engine to be installed.
REGISTRY: dict[str, str] = {
    "pyarrow_dataset": ".pyarrow_adapter:PyArrowDatasetAdapter",
}


def load_adapter(name: str) -> Adapter:
    if name not in REGISTRY:
        raise KeyError(f"unknown adapter {name!r}; known: {', '.join(sorted(REGISTRY))}")
    module, cls = REGISTRY[name].split(":")
    return getattr(import_module(module, __name__), cls)()
