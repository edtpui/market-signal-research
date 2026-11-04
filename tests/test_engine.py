"""Chronological event processing, order lifecycle, transaction costs, portfolio accounting."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from conftest import make_data
from strategy_lab.core.strategy import Strategy
from strategy_lab.core.types import CancelIntent, OrderIntent, OrderStatus, TargetWeights, TimeInForce
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.runner import StrategyError, StrategyRunner


class Script(Strategy):
    """Emits a fixed script of intents keyed by session index; records what it saw."""
    id = "script"
    version = "t"
    required_fields = frozenset({"close"})
    order_types = frozenset({"market", "limit", "stop"})
    long_only = False

    def __init__(self, script=None, **kw):
        super().__init__({})
        self.script = script or {}
        self.seen, self.events = [], []

    def on_order_event(self, ctx, e):
        self.events.append((ctx.timestamp, e))

    def on_bar(self, ctx):
        self.seen.append((ctx.timestamp, ctx.market.sessions[-1], ctx.portfolio.cash, ctx.portfolio.equity))
        f = self.script.get(ctx.execution.session_index)
        return f(ctx) if callable(f) else f


def run(strategy, data, cfg=None, start=None, end=None, capital=100_000.0, universe=None):
    cfg = cfg or ExecutionConfig()
    r = StrategyRunner(strategy, data, cfg, capital, start or data.index[0], end or data.index[-1], universe)
    return r.run(), r


def test_strategy_sees_only_its_session_and_orders_fill_next_open(data):
    s = Script({0: OrderIntent.market("AAA", 10)})
    out, _ = run(s, data)
    assert all(ts == last for ts, last, _, _ in s.seen)                   # history ends at the decision session
    f = out.fills[0]
    assert f.timestamp == data.index[1].date().isoformat()
    assert f.price == pytest.approx(data.fields["open"]["AAA"].iloc[1])
    assert f.reference_price == f.price                                    # zero costs by default


def test_next_close_timing(data):
    s = Script({0: OrderIntent.market("AAA", 10)})
    out, _ = run(s, data, ExecutionConfig(fill_timing="next_close"))
    assert out.fills[0].price == pytest.approx(data.fields["close"]["AAA"].iloc[1])


def test_events_arrive_in_order_at_the_next_close(data):
    s = Script({0: OrderIntent.market("AAA", 10)})
    run(s, data)
    (t1, sub), (t2, fill) = s.events[:2]
    assert sub.status == OrderStatus.SUBMITTED and fill.status == OrderStatus.FILLED
    assert t1 == t2 == data.index[1]                                       # both delivered at session 1's close
    assert sub.timestamp == data.index[0].date().isoformat()


def test_limit_order_fills_at_limit_or_better_and_day_orders_expire():
    d = make_data(30)
    o, lo = d.fields["open"]["AAA"].iloc[1], d.fields["low"]["AAA"].iloc[1]
    lim = (o + lo) / 2                                       # between low and open: touched intraday
    s = Script({0: OrderIntent.limit("AAA", 5, lim), 5: OrderIntent.limit("AAA", 5, lo * 0.5)})
    out, _ = run(s, d)
    assert out.fills[0].price == pytest.approx(lim) and out.fills[0].timestamp == d.index[1].date().isoformat()
    far = [x for x in out.orders if x.limit_price == pytest.approx(lo * 0.5)][0]
    assert far.status == OrderStatus.EXPIRED and far.filled_quantity == 0


def test_limit_gap_fills_at_open_and_gtc_persists():
    d = make_data(30)
    o1 = d.fields["open"]["AAA"].iloc[1]
    s = Script({0: OrderIntent.limit("AAA", 5, o1 * 1.5)})                  # buy limit above the open: fill at open
    out, _ = run(s, d)
    assert out.fills[0].price == pytest.approx(o1)
    low_after = d.fields["low"]["AAA"].iloc[1:].min()
    s2 = Script({0: OrderIntent.limit("AAA", 5, float(low_after) * 1.0001, tif=TimeInForce.GTC)})
    out2, _ = run(s2, d)
    assert len(out2.fills) == 1 and out2.fills[0].timestamp > d.index[1].date().isoformat()


def test_stop_order_triggers_and_cancel_works():
    d = make_data(60)
    s = Script({0: OrderIntent.market("AAA", 10), 2: OrderIntent.stop("AAA", -10, 1e9),     # sell stop: triggers
                3: OrderIntent.stop("BBB", 5, 1e9), 4: lambda ctx: [CancelIntent(ctx.execution.open_orders[0].order_id)]})
    out, _ = run(s, d)
    stop_sell = [x for x in out.orders if x.order_type.value == "stop" and x.symbol == "AAA"][0]
    assert stop_sell.status == OrderStatus.FILLED
    assert out.fills[1].price == pytest.approx(d.fields["open"]["AAA"].iloc[3])            # gapped through: at open
    stop_buy = [x for x in out.orders if x.symbol == "BBB"][0]
    assert stop_buy.status == OrderStatus.CANCELLED


def test_rejections_are_explicit():
    d = make_data(20)
    s = Script({0: [OrderIntent.market("AAA", 2.5)], 1: [OrderIntent.limit("AAA", 1, 10.0)]})
    out, _ = run(s, d.restrict(("open", "close")))
    rej = [o for o in out.orders if o.status == OrderStatus.REJECTED]
    assert len(rej) == 2
    assert "fractional" in rej[0].reason and "high and low" in rej[1].reason
    with pytest.raises(StrategyError, match="outside its universe"):
        run(Script({0: OrderIntent.market("ZZZ", 1)}), d)


def test_partial_fills_with_participation_cap():
    d = make_data(20)
    vol1 = d.fields["volume"]["AAA"].iloc[1]
    want = int(vol1 * 0.01) * 3
    s = Script({0: OrderIntent.market("AAA", want, tif=TimeInForce.GTC)})
    out, _ = run(s, d, ExecutionConfig(participation_cap=0.01), capital=1e12)
    o = out.orders[0]
    first = out.fills[0]
    assert first.reason == "partial" and first.quantity == math.floor(vol1 * 0.01)
    assert len(out.fills) >= 2 and o.status == OrderStatus.FILLED
    assert sum(f.quantity for f in out.fills) == want
    s2 = Script({0: OrderIntent.market("AAA", want)})                    # DAY: the remainder expires
    out2, _ = run(s2, d, ExecutionConfig(participation_cap=0.01), capital=1e12)
    assert out2.orders[0].status == OrderStatus.EXPIRED and len(out2.fills) == 1


def test_cash_policies():
    d = make_data(20)
    px = d.fields["open"]["AAA"].iloc[1]
    big = int(2_000 / px) + 50
    out, _ = run(Script({0: OrderIntent.market("AAA", big)}), d, capital=2_000)
    assert 0 < out.fills[0].quantity < big and out.fills[0].quantity * out.fills[0].price <= 2_000
    out2, _ = run(Script({0: OrderIntent.market("AAA", big)}), d, ExecutionConfig(cash_policy="reject"), capital=2_000)
    assert not out2.fills and out2.orders[0].status == OrderStatus.REJECTED


def test_short_disabled_caps_sells_and_shorting_when_enabled():
    d = make_data(20)
    out, _ = run(Script({0: OrderIntent.market("AAA", 10), 2: OrderIntent.market("AAA", -25)}), d)
    assert out.fills[1].quantity == -10 and out.orders[1].reason == "sell reduced to the held quantity"
    out2, _ = run(Script({0: OrderIntent.market("AAA", -10)}), d)
    assert not out2.fills and out2.orders[0].status == OrderStatus.REJECTED
    out3, r = run(Script({0: OrderIntent.market("AAA", -10)}), d, ExecutionConfig(allow_short=True,
                                                                                    short_borrow_bps_per_year=100))
    assert out3.fills[0].quantity == -10 and r.portfolio.quantity("AAA") == -10
    assert sum(x.borrow for x in out3.days) > 0


def test_costs_commission_slippage_spread(data):
    cfg = ExecutionConfig(slippage_bps=10, commission=dict(per_share=0.01, bps=2, per_order=1.0, minimum=2.0),
                          spread=dict(model="fixed_bps", bps=6))
    out, _ = run(Script({0: OrderIntent.market("AAA", 100), 3: OrderIntent.market("AAA", -100)}), data, cfg)
    buy, sell = out.fills
    o1, o4 = data.fields["open"]["AAA"].iloc[1], data.fields["open"]["AAA"].iloc[4]
    assert buy.price == pytest.approx(o1 * (1 + 13e-4))                    # 10 bps slippage + 3 bps half spread
    assert sell.price == pytest.approx(o4 * (1 - 13e-4))
    assert buy.commission == pytest.approx(1.0 + 0.01 * 100 + 100 * buy.price * 2e-4)
    assert buy.slippage_cost == pytest.approx(100 * (buy.price - o1))
    flat = out.days[-1]
    expected = 100_000 - 100 * buy.price - buy.commission + 100 * sell.price - sell.commission
    assert flat.equity == pytest.approx(expected) and flat.cash == pytest.approx(expected)
    tiny = ExecutionConfig(commission=dict(per_share=0.0001, minimum=2.0))
    out2, _ = run(Script({0: OrderIntent.market("AAA", 1)}), data, tiny)
    assert out2.fills[0].commission == 2.0


def test_hl_range_spread_uses_the_previous_bar(data):
    cfg = ExecutionConfig(spread=dict(model="hl_range", hl_fraction=0.5))
    out, _ = run(Script({4: OrderIntent.market("AAA", 10)}), data, cfg)
    h, l, c = (data.fields[f]["AAA"].iloc[4] for f in ("high", "low", "close"))
    half = 0.5 * (h - l) / c / 2
    assert out.fills[0].price == pytest.approx(data.fields["open"]["AAA"].iloc[5] * (1 + half))


class RandomTrader(Strategy):
    id = "random_trader"
    version = "t"

    def on_bar(self, ctx):
        out = []
        for s in ctx.universe:
            if ctx.rng.random() < 0.15 and ctx.market.has_bar(s):
                q = float(ctx.rng.integers(-20, 30))
                if q:
                    out.append(OrderIntent.market(s, q))
        return out


def test_accounting_invariants_hold_every_day():
    d = make_data(250, gaps={"BBB": [40, 41, 120]})
    cfg = ExecutionConfig(slippage_bps=3, commission=dict(per_share=0.005, minimum=1))
    out, r = run(RandomTrader(), d, cfg, capital=50_000)
    assert len(out.fills) > 50
    by_day = {}
    for h in out.holdings:
        by_day.setdefault(h["timestamp"], []).append(h)
    for day in out.days:
        mv = sum(h["market_value"] for h in by_day.get(day.timestamp, []))
        assert day.equity == pytest.approx(day.cash + mv, rel=1e-12, abs=1e-6)
    # cash reconciles to the fills
    cash = 50_000 - sum(f.quantity * f.price + f.commission for f in out.fills)
    assert out.days[-1].cash == pytest.approx(cash)
    # positions reconcile to the fills
    pos = {}
    for f in out.fills:
        pos[f.symbol] = pos.get(f.symbol, 0) + f.quantity
    assert {k: v for k, v in pos.items() if v} == {s: p.quantity for s, p in r.portfolio.positions.items()}
    assert sum(x.stale_marks for x in out.days) > 0                       # BBB's gaps kept the last mark


def test_target_weights_rebalance_and_leverage_guard(data):
    s = Script({0: TargetWeights({"AAA": 0.5, "BBB": 0.5}), 10: TargetWeights({"CCC": 1.0})})
    out, r = run(s, data, ExecutionConfig(), end=data.index[15])
    assert set(r.portfolio.positions) == {"CCC"}
    assert r.portfolio.snapshot("x").weight("CCC") == pytest.approx(1.0, abs=0.02)
    with pytest.raises(StrategyError, match="max_gross_leverage"):
        run(Script({0: TargetWeights({"AAA": 0.8, "BBB": 0.8})}), data)


def test_avg_cost_realized_pnl_and_flip():
    from strategy_lab.core.types import Fill
    from strategy_lab.engine.portfolio import Portfolio
    p = Portfolio(10_000, ("X",))
    mk = lambda q, px, i: Fill(f"f{i}", "o", "X", q, px, px, 0.0, 0.0, "2020-01-01")
    p.apply_fill(mk(10, 100, 1))
    p.apply_fill(mk(10, 110, 2))
    assert p.positions["X"].avg_cost == pytest.approx(105)
    assert p.apply_fill(mk(-5, 120, 3)) == pytest.approx(75)
    assert p.positions["X"].avg_cost == pytest.approx(105)
    p2 = Portfolio(10_000, ("X",))
    p2.apply_fill(mk(10, 100, 1))
    assert p2.apply_fill(mk(-15, 90, 2)) == pytest.approx(-100)            # flips short 5 at 90
    assert p2.positions["X"].quantity == -5 and p2.positions["X"].avg_cost == 90


def test_bad_inputs_fail_loudly(data):
    with pytest.raises(ValueError):
        OrderIntent("AAA", 0)
    with pytest.raises(ValueError):
        OrderIntent.limit("AAA", 1, -5)
    with pytest.raises(ValueError, match="no sessions"):
        StrategyRunner(Script(), data, ExecutionConfig(), 1000, pd.Timestamp("1990-01-01"), pd.Timestamp("1990-02-01"))
    no_volume = make_data(20, with_volume=False)
    with pytest.raises(StrategyError, match="needs fields"):
        StrategyRunner(Script(), no_volume, ExecutionConfig(participation_cap=0.1), 1000, no_volume.index[0],
                       no_volume.index[-1])
    with pytest.raises(ValueError):
        ExecutionConfig(spread=dict(model="fixed_bps", bps=0))


def test_runs_are_deterministic(data):
    a, _ = run(RandomTrader(), data)
    b, _ = run(RandomTrader(), data)
    assert [f.to_row() for f in a.fills] == [f.to_row() for f in b.fills]
    assert [d.equity for d in a.days] == [d.equity for d in b.days]
    c, _ = run(RandomTrader(), data, start=data.index[0])
    assert np.allclose([d.equity for d in a.days], [d.equity for d in c.days])
