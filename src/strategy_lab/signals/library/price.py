"""Price families: gaps, breakouts, price action, candlestick patterns, support/resistance, mean reversion, trend
reversals and drawdowns. A gap is the session's open against the previous close; it is known at t's open, and the
signal is evaluated at t's close like every other one."""
from __future__ import annotations

from strategy_lab.signals.indicators import Indicators, cross_above, rising_edge
from strategy_lab.signals.library._dsl import F, HLC, I, OHLC, OHLCV, fam, overlay, sig


def register(reg):
    _gap(reg)
    _breakout(reg)
    _price_action(reg)
    _candlestick(reg)
    _support_resistance(reg)
    _mean_reversion(reg)
    _trend_reversal(reg)
    _drawdown(reg)


# shared candle geometry ------------------------------------------------------------------------------------------------
def body(ind):
    return (ind.close - ind.open).abs()


def rng(ind):
    return (ind.high - ind.low).where(ind.high > ind.low)


def upper_wick(ind):
    return ind.high - ind.open.where(ind.open > ind.close, ind.close)


def lower_wick(ind):
    return ind.open.where(ind.open < ind.close, ind.close) - ind.low


def bull(ind):
    return ind.close > ind.open


def bear(ind):
    return ind.close < ind.open


def down_context(ind):
    """Prior downtrend: the previous close below its SMA(10)."""
    return (ind.close < ind.sma(10)).shift(1, fill_value=False)


def up_context(ind):
    return (ind.close > ind.sma(10)).shift(1, fill_value=False)


def gap(ind):
    return ind.open / ind.close.shift(1) - 1.0


def _gap(reg):
    f = fam(reg, "gap", "Gaps", "Opening gaps against the previous close: continuation, fills, size and volume.")
    prm = {"pct": F(0.02, 0.003, 0.2, "minimum gap")}
    sig(reg, "gap_up", f, "Gap up", "bullish", lambda ind, p: gap(ind) >= p["pct"],
        "open >= previous close x (1 + pct)", params=prm, fields=OHLC, warmup=2)
    sig(reg, "gap_down", f, "Gap down", "bearish", lambda ind, p: gap(ind) <= -p["pct"],
        "open <= previous close x (1 - pct)", params=prm, fields=OHLC, warmup=2)
    sig(reg, "gap_down_filled", f, "Gap down filled", "bullish",
        lambda ind, p: (gap(ind) <= -p["pct"]) & (ind.close >= ind.close.shift(1)),
        "gap down of at least pct, and the close recovers to the previous close or above", params=prm,
        fields=OHLC, warmup=2)
    sig(reg, "gap_up_filled", f, "Gap up filled", "bearish",
        lambda ind, p: (gap(ind) >= p["pct"]) & (ind.close <= ind.close.shift(1)),
        "gap up of at least pct, and the close falls back to the previous close or below", params=prm,
        fields=OHLC, warmup=2)
    sig(reg, "gap_up_continuation", f, "Gap up and go", "bullish",
        lambda ind, p: (gap(ind) >= p["pct"]) & (ind.close > ind.open),
        "gap up of at least pct and the close above the open", params=prm, fields=OHLC, warmup=2)
    sig(reg, "gap_down_continuation", f, "Gap down and go", "bearish",
        lambda ind, p: (gap(ind) <= -p["pct"]) & (ind.close < ind.open),
        "gap down of at least pct and the close below the open", params=prm, fields=OHLC, warmup=2)
    aprm = {"k": F(1.0, 0.3, 5.0, "gap in ATRs"), "n": I(14, 5, 50)}
    sig(reg, "gap_up_large_atr", f, "Gap up larger than ATR", "bullish",
        lambda ind, p: (ind.open - ind.close.shift(1)) >= p["k"] * ind.atr(p["n"]).shift(1),
        "open - previous close >= k x ATR(n) of t-1", params=aprm, fields=OHLC, warmup=lambda p: 5 * p["n"])
    sig(reg, "gap_down_large_atr", f, "Gap down larger than ATR", "bearish",
        lambda ind, p: (ind.close.shift(1) - ind.open) >= p["k"] * ind.atr(p["n"]).shift(1),
        "previous close - open >= k x ATR(n) of t-1", params=aprm, fields=OHLC, warmup=lambda p: 5 * p["n"])
    vprm = {"pct": F(0.02, 0.003, 0.2), "rvol": F(2.0, 1.2, 10)}
    sig(reg, "gap_up_rvol", f, "Gap up on high relative volume", "bullish",
        lambda ind, p: (gap(ind) >= p["pct"]) & (ind.rel_volume(20) >= p["rvol"]),
        "gap up of at least pct with volume >= rvol x the previous 20-session mean", params=vprm, fields=OHLCV,
        warmup=21)
    sig(reg, "gap_down_rvol", f, "Gap down on high relative volume", "bearish",
        lambda ind, p: (gap(ind) <= -p["pct"]) & (ind.rel_volume(20) >= p["rvol"]),
        "gap down of at least pct with volume >= rvol x the previous 20-session mean", params=vprm, fields=OHLCV,
        warmup=21)


def _breakout(reg):
    f = fam(reg, "breakout", "Breakouts", "Closes beyond prior highs and lows, Donchian and range expansion.")
    for n, sid, nm in ((20, "breakout_20d", "20-day high breakout"), (50, "breakout_50d", "50-day high breakout"),
                       (252, "breakout_52w", "52-week high breakout")):
        sig(reg, sid, f, nm, "bullish", lambda ind, p: ind.close > ind.prior_highest(p["n"], "close"),
            "close above the highest close of the previous n sessions", params={"n": I(n, 5, 504)},
            warmup=lambda p: p["n"] + 1, show=lambda ind, p: overlay(PriorHigh=ind.prior_highest(p["n"], "close")))
    for n, sid, nm in ((20, "breakdown_20d", "20-day low breakdown"), (252, "breakdown_52w", "52-week low breakdown")):
        sig(reg, sid, f, nm, "bearish", lambda ind, p: ind.close < ind.prior_lowest(p["n"], "close"),
            "close below the lowest close of the previous n sessions", params={"n": I(n, 5, 504)},
            warmup=lambda p: p["n"] + 1, show=lambda ind, p: overlay(PriorLow=ind.prior_lowest(p["n"], "close")))
    sig(reg, "donchian_breakout", f, "Donchian channel breakout", "bullish",
        lambda ind, p: (ind.high > ind.prior_highest(p["n"], "high")) & (ind.close > ind.prior_highest(p["n"], "high")),
        "high and close above the highest high of the previous n sessions", params={"n": I(20, 5, 252)},
        fields=HLC, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(Upper=ind.prior_highest(p["n"], "high"), Lower=ind.prior_lowest(p["n"], "low")))
    sig(reg, "donchian_breakdown", f, "Donchian channel breakdown", "bearish",
        lambda ind, p: (ind.low < ind.prior_lowest(p["n"], "low")) & (ind.close < ind.prior_lowest(p["n"], "low")),
        "low and close below the lowest low of the previous n sessions", params={"n": I(20, 5, 252)},
        fields=HLC, warmup=lambda p: p["n"] + 1)
    sig(reg, "range_expansion_up", f, "Range expansion up", "bullish",
        lambda ind, p: (rng(ind) >= p["k"] * rng(ind).rolling(p["n"], min_periods=p["n"]).mean().shift(1))
        & ((ind.close - ind.low) >= 0.75 * rng(ind)),
        "range >= k x the mean range of the previous n sessions, close in the top quarter",
        params={"n": I(20, 5, 100), "k": F(2.0, 1.2, 5)}, fields=HLC, warmup=lambda p: p["n"] + 1)
    sig(reg, "breakout_50d_volume", f, "50-day breakout on volume", "bullish",
        lambda ind, p: (ind.close > ind.prior_highest(50, "close")) & (ind.rel_volume(20) >= p["rvol"]),
        "close above the prior 50-session closing high with volume >= rvol x its 20-session mean",
        params={"rvol": F(1.5, 1.0, 6)}, fields=("close", "volume"), warmup=51)


def _price_action(reg):
    f = fam(reg, "price_action", "Price action", "Bar relationships: engulfing, inside/outside bars, wicks, runs.")
    sig(reg, "bullish_engulfing", f, "Bullish engulfing", "bullish",
        lambda ind, p: bear(ind).shift(1, fill_value=False) & bull(ind) & (ind.open <= ind.close.shift(1))
        & (ind.close >= ind.open.shift(1)) & (body(ind) > body(ind).shift(1)),
        "t-1 down bar; t up bar whose body covers t-1's body", fields=OHLC, warmup=2)
    sig(reg, "bearish_engulfing", f, "Bearish engulfing", "bearish",
        lambda ind, p: bull(ind).shift(1, fill_value=False) & bear(ind) & (ind.open >= ind.close.shift(1))
        & (ind.close <= ind.open.shift(1)) & (body(ind) > body(ind).shift(1)),
        "t-1 up bar; t down bar whose body covers t-1's body", fields=OHLC, warmup=2)
    inside = lambda ind: (ind.high < ind.high.shift(1)) & (ind.low > ind.low.shift(1))
    sig(reg, "inside_bar_breakout_up", f, "Inside bar breakout up", "bullish",
        lambda ind, p: inside(ind).shift(1, fill_value=False) & (ind.close > ind.high.shift(1)),
        "t-1 inside t-2's range; t closes above t-1's high", fields=HLC, warmup=3)
    sig(reg, "inside_bar_breakout_down", f, "Inside bar breakout down", "bearish",
        lambda ind, p: inside(ind).shift(1, fill_value=False) & (ind.close < ind.low.shift(1)),
        "t-1 inside t-2's range; t closes below t-1's low", fields=HLC, warmup=3)
    outside = lambda ind: (ind.high > ind.high.shift(1)) & (ind.low < ind.low.shift(1))
    sig(reg, "outside_bar_up", f, "Outside bar closing up", "bullish",
        lambda ind, p: outside(ind) & ((ind.close - ind.low) >= 0.75 * rng(ind)) & (ind.close > ind.close.shift(1)),
        "t's range engulfs t-1's, close in the top quarter and above t-1's close", fields=HLC, warmup=2)
    sig(reg, "outside_bar_down", f, "Outside bar closing down", "bearish",
        lambda ind, p: outside(ind) & ((ind.high - ind.close) >= 0.75 * rng(ind)) & (ind.close < ind.close.shift(1)),
        "t's range engulfs t-1's, close in the bottom quarter and below t-1's close", fields=HLC, warmup=2)
    sig(reg, "long_lower_wick", f, "Long lower wick (rejection of lows)", "bullish",
        lambda ind, p: (lower_wick(ind) >= p["share"] * rng(ind)) & (ind.low <= ind.prior_lowest(10, "low")),
        "lower wick >= share of the range at a 10-session low", params={"share": F(0.6, 0.3, 0.95)}, fields=OHLC,
        warmup=11)
    sig(reg, "long_upper_wick", f, "Long upper wick (rejection of highs)", "bearish",
        lambda ind, p: (upper_wick(ind) >= p["share"] * rng(ind)) & (ind.high >= ind.prior_highest(10, "high")),
        "upper wick >= share of the range at a 10-session high", params={"share": F(0.6, 0.3, 0.95)}, fields=OHLC,
        warmup=11)
    up = lambda ind: ind.close > ind.close.shift(1)
    dn = lambda ind: ind.close < ind.close.shift(1)
    sig(reg, "consecutive_up_closes", f, "Run of higher closes", "bullish",
        lambda ind, p: rising_edge(up(ind).astype(float).rolling(p["n"], min_periods=p["n"]).sum() >= p["n"]),
        "n consecutive higher closes (first session of the run; continuation reading)",
        params={"n": I(5, 2, 12)}, warmup=lambda p: p["n"] + 1)
    sig(reg, "consecutive_down_closes", f, "Run of lower closes", "bearish",
        lambda ind, p: rising_edge(dn(ind).astype(float).rolling(p["n"], min_periods=p["n"]).sum() >= p["n"]),
        "n consecutive lower closes (first session of the run; continuation reading)",
        params={"n": I(5, 2, 12)}, warmup=lambda p: p["n"] + 1)


def _candlestick(reg):
    f = fam(reg, "candlestick", "Candlestick patterns", "Classic one- to three-bar patterns with a trend context "
                                                         "(previous close against its SMA10).")
    sig(reg, "hammer", f, "Hammer", "bullish",
        lambda ind, p: down_context(ind) & (body(ind) <= 0.35 * rng(ind)) & (lower_wick(ind) >= 2 * body(ind))
        & (upper_wick(ind) <= 0.15 * rng(ind)),
        "after a decline: small body, lower wick >= 2 bodies, upper wick <= 15% of the range", fields=OHLC, warmup=12)
    sig(reg, "shooting_star", f, "Shooting star", "bearish",
        lambda ind, p: up_context(ind) & (body(ind) <= 0.35 * rng(ind)) & (upper_wick(ind) >= 2 * body(ind))
        & (lower_wick(ind) <= 0.15 * rng(ind)),
        "after an advance: small body, upper wick >= 2 bodies, lower wick <= 15% of the range", fields=OHLC, warmup=12)
    sig(reg, "doji_after_decline", f, "Doji after a decline", "bullish",
        lambda ind, p: down_context(ind) & (body(ind) <= 0.1 * rng(ind)) & (ind.close < ind.close.shift(5)),
        "body <= 10% of the range after a decline (close below 5 sessions ago)", fields=OHLC, warmup=12)
    big = lambda ind: body(ind) >= 0.6 * rng(ind)
    sig(reg, "bullish_harami", f, "Bullish harami", "bullish",
        lambda ind, p: down_context(ind) & (bear(ind) & big(ind)).shift(1, fill_value=False) & bull(ind)
        & (ind.open > ind.close.shift(1)) & (ind.close < ind.open.shift(1)),
        "after a decline, a long down bar then an up bar whose body sits inside it", fields=OHLC, warmup=12)
    sig(reg, "bearish_harami", f, "Bearish harami", "bearish",
        lambda ind, p: up_context(ind) & (bull(ind) & big(ind)).shift(1, fill_value=False) & bear(ind)
        & (ind.open < ind.close.shift(1)) & (ind.close > ind.open.shift(1)),
        "after an advance, a long up bar then a down bar whose body sits inside it", fields=OHLC, warmup=12)
    mid1 = lambda ind: (ind.open.shift(1) + ind.close.shift(1)) / 2.0
    sig(reg, "piercing_line", f, "Piercing line", "bullish",
        lambda ind, p: down_context(ind) & (bear(ind) & big(ind)).shift(1, fill_value=False) & bull(ind)
        & (ind.open < ind.low.shift(1)) & (ind.close > mid1(ind)) & (ind.close < ind.open.shift(1)),
        "long down bar, then an open below its low and a close above its midpoint", fields=OHLC, warmup=12)
    sig(reg, "dark_cloud_cover", f, "Dark cloud cover", "bearish",
        lambda ind, p: up_context(ind) & (bull(ind) & big(ind)).shift(1, fill_value=False) & bear(ind)
        & (ind.open > ind.high.shift(1)) & (ind.close < mid1(ind)) & (ind.close > ind.open.shift(1)),
        "long up bar, then an open above its high and a close below its midpoint", fields=OHLC, warmup=12)
    small1 = lambda ind: (body(ind) <= 0.3 * body(ind).shift(1)).shift(1, fill_value=False)
    mid2 = lambda ind: (ind.open.shift(2) + ind.close.shift(2)) / 2.0
    sig(reg, "morning_star", f, "Morning star", "bullish",
        lambda ind, p: (bear(ind) & big(ind)).shift(2, fill_value=False) & small1(ind)
        & (ind.open.where(ind.open > ind.close, ind.close).shift(1) < ind.close.shift(2)) & bull(ind)
        & (ind.close > mid2(ind)),
        "long down bar, a small bar gapping below its close, then an up bar closing above the first bar's midpoint",
        fields=OHLC, warmup=4)
    sig(reg, "evening_star", f, "Evening star", "bearish",
        lambda ind, p: (bull(ind) & big(ind)).shift(2, fill_value=False) & small1(ind)
        & (ind.open.where(ind.open < ind.close, ind.close).shift(1) > ind.close.shift(2)) & bear(ind)
        & (ind.close < mid2(ind)),
        "long up bar, a small bar gapping above its close, then a down bar closing below the first bar's midpoint",
        fields=OHLC, warmup=4)
    soldier = lambda ind: bull(ind) & (ind.close > ind.close.shift(1)) & (ind.open > ind.open.shift(1)) \
        & (ind.open < ind.close.shift(1)) & (upper_wick(ind) <= 0.25 * rng(ind))
    crow = lambda ind: bear(ind) & (ind.close < ind.close.shift(1)) & (ind.open < ind.open.shift(1)) \
        & (ind.open > ind.close.shift(1)) & (lower_wick(ind) <= 0.25 * rng(ind))
    sig(reg, "three_white_soldiers", f, "Three white soldiers", "bullish",
        lambda ind, p: rising_edge(soldier(ind).astype(float).rolling(3, min_periods=3).sum() >= 3),
        "three up bars, each closing higher and opening inside the previous body, closing near the high",
        fields=OHLC, warmup=5)
    sig(reg, "three_black_crows", f, "Three black crows", "bearish",
        lambda ind, p: rising_edge(crow(ind).astype(float).rolling(3, min_periods=3).sum() >= 3),
        "three down bars, each closing lower and opening inside the previous body, closing near the low",
        fields=OHLC, warmup=5)
    sig(reg, "bullish_marubozu", f, "Bullish marubozu", "bullish",
        lambda ind, p: bull(ind) & (body(ind) >= 0.9 * rng(ind)) & (rng(ind) >= ind.atr(14).shift(1)),
        "up bar with body >= 90% of a range at least one ATR(14) wide", fields=OHLC, warmup=70)


def _support_resistance(reg):
    f = fam(reg, "support_resistance", "Support and resistance", "Tests, failures and retests of prior highs and "
                                                                 "lows, 52-week extremes and pivot levels.")
    prm = {"n": I(20, 5, 252)}
    sig(reg, "support_bounce", f, "Bounce off support", "bullish",
        lambda ind, p: (ind.low <= ind.prior_lowest(p["n"], "low") * 1.005) & (ind.close > ind.prior_lowest(p["n"], "low"))
        & (ind.close > ind.open),
        "low within 0.5% of (or below) the prior n-session low, close above that low and above the open",
        params=prm, fields=OHLC, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(Support=ind.prior_lowest(p["n"], "low")))
    sig(reg, "resistance_rejection", f, "Rejection at resistance", "bearish",
        lambda ind, p: (ind.high >= ind.prior_highest(p["n"], "high") * 0.995)
        & (ind.close < ind.prior_highest(p["n"], "high")) & (ind.close < ind.open),
        "high within 0.5% of (or above) the prior n-session high, close below it and below the open",
        params=prm, fields=OHLC, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(Resistance=ind.prior_highest(p["n"], "high")))
    sig(reg, "failed_breakdown", f, "Failed breakdown (spring)", "bullish",
        lambda ind, p: (ind.low < ind.prior_lowest(p["n"], "low")) & (ind.close > ind.prior_lowest(p["n"], "low")),
        "low below the prior n-session low, close back above it", params=prm, fields=HLC,
        warmup=lambda p: p["n"] + 1)
    sig(reg, "failed_breakout", f, "Failed breakout (upthrust)", "bearish",
        lambda ind, p: (ind.high > ind.prior_highest(p["n"], "high")) & (ind.close < ind.prior_highest(p["n"], "high")),
        "high above the prior n-session high, close back below it", params=prm, fields=HLC,
        warmup=lambda p: p["n"] + 1)
    lvl = lambda ind: ind.prior_highest(50, "close")
    broke = lambda ind: (ind.close > lvl(ind)).astype(float)
    sig(reg, "breakout_retest", f, "Breakout retest holds", "bullish",
        lambda ind, p: (broke(ind).shift(1).rolling(10, min_periods=1).max() > 0) & ~(ind.close > lvl(ind))
        & (ind.low <= ind.highest(60, "close").shift(11) * 1.01) & (ind.close >= ind.highest(60, "close").shift(11)),
        "a close above the prior 50-session closing high on one of the 10 sessions before t (not on t); with H = the "
        "highest close of the 60 sessions ending 11 sessions ago, t's low comes within 1% of H and t closes at or "
        "above H", fields=HLC, warmup=75)
    sig(reg, "near_52w_high", f, "Near the 52-week high", "bullish",
        lambda ind, p: rising_edge(ind.close >= (1 - p["pct"]) * ind.highest(252, "close")),
        "close within pct of the 252-session closing high (first session)", params={"pct": F(0.02, 0.0, 0.2)},
        warmup=253)
    sig(reg, "near_52w_low", f, "Near the 52-week low", "bearish",
        lambda ind, p: rising_edge(ind.close <= (1 + p["pct"]) * ind.lowest(252, "close")),
        "close within pct of the 252-session closing low (first session)", params={"pct": F(0.02, 0.0, 0.2)},
        warmup=253)
    pivot = lambda ind: ((ind.high + ind.low + ind.close) / 3.0).shift(1)
    sig(reg, "pivot_r1_breakout", f, "Close above pivot R1", "bullish",
        lambda ind, p: ind.close > 2 * pivot(ind) - ind.low.shift(1),
        "close above R1 = 2 x pivot - low, from the previous session's H, L, C", fields=HLC, warmup=2)
    sig(reg, "pivot_s1_breakdown", f, "Close below pivot S1", "bearish",
        lambda ind, p: ind.close < 2 * pivot(ind) - ind.high.shift(1),
        "close below S1 = 2 x pivot - high, from the previous session's H, L, C", fields=HLC, warmup=2)


def _mean_reversion(reg):
    f = fam(reg, "mean_reversion", "Mean reversion", "Short-term stretches away from an average or from the bar's "
                                                     "own range.")
    zprm = {"n": I(20, 5, 126), "z": F(2.0, 1.0, 4.0)}
    sig(reg, "zscore_below", f, "Close 2 sd below its 20-day mean", "bullish",
        lambda ind, p: ind.zscore(p["n"]) <= -p["z"], "(close - SMA(n)) / sd(n) <= -z", params=zprm,
        warmup=lambda p: p["n"])
    sig(reg, "zscore_above", f, "Close 2 sd above its 20-day mean", "bearish",
        lambda ind, p: ind.zscore(p["n"]) >= p["z"], "(close - SMA(n)) / sd(n) >= z", params=zprm,
        warmup=lambda p: p["n"])
    sig(reg, "three_down_days", f, "Three lower closes", "bullish",
        lambda ind, p: (ind.ret(1) < 0) & (ind.ret(1).shift(1) < 0) & (ind.ret(1).shift(2) < 0),
        "three consecutive lower closes (reversal reading)", warmup=4)
    sig(reg, "three_up_days", f, "Three higher closes", "bearish",
        lambda ind, p: (ind.ret(1) > 0) & (ind.ret(1).shift(1) > 0) & (ind.ret(1).shift(2) > 0),
        "three consecutive higher closes (reversal reading)", warmup=4)
    ibs = lambda ind: (ind.close - ind.low) / rng(ind)
    sig(reg, "ibs_low", f, "Close at the bottom of the bar", "bullish",
        lambda ind, p: ibs(ind) <= p["level"], "internal bar strength (close - low) / (high - low) <= level",
        params={"level": F(0.1, 0.0, 0.5)}, fields=HLC, warmup=1)
    sig(reg, "ibs_high", f, "Close at the top of the bar", "bearish",
        lambda ind, p: ibs(ind) >= p["level"], "internal bar strength >= level",
        params={"level": F(0.9, 0.5, 1.0)}, fields=HLC, warmup=1)
    sig(reg, "dip_in_uptrend", f, "Short dip in a long uptrend", "bullish",
        lambda ind, p: (ind.close > ind.sma(200)) & (ind.close / ind.sma(5) - 1 <= -p["pct"]),
        "close above SMA200 and at least pct below SMA5", params={"pct": F(0.03, 0.005, 0.2)}, warmup=201)
    sig(reg, "short_term_loser_decile", f, "5-day loser decile", "bullish",
        lambda ind, p: Indicators.xs_rank(ind.ret(p["n"])) <= 0.1,
        "n-session return in the bottom decile of the universe (short-term reversal reading)",
        params={"n": I(5, 1, 21)}, warmup=lambda p: p["n"] + 1, scope="cross_section")
    sig(reg, "short_term_winner_decile", f, "5-day winner decile", "bearish",
        lambda ind, p: Indicators.xs_rank(ind.ret(p["n"])) > 0.9,
        "n-session return in the top decile of the universe (short-term reversal reading)",
        params={"n": I(5, 1, 21)}, warmup=lambda p: p["n"] + 1, scope="cross_section")


def _trend_reversal(reg):
    f = fam(reg, "trend_reversal", "Trend reversals", "Key reversal days, higher lows / lower highs and first "
                                                      "crosses after a trend.")
    sig(reg, "key_reversal_up", f, "Key reversal up", "bullish",
        lambda ind, p: (ind.low < ind.prior_lowest(p["n"], "low")) & (ind.close > ind.high.shift(1)),
        "new n-session low, then a close above the previous high", params={"n": I(20, 5, 126)}, fields=HLC,
        warmup=lambda p: p["n"] + 1)
    sig(reg, "key_reversal_down", f, "Key reversal down", "bearish",
        lambda ind, p: (ind.high > ind.prior_highest(p["n"], "high")) & (ind.close < ind.low.shift(1)),
        "new n-session high, then a close below the previous low", params={"n": I(20, 5, 126)}, fields=HLC,
        warmup=lambda p: p["n"] + 1)
    low_recent = lambda ind: (ind.close <= ind.lowest(20, "close")).astype(float).shift(1).rolling(10, min_periods=1).max() > 0
    high_recent = lambda ind: (ind.close >= ind.highest(20, "close")).astype(float).shift(1).rolling(10, min_periods=1).max() > 0
    sig(reg, "higher_low_reversal", f, "Higher low, then a 10-day high", "bullish",
        lambda ind, p: low_recent(ind) & (ind.lowest(5, "low") > ind.lowest(20, "low")) & cross_above(ind.close, ind.prior_highest(10, "close")),
        "a 20-session closing low within the last 10 sessions, the last 5 lows above the 20-session low, and close "
        "clears the prior 10-session closing high", fields=HLC, warmup=31)
    sig(reg, "lower_high_reversal", f, "Lower high, then a 10-day low", "bearish",
        lambda ind, p: high_recent(ind) & (ind.highest(5, "high") < ind.highest(20, "high"))
        & (ind.close < ind.prior_lowest(10, "close")) & (ind.close.shift(1) >= ind.prior_lowest(10, "close").shift(1)),
        "a 20-session closing high within the last 10 sessions, the last 5 highs below the 20-session high, and "
        "close breaks the prior 10-session closing low", fields=HLC, warmup=31)
    sig(reg, "sma20_reclaim_after_downtrend", f, "First close above a falling SMA20", "bullish",
        lambda ind, p: cross_above(ind.close, ind.sma(20)) & (ind.sma(20) < ind.sma(20).shift(20)),
        "close crosses above SMA20 while SMA20 is below its level 20 sessions ago", warmup=41)
    sig(reg, "sma20_loss_after_uptrend", f, "First close below a rising SMA20", "bearish",
        lambda ind, p: (ind.close < ind.sma(20)) & (ind.close.shift(1) >= ind.sma(20).shift(1))
        & (ind.sma(20) > ind.sma(20).shift(20)),
        "close crosses below SMA20 while SMA20 is above its level 20 sessions ago", warmup=41)
    sig(reg, "two_bar_reversal_up", f, "Two-bar reversal up", "bullish",
        lambda ind, p: (ind.ret(1).shift(1) <= -p["pct"]) & (ind.close > ind.open.shift(1)),
        "t-1 fell at least pct; t closes above t-1's open", params={"pct": F(0.02, 0.005, 0.2)}, fields=OHLC,
        warmup=3)
    sig(reg, "two_bar_reversal_down", f, "Two-bar reversal down", "bearish",
        lambda ind, p: (ind.ret(1).shift(1) >= p["pct"]) & (ind.close < ind.open.shift(1)),
        "t-1 rose at least pct; t closes below t-1's open", params={"pct": F(0.02, 0.005, 0.2)}, fields=OHLC,
        warmup=3)


def _drawdown(reg):
    f = fam(reg, "drawdown", "Drawdowns", "Distance from the trailing high and recoveries.")
    dd = lambda ind, n: ind.close / ind.highest(n, "close") - 1.0
    sig(reg, "deep_drawdown", f, "Deep drawdown from the 52-week high", "bullish",
        lambda ind, p: rising_edge(dd(ind, 252) <= -p["pct"]),
        "close at least pct below its 252-session closing high (first session; value/reversal reading)",
        params={"pct": F(0.3, 0.05, 0.9)}, warmup=253)
    sig(reg, "drawdown_recovery", f, "Recovery to near the high", "bullish",
        lambda ind, p: rising_edge(dd(ind, 252) >= -0.05)
        & ((dd(ind, 252) <= -p["pct"]).astype(float).rolling(126, min_periods=1).max() > 0),
        "close back within 5% of the 252-session high after being at least pct below it in the last 126 sessions",
        params={"pct": F(0.2, 0.05, 0.6)}, warmup=253)
    sig(reg, "pullback_in_uptrend", f, "10% pullback in an uptrend", "bullish",
        lambda ind, p: rising_edge(dd(ind, 63) <= -p["pct"]) & (ind.close > ind.sma(200)),
        "close at least pct below its 63-session high while above SMA200 (first session)",
        params={"pct": F(0.1, 0.03, 0.4)}, warmup=201)
