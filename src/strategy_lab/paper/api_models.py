"""Typed mirror of the ptrade API v1 responses the adapter reads (Ops/paper_trading gui/v1/schemas.py and
web/openapi.json at 792758b: the required field sets of Book, BookDetail, Evidence, Freshness, SeriesStyle,
LivePoint, AsOf, Picks and Equity). The client parses with extra fields allowed (an additive API change must not
break it); the mock host's responses are checked against the same models with extras forbidden."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

Row = dict[str, Any]
Point = list[Any]


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class Freshness(_M):
    last_asof: str | None
    expected_asof: str
    behind: bool


class LivePoint(_M):
    t: str
    pct: float | None = None
    r: float | None = None
    stale: bool = False


class SeriesStyle(_M):
    id: str
    name: str
    unit: str
    color: str
    default_color: str
    style: Literal["solid", "dashed", "dotted"]
    width: int
    visible: bool
    custom: bool


class Book(_M):
    id: str
    name: str
    family: Literal["bracket", "basket"]
    unit: Literal["R", "usd", "index"]
    model: str
    book: str | None
    ledger: str
    protocol: str
    history: list[str]
    status: Literal["active", "paused", "retired", "waiting", "unregistered"]
    model_id: str | None
    start_date: str | None
    last_asof: str | None
    value: float | None
    since_start: float | None
    day_change: float | None
    n_positions: int
    sparkline: list[Point]
    freshness: Freshness
    live: LivePoint | None
    series: SeriesStyle
    rule: str | None
    interim_months: int | None
    decision_months: int | None
    target_n: int | None


class Evidence(_M):
    protocol_path: str
    protocol_sha256: str | None
    registered_sha256: str | None
    registered_at_utc: str | None
    start_date: str | None
    months_elapsed: float | None
    interim_date: str | None
    decision_date: str | None
    target_n: int | None
    resolved_n: int | None
    rule: str | None
    deviations: list[str]
    events: list[Row]
    restatements: list[Row]


class BookDetail(Book):
    evidence: Evidence
    runs: list[Row]


class AsOf(_M):
    asof: str
    source: Literal["ledger", "history", "gap", "none"]
    run_id: str | None = None
    evidence: str | None = None
    note: str | None = None
    ledger_asof: str | None = None
    rows: list[Row]


class Picks(AsOf):
    entry_date: str | None = None


class Equity(_M):
    book_id: str
    unit: str
    start: str
    end: str
    points: list[Point]
    pct: list[Point]
    drawdown: list[Point]
    spy: list[Point]
    monthly: list[Row]
    days: list[Row]


def strict(model: type[BaseModel], data: dict) -> BaseModel:
    """Validate with extra fields forbidden at the top level (the mock must not invent fields)."""
    extra = set(data) - set(model.model_fields)
    if extra:
        raise ValueError(f"{model.__name__}: fields not in the ptrade API v1 schema: {sorted(extra)}")
    return model.model_validate(data)
