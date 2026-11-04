"""Researched signals in the Strategy Library.

Importing a signal (or a combination) from a finished Signal Research experiment writes a small generated module
into the signal-imports directory (var/signal_imports by default). The module embeds the import record (signal id,
version, parameters, source experiment, research and validation results) and builds its Strategy class with
make_signal_strategy(). The Lab loads that directory like strategies_user/, so the strategy is backtested by the
existing engine and deployed through the existing forward adapter; the export bundle pins the signal package with it
(pin_modules).

The rule itself is deliberately plain, so a backtest measures the signal and not a hidden optimizer:
  entry  at the next session's open after the signal fires (market order, day: no bar -> no entry), in the
         signal's direction (bearish signals short, which needs execution.allow_short), equal position_size of
         equity, at most max_positions, candidates in symbol order;
  exit   at the next open after hold_bars sessions in the position (the researched horizon by default), or earlier
         when a close-based stop_loss / take_profit is hit (0 = off).
A strategy built this way is a starting point: research significance is not trading profitability.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import strategy_lab
from strategy_lab.core.strategy import Param, Strategy, StrategyContext
from strategy_lab.core.types import OrderIntent, OrderStatus, TimeInForce
from strategy_lab.signals.base import CombinationSpec, SignalSpec, evaluate, resolve_spec
from strategy_lab.signals.indicators import Indicators, Panel

MODULE_PREFIX = "strategy_lab_user"
WARNING = ("Imported from signal research. A statistically significant signal is not a profitable strategy: backtest "
           "it with costs, on data it was not selected on, before drawing any conclusion.")


def pin_modules() -> tuple[str, ...]:
    """Package-relative files a signal strategy needs besides the pinned engine (export bundle)."""
    pkg = Path(strategy_lab.__file__).resolve().parent
    files = ["strategies/signal_rule.py"] + sorted(p.relative_to(pkg).as_posix() for p in (pkg / "signals").rglob("*.py"))
    return tuple(files)


def _spec(d: dict):
    return CombinationSpec.model_validate(d) if d.get("kind") == "combination" else SignalSpec.model_validate(d)


def make_signal_strategy(record: dict, module: str) -> type[Strategy]:
    from strategy_lab.signals.library import default_signal_registry
    spec = _spec(record["signal"]["spec"])
    r = resolve_spec(spec, default_signal_registry())
    if r.key != record["signal"]["key"]:
        raise ValueError(f"{record['strategy_id']}: the signal now resolves to {r.key}, not the researched "
                         f"{record['signal']['key']} (its definition or version changed)")
    direction = r.direction
    sign = 1.0 if direction == "bullish" else -1.0
    defaults = record.get("defaults", {})
    fields = frozenset(f for f in r.fields if f in ("open", "high", "low", "close", "volume")) | {"close"}

    def _warmup(self) -> int:
        return int(r.warmup) + 5

    def _initialize(self, ctx):
        self.state.update(entries={}, pending=[], exiting=[])
        if r.needs_benchmark and self.params["benchmark"] not in ctx.universe:
            raise ValueError(f"{self.id}: the signal needs the benchmark {self.params['benchmark']} in the "
                             "universe (it is read, never traded)")

    def _on_order_event(self, ctx, e):
        st = self.state
        if e.fill is not None and e.symbol in st["pending"]:
            st["entries"][e.symbol] = dict(date=str(e.timestamp), price=float(e.fill.price))
            st["pending"].remove(e.symbol)
        elif e.status in (OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.CANCELLED):
            if e.symbol in st["pending"]:
                st["pending"].remove(e.symbol)
            if e.symbol in st["exiting"]:
                st["exiting"].remove(e.symbol)
        if e.fill is not None and e.symbol in st["exiting"] and e.status == OrderStatus.FILLED:
            st["exiting"].remove(e.symbol)
            st["entries"].pop(e.symbol, None)

    def _on_bar(self, ctx: StrategyContext):
        p, st = self.params, self.state
        bench = p["benchmark"]
        trade = [s for s in ctx.universe if s != bench]
        frames = {f: ctx.market.history(f, trade) for f in sorted(fields)}
        bser = ctx.market.history("close", [bench])[bench] if bench in ctx.market.symbols else None
        ind = Indicators(Panel(frames, benchmark=bser, benchmark_symbol=bench))
        trig = evaluate(r, ind).iloc[-1]
        fired = sorted(trig.index[trig.to_numpy(bool)])
        out = []
        held = {s: pos.quantity for s, pos in ctx.portfolio.positions.items() if pos.quantity != 0}
        closing = 0
        for s in sorted(held):
            if s in st["exiting"]:
                closing += 1
                continue
            e = st["entries"].get(s)
            age = len(ctx.market.sessions[ctx.market.sessions > pd.Timestamp(e["date"])]) if e else p["hold_bars"]
            px = ctx.market.price(s)
            ret = sign * (px / e["price"] - 1.0) if e and math.isfinite(px) else None
            stop = p["stop_loss"] > 0 and ret is not None and ret <= -p["stop_loss"]
            take = p["take_profit"] > 0 and ret is not None and ret >= p["take_profit"]
            if age >= p["hold_bars"] - 1 or stop or take:
                out.append(OrderIntent.market(s, -held[s], tag="stop" if stop else "target" if take else "exit",
                                              tif=TimeInForce.GTC))
                st["exiting"].append(s)
                closing += 1
        slots = p["max_positions"] - (len(held) - closing) - len(st["pending"])
        for s in fired:
            if slots <= 0:
                break
            if s in held or s in st["pending"] or not ctx.market.has_bar(s):
                continue
            qty = math.floor(ctx.portfolio.equity * p["position_size"] / ctx.market.price(s))
            if qty >= 1:
                out.append(OrderIntent.market(s, sign * qty, tag="entry"))
                st["pending"].append(s)
                slots -= 1
        if fired:
            ctx.log(f"signal {r.key}: {', '.join(fired)}")
        return out

    def _describe(cls) -> dict:
        d = Strategy.describe.__func__(cls)
        d["provenance"] = cls.provenance
        return d

    def _source_sha256(cls) -> str:
        pkg = Path(strategy_lab.__file__).resolve().parent
        h = hashlib.sha256()
        for rel in pin_modules():
            h.update(rel.encode())
            h.update((pkg / rel).read_bytes())
        mod = sys.modules.get(cls.__module__)
        if mod is not None and getattr(mod, "__file__", None):
            h.update(Path(mod.__file__).read_bytes())
        return h.hexdigest()

    hold = int(defaults.get("hold_bars", 20))
    attrs = dict(
        __module__=module, id=record["strategy_id"], version=record["version"], name=record["name"],
        description=(f"Researched signal {r.key}: {r.trigger}. {direction.capitalize()} entries at the next open, "
                     f"exit after hold_bars sessions or at a close-based stop/target. {WARNING}"),
        category="signal",
        params_schema={
            "hold_bars": Param(hold, "int", 1, 252, description="sessions in a position (the researched horizon)"),
            "max_positions": Param(int(defaults.get("max_positions", 10)), "int", 1, 500),
            "position_size": Param(float(defaults.get("position_size", 0.1)), "float", 0.005, 1.0,
                                   description="share of equity per new position"),
            "stop_loss": Param(0.0, "float", 0.0, 0.9, description="close-based stop (0 = off): exit at the next "
                                                                   "open once the close is this far against entry"),
            "take_profit": Param(0.0, "float", 0.0, 5.0, description="close-based target (0 = off)"),
            "benchmark": Param(record.get("benchmark", "SPY"), "str",
                               description="read for relative strength / regime filters, never traded"),
        },
        required_fields=fields, order_types=frozenset({"market"}), long_only=direction == "bullish",
        provenance=record, pin_modules=pin_modules(), signal_key=r.key,
        warmup=_warmup, initialize=_initialize, on_order_event=_on_order_event, on_bar=_on_bar,
        describe=classmethod(_describe), source_sha256=classmethod(_source_sha256))
    cls_name = "Signal_" + re.sub(r"[^A-Za-z0-9]", "_", record["strategy_id"])
    return type(cls_name, (Strategy,), attrs)


MODULE = '''"""Strategy Lab signal import {strategy_id} (generated by strategy_lab.strategies.signal_rule; do not edit).
The file's sha256 is part of the strategy's identity: a changed record is a new strategy, never an edit of this one.
Source experiment {experiment_id}, signal {key}.
"""
import json

from strategy_lab.strategies.signal_rule import make_signal_strategy

RECORD = json.loads(r"""{record}""")
{cls} = make_signal_strategy(RECORD, __name__)
'''


def strategy_id_for(signal_id: str, experiment_id: str, key: str) -> str:
    h = hashlib.sha256(f"{experiment_id}|{key}".encode()).hexdigest()[:6]
    return f"sig_{signal_id[:40]}_{h}"


def write_module(record: dict, directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['strategy_id']}.py"
    text = MODULE.format(strategy_id=record["strategy_id"], experiment_id=record["research"]["experiment_id"],
                         key=record["signal"]["key"], record=json.dumps(record, indent=1, sort_keys=True),
                         cls="Signal_" + re.sub(r"[^A-Za-z0-9]", "_", record["strategy_id"]))
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"{path} exists with a different record; imports are write-once")
        return path
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load_module(path: Path, registry) -> list[type[Strategy]]:
    """Registers the strategies of one generated module (the same module naming as StrategyRegistry.load_dir)."""
    from strategy_lab.strategies.registry import _strategies_in
    modname = f"{MODULE_PREFIX}.{Path(path).stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    out = []
    for cls in _strategies_in(mod):
        registry.register(cls, f"signal-import:{Path(path).name}")
        out.append(cls)
    return out


__all__ = ["make_signal_strategy", "write_module", "load_module", "strategy_id_for", "pin_modules", "WARNING", "np"]
