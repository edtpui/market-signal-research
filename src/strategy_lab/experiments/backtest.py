"""Backtest orchestration: resolve a config, load exactly the data it needs, run the shared StrategyRunner, and turn
its output into result tables and metrics. Used by the experiment store, reproduction and forward reconciliation."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_lab.analytics import metrics as mt
from strategy_lab.core.market import MarketData
from strategy_lab.data.policy import DataPolicy, period_labels
from strategy_lab.data.store import FREQUENCIES, DataError, DataStore
from strategy_lab.engine.runner import RunOutput, StrategyRunner
from strategy_lab.experiments.config import ExperimentConfig
from strategy_lab.strategies.registry import StrategyRegistry


@dataclass
class Universes:
    named: dict[str, list[str]] = field(default_factory=dict)
    sp1500_file: Path | None = None

    def names(self) -> list[str]:
        return sorted(self.named) + (["sp1500"] if self.sp1500_file else [])

    def resolve(self, name: str, store: DataStore, dataset: str) -> list[str]:
        have = set(store.symbols(dataset))
        if name == "all":
            return sorted(have)
        if name == "sp1500" and self.sp1500_file:
            df = pd.read_csv(self.sp1500_file)
            return sorted(set(df.loc[df["in_sp1500"] == 1, "ticker"].astype(str)) & have)
        if name in self.named:
            return [s for s in self.named[name] if s in have]
        raise DataError(f"unknown universe {name!r} (have all, {', '.join(self.names())})")


@dataclass
class BacktestResult:
    config: ExperimentConfig                 # resolved: dataset_version, universe and strategy_version filled in
    equity: pd.DataFrame                     # per session: equity, cash, exposures, costs, benchmark, drawdown
    fills: pd.DataFrame
    orders: pd.DataFrame
    events: pd.DataFrame
    trades: pd.DataFrame
    open_lots: pd.DataFrame
    holdings: pd.DataFrame
    monthly: pd.DataFrame
    metrics: dict
    data_report: dict
    logs: list[dict]
    final_state: dict
    strategy_info: dict
    periods: list[str]


def resolve(cfg: ExperimentConfig, store: DataStore, registry: StrategyRegistry, universes: Universes,
            policy: DataPolicy) -> tuple[ExperimentConfig, type]:
    policy.check_range(cfg.start, cfg.end)
    cls = registry.get(cfg.strategy, cfg.strategy_version)
    cls.resolve_params(cfg.params)                    # fail early on bad params
    v = store.version(cfg.dataset, cfg.dataset_version) if cfg.dataset_version else store.current(cfg.dataset)
    universe = cfg.universe or universes.resolve(cfg.universe_name, store, cfg.dataset)
    if not universe:
        raise DataError(f"universe {cfg.universe_name!r} has no symbols in dataset {cfg.dataset}")
    resolved = cfg.model_copy(update=dict(strategy_version=cls.version, dataset_version=v.version,
                                          universe=list(universe)))
    return resolved, cls


def _benchmark(store: DataStore, cfg: ExperimentConfig, index: pd.DatetimeIndex) -> tuple[pd.Series | None, str]:
    """Total-return benchmark (adjusted close) on the run's sessions, from the run's dataset or any configured one
    that has the symbol."""
    sym = cfg.benchmark
    if not sym:
        return None, "none"
    order = [cfg.dataset] + [d for d in store.specs if d != cfg.dataset]
    for ds in order:
        try:
            v = store.version(ds, cfg.dataset_version) if ds == cfg.dataset else store.current(ds)
            if sym not in set(store.symbols(ds, v.version)):
                continue
            b = store.bars(ds, [sym], index[0], index[-1], v.version)
        except DataError:
            continue
        b = b[b["valid"]].set_index("date")["close"]
        return b.reindex(index), f"{ds}@{v.version}"
    return None, f"{sym} not found in any dataset"


def run_backtest(cfg: ExperimentConfig, store: DataStore, registry: StrategyRegistry, universes: Universes,
                 policy: DataPolicy, data: MarketData | None = None) -> BacktestResult:
    resolved, cls = resolve(cfg, store, registry, universes, policy)
    strategy = cls(resolved.params)
    report = {}
    if data is None:
        data, report = store.load(resolved.dataset, resolved.universe, resolved.start, resolved.end,
                                  version=resolved.dataset_version, frequency=resolved.frequency,
                                  price_mode=resolved.execution.price_mode, warmup_bars=strategy.warmup())
    ppy = FREQUENCIES[resolved.frequency]
    runner = StrategyRunner(strategy, data, resolved.execution, resolved.initial_capital,
                            pd.Timestamp(resolved.start), pd.Timestamp(resolved.end), tuple(resolved.universe),
                            seed=resolved.seed, periods_per_year=ppy)
    out = runner.run()
    bench, bench_src = _benchmark(store, resolved, pd.DatetimeIndex([d.timestamp for d in out.days]))
    report["benchmark_source"] = bench_src
    return assemble(resolved, cls, out, data, bench, report, ppy)


def assemble(cfg: ExperimentConfig, cls, out: RunOutput, data: MarketData, bench: pd.Series | None, report: dict,
             ppy: float) -> BacktestResult:
    eq = pd.DataFrame([d.__dict__ for d in out.days])
    eq["date"] = pd.to_datetime(eq.pop("timestamp"))
    eq = eq.set_index("date")
    eq["gross_exposure"] = (eq["long_value"] - eq["short_value"]) / eq["equity"]
    eq["net_exposure"] = (eq["long_value"] + eq["short_value"]) / eq["equity"]
    eq["costs"] = eq["commission"] + eq["slippage"]
    eq["drawdown"] = mt.drawdown(eq["equity"])
    eq["return"] = eq["equity"].pct_change()
    if bench is not None and bench.notna().any():
        first = bench.first_valid_index()
        eq["benchmark"] = bench / bench.loc[first] * cfg.initial_capital
        eq.loc[eq.index < first, "benchmark"] = np.nan
    else:
        eq["benchmark"] = np.nan
    # the equity curve starts from the initial capital on the session before the first: one return per session
    start_row = pd.Series({"equity": cfg.initial_capital}, name=eq.index[0] - pd.Timedelta(days=1))
    curve = pd.concat([pd.Series([cfg.initial_capital], index=[start_row.name]), eq["equity"]])
    bcurve = None
    if eq["benchmark"].notna().any():
        bcurve = pd.concat([pd.Series([cfg.initial_capital], index=[start_row.name]),
                            eq["benchmark"].ffill().fillna(cfg.initial_capital)])
    fills = pd.DataFrame([f.to_row() for f in out.fills],
                         columns=["fill_id", "order_id", "symbol", "quantity", "price", "reference_price",
                                  "commission", "slippage_cost", "timestamp", "reason"])
    trades, open_lots = mt.round_trips(fills)
    trades = mt.add_holding_periods(trades, data.index)
    m = mt.compute(mt.Inputs(equity=curve, periods_per_year=ppy, benchmark=bcurve,
                             trades=trades, exposure=eq["gross_exposure"], traded_notional=eq["traded_notional"],
                             costs=eq["costs"]))
    m["fill_count"] = mt.M(len(fills))
    m["dividends"] = mt.M(float(eq["dividends"].sum()))
    if len(open_lots):
        last = data.fields["close"].ffill().iloc[-1]
        open_lots = open_lots.copy()
        open_lots["mark"] = [float(last.get(s, np.nan)) for s in open_lots["symbol"]]
        sign = np.where(open_lots["side"] == "long", 1.0, -1.0)
        open_lots["unrealized_pnl"] = sign * open_lots["quantity"] * (open_lots["mark"] - open_lots["entry_price"])
    orders = pd.DataFrame([o.to_row() for o in out.orders],
                          columns=["order_id", "symbol", "quantity", "order_type", "tif", "created_at", "limit_price",
                                   "stop_price", "tag", "status", "filled_quantity", "avg_fill_price", "reason"])
    events = pd.DataFrame([e.to_row() for e in out.events],
                          columns=["order_id", "symbol", "status", "timestamp", "detail", "fill_id", "order_type",
                                   "tag"])
    holdings = pd.DataFrame(out.holdings, columns=["timestamp", "symbol", "quantity", "avg_cost", "close",
                                                   "market_value", "weight", "mark_date"])
    report = dict(report)
    report["stale_marks"] = int(eq["stale_marks"].sum())
    report["open_orders_at_end"] = int(sum(1 for o in out.orders if not o.status.terminal))
    return BacktestResult(config=cfg, equity=eq, fills=fills, orders=orders, events=events, trades=trades,
                          open_lots=open_lots, holdings=holdings, monthly=mt.monthly_returns(curve), metrics=m,
                          data_report=report, logs=out.logs, final_state=out.final_state,
                          strategy_info=cls.describe(), periods=period_labels(cfg.start, cfg.end))


def today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
