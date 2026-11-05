"""Entry-Exit Research: the shared fill model, attached exits, the causal precompute hook, the exit library
(crafted bars, ratchets, same-bar priority, no look-ahead), the trade replay against the event-driven engine, the
pairing engine (identical events across exits, random-entry calibration and power, BH, walk-forward), the store's
ledgers and reproduction, Strategy Library imports, the forward gate and the API."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from conftest import make_data, wait_done, write_settings
from strategy_lab.core.market import MarketData, MarketView
from strategy_lab.core.strategy import Strategy
from strategy_lab.core.types import AttachedExit, LookaheadError, OrderIntent, OrderType
from strategy_lab.engine import fills as F
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.runner import StrategyRunner, verify_precompute
from strategy_lab.entryexit.components import Common, default_exit_registry
from strategy_lab.entryexit.config import EntryExitExperimentConfig
from strategy_lab.entryexit.plan import ExitPlanSpec, PlanRuntime, resolve_plan, single
from strategy_lab.entryexit.replay import Bars, Costs, replay
from strategy_lab.signals.base import Family, SignalDef
from strategy_lab.signals.indicators import Indicators, Panel
from strategy_lab.signals.library import build_registry
from strategy_lab.strategies.entry_exit import EntryExitDefinition, make_entry_exit_strategy


# ---------------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------------
def bars_frame(rows: list[tuple], start="2020-01-02", sym="AAA") -> MarketData:
    """MarketData from explicit (open, high, low, close) rows for one symbol (volume constant)."""
    idx = pd.bdate_range(start, periods=len(rows))
    a = np.array(rows, dtype=float)
    f = {k: pd.DataFrame({sym: a[:, i]}, index=idx) for i, k in enumerate(("open", "high", "low", "close"))}
    f["volume"] = pd.DataFrame({sym: np.full(len(rows), 1e6)}, index=idx)
    for x in f.values():
        x.index.name = "date"
    return MarketData(f)


def run_plan(md: MarketData, comps: list[dict], events: list[tuple[int, int]], max_hold=60, ex=None, **plan_kw):
    """Replays events [(t, j)] with a plan and also runs them through the event-driven engine; returns both."""
    ex = ex or ExecutionConfig(fractional_shares=True)
    spec = ExitPlanSpec(components=comps, **plan_kw)
    plan = resolve_plan(spec)
    rt = PlanRuntime(plan, Common(14, 2.0), max_hold=max_hold)
    ind = Indicators(Panel(dict(md.fields)))
    ser = {k: v.to_numpy(float) for k, v in rt.series(ind).items()}
    bars = Bars(*(md.fields[f].to_numpy(float) for f in ("open", "high", "low", "close")))
    t = np.array([e[0] for e in events])
    j = np.array([e[1] for e in events])
    tr = replay(rt, bars, ser, t, j, np.full(len(t), len(md.index) - 1), Costs(ex, 10_000))
    mask = pd.DataFrame(0.0, index=md.index, columns=list(md.symbols))
    for a, b in events:
        mask.iloc[a, b] = 1.0
    d = EntryExitDefinition(entry=None, exit=spec, max_hold=max_hold, max_positions=50,
                            sizing={"mode": "weight", "position_weight": 0.02})
    cls = make_entry_exit_strategy(d, "ee_test")
    out = StrategyRunner(cls({}).inject({"entry": mask}), md, ex, 1_000_000, md.index[min(t)], md.index[-1]).run()
    return tr, out, rt


def engine_positions(out) -> pd.DataFrame:
    fills = pd.DataFrame([f.to_row() for f in out.fills])
    rows = []
    for sym, g in fills.groupby("symbol", sort=False):
        q, cur = 0.0, None
        for f in g.itertuples():
            if f.quantity > 0 and q == 0:
                cur = dict(sym=sym, entry=f.timestamp, P=f.price, val=0.0, q0=f.quantity)
            q += f.quantity
            if f.quantity < 0:
                cur["val"] += -f.quantity * f.price
                if abs(q) < 1e-9:
                    rows.append(dict(cur, exit=f.timestamp, X=cur["val"] / cur["q0"]))
                    q = 0.0
    return pd.DataFrame(rows, columns=["sym", "entry", "P", "val", "q0", "exit", "X"]).astype(
        {"sym": object, "entry": object, "exit": object})


def compare(md, tr, out) -> pd.DataFrame:
    eng = engine_positions(out)
    idx = md.index
    r = tr[(tr.status == "ok") & (tr.reason != "period_end")].copy()
    r["sym"] = [md.symbols[j] for j in r["j"]]
    r["entry"] = [idx[e].date().isoformat() for e in r["entry_bar"]]
    r["exit"] = [idx[e].date().isoformat() for e in r["exit_bar"]]
    r = r.astype({"sym": object, "entry": object, "exit": object})
    m = r.merge(eng, on=["sym", "entry"], how="outer", suffixes=("_r", "_e"), indicator=True)
    if m.empty:
        return m
    m = m.astype({c: float for c in ("entry_price", "P", "exit_price", "X")})
    return m[(m["_merge"] != "both") | (m["exit_r"] != m["exit_e"]) | ~np.isclose(m["entry_price"], m["P"])
             | ~np.isclose(m["exit_price"], m["X"])]


# ---------------------------------------------------------------------------------------------------------------------
# engine: fills, attached exits, precompute
# ---------------------------------------------------------------------------------------------------------------------
def _old_reference(kind, buy, op, hi, lo, lvl):
    if kind == "limit":
        if buy:
            return op if op <= lvl else (lvl if lo <= lvl else None)
        return op if op >= lvl else (lvl if hi >= lvl else None)
    if buy:
        return op if op >= lvl else (lvl if hi >= lvl else None)
    return op if op <= lvl else (lvl if lo <= lvl else None)


def test_fill_rules_match_the_previous_broker_logic_vectorized():
    rng = np.random.default_rng(0)
    for _ in range(3000):
        op = rng.uniform(90, 110)
        hi, lo = op + rng.uniform(0, 8), op - rng.uniform(0, 8)
        lvl = rng.uniform(80, 120)
        for kind in ("limit", "stop"):
            for buy in (True, False):
                want = _old_reference(kind, buy, op, hi, lo, lvl)
                got = F.scalar(F.reference_price(kind, buy, op, hi, lo, price=lvl))
                assert (want is None and got is None) or abs(want - got) < 1e-12
    # vectorized form equals the scalar one elementwise
    op = rng.uniform(90, 110, 500)
    hi, lo, lvl = op + 3, op - 3, rng.uniform(85, 115, 500)
    v = F.reference_price("stop", False, op, hi, lo, price=lvl)
    s = np.array([np.nan if (x := _old_reference("stop", False, a, b, c, d)) is None else x
                  for a, b, c, d in zip(op, hi, lo, lvl)])
    assert np.array_equal(np.isnan(v), np.isnan(s)) and np.allclose(np.nan_to_num(v), np.nan_to_num(s))
    cfg = ExecutionConfig(commission={"per_order": 1.0, "per_share": 0.005, "bps": 2.0, "minimum": 1.5})
    for q, px in ((10, 50.0), (1000, 12.3), (-300, 80.0)):
        c = cfg.commission
        q_ = abs(q)
        want = max(c.per_order + c.per_share * q_ + q_ * px * c.bps / 1e4, c.minimum)
        assert abs(cfg.commission.cost(q, px) - want) < 1e-12


class _Bracket(Strategy):
    id = "t_bracket"
    version = "1"
    order_types = frozenset({"market", "stop", "limit"})
    required_fields = frozenset({"open", "high", "low", "close"})
    legs: tuple = ()

    def on_bar(self, ctx):
        if ctx.execution.session_index == 0:
            return [OrderIntent.market("AAA", 100, tag="entry", attached=self.legs)]
        return None


def _bracket(rows, legs, timing="next_open"):
    md = bars_frame(rows)
    s = _Bracket()
    s.legs = tuple(legs)
    out = StrategyRunner(s, md, ExecutionConfig(fill_timing=timing), 1e6, md.index[0], md.index[-1]).run()
    return [(f.timestamp, f.quantity, round(f.price, 6), f.reason) for f in out.fills], out


def test_attached_stop_and_target_on_the_entry_session_stop_first():
    stop = AttachedExit(OrderType.STOP, 1.0, 1.0, -5.0, "stop")
    tgt = AttachedExit(OrderType.LIMIT, 1.0, 1.0, 5.0, "target:1")
    # entry at 100 (open of session 1); its low reaches 94 and its high 106: both legs reached -> the stop wins
    f, _ = _bracket([(99, 100, 98, 99), (100, 106, 94, 101), (101, 102, 100, 101)], [stop, tgt])
    assert f[0][:3] == ("2020-01-03", 100, 100.0) and f[1][1:3] == (-100, 95.0) and len(f) == 2
    # only the target is reached on the entry session
    f, _ = _bracket([(99, 100, 98, 99), (100, 106, 99, 101), (101, 102, 100, 101)], [stop, tgt])
    assert f[1][1:3] == (-100, 105.0)
    # neither reached: both legs expire at the entry session's close (the strategy places its own orders later)
    f, out = _bracket([(99, 100, 98, 99), (100, 101, 99, 100), (100, 120, 80, 100)], [stop, tgt])
    assert len(f) == 1 and sum(1 for o in out.orders if o.parent_id) == 2
    assert all(o.status.value == "expired" for o in out.orders if o.parent_id)
    # a stop at or above the entry price (absolute level) fills at the open
    f, _ = _bracket([(99, 100, 98, 99), (100, 101, 99, 100), (100, 101, 99, 100)],
                    [AttachedExit(OrderType.STOP, 1.0, 0.0, 102.0, "stop")])
    assert f[1][1:3] == (-100, 100.0)
    # a partial target sells its fraction; next_close timing drops the legs
    f, _ = _bracket([(99, 100, 98, 99), (100, 106, 99, 101), (101, 102, 100, 101)],
                    [AttachedExit(OrderType.LIMIT, 0.5, 1.0, 5.0, "target:0")])
    assert f[1][1:3] == (-50, 105.0)
    f, out = _bracket([(99, 100, 98, 99), (100, 106, 94, 101), (101, 102, 100, 101)], [stop], timing="next_close")
    assert len(f) == 1 and any("dropped" in e.detail for e in out.events)


class _Pre(Strategy):
    id = "t_pre"
    version = "1"
    shift = 0

    def precompute(self, data):
        c = data.fields["close"]
        return {"x": c.shift(self.shift).rolling(3).mean()}

    def on_bar(self, ctx):
        d = ctx.market.derived("x")
        assert d.index[-1] == ctx.timestamp
        assert ctx.market.derived_now("x").shape == (len(ctx.market.symbols),)
        return None


def test_precompute_is_clipped_and_checked_for_lookahead(data):
    s = _Pre()
    StrategyRunner(s, data, ExecutionConfig(), 1e5, data.index[50], data.index[-1]).run()
    assert verify_precompute(lambda: _Pre(), data, cuts=3)["checked"]

    class Leaky(_Pre):
        id = "t_leak"
        shift = -1

    with pytest.raises(LookaheadError):
        verify_precompute(lambda: Leaky(), data, cuts=3)


# ---------------------------------------------------------------------------------------------------------------------
# exit library
# ---------------------------------------------------------------------------------------------------------------------
def test_catalog_covers_every_requested_category():
    reg = default_exit_registry()
    ids = {d.id for d in reg.all()}
    need = {"rsi_overbought", "rsi_cross_below", "macd_bearish_cross", "ma_bearish_cross", "close_below_sma",
            "close_below_ema", "bb_upper_rejection", "momentum_deterioration", "rs_deterioration",
            "volatility_shock", "target_1r", "target_1_5r", "target_2r", "target_3r", "target_4r", "target_5r",
            "target_pct", "target_atr", "target_level", "stop_pct", "stop_dollar", "stop_atr", "stop_swing_low",
            "stop_candle_low", "stop_ma", "stop_volatility", "stop_structure", "trail_pct", "trail_atr",
            "chandelier", "trail_sma", "trail_ema", "trail_prior_low", "trail_swing_low", "donchian_exit",
            "profit_lock", "first_bearish_after_momentum", "consecutive_bearish", "loss_of_momentum", "no_new_high",
            "lower_high", "structure_break", "resistance_reversal", "failed_breakout", "failed_continuation",
            "bearish_reversal_pattern", "time_1d", "time_5d", "time_10d", "time_20d", "time_60d", "time_exit",
            "thesis_expiry", "signal_exit",
            # named in the 0RRXFUA-72M video
            "death_cross", "close_below_sma200", "supertrend_exit", "bb_upper_close", "stoch_overbought",
            "breakdown_20d_low", "breakdown_52w_low", "keltner_breakdown", "placebo_random_exit",
            "oracle_next_day_drop"}
    assert need <= ids
    d = reg.describe()
    assert {f["id"] for f in d["families"]} == {"indicator", "target", "stop", "trailing", "price_action", "time",
                                                "calibration"}
    styles = {e["id"]: e["style"] for e in d["exits"]}
    assert styles["rsi_cross_below"] == styles["bb_upper_close"] == styles["stoch_overbought"] == "strength"
    assert styles["death_cross"] == styles["breakdown_20d_low"] == styles["keltner_breakdown"] == "weakness"
    assert [e["id"] for e in d["exits"] if e["lookahead"]] == ["oracle_next_day_drop"]


@pytest.mark.parametrize("exit_id", [d.id for d in default_exit_registry().all()
                                     if d.id != "signal_exit" and not d.lookahead])
def test_no_exit_component_looks_ahead(exit_id):
    """Poison every bar after a cut: the component's series and the trades that close before the cut stay the
    same."""
    md = make_data(n=320, symbols=("AAA", "BBB", "CCC"), seed=5)
    cut = 230
    poisoned = {k: v.copy() for k, v in md.fields.items()}
    rng = np.random.default_rng(1)
    for k, v in poisoned.items():
        v.iloc[cut + 1:] = v.iloc[cut + 1:].to_numpy() * rng.uniform(0.5, 1.5, v.iloc[cut + 1:].shape)
    hi = np.maximum.reduce([poisoned[k].to_numpy() for k in ("open", "high", "low", "close")])
    lo = np.minimum.reduce([poisoned[k].to_numpy() for k in ("open", "high", "low", "close")])
    poisoned["high"].iloc[:, :] = hi
    poisoned["low"].iloc[:, :] = lo
    spec = single(exit_id)
    plan = resolve_plan(spec)
    rt = PlanRuntime(plan, Common(14, 2.0), max_hold=40)
    bench = md.fields["close"]["AAA"]
    a = rt.series(Indicators(Panel(dict(md.fields), benchmark=bench, benchmark_symbol="AAA")))
    b = rt.series(Indicators(Panel(poisoned, benchmark=poisoned["close"]["AAA"], benchmark_symbol="AAA")))
    for k in a:
        x, y = a[k].to_numpy(float)[: cut + 1], b[k].to_numpy(float)[: cut + 1]
        assert np.array_equal(np.isnan(x), np.isnan(y)) and np.allclose(np.nan_to_num(x), np.nan_to_num(y)), k
    ev = [(t, j) for t in range(150, 200, 9) for j in range(3)]
    t = np.array([e[0] for e in ev])
    j = np.array([e[1] for e in ev])
    res = []
    for f, s in ((md.fields, a), (poisoned, b)):
        bars = Bars(*(f[k].to_numpy(float) for k in ("open", "high", "low", "close")))
        res.append(replay(rt, bars, {k: v.to_numpy(float) for k, v in s.items()}, t, j, np.full(len(t), 319),
                          Costs(ExecutionConfig(), 10_000)))
    done = (res[0]["exit_bar"] <= cut) & (res[0]["status"] == "ok")
    assert done.any()
    for col in ("exit_bar", "ret", "reason"):
        assert res[0].loc[done, col].tolist() == res[1].loc[done, col].tolist(), (exit_id, col)


def test_fixed_levels_targets_and_time_exit_on_crafted_bars():
    flat = [(100, 101, 99, 100), (100, 101, 99, 100.5)] * 15      # closes move: the close-only reference risk is > 0
    rows = flat + [(100, 101, 99, 100), (100, 101, 99, 100), (100, 103, 99, 102), (102, 111, 101, 110),
                   (110, 111, 109, 110)] + [(110, 111, 109, 110)] * 30
    md = bars_frame(rows)
    # stop_pct 5% and target_pct 10% from the entry at the open of 31 (100): the target 110 fills on session 33
    tr, out, _ = run_plan(md, [{"id": "stop_pct", "params": {"pct": 0.05}},
                               {"id": "target_pct", "params": {"pct": 0.10}}], [(30, 0)])
    r = tr.iloc[0]
    assert r.entry_price == 100 and r.exit_bar == 33 and abs(r.exit_price - 110) < 1e-9 and r.reason.startswith(
        "target")
    assert abs(r.risk - 5.0) < 1e-9 and abs(r.r_mult - 2.0) < 1e-9 and compare(md, tr, out).empty
    # a fixed holding period of 5 sessions: exit at the open of entry + 5
    tr, out, _ = run_plan(md, [{"id": "time_5d"}], [(30, 0)])
    assert tr.iloc[0].exit_bar == 36 and tr.iloc[0].reason == "condition:time_5d" and compare(md, tr, out).empty
    # max_hold caps every plan
    tr, _, _ = run_plan(md, [{"id": "rsi_overbought", "params": {"level": 99}}], [(30, 0)], max_hold=7)
    assert tr.iloc[0].exit_bar == 38 and tr.iloc[0].reason == "max_hold"


def test_same_bar_stop_and_target_take_the_stop_and_gaps_fill_at_the_open():
    rows = [(100, 101, 99, 100)] * 30 + [(100, 101, 99, 100), (100, 101, 99, 100), (100, 112, 90, 100),
                                         (100, 101, 99, 100)] + [(100, 101, 99, 100)] * 5
    md = bars_frame(rows)
    tr, out, _ = run_plan(md, [{"id": "stop_pct", "params": {"pct": 0.05}},
                               {"id": "target_pct", "params": {"pct": 0.10}}], [(30, 0)])
    assert tr.iloc[0].exit_bar == 32 and tr.iloc[0].exit_price == 95 and tr.iloc[0].reason == "stop:stop_pct"
    assert compare(md, tr, out).empty
    rows = [(100, 101, 99, 100)] * 31 + [(100, 101, 99, 100), (90, 91, 88, 90)] + [(90, 91, 89, 90)] * 5
    md = bars_frame(rows)
    tr, out, _ = run_plan(md, [{"id": "stop_pct", "params": {"pct": 0.05}}], [(30, 0)])
    assert tr.iloc[0].exit_bar == 32 and tr.iloc[0].exit_price == 90 and compare(md, tr, out).empty


def test_trailing_stops_never_move_down_and_breakeven_after_a_partial():
    md = make_data(n=400, symbols=("AAA", "BBB"), seed=9)
    spec = ExitPlanSpec(components=[{"id": "chandelier"}, {"id": "trail_atr", "params": {"basis": "high"}}])
    rt = PlanRuntime(resolve_plan(spec), Common(14, 2.0), max_hold=200)
    ser = {k: v.to_numpy(float) for k, v in rt.series(Indicators(Panel(dict(md.fields)))).items()}
    B = rt.new_batch(1)
    from strategy_lab.entryexit.components import Ctx
    cur = {"i": 100}

    def g(key, ix):
        a = md.fields[key].to_numpy(float) if key in ("open", "high", "low", "close") else ser[key]
        return a[cur["i"], np.zeros(len(ix), int)]

    ix = np.array([0])
    rt.at_signal(Ctx(g, ix), B, ix)
    rt.at_fill(B, ix, np.array([md.fields["open"].iloc[101, 0]]))
    levels = []
    for i in range(101, 300):
        cur["i"] = i
        B["bars"][0] = i - 101
        rt.observe(Ctx(g, ix), B, ix)
        levels.append([B["c0.level"][0], B["c1.level"][0]])
    lv = np.array(levels)
    assert np.all(np.diff(lv[:, 0]) >= -1e-12) and np.all(np.diff(lv[:, 1]) >= -1e-12)
    # partial target at 1R then breakeven: after the tranche the stop floor is the entry price
    rows = [(100, 101, 99, 100)] * 30 + [(100, 101, 99, 100), (100, 101, 99, 100), (100, 106, 99, 105),
                                         (105, 106, 99.5, 101), (101, 101, 98, 99)] + [(99, 100, 98, 99)] * 5
    md = bars_frame(rows)
    tr, out, rt = run_plan(md, [{"id": "target_1r", "params": {"fraction": 0.5, "then": "breakeven"}},
                                {"id": "stop_pct", "params": {"pct": 0.05}}], [(30, 0)])
    r = tr.iloc[0]
    # tranche 50% at 105 on session 32; floor = 100 from session 33: low 99.5 there -> the rest at 100
    assert r.tranches == 1 and r.exit_bar == 33 and abs(r.exit_price - (0.5 * 105 + 0.5 * 100)) < 1e-9
    assert r.reason == "stop_floor" and compare(md, tr, out).empty


def test_trailing_levels_arm_only_below_the_price_and_fixed_levels_are_literal():
    # a fall from 120 to 100, then a slow recovery: the 22-session-high Chandelier starts far above the entry
    down = [(120 - i, 121 - i, 119 - i, 120 - i) for i in range(21)]
    rec = [(100 + 0.5 * i, 101 + 0.5 * i, 99.5 + 0.5 * i, 100.4 + 0.5 * i) for i in range(30)]
    rows = [(120, 121, 119, 120), (120, 121, 119, 120.5)] * 15 + down + rec
    md = bars_frame(rows)
    t = 30 + 20
    tr, out, _ = run_plan(md, [{"id": "chandelier", "params": {"n": 22, "k": 3.0}}], [(t, 0)])
    assert tr.iloc[0].status == "ok" and tr.iloc[0].bars > 5 and not tr.iloc[0].stop_breach     # not sold at once
    assert compare(md, tr, out).empty
    tr, out, _ = run_plan(md, [{"id": "stop_ma", "params": {"n": 20}}], [(t, 0)])
    r = tr.iloc[0]
    assert r.exit_bar == t + 1 and r.stop_breach and r.reason == "stop:stop_ma"    # a fixed level above entry
    assert compare(md, tr, out).empty


def test_a_stop_level_at_or_below_zero_is_no_stop_in_both_layers():
    """Regression (reference run, 2026-11-04): entry - 3 ATR below zero on a very volatile stock crashed the
    engine's stop order while the replay ignored it."""
    md = make_data(n=300, symbols=("AAA", "BBB"), seed=2)
    ev = [(t, j) for t in (120, 180) for j in range(2)]
    tr, out, _ = run_plan(md, [{"id": "stop_dollar", "params": {"amount": 5000.0}},
                               {"id": "trail_atr", "params": {"k": 20.0}}, {"id": "time_10d"}], ev)
    assert (tr.status == "ok").all() and (tr.reason == "condition:time_10d").all()
    assert compare(md, tr, out).empty


def test_position_aware_price_action_exits():
    up = [(100 + i, 101 + i, 99.5 + i, 100.8 + i) for i in range(6)]
    base = [(100, 101, 99, 100), (100, 101, 99, 100.5)] * 15     # closes move: the close-only reference risk is > 0
    rows = base + [(100, 101, 99, 100)] + up + [(106, 107, 104, 104.5)] + [(104, 105, 103, 104)] * 10
    md = bars_frame(rows)
    tr, out, _ = run_plan(md, [{"id": "first_bearish_after_momentum", "params": {"n": 3}}], [(30, 0)])
    assert tr.iloc[0].exit_bar == 38 and compare(md, tr, out).empty         # bearish bar 37 -> open of 38
    tr, out, _ = run_plan(md, [{"id": "no_new_high", "params": {"m": 3}}], [(30, 0)])
    assert tr.iloc[0].reason == "condition:no_new_high" and compare(md, tr, out).empty
    tr, out, _ = run_plan(md, [{"id": "consecutive_bearish", "params": {"n": 2}}], [(30, 0)])
    assert compare(md, tr, out).empty


def test_condition_combinations_and_min_bars():
    md = make_data(n=400, symbols=("AAA", "BBB", "CCC"), seed=4)
    ev = [(t, j) for t in (120, 200, 280) for j in range(3)]
    a, _, _ = run_plan(md, [{"id": "close_below_ema", "params": {"n": 10, "mode": "below"}},
                            {"id": "momentum_deterioration", "params": {"n": 5}}], ev, condition_mode="and")
    o, _, _ = run_plan(md, [{"id": "close_below_ema", "params": {"n": 10, "mode": "below"}},
                            {"id": "momentum_deterioration", "params": {"n": 5}}], ev, condition_mode="or")
    assert (a["bars"] >= o["bars"]).all()                    # AND can only exit later than OR
    m, _, _ = run_plan(md, [{"id": "close_below_ema", "params": {"n": 10, "mode": "below"}}], ev, min_bars=5)
    assert (m.loc[m.reason != "max_hold", "bars"] >= 5).all()


@pytest.mark.parametrize("comps", [
    [{"id": "stop_atr"}, {"id": "target_2r"}],
    [{"id": "trail_atr"}],
    [{"id": "chandelier"}, {"id": "time_20d"}],
    [{"id": "target_1r", "params": {"fraction": 0.5, "then": "breakeven"}}, {"id": "stop_pct", "params": {"pct": 0.04}},
     {"id": "trail_ema", "params": {"n": 10}}],
    [{"id": "rsi_overbought", "params": {"level": 60}}],
    [{"id": "trail_sma", "params": {"n": 10}}, {"id": "time_10d"}],
    [{"id": "first_bearish_after_momentum", "params": {"n": 2}}, {"id": "no_new_high", "params": {"m": 5}},
     {"id": "failed_continuation"}, {"id": "stop_swing_low"}],
    [{"id": "profit_lock", "params": {"activate_r": 0.5, "lock_r": 0.2, "trail_r": 1.0}},
     {"id": "stop_atr", "params": {"k": 1.5}}],
    [{"id": "donchian_exit", "params": {"n": 10}}, {"id": "target_atr", "params": {"k": 4}}],
    [{"id": "macd_bearish_cross"}, {"id": "trail_prior_low", "params": {"trigger": "close"}}],
])
def test_replay_matches_the_event_driven_engine(comps):
    """The trade replay and a backtest of the same events agree on entry and exit sessions and prices (missing bars
    included)."""
    md = make_data(n=420, symbols=("AAA", "BBB", "CCC", "DDD"), seed=3, gaps={"CCC": [250, 251, 300]})
    ev = [(t + 5 * j, j) for j in range(4) for t in (120, 220, 320)]
    ex = ExecutionConfig(slippage_bps=5, commission={"bps": 2}, spread={"model": "hl_range", "hl_fraction": 0.1},
                         fractional_shares=True)
    tr, out, _ = run_plan(md, comps, ev, ex=ex)
    assert (tr.status == "ok").sum() >= 10
    bad = compare(md, tr, out)
    assert bad.empty, bad.to_string()


# ---------------------------------------------------------------------------------------------------------------------
# the pairing engine and the store
# ---------------------------------------------------------------------------------------------------------------------
SYMS = [f"S{i:02d}" for i in range(40)]
SYN40 = f"""
[[dataset]]
id = "syn40"
name = "SYNTHETIC 40"
provider = "synthetic"
seed = 11
vol = 0.006
drift = 0.0001
symbols = {json.dumps(SYMS + ["SPYX"])}
start = "2018-01-01"
end = "2025-12-31"
"""
LOOSE = dict(eligibility={"min_price": 0, "min_dollar_volume": 0, "min_history": 60},
             matching={"vol_groups": 4, "min_pool": 3}, benchmarks=["SPYX"], max_hold=40,
             controls={"draws": 6, "date_draws": 3, "permutations": 300}, stats={"bootstrap": 100, "min_trades": 10},
             walk_forward={"min_trades": 10})


def _registry():
    reg = build_registry()
    reg.add_family(Family("test", "Test", "test-only"))

    def rnd(seed):
        def fn(ind, p):
            r = np.random.default_rng(seed).random(ind.close.shape) < 0.02
            return pd.DataFrame(r, index=ind.index, columns=ind.close.columns)
        return fn

    for k in range(6):
        reg.register(SignalDef(id=f"t_random{k}", family="test", name=f"random {k}", direction="bullish",
                               fn=rnd(100 + k), trigger="random"))

    def oracle(ind, p):              # reads the future: a 10-session forward return above 3% (proves power)
        fwd = ind.close.shift(-10) / ind.open.shift(-1) - 1
        return fwd > 0.03
    reg.register(SignalDef(id="t_oracle", family="test", name="oracle", direction="bullish", fn=oracle,
                           trigger="future", fields=("open", "close")))
    return reg


@pytest.fixture
def elab(tmp_path):
    from strategy_lab.lab import default_lab
    lb = default_lab(write_settings(tmp_path, SYN40))
    lb.signals = _registry()
    yield lb
    lb.close()


def ecfg(**kw):
    base = dict(dataset="syn40", universe=SYMS, start="2019-01-02", end="2025-12-31", **LOOSE)
    base.update(kw)
    return EntryExitExperimentConfig(**base)


def test_identical_events_across_exits_and_the_matrix(elab):
    R = elab.entry_exit
    eid = R.create(ecfg(entries=[{"kind": "signal", "id": "t_random0"}, {"kind": "signal", "id": "rsi14_below_30"}],
                        exit_ids=["target_2r", "trail_atr", "time_5d"],
                        protective=[{"id": "stop_atr", "params": {"k": 3.0}}],
                        portfolio={"run": "all", "random_controls": 1, "max_positions": 8}))
    R.execute(eid)
    meta = R.get(eid)
    assert meta["status"] == "done" and meta["n_cells"] == 8          # 2 entries x (3 + baseline time_20d)
    cfg = R.config(eid)
    assert all(any(c.id == "stop_atr" for c in p.components) for p in cfg.exits)
    for ei in range(2):
        sets = []
        for pi in range(4):
            t = R.cell_trades(eid, f"e{ei:03d}_x{pi:03d}", limit=100000)
            sets.append(sorted(zip(t["signal_date"].astype(str), t["symbol"])))
        assert all(s == sets[0] for s in sets) and len(sets[0]) > 20
    m = R.matrix(eid, "train", "excess_matched")
    assert len(m["cells"]) == 2 and len(m["cells"][0]) == 4 and all(c["value"] is not None for c in m["cells"][0])
    ms = R.matrix(eid, "validation", "sharpe")
    assert all(c["has_portfolio"] for row in ms["cells"] for c in row)
    cell = R.cell(eid, "e000_x000")
    assert set(cell["portfolio"]) == {"train", "validation"} and cell["vs_baseline"]
    assert "test" not in cell["partitions"]                              # the 2025 test partition stays locked
    eq = R.cell_equity(eid, "e000_x000", "train")
    assert len(eq["equity"]) > 200 and len(eq["buy_and_hold"]) == len(eq["equity"])
    comp = R.read_json(eid, "comparisons.json")
    assert comp["baseline"]["index"] == 3 and all(r["q"] is None or 0 <= r["q"] <= 1 for r in comp["hold_entry"])
    assert all("q_z" in r and "p_z" in r for r in comp["hold_entry"] if r.get("diff") is not None)
    tests = R.read_json(eid, "tests.json")
    assert tests["m_tests"]["train"] == 8
    # write-once: read-only files, append-only usage ledger
    with pytest.raises(PermissionError):
        (R.path(eid) / "config.json").write_text("x")
    with sqlite3.connect(elab.settings.catalog_path) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE ee_data_usage SET n_cells = 0")
    lb = R.leaderboard("pairs", "oos_excess", "validation", 10, eid, min_trades=1)
    assert lb and all(r["experiment_id"] == eid for r in lb)
    assert R.leaderboard("exits", "expectancy", "train", 10, eid, min_trades=1)


def test_random_entries_are_calibrated_and_an_oracle_is_detected(elab):
    R = elab.entry_exit
    eid = R.create(ecfg(entries=[{"kind": "signal", "id": f"t_random{k}"} for k in range(6)] +
                        [{"kind": "signal", "id": "t_oracle"}], exit_ids=["time_10d"], baseline_exit=None,
                        portfolio={"run": "none"}, controls={"draws": 8, "date_draws": 3, "permutations": 400}))
    R.execute(eid)
    tests = [t for t in R.read_json(eid, "tests.json")["tests"] if t["partition"] == "train"]
    rand = [t for t in tests if t["cell"] != "e006_x000"]
    orc = next(t for t in tests if t["cell"] == "e006_x000")
    assert sum(t["p"] < 0.05 for t in rand) <= 1                 # random entries: no excess vs random entries
    assert all(abs(t["excess"]) < 0.01 for t in rand)
    assert orc["p"] < 0.01 and orc["excess"] > 0.01 and orc["verdict"]["category"] == "supported"


def test_recovery_leaves_experiments_of_a_live_process_alone(elab):
    """Regression (2026-11-04): starting the API server marked an experiment that a script was executing as
    interrupted. Recovery now checks the owner process."""
    import os
    import subprocess
    import sys
    R = elab.entry_exit
    eid = R.create(ecfg(entries=[{"kind": "signal", "id": "t_random4"}], exit_ids=["time_5d"], baseline_exit=None,
                        portfolio={"run": "none"}))
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (R.path(eid) / "owner.json").write_text(json.dumps(dict(pid=child.pid, host=__import__("platform").node())))
        R._set(eid, status="running")
        assert R.recover_interrupted() == 0 and R.get(eid)["status"] == "running"
    finally:
        child.kill()
        child.wait()
    assert R.recover_interrupted() == 1 and R.get(eid)["status"] == "failed"
    assert os.getpid() != child.pid


def test_control_replay_chunking_does_not_change_results(elab, monkeypatch):
    from strategy_lab.entryexit import engine as E
    cfg = ecfg(entries=[{"kind": "signal", "id": "t_random3"}], exit_ids=["trail_atr"], baseline_exit=None,
               portfolio={"run": "none"})
    rc, entries, plans = E.resolve_config(cfg, elab)
    es = E.prepare(rc, entries, plans, elab)
    ev = E.entry_events(es, entries[0])
    rt = PlanRuntime(plans[0], E._common(rc), max_hold=rc.max_hold)
    ser = E.plan_series(es, rt)
    a = E.replay_cell(es, rt, ser, ev)
    monkeypatch.setattr(E, "REPLAY_CHUNK", 37)
    b = E.replay_cell(es, rt, ser, ev)
    assert np.array_equal(a[1], b[1], equal_nan=True) and np.array_equal(a[2], b[2], equal_nan=True)
    assert np.isfinite(a[1]).sum() > 100


def test_exit_grid_sensitivity_walk_forward_and_reproduction(elab):
    R = elab.entry_exit
    cfg = ecfg(entries=[{"kind": "signal", "id": "t_random1"}],
               exit_grids=[{"plan": {"components": [{"id": "stop_atr"}, {"id": "target_r"}]}, "component": 1,
                            "grid": {"k": [1.0, 2.0, 3.0]}}], portfolio={"run": "top", "top": 1, "random_controls": 0})
    eid = R.create(cfg)
    R.execute(eid)
    wf = R.read_json(eid, "walk_forward.json")
    assert len(wf["walk_forward"]["folds"]) >= 2
    assert all(f["test"] < "2025" for f in wf["walk_forward"]["folds"])   # never the locked test year
    s = wf["sensitivity"][0]
    assert s["params"] == ["k"] and len(s["points"]) == 3 and s["plateau"] is not None
    runs = [p for p in (R.path(eid) / "portfolio").iterdir()]
    assert len(runs) == 1                                               # run=top, top=1
    rep = R.reproduce(eid)
    assert rep["identical"] == rep["cells"] == 4 and not rep["differing"]


def test_import_backtest_and_forward_gate(elab):
    R = elab.entry_exit
    eid = R.create(ecfg(entries=[{"kind": "signal", "id": "sma20_reclaim_after_downtrend"}],
                        exits=[{"components": [{"id": "trail_sma", "params": {"n": 20}}, {"id": "time_20d"}]}],
                        baseline_exit=None, universe=SYMS[:10], portfolio={"run": "all", "random_controls": 0}))
    R.execute(eid)
    imp = elab.import_entry_exit(eid, "e000_x000")
    assert imp["created"] and elab.import_entry_exit(eid, "e000_x000")["created"] is False
    cls = elab.registry.get(imp["strategy_id"])
    assert cls.category == "entry_exit" and cls.order_types == frozenset({"market"})
    assert cls.provenance["research"]["cell"] == "e000_x000" and "trade_level" in cls.provenance["research"]
    from strategy_lab.paper.spec import DeploymentSpec
    spec = DeploymentSpec(strategy=cls.id, strategy_version=cls.version, universe=SYMS[:10],
                          start_date="2024-01-02")
    elab.forward.mock_dataset = "syn40"                      # before the mock host is first created
    v = elab.forward.validate(spec)
    assert not v["ok"] and any("historical backtest" in p for p in v["problems"])
    from strategy_lab.experiments.config import ExperimentConfig
    run = elab.experiments.create(ExperimentConfig(strategy=cls.id, dataset="syn40", universe=SYMS[:10],
                                                   start="2019-01-02", end="2023-12-29", benchmark="SPYX"))
    elab.experiments.execute(run)
    m = wait_done(elab, run)
    assert m["status"] == "done", m.get("error")
    v = elab.forward.validate(spec)
    assert v["ok"], v["problems"]
    dep = elab.forward.deploy(spec)
    assert dep["book_id"].startswith("sl-")
    from pathlib import Path
    pins = json.loads((Path(dep["bundle_dir"]) / "models" / "strategy_lab" / "PIN.json").read_text(encoding="utf-8"))
    assert any(k.endswith("strategies/entry_exit.py") for k in pins["files"])
    assert any(k.startswith(f"user/{cls.id}") for k in pins["files"])
    assert any("entryexit/plan.py" in k for k in pins["files"])


def test_api_routes(elab):
    from asgi import Client
    from strategy_lab.api.app import create_app
    api = Client(create_app(elab))
    cat = api.get("/api/v1/entry-exit/catalog").json()
    assert cat["n_exits"] >= 55 and {r["id"] for r in cat["roles"]} >= {"entry", "exit_signal", "stop", "target"}
    assert cat["entry_signals"] and cat["exit_signals"]
    body = ecfg(entries=[{"kind": "signal", "id": "t_random2"}], exit_ids=["chandelier"],
                portfolio={"run": "none"}).model_dump(mode="json")
    v = api.post("/api/v1/entry-exit/validate", json=body).json()
    assert v["n_cells"] == 2 and any(p["locked"] for p in v["partitions"])
    bad = dict(body, entries=[{"kind": "signal", "id": "rsi14_above_70"}])
    r = api.post("/api/v1/entry-exit/validate", json=bad)
    assert r.status_code == 422 and "bullish" in r.json()["detail"]
    meta = api.post("/api/v1/entry-exit/experiments", json=body).json()
    eid = meta["experiment_id"]
    elab.entry_exit.futures[eid].result(timeout=300)
    assert api.get(f"/api/v1/entry-exit/experiments/{eid}").json()["meta"]["status"] == "done"
    m = api.get(f"/api/v1/entry-exit/experiments/{eid}/matrix", {"partition": "train", "metric": "win_rate"}).json()
    assert len(m["cells"][0]) == 2
    c = api.get(f"/api/v1/entry-exit/experiments/{eid}/cells/e000_x000").json()
    assert c["partitions"]["train"]["n"] > 0
    t = api.get(f"/api/v1/entry-exit/experiments/{eid}/cells/e000_x000/trades", {"partition": "train"}).json()
    assert t and {"signal_date", "exit_date", "ret", "reason", "matched_mean"} <= set(t[0])
    assert api.get("/api/v1/entry-exit/leaderboards", {"board": "pairs", "role": "train",
                                                       "min_trades": 1}).status_code == 200
    p = api.post("/api/v1/entry-exit/plans/resolve", json={"components": [{"id": "stop_atr"}, {"id": "target_2r"}]})
    assert p.json()["intrabar"] and not p.json()["deployable"]


# ---------------------------------------------------------------------------------------------------------------------
# the random-timing exit benchmark (0RRXFUA-72M): shifts, calibration, random entries
# ---------------------------------------------------------------------------------------------------------------------
def test_circular_shift_keeps_count_and_spacing_and_moves_the_dates():
    from strategy_lab.entryexit.engine import circular_shift
    rng = np.random.default_rng(3)
    a = (rng.random((300, 4)) < 0.05).astype(float)
    lo, hi = np.array([0, 10, 50, 0]), np.array([299, 280, 299, 120])
    for c in range(4):
        a[:lo[c], c] = np.nan
        a[hi[c] + 1:, c] = np.nan
    u = np.array([7, 33, 101, 60])
    b = circular_shift(a, lo, hi, u)
    for c in range(4):
        x, y = a[lo[c]: hi[c] + 1, c], b[lo[c]: hi[c] + 1, c]
        L = hi[c] - lo[c] + 1
        assert np.nansum(x) == np.nansum(y)                                   # same number of signals
        px, py = np.flatnonzero(x > 0), np.flatnonzero(y > 0)
        assert sorted((px + u[c]) % L) == sorted(py)                          # same spacing, moved by u
        assert not np.array_equal(px, py)
        assert np.array_equal(np.isnan(a[:, c]), np.isnan(b[:, c]))           # outside the span untouched


def test_the_oracle_reads_the_future_and_is_refused_by_strategies():
    md = make_data(n=200, symbols=("AAA", "BBB"), seed=6)
    rt = PlanRuntime(resolve_plan(single("oracle_next_day_drop")), Common(14, 2.0))
    a = rt.series(Indicators(Panel(dict(md.fields))))["c0:on"].to_numpy()
    poisoned = {k: v.copy() for k, v in md.fields.items()}
    poisoned["close"].iloc[101:] *= 0.5                                       # a crash right after the cut
    b = rt.series(Indicators(Panel(poisoned)))["c0:on"].to_numpy()
    assert not np.array_equal(a[:101], b[:101])                               # the poison test catches it
    with pytest.raises(ValueError, match="reads the future"):
        make_entry_exit_strategy(EntryExitDefinition(exit=single("oracle_next_day_drop")), "ee_cheat")
    assert not resolve_plan(single("oracle_next_day_drop")).deployable


def test_random_timing_benchmark_scores_placebos_at_zero_and_detects_the_cheating_rule(elab):
    R = elab.entry_exit
    eid = R.create(ecfg(random_entries={"enabled": True, "n": 4000}, exit_ids=["rsi_cross_below", "death_cross"],
                        calibration={"enabled": True}, baseline_exit=None, max_hold=120,
                        controls={"matched": False, "random_dates": False}, portfolio={"run": "none"},
                        timing={"draws": 10}))
    R.execute(eid)
    tj = R.read_json(eid, "timing.json")
    cal = {(t["cell"], t["partition"]): t for t in tj["calibration"]}
    names = [x["name"] for x in R.config(eid).resolved_exits]
    oracle = names.index(next(n for n in names if n.startswith("Cheating")))
    placebo = [i for i, n in enumerate(names) if n.startswith("Placebo")]
    for part in ("train", "validation"):
        o = cal[(f"e000_x{oracle:03d}", part)]
        assert o["excess"] > 0.005 and o["p"] < 0.01                          # the edge is found
        for i in placebo:
            pl = cal[(f"e000_x{i:03d}", part)]
            assert pl["p"] > 0.01, (names[i], part, pl)                       # no edge is invented
    assert len(tj["tests"]) == 4 and all(t["q"] is not None for t in tj["tests"])   # 2 real rules x 2 partitions
    assert {g["style"] for g in tj["style_groups"]} == {"strength", "weakness"}
    m = R.matrix(eid, "train", "timing_excess")
    assert m["cells"][0][0]["value"] is not None and m["exits"][0]["style"] == "strength"
    with pytest.raises(Exception, match="random entries are a benchmark"):
        elab.import_entry_exit(eid, "e000_x000")


def test_random_entries_are_eligible_reproducible_and_shared_by_every_exit(elab):
    from strategy_lab.entryexit import engine as E
    cfg = ecfg(random_entries={"enabled": True, "n": 1500}, exit_ids=["time_5d"], baseline_exit=None,
               portfolio={"run": "none"})
    rc, entries, plans = E.resolve_config(cfg, elab)
    assert entries[-1].key == "random_entries@1.0:1500:0" and rc.entries == []
    es = E.prepare(rc, entries, plans, elab)
    a, b = E.entry_events(es, entries[-1]), E.entry_events(es, entries[-1])
    assert len(a.t) == 1500 and np.array_equal(a.t, b.t) and np.array_equal(a.s, b.s)
    assert es.study.eligible[a.t, a.s].all() and (np.diff(a.t) >= 0).all()
    assert len(set(zip(a.t.tolist(), a.s.tolist()))) == 1500                 # without replacement


def test_api_errors_are_explained_in_words_and_the_timing_route_serves_the_benchmark(elab):
    from asgi import Client
    from strategy_lab.api.app import create_app
    api = Client(create_app(elab))
    body = ecfg(entries=[{"kind": "signal", "id": "t_random2"}], exit_ids=["rsi_cross_below"], baseline_exit=None,
                portfolio={"run": "none"}, controls={"matched": False, "random_dates": False},
                timing={"draws": 4}).model_dump(mode="json")

    def problem(path, b):
        r = api.post(path, json=b)
        assert r.status_code == 422, r.text
        j = r.json()
        assert isinstance(j["detail"], str) and "{" not in j["detail"] and "Traceback" not in j["detail"]
        return j

    # a missing field and an unknown setting: field names in words, one line each
    j = problem("/api/v1/entry-exit/validate", {k: v for k, v in body.items() if k != "dataset"} | {"colour": 1})
    assert len(j["problems"]) == 2
    assert any("is required" in p for p in j["problems"]) and any("is not a setting" in p for p in j["problems"])
    assert all("loc" not in p and "type=" not in p for p in j["problems"])
    # nothing to test
    j = problem("/api/v1/entry-exit/validate", dict(body, entries=[]))
    assert "entr" in j["detail"].lower() and j["hint"]
    # an unknown exit and a bad parameter name the exit and say what to do
    j = problem("/api/v1/entry-exit/experiments", dict(body, exit_ids=["no_such_exit"]))
    assert "no_such_exit" in j["detail"] and j["detail"].startswith("The experiment could not be created")
    bad_param = dict(body, exit_ids=[], exits=[{"components": [{"id": "rsi_cross_below", "params": {"level": 400}}]}])
    j = problem("/api/v1/entry-exit/validate", bad_param)
    assert "rsi_cross_below" in j["detail"] and "level" in j["detail"]
    # past the data ceiling: HOLDOUT is explained, not just refused
    j = problem("/api/v1/entry-exit/validate", dict(body, end="2026-03-01"))
    assert "2025-12-31" in j["detail"] + " ".join(j["problems"]) + (j["hint"] or "")
    # symbols that are not in the dataset are named before anything runs
    j = problem("/api/v1/entry-exit/validate", dict(body, universe=[SYMS[0], "ZZQX1", "ZZQX2"]))
    assert "ZZQX1, ZZQX2" in j["detail"] and "2 of the 3 symbols" in j["detail"] and "exist" in j["hint"]
    # a cheating rule cannot be imported, in words
    meta = api.post("/api/v1/entry-exit/experiments", json=dict(body, calibration={"enabled": True})).json()
    eid = meta["experiment_id"]
    elab.entry_exit.futures[eid].result(timeout=300)
    tj = api.get(f"/api/v1/entry-exit/experiments/{eid}/timing").json()
    assert tj["tests"] and tj["calibration"] and tj["draws"] == 4 and tj["method"]
    names = [x["name"] for x in elab.entry_exit.config(eid).resolved_exits]
    cheat = names.index(next(n for n in names if n.startswith("Cheating")))
    r = api.post("/api/v1/entry-exit/import", json={"experiment_id": eid, "cell": f"e000_x{cheat:03d}"})
    assert r.status_code == 422 and r.json()["detail"].startswith("This cell cannot be imported")
    c = api.get(f"/api/v1/entry-exit/experiments/{eid}/cells/e000_x000").json()
    assert c["timing_tests"] and c["partitions"]["train"]["timing"]["excess"] is not None
    r = api.get("/api/v1/entry-exit/experiments/nope/timing")
    assert r.status_code == 404 and "nope" in r.json()["detail"]
