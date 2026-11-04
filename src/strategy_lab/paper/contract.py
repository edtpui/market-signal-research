"""A mirror of the parts of ptrade's basket contract the Strategy Lab plug-in targets, so the plug-in is built and
tested here without importing operational code. Source: QwenWorkspace/paper_trading at commit 792758b (read-only),
ptrade/book_registry.py (Frames, BookRun.check, SCORE_COLUMNS, TRADE_KINDS, check_scores) and baskets/run.py
(run_book: the nightly full re-run, restatement compare, store_candidates' features_sha256).
scripts/check_ptrade_contract.py re-hashes those files read-only and reports drift; any drift means this mirror
needs review before an export is applied.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

SOURCE_COMMIT = "792758b"
SOURCE_SHA256 = {
    "ptrade/book_registry.py": "842f0391ed5df53705ac719f2194fd84cd402961c8101ded7008fbf04315fda1",
    "baskets/plugins/__init__.py": "7fd9759312cce3856ca5fd282f4565f6e15dd32375dd7abbe5bcda0bab8fa430",
    "baskets/run.py": "30741466947c133e67e6a48abf130e02fce63ac3cb1c38d6a02911592c7292b9",
    "baskets/frames.py": "40a7341e50fdde678a8a26aae9d471c09d214eb7c07bbf3cd48539b702e8a1ae",
    "baskets/books.py": "008e329a5e0109c95225c1db3f113b27562eeb2ae9b219128417b4817c0dc32b",
    "baskets/ledger.py": "49475cc740f5820da7332c5dc7f5be1451d98816aa80b819d0dfa94207aa74ef",
    "gui/v1/schemas.py": "ce2fdce5d415ddc58dd091c0bb6f2b51cc512cdbe92169ab225c9210f0bb68c6",
    "web/openapi.json": "f26b60faa5a75a350bef95bca1f283cb3cc7f12f3914a5b8d01509713c0807f3",
}

SCORE_COLUMNS = ("ticker", "bucket", "direction", "date", "entry_date", "exit_date", "pred", "vol20")
STORED_EXTRA = ("features_sha256",)            # baskets.run.store_candidates reads it from the producer's frame
TRADE_KINDS = ("buy", "trim", "exit", "hedge")
TRADE_COLS = ["date", "ticker", "kind", "shares", "price", "value", "cost"]
HOLD_COLS = ["asof", "ticker", "shares", "close", "value", "weight", "exit_date"]
COMPARED = ("equity", "ret", "gross", "hedge")   # fields the nightly re-run must reproduce for stored days
TOL = 1e-9
AHEAD = 60                                       # sessions the calendar runs past asof (baskets.frames.AHEAD)
FIRST_BAR = "2018-01-02"                         # baskets.run.FIRST_BAR


@dataclass
class Frames:
    """Adjusted closes (forward-filled) and opens (not filled: no open, no trade), date x ticker, SPY's adjusted
    close, and the calendar, which runs AHEAD sessions past the end."""
    closes: pd.DataFrame
    opens: pd.DataFrame
    spy_close: pd.Series
    cal: pd.DatetimeIndex


@dataclass
class BookRun:
    daily: pd.DataFrame
    trades: pd.DataFrame
    holdings: pd.DataFrame
    picks: pd.DataFrame

    def check(self) -> "BookRun":
        need = {"daily": ("equity", "ret", "gross", "hedge", "n", "m"),
                "trades": tuple(TRADE_COLS), "holdings": tuple(HOLD_COLS), "picks": ("date", "ticker", "renew")}
        for name, cols in need.items():
            missing = set(cols) - set(getattr(self, name).columns)
            if missing:
                raise ValueError(f"BookRun.{name} lacks {sorted(missing)}")
        if not self.daily["equity"].map(lambda v: math.isfinite(v) and v > 0).all():
            raise ValueError("BookRun.daily: equity must be finite and positive every day")
        if not self.trades["kind"].isin(TRADE_KINDS).all():
            raise ValueError(f"BookRun.trades: kind must be one of {TRADE_KINDS}")
        return self


def check_scores(df: pd.DataFrame, asof) -> pd.DataFrame:
    missing = set(SCORE_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"scores lack {sorted(missing)}")
    if df.empty:
        return df
    asof = pd.Timestamp(asof)
    bad = {"date != asof": (df["date"] != asof).any(), "entry_date <= asof": (df["entry_date"] <= asof).any(),
           "exit_date < entry_date": (df["exit_date"] < df["entry_date"]).any(),
           "non-finite pred": (~df["pred"].map(lambda v: math.isfinite(float(v)))).any(),
           "duplicate (ticker, direction)": df.duplicated(["ticker", "direction"]).any()}
    problems = [k for k, v in bad.items() if v]
    if problems:
        raise ValueError(f"scores for {asof.date()}: {', '.join(problems)}")
    return df


# What the ptrade host offers a plug-in: deploy-time compatibility (docs/ARCHITECTURE.md 3.4).
HOST_FIELDS = frozenset({"open", "close"})
HOST_ORDER_TYPES = frozenset({"market"})
HOST_FREQUENCY = "1d"
HOST_PRICE_MODE = "adjusted"
