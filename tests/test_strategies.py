"""Strategy interface compliance for the built-in library and user strategies; discovery and versioning."""
from __future__ import annotations

import pickle

import numpy as np
import pytest

from conftest import make_data
from strategy_lab.core.strategy import ParamError, Strategy
from strategy_lab.engine.config import ExecutionConfig
from strategy_lab.engine.runner import StrategyRunner
from strategy_lab.strategies.ml import FEATURES, LinearModel
from strategy_lab.strategies.registry import StrategyRegistry, default_registry

SMALL = {"sma_crossover": {"fast": 10, "slow": 40}, "momentum_rotation": {"lookback": 40, "top_k": 2},
         "rsi_mean_reversion": {"entry_rsi": 45}, "ml_ridge_ranker": {"train_window": 60, "top_k": 2},
         "momentum_ml_ensemble": {"train_window": 60, "top_k": 2}, "buy_and_hold": {}}


def test_every_builtin_describes_itself_and_validates_params():
    reg = default_registry()
    ids = {c.id for c in reg.all()}
    assert {"buy_and_hold", "sma_crossover", "momentum_rotation", "rsi_mean_reversion", "ml_ridge_ranker",
            "momentum_ml_ensemble", "artifact_model"} <= ids
    for cls in reg.all():
        d = cls.describe()
        assert d["id"] and d["version"] and d["category"] and len(d["source_sha256"]) == 64
        for name, p in d["params"].items():
            assert p["kind"] in ("int", "float", "bool", "str", "choice", "symbols")
        with pytest.raises(ParamError, match="unknown"):
            cls({"no_such_param": 1})
    with pytest.raises(ParamError, match="below the minimum"):
        reg.get("momentum_rotation")({"top_k": 0})
    with pytest.raises(ParamError, match="integer"):
        reg.get("momentum_rotation")({"top_k": 2.5})


@pytest.mark.parametrize("sid", sorted(SMALL))
def test_builtins_run_and_never_see_the_mode(sid):
    d = make_data(400, symbols=("AAA", "BBB", "CCC", "DDD"), seed=4)
    cls = default_registry().get(sid)
    s = cls(SMALL[sid])
    assert not hasattr(s, "mode") and "mode" not in s.params
    out = StrategyRunner(s, d, ExecutionConfig(), 100_000, d.index[0], d.index[-1]).run()
    assert len(out.days) == 400 and all(np.isfinite(x.equity) for x in out.days)
    import json
    json.dumps(out.final_state)                                            # state stays serialisable


@pytest.mark.parametrize("sid", ["sma_crossover", "momentum_rotation", "ml_ridge_ranker", "momentum_ml_ensemble",
                                 "rsi_mean_reversion"])
def test_results_do_not_depend_on_extra_history(sid):
    """A strategy that declares its warm-up honestly gives the same results with more history loaded: what makes a
    backtest and a forward host (which loads everything since 2018) agree."""
    d = make_data(700, symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF"), seed=8)   # ML needs >= 5 per date
    cls = default_registry().get(sid)
    w = cls(SMALL[sid]).warmup()
    start = d.index[w + 150]
    trimmed = type(d)({k: v.iloc[150:] for k, v in d.fields.items()}, d.actions, d.price_mode)
    a = StrategyRunner(cls(SMALL[sid]), d, ExecutionConfig(), 1e5, start, d.index[-1]).run()
    b = StrategyRunner(cls(SMALL[sid]), trimmed, ExecutionConfig(), 1e5, start, d.index[-1]).run()
    assert [f.to_row() for f in a.fills] == [f.to_row() for f in b.fills]
    assert len(a.fills) > 0


def test_artifact_model_loads_only_the_pinned_file(tmp_path):
    model = LinearModel(coef_=np.array([0.0, 1.0, 0.5, -0.2, 0.0]))
    p = tmp_path / "m.pkl"
    p.write_bytes(pickle.dumps(model))
    import hashlib
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    d = make_data(200, symbols=("AAA", "BBB", "CCC", "DDD"), seed=2)
    cls = default_registry().get("artifact_model")
    out = StrategyRunner(cls({"artifact": str(p), "sha256": sha, "top_k": 2}), d, ExecutionConfig(), 1e5,
                         d.index[0], d.index[-1]).run()
    assert len(out.fills) > 0 and len(FEATURES) == 5
    with pytest.raises(ValueError, match="refusing"):
        StrategyRunner(cls({"artifact": str(p), "sha256": "0" * 64}), d, ExecutionConfig(), 1e5, d.index[0],
                       d.index[-1]).run()


USER = '''
from strategy_lab.core.strategy import Param, Strategy
from strategy_lab.core.types import TargetWeights


class Tilt(Strategy):
    id = "user_tilt"
    version = "{v}"
    category = "custom"
    params_schema = {{"w": Param(0.5, "float", 0.0, 1.0)}}

    def on_bar(self, ctx):
        if ctx.execution.session_index == 0:
            return TargetWeights({{ctx.universe[0]: self.params["w"]}})
'''


def test_user_strategies_are_discovered_versioned_and_errors_reported(tmp_path):
    (tmp_path / "tilt_v1.py").write_text(USER.format(v="1.0"), encoding="utf-8")
    (tmp_path / "tilt_v2.py").write_text(USER.format(v="2.0"), encoding="utf-8")
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    reg = default_registry(tmp_path)
    assert reg.get("user_tilt").version == "2.0" and reg.get("user_tilt", "1.0").version == "1.0"
    assert reg.versions("user_tilt") == ["1.0", "2.0"]
    assert len(reg.errors) == 1 and "boom" in reg.errors[0]["error"]
    with pytest.raises(KeyError, match="versions"):
        reg.get("user_tilt", "9.9")


def test_registry_rejects_bad_classes():
    reg = StrategyRegistry()

    class NoId(Strategy):
        version = "1"

        def on_bar(self, ctx):
            return None

    with pytest.raises(TypeError):
        reg.register(NoId)

    class A(Strategy):
        id, version = "dup", "1"

        def on_bar(self, ctx):
            return None

    class B(A):
        pass

    reg.register(A)
    with pytest.raises(ValueError, match="twice"):
        reg.register(B)


def test_the_shipped_user_strategies_load_and_run():
    from pathlib import Path
    reg = default_registry(Path(__file__).resolve().parents[1] / "strategies_user")
    assert not reg.errors, reg.errors
    user = [c for c in reg.all() if not c.__module__.startswith("strategy_lab.")]
    assert {c.id for c in user} >= {"inverse_vol"}
    d = make_data(200, symbols=("AAA", "BBB", "CCC"), seed=6)
    for cls in user:
        out = StrategyRunner(cls(), d, ExecutionConfig(), 1e5, d.index[cls().warmup()], d.index[-1]).run()
        assert len(out.fills) > 0, cls.id
