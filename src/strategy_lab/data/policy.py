"""Research-safety data policy (docs/ARCHITECTURE.md 3.3, ClaudeWorkspace docs/RESEARCH_SAFETY.md).

HOLDOUT starts 2026-01-01. No bar on or after it may enter Strategy Lab in this workspace: the ceiling is enforced at
ingest and in every query, and configuration can lower it but never raise it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

HOLDOUT_START = dt.date(2026, 1, 1)
HARD_CEILING = HOLDOUT_START - dt.timedelta(days=1)

# Research periods of the flag research (RESEARCH_SAFETY.md): every result is labelled with the ones it overlaps.
PERIODS = (
    ("BACK", dt.date(2010, 1, 1), dt.date(2018, 12, 31)),
    ("IS", dt.date(2019, 1, 1), dt.date(2022, 12, 31)),
    ("OOS", dt.date(2023, 1, 1), dt.date(2025, 12, 31)),
)


class PolicyError(ValueError):
    pass


def to_date(v) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if hasattr(v, "date") and callable(v.date):          # pandas Timestamp
        return v.date()
    return dt.date.fromisoformat(str(v)[:10])


@dataclass(frozen=True)
class DataPolicy:
    history_floor: dt.date = dt.date(2018, 1, 1)    # earliest bar loaded (indicator warm-up only)
    backtest_floor: dt.date = dt.date(2019, 1, 1)   # earliest backtest start
    ceiling: dt.date = HARD_CEILING                 # last bar ever loaded

    def __post_init__(self):
        if self.ceiling > HARD_CEILING:
            raise PolicyError(f"data ceiling {self.ceiling} is after {HARD_CEILING}: HOLDOUT data "
                              f"(>= {HOLDOUT_START}) may not enter this workspace")
        if not self.history_floor <= self.backtest_floor <= self.ceiling:
            raise PolicyError("need history_floor <= backtest_floor <= ceiling")

    @classmethod
    def from_settings(cls, d: dict) -> "DataPolicy":
        return cls(history_floor=to_date(d.get("history_floor", "2018-01-01")),
                   backtest_floor=to_date(d.get("backtest_floor", "2019-01-01")),
                   ceiling=to_date(d.get("ceiling", HARD_CEILING.isoformat())))

    def check_range(self, start, end) -> tuple[dt.date, dt.date]:
        s, e = to_date(start), to_date(end)
        if s > e:
            raise PolicyError(f"start {s} is after end {e}")
        if s < self.backtest_floor:
            raise PolicyError(f"start {s} is before the backtest floor {self.backtest_floor}")
        if e > self.ceiling:
            raise PolicyError(f"end {e} is after the data ceiling {self.ceiling} (HOLDOUT starts {HOLDOUT_START})")
        return s, e

    def to_dict(self) -> dict:
        return dict(history_floor=self.history_floor.isoformat(), backtest_floor=self.backtest_floor.isoformat(),
                    ceiling=self.ceiling.isoformat(), holdout_start=HOLDOUT_START.isoformat())


def period_labels(start, end) -> list[str]:
    s, e = to_date(start), to_date(end)
    return [name for name, lo, hi in PERIODS if s <= hi and e >= lo]
