"""Metric definitions against hand calculations; undefined values carry reasons."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from strategy_lab.analytics import metrics as mt


def curve(rets, start=100.0, ppy_days="B"):
    idx = pd.bdate_range("2020-01-01", periods=len(rets) + 1)
    return pd.Series(start * np.cumprod(np.concatenate([[1.0], 1 + np.asarray(rets)])), index=idx)


def test_core_metrics_match_hand_calculation():
    rng = np.random.default_rng(0)
    r = rng.normal(0.0005, 0.01, 504)
    eq = curve(r)
    m = mt.compute(mt.Inputs(equity=eq))
    assert m["total_return"]["value"] == pytest.approx(eq.iloc[-1] / eq.iloc[0] - 1)
    assert m["cagr"]["value"] == pytest.approx((eq.iloc[-1] / eq.iloc[0]) ** (252 / 504) - 1)
    rr = pd.Series(r)
    assert m["volatility"]["value"] == pytest.approx(rr.std(ddof=1) * math.sqrt(252))
    assert m["sharpe"]["value"] == pytest.approx(rr.mean() / rr.std(ddof=1) * math.sqrt(252))
    down = math.sqrt(np.mean(np.minimum(r, 0) ** 2))
    assert m["sortino"]["value"] == pytest.approx(rr.mean() / down * math.sqrt(252))
    dd = (eq / eq.cummax() - 1).min()
    assert m["max_drawdown"]["value"] == pytest.approx(dd)
    assert m["calmar"]["value"] == pytest.approx(m["cagr"]["value"] / abs(dd))


def test_drawdown_duration_and_recovery():
    eq = pd.Series([100, 110, 99, 95, 105, 111, 120.0], index=pd.bdate_range("2020-01-01", periods=7))
    info = mt.max_drawdown_info(eq)
    assert info["max_drawdown"] == pytest.approx(95 / 110 - 1)
    assert info["peak"] == eq.index[1] and info["trough"] == eq.index[3] and info["recovery"] == eq.index[5]
    assert info["duration_periods"] == 4


def test_undefined_is_never_zero():
    short = mt.compute(mt.Inputs(equity=curve([0.01] * 5)))
    assert short["sharpe"]["value"] is None and "returns" in short["sharpe"]["reason"]
    assert short["cagr"]["value"] is None and "year" in short["cagr"]["reason"]
    flat = mt.compute(mt.Inputs(equity=curve([0.0] * 300)))
    assert flat["sharpe"]["value"] is None and flat["sharpe"]["reason"] == "zero return variance"
    assert flat["calmar"]["value"] is None
    up = mt.compute(mt.Inputs(equity=curve([0.001] * 300)))
    assert up["sortino"]["value"] is None and up["sortino"]["reason"] == "no negative returns"
    none = mt.compute(mt.Inputs(equity=curve([0.001] * 300)))
    assert none["win_rate"]["value"] is None and none["trade_count"]["value"] == 0
    assert none["beta"]["value"] is None and none["beta"]["reason"] == "no benchmark"


def test_trade_metrics_and_round_trips():
    fills = pd.DataFrame([
        dict(symbol="A", quantity=10, price=100.0, commission=1.0, timestamp="2020-01-02"),
        dict(symbol="A", quantity=-4, price=110.0, commission=1.0, timestamp="2020-01-06"),
        dict(symbol="A", quantity=-6, price=90.0, commission=1.0, timestamp="2020-01-08"),
        dict(symbol="B", quantity=5, price=50.0, commission=0.0, timestamp="2020-01-03"),
    ])
    closed, open_lots = mt.round_trips(fills)
    assert len(closed) == 2 and len(open_lots) == 1
    t1, t2 = closed.iloc[0], closed.iloc[1]
    assert t1["pnl"] == pytest.approx(4 * 10 - 4 * 0.1 - 1.0)              # entry commission share 0.1/sh + exit 1
    assert t2["pnl"] == pytest.approx(-6 * 10 - 6 * 0.1 - 1.0)
    closed = mt.add_holding_periods(closed, pd.bdate_range("2020-01-01", periods=10))
    m = mt.compute(mt.Inputs(equity=curve([0.001] * 30), trades=closed))
    assert m["trade_count"]["value"] == 2 and m["win_rate"]["value"] == 0.5
    assert m["profit_factor"]["value"] == pytest.approx(t1["pnl"] / -t2["pnl"])


def test_benchmark_relative_metrics():
    rng = np.random.default_rng(1)
    r = rng.normal(0.0003, 0.01, 300)
    eq = curve(r)
    same = mt.compute(mt.Inputs(equity=eq, benchmark=eq * 3))
    assert same["beta"]["value"] == pytest.approx(1.0) and same["correlation"]["value"] == pytest.approx(1.0)
    assert same["excess_return"]["value"] == pytest.approx(0.0)
    lev = curve(2 * r)
    m = mt.compute(mt.Inputs(equity=lev, benchmark=eq))
    assert m["beta"]["value"] == pytest.approx(2.0, rel=1e-6)


def test_monthly_and_annual_returns_compound():
    idx = pd.to_datetime(["2020-01-30", "2020-01-31", "2020-02-28", "2020-03-31"])
    eq = pd.Series([100, 110, 99, 108.9], index=idx)
    mo = mt.monthly_returns(eq)
    assert list(mo["ret"].round(10)) == [0.1, -0.1, 0.1]
    yr = mt.annual_returns(eq)
    assert yr["ret"].iloc[0] == pytest.approx(0.089)


def test_rolling_window():
    eq = curve(np.full(100, 0.01))
    r = mt.rolling(eq, 10)
    assert r["return"].iloc[9] == pytest.approx(1.01 ** 10 - 1)
    assert np.isnan(r["return"].iloc[8])
