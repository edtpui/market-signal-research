"""The historical event-study engine.

Timing (every signal, every control): a signal is evaluated with bars up to and including the close of session t.
The position is entered at the next executable price, the open (entry=next_open) or close (next_close) of t+1, and
exited at the same field of session t+1+h, so every observation holds exactly h sessions. Returns use adjusted
prices (splits and dividends embedded). An observation is kept only when both prices exist (a missing bar is never
filled) and the exit falls inside the event's chronological partition (partitions are purged: no holding period
crosses into the next one). The experiment window bounds everything: nothing after its end is loaded.

Point-in-time universe on t: a valid bar on t, at least min_history valid bars, a trailing volatility, raw close >=
min_price and median raw dollar volume >= min_dollar_volume (both from bars <= t). Volatility groups are the
cross-sectional quantiles of trailing realized volatility among the eligible symbols on t.

Controls, per event (t, s):
  matched random stocks  the other eligible symbols on t in the same volatility group (and sector / liquidity /
                         price / market-cap group when enabled). Their exact leave-one-out mean is the per-event
                         benchmark; the randomization null draws one control per event per permutation.
  random entry dates     the same symbol on random eligible sessions of the same partition whose holding period
                         completes inside it, optionally in the same volatility group as on t.
  market benchmarks      the benchmark ETF (and the symbol's sector ETF) over exactly the same entry and exit sessions.

Randomization is reproducible: each signal draws from its own PCG64 stream seeded by (experiment seed, signal key),
in event order, so the draws do not depend on batch composition, chunking or thread scheduling.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import math
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from strategy_lab.data.policy import DataPolicy, to_date
from strategy_lab.data.store import FREQUENCIES, DataError, DataStore
from strategy_lab.research import stats as st
from strategy_lab.research.config import (DEFAULT_PARTITIONS, MAX_SIGNALS, Partition, SignalExperimentConfig)
from strategy_lab.research.reference import SECTOR_ETF, ReferenceData
from strategy_lab.signals.base import (CombinationSpec, ComponentSpec, Resolved, SignalRegistry, SignalSpec, evaluate,
                                       resolve_spec)
from strategy_lab.signals.indicators import Indicators, Panel

CHUNK_CELLS = 2_000_000          # events x permutations per chunk
HIST_BINS = 30
PATH_POINTS = 60


class Cancelled(RuntimeError):
    pass


class ResearchError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------------
# configuration resolution
# ---------------------------------------------------------------------------------------------------------------------
def _pin(spec, r: Resolved):
    """The spec with every catalog version filled in, so a saved config names exactly what ran."""
    if isinstance(spec, SignalSpec):
        return spec.model_copy(update=dict(version=r.version))
    comps = [c.model_copy(update=dict(version=d.version)) for (d, _, c) in r.components]
    return spec.model_copy(update=dict(components=comps))


def expand_signals(cfg: SignalExperimentConfig, registry: SignalRegistry) -> list[Resolved]:
    items: list = list(cfg.signals)
    for fam in cfg.families:
        for d in registry.in_family(fam):
            items.append(SignalSpec(id=d.id, version=d.version))
    for g in cfg.grids:
        d = registry.get(g.id, g.version)
        keys = list(g.grid)
        for combo in itertools.product(*(g.grid[k] for k in keys)):
            p = {**g.params, **dict(zip(keys, combo))}
            items.append(SignalSpec(id=d.id, version=d.version, params=p,
                                    label=f"{d.name} ({', '.join(f'{k}={v}' for k, v in zip(keys, combo))})"))
            if len(items) > MAX_SIGNALS:
                break
    out, seen = [], set()
    for it in items:
        r = resolve_spec(it, registry)
        if r.key in seen:
            continue
        seen.add(r.key)
        r.spec = _pin(r.spec, r)
        out.append(r)
    if not out:
        raise ResearchError("no signals selected")
    if len(out) > MAX_SIGNALS:
        raise ResearchError(f"{len(out)} signal instances; at most {MAX_SIGNALS} per experiment")
    return out


def build_partitions(cfg: SignalExperimentConfig) -> list[Partition]:
    s, e = cfg.start, cfg.end
    if cfg.partition_scheme == "none":
        return [Partition(name="full", role="exploration", start=s, end=e)]
    if cfg.partition_scheme == "default":
        parts = [Partition(name=n, role=r, start=max(a, s), end=min(b, e)) for n, r, a, b in DEFAULT_PARTITIONS
                 if a <= e and b >= s]
        if not parts:
            parts = [Partition(name="full", role="exploration", start=s, end=e)]
        return parts
    parts = sorted(cfg.partitions, key=lambda p: p.start)
    names = [p.name for p in parts]
    if len(set(names)) != len(names):
        raise ResearchError("partition names must be unique")
    for a, b in zip(parts, parts[1:]):
        if b.start <= a.end:
            raise ResearchError(f"partitions {a.name} and {b.name} overlap: partitions must be chronological and "
                                "disjoint (never shuffled)")
    for p in parts:
        if p.start > p.end or p.start < s or p.end > e:
            raise ResearchError(f"partition {p.name} {p.start}..{p.end} is outside the window {s}..{e}")
    tests = [p for p in parts if p.role == "test"]
    if len(tests) > 1:
        raise ResearchError("at most one test partition")
    if tests and parts[-1].role != "test":
        raise ResearchError("the test partition must be the last one (chronologically)")
    return parts


def resolve_config(cfg: SignalExperimentConfig, store: DataStore, registry: SignalRegistry, universes,
                   policy: DataPolicy, reference: ReferenceData) -> tuple[SignalExperimentConfig, list[Resolved]]:
    policy.check_range(cfg.start, cfg.end)
    v = store.version(cfg.dataset, cfg.dataset_version) if cfg.dataset_version else store.current(cfg.dataset)
    first, last = to_date(v.meta["first_date"]), to_date(v.meta["last_date"])
    if cfg.end > last:
        raise ResearchError(f"end {cfg.end} is after the dataset's last ingested session {last}: the data coverage "
                            "ends there (nothing is substituted)")
    if cfg.start < first:
        raise ResearchError(f"start {cfg.start} is before the dataset's first session {first}")
    universe = cfg.universe or universes.resolve(cfg.universe_name, store, cfg.dataset)
    if not universe:
        raise ResearchError(f"universe {cfg.universe_name!r} has no symbols in dataset {cfg.dataset}")
    if len(universe) < 2:
        raise ResearchError("a matched-random study needs at least 2 symbols")
    m = cfg.matching
    if m.sector and not reference.has_sectors:
        raise ResearchError("sector matching needs sector reference data ([signal_research] sector_file)")
    if m.market_cap and not reference.has_market_cap:
        raise ResearchError("market-cap matching needs a point-in-time market-cap file ([signal_research] "
                            "market_cap_file); none is configured, and it is not approximated")
    signals = expand_signals(cfg, registry)
    parts = build_partitions(cfg)
    # A locked test partition stays in the saved config (so a reproduction knows where it starts) but the analysis
    # ends the day before it: its bars are never loaded unless evaluate_test spends it.
    test = [p for p in parts if p.role == "test"]
    analysis_end = cfg.end
    if test and not cfg.evaluate_test:
        analysis_end = min(cfg.end, test[0].start - dt.timedelta(days=1))
        if analysis_end < cfg.start or not [p for p in parts if p.role != "test"]:
            raise ResearchError("the window lies entirely in the locked test partition; enable evaluate_test to "
                                "spend it (it is recorded in the test ledger)")
    ph = cfg.primary_horizon or (20 if 20 in cfg.horizons else cfg.horizons[len(cfg.horizons) // 2])
    resolved = cfg.model_copy(update=dict(
        dataset_version=v.version, universe=list(universe), signals=[r.spec for r in signals], families=[],
        grids=[], partition_scheme="custom", partitions=parts, primary_horizon=ph,
        resolved_signals=[r.describe() for r in signals], analysis_end=analysis_end))
    return SignalExperimentConfig.model_validate(resolved.model_dump()), signals


# ---------------------------------------------------------------------------------------------------------------------
# the prepared study (signal independent, shared by every signal of an experiment)
# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class Study:
    cfg: SignalExperimentConfig
    index: pd.DatetimeIndex
    symbols: list[str]
    first: int
    last: int
    ind: Indicators
    ppy: float
    entry: np.ndarray
    high: np.ndarray | None
    low: np.ndarray | None
    close: np.ndarray
    part: np.ndarray
    parts: list[dict]
    eligible: np.ndarray
    vol: np.ndarray
    volb: np.ndarray
    sector_id: np.ndarray
    sector_names: list[str]
    fr: dict[int, np.ndarray]
    censored: dict[int, np.ndarray]
    matched: dict
    rdates: dict | None
    bench_fr: dict[str, dict[int, np.ndarray]]
    sector_fr: dict[int, np.ndarray] | None
    mkt_trend: np.ndarray
    mkt_vol: np.ndarray
    path_ks: list[int]
    path_bench: dict[str, np.ndarray]
    path_groups: dict
    excursions: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]]
    report: dict
    timings: dict = field(default_factory=dict)

    @property
    def T(self) -> int:
        return len(self.index)

    @property
    def S(self) -> int:
        return len(self.symbols)


def _warmup(cfg: SignalExperimentConfig, signals: list[Resolved]) -> int:
    return int(max([r.warmup for r in signals] + [cfg.matching.vol_window + 1, cfg.eligibility.min_history, 21, 201]))


def _forward(price: np.ndarray, k: int) -> np.ndarray:
    """price[t+1+k] / price[t+1] - 1, NaN where either is missing or beyond the data."""
    T = price.shape[0]
    out = np.full(price.shape, np.nan)
    if T > 1 + k:
        with np.errstate(divide="ignore", invalid="ignore"):
            out[: T - 1 - k] = price[1 + k:] / price[1: T - k] - 1.0
    return out


def _bench_series(store: DataStore, cfg: SignalExperimentConfig, sym: str, lo, hi, index: pd.DatetimeIndex,
                  field_name: str) -> tuple[pd.Series | None, str]:
    """An adjusted bar field of sym on the study's sessions, from the study's dataset or any configured one."""
    order = [cfg.dataset] + [d for d in store.specs if d != cfg.dataset]
    for ds in order:
        try:
            v = store.version(ds, cfg.dataset_version) if ds == cfg.dataset else store.current(ds)
            if sym not in set(store.symbols(ds, v.version)):
                continue
            b = store.bars(ds, [sym], lo, hi, v.version)
        except (DataError, FileNotFoundError):
            continue
        b = b[b["valid"]].set_index("date")
        return b[field_name].reindex(index), f"{ds}@{v.version}"
    return None, f"{sym} not found in any configured dataset"


def _quantile_groups(x: np.ndarray, ok: np.ndarray, n: int) -> np.ndarray:
    """Per row, the cross-sectional quantile group (0..n-1) of x among ok cells; -1 elsewhere."""
    df = pd.DataFrame(np.where(ok, x, np.nan))
    pct = df.rank(axis=1, pct=True, method="average").to_numpy()
    g = np.where(np.isfinite(pct), np.minimum(np.ceil(pct * n) - 1, n - 1), -1)
    return g.astype(np.int64)


def prepare(cfg: SignalExperimentConfig, signals: list[Resolved], store: DataStore, reference: ReferenceData,
            cancel: threading.Event | None = None) -> Study:
    import time
    t0 = time.time()
    ppy = FREQUENCIES[cfg.frequency]
    warm = _warmup(cfg, signals)
    need_fields = {"open", "high", "low", "close", "volume"}
    md, report = store.load(cfg.dataset, cfg.universe, cfg.start, cfg.analysis_end, version=cfg.dataset_version,
                            frequency=cfg.frequency, price_mode="adjusted", warmup_bars=warm)
    idx = md.index
    symbols = list(md.symbols)
    fields = {k: v for k, v in md.fields.items() if k in need_fields}
    if cfg.frequency == "1d":
        raw, _ = store.load(cfg.dataset, cfg.universe, cfg.start, cfg.analysis_end, version=cfg.dataset_version,
                            frequency="1d", price_mode="raw", warmup_bars=warm)
        raw_close = raw.fields["close"].reindex(index=idx, columns=symbols).to_numpy(float)
        report["price_level"] = "raw close (split-adjusted, not dividend-adjusted) for min_price, price groups and " \
                                "dollar volume"
    else:
        raw_close = fields["close"].to_numpy(float)
        report["price_level"] = "adjusted close (aggregated bars have no raw price) for min_price and dollar volume"
    T, S = len(idx), len(symbols)
    pos = np.flatnonzero((idx >= pd.Timestamp(cfg.start)) & (idx <= pd.Timestamp(cfg.analysis_end)))
    if not len(pos):
        raise ResearchError("no sessions in the analysis window")
    first, last = int(pos[0]), int(pos[-1])
    report["warmup_requested"] = warm
    report["warmup_available"] = first
    if first < warm:
        report["warmup_note"] = (f"only {first} sessions of history before {cfg.start} (requested {warm}): signals "
                                 "with longer history requirements cannot trigger in the first sessions")

    # benchmark series (adjusted) for relative strength, regimes and benchmark comparisons
    entry_field = "open" if cfg.entry == "next_open" else "close"
    bench_src, bench_entry, bench_close = {}, {}, {}
    for b in cfg.benchmarks:
        sc, src = _bench_series(store, cfg, b, idx[0], idx[-1], idx, "close")
        se, _ = _bench_series(store, cfg, b, idx[0], idx[-1], idx, entry_field)
        bench_src[b] = src
        if sc is not None:
            bench_close[b], bench_entry[b] = sc, se
    report["benchmark_sources"] = bench_src
    primary_bench = next((b for b in cfg.benchmarks if b in bench_close), None)
    panel = Panel(fields, benchmark=bench_close.get(primary_bench), benchmark_symbol=primary_bench or "SPY",
                  periods_per_year=ppy)
    ind = Indicators(panel)

    close = fields["close"].to_numpy(float)
    entry = fields[entry_field].to_numpy(float)
    high = fields["high"].to_numpy(float) if "high" in fields else None
    low = fields["low"].to_numpy(float) if "low" in fields else None
    volume = fields["volume"].to_numpy(float) if "volume" in fields else None

    # partitions on the session index
    part = np.full(T, -1, dtype=np.int64)
    parts = []
    for i, p in enumerate(cfg.partitions):
        sel = np.flatnonzero((idx >= pd.Timestamp(p.start)) & (idx <= pd.Timestamp(min(p.end, cfg.analysis_end))))
        if not len(sel):
            continue
        part[sel] = len(parts)
        parts.append(dict(name=p.name, role=p.role, start=p.start.isoformat(), end=p.end.isoformat(),
                          first=int(sel[0]), last=int(sel[-1]), sessions=int(len(sel))))
    if not parts:
        raise ResearchError("no partition has sessions inside the analysis window")
    bound = np.array([p["last"] for p in parts], dtype=np.int64)

    # point-in-time eligibility
    el = cfg.eligibility
    valid_bar = np.isfinite(close) & (close > 0)
    for a in (entry, high, low):
        if a is not None:
            valid_bar &= np.isfinite(a) & (a > 0)
    hist = pd.DataFrame(valid_bar.astype(float)).cumsum().to_numpy()
    vol = ind.realized_vol(cfg.matching.vol_window).to_numpy(float)
    dvol = None
    if volume is not None:
        dvol = pd.DataFrame(raw_close * volume).rolling(21, min_periods=11).median().to_numpy()
    eligible = valid_bar & (hist >= el.min_history) & np.isfinite(vol) & (vol > 0)
    eligible &= np.where(np.isfinite(raw_close), raw_close, 0) >= el.min_price
    if el.min_dollar_volume > 0:
        if dvol is None:
            raise ResearchError("min_dollar_volume needs volume, which this dataset does not provide")
        eligible &= np.where(np.isfinite(dvol), dvol, 0) >= el.min_dollar_volume
    in_window = np.zeros(T, bool)
    in_window[first: last + 1] = True
    eligible &= (in_window & (part >= 0))[:, None]

    # matching groups (all known at t)
    m = cfg.matching
    volb = _quantile_groups(vol, eligible, m.vol_groups)
    key = np.zeros((T, S), dtype=np.int64)
    ok = eligible.copy()
    mult = 1
    if m.volatility:
        key += volb * mult
        mult *= m.vol_groups
    sector_names: list[str] = []
    sector_id = np.full(S, -1, dtype=np.int64)
    if reference.has_sectors:
        secs = reference.sector_of(symbols)
        sector_names = sorted({s for s in secs if s})
        sector_id = np.array([sector_names.index(s) if s else -1 for s in secs], dtype=np.int64)
    if m.sector:
        ok &= (sector_id >= 0)[None, :]
        key += np.maximum(sector_id, 0)[None, :] * mult
        mult *= max(1, len(sector_names))
    if m.liquidity:
        if dvol is None:
            raise ResearchError("liquidity matching needs volume")
        lb = _quantile_groups(dvol, eligible, m.liquidity_groups)
        key += np.maximum(lb, 0) * mult
        mult *= m.liquidity_groups
    if m.price:
        pb = _quantile_groups(raw_close, eligible, m.price_groups)
        key += np.maximum(pb, 0) * mult
        mult *= m.price_groups
    if m.market_cap:
        mc = reference.market_cap(symbols, idx)
        mb = _quantile_groups(mc, eligible & np.isfinite(mc), m.market_cap_groups)
        ok &= mb >= 0
        key += np.maximum(mb, 0) * mult
        mult *= m.market_cap_groups
    mkey = np.where(ok, key, -1)
    report["match_key_groups"] = int(mult)
    _check_cancel(cancel)

    # forward returns per horizon, censored at the partition bounds
    fr, cens = {}, {}
    tt = np.arange(T)
    for h in cfg.horizons:
        f = _forward(entry, h)
        c = (part < 0) | (tt + 1 + h > np.where(part >= 0, bound[np.maximum(part, 0)], -1))
        f[c] = np.nan
        fr[h], cens[h] = f, c

    # matched-pool index: members of each (session, key) group sorted by symbol
    t_i, s_i = np.nonzero(mkey >= 0)
    gk = t_i * mult + mkey[t_i, s_i]
    order = np.lexsort((s_i, gk))
    gk_s, mem_t, mem_s = gk[order], t_i[order], s_i[order]
    _, starts, sizes = np.unique(gk_s, return_index=True, return_counts=True)
    gid_of_member = np.repeat(np.arange(len(starts)), sizes)
    GID = np.full((T, S), -1, dtype=np.int64)
    POS = np.full((T, S), -1, dtype=np.int64)
    GID[mem_t, mem_s] = gid_of_member
    POS[mem_t, mem_s] = np.arange(len(mem_t)) - starts[gid_of_member]
    gsum, gcnt = {}, {}
    for h in cfg.horizons:
        vals = fr[h][mem_t, mem_s]
        okv = np.isfinite(vals)
        gsum[h] = np.bincount(gid_of_member, weights=np.where(okv, vals, 0.0), minlength=len(starts))
        gcnt[h] = np.bincount(gid_of_member, weights=okv.astype(float), minlength=len(starts))
    matched = dict(GID=GID, POS=POS, starts=starts, sizes=sizes, mem_s=mem_s, gsum=gsum, gcnt=gcnt,
                   n_groups=int(len(starts)))
    _check_cancel(cancel)

    # random-date index: each symbol's eligible sessions per (partition, volatility group), sorted by session
    rdates = None
    if cfg.random_dates.enabled:
        P = len(parts)
        VB = m.vol_groups if cfg.random_dates.same_vol_regime else 1
        rok = eligible & (part >= 0)[:, None]
        if cfg.random_dates.same_vol_regime:
            rok &= volb >= 0
        t2, s2 = np.nonzero(rok)
        rk = (s2 * P + part[t2]) * VB + (volb[t2, s2] if VB > 1 else 0)
        o2 = np.lexsort((t2, rk))
        rk_s, rt, rs = rk[o2], t2[o2], s2[o2]
        uk, st2, sz2 = np.unique(rk_s, return_index=True, return_counts=True)
        rgid_m = np.repeat(np.arange(len(uk)), sz2)
        RGID = np.full((T, S), -1, dtype=np.int64)
        RGID[rt, rs] = rgid_m
        comp = rgid_m.astype(np.int64) * T + rt
        cs, cn = {}, {}
        for h in cfg.horizons:
            vals = fr[h][rt, rs]
            okv = np.isfinite(vals)
            cs[h] = np.concatenate([[0.0], np.cumsum(np.where(okv, vals, 0.0))])
            cn[h] = np.concatenate([[0], np.cumsum(okv.astype(np.int64))])
        rdates = dict(RGID=RGID, starts=st2, sizes=sz2, mem_t=rt, comp=comp, cs=cs, cn=cn) if len(rt) else None
    _check_cancel(cancel)

    # benchmarks over the same entry/exit sessions
    bench_fr: dict[str, dict[int, np.ndarray]] = {}
    for b, se in bench_entry.items():
        arr = se.to_numpy(float)[:, None]
        bench_fr[b] = {h: _censor(_forward(arr, h)[:, 0], cens[h]) for h in cfg.horizons}
    sector_fr = None
    if cfg.sector_benchmark and reference.has_sectors and sector_names:
        etf_fr = {}
        for sname in sector_names:
            etf = SECTOR_ETF.get(sname)
            se, src = _bench_series(store, cfg, etf, idx[0], idx[-1], idx, entry_field) if etf else (None, "")
            bench_src[f"sector:{sname}"] = src if etf else "no sector ETF"
            if se is not None:
                etf_fr[sname] = se.to_numpy(float)[:, None]
        sector_fr = {}
        for h in cfg.horizons:
            mat = np.full((T, len(sector_names)), np.nan)
            for j, sname in enumerate(sector_names):
                if sname in etf_fr:
                    mat[:, j] = _censor(_forward(etf_fr[sname], h)[:, 0], cens[h])
            sector_fr[h] = mat

    # market regimes from the primary benchmark (known at t)
    mkt_trend = np.full(T, -1, dtype=np.int64)
    mkt_vol = np.full(T, -1, dtype=np.int64)
    if primary_bench:
        bc = bench_close[primary_bench]
        sma = bc.rolling(200, min_periods=200).mean()
        up = (bc > sma)
        mkt_trend = np.where(sma.notna() & bc.notna(), up.astype(int), -1).astype(np.int64)
        rv = np.log(bc).diff().rolling(21, min_periods=21).std() * math.sqrt(ppy)
        mkt_vol = np.where(rv.isna(), -1, np.where(rv < 0.15, 0, np.where(rv < 0.25, 1, 2))).astype(np.int64)

    # cumulative-return paths: holding-day grid, matched-group sums per day, benchmark paths
    hmax = max(cfg.horizons)
    ks = list(range(1, min(hmax, PATH_POINTS) + 1))
    if hmax > PATH_POINTS:
        ks = sorted(set(ks) | {int(round(x)) for x in np.linspace(PATH_POINTS, hmax, 20)})
    pg_sum, pg_cnt = np.zeros((len(starts), len(ks))), np.zeros((len(starts), len(ks)))
    path_bench = {}
    for j, k in enumerate(ks):
        f = _forward(entry, k)
        c = (part < 0) | (tt + 1 + k > np.where(part >= 0, bound[np.maximum(part, 0)], -1))
        f[c] = np.nan
        vals = f[mem_t, mem_s]
        okv = np.isfinite(vals)
        pg_sum[:, j] = np.bincount(gid_of_member, weights=np.where(okv, vals, 0.0), minlength=len(starts))
        pg_cnt[:, j] = np.bincount(gid_of_member, weights=okv.astype(float), minlength=len(starts))
        for b, se in bench_entry.items():
            path_bench.setdefault(b, np.full((T, len(ks)), np.nan))
            bf = _forward(se.to_numpy(float)[:, None], k)[:, 0]
            bf[c] = np.nan
            path_bench[b][:, j] = bf
    path_groups = dict(sum=pg_sum, cnt=pg_cnt)

    # excursions over each holding window (float32 to bound memory): with entry at bar a = t+1 (+1 for next_close),
    # the window is bars a..a+h-1, i.e. a rolling window ending at t+h+off; forward volatility uses the h daily
    # log returns after the entry session (rolling window ending at t+1+h)
    off = 0 if cfg.entry == "next_open" else 1
    hsrc = pd.DataFrame(high if high is not None else close)
    lsrc = pd.DataFrame(low if low is not None else close)
    lret = pd.DataFrame(np.log(close)).diff()
    excursions = {}
    for h in cfg.horizons:
        hm = hsrc.rolling(h, min_periods=h).max().to_numpy(np.float32)
        lm = lsrc.rolling(h, min_periods=h).min().to_numpy(np.float32)
        fv = (lret.rolling(h, min_periods=h).std(ddof=1).to_numpy(np.float32) * np.float32(math.sqrt(ppy))
              if h >= 2 else np.full((T, S), np.nan, np.float32))
        excursions[h] = (hm, lm, fv)

    # coverage report
    elig_any = eligible.any(axis=0)
    report.update(
        universe_size=S, eligible_symbols=int(elig_any.sum()),
        never_eligible=[symbols[i] for i in np.flatnonzero(~elig_any)][:200],
        eligible_cells=int(eligible.sum()), sessions=int(last - first + 1),
        mean_eligible_per_session=float(eligible[first:last + 1].sum(axis=1).mean()),
        matched_groups=int(len(starts)), median_group_size=float(np.median(sizes)) if len(sizes) else 0.0,
        sectors=dict(available=reference.has_sectors, n=len(sector_names),
                     unknown=[symbols[i] for i in np.flatnonzero(sector_id < 0)][:200] if reference.has_sectors
                     else None),
        survivorship=("universe lists are today's: delisted securities are absent unless the data source has them; "
                      f"{len(report.get('early_end', []))} symbols end before the window end"),
        analysis_end=cfg.analysis_end.isoformat(), partitions=parts)
    return Study(cfg=cfg, index=idx, symbols=symbols, first=first, last=last, ind=ind, ppy=ppy, entry=entry,
                 high=high, low=low, close=close, part=part, parts=parts, eligible=eligible, vol=vol, volb=volb,
                 sector_id=sector_id, sector_names=sector_names, fr=fr, censored=cens, matched=matched, rdates=rdates,
                 bench_fr=bench_fr, sector_fr=sector_fr, mkt_trend=mkt_trend, mkt_vol=mkt_vol, path_ks=ks,
                 path_bench=path_bench, path_groups=path_groups, excursions=excursions, report=report,
                 timings=dict(prepare_s=round(time.time() - t0, 2)))


def _censor(a: np.ndarray, c: np.ndarray) -> np.ndarray:
    a = a.copy()
    a[c] = np.nan
    return a


def _check_cancel(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled")


# ---------------------------------------------------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------------------------------------------------
def stream(seed: int, key: str, purpose: int) -> np.random.Generator:
    k = int(hashlib.sha256(key.encode()).hexdigest()[:15], 16)
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(entropy=seed, spawn_key=(k, purpose))))


def sample_events(trig: np.ndarray, eligible: np.ndarray, mode: str, cooldown: int, horizons: list[int]
                  ) -> tuple[np.ndarray, np.ndarray, dict[int, np.ndarray]]:
    """Events (session, symbol) sorted by session then symbol, and per horizon a use flag.

    first: a run's first triggering session must itself be eligible; a run is never shifted to a later session."""
    T, S = trig.shape
    if mode == "first":
        prev = np.vstack([np.zeros((1, S), bool), trig[:-1]])
        base = trig & ~prev & eligible
    else:
        base = trig & eligible
    if mode in ("all", "first"):
        t, s = np.nonzero(base)
        return t, s, {h: np.ones(len(t), bool) for h in horizons}
    keep_t, keep_s = [], []
    flags: dict[int, list] = {h: [] for h in horizons}
    for j in range(S):
        ts = np.flatnonzero(base[:, j])
        if not len(ts):
            continue
        if mode == "cooldown":
            last = -10**9
            for t in ts:
                if t - last > cooldown:
                    keep_t.append(t)
                    keep_s.append(j)
                    last = t
            continue
        # non_overlapping: per horizon, greedy with a gap of at least h sessions
        use = {}
        for h in horizons:
            last = -10**9
            u = np.zeros(len(ts), bool)
            for i, t in enumerate(ts):
                if t - last >= h:
                    u[i] = True
                    last = t
            use[h] = u
        anyu = np.zeros(len(ts), bool)
        for u in use.values():
            anyu |= u
        for i in np.flatnonzero(anyu):
            keep_t.append(ts[i])
            keep_s.append(j)
            for h in horizons:
                flags[h].append(bool(use[h][i]))
    t = np.asarray(keep_t, dtype=np.int64)
    s = np.asarray(keep_s, dtype=np.int64)
    o = np.lexsort((s, t))
    t, s = t[o], s[o]
    if mode == "cooldown":
        return t, s, {h: np.ones(len(t), bool) for h in horizons}
    return t, s, {h: np.asarray(flags[h], bool)[o] for h in horizons}


@dataclass
class SignalOutput:
    summary: dict
    events: pd.DataFrame
    nulls: pd.DataFrame
    tests: list[dict]


def run_signal(study: Study, r: Resolved, cancel: threading.Event | None = None) -> SignalOutput:
    """The full event study of one signal instance against every control."""
    import time
    t0 = time.time()
    cfg = study.cfg
    T, S, H = study.T, study.S, cfg.horizons
    trig = np.array(evaluate(r, study.ind).to_numpy(bool), dtype=bool, copy=True)
    win = np.zeros(T, bool)
    win[study.first: study.last + 1] = True
    trig &= (win & (study.part >= 0))[:, None]
    n_trig = int(trig.sum())
    n_inel = int((trig & ~study.eligible).sum())
    ev_t, ev_s, use = sample_events(trig, study.eligible, cfg.sampling.mode, cfg.sampling.cooldown, H)
    N = len(ev_t)
    _check_cancel(cancel)
    sgn = 1.0 if r.direction == "bullish" else -1.0
    cost = cfg.costs.round_trip_bps / 1e4
    B = cfg.permutations
    ph = cfg.primary_horizon
    P = len(study.parts)
    ev_part = study.part[ev_t]

    # per-event outcomes
    R = {h: study.fr[h][ev_t, ev_s] for h in H}
    own_ok = {h: use[h] & np.isfinite(R[h]) for h in H}
    M = study.matched
    gid = M["GID"][ev_t, ev_s]
    pool_n = np.where(gid >= 0, M["sizes"][np.maximum(gid, 0)] - 1, 0)
    matched_ok = (gid >= 0) & (pool_n >= cfg.matching.min_pool)
    pool_mean = {}
    for h in H:
        g = np.maximum(gid, 0)
        s_ = M["gsum"][h][g] - np.where(own_ok[h], R[h], 0.0)
        c_ = M["gcnt"][h][g] - own_ok[h].astype(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            pool_mean[h] = np.where(matched_ok & (c_ > 0), s_ / c_, np.nan)

    RD = study.rdates
    rd_n, rd_mean, rd_lo = {}, {}, None
    if RD is not None:
        rg = RD["RGID"][ev_t, ev_s]
        rd_lo = np.where(rg >= 0, RD["starts"][np.maximum(rg, 0)], 0)
        bound = np.array([p["last"] for p in study.parts])[np.maximum(ev_part, 0)]
        for h in H:
            target = np.maximum(rg, 0).astype(np.int64) * T + (bound - 1 - h)
            hi = np.searchsorted(RD["comp"], target, side="right")
            n = np.where(rg >= 0, np.maximum(hi - rd_lo, 0), 0)
            rd_n[h] = n
            cs, cn = RD["cs"][h], RD["cn"][h]
            hi_c = rd_lo + n
            cnt = cn[hi_c] - cn[rd_lo]
            with np.errstate(divide="ignore", invalid="ignore"):
                rd_mean[h] = np.where((n >= cfg.random_dates.min_pool) & (cnt > 0),
                                      (cs[hi_c] - cs[rd_lo]) / cnt, np.nan)

    # randomization: one control per event per permutation
    nsave = cfg.save_controls
    msum = {h: np.zeros((P, B)) for h in H}
    mcnt = {h: np.zeros((P, B)) for h in H}
    rsum = {h: np.zeros((P, B)) for h in H}
    rcnt = {h: np.zeros((P, B)) for h in H}
    ctrl_save = np.full((N, nsave), -1, dtype=np.int64)
    rd_save = np.full((N, nsave), -1, dtype=np.int64)
    rng_m = stream(cfg.seed, r.key, 1)
    rng_r = stream(cfg.seed, r.key, 2)
    chunk = max(1, CHUNK_CELLS // B)
    fr_flat = {h: study.fr[h].ravel() for h in H}
    for c0 in range(0, N, chunk):
        _check_cancel(cancel)
        c1 = min(N, c0 + chunk)
        m = c1 - c0
        tc, sc, pc = ev_t[c0:c1], ev_s[c0:c1], ev_part[c0:c1]
        U = rng_m.random((m, B))
        g = np.maximum(gid[c0:c1], 0)
        n = np.maximum(M["sizes"][g] - 1, 1)
        j = np.floor(U * n[:, None]).astype(np.int64)
        j = np.minimum(j, n[:, None] - 1)
        j += (j >= M["POS"][tc, sc][:, None])
        csym = M["mem_s"][M["starts"][g][:, None] + np.minimum(j, M["sizes"][g][:, None] - 1)]
        mok = matched_ok[c0:c1]
        if nsave:
            ctrl_save[c0:c1] = np.where(mok[:, None], csym[:, :nsave], -1)
        flat = tc[:, None] * S + csym
        for h in H:
            vals = fr_flat[h][flat]
            rows = mok & own_ok[h][c0:c1]
            okv = np.isfinite(vals) & rows[:, None]
            v0 = np.where(okv, vals, 0.0)
            for p in range(P):
                sel = pc == p
                if sel.any():
                    msum[h][p] += v0[sel].sum(axis=0)
                    mcnt[h][p] += okv[sel].sum(axis=0)
        if RD is not None:
            U2 = rng_r.random((m, B))
            lo = rd_lo[c0:c1]
            for h in H:
                n2 = rd_n[h][c0:c1]
                rows = (n2 >= cfg.random_dates.min_pool) & own_ok[h][c0:c1]
                j2 = np.minimum(np.floor(U2 * np.maximum(n2, 1)[:, None]).astype(np.int64),
                                np.maximum(n2 - 1, 0)[:, None])
                dt_ = RD["mem_t"][np.minimum(lo[:, None] + j2, len(RD["mem_t"]) - 1)]
                if h == ph and nsave:
                    rd_save[c0:c1] = np.where(rows[:, None], dt_[:, :nsave], -1)
                vals = fr_flat[h][dt_ * S + sc[:, None]]
                okv = np.isfinite(vals) & rows[:, None]
                v0 = np.where(okv, vals, 0.0)
                for p in range(P):
                    sel = pc == p
                    if sel.any():
                        rsum[h][p] += v0[sel].sum(axis=0)
                        rcnt[h][p] += okv[sel].sum(axis=0)
    t_perm = time.time() - t0

    # benchmarks per event
    BF = {b: {h: study.bench_fr[b][h][ev_t] for h in H} for b in study.bench_fr}
    SF = None
    if study.sector_fr is not None:
        sid = study.sector_id[ev_s]
        SF = {h: np.where(sid >= 0, study.sector_fr[h][ev_t, np.maximum(sid, 0)], np.nan) for h in H}

    # excursions and forward volatility (only where the holding period's return exists)
    off = 0 if cfg.entry == "next_open" else 1
    ex = {}
    entry_px = np.full(N, np.nan)
    ok_e = ev_t + 1 < T
    entry_px[ok_e] = study.entry[ev_t[ok_e] + 1, ev_s[ok_e]]
    for h in H:
        hm, lm, fvm = study.excursions[h]
        b_ = np.minimum(ev_t + h + off, T - 1)
        j_ = np.minimum(ev_t + 1 + h, T - 1)
        okw = own_ok[h] & (ev_t + h + off < T)
        with np.errstate(divide="ignore", invalid="ignore"):
            mfe = np.where(okw, hm[b_, ev_s] / entry_px - 1, np.nan)
            mae = np.where(okw, lm[b_, ev_s] / entry_px - 1, np.nan)
        fv = np.where(own_ok[h] & (ev_t + 1 + h < T), fvm[j_, ev_s], np.nan).astype(float)
        ex[h] = (mfe.astype(float), mae.astype(float), fv)

    # events table
    idx = study.index
    sym = np.asarray(study.symbols, dtype=object)
    sec_names = study.sector_names
    events = pd.DataFrame({
        "date": idx[ev_t].strftime("%Y-%m-%d"), "symbol": sym[ev_s],
        "partition": [study.parts[p]["name"] for p in ev_part],
        "entry_date": [idx[t + 1].strftime("%Y-%m-%d") if t + 1 < T else None for t in ev_t],
        "entry_price": entry_px, "vol": study.vol[ev_t, ev_s], "vol_group": study.volb[ev_t, ev_s],
        "sector": [sec_names[k] if k >= 0 else None for k in study.sector_id[ev_s]] if sec_names else None,
        "mkt_trend": study.mkt_trend[ev_t], "mkt_vol": study.mkt_vol[ev_t],
        "matched": matched_ok, "pool_size": pool_n,
    })
    for h in H:
        events[f"r_{h}"] = R[h]
        if cfg.sampling.mode == "non_overlapping":
            events[f"use_{h}"] = use[h]
        events[f"pool_{h}"] = pool_mean[h]
        if RD is not None:
            events[f"rd_{h}"] = rd_mean[h]
            events[f"rdn_{h}"] = rd_n[h]
        for b in BF:
            events[f"b_{b}_{h}"] = BF[b][h]
        if SF is not None:
            events[f"sec_{h}"] = SF[h]
        events[f"mfe_{h}"], events[f"mae_{h}"], events[f"fvol_{h}"] = ex[h]
    for k in range(nsave):
        events[f"ctrl_{k + 1}"] = np.where(ctrl_save[:, k] >= 0, sym[np.maximum(ctrl_save[:, k], 0)], None)
        if RD is not None:
            events[f"rdate_{k + 1}"] = [idx[d].strftime("%Y-%m-%d") if d >= 0 else None for d in rd_save[:, k]]

    # statistics per partition (+ combined over the non-test ones)
    groups = {p["name"]: np.array([i]) for i, p in enumerate(study.parts)}
    nontest = [i for i, p in enumerate(study.parts) if p["role"] != "test"]
    if len(nontest) > 1:
        groups["combined"] = np.array(nontest)
    rng_b = stream(cfg.seed, r.key, 3)
    partitions_out, tests, null_cols = {}, [], {}
    for gname, pids in groups.items():
        in_g = np.isin(ev_part, pids)
        horizons_out = {}
        for h in H:
            sel = in_g & own_ok[h]
            res = _horizon_stats(study, r, h, sel, ev_t, ev_s, R[h], pool_mean[h], matched_ok,
                                 rd_mean.get(h) if RD is not None else None, BF, SF, ex[h], cost, sgn, rng_b)
            nm = msum[h][pids].sum(axis=0) / np.where(mcnt[h][pids].sum(axis=0) > 0, mcnt[h][pids].sum(axis=0), np.nan)
            res["matched"].update(_null_block(res["matched"].get("signal_mean"), nm, r.direction))
            null_cols[f"{gname}|matched|{h}"] = nm
            if RD is not None:
                nr = rsum[h][pids].sum(axis=0) / np.where(rcnt[h][pids].sum(axis=0) > 0, rcnt[h][pids].sum(axis=0),
                                                         np.nan)
                res["random_date"].update(_null_block(res["random_date"].get("signal_mean"), nr, r.direction))
                null_cols[f"{gname}|random_date|{h}"] = nr
            horizons_out[str(h)] = res
            tests.append(dict(key=r.key, partition=gname, horizon=h, n=res["n"], n_dates=res["n_dates"],
                              direction=r.direction,
                              p_matched=res["matched"].get("p"), p_matched_z=res["matched"].get("p_z"),
                              p_random_date=(res.get("random_date") or {}).get("p"),
                              p_random_date_z=(res.get("random_date") or {}).get("p_z"),
                              excess=res["matched"].get("excess"), excess_net=res["matched"].get("excess_net"),
                              ci=res["matched"].get("ci_conservative") or [None, None],
                              p_boot=res["matched"].get("p_boot")))
        sel_any = in_g & np.logical_or.reduce([own_ok[h] for h in H])
        partitions_out[gname] = dict(
            horizons=horizons_out,
            breakdowns=_breakdowns(study, r, H, in_g, own_ok, ev_t, ev_s, R, pool_mean, BF, sgn),
            path=_path(study, ev_t, ev_s, in_g, gid, matched_ok, own_ok[max(H)]),
            n_events=int(sel_any.sum()))

    months = pd.Series(1, index=idx[ev_t]).groupby(idx[ev_t].to_period("M")).sum() if N else pd.Series(dtype=int)
    first_valid = None
    fv_any = np.flatnonzero(trig.any(axis=1))
    if len(fv_any):
        first_valid = idx[fv_any[0]].date().isoformat()
    summary = dict(
        signal=r.describe(), status="done",
        counts=dict(triggers=n_trig, ineligible_triggers=n_inel, events=int(N),
                    by_partition={p["name"]: int((ev_part == i).sum()) for i, p in enumerate(study.parts)},
                    missing_forward={str(h): int((use[h] & ~np.isfinite(R[h]) & ~study.censored[h][ev_t]).sum())
                                     for h in H},
                    censored={str(h): int((use[h] & study.censored[h][ev_t]).sum()) for h in H},
                    unmatched=int((~matched_ok).sum()),
                    unmatched_no_group=int((gid < 0).sum()),
                    unmatched_small_pool=int(((gid >= 0) & (pool_n < cfg.matching.min_pool)).sum()),
                    random_date_unmatched={str(h): int((rd_n[h] < cfg.random_dates.min_pool).sum())
                                           for h in H} if RD is not None else None,
                    first_trigger=first_valid),
        partitions=partitions_out,
        timeline=[dict(month=str(k), n=int(v)) for k, v in months.items()],
        sampling=dict(mode=cfg.sampling.mode, cooldown=cfg.sampling.cooldown),
        timings=dict(total_s=round(time.time() - t0, 2), permutations_s=round(t_perm, 2)))
    nulls = pd.DataFrame(null_cols) if null_cols else pd.DataFrame()
    return SignalOutput(summary=summary, events=events, nulls=nulls, tests=tests)


def _null_block(actual, null: np.ndarray, direction: str) -> dict:
    out = st.directional_p_from_null(actual if actual is not None else float("nan"), null, direction)
    fin = null[np.isfinite(null)]
    if len(fin):
        lo, hi = float(fin.min()), float(fin.max())
        if actual is not None and np.isfinite(actual):
            lo, hi = min(lo, actual), max(hi, actual)
        if hi > lo:
            counts, edges = np.histogram(fin, bins=HIST_BINS, range=(lo, hi))
            out["hist"] = dict(edges=edges.tolist(), counts=counts.tolist())
    return out


def _stat_block(x: np.ndarray, t: np.ndarray, s: np.ndarray, h: int, rng: np.random.Generator, n_boot: int,
                direction: str) -> dict:
    """Mean of per-event values with date-block and ticker cluster bootstraps; conservative = the larger se."""
    if len(x) == 0:
        return dict(n=0, mean=None, ci=[None, None], se=None, ci_conservative=[None, None], p_boot=None)
    d = st.cluster_bootstrap(x, st.block_ids(t, h), n_boot, rng)
    k = st.cluster_bootstrap(x, s, n_boot, rng)
    ses = [v for v in (d["se"], k["se"]) if v is not None]
    se = max(ses) if ses else None
    mean = float(np.mean(x))
    return dict(n=int(len(x)), mean=mean, ci=d["ci"], se_date=d["se"], se_ticker=k["se"], se=se,
                ci_conservative=[mean - 1.96 * se, mean + 1.96 * se] if se is not None else [None, None],
                p_boot=st.bootstrap_p(mean, se, direction), date_blocks=d["clusters"], tickers=k["clusters"])


def _horizon_stats(study: Study, r: Resolved, h: int, sel: np.ndarray, ev_t, ev_s, Rh, pm, matched_ok, rdm, BF, SF,
                   exh, cost, sgn, rng) -> dict:
    cfg = study.cfg
    nb = cfg.stats.bootstrap
    x = Rh[sel]
    t, s = ev_t[sel], ev_s[sel]
    out = dict(n=int(sel.sum()), n_dates=int(len(np.unique(t))), n_symbols=int(len(np.unique(s))))
    if not len(x):
        out.update(mean=None, matched={}, random_date={} if rdm is not None else None, benchmarks={})
        return out
    mfe, mae, fv = (a[sel] for a in exh)
    win = (x > 0) if sgn > 0 else (x < 0)
    out.update(mean=float(x.mean()), median=float(np.median(x)), std=float(x.std(ddof=1)) if len(x) > 1 else None,
               win_rate=float(win.mean()), mean_net=float(x.mean() - sgn * cost),
               mfe=_nanmean(mfe), mae=_nanmean(mae), fwd_vol=_nanmean(fv),
               q05=float(np.percentile(x, 5)), q95=float(np.percentile(x, 95)),
               mean_boot=_stat_block(x, t, s, h, rng, nb, r.direction))
    # matched random stocks
    sm = sel & matched_ok & np.isfinite(pm)
    xm = Rh[sm] - pm[sm]
    blk = _stat_block(xm, ev_t[sm], ev_s[sm], h, rng, nb, r.direction)
    out["matched"] = dict(n=int(sm.sum()), signal_mean=float(Rh[sm].mean()) if sm.any() else None,
                          control_mean=float(pm[sm].mean()) if sm.any() else None,
                          excess=blk["mean"], excess_net=None if blk["mean"] is None else blk["mean"] - sgn * cost,
                          ci=blk["ci"], ci_conservative=blk["ci_conservative"], se=blk["se"],
                          se_date=blk.get("se_date"), se_ticker=blk.get("se_ticker"), p_boot=blk["p_boot"],
                          excess_hist=_hist(xm))
    if rdm is not None:
        sr = sel & np.isfinite(rdm)
        xr = Rh[sr] - rdm[sr]
        blk = _stat_block(xr, ev_t[sr], ev_s[sr], h, rng, nb, r.direction)
        out["random_date"] = dict(n=int(sr.sum()), signal_mean=float(Rh[sr].mean()) if sr.any() else None,
                                  control_mean=float(rdm[sr].mean()) if sr.any() else None,
                                  excess=blk["mean"],
                                  excess_net=None if blk["mean"] is None else blk["mean"] - sgn * cost,
                                  ci=blk["ci"], ci_conservative=blk["ci_conservative"], se=blk["se"],
                                  p_boot=blk["p_boot"])
    else:
        out["random_date"] = None
    bm = {}
    for b, bfh in BF.items():
        bv = bfh[h][sel]
        ok = np.isfinite(bv)
        blk = _stat_block(x[ok] - bv[ok], t[ok], s[ok], h, rng, nb, r.direction)
        bm[b] = dict(n=int(ok.sum()), benchmark_mean=float(bv[ok].mean()) if ok.any() else None, excess=blk["mean"],
                     ci=blk["ci"], ci_conservative=blk["ci_conservative"], p_boot=blk["p_boot"])
    if SF is not None:
        sv = SF[h][sel]
        ok = np.isfinite(sv)
        blk = _stat_block(x[ok] - sv[ok], t[ok], s[ok], h, rng, nb, r.direction)
        bm["sector_etf"] = dict(n=int(ok.sum()), benchmark_mean=float(sv[ok].mean()) if ok.any() else None,
                                excess=blk["mean"], ci=blk["ci"], ci_conservative=blk["ci_conservative"],
                                p_boot=blk["p_boot"])
    out["benchmarks"] = bm
    out["return_hist"] = _hist(x)
    return out


def _nanmean(a: np.ndarray):
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else None


def _hist(x: np.ndarray) -> dict | None:
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return None
    lo, hi = np.percentile(x, [0.5, 99.5])
    if hi <= lo:
        return None
    counts, edges = np.histogram(np.clip(x, lo, hi), bins=HIST_BINS, range=(lo, hi))
    return dict(edges=edges.tolist(), counts=counts.tolist(), clipped=[float(lo), float(hi)])


def _group_table(keys: np.ndarray, x: np.ndarray, ex: np.ndarray, exb: np.ndarray | None, label) -> list[dict]:
    out = []
    df = pd.DataFrame(dict(k=keys, x=x, ex=ex, exb=exb if exb is not None else np.nan))
    for k, g in df.groupby("k", sort=True):
        e = g["ex"].to_numpy()
        e = e[np.isfinite(e)]
        eb = g["exb"].to_numpy()
        eb = eb[np.isfinite(eb)]
        out.append(dict(key=label(k), n=int(len(g)), mean=float(g["x"].mean()),
                        excess_matched=float(e.mean()) if len(e) else None, n_matched=int(len(e)),
                        excess_benchmark=float(eb.mean()) if len(eb) else None))
    return out


def _consistency(rows: list[dict], sgn: float, min_n: int = 10) -> dict:
    use = [r for r in rows if r["n_matched"] >= min_n and r["excess_matched"] is not None]
    if not use:
        return dict(groups=0, share_in_direction=None)
    k = sum(1 for r in use if sgn * r["excess_matched"] > 0)
    return dict(groups=len(use), in_direction=k, share_in_direction=k / len(use))


def _breakdowns(study: Study, r: Resolved, H, in_g, own_ok, ev_t, ev_s, R, pool_mean, BF, sgn) -> dict:
    """Per horizon: by year, sector, stock-volatility regime and market regime, with consistency shares."""
    out = {}
    years = study.index[ev_t].year.to_numpy()
    G = study.cfg.matching.vol_groups
    vreg = np.where(study.volb[ev_t, ev_s] < 0, -1, (study.volb[ev_t, ev_s] * 3) // G)
    vlab = {0: "low", 1: "mid", 2: "high", -1: "unknown"}
    tlab = {1: "up (SPY > SMA200)", 0: "down (SPY < SMA200)", -1: "unknown"}
    mvlab = {0: "calm (< 15%)", 1: "normal (15-25%)", 2: "stressed (> 25%)", -1: "unknown"}
    bname = next(iter(BF), None)
    for h in H:
        sel = in_g & own_ok[h]
        if not sel.any():
            out[str(h)] = None
            continue
        x = R[h][sel]
        ex = (R[h] - pool_mean[h])[sel]
        exb = (R[h] - BF[bname][h])[sel] if bname else None
        by_year = _group_table(years[sel], x, ex, exb, lambda k: str(int(k)))
        d = dict(by_year=by_year, consistency_years=_consistency(by_year, sgn),
                 by_vol_regime=_group_table(vreg[sel], x, ex, exb, lambda k: vlab[int(k)]),
                 by_market_trend=_group_table(study.mkt_trend[ev_t][sel], x, ex, exb, lambda k: tlab[int(k)]),
                 by_market_vol=_group_table(study.mkt_vol[ev_t][sel], x, ex, exb, lambda k: mvlab[int(k)]))
        if study.sector_names:
            sid = study.sector_id[ev_s][sel]
            names = study.sector_names
            by_sector = _group_table(sid, x, ex, exb, lambda k: names[int(k)] if k >= 0 else "unknown")
            d["by_sector"] = by_sector
            d["consistency_sectors"] = _consistency([b for b in by_sector if b["key"] != "unknown"], sgn)
        out[str(h)] = d
    return out


def _path(study: Study, ev_t, ev_s, in_g, gid, matched_ok, complete) -> dict | None:
    """Mean cumulative return by holding day for the events whose longest horizon completes (one cohort), against
    their matched pools (leave-one-out) and the benchmarks."""
    sel = in_g & complete
    if not sel.any():
        return None
    ks = study.path_ks
    t, s = ev_t[sel], ev_s[sel]
    e0 = study.entry[t + 1, s]
    sig = np.full((len(t), len(ks)), np.nan)
    for j, k in enumerate(ks):
        ex = study.entry[np.minimum(t + 1 + k, study.T - 1), s]
        sig[:, j] = np.where(t + 1 + k < study.T, ex / e0 - 1, np.nan)
    g = gid[sel]
    mo = matched_ok[sel]
    PG = study.path_groups
    gs = PG["sum"][np.maximum(g, 0)] - np.where(np.isfinite(sig), sig, 0.0)
    gc = PG["cnt"][np.maximum(g, 0)] - np.isfinite(sig).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        ctrl = np.where(mo[:, None] & (gc > 0), gs / gc, np.nan)
    out = dict(k=ks, n=int(sel.sum()), signal=_colmean(sig), matched=_colmean(ctrl))
    for b, arr in study.path_bench.items():
        out[b] = _colmean(arr[t])
    return out


def _colmean(a: np.ndarray) -> list:
    with np.errstate(invalid="ignore"):
        cnt = np.isfinite(a).sum(axis=0)
        s = np.where(np.isfinite(a), a, 0.0).sum(axis=0)
    return [float(x) if c > 0 else None for x, c in zip(s / np.maximum(cnt, 1), cnt)]


# ---------------------------------------------------------------------------------------------------------------------
# experiment-level: multiple testing and verdicts
# ---------------------------------------------------------------------------------------------------------------------
def finalize_tests(cfg: SignalExperimentConfig, tests: list[dict], ppy: float) -> list[dict]:
    """BH-FDR over every (signal, horizon) test of each partition group, then the verdict of each test."""
    use_z = cfg.stats.p_value == "normal_approx"
    out = [dict(t) for t in tests]
    by_group: dict[str, list[int]] = {}
    for i, t in enumerate(out):
        by_group.setdefault(t["partition"], []).append(i)
    for gname, ids in by_group.items():
        pm = [out[i]["p_matched_z" if use_z else "p_matched"] for i in ids]
        pr = [out[i]["p_random_date_z" if use_z else "p_random_date"] for i in ids]
        pb = [out[i]["p_boot"] for i in ids]
        for i, q1, q2, q3 in zip(ids, st.benjamini_hochberg(pm), st.benjamini_hochberg(pr),
                                 st.benjamini_hochberg(pb)):
            out[i]["q_matched"], out[i]["q_random_date"], out[i]["q_boot"] = q1, q2, q3
            out[i]["m_tests"] = len(ids)
    for t in out:
        t["verdict"] = st.verdict(n=t["n"], n_dates=t["n_dates"], direction=t["direction"], excess=t["excess"],
                                  excess_net=t["excess_net"], q=t["q_matched"], ci=t["ci"], horizon=t["horizon"],
                                  periods_per_year=ppy, rule=cfg.stats.significance, alpha=cfg.stats.alpha,
                                  econ_annual=cfg.stats.economic_annual_excess, min_events=cfg.stats.min_events)
    return out


def resolution_note(cfg: SignalExperimentConfig, m_tests: int) -> str | None:
    """With B permutations the smallest empirical p is 1/(B+1); BH at level alpha over m tests needs the smallest p
    below alpha/m for a single discovery."""
    pmin = 1.0 / (cfg.permutations + 1)
    if cfg.stats.p_value == "empirical" and m_tests > 0 and pmin > cfg.stats.alpha / m_tests:
        return (f"{cfg.permutations} permutations give a smallest p of {pmin:.4f}; with {m_tests} tests per "
                f"partition, a lone discovery needs p < {cfg.stats.alpha / m_tests:.5f}. Raise permutations to at "
                f"least {math.ceil(m_tests / cfg.stats.alpha)} or use the normal approximation of the null.")
    return None


# ---------------------------------------------------------------------------------------------------------------------
# inspection (one symbol, one signal): the calculation behind the triggers
# ---------------------------------------------------------------------------------------------------------------------
def inspect_signal(store: DataStore, registry: SignalRegistry, spec, dataset: str, symbol: str, universe: list[str],
                   start, end, benchmark: str = "SPY") -> dict:
    r = resolve_spec(spec, registry)
    syms = list(dict.fromkeys([symbol] + [u for u in universe if u != symbol])) if r.scope != "stock" else [symbol]
    md, report = store.load(dataset, syms, start, end, warmup_bars=r.warmup)
    idx = md.index
    fields = {k: v for k, v in md.fields.items()}
    cfg_like = type("C", (), dict(dataset=dataset, dataset_version=md.dataset_version))
    bc, src = _bench_series(store, cfg_like, benchmark, idx[0], idx[-1], idx, "close")
    ind = Indicators(Panel(fields, benchmark=bc, benchmark_symbol=benchmark))
    trig = evaluate(r, ind)[symbol]
    lo = pd.Timestamp(start)
    keep = idx >= lo
    series = {"overlay": {}, "osc": {}, "levels": []}
    d = r.definition
    if d is not None and d.show is not None:
        shown = d.show(ind, r.params)
        for part in ("overlay", "osc"):
            for name, v in (shown.get(part) or {}).items():
                col = v[symbol] if isinstance(v, pd.DataFrame) else v
                series[part][name] = [None if not np.isfinite(x) else float(x) for x in col[keep].to_numpy(float)]
        series["levels"] = list(shown.get("levels") or [])
    bars = {k: [None if not np.isfinite(x) else float(x) for x in fields[k][symbol][keep].to_numpy(float)]
            for k in ("open", "high", "low", "close") if k in fields}
    return dict(signal=r.describe(), symbol=symbol, dates=[d.strftime("%Y-%m-%d") for d in idx[keep]], bars=bars,
                series=series, triggers=[d.strftime("%Y-%m-%d") for d in idx[keep][trig[keep].to_numpy(bool)]],
                benchmark_source=src, report=dict(dataset_version=md.dataset_version, warmup=r.warmup,
                                                  universe_used=len(syms)))


__all__ = ["prepare", "run_signal", "resolve_config", "finalize_tests", "Study", "Cancelled", "ResearchError",
           "inspect_signal", "sample_events", "stream", "CombinationSpec", "ComponentSpec", "Callable"]
