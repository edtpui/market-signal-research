"""Performance metrics: one implementation for backtests and forward deployments (docs/METRICS.md).

Every metric is {"value": float | None, "reason": str | None}. A metric that cannot be computed (too few periods, zero
variance, no losing trade, no benchmark overlap) has value None and a reason. It is never reported as 0.
Returns are simple period returns of the equity curve; rf defaults to 0 (the ptrade/v7.1 convention).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

MIN_PERIODS_RATIO = 20          # Sharpe/Sortino/vol need at least this many returns
MIN_YEARS_CAGR = 1.0            # CAGR is undefined below one year (ptrade: below 252 sessions)


def M(value=None, reason: str | None = None) -> dict:
    if value is not None and (not isinstance(value, (int, float, np.floating, np.integer)) or not math.isfinite(value)):
        return dict(value=None, reason=reason or "not finite")
    return dict(value=None if value is None else float(value), reason=None if value is not None else reason)


@dataclass
class Inputs:
    equity: pd.Series                  # date -> equity (the first point is the start value)
    periods_per_year: float = 252.0
    benchmark: pd.Series | None = None  # date -> benchmark equity or price (rebased internally)
    trades: pd.DataFrame | None = None  # round trips: pnl, return, entry_date, exit_date, holding_periods
    exposure: pd.Series | None = None   # date -> gross exposure / equity
    traded_notional: pd.Series | None = None
    costs: pd.Series | None = None      # date -> commission + slippage dollars
    risk_free: float = 0.0              # annual


def returns(equity: pd.Series) -> pd.Series:
    e = equity.astype(float)
    return e.pct_change().iloc[1:]


def drawdown(equity: pd.Series) -> pd.Series:
    e = equity.astype(float)
    return e / e.cummax() - 1.0


def max_drawdown_info(equity: pd.Series) -> dict:
    dd = drawdown(equity)
    if dd.empty:
        return dict(max_drawdown=None, peak=None, trough=None, recovery=None, duration_periods=None)
    trough = dd.idxmin()
    peak = equity.loc[:trough].idxmax()
    after = equity.loc[trough:]
    rec = after[after >= equity.loc[peak]]
    recovery = rec.index[0] if len(rec) else None
    end = recovery if recovery is not None else equity.index[-1]
    dur = int(((equity.index >= peak) & (equity.index <= end)).sum()) - 1
    return dict(max_drawdown=float(dd.min()), peak=peak, trough=trough, recovery=recovery, duration_periods=dur)


def compute(inp: Inputs) -> dict[str, dict]:
    eq = inp.equity.dropna().astype(float)
    ppy = inp.periods_per_year
    out: dict[str, dict] = {}
    n = len(eq) - 1
    if n < 1:
        reason = "fewer than 2 equity points"
        for k in ("total_return", "cagr", "volatility", "sharpe", "sortino", "max_drawdown", "calmar"):
            out[k] = M(None, reason)
        out.update(_trade_metrics(inp.trades))
        return out
    r = returns(eq)
    years = n / ppy
    total = eq.iloc[-1] / eq.iloc[0] - 1
    out["total_return"] = M(total)
    out["cagr"] = M((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) if years >= MIN_YEARS_CAGR and eq.iloc[-1] > 0 \
        else M(None, f"needs >= {MIN_YEARS_CAGR:g} year of returns (have {years:.2f})")
    rf_p = (1 + inp.risk_free) ** (1 / ppy) - 1
    ex = r - rf_p
    if len(r) < MIN_PERIODS_RATIO:
        why = f"needs >= {MIN_PERIODS_RATIO} returns (have {len(r)})"
        out["volatility"] = M(None, why)
        out["sharpe"] = M(None, why)
        out["sortino"] = M(None, why)
    else:
        sd = r.std(ddof=1)
        out["volatility"] = M(sd * math.sqrt(ppy))
        out["sharpe"] = M(ex.mean() / ex.std(ddof=1) * math.sqrt(ppy)) if ex.std(ddof=1) > 0 \
            else M(None, "zero return variance")
        downside = np.sqrt(np.mean(np.minimum(ex.to_numpy(), 0.0) ** 2))
        out["sortino"] = M(ex.mean() / downside * math.sqrt(ppy)) if downside > 0 \
            else M(None, "no negative returns")
    mdd = max_drawdown_info(eq)
    out["max_drawdown"] = M(mdd["max_drawdown"])
    out["max_drawdown_duration"] = M(mdd["duration_periods"])
    if out["cagr"]["value"] is None:
        out["calmar"] = M(None, "CAGR undefined")
    elif mdd["max_drawdown"] == 0:
        out["calmar"] = M(None, "no drawdown")
    else:
        out["calmar"] = M(out["cagr"]["value"] / abs(mdd["max_drawdown"]))
    out["best_period"] = M(r.max())
    out["worst_period"] = M(r.min())
    out["positive_periods"] = M((r > 0).mean())
    if inp.exposure is not None and len(inp.exposure):
        x = inp.exposure.reindex(eq.index).dropna()
        out["exposure"] = M(x.mean()) if len(x) else M(None, "no exposure data")
        out["time_in_market"] = M((x > 1e-9).mean()) if len(x) else M(None, "no exposure data")
    if inp.traded_notional is not None and len(inp.traded_notional):
        avg_eq = eq.mean()
        tn = inp.traded_notional.reindex(eq.index).fillna(0.0).sum()
        out["turnover"] = M(tn / 2 / avg_eq / years) if years > 0 and avg_eq > 0 else M(None, "no period")
    if inp.costs is not None and len(inp.costs):
        c = inp.costs.reindex(eq.index).fillna(0.0).sum()
        out["total_costs"] = M(c)
        out["cost_drag"] = M(c / eq.mean() / years) if years > 0 else M(None, "no period")
    out.update(_trade_metrics(inp.trades))
    out.update(_benchmark_metrics(eq, r, inp, ppy))
    return out


def _trade_metrics(trades: pd.DataFrame | None) -> dict[str, dict]:
    out = {}
    if trades is None or trades.empty:
        out["trade_count"] = M(0)
        for k in ("win_rate", "profit_factor", "avg_trade_return", "avg_holding_periods", "avg_win", "avg_loss"):
            out[k] = M(None, "no closed trades")
        return out
    pnl = trades["pnl"].astype(float)
    out["trade_count"] = M(len(trades))
    out["win_rate"] = M((pnl > 0).mean())
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    out["profit_factor"] = M(gains / losses) if losses > 0 else M(None, "no losing trades")
    out["avg_trade_return"] = M(trades["return"].astype(float).mean())
    out["avg_holding_periods"] = M(trades["holding_periods"].astype(float).mean())
    out["avg_win"] = M(pnl[pnl > 0].mean()) if (pnl > 0).any() else M(None, "no winning trades")
    out["avg_loss"] = M(pnl[pnl < 0].mean()) if (pnl < 0).any() else M(None, "no losing trades")
    return out


def _benchmark_metrics(eq: pd.Series, r: pd.Series, inp: Inputs, ppy: float) -> dict[str, dict]:
    keys = ("benchmark_total_return", "excess_return", "beta", "alpha", "correlation", "tracking_error",
            "information_ratio")
    if inp.benchmark is None or inp.benchmark.dropna().empty:
        return {k: M(None, "no benchmark") for k in keys}
    b = inp.benchmark.reindex(eq.index).astype(float)
    if b.isna().any():
        b = b.ffill()
    if b.isna().iloc[0]:
        return {k: M(None, "benchmark has no value at the start") for k in keys}
    br = b.pct_change().iloc[1:]
    both = pd.concat([r, br], axis=1, keys=["s", "b"]).dropna()
    out = {"benchmark_total_return": M(b.iloc[-1] / b.iloc[0] - 1),
           "excess_return": M((eq.iloc[-1] / eq.iloc[0]) - (b.iloc[-1] / b.iloc[0]))}
    if len(both) < MIN_PERIODS_RATIO:
        why = f"needs >= {MIN_PERIODS_RATIO} overlapping returns"
        out.update({k: M(None, why) for k in keys[2:]})
        return out
    vb = both["b"].var(ddof=1)
    if vb > 0:
        beta = both["s"].cov(both["b"]) / vb
        out["beta"] = M(beta)
        out["alpha"] = M((both["s"].mean() - beta * both["b"].mean()) * ppy)
    else:
        out["beta"] = out["alpha"] = M(None, "benchmark has zero variance")
    sd_s, sd_b = both["s"].std(ddof=1), both["b"].std(ddof=1)
    out["correlation"] = M(both["s"].corr(both["b"])) if sd_s > 0 and sd_b > 0 else M(None, "zero variance")
    diff = both["s"] - both["b"]
    te = diff.std(ddof=1) * math.sqrt(ppy)
    out["tracking_error"] = M(te)
    out["information_ratio"] = M(diff.mean() * ppy / te) if te > 0 else M(None, "zero tracking error")
    return out


# -- tables for charts --------------------------------------------------------------------------------------------
def monthly_returns(equity: pd.Series) -> pd.DataFrame:
    """year, month, ret: compounded within each calendar month, from the last value of the previous month."""
    eq = equity.dropna().astype(float)
    if len(eq) < 2:
        return pd.DataFrame(columns=["year", "month", "ret"])
    last = eq.groupby(eq.index.to_period("M")).last()
    base = pd.concat([pd.Series([eq.iloc[0]], index=[last.index[0] - 1]), last])
    rets = (base / base.shift(1) - 1).iloc[1:]
    return pd.DataFrame(dict(year=rets.index.year, month=rets.index.month, ret=rets.to_numpy()))


def annual_returns(equity: pd.Series) -> pd.DataFrame:
    eq = equity.dropna().astype(float)
    if len(eq) < 2:
        return pd.DataFrame(columns=["year", "ret"])
    last = eq.groupby(eq.index.year).last()
    base = pd.concat([pd.Series([eq.iloc[0]], index=[last.index[0] - 1]), last])
    rets = (base / base.shift(1) - 1).iloc[1:]
    return pd.DataFrame(dict(year=rets.index.astype(int), ret=rets.to_numpy()))


def rolling(equity: pd.Series, window: int, periods_per_year: float = 252.0) -> pd.DataFrame:
    """Rolling return, volatility and Sharpe over `window` periods (NaN until the window is full)."""
    r = returns(equity)
    out = pd.DataFrame(index=r.index)
    out["return"] = (1 + r).rolling(window).apply(np.prod, raw=True) - 1
    sd = r.rolling(window).std(ddof=1)
    out["volatility"] = sd * math.sqrt(periods_per_year)
    out["sharpe"] = (r.rolling(window).mean() / sd.replace(0, np.nan)) * math.sqrt(periods_per_year)
    return out


def round_trips(fills: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """FIFO-matched closed trades and the lots still open. fills: symbol, quantity (signed), price, commission,
    timestamp, in execution order. A trade's pnl is net of its share of both fills' commissions."""
    closed, open_lots = [], []
    lots: dict[str, list[dict]] = {}
    for f in fills.itertuples(index=False):
        q, px, com = float(f.quantity), float(f.price), float(f.commission)
        per_share_cost = com / abs(q) if q else 0.0
        book = lots.setdefault(f.symbol, [])
        remaining = q
        while remaining and book and (book[0]["qty"] > 0) != (remaining > 0):
            lot = book[0]
            take = min(abs(remaining), abs(lot["qty"]))
            sign = 1 if lot["qty"] > 0 else -1
            gross = take * (px - lot["price"]) * sign
            cost = take * (lot["cost_ps"] + per_share_cost)
            closed.append(dict(symbol=f.symbol, side="long" if sign > 0 else "short", quantity=take,
                               entry_date=lot["date"], exit_date=f.timestamp, entry_price=lot["price"],
                               exit_price=px, pnl=gross - cost,
                               **{"return": (gross - cost) / (take * lot["price"])}))
            lot["qty"] -= take * sign
            remaining -= take * (1 if remaining > 0 else -1)
            if abs(lot["qty"]) < 1e-12:
                book.pop(0)
        if abs(remaining) > 1e-12:
            book.append(dict(qty=remaining, price=px, date=f.timestamp, cost_ps=per_share_cost))
    for sym, book in lots.items():
        for lot in book:
            open_lots.append(dict(symbol=sym, side="long" if lot["qty"] > 0 else "short", quantity=abs(lot["qty"]),
                                  entry_date=lot["date"], entry_price=lot["price"]))
    cols = ["symbol", "side", "quantity", "entry_date", "exit_date", "entry_price", "exit_price", "pnl", "return"]
    return pd.DataFrame(closed, columns=cols), pd.DataFrame(open_lots, columns=["symbol", "side", "quantity",
                                                                                 "entry_date", "entry_price"])


def add_holding_periods(trades: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    if trades.empty:
        trades = trades.copy()
        trades["holding_periods"] = pd.Series(dtype=float)
        return trades
    t = trades.copy()
    a = sessions.searchsorted(pd.DatetimeIndex(pd.to_datetime(t["entry_date"])))
    b = sessions.searchsorted(pd.DatetimeIndex(pd.to_datetime(t["exit_date"])))
    t["holding_periods"] = b - a
    return t
