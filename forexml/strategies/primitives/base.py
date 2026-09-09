"""Primitive registry and evaluation context.

Adding a new filter, entry condition, or exit rule is one small class
plus a `@register_*` decorator — never a change to `strategies/engine.py`.
That is the extensibility the brief requires: growing the vocabulary a
trader's YAML can use is not an engineering-core change.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

import polars as pl

from ...features.context.calendar import EconomicCalendar

_FILTER_REGISTRY: dict[str, type["Filter"]] = {}
_CONDITION_REGISTRY: dict[str, type["Condition"]] = {}
_EXIT_REGISTRY: dict[str, type["ExitRule"]] = {}


@dataclass
class EvalContext:
    symbol: str
    timestamp: datetime
    timeframe: str
    features: dict
    bars: pl.DataFrame  # closed bars only, already point-in-time truncated
    calendar: EconomicCalendar


class Filter(abc.ABC):
    def __init__(self, spec: dict):
        self.spec = spec

    @abc.abstractmethod
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        """Return (passes, reason_if_not)."""


class Condition(abc.ABC):
    def __init__(self, spec: dict):
        self.spec = spec

    @abc.abstractmethod
    def evaluate(self, ctx: EvalContext) -> tuple[bool, str | None]:
        """Return (met, reason_if_not)."""


class ExitRule(abc.ABC):
    def __init__(self, spec: dict):
        self.spec = spec

    @abc.abstractmethod
    def compute(
        self,
        entry_price: float,
        direction: str,
        ctx: EvalContext,
        stop_price: float | None = None,
    ) -> float:
        """Return the absolute price level for this exit leg."""


def register_filter(type_name: str):
    def deco(cls):
        _FILTER_REGISTRY[type_name] = cls
        return cls

    return deco


def register_condition(type_name: str):
    def deco(cls):
        _CONDITION_REGISTRY[type_name] = cls
        return cls

    return deco


def register_exit(key: str):
    """`key` is "<stop_loss|take_profit>:<type>" — the same type name can
    mean different things in each slot."""

    def deco(cls):
        _EXIT_REGISTRY[key] = cls
        return cls

    return deco


def build_filter(spec: dict) -> Filter:
    cls = _FILTER_REGISTRY.get(spec["type"])
    if cls is None:
        raise ValueError(f"unknown filter type {spec['type']!r}; known: {sorted(_FILTER_REGISTRY)}")
    return cls(spec)


def build_condition(spec: dict) -> Condition:
    cls = _CONDITION_REGISTRY.get(spec["type"])
    if cls is None:
        raise ValueError(
            f"unknown condition type {spec['type']!r}; known: {sorted(_CONDITION_REGISTRY)}"
        )
    return cls(spec)


def build_exit(spec: dict, kind: str) -> ExitRule:
    key = f"{kind}:{spec['type']}"
    cls = _EXIT_REGISTRY.get(key)
    if cls is None:
        raise ValueError(f"unknown {kind} type {spec['type']!r}; known: {sorted(_EXIT_REGISTRY)}")
    return cls(spec)
