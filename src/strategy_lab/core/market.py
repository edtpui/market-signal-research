"""Market data containers. MarketData holds the full loaded window; MarketView is what a strategy sees: every
accessor is clipped at the current session (inclusive) and an explicit request past it raises LookaheadError.

Frames are wide (session index x symbol). A missing bar is NaN in every field: it is never forward-filled in the
strategy's view. The engine marks a held position at its last valid close and reports it as stale.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from strategy_lab.core.types import LookaheadError

PRICE_FIELDS = ("open", "high", "low", "close")
ALL_FIELDS = ("open", "high", "low", "close", "volume")


@dataclass
class MarketData:
    """A window of bars for a fixed symbol list on one session index.

    fields: name -> DataFrame (index = sessions, columns = symbols). Only the fields the source actually provides
    are present; a strategy asking for another one gets a KeyError, not a guess.
    actions: corporate actions applied by the engine in raw price mode (columns date, symbol, kind, value), where
    kind is 'split' (value = new shares per old share) or 'dividend' (value = cash per share, on the ex-date).
    """
    fields: dict[str, pd.DataFrame]
    actions: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["date", "symbol", "kind", "value"]))
    price_mode: str = "adjusted"
    dataset_id: str = ""
    dataset_version: str = ""

    def __post_init__(self):
        if "close" not in self.fields:
            raise ValueError("MarketData needs at least a close field")
        idx = self.fields["close"].index
        if not isinstance(idx, pd.DatetimeIndex) or not idx.is_monotonic_increasing or idx.has_duplicates:
            raise ValueError("MarketData: the session index must be a sorted, unique DatetimeIndex")
        cols = list(self.fields["close"].columns)
        for name, f in self.fields.items():
            if not f.index.equals(idx) or list(f.columns) != cols:
                raise ValueError(f"MarketData: field {name!r} is not aligned with close")
        self._idx = idx
        self._symbols = tuple(cols)
        self._np = {k: v.to_numpy(dtype=float, copy=True) for k, v in self.fields.items()}
        for a in self._np.values():
            a.setflags(write=False)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self._idx

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._symbols

    @property
    def available_fields(self) -> frozenset[str]:
        return frozenset(self.fields)

    def array(self, name: str) -> np.ndarray:
        """Read-only array of a field (engine use; strategies go through MarketView)."""
        return self._np[name]

    def col(self, symbol: str) -> int:
        return self._symbols.index(symbol)

    def actions_on(self, ts: pd.Timestamp) -> pd.DataFrame:
        if self.actions.empty:
            return self.actions
        return self.actions[self.actions["date"] == ts]

    def restrict(self, fields: tuple[str, ...]) -> "MarketData":
        """The same data with only `fields` (used to reproduce a forward host that supplies fewer fields)."""
        return MarketData({k: v for k, v in self.fields.items() if k in fields}, self.actions, self.price_mode,
                          self.dataset_id, self.dataset_version)


class MarketView:
    """The strategy's window onto MarketData at session position i (inclusive). Cheap to construct per session.

    derived: the strategy's precomputed causal series (Strategy.precompute), name -> (frame, read-only array) aligned
    to the session index; like the bars they are only visible up to and including session i."""

    def __init__(self, data: MarketData, i: int, derived: dict | None = None):
        if not 0 <= i < len(data.index):
            raise IndexError(f"session position {i} outside 0..{len(data.index) - 1}")
        self._d = data
        self._i = i
        self._derived = derived or {}
        self.timestamp: pd.Timestamp = data.index[i]

    @property
    def position(self) -> int:
        """Integer position of the current session in the loaded window (for derived arrays)."""
        return self._i

    def derived(self, name: str, bars: int | None = None) -> pd.DataFrame:
        """The last `bars` rows (all when None) of a precomputed series, up to and including now."""
        try:
            f = self._derived[name][0]
        except KeyError:
            raise KeyError(f"no precomputed series {name!r} (have {sorted(self._derived)})") from None
        lo = 0 if bars is None else max(0, self._i + 1 - int(bars))
        return f.iloc[lo: self._i + 1]

    def derived_now(self, name: str) -> np.ndarray:
        """Row i of a precomputed series as a read-only array (columns as precomputed)."""
        try:
            return self._derived[name][1][self._i]
        except KeyError:
            raise KeyError(f"no precomputed series {name!r} (have {sorted(self._derived)})") from None

    def derived_columns(self, name: str) -> list:
        return list(self._derived[name][0].columns)

    @property
    def symbols(self) -> tuple[str, ...]:
        return self._d.symbols

    @property
    def fields(self) -> frozenset[str]:
        return self._d.available_fields

    @property
    def sessions(self) -> pd.DatetimeIndex:
        """Sessions up to and including now."""
        return self._d.index[: self._i + 1]

    def _field(self, name: str) -> pd.DataFrame:
        try:
            return self._d.fields[name]
        except KeyError:
            raise KeyError(f"field {name!r} is not provided by this data source "
                           f"(available: {sorted(self._d.available_fields)})") from None

    def history(self, name: str, symbols=None, bars: int | None = None) -> pd.DataFrame:
        """The last `bars` sessions of a field up to now (all of them when bars is None), date x symbol."""
        f = self._field(name)
        lo = 0 if bars is None else max(0, self._i + 1 - int(bars))
        out = f.iloc[lo: self._i + 1]
        return out if symbols is None else out.loc[:, list(symbols)]

    def series(self, name: str, symbol: str, bars: int | None = None) -> pd.Series:
        return self.history(name, [symbol], bars)[symbol]

    def latest(self, name: str = "close") -> pd.Series:
        """The field at the current session (NaN where the symbol has no bar today)."""
        return self._field(name).iloc[self._i]

    def price(self, symbol: str) -> float:
        return float(self._d.array("close")[self._i, self._d.col(symbol)])

    def has_bar(self, symbol: str) -> bool:
        return bool(np.isfinite(self._d.array("close")[self._i, self._d.col(symbol)]))

    def bars_available(self, symbol: str) -> int:
        """Number of valid closes for symbol up to now."""
        return int(np.isfinite(self._d.array("close")[: self._i + 1, self._d.col(symbol)]).sum())

    def at(self, name: str, symbol: str, when) -> float:
        ts = pd.Timestamp(when)
        if ts > self.timestamp:
            raise LookaheadError(f"{name} of {symbol} on {ts.date()} is after the current session "
                                 f"{self.timestamp.date()}")
        f = self._field(name)
        if ts not in f.index:
            raise KeyError(f"no session {ts.date()}")
        return float(f.at[ts, symbol])

    def past(self, sessions_ago: int) -> "MarketView":
        """The view as it was `sessions_ago` sessions earlier (for replaying warm-up history); never a later one."""
        if sessions_ago < 0:
            raise LookaheadError("a view of a later session is not available")
        return MarketView(self._d, self._i - int(sessions_ago), self._derived)

    def window(self, start=None, end=None, name: str = "close") -> pd.DataFrame:
        if end is not None and pd.Timestamp(end) > self.timestamp:
            raise LookaheadError(f"window end {pd.Timestamp(end).date()} is after {self.timestamp.date()}")
        out = self.history(name)
        if start is not None:
            out = out[out.index >= pd.Timestamp(start)]
        if end is not None:
            out = out[out.index <= pd.Timestamp(end)]
        return out
