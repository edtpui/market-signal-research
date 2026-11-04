"""The session loop every mode uses: a backtest calls it once over the whole window, and the ptrade plug-in calls it
over the forward book's window each night. There is no other execution path.

Per session i (after its bar exists):
  1. corporate actions dated i (raw price mode): splits adjust positions and open orders, dividends credit cash;
  2. the broker fills the open orders against bar i (they were submitted at an earlier close);
  3. positions are marked at close i, short borrow accrues, the day is recorded;
  4. the strategy gets the session's order events, then on_bar() with data up to close i;
  5. its intents become orders, eligible from session i+1.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategy_lab.core.market import MarketData, MarketView
from strategy_lab.core.strategy import ExecutionInfo, InitContext, Strategy, StrategyContext
from strategy_lab.core.types import (CancelIntent, Fill, Order, OrderEvent, OrderIntent, OrderType, TargetWeights,
                                     TimeInForce)
from strategy_lab.engine.broker import SimulatedBroker
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.portfolio import DayRecord, Portfolio

MAX_LOG_LINES = 20_000


class StrategyError(RuntimeError):
    """The strategy broke the interface contract (bad intent, a field it did not declare, too much leverage)."""


@dataclass
class RunOutput:
    days: list[DayRecord]
    fills: list[Fill]
    orders: list[Order]
    events: list[OrderEvent]
    holdings: list[dict]
    logs: list[dict]
    final_state: dict
    warnings: list[str] = field(default_factory=list)

    def equity(self) -> pd.Series:
        return pd.Series([d.equity for d in self.days], index=pd.DatetimeIndex([d.timestamp for d in self.days]),
                         name="equity")


class StrategyRunner:
    def __init__(self, strategy: Strategy, data: MarketData, execution: ExecutionConfig, initial_capital: float,
                 start: pd.Timestamp, end: pd.Timestamp, universe: tuple[str, ...] | None = None, seed: int = 0,
                 periods_per_year: float = 252.0):
        self.strategy = strategy
        self.data = data
        self.cfg = execution
        self.universe = tuple(universe or data.symbols)
        missing = set(self.universe) - set(data.symbols)
        if missing:
            raise ValueError(f"universe symbols without data: {sorted(missing)}")
        need = set(strategy.required_fields) | execution.needs_fields()
        absent = need - set(data.available_fields)
        if absent:
            raise StrategyError(f"{strategy.id} needs fields {sorted(absent)}, which the data source does not provide "
                                f"(available: {sorted(data.available_fields)})")
        idx = data.index
        sel = np.flatnonzero((idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end)))
        if len(sel) == 0:
            raise ValueError(f"no sessions between {pd.Timestamp(start).date()} and {pd.Timestamp(end).date()}")
        self.first, self.last = int(sel[0]), int(sel[-1])
        self.portfolio = Portfolio(initial_capital, data.symbols)
        self.broker = SimulatedBroker(data, execution, self.portfolio)
        self.rng = np.random.default_rng(seed)
        self.periods_per_year = periods_per_year
        self.logs: list[dict] = []
        self.warnings: list[str] = []
        self._col = {s: i for i, s in enumerate(data.symbols)}
        self._ts = ""

    def _log(self, msg: str) -> None:
        if len(self.logs) < MAX_LOG_LINES:
            self.logs.append(dict(timestamp=self._ts, message=str(msg)[:2000]))
        elif len(self.logs) == MAX_LOG_LINES:
            self.logs.append(dict(timestamp=self._ts, message="log truncated"))

    def _context(self, i: int, events: tuple[OrderEvent, ...]) -> StrategyContext:
        return StrategyContext(
            timestamp=self.data.index[i], market=MarketView(self.data, i),
            portfolio=self.portfolio.snapshot(self._ts), params=dict(self.strategy.params),
            execution=ExecutionInfo(open_orders=self.broker.open_orders(), events=events,
                                    fill_timing=self.cfg.fill_timing, session_index=i - self.first,
                                    sessions_run=i - self.first + 1),
            universe=self.universe, state=self.strategy.state, rng=self.rng, log=self._log)

    def run(self) -> RunOutput:
        s = self.strategy
        self._ts = self.data.index[self.first].date().isoformat()
        s.initialize(InitContext(universe=self.universe, params=dict(s.params), start=self.data.index[self.first],
                                 end=self.data.index[self.last], initial_capital=self.portfolio.initial_cash,
                                 fields=self.data.available_fields, state=s.state, rng=self.rng, log=self._log))
        closes = self.data.array("close")
        days, holdings = [], []
        ctx = None
        delivered = 0                     # events up to here were handed to the strategy (incl. last close's)
        for i in range(self.first, self.last + 1):
            ts = self.data.index[i]
            self._ts = ts.date().isoformat()
            self._corporate_actions(ts)
            self.broker.execute(i)
            stale = self.portfolio.mark(self._ts, closes[i], self._col)
            self.portfolio.accrue_borrow(self.cfg.short_borrow_bps_per_year, self.periods_per_year)
            day = self.portfolio.close_day(self._ts, stale)
            if not math.isfinite(day.equity):
                raise RuntimeError(f"equity is not finite on {self._ts}")
            days.append(day)
            holdings.extend(self.portfolio.holdings(self._ts))
            events = tuple(self.broker.events[delivered:])
            delivered = len(self.broker.events)
            ctx = self._context(i, events)
            for e in events:
                s.on_order_event(ctx, e)
            intents = s.on_bar(ctx)
            self._submit(intents, i)
        if ctx is not None:
            s.shutdown(ctx)
        return RunOutput(days=days, fills=list(self.broker.fills), orders=list(self.broker.orders.values()),
                         events=list(self.broker.events), holdings=holdings, logs=self.logs,
                         final_state=s.get_state(), warnings=self.warnings)

    def _corporate_actions(self, ts: pd.Timestamp) -> None:
        if self.cfg.price_mode != "raw":
            return
        for a in self.data.actions_on(ts).itertuples():
            if a.kind == "split":
                self.portfolio.apply_split(a.symbol, float(a.value))
                self.broker.apply_split(a.symbol, float(a.value), self._ts)
                self._log(f"split {a.symbol} {float(a.value):g}:1")
            elif a.kind == "dividend":
                cash = self.portfolio.apply_dividend(a.symbol, float(a.value))
                if cash:
                    self._log(f"dividend {a.symbol} {float(a.value):.4f}/sh -> {cash:,.2f}")

    # -- intents ------------------------------------------------------------------------------------------------
    def _submit(self, intents, i: int) -> None:
        if intents is None:
            return
        if isinstance(intents, (OrderIntent, TargetWeights, CancelIntent)):
            intents = [intents]
        for it in intents:
            if isinstance(it, OrderIntent):
                self._order(it)
            elif isinstance(it, TargetWeights):
                self._rebalance(it, i)
            elif isinstance(it, CancelIntent):
                self.broker.cancel(it.order_id, self._ts)
            else:
                raise StrategyError(f"{self.strategy.id} returned {type(it).__name__}, not an intent")

    def _order(self, it: OrderIntent) -> None:
        if it.order_type.value not in self.strategy.order_types:
            raise StrategyError(f"{self.strategy.id} emitted a {it.order_type.value} order but declares "
                                f"order_types={sorted(self.strategy.order_types)}")
        if it.symbol not in self.universe:
            raise StrategyError(f"{self.strategy.id} ordered {it.symbol}, which is outside its universe")
        self.broker.submit(it, self._ts)

    def _rebalance(self, tw: TargetWeights, i: int) -> None:
        gross = sum(abs(w) for w in tw.weights.values())
        if gross > self.cfg.max_gross_leverage + 1e-9:
            raise StrategyError(f"{self.strategy.id}: target gross weight {gross:.4f} exceeds max_gross_leverage "
                                f"{self.cfg.max_gross_leverage}")
        if self.strategy.long_only and any(w < 0 for w in tw.weights.values()):
            raise StrategyError(f"{self.strategy.id} is long_only but asked for a negative weight")
        outside = set(tw.weights) - set(self.universe)
        if outside:
            raise StrategyError(f"{self.strategy.id}: weights for symbols outside the universe {sorted(outside)}")
        equity = self.portfolio.equity()
        closes = self.data.array("close")[i]
        targets = dict(tw.weights)
        if tw.close_others:
            for sym in set(self.portfolio.positions) | {o.symbol for o in self.broker.open_orders()}:
                targets.setdefault(sym, 0.0)
        for sym in sorted(targets):
            w = targets[sym]
            px = closes[self._col[sym]]
            if not (math.isfinite(px) and px > 0):
                if w != 0 or self.portfolio.quantity(sym):
                    self._log(f"rebalance: no close for {sym} on {self._ts}; left unchanged")
                continue
            want = w * equity / px
            if not self.cfg.fractional_shares:
                want = math.floor(abs(want) + 1e-9) * (1 if want >= 0 else -1)
            delta = want - self.portfolio.quantity(sym) - self.broker.pending_quantity(sym)
            if abs(delta) < (1e-6 if self.cfg.fractional_shares else 1):
                continue
            self.broker.submit(OrderIntent(sym, delta, OrderType.MARKET, tif=TimeInForce.DAY, tag=tw.tag), self._ts)
