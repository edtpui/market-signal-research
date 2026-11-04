"""Built-in rule, indicator and allocation strategies. Each decides only from ctx.market (clipped at the session's
close) and its own state; none knows which mode it runs in."""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_lab.core.strategy import Param, Strategy, StrategyContext
from strategy_lab.core.types import CancelIntent, OrderEvent, OrderIntent, OrderStatus, TargetWeights


def _equal(symbols, gross: float) -> dict[str, float]:
    return {s: gross / len(symbols) for s in symbols} if symbols else {}


class BuyAndHold(Strategy):
    id = "buy_and_hold"
    version = "1.0"
    name = "Buy and hold"
    category = "allocation"
    description = ("Equal-weights every universe symbol that has a bar on the first session, then holds. "
                   "rebalance_every > 0 restores equal weights every N sessions.")
    params_schema = {
        "gross": Param(1.0, "float", 0.05, 1.0, description="share of equity invested"),
        "rebalance_every": Param(0, "int", 0, 2520, description="sessions between rebalances (0 = never)"),
    }

    def on_bar(self, ctx: StrategyContext):
        n = ctx.execution.session_index
        every = self.params["rebalance_every"]
        if n == 0 or (every and n % every == 0):
            live = [s for s in ctx.universe if ctx.market.has_bar(s)]
            return TargetWeights(_equal(live, self.params["gross"]), tag="hold")
        return None


class SmaCrossover(Strategy):
    id = "sma_crossover"
    version = "1.0"
    name = "SMA crossover"
    category = "indicator"
    description = ("Long a symbol while its fast simple moving average is above its slow one; equal weight across "
                   "the symbols in an uptrend (at most max_positions, strongest spread first). Rebalances when the "
                   "set changes.")
    params_schema = {
        "fast": Param(50, "int", 2, 250, description="fast SMA length (sessions)"),
        "slow": Param(200, "int", 5, 400, description="slow SMA length (sessions)"),
        "max_positions": Param(10, "int", 1, 200),
        "gross": Param(1.0, "float", 0.05, 1.0),
    }

    def warmup(self) -> int:
        return self.params["slow"]

    def initialize(self, ctx):
        if self.params["fast"] >= self.params["slow"]:
            raise ValueError("sma_crossover: fast must be shorter than slow")
        self.state["held"] = []

    def on_bar(self, ctx: StrategyContext):
        p = self.params
        hist = ctx.market.history("close", bars=p["slow"])
        if len(hist) < p["slow"]:
            return None
        slow = hist.mean(skipna=False)
        fast = hist.iloc[-p["fast"]:].mean(skipna=False)
        spread = ((fast / slow) - 1).dropna()
        up = spread[spread > 0].sort_values(ascending=False)
        chosen = sorted(up.index[: p["max_positions"]])
        if chosen == self.state["held"]:
            return None
        self.state["held"] = chosen
        return TargetWeights(_equal(chosen, p["gross"]), tag="sma")


class MomentumRotation(Strategy):
    id = "momentum_rotation"
    version = "1.0"
    name = "Momentum rotation"
    category = "allocation"
    description = ("Every rebalance_every sessions, holds the top_k symbols by total return over `lookback` "
                   "sessions ending `skip` sessions ago, equal weight. With absolute=true a symbol needs positive "
                   "momentum, and unused weight stays in cash.")
    params_schema = {
        "lookback": Param(126, "int", 5, 504),
        "skip": Param(5, "int", 0, 63),
        "top_k": Param(5, "int", 1, 100),
        "rebalance_every": Param(21, "int", 1, 252),
        "absolute": Param(True, "bool"),
        "gross": Param(1.0, "float", 0.05, 1.0),
    }

    def warmup(self) -> int:
        return self.params["lookback"] + self.params["skip"] + 1

    def on_bar(self, ctx: StrategyContext):
        p = self.params
        if ctx.execution.session_index % p["rebalance_every"]:
            return None
        need = p["lookback"] + p["skip"] + 1
        hist = ctx.market.history("close", bars=need)
        if len(hist) < need:
            return None
        mom = (hist.iloc[-1 - p["skip"]] / hist.iloc[0] - 1).dropna()
        mom = mom[[ctx.market.has_bar(s) for s in mom.index]]
        if p["absolute"]:
            mom = mom[mom > 0]
        top = mom.sort_values(ascending=False, kind="mergesort").index[: p["top_k"]]
        w = {s: p["gross"] / p["top_k"] for s in sorted(top)}
        ctx.log(f"momentum top: {', '.join(f'{s} {mom[s]:+.1%}' for s in top)}" if len(top) else "momentum: cash")
        return TargetWeights(w, tag="momentum")


def rsi(close: pd.DataFrame, n: int) -> pd.Series:
    """Wilder RSI at the last row, from the given history (NaN where there is not enough)."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up.iloc[-1] / dn.iloc[-1].replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    out[(dn.iloc[-1] == 0) & (up.iloc[-1] > 0)] = 100.0
    return out


class RsiMeanReversion(Strategy):
    id = "rsi_mean_reversion"
    version = "1.0"
    name = "RSI mean reversion with stops"
    category = "rule"
    description = ("Buys position_size of equity when RSI(rsi_len) falls below entry_rsi while the close is above "
                   "its trend SMA; protects each position with a GTC stop stop_pct below the entry fill; exits at "
                   "market when RSI rises above exit_rsi or after max_hold sessions (cancelling the stop). Needs "
                   "OHLC bars for the stop orders.")
    params_schema = {
        "rsi_len": Param(14, "int", 2, 50),
        "entry_rsi": Param(30.0, "float", 1, 50),
        "exit_rsi": Param(55.0, "float", 40, 99),
        "trend": Param(0, "int", 0, 300, description="trend SMA length; 0 disables"),
        "stop_pct": Param(0.08, "float", 0.005, 0.5),
        "max_hold": Param(20, "int", 1, 250),
        "position_size": Param(0.1, "float", 0.01, 1.0),
        "max_positions": Param(8, "int", 1, 100),
    }
    required_fields = frozenset({"open", "high", "low", "close"})
    order_types = frozenset({"market", "stop"})

    def warmup(self) -> int:
        return max(self.params["trend"], self.params["rsi_len"] * 3)

    def initialize(self, ctx):
        self.state.update(entries={}, stops={}, pending_entry=[])

    def on_order_event(self, ctx: StrategyContext, e: OrderEvent):
        st = self.state
        if e.status == OrderStatus.SUBMITTED and e.tag == "stop":
            st["stops"][e.symbol] = e.order_id
        if e.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED) and e.tag == "stop" \
                and st["stops"].get(e.symbol) == e.order_id:
            st["stops"].pop(e.symbol)
        if e.fill is not None and e.fill.quantity > 0 and e.symbol in st["pending_entry"]:
            st["entries"][e.symbol] = dict(date=e.timestamp, price=e.fill.price)
            st["pending_entry"].remove(e.symbol)
        if e.status in (OrderStatus.EXPIRED, OrderStatus.REJECTED, OrderStatus.CANCELLED) and \
                e.symbol in st["pending_entry"]:
            st["pending_entry"].remove(e.symbol)
        if e.fill is not None and st["stops"].get(e.symbol) == e.order_id:
            st["stops"].pop(e.symbol)
            st["entries"].pop(e.symbol, None)

    def on_bar(self, ctx: StrategyContext):
        p, st = self.params, self.state
        need = max(p["trend"], p["rsi_len"] * 3) + 1
        hist = ctx.market.history("close", bars=need)
        if len(hist) < need:
            return None
        r = rsi(hist, p["rsi_len"])
        trend = hist.iloc[-p["trend"]:].mean(skipna=False) if p["trend"] else None
        out = []
        held = {s for s, pos in ctx.portfolio.positions.items() if pos.quantity > 0}
        for s in sorted(held):
            e = st["entries"].get(s)
            age = len(ctx.market.sessions[ctx.market.sessions > pd.Timestamp(e["date"])]) if e else 0
            if (np.isfinite(r.get(s, np.nan)) and r[s] > p["exit_rsi"]) or age >= p["max_hold"]:
                stop_id = st["stops"].pop(s, None)
                if stop_id:
                    out.append(CancelIntent(stop_id))
                out.append(OrderIntent.market(s, -ctx.portfolio.quantity(s), tag="exit"))
                st["entries"].pop(s, None)
            elif e and s not in st["stops"]:
                qty = ctx.portfolio.quantity(s)
                out.append(OrderIntent.stop(s, -qty, round(e["price"] * (1 - p["stop_pct"]), 4), tag="stop"))
        slots = p["max_positions"] - len(held) - len(st["pending_entry"])
        cands = r[(r < p["entry_rsi"])].dropna()
        if trend is not None:
            cands = cands[[hist[s].iloc[-1] > trend[s] for s in cands.index]]
        for s in cands.sort_values().index:
            if slots <= 0:
                break
            if s in held or s in st["pending_entry"] or not ctx.market.has_bar(s):
                continue
            qty = np.floor(ctx.portfolio.equity * p["position_size"] / ctx.market.price(s))
            if qty >= 1:
                out.append(OrderIntent.market(s, float(qty), tag="entry"))
                st["pending_entry"].append(s)
                slots -= 1
        return out
