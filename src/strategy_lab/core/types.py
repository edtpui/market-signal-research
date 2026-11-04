"""Domain types shared by every mode: order intents (what a strategy asks for), orders and their lifecycle, fills.

A strategy never creates Orders directly. It returns intents; the runner turns them into Orders, and a broker
(simulated or the paper host) decides fills. Quantities are signed share counts: buy > 0, sell < 0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


class LookaheadError(ValueError):
    """A request would expose data after the current simulation timestamp."""


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class TimeInForce(str, Enum):
    DAY = "day"      # eligible for one session, then expires
    GTC = "gtc"      # good until cancelled


class OrderStatus(str, Enum):
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED)


@dataclass(frozen=True)
class OrderIntent:
    """A request for a share quantity. quantity is signed (buy > 0, sell < 0) and never zero."""
    symbol: str
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    tif: TimeInForce = TimeInForce.GTC
    tag: str = ""

    def __post_init__(self):
        if not self.symbol:
            raise ValueError("OrderIntent needs a symbol")
        if not math.isfinite(self.quantity) or self.quantity == 0:
            raise ValueError(f"OrderIntent {self.symbol}: quantity must be finite and non-zero, got {self.quantity}")
        if self.order_type == OrderType.LIMIT and not _positive(self.limit_price):
            raise ValueError(f"OrderIntent {self.symbol}: a limit order needs a positive limit_price")
        if self.order_type == OrderType.STOP and not _positive(self.stop_price):
            raise ValueError(f"OrderIntent {self.symbol}: a stop order needs a positive stop_price")

    @staticmethod
    def market(symbol: str, quantity: float, tag: str = "", tif: TimeInForce = TimeInForce.DAY) -> "OrderIntent":
        """Default DAY: a market order whose next session has no bar is expired with a reason, never carried
        silently (the ptrade convention: a name without a valid open is passed over)."""
        return OrderIntent(symbol, quantity, OrderType.MARKET, tif=tif, tag=tag)

    @staticmethod
    def limit(symbol: str, quantity: float, price: float, tif: TimeInForce = TimeInForce.DAY,
              tag: str = "") -> "OrderIntent":
        return OrderIntent(symbol, quantity, OrderType.LIMIT, limit_price=price, tif=tif, tag=tag)

    @staticmethod
    def stop(symbol: str, quantity: float, price: float, tif: TimeInForce = TimeInForce.GTC,
             tag: str = "") -> "OrderIntent":
        return OrderIntent(symbol, quantity, OrderType.STOP, stop_price=price, tif=tif, tag=tag)


@dataclass(frozen=True)
class TargetWeights:
    """Rebalance to these weights of equity (at the decision close) with market orders. Symbols held but absent are
    closed when close_others is True. Weights are signed; their absolute sum is checked against max_gross."""
    weights: dict[str, float]
    close_others: bool = True
    tag: str = "rebalance"

    def __post_init__(self):
        for s, w in self.weights.items():
            if not s or not math.isfinite(w):
                raise ValueError(f"TargetWeights: bad weight {s!r}={w!r}")


@dataclass(frozen=True)
class CancelIntent:
    order_id: str


Intent = OrderIntent | TargetWeights | CancelIntent


@dataclass
class Order:
    order_id: str
    symbol: str
    quantity: float                    # signed, as requested
    order_type: OrderType
    tif: TimeInForce
    created_at: str                    # session (ISO date) whose close decided it
    limit_price: float | None = None
    stop_price: float | None = None
    tag: str = ""
    status: OrderStatus = OrderStatus.SUBMITTED
    filled_quantity: float = 0.0       # signed
    avg_fill_price: float | None = None
    sessions_eligible: int = 0
    reason: str = ""

    @property
    def remaining(self) -> float:
        return self.quantity - self.filled_quantity

    def to_row(self) -> dict:
        return dict(order_id=self.order_id, symbol=self.symbol, quantity=self.quantity,
                    order_type=self.order_type.value, tif=self.tif.value, created_at=self.created_at,
                    limit_price=self.limit_price, stop_price=self.stop_price, tag=self.tag, status=self.status.value,
                    filled_quantity=self.filled_quantity, avg_fill_price=self.avg_fill_price, reason=self.reason)


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    quantity: float                    # signed
    price: float                       # execution price including slippage and spread
    reference_price: float             # the bar price the fill was based on (open, close, limit or stop)
    commission: float
    slippage_cost: float               # dollars paid to slippage + spread versus the reference price
    timestamp: str                     # session date of the fill
    reason: str = "fill"               # fill | partial

    @property
    def notional(self) -> float:
        return abs(self.quantity) * self.price

    def to_row(self) -> dict:
        return dict(fill_id=self.fill_id, order_id=self.order_id, symbol=self.symbol, quantity=self.quantity,
                    price=self.price, reference_price=self.reference_price, commission=self.commission,
                    slippage_cost=self.slippage_cost, timestamp=self.timestamp, reason=self.reason)


@dataclass(frozen=True)
class OrderEvent:
    """One lifecycle step of an order: submitted, partially_filled, filled, cancelled, rejected or expired."""
    order_id: str
    symbol: str
    status: OrderStatus
    timestamp: str
    detail: str = ""
    fill: Fill | None = None
    order_type: str = ""
    tag: str = ""

    def to_row(self) -> dict:
        return dict(order_id=self.order_id, symbol=self.symbol, status=self.status.value, timestamp=self.timestamp,
                    detail=self.detail, fill_id=self.fill.fill_id if self.fill else None, order_type=self.order_type,
                    tag=self.tag)


@dataclass
class Position:
    symbol: str
    quantity: float = 0.0
    avg_cost: float = 0.0              # average entry price of the open quantity
    last_price: float | None = None    # last valid close (the mark)
    last_price_date: str | None = None

    @property
    def market_value(self) -> float:
        return self.quantity * (self.last_price or 0.0)


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Read-only portfolio state handed to a strategy (after the session's close, before its decisions)."""
    timestamp: str
    cash: float
    equity: float
    positions: dict[str, Position] = field(default_factory=dict)

    @property
    def gross_exposure(self) -> float:
        return sum(abs(p.market_value) for p in self.positions.values())

    def quantity(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.quantity if p else 0.0

    def weight(self, symbol: str) -> float:
        p = self.positions.get(symbol)
        return p.market_value / self.equity if p and self.equity else 0.0

    def weights(self) -> dict[str, float]:
        return {s: self.weight(s) for s in self.positions}


def _positive(v) -> bool:
    return v is not None and math.isfinite(v) and v > 0
