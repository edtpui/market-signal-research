"""Signal Research engine: timing and forward returns, censoring and partitions, missing data, corporate actions,
point-in-time eligibility and volatility groups, matched-random and random-date controls, reproducible sampling,
the randomization test's calibration and power, statistics, the store's ledgers, cancellation and error isolation,
and imports into the Strategy Library (backtest and forward deployment)."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from conftest import check_live_owner_is_left_alone, wait_done, write_settings
from strategy_lab.core.strategy import Param
from strategy_lab.research import engine as E
from strategy_lab.research import stats as st
from strategy_lab.research.config import SignalExperimentConfig
from strategy_lab.research.engine import ResearchError, prepare, resolve_config, run_signal, sample_events
from strategy_lab.research.reference import ReferenceData, sic_to_sector
from strategy_lab.signals.base import Family, SignalDef, SignalSpec
from strategy_lab.signals.library import build_registry

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
             matching={"vol_groups": 4, "min_pool": 3}, benchmarks=["SPYX"], permutations=200,
             stats={"bootstrap": 100, "min_events": 10}, sector_benchmark=False)


@pytest.fixture
def rlab(tmp_path):
    from strategy_lab.lab import default_lab
    lb = default_lab(write_settings(tmp_path, SYN40))
    lb.signals = registry_with_tests()
    yield lb
    lb.close()


def registry_with_tests():
    """The catalog plus test-only signals: fixed event lists, a random mask, oracles that read the future (to prove
    the controls detect real information), and one that raises."""
    reg = build_registry()
    reg.add_family(Family("test", "Test", "test-only signals"))

    def at(dates_syms):
        def fn(ind, p):
            m = pd.DataFrame(False, index=ind.index, columns=ind.close.columns)
            for d, s in dates_syms:
                if pd.Timestamp(d) in m.index and s in m.columns:
                    m.loc[pd.Timestamp(d), s] = True
            return m
        return fn

    reg.register(SignalDef(id="t_fixed", family="test", name="fixed", direction="bullish", trigger="fixed",
                           fn=at([("2019-06-03", "S05"), ("2019-06-03", "S06"), ("2020-02-03", "S07"),
                                  ("2022-12-28", "S08"), ("2021-05-03", "S09")])))
    reg.register(SignalDef(id="t_random", family="test", name="random", direction="bullish", trigger="random",
                           fn=lambda ind, p: pd.DataFrame(np.random.default_rng(int(p["seed"])).random(ind.close.shape)
                                                          < 0.02, index=ind.index, columns=ind.close.columns),
                           params={"seed": Param(0, "int", 0, 10_000)}))
    fut = lambda ind: ind.open.shift(-21) / ind.open.shift(-1) - 1           # deliberately reads the future
    reg.register(SignalDef(id="t_oracle", family="test", name="oracle", direction="bullish", trigger="future top",
                           fn=lambda ind, p: fut(ind).rank(axis=1, pct=True) > 0.9))
    reg.register(SignalDef(id="t_oracle_bear", family="test", name="oracle bear", direction="bearish",
                           trigger="future bottom", fn=lambda ind, p: fut(ind).rank(axis=1, pct=True) <= 0.1))

    def boom(ind, p):
        raise RuntimeError("boom")
    reg.register(SignalDef(id="t_boom", family="test", name="boom", direction="bullish", trigger="raises", fn=boom))
    return reg


def cfg_(**kw):
    base = dict(dataset="syn40", universe=SYMS, start="2019-01-02", end="2025-12-31", **LOOSE)
    base.update(kw)
    return SignalExperimentConfig(**base)


def study_for(lab, cfg):
    rc, sigs = resolve_config(cfg, lab.store, lab.signals, lab.universes, lab.policy, ReferenceData())
    return rc, sigs, prepare(rc, sigs, lab.store, ReferenceData())


# -- timing, forward returns, partitions --------------------------------------------------------------------------
def test_entry_at_next_open_and_exactly_h_sessions(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="t_fixed")], horizons=[1, 5, 20]))
    out = run_signal(study, sigs[0])
    ev = out.events.set_index(["date", "symbol"])
    md, _ = rlab.store.load("syn40", ["S05"], "2019-05-01", "2019-08-30")
    o = md.fields["open"]["S05"]
    t = list(o.index).index(pd.Timestamp("2019-06-03"))
    row = ev.loc[("2019-06-03", "S05")]
    assert row["entry_date"] == o.index[t + 1].strftime("%Y-%m-%d")
    for h in (1, 5, 20):
        assert np.isclose(row[f"r_{h}"], o.iloc[t + 1 + h] / o.iloc[t + 1] - 1)
    # next_close: the close of t+1 to the close of t+1+h
    rc2, sigs2, study2 = study_for(rlab, cfg_(signals=[SignalSpec(id="t_fixed")], horizons=[5], entry="next_close"))
    r2 = run_signal(study2, sigs2[0]).events.set_index(["date", "symbol"]).loc[("2019-06-03", "S05")]
    c = md.fields["close"]["S05"]
    assert np.isclose(r2["r_5"], c.iloc[t + 6] / c.iloc[t + 1] - 1)


def test_partitions_purge_and_lock_the_test_period(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="t_fixed")], horizons=[5]))
    assert [p.name for p in rc.partitions] == ["train", "validation", "test"]
    assert rc.analysis_end.isoformat() == "2024-12-31"
    assert study.index[-1] <= pd.Timestamp("2024-12-31")            # locked test bars are never loaded
    assert [p["name"] for p in study.parts] == ["train", "validation"]
    out = run_signal(study, sigs[0])
    ev = out.events.set_index(["date", "symbol"])
    # 2022-12-28 is three sessions before the end of train: a 5-session holding period would cross into validation
    assert np.isnan(ev.loc[("2022-12-28", "S08"), "r_5"])
    assert out.summary["counts"]["censored"]["5"] == 1
    # partitions are chronological: every train session precedes every validation session
    tr = np.flatnonzero(study.part == 0)
    va = np.flatnonzero(study.part == 1)
    assert tr.max() < va.min()
    overlapping = cfg_(signals=[SignalSpec(id="t_fixed")], partition_scheme="custom", partitions=[
        dict(name="a", role="train", start="2019-01-02", end="2021-12-31"),
        dict(name="b", role="validation", start="2021-06-01", end="2022-12-31")])
    with pytest.raises(ResearchError):
        resolve_config(overlapping, rlab.store, rlab.signals, rlab.universes, rlab.policy, ReferenceData())
    test_first = cfg_(signals=[SignalSpec(id="t_fixed")], partition_scheme="custom", evaluate_test=True, partitions=[
        dict(name="a", role="test", start="2019-01-02", end="2020-12-31"),
        dict(name="b", role="train", start="2021-01-04", end="2022-12-30")])
    with pytest.raises(ResearchError):
        resolve_config(test_first, rlab.store, rlab.signals, rlab.universes, rlab.policy, ReferenceData())
    with pytest.raises(ResearchError):
        resolve_config(cfg_(signals=[SignalSpec(id="t_fixed")], start="2025-02-03"), rlab.store, rlab.signals,
                       rlab.universes, rlab.policy, ReferenceData())


def test_missing_bars_are_excluded_and_counted(lab):
    """SYNE has no bars on 2022-05-10/11: an event on 2022-05-09 has no entry price and is never filled."""
    reg = registry_with_tests()
    reg.register(SignalDef(id="t_syne", family="test", name="syne", direction="bullish", trigger="fixed",
                           fn=lambda ind, p: pd.DataFrame(
                               (ind.index == pd.Timestamp("2022-05-09"))[:, None] & (ind.close.columns == "SYNE")[None, :],
                               index=ind.index, columns=ind.close.columns)))
    lab.signals = reg
    cfg = SignalExperimentConfig(dataset="synthetic", universe=["SYNA", "SYNB", "SYNC", "SYND", "SYNE"],
                                 start="2019-01-02", end="2024-12-31", horizons=[5], signals=[SignalSpec(id="t_syne")],
                                 eligibility={"min_price": 0, "min_dollar_volume": 0, "min_history": 20},
                                 matching={"vol_groups": 2, "min_pool": 1}, benchmarks=["SPYX"], permutations=100,
                                 stats={"bootstrap": 50})
    rc, sigs = resolve_config(cfg, lab.store, lab.signals, lab.universes, lab.policy, ReferenceData())
    study = prepare(rc, sigs, lab.store, ReferenceData())
    out = run_signal(study, sigs[0])
    assert out.summary["counts"]["events"] == 1 and out.summary["counts"]["missing_forward"]["5"] == 1
    assert np.isnan(out.events["r_5"].iloc[0])


def test_returns_are_split_adjusted(lab):
    """SYNB splits 4:1 on 2021-06-01. An event the session before has a smooth adjusted return, not -75%."""
    reg = registry_with_tests()
    reg.register(SignalDef(id="t_synb", family="test", name="synb", direction="bullish", trigger="fixed",
                           fn=lambda ind, p: pd.DataFrame(
                               (ind.index == pd.Timestamp("2021-05-27"))[:, None] & (ind.close.columns == "SYNB")[None, :],
                               index=ind.index, columns=ind.close.columns)))
    lab.signals = reg
    cfg = SignalExperimentConfig(dataset="synthetic", universe=["SYNA", "SYNB", "SYNC", "SYND", "SYNE"],
                                 start="2019-01-02", end="2024-12-31", horizons=[5], signals=[SignalSpec(id="t_synb")],
                                 eligibility={"min_price": 0, "min_dollar_volume": 0, "min_history": 20},
                                 matching={"vol_groups": 2, "min_pool": 1}, benchmarks=["SPYX"], permutations=100,
                                 stats={"bootstrap": 50})
    rc, sigs = resolve_config(cfg, lab.store, lab.signals, lab.universes, lab.policy, ReferenceData())
    out = run_signal(prepare(rc, sigs, lab.store, ReferenceData()), sigs[0])
    r = out.events["r_5"].iloc[0]
    raw, _ = lab.store.load("synthetic", ["SYNB"], "2021-05-01", "2021-06-30", price_mode="raw")
    o = raw.fields["open"]["SYNB"]
    t = list(o.index).index(pd.Timestamp("2021-05-27"))
    raw_ret = o.iloc[t + 6] / o.iloc[t + 1] - 1
    assert raw_ret < -0.6 and abs(r) < 0.2                       # raw prices show the split; adjusted do not


# -- eligibility and matching ---------------------------------------------------------------------------------------
def test_volatility_groups_and_eligibility_use_no_future_data(rlab):
    _, _, a = study_for(rlab, cfg_(signals=[SignalSpec(id="t_fixed")], end="2021-12-31", partition_scheme="none"))
    _, _, b = study_for(rlab, cfg_(signals=[SignalSpec(id="t_fixed")], end="2023-12-29", partition_scheme="none"))
    n = len(a.index)
    assert a.index.equals(b.index[:n])
    assert (a.volb == b.volb[:n]).all() and (a.eligible == b.eligible[:n]).all()
    assert np.allclose(a.vol, b.vol[:n], equal_nan=True)


def test_matched_controls_share_session_and_volatility_group(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[10]))
    out = run_signal(study, sigs[0])
    ev = out.events[out.events["matched"]].head(200)
    pos = {s: i for i, s in enumerate(study.symbols)}
    tix = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(study.index)}
    checked = 0
    for _, row in ev.iterrows():
        t, s = tix[row["date"]], pos[row["symbol"]]
        members = np.flatnonzero(study.eligible[t] & (study.volb[t] == study.volb[t, s]))
        others = [m for m in members if m != s]
        for k in range(1, rc.save_controls + 1):
            c = pos[row[f"ctrl_{k}"]]
            assert c != s and c in others
            checked += 1
        f = study.fr[10][t, others]
        own = study.fr[10][t, s]
        if np.isfinite(own):
            assert np.isclose(row["pool_10"], np.nanmean(f))          # exact leave-one-out pool mean
    assert checked > 100


def test_small_pools_stay_unmatched(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[10],
                                           matching={"vol_groups": 4, "min_pool": 100}))
    out = run_signal(study, sigs[0])
    c = out.summary["counts"]
    assert c["unmatched"] == c["events"] == c["unmatched_small_pool"] > 0
    assert out.summary["partitions"]["train"]["horizons"]["10"]["matched"]["n"] == 0


def test_random_dates_same_symbol_partition_and_regime(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[20],
                                           primary_horizon=20))
    out = run_signal(study, sigs[0])
    ev = out.events.dropna(subset=["rdate_1"]).head(150)
    tix = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(study.index)}
    pos = {s: i for i, s in enumerate(study.symbols)}
    for _, row in ev.iterrows():
        t, s = tix[row["date"]], pos[row["symbol"]]
        for k in range(1, 4):
            d = tix[row[f"rdate_{k}"]]
            assert study.part[d] == study.part[t] and study.eligible[d, s]
            assert study.volb[d, s] == study.volb[t, s]
            assert d + 1 + 20 <= study.parts[study.part[t]]["last"]        # its holding period completes inside


def test_benchmark_excess_uses_the_same_sessions(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="t_fixed")], horizons=[5]))
    ev = run_signal(study, sigs[0]).events.set_index(["date", "symbol"])
    md, _ = rlab.store.load("syn40", ["SPYX"], "2019-05-01", "2019-08-30")
    o = md.fields["open"]["SPYX"]
    t = list(o.index).index(pd.Timestamp("2019-06-03"))
    assert np.isclose(ev.loc[("2019-06-03", "S05"), "b_SPYX_5"], o.iloc[t + 6] / o.iloc[t + 1] - 1)


# -- sampling --------------------------------------------------------------------------------------------------------
def test_event_sampling_modes():
    trig = np.zeros((20, 2), bool)
    trig[[2, 3, 4, 10, 11, 15], 0] = True
    trig[[5, 6], 1] = True
    el = np.ones_like(trig)
    el[10, 0] = False                                           # a run whose first session is ineligible
    t, s, _ = sample_events(trig, el, "all", 0, [5])
    assert list(zip(t, s)) == [(2, 0), (3, 0), (4, 0), (5, 1), (6, 1), (11, 0), (15, 0)]
    t, s, _ = sample_events(trig, el, "first", 0, [5])
    assert list(zip(t, s)) == [(2, 0), (5, 1), (15, 0)]         # the 10-11 run is not shifted to 11
    t, s, _ = sample_events(trig, el, "cooldown", 3, [5])
    assert list(zip(t, s)) == [(2, 0), (5, 1), (11, 0), (15, 0)]
    t, s, use = sample_events(trig, el, "non_overlapping", 0, [1, 5])
    got = {(a, b): (bool(use[1][i]), bool(use[5][i])) for i, (a, b) in enumerate(zip(t, s))}
    assert got[(2, 0)] == (True, True) and got[(3, 0)] == (True, False) and got[(11, 0)] == (True, True)
    assert got[(6, 1)] == (True, False) and got[(15, 0)] == (True, False)


# -- reproducibility ----------------------------------------------------------------------------------------------------
def test_sampling_is_reproducible_and_chunk_independent(rlab, monkeypatch):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[5, 20]))
    a = run_signal(study, sigs[0])
    monkeypatch.setattr(E, "CHUNK_CELLS", 3_000)                  # 15 events per chunk instead of 10,000
    b = run_signal(study, sigs[0])
    assert a.events.equals(b.events)                              # identical draws (saved controls, dates)
    # the null means are sums over chunks: only the floating-point summation order differs
    assert np.allclose(a.nulls.to_numpy(), b.nulls.to_numpy(), rtol=1e-12, atol=1e-15, equal_nan=True)
    again = run_signal(study, sigs[0])
    assert again.nulls.equals(b.nulls)                            # a fixed configuration is exactly reproducible
    rc2, sigs2, study2 = study_for(rlab, cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[5, 20], seed=7))
    c = run_signal(study2, sigs2[0])
    assert not a.nulls.equals(c.nulls)
    assert a.events["r_20"].equals(c.events["r_20"])              # only the random draws depend on the seed


def test_streams_are_independent_of_batch_composition(rlab):
    rc, sigs, study = study_for(rlab, cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[5]))
    rc2, sigs2, study2 = study_for(rlab, cfg_(signals=[SignalSpec(id="gap_up"), SignalSpec(id="rsi14_below_30")],
                                              horizons=[5]))
    a = run_signal(study, sigs[0])
    b = run_signal(study2, [s for s in sigs2 if s.id == "rsi14_below_30"][0])
    assert a.nulls.equals(b.nulls)


# -- the randomization test: calibration and power -----------------------------------------------------------------------
def test_random_signals_are_not_significant_and_oracles_are(rlab):
    specs = [SignalSpec(id="t_random", params={"seed": k}) for k in range(12)]
    rc, sigs, study = study_for(rlab, cfg_(signals=specs + [SignalSpec(id="t_oracle"), SignalSpec(id="t_oracle_bear")],
                                           horizons=[20], partition_scheme="none", permutations=300))
    outs = {s.key: run_signal(study, s) for s in sigs}
    tests = E.finalize_tests(rc, [t for o in outs.values() for t in o.tests], 252.0)
    rnd = [t for t in tests if t["key"].startswith("t_random")]
    ps = [t["p_matched"] for t in rnd]
    assert np.mean(ps) > 0.25 and sum(p < 0.05 for p in ps) <= 2
    assert all(t["verdict"]["category"] in ("not_significant", "reversed") for t in rnd)
    for name in ("t_oracle", "t_oracle_bear"):
        t = next(x for x in tests if x["key"].startswith(name + "@"))
        assert t["p_matched"] == pytest.approx(1 / 301)             # never reached by any permutation
        assert t["verdict"]["category"] == "supported"
        sgn = 1 if name == "t_oracle" else -1
        assert sgn * t["excess"] > 0.02


# -- statistics ---------------------------------------------------------------------------------------------------------
def test_benjamini_hochberg_matches_hand_calculation():
    q = st.benjamini_hochberg([0.01, 0.04, 0.03, 0.005, None])
    assert q[:4] == pytest.approx([0.02, 0.04, 0.04, 0.02]) and q[4] is None
    assert st.benjamini_hochberg([0.5]) == [0.5]


def test_bootstrap_and_randomization_p_values():
    rng = np.random.default_rng(0)
    b = st.cluster_bootstrap(np.full(50, 0.01), np.arange(50) // 5, 200, rng)
    assert b["mean"] == pytest.approx(0.01) and b["se"] == pytest.approx(0.0)
    x = rng.normal(0.0, 0.01, 2000)
    b = st.cluster_bootstrap(x, np.arange(2000), 500, rng)
    assert b["se"] == pytest.approx(0.01 / np.sqrt(2000), rel=0.15) and b["ci"][0] < 0 < b["ci"][1]
    null = np.linspace(-1, 1, 99)
    assert st.directional_p_from_null(2.0, null, "bullish")["p"] == pytest.approx(1 / 100)
    assert st.directional_p_from_null(2.0, null, "bearish")["p"] == pytest.approx(1.0)
    assert st.directional_p_from_null(0.0, null, "bullish")["p"] == pytest.approx(0.5, abs=0.02)


def test_verdict_separates_statistical_and_economic_significance():
    kw = dict(n=500, n_dates=200, direction="bullish", horizon=20, periods_per_year=252.0,
              rule="randomization_and_bootstrap", alpha=0.05, econ_annual=0.02, min_events=30)
    v = st.verdict(excess=0.004, excess_net=0.004, q=0.01, ci=[0.001, 0.007], **kw)
    assert v["category"] == "supported" and v["annualized_net_excess"] == pytest.approx(0.004 * 252 / 20)
    v = st.verdict(excess=0.0001, excess_net=0.0001, q=0.01, ci=[0.00005, 0.0002], **kw)
    assert v["category"] == "significant_negligible"
    v = st.verdict(excess=0.004, excess_net=0.004, q=0.2, ci=[0.001, 0.007], **kw)
    assert v["category"] == "not_significant"
    v = st.verdict(excess=-0.004, excess_net=-0.004, q=0.9, ci=[-0.007, -0.001], **kw)
    assert v["category"] == "reversed"
    v = st.verdict(excess=0.004, excess_net=0.004, q=0.01, ci=[0.001, 0.007], **{**kw, "n": 10})
    assert v["category"] == "insufficient_sample"
    kw["direction"] = "bearish"
    assert st.verdict(excess=-0.004, excess_net=-0.004, q=0.01, ci=[-0.007, -0.001], **kw)["category"] == "supported"


def test_sic_sector_mapping():
    assert sic_to_sector(3674) == "Information Technology"
    assert sic_to_sector(2834) == "Health Care"
    assert sic_to_sector(6798) == "Real Estate"
    assert sic_to_sector(1311) == "Energy"
    assert sic_to_sector(4911) == "Utilities"
    assert sic_to_sector("nan") is None


def test_sector_matching_and_sector_benchmark(rlab, tmp_path):
    f = tmp_path / "sic.csv"
    rows = ["ticker,sic"] + [f"S{i:02d},{3674 if i % 2 else 2834}" for i in range(39)]   # S39 has no sector
    f.write_text("\n".join(rows), encoding="utf-8")
    ref = ReferenceData(sector_file=f)
    cfg = cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[10], matching={"vol_groups": 2, "min_pool": 3,
                                                                                    "sector": True})
    rc, sigs = resolve_config(cfg, rlab.store, rlab.signals, rlab.universes, rlab.policy, ref)
    study = prepare(rc, sigs, rlab.store, ref)
    out = run_signal(study, sigs[0])
    ev = out.events[out.events["matched"]]
    sec = dict(zip(study.symbols, ref.sector_of(study.symbols)))
    assert (ev["ctrl_1"].map(sec) == ev["symbol"].map(sec)).all()
    assert (out.events.loc[out.events["symbol"] == "S39", "matched"] == False).all()  # noqa: E712
    with pytest.raises(ResearchError):
        resolve_config(cfg, rlab.store, rlab.signals, rlab.universes, rlab.policy, ReferenceData())
    with pytest.raises(ResearchError):
        resolve_config(cfg_(signals=[SignalSpec(id="gap_up")], matching={"market_cap": True}), rlab.store,
                       rlab.signals, rlab.universes, rlab.policy, ReferenceData())


# -- the store: persistence, ledgers, cancellation, isolation --------------------------------------------------------------
def test_store_isolates_failures_and_is_reproducible(rlab):
    R = rlab.research
    eid = R.create(cfg_(signals=[SignalSpec(id="rsi14_below_30"), SignalSpec(id="t_boom")], horizons=[5, 20],
                        workers=2))
    R.execute(eid)
    meta = R.get(eid)
    assert meta["status"] == "done" and meta["progress_done"] == 1 and meta["progress_failed"] == 1
    runs = {r["signal_key"].split("@")[0]: r for r in R.runs(eid)}
    assert runs["t_boom"]["status"] == "failed" and "boom" in runs["t_boom"]["error"]
    assert runs["rsi14_below_30"]["status"] == "done"
    d = R.path(eid)
    assert (d / "manifest.json").exists() and (d / "tests.json").exists()
    with pytest.raises(PermissionError):
        (d / "config.json").write_text("x")
    with pytest.raises(ResearchError):
        R.execute(eid)                                          # never re-executed
    rep = R.reproduce(eid)
    assert rep["identical"] and rep["compared"] == 2
    rows = R.results(eid, partition="train", horizon=20)
    assert len(rows) == 1 and rows[0]["verdict"] in st.VERDICTS


def test_cancellation_marks_the_experiment(rlab):
    R = rlab.research
    eid = R.create(cfg_(signals=[SignalSpec(id="rsi14_below_30"), SignalSpec(id="gap_up")], horizons=[5]))
    R._cancel.setdefault(eid, threading.Event()).set()
    out = R.execute(eid)
    assert out["status"] == "cancelled" and R.get(eid)["status"] == "cancelled"


def test_recovery_leaves_signal_studies_of_a_live_process_alone(rlab):
    eid = rlab.research.create(cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[5]))
    check_live_owner_is_left_alone(rlab.research, eid)


def test_test_partition_ledger_locks_the_first_result(rlab):
    R = rlab.research
    c = cfg_(signals=[SignalSpec(id="rsi14_below_30")], horizons=[5], evaluate_test=True)
    a = R.create(c)
    R.execute(a)
    b = R.create(c)
    R.execute(b)
    la, lb = R.locked(a), R.locked(b)
    assert la[0]["first_use"] == 1 and lb[0]["first_use"] == 0
    assert la[0]["lock_key"] == lb[0]["lock_key"] and lb[0]["locked_experiment"] == a and lb[0]["evaluations"] == 2
    assert R.locked_result(la[0]["lock_key"])["result"]["horizons"]["5"]["n"] > 0
    con = sqlite3.connect(R.catalog_path)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("UPDATE signal_test_ledger SET first_use=1")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM signal_locked_results")
    for sql in ("UPDATE signal_data_usage SET n_tests = 0", "DELETE FROM signal_data_usage"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(sql)
    assert con.execute("SELECT COUNT(*) FROM signal_data_usage").fetchone()[0] > 0
    con.close()
    u = R.usage()
    test_rows = [p for p in u["partitions"] if p["role"] == "test"]
    assert test_rows[0]["experiments"] == 2
    assert R.prior_usage(b)[0]["experiments_so_far"] == 2


# -- the Strategy Library: import, backtest, forward ---------------------------------------------------------------------
def test_imported_signal_strategy_trades_the_research_events(rlab):
    R = rlab.research
    eid = R.create(cfg_(signals=[SignalSpec(id="sma_10_20_cross_up")], horizons=[5], sampling={"mode": "all"},
                        partition_scheme="none", start="2021-01-04", end="2021-12-31"))
    R.execute(eid)
    key = R.runs(eid)[0]["signal_key"]
    imp = rlab.import_signal(eid, key)
    assert imp["created"] and rlab.import_signal(eid, key)["created"] is False        # idempotent
    cls = rlab.registry.get(imp["strategy_id"])
    d = cls.describe()
    assert d["category"] == "signal" and d["provenance"]["signal"]["key"] == key
    assert d["provenance"]["research"]["experiment_id"] == eid and d["long_only"]
    from strategy_lab.experiments.config import ExperimentConfig
    run = rlab.experiments.create(ExperimentConfig(strategy=imp["strategy_id"], dataset="syn40", universe=SYMS,
                                                   start="2021-01-04", end="2021-12-31", benchmark=None,
                                                   params={"max_positions": 40, "position_size": 0.02,
                                                           "hold_bars": 5}))
    rlab.experiments.execute(run)
    assert rlab.experiments.get(run)["status"] == "done"
    fired = set()
    for line in (rlab.experiments.path(run) / "logs.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["message"].startswith("signal "):
            for s in rec["message"].split(": ", 1)[1].split(", "):
                fired.add((rec["timestamp"][:10], s))
    ev = R.signal_events(eid, key, limit=None)
    research = set(zip(ev["date"], ev["symbol"]))
    assert research and research == fired          # the same triggers in research and in the strategy
    # a fresh Lab finds the generated module again
    from strategy_lab.lab import Lab
    lab2 = Lab(rlab.settings)
    try:
        assert lab2.registry.get(imp["strategy_id"]).describe()["provenance"]["signal"]["key"] == key
    finally:
        lab2.close()


def test_imported_signal_deploys_to_the_paper_host_with_its_signal_code(rlab):
    R = rlab.research
    eid = R.create(cfg_(signals=[SignalSpec(id="golden_cross_50_200"), SignalSpec(id="bullish_engulfing")],
                        horizons=[5], partition_scheme="none", start="2021-01-04", end="2022-12-30"))
    R.execute(eid)
    keys = {r["signal_key"].split("@")[0]: r["signal_key"] for r in R.runs(eid)}
    close_only = rlab.import_signal(eid, keys["golden_cross_50_200"])["strategy_id"]
    ohlc = rlab.import_signal(eid, keys["bullish_engulfing"])["strategy_id"]
    from strategy_lab.paper.spec import DeploymentSpec
    base = dict(name="t", strategy_version="1.0", params={}, universe=SYMS[:10], initial_capital=100_000,
                start_date="2023-01-03", seed=0)
    assert rlab.forward.validate(DeploymentSpec(strategy=ohlc, **base))["ok"] is False
    rlab.forward.mock_dataset = "syn40"                      # before the mock host is first created
    v = rlab.forward.validate(DeploymentSpec(strategy=close_only, **base))
    assert v["ok"], v
    dep = rlab.forward.deploy(DeploymentSpec(strategy=close_only, **base))
    pin = json.loads((Path(dep["bundle_dir"]) / "models" / "strategy_lab" / "PIN.json").read_text(encoding="utf-8"))
    assert "strategy_lab/signals/base.py" in pin["files"] and "strategy_lab/strategies/signal_rule.py" in pin["files"]
    assert any(k.startswith("user/sig_golden_cross_50_200") for k in pin["files"])


def test_api_routes(rlab):
    from asgi import Client
    from strategy_lab.api.app import create_app
    api = Client(create_app(rlab))
    cat = api.get("/api/v1/signals/catalog").json()
    assert cat["n_signals"] >= 200
    assert api.get("/api/v1/signals/catalog/rsi14_below_30").json()["family"] == "rsi"
    assert api.get("/api/v1/signals/catalog/nope").status_code == 404
    opt = api.get("/api/v1/signals/options").json()
    assert opt["defaults"]["permutations"] == 1000 and opt["defaults"]["horizons"] == [1, 5, 10, 20, 60]
    body = dict(dataset="syn40", universe=SYMS, start="2019-01-02", end="2025-12-31",
                signals=[{"kind": "signal", "id": "rsi14_below_30"}], horizons=[5], **LOOSE)
    v = api.post("/api/v1/signals/experiments/validate", json=body).json()
    assert v["ok"] and v["analysis_end"] == "2024-12-31" and v["n_signals"] == 1
    bad = api.post("/api/v1/signals/experiments/validate", json={**body, "end": "2026-03-01"})
    assert bad.status_code == 422
    eid = api.post("/api/v1/signals/experiments", json=body).json()["experiment_id"]
    import time
    for _ in range(600):
        if api.get(f"/api/v1/signals/experiments/{eid}").json()["experiment"]["status"] not in ("queued", "running"):
            break
        time.sleep(0.1)
    d = api.get(f"/api/v1/signals/experiments/{eid}").json()
    assert d["experiment"]["status"] == "done" and d["manifest"]["seed"] == 0
    key = d["runs"][0]["signal_key"]
    s = api.get(f"/api/v1/signals/experiments/{eid}/signals/{key}").json()
    assert s["summary"]["partitions"]["train"]["horizons"]["5"]["matched"]["hist"]["counts"]
    assert len(api.get(f"/api/v1/signals/experiments/{eid}/signals/{key}/events", {"limit": 3}).json()["rows"]) == 3
    assert api.get(f"/api/v1/signals/experiments/{eid}/results", {"partition": "train"}).json()
    rk = api.get("/api/v1/signals/rankings", {"horizon": 5}).json()
    assert rk["n"] >= 2 and "q_across_rows" in rk["rows"][0]
    ins = api.post("/api/v1/signals/inspect", json={"spec": {"kind": "signal", "id": "rsi14_below_30"},
                                                     "dataset": "syn40", "symbol": "S03", "start": "2024-01-02"}).json()
    assert ins["series"]["osc"]["RSI"] and ins["dates"][0] >= "2024-01-02"
    cid = api.post("/api/v1/signals/configs", json={"name": "n", "config": body}).json()["config_id"]
    assert any(c["config_id"] == cid for c in api.get("/api/v1/signals/configs").json())
    assert api.delete(f"/api/v1/signals/configs/{cid}").status_code == 200
    imp = api.post("/api/v1/signals/import", json={"experiment_id": eid, "signal_key": key}).json()
    one = api.get(f"/api/v1/strategies/{imp['strategy_id']}").json()
    assert one["category"] == "signal" and "make_signal_strategy" in one["code"]
    assert api.get("/api/v1/signals/usage").json()["partitions"]
