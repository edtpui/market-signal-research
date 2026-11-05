"""Import an entry-exit cell into the Strategy Library: build the record from a finished experiment (entry and exit
definitions, parameters, sizing, costs, the cell's trade-level and portfolio results per partition, the random-entry
and baseline comparisons, walk-forward rows, provenance), write the generated module (write-once) and register it.

The imported strategy runs in the existing engine (Backtesting) and deploys through the existing forward adapter;
ForwardService refuses it until a backtest run of the same strategy version exists (requires_historical_test)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re

from strategy_lab.entryexit.engine import EntryExitError
from strategy_lab.strategies.entry_exit import WARNING, EntryExitDefinition, load_module, write_module


def strategy_id_for(entry_id: str, exit_key: str, eid: str, cell: str) -> str:
    h = hashlib.sha256(f"{eid}|{cell}".encode()).hexdigest()[:6]
    slug = re.sub(r"[^a-z0-9]+", "_", exit_key.split("@")[0].lower()).strip("_")
    return f"ee_{entry_id[:24]}_{slug[5:29] or 'exit'}_{h}"


def build_record(lab, eid: str, cell: str, name: str = "") -> dict:
    st = lab.entry_exit
    meta = st.get(eid)
    if meta is None:
        raise KeyError(f"no entry-exit experiment {eid}")
    if meta["status"] not in ("done", "cancelled"):
        raise EntryExitError(f"{eid} is {meta['status']}: import from a finished experiment")
    cfg = st.config(eid)
    c = st.cell(eid, cell)
    if c["entry"].get("kind") == "random":
        raise EntryExitError("random entries are a benchmark, not a strategy: import a cell whose entry is a signal")
    if c["exit"].get("lookahead") or c["exit"].get("style") == "calibration":
        raise EntryExitError("calibration exits (placebo and cheating rules) check the benchmark; they are never "
                             "imported into the Strategy Library")
    ei, pi = c["entry"]["index"], c["exit"]["index"]
    entry = cfg.entries[ei]
    plan = cfg.exits[pi]
    d = EntryExitDefinition(entry=entry, exit=plan, ref_atr_n=cfg.ref_atr_n, ref_atr_mult=cfg.ref_atr_mult,
                            max_hold=cfg.max_hold, sizing=cfg.portfolio.sizing,
                            max_positions=cfg.portfolio.max_positions,
                            eligibility=dict(min_history=cfg.eligibility.min_history,
                                             min_price=cfg.eligibility.min_price,
                                             min_dollar_volume=cfg.eligibility.min_dollar_volume),
                            benchmark=cfg.benchmarks[0] if cfg.benchmarks else "SPY", seed=cfg.seed)
    parts = {}
    for pname, s in c["partitions"].items():
        m = s.get("matched") or {}
        t = next((x for x in c["tests"] if x["partition"] == pname), {})
        parts[pname] = dict(role=s["role"], n=s.get("n"), mean_ret=s.get("mean_ret"), win_rate=s.get("win_rate"),
                            profit_factor=s.get("profit_factor"), expectancy_r=s.get("expectancy_r"),
                            avg_bars=s.get("avg_bars"), excess_matched=m.get("excess"), ci=m.get("ci"),
                            p=m.get("p"), q=t.get("q"), verdict=(t.get("verdict") or {}).get("category"),
                            excess_random_dates=(s.get("random_dates") or {}).get("excess"),
                            excess_bench=s.get("excess_bench"), consistency_years=s.get("consistency_years"))
    portfolio = {k: dict(role=v.get("role"), metrics=v.get("metrics"), random_entry=v.get("random_entry"))
                 for k, v in c.get("portfolio", {}).items()}
    wf = (st.read_json(eid, "walk_forward.json") or {}).get("walk_forward", {})
    wf_rows = {g: v for g, v in (wf.get("groups") or {}).items() if cell in (v.get("cells") or [])}
    research = dict(experiment_id=eid, experiment_name=meta["name"], cell=cell, entry_key=c["entry"]["key"],
                    exit_key=c["exit"]["key"], entry_name=c["entry"]["name"], exit_name=c["exit"]["name"],
                    trigger=c["exit"]["trigger"], dataset=cfg.dataset, dataset_version=cfg.dataset_version,
                    universe_name=meta["universe_name"], n_symbols=len(cfg.universe or []),
                    start=cfg.start.isoformat(), end=cfg.analysis_end.isoformat() if cfg.analysis_end else None,
                    config_hash=cfg.config_hash(), methodology_hash=cfg.methodology_hash(), seed=cfg.seed,
                    execution=cfg.execution.model_dump(mode="json"), counts=c.get("counts"), trade_level=parts,
                    portfolio=portfolio, vs_baseline=c.get("vs_baseline"), walk_forward=wf_rows)
    validation = dict(partitions=[dict(name=p.name, role=p.role, start=p.start.isoformat(), end=p.end.isoformat())
                                  for p in cfg.partitions], test_evaluated=cfg.evaluate_test,
                      note="Validation and test rows are chronological hold-outs of the source experiment; the "
                           "test partition stays locked unless the experiment spent it (recorded in ee_test_ledger).")
    sid = strategy_id_for(c["entry"]["id"], c["exit"]["key"], eid, cell)
    return dict(strategy_id=sid, version="1.0",
                name=name or f"{c['entry']['name']} -> {c['exit']['name']}", definition=d.model_dump(mode="json"),
                research=research, validation=validation, universe=cfg.universe, warning=WARNING,
                created_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def import_cell(lab, eid: str, cell: str, name: str = "") -> dict:
    """Idempotent per (experiment, cell)."""
    with lab._import_lock:
        existing = [i for i in lab.entry_exit.imports() if i["experiment_id"] == eid and i["cell"] == cell]
        if existing:
            i = existing[0]
            return dict(strategy_id=i["strategy_id"], version=i["version"], created=False,
                        module_file=i["module_file"])
        rec = build_record(lab, eid, cell, name)
        path = write_module(rec, lab.settings_ee_imports_dir)
        try:
            lab.registry.get(rec["strategy_id"], rec["version"])
        except KeyError:
            load_module(path, lab.registry)
        lab.entry_exit.record_import(rec, path)
        return dict(strategy_id=rec["strategy_id"], version=rec["version"], created=True, module_file=str(path),
                    record=json.loads(json.dumps(rec)))


__all__ = ["build_record", "import_cell", "strategy_id_for"]
