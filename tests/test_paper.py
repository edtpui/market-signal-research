"""The forward paper-trading adapter: export bundle, plug-in contract, the mock host, sync, failure handling, and
historical vs forward parity on deterministic data."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import pytest
import requests

from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.paper import api_models as A
from strategy_lab.paper import contract as C
from strategy_lab.paper.client import HttpTransport, PtradeApiError
from strategy_lab.paper.mock_host import HostError, load_plugin
from strategy_lab.paper.service import DeployError
from strategy_lab.paper.spec import DeploymentSpec

UNI = ["SYNA", "SYNB", "SYNC", "SYND", "SYNE"]


def spec(**kw) -> DeploymentSpec:
    base = dict(name="mom", strategy="momentum_rotation", strategy_version="1.0",
                params={"lookback": 40, "top_k": 2, "rebalance_every": 5}, universe=UNI,
                start_date="2023-01-03", execution=ExecutionConfig(slippage_bps=5, commission=dict(bps=1)))
    base.update(kw)
    return DeploymentSpec(**base)


def test_bundle_layout_pin_and_import_safety(lab):
    d = lab.forward.deploy(spec(), target="ptrade")
    assert d["status"] == "exported" and d["created"]
    b = Path(d["bundle_dir"])
    man = json.loads((b / "MANIFEST.json").read_text())
    plugin = b / "baskets" / "plugins" / f"{man['module']}.py"
    tree = ast.parse(plugin.read_text())
    top = {(n.module if isinstance(n, ast.ImportFrom) else n.names[0].name) for n in tree.body
           if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert top <= {"__future__", "hashlib", "json", "sys", "pathlib"}         # import-safe in ptrade's loader
    pin = json.loads((b / "models" / "strategy_lab" / "PIN.json").read_text())
    import hashlib
    for rel, sha in pin["files"].items():
        assert hashlib.sha256((b / "models" / "strategy_lab" / rel).read_bytes()).hexdigest() == sha
    entry = (b / "config" / "books.entry.toml").read_text()
    assert 'ledger = "ledger_strategy_lab"' in entry and f'id = "{d["book_id"]}"' in entry
    assert d["model_id"] in (b / "docs" / "protocols" / f"{d['book_id']}.md").read_text()
    assert "Nothing in this bundle has been applied" in (b / "OPERATOR.md").read_text()
    rule, producer, _ = load_plugin(plugin)
    assert rule.name == d["book_id"] and producer.config()["deployment"] == spec().key()
    with pytest.raises(PtradeApiError, match="disabled"):
        lab.forward.sync(d["deployment_id"])
    with pytest.raises(DeployError, match="operator"):
        lab.forward.set_status(d["deployment_id"], "paused", "x")


def test_bundle_is_lf_only_whatever_the_checkout(lab):
    """ptrade runs on Linux and every sha256 in the bundle feeds the model_id, so a CRLF checkout (a user strategy
    here) must give the same LF bundle."""
    user = Path(lab.settings.user_strategies_dir)
    user.mkdir(parents=True, exist_ok=True)
    src = (Path(__file__).resolve().parents[1] / "strategies_user" / "inverse_vol.py").read_bytes()
    assert b"\r" not in src
    (user / "inverse_vol.py").write_bytes(src.replace(b"\n", b"\r\n"))
    lab.registry.load_dir(user)
    d = lab.forward.deploy(spec(strategy="inverse_vol", strategy_version=lab.registry.get("inverse_vol").version,
                                params={}), target="ptrade")
    b = Path(d["bundle_dir"])
    files = [p for p in b.rglob("*") if p.is_file()]
    assert files and not [p for p in files if b"\r" in p.read_bytes()]
    pinned = b / "models" / "strategy_lab" / "user" / "inverse_vol.py"
    assert pinned.read_bytes() == src
    pin = json.loads((b / "models" / "strategy_lab" / "PIN.json").read_text())
    import hashlib
    assert pin["files"]["user/inverse_vol.py"] == hashlib.sha256(src).hexdigest()


def test_reexport_rebuilds_an_exported_ptrade_bundle_only(lab, monkeypatch):
    import datetime
    import types
    from strategy_lab.paper import export as X
    d = lab.forward.deploy(spec(), target="ptrade")
    b = Path(d["bundle_dir"])
    (b / "stale.txt").write_text("left from an older export")

    class Later(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.datetime(2031, 6, 1, 12, 0, tzinfo=tz)

    monkeypatch.setattr(X, "dt", types.SimpleNamespace(datetime=Later, timezone=datetime.timezone))
    r = lab.forward.reexport(d["deployment_id"])
    assert r["book_id"] == d["book_id"] and r["model_id"] == d["model_id"]      # same code, later: same model_id
    assert json.loads((b / "MANIFEST.json").read_text())["created_utc"].startswith("2031-06-01")
    assert not (b / "stale.txt").exists()
    man = json.loads((b / "MANIFEST.json").read_text())
    assert man["model_id"] == r["model_id"] and man["deployment_key"] == spec().key()
    m = lab.forward.deploy(spec(start_date="2023-01-04"), target="mock")
    with pytest.raises(DeployError, match="only an exported ptrade"):
        lab.forward.reexport(m["deployment_id"])


JOB_STUBS = {        # the ptrade modules scripts/strategy_lab_job.py imports, reduced to what it uses
    "baskets/__init__.py": "",
    "baskets/job.py": textwrap.dedent("""\
        import os
        def registered(p):
            return os.environ.get("REG") == "1"
        def step(asof, prices_db, ledger_path, alert):
            if os.environ.get("BOOM"):
                raise RuntimeError("boom")
            alert("WARN", "stub warn")
            return dict(status=os.environ.get("STATUS", "ok"), sessions=[asof], books={}, errors=[])
        """),
    "ptrade/__init__.py": "",
    "ptrade/ops.py": "def last_completed_session():\n    return '2026-11-04'\n",
    "ptrade/config.py": textwrap.dedent("""\
        from pathlib import Path
        R = Path(__file__).resolve().parents[1]
        def resolve_path(k):
            return {"prices": R / "data/prices.duckdb", "ledger_strategy_lab": R / "data/ledger_strategy_lab.db"}[k]
        """),
}


@pytest.mark.parametrize("env,args,status,code", [
    ({"REG": "1"}, [], "skipped", 0),                    # the price store ends 2026-11-03, asof is 2026-11-04
    ({"REG": "1"}, ["2026-11-03"], "ok", 0),
    ({"REG": "0"}, [], "ok", 0),                         # nothing registered: step decides (ptrade returns idle)
    ({"REG": "1", "STATUS": "error"}, ["2026-11-03"], "error", 1),
    ({"REG": "1", "BOOM": "1"}, ["2026-11-03"], "error", 1),
])
def test_bundled_job_script_guards_like_the_daily_job(lab, tmp_path, env, args, status, code):
    import duckdb
    d = lab.forward.deploy(spec(), target="ptrade")
    root = tmp_path / "pt"
    for rel, src in JOB_STUBS.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(src)
    (root / "data").mkdir()
    (root / "scripts").mkdir()
    job = root / "scripts" / "strategy_lab_job.py"
    job.write_bytes((Path(d["bundle_dir"]) / "scripts" / "strategy_lab_job.py").read_bytes())
    con = duckdb.connect(str(root / "data" / "prices.duckdb"))
    con.execute("CREATE TABLE prices AS SELECT DATE '2026-11-03' AS date")
    con.close()
    r = subprocess.run([sys.executable, "-B", str(job), *args], capture_output=True, text=True, cwd=root,
                       env={**os.environ, **env}, timeout=120)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert (r.returncode, out["status"]) == (code, status), r.stderr
    assert out["asof"] == (args[0] if args else "2026-11-04")


def test_tampered_pin_is_refused(lab, tmp_path):
    d = lab.forward.deploy(spec(), target="ptrade")
    b = Path(d["bundle_dir"])
    pin = b / "models" / "strategy_lab" / "PIN.json"
    os.chmod(pin, 0o666)
    pin.write_text(pin.read_text().replace('"engine"', '"engine2"'))
    rule, _, _ = load_plugin(next((b / "baskets" / "plugins").glob("*.py")))
    with pytest.raises(RuntimeError, match="PIN.json"):
        rule.simulate(pd.DataFrame(), None, "2023-01-03", "2023-01-04", {})


def test_pinned_bundle_runs_in_a_fresh_process_without_strategy_lab(lab, tmp_path):
    """What ptrade's process sees: only the bundle (no src/ on the path). The pinned package must be complete."""
    d = lab.forward.deploy(spec(), target="ptrade")
    md, _ = lab.store.load("synthetic", UNI, "2018-01-02", "2023-03-31")
    md.fields["open"].to_csv(tmp_path / "opens.csv")
    md.fields["close"].to_csv(tmp_path / "closes.csv")
    plugin = next((Path(d["bundle_dir"]) / "baskets" / "plugins").glob("*.py"))
    man = json.loads((Path(d["bundle_dir"]) / "MANIFEST.json").read_text())
    script = textwrap.dedent(f"""
        import importlib.util, json, sys
        import pandas as pd
        assert "strategy_lab" not in sys.modules
        spec = importlib.util.spec_from_file_location("baskets.plugins.x", r"{plugin}")
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        assert "strategy_lab" not in sys.modules          # import-safe
        o = pd.read_csv(r"{tmp_path / 'opens.csv'}", index_col=0, parse_dates=True)
        c = pd.read_csv(r"{tmp_path / 'closes.csv'}", index_col=0, parse_dates=True)
        from types import SimpleNamespace
        fr = SimpleNamespace(opens=o, closes=c.ffill(), spy_close=pd.Series(dtype=float), cal=o.index)
        run = mod.RULE.simulate(None, fr, "2023-01-03", "2023-03-31", {json.dumps(man["params"])})
        import strategy_lab
        print(json.dumps(dict(path=strategy_lab.__file__, days=len(run.daily), eq=float(run.daily.equity.iloc[-1]),
                              trades=len(run.trades))))
    """)
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH",)}
    r = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, env=env, cwd=tmp_path,
                       timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert "models" in got["path"] and got["days"] > 50 and got["trades"] > 0


@pytest.mark.parametrize("bad,match", [
    (dict(strategy="rsi_mean_reversion", params={}), "bar fields"),
    (dict(execution=ExecutionConfig(allow_short=True)), "short"),
    (dict(execution=ExecutionConfig(participation_cap=0.1)), "volume"),
    (dict(execution=ExecutionConfig(price_mode="raw")), "adjusted"),
    (dict(universe=UNI + ["SPYX", "NOPE"]), "no data"),
])
def test_incompatible_deployments_are_refused(lab, bad, match):
    with pytest.raises(DeployError, match=match):
        lab.forward.deploy(spec(**bad))


def test_spy_is_refused(lab):
    v = lab.forward.validate(spec(universe=["SYNA", "SPY"]))
    assert not v["ok"] and any("SPY" in p for p in v["problems"])


def test_deploy_is_idempotent_and_keyed_by_config(lab):
    a = lab.forward.deploy(spec())
    b = lab.forward.deploy(spec(name="renamed"))                       # the name is not part of the key
    c = lab.forward.deploy(spec(params={"lookback": 41, "top_k": 2, "rebalance_every": 5}))
    assert a["created"] and not b["created"] and b["deployment_id"] == a["deployment_id"]
    assert c["created"] and c["deployment_id"] != a["deployment_id"]
    assert a["status"] == "registered" and a["start_date"] == "2023-01-03"
    assert len(lab.forward.host.registrations()) == 2


def test_forward_matches_the_backtest_engine(lab):
    deps = [lab.forward.deploy(spec()),
            lab.forward.deploy(spec(strategy="sma_crossover", params={"fast": 10, "slow": 40, "max_positions": 3})),
            lab.forward.deploy(spec(strategy="ml_ridge_ranker", params={"train_window": 40, "retrain_every": 10,
                                                                         "top_k": 2, "horizon": 5})),
            lab.forward.deploy(spec(strategy="buy_and_hold", params={"rebalance_every": 20}))]
    out = lab.forward.advance(to="2023-05-31")
    assert out["clock"] == "2023-05-31"
    for d in deps:
        sync = out["synced"][d["deployment_id"]]
        assert "error" not in sync and sync["days"] > 90
        rec = lab.forward.reconcile(d["deployment_id"])
        assert rec["identical"], rec
        assert rec["max_abs_equity_diff"] == 0.0 and rec["forward_trades"] > 0
    snap = lab.forward.snapshot(deps[0]["deployment_id"])
    assert snap["equity_value"] == pytest.approx(snap["cash"] + sum(p["value"] for p in snap["positions"]))
    assert snap["metrics"]["total_return"]["value"] == pytest.approx(snap["equity_value"] / 100_000 - 1)
    again = lab.forward.sync(deps[0]["deployment_id"])
    assert again == dict(days=0, trades=0, holdings=0, picks=0, restated=0)


def test_forward_metrics_use_the_backtest_definitions(lab):
    d = lab.forward.deploy(spec())
    lab.forward.advance(to="2023-06-30")
    from strategy_lab.experiments.config import ExperimentConfig
    snap = lab.forward.snapshot(d["deployment_id"])
    cfg = ExperimentConfig(strategy="momentum_rotation", params=spec().params, dataset="synthetic", universe=UNI,
                           start=snap["deployment"]["start_date"], end="2023-06-30", benchmark=None,
                           execution=spec().execution)
    md, _ = lab.store.load("synthetic", UNI, cfg.start, cfg.end, warmup_bars=60)
    res = lab.backtest(cfg, data=md.restrict(("open", "close")))
    for k in ("total_return", "volatility", "sharpe", "sortino", "max_drawdown", "trade_count", "win_rate",
              "turnover", "exposure"):
        assert snap["metrics"][k]["value"] == pytest.approx(res.metrics[k]["value"], rel=1e-9, abs=1e-12), k


def test_pause_resume_and_late_days(lab):
    d = lab.forward.deploy(spec())
    lab.forward.advance(to="2023-02-15")
    n = len(lab.forward.snapshot(d["deployment_id"])["equity"])
    lab.forward.set_status(d["deployment_id"], "paused", "test pause")
    lab.forward.advance(to="2023-03-15")
    assert len(lab.forward.snapshot(d["deployment_id"])["equity"]) == n
    lab.forward.set_status(d["deployment_id"], "active", "test resume")
    out = lab.forward.advance(to="2023-03-20")
    summary = out["sessions"][0]["books"][d["book_id"]]
    assert summary["late_days"]                                          # the paused days are appended late
    assert lab.forward.reconcile(d["deployment_id"])["identical"]


FAILING = '''
from strategy_lab.core.strategy import Strategy
from strategy_lab.core.types import TargetWeights


class Breaks(Strategy):
    id = "breaks_later"
    version = "1"

    def on_bar(self, ctx):
        if ctx.timestamp >= __import__("pandas").Timestamp("2023-02-01"):
            raise RuntimeError("model failure")
        if ctx.execution.session_index == 0:
            return TargetWeights({"SYNA": 0.5})
'''


def test_one_failing_book_never_stops_the_others(lab, tmp_path):
    (tmp_path / "user_strategies" / "breaks.py").write_text(FAILING, encoding="utf-8")
    lab.registry.load_dir(tmp_path / "user_strategies")
    bad = lab.forward.deploy(spec(strategy="breaks_later", strategy_version="1", params={}))
    good = lab.forward.deploy(spec())
    out = lab.forward.advance(to="2023-02-10")
    last = out["sessions"][-1]["books"]
    assert "error" in last[bad["book_id"]] and "model failure" in last[bad["book_id"]]["error"]
    assert "error" not in last[good["book_id"]]
    assert lab.forward.host.run_log(1)[0]["status"] == "warn"
    assert lab.forward.snapshot(good["deployment_id"])["deployment"]["last_asof"] == "2023-02-10"
    assert lab.forward.snapshot(bad["deployment_id"])["deployment"]["last_asof"] < "2023-02-01"


def test_restatement_is_recorded_not_overwritten(lab):
    d = lab.forward.deploy(spec())
    lab.forward.advance(to="2023-02-15")
    h = lab.forward.host
    reg = h.registrations()[0]
    rule = h._plugin(reg["bundle_dir"])[0]

    class Shifted:
        name = rule.name

        def simulate(self, *a):
            run = rule.simulate(*a)
            run.daily = run.daily.copy()
            run.daily.loc[run.daily.index[3], "equity"] *= 1.01
            return run

        def pending(self, *a):
            return rule.pending(*a)

    h._plugins[reg["bundle_dir"]] = (Shifted(), *h._plugins[reg["bundle_dir"]][1:])
    h.advance(to="2023-02-16")
    with h._conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM restatements")]
        stored = c.execute("SELECT equity FROM days WHERE asof = ?", (rows[0]["asof"],)).fetchone()[0]
    assert len(rows) == 1 and rows[0]["field"] == "equity"
    assert stored == pytest.approx(rows[0]["ledger_value"]) and stored != pytest.approx(rows[0]["rerun_value"])
    book = h.api_book(d["book_id"])
    assert book["evidence"]["restatements"]
    with h._conn() as c, pytest.raises(Exception, match="append-only"):
        c.execute("UPDATE days SET equity = 2 WHERE asof = ?", (rows[0]["asof"],))


def test_mock_api_matches_the_ptrade_v1_schema(lab):
    d = lab.forward.deploy(spec())
    lab.forward.advance(to="2023-02-28")
    h, bid = lab.forward.host, d["book_id"]
    for b in h.api_books():
        A.strict(A.Book, b)
    A.strict(A.BookDetail, h.api_book(bid))
    A.strict(A.Equity, h.api_equity(bid))
    A.strict(A.AsOf, h.api_holdings(bid))
    A.strict(A.AsOf, h.api_trades(bid, "2023-01-01", "2023-02-28"))
    A.strict(A.Picks, h.api_picks(bid))
    with pytest.raises(ValueError, match="not in the ptrade API v1 schema"):
        A.strict(A.AsOf, {**h.api_holdings(bid), "invented": 1})


def test_mock_host_refuses_a_mismatched_bundle(lab):
    d = lab.forward.deploy(spec(), target="ptrade")
    b = Path(d["bundle_dir"])
    man = b / "MANIFEST.json"
    os.chmod(man, 0o666)
    m = json.loads(man.read_text())
    m["model_id"] = m["model_id"][:-4] + "beef"
    man.write_text(json.dumps(m))
    with pytest.raises(HostError, match="model_id mismatch"):
        lab.forward.host.install(b, "2023-01-03")


class FakeResp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body
        self.text = json.dumps(body)

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), 0

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls += 1
        x = self.seq.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def test_http_transport_retries_only_transient_errors():
    s = FakeSession([requests.ConnectionError("down"), FakeResp(503, {}), FakeResp(200, [])])
    assert HttpTransport("http://127.0.0.1:9", session=s, backoff=0).get("/books") == []
    assert s.calls == 3
    s2 = FakeSession([FakeResp(404, {"detail": "no"})])
    with pytest.raises(PtradeApiError) as e:
        HttpTransport("http://127.0.0.1:9", session=s2, backoff=0).get("/books/x")
    assert e.value.status == 404 and s2.calls == 1
    s3 = FakeSession([FakeResp(500, {})] * 4)
    with pytest.raises(PtradeApiError):
        HttpTransport("http://127.0.0.1:9", session=s3, backoff=0, retries=3).get("/books")
    assert s3.calls == 4


def test_contract_mirror_checks():
    run = C.BookRun(daily=pd.DataFrame(dict(equity=[1.0, 0.0], ret=0, gross=0, hedge=0, n=0, m=1)),
                    trades=pd.DataFrame(columns=C.TRADE_COLS), holdings=pd.DataFrame(columns=C.HOLD_COLS),
                    picks=pd.DataFrame(columns=["date", "ticker", "renew"]))
    with pytest.raises(ValueError, match="positive"):
        run.check()
    d = pd.Timestamp("2023-01-03")
    bad = pd.DataFrame([dict(ticker="X", bucket="na", direction="sl", date=d, entry_date=d, exit_date=d, pred=0.0,
                             vol20=None)])
    with pytest.raises(ValueError, match="entry_date <= asof"):
        C.check_scores(bad, d)
