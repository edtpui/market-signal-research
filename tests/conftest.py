"""Shared fixtures: synthetic market data built in memory, and a Lab whose every path is under tmp_path.
No test reads operational data; tests marked `realdata` read the workspace research downloads read-only."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from strategy_lab.core.market import MarketData  # noqa: E402

SYNTH_SYMBOLS = ["SYNA", "SYNB", "SYNC", "SYND", "SYNE", "SPYX"]


def make_data(n: int = 300, symbols=("AAA", "BBB", "CCC"), seed: int = 1, start: str = "2019-01-02",
              with_volume: bool = True, actions: pd.DataFrame | None = None, price_mode: str = "adjusted",
              gaps: dict | None = None) -> MarketData:
    """Deterministic OHLCV random walks on business days. gaps: {symbol: [positions]} become missing bars."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    fields = {k: {} for k in ("open", "high", "low", "close", "volume")}
    for k, s in enumerate(symbols):
        r = rng.normal(0.0004, 0.012, n)
        close = 40.0 * (k + 1) * np.exp(np.cumsum(r))
        opn = close * np.exp(rng.normal(0, 0.004, n))
        hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, 0.004, n)))
        lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, 0.004, n)))
        vol = rng.integers(100_000, 1_000_000, n).astype(float)
        for f, v in zip(("open", "high", "low", "close", "volume"), (opn, hi, lo, close, vol)):
            fields[f][s] = v
    out = {f: pd.DataFrame(v, index=idx) for f, v in fields.items()}
    for f in out.values():
        f.index.name = "date"
    if gaps:
        for s, pos in gaps.items():
            for f in out.values():
                f.iloc[pos, f.columns.get_loc(s)] = np.nan
    if not with_volume:
        out.pop("volume")
    acts = actions if actions is not None else pd.DataFrame(columns=["date", "symbol", "kind", "value"])
    return MarketData(out, acts, price_mode)


@pytest.fixture
def data():
    return make_data()


SETTINGS = """
root = "{root}"
[data]
cache_dir = "{tmp}/cache"
[store]
experiments_dir = "{tmp}/experiments"
catalog = "{tmp}/catalog.db"
[strategies]
user_dir = "{tmp}/user_strategies"
[forward]
dir = "{tmp}/forward"
mock_dataset = "synthetic"
[universes]
syn = ["SYNA", "SYNB", "SYNC", "SYND", "SYNE"]
[[dataset]]
id = "synthetic"
name = "SYNTHETIC test data"
provider = "synthetic"
seed = 7
symbols = {symbols}
start = "2018-01-01"
end = "2025-12-31"
splits = [["SYNB", "2021-06-01", 4.0], ["SYND", "2023-03-01", 2.0]]
dividends = [["SYNA", "2020-03-02", 0.50], ["SYNA", "2021-03-01", 0.55], ["SYNC", "2022-09-01", 1.20]]
gaps = [["SYNE", "2022-05-10"], ["SYNE", "2022-05-11"]]
"""


def write_settings(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "settings.toml"
    p.write_text(SETTINGS.format(root=ROOT.as_posix(), tmp=tmp_path.as_posix(),
                                 symbols=str(SYNTH_SYMBOLS).replace("'", '"')) + extra, encoding="utf-8")
    (tmp_path / "user_strategies").mkdir(exist_ok=True)
    return p


@pytest.fixture
def lab(tmp_path):
    from strategy_lab.lab import default_lab
    lb = default_lab(write_settings(tmp_path))
    yield lb
    lb.close()


def wait_done(lab, run_id: str, timeout: float = 120.0) -> dict:
    import time
    t = time.time()
    while time.time() - t < timeout:
        m = lab.experiments.get(run_id)
        if m["status"] in ("done", "failed"):
            return m
        time.sleep(0.05)
    raise TimeoutError(run_id)


def check_live_owner_is_left_alone(store, eid: str) -> None:
    """recover_interrupted (run at server start) leaves an experiment alone while the process its owner file names is
    alive, and marks it failed once that process is gone (shared by the three stores, strategy_lab.catalog)."""
    import json
    import platform
    import subprocess
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (store.path(eid) / "owner.json").write_text(json.dumps(dict(pid=child.pid, host=platform.node())))
        store._set(eid, status="running")
        assert store.recover_interrupted() == 0 and store.get(eid)["status"] == "running"
    finally:
        child.kill()
        child.wait()
    assert store.recover_interrupted() == 1 and store.get(eid)["status"] == "failed"
    assert "interrupted" in store.get(eid)["error"]
