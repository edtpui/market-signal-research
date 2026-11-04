"""Cash and position accounting. Invariant after every step: equity == cash + sum(quantity * mark).

Marks are the last valid close; a symbol without a bar today keeps its previous mark and counts as stale.
Corporate actions (raw price mode): a split multiplies quantity and divides cost and mark by its ratio; a dividend
credits quantity * amount to cash on the ex-date (a short pays it).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from strategy_lab.core.types import Fill, PortfolioSnapshot, Position

EPS = 1e-9


@dataclass
class DayRecord:
    timestamp: str
    cash: float
    equity: float
    long_value: float
    short_value: float
    n_positions: int
    traded_notional: float
    commission: float
    slippage: float
    dividends: float
    borrow: float
    stale_marks: int


class Portfolio:
    def __init__(self, initial_cash: float, symbols: tuple[str, ...]):
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("initial capital must be positive")
        self.initial_cash = float(initial_cash)
        self.cash = float(initial_cash)
        self.symbols = symbols
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self._day = self._zero_day()

    @staticmethod
    def _zero_day() -> dict:
        return dict(traded_notional=0.0, commission=0.0, slippage=0.0, dividends=0.0, borrow=0.0)

    # -- state --------------------------------------------------------------------------------------------------
    def quantity(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.quantity if p else 0.0

    def market_value(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    def equity(self) -> float:
        return self.cash + self.market_value()

    def snapshot(self, timestamp: str) -> PortfolioSnapshot:
        pos = {s: Position(p.symbol, p.quantity, p.avg_cost, p.last_price, p.last_price_date)
               for s, p in self.positions.items()}
        return PortfolioSnapshot(timestamp=timestamp, cash=self.cash, equity=self.equity(), positions=pos)

    # -- mutations ----------------------------------------------------------------------------------------------
    def apply_fill(self, fill: Fill) -> float:
        """Books a fill; returns the realized PnL of the quantity it closed (before commission)."""
        q, px = fill.quantity, fill.price
        self.cash -= q * px + fill.commission
        self._day["traded_notional"] += abs(q) * px
        self._day["commission"] += fill.commission
        self._day["slippage"] += fill.slippage_cost
        p = self.positions.get(fill.symbol)
        if p is None:
            p = self.positions[fill.symbol] = Position(fill.symbol)
        realized = 0.0
        old = p.quantity
        new = old + q
        if old == 0 or (old > 0) == (q > 0):                                   # open or add
            p.avg_cost = (abs(old) * p.avg_cost + abs(q) * px) / abs(new)
        elif abs(q) <= abs(old) + EPS:                                          # reduce or close
            closed = abs(q)
            realized = closed * (px - p.avg_cost) * (1 if old > 0 else -1)
        else:                                                                   # flip through zero
            realized = abs(old) * (px - p.avg_cost) * (1 if old > 0 else -1)
            p.avg_cost = px
        p.quantity = 0.0 if abs(new) < EPS else new
        if p.last_price is None or not math.isfinite(p.last_price):
            p.last_price, p.last_price_date = px, fill.timestamp
        if p.quantity == 0.0:
            del self.positions[fill.symbol]
        self.realized_pnl += realized
        return realized

    def apply_split(self, symbol: str, ratio: float) -> None:
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError(f"bad split ratio {ratio} for {symbol}")
        p = self.positions.get(symbol)
        if p is None:
            return
        p.quantity *= ratio
        p.avg_cost /= ratio
        if p.last_price is not None:
            p.last_price /= ratio

    def apply_dividend(self, symbol: str, amount: float) -> float:
        p = self.positions.get(symbol)
        if p is None or not math.isfinite(amount):
            return 0.0
        cash = p.quantity * amount
        self.cash += cash
        self._day["dividends"] += cash
        return cash

    def mark(self, timestamp: str, closes: np.ndarray, col: dict[str, int]) -> int:
        """Marks every position at today's close where there is one; returns the number of stale marks."""
        stale = 0
        for s, p in self.positions.items():
            c = closes[col[s]]
            if math.isfinite(c) and c > 0:
                p.last_price, p.last_price_date = float(c), timestamp
            else:
                stale += 1
        return stale

    def accrue_borrow(self, bps_per_year: float, periods_per_year: float) -> float:
        if bps_per_year <= 0:
            return 0.0
        short = sum(-p.market_value for p in self.positions.values() if p.quantity < 0)
        cost = short * bps_per_year / 1e4 / periods_per_year
        self.cash -= cost
        self._day["borrow"] += cost
        return cost

    def close_day(self, timestamp: str, stale: int) -> DayRecord:
        long_v = sum(p.market_value for p in self.positions.values() if p.quantity > 0)
        short_v = sum(p.market_value for p in self.positions.values() if p.quantity < 0)
        rec = DayRecord(timestamp=timestamp, cash=self.cash, equity=self.cash + long_v + short_v, long_value=long_v,
                        short_value=short_v, n_positions=len(self.positions), stale_marks=stale, **self._day)
        self._day = self._zero_day()
        return rec

    def holdings(self, timestamp: str) -> list[dict]:
        eq = self.equity()
        return [dict(timestamp=timestamp, symbol=s, quantity=p.quantity, avg_cost=p.avg_cost, close=p.last_price,
                     market_value=p.market_value, weight=p.market_value / eq if eq else None,
                     mark_date=p.last_price_date)
                for s, p in sorted(self.positions.items())]
