"""Volatility families: realized volatility, ATR, Bollinger Bands, volatility compression and price channels
(Keltner, Donchian midline)."""
from __future__ import annotations

import numpy as np

from strategy_lab.signals.indicators import Indicators, cross_above, cross_below, rising_edge
from strategy_lab.signals.library._dsl import F, HLC, I, OHLC, fam, osc, overlay, sig


def register(reg):
    _volatility(reg)
    _atr(reg)
    _bollinger(reg)
    _compression(reg)
    _channel(reg)


def _volatility(reg):
    f = fam(reg, "volatility", "Realized volatility", "Volatility levels, percentiles, expansions and outsized moves.")
    pct = lambda ind, p: ind.ts_rank(f"rv{p['n']}", ind.realized_vol(p["n"]), p["lookback"])
    prm = {"n": I(20, 5, 126), "lookback": I(252, 63, 756)}
    show = lambda ind, p: osc((), RV=ind.realized_vol(p["n"]))
    sig(reg, "rv_percentile_high", f, "Volatility at a 1-year high percentile", "bearish",
        lambda ind, p: rising_edge(pct(ind, p) >= p["q"]),
        "RV(n) in the top q of its own last `lookback` values (first session)",
        params={**prm, "q": F(0.9, 0.5, 0.99)}, warmup=lambda p: p["n"] + p["lookback"], show=show)
    sig(reg, "rv_percentile_low", f, "Volatility at a 1-year low percentile", "bullish",
        lambda ind, p: rising_edge(pct(ind, p) <= p["q"]),
        "RV(n) in the bottom q of its own last `lookback` values (first session)",
        params={**prm, "q": F(0.1, 0.01, 0.5)}, warmup=lambda p: p["n"] + p["lookback"], show=show)
    sig(reg, "vol_expansion", f, "Short-term volatility expansion", "bearish",
        lambda ind, p: rising_edge(ind.realized_vol(p["short"]) / ind.realized_vol(p["long"]) >= p["ratio"]),
        "RV(short) / RV(long) >= ratio (first session)",
        params={"short": I(10, 3, 42), "long": I(60, 21, 252), "ratio": F(1.5, 1.05, 4)},
        warmup=lambda p: p["long"] + 1)
    sd = lambda ind, p: ind.ret(1).rolling(p["n"], min_periods=p["n"]).std(ddof=1).shift(1)
    sig(reg, "vol_breakout_up", f, "Outsized up move", "bullish",
        lambda ind, p: ind.ret(1) >= p["k"] * sd(ind, p),
        "daily return >= k x the standard deviation of the previous n daily returns",
        params={"n": I(20, 10, 126), "k": F(2.5, 1.5, 6)}, warmup=lambda p: p["n"] + 2)
    sig(reg, "vol_breakout_down", f, "Outsized down move", "bearish",
        lambda ind, p: ind.ret(1) <= -p["k"] * sd(ind, p),
        "daily return <= -k x the standard deviation of the previous n daily returns",
        params={"n": I(20, 10, 126), "k": F(2.5, 1.5, 6)}, warmup=lambda p: p["n"] + 2)
    sig(reg, "low_vol_decile", f, "Lowest-volatility decile", "bullish",
        lambda ind, p: rising_edge(Indicators.xs_rank(ind.realized_vol(p["n"])) <= 0.1),
        "RV(n) in the bottom decile of the universe (first session)", params={"n": I(63, 10, 252)},
        warmup=lambda p: p["n"] + 1, scope="cross_section", doc="The low-volatility anomaly.")
    sig(reg, "high_vol_decile", f, "Highest-volatility decile", "bearish",
        lambda ind, p: rising_edge(Indicators.xs_rank(ind.realized_vol(p["n"])) > 0.9),
        "RV(n) in the top decile of the universe (first session)", params={"n": I(63, 10, 252)},
        warmup=lambda p: p["n"] + 1, scope="cross_section")


def _atr(reg):
    f = fam(reg, "atr", "Average true range", "Range expansion relative to ATR and ATR-based trailing stops.")
    rng = lambda ind: ind.high - ind.low
    loc = lambda ind: (ind.close - ind.low) / (ind.high - ind.low).where(ind.high > ind.low)
    prm = {"n": I(14, 5, 50), "k": F(2.0, 1.0, 5.0, "range in ATRs")}
    sig(reg, "wide_range_bar_up", f, "Wide-range bar closing strong", "bullish",
        lambda ind, p: (rng(ind) >= p["k"] * ind.atr(p["n"]).shift(1)) & (loc(ind) >= 0.75),
        "high - low >= k x ATR(n) of t-1 and the close is in the top quarter of the bar", params=prm, fields=HLC,
        warmup=lambda p: 5 * p["n"])
    sig(reg, "wide_range_bar_down", f, "Wide-range bar closing weak", "bearish",
        lambda ind, p: (rng(ind) >= p["k"] * ind.atr(p["n"]).shift(1)) & (loc(ind) <= 0.25),
        "high - low >= k x ATR(n) of t-1 and the close is in the bottom quarter of the bar", params=prm, fields=HLC,
        warmup=lambda p: 5 * p["n"])
    cprm = {"n": I(22, 5, 100), "k": F(3.0, 1.0, 6.0)}
    show = lambda ind, p: overlay(LongStop=ind.highest(p["n"], "high") - p["k"] * ind.atr(p["n"]),
                                  ShortStop=ind.lowest(p["n"], "low") + p["k"] * ind.atr(p["n"]))
    sig(reg, "chandelier_flip_up", f, "Close clears the ATR short stop", "bullish",
        lambda ind, p: cross_above(ind.close, ind.lowest(p["n"], "low") + p["k"] * ind.atr(p["n"])),
        "close crosses above lowest low(n) + k x ATR(n) (chandelier short stop)", params=cprm, fields=HLC,
        warmup=lambda p: 5 * p["n"], show=show)
    sig(reg, "chandelier_flip_down", f, "Close breaks the ATR long stop", "bearish",
        lambda ind, p: cross_below(ind.close, ind.highest(p["n"], "high") - p["k"] * ind.atr(p["n"])),
        "close crosses below highest high(n) - k x ATR(n) (chandelier long stop)", params=cprm, fields=HLC,
        warmup=lambda p: 5 * p["n"], show=show)
    sig(reg, "atr_expansion_up", f, "ATR expansion in an up move", "bullish",
        lambda ind, p: rising_edge(ind.atr(p["short"]) / ind.atr(p["long"]) >= p["ratio"]) & (ind.ret(10) > 0),
        "ATR(short) / ATR(long) reaches ratio (first session) with a positive 10-session return",
        params={"short": I(14, 5, 30), "long": I(50, 20, 200), "ratio": F(1.3, 1.05, 3)}, fields=HLC,
        warmup=lambda p: 5 * p["long"])
    sig(reg, "atr_expansion_down", f, "ATR expansion in a down move", "bearish",
        lambda ind, p: rising_edge(ind.atr(p["short"]) / ind.atr(p["long"]) >= p["ratio"]) & (ind.ret(10) < 0),
        "ATR(short) / ATR(long) reaches ratio (first session) with a negative 10-session return",
        params={"short": I(14, 5, 30), "long": I(50, 20, 200), "ratio": F(1.3, 1.05, 3)}, fields=HLC,
        warmup=lambda p: 5 * p["long"])


def _bollinger(reg):
    f = fam(reg, "bollinger", "Bollinger Bands", "Close against SMA(n) +/- k population standard deviations.")
    prm = {"n": I(20, 5, 100), "k": F(2.0, 0.5, 4.0)}
    b = lambda ind, p: ind.bbands(p["n"], p["k"])
    show = lambda ind, p: overlay(Upper=b(ind, p)["upper"], Mid=b(ind, p)["mid"], Lower=b(ind, p)["lower"])
    sig(reg, "bb_below_lower", f, "Close below the lower band", "bullish",
        lambda ind, p: ind.close < b(ind, p)["lower"], "close < lower band (mean-reversion reading)", params=prm,
        warmup=lambda p: p["n"], show=show)
    sig(reg, "bb_lower_reentry", f, "Close back inside from the lower band", "bullish",
        lambda ind, p: cross_above(ind.close, b(ind, p)["lower"]), "close crosses above the lower band", params=prm,
        warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "bb_upper_reentry", f, "Close back inside from the upper band", "bearish",
        lambda ind, p: cross_below(ind.close, b(ind, p)["upper"]), "close crosses below the upper band", params=prm,
        warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "bb_upper_breakout", f, "Close breaks above the upper band", "bullish",
        lambda ind, p: cross_above(ind.close, b(ind, p)["upper"]), "close crosses above the upper band "
        "(momentum reading)", params=prm, warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "bb_oversold_in_uptrend", f, "Below the lower band in an uptrend", "bullish",
        lambda ind, p: (b(ind, p)["pctb"] < 0) & (ind.close > ind.sma(200)), "%B < 0 while close > SMA200",
        params=prm, warmup=201, show=show)
    sig(reg, "bb_walk_upper", f, "Walking the upper band", "bullish",
        lambda ind, p: rising_edge((ind.close > b(ind, p)["upper"]).astype(float).rolling(3, min_periods=3).sum() >= 3),
        "close above the upper band on 3 consecutive sessions (first session)", params=prm,
        warmup=lambda p: p["n"] + 3, show=show)


def _compression(reg):
    f = fam(reg, "volatility_compression", "Volatility compression", "Narrow ranges and squeezes, then a break.")
    bw = lambda ind: ind.bbands(20, 2.0)["width"]
    squeezed = lambda ind, p: (bw(ind) <= bw(ind).rolling(p["lookback"], min_periods=p["lookback"]).min() * 1.0001)
    prm = {"lookback": I(126, 20, 504), "recent": I(5, 1, 20)}
    sig(reg, "bb_squeeze_breakout_up", f, "Bollinger squeeze, upside release", "bullish",
        lambda ind, p: squeezed(ind, p).astype(float).shift(1).rolling(p["recent"], min_periods=1).max().fillna(0).astype(bool)
        & cross_above(ind.close, ind.bbands(20, 2.0)["upper"]),
        "band width hit its lookback-session low within the last `recent` sessions, then close crosses above the "
        "upper band", params=prm, warmup=lambda p: p["lookback"] + 21,
        show=lambda ind, p: osc((), BandWidth=bw(ind)))
    sig(reg, "bb_squeeze_breakout_down", f, "Bollinger squeeze, downside release", "bearish",
        lambda ind, p: squeezed(ind, p).astype(float).shift(1).rolling(p["recent"], min_periods=1).max().fillna(0).astype(bool)
        & cross_below(ind.close, ind.bbands(20, 2.0)["lower"]),
        "band width hit its lookback-session low within the last `recent` sessions, then close crosses below the "
        "lower band", params=prm, warmup=lambda p: p["lookback"] + 21,
        show=lambda ind, p: osc((), BandWidth=bw(ind)))
    rng = lambda ind: ind.high - ind.low
    nr = lambda ind, p: rng(ind) <= rng(ind).rolling(p["n"], min_periods=p["n"]).min()
    sig(reg, "nr7_breakout_up", f, "NR7 upside breakout", "bullish",
        lambda ind, p: nr(ind, p).shift(1, fill_value=False) & (ind.close > ind.high.shift(1)),
        "t-1 had the narrowest range of the last n sessions (NR7) and t closes above its high",
        params={"n": I(7, 4, 20)}, fields=HLC, warmup=lambda p: p["n"] + 1)
    sig(reg, "nr7_breakout_down", f, "NR7 downside breakout", "bearish",
        lambda ind, p: nr(ind, p).shift(1, fill_value=False) & (ind.close < ind.low.shift(1)),
        "t-1 had the narrowest range of the last n sessions (NR7) and t closes below its low",
        params={"n": I(7, 4, 20)}, fields=HLC, warmup=lambda p: p["n"] + 1)
    sq = lambda ind: (ind.bbands(20, 2.0)["upper"] < ind.keltner(20, 1.5)["upper"]) & \
        (ind.bbands(20, 2.0)["lower"] > ind.keltner(20, 1.5)["lower"])
    sig(reg, "squeeze_fire_up", f, "Squeeze fires up", "bullish",
        lambda ind, p: sq(ind).shift(1, fill_value=False) & ~sq(ind) & (ind.close > ind.sma(20)),
        "Bollinger(20, 2) inside Keltner(20, 1.5 ATR) on t-1 but not on t, with close > SMA20", fields=HLC,
        warmup=100)
    sig(reg, "squeeze_fire_down", f, "Squeeze fires down", "bearish",
        lambda ind, p: sq(ind).shift(1, fill_value=False) & ~sq(ind) & (ind.close < ind.sma(20)),
        "Bollinger(20, 2) inside Keltner(20, 1.5 ATR) on t-1 but not on t, with close < SMA20", fields=HLC,
        warmup=100)


def _channel(reg):
    f = fam(reg, "channel", "Price channels", "Keltner channels and the Donchian midline.")
    prm = {"n": I(20, 5, 100), "mult": F(2.0, 0.5, 4.0)}
    k = lambda ind, p: ind.keltner(p["n"], p["mult"])
    show = lambda ind, p: overlay(Upper=k(ind, p)["upper"], Mid=k(ind, p)["mid"], Lower=k(ind, p)["lower"])
    w = lambda p: 5 * p["n"]
    sig(reg, "keltner_breakout_up", f, "Close breaks above the Keltner channel", "bullish",
        lambda ind, p: cross_above(ind.close, k(ind, p)["upper"]), "close crosses above EMA(n) + mult x ATR(n)",
        params=prm, fields=HLC, warmup=w, show=show)
    sig(reg, "keltner_breakdown", f, "Close breaks below the Keltner channel", "bearish",
        lambda ind, p: cross_below(ind.close, k(ind, p)["lower"]), "close crosses below EMA(n) - mult x ATR(n)",
        params=prm, fields=HLC, warmup=w, show=show)
    sig(reg, "keltner_lower_reentry", f, "Close back inside from the lower Keltner band", "bullish",
        lambda ind, p: cross_above(ind.close, k(ind, p)["lower"]), "close crosses above the lower Keltner band",
        params=prm, fields=HLC, warmup=w, show=show)
    sig(reg, "keltner_upper_reentry", f, "Close back inside from the upper Keltner band", "bearish",
        lambda ind, p: cross_below(ind.close, k(ind, p)["upper"]), "close crosses below the upper Keltner band",
        params=prm, fields=HLC, warmup=w, show=show)
    mid = lambda ind, p: (ind.highest(p["n"], "high") + ind.lowest(p["n"], "low")) / 2.0
    sig(reg, "donchian_mid_cross_up", f, "Close crosses above the Donchian midline", "bullish",
        lambda ind, p: cross_above(ind.close, mid(ind, p)), "close crosses above (highest high + lowest low) / 2 "
        "over n sessions", params={"n": I(20, 5, 252)}, fields=HLC, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(Mid=mid(ind, p)))
    sig(reg, "donchian_mid_cross_down", f, "Close crosses below the Donchian midline", "bearish",
        lambda ind, p: cross_below(ind.close, mid(ind, p)), "close crosses below the Donchian midline",
        params={"n": I(20, 5, 252)}, fields=HLC, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(Mid=mid(ind, p)))


__all__ = ["register", "np", "OHLC"]
