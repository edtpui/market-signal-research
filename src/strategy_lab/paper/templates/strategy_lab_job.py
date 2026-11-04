"""Strategy Lab books: their own ledger (settings [paths] ledger_strategy_lab; docs/protocols/sl-*.md), run by a
separate Task Scheduler task after the daily job, which is unchanged. Shipped by Strategy Lab's export bundle
(strategy_lab/paper/templates/strategy_lab_job.py); the same file serves every Strategy Lab book.

Same guard and step as the daily job's basket step (scripts/daily_job.py run_baskets, baskets.job.step): nothing runs
while the price store lacks asof's bar (the next run catches the session up), baskets.run runs in its own process
with a timeout, and the ledger is backed up afterwards (data/backups/ledger_strategy_lab_*.db, the newest 30).

python scripts/strategy_lab_job.py [ASOF]   prints one JSON line; exit 1 on an error, else 0 (ok, warn, idle, skipped)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))     # run as a script: make ptrade importable

import duckdb  # noqa: E402

from baskets import job  # noqa: E402
from ptrade import ops  # noqa: E402
from ptrade.config import resolve_path  # noqa: E402


def last_bar(prices_db: Path) -> str | None:
    con = duckdb.connect(str(prices_db), read_only=True)
    try:
        v = con.execute("SELECT MAX(date) FROM prices").fetchone()[0]
    finally:
        con.close()
    return str(v)[:10] if v is not None else None


def main(argv: list[str]) -> int:
    asof = str(argv[0])[:10] if argv else ops.last_completed_session()
    prices_db, ledger_path = resolve_path("prices"), resolve_path("ledger_strategy_lab")
    alerts: list[dict] = []

    def alert(level: str, message: str) -> None:
        alerts.append(dict(level=level, message=message))

    try:
        last = last_bar(prices_db) if Path(prices_db).exists() else None
        if job.registered(ledger_path) and (last is None or last < asof):
            out = dict(status="skipped", sessions=[], books={}, errors=[],
                       reason=f"the price store's last bar is {last}, asof is {asof}")
        else:
            out = job.step(asof, prices_db, ledger_path, alert)
    except Exception as e:  # noqa: BLE001 - reported as one JSON line and exit 1
        out = dict(status="error", sessions=[], books={}, errors=[f"{type(e).__name__}: {e}"[:500]])
    print(json.dumps(dict(at_utc=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), asof=asof,
                          ledger=str(ledger_path), **out, alerts=alerts), default=str), flush=True)
    return 1 if out["status"] == "error" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
