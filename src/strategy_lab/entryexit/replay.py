"""Trade replay: every entry event as an independent position managed by an exit plan, vectorized across trades
and stepped through holding sessions. It is the trade-level measurement of the pairing engine (identical entry
events for every exit, random-entry controls), not a portfolio simulator: positions never compete for cash or
slots. Portfolio results come from the event-driven engine (strategies/entry_exit.py).

It shares the engine's execution model: fill prices, slippage, spread and commissions are engine/fills.py; the
plan's decisions are entryexit/plan.py, the code the event-driven strategy runs; the entry session's attached stop
and targets are evaluated after the entry fill as the broker does. tests/test_entry_exit.py checks that a backtest
of the same events produces the same entry and exit sessions, prices and reasons.

Timing for an event on the signal session t: entry at the open of t+1 (the entry session e, holding session 0);
orders decided at a close execute in the next session; a position still open at the close of the last session of
its partition (`end`) is closed at that close (reason period_end, at the last valid close if the bar is missing).
Per-share commissions and per-order minimums are converted with a reference notional per trade.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from strategy_lab.engine import fills as F
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.entryexit.components import Ctx
from strategy_lab.entryexit.plan import R_DATA_END, R_NONE, R_PERIOD_END, PlanRuntime

EPS = 1e-12
EXIT_AT_OPEN, EXIT_INTRABAR, EXIT_AT_CLOSE = 0, 1, 2


@dataclass
class Bars:
    """Aligned (session x symbol) arrays of one study: adjusted open/high/low/close."""
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray

    def __post_init__(self):
        with np.errstate(invalid="ignore"):
            self.open_ok = np.isfinite(self.open) & (self.open > 0)
            self.ohl_ok = self.open_ok & np.isfinite(self.high) & (self.high > 0) & np.isfinite(self.low) & \
                (self.low > 0)
            self.close_ok = np.isfinite(self.close) & (self.close > 0)
        # last valid close at or before each session (stale mark for a period-end exit on a missing bar)
        self.last_close = pd.DataFrame(np.where(self.close_ok, self.close, np.nan)).ffill().to_numpy()

    @property
    def shape(self):
        return self.close.shape


class Costs:
    def __init__(self, ex: ExecutionConfig, notional: float):
        self.ex, self.notional = ex, float(notional)
        self.slip = ex.slippage_bps
        self.c = ex.commission

    def half_spread(self, bars: Bars, i: np.ndarray, j: np.ndarray) -> np.ndarray:
        sp = self.ex.spread
        if sp.model == "fixed_bps":
            return np.full(len(i), sp.bps / 2)
        if sp.model == "hl_range":
            p = i - 1
            ok = p >= 0
            pp = np.maximum(p, 0)
            out = F.half_spread_bps("hl_range", sp.bps, sp.hl_fraction, bars.high[pp, j], bars.low[pp, j],
                                    bars.close[pp, j])
            return np.where(ok, out, 0.0)
        return np.zeros(len(i))

    def commission(self, shares: np.ndarray, price: np.ndarray) -> np.ndarray:
        c = self.c
        return F.commission(c.per_order, c.per_share, c.bps, c.minimum, shares, price)


def replay(rt: PlanRuntime, bars: Bars, series: dict[str, np.ndarray], t: np.ndarray, j: np.ndarray,
           end: np.ndarray, costs: Costs, bench_open: np.ndarray | None = None,
           bench_close: np.ndarray | None = None) -> pd.DataFrame:
    """Replays events (signal session t, symbol column j), each closed by the session end[k] at the latest.
    series: the plan's series as (session x symbol) arrays (PlanRuntime.series), including 'ref_atr'.
    Returns one row per event (excluded events have status != 'ok' and NaN results)."""
    t = np.asarray(t, dtype=np.int64)
    j = np.asarray(j, dtype=np.int64)
    end = np.asarray(end, dtype=np.int64)
    n = len(t)
    T = bars.shape[0]
    cur = t.copy()                                  # each trade's current session

    def getter(key, ix):
        a = bars.__dict__[key] if key in ("open", "high", "low", "close") else series[key]
        return a[cur[ix], j[ix]]

    B = rt.new_batch(n)
    allx = np.arange(n)
    rt.at_signal(Ctx(getter, allx), B, allx)
    e = t + 1
    status = np.full(n, "ok", dtype=object)
    status[e > np.minimum(end, T - 1)] = "no_entry_session"
    inb = e <= T - 1
    eo = np.where(inb, e, 0)
    status[(status == "ok") & ~(inb & bars.open_ok[eo, j])] = "missing_entry_bar"
    status[(status == "ok") & ~(B["valid"] > 0.5)] = "no_reference_risk"
    alive = status == "ok"
    ix = np.flatnonzero(alive)

    # results
    P = np.full(n, np.nan)
    value = np.zeros(n)          # sum of fraction x exit price
    comm = np.zeros(n)           # dollars, on the reference notional
    shares = np.full(n, np.nan)
    exit_bar = np.full(n, -1, dtype=np.int64)
    exit_kind = np.full(n, -1, dtype=np.int64)
    reason = np.full(n, R_NONE, dtype=np.int64)
    n_fills = np.zeros(n, dtype=np.int64)
    tranches = np.zeros(n, dtype=np.int64)
    mae = np.full(n, np.nan)
    mfe = np.full(n, np.nan)
    stop_at_entry = np.full(n, np.nan)

    # entry fill at the open of e
    cur[ix] = e[ix]
    ref = bars.open[e[ix], j[ix]]
    px = F.execution_price(ref, True, costs.slip, costs.half_spread(bars, e[ix], j[ix]))
    P[ix] = px
    shares[ix] = costs.notional / px
    comm[ix] += costs.commission(shares[ix], px)
    rt.at_fill(B, ix, px)
    stop_at_entry[ix] = B["S0"][ix]

    # pending orders for the entry session: the attached legs (no market exit)
    mkt = np.zeros(n, bool)
    mkt_reason = np.full(n, R_NONE, dtype=np.int64)
    stop = np.full(n, np.nan)
    stop_reason = np.full(n, R_NONE, dtype=np.int64)
    stop[ix] = B["ibm"][ix] * px + B["iba"][ix]
    stop_reason[ix] = _stop_comp_at_entry(rt, B, ix, stop[ix])
    tg_level = {k: np.full(n, np.nan) for k in rt.targets}
    tg_frac = {k: np.zeros(n) for k in rt.targets}
    for k in rt.targets:
        comp = rt.comps[k]
        tg_level[k][ix] = B[comp.st("level")][ix]
        tg_frac[k][ix] = np.where(B[comp.st("filled")][ix] < 0.5, B[comp.st("frac")][ix], 0.0)

    def close_out(sel, price, kind, code):
        """Sells the whole remaining fraction of trades sel at price."""
        rem = B["rem"][sel]
        sh = shares[sel] * rem
        value[sel] += rem * price
        comm[sel] += costs.commission(sh, price)
        n_fills[sel] += 1
        B["rem"][sel] = 0.0
        exit_bar[sel] = cur[sel]
        exit_kind[sel] = kind
        reason[sel] = code
        mae[sel] = np.fmin(B["ll"][sel], price)
        mfe[sel] = np.fmax(B["hh"][sel], price)
        alive[sel] = False

    while True:
        ix = np.flatnonzero(alive)
        if not len(ix):
            break
        i, jj = cur[ix], j[ix]
        first = i == e[ix]
        # 1. market exits decided at the previous close: the open
        m = mkt[ix] & bars.open_ok[i, jj] & ~first
        if m.any():
            s = ix[m]
            p = F.execution_price(bars.open[cur[s], j[s]], False, costs.slip, costs.half_spread(bars, cur[s], j[s]))
            close_out(s, p, EXIT_AT_OPEN, mkt_reason[s])
        # 2. the protective stop
        ix = np.flatnonzero(alive)
        i, jj = cur[ix], j[ix]
        ok = bars.ohl_ok[i, jj] & np.isfinite(stop[ix])
        if ok.any():
            s = ix[ok]
            r = F.reference_price("stop", False, bars.open[cur[s], j[s]], bars.high[cur[s], j[s]],
                                  bars.low[cur[s], j[s]], price=stop[s])
            hit = np.isfinite(r)
            if hit.any():
                h = s[hit]
                p = F.execution_price(r[hit], False, costs.slip, costs.half_spread(bars, cur[h], j[h]))
                close_out(h, p, EXIT_INTRABAR, stop_reason[h])
        # 3. targets, in plan order
        for k in rt.targets:
            ix = np.flatnonzero(alive)
            i, jj = cur[ix], j[ix]
            ok = bars.ohl_ok[i, jj] & np.isfinite(tg_level[k][ix]) & (tg_frac[k][ix] > 0)
            if not ok.any():
                continue
            s = ix[ok]
            lvl = tg_level[k][s]
            r = F.reference_price("limit", False, bars.open[cur[s], j[s]], bars.high[cur[s], j[s]],
                                  bars.low[cur[s], j[s]], price=lvl)
            hit = np.isfinite(r)
            if not hit.any():
                continue
            h = s[hit]
            p = F.execution_price(r[hit], False, costs.slip, costs.half_spread(bars, cur[h], j[h]), limit=lvl[hit])
            frac = np.minimum(tg_frac[k][h], B["rem"][h])
            value[h] += frac * p
            comm[h] += costs.commission(shares[h] * frac, p)
            n_fills[h] += 1
            tranches[h] += 1
            rt.tranche_filled(B, h, k, frac)
            tg_frac[k][h] = 0.0
            done = B["rem"][h] <= EPS
            if done.any():
                d = h[done]
                B["rem"][d] = 0.0
                exit_bar[d] = cur[d]
                exit_kind[d] = EXIT_INTRABAR
                reason[d] = k
                mae[d] = np.fmin(B["ll"][d], p[done])
                mfe[d] = np.fmax(B["hh"][d], p[done])
                alive[d] = False
        # 4. the close: period end, or observe and decide the next session's orders
        ix = np.flatnonzero(alive)
        if not len(ix):
            break
        i, jj = cur[ix], j[ix]
        B["bars"][ix] = i - e[ix]
        at_end = i >= np.minimum(end[ix], T - 1)
        if at_end.any():
            s = ix[at_end]
            lc = bars.last_close[cur[s], j[s]]
            p = F.execution_price(lc, False, costs.slip, costs.half_spread(bars, cur[s], j[s]))
            okp = np.isfinite(p)
            close_out(s[okp], p[okp], EXIT_AT_CLOSE, np.int64(R_PERIOD_END))
            if (~okp).any():
                bad = s[~okp]
                status[bad] = "no_exit_price"
                alive[bad] = False
        ix = np.flatnonzero(alive)
        if len(ix):
            i, jj = cur[ix], j[ix]
            okc = bars.close_ok[i, jj] & bars.ohl_ok[i, jj] if rt.common.has_high_low else bars.close_ok[i, jj]
            s = ix[okc]
            if len(s):
                ctx = Ctx(getter, s)
                rt.observe(ctx, B, s)
                d = rt.decide(ctx, B, s)
                mkt[s] = d.exit_now
                mkt_reason[s] = d.reason
                stop[s] = np.where(d.exit_now, np.nan, d.stop)
                stop_reason[s] = d.stop_reason
                for k, lvl, frac in d.targets:
                    tg_level[k][s] = lvl
                    tg_frac[k][s] = np.where(d.exit_now, 0.0, frac)
            cur[ix] += 1
            over = cur[ix] > T - 1
            if over.any():                             # cannot happen while end <= T-1; kept as a guard
                o = ix[over]
                status[o] = "data_end"
                reason[o] = R_DATA_END
                alive[o] = False

    ok = status == "ok"
    with np.errstate(invalid="ignore", divide="ignore"):
        gross = np.where(ok, value / P - 1.0, np.nan)
        net = np.where(ok, gross - comm / costs.notional, np.nan)
        pnl_ps = np.where(ok, value - P - comm / shares, np.nan)
        r_mult = pnl_ps / B["R"]
        r_ref = pnl_ps / B["Rref"]
    out = pd.DataFrame(dict(
        t=t, j=j, entry_bar=np.where(ok, e, -1), exit_bar=exit_bar, status=status, entry_price=P,
        exit_price=np.where(ok, value, np.nan), ret_gross=gross, ret=net, r_mult=r_mult, r_ref=r_ref,
        r_own=B["Rown"] > 0.5, risk=B["R"], risk_ref=B["Rref"], stop_at_entry=stop_at_entry,
        stop_breach=B["breach"] > 0.5, bars=np.where(ok, exit_bar - e, -1), reason_code=reason,
        exit_kind=exit_kind, fills=n_fills, tranches=tranches, commission=comm,
        mae=np.where(ok, mae / P - 1.0, np.nan), mfe=np.where(ok, mfe / P - 1.0, np.nan)))
    with np.errstate(invalid="ignore", divide="ignore"):
        out["mae_r"] = (out["mae"] * out["entry_price"]) / out["risk"]
        out["mfe_r"] = (out["mfe"] * out["entry_price"]) / out["risk"]
    out["reason"] = [rt.reason_label(int(c)) if s == "ok" else s for c, s in zip(reason, status)]
    if bench_open is not None and bench_close is not None:
        xb = np.clip(exit_bar, 0, T - 1)
        eb = np.clip(e, 0, T - 1)
        bexit = np.where(exit_kind == EXIT_AT_OPEN, bench_open[xb], bench_close[xb])
        with np.errstate(invalid="ignore", divide="ignore"):
            out["bench_ret"] = np.where(ok, bexit / bench_open[eb] - 1.0, np.nan)
    return out


def _stop_comp_at_entry(rt: PlanRuntime, B: dict, ix: np.ndarray, lvl: np.ndarray) -> np.ndarray:
    """The intrabar stop component whose terms define the entry session's attached stop."""
    out = np.full(len(ix), R_NONE, dtype=np.int64)
    for k in rt.stops:
        comp = rt.comps[k]
        if not comp.intrabar:
            continue
        m, a = B[comp.st("m")][ix], B[comp.st("a")][ix]
        same = np.isclose(m, B["ibm"][ix], equal_nan=False) & np.isclose(a, B["iba"][ix], equal_nan=False)
        out = np.where((out == R_NONE) & same, k, out)
    return out


__all__ = ["Bars", "Costs", "replay", "EXIT_AT_OPEN", "EXIT_INTRABAR", "EXIT_AT_CLOSE"]
