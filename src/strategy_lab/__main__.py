"""Command line: python -m strategy_lab <command> (scripts/sl.sh runs it with src/ on the path).

  serve [--host 127.0.0.1] [--port 8070]      API + web UI
  ingest DATASET                              build or reuse the dataset's cached version
  backtest CONFIG.json [--wait]               create a run from an ExperimentConfig file and execute it
  reproduce RUN_ID                            re-run a finished experiment and compare artifact hashes
  experiments                                 list runs
  forward-advance --to YYYY-MM-DD             advance the MOCK paper host and sync its deployments
  check-contract [--source DIR]               re-hash ptrade's contract sources (read-only) against the mirror
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m strategy_lab")
    ap.add_argument("--settings", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8070)
    sub.add_parser("ingest").add_argument("dataset")
    sub.add_parser("backtest").add_argument("config")
    sub.add_parser("reproduce").add_argument("run_id")
    sub.add_parser("experiments")
    sub.add_parser("forward-advance").add_argument("--to", required=True)
    c = sub.add_parser("check-contract")
    c.add_argument("--source", default="C:/AI/Workspace/trading/paper_trading")
    a = ap.parse_args(argv)

    if a.cmd == "check-contract":
        from strategy_lab.paper.contract import SOURCE_COMMIT, SOURCE_SHA256
        root, drift = Path(a.source), []
        for rel, sha in SOURCE_SHA256.items():
            p = root / rel
            got = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "missing"
            if got != sha:
                drift.append(dict(file=rel, expected=sha, found=got))
        print(json.dumps(dict(source=str(root), mirrored_commit=SOURCE_COMMIT, drift=drift), indent=1))
        return 1 if drift else 0

    from strategy_lab.lab import default_lab
    lab = default_lab(a.settings)
    try:
        if a.cmd == "serve":
            import uvicorn
            from strategy_lab.api.app import create_app
            uvicorn.run(create_app(lab), host=a.host, port=a.port, log_level="info")
        elif a.cmd == "ingest":
            print(json.dumps(lab.store.ingest(a.dataset).meta, indent=1, default=str))
        elif a.cmd == "backtest":
            from strategy_lab.experiments.config import ExperimentConfig
            cfg = ExperimentConfig.model_validate_json(Path(a.config).read_text(encoding="utf-8"))
            run_id = lab.experiments.create(cfg)
            lab.experiments.execute(run_id)
            meta = lab.experiments.get(run_id)
            print(json.dumps(dict(run_id=run_id, status=meta["status"], summary=meta["summary"]), indent=1))
        elif a.cmd == "reproduce":
            print(json.dumps(lab.experiments.reproduce(a.run_id), indent=1))
        elif a.cmd == "experiments":
            for r in lab.experiments.list():
                print(f"{r['run_id']}  {r['status']:8}  {r['strategy']:<22} {r['start']}..{r['end']}  "
                      f"{(r['summary'] or {}).get('total_return')}")
        elif a.cmd == "forward-advance":
            out = lab.forward.advance(to=a.to)
            print(json.dumps(dict(clock=out.get("clock"), sessions=len(out["sessions"]), synced=out["synced"]),
                             indent=1, default=str))
    finally:
        lab.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
