"""Momentum and oscillator families: momentum, RSI, MACD, stochastic, Williams %R, CCI, TRIX and the ultimate
oscillator. Level conditions (e.g. RSI below 30) trigger on every qualifying session; the experiment's event
sampling (first trigger, cooldown, non-overlapping) decides which of them are events."""
from __future__ import annotations

from strategy_lab.signals.indicators import Indicators, cross_above, cross_below, rising_edge
from strategy_lab.signals.library._dsl import F, HLC, I, OHLC, fam, osc, sig


def register(reg):
    _momentum(reg)
    _rsi(reg)
    _macd(reg)
    _stochastic(reg)
    _williams(reg)
    _cci(reg)
    _trix(reg)
    _ultimate(reg)


def _momentum(reg):
    f = fam(reg, "momentum", "Momentum", "Past returns: cross-sectional deciles, rate of change, acceleration and "
                                          "divergence.")
    for n in (5, 10, 20):
        sig(reg, f"mom_{n}d_top_decile", f, f"{n}-day momentum, top decile", "bullish",
            lambda ind, p: Indicators.xs_rank(ind.ret(p["n"])) > 1 - p["q"],
            "n-session return in the top q of the universe on t", params={"n": I(n, 2, 252), "q": F(0.1, 0.02, 0.5)},
            warmup=lambda p: p["n"] + 1, scope="cross_section",
            doc="Cross-sectional: depends on the experiment's universe.",
            show=lambda ind, p: osc((), **{f"ret{p['n']}": ind.ret(p["n"])}))
    sig(reg, "mom_20d_bottom_decile", f, "20-day momentum, bottom decile", "bearish",
        lambda ind, p: Indicators.xs_rank(ind.ret(p["n"])) <= p["q"],
        "n-session return in the bottom q of the universe on t", params={"n": I(20, 2, 252), "q": F(0.1, 0.02, 0.5)},
        warmup=lambda p: p["n"] + 1, scope="cross_section")
    mom121 = lambda ind, p: ind.close.shift(p["skip"]) / ind.close.shift(p["lookback"]) - 1
    prm = {"lookback": I(252, 63, 504), "skip": I(21, 0, 63)}
    sig(reg, "mom_12_1_top_decile", f, "12-1 month momentum, top decile", "bullish",
        lambda ind, p: Indicators.xs_rank(mom121(ind, p)) > 0.9,
        "return from t-lookback to t-skip in the top decile of the universe", params=prm,
        warmup=lambda p: p["lookback"] + 1, scope="cross_section")
    sig(reg, "mom_12_1_bottom_decile", f, "12-1 month momentum, bottom decile", "bearish",
        lambda ind, p: Indicators.xs_rank(mom121(ind, p)) <= 0.1,
        "return from t-lookback to t-skip in the bottom decile of the universe", params=prm,
        warmup=lambda p: p["lookback"] + 1, scope="cross_section")
    show = lambda ind, p: osc((0,), ROC=ind.roc(p["n"]))
    sig(reg, "roc_cross_zero_up", f, "Rate of change turns positive", "bullish",
        lambda ind, p: cross_above(ind.roc(p["n"]), 0.0), "ROC(n) crosses above 0", params={"n": I(10, 2, 126)},
        warmup=lambda p: p["n"] + 2, show=show)
    sig(reg, "roc_cross_zero_down", f, "Rate of change turns negative", "bearish",
        lambda ind, p: cross_below(ind.roc(p["n"]), 0.0), "ROC(n) crosses below 0", params={"n": I(10, 2, 126)},
        warmup=lambda p: p["n"] + 2, show=show)
    sig(reg, "mom_acceleration", f, "Momentum acceleration", "bullish",
        lambda ind, p: rising_edge((ind.roc(p["n"]) > 0) & (ind.roc(p["n"]) - ind.roc(p["n"]).shift(p["n"]) >= p["pts"])),
        "ROC(n) > 0 and at least pts points above its value n sessions earlier (first session)",
        params={"n": I(10, 3, 63), "pts": F(5.0, 0.5, 50, "ROC points")}, warmup=lambda p: 2 * p["n"] + 2, show=show)
    div = lambda ind, p: (ind.close <= ind.prior_lowest(p["n"], "close")) & \
        (ind.roc(10) > ind.roc(10).shift(1).rolling(p["n"], min_periods=p["n"]).min() + p["pts"])
    sig(reg, "mom_bull_divergence", f, "Momentum bullish divergence", "bullish", div,
        "close makes an n-session closing low while ROC(10) is at least pts above its prior n-session low",
        params={"n": I(20, 5, 126), "pts": F(2.0, 0.0, 30)}, warmup=lambda p: p["n"] + 12)
    sig(reg, "mom_bear_divergence", f, "Momentum bearish divergence", "bearish",
        lambda ind, p: (ind.close >= ind.prior_highest(p["n"], "close"))
        & (ind.roc(10) < ind.roc(10).shift(1).rolling(p["n"], min_periods=p["n"]).max() - p["pts"]),
        "close makes an n-session closing high while ROC(10) is at least pts below its prior n-session high",
        params={"n": I(20, 5, 126), "pts": F(2.0, 0.0, 30)}, warmup=lambda p: p["n"] + 12)


def _rsi(reg):
    f = fam(reg, "rsi", "RSI", "Wilder's relative strength index: levels, crosses and divergence.")
    show = lambda ind, p: osc((30, 70), RSI=ind.rsi(p["n"]))
    w = lambda p: 5 * p["n"] + 1
    for lvl, d in ((20, "bullish"), (30, "bullish")):
        sig(reg, f"rsi14_below_{lvl}", f, f"RSI(14) below {lvl}", d, lambda ind, p: ind.rsi(p["n"]) < p["level"],
            "RSI(n) < level at the close of t", params={"n": I(14, 2, 50), "level": F(float(lvl), 1, 50)},
            warmup=w, show=show, doc="Oversold: the mean-reversion reading.")
    for lvl in (70, 80):
        sig(reg, f"rsi14_above_{lvl}", f, f"RSI(14) above {lvl}", "bearish", lambda ind, p: ind.rsi(p["n"]) > p["level"],
            "RSI(n) > level at the close of t", params={"n": I(14, 2, 50), "level": F(float(lvl), 50, 99)},
            warmup=w, show=show, doc="Overbought: the mean-reversion reading.")
    sig(reg, "rsi14_cross_above_30", f, "RSI(14) crosses above 30", "bullish",
        lambda ind, p: cross_above(ind.rsi(p["n"]), p["level"]), "RSI(n) crosses above level",
        params={"n": I(14, 2, 50), "level": F(30.0, 5, 50)}, warmup=w, show=show)
    sig(reg, "rsi14_cross_below_70", f, "RSI(14) crosses below 70", "bearish",
        lambda ind, p: cross_below(ind.rsi(p["n"]), p["level"]), "RSI(n) crosses below level",
        params={"n": I(14, 2, 50), "level": F(70.0, 50, 95)}, warmup=w, show=show)
    sig(reg, "rsi14_cross_above_50", f, "RSI(14) crosses above 50", "bullish",
        lambda ind, p: cross_above(ind.rsi(p["n"]), 50.0), "RSI(n) crosses above 50 (momentum reading)",
        params={"n": I(14, 2, 50)}, warmup=w, show=show)
    sig(reg, "rsi2_below_10", f, "RSI(2) below 10", "bullish", lambda ind, p: ind.rsi(p["n"]) < p["level"],
        "RSI(n) < level (short-term oversold)", params={"n": I(2, 2, 10), "level": F(10.0, 1, 40)},
        warmup=lambda p: 10 * p["n"] + 5, show=show)
    sig(reg, "rsi2_above_90", f, "RSI(2) above 90", "bearish", lambda ind, p: ind.rsi(p["n"]) > p["level"],
        "RSI(n) > level (short-term overbought)", params={"n": I(2, 2, 10), "level": F(90.0, 60, 99)},
        warmup=lambda p: 10 * p["n"] + 5, show=show)
    sig(reg, "rsi_bull_divergence", f, "RSI bullish divergence", "bullish",
        lambda ind, p: (ind.close <= ind.prior_lowest(p["lookback"], "close"))
        & (ind.rsi(p["n"]) > ind.rsi(p["n"]).shift(1).rolling(p["lookback"], min_periods=p["lookback"]).min() + p["pts"]),
        "close makes a lookback-session closing low while RSI(n) is at least pts above its prior lookback-session low",
        params={"n": I(14, 2, 50), "lookback": I(20, 5, 126), "pts": F(5.0, 0, 40)},
        warmup=lambda p: 5 * p["n"] + p["lookback"] + 1, show=show)
    sig(reg, "rsi_bear_divergence", f, "RSI bearish divergence", "bearish",
        lambda ind, p: (ind.close >= ind.prior_highest(p["lookback"], "close"))
        & (ind.rsi(p["n"]) < ind.rsi(p["n"]).shift(1).rolling(p["lookback"], min_periods=p["lookback"]).max() - p["pts"]),
        "close makes a lookback-session closing high while RSI(n) is at least pts below its prior lookback-session high",
        params={"n": I(14, 2, 50), "lookback": I(20, 5, 126), "pts": F(5.0, 0, 40)},
        warmup=lambda p: 5 * p["n"] + p["lookback"] + 1, show=show)


def _macd(reg):
    f = fam(reg, "macd", "MACD", "Moving average convergence/divergence: signal-line and zero-line crosses, "
                                  "histogram turns, divergence.")
    prm = {"fast": I(12, 2, 50), "slow": I(26, 5, 200), "signal": I(9, 2, 50)}
    m = lambda ind, p: ind.macd(p["fast"], p["slow"], p["signal"])
    w = lambda p: 4 * p["slow"] + p["signal"]
    show = lambda ind, p: osc((0,), MACD=m(ind, p)[0], Signal=m(ind, p)[1], Hist=m(ind, p)[2])
    sig(reg, "macd_signal_cross_up", f, "MACD crosses above its signal", "bullish",
        lambda ind, p: cross_above(m(ind, p)[0], m(ind, p)[1]), "MACD line crosses above the signal line",
        params=prm, warmup=w, show=show)
    sig(reg, "macd_signal_cross_down", f, "MACD crosses below its signal", "bearish",
        lambda ind, p: cross_below(m(ind, p)[0], m(ind, p)[1]), "MACD line crosses below the signal line",
        params=prm, warmup=w, show=show)
    sig(reg, "macd_zero_cross_up", f, "MACD crosses above zero", "bullish",
        lambda ind, p: cross_above(m(ind, p)[0], 0.0), "MACD line crosses above 0", params=prm, warmup=w, show=show)
    sig(reg, "macd_zero_cross_down", f, "MACD crosses below zero", "bearish",
        lambda ind, p: cross_below(m(ind, p)[0], 0.0), "MACD line crosses below 0", params=prm, warmup=w, show=show)
    sig(reg, "macd_hist_turn_up", f, "MACD histogram turns up below zero", "bullish",
        lambda ind, p: (m(ind, p)[2] < 0) & (m(ind, p)[2] > m(ind, p)[2].shift(1))
        & (m(ind, p)[2].shift(1) < m(ind, p)[2].shift(2)),
        "histogram < 0, rises on t after falling on t-1", params=prm, warmup=w, show=show)
    sig(reg, "macd_hist_turn_down", f, "MACD histogram turns down above zero", "bearish",
        lambda ind, p: (m(ind, p)[2] > 0) & (m(ind, p)[2] < m(ind, p)[2].shift(1))
        & (m(ind, p)[2].shift(1) > m(ind, p)[2].shift(2)),
        "histogram > 0, falls on t after rising on t-1", params=prm, warmup=w, show=show)
    sig(reg, "macd_bull_divergence", f, "MACD bullish divergence", "bullish",
        lambda ind, p: (ind.close <= ind.prior_lowest(p["lookback"], "close"))
        & (m(ind, p)[0] > m(ind, p)[0].shift(1).rolling(p["lookback"], min_periods=p["lookback"]).min()),
        "close makes a lookback-session closing low while the MACD line is above its prior lookback-session low",
        params={**prm, "lookback": I(20, 5, 126)}, warmup=lambda p: 4 * p["slow"] + p["lookback"], show=show)


def _stochastic(reg):
    f = fam(reg, "stochastic", "Stochastic oscillator", "Slow stochastic %K / %D levels and crosses.")
    prm = {"k": I(14, 3, 50), "smooth": I(3, 1, 10), "d": I(3, 1, 10)}
    s = lambda ind, p: ind.stoch(p["k"], p["smooth"], p["d"])
    w = lambda p: p["k"] + p["smooth"] + p["d"]
    show = lambda ind, p: osc((20, 80), K=s(ind, p)[0], D=s(ind, p)[1])
    sig(reg, "stoch_oversold", f, "Stochastic below 20", "bullish", lambda ind, p: s(ind, p)[0] < p["level"],
        "%K < level", params={**prm, "level": F(20.0, 1, 50)}, fields=HLC, warmup=w, show=show)
    sig(reg, "stoch_overbought", f, "Stochastic above 80", "bearish", lambda ind, p: s(ind, p)[0] > p["level"],
        "%K > level", params={**prm, "level": F(80.0, 50, 99)}, fields=HLC, warmup=w, show=show)
    sig(reg, "stoch_bull_cross_oversold", f, "Stochastic bullish cross in oversold zone", "bullish",
        lambda ind, p: cross_above(s(ind, p)[0], s(ind, p)[1]) & (s(ind, p)[1] < 20),
        "%K crosses above %D while %D < 20", params=prm, fields=HLC, warmup=w, show=show)
    sig(reg, "stoch_bear_cross_overbought", f, "Stochastic bearish cross in overbought zone", "bearish",
        lambda ind, p: cross_below(s(ind, p)[0], s(ind, p)[1]) & (s(ind, p)[1] > 80),
        "%K crosses below %D while %D > 80", params=prm, fields=HLC, warmup=w, show=show)


def _williams(reg):
    f = fam(reg, "williams_r", "Williams %R", "Close relative to the n-session high-low range.")
    prm = {"n": I(14, 3, 100)}
    show = lambda ind, p: osc((-80, -20), WR=ind.willr(p["n"]))
    sig(reg, "willr_oversold", f, "Williams %R below -80", "bullish", lambda ind, p: ind.willr(p["n"]) < -80,
        "%R(n) < -80", params=prm, fields=HLC, warmup=lambda p: p["n"], show=show)
    sig(reg, "willr_overbought", f, "Williams %R above -20", "bearish", lambda ind, p: ind.willr(p["n"]) > -20,
        "%R(n) > -20", params=prm, fields=HLC, warmup=lambda p: p["n"], show=show)
    sig(reg, "willr_exit_oversold", f, "Williams %R leaves oversold", "bullish",
        lambda ind, p: cross_above(ind.willr(p["n"]), -80.0), "%R(n) crosses above -80", params=prm, fields=HLC,
        warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "willr_exit_overbought", f, "Williams %R leaves overbought", "bearish",
        lambda ind, p: cross_below(ind.willr(p["n"]), -20.0), "%R(n) crosses below -20", params=prm, fields=HLC,
        warmup=lambda p: p["n"] + 1, show=show)


def _cci(reg):
    f = fam(reg, "cci", "Commodity channel index", "Typical price against its mean deviation.")
    prm = {"n": I(20, 5, 100)}
    show = lambda ind, p: osc((-100, 100), CCI=ind.cci(p["n"]))
    sig(reg, "cci_oversold_reversal", f, "CCI crosses back above -100", "bullish",
        lambda ind, p: cross_above(ind.cci(p["n"]), -100.0), "CCI(n) crosses above -100", params=prm, fields=HLC,
        warmup=lambda p: 2 * p["n"], show=show)
    sig(reg, "cci_overbought_reversal", f, "CCI crosses back below +100", "bearish",
        lambda ind, p: cross_below(ind.cci(p["n"]), 100.0), "CCI(n) crosses below +100", params=prm, fields=HLC,
        warmup=lambda p: 2 * p["n"], show=show)
    sig(reg, "cci_breakout", f, "CCI breaks above +100", "bullish",
        lambda ind, p: cross_above(ind.cci(p["n"]), 100.0), "CCI(n) crosses above +100 (trend-following reading)",
        params=prm, fields=HLC, warmup=lambda p: 2 * p["n"], show=show)
    sig(reg, "cci_breakdown", f, "CCI breaks below -100", "bearish",
        lambda ind, p: cross_below(ind.cci(p["n"]), -100.0), "CCI(n) crosses below -100 (trend-following reading)",
        params=prm, fields=HLC, warmup=lambda p: 2 * p["n"], show=show)


def _trix(reg):
    f = fam(reg, "trix", "TRIX", "Rate of change of a triple-smoothed EMA.")
    prm = {"n": I(15, 3, 60), "signal": I(9, 2, 30)}
    sl = lambda ind, p: ind.trix(p["n"]).ewm(span=p["signal"], adjust=False, min_periods=p["signal"]).mean()
    show = lambda ind, p: osc((0,), TRIX=ind.trix(p["n"]), Signal=sl(ind, p))
    w = lambda p: 10 * p["n"]
    sig(reg, "trix_zero_cross_up", f, "TRIX crosses above zero", "bullish",
        lambda ind, p: cross_above(ind.trix(p["n"]), 0.0), "TRIX(n) crosses above 0", params=prm, warmup=w, show=show)
    sig(reg, "trix_zero_cross_down", f, "TRIX crosses below zero", "bearish",
        lambda ind, p: cross_below(ind.trix(p["n"]), 0.0), "TRIX(n) crosses below 0", params=prm, warmup=w, show=show)
    sig(reg, "trix_signal_cross_up", f, "TRIX crosses above its signal", "bullish",
        lambda ind, p: cross_above(ind.trix(p["n"]), sl(ind, p)), "TRIX crosses above EMA(signal) of TRIX",
        params=prm, warmup=w, show=show)
    sig(reg, "trix_signal_cross_down", f, "TRIX crosses below its signal", "bearish",
        lambda ind, p: cross_below(ind.trix(p["n"]), sl(ind, p)), "TRIX crosses below EMA(signal) of TRIX",
        params=prm, warmup=w, show=show)


def _ultimate(reg):
    f = fam(reg, "ultimate_oscillator", "Ultimate oscillator", "Buying pressure over three windows (7/14/28).")
    show = lambda ind, p: osc((30, 70), UO=ind.ultimate())
    sig(reg, "uo_oversold", f, "Ultimate oscillator below 30", "bullish", lambda ind, p: ind.ultimate() < p["level"],
        "UO < level", params={"level": F(30.0, 5, 50)}, fields=HLC, warmup=30, show=show)
    sig(reg, "uo_overbought", f, "Ultimate oscillator above 70", "bearish", lambda ind, p: ind.ultimate() > p["level"],
        "UO > level", params={"level": F(70.0, 50, 95)}, fields=HLC, warmup=30, show=show)
    sig(reg, "uo_cross_above_30", f, "Ultimate oscillator leaves oversold", "bullish",
        lambda ind, p: cross_above(ind.ultimate(), 30.0), "UO crosses above 30", fields=HLC, warmup=31, show=show)
    sig(reg, "uo_cross_below_70", f, "Ultimate oscillator leaves overbought", "bearish",
        lambda ind, p: cross_below(ind.ultimate(), 70.0), "UO crosses below 70", fields=HLC, warmup=31, show=show)


__all__ = ["register", "OHLC"]
