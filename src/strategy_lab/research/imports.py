"""Import a researched signal into the Strategy Library: build the import record from a finished experiment, write
the generated strategy module (write-once) and register it with the live strategy registry."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from strategy_lab.research.engine import ResearchError
from strategy_lab.strategies.signal_rule import WARNING, load_module, strategy_id_for, write_module


def build_record(lab, eid: str, key: str, name: str = "", defaults: dict | None = None) -> dict:
    st = lab.research
    meta = st.get(eid)
    if meta is None:
        raise KeyError(f"no signal experiment {eid}")
    if meta["status"] not in ("done", "cancelled"):
        raise ResearchError(f"{eid} is {meta['status']}: import from a finished experiment")
    cfg = st.config(eid)
    summ = st.signal_summary(eid, key)
    sig = summ["signal"]
    tests = [t for t in (st.read_json(eid, "tests.json") or {}).get("tests", []) if t["key"] == key]
    roles = {p.name: p.role for p in cfg.partitions}
    results = []
    for t in tests:
        hz = summ["partitions"][t["partition"]]["horizons"][str(t["horizon"])]
        results.append(dict(partition=t["partition"], role=roles.get(t["partition"], "combined"), horizon=t["horizon"],
                            n=t["n"], mean=hz.get("mean"), excess_matched=t["excess"], ci=t["ci"],
                            p_matched=t["p_matched"], q_matched=t["q_matched"],
                            excess_random_date=(hz.get("random_date") or {}).get("excess"),
                            verdict=t["verdict"]["category"]))
    locked = [x for x in st.locked(eid) if x["signal_key"] == key]
    research = dict(experiment_id=eid, experiment_name=meta["name"], dataset=cfg.dataset,
                    dataset_version=cfg.dataset_version, universe_name=meta["universe_name"],
                    n_symbols=len(cfg.universe), start=cfg.start.isoformat(), end=cfg.analysis_end.isoformat(),
                    methodology_hash=cfg.methodology_hash(), horizons=cfg.horizons,
                    primary_horizon=cfg.primary_horizon, entry=cfg.entry, sampling=cfg.sampling.model_dump(),
                    matching=cfg.matching.model_dump(), permutations=cfg.permutations, seed=cfg.seed,
                    counts=summ["counts"], results=results)
    validation = dict(partitions=[dict(name=p.name, role=p.role, start=p.start.isoformat(), end=p.end.isoformat())
                                  for p in cfg.partitions],
                      test_evaluated=cfg.evaluate_test,
                      test_evaluations=[dict(first_use=bool(x["first_use"]), evaluations=x["evaluations"],
                                             locked_experiment=x["locked_experiment"]) for x in locked],
                      note="Validation and test rows are the chronological hold-outs of the source experiment; "
                           "a test row marked first_use=false is a re-evaluation (the locked result is the first).")
    d = dict(hold_bars=cfg.primary_horizon, max_positions=10, position_size=0.1)
    d.update({k: v for k, v in (defaults or {}).items() if k in ("hold_bars", "max_positions", "position_size")})
    sid = strategy_id_for(sig["id"], eid, key)
    return dict(strategy_id=sid, version="1.0", name=name or f"{sig['name']} (researched signal)",
                signal=dict(key=sig["key"], kind=sig["kind"], id=sig["id"], version=sig["version"],
                            family=sig["family"], name=sig["name"], direction=sig["direction"], params=sig["params"],
                            spec=sig["spec"], source_sha256=sig["source_sha256"], trigger=sig["trigger"],
                            fields=sig["fields"], warmup=sig["warmup"]),
                research=research, validation=validation, defaults=d,
                benchmark=cfg.benchmarks[0] if cfg.benchmarks else "SPY", warning=WARNING,
                created_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))


def import_signal(lab, eid: str, key: str, name: str = "", defaults: dict | None = None) -> dict:
    """Idempotent per (experiment, signal): a second import returns the existing strategy."""
    with lab._import_lock:
        existing = [i for i in lab.research.imports() if i["experiment_id"] == eid and i["signal_key"] == key]
        if existing:
            i = existing[0]
            return dict(strategy_id=i["strategy_id"], version=i["version"], created=False,
                        module_file=i["module_file"])
        rec = build_record(lab, eid, key, name, defaults)
        path = write_module(rec, lab.settings_signal_imports_dir)
        try:
            lab.registry.get(rec["strategy_id"], rec["version"])
            have = True
        except KeyError:
            have = False
        if not have:
            load_module(path, lab.registry)
        lab.research.record_import(rec, path)
        return dict(strategy_id=rec["strategy_id"], version=rec["version"], created=True, module_file=str(path),
                    record=json.loads(json.dumps(rec)))


__all__ = ["build_record", "import_signal", "Path"]
