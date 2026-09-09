"""Load and validate a strategy YAML file against the schema.

A bad definition raises `pydantic.ValidationError` with a clear field
path — the CLI's `validate-strategy` command surfaces that directly to
the trader instead of a stack trace.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from .schema import StrategyDefinition


def load_strategy(path: Path | str) -> StrategyDefinition:
    path = Path(path)
    with path.open() as fh:
        raw = yaml.safe_load(fh)
    return StrategyDefinition.model_validate(raw)


def load_strategies(directory: Path | str) -> list[StrategyDefinition]:
    directory = Path(directory)
    return [load_strategy(p) for p in sorted(directory.glob("*.yaml"))]
