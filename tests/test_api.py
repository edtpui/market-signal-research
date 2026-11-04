"""API contract: every route the UI uses, error mapping, and the end-to-end workflows through HTTP semantics."""
from __future__ import annotations

import pytest

from asgi import Client
from conftest import wait_done
from strategy_lab.api.app import create_app

EXP = dict(strategy="momentum_rotation", params={"lookback": 40, "top_k": 2, "rebalance_every": 5},
           dataset="synthetic", universe_name="syn", start="2019-01-02", end="2020-12-31", benchmark="SPYX")


@pytest.fixture
def api(lab):
    return Client(create_app(lab))


def test_status_strategies_and_data(api):
    h = api.get("/api/v1/health").json()
    assert h["ok"] and h["policy"]["ceiling"] == "2025-12-31" and h["forward"]["ptrade_api_enabled"] is False
    s = api.get("/api/v1/strategies").json()
    assert any(x["id"] == "ml_ridge_ranker" for x in s["strategies"])
    one = api.get("/api/v1/strategies/sma_crossover").json()
    assert "class SmaCrossover" in one["code"] and one["warmup_default"] == 200
    assert one["source"].startswith("builtin:")
    assert api.get("/api/v1/strategies/nope").status_code == 404
    ds = api.get("/api/v1/data/datasets").json()
    assert ds[0]["id"] == "synthetic" and ds[0]["cached"] is False
    meta = api.post("/api/v1/data/datasets/synthetic/ingest").json()
    assert meta["last_date"] == "2025-12-31"
    det = api.get("/api/v1/data/datasets/synthetic").json()
    assert det["summary"]["symbols"] == 6 and det["summary"]["with_gaps"] == 1
    assert det["periods"] == ["BACK", "IS", "OOS"]          # 2018 warm-up bars overlap BACK; backtests start >= 2019
    cov = api.get("/api/v1/data/datasets/synthetic/coverage", {"only_gaps": "true"}).json()
    assert [r["symbol"] for r in cov["rows"]] == ["SYNE"]
    bars = api.get("/api/v1/data/datasets/synthetic/bars/SYNA", {"price_mode": "raw"}).json()
    assert len(bars["rows"]) > 1000 and len(bars["actions"]) == 2
    u = api.get("/api/v1/data/universes", {"dataset": "synthetic"}).json()
    assert {x["name"]: len(x.get("symbols", [])) for x in u}["syn"] == 5
    st = api.get("/api/v1/settings").json()
    assert [p["name"] for p in st["research_periods"]] == ["BACK", "IS", "OOS"]


def test_backtest_workflow(api, lab):
    v = api.post("/api/v1/experiments/validate", json=EXP).json()
    assert v["ok"] and v["n_symbols"] == 5 and v["periods"] == ["IS"]
    r = api.post("/api/v1/experiments", json={**EXP, "name": "api run"}).json()
    meta = wait_done(lab, r["run_id"])
    assert meta["status"] == "done"
    d = api.get(f"/api/v1/experiments/{r['run_id']}").json()
    assert d["metrics"]["sharpe"]["value"] is not None and d["manifest"]["dataset"]["id"] == "synthetic"
    eq = api.get(f"/api/v1/experiments/{r['run_id']}/equity").json()
    assert len(eq) > 400 and {"equity", "benchmark", "drawdown", "gross_exposure"} <= set(eq[0])
    for t in ("fills", "orders", "trades", "holdings", "monthly", "events"):
        assert api.get(f"/api/v1/experiments/{r['run_id']}/table/{t}").status_code == 200, t
    assert api.get(f"/api/v1/experiments/{r['run_id']}/table/secrets").status_code == 404
    roll = api.get(f"/api/v1/experiments/{r['run_id']}/rolling", {"window": 63}).json()
    assert roll["window"] == 63 and "benchmark" in roll
    b = api.post("/api/v1/experiments/batch", json=[{**EXP, "params": {**EXP["params"], "top_k": k}}
                                                    for k in (1, 3)]).json()
    for i in b["run_ids"]:
        assert wait_done(lab, i)["status"] == "done"
    cmp = api.get("/api/v1/compare", {"ids": ",".join([r["run_id"], *b["run_ids"]])}).json()
    assert len(cmp["runs"]) == 3 and cmp["overlap"]["start"] and len(cmp["correlation"]) == 3
    assert all("overlap_metrics" in x for x in cmp["runs"])
    assert len(api.get("/api/v1/experiments").json()) == 3
    rep = api.post(f"/api/v1/experiments/{r['run_id']}/reproduce").json()
    assert rep["status"] == "submitted"


def test_errors_are_mapped(api):
    past = api.post("/api/v1/experiments", json={**EXP, "end": "2026-02-02"})
    assert past.status_code == 422 and "ceiling" in past.json()["detail"]
    bad = api.post("/api/v1/experiments", json={**EXP, "params": {"top_k": 0}})
    assert bad.status_code == 422 and "minimum" in bad.json()["detail"]
    schema = api.post("/api/v1/experiments", json={**EXP, "execution": {"slippage_bps": -1}})
    assert schema.status_code == 422
    assert api.get("/api/v1/experiments/nope").status_code == 404


def test_configs_crud(api):
    c = api.post("/api/v1/configs", json={"name": "saved", "config": EXP}).json()
    assert api.get("/api/v1/configs").json()[0]["config_id"] == c["config_id"]
    assert api.delete(f"/api/v1/configs/{c['config_id']}").status_code == 200
    assert api.delete(f"/api/v1/configs/{c['config_id']}").status_code == 404


def test_forward_workflow(api, lab):
    r = api.post("/api/v1/experiments", json=EXP).json()
    wait_done(lab, r["run_id"])
    dep = api.post("/api/v1/forward/deployments", json={"from_run_id": r["run_id"], "start_date": "2024-01-02",
                                                         "name": "from api"}).json()
    assert dep["status"] == "registered" and dep["source_run_id"] == r["run_id"]
    bad = api.post("/api/v1/forward/deployments", json={"spec": {**dep["spec"], "strategy": "rsi_mean_reversion",
                                                                 "params": {}}})
    assert bad.status_code == 422 and bad.json()["problems"]
    host = api.get("/api/v1/forward/host").json()
    assert host["label"] == "SIMULATED" and host["clock"] is None
    adv = api.post("/api/v1/forward/host/advance", json={"to": "2024-02-29"}).json()
    assert adv["clock"] == "2024-02-29"
    lst = api.get("/api/v1/forward/deployments").json()
    assert lst[0]["equity_value"] and len(lst[0]["sparkline"]) > 30
    snap = api.get(f"/api/v1/forward/deployments/{dep['deployment_id']}").json()
    assert snap["positions"] and snap["fills"] and snap["metrics"]["total_return"]["value"] is not None
    rec = api.post(f"/api/v1/forward/deployments/{dep['deployment_id']}/reconcile").json()
    assert rec["identical"]
    p = api.post(f"/api/v1/forward/deployments/{dep['deployment_id']}/status", json={"status": "paused",
                                                                                   "reason": "check"}).json()
    assert p["host_status"] == "paused"
    assert api.post(f"/api/v1/forward/deployments/{dep['deployment_id']}/status",
                    json={"status": "paused", "reason": ""}).status_code == 422
    b = api.get(f"/api/v1/forward/deployments/{dep['deployment_id']}/bundle").json()
    assert "OPERATOR" not in b["manifest"] and "baskets.register" in b["operator"]
    mb = api.get("/mock-ptrade/api/v1/books").json()
    assert mb[0]["id"] == dep["book_id"] and mb[0]["status"] == "paused"
    assert api.get(f"/mock-ptrade/api/v1/books/{dep['book_id']}/equity").json()["points"]
    assert api.get("/mock-ptrade/api/v1/books/nope").status_code == 404
