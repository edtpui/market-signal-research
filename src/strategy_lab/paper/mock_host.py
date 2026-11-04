"""MockPtradeHost: a SIMULATED stand-in for ptrade's basket host, for developing and testing the forward workflow in
ClaudeWorkspace on <= 2025 data. It is a test double of the contract, not a second engine: trades are decided by
the exported plug-in's rule (Strategy Lab's engine) exactly as in ptrade. What it mirrors from paper_trading 792758b:

  * plug-in loading: the bundle's baskets/plugins/<module>.py is executed from its file and its RULE / PRODUCER
    get source_sha256 (baskets/plugins/__init__.py); model_id = '<id>:1:<config hash>' (baskets/books.py);
  * one session = one transaction (baskets.run.run_session): producer scores -> stored candidates -> every active
    book with start <= D re-runs its rule from its start over Frames built for the stored candidates' tickers,
    clipped at D; stored days must come out the same within TOL (else a restatement row, never an overwrite);
    D's day, trades, holdings and the next session's pending picks are appended; a failing book is rolled back to a
    savepoint and reported, the others still run;
  * Frames (baskets.frames): closes forward-filled, opens not, SPY only as spy_close, calendar AHEAD sessions past D
    (future sessions are business days: no future data is read);
  * append-only tables (triggers), registration start = the first session after the host's clock;
  * read API in the API v1 shapes the client parses (books, book detail, equity, holdings, trades, picks).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import math
import sqlite3
import sys
import threading
from pathlib import Path

import pandas as pd

from strategy_lab.core.strategy import canonical_hash
from strategy_lab.data.store import DataStore
from strategy_lab.paper import contract as C

TABLES = ("registrations", "candidates", "days", "holdings", "trades", "picks", "restatements", "model_events",
          "run_log")
DDL = """
CREATE TABLE IF NOT EXISTS models (model_id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL,
  created_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS model_events (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
  event TEXT NOT NULL, detail TEXT, at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS registrations (model_id TEXT PRIMARY KEY, book_id TEXT NOT NULL UNIQUE,
  start_date TEXT NOT NULL, protocol_sha256 TEXT NOT NULL, bundle_dir TEXT NOT NULL, registered_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (asof TEXT NOT NULL, ticker TEXT NOT NULL, direction TEXT NOT NULL, bucket TEXT,
  pred REAL NOT NULL, vol20 REAL, entry_date TEXT NOT NULL, exit_date TEXT NOT NULL, features_sha256 TEXT NOT NULL,
  created_at_utc TEXT NOT NULL, PRIMARY KEY (asof, ticker, direction));
CREATE TABLE IF NOT EXISTS days (model_id TEXT NOT NULL, asof TEXT NOT NULL, equity REAL NOT NULL, ret REAL NOT NULL,
  gross REAL, hedge REAL, n INTEGER, m REAL, created_at_utc TEXT NOT NULL, PRIMARY KEY (model_id, asof));
CREATE TABLE IF NOT EXISTS holdings (model_id TEXT NOT NULL, asof TEXT NOT NULL, ticker TEXT NOT NULL,
  shares REAL NOT NULL, close REAL, value REAL, weight REAL, exit_date TEXT, PRIMARY KEY (model_id, asof, ticker));
CREATE TABLE IF NOT EXISTS trades (trade_id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
  date TEXT NOT NULL, ticker TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('buy','trim','exit','hedge')),
  shares REAL NOT NULL, price REAL, value REAL, cost REAL);
CREATE TABLE IF NOT EXISTS picks (model_id TEXT NOT NULL, asof TEXT NOT NULL, entry_date TEXT NOT NULL,
  rank INTEGER NOT NULL, ticker TEXT NOT NULL, pred REAL, renew INTEGER NOT NULL, PRIMARY KEY (model_id, asof, ticker));
CREATE TABLE IF NOT EXISTS restatements (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,
  asof TEXT NOT NULL, field TEXT NOT NULL, ledger_value REAL, rerun_value REAL, found_on TEXT NOT NULL,
  created_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run_log (id INTEGER PRIMARY KEY AUTOINCREMENT, asof TEXT NOT NULL, status TEXT NOT NULL,
  details TEXT, started_at_utc TEXT NOT NULL, finished_at_utc TEXT NOT NULL);
""" + "".join(
    f"CREATE TRIGGER IF NOT EXISTS no_update_{t} BEFORE UPDATE ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'mock ledger: {t} is append-only'); END;\n"
    f"CREATE TRIGGER IF NOT EXISTS no_delete_{t} BEFORE DELETE ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'mock ledger: {t} is append-only'); END;\n" for t in TABLES)

SPY = "SPY"


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _d(x) -> str:
    return pd.Timestamp(x).date().isoformat()


class HostError(RuntimeError):
    pass


class Data:
    """baskets.frames.Data: ticker -> (adjusted OHLCV with a 'Date' index, raw or None); SPY kept apart."""

    def __init__(self, asof, bars, spy):
        self.asof, self.bars, self.spy = pd.Timestamp(asof), bars, spy


class Context:
    def __init__(self, data, buckets, cal):
        self.data, self.buckets, self.cal = data, buckets, cal


def load_plugin(path: Path):
    """As baskets/plugins/__init__.py: exec the module from its file, read RULE / PRODUCER, stamp source_sha256."""
    path = Path(path)
    name = f"mock_baskets_plugins.{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    for attr in ("RULE", "PRODUCER"):
        obj = getattr(mod, attr, None)
        if obj is None:
            raise HostError(f"plug-in {path.name} defines no {attr}")
        obj.source_sha256 = sha
    return mod.RULE, mod.PRODUCER, sha


class MockPtradeHost:
    def __init__(self, root: Path, store: DataStore, dataset: str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "mock_ptrade.db"
        self.store = store
        self.dataset = dataset
        self._lock = threading.RLock()
        self._plugins: dict[str, tuple] = {}
        self._bars_cache: dict[tuple, dict] = {}
        with self._conn() as c:
            c.executescript(DDL)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    # -- calendar and clock -------------------------------------------------------------------------------------
    def sessions(self) -> pd.DatetimeIndex:
        return self.store.calendar(self.dataset)

    def clock(self) -> str | None:
        with self._conn() as c:
            return c.execute("SELECT MAX(asof) FROM run_log WHERE status IN ('ok','warn')").fetchone()[0]

    def _calendar(self, asof: pd.Timestamp) -> pd.DatetimeIndex:
        past = self.sessions()
        past = past[(past >= pd.Timestamp(C.FIRST_BAR)) & (past <= asof)]
        future = pd.bdate_range(asof + pd.Timedelta(days=1), periods=C.AHEAD)
        return past.append(future)

    # -- books --------------------------------------------------------------------------------------------------
    def _plugin(self, bundle_dir: str) -> tuple:
        if bundle_dir not in self._plugins:
            mods = sorted((Path(bundle_dir) / "baskets" / "plugins").glob("*.py"))
            if len(mods) != 1:
                raise HostError(f"bundle {bundle_dir} must hold exactly one plug-in module")
            self._plugins[bundle_dir] = load_plugin(mods[0])
        return self._plugins[bundle_dir]

    def install(self, bundle_dir: Path, start_date) -> dict:
        """Loads the bundle's plug-in, computes its model_id the way ptrade does and registers it (active).
        Idempotent per model_id. The book starts at the first session after the host's clock (if the clock is
        already past start_date) or at the first session on or after start_date."""
        bundle_dir = str(Path(bundle_dir).resolve())
        manifest = json.loads((Path(bundle_dir) / "MANIFEST.json").read_text(encoding="utf-8"))
        rule, producer, sha = self._plugin(bundle_dir)
        cfg = dict(book=rule.name, unit="index", rule=rule.name, params=manifest["params"], scores=producer.config(),
                   rule_source_sha256=sha, scores_source_sha256=sha)
        model_id = f"{rule.name}:1:{canonical_hash(cfg)}"
        if model_id != manifest["model_id"]:
            raise HostError(f"model_id mismatch: host computes {model_id}, bundle says {manifest['model_id']}")
        proto = Path(bundle_dir) / "docs" / "protocols" / f"{rule.name}.md"
        with self._lock, self._conn() as c:
            row = c.execute("SELECT * FROM registrations WHERE model_id=?", (model_id,)).fetchone()
            if row:
                return dict(row)
            cal = self.sessions()
            clock = self.clock()
            lo = pd.Timestamp(start_date)
            if clock and pd.Timestamp(clock) >= lo:
                lo = pd.Timestamp(clock) + pd.Timedelta(days=1)
            later = cal[cal >= lo]
            if len(later) == 0:
                raise HostError(f"no session on or after {lo.date()} in {self.dataset} (data ends "
                                f"{cal[-1].date()})")
            start = _d(later[0])
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO models VALUES (?,?,?,?)", (model_id, rule.name, "active", now_utc()))
            c.execute("INSERT INTO model_events (model_id, event, detail, at_utc) VALUES (?,?,?,?)",
                      (model_id, "registered", f"start={start}", now_utc()))
            c.execute("INSERT INTO registrations VALUES (?,?,?,?,?,?)",
                      (model_id, rule.name, start, hashlib.sha256(proto.read_bytes()).hexdigest(), bundle_dir,
                       now_utc()))
            c.execute("COMMIT")
            return dict(c.execute("SELECT * FROM registrations WHERE model_id=?", (model_id,)).fetchone())

    def set_status(self, book_id: str, status: str, reason: str) -> None:
        if status not in ("active", "paused", "retired"):
            raise HostError(f"bad status {status!r}")
        if not reason.strip():
            raise HostError("a reason is required")
        with self._lock, self._conn() as c:
            r = c.execute("SELECT model_id FROM registrations WHERE book_id=?", (book_id,)).fetchone()
            if r is None:
                raise HostError(f"no book {book_id}")
            c.execute("UPDATE models SET status=? WHERE model_id=?", (status, r["model_id"]))
            c.execute("INSERT INTO model_events (model_id, event, detail, at_utc) VALUES (?,?,?,?)",
                      (r["model_id"], f"status:{status}", reason, now_utc()))

    def registrations(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT r.*, m.status FROM registrations r JOIN models m USING (model_id) ORDER BY r.registered_at_utc")]

    # -- the nightly run ----------------------------------------------------------------------------------------
    def _data(self, tickers: list[str], asof: pd.Timestamp) -> Data:
        key = tuple(sorted(set(tickers) | {SPY}))
        if key not in self._bars_cache:
            have = set(self.store.symbols(self.dataset))
            names = [t for t in key if t in have]
            cal = self.sessions()
            df = self.store.bars(self.dataset, names, "2018-01-01", cal[-1])
            df = df[df["valid"]]
            frames = {}
            for t, g in df.groupby("symbol", sort=False):
                f = g.set_index(pd.DatetimeIndex(g["date"], name="Date"))[["open", "high", "low", "close", "volume"]]
                f.columns = ["Open", "High", "Low", "Close", "Volume"]
                frames[t] = f
            self._bars_cache = {key: frames}
        full = self._bars_cache[key]
        clipped = {t: (f.loc[:asof], None) for t, f in full.items() if t != SPY}      # nothing after asof
        spy = full[SPY].loc[:asof] if SPY in full else pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return Data(asof, {t: v for t, v in clipped.items() if len(v[0])}, spy)

    @staticmethod
    def _frames(data: Data, tickers, cal) -> C.Frames:
        tickers = sorted(t for t in set(tickers) if t in data.bars)
        closes = pd.DataFrame({t: data.bars[t][0].Close for t in tickers}).reindex(cal).ffill()
        opens = pd.DataFrame({t: data.bars[t][0].Open for t in tickers}).reindex(cal)
        return C.Frames(closes=closes, opens=opens, spy_close=data.spy.Close if len(data.spy) else pd.Series(dtype=float),
                        cal=cal)

    def _active(self, c) -> list[dict]:
        return [dict(r) for r in c.execute("SELECT r.*, m.status FROM registrations r JOIN models m USING (model_id) "
                                           "WHERE m.status='active' ORDER BY r.book_id")]

    def sessions_to_run(self, to) -> list[str]:
        to = pd.Timestamp(to)
        cal = self.sessions()
        clock = self.clock()
        if clock:                          # as baskets.run: once the ledger has run, every session runs, books or not
            first = pd.Timestamp(clock) + pd.Timedelta(days=1)
        else:
            with self._conn() as c:
                books = self._active(c)
            if not books:
                return []
            first = cal[max(0, int(cal.searchsorted(pd.Timestamp(min(b["start_date"] for b in books)))) - 1)]
        return [_d(d) for d in cal[(cal >= first) & (cal <= to)]]

    def advance(self, to) -> list[dict]:
        """Runs every session after the clock through `to` (capped at the dataset's last session), in order."""
        cal = self.sessions()
        to = min(pd.Timestamp(to), cal[-1])
        with self._lock:
            return [self.run_session(d) for d in self.sessions_to_run(to)]

    def run_session(self, asof: str) -> dict:
        D = pd.Timestamp(asof)
        started = now_utc()
        with self._conn() as c:
            books = self._active(c)
        plugins = {b["book_id"]: self._plugin(b["bundle_dir"]) for b in books}
        universe = sorted({t for rule, prod, _ in plugins.values() for t in _universe(prod)})
        data = self._data(universe, D)
        cal = self._calendar(D)
        summary = dict(asof=asof, candidates=0, books={})
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            for bid in sorted(plugins):
                _, producer, _ = plugins[bid]
                scored = producer.scores(asof, Context(data=data, buckets={}, cal=cal))
                c.executemany("INSERT INTO candidates VALUES (?,?,?,?,?,?,?,?,?,?)",
                              [(asof, r.ticker, r.direction, r.bucket, float(r.pred),
                                None if pd.isna(r.vol20) else float(r.vol20), _d(r.entry_date), _d(r.exit_date),
                                r.features_sha256, now_utc()) for r in scored.itertuples()])
                summary["candidates"] += len(scored)
            cands = pd.DataFrame([dict(r) for r in c.execute(
                "SELECT * FROM candidates WHERE asof <= ? ORDER BY asof, ticker, direction", (asof,))])
            if len(cands):
                cands = cands.rename(columns={"asof": "date"})
                for col in ("date", "entry_date", "exit_date"):
                    cands[col] = pd.to_datetime(cands[col])
            for b in books:
                if b["start_date"] > asof:
                    continue
                rule = plugins[b["book_id"]][0]
                manifest = json.loads((Path(b["bundle_dir"]) / "MANIFEST.json").read_text(encoding="utf-8"))
                c.execute("SAVEPOINT book")
                try:
                    tickers = cands["ticker"].unique() if len(cands) else []
                    summary["books"][b["book_id"]] = self._run_book(c, rule, b, manifest["params"], cands,
                                                                     self._frames(data, tickers, cal), asof)
                    c.execute("RELEASE book")
                except Exception as e:  # noqa: BLE001 - one book's failure never stops the others
                    c.execute("ROLLBACK TO book")
                    c.execute("RELEASE book")
                    summary["books"][b["book_id"]] = dict(error=f"{type(e).__name__}: {e}"[:500])
            failed = [k for k, v in summary["books"].items() if "error" in v]
            c.execute("INSERT INTO run_log (asof, status, details, started_at_utc, finished_at_utc) VALUES (?,?,?,?,?)",
                      (asof, "warn" if failed else "ok", json.dumps(summary, default=str)[:4000], started, now_utc()))
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise
        finally:
            c.close()
        return summary

    def _run_book(self, c, rule, b: dict, params: dict, cands, fr: C.Frames, asof: str) -> dict:
        mid = b["model_id"]
        run = rule.simulate(cands, fr, b["start_date"], asof, params)
        stored = {r["asof"]: dict(r) for r in c.execute("SELECT * FROM days WHERE model_id=?", (mid,))}
        restated, trades, late, now = [], 0, [], now_utc()
        for d, row in run.daily.iterrows():
            ds = _d(d)
            old = stored.get(ds)
            if old is None:
                trades += self._append_day(c, mid, run, d, now)
                if ds != asof:
                    late.append(ds)
                continue
            for f in C.COMPARED:
                if _differs(old[f], row[f]):
                    restated.append((mid, ds, f, old[f], float(row[f]), asof, now))
        c.executemany("INSERT INTO restatements (model_id, asof, field, ledger_value, rerun_value, found_on, "
                      "created_at_utc) VALUES (?,?,?,?,?,?,?)", restated)
        day = run.daily.loc[pd.Timestamp(asof)]
        held = set(run.holdings.loc[run.holdings["asof"] == pd.Timestamp(asof), "ticker"])
        picks = rule.pending(None, held, params)
        nxt = _d(fr.cal[fr.cal > pd.Timestamp(asof)][0])
        c.executemany("INSERT INTO picks VALUES (?,?,?,?,?,?,?)",
                      [(mid, asof, nxt, p["rank"], p["ticker"], p["pred"], p["renew"]) for p in picks])
        return dict(equity=float(day["equity"]), ret=float(day["ret"]), n=int(day["n"]), trades=trades,
                    restatements=len(restated), late_days=late)

    @staticmethod
    def _append_day(c, mid, run, d, now) -> int:
        day, ds = run.daily.loc[d], _d(d)
        c.execute("INSERT INTO days VALUES (?,?,?,?,?,?,?,?,?)", (mid, ds, float(day["equity"]), float(day["ret"]),
                                                                 float(day["gross"]), float(day["hedge"]),
                                                                 int(day["n"]), float(day["m"]), now))
        t = run.trades[run.trades["date"] == d]
        c.executemany("INSERT INTO trades (model_id, date, ticker, kind, shares, price, value, cost) "
                      "VALUES (?,?,?,?,?,?,?,?)", [(mid, ds, r.ticker, r.kind, float(r.shares), float(r.price),
                                                    float(r.value), float(r.cost)) for r in t.itertuples()])
        h = run.holdings[run.holdings["asof"] == d]
        c.executemany("INSERT INTO holdings VALUES (?,?,?,?,?,?,?,?)",
                      [(mid, ds, r.ticker, float(r.shares), float(r.close), float(r.value), float(r.weight),
                        _d(r.exit_date) if pd.notna(r.exit_date) else None) for r in h.itertuples()])
        return len(t)

    # -- the read API (API v1 shapes) ---------------------------------------------------------------------------
    def _book_row(self, c, book_id: str) -> dict:
        r = c.execute("SELECT r.*, m.status FROM registrations r JOIN models m USING (model_id) WHERE r.book_id=?",
                      (book_id,)).fetchone()
        if r is None:
            raise KeyError(f"no book {book_id!r}")
        return dict(r)

    def api_books(self) -> list[dict]:
        with self._conn() as c:
            ids = [r["book_id"] for r in c.execute("SELECT book_id FROM registrations ORDER BY book_id")]
            return [self._summary(c, self._book_row(c, i)) for i in ids]

    def _summary(self, c, b: dict) -> dict:
        days = [dict(r) for r in c.execute("SELECT asof, equity, ret FROM days WHERE model_id=? ORDER BY asof",
                                           (b["model_id"],))]
        last = days[-1] if days else None
        n_pos = c.execute("SELECT COUNT(*) FROM holdings WHERE model_id=? AND asof=?",
                          (b["model_id"], last["asof"] if last else "")).fetchone()[0]
        clock = self.clock()
        manifest = json.loads((Path(b["bundle_dir"]) / "MANIFEST.json").read_text(encoding="utf-8"))
        status = b["status"] if b["start_date"] <= (clock or "") else ("waiting" if b["status"] == "active"
                                                                         else b["status"])
        return dict(id=b["book_id"], name=manifest["spec"].get("name") or b["book_id"], family="basket",
                    unit="index", model=b["book_id"], book=None, ledger="ledger_strategy_lab",
                    protocol=f"docs/protocols/{b['book_id']}.md", history=[], status=status,
                    model_id=b["model_id"], start_date=b["start_date"], last_asof=last["asof"] if last else None,
                    value=last["equity"] if last else None, since_start=last["equity"] - 1 if last else None,
                    day_change=last["ret"] if last else None, n_positions=int(n_pos),
                    sparkline=[[d["asof"], d["equity"]] for d in days[-60:]],
                    freshness=dict(last_asof=last["asof"] if last else None, expected_asof=clock or b["start_date"],
                                   behind=bool(clock and (not last or last["asof"] < clock))),
                    live=None, series=dict(id=b["book_id"], name=b["book_id"], unit="index", color="#22a6b3",
                                           default_color="#22a6b3", style="solid", width=2, visible=True,
                                           custom=False),
                    rule=b["book_id"], interim_months=None, decision_months=None, target_n=None)

    def api_book(self, book_id: str) -> dict:
        with self._conn() as c:
            b = self._book_row(c, book_id)
            out = self._summary(c, b)
            events = [dict(r) for r in c.execute("SELECT event, detail, at_utc FROM model_events WHERE model_id=? "
                                                 "ORDER BY id", (b["model_id"],))]
            rest = [dict(r) for r in c.execute("SELECT asof, field, ledger_value, rerun_value, found_on FROM "
                                               "restatements WHERE model_id=? ORDER BY id", (b["model_id"],))]
        start = dt.date.fromisoformat(b["start_date"])
        last = dt.date.fromisoformat(out["last_asof"]) if out["last_asof"] else None
        out["evidence"] = dict(protocol_path=out["protocol"], protocol_sha256=b["protocol_sha256"],
                               registered_sha256=b["protocol_sha256"], registered_at_utc=b["registered_at_utc"],
                               start_date=b["start_date"],
                               months_elapsed=round((last - start).days / 30.4375, 2) if last else None,
                               interim_date=None, decision_date=None, target_n=None, resolved_n=None,
                               rule=out["rule"], deviations=[], events=events, restatements=rest)
        out["runs"] = []
        return out

    def api_equity(self, book_id: str, start: str | None = None, end: str | None = None) -> dict:
        with self._conn() as c:
            b = self._book_row(c, book_id)
            rows = [dict(r) for r in c.execute("SELECT * FROM days WHERE model_id=? ORDER BY asof", (b["model_id"],))]
        lo, hi = start or b["start_date"], end or (rows[-1]["asof"] if rows else b["start_date"])
        lo = max(lo, b["start_date"])
        sel = [r for r in rows if lo <= r["asof"] <= hi]
        peak, dd = 1.0, []
        for r in rows:
            peak = max(peak, r["equity"])
            if lo <= r["asof"] <= hi:
                dd.append([r["asof"], r["equity"] / peak - 1])
        monthly, prev, cur, first, lastv = [], 1.0, None, None, None
        for r in rows:
            m = r["asof"][:7]
            if m != cur:
                if cur is not None:
                    monthly.append(dict(month=cur, ret=lastv / first - 1))
                cur, first = m, prev
            lastv = prev = r["equity"]
        if cur is not None:
            monthly.append(dict(month=cur, ret=lastv / first - 1))
        return dict(book_id=book_id, unit="index", start=lo, end=hi, points=[[r["asof"], r["equity"]] for r in sel],
                    pct=[[r["asof"], r["equity"] - 1] for r in sel], drawdown=dd, spy=[],
                    monthly=[m for m in monthly if m["month"] >= lo[:7]],
                    days=[{k: r[k] for k in ("asof", "equity", "ret", "gross", "hedge", "n", "m")} for r in sel])

    def api_holdings(self, book_id: str, asof: str | None = None) -> dict:
        with self._conn() as c:
            b = self._book_row(c, book_id)
            day = asof or self.clock() or b["start_date"]
            d = c.execute("SELECT MAX(asof) FROM days WHERE model_id=? AND asof <= ?", (b["model_id"], day)).fetchone()[0]
            rows = [dict(r) for r in c.execute("SELECT ticker, shares, close, value, weight, exit_date FROM holdings "
                                               "WHERE model_id=? AND asof=? ORDER BY ticker", (b["model_id"], d or ""))]
        for r in rows:
            r["mark"] = r["close"]
        return dict(asof=day, source="ledger", ledger_asof=d, rows=rows)

    def api_trades(self, book_id: str, start: str | None = None, end: str | None = None, limit: int = 2000) -> dict:
        with self._conn() as c:
            b = self._book_row(c, book_id)
            hi = end or self.clock() or b["start_date"]
            lo = start or hi
            rows = [dict(r) for r in c.execute(
                "SELECT trade_id, date, ticker, kind, shares, price, value, cost FROM trades WHERE model_id=? AND "
                "date BETWEEN ? AND ? ORDER BY date, trade_id LIMIT ?", (b["model_id"], lo, hi, int(limit)))]
        return dict(asof=hi, source="ledger", rows=rows)

    def api_picks(self, book_id: str, asof: str | None = None) -> dict:
        with self._conn() as c:
            b = self._book_row(c, book_id)
            day = asof or self.clock() or b["start_date"]
            d = c.execute("SELECT MAX(asof) FROM picks WHERE model_id=? AND asof <= ?", (b["model_id"], day)).fetchone()[0]
            rows = [dict(r) for r in c.execute("SELECT rank, ticker, pred, renew, entry_date FROM picks WHERE "
                                               "model_id=? AND asof=? ORDER BY rank", (b["model_id"], d or ""))]
        return dict(asof=day, source="ledger", ledger_asof=d, entry_date=rows[0]["entry_date"] if rows else None,
                    rows=rows)

    def run_log(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT ?", (limit,))]


def _universe(producer) -> list[str]:
    cfg = producer.config()
    spec = getattr(producer, "_impl", None)
    if spec is not None:
        return list(spec.spec.universe)
    # the plug-in's SPEC is embedded in its module; read it from there without importing the engine
    mod = sys.modules.get(type(producer).__module__)
    if mod is not None and hasattr(mod, "SPEC"):
        return list(mod.SPEC["universe"])
    raise HostError(f"cannot read the universe of {cfg.get('producer')}")


def _differs(a, b) -> bool:
    a, b = (float("nan") if v is None else float(v) for v in (a, b))
    return not (math.isnan(a) and math.isnan(b)) and not abs(a - b) <= C.TOL


__all__ = ["MockPtradeHost", "HostError", "load_plugin"]
