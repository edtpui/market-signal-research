"""Read-only checks on the workspace research downloads (skipped when absent): the holdout ceiling holds on real
files, coverage is reported, derived dividends are plausible, and a real backtest runs end to end."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from strategy_lab.data.policy import DataPolicy
from strategy_lab.data.providers import DatasetSpec
from strategy_lab.data.store import DataStore

DATA = Path(__file__).resolve().parents[3] / "data" / "yf_etf_2008_2025"
pytestmark = [pytest.mark.realdata, pytest.mark.skipif(not DATA.is_dir(), reason="workspace data not present")]


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    spec = DatasetSpec(id="etf", name="etf", provider="yfinance_csv", paths=[DATA])
    return DataStore(tmp_path_factory.mktemp("cache"), DataPolicy(), [spec])


def test_real_etf_data_respects_the_policy(store):
    v = store.ingest("etf")
    assert v.meta["last_date"] <= "2025-12-31" and v.meta["first_date"] >= "2018-01-01"
    assert v.meta["stats"]["rows_before_floor"] > 0                       # 2008-2017 (BACK) rows were dropped
    bars = store.bars("etf", ["SPY", "IJH", "IJR"], "2018-01-01", "2025-12-31")
    assert bars["date"].max() <= pd.Timestamp("2025-12-31") and bars["date"].min() >= pd.Timestamp("2018-01-01")
    cov = store.coverage("etf").set_index("symbol")
    assert cov.loc["SPY", "n_bars"] > 1900


def test_spy_dividends_are_quarterly_and_plausible(store):
    md, _ = store.load("etf", ["SPY"], "2019-01-02", "2025-12-31", price_mode="raw")
    d = md.actions[md.actions["kind"] == "dividend"]
    per_year = d.groupby(d["date"].dt.year).size()
    assert per_year.between(3, 5).all()
    assert d["value"].between(1.0, 2.5).all()                             # SPY pays roughly $1.2-1.9 a quarter


def test_real_buy_and_hold_tracks_the_benchmark(store, tmp_path):
    from strategy_lab.experiments.backtest import Universes, run_backtest
    from strategy_lab.experiments.config import ExperimentConfig
    from strategy_lab.strategies.registry import default_registry
    cfg = ExperimentConfig(strategy="buy_and_hold", dataset="etf", universe=["SPY"], start="2019-01-02",
                           end="2025-12-31", benchmark="SPY")
    r = run_backtest(cfg, store, default_registry(), Universes(), DataPolicy())
    assert abs(r.metrics["total_return"]["value"] - r.metrics["benchmark_total_return"]["value"]) < 0.02
    assert r.metrics["beta"]["value"] == pytest.approx(1.0, abs=0.01)
    assert r.periods == ["IS", "OOS"]
