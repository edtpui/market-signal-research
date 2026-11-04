"""Look-ahead prevention: a strategy's decisions up to session t never change when bars after t are replaced by
garbage, the view refuses explicit future requests, and ML training rows never use unfinished labels."""
from __future__ import annotations

import numpy as np
import pytest

from conftest import make_data
from strategy_lab.core.market import MarketData, MarketView
from strategy_lab.core.types import LookaheadError
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.strategies.ml import FEATURE_BARS, supervised_rows
from strategy_lab.strategies.registry import default_registry

CUT = 400


def poisoned(d: MarketData, cut: int) -> MarketData:
    rng = np.random.default_rng(99)
    f = {}
    for k, v in d.fields.items():
        x = v.copy()
        x.iloc[cut + 1:] = rng.uniform(1, 1000, size=x.iloc[cut + 1:].shape)
        if k in ("high",):
            x.iloc[cut + 1:] = 2000.0
        if k in ("low",):
            x.iloc[cut + 1:] = 0.5
        f[k] = x
    return MarketData(f, d.actions, d.price_mode)


def decisions(strategy, d, cut):
    r = StrategyRunner(strategy, d, ExecutionConfig(slippage_bps=2), 100_000, d.index[0], d.index[-1])
    out = r.run()
    ts = d.index[cut].date().isoformat()
    orders = [o.to_row() for o in out.orders if o.created_at <= ts]
    for o in orders:                                 # the order's later fate may depend on later bars
        o.pop("status"), o.pop("filled_quantity"), o.pop("avg_fill_price"), o.pop("reason")
    fills = [f.to_row() for f in out.fills if f.timestamp <= ts]
    eq = [x.equity for x in out.days if x.timestamp <= ts]
    return orders, fills, eq


PARAMS = {"buy_and_hold": {"rebalance_every": 20}, "sma_crossover": {"fast": 10, "slow": 40},
          "momentum_rotation": {"lookback": 40, "top_k": 2, "rebalance_every": 5},
          "rsi_mean_reversion": {"entry_rsi": 45, "exit_rsi": 55},
          "ml_ridge_ranker": {"train_window": 60, "retrain_every": 10, "top_k": 2, "horizon": 5},
          "momentum_ml_ensemble": {"train_window": 60, "retrain_every": 10, "top_k": 2, "horizon": 5}}


@pytest.mark.parametrize("sid", sorted(PARAMS))
def test_future_bars_never_change_past_decisions(sid):
    d = make_data(520, symbols=("AAA", "BBB", "CCC", "DDD", "EEE"), seed=3)
    cls = default_registry().get(sid)
    clean = decisions(cls(PARAMS[sid]), d, CUT)
    dirty = decisions(cls(PARAMS[sid]), poisoned(d, CUT), CUT)
    assert clean[0] == dirty[0] and clean[1] == dirty[1]
    assert np.allclose(clean[2], dirty[2])
    assert len(clean[1]) > 0, f"{sid} made no fills before the cut: the test would prove nothing"


def test_view_refuses_future_requests():
    d = make_data(50)
    v = MarketView(d, 10)
    assert v.history("close").index[-1] == d.index[10]
    assert len(v.history("close", bars=5)) == 5
    with pytest.raises(LookaheadError):
        v.at("close", "AAA", d.index[11])
    with pytest.raises(LookaheadError):
        v.window(end=d.index[20])
    with pytest.raises(LookaheadError):
        v.past(-1)
    assert v.past(3).timestamp == d.index[7]
    with pytest.raises(KeyError, match="not provided"):
        MarketView(d.restrict(("open", "close")), 10).history("high")


def test_view_history_is_not_writable_into_the_source():
    d = make_data(50)
    v = MarketView(d, 10)
    h = v.history("close")
    h.iloc[:, :] = -1.0
    assert (MarketView(d, 10).history("close") > 0).all().all()
    with pytest.raises(ValueError):
        d.array("close")[0, 0] = 1.0


def test_supervised_rows_use_only_finished_labels():
    d = make_data(300, symbols=tuple(f"S{i}" for i in range(8)), seed=5)
    from strategy_lab.strategies.ml import features_today
    log = {}
    for i in range(FEATURE_BARS, 250):
        f = features_today(MarketView(d, i))
        log[d.index[i]] = f
    horizon = 10
    view = MarketView(d, 200)
    X, y, used = supervised_rows(log, view, horizon, window=1000)
    pos = {t: i for i, t in enumerate(d.index)}
    assert used and max(pos[t] for t in used) + horizon <= 200
    assert all(t <= view.timestamp for t in used)
    assert len(X) == len(y) > 0
