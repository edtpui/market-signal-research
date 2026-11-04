"""Fill prices and transaction costs: one implementation shared by the simulated broker (engine/broker.py) and the
entry-exit trade replay (entryexit/replay.py), so a trade measured in research and the same trade in a portfolio
backtest are priced by the same rules (docs/ARCHITECTURE.md 3.1).

Every function accepts numpy arrays (vectorized over trades) or plain floats; "not triggered" and "invalid bar" are
NaN, never a guessed price.

Reference prices on the fill bar (open, high, low, close):
  market  the open (next_open) or the close (next_close)
  limit   buy: the open if open <= limit, else the limit if low <= limit; sell mirrored (open >= limit, high >= limit)
  stop    buy: the open if open >= stop, else the stop if high >= stop; sell mirrored (open <= stop, low <= stop)
Limit and stop orders need finite, positive open, high and low; a gap through the level fills at the open.
The execution price adds adverse slippage and half the modelled spread; a limit fill never prices beyond its limit.
"""
from __future__ import annotations

import math

import numpy as np


def _finite_pos(*xs):
    ok = None
    for x in xs:
        m = np.isfinite(x) & (x > 0)
        ok = m if ok is None else ok & m
    return ok


def reference_price(kind: str, buy, op, hi=None, lo=None, cl=None, price=None, timing: str = "next_open"):
    """The bar price an order of `kind` ('market' | 'limit' | 'stop') would fill at, NaN where it does not fill."""
    with np.errstate(invalid="ignore"):
        if kind == "market":
            px = np.asarray(op if timing == "next_open" else cl, dtype=float)
            return np.where(_finite_pos(px), px, np.nan)
        op, hi, lo = (np.asarray(v, dtype=float) for v in (op, hi, lo))
        lvl = np.asarray(price, dtype=float)
        ok = _finite_pos(op, hi, lo) & np.isfinite(lvl) & (lvl > 0)
        if kind == "limit":
            b = np.where(op <= lvl, op, np.where(lo <= lvl, lvl, np.nan))
            s = np.where(op >= lvl, op, np.where(hi >= lvl, lvl, np.nan))
        elif kind == "stop":
            b = np.where(op >= lvl, op, np.where(hi >= lvl, lvl, np.nan))
            s = np.where(op <= lvl, op, np.where(lo <= lvl, lvl, np.nan))
        else:
            raise ValueError(f"unknown order kind {kind!r}")
        return np.where(ok, np.where(buy, b, s), np.nan)


def half_spread_bps(model: str, bps: float = 0.0, hl_fraction: float = 0.0, prev_hi=None, prev_lo=None,
                    prev_cl=None):
    """Half of the modelled full spread, in bps. hl_range uses the PREVIOUS bar's range (known before the fill bar);
    without a valid previous bar it is 0."""
    if model == "fixed_bps":
        return bps / 2
    if model == "hl_range":
        if prev_hi is None:
            return 0.0
        hi, lo, c = (np.asarray(v, dtype=float) for v in (prev_hi, prev_lo, prev_cl))
        with np.errstate(invalid="ignore", divide="ignore"):
            ok = np.isfinite(hi) & np.isfinite(lo) & np.isfinite(c) & (c > 0)
            return np.where(ok, hl_fraction * (hi - lo) / np.where(ok, c, 1.0) * 1e4 / 2, 0.0)
    return 0.0


def execution_price(ref, buy, slippage_bps: float, half_spread, limit=None):
    """ref adjusted against the trader by slippage plus half spread; a limit order never fills beyond its limit."""
    adj = (slippage_bps + np.asarray(half_spread, dtype=float)) / 1e4
    ref = np.asarray(ref, dtype=float)
    px = np.where(buy, ref * (1 + adj), ref * (1 - adj))
    if limit is not None:
        lim = np.asarray(limit, dtype=float)
        px = np.where(buy, np.minimum(px, lim), np.maximum(px, lim))
    return px


def commission(per_order: float, per_share: float, bps: float, minimum: float, quantity, price):
    """CommissionConfig.cost, vectorized: per_order + per_share x |q| + |q| x price x bps, at least `minimum` when
    positive, 0 for no trade."""
    q = np.abs(np.asarray(quantity, dtype=float))
    c = per_order + per_share * q + q * np.asarray(price, dtype=float) * bps / 1e4
    return np.where(c > 0, np.maximum(c, minimum), 0.0)


def scalar(x) -> float | None:
    """A 0-d result as a float, NaN as None (the broker's convention for "does not fill")."""
    v = float(x)
    return v if math.isfinite(v) else None


__all__ = ["reference_price", "half_spread_bps", "execution_price", "commission", "scalar"]
