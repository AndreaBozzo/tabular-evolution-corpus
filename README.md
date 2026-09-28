# tabular-evolution-corpus

[![CI](https://github.com/AndreaBozzo/tabular-evolution-corpus/actions/workflows/ci.yml/badge.svg)](https://github.com/AndreaBozzo/tabular-evolution-corpus/actions/workflows/ci.yml)
[![Hugging Face dataset](https://img.shields.io/badge/%F0%9F%A4%97-dataset-yellow)](https://huggingface.co/datasets/AndreaBozzo/tabular-evolution-corpus)

Tabular datasets change shape over time. This corpus has **35 small, versioned
Parquet scenarios** that record what changed: schemas, structural mutations,
rows, and values. It leaves compatibility decisions to the system you test.

Each scenario contains versioned Parquet files and a `scenario.json` manifest.
The validator re-derives every declared fact from the files. A Parquet catalog
at [`data/scenarios.parquet`](data/scenarios.parquet) makes the scenarios easy
to browse. See [`int64_to_float64`](fixtures/int64_to_float64/scenario.json)
for a concrete example.

## Try it

```bash
uv sync
uv run tec list
uv run tec inspect int64_to_float64
uv run tec validate
```

You can also use the fixtures directly without installing the Python package.
See [using the corpus](docs/USAGE.md) for examples.

## Read more

- [Scenario inventory](docs/SCENARIOS.md) — all 35 changes, with a manifest example.
- [Using the corpus](docs/USAGE.md) — catalog queries and fixture consumption.
- [Engine observations](docs/OBSERVATIONS.md) — PyArrow, DuckDB, Polars, and Delta Lake.
- [Development and releases](docs/DEVELOPMENT.md) — regeneration, contribution, and versioning.
- [Design](DESIGN.md) — the fact model and validation rules.

The dataset is also available on [Hugging Face](https://huggingface.co/datasets/AndreaBozzo/tabular-evolution-corpus).
Licensed under [Apache-2.0](LICENSE); citation details are in [CITATION.cff](CITATION.cff).
