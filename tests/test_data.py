"""Data layer: policy, ingest and versioning, coverage and missing data, the CSV provider, corporate actions."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from conftest import make_data
from strategy_lab.core.strategy import Strategy
from strategy_lab.core.types import OrderIntent
from strategy_lab.data.policy import HARD_CEILING, DataPolicy, PolicyError, period_labels
from strategy_lab.data.providers import DatasetSpec, YFinanceCsvProvider, derive_dividends
from strategy_lab.data.store import DataError, DataStore
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.runner import StrategyRunner


def test_policy_ceiling_cannot_be_raised():
    assert HARD_CEILING == dt.date(2025, 12, 31)
    with pytest.raises(PolicyError, match="HOLDOUT"):
        DataPolicy(ceiling=dt.date(2026, 1, 2))
    p = DataPolicy()
    with pytest.raises(PolicyError, match="ceiling"):
        p.check_range("2024-01-02", "2026-02-01")
    with pytest.raises(PolicyError, match="backtest floor"):
        p.check_range("2018-06-01", "2020-01-01")
    assert period_labels("2021-01-01", "2023-06-01") == ["IS", "OOS"]
    assert period_labels("2019-01-02", "2019-12-31") == ["IS"]


def _write_csv_dataset(root, rows_2026=True):
    root.mkdir(parents=True, exist_ok=True)
    days = pd.bdate_range("2017-12-01", "2026-01-30" if rows_2026 else "2025-12-31")
    rng = np.random.default_rng(2)
    for k, sym in enumerate(["AAA", "BBB"]):
        close_raw = 20 * (k + 1) * np.exp(np.cumsum(rng.normal(0, 0.01, len(days))))
        # one dividend of 0.40 on 2021-03-01: adjusted closes before it are scaled by 1 - D / close_raw(prev)
        ex = days.searchsorted(pd.Timestamp("2021-03-01"))
        factor = np.ones(len(days))
        factor[:ex] *= 1 - 0.40 / close_raw[ex - 1]
        adj = close_raw * factor
        df = pd.DataFrame(dict(Date=days.date, Open=adj * 0.999, High=adj * 1.01, Low=adj * 0.99, Close=adj,
                               Volume=1e6))
        if sym == "BBB":
            df.loc[400, ["Open", "High", "Low", "Close"]] = [-1, -1, -1, -1]          # an invalid bar (2019)
            df = df.drop(index=[600, 601])                                          # a gap (2020)
        df.to_csv(root / f"{sym}_1d.csv", index=False)
        pd.DataFrame(dict(Date=days.date, Close=close_raw, Volume=5e5)).to_csv(root / f"{sym}_1d_raw.csv", index=False)
    (root / "manifest.json").write_text(json.dumps({"note": "test"}), encoding="utf-8")


def test_csv_provider_clips_validates_and_derives_dividends(tmp_path):
    src = tmp_path / "src"
    _write_csv_dataset(src)
    spec = DatasetSpec(id="t", name="t", provider="yfinance_csv", paths=[src])
    store = DataStore(tmp_path / "cache", DataPolicy(), [spec])
    v = store.ingest("t")
    m = v.meta
    assert m["last_date"] <= "2025-12-31" and m["first_date"] >= "2018-01-01"
    assert m["stats"]["rows_beyond_ceiling"] > 0 and m["stats"]["rows_before_floor"] > 0
    bars = store.bars("t", ["AAA", "BBB"], "2018-01-01", "2025-12-31")
    assert bars["date"].max() <= pd.Timestamp("2025-12-31")
    assert m["invalid_bars"] == 1
    cov = store.coverage("t").set_index("symbol")
    assert cov.at["BBB", "missing"] == 3 and cov.at["BBB", "invalid_bars"] == 1 and cov.at["AAA", "missing"] == 0
    assert (bars["volume"] == 5e5).all()                                    # volume from the raw file (A9)
    md, rep = store.load("t", ["AAA", "BBB"], "2019-01-02", "2025-12-31", price_mode="raw")
    divs = md.actions[md.actions["kind"] == "dividend"]
    assert len(divs) == 2 and np.allclose(divs["value"], 0.40, atol=1e-6)
    assert set(divs["date"]) == {pd.Timestamp("2021-03-01")}
    assert rep["missing_bars"]["BBB"] >= 2
    assert np.isnan(md.fields["close"]["BBB"]).sum() >= 3                   # gaps and the invalid bar stay missing
    with pytest.raises(DataError, match="outside"):
        store.load("t", ["AAA"], "2019-01-02", "2026-01-15")
    with pytest.raises(DataError, match="no data"):
        store.load("t", ["ZZZ"], "2019-01-02", "2020-01-02")


def test_versions_are_content_addressed_and_immutable(tmp_path):
    src = tmp_path / "src"
    _write_csv_dataset(src, rows_2026=False)
    spec = DatasetSpec(id="t", name="t", provider="yfinance_csv", paths=[src])
    store = DataStore(tmp_path / "cache", DataPolicy(), [spec])
    v1 = store.ingest("t")
    assert DataStore(tmp_path / "cache", DataPolicy(), [spec]).ingest("t").version == v1.version
    df = pd.read_csv(src / "AAA_1d.csv")
    df.loc[500, "Close"] *= 1.0001
    df.to_csv(src / "AAA_1d.csv", index=False)
    v2 = DataStore(tmp_path / "cache", DataPolicy(), [spec]).ingest("t")
    assert v2.version != v1.version
    assert store.version("t", v1.version).path.exists()                     # the old version stays readable
    assert {x["version"] for x in store.cached_versions("t")} == {v1.version, v2.version}


def test_synthetic_dataset_and_frequencies(lab):
    v = lab.store.ingest("synthetic")
    assert v.meta["n_actions"] == 5
    md, _ = lab.store.load("synthetic", ["SYNA", "SYNB"], "2019-01-02", "2020-12-31", frequency="1w")
    assert len(md.index) < 110 and md.index.is_monotonic_increasing
    d, _ = lab.store.load("synthetic", ["SYNA"], "2019-01-02", "2019-03-29")
    w, _ = lab.store.load("synthetic", ["SYNA"], "2019-01-02", "2019-03-29", frequency="1w")
    wk = d.fields["close"]["SYNA"].groupby(d.index.to_period("W-FRI")).last()
    assert np.allclose(w.fields["close"]["SYNA"].to_numpy()[-len(wk):], wk.to_numpy()[-len(w.index):])
    with pytest.raises(DataError, match="raw price mode needs daily"):
        lab.store.load("synthetic", ["SYNA"], "2019-01-02", "2019-03-29", frequency="1w", price_mode="raw")
    with pytest.raises(DataError, match="not available"):
        lab.store.load("synthetic", ["SYNA"], "2019-01-02", "2019-03-29", frequency="1h")


class Hold(Strategy):
    id = "hold_test"
    version = "t"

    def on_bar(self, ctx):
        if ctx.execution.session_index == 0:
            return [OrderIntent.market(s, 100) for s in ctx.universe]


def test_split_keeps_value_and_quantity_scales(lab):
    md, _ = lab.store.load("synthetic", ["SYNB"], "2021-01-04", "2021-12-31", price_mode="raw")
    out = StrategyRunner(Hold(), md, ExecutionConfig(price_mode="raw"), 100_000, md.index[0], md.index[-1]).run()
    split = pd.Timestamp("2021-06-01")
    pos = {h["timestamp"]: h for h in out.holdings}
    before = pos[md.index[md.index < split][-1].date().isoformat()]
    after = pos[split.date().isoformat()]
    assert before["quantity"] == 100 and after["quantity"] == 400
    assert after["avg_cost"] == pytest.approx(before["avg_cost"] / 4)
    eq = pd.Series({d.timestamp: d.equity for d in out.days})
    jump = eq.pct_change().abs().max()
    assert jump < 0.2                                                       # no fake -75% day at the split


def test_dividend_is_credited_on_the_ex_date(lab):
    md, _ = lab.store.load("synthetic", ["SYNC"], "2022-06-01", "2022-12-30", price_mode="raw")
    out = StrategyRunner(Hold(), md, ExecutionConfig(price_mode="raw"), 100_000, md.index[0], md.index[-1]).run()
    day = {d.timestamp: d for d in out.days}
    assert day["2022-09-01"].dividends == pytest.approx(100 * 1.20)
    assert sum(d.dividends for d in out.days) == pytest.approx(120.0)
    out_adj = StrategyRunner(Hold(), lab.store.load("synthetic", ["SYNC"], "2022-06-01", "2022-12-30")[0],
                             ExecutionConfig(), 100_000, md.index[0], md.index[-1]).run()
    assert sum(d.dividends for d in out_adj.days) == 0                     # adjusted mode: embedded in prices


def test_derive_dividends_matches_known_events():
    days = pd.bdate_range("2020-01-01", periods=30)
    raw = np.linspace(50, 52, 30)
    f = np.ones(30)
    f[:10] *= 1 - 0.30 / raw[9]
    bars = pd.DataFrame(dict(symbol="X", date=days, close_raw=raw, factor=f))
    d = derive_dividends(bars)
    assert len(d) == 1 and d["date"].iloc[0] == days[10] and d["value"].iloc[0] == pytest.approx(0.30)


def test_missing_bars_are_not_filled():
    d = make_data(30, gaps={"AAA": [5, 6]})
    assert np.isnan(d.fields["close"]["AAA"].iloc[5])

    class Once(Strategy):
        id = "once"
        version = "t"

        def on_bar(self, ctx):
            if ctx.execution.session_index == 4:
                return OrderIntent.market("AAA", 10)

    out = StrategyRunner(Once(), d, ExecutionConfig(), 10_000, d.index[0], d.index[-1]).run()
    assert not out.fills and out.orders[0].status.value == "expired" and "no bar" in out.orders[0].reason


def test_provider_lists_first_directory_wins(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _write_csv_dataset(a, rows_2026=False)
    _write_csv_dataset(b, rows_2026=False)
    p = YFinanceCsvProvider(DatasetSpec(id="x", name="x", provider="yfinance_csv", paths=[a, b]))
    assert {str(f["adj"].parent) for f in p.files().values()} == {str(a)}


def test_calendar_ignores_a_series_that_trades_on_exchange_holidays(tmp_path):
    """A macro series with a bar on a market holiday must not give every stock a phantom missing session."""
    src = tmp_path / "src"
    _write_csv_dataset(src, rows_2026=False)
    days = pd.bdate_range("2018-01-01", "2025-12-31")           # the same business days as the test stocks
    for name, extra in (("SPY", False), ("FX", True)):
        d = days if extra else days.delete(days.get_loc(pd.Timestamp("2025-01-09")))
        px = np.linspace(100, 200, len(d))
        pd.DataFrame(dict(Date=d.date, Open=px, High=px * 1.01, Low=px * 0.99, Close=px, Volume=1e6)).to_csv(
            src / f"{name}_1d.csv", index=False)
    for sym in ("AAA", "BBB"):                                   # the stocks are closed that day too
        df = pd.read_csv(src / f"{sym}_1d.csv")
        df[df["Date"] != "2025-01-09"].to_csv(src / f"{sym}_1d.csv", index=False)
    store = DataStore(tmp_path / "cache", DataPolicy(), [DatasetSpec(id="t", name="t", provider="yfinance_csv",
                                                                     paths=[src])])
    v = store.ingest("t")
    assert v.meta["calendar"] == "SPY sessions" and v.meta["bars_outside_calendar"] == 1
    assert pd.Timestamp("2025-01-09") not in store.calendar("t")
    cov = store.coverage("t").set_index("symbol")
    assert cov.at["AAA", "missing"] == 0 and cov.at["SPY", "missing"] == 0
    md, _ = store.load("t", ["AAA", "FX"], "2024-12-02", "2025-01-31")
    assert pd.Timestamp("2025-01-09") not in md.index
