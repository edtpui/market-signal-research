"""A compact way to declare catalog signals: sig(reg, id, family, name, direction, fn, trigger, ...)."""
from __future__ import annotations

from typing import Callable

from strategy_lab.core.strategy import Param
from strategy_lab.signals.base import Family, SignalDef, SignalRegistry

OHLC = ("open", "high", "low", "close")
HLC = ("high", "low", "close")
OHLCV = ("open", "high", "low", "close", "volume")


def fam(reg: SignalRegistry, id: str, name: str, description: str) -> str:
    reg.add_family(Family(id, name, description))
    return id


def sig(reg: SignalRegistry, id: str, family: str, name: str, direction: str, fn: Callable, trigger: str, *,
        params: dict[str, Param] | None = None, fields: tuple[str, ...] = ("close",), warmup: int | Callable = 1,
        doc: str = "", scope: str = "stock", bench: bool = False, show: Callable | None = None,
        version: str = "1.0") -> None:
    wf = warmup if callable(warmup) else (lambda p, n=int(warmup): n)
    reg.register(SignalDef(id=id, family=family, name=name, direction=direction, fn=fn, trigger=trigger, doc=doc,
                           params=params or {}, fields=tuple(fields), warmup_fn=wf, version=version, scope=scope,
                           needs_benchmark=bench, show=show))


def I(default: int, lo: int, hi: int, desc: str = "") -> Param:  # noqa: E743 - short on purpose
    return Param(default, "int", lo, hi, description=desc)


def F(default: float, lo: float, hi: float, desc: str = "") -> Param:
    return Param(default, "float", lo, hi, description=desc)


def overlay(**series) -> dict:
    return {"overlay": series}


def osc(levels: tuple = (), **series) -> dict:
    return {"osc": series, "levels": list(levels)}
