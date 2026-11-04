"""ExperimentConfig: everything a backtest depends on. A run saves the *resolved* config (dataset version pinned,
universe expanded, strategy version fixed), so re-running that file reproduces the run."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from strategy_lab.core.strategy import canonical_hash
from strategy_lab.engine.config import ExecutionConfig


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=200)
    strategy: str
    strategy_version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    dataset: str
    dataset_version: str | None = None
    universe: list[str] | None = None
    universe_name: str | None = None
    start: dt.date
    end: dt.date
    frequency: Literal["1d", "1w", "1mo"] = "1d"
    initial_capital: float = Field(100_000.0, gt=0, le=1e12)
    benchmark: str | None = "SPY"
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    seed: int = 0
    notes: str = Field("", max_length=4000)

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

    @model_validator(mode="after")
    def _check(self):
        if self.start > self.end:
            raise ValueError("start is after end")
        if not self.universe and not self.universe_name:
            raise ValueError("give a universe (symbols) or a universe_name")
        return self

    def config_hash(self) -> str:
        d = self.model_dump(mode="json")
        d.pop("name", None)
        d.pop("notes", None)
        return canonical_hash(d)
