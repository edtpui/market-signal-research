"""Entry-Exit Research routes (/api/v1/entry-exit/...): the component catalog (entry signals, exit signals, stops,
targets, trailing, time and position-management exits), experiment validation and launch, the entry-exit matrix,
cell details (trades, equity curves), controlled comparisons, walk-forward results, leaderboards, saved
configurations and imports into the Strategy Library."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from strategy_lab.data.policy import period_labels
from strategy_lab.entryexit.config import EntryExitExperimentConfig
from strategy_lab.entryexit.plan import ExitPlanSpec, resolve_plan
from strategy_lab.entryexit.store import MATRIX_METRICS, PORTFOLIO_METRICS
from strategy_lab.research.config import DEFAULT_PARTITIONS

ROLES = [
    dict(id="entry", name="Entry signals", description="bullish catalog signals and combinations: open a long "
                                                        "position at the next open"),
    dict(id="exit_signal", name="Exit signals", description="close-based conditions (indicator, price-action, "
                                                            "time, and every bearish catalog signal via signal_exit): "
                                                            "sell at the next open"),
    dict(id="stop", name="Protective stops", description="fixed protective levels set at entry"),
    dict(id="trailing", name="Trailing exits", description="protective levels updated at each close, never lowered"),
    dict(id="target", name="Profit taking", description="limit orders at levels fixed at entry"),
    dict(id="management", name="Position management", description="partial targets (fraction), stop moves after a "
                                                                   "partial (then: breakeven / lock), min_bars, "
                                                                   "max_hold, sizing, the position cap"),
]


class SavedEEConfig(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: EntryExitExperimentConfig


class EEImportRequest(BaseModel):
    experiment_id: str
    cell: str
    name: str = Field("", max_length=200)


def _role(d: dict) -> str:
    if d["kind"] == "condition":
        return "exit_signal"
    if d["kind"] == "target":
        return "target"
    return "trailing" if d["family"] == "trailing" else "stop"


def build_router(lab) -> APIRouter:
    from strategy_lab.api.app import records

    r = APIRouter(prefix="/api/v1/entry-exit", tags=["entry-exit research"])
    R = lab.entry_exit

    @r.get("/catalog")
    def catalog():
        ex = lab.exits.describe()
        for d in ex["exits"]:
            d["role"] = _role(d)
        sig = lab.signals.describe()
        entries = [dict(id=s["id"], name=s["name"], family=s["family"], trigger=s["trigger"], fields=s["fields"])
                   for s in sig["signals"] if s["direction"] == "bullish"]
        exit_signals = [dict(id=s["id"], name=s["name"], family=s["family"], trigger=s["trigger"], fields=s["fields"])
                        for s in sig["signals"] if s["direction"] == "bearish"]
        return dict(roles=ROLES, exits=ex["exits"], exit_families=ex["families"], n_exits=ex["n_exits"],
                    entry_signals=entries, exit_signals=exit_signals,
                    signal_families=[f for f in sig["families"]],
                    semantics=dict(
                        timing="signal at the close of t; entry at the open of t+1 (market); exits decided at a "
                               "close execute at the next session",
                        priority=["market exit decided at the previous close (the open)",
                                  "protective stop (open on a gap through, else the level)",
                                  "targets in plan order (only what the stop left: stop-first when both are reached "
                                  "in one bar)"],
                        entry_session="the initial stop and targets are attached to the entry and evaluated after "
                                      "the entry fill at the open, in the same order",
                        r="R = entry price - the plan's initial protective stop; without a stop R = ref_atr_mult x "
                          "ATR(ref_atr_n) at the signal close",
                        trailing="a stop level is the maximum of its previous value and its new candidate: it "
                                 "never moves down"))

    @r.post("/plans/resolve")
    def plan(body: ExitPlanSpec):
        return resolve_plan(body).describe()

    @r.get("/options")
    def options():
        d = EntryExitExperimentConfig.model_fields
        defaults = {k: (v.default_factory() if v.default_factory else v.default) for k, v in d.items()
                    if k not in ("dataset", "start", "end")}
        return dict(defaults=defaults, policy=lab.policy.to_dict(),
                    default_partitions=[dict(name=n, role=ro, start=a.isoformat(), end=b.isoformat())
                                        for n, ro, a, b in DEFAULT_PARTITIONS],
                    matrix_metrics=list(MATRIX_METRICS), portfolio_metrics=list(PORTFOLIO_METRICS),
                    objectives=["risk_adjusted", "low_drawdown", "expectancy", "consistency", "oos_excess",
                                "confidence", "cagr", "mean_ret"],
                    boards=["entries", "exits", "pairs", "multi_entry", "systems"])

    @r.post("/validate")
    def validate(cfg: EntryExitExperimentConfig):
        resolved, entries, plans = R.resolve(cfg)
        notes = []
        nd = [p for p in plans if not p.deployable]
        if nd:
            notes.append(f"{len(nd)} of {len(plans)} exit plans use intrabar stop/limit orders or high/low bars: "
                         "they backtest here but cannot run on the ptrade host (open/close bars, market orders)")
        if resolved.portfolio.run == "all" and len(entries) * len(plans) > 150:
            notes.append(f"{len(entries) * len(plans)} cells x partitions event-driven backtests: consider "
                         "portfolio.run = top")
        m = len(entries) * len(plans)
        notes.append(f"{m} cells are tested per partition; BH controls the false discovery rate across them, and "
                     "every earlier experiment on the same partitions adds to the search (see usage)")
        return dict(ok=True, n_entries=len(entries), n_exits=len(plans), n_cells=m,
                    n_symbols=len(resolved.universe), dataset_version=resolved.dataset_version,
                    partitions=[dict(name=p.name, role=p.role, start=p.start.isoformat(), end=p.end.isoformat(),
                                     locked=p.role == "test" and not resolved.evaluate_test)
                                for p in resolved.partitions],
                    analysis_end=resolved.analysis_end.isoformat(),
                    entries=[dict(key=e.key, name=e.name, family=e.family, warmup=e.warmup) for e in entries],
                    exits=[dict(key=p.key, name=p.name, family=p.family, deployable=p.deployable,
                                trigger=p.trigger) for p in plans],
                    periods=period_labels(resolved.start, resolved.analysis_end), notes=notes)

    @r.post("/experiments")
    def launch(cfg: EntryExitExperimentConfig):
        eid = R.create(cfg)
        R.submit(eid)
        return R.get(eid)

    @r.get("/experiments")
    def experiments(limit: int = Query(200, ge=1, le=2000)):
        return R.list(limit)

    @r.get("/experiments/{eid}")
    def experiment(eid: str):
        meta = R.get(eid)
        if meta is None:
            raise KeyError(f"no entry-exit experiment {eid}")
        return dict(meta=meta, config=R.read_json(eid, "config.json"), study=R.read_json(eid, "study.json"),
                    entries=R.read_json(eid, "entries.json"), manifest=R.read_json(eid, "manifest.json"),
                    prior_usage=R.prior_usage(eid))

    @r.post("/experiments/{eid}/cancel")
    def cancel(eid: str):
        return R.cancel(eid)

    @r.post("/experiments/{eid}/reproduce")
    def reproduce(eid: str):
        return R.reproduce(eid)

    @r.get("/experiments/{eid}/matrix")
    def matrix(eid: str, partition: str, metric: str = "excess_matched"):
        if metric not in MATRIX_METRICS + PORTFOLIO_METRICS + ("excess_random_portfolio",):
            raise ValueError(f"unknown metric {metric!r}")
        return R.matrix(eid, partition, metric)

    @r.get("/experiments/{eid}/cells/{cell}")
    def cell(eid: str, cell: str):
        return R.cell(eid, cell)

    @r.get("/experiments/{eid}/cells/{cell}/trades")
    def trades(eid: str, cell: str, partition: str | None = None, limit: int = Query(3000, ge=1, le=20000)):
        return records(R.cell_trades(eid, cell, limit, partition))

    @r.get("/experiments/{eid}/cells/{cell}/equity")
    def equity(eid: str, cell: str, partition: str):
        out = R.cell_equity(eid, cell, partition)
        return {k: records(v) for k, v in out.items()}

    @r.get("/experiments/{eid}/comparisons")
    def comparisons(eid: str):
        return R.read_json(eid, "comparisons.json") or {}

    @r.get("/experiments/{eid}/walk-forward")
    def walk_forward(eid: str):
        return R.read_json(eid, "walk_forward.json") or {}

    @r.get("/experiments/{eid}/tests")
    def tests(eid: str):
        return R.read_json(eid, "tests.json") or {}

    @r.get("/leaderboards")
    def leaderboards(board: str = "pairs", objective: str = "oos_excess", role: str = "validation",
                     experiment_id: str | None = None, min_trades: int = Query(30, ge=1),
                     max_drawdown: float | None = None, limit: int = Query(100, ge=1, le=1000)):
        return R.leaderboard(board, objective, role, limit, experiment_id, min_trades, max_drawdown)

    @r.get("/usage")
    def usage():
        return R.usage()

    @r.post("/import")
    def import_cell(body: EEImportRequest):
        return lab.import_entry_exit(body.experiment_id, body.cell, body.name)

    @r.get("/imports")
    def imports():
        return R.imports()

    @r.get("/configs")
    def configs():
        return R.configs()

    @r.post("/configs")
    def save_config(body: SavedEEConfig):
        return dict(config_id=R.save_config(body.name, body.config))

    @r.delete("/configs/{cid}")
    def delete_config(cid: str):
        return dict(deleted=R.delete_config(cid))

    return r


__all__ = ["build_router"]
