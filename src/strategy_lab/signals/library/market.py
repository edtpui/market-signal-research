"""Families that look beyond the symbol: relative strength against the benchmark (SPY by default) and market breadth
computed over the experiment's own universe.

Market-scope signals fire for every symbol on the same session. A matched random stock drawn on that session is
then a draw from the same population, so the matched-stock comparison has no power by construction; their evidence
is the random-date and benchmark comparisons (docs/SIGNAL_RESEARCH.md)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_lab.signals.indicators import cross_above, cross_below, rising_edge
from strategy_lab.signals.library._dsl import F, I, fam, osc, sig


def register(reg):
    _relative_strength(reg)
    _breadth(reg)


def _relative_strength(reg):
    f = fam(reg, "relative_strength", "Relative strength", "The stock against the benchmark: RS-line highs, lows, "
                                                           "crosses and outperformance.")
    rs = lambda ind: ind.rs_line()
    show = lambda ind, p: osc((), RS=rs(ind))
    sig(reg, "rs_line_new_high", f, "RS line at a 50-day high", "bullish",
        lambda ind, p: rising_edge(rs(ind) >= rs(ind).rolling(p["n"], min_periods=p["n"]).max()),
        "close / benchmark close at its n-session high (first session)", params={"n": I(50, 10, 252)},
        warmup=lambda p: p["n"] + 1, bench=True, show=show)
    sig(reg, "rs_line_new_low", f, "RS line at a 50-day low", "bearish",
        lambda ind, p: rising_edge(rs(ind) <= rs(ind).rolling(p["n"], min_periods=p["n"]).min()),
        "close / benchmark close at its n-session low (first session)", params={"n": I(50, 10, 252)},
        warmup=lambda p: p["n"] + 1, bench=True, show=show)
    sig(reg, "rs_cross_above_sma", f, "RS line crosses above its average", "bullish",
        lambda ind, p: cross_above(rs(ind), rs(ind).rolling(p["n"], min_periods=p["n"]).mean()),
        "RS line crosses above its SMA(n)", params={"n": I(50, 10, 252)}, warmup=lambda p: p["n"] + 1, bench=True,
        show=show)
    sig(reg, "rs_cross_below_sma", f, "RS line crosses below its average", "bearish",
        lambda ind, p: cross_below(rs(ind), rs(ind).rolling(p["n"], min_periods=p["n"]).mean()),
        "RS line crosses below its SMA(n)", params={"n": I(50, 10, 252)}, warmup=lambda p: p["n"] + 1, bench=True,
        show=show)
    rel = lambda ind, p: rs(ind) / rs(ind).shift(p["n"]) - 1.0
    sig(reg, "rs_outperformance", f, "Outperforming the benchmark by 10%", "bullish",
        lambda ind, p: rising_edge(rel(ind, p) >= p["pct"]),
        "stock return minus benchmark return over n sessions (as RS-line change) >= pct (first session)",
        params={"n": I(63, 10, 252), "pct": F(0.1, 0.02, 0.5)}, warmup=lambda p: p["n"] + 1, bench=True)
    sig(reg, "rs_underperformance", f, "Underperforming the benchmark by 10%", "bearish",
        lambda ind, p: rising_edge(rel(ind, p) <= -p["pct"]),
        "RS-line change over n sessions <= -pct (first session)",
        params={"n": I(63, 10, 252), "pct": F(0.1, 0.02, 0.5)}, warmup=lambda p: p["n"] + 1, bench=True)
    sig(reg, "rs_leads_price", f, "RS line high before price high", "bullish",
        lambda ind, p: rising_edge((rs(ind) >= rs(ind).rolling(p["n"], min_periods=p["n"]).max())
                                   & (ind.close < ind.highest(p["n"], "close"))),
        "RS line at its n-session high while the close is below its n-session high (first session)",
        params={"n": I(50, 10, 252)}, warmup=lambda p: p["n"] + 1, bench=True, show=show)


def _breadth(reg):
    f = fam(reg, "market_breadth", "Market breadth", "Participation across the experiment's universe: washouts, "
                                                     "thrusts, the advance/decline line, and leaders against breadth.")
    above = lambda ind, p: ind.breadth_above_sma(p["n"])
    show = lambda ind, p: osc((), BreadthAboveSMA=above(ind, p))
    sig(reg, "breadth_washout", f, "Breadth washout", "bullish",
        lambda ind, p: _edge(above(ind, p) <= p["level"]),
        "share of the universe above its SMA(n) falls to level or lower (first session)",
        params={"n": I(50, 10, 200), "level": F(0.15, 0.02, 0.5)}, warmup=lambda p: p["n"] + 1, scope="market",
        show=show)
    sig(reg, "breadth_overheated", f, "Breadth overheated", "bearish",
        lambda ind, p: _edge(above(ind, p) >= p["level"]),
        "share of the universe above its SMA(n) rises to level or higher (first session)",
        params={"n": I(50, 10, 200), "level": F(0.85, 0.5, 0.99)}, warmup=lambda p: p["n"] + 1, scope="market",
        show=show)
    adv = lambda ind: ind.advance_share().ewm(span=10, adjust=False, min_periods=10).mean()
    sig(reg, "breadth_thrust", f, "Breadth thrust", "bullish",
        lambda ind, p: (adv(ind) >= p["hi"]) & (adv(ind).shift(1) < p["hi"])
        & (adv(ind).shift(1).rolling(10, min_periods=1).min() <= p["lo"]),
        "10-session EMA of the advancing share crosses above hi within 10 sessions of being at or below lo",
        params={"lo": F(0.4, 0.1, 0.5), "hi": F(0.615, 0.5, 0.9)}, warmup=50, scope="market",
        show=lambda ind, p: osc((p["lo"], p["hi"]), AdvanceEMA=adv(ind)))
    sig(reg, "ad_line_new_high", f, "Advance/decline line at a 50-day high", "bullish",
        lambda ind, p: _edge(ind.ad_cumulative() >= ind.ad_cumulative().rolling(p["n"], min_periods=p["n"]).max()),
        "cumulative advances minus declines at its n-session high (first session)", params={"n": I(50, 10, 252)},
        warmup=lambda p: p["n"] + 1, scope="market")
    sig(reg, "leader_in_weak_market", f, "Leader in a weak market", "bullish",
        lambda ind, p: (ind.close >= ind.highest(50, "close")) & _frame_of(above(ind, {"n": 50}) < p["level"], ind),
        "close at its 50-session closing high while fewer than `level` of the universe are above their SMA50",
        params={"level": F(0.4, 0.1, 0.6)}, warmup=51, scope="cross_section")
    sig(reg, "laggard_in_strong_market", f, "Laggard in a strong market", "bearish",
        lambda ind, p: (ind.close <= ind.lowest(50, "close")) & _frame_of(above(ind, {"n": 50}) > p["level"], ind),
        "close at its 50-session closing low while more than `level` of the universe are above their SMA50",
        params={"level": F(0.6, 0.4, 0.9)}, warmup=51, scope="cross_section")


def _edge(s: pd.Series) -> pd.Series:
    s = s.fillna(False).astype(bool)
    return s & ~s.shift(1, fill_value=False)


def _frame_of(s: pd.Series, ind) -> pd.DataFrame:
    cols = ind.panel.fields["close"].columns
    v = s.reindex(ind.index).fillna(False).astype(bool).to_numpy()
    return pd.DataFrame(np.repeat(v[:, None], len(cols), axis=1), index=ind.index, columns=cols)
