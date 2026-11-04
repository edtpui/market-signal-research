"""The strategy interface. A strategy never knows whether it runs in a backtest or forward on the paper host: both
drive it through engine.runner.StrategyRunner with the same context.

Lifecycle: __init__(params) -> initialize(ctx) once -> each session after its close: on_order_event(ctx, e) for the
session's order events, then on_bar(ctx) -> intents -> shutdown(ctx) once at the end of the run.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable, Mapping

import numpy as np
import pandas as pd

from strategy_lab.core.market import MarketView
from strategy_lab.core.types import Intent, Order, OrderEvent, PortfolioSnapshot


class ParamError(ValueError):
    pass


@dataclass(frozen=True)
class Param:
    """A typed strategy parameter. kind: int | float | bool | str | choice | symbols."""
    default: Any
    kind: str = "float"
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None
    description: str = ""

    def validate(self, name: str, value: Any) -> Any:
        k = self.kind
        try:
            if k == "int":
                if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
                    raise ParamError(f"{name}: expected an integer, got {value!r}")
                value = int(value)
            elif k == "float":
                if isinstance(value, bool):
                    raise ParamError(f"{name}: expected a number, got {value!r}")
                value = float(value)
                if not np.isfinite(value):
                    raise ParamError(f"{name}: must be finite")
            elif k == "bool":
                if not isinstance(value, bool):
                    raise ParamError(f"{name}: expected true/false, got {value!r}")
            elif k in ("str", "choice"):
                value = str(value)
            elif k == "symbols":
                if isinstance(value, str):
                    value = [s.strip() for s in value.split(",") if s.strip()]
                value = [str(s) for s in value]
            else:
                raise ParamError(f"{name}: unknown parameter kind {k!r}")
        except (TypeError, ValueError) as e:
            if isinstance(e, ParamError):
                raise
            raise ParamError(f"{name}: cannot use {value!r} as {k}") from None
        if self.choices is not None and value not in self.choices:
            raise ParamError(f"{name}: {value!r} is not one of {list(self.choices)}")
        if self.min is not None and k in ("int", "float") and value < self.min:
            raise ParamError(f"{name}: {value} is below the minimum {self.min}")
        if self.max is not None and k in ("int", "float") and value > self.max:
            raise ParamError(f"{name}: {value} is above the maximum {self.max}")
        return value

    def describe(self) -> dict:
        return dict(default=self.default, kind=self.kind, min=self.min, max=self.max,
                    choices=list(self.choices) if self.choices else None, description=self.description)


@dataclass(frozen=True)
class ExecutionInfo:
    """What a strategy may know about execution: its open orders, the session's order events and the timing rule."""
    open_orders: tuple[Order, ...]
    events: tuple[OrderEvent, ...]
    fill_timing: str
    session_index: int
    sessions_run: int


@dataclass
class StrategyContext:
    timestamp: pd.Timestamp
    market: MarketView
    portfolio: PortfolioSnapshot
    params: Mapping[str, Any]
    execution: ExecutionInfo
    universe: tuple[str, ...]
    state: dict
    rng: np.random.Generator
    log: Callable[[str], None]

    @property
    def cash(self) -> float:
        return self.portfolio.cash

    @property
    def positions(self):
        return self.portfolio.positions


@dataclass
class InitContext:
    universe: tuple[str, ...]
    params: Mapping[str, Any]
    start: pd.Timestamp
    end: pd.Timestamp
    initial_capital: float
    fields: frozenset[str]
    state: dict
    rng: np.random.Generator
    log: Callable[[str], None]


class Strategy(ABC):
    """Subclass, set the class attributes, implement on_bar. Everything a run depends on must be in params."""

    id: ClassVar[str]
    version: ClassVar[str]
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    category: ClassVar[str] = "custom"          # rule | indicator | allocation | ml | ensemble | custom
    params_schema: ClassVar[dict[str, Param]] = {}
    required_fields: ClassVar[frozenset[str]] = frozenset({"close"})
    order_types: ClassVar[frozenset[str]] = frozenset({"market"})
    long_only: ClassVar[bool] = True
    warmup_bars: ClassVar[int] = 0              # history the strategy needs before it can decide (from params or fixed)

    def __init__(self, params: Mapping[str, Any] | None = None):
        self.params: dict[str, Any] = self.resolve_params(params or {})
        self.state: dict = {}

    @classmethod
    def resolve_params(cls, params: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(params) - set(cls.params_schema)
        if unknown:
            raise ParamError(f"{cls.id}: unknown parameters {sorted(unknown)}")
        out = {}
        for name, spec in cls.params_schema.items():
            out[name] = spec.validate(name, params[name] if name in params else spec.default)
        return out

    def warmup(self) -> int:
        """Bars of history needed; override when it depends on params."""
        return int(self.warmup_bars)

    def initialize(self, ctx: InitContext) -> None:
        """Called once before the first session."""

    @abstractmethod
    def on_bar(self, ctx: StrategyContext) -> Iterable[Intent] | None:
        """Called after each session's close; returns intents for the next session."""

    def on_order_event(self, ctx: StrategyContext, event: OrderEvent) -> None:
        """Called for every order event of the session (submitted, fills, expiry, ...), before on_bar."""

    def shutdown(self, ctx: StrategyContext) -> None:
        """Called once after the last session."""

    def get_state(self) -> dict:
        return copy.deepcopy(self.state)

    def set_state(self, state: Mapping) -> None:
        self.state = copy.deepcopy(dict(state))

    # -- identity --------------------------------------------------------------------------------------------------
    @classmethod
    def source_sha256(cls) -> str:
        """sha256 of the module file that defines the strategy: a code change is visible in every manifest."""
        try:
            path = Path(inspect.getfile(cls))
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except (TypeError, OSError):
            return hashlib.sha256(inspect.getsource(cls).encode()).hexdigest()

    @classmethod
    def describe(cls) -> dict:
        return dict(id=cls.id, version=cls.version, name=cls.name or cls.id, description=cls.description,
                    category=cls.category, params={k: v.describe() for k, v in cls.params_schema.items()},
                    required_fields=sorted(cls.required_fields), order_types=sorted(cls.order_types),
                    long_only=cls.long_only, source_sha256=cls.source_sha256())


def canonical_hash(obj: Any) -> str:
    """Stable sha256 over JSON (sorted keys, compact): the same convention as ptrade.strategy.canonical_config_hash."""
    payload = json.dumps(obj, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


__all__ = ["Strategy", "StrategyContext", "InitContext", "ExecutionInfo", "Param", "ParamError", "canonical_hash"]
