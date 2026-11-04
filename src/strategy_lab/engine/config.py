"""Execution assumptions: one typed, hashable config used by every mode (pydantic, so the API validates it too)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommissionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    per_share: float = Field(0.0, ge=0, description="dollars per share")
    bps: float = Field(0.0, ge=0, description="basis points of traded notional")
    per_order: float = Field(0.0, ge=0, description="fixed dollars per fill")
    minimum: float = Field(0.0, ge=0, description="minimum dollars per fill (applied when the sum is positive)")

    def cost(self, quantity: float, price: float) -> float:
        q = abs(quantity)
        c = self.per_order + self.per_share * q + q * price * self.bps / 1e4
        return max(c, self.minimum) if c > 0 else 0.0


class SpreadConfig(BaseModel):
    """No bid/ask data exists in the configured datasets, so the spread is modelled. fixed_bps: a constant full
    spread. hl_range: a fraction of the PREVIOUS bar's (high - low) / close range (known before the fill bar)."""
    model_config = ConfigDict(extra="forbid")
    model: Literal["none", "fixed_bps", "hl_range"] = "none"
    bps: float = Field(0.0, ge=0, description="full spread in bps (fixed_bps)")
    hl_fraction: float = Field(0.0, ge=0, le=1, description="share of the prior bar's range taken as full spread")


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fill_timing: Literal["next_open", "next_close"] = "next_open"
    slippage_bps: float = Field(0.0, ge=0, le=1000, description="adverse slippage on every fill, bps of reference")
    commission: CommissionConfig = Field(default_factory=CommissionConfig)
    spread: SpreadConfig = Field(default_factory=SpreadConfig)
    participation_cap: float | None = Field(None, gt=0, le=1, description="max share of the fill bar's volume")
    cash_policy: Literal["scale", "reject"] = "scale"
    allow_short: bool = False
    short_borrow_bps_per_year: float = Field(0.0, ge=0)
    fractional_shares: bool = False
    max_gross_leverage: float = Field(1.0, gt=0, le=4, description="cap on |weights| sum for TargetWeights")
    price_mode: Literal["adjusted", "raw"] = "adjusted"

    @model_validator(mode="after")
    def _check(self):
        if self.spread.model == "fixed_bps" and self.spread.bps <= 0:
            raise ValueError("spread.model fixed_bps needs spread.bps > 0")
        if self.spread.model == "hl_range" and self.spread.hl_fraction <= 0:
            raise ValueError("spread.model hl_range needs spread.hl_fraction > 0")
        return self

    def needs_fields(self) -> set[str]:
        need = {"open" if self.fill_timing == "next_open" else "close", "close"}
        if self.participation_cap is not None:
            need.add("volume")
        if self.spread.model == "hl_range":
            need |= {"high", "low"}
        return need
