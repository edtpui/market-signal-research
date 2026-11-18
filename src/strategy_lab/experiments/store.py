"""Experiment store: one write-once directory per run plus a SQLite catalog.

  <experiments_dir>/<run_id>/config.json      the resolved ExperimentConfig (dataset version pinned)
                            manifest.json    provenance: code hashes, package versions, dataset meta, artifact sha256
                            equity.csv fills.csv orders.csv events.csv trades.csv open_lots.csv holdings.csv
                            monthly.csv metrics.json data_report.json logs.jsonl state.json

A finished run directory is made read-only and is never rewritten; a new run (also a reproduction) gets a new id.
The catalog only indexes runs (status, timings, a metric summary). It can be rebuilt from the directories. The
catalog bookkeeping shared with the research stores is strategy_lab.catalog.
"""
from __future__ import annotations

import datetime as dt
import json
import traceback
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from strategy_lab.catalog import (CatalogStore, git_state, make_readonly, now_utc, package_versions, sha256_file,
                                  source_sha256, write_owner)
from strategy_lab.experiments.backtest import BacktestResult
from strategy_lab.experiments.config import ExperimentConfig

ARTIFACTS = ("equity.csv", "fills.csv", "orders.csv", "events.csv", "trades.csv", "open_lots.csv", "holdings.csv",
             "monthly.csv", "metrics.json", "data_report.json", "logs.jsonl", "state.json")
COMPARED = ("equity.csv", "fills.csv", "orders.csv", "trades.csv", "holdings.csv", "metrics.json")
SUMMARY = ("total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "volatility", "win_rate",
           "profit_factor", "trade_count", "exposure", "turnover")
ENGINE_DIRS = ("core", "engine", "analytics", "data", "experiments")

DDL = """
CREATE TABLE IF NOT EXISTS experiments (
  run_id TEXT PRIMARY KEY, name TEXT, kind TEXT NOT NULL, parent_run_id TEXT, strategy TEXT NOT NULL,
  strategy_version TEXT, dataset TEXT NOT NULL, dataset_version TEXT, start TEXT NOT NULL, end TEXT NOT NULL,
  frequency TEXT NOT NULL, config_hash TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at_utc TEXT NOT NULL,
  started_at_utc TEXT, finished_at_utc TEXT, periods TEXT, summary TEXT, reproduction TEXT);
CREATE TABLE IF NOT EXISTS saved_configs (
  config_id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, created_at_utc TEXT NOT NULL);
"""


def engine_source_sha256() -> str:
    """sha256 over the engine's own source files (core, engine, analytics, data, experiments)."""
    return source_sha256(ENGINE_DIRS)


def _write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False, lineterminator="\n")


def _json(obj, path: Path) -> None:
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n", encoding="utf-8")


class RunExists(FileExistsError):
    pass


class ExperimentStore(CatalogStore):
    TABLE, KEY, CONFIGS_TABLE, CONFIG_MODEL = "experiments", "run_id", "saved_configs", ExperimentConfig
    INTERRUPTED = "interrupted: the process stopped before the run finished"

    def __init__(self, root: Path, catalog: Path, lab):
        super().__init__(root, catalog, lab, DDL)

    # -- catalog ------------------------------------------------------------------------------------------------
    def create(self, cfg: ExperimentConfig, kind: str = "backtest", parent: str | None = None) -> str:
        """Validates and resolves the config (fails fast), writes config.json and a 'queued' catalog row."""
        resolved, _ = self.lab.resolve(cfg)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        run_id = f"{stamp}-{resolved.config_hash()[:6]}-{uuid.uuid4().hex[:4]}"
        d = self.root / run_id
        if d.exists():
            raise RunExists(run_id)
        d.mkdir(parents=True)
        _json(resolved.model_dump(mode="json"), d / "config.json")
        write_owner(d)
        from strategy_lab.data.policy import period_labels
        with self._db() as c:
            c.execute("INSERT INTO experiments (run_id, name, kind, parent_run_id, strategy, strategy_version, dataset, "
                      "dataset_version, start, end, frequency, config_hash, status, created_at_utc, periods) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (run_id, resolved.name, kind, parent, resolved.strategy, resolved.strategy_version,
                       resolved.dataset, resolved.dataset_version, resolved.start.isoformat(),
                       resolved.end.isoformat(), resolved.frequency, resolved.config_hash(), "queued", now_utc(),
                       ",".join(period_labels(resolved.start, resolved.end))))
        return run_id

    # -- execution ----------------------------------------------------------------------------------------------
    def execute(self, run_id: str) -> dict:
        """Runs a queued experiment and writes its artifacts. Never rewrites a finished run."""
        meta = self.get(run_id)
        if meta is None:
            raise KeyError(f"no experiment {run_id}")
        if meta["status"] not in ("queued",):
            raise RunExists(f"{run_id} is {meta['status']}; a finished run is never re-executed (reproduce it)")
        d = self.root / run_id
        cfg = ExperimentConfig.model_validate_json((d / "config.json").read_text(encoding="utf-8"))
        write_owner(d)
        self._set(run_id, status="running", started_at_utc=now_utc())
        try:
            res = self.lab.backtest(cfg)
            manifest = self._write(d, res, meta)
            summary = {k: res.metrics.get(k, {}).get("value") for k in SUMMARY}
            self._set(run_id, status="done", finished_at_utc=now_utc(), summary=json.dumps(summary))
            make_readonly(d)
            return dict(run_id=run_id, status="done", manifest=manifest)
        except Exception as e:  # noqa: BLE001 - recorded on the run, then re-raised
            (d / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            self._set(run_id, status="failed", finished_at_utc=now_utc(), error=f"{type(e).__name__}: {e}"[:2000])
            make_readonly(d)
            raise

    def _write(self, d: Path, res: BacktestResult, meta: dict) -> dict:
        eq = res.equity.reset_index()
        eq["date"] = eq["date"].dt.date.astype(str)
        _write_csv(eq, d / "equity.csv")
        for name in ("fills", "orders", "events", "trades", "open_lots", "holdings"):
            _write_csv(getattr(res, name), d / f"{name}.csv")
        _write_csv(res.monthly, d / "monthly.csv")
        _json(res.metrics, d / "metrics.json")
        _json(res.data_report, d / "data_report.json")
        with open(d / "logs.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for row in res.logs:
                f.write(json.dumps(row) + "\n")
        _json(res.final_state, d / "state.json")
        ds = self.lab.store.version(res.config.dataset, res.config.dataset_version)
        manifest = dict(
            run_id=d.name, kind=meta["kind"], parent_run_id=meta["parent_run_id"], created_at_utc=meta["created_at_utc"],
            finished_at_utc=now_utc(), config_hash=res.config.config_hash(), periods=res.periods,
            label="EXPLORATORY (dev workspace; data <= 2025-12-31)",
            strategy=dict(id=res.strategy_info["id"], version=res.strategy_info["version"],
                          source_sha256=res.strategy_info["source_sha256"]),
            engine=dict(source_sha256=engine_source_sha256(), **git_state()),
            versions=package_versions(),
            dataset=dict(id=ds.dataset_id, version=ds.version, provider=ds.meta["provider"],
                         provider_version=ds.meta.get("provider_version"), sources=ds.meta["sources"],
                         ingested_at_utc=ds.meta["ingested_at_utc"], policy=ds.meta["policy"]),
            artifacts={a: sha256_file(d / a) for a in ARTIFACTS})
        _json(manifest, d / "manifest.json")
        return manifest

    # -- reading --------------------------------------------------------------------------------------------------
    def read_csv(self, run_id: str, name: str) -> pd.DataFrame:
        p = self.path(run_id) / name
        if not p.exists():
            raise FileNotFoundError(f"{run_id} has no {name} (status: {(self.get(run_id) or {}).get('status')})")
        return pd.read_csv(p)

    # -- reproduction -------------------------------------------------------------------------------------------
    def reproduce(self, run_id: str) -> dict:
        """Re-runs a finished experiment from its saved config into a new run and compares artifact hashes."""
        orig = self.get(run_id)
        if orig is None or orig["status"] != "done":
            raise ValueError(f"{run_id} is not a finished experiment")
        cfg = self.config(run_id)
        try:
            self.lab.store.version(cfg.dataset, cfg.dataset_version)
        except Exception as e:  # noqa: BLE001
            return dict(run_id=run_id, reproducible=False, reason=f"dataset version unavailable: {e}")
        new = self.create(cfg, kind="reproduction", parent=run_id)
        self.execute(new)
        a, b = self.read_json(run_id, "manifest.json"), self.read_json(new, "manifest.json")
        diffs = [k for k in COMPARED if a["artifacts"][k] != b["artifacts"][k]]
        code_changed = [k for k in ("strategy", "engine")
                        if a[k]["source_sha256"] != b[k]["source_sha256"]]
        out = dict(run_id=run_id, reproduction_run_id=new, identical=not diffs, differing_artifacts=diffs,
                   code_changed=code_changed, checked=list(COMPARED), at_utc=now_utc())
        self._set(new, reproduction=json.dumps(out))
        return out

class JobRunner:
    """Runs queued experiments on a small thread pool (one local user; independent runs in parallel)."""

    def __init__(self, store: ExperimentStore, workers: int = 2):
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="experiment")
        self.futures: dict[str, Future] = {}

    def submit(self, run_id: str) -> Future:
        f = self.pool.submit(self._run, run_id)
        self.futures[run_id] = f
        return f

    def _run(self, run_id: str):
        try:
            return self.store.execute(run_id)
        except Exception:  # noqa: BLE001 - the failure is recorded on the run itself
            return None

    def shutdown(self):
        self.pool.shutdown(wait=True, cancel_futures=False)
