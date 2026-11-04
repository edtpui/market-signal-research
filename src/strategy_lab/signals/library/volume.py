"""Volume families: relative volume, spikes, on-balance volume, price-volume divergence; money flow (MFI, CMF,
accumulation/distribution). Volume is the raw (split-adjusted) volume of the data source."""
from __future__ import annotations

from strategy_lab.signals.indicators import cross_above, cross_below, rising_edge
from strategy_lab.signals.library._dsl import F, I, OHLCV, fam, osc, sig

CV = ("close", "volume")
HLCV = ("high", "low", "close", "volume")


def register(reg):
    _volume(reg)
    _money_flow(reg)


def _volume(reg):
    f = fam(reg, "volume", "Volume", "Relative volume, volume spikes, OBV and price-volume divergence.")
    prm = {"n": I(20, 5, 126, "average window"), "rvol": F(2.0, 1.2, 10, "relative volume"),
           "move": F(0.01, 0.0, 0.2, "minimum close-to-close move")}
    show = lambda ind, p: osc((p.get("rvol", 2.0),), RelVolume=ind.rel_volume(p["n"]))
    sig(reg, "rvol_up_day", f, "High relative volume up day", "bullish",
        lambda ind, p: (ind.rel_volume(p["n"]) >= p["rvol"]) & (ind.ret(1) >= p["move"]),
        "volume >= rvol x mean volume of the previous n sessions and close up at least `move`", params=prm,
        fields=CV, warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "rvol_down_day", f, "High relative volume down day", "bearish",
        lambda ind, p: (ind.rel_volume(p["n"]) >= p["rvol"]) & (ind.ret(1) <= -p["move"]),
        "volume >= rvol x mean volume of the previous n sessions and close down at least `move`", params=prm,
        fields=CV, warmup=lambda p: p["n"] + 1, show=show)
    sig(reg, "capitulation_volume", f, "Volume spike at a 20-day low", "bullish",
        lambda ind, p: (ind.rel_volume(p["n"]) >= p["rvol"]) & (ind.close <= ind.prior_lowest(20, "close")),
        "volume >= rvol x average and close at a 20-session closing low (capitulation reading)",
        params={"n": I(20, 5, 126), "rvol": F(3.0, 1.5, 10)}, fields=CV, warmup=lambda p: max(p["n"], 20) + 1,
        show=show)
    obv_show = lambda ind, p: osc((), OBV=ind.obv(), OBV_SMA=ind.obv().rolling(p["n"], min_periods=p["n"]).mean())
    sig(reg, "obv_cross_up", f, "OBV crosses above its average", "bullish",
        lambda ind, p: cross_above(ind.obv(), ind.obv().rolling(p["n"], min_periods=p["n"]).mean()),
        "on-balance volume crosses above its SMA(n)", params={"n": I(20, 5, 126)}, fields=CV,
        warmup=lambda p: p["n"] + 1, show=obv_show)
    sig(reg, "obv_cross_down", f, "OBV crosses below its average", "bearish",
        lambda ind, p: cross_below(ind.obv(), ind.obv().rolling(p["n"], min_periods=p["n"]).mean()),
        "on-balance volume crosses below its SMA(n)", params={"n": I(20, 5, 126)}, fields=CV,
        warmup=lambda p: p["n"] + 1, show=obv_show)
    sig(reg, "obv_leads_price", f, "OBV new high before price", "bullish",
        lambda ind, p: rising_edge((ind.obv() >= ind.obv().rolling(p["n"], min_periods=p["n"]).max())
                                   & (ind.close < ind.highest(p["n"], "close"))),
        "OBV at its n-session high while the close is below its n-session high (first session)",
        params={"n": I(50, 10, 252)}, fields=CV, warmup=lambda p: p["n"] + 1, show=obv_show)
    sig(reg, "price_volume_divergence", f, "New high on fading volume", "bearish",
        lambda ind, p: (ind.close >= ind.prior_highest(p["n"], "close"))
        & (ind.volume.rolling(p["n"], min_periods=p["n"]).mean()
           < p["ratio"] * ind.volume.rolling(p["long"], min_periods=p["long"]).mean()),
        "close at an n-session closing high while mean volume(n) < ratio x mean volume(long)",
        params={"n": I(20, 5, 126), "long": I(60, 20, 252), "ratio": F(0.8, 0.3, 1.0)}, fields=CV,
        warmup=lambda p: p["long"] + 1)
    sig(reg, "low_volume_pullback", f, "Quiet pullback in an uptrend", "bullish",
        lambda ind, p: (ind.close > ind.sma(50)) & (ind.ret(1) < 0) & (ind.ret(1).shift(1) < 0)
        & (ind.ret(1).shift(2) < 0)
        & ((ind.volume / ind.volume.rolling(20, min_periods=20).mean().shift(3)) < p["ratio"]).astype(float)
        .rolling(3, min_periods=3).sum().ge(3),
        "three down closes above SMA50, each on volume below ratio x the 20-session average before the pullback",
        params={"ratio": F(0.8, 0.2, 1.0)}, fields=CV, warmup=55)


def _money_flow(reg):
    f = fam(reg, "money_flow", "Money flow", "Money flow index, Chaikin money flow and the accumulation/distribution "
                                             "line.")
    sig(reg, "mfi_oversold", f, "MFI below 20", "bullish", lambda ind, p: ind.mfi(p["n"]) < p["level"],
        "MFI(n) < level", params={"n": I(14, 5, 50), "level": F(20.0, 1, 50)}, fields=HLCV,
        warmup=lambda p: p["n"] + 1, show=lambda ind, p: osc((20, 80), MFI=ind.mfi(p["n"])))
    sig(reg, "mfi_overbought", f, "MFI above 80", "bearish", lambda ind, p: ind.mfi(p["n"]) > p["level"],
        "MFI(n) > level", params={"n": I(14, 5, 50), "level": F(80.0, 50, 99)}, fields=HLCV,
        warmup=lambda p: p["n"] + 1, show=lambda ind, p: osc((20, 80), MFI=ind.mfi(p["n"])))
    sig(reg, "cmf_cross_up", f, "Chaikin money flow turns positive", "bullish",
        lambda ind, p: cross_above(ind.cmf(p["n"]), p["level"]), "CMF(n) crosses above level",
        params={"n": I(20, 5, 100), "level": F(0.0, -0.3, 0.3)}, fields=HLCV, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: osc((0,), CMF=ind.cmf(p["n"])))
    sig(reg, "cmf_cross_down", f, "Chaikin money flow turns negative", "bearish",
        lambda ind, p: cross_below(ind.cmf(p["n"]), p["level"]), "CMF(n) crosses below level",
        params={"n": I(20, 5, 100), "level": F(0.0, -0.3, 0.3)}, fields=HLCV, warmup=lambda p: p["n"] + 1,
        show=lambda ind, p: osc((0,), CMF=ind.cmf(p["n"])))
    sig(reg, "ad_bull_divergence", f, "Accumulation at a price low", "bullish",
        lambda ind, p: (ind.close <= ind.prior_lowest(p["n"], "close"))
        & (ind.ad_line() > ind.ad_line().shift(1).rolling(p["n"], min_periods=p["n"]).min()),
        "close makes an n-session closing low while the A/D line stays above its prior n-session low",
        params={"n": I(20, 5, 126)}, fields=HLCV, warmup=lambda p: p["n"] + 2)


__all__ = ["register", "OHLCV"]
