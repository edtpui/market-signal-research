"""EntryExitExperimentConfig: everything an entry-exit experiment depends on. The store saves the resolved config
(dataset version pinned, universe expanded, every entry and exit plan listed with its key, partitions explicit, seed
fixed), so re-running the saved file reproduces the experiment."""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from strategy_lab.core.strategy import canonical_hash
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.entryexit.plan import ExitComponentSpec, ExitPlanSpec
from strategy_lab.research.config import Eligibility, GridSpec, Matching, Partition, Sampling
from strategy_lab.signals.base import CombinationSpec, SignalSpec
from strategy_lab.strategies.entry_exit import Sizing

SpecItem = Annotated[Union[SignalSpec, CombinationSpec], Field(discriminator="kind")]
MAX_ENTRIES = 120
MAX_PLANS = 200
MAX_CELLS = 3000


class ExitGrid(BaseModel):
    """A parameter sweep of one component of an exit plan: one plan per combination of the listed values."""
    model_config = ConfigDict(extra="forbid")
    plan: ExitPlanSpec
    component: int = Field(0, ge=0, le=9)
    grid: dict[str, list[Any]] = Field(min_length=1)

    @field_validator("grid")
    @classmethod
    def _grid(cls, v):
        for k, vals in v.items():
            if not vals:
                raise ValueError(f"grid {k}: give at least one value")
        return v


class Controls(BaseModel):
    """Random-entry controls for every (entry, exit) cell, replayed with the same exit plan and costs.
    matched: `draws` random stocks per event from the same session and matching group (volatility, optional
    sector / liquidity / price), the Signal Research Lab's pools. random_dates: `date_draws` random sessions of the
    same stock in the same partition (optionally the same volatility group)."""
    model_config = ConfigDict(extra="forbid")
    matched: bool = True
    draws: int = Field(10, ge=1, le=100)
    random_dates: bool = True
    date_draws: int = Field(5, ge=1, le=50)
    same_vol_regime: bool = True
    min_date_pool: int = Field(20, ge=1, le=5000)
    permutations: int = Field(1000, ge=100, le=20000)


class RandomEntries(BaseModel):
    """A random-entry cohort (the 0RRXFUA-72M design): n stock-sessions drawn uniformly, without replacement, from
    the eligible cells of the analysis window, bought at the next open. Every exit plan gets the same draws."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    n: int = Field(20000, ge=100, le=500_000)


class Timing(BaseModel):
    """The random-timing exit benchmark (0RRXFUA-72M): each plan's condition signals are moved to random dates on
    the same stock (a circular shift of the stock's signal series by a random offset: the same stock, the same number
    of signals and the same spacing; only the timing is random) and the same entries are replayed. The excess of the
    real plan over its shifted copies is the value of the exit's timing. Stops and targets stay as they are."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    draws: int = Field(10, ge=2, le=100)


class Calibration(BaseModel):
    """Benchmark checks added as exit plans: placebo rules selling on random days (they must score zero against the
    timing benchmark) and a cheating rule that knows tomorrow's drop (it must be detected). Never tradable."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    placebo_probs: list[float] = Field(default_factory=lambda: [0.01, 0.03, 0.1])
    oracle_drop: float = Field(0.03, gt=0, lt=0.5)

    @field_validator("placebo_probs")
    @classmethod
    def _probs(cls, v):
        if any(not 0 < x < 0.5 for x in v) or len(v) > 6:
            raise ValueError("placebo probabilities must be between 0 and 0.5 (at most 6)")
        return v


class Portfolio(BaseModel):
    """The event-driven backtests of the cells (existing engine, one run per cell and partition)."""
    model_config = ConfigDict(extra="forbid")
    run: Literal["all", "top", "none"] = "all"
    top: int = Field(20, ge=1, le=500, description="with run=top: the cells ranked best on train by the objective")
    initial_capital: float = Field(1_000_000.0, gt=0, le=1e12)
    max_positions: int = Field(20, ge=1, le=500)
    sizing: Sizing = Field(default_factory=Sizing)
    random_controls: int = Field(3, ge=0, le=50, description="random-entry portfolio backtests per evaluated cell "
                                                             "(matched draws 0..n-1; at most controls.draws)")
    baseline_buy_and_hold: bool = True


class WalkForward(BaseModel):
    """Chronological walk-forward selection inside the unlocked window: expanding training windows of
    first_train_years (+ step_years per fold), each evaluated on the following step_years. Trades whose holding
    period crosses a fold boundary are purged from both sides."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    first_train_years: int = Field(2, ge=1, le=10)
    step_years: int = Field(1, ge=1, le=5)
    objective: Literal["mean_ret", "excess_matched", "expectancy_r", "ret_per_session"] = "excess_matched"
    min_trades: int = Field(30, ge=5, le=100000)


class Stats(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bootstrap: int = Field(1000, ge=0, le=20000)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    economic_annual_excess: float = Field(0.02, ge=0, le=1.0)
    min_trades: int = Field(30, ge=5, le=100000)


class EntryExitExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=200)
    notes: str = Field("", max_length=4000)
    # entries
    entries: list[SpecItem] = Field(default_factory=list)
    entry_families: list[str] = Field(default_factory=list, description="every bullish catalog signal of a family")
    entry_grids: list[GridSpec] = Field(default_factory=list)
    random_entries: RandomEntries = Field(default_factory=RandomEntries)
    # exits
    exits: list[ExitPlanSpec] = Field(default_factory=list)
    exit_ids: list[str] = Field(default_factory=list, description="catalog exits, one single-component plan each")
    exit_families: list[str] = Field(default_factory=list, description="every catalog exit of a family")
    exit_grids: list[ExitGrid] = Field(default_factory=list)
    protective: list[ExitComponentSpec] = Field(default_factory=list,
                                                description="components added to every plan (e.g. a catastrophic "
                                                            "stop), so all exits share them")
    baseline_exit: str | None = Field("time_20d", description="the reference exit of the hold-entry comparisons: a "
                                                               "catalog exit id, added as a plan if absent")
    # data
    dataset: str
    dataset_version: str | None = None
    universe: list[str] | None = None
    universe_name: str | None = None
    start: dt.date
    end: dt.date
    frequency: Literal["1d"] = "1d"
    benchmarks: list[str] = Field(default_factory=lambda: ["SPY"])
    # events and holding
    sampling: Sampling = Field(default_factory=Sampling)
    eligibility: Eligibility = Field(default_factory=Eligibility)
    matching: Matching = Field(default_factory=Matching)
    max_hold: int = Field(252, ge=1, le=1260, description="every plan exits at the open after this many sessions")
    ref_atr_n: int = Field(14, ge=2, le=100)
    ref_atr_mult: float = Field(2.0, gt=0, le=20, description="the common reference risk: ref_atr_mult x ATR")
    # execution (both layers)
    execution: ExecutionConfig = Field(default_factory=lambda: ExecutionConfig(slippage_bps=5.0,
                                                                                commission={"bps": 1.0}))
    trade_notional: float = Field(10_000.0, gt=0, description="per-trade notional of the replay (per-share and "
                                                              "per-order commissions)")
    portfolio: Portfolio = Field(default_factory=Portfolio)
    controls: Controls = Field(default_factory=Controls)
    timing: Timing = Field(default_factory=Timing)
    calibration: Calibration = Field(default_factory=Calibration)
    # validation
    partition_scheme: Literal["default", "none", "custom"] = "default"
    partitions: list[Partition] = Field(default_factory=list)
    evaluate_test: bool = False
    walk_forward: WalkForward = Field(default_factory=WalkForward)
    stats: Stats = Field(default_factory=Stats)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    # filled by resolution
    resolved_entries: list[dict] | None = None
    resolved_exits: list[dict] | None = None
    resolved_grids: list[dict] | None = None
    analysis_end: dt.date | None = None

    @field_validator("universe")
    @classmethod
    def _universe(cls, v):
        if v is None:
            return v
        out = []
        for s in v:
            s = str(s).strip()
            if s and s not in out:
                out.append(s)
        if not out:
            raise ValueError("universe is empty")
        return out

    @field_validator("benchmarks")
    @classmethod
    def _benchmarks(cls, v):
        out = []
        for s in v:
            s = str(s).strip().upper()
            if s and s not in out:
                out.append(s)
        return out[:3]

    @model_validator(mode="after")
    def _check(self):
        if self.start > self.end:
            raise ValueError("start is after end")
        if not self.universe and not self.universe_name:
            raise ValueError("give a universe (symbols) or a universe_name")
        if not (self.entries or self.entry_families or self.entry_grids or self.resolved_entries
                or self.random_entries.enabled):
            raise ValueError("select at least one entry signal, family or grid, or turn on random entries")
        if not (self.exits or self.exit_ids or self.exit_families or self.exit_grids or self.resolved_exits
                or self.calibration.enabled):
            raise ValueError("select at least one exit, exit family or exit grid")
        if self.sampling.mode == "non_overlapping":
            raise ValueError("sampling non_overlapping is horizon-specific; use first, all or cooldown")
        if self.partition_scheme == "custom" and not self.partitions:
            raise ValueError("partition_scheme custom needs partitions")
        if self.execution.fill_timing != "next_open":
            raise ValueError("entry-exit experiments fill at the next open (attached stops protect the entry "
                             "session after the open)")
        if self.execution.allow_short or self.execution.price_mode != "adjusted":
            raise ValueError("entry-exit experiments are long only on adjusted prices")
        return self

    def config_hash(self) -> str:
        d = self.model_dump(mode="json")
        for k in ("name", "notes"):
            d.pop(k, None)
        return canonical_hash(d)

    def methodology(self) -> dict:
        """How a cell is measured (not which entries, exits or data)."""
        return dict(sampling=self.sampling.model_dump(), eligibility=self.eligibility.model_dump(),
                    matching=self.matching.model_dump(), max_hold=self.max_hold, ref_atr_n=self.ref_atr_n,
                    ref_atr_mult=self.ref_atr_mult, execution=self.execution.model_dump(mode="json"),
                    trade_notional=self.trade_notional, controls=self.controls.model_dump(),
                    portfolio=self.portfolio.model_dump(mode="json"), stats=self.stats.model_dump(),
                    timing=self.timing.model_dump(),
                    protective=[c.model_dump(mode="json") for c in self.protective], seed=self.seed)

    def methodology_hash(self) -> str:
        return canonical_hash(self.methodology())


__all__ = ["EntryExitExperimentConfig", "ExitGrid", "Controls", "Portfolio", "WalkForward", "Stats", "MAX_CELLS"]
