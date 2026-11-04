"""Strategy Lab API (FastAPI, /api/v1) and the web UI (web/dist). Binds to 127.0.0.1 by default.

Writes: launch experiments and reproductions, save/delete configurations, ingest a dataset, deploy to the paper host,
advance/pause/resume the MOCK host, sync and reconcile deployments. Nothing here can place an order on the
operational paper system: the ptrade target only exports a bundle and reads API v1.
"""
from __future__ import annotations

import datetime as dt
import inspect
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError

import strategy_lab
from strategy_lab.analytics import metrics as mt
from strategy_lab.core.strategy import ParamError
from strategy_lab.data.policy import PERIODS, PolicyError, period_labels
from strategy_lab.data.store import DataError
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.runner import StrategyError
from strategy_lab.experiments.config import ExperimentConfig
from strategy_lab.lab import Lab
from strategy_lab.paper.client import PtradeApiError
from strategy_lab.paper.mock_host import HostError
from strategy_lab.paper.service import DeployError
from strategy_lab.paper.spec import DeploymentSpec
from strategy_lab.strategies.registry import StrategyNotFound

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def clean(o: Any) -> Any:
    """JSON-safe: NaN/inf -> None, numpy/pandas scalars -> Python, timestamps -> ISO strings."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return None if pd.isna(o) else (o.date().isoformat() if o == o.normalize() else o.isoformat())
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if o is pd.NaT:
        return None
    if isinstance(o, BaseModel):
        return clean(o.model_dump(mode="json"))
    return o


class J(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(clean(content), allow_nan=False, separators=(",", ":")).encode("utf-8")


def records(df: pd.DataFrame) -> list[dict]:
    return clean(df.to_dict("records")) if df is not None and len(df) else []


class StatusChange(BaseModel):
    status: str = Field(pattern="^(active|paused|retired)$")
    reason: str = Field(min_length=1, max_length=500)


class DeployRequest(BaseModel):
    spec: DeploymentSpec | None = None
    from_run_id: str | None = None
    start_date: dt.date | None = None
    name: str = ""
    target: str = "mock"


class AdvanceRequest(BaseModel):
    to: dt.date | None = None
    sessions: int | None = Field(None, ge=1, le=2000)


class SavedConfig(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: ExperimentConfig


def create_app(lab: Lab | None = None) -> FastAPI:
    lab = lab or Lab()
    lab.experiments.recover_interrupted()
    app = FastAPI(title="Strategy Lab API", version=strategy_lab.__version__, default_response_class=J,
                  description="Historical backtesting and forward paper testing (dev workspace, data <= 2025-12-31).")
    app.state.lab = lab
    api = APIRouter(prefix="/api/v1")

    async def _errors(request: Request, exc: Exception):
        """Domain errors -> 4xx/502 with the message. Registered per class: Starlette's catch-all handler would
        re-raise after responding. Anything else is a genuine 500."""
        status, detail = 500, f"{type(exc).__name__}: {exc}"
        if isinstance(exc, DeployError):
            return J({"detail": str(exc), "problems": exc.problems}, status_code=422)
        if isinstance(exc, (DataError, PolicyError, ParamError, StrategyError, ValidationError, ValueError,
                            HostError)):
            status = 422
        elif isinstance(exc, (KeyError, StrategyNotFound, FileNotFoundError)):
            status, detail = 404, str(exc).strip("'\"")
        elif isinstance(exc, PtradeApiError):
            status = 502
        return J({"detail": detail}, status_code=status)

    for cls in (DeployError, ValueError, ValidationError, StrategyError, HostError, KeyError, FileNotFoundError,
                PtradeApiError):
        app.add_exception_handler(cls, _errors)

    # -- status -------------------------------------------------------------------------------------------------
    @api.get("/health")
    def health():
        return dict(ok=True, version=strategy_lab.__version__, now_utc=dt.datetime.now(dt.timezone.utc).isoformat(),
                    policy=lab.policy.to_dict(), strategies=len(lab.registry.all()),
                    strategy_errors=lab.registry.errors, workspace="ClaudeWorkspace (dev, EXPLORATORY)",
                    forward=dict(mock_dataset=lab.forward.mock_dataset, ptrade_api_enabled=bool(lab.forward.ptrade_url)))

    @api.get("/settings")
    def settings():
        s = lab.settings
        return dict(policy=lab.policy.to_dict(), settings_file=str(s.path),
                    datasets=[dict(id=d.id, name=d.name, provider=d.provider, sources=[str(p) for p in d.paths],
                                   description=d.description) for d in lab.store.specs.values()],
                    universes=lab.universes.names(), execution_defaults=ExecutionConfig().model_dump(),
                    execution_schema=ExecutionConfig.model_json_schema(),
                    paths=dict(cache=str(s.cache_dir), experiments=str(s.experiments_dir), catalog=str(s.catalog_path),
                               forward=str(s.forward_dir), user_strategies=str(s.user_strategies_dir)),
                    forward=dict(host="mock", mock_dataset=lab.forward.mock_dataset,
                                 ptrade_api_url=lab.forward.ptrade_url or None),
                    research_periods=[dict(name=n, start=a.isoformat(), end=b.isoformat()) for n, a, b in PERIODS])

    # -- strategies -----------------------------------------------------------------------------------------------
    @api.get("/strategies")
    def strategies():
        return dict(strategies=lab.registry.describe(), errors=lab.registry.errors)

    @api.get("/strategies/{strategy_id}")
    def strategy(strategy_id: str, version: str | None = None):
        cls = lab.registry.get(strategy_id, version)
        d = cls.describe()
        d["versions"] = lab.registry.versions(strategy_id)
        d["source"] = lab.registry.sources[(cls.id, cls.version)]
        d["source_file"] = inspect.getfile(cls)
        d["code"] = inspect.getsource(cls)
        d["warmup_default"] = cls().warmup()
        return d

    # -- data -----------------------------------------------------------------------------------------------------
    @api.get("/data/datasets")
    def datasets():
        out = []
        for spec in lab.store.specs.values():
            versions = lab.store.cached_versions(spec.id)
            cur = lab.store._versions.get(spec.id)
            meta = cur.meta if cur else (versions[-1] if versions else None)
            out.append(dict(id=spec.id, name=spec.name, provider=spec.provider, sources=[str(p) for p in spec.paths],
                            description=spec.description, cached=bool(versions), current=meta,
                            versions=[dict(version=v["version"], ingested_at_utc=v["ingested_at_utc"],
                                           n_symbols=v["n_symbols"], n_rows=v["n_rows"]) for v in versions]))
        return out

    @api.post("/data/datasets/{dataset_id}/ingest")
    def ingest(dataset_id: str):
        return lab.store.ingest(dataset_id).meta

    @api.get("/data/datasets/{dataset_id}")
    def dataset(dataset_id: str):
        v = lab.store.current(dataset_id)
        cov = lab.store.coverage(dataset_id)
        cal = lab.store.calendar(dataset_id)
        years = pd.Series(1, index=cal).groupby(cal.year).sum()
        return dict(meta=v.meta, sessions_by_year={int(k): int(n) for k, n in years.items()},
                    summary=dict(symbols=int(len(cov)), with_gaps=int((cov["missing"] > 0).sum()),
                                 missing_bars=int(cov["missing"].sum()), invalid_bars=int(cov["invalid_bars"].sum()),
                                 late_start=int((cov["first_date"] > v.meta["first_date"]).sum()),
                                 early_end=int((cov["last_date"] < v.meta["last_date"]).sum())),
                    periods=period_labels(v.meta["first_date"], v.meta["last_date"]))

    @api.get("/data/datasets/{dataset_id}/coverage")
    def coverage(dataset_id: str, q: str | None = None, only_gaps: bool = False, limit: int = Query(5000, le=20000)):
        cov = lab.store.coverage(dataset_id)
        if q:
            cov = cov[cov["symbol"].str.contains(q.upper(), regex=False)]
        if only_gaps:
            cov = cov[(cov["missing"] > 0) | (cov["invalid_bars"] > 0)]
        return dict(rows=records(cov.head(limit)), total=int(len(cov)))

    @api.get("/data/datasets/{dataset_id}/bars/{symbol}")
    def bars(dataset_id: str, symbol: str, start: str | None = None, end: str | None = None,
             price_mode: str = "adjusted"):
        v = lab.store.current(dataset_id)
        md, report = lab.store.load(dataset_id, [symbol], start or v.meta["first_date"], end or v.meta["last_date"],
                                    price_mode=price_mode)
        df = pd.DataFrame({k: f[symbol] for k, f in md.fields.items()})
        df.index.name = "date"
        acts = md.actions[md.actions["symbol"] == symbol] if len(md.actions) else md.actions
        return dict(symbol=symbol, price_mode=price_mode, rows=records(df.reset_index()), actions=records(acts),
                    report=report)

    @api.get("/data/universes")
    def universes(dataset: str | None = None):
        out = []
        for name in ["all", *lab.universes.names()]:
            item = dict(name=name)
            if dataset:
                try:
                    item["symbols"] = lab.universes.resolve(name, lab.store, dataset)
                except DataError as e:
                    item["error"] = str(e)
            out.append(item)
        return out

    # -- experiments ----------------------------------------------------------------------------------------------
    @api.post("/experiments")
    def launch(cfg: ExperimentConfig):
        run_id = lab.experiments.create(cfg)
        lab.jobs.submit(run_id)
        return dict(run_id=run_id, status="queued")

    @api.post("/experiments/batch")
    def launch_batch(cfgs: list[ExperimentConfig] = Body(..., max_length=50)):
        ids = [lab.experiments.create(c) for c in cfgs]          # validate all before starting any
        for i in ids:
            lab.jobs.submit(i)
        return dict(run_ids=ids)

    @api.post("/experiments/validate")
    def validate(cfg: ExperimentConfig):
        resolved, cls = lab.resolve(cfg)
        return dict(ok=True, resolved=resolved.model_dump(mode="json"), periods=period_labels(cfg.start, cfg.end),
                    warmup=cls(resolved.params).warmup(), n_symbols=len(resolved.universe))

    @api.get("/experiments")
    def experiments(limit: int = Query(500, le=5000)):
        return lab.experiments.list(limit)

    def _run(run_id: str) -> dict:
        meta = lab.experiments.get(run_id)
        if meta is None:
            raise KeyError(f"no experiment {run_id}")
        return meta

    @api.get("/experiments/{run_id}")
    def experiment(run_id: str):
        meta = _run(run_id)
        st = lab.experiments
        return dict(run=meta, config=st.read_json(run_id, "config.json"), manifest=st.read_json(run_id, "manifest.json"),
                    metrics=st.read_json(run_id, "metrics.json"), data_report=st.read_json(run_id, "data_report.json"),
                    state=st.read_json(run_id, "state.json"),
                    error=(st.path(run_id) / "error.txt").read_text(encoding="utf-8")
                    if (st.path(run_id) / "error.txt").exists() else None)

    @api.get("/experiments/{run_id}/equity")
    def equity(run_id: str):
        _run(run_id)
        return records(lab.experiments.read_csv(run_id, "equity.csv"))

    @api.get("/experiments/{run_id}/table/{name}")
    def table(run_id: str, name: str, symbol: str | None = None, date: str | None = None,
              limit: int = Query(20000, le=200000)):
        if name not in ("fills", "orders", "events", "trades", "open_lots", "holdings", "monthly"):
            raise HTTPException(404, f"no table {name}")
        _run(run_id)
        df = lab.experiments.read_csv(run_id, f"{name}.csv")
        if symbol and "symbol" in df:
            df = df[df["symbol"] == symbol]
        if date:
            col = "timestamp" if "timestamp" in df else "created_at" if "created_at" in df else None
            if col:
                df = df[df[col] == date]
        return dict(rows=records(df.head(limit)), total=int(len(df)))

    @api.get("/experiments/{run_id}/rolling")
    def rolling(run_id: str, window: int = Query(63, ge=5, le=756)):
        _run(run_id)
        eq = lab.experiments.read_csv(run_id, "equity.csv")
        cfg = lab.experiments.config(run_id)
        from strategy_lab.data.store import FREQUENCIES
        s = pd.Series(eq["equity"].to_numpy(), index=pd.DatetimeIndex(eq["date"]))
        r = mt.rolling(s, window, FREQUENCIES[cfg.frequency]).reset_index(names="date")
        out = dict(window=window, rows=records(r))
        if eq["benchmark"].notna().any():
            b = pd.Series(eq["benchmark"].to_numpy(), index=pd.DatetimeIndex(eq["date"]))
            out["benchmark"] = records(mt.rolling(b, window, FREQUENCIES[cfg.frequency]).reset_index(names="date"))
        return out

    @api.get("/experiments/{run_id}/logs")
    def logs(run_id: str, limit: int = Query(2000, le=20000)):
        _run(run_id)
        p = lab.experiments.path(run_id) / "logs.jsonl"
        if not p.exists():
            return []
        lines = p.read_text(encoding="utf-8").splitlines()
        return [json.loads(x) for x in lines[:limit]]

    @api.post("/experiments/{run_id}/reproduce")
    def reproduce(run_id: str):
        meta = _run(run_id)
        if meta["status"] != "done":
            raise HTTPException(422, f"{run_id} is {meta['status']}; only finished runs can be reproduced")
        lab.jobs.pool.submit(_safe_reproduce, lab, run_id)
        return dict(run_id=run_id, status="submitted")

    @api.get("/compare")
    def compare(ids: str):
        run_ids = [x for x in ids.split(",") if x][:12]
        if len(run_ids) < 1:
            raise HTTPException(422, "give run ids")
        series, rows, rets = {}, [], {}
        for rid in run_ids:
            meta = _run(rid)
            if meta["status"] != "done":
                raise HTTPException(422, f"{rid} is {meta['status']}")
            eq = lab.experiments.read_csv(rid, "equity.csv")
            s = pd.Series(eq["equity"].to_numpy(), index=pd.DatetimeIndex(eq["date"]))
            series[rid] = s
            rows.append(dict(run_id=rid, name=meta["name"] or meta["strategy"], strategy=meta["strategy"],
                             version=meta["strategy_version"], start=meta["start"], end=meta["end"],
                             periods=meta["periods"], dataset=f"{meta['dataset']}@{meta['dataset_version']}",
                             metrics=lab.experiments.read_json(rid, "metrics.json")))
        lo = max(s.index[0] for s in series.values())
        hi = min(s.index[-1] for s in series.values())
        overlap = []
        if lo <= hi:
            for rid, s in series.items():
                w = s.loc[lo:hi]
                overlap.append(dict(run_id=rid, points=[[d.date().isoformat(), float(v / w.iloc[0])]
                                                         for d, v in w.items()],
                                    drawdown=[[d.date().isoformat(), float(v)] for d, v in mt.drawdown(w).items()]))
                rets[rid] = w.pct_change().iloc[1:]
                start = w.index[0] - pd.Timedelta(days=1)
                curve = pd.concat([pd.Series([w.iloc[0]], index=[start]), w.iloc[1:]]) if len(w) > 1 else w
                next(r for r in rows if r["run_id"] == rid)["overlap_metrics"] = mt.compute(mt.Inputs(curve))
        corr = pd.DataFrame(rets).corr() if len(rets) > 1 else pd.DataFrame()
        return dict(runs=rows, overlap=dict(start=lo.date().isoformat() if lo <= hi else None,
                                            end=hi.date().isoformat() if lo <= hi else None, series=overlap,
                                            periods=period_labels(lo, hi) if lo <= hi else []),
                    correlation=clean(corr.to_dict()) if len(corr) else {},
                    note="Overlap metrics use only the common window, rebased to 1.0; results are EXPLORATORY.")

    @api.get("/configs")
    def configs():
        return lab.experiments.configs()

    @api.post("/configs")
    def save_config(body: SavedConfig):
        lab.resolve(body.config)
        return dict(config_id=lab.experiments.save_config(body.name, body.config))

    @api.delete("/configs/{config_id}")
    def delete_config(config_id: str):
        if not lab.experiments.delete_config(config_id):
            raise HTTPException(404, f"no saved configuration {config_id}")
        return dict(deleted=config_id)

    # -- forward ----------------------------------------------------------------------------------------------------
    @api.get("/forward/host")
    def host():
        return lab.forward.host_state()

    @api.post("/forward/host/advance")
    def advance(body: AdvanceRequest):
        return lab.forward.advance(to=body.to, sessions=body.sessions)

    @api.post("/forward/validate")
    def validate_deploy(spec: DeploymentSpec):
        return lab.forward.validate(spec)

    @api.get("/forward/deployments")
    def deployments():
        out = []
        for d in lab.forward.list():
            snap = lab.forward.snapshot(d["deployment_id"])
            out.append(dict(deployment=d, equity_value=snap["equity_value"], cash=snap["cash"],
                            current_drawdown=snap["current_drawdown"], n_positions=len(snap["positions"]),
                            since_start=(snap["equity_value"] / snap["capital"] - 1) if snap["equity_value"] else None,
                            sparkline=[[e["date"], e["equity"]] for e in snap["equity"][-60:]],
                            label=snap["label"]))
        return out

    @api.post("/forward/deployments")
    def deploy(body: DeployRequest):
        if body.spec is None and not (body.from_run_id and body.start_date):
            raise HTTPException(422, "give a spec, or from_run_id and start_date")
        spec = body.spec or lab.forward.spec_from_experiment(body.from_run_id, body.start_date, body.name)
        return lab.forward.deploy(spec, target=body.target, source_run_id=body.from_run_id)

    @api.get("/forward/deployments/{deployment_id}")
    def deployment(deployment_id: str):
        return lab.forward.snapshot(deployment_id)

    @api.post("/forward/deployments/{deployment_id}/sync")
    def sync(deployment_id: str):
        return lab.forward.sync(deployment_id)

    @api.post("/forward/deployments/{deployment_id}/reconcile")
    def reconcile(deployment_id: str):
        return lab.forward.reconcile(deployment_id)

    @api.post("/forward/deployments/{deployment_id}/status")
    def status(deployment_id: str, body: StatusChange):
        return lab.forward.set_status(deployment_id, body.status, body.reason)

    @api.get("/forward/deployments/{deployment_id}/bundle")
    def bundle(deployment_id: str):
        d = lab.forward.get(deployment_id)
        b = Path(d["bundle_dir"])
        return dict(manifest=json.loads((b / "MANIFEST.json").read_text(encoding="utf-8")),
                    operator=(b / "OPERATOR.md").read_text(encoding="utf-8"),
                    books_entry=(b / "config" / "books.entry.toml").read_text(encoding="utf-8"),
                    protocol=(b / "docs" / "protocols" / f"{d['book_id']}.md").read_text(encoding="utf-8"))

    app.include_router(api)

    # -- the mock host's read API (API v1 shapes, for HTTP-level checks) --------------------------------------------
    mock = APIRouter(prefix="/mock-ptrade/api/v1", tags=["mock ptrade (SIMULATED)"])

    @mock.get("/books")
    def m_books():
        return lab.forward.host.api_books()

    @mock.get("/books/{book_id}")
    def m_book(book_id: str):
        return lab.forward.host.api_book(book_id)

    @mock.get("/books/{book_id}/equity")
    def m_equity(book_id: str, start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to")):
        return lab.forward.host.api_equity(book_id, start, end)

    @mock.get("/books/{book_id}/holdings")
    def m_holdings(book_id: str, asof: str | None = None):
        return lab.forward.host.api_holdings(book_id, asof)

    @mock.get("/books/{book_id}/trades")
    def m_trades(book_id: str, start: str | None = Query(None, alias="from"), end: str | None = Query(None, alias="to"),
                 limit: int = Query(2000, ge=1, le=5000)):
        return lab.forward.host.api_trades(book_id, start, end, limit)

    @mock.get("/books/{book_id}/picks")
    def m_picks(book_id: str, asof: str | None = None):
        return lab.forward.host.api_picks(book_id, asof)

    app.include_router(mock)

    # -- the web UI -----------------------------------------------------------------------------------------------
    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith(("api/", "mock-ptrade/")):
            raise HTTPException(404, f"no route /{path}")
        if not WEB_DIST.exists():
            return Response("Strategy Lab API is running; the web UI is not built (web/dist). See README.md.",
                            media_type="text/plain")
        f = (WEB_DIST / path).resolve()
        if path and f.is_file() and WEB_DIST.resolve() in f.parents:
            return FileResponse(f)
        return FileResponse(WEB_DIST / "index.html")

    return app


def _safe_reproduce(lab: Lab, run_id: str):
    try:
        return lab.experiments.reproduce(run_id)
    except Exception:  # noqa: BLE001 - the new run records its own failure
        return None
