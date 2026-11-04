"""SignalExperimentConfig: everything an event study depends on. The store saves the *resolved* config (dataset
version pinned, universe expanded, every signal instance listed with its key, partitions explicit, seed fixed), so
re-running that file reproduces the experiment exactly."""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from strategy_lab.core.strategy import canonical_hash
from strategy_lab.signals.base import CombinationSpec, SignalSpec

SpecItem = Annotated[Union[SignalSpec, CombinationSpec], Field(discriminator="kind")]
MAX_SIGNALS = 600
MAX_HORIZON = 252


class GridSpec(BaseModel):
    """A parameter comparison: one catalog signal, one instance per combination of the listed values."""
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict, description="fixed overrides")
    grid: dict[str, list[Any]] = Field(min_length=1)

    @field_validator("grid")
    @classmethod
    def _grid(cls, v):
        for k, vals in v.items():
            if not vals:
                raise ValueError(f"grid {k}: give at least one value")
        return v


class Sampling(BaseModel):
    """Which triggers become events. all: every triggering session. first: the first session of each run of
    consecutive triggers. cooldown: after an event, the symbol's triggers in the next `cooldown` sessions are
    skipped. non_overlapping: per horizon h, an event only when the symbol's previous event is at least h sessions
    earlier, so one symbol's holding periods never overlap."""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["all", "first", "cooldown", "non_overlapping"] = "first"
    cooldown: int = Field(0, ge=0, le=252)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "cooldown" and self.cooldown < 1:
            raise ValueError("sampling mode cooldown needs cooldown >= 1")
        return self


class Matching(BaseModel):
    """Matched-random controls: same session, same volatility group (cross-sectional quantile of trailing
    volatility, known at t), plus the optional dimensions. A pool smaller than min_pool leaves the event unmatched:
    it is reported, never matched on a relaxed rule."""
    model_config = ConfigDict(extra="forbid")
    volatility: bool = True
    vol_window: int = Field(63, ge=10, le=252)
    vol_groups: int = Field(10, ge=2, le=50)
    sector: bool = False
    liquidity: bool = False
    liquidity_groups: int = Field(3, ge=2, le=10)
    price: bool = False
    price_groups: int = Field(3, ge=2, le=10)
    market_cap: bool = False
    market_cap_groups: int = Field(3, ge=2, le=10)
    min_pool: int = Field(10, ge=1, le=1000)


class RandomDates(BaseModel):
    """Random-entry-date controls: the same symbol on random eligible sessions of the same partition whose holding
    period completes inside it; with same_vol_regime, only sessions on which the symbol was in the same volatility
    group as on the event."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    same_vol_regime: bool = True
    min_pool: int = Field(20, ge=1, le=5000)


class Eligibility(BaseModel):
    """Point-in-time eligibility on t, applied identically to signal events and to every control."""
    model_config = ConfigDict(extra="forbid")
    min_price: float = Field(5.0, ge=0, description="raw close on t")
    min_dollar_volume: float = Field(1_000_000.0, ge=0, description="median raw close x volume, last 21 sessions")
    min_history: int = Field(126, ge=1, le=1000, description="valid bars up to and including t")


class Partition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=40)
    role: Literal["exploration", "train", "validation", "test"]
    start: dt.date
    end: dt.date


class Costs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round_trip_bps: float = Field(0.0, ge=0, le=1000, description="subtracted from each event's return for net "
                                                                   "figures (signal side only: controls are gross)")


class Statistics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bootstrap: int = Field(1000, ge=0, le=20000)
    alpha: float = Field(0.05, gt=0, lt=0.5)
    economic_annual_excess: float = Field(0.02, ge=0, le=1.0)
    min_events: int = Field(30, ge=5, le=100000)
    significance: Literal["randomization_and_bootstrap", "randomization", "bootstrap"] = "randomization_and_bootstrap"
    p_value: Literal["empirical", "normal_approx"] = "empirical"


DEFAULT_PARTITIONS = (
    ("train", "train", dt.date(2019, 1, 1), dt.date(2022, 12, 31)),
    ("validation", "validation", dt.date(2023, 1, 1), dt.date(2024, 12, 31)),
    ("test", "test", dt.date(2025, 1, 1), dt.date(2025, 12, 31)),
)


class SignalExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=200)
    notes: str = Field("", max_length=4000)
    signals: list[SpecItem] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    grids: list[GridSpec] = Field(default_factory=list)
    dataset: str
    dataset_version: str | None = None
    universe: list[str] | None = None
    universe_name: str | None = None
    start: dt.date
    end: dt.date
    frequency: Literal["1d", "1w", "1mo"] = "1d"
    horizons: list[int] = Field(default_factory=lambda: [1, 5, 10, 20, 60])
    primary_horizon: int | None = None
    entry: Literal["next_open", "next_close"] = "next_open"
    sampling: Sampling = Field(default_factory=Sampling)
    matching: Matching = Field(default_factory=Matching)
    random_dates: RandomDates = Field(default_factory=RandomDates)
    permutations: int = Field(1000, ge=100, le=20000)
    benchmarks: list[str] = Field(default_factory=lambda: ["SPY"])
    sector_benchmark: bool = True
    eligibility: Eligibility = Field(default_factory=Eligibility)
    partition_scheme: Literal["default", "none", "custom"] = "default"
    partitions: list[Partition] = Field(default_factory=list)
    evaluate_test: bool = False
    costs: Costs = Field(default_factory=Costs)
    stats: Statistics = Field(default_factory=Statistics)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    save_controls: int = Field(5, ge=0, le=50)
    workers: int = Field(2, ge=1, le=16)
    # filled by resolution (the saved config carries them; a user config leaves them empty)
    resolved_signals: list[dict] | None = None
    analysis_end: dt.date | None = None

    @field_validator("horizons")
    @classmethod
    def _horizons(cls, v):
        out = sorted({int(h) for h in v})
        if not out or out[0] < 1 or out[-1] > MAX_HORIZON:
            raise ValueError(f"horizons must be between 1 and {MAX_HORIZON} sessions")
        if len(out) > 12:
            raise ValueError("at most 12 horizons")
        return out

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
        return out[:6]

    @model_validator(mode="after")
    def _check(self):
        if self.start > self.end:
            raise ValueError("start is after end")
        if not self.universe and not self.universe_name:
            raise ValueError("give a universe (symbols) or a universe_name")
        if not (self.signals or self.families or self.grids or self.resolved_signals):
            raise ValueError("select at least one signal, family or parameter grid")
        if self.primary_horizon is not None and self.primary_horizon not in self.horizons:
            raise ValueError("primary_horizon must be one of the horizons")
        if self.partition_scheme == "custom" and not self.partitions:
            raise ValueError("partition_scheme custom needs partitions")
        return self

    def config_hash(self) -> str:
        d = self.model_dump(mode="json")
        for k in ("name", "notes", "workers"):
            d.pop(k, None)
        return canonical_hash(d)

    def methodology(self) -> dict:
        """The settings that define how a signal is measured (not which signal or which data): two experiments with
        the same methodology hash measure signals the same way."""
        return dict(frequency=self.frequency, horizons=self.horizons, entry=self.entry,
                    sampling=self.sampling.model_dump(), matching=self.matching.model_dump(),
                    random_dates=self.random_dates.model_dump(), permutations=self.permutations,
                    benchmarks=self.benchmarks, sector_benchmark=self.sector_benchmark,
                    eligibility=self.eligibility.model_dump(), costs=self.costs.model_dump(),
                    stats=self.stats.model_dump(), seed=self.seed)

    def methodology_hash(self) -> str:
        return canonical_hash(self.methodology())
