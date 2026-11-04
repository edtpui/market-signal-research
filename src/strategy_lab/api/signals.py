"""Signal Research Lab routes (/api/v1/signals/...): the signal catalog, signal inspection, event-study experiments
(validate, launch, cancel, reproduce, results), rankings across experiments, the research-integrity ledgers, saved
configurations and imports into the Strategy Library."""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Union

import pandas as pd
from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from strategy_lab.data.policy import period_labels
from strategy_lab.research import stats as st
from strategy_lab.research.config import DEFAULT_PARTITIONS, SignalExperimentConfig
from strategy_lab.research.engine import inspect_signal, resolution_note
from strategy_lab.signals.base import CombinationSpec, SignalSpec


class InspectRequest(BaseModel):
    spec: Annotated[Union[SignalSpec, CombinationSpec], Field(discriminator="kind")]
    dataset: str
    symbol: str
    start: dt.date | None = None
    end: dt.date | None = None
    universe: list[str] | None = None
    universe_name: str | None = None
    benchmark: str = "SPY"


class SavedSignalConfig(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: SignalExperimentConfig


class ImportRequest(BaseModel):
    experiment_id: str
    signal_key: str
    name: str = Field("", max_length=200)
    defaults: dict[str, Any] = Field(default_factory=dict)


def build_router(lab) -> APIRouter:
    from strategy_lab.api.app import clean, records

    r = APIRouter(prefix="/api/v1/signals", tags=["signal research"])
    R = lab.research

    def _exp(eid: str) -> dict:
        meta = R.get(eid)
        if meta is None:
            raise KeyError(f"no signal experiment {eid}")
        return meta

    # -- catalog ------------------------------------------------------------------------------------------------
    @r.get("/catalog")
    def catalog():
        return lab.signals.describe()

    @r.get("/catalog/{signal_id}")
    def signal(signal_id: str, version: str | None = None):
        d = lab.signals.get(signal_id, version)
        out = d.describe()
        out["code"] = d.source()
        out["family_info"] = lab.signals.families[d.family].__dict__
        out["versions"] = [v for (i, v) in lab.signals._order if i == signal_id]
        out["results"] = records(pd.DataFrame(R.results(signal_id=signal_id, limit=500)))
        return out

    @r.post("/inspect")
    def inspect(body: InspectRequest):
        v = lab.store.current(body.dataset)
        last = pd.Timestamp(v.meta["last_date"]).date()
        end = body.end or last
        start = body.start or max(lab.policy.backtest_floor, end - dt.timedelta(days=365 * 2))
        universe = body.universe or (lab.universes.resolve(body.universe_name, lab.store, body.dataset)
                                     if body.universe_name else [body.symbol])
        return inspect_signal(lab.store, lab.signals, body.spec, body.dataset, body.symbol, universe, start, end,
                              body.benchmark)

    @r.get("/options")
    def options():
        defaults = SignalExperimentConfig(dataset="x", universe=["A", "B"], start=lab.policy.backtest_floor,
                                          end=lab.policy.ceiling, families=["rsi"])
        dd = defaults.model_dump(mode="json")
        for k in ("dataset", "universe", "families", "start", "end", "resolved_signals", "analysis_end"):
            dd.pop(k, None)
        return dict(defaults=dd, reference=lab.reference.availability(), policy=lab.policy.to_dict(),
                    default_partitions=[dict(name=n, role=ro, start=a.isoformat(), end=b.isoformat())
                                        for n, ro, a, b in DEFAULT_PARTITIONS],
                    verdicts=list(st.VERDICTS), schema=SignalExperimentConfig.model_json_schema())

    # -- experiments ----------------------------------------------------------------------------------------------
    @r.post("/experiments/validate")
    def validate(cfg: SignalExperimentConfig):
        resolved, signals = R.resolve(cfg)
        m = len(signals) * len(resolved.horizons)
        notes = []
        rn = resolution_note(resolved, m)
        if rn:
            notes.append(rn)
        mkt = [s.key for s in signals if s.scope == "market"]
        if mkt:
            notes.append(f"{len(mkt)} market-scope signal(s) fire for every symbol on the same session: the matched-"
                         "stock comparison cannot test them; read the random-date and benchmark comparisons.")
        if not resolved.evaluate_test and any(p[1] == "test" for p in DEFAULT_PARTITIONS) and \
                resolved.analysis_end < cfg.end:
            notes.append(f"the test partition after {resolved.analysis_end} is locked and was not loaded")
        if resolved.evaluate_test:
            notes.append("evaluate_test spends the test partition: every signal's evaluation is recorded in the "
                         "append-only test ledger, and the first result per signal and methodology is locked")
        return dict(ok=True, n_signals=len(signals), n_tests=m, n_symbols=len(resolved.universe),
                    signals=[dict(key=s.key, name=s.name, family=s.family, direction=s.direction, scope=s.scope,
                                  warmup=s.warmup) for s in signals],
                    partitions=[p.model_dump(mode="json") for p in resolved.partitions],
                    analysis_end=resolved.analysis_end.isoformat(), primary_horizon=resolved.primary_horizon,
                    dataset_version=resolved.dataset_version,
                    periods=period_labels(resolved.start, resolved.analysis_end), notes=notes)

    @r.post("/experiments")
    def launch(cfg: SignalExperimentConfig):
        eid = R.create(cfg)
        R.submit(eid)
        return dict(experiment_id=eid, status="queued")

    @r.get("/experiments")
    def experiments(limit: int = Query(500, le=5000)):
        return R.list(limit)

    @r.get("/experiments/{eid}")
    def experiment(eid: str):
        meta = _exp(eid)
        d = R.path(eid)
        tests = R.read_json(eid, "tests.json")
        return dict(experiment=meta, config=R.read_json(eid, "config.json"), study=R.read_json(eid, "study.json"),
                    manifest=R.read_json(eid, "manifest.json"), runs=R.runs(eid),
                    resolution_note=(tests or {}).get("resolution_note"), partial=(tests or {}).get("partial"),
                    prior_usage=R.prior_usage(eid), locked=R.locked(eid),
                    error=(d / "error.txt").read_text(encoding="utf-8") if (d / "error.txt").exists() else None)

    @r.get("/experiments/{eid}/results")
    def results(eid: str, partition: str | None = None, horizon: int | None = None):
        _exp(eid)
        return records(pd.DataFrame(R.results(eid, partition=partition, horizon=horizon, limit=50000)))

    @r.get("/experiments/{eid}/signals/{key}")
    def signal_result(eid: str, key: str):
        _exp(eid)
        tests = [t for t in (R.read_json(eid, "tests.json") or {}).get("tests", []) if t["key"] == key]
        run = next((x for x in R.runs(eid) if x["signal_key"] == key), None)
        if run is None:
            raise KeyError(f"no signal {key} in {eid}")
        summary = R.signal_summary(eid, key) if run["status"] == "done" else None
        err = None
        p = R.path(eid) / "signals" / run["dir"] / "error.txt"
        if p.exists():
            err = p.read_text(encoding="utf-8")
        return dict(run=run, summary=summary, tests=tests, error=err,
                    locked=[x for x in R.locked(eid) if x["signal_key"] == key])

    @r.get("/experiments/{eid}/signals/{key}/events")
    def events(eid: str, key: str, limit: int = Query(5000, ge=1, le=200000), symbol: str | None = None):
        _exp(eid)
        df = R.signal_events(eid, key, limit=limit, symbol=symbol)
        return dict(rows=records(df), columns=list(df.columns))

    @r.get("/experiments/{eid}/signals/{key}/nulls")
    def nulls(eid: str, key: str):
        _exp(eid)
        df = R.signal_nulls(eid, key)
        return dict(columns={c: clean(df[c].tolist()) for c in df.columns})

    @r.post("/experiments/{eid}/cancel")
    def cancel(eid: str):
        return R.cancel(eid)

    @r.post("/experiments/{eid}/reproduce")
    def reproduce(eid: str):
        meta = _exp(eid)
        if meta["status"] != "done":
            raise HTTPException(422, f"{eid} is {meta['status']}; only finished experiments can be reproduced")
        R.pool.submit(_safe, R.reproduce, eid)
        return dict(experiment_id=eid, status="submitted")

    # -- rankings across experiments ----------------------------------------------------------------------------
    @r.get("/rankings")
    def rankings(partition: str | None = None, horizon: int | None = None, family: str | None = None,
                 direction: str | None = None, verdict: str | None = None, experiment_id: str | None = None,
                 limit: int = Query(5000, le=50000)):
        rows = R.results(experiment_id, partition=partition, horizon=horizon, family=family, direction=direction,
                         verdict=verdict, limit=limit)
        q = st.benjamini_hochberg([x["p_matched"] for x in rows])
        for x, qq in zip(rows, q):
            x["q_across_rows"] = qq
        return dict(rows=records(pd.DataFrame(rows)), n=len(rows),
                    note=("q_matched is BH within each experiment and partition; q_across_rows re-applies BH over "
                          "every row shown, the stricter view when several experiments looked at the same data."))

    @r.get("/usage")
    def usage():
        return R.usage()

    # -- saved configurations -------------------------------------------------------------------------------------
    @r.get("/configs")
    def configs():
        return R.configs()

    @r.post("/configs")
    def save_config(body: SavedSignalConfig):
        R.resolve(body.config)
        return dict(config_id=R.save_config(body.name, body.config))

    @r.delete("/configs/{config_id}")
    def delete_config(config_id: str):
        if not R.delete_config(config_id):
            raise HTTPException(404, f"no saved configuration {config_id}")
        return dict(deleted=config_id)

    # -- Strategy Library ---------------------------------------------------------------------------------------
    @r.post("/import")
    def import_(body: ImportRequest = Body(...)):
        return lab.import_signal(body.experiment_id, body.signal_key, body.name, body.defaults)

    @r.get("/imports")
    def imports():
        return R.imports()

    return r


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:  # noqa: BLE001 - the new experiment records its own failure
        return None
