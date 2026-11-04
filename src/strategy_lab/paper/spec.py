"""DeploymentSpec: a strategy configuration frozen for forward paper testing. Its key (a hash of everything that
decides trades) makes deployment idempotent and names the paper book; any change is a new key, so a new book."""
from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from strategy_lab.core.strategy import canonical_hash
from strategy_lab.engine.config import ExecutionConfig


class DeploymentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("", max_length=200)
    strategy: str
    strategy_version: str
    params: dict[str, Any] = Field(default_factory=dict)
    universe: list[str]
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    initial_capital: float = Field(100_000.0, gt=0, le=1e12)
    start_date: dt.date
    seed: int = 0

    @field_validator("universe")
    @classmethod
    def _universe(cls, v):
        out = sorted({str(s).strip() for s in v if str(s).strip()})
        if not out:
            raise ValueError("universe is empty")
        return out

    def key(self) -> str:
        d = self.model_dump(mode="json")
        d.pop("name", None)
        return canonical_hash(d)

    def book_id(self) -> str:
        return f"sl-{self.key()[:10]}"
