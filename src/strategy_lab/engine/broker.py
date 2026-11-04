"""The simulated broker: order book, lifecycle and fills against the next session's bar (docs/ARCHITECTURE.md 3.1).

Deterministic: order ids are sequential; within a session sells execute before buys (cash from sells funds buys),
each side in submission order. Reference prices (engine/fills.py, shared with the entry-exit trade replay):
  market  the fill bar's open (next_open) or close (next_close)
  limit   buy: open if open <= limit, else limit if low <= limit; sell mirrored. Needs high/low.
  stop    buy: open if open >= stop, else stop if high >= stop; sell mirrored. Needs high/low.
The execution price adds adverse slippage and half the modelled spread; a limit fill never prices beyond its limit.

Attached exits (core.types.AttachedExit): an entry order may carry stop/limit legs. They are created when the entry
first fills (price = mult x fill price + add, quantity = fraction of the filled quantity) and, with next_open timing,
evaluated on the rest of that same bar after every other order, in the order given, then expire (DAY).
"""
from __future__ import annotations

import math

import numpy as np

from strategy_lab.core.market import MarketData
from strategy_lab.core.types import Fill, Order, OrderEvent, OrderIntent, OrderStatus, OrderType, TimeInForce
from strategy_lab.engine import fills as F
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.portfolio import EPS, Portfolio


class OrderRejected(Exception):
    pass


class SimulatedBroker:
    def __init__(self, data: MarketData, config: ExecutionConfig, portfolio: Portfolio):
        self.data = data
        self.cfg = config
        self.pf = portfolio
        self.orders: dict[str, Order] = {}
        self.open: list[str] = []
        self.fills: list[Fill] = []
        self.events: list[OrderEvent] = []
        self._n_orders = 0
        self._n_fills = 0
        self._has = data.available_fields
        self._col = {s: i for i, s in enumerate(data.symbols)}
        self._activated: list[str] = []          # attached exits created during the current execute()

    # -- submission ---------------------------------------------------------------------------------------------
    def submit(self, intent: OrderIntent, timestamp: str) -> Order:
        self._n_orders += 1
        o = Order(order_id=f"o{self._n_orders:06d}", symbol=intent.symbol, quantity=float(intent.quantity),
                  order_type=intent.order_type, tif=intent.tif, created_at=timestamp, limit_price=intent.limit_price,
                  stop_price=intent.stop_price, tag=intent.tag, attached=tuple(intent.attached))
        self.orders[o.order_id] = o
        reason = self._reject_reason(o)
        if reason:
            o.status, o.reason = OrderStatus.REJECTED, reason
            self._event(o, timestamp, reason)
            return o
        self.open.append(o.order_id)
        self._event(o, timestamp, f"{o.order_type.value} {o.quantity:+g}")
        return o

    def _reject_reason(self, o: Order) -> str:
        if o.symbol not in self._col:
            return f"{o.symbol} is not in the run's universe"
        if (o.order_type in (OrderType.LIMIT, OrderType.STOP) or o.attached) and \
                not {"high", "low", "open"} <= self._has:
            return "limit/stop orders need open, high and low bars, which this data source does not provide"
        if not self.cfg.fractional_shares and not float(o.quantity).is_integer():
            return f"fractional quantity {o.quantity} with fractional_shares off"
        return ""

    def cancel(self, order_id: str, timestamp: str, reason: str = "cancelled by strategy") -> bool:
        o = self.orders.get(order_id)
        if o is None or o.status.terminal:
            return False
        o.status, o.reason = OrderStatus.CANCELLED, reason
        self.open.remove(order_id)
        self._event(o, timestamp, reason)
        return True

    def pending_quantity(self, symbol: str) -> float:
        return sum(self.orders[i].remaining for i in self.open if self.orders[i].symbol == symbol)

    def open_orders(self) -> tuple[Order, ...]:
        return tuple(self.orders[i] for i in self.open)

    # -- execution ----------------------------------------------------------------------------------------------
    def execute(self, i: int) -> list[OrderEvent]:
        """Fills every order eligible at session position i (all open orders: they were submitted before it)."""
        ts = self.data.index[i].date().isoformat()
        start = len(self.events)
        ids = list(self.open)
        sells = [x for x in ids if self.orders[x].quantity < 0]
        buys = [x for x in ids if self.orders[x].quantity > 0]
        self._activated = []
        for oid in sells + buys:
            self._execute_one(self.orders[oid], i, ts)
        # attached exits of entries filled at this bar's open: the rest of the bar, in creation order
        for oid in self._activated:
            self._execute_one(self.orders[oid], i, ts)
        self._activated = []
        return self.events[start:]

    def _execute_one(self, o: Order, i: int, ts: str) -> None:
        o.sessions_eligible += 1
        self._try_fill(o, i, ts)
        if o.status.terminal:
            self.open.remove(o.order_id)
        elif o.tif == TimeInForce.DAY:
            o.status = OrderStatus.EXPIRED
            o.reason = o.reason or "day order not (fully) filled"
            self.open.remove(o.order_id)
            self._event(o, ts, o.reason)

    def _bar(self, name: str, i: int, col: int) -> float:
        return float(self.data.array(name)[i, col]) if name in self._has else math.nan

    def _reference(self, o: Order, i: int, col: int) -> float | None:
        buy = o.quantity > 0
        if o.order_type == OrderType.MARKET:
            return F.scalar(F.reference_price("market", buy, self._bar("open", i, col), cl=self._bar("close", i, col),
                                              timing=self.cfg.fill_timing))
        lvl = o.limit_price if o.order_type == OrderType.LIMIT else o.stop_price
        return F.scalar(F.reference_price(o.order_type.value, buy, self._bar("open", i, col), self._bar("high", i, col),
                                          self._bar("low", i, col), price=lvl))

    def _half_spread_bps(self, i: int, col: int) -> float:
        sp = self.cfg.spread
        if sp.model == "fixed_bps":
            return float(F.half_spread_bps(sp.model, sp.bps))
        if sp.model == "hl_range" and i > 0:
            return float(F.half_spread_bps(sp.model, sp.bps, sp.hl_fraction,
                                           *(self._bar(f, i - 1, col) for f in ("high", "low", "close"))))
        return 0.0

    def _round(self, q: float) -> float:
        if self.cfg.fractional_shares:
            return q
        return float(math.floor(abs(q) + 1e-9)) * (1 if q > 0 else -1)

    def _try_fill(self, o: Order, i: int, ts: str) -> None:
        col = self._col[o.symbol]
        ref = self._reference(o, i, col)
        if ref is None:
            if o.order_type == OrderType.MARKET and not self._has_bar(i, col):
                o.reason = f"no bar for {o.symbol} on {ts}"
            return
        buy = o.quantity > 0
        price = float(F.execution_price(ref, buy, self.cfg.slippage_bps, self._half_spread_bps(i, col),
                                        o.limit_price if o.order_type == OrderType.LIMIT else None))
        qty = o.remaining
        if self.cfg.participation_cap is not None:
            vol = self._bar("volume", i, col)
            cap = self.cfg.participation_cap * vol if math.isfinite(vol) and vol > 0 else 0.0
            if abs(qty) > cap:
                qty = math.copysign(cap, qty)
                if not self.cfg.fractional_shares:
                    qty = self._round(qty)
                if qty == 0:
                    o.reason = f"no volume capacity on {ts}"
                    return
        if not buy and not self.cfg.allow_short:
            held = self.pf.quantity(o.symbol)
            if held <= EPS:
                return self._reject(o, ts, "short selling is disabled and nothing is held")
            if -qty > held:
                qty = -held
        if buy:
            qty = self._afford(o, qty, price, ts)
            if qty is None:
                return
        if qty == 0:
            return
        commission = self.cfg.commission.cost(qty, price)
        self._n_fills += 1
        partial = abs(o.remaining - qty) > EPS
        fill = Fill(fill_id=f"f{self._n_fills:06d}", order_id=o.order_id, symbol=o.symbol, quantity=qty, price=price,
                    reference_price=ref, commission=commission, slippage_cost=abs(price - ref) * abs(qty),
                    timestamp=ts, reason="partial" if partial else "fill")
        self.pf.apply_fill(fill)
        self.fills.append(fill)
        prev = o.filled_quantity
        o.filled_quantity += qty
        o.avg_fill_price = ((o.avg_fill_price or 0.0) * abs(prev) + price * abs(qty)) / abs(o.filled_quantity)
        if not partial:
            o.status = OrderStatus.FILLED
        elif not buy and not self.cfg.allow_short and self.pf.quantity(o.symbol) <= EPS:
            o.status, o.reason = OrderStatus.FILLED, "sell reduced to the held quantity"
        else:
            o.status = OrderStatus.PARTIALLY_FILLED
        self._event(o, ts, f"{qty:+g} @ {price:.4f}", fill)
        if o.attached and prev == 0:
            self._attach(o, fill, ts)

    def _attach(self, parent: Order, fill: Fill, ts: str) -> None:
        """Creates the parent's attached exits at its first fill (see the module docstring)."""
        legs, parent.attached = parent.attached, ()
        if self.cfg.fill_timing != "next_open":
            self._event(parent, ts, f"{len(legs)} attached exit(s) dropped: with next_close fills the session is over")
            return
        for leg in legs:
            q = -math.copysign(abs(fill.quantity) * leg.fraction, fill.quantity)
            if not self.cfg.fractional_shares:
                q = self._round(q)
            price = leg.price(fill.price)
            if q == 0 or not (math.isfinite(price) and price > 0):
                self._event(parent, ts, f"attached {leg.order_type.value} {leg.tag!r} skipped "
                                        f"(quantity {q:g}, price {price:.4f})")
                continue
            self._n_orders += 1
            child = Order(order_id=f"o{self._n_orders:06d}", symbol=parent.symbol, quantity=q,
                          order_type=leg.order_type, tif=TimeInForce.DAY, created_at=ts,
                          limit_price=price if leg.order_type == OrderType.LIMIT else None,
                          stop_price=price if leg.order_type == OrderType.STOP else None, tag=leg.tag,
                          parent_id=parent.order_id)
            self.orders[child.order_id] = child
            self.open.append(child.order_id)
            self._activated.append(child.order_id)
            self._event(child, ts, f"attached {child.order_type.value} {q:+g} @ {price:.4f} (parent "
                                   f"{parent.order_id})")

    def _afford(self, o: Order, qty: float, price: float, ts: str) -> float | None:
        need = qty * price + self.cfg.commission.cost(qty, price)
        if need <= self.pf.cash + EPS:
            return qty
        if self.cfg.cash_policy == "reject":
            self._reject(o, ts, f"insufficient cash: needs {need:,.2f}, has {self.pf.cash:,.2f}")
            return None
        q = qty
        for _ in range(4):                         # commission depends on quantity: converge in a few steps
            budget = max(self.pf.cash - self.cfg.commission.cost(q, price), 0.0)
            q = budget / price if self.cfg.fractional_shares else self._round(budget / price)
        while q > 0 and q * price + self.cfg.commission.cost(q, price) > self.pf.cash + EPS:
            q = q - 1 if not self.cfg.fractional_shares else q * 0.999
        if q <= 0:
            self._reject(o, ts, f"insufficient cash for one share at {price:,.4f}")
            return None
        return q

    def _has_bar(self, i: int, col: int) -> bool:
        return bool(np.isfinite(self.data.array("close")[i, col]))

    def _reject(self, o: Order, ts: str, reason: str) -> None:
        """An order that already filled in part has its remainder cancelled; otherwise it is rejected."""
        o.status = OrderStatus.CANCELLED if o.filled_quantity != 0 else OrderStatus.REJECTED
        o.reason = reason
        self._event(o, ts, reason)

    def _event(self, o: Order, ts: str, detail: str, fill: Fill | None = None) -> None:
        self.events.append(OrderEvent(order_id=o.order_id, symbol=o.symbol, status=o.status, timestamp=ts,
                                      detail=detail, fill=fill, order_type=o.order_type.value, tag=o.tag))

    # -- corporate actions --------------------------------------------------------------------------------------
    def apply_split(self, symbol: str, ratio: float, ts: str) -> None:
        """Open orders on a split symbol keep their value: quantity x ratio, prices / ratio."""
        for oid in self.open:
            o = self.orders[oid]
            if o.symbol != symbol:
                continue
            o.quantity *= ratio
            o.filled_quantity *= ratio
            if o.limit_price:
                o.limit_price /= ratio
            if o.stop_price:
                o.stop_price /= ratio
            if not self.cfg.fractional_shares:
                o.quantity = self._round(o.quantity) or math.copysign(1.0, o.quantity)
            self._event(o, ts, f"adjusted for a {ratio:g}:1 split")
