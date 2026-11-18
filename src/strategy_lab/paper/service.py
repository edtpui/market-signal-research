"""Forward paper testing: deployments, the paper host, synchronisation and reconciliation.

The paper host is the execution authority. Strategy Lab exports a deployment (paper/export.py), the host registers
and runs it, and Strategy Lab only *reads* the results back through the API v1 client into its own append-only
tables, tagged with the deployment (and so with the originating strategy, version and params).

Hosts:
  mock    MockPtradeHost in var/forward (SIMULATED, <= 2025 data, driven by advance()).
  ptrade  the operational paper_trading: export only (a human applies the bundle) plus API v1 reads. Disabled in
          Lab: [forward] ptrade_api_url must stay empty here, because API v1 serves HOLDOUT/FWD evidence.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from pathlib import Path

import pandas as pd

from strategy_lab.analytics import metrics as mt
from strategy_lab.core.types import OrderStatus
from strategy_lab.experiments.config import ExperimentConfig
from strategy_lab.paper import contract as C
from strategy_lab.paper import export as X
from strategy_lab.paper.client import HttpTransport, LocalTransport, PtradeApiClient, PtradeApiError
from strategy_lab.paper.mock_host import MockPtradeHost
from strategy_lab.paper.spec import DeploymentSpec

SYNCED = ("forward_days", "forward_trades", "forward_holdings", "forward_picks", "sync_log")
DDL = """
CREATE TABLE IF NOT EXISTS deployments (
  deployment_id TEXT PRIMARY KEY, deployment_key TEXT NOT NULL UNIQUE, name TEXT, target TEXT NOT NULL,
  book_id TEXT NOT NULL, model_id TEXT, strategy TEXT NOT NULL, strategy_version TEXT NOT NULL, spec TEXT NOT NULL,
  requested_start TEXT NOT NULL, start_date TEXT, status TEXT NOT NULL, bundle_dir TEXT, created_at_utc TEXT NOT NULL,
  last_sync_utc TEXT, last_asof TEXT, error TEXT, source_run_id TEXT);
CREATE TABLE IF NOT EXISTS forward_days (deployment_id TEXT NOT NULL, asof TEXT NOT NULL, equity_index REAL NOT NULL,
  ret REAL, gross REAL, n INTEGER, synced_at_utc TEXT NOT NULL, PRIMARY KEY (deployment_id, asof));
CREATE TABLE IF NOT EXISTS forward_trades (deployment_id TEXT NOT NULL, trade_key TEXT NOT NULL, date TEXT NOT NULL,
  ticker TEXT NOT NULL, kind TEXT NOT NULL, shares REAL NOT NULL, price REAL, value REAL, cost REAL,
  synced_at_utc TEXT NOT NULL, PRIMARY KEY (deployment_id, trade_key));
CREATE TABLE IF NOT EXISTS forward_holdings (deployment_id TEXT NOT NULL, asof TEXT NOT NULL, ticker TEXT NOT NULL,
  shares REAL NOT NULL, close REAL, value REAL, weight REAL, synced_at_utc TEXT NOT NULL,
  PRIMARY KEY (deployment_id, asof, ticker));
CREATE TABLE IF NOT EXISTS forward_picks (deployment_id TEXT NOT NULL, asof TEXT NOT NULL, entry_date TEXT,
  rank INTEGER, ticker TEXT NOT NULL, pred REAL, renew INTEGER, synced_at_utc TEXT NOT NULL,
  PRIMARY KEY (deployment_id, asof, ticker));
CREATE TABLE IF NOT EXISTS sync_log (id INTEGER PRIMARY KEY AUTOINCREMENT, deployment_id TEXT, at_utc TEXT NOT NULL,
  level TEXT NOT NULL, message TEXT NOT NULL, details TEXT);
""" + "".join(
    f"CREATE TRIGGER IF NOT EXISTS no_update_{t} BEFORE UPDATE ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'forward store: {t} is append-only'); END;\n"
    f"CREATE TRIGGER IF NOT EXISTS no_delete_{t} BEFORE DELETE ON {t} "
    f"BEGIN SELECT RAISE(ABORT, 'forward store: {t} is append-only'); END;\n" for t in SYNCED)


class DeployError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ForwardService:
    def __init__(self, lab):
        self.lab = lab
        s = lab.settings
        f = s.section("forward")
        self.dir = s.forward_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "forward.db"
        self.mock_dataset = f.get("mock_dataset", "us_core")
        self.ptrade_url = (f.get("ptrade_api_url") or "").strip()
        p = s.section("ptrade")
        self.ptrade_universe_file = s.resolve(p["universe_file"]) if p.get("universe_file") else None
        self._host: MockPtradeHost | None = None
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(DDL)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        return c

    @property
    def host(self) -> MockPtradeHost:
        if self._host is None:
            self._host = MockPtradeHost(self.dir / "mock_host", self.lab.store, self.mock_dataset)
        return self._host

    def _log(self, c, deployment_id: str | None, level: str, message: str, details=None) -> None:
        c.execute("INSERT INTO sync_log (deployment_id, at_utc, level, message, details) VALUES (?,?,?,?,?)",
                  (deployment_id, now_utc(), level, message[:2000],
                   json.dumps(details, default=str)[:20000] if details is not None else None))

    # -- validation ---------------------------------------------------------------------------------------------
    def validate(self, spec: DeploymentSpec) -> dict:
        problems, warnings = [], []
        try:
            cls = self.lab.registry.get(spec.strategy, spec.strategy_version)
            cls.resolve_params(spec.params)
        except Exception as e:  # noqa: BLE001
            return dict(ok=False, problems=[str(e)], warnings=[])
        if getattr(cls, "requires_historical_test", False):
            problems += self._historical_test_problems(cls, spec, warnings)
        need = set(cls.required_fields) | spec.execution.needs_fields()
        if not need <= C.HOST_FIELDS:
            problems.append(f"needs bar fields {sorted(need - C.HOST_FIELDS)}; the ptrade host supplies only "
                            f"{sorted(C.HOST_FIELDS)} (adjusted)")
        if not set(cls.order_types) <= C.HOST_ORDER_TYPES:
            problems.append(f"emits {sorted(set(cls.order_types) - C.HOST_ORDER_TYPES)} orders; only market orders "
                            "can be simulated on open/close bars")
        if not cls.long_only or spec.execution.allow_short:
            problems.append("short positions are not supported (ptrade basket trades are buy/trim/exit/hedge)")
        if spec.execution.price_mode != C.HOST_PRICE_MODE:
            problems.append("price_mode must be adjusted (ptrade Frames are adjusted prices)")
        if spec.execution.participation_cap is not None:
            problems.append("participation_cap needs volume, which ptrade Frames do not carry")
        if "SPY" in spec.universe:
            problems.append("SPY cannot be traded: ptrade keeps it out of the basket Frames (only as spy_close)")
        have = set(self.lab.store.symbols(self.mock_dataset))
        missing = sorted(set(spec.universe) - have)
        if missing:
            problems.append(f"no data in {self.mock_dataset} for {missing[:20]}")
        if spec.start_date > self.lab.policy.ceiling:
            problems.append(f"start {spec.start_date} is after the data ceiling {self.lab.policy.ceiling}")
        if self.ptrade_universe_file and self.ptrade_universe_file.exists():
            allowed = {x.strip() for x in self.ptrade_universe_file.read_text(encoding="utf-8").split() if x.strip()}
            outside = sorted(set(spec.universe) - allowed)
            if outside:
                problems.append(f"outside ptrade's universe.txt: {outside[:20]}")
        else:
            warnings.append("ptrade universe not checked here ([ptrade] universe_file not set): ptrade loads only "
                            "the tickers in its config/universe.txt")
        warnings.append("the strategy's universe is today's list (survivorship bias)")
        return dict(ok=not problems, problems=problems, warnings=warnings,
                    strategy=dict(id=cls.id, version=cls.version, source_sha256=cls.source_sha256()))

    def _historical_test_problems(self, cls, spec: DeploymentSpec, warnings: list[str]) -> list[str]:
        """Strategies built in the research labs (requires_historical_test) are eligible for forward paper testing
        only after a finished backtest of the same strategy version in the existing engine."""
        if cls.id == "entry_exit_system":
            return ["the generic entry_exit_system cannot be deployed: import the cell into the Strategy Library "
                    "(Entry-Exit Research), backtest the imported strategy, then deploy it"]
        runs = [r for r in self.lab.experiments.list(limit=5000)
                if r["strategy"] == cls.id and r["strategy_version"] == cls.version and r["status"] == "done"]
        if not runs:
            return [f"{cls.id} {cls.version} has no finished historical backtest: run it in Backtesting first "
                    "(every constructed strategy is tested in historical simulation before forward paper testing)"]
        same = []
        for r in runs:
            try:
                rc = self.lab.experiments.config(r["run_id"])
            except Exception:  # noqa: BLE001 - an unreadable run does not count
                continue
            if cls.resolve_params(rc.params) == cls.resolve_params(spec.params) and sorted(rc.universe or []) == \
                    sorted(spec.universe):
                same.append(r["run_id"])
        if not same:
            warnings.append(f"the backtests of {cls.id} used other parameters or another universe than this "
                            f"deployment ({len(runs)} run(s), e.g. {runs[0]['run_id']})")
        return []

    # -- deployments --------------------------------------------------------------------------------------------
    def deploy(self, spec: DeploymentSpec, target: str = "mock", source_run_id: str | None = None) -> dict:
        """Idempotent: the same spec (same key) returns the existing deployment with created=False."""
        if target not in ("mock", "ptrade"):
            raise DeployError([f"unknown target {target!r}"])
        key = spec.key()
        with self._lock:
            existing = self._by_key(key)
            if existing:
                existing["created"] = False
                return existing
            v = self.validate(spec)
            if not v["ok"]:
                raise DeployError(v["problems"])
            cls = self.lab.registry.get(spec.strategy, spec.strategy_version)
            dep_id = spec.book_id()
            bundle = self.dir / "exports" / dep_id
            manifest = X.build(spec, cls, bundle, v)
            status, start, error = "exported", None, None
            if target == "mock":
                reg = self.host.install(bundle, spec.start_date)
                status, start = "registered", reg["start_date"]
            with self._conn() as c:
                c.execute("INSERT INTO deployments (deployment_id, deployment_key, name, target, book_id, model_id, "
                          "strategy, strategy_version, spec, requested_start, start_date, status, bundle_dir, "
                          "created_at_utc, error, source_run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (dep_id, key, spec.name, target, manifest["book_id"], manifest["model_id"], spec.strategy,
                           spec.strategy_version, spec.model_dump_json(), spec.start_date.isoformat(), start, status,
                           str(bundle), now_utc(), error, source_run_id))
                self._log(c, dep_id, "info", f"deployed to {target} ({status})",
                          dict(model_id=manifest["model_id"], start=start, warnings=v["warnings"]))
            out = self.get(dep_id)
            out["created"] = True
            return out

    def reexport(self, deployment_id: str) -> dict:
        """Rebuilds an exported ptrade deployment's bundle with the current code: the same spec and book id, a new pin
        and model_id when the pinned code changed. Only for target ptrade with status exported, i.e. a bundle ptrade
        has not been told about by Strategy Lab; re-export before applying it, never after registration (OPERATOR.md
        says a changed engine is a new registration). A mock deployment's host already runs its bundle."""
        with self._lock:
            d = self.get(deployment_id)
            if d["target"] != "ptrade" or d["status"] != "exported":
                raise DeployError([f"{deployment_id} is {d['target']}/{d['status']}: only an exported ptrade "
                                   "deployment can be re-exported"])
            spec = DeploymentSpec.model_validate(d["spec"])
            v = self.validate(spec)
            if not v["ok"]:
                raise DeployError(v["problems"])
            cls = self.lab.registry.get(spec.strategy, spec.strategy_version)
            manifest = X.build(spec, cls, Path(d["bundle_dir"]), v)
            with self._conn() as c:
                c.execute("UPDATE deployments SET model_id=? WHERE deployment_id=?",
                          (manifest["model_id"], deployment_id))
                self._log(c, deployment_id, "info", "re-exported",
                          dict(old_model_id=d["model_id"], model_id=manifest["model_id"],
                               pin_sha256=manifest["pin_sha256"], warnings=v["warnings"]))
            return self.get(deployment_id)

    def spec_from_experiment(self, run_id: str, start_date, name: str = "") -> DeploymentSpec:
        cfg: ExperimentConfig = self.lab.experiments.config(run_id)
        return DeploymentSpec(name=name or cfg.name or f"{cfg.strategy} from {run_id}", strategy=cfg.strategy,
                              strategy_version=cfg.strategy_version, params=cfg.params, universe=cfg.universe,
                              execution=cfg.execution, initial_capital=cfg.initial_capital,
                              start_date=pd.Timestamp(start_date).date(), seed=cfg.seed)

    def _by_key(self, key: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT deployment_id FROM deployments WHERE deployment_key=?", (key,)).fetchone()
        return self.get(r[0]) if r else None

    def get(self, deployment_id: str) -> dict:
        with self._conn() as c:
            r = c.execute("SELECT * FROM deployments WHERE deployment_id=?", (deployment_id,)).fetchone()
        if r is None:
            raise KeyError(f"no deployment {deployment_id!r}")
        d = dict(r)
        d["spec"] = json.loads(d["spec"])
        if d["target"] == "mock":
            regs = {x["book_id"]: x for x in self.host.registrations()}
            reg = regs.get(d["book_id"])
            d["host_status"] = reg["status"] if reg else "unregistered"
        else:
            d["host_status"] = None
        return d

    def list(self) -> list[dict]:
        with self._conn() as c:
            ids = [r[0] for r in c.execute("SELECT deployment_id FROM deployments ORDER BY created_at_utc DESC")]
        return [self.get(i) for i in ids]

    def set_status(self, deployment_id: str, status: str, reason: str) -> dict:
        d = self.get(deployment_id)
        if d["target"] != "mock":
            raise DeployError(["pause/resume on ptrade is an operator action: python -m ptrade.registry --ledger "
                               f"{X.LEDGER_PATH} pause {d['book_id']} --reason ..."])
        self.host.set_status(d["book_id"], status, reason)
        with self._conn() as c:
            self._log(c, deployment_id, "info", f"host status -> {status}", dict(reason=reason))
        return self.get(deployment_id)

    # -- the mock clock -----------------------------------------------------------------------------------------
    def host_state(self) -> dict:
        h = self.host
        cal = h.sessions()
        return dict(target="mock", label="SIMULATED", dataset=self.mock_dataset, clock=h.clock(),
                    first_session=cal[0].date().isoformat(), last_session=cal[-1].date().isoformat(),
                    books=h.registrations(), run_log=h.run_log(30), ptrade_api=bool(self.ptrade_url))

    def advance(self, to=None, sessions: int | None = None) -> dict:
        """Runs the mock host to `to` (or `sessions` sessions past its clock), then syncs every mock deployment."""
        h = self.host
        if to is None:
            todo = h.sessions_to_run(h.sessions()[-1])
            if not todo:
                return dict(sessions=[], synced={})
            to = todo[min(len(todo), sessions or 1) - 1]
        with self._lock:
            ran = h.advance(to)
            synced = {}
            for d in self.list():
                if d["target"] == "mock":
                    try:
                        synced[d["deployment_id"]] = self.sync(d["deployment_id"])
                    except Exception as e:  # noqa: BLE001 - recorded, the others still sync
                        synced[d["deployment_id"]] = dict(error=f"{type(e).__name__}: {e}")
            return dict(sessions=ran, synced=synced, clock=h.clock())

    # -- synchronisation ----------------------------------------------------------------------------------------
    def client(self, d: dict) -> PtradeApiClient:
        if d["target"] == "mock":
            return PtradeApiClient(LocalTransport(self.host))
        if not self.ptrade_url:
            raise PtradeApiError("the ptrade API is disabled in this workspace ([forward] ptrade_api_url is empty): "
                                 "it serves HOLDOUT/FWD evidence")
        return PtradeApiClient(HttpTransport(self.ptrade_url))

    def sync(self, deployment_id: str) -> dict:
        """Pulls the book's days, trades, holdings and pending orders; idempotent (natural keys). A day the host
        restated is logged, never overwritten."""
        d = self.get(deployment_id)
        api = self.client(d)
        bid = d["book_id"]
        try:
            book = api.book(bid)
        except PtradeApiError as e:
            with self._conn() as c:
                self._log(c, deployment_id, "error", f"sync failed: {e}")
                c.execute("UPDATE deployments SET error=?, last_sync_utc=? WHERE deployment_id=?",
                          (str(e)[:500], now_utc(), deployment_id))
            raise
        if book.model_id != d["model_id"]:
            raise PtradeApiError(f"host book {bid} has model_id {book.model_id}, the deployment expects "
                                 f"{d['model_id']}: refusing to associate its results")
        counts = dict(days=0, trades=0, holdings=0, picks=0, restated=0)
        if book.last_asof:
            eq = api.equity(bid, book.start_date, book.last_asof)
            tr = api.trades(bid, book.start_date, book.last_asof)
            ho = api.holdings(bid, book.last_asof)
            pk = api.picks(bid, book.last_asof)
            now = now_utc()
            with self._lock, self._conn() as c:
                c.execute("BEGIN IMMEDIATE")
                stored = {r["asof"]: r["equity_index"] for r in c.execute(
                    "SELECT asof, equity_index FROM forward_days WHERE deployment_id=?", (deployment_id,))}
                for row in eq.days:
                    old = stored.get(row["asof"])
                    if old is None:
                        c.execute("INSERT INTO forward_days VALUES (?,?,?,?,?,?,?)",
                                  (deployment_id, row["asof"], row["equity"], row.get("ret"), row.get("gross"),
                                   row.get("n"), now))
                        counts["days"] += 1
                    elif abs(old - row["equity"]) > C.TOL:
                        counts["restated"] += 1
                        self._log(c, deployment_id, "warn", f"host restated {row['asof']}",
                                  dict(stored=old, host=row["equity"]))
                for r in tr.rows:
                    key = str(r.get("trade_id") or hashlib.sha256(json.dumps(r, sort_keys=True).encode())
                              .hexdigest()[:24])
                    n = c.execute("INSERT OR IGNORE INTO forward_trades VALUES (?,?,?,?,?,?,?,?,?,?)",
                                  (deployment_id, key, r["date"], r["ticker"], r["kind"], r["shares"], r.get("price"),
                                   r.get("value"), r.get("cost"), now)).rowcount
                    counts["trades"] += n
                for r in ho.rows:
                    counts["holdings"] += c.execute(
                        "INSERT OR IGNORE INTO forward_holdings VALUES (?,?,?,?,?,?,?,?)",
                        (deployment_id, ho.ledger_asof or ho.asof, r["ticker"], r["shares"], r.get("close"),
                         r.get("value"), r.get("weight"), now)).rowcount
                for r in pk.rows:
                    counts["picks"] += c.execute(
                        "INSERT OR IGNORE INTO forward_picks VALUES (?,?,?,?,?,?,?,?)",
                        (deployment_id, pk.ledger_asof or pk.asof, r.get("entry_date"), r.get("rank"), r["ticker"],
                         r.get("pred"), r.get("renew"), now)).rowcount
                c.execute("UPDATE deployments SET status=?, start_date=?, last_asof=?, last_sync_utc=?, error=NULL "
                          "WHERE deployment_id=?", ("running" if book.status == "active" else book.status,
                                                    book.start_date, book.last_asof, now, deployment_id))
                self._log(c, deployment_id, "info", f"synced through {book.last_asof}", counts)
                c.execute("COMMIT")
        else:
            with self._conn() as c:
                c.execute("UPDATE deployments SET status=?, start_date=?, last_sync_utc=? WHERE deployment_id=?",
                          ("waiting" if book.status in ("active", "waiting") else book.status, book.start_date,
                           now_utc(), deployment_id))
        return counts

    # -- views ----------------------------------------------------------------------------------------------------
    def _frame(self, sql: str, args) -> pd.DataFrame:
        with self._conn() as c:
            return pd.DataFrame([dict(r) for r in c.execute(sql, args)])

    def snapshot(self, deployment_id: str) -> dict:
        d = self.get(deployment_id)
        cap = float(d["spec"]["initial_capital"])
        days = self._frame("SELECT * FROM forward_days WHERE deployment_id=? ORDER BY asof", (deployment_id,))
        trades = self._frame("SELECT * FROM forward_trades WHERE deployment_id=? ORDER BY date, trade_key",
                             (deployment_id,))
        last = d["last_asof"]
        hold = self._frame("SELECT * FROM forward_holdings WHERE deployment_id=? AND asof=? ORDER BY ticker",
                           (deployment_id, last or ""))
        picks = self._frame("SELECT * FROM forward_picks WHERE deployment_id=? AND asof=? ORDER BY rank",
                            (deployment_id, last or ""))
        logs = self._frame("SELECT at_utc, level, message, details FROM sync_log WHERE deployment_id=? "
                           "ORDER BY id DESC LIMIT 200", (deployment_id,))
        out = dict(deployment=d, label="SIMULATED (mock host)" if d["target"] == "mock" else "ptrade",
                   capital=cap, equity=[], positions=[], orders=[], fills=[], metrics={}, logs=logs.to_dict("records"),
                   cash=None, equity_value=None, current_drawdown=None)
        if days.empty:
            return out
        eq = pd.Series(days["equity_index"].to_numpy() * cap, index=pd.DatetimeIndex(days["asof"]))
        curve = pd.concat([pd.Series([cap], index=[eq.index[0] - pd.Timedelta(days=1)]), eq])
        dd = mt.drawdown(curve).iloc[1:]
        out["equity"] = [dict(date=i.date().isoformat(), equity=float(v), drawdown=float(dd.loc[i]))
                         for i, v in eq.items()]
        if not hold.empty:
            # index units x capital: round away the float residue (28.000000001 shares)
            out["positions"] = [dict(symbol=r.ticker, quantity=round(r.shares * cap, 6), close=r.close, value=r.value * cap,
                                     weight=r.weight) for r in hold.itertuples()]
        out["equity_value"] = float(eq.iloc[-1])
        out["cash"] = float(eq.iloc[-1] - sum(p["value"] for p in out["positions"]))
        out["current_drawdown"] = float(dd.iloc[-1])
        if not picks.empty:
            out["orders"] = [dict(symbol=r.ticker, rank=r.rank, size=r.pred, eligible=r.entry_date, renew=bool(r.renew),
                                  status=OrderStatus.SUBMITTED.value) for r in picks.itertuples()]
        fills = pd.DataFrame()
        if not trades.empty:
            fills = pd.DataFrame(dict(symbol=trades["ticker"], quantity=(trades["shares"] * cap).round(6), price=trades["price"],
                                      commission=trades["cost"] * cap, timestamp=trades["date"], kind=trades["kind"]))
            out["fills"] = fills.to_dict("records")
        rt, _ = mt.round_trips(fills) if not fills.empty else (pd.DataFrame(), None)
        if rt is not None and not rt.empty:
            rt = mt.add_holding_periods(rt, pd.DatetimeIndex(days["asof"]))
        gross = pd.Series(days["gross"].to_numpy(), index=eq.index)
        notional = (fills.assign(n=fills["quantity"].abs() * fills["price"]).groupby("timestamp")["n"].sum()
                    if not fills.empty else pd.Series(dtype=float))
        notional.index = pd.DatetimeIndex(notional.index)
        out["metrics"] = mt.compute(mt.Inputs(equity=curve, trades=rt if rt is not None and len(rt) else None,
                                              exposure=gross, traded_notional=notional))
        out["monthly"] = mt.monthly_returns(curve).to_dict("records")
        return out

    def reconcile(self, deployment_id: str) -> dict:
        """Re-runs the deployment with the backtest engine over the host's window, on the same dataset restricted to
        the host's fields (open, close), and compares day by day. In the mock both read the same bars, so any
        difference is an engine/adapter defect; against ptrade, differences also come from its price store."""
        d = self.get(deployment_id)
        if not d["last_asof"]:
            return dict(deployment_id=deployment_id, compared_days=0, note="nothing synced yet")
        spec = DeploymentSpec.model_validate(d["spec"])
        cfg = ExperimentConfig(name=f"reconcile {deployment_id}", strategy=spec.strategy,
                               strategy_version=spec.strategy_version, params=spec.params,
                               dataset=self.mock_dataset, universe=spec.universe, start=d["start_date"],
                               end=d["last_asof"], initial_capital=spec.initial_capital, benchmark=None,
                               execution=spec.execution, seed=spec.seed)
        resolved, cls = self.lab.resolve(cfg)
        md, _ = self.lab.store.load(resolved.dataset, resolved.universe, resolved.start, resolved.end,
                                    version=resolved.dataset_version, warmup_bars=cls(spec.params).warmup())
        md = md.restrict(tuple(C.HOST_FIELDS))
        res = self.lab.backtest(resolved, data=md)
        bt = (res.equity["equity"] / spec.initial_capital).rename("backtest")
        fw = self._frame("SELECT asof, equity_index FROM forward_days WHERE deployment_id=? ORDER BY asof",
                         (deployment_id,))
        fw = pd.Series(fw["equity_index"].to_numpy(), index=pd.DatetimeIndex(fw["asof"]), name="forward")
        both = pd.concat([bt, fw], axis=1).dropna()
        diff = (both["backtest"] - both["forward"]).abs()
        tr = self._frame("SELECT date, ticker, shares, price FROM forward_trades WHERE deployment_id=? ORDER BY date, "
                         "ticker", (deployment_id,))
        bt_tr = res.fills.assign(date=res.fills["timestamp"], shares=res.fills["quantity"] / spec.initial_capital,
                                 ticker=res.fills["symbol"])[["date", "ticker", "shares", "price"]]
        key = lambda df: sorted((r.date, r.ticker, round(r.shares, 12), round(r.price, 8)) for r in df.itertuples())
        a, b = key(tr) if len(tr) else [], key(bt_tr) if len(bt_tr) else []
        out = dict(deployment_id=deployment_id, window=[d["start_date"], d["last_asof"]],
                   compared_days=int(len(both)), forward_days=int(len(fw)), backtest_days=int(len(bt)),
                   max_abs_equity_diff=float(diff.max()) if len(diff) else None,
                   mismatched_days=int((diff > C.TOL).sum()), forward_trades=len(a), backtest_trades=len(b),
                   trades_identical=a == b, dataset=f"{resolved.dataset}@{resolved.dataset_version}",
                   at_utc=now_utc())
        out["identical"] = bool(out["mismatched_days"] == 0 and out["trades_identical"]
                                and len(both) == len(fw) == len(bt))
        with self._conn() as c:
            self._log(c, deployment_id, "info" if out["identical"] else "warn", "reconciliation", out)
        return out


__all__ = ["ForwardService", "DeployError"]
