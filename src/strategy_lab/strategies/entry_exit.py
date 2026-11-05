"""Entry-exit systems in the event-driven engine: an entry signal (a catalog signal or combination) paired with an
exit plan (entryexit/plan.py), with position sizing and a position cap. The same plan code drives the trade replay of
the Entry-Exit Research Lab, so a researched pairing and its backtest follow one set of rules.

Session loop (after each close t, data up to t):
  1. fills of the session: entry fills open a position (entry price = the fill price), tranche and stop fills reduce
     it; the plan's state is updated from them;
  2. held positions with a valid bar: the plan observes the bar and decides the next session's orders: a market exit
     (DAY), or a DAY stop for the whole position followed by DAY limits for the open target tranches (stop first:
     the broker executes sells in submission order, so a bar that reaches both is a stop: the conservative rule);
     on a missing bar the previous orders are placed again;
  3. new entries: symbols whose entry fires at t, that are not held or pending, in a deterministic pseudo-random
     order (sha256 of seed, session and symbol: no alphabetical bias), while slots remain; a market buy for the next
     open with the plan's initial stop and targets attached (core.types.AttachedExit), so the entry session itself is
     protected after the fill.
Sizing: risk (shares = risk_per_position x equity / initial risk per share, the plan's own stop distance or the
common ATR reference, capped at max_position_weight of equity) or weight (position_weight of equity).

Entries come from the signal (evaluated in precompute over the loaded data; causal, checked by verify_precompute)
filtered by point-in-time eligibility on the available bars, or from an explicit event mask injected by the research
engine (the identical events its trade replay used, and the matched-random control events).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

import strategy_lab
from strategy_lab.core.strategy import Param, Strategy, StrategyContext
from strategy_lab.core.types import AttachedExit, OrderIntent, OrderStatus, OrderType, TimeInForce
from strategy_lab.entryexit.components import Common, Ctx
from strategy_lab.entryexit.plan import ExitPlanSpec, PlanRuntime, resolve_plan
from strategy_lab.signals.base import CombinationSpec, SignalSpec, evaluate, resolve_spec
from strategy_lab.signals.indicators import Indicators, Panel

MODULE_PREFIX = "strategy_lab_user"
EntryItem = Annotated[Union[SignalSpec, CombinationSpec], Field(discriminator="kind")]
WARNING = ("Constructed in the Entry-Exit Research Lab. A historical pairing result is not evidence of future "
           "profitability: it must survive chronological out-of-sample tests, costs and forward paper testing.")


class Sizing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["risk", "weight"] = "risk"
    risk_per_position: float = Field(0.01, gt=0, le=0.2, description="share of equity risked per position")
    position_weight: float = Field(0.1, gt=0, le=1.0, description="weight mode: share of equity per position")
    max_position_weight: float = Field(0.25, gt=0, le=1.0, description="cap on one position's entry weight")


class EntryEligibility(BaseModel):
    """Point-in-time filters on the strategy's own bars (adjusted prices; the research engine uses raw prices for the
    price and dollar-volume floors, so research and a standalone run can differ slightly: see the record)."""
    model_config = ConfigDict(extra="forbid")
    min_history: int = Field(126, ge=1, le=1000)
    min_price: float = Field(5.0, ge=0)
    min_dollar_volume: float = Field(1_000_000.0, ge=0)


class EntryExitDefinition(BaseModel):
    """Everything that decides an entry-exit system's trades."""
    model_config = ConfigDict(extra="forbid")
    entry: EntryItem | None = None
    exit: ExitPlanSpec
    ref_atr_n: int = Field(14, ge=2, le=100)
    ref_atr_mult: float = Field(2.0, gt=0, le=20)
    max_hold: int = Field(252, ge=1, le=2520)
    sizing: Sizing = Field(default_factory=Sizing)
    max_positions: int = Field(10, ge=1, le=500)
    eligibility: EntryEligibility = Field(default_factory=EntryEligibility)
    benchmark: str = "SPY"
    seed: int = 0


def pin_modules() -> tuple[str, ...]:
    pkg = Path(strategy_lab.__file__).resolve().parent
    files = ["strategies/entry_exit.py"] + sorted(p.relative_to(pkg).as_posix() for p in (pkg / "entryexit").rglob("*.py"))
    files += sorted(p.relative_to(pkg).as_posix() for p in (pkg / "signals").rglob("*.py"))
    return tuple(files)


def _order_key(seed: int, ts: str, sym: str) -> str:
    return hashlib.sha256(f"{seed}|{ts}|{sym}".encode()).hexdigest()


class EntryExitBase(Strategy):
    """Base of every entry-exit system: subclasses set `definition` (an EntryExitDefinition)."""
    definition: EntryExitDefinition
    category = "entry_exit"
    requires_historical_test = True

    # -- setup --------------------------------------------------------------------------------------------------
    @classmethod
    def plan(cls):
        return resolve_plan(cls.definition.exit)

    @classmethod
    def entry(cls):
        if cls.definition.entry is None:
            return None
        from strategy_lab.signals.library import default_signal_registry
        return resolve_spec(cls.definition.entry, default_signal_registry())

    def _runtime(self) -> PlanRuntime:
        d = self.definition
        p = self.plan()
        return PlanRuntime(p, Common(d.ref_atr_n, d.ref_atr_mult, has_high_low=True), max_hold=d.max_hold)

    def warmup(self) -> int:
        e = self.entry()
        return int(max(self._runtime().warmup(), e.warmup if e else 1, self.definition.eligibility.min_history)) + 5

    def inject(self, frames: dict[str, pd.DataFrame], complete: bool = False) -> "EntryExitBase":
        """Research engine: the entry event mask ('entry', events of the trade replay or of a random control) and,
        with complete=True, every other precomputed series too (shared indicators: nothing is recomputed)."""
        self._injected = (dict(frames), bool(complete))
        return self

    def _trade_symbols(self, symbols) -> list[str]:
        b = self.definition.benchmark
        return [s for s in symbols if s != b]

    def precompute(self, data) -> dict[str, pd.DataFrame]:
        trade = self._trade_symbols(data.symbols)
        inj, complete = getattr(self, "_injected", None) or ({}, False)
        rt = self._runtime()
        fields = {k: v.loc[:, trade] for k, v in data.fields.items() if k in ("open", "high", "low", "close", "volume")}
        out: dict[str, pd.DataFrame] = {f: fields[f] for f in ("open", "high", "low", "close") if f in fields}
        if complete:
            out.update(inj)
            return out
        b = self.definition.benchmark
        bser = data.fields["close"][b] if b in data.symbols else None
        ind = Indicators(Panel(fields, benchmark=bser, benchmark_symbol=b))
        out.update(rt.series(ind))
        if "entry" in inj:
            out["entry"] = inj["entry"].reindex(index=data.index, columns=trade).fillna(0.0).astype(float)
        else:
            e = self.entry()
            if e is None:
                raise ValueError(f"{self.id}: no entry signal and no injected entry events")
            if e.needs_benchmark and bser is None:
                raise ValueError(f"{self.id}: the entry signal needs the benchmark {b} in the universe (read, never "
                                 "traded)")
            trig = evaluate(e, ind)
            out["entry"] = (trig & self._eligible(ind, fields)).astype(float)
        return out

    def _eligible(self, ind: Indicators, fields: dict) -> pd.DataFrame:
        el = self.definition.eligibility
        close = fields["close"]
        valid = ind.valid(tuple(f for f in ("open", "close") if f in fields))
        hist = valid.astype(float).cumsum()
        ok = valid & (hist >= el.min_history) & (close >= el.min_price)
        if el.min_dollar_volume > 0 and "volume" in fields:
            dv = (close * fields["volume"]).rolling(21, min_periods=11).median()
            ok &= dv >= el.min_dollar_volume
        return ok.fillna(False)

    def initialize(self, ctx):
        self.state.update(positions={}, pending={})
        self._rt = self._runtime()

    # -- the session --------------------------------------------------------------------------------------------
    def on_bar(self, ctx: StrategyContext):
        rt = self._rt
        st = self.state
        mk = ctx.market
        i = mk.position
        cols = {s: k for k, s in enumerate(mk.derived_columns("close"))}
        held = {s: p.quantity for s, p in ctx.portfolio.positions.items() if p.quantity > 0}
        fills: dict[str, list] = {}
        for ev in ctx.execution.events:
            if ev.fill is not None:
                fills.setdefault(ev.symbol, []).append(ev)

        # 1. entry fills open positions; same-session attached fills reduce them
        for sym in list(st["pending"]):
            rec = st["pending"].pop(sym)
            evs = [e for e in fills.get(sym, []) if e.tag == "entry"]
            if not evs:
                continue                                      # the entry order expired or was rejected
            qty = sum(e.fill.quantity for e in evs)
            price = sum(e.fill.quantity * e.fill.price for e in evs) / qty
            B = {k: np.array([v]) for k, v in rec["B"].items()}
            rt.at_fill(B, np.array([0]), np.array([price]))
            pos = dict(B={k: float(v[0]) for k, v in B.items()}, e=i, qty0=float(qty),
                       orders=self._entry_orders(rt, B))
            st["positions"][sym] = pos
        for sym, pos in list(st["positions"].items()):
            B = {k: np.array([v]) for k, v in pos["B"].items()}
            for ev in fills.get(sym, []):
                if ev.tag.startswith("target:"):
                    k = int(ev.tag.split(":")[1])
                    frac = min(abs(ev.fill.quantity) / pos["qty0"], float(B["rem"][0]))
                    rt.tranche_filled(B, np.array([0]), k, np.array([frac]))
            pos["B"] = {k: float(v[0]) for k, v in B.items()}
            if held.get(sym, 0.0) <= 0:
                del st["positions"][sym]

        # 2. held positions: observe the bar and decide the next session's orders
        out = []
        syms = sorted(s for s in st["positions"] if held.get(s, 0) > 0)
        if syms:
            B = {k: np.array([st["positions"][s]["B"][k] for s in syms]) for k in rt.keys()}
            jj = np.array([cols[s] for s in syms])
            ix = np.arange(len(syms))
            B["bars"][:] = [i - st["positions"][s]["e"] for s in syms]

            def getter(key, sel):
                return mk.derived_now(key)[jj[sel]]

            ok = np.array([self._bar_ok(mk, cols[s], rt.common.has_high_low) for s in syms])
            s_ok = ix[ok]
            if len(s_ok):
                ctx_ok = Ctx(getter, s_ok)
                rt.observe(ctx_ok, B, s_ok)
                d = rt.decide(ctx_ok, B, s_ok)
                for n_, k in enumerate(s_ok):
                    sym = syms[k]
                    if d.exit_now[n_]:
                        orders = [("exit", rt.reason_label(int(d.reason[n_])), None, None)]
                    else:
                        orders = []
                        if np.isfinite(d.stop[n_]):
                            orders.append(("stop", rt.reason_label(int(d.stop_reason[n_])), float(d.stop[n_]), 1.0))
                        for tk, lvl, frac in d.targets:
                            if frac[n_] > 0 and np.isfinite(lvl[n_]):
                                orders.append((f"target:{tk}", "", float(lvl[n_]), float(frac[n_])))
                    st["positions"][sym]["orders"] = orders
            for k, sym in enumerate(syms):
                pos = st["positions"][sym]
                pos["B"] = {key: float(B[key][k]) for key in rt.keys()}
                out += self._intents(sym, pos, held[sym], ctx.execution.fractional_shares)

        # 3. new entries
        slots = self.params["max_positions"] - len(syms) - len(st["pending"])
        if slots > 0:
            trig = mk.derived_now("entry")
            names = mk.derived_columns("entry")
            fired = [names[k] for k in np.flatnonzero(trig > 0.5)]
            ts = ctx.timestamp.date().isoformat()
            cand = sorted((s for s in fired if s not in held and s not in st["pending"] and s in cols),
                          key=lambda s: _order_key(self.definition.seed, ts, s))
            if cand:
                jj = np.array([cols[s] for s in cand])
                B = rt.new_batch(len(cand))
                ix = np.arange(len(cand))

                def getter2(key, sel):
                    return mk.derived_now(key)[jj[sel]]

                rt.at_signal(Ctx(getter2, ix), B, ix)
                eq = ctx.portfolio.equity
                for k, sym in enumerate(cand):
                    if slots <= 0:
                        break
                    if B["valid"][k] < 0.5:
                        continue
                    qty = self._size(eq, float(B["sig_close"][k]), float(B["Rhat"][k]))
                    if qty < 1:
                        continue
                    legs = tuple(AttachedExit(OrderType.STOP if t == "stop" else OrderType.LIMIT, fraction=f, mult=m,
                                              add=a, tag=tag) for t, f, m, a, tag in rt.legs(B, k))
                    out.append(OrderIntent.market(sym, qty, tag="entry", attached=legs))
                    st["pending"][sym] = dict(B={key: float(B[key][k]) for key in rt.keys()})
                    slots -= 1
        return out

    @staticmethod
    def _bar_ok(mk, col: int, hl: bool) -> bool:
        """The replay's rule: a close decision needs a valid close (and open, high and low when the plan reads
        them)."""
        vals = [mk.derived_now(f)[col] for f in (("open", "high", "low", "close") if hl else ("close",))]
        return all(math.isfinite(v) and v > 0 for v in vals)

    @staticmethod
    def _entry_orders(rt: PlanRuntime, B: dict) -> list:
        """The orders in force after an entry fill until the first close decision: the attached legs."""
        orders = []
        stop = B["ibm"][0] * B["P"][0] + B["iba"][0]
        if np.isfinite(stop) and stop > 0:       # the broker skips a leg priced at or below zero, so does this
            orders.append(("stop", "stop", float(stop), 1.0))
        for k in rt.targets:
            comp = rt.comps[k]
            lvl = B[comp.st("level")][0]
            if B[comp.st("filled")][0] < 0.5 and np.isfinite(lvl):
                orders.append((f"target:{k}", "", float(lvl), float(B[comp.st("frac")][0])))
        return orders

    def _size(self, equity: float, price: float, risk: float) -> int:
        s = self.params
        if not (math.isfinite(price) and price > 0):
            return 0
        if s["sizing_mode"] == "weight":
            q = s["position_weight"] * equity / price
        else:
            if not (math.isfinite(risk) and risk > 0):
                return 0
            q = min(s["risk_per_position"] * equity / risk, s["max_position_weight"] * equity / price)
        return int(math.floor(q + 1e-9))

    def _intents(self, sym: str, pos: dict, qty: float, fractional: bool = False) -> list:
        out = []
        for kind, why, lvl, frac in pos["orders"]:
            if kind == "exit":
                out.append(OrderIntent.market(sym, -qty, tag=f"exit:{why}"))
                return out
            if kind == "stop":
                out.append(OrderIntent.stop(sym, -qty, lvl, tif=TimeInForce.DAY, tag=f"stop:{why}"))
            else:
                q = frac * pos["qty0"] if fractional else math.floor(frac * pos["qty0"] + 1e-9)
                q = min(qty, q)
                if q > 0 if fractional else q >= 1:
                    out.append(OrderIntent.limit(sym, -q, lvl, tif=TimeInForce.DAY, tag=kind))
        return out


def _params_schema(d: EntryExitDefinition) -> dict[str, Param]:
    s = d.sizing
    return {
        "max_positions": Param(d.max_positions, "int", 1, 500, description="concurrent positions"),
        "sizing_mode": Param(s.mode, "choice", choices=("risk", "weight"),
                             description="risk: size by the initial risk per share; weight: a fixed share of equity"),
        "risk_per_position": Param(s.risk_per_position, "float", 0.0005, 0.2, description="risk mode"),
        "position_weight": Param(s.position_weight, "float", 0.005, 1.0, description="weight mode"),
        "max_position_weight": Param(s.max_position_weight, "float", 0.005, 1.0,
                                     description="cap on one position's weight at entry"),
    }


def make_entry_exit_strategy(definition: EntryExitDefinition | dict, strategy_id: str, version: str = "1.0",
                             name: str = "", module: str | None = None, provenance: dict | None = None,
                             description: str = "") -> type[EntryExitBase]:
    d = definition if isinstance(definition, EntryExitDefinition) else EntryExitDefinition.model_validate(definition)
    plan = resolve_plan(d.exit)
    if plan.lookahead:
        raise ValueError(f"{strategy_id}: the exit plan contains a calibration rule that reads the future; it exists "
                         "only to check the timing benchmark and can never be traded or backtested as a strategy")
    entry = None
    if d.entry is not None:
        from strategy_lab.signals.library import default_signal_registry
        entry = resolve_spec(d.entry, default_signal_registry())
    efields = set(entry.fields) if entry else {"close"}
    fields = frozenset((set(plan.fields) | efields | {"open", "close"}) & {"open", "high", "low", "close", "volume"})
    order_types = frozenset({"market", "stop", "limit"} if plan.intrabar else {"market"})
    desc = description or (f"Entry {entry.name if entry else 'injected events'} ({entry.key if entry else '-'}); "
                           f"exit {plan.name}: {plan.trigger}. {WARNING}")
    attrs = dict(
        __module__=module or __name__, id=strategy_id, version=version, name=name or strategy_id,
        description=desc, definition=d, params_schema=_params_schema(d), required_fields=fields,
        order_types=order_types, long_only=True, provenance=provenance or {}, pin_modules=pin_modules(),
        plan_key=plan.key, entry_key=entry.key if entry else None)
    cls = type("EE_" + re.sub(r"[^A-Za-z0-9]", "_", strategy_id), (EntryExitBase,), attrs)
    if not plan.deployable or (entry is not None and (entry.needs_benchmark or not set(entry.fields) <= {"open", "close"})):
        cls.deploy_note = "; ".join(plan.problems + ([f"entry reads {sorted(set(entry.fields) - {'open', 'close'})}"]
                                                     if entry and not set(entry.fields) <= {"open", "close"} else []))
    return cls


def _describe(cls) -> dict:
    d = Strategy.describe.__func__(cls)
    d["provenance"] = getattr(cls, "provenance", {})
    d["definition"] = cls.definition.model_dump(mode="json")
    d["plan"] = resolve_plan(cls.definition.exit).describe()
    return d


def _source_sha256(cls) -> str:
    pkg = Path(strategy_lab.__file__).resolve().parent
    h = hashlib.sha256()
    for rel in pin_modules():
        h.update(rel.encode())
        h.update((pkg / rel).read_bytes())
    h.update(cls.definition.model_dump_json().encode())
    mod = sys.modules.get(cls.__module__)
    if mod is not None and getattr(mod, "__file__", None) and cls.__module__ != __name__:
        h.update(Path(mod.__file__).read_bytes())
    return h.hexdigest()


EntryExitBase.describe = classmethod(_describe)
EntryExitBase.source_sha256 = classmethod(_source_sha256)


class EntryExitSystem(EntryExitBase):
    """The generic entry-exit system of the Backtesting page: its definition is a JSON parameter."""
    id = "entry_exit_system"
    version = "1.0"
    name = "Entry-exit system (JSON definition)"
    description = ("An entry signal paired with an exit plan, both given as JSON (docs/ENTRY_EXIT_RESEARCH.md). "
                   "Usually created from the Entry-Exit Research Lab.")
    definition = EntryExitDefinition(exit=ExitPlanSpec(components=[{"id": "time_20d"}]),
                                     entry=SignalSpec(id="breakout_20d"))
    params_schema = dict(_params_schema(definition), definition_json=Param(
        definition.model_dump_json(), "str", description="EntryExitDefinition as JSON"))
    required_fields = frozenset({"open", "high", "low", "close", "volume"})
    order_types = frozenset({"market", "stop", "limit"})

    def __init__(self, params=None):
        super().__init__(params)
        self.definition = EntryExitDefinition.model_validate_json(self.params["definition_json"])

    def plan(self):
        return resolve_plan(self.definition.exit)

    def entry(self):
        if self.definition.entry is None:
            return None
        from strategy_lab.signals.library import default_signal_registry
        return resolve_spec(self.definition.entry, default_signal_registry())


MODULE = '''"""Strategy Lab entry-exit import {strategy_id} (generated by strategy_lab.strategies.entry_exit; do not edit).
The file's sha256 is part of the strategy's identity: a changed record is a new strategy, never an edit of this one.
Source experiment {experiment_id}, cell {cell}.
"""
import json

from strategy_lab.strategies.entry_exit import make_entry_exit_strategy

RECORD = json.loads(r"""{record}""")
{cls} = make_entry_exit_strategy(RECORD["definition"], RECORD["strategy_id"], RECORD["version"], RECORD["name"],
                                 __name__, RECORD)
'''


def write_module(record: dict, directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record['strategy_id']}.py"
    text = MODULE.format(strategy_id=record["strategy_id"], experiment_id=record["research"]["experiment_id"],
                         cell=record["research"]["cell"], record=json.dumps(record, indent=1, sort_keys=True),
                         cls="EE_" + re.sub(r"[^A-Za-z0-9]", "_", record["strategy_id"]))
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise FileExistsError(f"{path} exists with a different record; imports are write-once")
        return path
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def load_module(path: Path, registry) -> list[type[Strategy]]:
    from strategy_lab.strategies.registry import _strategies_in
    modname = f"{MODULE_PREFIX}.{Path(path).stem}"
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    out = []
    for cls in _strategies_in(mod):
        registry.register(cls, f"entry-exit-import:{Path(path).name}")
        out.append(cls)
    return out


__all__ = ["EntryExitDefinition", "Sizing", "EntryEligibility", "EntryExitBase", "EntryExitSystem",
           "make_entry_exit_strategy", "write_module", "load_module", "pin_modules", "WARNING", "Any"]
