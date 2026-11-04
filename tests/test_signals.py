"""Signal library: catalog completeness, indicator correctness against explicit loops, trigger definitions on
crafted bars, no look-ahead for every catalog signal, missing bars, parameters and combinations."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_data
from strategy_lab.core.strategy import Param, ParamError
from strategy_lab.signals.base import (CombinationSpec, ComponentSpec, Family, RegimeFilter, SignalDef,
                                       SignalRegistry, SignalSpec, evaluate, resolve_spec)
from strategy_lab.signals.indicators import Indicators, Panel, cross_above, rising_edge
from strategy_lab.signals.library import build_registry

REG = build_registry()


def panel_from(md, bench_sym=None):
    fields = {k: v.copy() for k, v in md.fields.items()}
    bench = fields["close"][bench_sym].copy() if bench_sym else None
    return Panel(fields, benchmark=bench, benchmark_symbol=bench_sym)


def frames(**cols):
    """A one-symbol panel from explicit columns (lists)."""
    n = len(next(iter(cols.values())))
    idx = pd.bdate_range("2020-01-01", periods=n)
    return Panel({k: pd.DataFrame({"X": np.asarray(v, float)}, index=idx) for k, v in cols.items()})


# -- catalog --------------------------------------------------------------------------------------------------------
def test_catalog_size_families_and_metadata():
    d = REG.describe()
    assert d["n_signals"] >= 200 and d["n_families"] >= 30
    ids = [s["id"] for s in d["signals"]]
    assert len(ids) == len(set(ids))
    for f in d["families"]:
        assert f["n_signals"] >= 2, f["id"]
        assert f["name"] and f["description"]
    for s in d["signals"]:
        assert s["direction"] in ("bullish", "bearish") and s["trigger"] and s["version"] and s["family"]
        assert s["warmup"] >= 1 and len(s["source_sha256"]) == 64
        assert set(s["fields"]) <= {"open", "high", "low", "close", "volume"}
    required = {"moving_average", "momentum", "rsi", "macd", "mean_reversion", "bollinger", "volatility", "atr",
                "volume", "gap", "breakout", "trend_strength", "price_action", "candlestick", "support_resistance",
                "relative_strength", "market_breadth", "channel", "volatility_compression", "trend_reversal"}
    assert required <= {f["id"] for f in d["families"]}
    for sid in ("rsi14_below_20", "rsi14_below_30", "rsi14_above_70", "rsi14_above_80", "rsi14_cross_above_30",
                "rsi14_cross_below_70", "rsi_bull_divergence", "sma_10_20_cross_up", "sma_20_50_cross_up",
                "golden_cross_50_200", "ema20_reclaim", "sma50_break_below", "gap_up", "gap_down", "gap_down_filled",
                "gap_up_continuation", "gap_up_large_atr", "gap_up_rvol", "breakout_20d", "breakout_50d",
                "breakout_52w", "donchian_breakout", "range_expansion_up", "bullish_engulfing", "bearish_engulfing",
                "inside_bar_breakout_up", "outside_bar_up", "long_lower_wick", "consecutive_up_closes",
                "obv_cross_up", "price_volume_divergence", "bb_squeeze_breakout_up", "rv_percentile_high"):
        REG.get(sid)


def test_every_signal_runs_with_only_its_declared_fields():
    md = make_data(n=320, symbols=[f"S{i}" for i in range(12)], seed=4)
    for d in REG.all():
        keep = set(d.fields) | {"close"}
        fields = {k: v for k, v in md.fields.items() if k in keep}
        bench = md.fields["close"]["S0"] if d.needs_benchmark else None
        ind = Indicators(Panel(fields, benchmark=bench, benchmark_symbol="S0"))
        m = evaluate(resolve_spec(SignalSpec(id=d.id), REG), ind)
        assert m.shape == md.fields["close"].shape and m.dtypes.eq(bool).all(), d.id


def test_no_signal_looks_ahead():
    """Poison every bar after `cut` (all fields, random scaling): no trigger on or before `cut` may change."""
    syms = [f"S{i:02d}" for i in range(25)]
    md = make_data(n=520, symbols=syms, seed=9)
    cut = 380
    rng = np.random.default_rng(1)
    pf = {k: v.copy() for k, v in md.fields.items()}
    for k in pf:
        tail = pf[k].iloc[cut + 1:].to_numpy()
        pf[k].iloc[cut + 1:] = tail * rng.uniform(0.2, 5.0, tail.shape)
    a = Indicators(panel_from(md, "S00"))
    b = Indicators(Panel(pf, benchmark=pf["close"]["S00"].copy(), benchmark_symbol="S00"))
    specs = [SignalSpec(id=d.id) for d in REG.all()]
    specs += [SignalSpec(id="rsi14_below_30", regime=RegimeFilter(market_trend="up", stock_vol="high")),
              CombinationSpec(name="c", direction="bullish", mode="k_of_n", k=2, window=3,
                              components=[ComponentSpec(id="rsi14_below_30"), ComponentSpec(id="bb_below_lower"),
                                          ComponentSpec(id="mom_5d_top_decile", negate=True)])]
    bad = []
    for s in specs:
        r = resolve_spec(s, REG)
        if not evaluate(r, a).iloc[: cut + 1].equals(evaluate(r, b).iloc[: cut + 1]):
            bad.append(r.key)
    assert bad == []


def test_a_missing_bar_never_triggers():
    md = make_data(n=300, symbols=["A", "B"], seed=2, gaps={"A": [150, 151]})
    ind = Indicators(panel_from(md))
    for sid in ("rsi14_below_30", "stoch_oversold", "ibs_low", "zscore_below", "ha_strong_bull", "willr_overbought"):
        m = evaluate(resolve_spec(SignalSpec(id=sid), REG), ind)
        assert not m["A"].iloc[150] and not m["A"].iloc[151], sid


# -- indicators against explicit loops -------------------------------------------------------------------------------
def test_sma_ema_rsi_atr_match_reference_loops():
    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, 120)))
    h = c * (1 + np.abs(rng.normal(0, 0.01, 120)))
    lo = c * (1 - np.abs(rng.normal(0, 0.01, 120)))
    ind = Indicators(frames(close=c, high=h, low=lo, open=c))
    n = 14
    assert np.isclose(ind.sma(10)["X"].iloc[50], c[41:51].mean())
    assert np.isnan(ind.sma(10)["X"].iloc[8])
    # EMA (adjust=False, seeded with the first value, NaN until n observations)
    a, e = 2 / (n + 1), c[0]
    for x in c[1:60]:
        e = a * x + (1 - a) * e
    assert np.isclose(ind.ema(n)["X"].iloc[59], e)
    # Wilder RSI
    d = np.diff(c)
    up, dn = np.clip(d, 0, None), np.clip(-d, 0, None)
    au, ad = up[0], dn[0]
    for i in range(1, 80):
        au = au + (up[i] - au) / n
        ad = ad + (dn[i] - ad) / n
    assert np.isclose(ind.rsi(n)["X"].iloc[80], 100 - 100 / (1 + au / ad))
    # true range and Wilder ATR
    tr = np.maximum(h[1:] - lo[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    assert np.allclose(ind.true_range()["X"].iloc[1:].to_numpy(), tr)
    assert np.isnan(ind.true_range()["X"].iloc[0])           # no previous close on the first session
    v = tr[0]                                                # session 1 seeds Wilder's average
    for x in tr[1:90]:                                       # sessions 2..90
        v = v + (x - v) / n
    assert np.isclose(ind.atr(n)["X"].iloc[90], v)


def test_macd_bbands_stoch_obv_cci_aroon():
    rng = np.random.default_rng(5)
    c = 50 * np.exp(np.cumsum(rng.normal(0, 0.015, 150)))
    h, lo = c * 1.01, c * 0.99
    vol = rng.integers(100, 1000, 150).astype(float)
    ind = Indicators(frames(close=c, high=h, low=lo, open=c, volume=vol))
    line, sig, hist = ind.macd()
    assert np.isclose(line["X"].iloc[100], ind.ema(12)["X"].iloc[100] - ind.ema(26)["X"].iloc[100])
    assert np.isclose(hist["X"].iloc[100], line["X"].iloc[100] - sig["X"].iloc[100])
    bb = ind.bbands(20, 2.0)
    w = c[81:101]
    assert np.isclose(bb["upper"]["X"].iloc[100], w.mean() + 2 * w.std(ddof=0))
    k, dd = ind.stoch(14, 3, 3)
    fast = [100 * (c[i] - lo[i - 13:i + 1].min()) / (h[i - 13:i + 1].max() - lo[i - 13:i + 1].min()) for i in range(98, 101)]
    assert np.isclose(k["X"].iloc[100], np.mean(fast))
    obv = np.concatenate([[0], np.cumsum(np.sign(np.diff(c)) * vol[1:])])
    assert np.allclose(ind.obv()["X"].to_numpy(), obv)
    tp = (h + lo + c) / 3
    win = tp[81:101]
    assert np.isclose(ind.cci(20)["X"].iloc[100], (tp[100] - win.mean()) / (0.015 * np.abs(win - win.mean()).mean()))
    up, dn = ind.aroon(25)
    hw = h[75:101]
    since = 25 - int(np.flatnonzero(hw == hw.max())[-1])
    assert np.isclose(up["X"].iloc[100], 100 * (25 - since) / 25)


def test_parabolic_sar_flips_with_the_trend():
    c = np.concatenate([np.linspace(100, 150, 40), np.linspace(150, 100, 40)])
    ind = Indicators(frames(close=c, high=c * 1.005, low=c * 0.995, open=c))
    sar, trend = ind.psar()
    assert trend["X"].iloc[30] == 1 and trend["X"].iloc[75] == -1
    assert (sar["X"].iloc[5:38] < c[5:38]).all()
    flips = evaluate(resolve_spec(SignalSpec(id="psar_flip_down"), REG), ind)["X"]
    assert flips.sum() == 1 and 40 <= int(np.flatnonzero(flips.to_numpy())[0]) <= 50


# -- trigger definitions on crafted bars ----------------------------------------------------------------------------
def fire(sid, panel, params=None):
    return evaluate(resolve_spec(SignalSpec(id=sid, params=params or {}), REG), Indicators(panel))["X"].to_numpy()


def test_crossover_fires_exactly_on_the_crossing_session():
    c = [10.0] * 25 + [9.0] * 5 + [12.0] * 10
    m = fire("sma_10_20_cross_up", frames(close=c))
    sma10 = pd.Series(c).rolling(10).mean()
    sma20 = pd.Series(c).rolling(20).mean()
    expect = ((sma10 > sma20) & (sma10.shift(1) <= sma20.shift(1))).to_numpy()
    assert (m == expect).all() and m.sum() == 1


def test_gap_engulfing_breakout_and_patterns():
    # gap up 3%: open on t vs the previous close
    o = [100, 100, 103.5, 104]
    c = [100, 100, 104, 104]
    p = frames(open=o, high=[101, 101, 105, 105], low=[99, 99, 103, 103], close=c)
    assert fire("gap_up", p).tolist() == [False, False, True, False]
    assert fire("gap_up_continuation", p).tolist() == [False, False, True, False]
    # gap down 3%, filled: closes back above the previous close
    p = frames(open=[100, 100, 97, 99], high=[101, 101, 101, 100], low=[99, 99, 96, 98], close=[100, 100, 100.5, 99])
    assert fire("gap_down_filled", p).tolist() == [False, False, True, False]
    assert not fire("gap_down_continuation", p).any()
    # bullish engulfing: down bar, then an up bar whose body covers it
    p = frames(open=[10, 10.5, 9.8], high=[10.6, 10.6, 10.9], low=[9.9, 9.9, 9.7], close=[10.4, 10.0, 10.8])
    assert fire("bullish_engulfing", p).tolist() == [False, False, True]
    # breakout: first close above the prior 20-session closing high; an equal close does not count
    c = [10.0] * 21 + [10.0, 10.5, 10.4]
    m = fire("breakout_20d", frames(close=c))
    assert m.tolist()[-3:] == [False, True, False]
    # three white soldiers
    o = [10, 9.8, 10.0, 10.5, 11.0]
    c = [10, 10.1, 10.6, 11.1, 11.7]
    p = frames(open=o, high=[x + 0.05 for x in c], low=[x - 0.05 for x in o], close=c)
    assert fire("three_white_soldiers", p).tolist() == [False, False, False, False, True]


def test_rsi_threshold_equals_its_definition():
    rng = np.random.default_rng(8)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 200)))
    ind = Indicators(frames(close=c))
    assert (fire("rsi14_below_30", frames(close=c)) == (ind.rsi(14)["X"] < 30).to_numpy()).all()
    m25 = fire("rsi14_below_30", frames(close=c), {"level": 25.0})
    assert (m25 == (ind.rsi(14)["X"] < 25).to_numpy()).all()


def test_market_breadth_is_market_wide():
    md = make_data(n=200, symbols=[f"S{i}" for i in range(10)], seed=6)
    f = {k: v.copy() for k, v in md.fields.items()}
    for k in ("open", "high", "low", "close"):
        f[k].iloc[150:] = f[k].iloc[150:] * 0.6            # every symbol collapses below its SMA50
    m = evaluate(resolve_spec(SignalSpec(id="breadth_washout"), REG), Indicators(Panel(f)))
    assert m.iloc[150].all() and m.sum().sum() == 10       # one session, every symbol, first session only


# -- parameters and combinations --------------------------------------------------------------------------------------
def test_parameters_are_validated_and_keys_are_canonical():
    with pytest.raises(ParamError):
        resolve_spec(SignalSpec(id="rsi14_below_30", params={"nope": 1}), REG)
    with pytest.raises(ParamError):
        resolve_spec(SignalSpec(id="rsi14_below_30", params={"level": 500}), REG)
    a = resolve_spec(SignalSpec(id="rsi14_below_30"), REG)
    b = resolve_spec(SignalSpec(id="rsi14_below_30", params={"level": 30.0, "n": 14}), REG)
    c = resolve_spec(SignalSpec(id="rsi14_below_30", params={"level": 25}), REG)
    assert a.key == b.key != c.key and a.key.startswith("rsi14_below_30@1.0:")


def _mask_registry(masks: dict[str, np.ndarray]) -> SignalRegistry:
    reg = SignalRegistry()
    reg.add_family(Family("t", "test", "test"))
    for k, v in masks.items():
        reg.register(SignalDef(id=k, family="t", name=k, direction="bullish", trigger="fixed",
                               fn=lambda ind, p, v=v: pd.DataFrame({"X": v}, index=ind.index)))
    return reg


def test_combination_modes():
    n = 8
    a = np.array([1, 0, 1, 0, 1, 1, 0, 0], bool)
    b = np.array([0, 0, 1, 1, 0, 1, 0, 1], bool)
    reg = _mask_registry({"a": a, "b": b})
    ind = Indicators(frames(close=np.linspace(10, 11, n)))

    def run(**kw):
        spec = CombinationSpec(name="x", direction="bullish", components=[ComponentSpec(id="a"),
                               ComponentSpec(**kw.pop("b", {"id": "b"}))], edge=kw.pop("edge", False), **kw)
        return evaluate(resolve_spec(spec, reg), ind)["X"].to_numpy()

    assert (run(mode="and") == (a & b)).all()
    assert (run(mode="or") == (a | b)).all()
    assert (run(mode="and", b={"id": "b", "negate": True}) == (a & ~b)).all()
    assert (run(mode="k_of_n", k=2) == (a & b)).all()
    assert (run(mode="weighted", threshold=1.5, b={"id": "b", "weight": 0.5}) == (a & b)).all()
    recent_b = pd.Series(b).rolling(2, min_periods=1).max().astype(bool).to_numpy()
    recent_a = pd.Series(a).rolling(2, min_periods=1).max().astype(bool).to_numpy()
    assert (run(mode="and", window=2) == (recent_a & recent_b)).all()
    assert (run(mode="or", edge=True) == rising_edge(pd.DataFrame({"X": a | b}))["X"].to_numpy()).all()


def test_cross_helpers_ignore_missing_neighbours():
    a = pd.DataFrame({"X": [1.0, np.nan, 3.0, 1.0, 3.0]})
    assert cross_above(a, 2.0)["X"].tolist() == [False, False, False, False, True]


def test_registry_rejects_duplicates_and_bad_definitions():
    reg = SignalRegistry()
    reg.add_family(Family("t", "t", "t"))
    d = SignalDef(id="x", family="t", name="x", direction="bullish", fn=lambda i, p: i.close > 0, trigger="t",
                  params={"n": Param(3, "int", 1, 5)})
    reg.register(d)
    with pytest.raises(ValueError):
        reg.register(d)
    with pytest.raises(ValueError):
        SignalDef(id="Bad Id", family="t", name="x", direction="bullish", fn=lambda i, p: None, trigger="t")
    with pytest.raises(ValueError):
        SignalDef(id="y", family="t", name="x", direction="up", fn=lambda i, p: None, trigger="t")
