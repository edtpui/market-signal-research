"""Strategy Lab inside ptrade: a PortfolioRule and a ScoreProducer (ptrade.book_registry contracts, mirrored in
paper/contract.py) that run a DeploymentSpec through the same StrategyRunner as a backtest.

ptrade calls, each night D, for a registered book with start S:
  PRODUCER.scores(D, ctx)                     one candidate row per universe symbol with a bar on D, so that ptrade
                                              builds Frames for the whole universe (it builds them only for tickers
                                              that appear among the stored candidates)
  RULE.simulate(cands, frames, S, D, params)  the whole book S..D, re-run in full; days already stored must come out
                                              the same (ptrade records any difference as a restatement)
  RULE.pending(today, held, params)           the orders submitted at D's close, eligible at the next session

Data mapping: a session has a bar for a symbol only where ptrade's open exists (ptrade: no open, no trade), and then
its close is the adjusted close; ptrade's forward-filled closes are only used for marks, which is also what the
engine does with a missing bar. Units: ptrade basket books are equity indices (1.0 before the start), so shares,
values and costs are divided by the spec's capital. Prices include slippage and spread; cost is the commission.
"""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from strategy_lab.core.market import MarketData
from strategy_lab.core.types import OrderStatus
from strategy_lab.engine.runner import RunOutput, StrategyRunner
from strategy_lab.paper import contract as C
from strategy_lab.paper.spec import DeploymentSpec


def frames_to_market_data(fr: C.Frames, universe: list[str], end) -> MarketData:
    sessions = fr.cal[fr.cal <= pd.Timestamp(end)]
    opens = fr.opens.reindex(index=sessions, columns=universe).astype(float)
    closes = fr.closes.reindex(index=sessions, columns=universe).astype(float).where(opens.notna())
    opens.index.name = closes.index.name = "date"
    opens.columns.name = closes.columns.name = None
    return MarketData({"open": opens, "close": closes}, price_mode="adjusted")


def to_book_run(out: RunOutput, capital: float) -> C.BookRun:
    days = pd.DataFrame([d.__dict__ for d in out.days])
    idx = pd.DatetimeIndex(pd.to_datetime(days["timestamp"]))
    eq = days["equity"].to_numpy() / capital
    prev = np.concatenate([[1.0], eq[:-1]])
    daily = pd.DataFrame(dict(equity=eq, ret=eq / prev - 1,
                              gross=((days["long_value"] - days["short_value"]) / days["equity"]).to_numpy(),
                              hedge=0.0, n=days["n_positions"].astype(int).to_numpy(), m=1.0), index=idx)
    trades, picks, pos = [], [], {}
    for f in out.fills:
        before = pos.get(f.symbol, 0.0)
        after = before + f.quantity
        pos[f.symbol] = after
        if f.quantity > 0:
            kind = "buy"
            picks.append(dict(date=pd.Timestamp(f.timestamp), ticker=f.symbol, renew=int(before > 0)))
        else:
            kind = "exit" if abs(after) < 1e-9 else "trim"
        trades.append(dict(date=pd.Timestamp(f.timestamp), ticker=f.symbol, kind=kind, shares=f.quantity / capital,
                           price=f.price, value=abs(f.quantity) * f.price / capital, cost=f.commission / capital))
    holdings = [dict(asof=pd.Timestamp(h["timestamp"]), ticker=h["symbol"], shares=h["quantity"] / capital,
                     close=h["close"], value=h["market_value"] / capital, weight=h["weight"], exit_date=pd.NaT)
                for h in out.holdings]
    return C.BookRun(daily=daily, trades=pd.DataFrame(trades, columns=C.TRADE_COLS),
                     holdings=pd.DataFrame(holdings, columns=C.HOLD_COLS),
                     picks=pd.DataFrame(picks, columns=["date", "ticker", "renew"])).check()


def pending_from(out: RunOutput, capital: float, end) -> list[dict]:
    """Orders submitted at the end session's close (eligible next session), in submission order."""
    end_s = pd.Timestamp(end).date().isoformat()
    marks = {h["symbol"]: h["close"] for h in out.holdings if h["timestamp"] == end_s}
    eq = out.days[-1].equity if out.days else capital
    rows = []
    for o in out.orders:
        if o.created_at != end_s or o.status != OrderStatus.SUBMITTED:
            continue
        mark = marks.get(o.symbol)
        # pred: the order's signed size as a fraction of equity (at the held mark, else in index shares)
        pred = o.quantity * mark / eq if mark else o.quantity / capital
        rows.append(dict(rank=len(rows) + 1, ticker=o.symbol, pred=float(pred), renew=int(o.symbol in marks)))
    return rows


class StrategyLabRule:
    """PortfolioRule for one deployment."""

    def __init__(self, spec: DeploymentSpec, registry):
        self.spec = spec
        self.cls = registry.get(spec.strategy, spec.strategy_version)
        self.cls.resolve_params(spec.params)
        self.name = spec.book_id()
        self._pending: list[dict] = []

    def params(self) -> dict:
        return {"deployment": self.spec.key()[:16]}

    def _check_params(self, params: dict) -> None:
        if dict(params) != self.params():
            raise ValueError(f"{self.name}: book params {params!r} do not match this plug-in's deployment "
                             f"{self.params()!r}")

    def run(self, frames: C.Frames, start, end) -> RunOutput:
        md = frames_to_market_data(frames, self.spec.universe, end)
        strat = self.cls(self.spec.params)
        return StrategyRunner(strat, md, self.spec.execution, self.spec.initial_capital, pd.Timestamp(start),
                              pd.Timestamp(end), tuple(self.spec.universe), seed=self.spec.seed).run()

    def simulate(self, cands: pd.DataFrame, frames: C.Frames, start, end, params: dict) -> C.BookRun:
        self._check_params(params)
        out = self.run(frames, start, end)
        self._pending = pending_from(out, self.spec.initial_capital, end)
        return to_book_run(out, self.spec.initial_capital)

    def pending(self, today: pd.DataFrame, held: set, params: dict) -> list[dict]:
        self._check_params(params)
        return list(self._pending)


class UniverseProducer:
    """ScoreProducer: one placeholder candidate per universe symbol with a bar on asof (pred 0)."""

    def __init__(self, spec: DeploymentSpec):
        self.spec = spec
        self.name = f"{spec.book_id()}-universe"
        # the candidate table's key is (asof, ticker, direction) and every producer of a ledger shares it, so the
        # direction carries the book id: two deployments with overlapping universes must never collide
        self.direction = spec.book_id()

    def config(self) -> dict:
        return dict(producer=self.name, universe_sha256=hashlib.sha256(",".join(self.spec.universe).encode())
                    .hexdigest(), deployment=self.spec.key())

    def scores(self, asof, ctx) -> pd.DataFrame:
        asof = pd.Timestamp(asof)
        later = ctx.cal[ctx.cal > asof]
        if len(later) == 0:
            raise ValueError(f"{self.name}: the calendar has no session after {asof.date()}")
        nxt = later[0]
        rows = []
        for t in self.spec.universe:
            bars = ctx.data.bars.get(t)
            if bars is None or asof not in bars[0].index:
                continue
            o, c = float(bars[0].at[asof, "Open"]), float(bars[0].at[asof, "Close"])
            if not (np.isfinite(o) and np.isfinite(c)):
                continue
            rows.append(dict(ticker=t, bucket=ctx.buckets.get(t, "na"), direction=self.direction, date=asof,
                             entry_date=nxt,
                             exit_date=nxt, pred=0.0, vol20=np.nan,
                             features_sha256=hashlib.sha256(f"{t}|{asof.date()}|{o!r}|{c!r}".encode()).hexdigest()))
        df = pd.DataFrame(rows, columns=[*C.SCORE_COLUMNS, *C.STORED_EXTRA])
        return C.check_scores(df, asof)
