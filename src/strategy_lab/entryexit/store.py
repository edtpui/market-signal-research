"""Entry-exit experiment store: one write-once directory per experiment plus catalog tables in catalog.db.

  <dir>/<experiment_id>/config.json          resolved EntryExitExperimentConfig (dataset version, universe, every
                                             entry and exit plan with its key, explicit partitions, seed)
                        study.json           data coverage, eligibility and matching report, partitions, timings
                        entries.json         per entry: events, triggers, unmatched counts
                        cells/<cell>/        summary.json (statistics per partition) and trades.parquet (every
                                             event's trade with its matched / random-date control means)
                        portfolio/<cell>/    <partition>.json (event-driven backtest metrics, random-entry
                                             portfolios) and <partition>_equity.parquet, _trades.parquet
                        baselines/           buy-and-hold of the universe per partition (event-driven engine)
                        tests.json           every (cell, partition) test with BH q-values and verdicts
                        comparisons.json     hold-entry (paired vs the baseline exit) and hold-exit tables
                        walk_forward.json    walk-forward selection and parameter sensitivity
                        manifest.json        code and data provenance, cell content hashes, number of tests
Finished directories are read-only. Reproduction is a new experiment from the saved config compared by content
hash. Ledgers (append-only by SQLite triggers): ee_data_usage (which partitions every experiment used and how many
cells it tested there) and ee_test_ledger (every evaluation of the locked test partition; the first evaluation of a
(cell, methodology, data) key stays the authoritative one in ee_locked_results).
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
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_lab.catalog import (QueuedStore, git_state, make_readonly, now_utc, package_versions, source_sha256,
                                  write_owner)
from strategy_lab.core.strategy import canonical_hash
from strategy_lab.data.policy import period_labels
from strategy_lab.entryexit import engine as E
from strategy_lab.entryexit.components import Common
from strategy_lab.entryexit.config import EntryExitExperimentConfig
from strategy_lab.entryexit.plan import PlanRuntime
from strategy_lab.research.store import _clean, _json, _write_parquet, read_parquet

CODE_DIRS = ("entryexit", "signals", "research", "engine", "data")
MATRIX_METRICS = ("timing_excess", "q_timing", "timing_beat_share", "mean_ret", "median_ret", "win_rate", "profit_factor", "expectancy_r", "avg_bars", "ret_per_session",
                  "trade_sharpe", "mae", "mfe", "stop_rate", "target_rate", "excess_matched", "excess_random_dates",
                  "excess_bench", "q", "consistency_years", "positive_years", "n")
PORTFOLIO_METRICS = ("total_return", "cagr", "sharpe", "sortino", "max_drawdown", "calmar", "volatility", "exposure",
                     "turnover", "win_rate", "profit_factor", "trade_count", "avg_trade_return", "excess_return")

DDL = """
CREATE TABLE IF NOT EXISTS ee_experiments (
  experiment_id TEXT PRIMARY KEY, name TEXT, kind TEXT NOT NULL, parent_id TEXT, dataset TEXT NOT NULL,
  dataset_version TEXT, universe_name TEXT, n_symbols INTEGER, start TEXT NOT NULL, end TEXT NOT NULL,
  analysis_end TEXT, config_hash TEXT NOT NULL, methodology_hash TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
  created_at_utc TEXT NOT NULL, started_at_utc TEXT, finished_at_utc TEXT, n_entries INTEGER, n_exits INTEGER,
  n_cells INTEGER, progress_done INTEGER NOT NULL DEFAULT 0, progress_total INTEGER NOT NULL DEFAULT 0,
  stage TEXT, evaluate_test INTEGER, partitions TEXT, periods TEXT, summary TEXT, reproduction TEXT);
CREATE TABLE IF NOT EXISTS ee_cells (
  experiment_id TEXT NOT NULL, cell TEXT NOT NULL, partition TEXT NOT NULL, role TEXT, entry_key TEXT, entry_id TEXT,
  entry_name TEXT, entry_family TEXT, entry_kind TEXT, exit_key TEXT, exit_name TEXT, exit_family TEXT,
  exit_components INTEGER, n INTEGER, mean_ret REAL, win_rate REAL, profit_factor REAL, expectancy_r REAL,
  avg_bars REAL, excess_matched REAL, q REAL, verdict TEXT, consistency_years REAL, sharpe REAL, cagr REAL,
  max_drawdown REAL, sortino REAL, total_return REAL, portfolio_trades INTEGER,
  PRIMARY KEY (experiment_id, cell, partition));
CREATE TABLE IF NOT EXISTS ee_saved_configs (
  config_id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT NOT NULL, created_at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ee_data_usage (
  experiment_id TEXT NOT NULL, dataset TEXT NOT NULL, dataset_version TEXT, partition TEXT NOT NULL,
  role TEXT NOT NULL, start TEXT, end TEXT, n_cells INTEGER NOT NULL, at_utc TEXT NOT NULL,
  PRIMARY KEY (experiment_id, partition));
CREATE TABLE IF NOT EXISTS ee_test_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL, cell TEXT NOT NULL, lock_key TEXT NOT NULL,
  test_start TEXT, test_end TEXT, at_utc TEXT NOT NULL, first_use INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ee_locked_results (
  lock_key TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, cell TEXT NOT NULL, result TEXT NOT NULL,
  at_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ee_imports (
  strategy_id TEXT PRIMARY KEY, version TEXT NOT NULL, experiment_id TEXT NOT NULL, cell TEXT NOT NULL,
  module_file TEXT NOT NULL, record TEXT NOT NULL, created_at_utc TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS ee_usage_no_update BEFORE UPDATE ON ee_data_usage
  BEGIN SELECT RAISE(ABORT, 'ee_data_usage is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ee_usage_no_delete BEFORE DELETE ON ee_data_usage
  BEGIN SELECT RAISE(ABORT, 'ee_data_usage is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ee_test_ledger_no_update BEFORE UPDATE ON ee_test_ledger
  BEGIN SELECT RAISE(ABORT, 'ee_test_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ee_test_ledger_no_delete BEFORE DELETE ON ee_test_ledger
  BEGIN SELECT RAISE(ABORT, 'ee_test_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS ee_locked_no_update BEFORE UPDATE ON ee_locked_results
  BEGIN SELECT RAISE(ABORT, 'locked results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ee_locked_no_delete BEFORE DELETE ON ee_locked_results
  BEGIN SELECT RAISE(ABORT, 'locked results are immutable'); END;
"""


def code_sha256() -> str:
    return source_sha256(CODE_DIRS, extra_files=("strategies/entry_exit.py",))


def cell_id(ei: int, pi: int) -> str:
    return f"e{ei:03d}_x{pi:03d}"


def content_sha256(summary: dict, trades: pd.DataFrame) -> str:
    s = _clean(summary)
    h = hashlib.sha256(json.dumps(s, sort_keys=True, separators=(",", ":")).encode())
    buf = io.StringIO()
    trades.to_csv(buf, index=False, float_format="%.12g", lineterminator="\n")
    h.update(buf.getvalue().encode())
    return h.hexdigest()


def flat_row(summ: dict) -> dict:
    """The matrix metrics of one (cell, partition) summary."""
    m = summ.get("matched") or {}
    rd = summ.get("random_dates") or {}
    return dict(n=summ.get("n"), mean_ret=summ.get("mean_ret"), median_ret=summ.get("median_ret"),
                win_rate=summ.get("win_rate"), profit_factor=summ.get("profit_factor"),
                expectancy_r=summ.get("expectancy_r"), avg_bars=summ.get("avg_bars"),
                ret_per_session=summ.get("ret_per_session"), trade_sharpe=summ.get("trade_sharpe"),
                mae=summ.get("mae"), mfe=summ.get("mfe"), stop_rate=summ.get("stop_rate"),
                target_rate=summ.get("target_rate"), excess_matched=m.get("excess"),
                excess_random_dates=rd.get("excess"), excess_bench=summ.get("excess_bench"),
                consistency_years=summ.get("consistency_years"), positive_years=summ.get("positive_years"),
                p=m.get("p"), ci=m.get("ci"), timing_excess=(summ.get("timing") or {}).get("excess"),
                timing_p=(summ.get("timing") or {}).get("p"), timing_beat_share=(summ.get("timing") or {}).get("beat_share"))


class EntryExitStore(QueuedStore):
    TABLE, KEY, CONFIGS_TABLE = "ee_experiments", "experiment_id", "ee_saved_configs"
    CONFIG_MODEL = EntryExitExperimentConfig
    CONFIG_ID_PREFIX, CONFIG_ID_HEX = "eec-", 10
    LABEL = "entry-exit experiment"
    JSON_COLS, BOOL_COLS = ("partitions", "summary", "reproduction"), ("evaluate_test",)

    def __init__(self, root: Path, catalog: Path, lab, workers: int = 1):
        super().__init__(root, catalog, lab, DDL, workers, "entry-exit")

    # -- creation ---------------------------------------------------------------------------------------------------
    def resolve(self, cfg: EntryExitExperimentConfig):
        return E.resolve_config(cfg, self.lab)

    def create(self, cfg: EntryExitExperimentConfig, kind: str = "study", parent: str | None = None) -> str:
        resolved, entries, plans = self.resolve(cfg)
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        eid = f"ee-{stamp}-{resolved.config_hash()[:6]}-{uuid.uuid4().hex[:4]}"
        d = self.root / eid
        d.mkdir(parents=True)
        _json(_clean(resolved.model_dump(mode="json")), d / "config.json")
        write_owner(d)
        parts = [dict(name=p.name, role=p.role, start=p.start.isoformat(), end=p.end.isoformat(),
                      locked=p.role == "test" and not resolved.evaluate_test) for p in resolved.partitions]
        with self._db() as c:
            c.execute("INSERT INTO ee_experiments (experiment_id, name, kind, parent_id, dataset, dataset_version, "
                      "universe_name, n_symbols, start, end, analysis_end, config_hash, methodology_hash, status, "
                      "created_at_utc, n_entries, n_exits, n_cells, evaluate_test, partitions, periods) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (eid, resolved.name, kind, parent, resolved.dataset, resolved.dataset_version,
                       cfg.universe_name or ("custom" if cfg.universe else None), len(resolved.universe),
                       resolved.start.isoformat(), resolved.end.isoformat(), resolved.analysis_end.isoformat(),
                       resolved.config_hash(), resolved.methodology_hash(), "queued", now_utc(), len(entries),
                       len(plans), len(entries) * len(plans), int(resolved.evaluate_test), json.dumps(parts),
                       ",".join(period_labels(resolved.start, resolved.analysis_end))))
        return eid

    # -- execution --------------------------------------------------------------------------------------------------
    def execute(self, eid: str) -> dict:
        meta = self.get(eid)
        if meta is None:
            raise KeyError(f"no entry-exit experiment {eid}")
        if meta["status"] != "queued":
            raise E.EntryExitError(f"{eid} is {meta['status']}; an experiment is never re-executed (reproduce it)")
        d = self.root / eid
        cfg = self.config(eid)
        cancel = self._cancel.setdefault(eid, threading.Event())
        write_owner(d)
        self._set(eid, status="running", started_at_utc=now_utc(), stage="prepare")
        t0 = time.time()
        try:
            out = self._run(eid, d, cfg, cancel)
            status = "cancelled" if cancel.is_set() else "done"
            self._set(eid, status=status, finished_at_utc=now_utc(), stage="done",
                      summary=json.dumps(_clean(dict(out, seconds=round(time.time() - t0, 1)))),
                      error="cancelled by the user" if status == "cancelled" else None)
            make_readonly(d)
            return dict(experiment_id=eid, status=status, summary=out)
        except Exception as e:  # noqa: BLE001 - recorded, then re-raised
            (d / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
            status = "cancelled" if isinstance(e, E.Cancelled) else "failed"
            self._set(eid, status=status, finished_at_utc=now_utc(), error=f"{type(e).__name__}: {e}"[:2000])
            make_readonly(d)
            if status == "cancelled":
                return dict(experiment_id=eid, status=status)
            raise
        finally:
            self._cancel.pop(eid, None)

    def _run(self, eid: str, d: Path, cfg: EntryExitExperimentConfig, cancel) -> dict:
        lab = self.lab
        entries = E.all_entries(cfg, lab.signals)
        plans = E.expand_exits(cfg)
        if [r.key for r in entries] != [x["key"] for x in cfg.resolved_entries or []] or \
                [p.key for p in plans] != [x["key"] for x in cfg.resolved_exits or []]:
            raise E.EntryExitError("the catalogs resolve this config to different keys than when it was saved (a "
                                   "signal or exit definition changed)")
        es = E.prepare(cfg, entries, plans, lab, cancel)
        S = es.study
        _json(_clean(dict(report=es.report, timings=es.timings, parts=S.parts)), d / "study.json")
        self._record_usage(eid, cfg, S, len(entries) * len(plans))
        events = []
        for r in entries:
            E._check(cancel)
            events.append(E.entry_events(es, r))
        _json(_clean([dict(key=r.key, name=r.name, family=r.family, kind=r.spec.kind, counts=ev.counts)
                      for r, ev in zip(entries, events)]), d / "entries.json")
        n_cells = len(entries) * len(plans)
        self._set(eid, stage="cells", progress_total=n_cells, progress_done=0)
        (d / "cells").mkdir(exist_ok=True)
        base_key = E.baseline_key(cfg, plans)
        base_pi = next((pi for pi, p in enumerate(plans) if p.key == base_key), None)
        summaries, tests, wf_cells, hashes = {}, [], {}, {}
        base_trades: dict[int, pd.DataFrame] = {}
        pair_rows = []
        timing_tests, timing_pe, styles, ev_t, part_of, entry_of = [], {}, {}, {}, {}, {}
        done = 0
        t_cells = time.time()
        order = ([base_pi] if base_pi is not None else []) + [pi for pi in range(len(plans)) if pi != base_pi]
        for pi in order:
            plan = plans[pi]
            rt = PlanRuntime(plan, E._common(cfg), max_hold=cfg.max_hold)
            ser = E.plan_series(es, rt)
            for ei, (r, ev) in enumerate(zip(entries, events)):
                E._check(cancel)
                cid = cell_id(ei, pi)
                tr, cm, rdm = E.replay_cell(es, rt, ser, ev)
                tm = E.timing_returns(es, rt, ser, ev, plan)
                summ = E.cell_stats(es, tr, cm, rdm, f"{r.key}|{plan.key}", tm)
                trades = E.cell_trades_frame(es, tr, cm, rdm, tm)
                cdir = d / "cells" / cid
                cdir.mkdir(parents=True, exist_ok=True)
                head = dict(cell=cid, entry=dict(key=r.key, name=r.name, family=r.family, kind=r.spec.kind,
                                                 id=r.id, index=ei),
                            exit=dict(key=plan.key, name=plan.name, family=plan.family, index=pi,
                                      components=len(plan.components), kinds=plan.kinds, trigger=plan.trigger,
                                      deployable=plan.deployable, style=plan.style, timing=bool(tm is not None),
                                      lookahead=plan.lookahead),
                            counts=ev.counts, partitions=summ)
                _json(_clean(head), cdir / "summary.json")
                _write_parquet(trades, cdir / "trades.parquet")
                hashes[cid] = content_sha256(head, trades)
                summaries[cid] = summ
                for pname, s in summ.items():
                    m = s.get("matched") or {}
                    tests.append(dict(cell=cid, partition=pname, role=s["role"], n=s.get("n", 0),
                                      n_dates=s.get("n_dates", 0), excess=m.get("excess"), ci=m.get("ci", [None, None]),
                                      p=m.get("p"), avg_bars=s.get("avg_bars")))
                if tm is not None:
                    for pname, s_ in summ.items():
                        tb = s_.get("timing") or {}
                        if tb.get("excess") is not None:
                            timing_tests.append(dict(cell=cid, entry=ei, exit=pi, partition=pname, role=s_["role"],
                                                     style=plan.style, n=tb["n"], excess=tb["excess"], ci=tb["ci"],
                                                     p=tb["p"], beat_share=tb.get("beat_share"),
                                                     positive_years=tb.get("positive_years"),
                                                     calibration=plan.style == "calibration"))
                    okt = (tr["status"] == "ok").to_numpy()
                    timing_pe[cid] = np.where(okt, tr["ret"].to_numpy(float) - E.rowmean(tm), np.nan)
                    styles[cid], ev_t[cid], part_of[cid], entry_of[cid] = plan.style, ev.t, ev.part, ei
                cmean = trades["matched_mean"].to_numpy(float) if "matched_mean" in trades else np.full(len(tr), np.nan)
                ok = (tr["status"] == "ok").to_numpy()
                wf_cells[cid] = dict(entry_bar=np.where(ok, tr["entry_bar"], -1), exit_bar=np.where(ok, tr["exit_bar"],
                                                                                                   -1),
                                     ret=np.where(ok, tr["ret"], np.nan), r_mult=np.where(ok, tr["r_mult"], np.nan),
                                     bars=np.where(ok, tr["bars"], np.nan),
                                     excess=np.where(ok, tr["ret"].to_numpy(float) - cmean, np.nan))
                if pi == base_pi:
                    base_trades[ei] = tr
                elif base_pi is not None and ei in base_trades:
                    for pname, row in E.paired(es, base_trades[ei], tr, f"{r.key}|{plan.key}").items():
                        pair_rows.append(dict(entry=ei, exit=pi, cell=cid, base_cell=cell_id(ei, base_pi),
                                              partition=pname, **row))
                done += 1
                self._set(eid, progress_done=done)
            del ser
        es.timings["cells_s"] = round(time.time() - t_cells, 2)
        tests = E.finalize(cfg, tests, S.ppy)
        pair_rows = E.finalize_pairs(pair_rows)
        # the random-timing exit benchmark: BH over the real rules; calibration rows are checks, reported apart
        real = E.finalize_timing([t for t in timing_tests if not t["calibration"]])
        calib = [dict(t, q=None) for t in timing_tests if t["calibration"]]
        groups_ = E.style_groups(es, {c: v for c, v in timing_pe.items() if styles[c] != "calibration"}, styles,
                                 ev_t, part_of, entry_of)
        _json(_clean(dict(
            tests=real, calibration=calib, style_groups=groups_, draws=cfg.timing.draws,
            method="each plan's condition signals moved to random dates on the same stock (circular shift by a random "
                   "offset inside the stock's history: same stock, same number of signals, same spacing); the same "
                   "entries replayed; excess = real return - mean of the shifted copies, per trade",
            note="Placebo rules (random-day sells) should land near zero and the cheating rule (knows tomorrow's drop) "
                 "far above it; if not, the benchmark is not trustworthy for this experiment.")), d / "timing.json")
        _json(_clean(dict(tests=tests, m_tests={p["name"]: sum(1 for t in tests if t["partition"] == p["name"] and
                                                               t["p"] is not None) for p in S.parts},
                          n_cells=n_cells)), d / "tests.json")
        by_test = {(t["cell"], t["partition"]): t for t in tests}

        # hold exit constant: entries ranked by their excess over matched random entries under each exit
        hold_exit = {}
        for pi, plan in enumerate(plans):
            for p in S.parts:
                rows = []
                for ei, r in enumerate(entries):
                    t = by_test[(cell_id(ei, pi), p["name"])]
                    rows.append(dict(entry=ei, cell=cell_id(ei, pi), excess=t["excess"], q=t.get("q"),
                                     verdict=t["verdict"]["category"], n=t["n"]))
                rows.sort(key=lambda x: -np.inf if x["excess"] is None else -x["excess"])
                hold_exit.setdefault(str(pi), {})[p["name"]] = rows
        B = cfg.controls.permutations
        m_pairs = max([r.get("m_tests", 0) for r in pair_rows], default=0)
        note = (f"With {B} sign flips the smallest empirical p is {1 / (B + 1):.4f}; BH across {m_pairs} pairs of a "
                f"partition needs p <= {cfg.stats.alpha / max(m_pairs, 1):.5f} for the best pair, so q is reported "
                "from the empirical p and q_z from the normal approximation of the same null")
        _json(_clean(dict(baseline=dict(key=base_key, index=base_pi), hold_entry=pair_rows, hold_exit=hold_exit,
                          resolution_note=note if m_pairs * (1 / (B + 1)) > cfg.stats.alpha else None)),
              d / "comparisons.json")

        # walk-forward selection and parameter sensitivity
        groups = {"all cells": list(summaries)}
        for ei, r in enumerate(entries):
            groups[f"exit for entry {ei}"] = [cell_id(ei, pi) for pi in range(len(plans))]
        for pi, plan in enumerate(plans):
            groups[f"entry for exit {pi}"] = [cell_id(ei, pi) for ei in range(len(entries))]
        wf = E.walk_forward(es, wf_cells, groups)
        sens = self._sensitivity(cfg, entries, plans, summaries, S)
        _json(_clean(dict(walk_forward=wf, sensitivity=sens)), d / "walk_forward.json")

        # portfolio layer: the event-driven engine
        self._set(eid, stage="portfolio")
        t_port = time.time()
        port = self._portfolios(eid, d, es, entries, plans, events, summaries, by_test, cancel)
        es.timings["portfolio_s"] = round(time.time() - t_port, 2)
        self._index_cells(eid, entries, plans, summaries, by_test, port)
        if cfg.evaluate_test and any(p["role"] == "test" for p in S.parts):
            self._record_test_use(eid, cfg, entries, plans, summaries, S)
        manifest = self._manifest(eid, cfg, es, hashes, n_cells, tests)
        _json(_clean(manifest), d / "manifest.json")
        verdicts: dict[str, dict] = {}
        for t in tests:
            verdicts.setdefault(t["partition"], {})
            v = t["verdict"]["category"]
            verdicts[t["partition"]][v] = verdicts[t["partition"]].get(v, 0) + 1
        return dict(entries=len(entries), exits=len(plans), cells=n_cells, verdicts=verdicts,
                    portfolio_runs=port["runs"], timings=es.timings,
                    events={r.key: ev.counts["events"] for r, ev in zip(entries, events)})

    def _sensitivity(self, cfg, entries, plans, summaries, S) -> list[dict]:
        """Parameter sensitivity of every exit grid, per entry (grids are recorded at resolution)."""
        out = []
        train = next((p["name"] for p in S.parts if p["role"] == "train"), S.parts[0]["name"])
        valid = next((p["name"] for p in S.parts if p["role"] == "validation"), None)
        keys = {p.key: pi for pi, p in enumerate(plans)}
        for g in cfg.resolved_grids or []:
            for ei in range(len(entries)):
                gc = [(pt["params"], cell_id(ei, keys[pt["key"]])) for pt in g["points"] if pt["key"] in keys]
                s = E.sensitivity(gc, train, valid, summaries)
                if s:
                    out.append(dict(grid=g["grid"], entry=ei, component=g["component"], **s))
        return out

    def _portfolios(self, eid, d, es, entries, plans, events, summaries, by_test, cancel) -> dict:
        cfg = es.cfg
        S = es.study
        pc = cfg.portfolio
        parts = [p for p in S.parts]
        train = next((p["name"] for p in parts if p["role"] == "train"), parts[0]["name"])
        # calibration plans are benchmark checks, never strategies
        cells = [(ei, pi) for pi in range(len(plans)) for ei in range(len(entries)) if plans[pi].style != "calibration"]
        if pc.run == "none":
            return dict(runs=0, cells={})
        if pc.run == "top":
            def score(c):
                t = by_test[(cell_id(*c), train)]
                return -np.inf if t["excess"] is None else t["excess"]
            cells = sorted(cells, key=score, reverse=True)[: pc.top]
        (d / "portfolio").mkdir(exist_ok=True)
        (d / "baselines").mkdir(exist_ok=True)
        runs = 0
        total = len(cells) * len(parts) * (1 + min(pc.random_controls, cfg.controls.draws if cfg.controls.matched
                                                   else 0))
        self._set(eid, progress_total=total, progress_done=0)
        if pc.baseline_buy_and_hold:
            for p in parts:
                E._check(cancel)
                res = E.buy_and_hold(es, p)
                _json(_clean(dict(metrics=res["metrics"], reasons=res["reasons"])), d / "baselines" /
                      f"buy_and_hold_{p['name']}.json")
                _write_parquet(res["equity"], d / "baselines" / f"buy_and_hold_{p['name']}_equity.parquet")
        series_cache: dict[int, dict] = {}
        out = {}
        for ei, pi in cells:
            E._check(cancel)
            plan, ev = plans[pi], events[ei]
            if pi not in series_cache:
                series_cache.clear()
                rt = PlanRuntime(plan, E._common(cfg), max_hold=cfg.max_hold)
                series_cache[pi] = {k: v for k, v in rt.series(S.ind).items()}
            frames = series_cache[pi]
            _, cls = E.strategy_for(es, entries[ei], plan)
            cid = cell_id(ei, pi)
            cdir = d / "portfolio" / cid
            cdir.mkdir(parents=True, exist_ok=True)
            out[cid] = {}
            for k, p in enumerate(parts):
                sel = ev.part == k
                res = E.run_portfolio(es, cls, frames, E.event_mask(es, ev.t[sel], ev.s[sel]), p)
                runs += 1
                ctrl = []
                R = min(pc.random_controls, ev.ctrl.shape[1] if ev.ctrl is not None else 0)
                for dd in range(R):
                    E._check(cancel)
                    cs = ev.ctrl[sel, dd]
                    okc = cs >= 0
                    rc = E.run_portfolio(es, cls, frames, E.event_mask(es, ev.t[sel][okc], cs[okc]), p)
                    ctrl.append(rc["metrics"])
                    runs += 1
                pm = res["metrics"]
                cmp = {}
                for mname in ("total_return", "cagr", "sharpe", "max_drawdown", "sortino"):
                    vals = [c.get(mname) for c in ctrl if c.get(mname) is not None]
                    v = pm.get(mname)
                    cmp[mname] = dict(controls=vals, mean=float(np.mean(vals)) if vals else None,
                                      excess=(v - float(np.mean(vals))) if vals and v is not None else None,
                                      share_below=float(np.mean([x < v for x in vals])) if vals and v is not None
                                      else None)
                body = dict(cell=cid, partition=p["name"], role=p["role"], metrics=pm, reasons=res["reasons"],
                            random_entry=cmp, n_random=len(ctrl), events=int(sel.sum()))
                _json(_clean(body), cdir / f"{p['name']}.json")
                _write_parquet(res["equity"], cdir / f"{p['name']}_equity.parquet")
                tr = res["trades"].copy()
                for c in ("entry_date", "exit_date"):
                    tr[c] = tr[c].astype(str)
                _write_parquet(tr, cdir / f"{p['name']}_trades.parquet")
                _write_parquet(res["monthly"], cdir / f"{p['name']}_monthly.parquet")
                out[cid][p["name"]] = body
                self._set(eid, progress_done=runs)
        return dict(runs=runs, cells=out)

    def _index_cells(self, eid, entries, plans, summaries, by_test, port) -> None:
        rows = []
        for cid, summ in summaries.items():
            ei, pi = int(cid[1:4]), int(cid[6:9])
            r, plan = entries[ei], plans[pi]
            for pname, s in summ.items():
                t = by_test[(cid, pname)]
                pm = (port.get("cells", {}).get(cid, {}).get(pname) or {}).get("metrics") or {}
                rows.append((eid, cid, pname, s["role"], r.key, r.id, r.name, r.family, r.spec.kind, plan.key,
                             plan.name, plan.family, len(plan.components), s.get("n"), s.get("mean_ret"),
                             s.get("win_rate"), s.get("profit_factor"), s.get("expectancy_r"), s.get("avg_bars"),
                             t.get("excess"), t.get("q"), t["verdict"]["category"], s.get("consistency_years"),
                             pm.get("sharpe"), pm.get("cagr"), pm.get("max_drawdown"), pm.get("sortino"),
                             pm.get("total_return"), pm.get("trade_count")))
        with self._db() as c:
            c.executemany("INSERT OR REPLACE INTO ee_cells VALUES (" + ",".join("?" * 29) + ")",
                          [tuple(_clean(list(x))) for x in rows])

    def _record_usage(self, eid, cfg, S, n_cells) -> None:
        with self._db() as c:
            for p in S.parts:
                c.execute("INSERT INTO ee_data_usage VALUES (?,?,?,?,?,?,?,?,?)",
                          (eid, cfg.dataset, cfg.dataset_version, p["name"], p["role"], p["start"], p["end"], n_cells,
                           now_utc()))

    def _record_test_use(self, eid, cfg, entries, plans, summaries, S) -> None:
        test = next(p for p in S.parts if p["role"] == "test")
        with self._db() as c:
            for cid, summ in summaries.items():
                ei, pi = int(cid[1:4]), int(cid[6:9])
                lk = canonical_hash(dict(entry=entries[ei].key, exit=plans[pi].key, methodology=cfg.methodology_hash(),
                                         dataset=cfg.dataset, version=cfg.dataset_version, universe=cfg.universe,
                                         test=[test["start"], test["end"]]))
                first = c.execute("SELECT 1 FROM ee_locked_results WHERE lock_key=?", (lk,)).fetchone() is None
                c.execute("INSERT INTO ee_test_ledger (experiment_id, cell, lock_key, test_start, test_end, at_utc, "
                          "first_use) VALUES (?,?,?,?,?,?,?)", (eid, cid, lk, test["start"], test["end"], now_utc(),
                                                               int(first)))
                if first:
                    c.execute("INSERT INTO ee_locked_results VALUES (?,?,?,?,?)",
                              (lk, eid, cid, json.dumps(_clean(summ.get(test["name"]))), now_utc()))

    def _manifest(self, eid, cfg, es, hashes, n_cells, tests) -> dict:
        prior = self.prior_usage(eid)
        return dict(experiment_id=eid, created_utc=now_utc(), code_sha256=code_sha256(), git=git_state(),
                    versions=package_versions(), dataset=cfg.dataset, dataset_version=cfg.dataset_version,
                    config_hash=cfg.config_hash(), methodology_hash=cfg.methodology_hash(), cells=hashes,
                    n_cells=n_cells, n_tests=sum(1 for t in tests if t["p"] is not None), prior_usage=prior,
                    timings=es.timings,
                    label="EXPLORATORY: dev workspace, data <= 2025-12-31; universes are today's lists (survivorship "
                          "bias)")

    # -- reading ----------------------------------------------------------------------------------------------------
    def cell(self, eid: str, cid: str) -> dict:
        p = self.path(eid) / "cells" / cid / "summary.json"
        if not p.exists():
            raise KeyError(f"no cell {cid} in {eid}")
        out = json.loads(p.read_text(encoding="utf-8"))
        port = {}
        pdir = self.path(eid) / "portfolio" / cid
        if pdir.exists():
            for f in sorted(pdir.glob("*.json")):
                port[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        out["portfolio"] = port
        tests = (self.read_json(eid, "tests.json") or {}).get("tests", [])
        out["tests"] = [t for t in tests if t["cell"] == cid]
        comp = self.read_json(eid, "comparisons.json") or {}
        out["vs_baseline"] = [r for r in comp.get("hold_entry", []) if r["cell"] == cid]
        tj = self.read_json(eid, "timing.json") or {}
        out["timing_tests"] = [t for t in tj.get("tests", []) + tj.get("calibration", []) if t["cell"] == cid]
        return out

    def cell_trades(self, eid: str, cid: str, limit: int = 5000, partition: str | None = None) -> pd.DataFrame:
        p = self.path(eid) / "cells" / cid / "trades.parquet"
        if not p.exists():
            raise KeyError(f"no cell {cid} in {eid}")
        where = f"WHERE partition = '{partition.replace(chr(39), '')}'" if partition else ""
        return read_parquet(p, limit=limit, where=where)

    def cell_equity(self, eid: str, cid: str, partition: str) -> dict:
        pdir = self.path(eid) / "portfolio" / cid
        f = pdir / f"{partition}_equity.parquet"
        if not f.exists():
            raise KeyError(f"no portfolio run of {cid} on {partition}")
        out = dict(equity=read_parquet(f))
        for k in ("trades", "monthly"):
            g = pdir / f"{partition}_{k}.parquet"
            out[k] = read_parquet(g) if g.exists() else pd.DataFrame()
        b = self.path(eid) / "baselines" / f"buy_and_hold_{partition}_equity.parquet"
        out["buy_and_hold"] = read_parquet(b) if b.exists() else pd.DataFrame()
        return out

    def matrix(self, eid: str, partition: str, metric: str) -> dict:
        """Rows = entries, columns = exit plans, cells = the metric (trade-level or portfolio) on one partition."""
        cfg = self.config(eid)
        entries, exits = cfg.resolved_entries or [], cfg.resolved_exits or []
        tests = {(t["cell"], t["partition"]): t for t in (self.read_json(eid, "tests.json") or {}).get("tests", [])}
        tj = self.read_json(eid, "timing.json") or {}
        ttests = {(t["cell"], t["partition"]): t for t in tj.get("tests", []) + tj.get("calibration", [])}
        rows = []
        for ei, e in enumerate(entries):
            row = []
            for pi, x in enumerate(exits):
                cid = cell_id(ei, pi)
                s = self.read_json(eid, f"cells/{cid}/summary.json")
                summ = (s or {}).get("partitions", {}).get(partition) or {}
                flat = flat_row(summ) if summ else {}
                t = tests.get((cid, partition), {})
                pf = self.path(eid) / "portfolio" / cid / f"{partition}.json"
                pm = json.loads(pf.read_text(encoding="utf-8")) if pf.exists() else None
                val = None
                if metric in PORTFOLIO_METRICS:
                    val = (pm or {}).get("metrics", {}).get(metric)
                elif metric == "q":
                    val = t.get("q")
                elif metric == "q_timing":
                    val = ttests.get((cid, partition), {}).get("q")
                elif metric == "excess_random_portfolio":
                    val = ((pm or {}).get("random_entry") or {}).get("sharpe", {}).get("excess")
                else:
                    val = flat.get(metric)
                tt = ttests.get((cid, partition), {})
                if metric.startswith("timing") or metric == "q_timing":
                    qcell = tt.get("q")
                else:
                    qcell = t.get("q")
                row.append(dict(cell=cid, value=val, n=flat.get("n"), q=qcell,
                                verdict=(t.get("verdict") or {}).get("category"), has_portfolio=pm is not None))
            rows.append(row)
        return dict(partition=partition, metric=metric, entries=[dict(index=i, key=e["key"], name=e["name"],
                                                                      family=e["family"], kind=e["kind"])
                                                                 for i, e in enumerate(entries)],
                    exits=[dict(index=i, key=x["key"], name=x["name"], family=x["family"], kinds=x["kinds"],
                                deployable=x["deployable"], components=len(x["components"]),
                                style=x.get("style", "neutral"), timing=bool(x.get("shiftable")))
                           for i, x in enumerate(exits)], cells=rows)

    def leaderboard(self, board: str, objective: str, partition_role: str = "validation", limit: int = 100,
                    experiment_id: str | None = None, min_trades: int = 30, max_dd: float | None = None) -> list[dict]:
        """Ranked (experiment, cell, partition) rows from the catalog. board: entries | exits | pairs | multi_entry |
        systems."""
        where, args = ["role = ?", "n >= ?"], [partition_role, min_trades]
        if experiment_id:
            where.append("experiment_id = ?")
            args.append(experiment_id)
        if board == "multi_entry":
            where.append("entry_kind = 'combination'")
        elif board == "systems":
            where.append("entry_kind = 'combination' AND exit_components > 1")
        if max_dd is not None:
            where.append("max_drawdown >= ?")
            args.append(-abs(max_dd))
        order = {"risk_adjusted": "sharpe", "low_drawdown": "max_drawdown", "expectancy": "expectancy_r",
                 "consistency": "consistency_years", "oos_excess": "excess_matched", "confidence": "q",
                 "cagr": "cagr", "mean_ret": "mean_ret"}.get(objective, "excess_matched")
        desc = "ASC" if order == "q" else "DESC"
        with self._db() as c:
            rows = [dict(r) for r in c.execute(
                f"SELECT * FROM ee_cells WHERE {' AND '.join(where)} AND {order} IS NOT NULL "
                f"ORDER BY {order} {desc} LIMIT ?", (*args, limit * 20 if board in ('entries', 'exits') else limit))]
        if board in ("entries", "exits"):
            k = "entry_key" if board == "entries" else "exit_key"
            agg: dict[tuple, dict] = {}
            for r in rows:
                g = agg.setdefault((r["experiment_id"], r[k]), dict(experiment_id=r["experiment_id"], key=r[k],
                                                                     name=r["entry_name" if board == "entries"
                                                                            else "exit_name"],
                                                                     family=r["entry_family" if board == "entries"
                                                                              else "exit_family"],
                                                                     values=[], cells=0, supported=0))
                g["values"].append(r[order])
                g["cells"] += 1
                g["supported"] += int(r["verdict"] == "supported")
            out = []
            for g in agg.values():
                vals = [v for v in g.pop("values") if v is not None]
                g["mean"], g["best"] = float(np.mean(vals)), (min(vals) if order == "q" else max(vals))
                out.append(g)
            out.sort(key=lambda x: x["mean"], reverse=desc == "DESC")
            return out[:limit]
        return rows[:limit]

    def usage(self) -> list[dict]:
        with self._db() as c:
            return [dict(r) for r in c.execute("SELECT * FROM ee_data_usage ORDER BY at_utc")]

    def prior_usage(self, eid: str) -> list[dict]:
        with self._db() as c:
            me = c.execute("SELECT created_at_utc FROM ee_experiments WHERE experiment_id=?", (eid,)).fetchone()
            if me is None:
                return []
            return [dict(r) for r in c.execute(
                "SELECT partition, COUNT(DISTINCT experiment_id) AS experiments, SUM(n_cells) AS cells "
                "FROM ee_data_usage WHERE experiment_id IN (SELECT experiment_id FROM ee_experiments WHERE "
                "created_at_utc < ?) GROUP BY partition", (me[0],))]

    def reproduce(self, eid: str) -> dict:
        meta = self.get(eid)
        if meta is None or meta["status"] != "done":
            raise E.EntryExitError(f"{eid} is not a finished experiment")
        cfg = self.config(eid)
        new = self.create(cfg, kind="reproduction", parent=eid)
        self.execute(new)
        a = (self.read_json(eid, "manifest.json") or {}).get("cells", {})
        b = (self.read_json(new, "manifest.json") or {}).get("cells", {})
        same = [k for k in a if a.get(k) == b.get(k)]
        res = dict(original=eid, reproduction=new, cells=len(a), identical=len(same),
                   differing=sorted(set(a) - set(same)),
                   code_changed=(self.read_json(eid, "manifest.json") or {}).get("code_sha256") !=
                   (self.read_json(new, "manifest.json") or {}).get("code_sha256"))
        self._set(new, reproduction=json.dumps(res))
        return res

    def imports(self) -> list[dict]:
        with self._db() as c:
            return [dict(r) for r in c.execute("SELECT strategy_id, version, experiment_id, cell, module_file, "
                                               "created_at_utc FROM ee_imports ORDER BY created_at_utc")]

    def record_import(self, record: dict, module_file: Path) -> None:
        with self._db() as c:
            c.execute("INSERT INTO ee_imports VALUES (?,?,?,?,?,?,?)",
                      (record["strategy_id"], record["version"], record["research"]["experiment_id"],
                       record["research"]["cell"], str(module_file), json.dumps(record, sort_keys=True), now_utc()))

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["EntryExitStore", "cell_id", "MATRIX_METRICS", "PORTFOLIO_METRICS", "Common"]
