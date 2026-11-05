"""The entry-exit pairing engine.

For every (entry, exit plan) cell of an experiment:
  events        the entry signal's triggers sampled as in the Signal Research Lab (first session of a run by
                default) on point-in-time eligible stocks, inside chronological partitions (an event belongs to the
                partition of its signal session; its trade is closed at that partition's last session at the latest)
  trades        every event replayed as an independent position with the exit plan (entryexit/replay.py): the same
                events for every exit of an entry, so exit comparisons isolate exit management
  controls      matched random stocks (same session, same matching group) and random dates of the same stock, drawn
                once per entry and replayed with the same exit plan and costs, so entry quality is measured against
                random entries under an identical exit
  portfolio     the existing event-driven engine runs the cell's events (StrategyRunner via strategies/entry_exit.py)
                per partition with capital, sizing, a position cap and costs; random-entry portfolios replace the
                events by matched draws
Statistics: cluster-bootstrap CIs (date blocks and tickers, the wider one), randomization p-values against the
matched-random null, Benjamini-Hochberg across every cell of a partition, paired comparisons against the baseline exit
(sign flips by date block), and walk-forward selection inside the unlocked window.
"""
from __future__ import annotations

import datetime as dt
import math
import threading
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategy_lab.analytics import metrics as mt
from strategy_lab.core.market import MarketData
from strategy_lab.core.strategy import ParamError
from strategy_lab.data.policy import to_date
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.entryexit.components import Common, default_exit_registry
from strategy_lab.entryexit.config import MAX_CELLS, MAX_ENTRIES, MAX_PLANS, EntryExitExperimentConfig
from strategy_lab.entryexit.plan import (ExitComponentSpec, ExitPlanSpec, PlanRuntime, ResolvedPlan, resolve_plan,
                                         single)
from strategy_lab.entryexit.replay import Bars, Costs, replay
from strategy_lab.research import stats as st
from strategy_lab.research.config import SignalExperimentConfig
from strategy_lab.research.engine import (Cancelled, ResearchError, _bench_series, build_partitions, prepare as
                                          research_prepare, sample_events, stream)
from strategy_lab.signals.base import Resolved, SignalSpec, evaluate, resolve_spec

YEAR_MIN = 10
REPLAY_CHUNK = 250_000           # control trades per replay batch (bounds memory; results do not depend on it)


class EntryExitError(ValueError):
    pass


def _check(cancel):
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled")


# ---------------------------------------------------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------------------------------------------------
def expand_entries(cfg: EntryExitExperimentConfig, registry) -> list[Resolved]:
    import itertools
    items = []
    for s in cfg.entries:
        items.append(s)
    for fam in cfg.entry_families:
        items += [SignalSpec(id=d.id, version=d.version) for d in registry.in_family(fam) if d.direction == "bullish"]
    for g in cfg.entry_grids:
        d = registry.get(g.id, g.version)
        keys = list(g.grid)
        for combo in itertools.product(*(g.grid[k] for k in keys)):
            p = {**g.params, **dict(zip(keys, combo))}
            items.append(SignalSpec(id=d.id, version=d.version, params=p,
                                    label=f"{d.name} ({', '.join(f'{k}={v}' for k, v in zip(keys, combo))})"))
    out, seen = [], set()
    for it in items:
        try:
            r = resolve_spec(it, registry)
        except ParamError as e:
            raise ParamError(f"entry signal '{getattr(it, 'id', None) or getattr(it, 'name', '')}': {e}") from None
        if r.direction != "bullish":
            raise EntryExitError(f"entry '{r.name}' ({r.id}) is a {r.direction} signal: entries open long positions, "
                                 "so they must be bullish signals (a bearish signal can be an exit through 'Any "
                                 "catalog signal')")
        if r.key in seen:
            continue
        seen.add(r.key)
        if isinstance(it, SignalSpec):
            r.spec = it.model_copy(update=dict(version=r.version))
        out.append(r)
    if not out:
        raise EntryExitError("no entry signals selected")
    if len(out) > MAX_ENTRIES:
        raise EntryExitError(f"{len(out)} entries; at most {MAX_ENTRIES} per experiment")
    return out


class _Kind:
    kind = "random"


@dataclass
class RandomEntry:
    """The random-entry cohort (config.random_entries): not a catalog signal, so it cannot be imported or deployed."""
    n: int
    seed: int
    spec: object = field(default_factory=_Kind)
    family: str = "random"
    direction: str = "bullish"
    warmup: int = 1
    needs_benchmark: bool = False

    @property
    def key(self) -> str:
        return f"random_entries@1.0:{self.n}:{self.seed}"

    @property
    def id(self) -> str:
        return "random_entries"

    @property
    def name(self) -> str:
        return f"Random entries ({self.n:,})"

    def describe(self) -> dict:
        return dict(key=self.key, kind="random", id=self.id, version="1.0", family=self.family, name=self.name,
                    direction=self.direction, n=self.n, seed=self.seed, warmup=1, scope="stock",
                    trigger="n eligible stock-sessions drawn uniformly without replacement; buy at the next open")


def all_entries(cfg: EntryExitExperimentConfig, registry) -> list:
    """Catalog entries plus the random-entry cohort when it is on."""
    out = expand_entries(cfg, registry) if (cfg.entries or cfg.entry_families or cfg.entry_grids) else []
    if cfg.random_entries.enabled:
        out.append(RandomEntry(cfg.random_entries.n, cfg.seed))
    if not out:
        raise EntryExitError("no entry signals selected (add a signal, a family, a grid or random entries)")
    return out


def _same(a: ExitComponentSpec, b: ExitComponentSpec) -> bool:
    return a.model_dump(mode="json") == b.model_dump(mode="json")


def _with_protective(cfg, s: ExitPlanSpec) -> ExitPlanSpec:
    comps = list(s.components)
    for pc in cfg.protective:
        if not any(_same(pc, c) for c in comps):
            comps.append(pc)
    return s.model_copy(update=dict(components=comps))


def expand_grid(cfg, g, reg=None) -> list[tuple[dict, ResolvedPlan]]:
    """One plan per combination of an exit grid's values, with the protective components added."""
    import itertools
    if g.component >= len(g.plan.components):
        raise EntryExitError(f"exit grid: the plan has no component {g.component}")
    keys = list(g.grid)
    out = []
    for combo in itertools.product(*(g.grid[k] for k in keys)):
        comps = [c.model_copy(deep=True) for c in g.plan.components]
        c = comps[g.component]
        c.params = {**c.params, **dict(zip(keys, combo))}
        spec = _with_protective(cfg, g.plan.model_copy(update=dict(components=comps, name="")))
        p = resolve_plan(spec, reg)
        # the label (display only, not part of the key) names the swept value even when it equals the default
        sweep = ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}" for k, v in zip(keys, combo))
        label = f"{p.name} · sweep {c.id} {sweep}"[:120]
        out.append((dict(zip(keys, combo)), resolve_plan(spec.model_copy(update=dict(label=label)), reg)))
    return out


def expand_exits(cfg: EntryExitExperimentConfig, registry=None) -> list[ResolvedPlan]:
    reg = registry or default_exit_registry()
    specs: list[ExitPlanSpec] = list(cfg.exits)
    specs += [single(i) for i in cfg.exit_ids]
    for fam in cfg.exit_families:
        specs += [single(d.id) for d in reg.in_family(fam) if d.id != "signal_exit"]
    plans = [resolve_plan(_with_protective(cfg, s), reg) for s in specs]
    for g in cfg.exit_grids:
        plans += [p for _, p in expand_grid(cfg, g, reg)]
    if cfg.baseline_exit:
        base = resolve_plan(_with_protective(cfg, single(cfg.baseline_exit)), reg)
        if base.key not in {p.key for p in plans}:
            plans.append(base)
    if cfg.calibration.enabled:
        for i, q in enumerate(cfg.calibration.placebo_probs):
            sp = single("placebo_random_exit", prob=float(q), seed=cfg.seed + 101 + i)
            sp = sp.model_copy(update=dict(label=f"Placebo: random-day sell, {q:.1%} of sessions"))
            plans.append(resolve_plan(_with_protective(cfg, sp), reg))
        sp = single("oracle_next_day_drop", drop=cfg.calibration.oracle_drop)
        sp = sp.model_copy(update=dict(label=f"Cheating rule: knows tomorrow's drop > {cfg.calibration.oracle_drop:.0%}"))
        plans.append(resolve_plan(_with_protective(cfg, sp), reg))
    out, seen = [], set()
    for p in plans:
        if p.key in seen:
            continue
        seen.add(p.key)
        out.append(p)
    if len(out) > MAX_PLANS:
        raise EntryExitError(f"{len(out)} exit plans; at most {MAX_PLANS} per experiment")
    return out


def baseline_key(cfg: EntryExitExperimentConfig, plans: list[ResolvedPlan]) -> str | None:
    if not cfg.baseline_exit:
        return None
    return resolve_plan(_with_protective(cfg, single(cfg.baseline_exit))).key


def resolve_config(cfg: EntryExitExperimentConfig, lab) -> tuple[EntryExitExperimentConfig, list[Resolved],
                                                                   list[ResolvedPlan]]:
    lab.policy.check_range(cfg.start, cfg.end)
    store = lab.store
    v = store.version(cfg.dataset, cfg.dataset_version) if cfg.dataset_version else store.current(cfg.dataset)
    first, last = to_date(v.meta["first_date"]), to_date(v.meta["last_date"])
    if cfg.end > last:
        raise EntryExitError(f"end {cfg.end} is after the dataset's last ingested session {last}")
    if cfg.start < first:
        raise EntryExitError(f"start {cfg.start} is before the dataset's first session {first}")
    universe = cfg.universe or lab.universes.resolve(cfg.universe_name, store, cfg.dataset)
    if cfg.universe:
        cov = store.coverage(cfg.dataset, v.version)
        have = set(cov.loc[cov["n_bars"] > 0, "symbol"])
        unknown = sorted(set(universe) - have)
        if unknown:
            raise EntryExitError(f"{len(unknown)} of the {len(set(universe))} symbols are not in dataset {cfg.dataset}: "
                                 f"{', '.join(unknown[:15])}{' ...' if len(unknown) > 15 else ''}")
    if len(universe) < 2:
        raise EntryExitError("matched-random controls need at least 2 symbols")
    m = cfg.matching
    if m.sector and not lab.reference.has_sectors:
        raise EntryExitError("sector matching needs sector reference data ([signal_research] sector_file)")
    if m.market_cap:
        raise EntryExitError("market-cap matching needs a point-in-time market-cap file; it is not approximated")
    entries = all_entries(cfg, lab.signals)
    plans = expand_exits(cfg)
    if not plans:
        raise EntryExitError("no exit plans selected")
    if len(entries) * len(plans) > MAX_CELLS:
        raise EntryExitError(f"{len(entries)} entries x {len(plans)} exits = {len(entries) * len(plans)} cells; at "
                             f"most {MAX_CELLS}")
    parts = build_partitions(cfg)
    test = [p for p in parts if p.role == "test"]
    analysis_end = cfg.end
    if test and not cfg.evaluate_test:
        analysis_end = min(cfg.end, test[0].start - dt.timedelta(days=1))
        if analysis_end < cfg.start or not [p for p in parts if p.role != "test"]:
            raise EntryExitError("the window lies entirely in the locked test partition; enable evaluate_test to "
                                 "spend it (it is recorded in the test ledger)")
    grids = [dict(grid=gi, component=g.plan.components[g.component].id, params=list(g.grid),
                  points=[dict(params=pv, key=p.key) for pv, p in expand_grid(cfg, g)])
             for gi, g in enumerate(cfg.exit_grids)] + list(cfg.resolved_grids or [])
    resolved = cfg.model_copy(update=dict(
        dataset_version=v.version, universe=list(universe),
        entries=[r.spec for r in entries if not isinstance(r, RandomEntry)], entry_families=[],
        entry_grids=[], exits=[p.spec for p in plans], exit_ids=[], exit_families=[], exit_grids=[],
        partition_scheme="custom", partitions=parts, resolved_entries=[r.describe() for r in entries],
        resolved_exits=[p.describe() for p in plans], resolved_grids=grids, analysis_end=analysis_end,
        baseline_exit=cfg.baseline_exit))
    return EntryExitExperimentConfig.model_validate(resolved.model_dump()), entries, plans


# ---------------------------------------------------------------------------------------------------------------------
# preparation (shared by every cell)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class EEStudy:
    cfg: EntryExitExperimentConfig
    study: object                 # research.engine.Study: data, indicators, eligibility, matching pools, partitions
    bars: Bars
    costs: Costs
    bench_open: np.ndarray | None
    bench_close: np.ndarray | None
    md: MarketData
    report: dict
    timings: dict = field(default_factory=dict)
    span_lo: np.ndarray | None = None     # per symbol: first and last session with a valid close (timing shifts)
    span_hi: np.ndarray | None = None

    @property
    def index(self):
        return self.study.index

    @property
    def symbols(self):
        return self.study.symbols


def research_config(cfg: EntryExitExperimentConfig, entries: list[Resolved]) -> SignalExperimentConfig:
    entries = [r for r in entries if not isinstance(r, RandomEntry)]
    return SignalExperimentConfig(
        name=cfg.name, signals=[r.spec for r in entries], dataset=cfg.dataset, dataset_version=cfg.dataset_version,
        universe=cfg.universe, start=cfg.start, end=cfg.end, frequency=cfg.frequency, horizons=[1],
        primary_horizon=1, entry="next_open", sampling=cfg.sampling, matching=cfg.matching,
        random_dates=dict(enabled=cfg.controls.random_dates, same_vol_regime=cfg.controls.same_vol_regime,
                          min_pool=cfg.controls.min_date_pool),
        permutations=100, benchmarks=cfg.benchmarks, sector_benchmark=False, eligibility=cfg.eligibility,
        partition_scheme="custom", partitions=cfg.partitions, evaluate_test=cfg.evaluate_test, seed=cfg.seed,
        save_controls=0, workers=1, analysis_end=cfg.analysis_end,
        resolved_signals=[r.describe() for r in entries] or [dict(note="random entries only")])


def prepare(cfg: EntryExitExperimentConfig, entries: list[Resolved], plans: list[ResolvedPlan], lab,
            cancel: threading.Event | None = None) -> EEStudy:
    t0 = time.time()
    scfg = research_config(cfg, entries)
    study = research_prepare(scfg, [r for r in entries if not isinstance(r, RandomEntry)], lab.store, lab.reference,
                             cancel)
    f = study.ind.panel.fields
    bars = Bars(*(f[k].to_numpy(float) for k in ("open", "high", "low", "close")))
    bench_open = bench_close = None
    b = cfg.benchmarks[0] if cfg.benchmarks else None
    if b:
        so, _ = _bench_series(lab.store, scfg, b, study.index[0], study.index[-1], study.index, "open")
        sc, _ = _bench_series(lab.store, scfg, b, study.index[0], study.index[-1], study.index, "close")
        if so is not None and sc is not None:
            bench_open, bench_close = so.to_numpy(float), sc.to_numpy(float)
    md = MarketData({k: v for k, v in f.items()}, price_mode="adjusted", dataset_id=cfg.dataset,
                    dataset_version=cfg.dataset_version or "")
    rep = dict(study.report)
    rep["plan_warmup_max"] = int(max(PlanRuntime(p, _common(cfg)).warmup() for p in plans))
    if rep["plan_warmup_max"] > study.first:
        rep["plan_warmup_note"] = (f"some exits ask for {rep['plan_warmup_max']} sessions of warm-up; "
                                   f"{study.first} are loaded before the start (recursive averages start there)")
    ok = bars.close_ok
    has = ok.any(axis=0)
    span_lo = np.where(has, ok.argmax(axis=0), 0)
    span_hi = np.where(has, ok.shape[0] - 1 - ok[::-1].argmax(axis=0), -1)
    return EEStudy(cfg=cfg, study=study, bars=bars, costs=Costs(cfg.execution, cfg.trade_notional),
                   bench_open=bench_open, bench_close=bench_close, md=md, report=rep,
                   timings=dict(prepare_s=round(time.time() - t0, 2)), span_lo=span_lo, span_hi=span_hi)


def _common(cfg) -> Common:
    return Common(cfg.ref_atr_n, cfg.ref_atr_mult)


# ---------------------------------------------------------------------------------------------------------------------
# events and controls (once per entry, shared by every exit)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Events:
    key: str
    t: np.ndarray
    s: np.ndarray
    part: np.ndarray
    end: np.ndarray
    ctrl: np.ndarray | None          # (n, K) matched control symbols, -1 when unmatched
    rd: np.ndarray | None            # (n, Kd) random-date sessions of the same symbol, -1 when unmatched
    counts: dict


def entry_events(es: EEStudy, r: Resolved) -> Events:
    S = es.study
    cfg = es.cfg
    T = S.T
    win = np.zeros(T, bool)
    win[S.first: S.last + 1] = True
    if isinstance(r, RandomEntry):
        cand = np.flatnonzero((S.eligible & (win & (S.part >= 0))[:, None]).ravel())
        rng = stream(cfg.seed, r.key, 13)
        pick = np.sort(rng.choice(len(cand), size=min(r.n, len(cand)), replace=False)) if len(cand) else cand
        flat = cand[pick]
        t, s = (flat // S.S).astype(np.int64), (flat % S.S).astype(np.int64)
        n_trig, n_inel = int(len(t)), 0
    else:
        trig = np.array(evaluate(r, S.ind).to_numpy(bool), dtype=bool, copy=True)
        trig &= (win & (S.part >= 0))[:, None]
        n_trig = int(trig.sum())
        n_inel = int((trig & ~S.eligible).sum())
        t, s, _ = sample_events(trig, S.eligible, cfg.sampling.mode, cfg.sampling.cooldown, [1])
    part = S.part[t]
    bounds = np.array([p["last"] for p in S.parts], dtype=np.int64)
    end = bounds[np.maximum(part, 0)]
    ctrl = rd = None
    unmatched = 0
    if cfg.controls.matched:
        M = S.matched
        gid = M["GID"][t, s]
        pool = np.where(gid >= 0, M["sizes"][np.maximum(gid, 0)] - 1, 0)
        ok = (gid >= 0) & (pool >= cfg.matching.min_pool)
        unmatched = int((~ok).sum())
        K = cfg.controls.draws
        rng = stream(cfg.seed, r.key, 11)
        U = rng.random((len(t), K))
        g = np.maximum(gid, 0)
        n = np.maximum(M["sizes"][g] - 1, 1)
        jj = np.minimum(np.floor(U * n[:, None]).astype(np.int64), n[:, None] - 1)
        jj += jj >= M["POS"][t, s][:, None]
        csym = M["mem_s"][M["starts"][g][:, None] + np.minimum(jj, M["sizes"][g][:, None] - 1)]
        ctrl = np.where(ok[:, None], csym, -1)
    rd_unmatched = 0
    RD = S.rdates
    if cfg.controls.random_dates and RD is not None:
        rg = RD["RGID"][t, s]
        size = np.where(rg >= 0, RD["sizes"][np.maximum(rg, 0)], 0)
        ok = (rg >= 0) & (size - 1 >= cfg.controls.min_date_pool)
        rd_unmatched = int((~ok).sum())
        Kd = cfg.controls.date_draws
        rng = stream(cfg.seed, r.key, 12)
        U = rng.random((len(t), Kd))
        g = np.maximum(rg, 0)
        n = np.maximum(size - 1, 1)
        jj = np.minimum(np.floor(U * n[:, None]).astype(np.int64), n[:, None] - 1)
        own = np.searchsorted(RD["comp"], g.astype(np.int64) * T + t) - RD["starts"][g]
        jj += jj >= own[:, None]
        sess = RD["mem_t"][RD["starts"][g][:, None] + np.minimum(jj, np.maximum(size[:, None] - 1, 0))]
        rd = np.where(ok[:, None], sess, -1)
    counts = dict(triggers=n_trig, ineligible_triggers=n_inel, events=int(len(t)), unmatched=unmatched,
                  rd_unmatched=rd_unmatched,
                  by_partition={p["name"]: int((part == k).sum()) for k, p in enumerate(S.parts)})
    return Events(key=r.key, t=t, s=s, part=part, end=end, ctrl=ctrl, rd=rd, counts=counts)


# ---------------------------------------------------------------------------------------------------------------------
# one cell: replay events and controls, statistics per partition
# ---------------------------------------------------------------------------------------------------------------------
def plan_series(es: EEStudy, rt: PlanRuntime) -> dict[str, np.ndarray]:
    return {k: v.to_numpy(dtype=float) for k, v in rt.series(es.study.ind).items()}


def replay_cell(es: EEStudy, rt: PlanRuntime, ser: dict, ev: Events) -> tuple[pd.DataFrame, np.ndarray | None,
                                                                              np.ndarray | None]:
    """Event trades, and the matched / random-date control net returns per event (n, K) (NaN where missing)."""
    tr = replay(rt, es.bars, ser, ev.t, ev.s, ev.end, es.costs, es.bench_open, es.bench_close)
    tr["part"] = ev.part
    cm = rdm = None
    if ev.ctrl is not None:
        n, K = ev.ctrl.shape
        flat = ev.ctrl.ravel()
        ok = flat >= 0
        cm = _control_returns(es, rt, ser, np.repeat(ev.t, K), flat, np.repeat(ev.end, K), ok).reshape(n, K)
    if ev.rd is not None:
        n, K = ev.rd.shape
        flat = ev.rd.ravel()
        ok = flat >= 0
        rdm = _control_returns(es, rt, ser, flat, np.repeat(ev.s, K), np.repeat(ev.end, K), ok).reshape(n, K)
    return tr, cm, rdm


def _control_returns(es: EEStudy, rt: PlanRuntime, ser: dict, t: np.ndarray, s: np.ndarray, end: np.ndarray,
                     ok: np.ndarray) -> np.ndarray:
    """Net returns of control entries (NaN where there is no control or no trade), replayed in chunks so memory
    stays bounded for large event sets; trades are independent, so chunking does not change any result."""
    vals = np.full(len(t), np.nan)
    idx = np.flatnonzero(ok)
    for a in range(0, len(idx), REPLAY_CHUNK):
        sel = idx[a: a + REPLAY_CHUNK]
        cr = replay(rt, es.bars, ser, t[sel], s[sel], end[sel], es.costs)
        vals[sel] = np.where(cr["status"] == "ok", cr["ret"], np.nan)
    return vals


def circular_shift(a: np.ndarray, lo: np.ndarray, hi: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Each column's values between rows lo[j] and hi[j] rotated forward by u[j] rows (wrapping inside the span):
    the same values, the same count and the same spacing, at other dates. Rows outside the span are unchanged."""
    T, S = a.shape
    R = np.arange(T)[:, None]
    L = np.maximum(hi - lo + 1, 1)[None, :]
    inside = (R >= lo[None, :]) & (R <= hi[None, :])
    src = np.where(inside, lo[None, :] + (R - lo[None, :] - u[None, :]) % L, R)
    return a[src, np.arange(S)[None, :]]


def timing_returns(es: EEStudy, rt: PlanRuntime, ser: dict, ev: Events, plan: ResolvedPlan) -> np.ndarray | None:
    """(events, draws) net returns of the plan with its condition signals moved to random dates on each stock.
    The offsets depend on (seed, plan, draw) only, so every entry of a plan meets the same shifted rule."""
    cfg = es.cfg
    if not (cfg.timing.enabled and plan.shiftable) or not len(ev.t):
        return None
    keys = [rt.comps[k].key("on") for k in plan.shiftable]
    lo, hi = es.span_lo, es.span_hi
    L = np.maximum(hi - lo + 1, 2)
    out = np.full((len(ev.t), cfg.timing.draws), np.nan)
    for d in range(cfg.timing.draws):
        rng = stream(cfg.seed, f"timing|{plan.key}", d)
        u = 1 + np.floor(rng.random(len(L)) * (L - 1)).astype(np.int64)
        shifted = dict(ser)
        for k in keys:
            shifted[k] = circular_shift(ser[k], lo, hi, u)
        tr = replay(rt, es.bars, shifted, ev.t, ev.s, ev.end, es.costs)
        out[:, d] = np.where(tr["status"] == "ok", tr["ret"], np.nan)
    return out


def timing_block(ret: np.ndarray, draws: np.ndarray, t: np.ndarray, s: np.ndarray, years: np.ndarray, block: int,
                 cfg, rng: np.random.Generator) -> dict:
    """The real plan against its own random timing: per trade d = return - mean of its shifted copies; the mean of
    d with the wider of a date-block and a ticker cluster bootstrap; a two-sided p from that standard error; the
    draw-level means (one per shifted copy); the share of years with a positive mean d."""
    m = rowmean(draws)
    ok = np.isfinite(m) & np.isfinite(ret)
    if ok.sum() < 2:
        return dict(n=int(ok.sum()), excess=None, ci=[None, None], se=None, p=None)
    d = ret[ok] - m[ok]
    bd = st.cluster_bootstrap(d, st.block_ids(t[ok], block), cfg.stats.bootstrap, rng)
    bt = st.cluster_bootstrap(d, s[ok], cfg.stats.bootstrap, rng)
    use = bd if (bd["se"] or 0) >= (bt["se"] or 0) else bt
    se = use["se"]
    p = float(2 * (1 - st.norm_cdf(abs(float(d.mean())) / se))) if se else None
    dm = draws[ok]
    draw_means = [float(dm[np.isfinite(dm[:, k]), k].mean()) if np.isfinite(dm[:, k]).any() else None
                  for k in range(dm.shape[1])]
    by_year = []
    for y in sorted(set(years[ok].tolist())):
        sel = years[ok] == y
        if sel.sum() >= YEAR_MIN:
            by_year.append(dict(group=int(y), n=int(sel.sum()), excess=float(d[sel].mean())))
    pos = [g["excess"] > 0 for g in by_year]
    return dict(n=int(ok.sum()), excess=float(d.mean()), real_mean=float(ret[ok].mean()), shifted_mean=float(m[ok].mean()),
                ci=use["ci"], se=se, p=p, draws=draw_means, beat_share=float((d > 0).mean()), by_year=by_year,
                positive_years=float(np.mean(pos)) if pos else None, per_event=np.where(ok, ret - m, np.nan))


def rowmean(a: np.ndarray) -> np.ndarray:
    """Mean of the finite values of each row (NaN for a row without one), without warnings."""
    ok = np.isfinite(a)
    c = ok.sum(axis=1)
    s = np.where(ok, a, 0.0).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(c > 0, s / np.maximum(c, 1), np.nan)


def _null_from_draws(vals: np.ndarray, B: int, rng: np.random.Generator) -> np.ndarray:
    """B means of one valid draw per row (rows: events with at least one valid draw)."""
    n, K = vals.shape
    if n == 0:
        return np.array([])
    okm = np.isfinite(vals)
    cnt = okm.sum(axis=1)
    order = np.argsort(~okm, axis=1, kind="stable")              # valid draws first
    sorted_vals = np.take_along_axis(vals, order, axis=1)
    out = np.empty(B)
    step = max(1, int(2_000_000 // max(n, 1)))
    for a in range(0, B, step):
        b = min(B, a + step)
        pick = np.floor(rng.random((b - a, n)) * cnt[None, :]).astype(np.int64)
        out[a:b] = sorted_vals[np.arange(n)[None, :], pick].mean(axis=1)
    return out


def _excess_block(ret: np.ndarray, draws: np.ndarray | None, t: np.ndarray, s: np.ndarray, block: int, cfg,
                  rng: np.random.Generator, B: int) -> dict | None:
    if draws is None:
        return None
    cm = rowmean(draws)
    has = np.isfinite(cm) & np.isfinite(ret)
    if not has.any():
        return dict(n=0, excess=None, control_mean=None, ci=[None, None], se=None, p=None, null=None, per_event=cm)
    x = ret[has] - cm[has]
    bd = st.cluster_bootstrap(x, st.block_ids(t[has], block), cfg.stats.bootstrap, rng)
    bt = st.cluster_bootstrap(x, s[has], cfg.stats.bootstrap, rng)
    use = bd if (bd["se"] or 0) >= (bt["se"] or 0) else bt
    null = _null_from_draws(draws[has], B, rng)
    p = st.directional_p_from_null(float(ret[has].mean()), null, "bullish")
    return dict(n=int(has.sum()), excess=float(x.mean()), control_mean=float(cm[has].mean()), ci=use["ci"],
                se=use["se"], se_dates=bd["se"], se_tickers=bt["se"], p=p["p"], p_z=p["p_z"],
                null=dict(mean=p["null_mean"], sd=p["null_sd"], q=p["null_q"]), per_event=cm)


def _hist(x: np.ndarray, bins: int = 30) -> dict | None:
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return None
    lo, hi = np.percentile(x, [0.5, 99.5])
    if hi <= lo:
        hi = lo + 1e-9
    c, e = np.histogram(np.clip(x, lo, hi), bins=bins, range=(lo, hi))
    return dict(edges=e.tolist(), counts=c.tolist())


def _basic(tr: pd.DataFrame, ppy: float) -> dict:
    r = tr["ret"].to_numpy(float)
    n = len(r)
    if n == 0:
        return dict(n=0)
    win = r > 0
    gains, losses = r[win].sum(), -r[r < 0].sum()
    bars = tr["bars"].to_numpy(float) + 1.0                  # sessions in the position (entry session counts)
    reasons = tr["reason"].value_counts().to_dict()
    kinds = tr["reason"].str.split(":").str[0].value_counts().to_dict()
    q05 = float(np.percentile(r, 5)) if n >= 20 else None
    sd = float(r.std(ddof=1)) if n > 1 else None
    out = dict(
        n=n, n_dates=int(tr["t"].nunique()), mean_ret=float(r.mean()), median_ret=float(np.median(r)),
        sd_ret=sd, win_rate=float(win.mean()), profit_factor=float(gains / losses) if losses > 0 else None,
        expectancy_r=float(np.nanmean(tr["r_mult"])) if tr["r_mult"].notna().any() else None,
        expectancy_ref_r=float(np.nanmean(tr["r_ref"])) if tr["r_ref"].notna().any() else None,
        avg_win=float(r[win].mean()) if win.any() else None, avg_loss=float(r[r < 0].mean()) if (r < 0).any() else None,
        avg_bars=float(bars.mean()), median_bars=float(np.median(bars)),
        avg_win_bars=float(bars[win].mean()) if win.any() else None,
        avg_loss_bars=float(bars[~win].mean()) if (~win).any() else None,
        ret_per_session=float(r.mean() / bars.mean() * ppy) if bars.mean() > 0 else None,
        trade_sharpe=float(r.mean() / sd * math.sqrt(ppy / bars.mean())) if sd and sd > 0 else None,
        mae=float(np.nanmean(tr["mae"])), mae_r=float(np.nanmean(tr["mae_r"])) if tr["mae_r"].notna().any() else None,
        mfe=float(np.nanmean(tr["mfe"])), mfe_r=float(np.nanmean(tr["mfe_r"])) if tr["mfe_r"].notna().any() else None,
        worst=float(r.min()), q05=q05, cvar05=float(r[r <= q05].mean()) if q05 is not None else None,
        stop_rate=float((kinds.get("stop", 0) + kinds.get("stop_floor", 0)) / n), target_rate=float(kinds.get("target", 0) / n),
        condition_rate=float((kinds.get("condition", 0) + kinds.get("conditions", 0)) / n),
        max_hold_rate=float(kinds.get("max_hold", 0) / n), period_end_rate=float(kinds.get("period_end", 0) / n),
        partial_rate=float((tr["tranches"] > 0).mean()), own_risk_rate=float(tr["r_own"].mean()),
        stop_breach_rate=float(tr["stop_breach"].mean()), reasons=reasons,
        commission_mean=float(tr["commission"].mean()))
    if "bench_ret" in tr:
        b = tr["bench_ret"].to_numpy(float)
        ok = np.isfinite(b)
        out["excess_bench"] = float((r[ok] - b[ok]).mean()) if ok.any() else None
        out["bench_ret"] = float(b[ok].mean()) if ok.any() else None
    return out


def _groups(keys: np.ndarray, x: np.ndarray, ex: np.ndarray | None, labels) -> list[dict]:
    out = []
    for k in sorted(set(keys.tolist())):
        m = keys == k
        row = dict(group=labels(k), n=int(m.sum()), mean_ret=float(x[m].mean()))
        if ex is not None:
            e = ex[m]
            e = e[np.isfinite(e)]
            row["excess"] = float(e.mean()) if len(e) else None
            row["n_excess"] = int(len(e))
        out.append(row)
    return out


def cell_stats(es: EEStudy, tr: pd.DataFrame, cm: np.ndarray | None, rdm: np.ndarray | None, key: str,
               tm: np.ndarray | None = None) -> dict:
    """Statistics of one cell, per partition. tr: replay rows of the cell's events (all statuses)."""
    S = es.study
    cfg = es.cfg
    ppy = S.ppy
    B = cfg.controls.permutations
    out = {}
    ok_all = (tr["status"] == "ok").to_numpy()
    for k, p in enumerate(S.parts):
        sel = ok_all & (tr["part"].to_numpy() == k)
        sub = tr[sel]
        rng = stream(cfg.seed, f"{key}|{p['name']}", 21)
        row = dict(partition=p["name"], role=p["role"], excluded=dict(tr.loc[(tr["part"].to_numpy() == k) & ~ok_all,
                                                                              "status"].value_counts()))
        row.update(_basic(sub, ppy))
        if row["n"] == 0:
            out[p["name"]] = row
            continue
        ret = sub["ret"].to_numpy(float)
        t = sub["t"].to_numpy()
        s = sub["j"].to_numpy()
        block = max(5, int(round(row["median_bars"])))
        m = _excess_block(ret, cm[sel] if cm is not None else None, t, s, block, cfg, rng, B)
        rd = _excess_block(ret, rdm[sel] if rdm is not None else None, t, s, block, cfg, rng, B)
        ex = None
        if m is not None:
            ex = ret - m.pop("per_event")
        if rd is not None:
            rd.pop("per_event")
        row["matched"], row["random_dates"] = m, rd
        if tm is not None:
            tb = timing_block(ret, tm[sel], t, s, S.index[t].year.to_numpy(), block, cfg,
                              stream(cfg.seed, f"{key}|{p['name']}|timing", 22))
            tb.pop("per_event", None)
            row["timing"] = tb
        # per year and regime (all known at the signal session)
        years = S.index[t].year.to_numpy()
        row["by_year"] = _groups(years, ret, ex, int)
        cons = [g for g in row["by_year"] if g.get("n_excess", 0) >= YEAR_MIN and g.get("excess") is not None]
        row["consistency_years"] = (float(np.mean([g["excess"] > 0 for g in cons])) if cons else None)
        pos_years = [g for g in row["by_year"] if g["n"] >= YEAR_MIN]
        row["positive_years"] = float(np.mean([g["mean_ret"] > 0 for g in pos_years])) if pos_years else None
        row["by_market_trend"] = _groups(S.mkt_trend[t], ret, ex, lambda v: {1: "up", 0: "down"}.get(v, "unknown"))
        row["by_market_vol"] = _groups(S.mkt_vol[t], ret, ex, lambda v: {0: "low (<15%)", 1: "mid (15-25%)",
                                                                         2: "high (>25%)"}.get(v, "unknown"))
        vg = S.volb[t, s]
        terc = np.where(vg >= 0, np.minimum(vg * 3 // max(cfg.matching.vol_groups, 1), 2), -1)
        row["by_stock_vol"] = _groups(terc, ret, ex, lambda v: {0: "low", 1: "mid", 2: "high"}.get(v, "unknown"))
        row["hist_ret"] = _hist(ret)
        row["hist_bars"] = _hist(sub["bars"].to_numpy(float) + 1)
        row["excess_per_event"] = None
        out[p["name"]] = row
    return out


def cell_trades_frame(es: EEStudy, tr: pd.DataFrame, cm: np.ndarray | None, rdm: np.ndarray | None,
                      tm: np.ndarray | None = None) -> pd.DataFrame:
    """The stored per-event table of a cell (dates and symbols resolved, control means attached)."""
    S = es.study
    idx = S.index
    ok = tr["status"].to_numpy() == "ok"
    out = pd.DataFrame(dict(
        signal_date=idx[tr["t"].to_numpy()].date, symbol=np.asarray(S.symbols)[tr["j"].to_numpy()],
        partition=[S.parts[p]["name"] if p >= 0 else None for p in tr["part"]], status=tr["status"],
        entry_date=np.where(ok, pd.Series(idx[np.clip(tr["entry_bar"].to_numpy(), 0, len(idx) - 1)].date), None),
        exit_date=np.where(ok, pd.Series(idx[np.clip(tr["exit_bar"].to_numpy(), 0, len(idx) - 1)].date), None),
        entry_price=tr["entry_price"], exit_price=tr["exit_price"], ret=tr["ret"], ret_gross=tr["ret_gross"],
        r_mult=tr["r_mult"], r_ref=tr["r_ref"], r_own=tr["r_own"], stop_at_entry=tr["stop_at_entry"],
        bars=tr["bars"], reason=tr["reason"], tranches=tr["tranches"], mae=tr["mae"], mfe=tr["mfe"],
        mae_r=tr["mae_r"], mfe_r=tr["mfe_r"]))
    if "bench_ret" in tr:
        out["bench_ret"] = tr["bench_ret"]
    if cm is not None:
        out["matched_mean"] = rowmean(cm)
    if rdm is not None:
        out["random_date_mean"] = rowmean(rdm)
    if tm is not None:
        out["random_timing_mean"] = rowmean(tm)
    return out


def paired(es: EEStudy, base: pd.DataFrame, other: pd.DataFrame, key: str) -> dict:
    """other - base on identical events, per partition: mean difference, date-block bootstrap CI and a two-sided
    sign-flip randomization p (flips by date block)."""
    S = es.study
    cfg = es.cfg
    out = {}
    okb = (base["status"] == "ok").to_numpy()
    oko = (other["status"] == "ok").to_numpy()
    for k, p in enumerate(S.parts):
        sel = okb & oko & (base["part"].to_numpy() == k)
        n = int(sel.sum())
        if n < 2:
            out[p["name"]] = dict(n=n, diff=None)
            continue
        d = other["ret"].to_numpy(float)[sel] - base["ret"].to_numpy(float)[sel]
        t = base["t"].to_numpy()[sel]
        block = max(5, int(np.median(np.maximum(base["bars"].to_numpy()[sel], other["bars"].to_numpy()[sel]) + 1)))
        rng = stream(cfg.seed, f"pair|{key}|{p['name']}", 31)
        bs = st.cluster_bootstrap(d, st.block_ids(t, block), cfg.stats.bootstrap, rng)
        blocks, inv = np.unique(st.block_ids(t, block), return_inverse=True)
        sums = np.bincount(inv, weights=d, minlength=len(blocks))
        Bn = cfg.controls.permutations
        flips = rng.choice((-1.0, 1.0), size=(Bn, len(blocks)))
        null = (flips * sums[None, :]).sum(axis=1) / n
        actual = float(d.mean())
        pval = float((1 + (np.abs(null) >= abs(actual)).sum()) / (Bn + 1))
        # normal approximation from the same sign-flip null (no 1/(B+1) floor): with B permutations the empirical
        # p cannot fall below 1/(B+1), which BH across m pairs cannot pass when m > alpha x (B+1)
        sd = float(null.std(ddof=1)) if Bn > 1 else 0.0
        p_z = float(2 * (1 - st.norm_cdf(abs(actual) / sd))) if sd > 0 else None
        out[p["name"]] = dict(n=n, diff=actual, ci=bs["ci"], se=bs["se"], p=pval, p_z=p_z, blocks=int(len(blocks)),
                              win_share=float((d > 0).mean()), diff_median=float(np.median(d)))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# portfolio layer: the event-driven engine
# ---------------------------------------------------------------------------------------------------------------------
def event_mask(es: EEStudy, t: np.ndarray, s: np.ndarray) -> pd.DataFrame:
    S = es.study
    a = np.zeros((S.T, S.S))
    a[t, s] = 1.0
    return pd.DataFrame(a, index=S.index, columns=S.symbols)


def strategy_for(es: EEStudy, entry: Resolved | None, plan: ResolvedPlan, sid: str = "ee_cell"):
    from strategy_lab.strategies.entry_exit import EntryExitDefinition, make_entry_exit_strategy
    cfg = es.cfg
    d = EntryExitDefinition(entry=entry.spec if entry is not None else None, exit=plan.spec, ref_atr_n=cfg.ref_atr_n,
                            ref_atr_mult=cfg.ref_atr_mult, max_hold=cfg.max_hold, sizing=cfg.portfolio.sizing,
                            max_positions=cfg.portfolio.max_positions,
                            eligibility=dict(min_history=cfg.eligibility.min_history,
                                             min_price=cfg.eligibility.min_price,
                                             min_dollar_volume=cfg.eligibility.min_dollar_volume),
                            benchmark=cfg.benchmarks[0] if cfg.benchmarks else "SPY", seed=cfg.seed)
    # the research engine injects the cell's events, so the class it runs needs no entry signal (the signal may be
    # a test or experiment registry's); the returned definition keeps it for the record
    return d, make_entry_exit_strategy(d.model_copy(update=dict(entry=None)), sid)


def run_portfolio(es: EEStudy, cls, frames: dict, mask: pd.DataFrame, part: dict) -> dict:
    """One event-driven backtest of the injected events over a partition; metrics, equity and round trips."""
    S = es.study
    cfg = es.cfg
    strat = cls({}).inject({**frames, "entry": mask}, complete=True)
    start, end = S.index[part["first"]], S.index[part["last"]]
    runner = StrategyRunner(strat, es.md, cfg.execution, cfg.portfolio.initial_capital, start, end,
                            tuple(s for s in S.symbols if s != strat.definition.benchmark), seed=cfg.seed)
    out = runner.run()
    return _portfolio_result(es, out, cfg.portfolio.initial_capital)


def _portfolio_result(es: EEStudy, out, capital: float) -> dict:
    S = es.study
    days = pd.DataFrame([d.__dict__ for d in out.days])
    idx = pd.DatetimeIndex(pd.to_datetime(days["timestamp"]))
    eq = pd.Series(days["equity"].to_numpy(float), index=idx)
    curve = pd.concat([pd.Series([capital], index=[idx[0] - pd.Timedelta(days=1)]), eq])
    bench = None
    if es.bench_close is not None:
        b = pd.Series(es.bench_close, index=S.index).reindex(idx).ffill()
        if b.notna().iloc[0]:
            bench = pd.concat([pd.Series([b.iloc[0]], index=[curve.index[0]]), b])
    fills = pd.DataFrame([f.to_row() for f in out.fills],
                         columns=["fill_id", "order_id", "symbol", "quantity", "price", "reference_price",
                                  "commission", "slippage_cost", "timestamp", "reason"])
    trades, open_lots = mt.round_trips(fills)
    trades = mt.add_holding_periods(trades, S.index)
    expo = (days["long_value"] - days["short_value"]).to_numpy(float) / days["equity"].to_numpy(float)
    m = mt.compute(mt.Inputs(equity=curve, periods_per_year=S.ppy, benchmark=bench, trades=trades,
                             exposure=pd.Series(expo, index=idx),
                             traded_notional=pd.Series(days["traded_notional"].to_numpy(float), index=idx),
                             costs=pd.Series((days["commission"] + days["slippage"]).to_numpy(float), index=idx)))
    tags = pd.Series([e.tag for e in out.events if e.fill is not None and e.fill.quantity < 0])
    kinds = tags.str.split(":").str[0].value_counts().to_dict() if len(tags) else {}
    flat = {k: v["value"] for k, v in m.items()}
    flat.update(open_positions_at_end=int(len(open_lots)), exit_fill_kinds=kinds,
                annual=mt.annual_returns(curve).to_dict("records"))
    eqdf = pd.DataFrame(dict(date=curve.index, equity=curve.to_numpy(), drawdown=mt.drawdown(curve).to_numpy(),
                             benchmark=(bench / bench.iloc[0] * capital).to_numpy() if bench is not None else np.nan))
    return dict(metrics=flat, reasons={k: v["reason"] for k, v in m.items() if v["value"] is None},
                equity=eqdf, trades=trades, monthly=mt.monthly_returns(curve))


def buy_and_hold(es: EEStudy, part: dict) -> dict:
    from strategy_lab.strategies.basic import BuyAndHold
    S = es.study
    cfg = es.cfg
    start, end = S.index[part["first"]], S.index[part["last"]]
    syms = tuple(s for s in S.symbols if s not in cfg.benchmarks)
    out = StrategyRunner(BuyAndHold({}), es.md, cfg.execution, cfg.portfolio.initial_capital, start, end, syms,
                         seed=cfg.seed).run()
    return _portfolio_result(es, out, cfg.portfolio.initial_capital)


# ---------------------------------------------------------------------------------------------------------------------
# multiple testing, verdicts, walk-forward
# ---------------------------------------------------------------------------------------------------------------------
def finalize(cfg: EntryExitExperimentConfig, rows: list[dict], ppy: float) -> list[dict]:
    """BH over every cell of each partition (matched-random test), then verdicts."""
    by_part: dict[str, list[dict]] = {}
    for r in rows:
        by_part.setdefault(r["partition"], []).append(r)
    for part, rs in by_part.items():
        qs = st.benjamini_hochberg([r["p"] for r in rs])
        for r, q in zip(rs, qs):
            r["q"] = q
            r["m_tests"] = sum(1 for x in rs if x["p"] is not None)
            r["verdict"] = st.verdict(n=r["n"], n_dates=r["n_dates"], direction="bullish", excess=r["excess"],
                                      excess_net=r["excess"], q=q, ci=r["ci"],
                                      horizon=max(1, int(round(r["avg_bars"] or 1))), periods_per_year=ppy,
                                      rule="randomization_and_bootstrap", alpha=cfg.stats.alpha,
                                      econ_annual=cfg.stats.economic_annual_excess, min_events=cfg.stats.min_trades)
    return rows


def finalize_timing(rows: list[dict]) -> list[dict]:
    """BH over the timing tests of each partition (cells whose plan has shiftable conditions)."""
    by_part: dict[str, list[dict]] = {}
    for r in rows:
        by_part.setdefault(r["partition"], []).append(r)
    for rs in by_part.values():
        qs = st.benjamini_hochberg([r.get("p") for r in rs])
        for r, q in zip(rs, qs):
            r["q"] = q
            r["m_tests"] = sum(1 for x in rs if x.get("p") is not None)
    return rows


def style_groups(es: EEStudy, per_event: dict[str, np.ndarray], styles: dict[str, str], ev_t: dict[str, np.ndarray],
                 part_of: dict[str, np.ndarray], entry_of: dict[str, int]) -> list[dict]:
    """Per entry, partition and exit style: the average over the style's exits of each event's timing excess, then
    its mean with a date-block bootstrap CI, the number of exits beating their random timing, and years positive.
    Exits of one entry share its events, so the per-event average is well defined."""
    S = es.study
    cfg = es.cfg
    out = []
    entries = sorted(set(entry_of.values()))
    for ei in entries:
        cells = [c for c in per_event if entry_of[c] == ei]
        if not cells:
            continue
        t = ev_t[cells[0]]
        part = part_of[cells[0]]
        years = S.index[np.clip(t, 0, S.T - 1)].year.to_numpy()
        for style in sorted({styles[c] for c in cells}):
            sc = [c for c in cells if styles[c] == style]
            M = np.vstack([per_event[c] for c in sc])
            g = rowmean(M.T)
            for k, pp in enumerate(S.parts):
                sel = (part == k) & np.isfinite(g)
                if sel.sum() < 2:
                    continue
                rng = stream(cfg.seed, f"style|{ei}|{style}|{pp['name']}", 23)
                bs = st.cluster_bootstrap(g[sel], st.block_ids(t[sel], 20), cfg.stats.bootstrap, rng)
                cell_means = []
                for c in sc:
                    v = per_event[c][part == k]
                    v = v[np.isfinite(v)]
                    if len(v):
                        cell_means.append(float(v.mean()))
                yrs = []
                for y in sorted(set(years[sel].tolist())):
                    ys = sel & (years == y)
                    if ys.sum() >= YEAR_MIN:
                        yrs.append(dict(group=int(y), n=int(ys.sum()), excess=float(g[ys].mean())))
                out.append(dict(entry=ei, style=style, partition=pp["name"], role=pp["role"], exits=len(sc),
                                n=int(sel.sum()), excess=float(g[sel].mean()), ci=bs["ci"], se=bs["se"],
                                beat=int(sum(m > 0 for m in cell_means)), of=len(cell_means),
                                by_year=yrs, positive_years=(float(np.mean([y["excess"] > 0 for y in yrs]))
                                                             if yrs else None)))
    return out


def finalize_pairs(rows: list[dict]) -> list[dict]:
    by_part: dict[str, list[dict]] = {}
    for r in rows:
        by_part.setdefault(r["partition"], []).append(r)
    for rs in by_part.values():
        qs = st.benjamini_hochberg([r.get("p") for r in rs])
        qz = st.benjamini_hochberg([r.get("p_z") for r in rs])
        for r, q, z in zip(rs, qs, qz):
            r["q"], r["q_z"] = q, z
            r["m_tests"] = sum(1 for x in rs if x.get("p") is not None)
    return rows


def folds(es: EEStudy) -> list[dict]:
    """Expanding walk-forward folds inside the unlocked window (never the locked test partition)."""
    S = es.study
    wf = es.cfg.walk_forward
    idx = S.index
    parts = [p for p in S.parts if p["role"] != "test" or es.cfg.evaluate_test]
    if not parts:
        return []
    lo, hi = parts[0]["first"], max(p["last"] for p in parts if p["role"] != "test")
    y0 = idx[lo].year
    out = []
    k = 0
    while True:
        a = pd.Timestamp(year=y0, month=1, day=1)
        b = pd.Timestamp(year=y0 + wf.first_train_years + k * wf.step_years, month=1, day=1)
        c = pd.Timestamp(year=b.year + wf.step_years, month=1, day=1)
        bi, ci = int(idx.searchsorted(b)), int(idx.searchsorted(c)) - 1
        if bi > hi:
            break
        ci = min(ci, hi)
        if ci <= bi:
            break
        out.append(dict(fold=k, train_start=int(lo), train_end=bi - 1, test_start=bi, test_end=ci,
                        train=f"{idx[lo].date()}..{idx[bi - 1].date()}", test=f"{idx[bi].date()}..{idx[ci].date()}"))
        k += 1
    return out


def objective(c: dict, lo: int, hi: int, name: str, min_trades: int) -> float | None:
    """A cell's objective on trades entered and exited inside sessions lo..hi (purged at the window bounds)."""
    m = (c["entry_bar"] >= lo) & (c["exit_bar"] <= hi) & np.isfinite(c["ret"])
    if m.sum() < min_trades:
        return None
    r = c["ret"][m]
    if name == "mean_ret":
        return float(r.mean())
    if name == "expectancy_r":
        x = c["r_mult"][m]
        x = x[np.isfinite(x)]
        return float(x.mean()) if len(x) else None
    if name == "ret_per_session":
        return float(r.mean() / (c["bars"][m] + 1).mean() * 252)
    ex = c["excess"][m]
    ex = ex[np.isfinite(ex)]
    return float(ex.mean()) if len(ex) >= min_trades else None


def _spearman(a: list, b: list) -> float | None:
    x = np.array([(u, v) for u, v in zip(a, b) if u is not None and v is not None], dtype=float)
    if len(x) < 3:
        return None
    ra, rb = pd.Series(x[:, 0]).rank().to_numpy(), pd.Series(x[:, 1]).rank().to_numpy()
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def walk_forward(es: EEStudy, cells: dict[str, dict], groups: dict[str, list[str]]) -> dict:
    """For each selection group (a list of cell ids: all cells, the exits of one entry, one exit grid ...), choose
    the best cell on each fold's training window and record its result on the following window."""
    wf = es.cfg.walk_forward
    fl = folds(es)
    out = dict(folds=fl, objective=wf.objective, groups={})
    if not wf.enabled or not fl:
        return out
    for g, ids in groups.items():
        rows = []
        for f in fl:
            ins = {c: objective(cells[c], f["train_start"], f["train_end"], wf.objective, wf.min_trades) for c in ids}
            oos = {c: objective(cells[c], f["test_start"], f["test_end"], wf.objective, wf.min_trades) for c in ids}
            cand = [c for c in ids if ins[c] is not None]
            if not cand:
                rows.append(dict(fold=f["fold"], chosen=None))
                continue
            best = max(cand, key=lambda c: ins[c])
            avail = [v for v in oos.values() if v is not None]
            rows.append(dict(fold=f["fold"], chosen=best, is_value=ins[best], oos_value=oos[best],
                             oos_mean_all=float(np.mean(avail)) if avail else None,
                             oos_rank=(sorted(avail, reverse=True).index(oos[best]) + 1
                                       if oos[best] is not None else None), n_candidates=len(avail),
                             spearman_is_oos=_spearman([ins[c] for c in ids], [oos[c] for c in ids])))
        ok = [r for r in rows if r.get("oos_value") is not None]
        out["groups"][g] = dict(cells=ids, folds=rows,
                                oos_mean_selected=float(np.mean([r["oos_value"] for r in ok])) if ok else None,
                                oos_mean_all=float(np.mean([r["oos_mean_all"] for r in ok
                                                            if r["oos_mean_all"] is not None])) if ok else None,
                                selection_skill=(float(np.mean([r["oos_value"] - r["oos_mean_all"] for r in ok
                                                                if r["oos_mean_all"] is not None])) if ok else None),
                                spearman_mean=(float(np.mean([r["spearman_is_oos"] for r in ok
                                                              if r["spearman_is_oos"] is not None]))
                                               if any(r["spearman_is_oos"] is not None for r in ok) else None))
    return out


def sensitivity(grid_cells: list[tuple[dict, str]], train: str, valid: str | None, summaries: dict) -> dict:
    """Parameter sensitivity of one exit grid for one entry: the train / validation value of every grid point and a
    neighbourhood-smoothed train score (mean over the point and its grid neighbours), so a stable region is
    preferred to an isolated best point."""
    if not grid_cells:
        return {}
    keys = sorted(grid_cells[0][0])
    axes = {k: sorted({g[0][k] for g in grid_cells}, key=lambda v: (str(type(v)), v)) for k in keys}
    pos = {cid: tuple(axes[k].index(g[k]) for k in keys) for g, cid in grid_cells}

    def val(cid, part):
        s = summaries[cid].get(part) or {}
        m = s.get("matched") or {}
        return m.get("excess") if m else None

    pts = []
    for g, cid in grid_cells:
        here = pos[cid]
        neigh = [c for c in pos if max(abs(a - b) for a, b in zip(pos[c], here)) <= 1]
        tv = [val(c, train) for c in neigh if val(c, train) is not None]
        pts.append(dict(cell=cid, params=g, train=val(cid, train), validation=val(cid, valid) if valid else None,
                        smoothed_train=float(np.mean(tv)) if tv else None, neighbours=len(neigh)))
    ok = [p for p in pts if p["train"] is not None]
    best = max(ok, key=lambda p: p["train"]) if ok else None
    plateau = max((p for p in pts if p["smoothed_train"] is not None), key=lambda p: p["smoothed_train"],
                  default=None)
    both = [p for p in pts if p["train"] is not None and p["validation"] is not None]
    return dict(params=keys, points=pts, best_train=best["cell"] if best else None,
                plateau=plateau["cell"] if plateau else None,
                share_positive_train=float(np.mean([p["train"] > 0 for p in ok])) if ok else None,
                share_positive_both=float(np.mean([p["train"] > 0 and p["validation"] > 0 for p in both]))
                if both else None,
                spearman_train_validation=_spearman([p["train"] for p in both], [p["validation"] for p in both]))


__all__ = ["RandomEntry", "all_entries", "timing_returns", "timing_block", "circular_shift", "finalize_timing",
           "style_groups", "resolve_config", "prepare", "entry_events", "replay_cell", "cell_stats", "paired", "finalize",
           "finalize_pairs", "walk_forward", "sensitivity", "run_portfolio", "buy_and_hold", "strategy_for",
           "event_mask", "plan_series", "cell_trades_frame", "baseline_key", "expand_entries", "expand_exits",
           "EntryExitError", "Cancelled", "ResearchError", "EEStudy", "Events", "folds"]
