"""Trend families: moving averages, trend strength (ADX/DMI), Aroon, parabolic SAR, Ichimoku, linear regression,
Heikin-Ashi and the vortex indicator. Directions state the conventional reading being tested."""
from __future__ import annotations

import numpy as np

from strategy_lab.signals.indicators import cross_above, cross_below, rising_edge
from strategy_lab.signals.library._dsl import F, HLC, I, OHLC, fam, osc, overlay, sig


def register(reg):
    _moving_average(reg)
    _trend_strength(reg)
    _aroon(reg)
    _psar(reg)
    _ichimoku(reg)
    _linreg(reg)
    _heikin_ashi(reg)
    _vortex(reg)


def _moving_average(reg):
    f = fam(reg, "moving_average", "Moving averages",
            "Crossovers of moving averages, price crossing a moving average, and distance from one.")

    def sma_cross(fast, slow, up):
        def fn(ind, p):
            a, b = ind.sma(p["fast"]), ind.sma(p["slow"])
            return cross_above(a, b) if up else cross_below(a, b)
        return fn

    def sma_show(ind, p):
        return overlay(**{f"SMA{p['fast']}": ind.sma(p["fast"]), f"SMA{p['slow']}": ind.sma(p["slow"])})

    for fast, slow, sid_up, sid_dn, nm in ((10, 20, "sma_10_20_cross_up", "sma_10_20_cross_down", "10/20"),
                                           (20, 50, "sma_20_50_cross_up", "sma_20_50_cross_down", "20/50"),
                                           (50, 200, "golden_cross_50_200", "death_cross_50_200", "50/200")):
        prm = {"fast": I(fast, 2, 250, "fast SMA length"), "slow": I(slow, 3, 400, "slow SMA length")}
        sig(reg, sid_up, f, f"SMA {nm} cross up" if slow != 200 else "Golden cross (SMA 50/200)", "bullish",
            sma_cross(fast, slow, True), "SMA(fast) crosses above SMA(slow) at the close of t",
            params=prm, warmup=lambda p: p["slow"] + 1, show=sma_show)
        sig(reg, sid_dn, f, f"SMA {nm} cross down" if slow != 200 else "Death cross (SMA 50/200)", "bearish",
            sma_cross(fast, slow, False), "SMA(fast) crosses below SMA(slow) at the close of t",
            params=prm, warmup=lambda p: p["slow"] + 1, show=sma_show)

    prm = {"fast": I(9, 2, 100), "slow": I(21, 3, 250)}
    ema_show = lambda ind, p: overlay(**{f"EMA{p['fast']}": ind.ema(p["fast"]), f"EMA{p['slow']}": ind.ema(p["slow"])})
    sig(reg, "ema_9_21_cross_up", f, "EMA 9/21 cross up", "bullish",
        lambda ind, p: cross_above(ind.ema(p["fast"]), ind.ema(p["slow"])),
        "EMA(fast) crosses above EMA(slow)", params=prm, warmup=lambda p: 4 * p["slow"], show=ema_show)
    sig(reg, "ema_9_21_cross_down", f, "EMA 9/21 cross down", "bearish",
        lambda ind, p: cross_below(ind.ema(p["fast"]), ind.ema(p["slow"])),
        "EMA(fast) crosses below EMA(slow)", params=prm, warmup=lambda p: 4 * p["slow"], show=ema_show)

    sig(reg, "ema20_reclaim", f, "Price reclaims the 20-day EMA", "bullish",
        lambda ind, p: cross_above(ind.close, ind.ema(p["n"])), "close crosses above EMA(n)",
        params={"n": I(20, 5, 200)}, warmup=lambda p: 4 * p["n"],
        show=lambda ind, p: overlay(**{f"EMA{p['n']}": ind.ema(p["n"])}))
    sig(reg, "sma50_break_below", f, "Price breaks below the 50-day SMA", "bearish",
        lambda ind, p: cross_below(ind.close, ind.sma(p["n"])), "close crosses below SMA(n)",
        params={"n": I(50, 5, 300)}, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(**{f"SMA{p['n']}": ind.sma(p["n"])}))
    sig(reg, "sma200_reclaim", f, "Price reclaims the 200-day SMA", "bullish",
        lambda ind, p: cross_above(ind.close, ind.sma(p["n"])), "close crosses above SMA(n)",
        params={"n": I(200, 50, 400)}, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(**{f"SMA{p['n']}": ind.sma(p["n"])}))
    sig(reg, "sma200_break_below", f, "Price breaks below the 200-day SMA", "bearish",
        lambda ind, p: cross_below(ind.close, ind.sma(p["n"])), "close crosses below SMA(n)",
        params={"n": I(200, 50, 400)}, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: overlay(**{f"SMA{p['n']}": ind.sma(p["n"])}))
    sig(reg, "dist_sma50_far_below", f, "Far below the 50-day SMA", "bullish",
        lambda ind, p: rising_edge(ind.close / ind.sma(p["n"]) - 1 <= -p["pct"]),
        "close / SMA(n) - 1 falls to -pct or lower (first session)", doc="Mean-reversion reading of distance.",
        params={"n": I(50, 10, 300), "pct": F(0.15, 0.02, 0.6, "distance below the SMA")},
        warmup=lambda p: p["n"] + 1, show=lambda ind, p: overlay(**{f"SMA{p['n']}": ind.sma(p["n"])}))
    sig(reg, "dist_sma50_far_above", f, "Far above the 50-day SMA", "bearish",
        lambda ind, p: rising_edge(ind.close / ind.sma(p["n"]) - 1 >= p["pct"]),
        "close / SMA(n) - 1 rises to +pct or more (first session)", doc="Mean-reversion (overextension) reading.",
        params={"n": I(50, 10, 300), "pct": F(0.15, 0.02, 1.0)},
        warmup=lambda p: p["n"] + 1, show=lambda ind, p: overlay(**{f"SMA{p['n']}": ind.sma(p["n"])}))
    sig(reg, "ma_stack_bullish", f, "Moving averages stacked bullish", "bullish",
        lambda ind, p: rising_edge((ind.close > ind.sma(20)) & (ind.sma(20) > ind.sma(50)) & (ind.sma(50) > ind.sma(200))),
        "close > SMA20 > SMA50 > SMA200 (first session of the alignment)", warmup=201,
        show=lambda ind, p: overlay(SMA20=ind.sma(20), SMA50=ind.sma(50), SMA200=ind.sma(200)))
    sig(reg, "pullback_to_rising_sma50", f, "Pullback to a rising 50-day SMA", "bullish",
        lambda ind, p: (ind.low <= ind.sma(50)) & (ind.close > ind.sma(50)) & (ind.sma(50) > ind.sma(50).shift(10))
        & (ind.close > ind.sma(200)),
        "low touches SMA50, close holds above it, SMA50 higher than 10 sessions ago, close above SMA200",
        fields=HLC, warmup=211, show=lambda ind, p: overlay(SMA50=ind.sma(50), SMA200=ind.sma(200)))


def _trend_strength(reg):
    f = fam(reg, "trend_strength", "Trend strength (ADX / DMI)", "Wilder's directional movement system.")
    prm = {"n": I(14, 5, 50), "level": F(25.0, 10, 50, "ADX level")}
    show = lambda ind, p: osc((p.get("level", 25),), ADX=ind.dmi(p["n"])["adx"], **{"+DI": ind.dmi(p["n"])["pdi"],
                                                                                      "-DI": ind.dmi(p["n"])["mdi"]})
    w = lambda p: 6 * p["n"]
    sig(reg, "adx_trend_up", f, "ADX confirms an uptrend", "bullish",
        lambda ind, p: cross_above(ind.dmi(p["n"])["adx"], p["level"]) & (ind.dmi(p["n"])["pdi"] > ind.dmi(p["n"])["mdi"]),
        "ADX crosses above level while +DI > -DI", params=prm, fields=HLC, warmup=w, show=show)
    sig(reg, "adx_trend_down", f, "ADX confirms a downtrend", "bearish",
        lambda ind, p: cross_above(ind.dmi(p["n"])["adx"], p["level"]) & (ind.dmi(p["n"])["pdi"] < ind.dmi(p["n"])["mdi"]),
        "ADX crosses above level while -DI > +DI", params=prm, fields=HLC, warmup=w, show=show)
    prm2 = {"n": I(14, 5, 50)}
    sig(reg, "di_cross_up", f, "+DI crosses above -DI", "bullish",
        lambda ind, p: cross_above(ind.dmi(p["n"])["pdi"], ind.dmi(p["n"])["mdi"]),
        "+DI crosses above -DI", params=prm2, fields=HLC, warmup=w, show=show)
    sig(reg, "di_cross_down", f, "-DI crosses above +DI", "bearish",
        lambda ind, p: cross_below(ind.dmi(p["n"])["pdi"], ind.dmi(p["n"])["mdi"]),
        "+DI crosses below -DI", params=prm2, fields=HLC, warmup=w, show=show)
    sig(reg, "adx_emerging_uptrend", f, "Trend emerging from a range", "bullish",
        lambda ind, p: (ind.dmi(p["n"])["adx"].shift(10) < 20) & cross_above(ind.dmi(p["n"])["adx"], 20)
        & (ind.dmi(p["n"])["pdi"] > ind.dmi(p["n"])["mdi"]),
        "ADX was below 20 ten sessions ago and crosses above 20 with +DI > -DI", params=prm2, fields=HLC,
        warmup=lambda p: 6 * p["n"] + 10, show=show)


def _aroon(reg):
    f = fam(reg, "aroon", "Aroon", "Time since the latest high and low over a lookback.")
    prm = {"n": I(25, 5, 100)}
    show = lambda ind, p: osc((30, 70), AroonUp=ind.aroon(p["n"])[0], AroonDown=ind.aroon(p["n"])[1])
    sig(reg, "aroon_cross_up", f, "Aroon up crosses above Aroon down", "bullish",
        lambda ind, p: cross_above(ind.aroon(p["n"])[0], ind.aroon(p["n"])[1]), "Aroon up crosses above Aroon down",
        params=prm, fields=HLC, warmup=lambda p: p["n"] + 2, show=show)
    sig(reg, "aroon_cross_down", f, "Aroon down crosses above Aroon up", "bearish",
        lambda ind, p: cross_below(ind.aroon(p["n"])[0], ind.aroon(p["n"])[1]), "Aroon up crosses below Aroon down",
        params=prm, fields=HLC, warmup=lambda p: p["n"] + 2, show=show)
    sig(reg, "aroon_up_strong", f, "Aroon up at 100, down weak", "bullish",
        lambda ind, p: rising_edge((ind.aroon(p["n"])[0] >= 100) & (ind.aroon(p["n"])[1] <= 30)),
        "Aroon up = 100 (new n-session high) while Aroon down <= 30 (first session)", params=prm, fields=HLC,
        warmup=lambda p: p["n"] + 2, show=show)
    sig(reg, "aroon_down_strong", f, "Aroon down at 100, up weak", "bearish",
        lambda ind, p: rising_edge((ind.aroon(p["n"])[1] >= 100) & (ind.aroon(p["n"])[0] <= 30)),
        "Aroon down = 100 (new n-session low) while Aroon up <= 30 (first session)", params=prm, fields=HLC,
        warmup=lambda p: p["n"] + 2, show=show)


def _psar(reg):
    f = fam(reg, "parabolic_sar", "Parabolic SAR", "Wilder's stop-and-reverse trailing level.")
    prm = {"af": F(0.02, 0.005, 0.1, "acceleration step"), "af_max": F(0.2, 0.05, 0.5)}
    tr = lambda ind, p: ind.psar(p["af"], p["af"], p["af_max"])[1]
    show = lambda ind, p: overlay(SAR=ind.psar(p["af"], p["af"], p["af_max"])[0])
    sig(reg, "psar_flip_up", f, "Parabolic SAR flips to long", "bullish",
        lambda ind, p: (tr(ind, p) > 0) & (tr(ind, p).shift(1) < 0), "SAR trend turns from short to long on t",
        params=prm, fields=HLC, warmup=30, show=show)
    sig(reg, "psar_flip_down", f, "Parabolic SAR flips to short", "bearish",
        lambda ind, p: (tr(ind, p) < 0) & (tr(ind, p).shift(1) > 0), "SAR trend turns from long to short on t",
        params=prm, fields=HLC, warmup=30, show=show)
    sig(reg, "psar_flip_up_above_sma200", f, "SAR flips long above the 200-day SMA", "bullish",
        lambda ind, p: (tr(ind, p) > 0) & (tr(ind, p).shift(1) < 0) & (ind.close > ind.sma(200)),
        "SAR turns long while close > SMA200", params=prm, fields=HLC, warmup=201, show=show)


def _ichimoku(reg):
    f = fam(reg, "ichimoku", "Ichimoku", "Tenkan/kijun crosses and the cloud (spans as known at t).")
    show = lambda ind, p: overlay(Tenkan=ind.ichimoku()["tenkan"], Kijun=ind.ichimoku()["kijun"],
                                  CloudTop=ind.ichimoku()["cloud_top"], CloudBottom=ind.ichimoku()["cloud_bottom"])
    sig(reg, "ichimoku_tk_cross_up", f, "Tenkan crosses above kijun", "bullish",
        lambda ind, p: cross_above(ind.ichimoku()["tenkan"], ind.ichimoku()["kijun"]),
        "tenkan (9) crosses above kijun (26)", fields=HLC, warmup=80, show=show)
    sig(reg, "ichimoku_tk_cross_down", f, "Tenkan crosses below kijun", "bearish",
        lambda ind, p: cross_below(ind.ichimoku()["tenkan"], ind.ichimoku()["kijun"]),
        "tenkan (9) crosses below kijun (26)", fields=HLC, warmup=80, show=show)
    sig(reg, "ichimoku_cloud_breakout", f, "Close breaks above the cloud", "bullish",
        lambda ind, p: cross_above(ind.close, ind.ichimoku()["cloud_top"]), "close crosses above the cloud top",
        fields=HLC, warmup=80, show=show)
    sig(reg, "ichimoku_cloud_breakdown", f, "Close breaks below the cloud", "bearish",
        lambda ind, p: cross_below(ind.close, ind.ichimoku()["cloud_bottom"]), "close crosses below the cloud bottom",
        fields=HLC, warmup=80, show=show)


def _linreg(reg):
    f = fam(reg, "linear_regression", "Linear regression trend", "Slope and fit quality of a least-squares line "
                                                                   "through log closes.")
    prm = {"n": I(63, 10, 252), "r2": F(0.8, 0.3, 0.99, "minimum r^2")}
    show = lambda ind, p: osc((), slope_bps=ind.linreg(p["n"])["slope"] * 1e4, r2=ind.linreg(p["n"])["r2"])
    sig(reg, "linreg_strong_uptrend", f, "Strong, orderly uptrend", "bullish",
        lambda ind, p: rising_edge((ind.linreg(p["n"])["slope"] > 0) & (ind.linreg(p["n"])["r2"] >= p["r2"])),
        "slope of log close over n sessions > 0 with r^2 >= r2 (first session)", params=prm,
        warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "linreg_strong_downtrend", f, "Strong, orderly downtrend", "bearish",
        lambda ind, p: rising_edge((ind.linreg(p["n"])["slope"] < 0) & (ind.linreg(p["n"])["r2"] >= p["r2"])),
        "slope < 0 with r^2 >= r2 (first session)", params=prm, warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "linreg_channel_lower_touch", f, "Lower regression channel in an uptrend", "bullish",
        lambda ind, p: (ind.linreg(p["n"])["slope"] > 0)
        & (ind.linreg(p["n"])["fit"] - 2 * ind.linreg(p["n"])["resid_sd"] > np.log(ind.close)),
        "uptrend (slope > 0) and log close more than 2 residual sd below the fitted line",
        params={"n": I(63, 20, 252)}, warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "linreg_slope_turns_up", f, "Short regression slope turns positive", "bullish",
        lambda ind, p: cross_above(ind.linreg(p["n"])["slope"], 0.0), "slope over n sessions crosses above 0",
        params={"n": I(20, 5, 126)}, warmup=lambda p: p["n"] + 2, show=show)


def _heikin_ashi(reg):
    f = fam(reg, "heikin_ashi", "Heikin-Ashi", "Smoothed candles: colour changes and shadowless trend candles.")
    bull = lambda ind: ind.heikin_ashi()[1] > ind.heikin_ashi()[0]
    bear = lambda ind: ind.heikin_ashi()[1] < ind.heikin_ashi()[0]
    sig(reg, "ha_flip_up", f, "Heikin-Ashi turns bullish", "bullish",
        lambda ind, p: bull(ind) & bear(ind).shift(1, fill_value=False) & bear(ind).shift(2, fill_value=False)
        & bear(ind).shift(3, fill_value=False),
        "a bullish HA candle after three bearish ones", fields=OHLC, warmup=20)
    sig(reg, "ha_flip_down", f, "Heikin-Ashi turns bearish", "bearish",
        lambda ind, p: bear(ind) & bull(ind).shift(1, fill_value=False) & bull(ind).shift(2, fill_value=False)
        & bull(ind).shift(3, fill_value=False),
        "a bearish HA candle after three bullish ones", fields=OHLC, warmup=20)
    sig(reg, "ha_strong_bull", f, "Shadowless bullish Heikin-Ashi", "bullish",
        lambda ind, p: rising_edge(bull(ind) & (ind.low >= ind.heikin_ashi()[0])),
        "bullish HA candle with no lower shadow (low >= HA open), first session", fields=OHLC, warmup=20)


def _vortex(reg):
    f = fam(reg, "vortex", "Vortex", "Positive and negative trend movement (VI+ / VI-).")
    prm = {"n": I(14, 5, 50)}
    show = lambda ind, p: osc((1.0,), **{"VI+": ind.vortex(p["n"])[0], "VI-": ind.vortex(p["n"])[1]})
    sig(reg, "vortex_cross_up", f, "VI+ crosses above VI-", "bullish",
        lambda ind, p: cross_above(ind.vortex(p["n"])[0], ind.vortex(p["n"])[1]), "VI+ crosses above VI-",
        params=prm, fields=HLC, warmup=lambda p: p["n"] + 2, show=show)
    sig(reg, "vortex_cross_down", f, "VI- crosses above VI+", "bearish",
        lambda ind, p: cross_below(ind.vortex(p["n"])[0], ind.vortex(p["n"])[1]), "VI+ crosses below VI-",
        params=prm, fields=HLC, warmup=lambda p: p["n"] + 2, show=show)
