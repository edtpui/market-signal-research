"""ML and multi-model strategies, with the information boundary built in.

Training rows are (features at session s, forward return from s to s + horizon). At session t a row is usable only
when s + horizon <= t, so its label is already known (supervised_rows enforces this; tests check it). Features at s
use closes up to s only. Models are refit every retrain_every sessions on a rolling window. Nothing is fit on data
after the current session, and nothing is precomputed over the whole window.
"""
from __future__ import annotations

import hashlib
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_lab.core.market import MarketView
from strategy_lab.core.strategy import Param, Strategy, StrategyContext
from strategy_lab.core.types import TargetWeights

FEATURES = ("r5", "r21", "r63", "vol21", "sma50_gap")
FEATURE_BARS = 64
MIN_CROSS_SECTION = 5       # symbols with a label on a date for that date to become training rows
MIN_TRAIN_ROWS = 50


def features_today(view: MarketView) -> pd.DataFrame:
    """One row per symbol with a bar today: trailing log returns, 21-day volatility, distance to the 50-day SMA."""
    c = view.history("close", bars=FEATURE_BARS)
    if len(c) < FEATURE_BARS:
        return pd.DataFrame(columns=FEATURES)
    lc = np.log(c)
    last = lc.iloc[-1]
    f = pd.DataFrame({
        "r5": last - lc.iloc[-6], "r21": last - lc.iloc[-22], "r63": last - lc.iloc[-64],
        "vol21": lc.diff().iloc[-21:].std(ddof=1),
        "sma50_gap": c.iloc[-1] / c.iloc[-50:].mean(skipna=False) - 1,
    })
    return f.dropna()


def zscore(df: pd.DataFrame) -> pd.DataFrame:
    sd = df.std(ddof=0).replace(0, np.nan)
    return ((df - df.mean()) / sd).fillna(0.0).clip(-5, 5)


def supervised_rows(feature_log: dict[pd.Timestamp, pd.DataFrame], view: MarketView, horizon: int,
                    window: int) -> tuple[np.ndarray, np.ndarray, list[pd.Timestamp]]:
    """X, y from logged feature rows whose label horizon has ended by the view's session. y is the forward log
    return from s to s + horizon, demeaned per date (a cross-sectional target)."""
    sessions = view.sessions
    pos = {d: i for i, d in enumerate(sessions)}
    now = len(sessions) - 1
    usable = sorted(d for d in feature_log if d in pos and pos[d] + horizon <= now)[-window:]
    if not usable:
        return np.empty((0, len(FEATURES))), np.empty(0), []
    lc = np.log(view.history("close", bars=now - pos[usable[0]] + 1))
    xs, ys = [], []
    for d in usable:
        f = feature_log[d]
        end = sessions[pos[d] + horizon]
        assert end <= view.timestamp                             # the information boundary
        y = (lc.loc[end, f.index] - lc.loc[d, f.index]).dropna()
        if len(y) < MIN_CROSS_SECTION:
            continue
        xs.append(zscore(f.loc[y.index, list(FEATURES)]).to_numpy())
        ys.append((y - y.mean()).to_numpy())
    if not xs:
        return np.empty((0, len(FEATURES))), np.empty(0), []
    return np.vstack(xs), np.concatenate(ys), usable


@dataclass
class LinearModel:
    """A fitted linear model (also the picklable artifact format of ArtifactModel's tests and demos)."""
    coef_: np.ndarray
    intercept_: float = 0.0

    def predict(self, X) -> np.ndarray:
        return np.asarray(X, dtype=float) @ self.coef_ + self.intercept_


def fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float) -> LinearModel:
    k = X.shape[1]
    coef = np.linalg.solve(X.T @ X + alpha * np.eye(k), X.T @ y)
    return LinearModel(coef_=coef, intercept_=0.0)


class RidgeScorer:
    """Model 1: walk-forward ridge regression on FEATURES -> forward return."""

    def __init__(self, horizon: int, train_window: int, retrain_every: int, alpha: float):
        self.horizon, self.window, self.every, self.alpha = horizon, train_window, retrain_every, alpha
        self.log: dict[pd.Timestamp, pd.DataFrame] = {}
        self.model: LinearModel | None = None
        self.fitted_at: pd.Timestamp | None = None
        self.n_train = 0

    def observe(self, view: MarketView, session_index: int) -> pd.DataFrame:
        f = features_today(view)
        if len(f):
            self.log[view.timestamp] = f
        keep = sorted(self.log)[-(self.window + self.horizon + 5):]
        self.log = {d: self.log[d] for d in keep}
        if session_index % self.every == 0:
            X, y, used = supervised_rows(self.log, view, self.horizon, self.window)
            if len(y) >= MIN_TRAIN_ROWS:
                self.model, self.fitted_at, self.n_train = fit_ridge(X, y, self.alpha), view.timestamp, len(y)
        return f

    def score(self, f: pd.DataFrame) -> pd.Series:
        if self.model is None or f.empty:
            return pd.Series(dtype=float)
        return pd.Series(self.model.predict(zscore(f[list(FEATURES)]).to_numpy()), index=f.index)


class MomentumScorer:
    """Model 2: 6-month momentum skipping the last week."""

    def __init__(self, lookback: int = 126, skip: int = 5):
        self.lookback, self.skip = lookback, skip

    def score(self, view: MarketView) -> pd.Series:
        need = self.lookback + self.skip + 1
        c = view.history("close", bars=need)
        if len(c) < need:
            return pd.Series(dtype=float)
        return (c.iloc[-1 - self.skip] / c.iloc[0] - 1).dropna()


def _top(scores: pd.Series, view: MarketView, k: int, gross: float) -> dict[str, float]:
    s = scores[[view.has_bar(x) for x in scores.index]].dropna()
    top = s.sort_values(ascending=False, kind="mergesort").index[:k]
    return {x: gross / k for x in sorted(top)}


class MLRidgeRanker(Strategy):
    id = "ml_ridge_ranker"
    version = "1.0"
    name = "ML ridge ranker (walk-forward)"
    category = "ml"
    description = ("Ridge regression of the horizon-day forward return on five price features, refit every "
                   "retrain_every sessions on the last train_window sessions whose labels are already known; holds "
                   "the top_k predictions equal weight, rebalancing every rebalance_every sessions. Needs >= 5 "
                   "symbols per session to train.")
    params_schema = {
        "horizon": Param(10, "int", 1, 63),
        "train_window": Param(250, "int", 20, 1000),
        "retrain_every": Param(21, "int", 1, 252),
        "alpha": Param(10.0, "float", 0.0, 1e6),
        "top_k": Param(10, "int", 1, 100),
        "rebalance_every": Param(5, "int", 1, 63),
        "gross": Param(1.0, "float", 0.05, 1.0),
    }

    def warmup(self) -> int:
        # the warm-up replay reaches train_window + horizon sessions back, each needing FEATURE_BARS of history:
        # declaring all of it keeps results independent of how much extra history a host loads
        return FEATURE_BARS + self.params["train_window"] + self.params["horizon"] + 1

    def initialize(self, ctx):
        p = self.params
        self.scorer = RidgeScorer(p["horizon"], p["train_window"], p["retrain_every"], p["alpha"])
        self._warm = False

    def on_bar(self, ctx: StrategyContext):
        p = self.params
        if not self._warm:                                    # replay warm-up sessions into the feature log
            self._warm = True
            for ago in range(min(len(ctx.market.sessions) - 1, p["train_window"] + p["horizon"]), 0, -1):
                self.scorer.observe(ctx.market.past(ago), 1)
        f = self.scorer.observe(ctx.market, ctx.execution.session_index)
        if self.scorer.fitted_at is not None:
            self.state["last_fit"] = self.scorer.fitted_at.date().isoformat()
            self.state["n_train"] = self.scorer.n_train
        if ctx.execution.session_index % p["rebalance_every"] or self.scorer.model is None:
            return None
        return TargetWeights(_top(self.scorer.score(f), ctx.market, p["top_k"], p["gross"]), tag="ml")


class MomentumMLEnsemble(Strategy):
    id = "momentum_ml_ensemble"
    version = "1.0"
    name = "Momentum + ML ensemble (two models)"
    category = "ensemble"
    description = ("Combines two models: cross-sectional z-scores of 6-month momentum and of the walk-forward ridge "
                   "prediction, weighted w_momentum / (1 - w_momentum); holds the top_k combined scores.")
    params_schema = {
        "w_momentum": Param(0.5, "float", 0.0, 1.0),
        "horizon": Param(10, "int", 1, 63),
        "train_window": Param(250, "int", 20, 1000),
        "retrain_every": Param(21, "int", 1, 252),
        "alpha": Param(10.0, "float", 0.0, 1e6),
        "top_k": Param(10, "int", 1, 100),
        "rebalance_every": Param(10, "int", 1, 63),
        "gross": Param(1.0, "float", 0.05, 1.0),
    }

    def warmup(self) -> int:
        return max(132, FEATURE_BARS + self.params["train_window"] + self.params["horizon"] + 1)

    def initialize(self, ctx):
        p = self.params
        self.ridge = RidgeScorer(p["horizon"], p["train_window"], p["retrain_every"], p["alpha"])
        self.mom = MomentumScorer()
        self._warm = False

    def on_bar(self, ctx: StrategyContext):
        p = self.params
        if not self._warm:
            self._warm = True
            for ago in range(min(len(ctx.market.sessions) - 1, p["train_window"] + p["horizon"]), 0, -1):
                self.ridge.observe(ctx.market.past(ago), 1)
        f = self.ridge.observe(ctx.market, ctx.execution.session_index)
        if ctx.execution.session_index % p["rebalance_every"]:
            return None
        m, r = self.mom.score(ctx.market), self.ridge.score(f)
        if m.empty:
            return None
        zm = (m - m.mean()) / (m.std(ddof=0) or 1.0)
        combined = p["w_momentum"] * zm
        if not r.empty:
            zr = (r - r.mean()) / (r.std(ddof=0) or 1.0)
            combined = combined.add((1 - p["w_momentum"]) * zr, fill_value=0.0)
        self.state["models"] = dict(momentum=len(m), ridge=len(r), ridge_fit=bool(self.ridge.model is not None))
        return TargetWeights(_top(combined, ctx.market, p["top_k"], p["gross"]), tag="ensemble")


class ArtifactModel(Strategy):
    """Wraps an existing, already-trained model: a pickled object with predict(X) over FEATURES (z-scored per
    session). The file is checked against the sha256 in params before it is unpickled, so a run is tied to one exact
    model file. Only point it at files you trust: unpickling runs code."""
    id = "artifact_model"
    version = "1.0"
    name = "Existing ML model (pickled artifact)"
    category = "ml"
    description = ("Scores the universe each rebalance with a frozen, sha256-pinned model file (predict(X) on the "
                   "z-scored FEATURES r5, r21, r63, vol21, sma50_gap) and holds the top_k.")
    params_schema = {
        "artifact": Param("", "str", description="absolute path of the pickled model"),
        "sha256": Param("", "str", description="expected sha256 of the file"),
        "top_k": Param(10, "int", 1, 100),
        "rebalance_every": Param(5, "int", 1, 63),
        "gross": Param(1.0, "float", 0.05, 1.0),
    }

    def warmup(self) -> int:
        return FEATURE_BARS

    def initialize(self, ctx):
        path = Path(self.params["artifact"])
        if not path.is_file():
            raise FileNotFoundError(f"artifact_model: no file {path}")
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not self.params["sha256"] or digest != self.params["sha256"]:
            raise ValueError(f"artifact_model: {path.name} has sha256 {digest}, params say "
                             f"{self.params['sha256'] or '(none)'}; refusing to load it")
        self.model = pickle.loads(data)  # noqa: S301 - sha256-pinned file chosen by the user
        if not callable(getattr(self.model, "predict", None)):
            raise TypeError("artifact_model: the unpickled object has no predict()")

    def on_bar(self, ctx: StrategyContext):
        p = self.params
        if ctx.execution.session_index % p["rebalance_every"]:
            return None
        f = features_today(ctx.market)
        if f.empty:
            return None
        s = pd.Series(np.asarray(self.model.predict(zscore(f[list(FEATURES)]).to_numpy()), dtype=float), index=f.index)
        return TargetWeights(_top(s, ctx.market, p["top_k"], p["gross"]), tag="artifact")
