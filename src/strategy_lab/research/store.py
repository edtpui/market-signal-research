"""Signal research store: one write-once directory per experiment plus catalog tables in the shared catalog.db.

  <research_dir>/<experiment_id>/config.json            resolved SignalExperimentConfig (dataset version, universe,
                                                         pinned signal versions, explicit partitions, seed)
                                 study.json             data coverage, partitions, eligibility and matching report
                                 signals/<nnnn>_<id>/   summary.json, events.parquet, nulls.parquet (or error.txt),
                                                         written as each signal finishes (incremental persistence)
                                 tests.json             every (signal, partition, horizon) test with BH q-values
                                                         and verdicts, written once the experiment ends
                                 manifest.json          code and data provenance, per-signal content sha256

Opening an experiment reads these files; nothing is re-sampled. A finished directory is read-only. Reproduction is a
new experiment from the saved config, compared by content hash.

Research integrity ledgers (append-only, enforced by SQLite triggers):
  signal_test_ledger     every evaluation of a locked test partition; the first evaluation of a (signal,
                         methodology, data) key is stored in signal_locked_results and stays the authoritative one
  signal_data_usage      which dataset partitions every experiment used, and how many tests it ran on them
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import duckdb
import pandas as pd

from strategy_lab.catalog import (QueuedStore, git_state, make_readonly, now_utc, package_versions, sha256_file,
                                  source_sha256, write_owner)
from strategy_lab.core.strategy import canonical_hash
from strategy_lab.data.policy import period_labels
from strategy_lab.research.config import SignalExperimentConfig
from strategy_lab.research.engine import (Cancelled, ResearchError, expand_signals, finalize_tests, prepare,
                                          resolution_note, resolve_config, run_signal)

CODE_DIRS = ("signals", "research", "data")

DDL = """
CREATE TABLE IF NOT EXISTS signal_experiments (
  experiment_id TEXT PRIMARY KEY, name TEXT, kind TEXT NOT NULL, parent_id TEXT, dataset TEXT NOT NULL,
  dataset_version TEXT, universe_name TEXT, n_symbols INTEGER, start TEXT NOT NULL, end TEXT NOT NULL,
  analysis_end TEXT, frequency TEXT, config_hash TEXT NOT NULL, methodology_hash TEXT NOT NULL, status TEXT NOT NULL,
  error TEXT, created_at_utc TEXT NOT NULL, started_at_utc TEXT, finished_at_utc TEXT, n_signals INTEGER,
  progress_done INTEGER NOT NULL DEFAULT 0, progress_failed INTEGER NOT NULL DEFAULT 0, evaluate_test INTEGER,
  partitions TEXT, periods TEXT, summary TEXT, reproduction TEXT);
CREATE TABLE IF NOT EXISTS signal_runs (
  experiment_id TEXT NOT NULL, signal_key TEXT NOT NULL, dir TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
  n_events INTEGER, seconds REAL, content_sha256 TEXT, PRIMARY KEY (experiment_id, signal_key));
CREATE TABLE IF NOT EXISTS signal_results (
  experiment_id TEXT NOT NULL, signal_key TEXT NOT NULL, signal_id TEXT NOT NULL, version TEXT, family TEXT,
  name TEXT, direction TEXT, scope TEXT, kind TEXT, partition TEXT NOT NULL, role TEXT, horizon INTEGER NOT NULL,
  n INTEGER, n_dates INTEGER, mean REAL, excess REAL, excess_net REAL, ci_lo REAL, ci_hi REAL, p_matched REAL,
  q_matched REAL, excess_rd REAL, p_rd REAL, q_rd REAL, excess_bench REAL, win_rate REAL, ann_net_excess REAL,
  consistency_years REAL, verdict TEXT, significant INTEGER, economic INTEGER,
  PRIMARY KEY (experiment_id, signal_key, partition, horizon));
CREATE INDEX IF NOT EXISTS signal_results_signal ON signal_results (signal_id, partition, horizon);
CREATE TABLE IF NOT EXISTS signal_saved_configs (
  config_id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, created_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signal_test_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL, kind TEXT NOT NULL, signal_key TEXT NOT NULL,
  signal_id TEXT NOT NULL, family TEXT, lock_key TEXT NOT NULL, dataset TEXT, dataset_version TEXT, test_start TEXT,
  test_end TEXT, at_utc TEXT NOT NULL, first_use INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS signal_locked_results (
  lock_key TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, signal_key TEXT NOT NULL, result TEXT NOT NULL,
  at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signal_data_usage (
  experiment_id TEXT NOT NULL, kind TEXT NOT NULL, dataset TEXT NOT NULL, dataset_version TEXT, partition TEXT NOT NULL,
  role TEXT NOT NULL, start TEXT, end TEXT, n_tests INTEGER NOT NULL, at_utc TEXT NOT NULL,
  PRIMARY KEY (experiment_id, partition));
CREATE TABLE IF NOT EXISTS signal_imports (
  strategy_id TEXT PRIMARY KEY, version TEXT NOT NULL, signal_key TEXT NOT NULL, experiment_id TEXT NOT NULL,
  module_file TEXT NOT NULL, record TEXT NOT NULL, created_at_utc TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS signal_test_ledger_no_update BEFORE UPDATE ON signal_test_ledger
  BEGIN SELECT RAISE(ABORT, 'signal_test_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS signal_test_ledger_no_delete BEFORE DELETE ON signal_test_ledger
  BEGIN SELECT RAISE(ABORT, 'signal_test_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS signal_locked_no_update BEFORE UPDATE ON signal_locked_results
  BEGIN SELECT RAISE(ABORT, 'locked results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS signal_locked_no_delete BEFORE DELETE ON signal_locked_results
  BEGIN SELECT RAISE(ABORT, 'locked results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS signal_usage_no_update BEFORE UPDATE ON signal_data_usage
  BEGIN SELECT RAISE(ABORT, 'signal_data_usage is append-only'); END;
CREATE TRIGGER IF NOT EXISTS signal_usage_no_delete BEFORE DELETE ON signal_data_usage
  BEGIN SELECT RAISE(ABORT, 'signal_data_usage is append-only'); END;
"""


def code_sha256() -> str:
    return source_sha256(CODE_DIRS)


def _json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str, allow_nan=False) + "\n", encoding="utf-8")


def _clean(o):
    """NaN/inf -> None so JSON stays strict."""
    import math
    import numpy as np
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (float, np.floating)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _q(p: Path) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    con = duckdb.connect()
    try:
        con.register("t", df)
        con.execute(f"COPY (SELECT * FROM t) TO '{_q(path)}' (FORMAT parquet)")
        con.unregister("t")
    finally:
        con.close()


def read_parquet(path: Path, limit: int | None = None, where: str = "") -> pd.DataFrame:
    con = duckdb.connect()
    try:
        sql = f"SELECT * FROM read_parquet('{_q(path)}') {where}" + (f" LIMIT {int(limit)}" if limit else "")
        return con.execute(sql).df()
    finally:
        con.close()


def content_sha256(summary: dict, events: pd.DataFrame, nulls: pd.DataFrame) -> str:
    """Hash of the results themselves (timings excluded; floats at 12 significant digits), so a reproduction is
    compared on content, not on file bytes."""
    s = dict(summary)
    s.pop("timings", None)
    h = hashlib.sha256(json.dumps(_clean(s), sort_keys=True, separators=(",", ":")).encode())
    for df in (events, nulls):
        buf = io.StringIO()
        df.to_csv(buf, index=False, float_format="%.12g", lineterminator="\n")
        h.update(buf.getvalue().encode())
    return h.hexdigest()


class SignalResearchStore(QueuedStore):
    TABLE, KEY, CONFIGS_TABLE = "signal_experiments", "experiment_id", "signal_saved_configs"
    CONFIG_MODEL = SignalExperimentConfig
    LABEL = "signal experiment"
    JSON_COLS, BOOL_COLS = ("summary", "reproduction", "partitions"), ("evaluate_test",)

    def __init__(self, root: Path, catalog: Path, lab, workers: int = 1):
        super().__init__(root, catalog, lab, DDL, workers, "signal-research")

    # -- resolution and creation ------------------------------------------------------------------------------------
    def resolve(self, cfg: SignalExperimentConfig):
        lab = self.lab
        return resolve_config(cfg, lab.store, lab.signals, lab.universes, lab.policy, lab.reference)

    def create(self, cfg: SignalExperimentConfig, kind: str = "study", parent: str | None = None) -> str:
        resolved, signals = self.resolve(cfg)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        eid = f"sr-{stamp}-{resolved.config_hash()[:6]}-{uuid.uuid4().hex[:4]}"
        d = self.root / eid
        d.mkdir(parents=True)
        _json(_clean(resolved.model_dump(mode="json")), d / "config.json")
        write_owner(d)
        parts = [dict(name=p.name, role=p.role, start=p.start.isoformat(), end=p.end.isoformat(),
                      locked=p.role == "test" and not resolved.evaluate_test) for p in resolved.partitions]
        with self._db() as c:
            c.execute("INSERT INTO signal_experiments (experiment_id, name, kind, parent_id, dataset, dataset_version, "
                      "universe_name, n_symbols, start, end, analysis_end, frequency, config_hash, methodology_hash, "
                      "status, created_at_utc, n_signals, evaluate_test, partitions, periods) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, resolved.name, kind, parent, resolved.dataset, resolved.dataset_version,
                       cfg.universe_name or ("custom" if cfg.universe else None), len(resolved.universe),
                       resolved.start.isoformat(), resolved.end.isoformat(), resolved.analysis_end.isoformat(),
                       resolved.frequency, resolved.config_hash(), resolved.methodology_hash(), "queued", now_utc(),
                       len(signals), int(resolved.evaluate_test), json.dumps(parts),
                       ",".join(period_labels(resolved.start, resolved.analysis_end))))
        return eid

    # -- execution ----------------------------------------------------------------------------------------------------
    def execute(self, eid: str) -> dict:
        meta = self.get(eid)
        if meta is None:
            raise KeyError(f"no signal experiment {eid}")
        if meta["status"] != "queued":
            raise ResearchError(f"{eid} is {meta['status']}; an experiment is never re-executed (reproduce it)")
        d = self.root / eid
        cfg = self.config(eid)
        cancel = self._cancel.setdefault(eid, threading.Event())
        write_owner(d)
        self._set(eid, status="running", started_at_utc=now_utc())
        t0 = time.time()
        try:
            signals = expand_signals(cfg, self.lab.signals)
            keys = [s["key"] for s in cfg.resolved_signals or []]
            if keys and keys != [s.key for s in signals]:
                raise ResearchError("the catalog resolves this config to different signal keys than when it was "
                                    "saved (a signal's parameters or versions changed)")
            study = prepare(cfg, signals, self.lab.store, self.lab.reference, cancel)
            _json(_clean(dict(report=study.report, timings=study.timings, parts=study.parts,
                              reference=self.lab.reference.availability())), d / "study.json")
            self._record_usage(eid, meta["kind"], cfg, study, len(signals))
            (d / "signals").mkdir(exist_ok=True)
            tests, done, failed = [], 0, 0
            dirs = {s.key: f"{i:04d}_{s.id}" for i, s in enumerate(signals)}
            with ThreadPoolExecutor(max_workers=cfg.workers, thread_name_prefix=f"{eid[-4:]}-sig") as ex:
                futs = {ex.submit(self._one, eid, study, s, d / "signals" / dirs[s.key], cancel): s for s in signals}
                for f in as_completed(futs):
                    s = futs[f]
                    res = f.result()
                    if res["status"] == "done":
                        tests += res["tests"]
                        done += 1
                    elif res["status"] == "failed":
                        failed += 1
                    self._set(eid, progress_done=done, progress_failed=failed)
            status = "cancelled" if cancel.is_set() else "done"
            final = finalize_tests(cfg, tests, study.ppy)
            _json(_clean(dict(tests=final, partial=status != "done", resolution_note=resolution_note(
                cfg, max([t["m_tests"] for t in final], default=0)))), d / "tests.json")
            self._index_results(eid, cfg, signals, final, study)
            if cfg.evaluate_test and any(p["role"] == "test" for p in study.parts):
                self._record_test_use(eid, meta["kind"], cfg, signals, study)
            manifest = self._manifest(eid, cfg, meta, study)
            _json(_clean(manifest), d / "manifest.json")
            summary = dict(signals=len(signals), done=done, failed=failed, seconds=round(time.time() - t0, 1),
                           verdicts=_verdict_counts(final, cfg.primary_horizon))
            self._set(eid, status=status, finished_at_utc=now_utc(), summary=json.dumps(summary),
                      error="cancelled by the user" if status == "cancelled" else None)
            make_readonly(d)
            return dict(experiment_id=eid, status=status, summary=summary)
        except Exception as e:  # noqa: BLE001 - recorded on the experiment, then re-raised
            (d / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            status = "cancelled" if isinstance(e, Cancelled) else "failed"
            self._set(eid, status=status, finished_at_utc=now_utc(), error=f"{type(e).__name__}: {e}"[:2000])
            make_readonly(d)
            if status == "cancelled":
                return dict(experiment_id=eid, status=status)
            raise
        finally:
            self._cancel.pop(eid, None)

    def _one(self, eid: str, study, s, sdir: Path, cancel: threading.Event) -> dict:
        """One signal, isolated: a failure is recorded in its own directory and does not stop the others."""
        if cancel.is_set():
            return dict(status="skipped")
        t0 = time.time()
        sdir.mkdir(parents=True, exist_ok=True)
        try:
            out = run_signal(study, s, cancel)
            summary = _clean(out.summary)
            _json(summary, sdir / "summary.json")
            _write_parquet(out.events, sdir / "events.parquet")
            _write_parquet(out.nulls, sdir / "nulls.parquet")
            h = content_sha256(summary, out.events, out.nulls)
            self._run_row(eid, s.key, sdir.name, "done", None, int(out.summary["counts"]["events"]),
                          time.time() - t0, h)
            return dict(status="done", tests=out.tests)
        except Cancelled:
            self._run_row(eid, s.key, sdir.name, "cancelled", "cancelled", None, time.time() - t0, None)
            return dict(status="cancelled")
        except Exception as e:  # noqa: BLE001 - isolated per signal
            (sdir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._run_row(eid, s.key, sdir.name, "failed", f"{type(e).__name__}: {e}"[:2000], None, time.time() - t0,
                          None)
            return dict(status="failed")

    def _run_row(self, eid, key, dirname, status, error, n_events, seconds, sha):
        with self._db() as c:
            c.execute("INSERT OR REPLACE INTO signal_runs VALUES (?,?,?,?,?,?,?,?)",
                      (eid, key, dirname, status, error, n_events, round(seconds, 2), sha))

    def _index_results(self, eid: str, cfg: SignalExperimentConfig, signals, final: list[dict], study) -> None:
        by_key = {s.key: s for s in signals}
        roles = {p["name"]: p["role"] for p in study.parts}
        rows = []
        for t in final:
            s = by_key[t["key"]]
            summ = self.signal_summary(eid, t["key"])
            hz = summ["partitions"][t["partition"]]["horizons"][str(t["horizon"])]
            rd = hz.get("random_date") or {}
            bench = next(iter((hz.get("benchmarks") or {}).values()), {}) if hz.get("benchmarks") else {}
            bd = (summ["partitions"][t["partition"]].get("breakdowns") or {}).get(str(t["horizon"])) or {}
            cons = (bd.get("consistency_years") or {}).get("share_in_direction")
            v = t["verdict"]
            rows.append((eid, s.key, s.id, s.version, s.family, s.name, s.direction, s.scope, s.spec.kind,
                         t["partition"], roles.get(t["partition"], "combined"), t["horizon"], t["n"], t["n_dates"],
                         hz.get("mean"), t["excess"], t["excess_net"], t["ci"][0], t["ci"][1], t["p_matched"],
                         t["q_matched"], rd.get("excess"), t["p_random_date"], t["q_random_date"],
                         bench.get("excess"), hz.get("win_rate"), v["annualized_net_excess"], cons, v["category"],
                         int(v["significant"]), int(v["economic"])))
        with self._db() as c:
            c.executemany("INSERT OR REPLACE INTO signal_results VALUES (" + ",".join("?" * 31) + ")",
                          [tuple(_clean(list(r))) for r in rows])

    def _record_usage(self, eid, kind, cfg, study, n_signals) -> None:
        with self._db() as c:
            for p in study.parts:
                c.execute("INSERT OR IGNORE INTO signal_data_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (eid, kind, cfg.dataset, cfg.dataset_version, p["name"], p["role"], p["start"], p["end"],
                           n_signals * len(cfg.horizons), now_utc()))

    def lock_key(self, cfg: SignalExperimentConfig, signal_key: str, test: dict) -> str:
        return canonical_hash(dict(signal=signal_key, methodology=cfg.methodology_hash(), dataset=cfg.dataset,
                                   version=cfg.dataset_version, universe=canonical_hash(cfg.universe),
                                   test=[test["start"], test["end"]]))

    def _record_test_use(self, eid, kind, cfg, signals, study) -> None:
        test = next(p for p in study.parts if p["role"] == "test")
        with self._db() as c:
            for s in signals:
                run = c.execute("SELECT status FROM signal_runs WHERE experiment_id=? AND signal_key=?",
                                (eid, s.key)).fetchone()
                if not run or run["status"] != "done":
                    continue
                lk = self.lock_key(cfg, s.key, test)
                first = c.execute("SELECT 1 FROM signal_locked_results WHERE lock_key=?", (lk,)).fetchone() is None
                if first:
                    part = self.signal_summary(eid, s.key)["partitions"].get(test["name"])
                    c.execute("INSERT INTO signal_locked_results VALUES (?,?,?,?,?)",
                              (lk, eid, s.key, json.dumps(_clean(part)), now_utc()))
                c.execute("INSERT INTO signal_test_ledger (experiment_id, kind, signal_key, signal_id, family, "
                          "lock_key, dataset, dataset_version, test_start, test_end, at_utc, first_use) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (eid, kind, s.key, s.id, s.family, lk, cfg.dataset, cfg.dataset_version, test["start"],
                           test["end"], now_utc(), int(first)))

    def _manifest(self, eid, cfg, meta, study) -> dict:
        d = self.root / eid
        ds = self.lab.store.version(cfg.dataset, cfg.dataset_version)
        with self._db() as c:
            runs = [dict(r) for r in c.execute("SELECT * FROM signal_runs WHERE experiment_id=? ORDER BY dir",
                                               (eid,))]
        files = {p.relative_to(d).as_posix(): sha256_file(p) for p in sorted(d.rglob("*"))
                 if p.is_file() and p.name not in ("manifest.json",)}
        return dict(experiment_id=eid, kind=meta["kind"], parent_id=meta["parent_id"],
                    created_at_utc=meta["created_at_utc"], finished_at_utc=now_utc(), config_hash=cfg.config_hash(),
                    methodology_hash=cfg.methodology_hash(),
                    label="EXPLORATORY (dev workspace; data <= 2025-12-31)",
                    periods=period_labels(cfg.start, cfg.analysis_end),
                    code=dict(signals_research_data_sha256=code_sha256(), **git_state()), versions=package_versions(),
                    dataset=dict(id=ds.dataset_id, version=ds.version, provider=ds.meta["provider"],
                                 sources=ds.meta["sources"], ingested_at_utc=ds.meta["ingested_at_utc"],
                                 policy=ds.meta["policy"]),
                    seed=cfg.seed, permutations=cfg.permutations,
                    signals=[dict(key=r["signal_key"], dir=r["dir"], status=r["status"], error=r["error"],
                                  n_events=r["n_events"], content_sha256=r["content_sha256"]) for r in runs],
                    files=files)

    # -- reading ---------------------------------------------------------------------------------------------------
    def runs(self, eid: str) -> list[dict]:
        with self._db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM signal_runs WHERE experiment_id=? ORDER BY dir", (eid,))]

    def _signal_dir(self, eid: str, key: str) -> Path:
        with self._db() as c:
            r = c.execute("SELECT dir, status, error FROM signal_runs WHERE experiment_id=? AND signal_key=?",
                          (eid, key)).fetchone()
        if r is None:
            raise KeyError(f"no signal {key} in {eid}")
        return self.path(eid) / "signals" / r["dir"]

    def signal_summary(self, eid: str, key: str) -> dict:
        p = self._signal_dir(eid, key) / "summary.json"
        if not p.exists():
            raise FileNotFoundError(f"signal {key} of {eid} has no results (it failed or was cancelled)")
        return json.loads(p.read_text(encoding="utf-8"))

    def signal_events(self, eid: str, key: str, limit: int = 5000, symbol: str | None = None) -> pd.DataFrame:
        p = self._signal_dir(eid, key) / "events.parquet"
        if not p.exists():
            raise FileNotFoundError(f"signal {key} of {eid} has no events file")
        where = ""
        if symbol:
            where = "WHERE symbol = '" + symbol.replace("'", "''") + "'"
        return read_parquet(p, limit=limit, where=where)

    def results(self, eid: str | None = None, **filters) -> list[dict]:
        sql = ("SELECT r.*, e.dataset, e.dataset_version, e.name AS experiment_name, e.created_at_utc, e.status AS "
               "experiment_status, e.methodology_hash, e.n_symbols, e.start, e.analysis_end FROM signal_results r "
               "JOIN signal_experiments e USING (experiment_id) WHERE 1=1")
        args: list = []
        if eid:
            sql += " AND r.experiment_id=?"
            args.append(eid)
        for k in ("partition", "horizon", "family", "signal_id", "direction", "verdict"):
            if filters.get(k) not in (None, ""):
                sql += f" AND r.{k}=?"
                args.append(filters[k])
        sql += " ORDER BY r.q_matched IS NULL, r.q_matched, r.excess DESC LIMIT ?"
        args.append(int(filters.get("limit") or 5000))
        with self._db() as c:
            return [dict(r) for r in c.execute(sql, args)]

    # -- ledgers -----------------------------------------------------------------------------------------------------
    def usage(self) -> dict:
        with self._db() as c:
            parts = [dict(r) for r in c.execute(
                "SELECT dataset, partition, role, start, end, COUNT(DISTINCT experiment_id) AS experiments, "
                "SUM(n_tests) AS tests FROM signal_data_usage GROUP BY dataset, partition, role, start, end "
                "ORDER BY dataset, start")]
            ledger = [dict(r) for r in c.execute(
                "SELECT signal_id, family, dataset, test_start, test_end, COUNT(*) AS evaluations, "
                "SUM(first_use) AS locked, MIN(at_utc) AS first_at, MAX(at_utc) AS last_at FROM signal_test_ledger "
                "GROUP BY signal_id, family, dataset, test_start, test_end ORDER BY evaluations DESC")]
            fams = [dict(r) for r in c.execute(
                "SELECT family, COUNT(*) AS evaluations, COUNT(DISTINCT signal_key) AS configurations "
                "FROM signal_test_ledger GROUP BY family ORDER BY evaluations DESC")]
        return dict(partitions=parts, test_ledger=ledger, test_by_family=fams)

    def prior_usage(self, eid: str) -> list[dict]:
        """For each partition of an experiment: how many experiments and tests used the same dataset partition
        before it (researcher degrees of freedom)."""
        meta = self.get(eid)
        with self._db() as c:
            return [dict(r) for r in c.execute(
                "SELECT u.partition, u.role, u.start, u.end, "
                "(SELECT COUNT(DISTINCT x.experiment_id) FROM signal_data_usage x JOIN signal_experiments e2 "
                " ON e2.experiment_id = x.experiment_id WHERE x.dataset = u.dataset AND x.start = u.start AND "
                " x.end = u.end AND e2.created_at_utc <= ?) AS experiments_so_far, "
                "(SELECT COALESCE(SUM(x.n_tests), 0) FROM signal_data_usage x JOIN signal_experiments e2 "
                " ON e2.experiment_id = x.experiment_id WHERE x.dataset = u.dataset AND x.start = u.start AND "
                " x.end = u.end AND e2.created_at_utc <= ?) AS tests_so_far "
                "FROM signal_data_usage u WHERE u.experiment_id = ?",
                (meta["created_at_utc"], meta["created_at_utc"], eid))]

    def locked(self, eid: str) -> list[dict]:
        """Test-partition evaluations of this experiment's signals and whether each is the locked (first) one."""
        with self._db() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT l.signal_key, l.lock_key, l.first_use, l.at_utc, k.experiment_id AS locked_experiment, "
                "(SELECT COUNT(*) FROM signal_test_ledger z WHERE z.lock_key = l.lock_key) AS evaluations "
                "FROM signal_test_ledger l JOIN signal_locked_results k USING (lock_key) WHERE l.experiment_id=?",
                (eid,))]
        return rows

    def locked_result(self, lock_key: str) -> dict | None:
        with self._db() as c:
            r = c.execute("SELECT * FROM signal_locked_results WHERE lock_key=?", (lock_key,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["result"] = json.loads(d["result"])
        return d

    # -- reproduction --------------------------------------------------------------------------------------------
    def reproduce(self, eid: str) -> dict:
        orig = self.get(eid)
        if orig is None or orig["status"] != "done":
            raise ResearchError(f"{eid} is not a finished experiment")
        cfg = self.config(eid)
        try:
            self.lab.store.version(cfg.dataset, cfg.dataset_version)
        except Exception as e:  # noqa: BLE001
            return dict(experiment_id=eid, reproducible=False, reason=f"dataset version unavailable: {e}")
        new = self.create(cfg, kind="reproduction", parent=eid)
        self.execute(new)
        a = {r["signal_key"]: r["content_sha256"] for r in self.runs(eid)}
        b = {r["signal_key"]: r["content_sha256"] for r in self.runs(new)}
        diffs = sorted(k for k in a if a[k] != b.get(k))
        ma, mb = self.read_json(eid, "manifest.json"), self.read_json(new, "manifest.json")
        out = dict(experiment_id=eid, reproduction_id=new, identical=not diffs, differing_signals=diffs,
                   compared=len(a), code_changed=ma["code"]["signals_research_data_sha256"] !=
                   mb["code"]["signals_research_data_sha256"], at_utc=now_utc())
        self._set(new, reproduction=json.dumps(out))
        return out

    # -- imports into the Strategy Library ------------------------------------------------------------------------
    def imports(self) -> list[dict]:
        with self._db() as c:
            rows = c.execute("SELECT * FROM signal_imports ORDER BY created_at_utc DESC").fetchall()
        return [dict(r, record=json.loads(r["record"])) for r in rows]

    def record_import(self, record: dict, module_file: Path) -> None:
        with self._db() as c:
            c.execute("INSERT OR IGNORE INTO signal_imports VALUES (?,?,?,?,?,?,?)",
                      (record["strategy_id"], record["version"], record["signal"]["key"],
                       record["research"]["experiment_id"], str(module_file), json.dumps(record), now_utc()))

    def shutdown(self):
        for ev in self._cancel.values():
            ev.set()
        self.pool.shutdown(wait=True, cancel_futures=True)


def _verdict_counts(final: list[dict], ph: int) -> dict:
    out: dict[str, dict[str, int]] = {}
    for t in final:
        if t["horizon"] != ph:
            continue
        out.setdefault(t["partition"], {})
        c = t["verdict"]["category"]
        out[t["partition"]][c] = out[t["partition"]].get(c, 0) + 1
    return out
