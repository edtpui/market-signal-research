"""The exit library: every exit is an independently testable component with a deterministic rule.

Kinds and their execution (the same in the trade replay and in the event-driven engine):
  condition  evaluated at the close of a session with data up to that close; when it is true the whole remaining
             position is sold at the next session's open (market order).
  stop       a protective price level, ratcheted: once set it never moves down (fmax with its previous value).
             trigger "intrabar": a resting stop order for the next session (fills at the level, or at the open when
             the bar gaps through it); trigger "close": a close at or below the level sells at the next open.
  target     a profit-taking level fixed at entry for `fraction` of the original position: a resting limit order
             (fills at the level, or at the open when the bar gaps above it). After it fills, `then` can raise the
             remaining position's stop floor to the entry price (breakeven) or to entry + lock_r x R (lock).

Stops and targets that depend on the entry price are linear in it: level = mult x entry fill price + add, with mult
and add known at the signal close (so the entry order can carry them as attached exits, core.types.AttachedExit).

R (initial risk per share) is entry price minus the plan's initial protective stop (the tightest stop component at
the signal close); a plan without a stop uses the common reference R = ref_atr_mult x ATR(ref_atr_n) at the signal
close, so R-multiples are defined for every exit and targets in R are comparable.

Every series a component reads is causal (rolling windows end at t, recursive averages, pivots confirmed only after
their right-hand bars): tests/test_entry_exit.py poisons future bars for every component.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from strategy_lab.core.strategy import Param, ParamError
from strategy_lab.signals.indicators import Indicators, cross_below, rising_edge

KINDS = ("condition", "stop", "target")
FAMILIES = {
    "indicator": ("Indicator-based exits", "Close-based exits when an indicator turns against the position."),
    "target": ("Fixed profit targets", "Resting limit orders at a level fixed at entry (R multiples, %, ATR, levels)."),
    "stop": ("Protective stop-losses", "A fixed protective level set at entry."),
    "trailing": ("Trailing exits", "Protective levels updated at every close; they only move up."),
    "price_action": ("Momentum and price-action exits", "Exits from candle sequences, swing structure and the "
                                                        "position's own path since entry."),
    "time": ("Time-based exits", "Exits after a holding period or when the trade thesis expires."),
    "calibration": ("Benchmark calibration", "Rules that know nothing (random-day sells) or know the future (a "
                                             "cheating rule): they check that a benchmark scores zero when there is "
                                             "no information and detects it when there is. Never tradable."),
}
# How an exit relates to the recent move (the 0RRXFUA-72M video's split): strength sells after a run-up or when the
# price is stretched above a band or overbought; weakness sells after a drop, a break below an average, a channel or
# a low. Stops, targets, time and position-path exits are classified separately.
STYLES = ("strength", "weakness", "neutral", "time", "protective", "target", "calibration")
# bearish catalog signals used as exit signals (signal_exit), by style
STRENGTH_SIGNALS = frozenset({
    "rsi14_above_70", "rsi14_above_80", "rsi14_cross_below_70", "rsi2_above_90", "rsi_bear_divergence",
    "stoch_overbought", "stoch_bear_cross_overbought", "willr_overbought", "willr_exit_overbought",
    "cci_overbought_reversal", "uo_overbought", "uo_cross_below_70", "mfi_overbought", "bb_upper_reentry",
    "keltner_upper_reentry", "zscore_above", "three_up_days", "ibs_high", "short_term_winner_decile",
    "dist_sma50_far_above", "consecutive_up_closes", "breadth_overheated", "mom_bear_divergence",
    "price_volume_divergence"})
WEAKNESS_SIGNALS = frozenset({
    "sma_10_20_cross_down", "sma_20_50_cross_down", "death_cross_50_200", "ema_9_21_cross_down", "sma50_break_below",
    "sma200_break_below", "breakdown_20d", "breakdown_52w", "donchian_breakdown", "keltner_breakdown",
    "donchian_mid_cross_down", "macd_signal_cross_down", "macd_zero_cross_down", "roc_cross_zero_down",
    "trix_zero_cross_down", "trix_signal_cross_down", "adx_trend_down", "di_cross_down", "aroon_cross_down",
    "aroon_down_strong", "psar_flip_down", "ichimoku_tk_cross_down", "ichimoku_cloud_breakdown",
    "linreg_strong_downtrend", "vortex_cross_down", "ha_flip_down", "mom_20d_bottom_decile", "mom_12_1_bottom_decile",
    "cci_breakdown", "rs_line_new_low", "rs_cross_below_sma", "rs_underperformance", "obv_cross_down",
    "cmf_cross_down", "gap_down", "gap_down_continuation", "gap_down_large_atr", "gap_down_rvol",
    "vol_breakout_down", "wide_range_bar_down", "chandelier_flip_down", "atr_expansion_down", "near_52w_low",
    "pivot_s1_breakdown", "bb_squeeze_breakout_down", "nr7_breakout_down", "squeeze_fire_down",
    "sma20_loss_after_uptrend", "inside_bar_breakdown_down", "inside_bar_breakout_down", "outside_bar_down",
    "consecutive_down_closes", "rvol_down_day", "laggard_in_strong_market"})


def signal_style(signal_id: str | None) -> str:
    if signal_id in STRENGTH_SIGNALS:
        return "strength"
    if signal_id in WEAKNESS_SIGNALS:
        return "weakness"
    return "neutral"
TRIGGER = Param("intrabar", "choice", choices=("intrabar", "close"),
                description="intrabar: a resting stop order; close: the close at or below the level sells at the "
                            "next open (market orders only)")
THEN = Param("none", "choice", choices=("none", "breakeven", "lock"),
             description="after this tranche fills: raise the remaining position's stop to the entry price "
                         "(breakeven) or to entry + lock_r x R (lock)")
ACTIVATION = Param("always", "choice", choices=("always", "after_r", "after_partial"),
                   description="always: from entry; after_r: once the high since entry reached activate_r x R; "
                               "after_partial: once a partial target has filled")


@dataclass
class Common:
    """Settings shared by every component of an experiment (and baked into an imported strategy)."""
    ref_atr_n: int = 14
    ref_atr_mult: float = 2.0
    has_high_low: bool = True


class Ctx:
    """Values for a set of positions (index array ix into the batch) at their current session. get(key, ix) returns
    an array: bar fields 'open' 'high' 'low' 'close', the reference ATR 'ref_atr', and component series
    '<uid>:<name>'. The replay reads arrays at (session_t, symbol) per trade; the live strategy reads the current
    row of its precomputed series, so it can never see a later session."""

    def __init__(self, getter: Callable[[str, np.ndarray], np.ndarray], ix: np.ndarray):
        self._g = getter
        self.ix = ix

    def __call__(self, key: str) -> np.ndarray:
        return self._g(key, self.ix)


class Comp:
    """Behaviour of one component instance. uid is its position in the plan ('c0', 'c1', ...). State arrays live in
    the plan's batch under '<uid>.<name>' (float64; 0/1 for flags), so they are JSON-serialisable per position."""
    kind = "condition"
    fields: tuple[str, ...] = ("close",)
    needs_benchmark = False
    state: tuple[str, ...] = ()

    def __init__(self, uid: str, p: dict, common: Common, extra: dict | None = None):
        self.uid, self.p, self.common, self.extra = uid, p, common, extra or {}
        self.check()

    def check(self) -> None:
        """Raises ParamError for an inconsistent parameter set."""

    def key(self, name: str) -> str:
        return f"{self.uid}:{name}"

    def st(self, name: str) -> str:
        return f"{self.uid}.{name}"

    def series(self, ind: Indicators) -> dict[str, pd.DataFrame]:
        return {}

    def warmup(self) -> int:
        return 1

    # lifecycle (vectorized over ix)
    def at_signal(self, c: Ctx, B: dict, ix: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        """At the signal close. Stops and targets return the linear terms (mult, add) of their level."""
        return None

    def observe(self, c: Ctx, B: dict, ix: np.ndarray) -> None:
        """At a close with a valid bar, after the plan's own path state (hh, ll, bars, ...) is updated."""

    def value(self, c: Ctx, B: dict, ix: np.ndarray) -> np.ndarray:
        """condition: bool array; stop: the candidate level (NaN = none); targets use their fixed level."""
        raise NotImplementedError


@dataclass(frozen=True)
class ExitDef:
    id: str
    family: str
    kind: str
    name: str
    rule: str
    impl: type
    params: dict[str, Param] = field(default_factory=dict)
    doc: str = ""
    version: str = "1.0"
    style: str = "neutral"
    lookahead: bool = False        # reads future bars on purpose (calibration only): never tradable

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9_]+", self.id):
            raise ValueError(f"exit id {self.id!r}: use lowercase letters, digits and _")
        if self.family not in FAMILIES or self.kind not in KINDS or self.style not in STYLES:
            raise ValueError(f"{self.id}: bad family/kind/style {self.family}/{self.kind}/{self.style}")

    def resolve_params(self, params: dict | None) -> dict:
        params = dict(params or {})
        unknown = set(params) - set(self.params)
        if unknown:
            raise ParamError(f"exit '{self.name}' ({self.id}) has no parameter {', '.join(sorted(unknown))} "
                             f"(its parameters: {', '.join(self.params) or 'none'})")
        try:
            return {k: spec.validate(k, params[k] if k in params else spec.default) for k, spec in self.params.items()}
        except ParamError as e:
            raise ParamError(f"exit '{self.name}' ({self.id}), parameter {e}") from None

    def build(self, uid: str, params: dict, common: Common, extra: dict | None = None) -> Comp:
        return self.impl(uid, self.resolve_params(params), common, extra)

    def describe(self) -> dict:
        p = self.resolve_params({})
        comp = self.build("c0", p, Common(), {"signal": None} if self.id == "signal_exit" else None)
        fields = sorted(set(comp.fields) | ({"open", "high", "low", "close"} if self.kind == "target" or
                                            (self.kind == "stop" and p.get("trigger") == "intrabar") else set()))
        return dict(id=self.id, version=self.version, family=self.family, kind=self.kind, name=self.name,
                    rule=self.rule, doc=self.doc, params={k: v.describe() for k, v in self.params.items()},
                    fields=fields, needs_benchmark=comp.needs_benchmark, warmup=comp.warmup(),
                    intrabar=self.kind == "target" or (self.kind == "stop" and p.get("trigger") == "intrabar"),
                    style=self.style, lookahead=self.lookahead, shiftable=isinstance(comp, _Panel))


class ExitRegistry:
    def __init__(self):
        self._defs: dict[str, ExitDef] = {}

    def register(self, d: ExitDef) -> None:
        if d.id in self._defs:
            raise ValueError(f"exit {d.id} is defined twice")
        self._defs[d.id] = d

    def get(self, exit_id: str) -> ExitDef:
        try:
            return self._defs[exit_id]
        except KeyError:
            raise KeyError(f"no exit {exit_id!r} (have {len(self._defs)}: see /api/v1/entry-exit/catalog)") from None

    def all(self) -> list[ExitDef]:
        return list(self._defs.values())

    def in_family(self, family: str) -> list[ExitDef]:
        if family not in FAMILIES:
            raise KeyError(f"no exit family {family!r} (have {sorted(FAMILIES)})")
        return [d for d in self._defs.values() if d.family == family]

    def describe(self) -> dict:
        counts: dict[str, int] = {}
        for d in self._defs.values():
            counts[d.family] = counts.get(d.family, 0) + 1
        return dict(families=[dict(id=k, name=v[0], description=v[1], n_exits=counts.get(k, 0))
                              for k, v in FAMILIES.items()],
                    exits=[d.describe() for d in self._defs.values()], n_exits=len(self._defs))


# ---------------------------------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------------------------------
def _f(x: pd.DataFrame) -> pd.DataFrame:
    """A boolean frame as floats (1 / 0) for the batch getters."""
    return x.fillna(False).astype(bool).astype(float)


def _on(c: Ctx, k: str) -> np.ndarray:
    v = c(k)
    return np.isfinite(v) & (v > 0.5)


def pivots(x: pd.DataFrame, w: int, high: bool) -> pd.DataFrame:
    """A swing point of width w confirmed at t: bar t-w is the extreme of bars t-2w..t (ties count). The value is
    x[t-w] on the confirming session t, NaN elsewhere: causal (it needs the w bars after the pivot)."""
    roll = x.rolling(2 * w + 1, min_periods=2 * w + 1)
    ext = roll.max() if high else roll.min()
    cand = x.shift(w)
    return cand.where(cand == ext)


def _ma(ind: Indicators, kind: str, n: int) -> pd.DataFrame:
    return ind.sma(n) if kind == "sma" else ind.ema(n)


class _Panel(Comp):
    """A position-independent condition: one causal boolean frame 'on'."""
    kind = "condition"

    def frame(self, ind: Indicators) -> pd.DataFrame:
        raise NotImplementedError

    def series(self, ind):
        return {self.key("on"): _f(self.frame(ind))}

    def value(self, c, B, ix):
        return _on(c, self.key("on"))


# ---------------------------------------------------------------------------------------------------------------------
# A. indicator-based exits (conditions)
# ---------------------------------------------------------------------------------------------------------------------
class RsiAbove(_Panel):
    def frame(self, ind):
        return ind.rsi(self.p["n"]) > self.p["level"]

    def warmup(self):
        return 5 * self.p["n"]


class RsiCrossBelow(_Panel):
    def frame(self, ind):
        return cross_below(ind.rsi(self.p["n"]), self.p["level"])

    def warmup(self):
        return 5 * self.p["n"]


class MacdBearish(_Panel):
    def check(self):
        if self.p["fast"] >= self.p["slow"]:
            raise ParamError("macd: fast must be below slow")

    def frame(self, ind):
        line, sig, _ = ind.macd(self.p["fast"], self.p["slow"], self.p["signal"])
        return cross_below(line, sig)

    def warmup(self):
        return 4 * self.p["slow"] + self.p["signal"]


class MaBearishCross(_Panel):
    def check(self):
        if self.p["fast"] >= self.p["slow"]:
            raise ParamError("moving average cross: fast must be below slow")

    def frame(self, ind):
        return cross_below(_ma(ind, self.p["ma"], self.p["fast"]), _ma(ind, self.p["ma"], self.p["slow"]))

    def warmup(self):
        return 4 * self.p["slow"]


class CloseBelowMa(_Panel):
    ma = "sma"

    def frame(self, ind):
        m = _ma(ind, self.ma, self.p["n"])
        return cross_below(ind.close, m) if self.p["mode"] == "cross" else (ind.close < m)

    def warmup(self):
        return 4 * self.p["n"]


class CloseBelowEma(CloseBelowMa):
    ma = "ema"


class BbUpperRejection(_Panel):
    def frame(self, ind):
        return cross_below(ind.close, ind.bbands(self.p["n"], self.p["k"])["upper"])

    def warmup(self):
        return self.p["n"] + 1


class MomentumDeterioration(_Panel):
    def frame(self, ind):
        return cross_below(ind.roc(self.p["n"]), 0.0)

    def warmup(self):
        return self.p["n"] + 2


class RsDeterioration(_Panel):
    needs_benchmark = True

    def frame(self, ind):
        rs = ind.rs_line()
        return cross_below(rs, rs.rolling(self.p["n"], min_periods=self.p["n"]).mean())

    def warmup(self):
        return self.p["n"] + 2


class VolatilityShock(_Panel):
    def frame(self, ind):
        r = ind.ret(1)
        sd = r.rolling(self.p["n"], min_periods=self.p["n"]).std(ddof=1).shift(1)
        return r <= -self.p["k"] * sd

    def warmup(self):
        return self.p["n"] + 2


class VolatilityExpansion(_Panel):
    def check(self):
        if self.p["short"] >= self.p["long"]:
            raise ParamError("volatility expansion: short must be below long")

    def frame(self, ind):
        with np.errstate(divide="ignore", invalid="ignore"):
            return rising_edge(ind.realized_vol(self.p["short"]) / ind.realized_vol(self.p["long"]) >= self.p["ratio"])

    def warmup(self):
        return self.p["long"] + 2


class SignalExit(_Panel):
    """Any catalog signal (or combination) as an exit trigger: the spec is passed as extra['signal']."""

    def check(self):
        self._r = None
        spec = self.extra.get("signal")
        if spec is None:
            return
        from strategy_lab.signals.base import resolve_spec
        from strategy_lab.signals.library import default_signal_registry
        self._r = resolve_spec(spec, default_signal_registry())
        self.fields = tuple(f for f in self._r.fields if f in ("open", "high", "low", "close", "volume")) or ("close",)
        self.needs_benchmark = self._r.needs_benchmark

    def frame(self, ind):
        if self._r is None:
            raise ParamError("signal_exit needs a signal spec")
        from strategy_lab.signals.base import evaluate
        return evaluate(self._r, ind)

    def warmup(self):
        return int(self._r.warmup) if self._r is not None else 1


class Supertrend(_Panel):
    """Supertrend (ATR bands around the bar midpoint, ratcheted while the trend lasts); the exit fires when the close
    crosses below the line, i.e. when the trend flips down."""
    fields = ("high", "low", "close")

    def frame(self, ind):
        n, m = self.p["n"], self.p["mult"]
        key = ("supertrend", n, m)

        def calc():
            h, lo, c = ind.high.to_numpy(float), ind.low.to_numpy(float), ind.close.to_numpy(float)
            atr = ind.atr(n).to_numpy(float)
            mid = (h + lo) / 2.0
            up, dn = mid + m * atr, mid - m * atr
            T, S = c.shape
            fu, fl = np.full(S, np.nan), np.full(S, np.nan)          # final upper / lower band
            trend = np.zeros(S)                                       # +1 up, -1 down, 0 unknown
            pc = np.full(S, np.nan)
            out = np.zeros((T, S), bool)
            for t in range(T):
                ok = np.isfinite(up[t]) & np.isfinite(dn[t]) & np.isfinite(c[t])
                with np.errstate(invalid="ignore"):
                    nfu = np.where(np.isnan(fu) | (up[t] < fu) | (pc > fu), up[t], fu)
                    nfl = np.where(np.isnan(fl) | (dn[t] > fl) | (pc < fl), dn[t], fl)
                    ntrend = np.where(trend == 0, np.where(c[t] >= nfl, 1.0, -1.0),
                                      np.where((trend > 0) & (c[t] < nfl), -1.0,
                                               np.where((trend < 0) & (c[t] > nfu), 1.0, trend)))
                out[t] = ok & (trend > 0) & (ntrend < 0)
                fu, fl = np.where(ok, nfu, fu), np.where(ok, nfl, fl)
                trend = np.where(ok, ntrend, trend)
                pc = np.where(ok, c[t], pc)
            return pd.DataFrame(out, index=ind.index, columns=ind.close.columns)
        return ind._memo(key, calc)

    def warmup(self):
        return 5 * self.p["n"]


class CloseAboveUpperBand(_Panel):
    def frame(self, ind):
        return ind.close > ind.bbands(self.p["n"], self.p["k"])["upper"]

    def warmup(self):
        return self.p["n"] + 1


class StochOverbought(_Panel):
    fields = ("high", "low", "close")

    def frame(self, ind):
        k, _ = ind.stoch(self.p["k"], self.p["smooth"], self.p["d"])
        return k > self.p["level"]

    def warmup(self):
        return self.p["k"] + self.p["smooth"] + self.p["d"]


class BreakdownLow(_Panel):
    def frame(self, ind):
        return ind.close < ind.prior_lowest(self.p["n"], "close")

    def warmup(self):
        return self.p["n"] + 1


class KeltnerBreakdown(_Panel):
    fields = ("high", "low", "close")

    def frame(self, ind):
        return cross_below(ind.close, ind.keltner(self.p["n"], self.p["mult"])["lower"])

    def warmup(self):
        return 5 * self.p["n"]


class PlaceboRandom(_Panel):
    """Calibration: a sell signal on random days (independent of every price), probability `prob` per stock and
    session, reproducible from `seed`. Any benchmark must score it at zero."""
    def frame(self, ind):
        rng = np.random.default_rng(int(self.p["seed"]))
        a = rng.random(ind.close.shape) < self.p["prob"]
        return pd.DataFrame(a, index=ind.index, columns=ind.close.columns)


class OracleDrop(_Panel):
    """Calibration only, reads the future on purpose: sells when the NEXT session's close is more than `drop` below
    today's. A benchmark must detect it. Refused by every tradable strategy."""
    def frame(self, ind):
        nxt = ind.close.shift(-1) / ind.close - 1.0
        return nxt < -self.p["drop"]


# ---------------------------------------------------------------------------------------------------------------------
# E. momentum and price-action exits (conditions; several depend on the position's path since entry)
# ---------------------------------------------------------------------------------------------------------------------
class FirstBearishAfterMomentum(Comp):
    fields = ("open", "close")
    state = ("armed", "fire")

    def observe(self, c, B, ix):
        bearish = c("close") < c("open")
        armed = B[self.st("armed")][ix] > 0.5
        B[self.st("fire")][ix] = (armed & bearish).astype(float)
        if self.p["momentum"] == "up_closes":
            now = B["up_run"][ix] >= self.p["n"]
        else:
            now = B["mfe_r"][ix] >= self.p["min_r"]
        B[self.st("armed")][ix] = (armed | now).astype(float)

    def value(self, c, B, ix):
        return B[self.st("fire")][ix] > 0.5



class ConsecutiveBearish(Comp):
    fields = ("open", "close")
    state = ("run",)

    def observe(self, c, B, ix):
        bearish = c("close") < c("open")
        run = np.nan_to_num(B[self.st("run")][ix])
        B[self.st("run")][ix] = np.where(bearish, run + 1, 0.0)

    def value(self, c, B, ix):
        return np.nan_to_num(B[self.st("run")][ix]) >= self.p["n"]


class LossOfMomentum(Comp):
    state = ("rmax", "now")

    def series(self, ind):
        return {self.key("roc"): ind.roc(self.p["n"])}

    def warmup(self):
        return self.p["n"] + 1

    def observe(self, c, B, ix):
        roc = c(self.key("roc"))
        B[self.st("rmax")][ix] = np.fmax(B[self.st("rmax")][ix], roc)
        B[self.st("now")][ix] = roc

    def value(self, c, B, ix):
        roc, mx = B[self.st("now")][ix], B[self.st("rmax")][ix]
        with np.errstate(invalid="ignore"):
            return np.isfinite(roc) & np.isfinite(mx) & (roc <= mx - self.p["pts"])



class NoNewHigh(Comp):
    fields = ("high", "close")

    def value(self, c, B, ix):
        return B["hh_age"][ix] >= self.p["m"]


class LowerHigh(_Panel):
    fields = ("high", "close")

    def frame(self, ind):
        ph = pivots(ind.high, self.p["w"], high=True)
        prev = ph.ffill().shift(1)
        return ph.notna() & (ph < prev)

    def warmup(self):
        return 4 * self.p["w"] + 2


class StructureBreak(_Panel):
    fields = ("low", "close")

    def frame(self, ind):
        lp = pivots(ind.low, self.p["w"], high=False).ffill()
        return cross_below(ind.close, lp) if self.p["mode"] == "cross" else (ind.close < lp)

    def warmup(self):
        return 2 * self.p["w"] + 2


class ResistanceReversal(_Panel):
    fields = ("open", "high", "close")

    def frame(self, ind):
        level = ind.prior_highest(self.p["n"], "high")
        return (ind.high >= level * (1 - self.p["tolerance"])) & (ind.close < level) & (ind.close < ind.open)

    def warmup(self):
        return self.p["n"] + 1


class FailedBreakout(Comp):
    state = ("level",)

    def series(self, ind):
        return {self.key("level"): ind.prior_highest(self.p["n"], "close")}

    def warmup(self):
        return self.p["n"] + 1

    def at_signal(self, c, B, ix):
        B[self.st("level")][ix] = c(self.key("level"))
        return None

    def value(self, c, B, ix):
        lvl = B[self.st("level")][ix]
        within = (B["bars"][ix] <= self.p["k"]) if self.p["k"] > 0 else np.ones(len(ix), bool)
        with np.errstate(invalid="ignore"):
            return np.isfinite(lvl) & (c("close") < lvl) & within


class FailedContinuation(Comp):
    state = ("armed",)

    def observe(self, c, B, ix):
        B[self.st("armed")][ix] = ((B[self.st("armed")][ix] > 0.5) | (B["mfe_r"][ix] >= self.p["min_r"])).astype(float)

    def value(self, c, B, ix):
        return (B[self.st("armed")][ix] > 0.5) & (c("close") < B["P"][ix])


PATTERNS = ("bearish_engulfing", "shooting_star", "evening_star", "dark_cloud_cover", "three_black_crows",
            "bearish_harami", "key_reversal_down", "outside_bar_down")


class BearishPattern(_Panel):
    fields = ("open", "high", "low", "close")

    def frame(self, ind):
        from strategy_lab.signals.base import SignalSpec, evaluate, resolve_spec
        from strategy_lab.signals.library import default_signal_registry
        reg = default_signal_registry()
        ids = PATTERNS if self.p["pattern"] == "any" else (self.p["pattern"],)
        out = None
        for i in ids:
            m = evaluate(resolve_spec(SignalSpec(id=i), reg), ind)
            out = m if out is None else out | m
        return out

    def warmup(self):
        return 30


# ---------------------------------------------------------------------------------------------------------------------
# F. time-based exits (conditions)
# ---------------------------------------------------------------------------------------------------------------------
class TimeExit(Comp):
    def value(self, c, B, ix):
        return B["bars"][ix] >= self.p["n"] - 1


class ThesisExpiry(Comp):
    state = ("checked", "fire")

    def observe(self, c, B, ix):
        due = (B["bars"][ix] >= self.p["n"] - 1) & (B[self.st("checked")][ix] < 0.5)
        with np.errstate(invalid="ignore", divide="ignore"):
            open_r = (c("close") - B["P"][ix]) / B["R"][ix]
        B[self.st("fire")][ix] = (due & ~(open_r >= self.p["min_r"])).astype(float)
        B[self.st("checked")][ix] = np.where(due, 1.0, B[self.st("checked")][ix])

    def value(self, c, B, ix):
        return B[self.st("fire")][ix] > 0.5


# ---------------------------------------------------------------------------------------------------------------------
# B. profit targets (fixed at entry)
# ---------------------------------------------------------------------------------------------------------------------
class _Target(Comp):
    kind = "target"
    fields = ("open", "high", "low", "close")

    def value(self, c, B, ix):
        return B[self.st("level")][ix]


class TargetR(_Target):
    def at_signal(self, c, B, ix):
        k = self.p["k"]
        return 1.0 + k * B["Rm"][ix], k * B["Ra"][ix]


class TargetPct(_Target):
    def at_signal(self, c, B, ix):
        return np.full(len(ix), 1.0 + self.p["pct"]), np.zeros(len(ix))


class TargetAtr(_Target):
    fields = ("high", "low", "close")
    def series(self, ind):
        return {self.key("atr"): ind.atr(self.p["n"])}

    def warmup(self):
        return 5 * self.p["n"]

    def at_signal(self, c, B, ix):
        return np.ones(len(ix)), self.p["k"] * c(self.key("atr"))


class TargetLevel(_Target):
    def series(self, ind):
        lv = self.p["level"]
        if lv == "prior_high":
            s = ind.highest(self.p["n"], "high")
        elif lv == "bb_upper":
            s = ind.bbands(self.p["n"], 2.0)["upper"]
        elif lv == "pivot_r1":
            s = 2.0 * ind.typical() - ind.low
        else:
            s = pd.DataFrame(self.p["price"], index=ind.index, columns=ind.close.columns)
        return {self.key("level"): s}

    def warmup(self):
        return self.p["n"] + 1

    def at_signal(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("level"))


# ---------------------------------------------------------------------------------------------------------------------
# C. protective stops (fixed at entry) and D. trailing exits (updated at each close, never lowered)
# ---------------------------------------------------------------------------------------------------------------------
class _Stop(Comp):
    """arm: a trailing level counts only once a close is above it (at the signal close or later); until then it is
    not a stop yet. Without this, a trail whose level starts above the price (a 22-session-high Chandelier or an
    EMA above a pullback entry) would sell every such trade at the next open. Fixed stops are taken literally: a
    fixed level at or above the entry price stops the trade out at once (counted as stop_breach)."""
    kind = "stop"
    trailing = False
    arm = False
    state = ("armed",)

    @property
    def intrabar(self) -> bool:
        return self.p.get("trigger", "intrabar") == "intrabar"

    def active(self, B, ix) -> np.ndarray:
        a = self.p.get("activation", "always")
        if a == "after_r":
            return B["mfe_r"][ix] >= self.p["activate_r"]
        if a == "after_partial":
            return B["partial"][ix] > 0.5
        return np.ones(len(ix), bool)

    def initial(self, c, B, ix):
        """(mult, add) of the level at entry; NaN when the stop only starts later."""
        raise NotImplementedError

    def at_signal(self, c, B, ix):
        if self.p.get("activation", "always") != "always":
            n = len(ix)
            return np.full(n, np.nan), np.full(n, np.nan)
        return self.initial(c, B, ix)

    def candidate(self, c, B, ix) -> np.ndarray:
        """The level implied by data up to this close (fixed stops: NaN, their level is kept)."""
        return np.full(len(ix), np.nan)

    def value(self, c, B, ix):
        cand = self.candidate(c, B, ix)
        return np.where(self.active(B, ix), cand, np.nan)


class StopPct(_Stop):
    def initial(self, c, B, ix):
        return np.full(len(ix), 1.0 - self.p["pct"]), np.zeros(len(ix))


class StopDollar(_Stop):
    def initial(self, c, B, ix):
        return np.ones(len(ix)), np.full(len(ix), -self.p["amount"])


class StopAtr(_Stop):
    fields = ("high", "low", "close")
    def series(self, ind):
        return {self.key("atr"): ind.atr(self.p["n"])}

    def warmup(self):
        return 5 * self.p["n"]

    def initial(self, c, B, ix):
        return np.ones(len(ix)), -self.p["k"] * c(self.key("atr"))


class StopSwingLow(_Stop):
    fields = ("low", "close")

    def series(self, ind):
        return {self.key("ll"): ind.lowest(self.p["n"], "low")}

    def warmup(self):
        return self.p["n"]

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("ll")) * (1 - self.p["buffer"])


class StopCandleLow(_Stop):
    fields = ("low", "close")

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c("low") * (1 - self.p["buffer"])


class StopMa(_Stop):
    def series(self, ind):
        return {self.key("ma"): _ma(ind, self.p["ma"], self.p["n"])}

    def warmup(self):
        return 4 * self.p["n"]

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("ma")) * (1 - self.p["buffer"])


class StopVolatility(_Stop):
    def series(self, ind):
        return {self.key("sd"): ind.ret(1).rolling(self.p["n"], min_periods=self.p["n"]).std(ddof=1)}

    def warmup(self):
        return self.p["n"] + 1

    def initial(self, c, B, ix):
        return 1.0 - self.p["k"] * c(self.key("sd")), np.zeros(len(ix))


class StopStructure(_Stop):
    fields = ("low", "close")

    def series(self, ind):
        return {self.key("lp"): pivots(ind.low, self.p["w"], high=False).ffill()}

    def warmup(self):
        return 2 * self.p["w"] + 1

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("lp")) * (1 - self.p["buffer"])


class TrailPct(_Stop):
    trailing = True
    arm = True

    def initial(self, c, B, ix):
        return np.full(len(ix), 1.0 - self.p["pct"]), np.zeros(len(ix))

    def candidate(self, c, B, ix):
        base = B["hh"][ix] if self.p["basis"] == "high" else B["hc"][ix]
        return base * (1 - self.p["pct"])


class TrailAtr(_Stop):
    trailing = True
    arm = True
    fields = ("high", "low", "close")

    def series(self, ind):
        return {self.key("atr"): ind.atr(self.p["n"])}

    def warmup(self):
        return 5 * self.p["n"]

    def initial(self, c, B, ix):
        return np.ones(len(ix)), -self.p["k"] * c(self.key("atr"))

    def candidate(self, c, B, ix):
        base = B["hh"][ix] if self.p["basis"] == "high" else B["hc"][ix]
        return base - self.p["k"] * c(self.key("atr"))


class Chandelier(_Stop):
    trailing = True
    arm = True
    fields = ("high", "low", "close")

    def series(self, ind):
        n = self.p["n"]
        return {self.key("lvl"): ind.highest(n, "high") - self.p["k"] * ind.atr(n)}

    def warmup(self):
        return 5 * self.p["n"]

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("lvl"))

    def candidate(self, c, B, ix):
        return c(self.key("lvl"))


class TrailMa(_Stop):
    trailing = True
    arm = True
    ma = "sma"

    def series(self, ind):
        return {self.key("ma"): _ma(ind, self.ma, self.p["n"])}

    def warmup(self):
        return 4 * self.p["n"]

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("ma"))

    def candidate(self, c, B, ix):
        return c(self.key("ma"))


class TrailEma(TrailMa):
    ma = "ema"


class TrailPriorLow(_Stop):
    trailing = True
    arm = True
    fields = ("low", "close")

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c("low")

    def candidate(self, c, B, ix):
        return c("low")


class TrailSwingLow(_Stop):
    trailing = True
    arm = True
    fields = ("low", "close")

    def series(self, ind):
        return {self.key("lp"): pivots(ind.low, self.p["w"], high=False).ffill()}

    def warmup(self):
        return 2 * self.p["w"] + 1

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("lp"))

    def candidate(self, c, B, ix):
        return c(self.key("lp"))


class Donchian(_Stop):
    trailing = True
    arm = True
    fields = ("low", "close")

    def series(self, ind):
        return {self.key("ll"): ind.lowest(self.p["n"], "low")}

    def warmup(self):
        return self.p["n"]

    def initial(self, c, B, ix):
        return np.zeros(len(ix)), c(self.key("ll"))

    def candidate(self, c, B, ix):
        return c(self.key("ll"))


class ProfitLock(_Stop):
    trailing = True

    def at_signal(self, c, B, ix):
        n = len(ix)
        return np.full(n, np.nan), np.full(n, np.nan)

    def candidate(self, c, B, ix):
        P, R = B["P"][ix], B["R"][ix]
        on = B["mfe_r"][ix] >= self.p["activate_r"]
        lvl = P + self.p["lock_r"] * R
        if self.p["trail_r"] > 0:
            lvl = np.fmax(lvl, B["hh"][ix] - self.p["trail_r"] * R)
        return np.where(on, lvl, np.nan)

    def value(self, c, B, ix):
        return self.candidate(c, B, ix)


class Breakeven(ProfitLock):
    def candidate(self, c, B, ix):
        on = B["mfe_r"][ix] >= self.p["activate_r"]
        return np.where(on, B["P"][ix] + self.p["offset_r"] * B["R"][ix], np.nan)


# ---------------------------------------------------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------------------------------------------------
def I(d, lo, hi, desc=""):  # noqa: E743
    return Param(d, "int", lo, hi, description=desc)


def Fl(d, lo, hi, desc=""):
    return Param(d, "float", lo, hi, description=desc)


def C(d, choices, desc=""):
    return Param(d, "choice", choices=tuple(choices), description=desc)


def _target_params(**extra) -> dict:
    return dict(**extra, fraction=Fl(1.0, 0.05, 1.0, "share of the original position this target sells"),
                then=THEN, lock_r=Fl(0.0, 0.0, 10.0, "with then=lock: the stop floor in R above entry"))


def _stop_params(trigger="intrabar", trailing=False, **extra) -> dict:
    out = dict(**extra, trigger=Param(trigger, "choice", choices=("intrabar", "close"), description=TRIGGER.description))
    if trailing:
        out.update(activation=ACTIVATION, activate_r=Fl(1.0, 0.0, 20.0, "with activation=after_r"))
    return out


def build_exit_registry() -> ExitRegistry:
    reg = ExitRegistry()
    a = reg.register
    # A. indicator-based
    a(ExitDef("rsi_overbought", "indicator", "condition", "RSI overbought", "RSI(n) > level at the close",
              RsiAbove, dict(n=I(14, 2, 100), level=Fl(70.0, 50.0, 99.0))))
    a(ExitDef("rsi_cross_below", "indicator", "condition", "RSI crosses below a threshold",
              "RSI(n) crosses below level (above or at it on the previous close, below it now)", RsiCrossBelow,
              dict(n=I(14, 2, 100), level=Fl(70.0, 10.0, 95.0))))
    a(ExitDef("macd_bearish_cross", "indicator", "condition", "MACD bearish crossover",
              "MACD line (EMA fast - EMA slow) crosses below its signal EMA", MacdBearish,
              dict(fast=I(12, 2, 100), slow=I(26, 3, 200), signal=I(9, 2, 50))))
    a(ExitDef("ma_bearish_cross", "indicator", "condition", "Moving-average bearish crossover",
              "MA(fast) crosses below MA(slow)", MaBearishCross,
              dict(fast=I(10, 2, 200), slow=I(20, 3, 400), ma=C("sma", ("sma", "ema")))))
    a(ExitDef("close_below_sma", "indicator", "condition", "Price crosses below SMA",
              "close crosses below SMA(n) (mode cross) or is below it (mode below)", CloseBelowMa,
              dict(n=I(50, 2, 400), mode=C("cross", ("cross", "below")))))
    a(ExitDef("close_below_ema", "indicator", "condition", "Price crosses below EMA",
              "close crosses below EMA(n) (mode cross) or is below it (mode below)", CloseBelowEma,
              dict(n=I(20, 2, 400), mode=C("cross", ("cross", "below")))))
    a(ExitDef("bb_upper_rejection", "indicator", "condition", "Bollinger upper-band rejection",
              "close falls back below the upper band (SMA(n) + k sd) after closing at or above it", BbUpperRejection,
              dict(n=I(20, 5, 200), k=Fl(2.0, 0.5, 4.0))))
    a(ExitDef("momentum_deterioration", "indicator", "condition", "Momentum deterioration",
              "ROC(n) crosses below 0", MomentumDeterioration, dict(n=I(10, 2, 252))))
    a(ExitDef("rs_deterioration", "indicator", "condition", "Relative-strength deterioration",
              "relative-strength line (close / benchmark close) crosses below its SMA(n)", RsDeterioration,
              dict(n=I(50, 5, 252))))
    a(ExitDef("volatility_shock", "indicator", "condition", "Volatility shock",
              "daily return <= -k x the standard deviation of the previous n daily returns", VolatilityShock,
              dict(n=I(20, 5, 252), k=Fl(2.5, 0.5, 10.0))))
    a(ExitDef("volatility_expansion", "indicator", "condition", "Volatility expansion",
              "realized volatility RV(short) / RV(long) reaches ratio (first session)", VolatilityExpansion,
              dict(short=I(10, 2, 100), long=I(60, 5, 252), ratio=Fl(1.5, 1.0, 5.0))))
    a(ExitDef("death_cross", "indicator", "condition", "Death cross", "SMA(50) crosses below SMA(200)",
              MaBearishCross, dict(fast=I(50, 2, 200), slow=I(200, 3, 400), ma=C("sma", ("sma", "ema")))))
    a(ExitDef("close_below_sma200", "indicator", "condition", "Close below the 200-day average",
              "close crosses below SMA(200) (mode cross) or is below it (mode below)", CloseBelowMa,
              dict(n=I(200, 2, 400), mode=C("cross", ("cross", "below")))))
    a(ExitDef("supertrend_exit", "indicator", "condition", "Supertrend flip",
              "the close crosses below the Supertrend line (midpoint -/+ mult x ATR(n), ratcheted while the trend "
              "lasts): the trend turns down", Supertrend, dict(n=I(10, 2, 100), mult=Fl(3.0, 0.5, 10.0))))
    a(ExitDef("bb_upper_close", "indicator", "condition", "Close above the upper Bollinger band",
              "close above SMA(n) + k sd (selling into strength)", CloseAboveUpperBand,
              dict(n=I(20, 5, 200), k=Fl(2.0, 0.5, 4.0))))
    a(ExitDef("stoch_overbought", "indicator", "condition", "Stochastic overbought",
              "slow %K above level", StochOverbought,
              dict(k=I(14, 2, 100), smooth=I(3, 1, 20), d=I(3, 1, 20), level=Fl(80.0, 50.0, 99.0))))
    a(ExitDef("breakdown_low", "indicator", "condition", "Break below the n-session low",
              "close below the lowest close of the n sessions before", BreakdownLow, dict(n=I(20, 2, 504))))
    a(ExitDef("breakdown_20d_low", "indicator", "condition", "Break below the 20-day low",
              "close below the lowest close of the 20 sessions before", BreakdownLow, dict(n=I(20, 2, 504))))
    a(ExitDef("breakdown_52w_low", "indicator", "condition", "Break below the 52-week low",
              "close below the lowest close of the 252 sessions before", BreakdownLow, dict(n=I(252, 2, 504))))
    a(ExitDef("keltner_breakdown", "indicator", "condition", "Keltner channel breakdown",
              "close crosses below EMA(n) - mult x ATR(n)", KeltnerBreakdown, dict(n=I(20, 2, 200), mult=Fl(2.0, 0.5, 6.0))))
    a(ExitDef("placebo_random_exit", "calibration", "condition", "Placebo: random-day sell",
              "sells on random sessions with probability prob (no price information)", PlaceboRandom,
              dict(prob=Fl(0.03, 0.0005, 0.5), seed=I(1, 0, 2**31 - 1)), style="calibration"))
    a(ExitDef("oracle_next_day_drop", "calibration", "condition", "Cheating rule: knows tomorrow's drop",
              "sells when the NEXT close is more than drop below today's (reads the future: calibration only)",
              OracleDrop, dict(drop=Fl(0.03, 0.001, 0.5)), style="calibration", lookahead=True))
    a(ExitDef("signal_exit", "indicator", "condition", "Any catalog signal",
              "a Signal Research catalog signal or combination (usually a bearish one) fires", SignalExit, {},
              doc="The component spec carries the signal spec; every bearish catalog signal is an exit signal."))
    # E. momentum and price action
    a(ExitDef("first_bearish_after_momentum", "price_action", "condition", "First bearish candle after momentum",
              "once momentum is established (n consecutive higher closes since entry, or the high since entry at "
              ">= min_r x R), the first later bar closing below its open", FirstBearishAfterMomentum,
              dict(momentum=C("up_closes", ("up_closes", "mfe_r")), n=I(3, 1, 20), min_r=Fl(1.0, 0.1, 20.0))))
    a(ExitDef("consecutive_bearish", "price_action", "condition", "Consecutive bearish candles",
              "n consecutive bars closing below their open since entry", ConsecutiveBearish, dict(n=I(3, 1, 10))))
    a(ExitDef("loss_of_momentum", "price_action", "condition", "Loss of momentum",
              "ROC(n) has fallen at least pts points below its maximum since entry", LossOfMomentum,
              dict(n=I(10, 2, 252), pts=Fl(5.0, 0.1, 100.0))))
    a(ExitDef("no_new_high", "price_action", "condition", "Failure to make a new high",
              "no new high since entry for m sessions", NoNewHigh, dict(m=I(10, 1, 252))))
    a(ExitDef("lower_high", "price_action", "condition", "Lower high",
              "a newly confirmed swing high (pivot with w bars on each side) is below the previous swing high",
              LowerHigh, dict(w=I(3, 1, 20))))
    a(ExitDef("structure_break", "price_action", "condition", "Break of market structure",
              "close crosses below (or is below) the most recent confirmed swing low (pivot width w)",
              StructureBreak, dict(w=I(3, 1, 20), mode=C("cross", ("cross", "below")))))
    a(ExitDef("resistance_reversal", "price_action", "condition", "Reversal at resistance",
              "high reaches the prior n-session high (within tolerance), close below that level and below the open",
              ResistanceReversal, dict(n=I(20, 5, 252), tolerance=Fl(0.005, 0.0, 0.05))))
    a(ExitDef("failed_breakout", "price_action", "condition", "Failure to hold a breakout",
              "close falls below the breakout level (highest close of the n sessions before the signal session) "
              "within k sessions of entry (k = 0: at any time)", FailedBreakout,
              dict(n=I(20, 2, 252), k=I(5, 0, 252))))
    a(ExitDef("failed_continuation", "price_action", "condition", "Failed continuation",
              "after the high since entry reached min_r x R, a close below the entry price", FailedContinuation,
              dict(min_r=Fl(1.0, 0.1, 20.0))))
    a(ExitDef("bearish_reversal_pattern", "price_action", "condition", "Bearish reversal pattern",
              "a bearish catalog pattern fires (one, or any of: " + ", ".join(PATTERNS) + ")", BearishPattern,
              dict(pattern=C("any", ("any",) + PATTERNS))))
    # F. time
    a(ExitDef("time_exit", "time", "condition", "Fixed holding period",
              "exit at the open after n sessions in the position (entry session = session 0)", TimeExit,
              dict(n=I(20, 1, 504))))
    for n in (1, 5, 10, 20, 60):
        a(ExitDef(f"time_{n}d", "time", "condition", f"Exit after {n} trading day{'s' if n > 1 else ''}",
                  f"exit at the open after {n} session{'s' if n > 1 else ''} in the position", TimeExit,
                  dict(n=I(n, 1, 504))))
    a(ExitDef("thesis_expiry", "time", "condition", "Thesis expiry",
              "after n sessions, exit unless the close is at least min_r x R above entry (checked once)",
              ThesisExpiry, dict(n=I(10, 1, 252), min_r=Fl(0.5, -5.0, 20.0))))
    # B. targets
    for k, lab in ((1.0, "1"), (1.5, "1_5"), (2.0, "2"), (3.0, "3"), (4.0, "4"), (5.0, "5")):
        a(ExitDef(f"target_{lab}r", "target", "target", f"{lab.replace('_', '.')}R target",
                  f"limit at entry + {k:g} x R", TargetR, _target_params(k=Fl(k, 0.1, 50.0))))
    a(ExitDef("target_r", "target", "target", "R-multiple target", "limit at entry + k x R", TargetR,
              _target_params(k=Fl(2.0, 0.1, 50.0))))
    a(ExitDef("target_pct", "target", "target", "Percentage take-profit", "limit at entry x (1 + pct)", TargetPct,
              _target_params(pct=Fl(0.10, 0.001, 10.0))))
    a(ExitDef("target_atr", "target", "target", "ATR take-profit", "limit at entry + k x ATR(n) of the signal session",
              TargetAtr, _target_params(n=I(14, 2, 100), k=Fl(3.0, 0.1, 50.0))))
    a(ExitDef("target_level", "target", "target", "Price-level target",
              "limit at a level fixed at the signal close: the n-session high, the upper Bollinger band (n, 2), "
              "pivot R1, or a fixed price", TargetLevel,
              _target_params(level=C("prior_high", ("prior_high", "bb_upper", "pivot_r1", "price")),
                             n=I(20, 2, 252), price=Fl(0.0, 0.0, 1e7))))
    # C. protective stops
    a(ExitDef("stop_pct", "stop", "stop", "Fixed percentage stop", "stop at entry x (1 - pct)", StopPct,
              _stop_params(pct=Fl(0.05, 0.001, 0.9))))
    a(ExitDef("stop_dollar", "stop", "stop", "Fixed dollar stop", "stop at entry - amount (dollars per share)",
              StopDollar, _stop_params(amount=Fl(2.0, 0.01, 10000.0))))
    a(ExitDef("stop_atr", "stop", "stop", "ATR stop", "stop at entry - k x ATR(n) of the signal session", StopAtr,
              _stop_params(n=I(14, 2, 100), k=Fl(2.0, 0.1, 20.0))))
    a(ExitDef("stop_swing_low", "stop", "stop", "Previous swing-low stop",
              "stop at the lowest low of the n sessions up to the signal session, less buffer", StopSwingLow,
              _stop_params(n=I(10, 2, 252), buffer=Fl(0.0, 0.0, 0.2))))
    a(ExitDef("stop_candle_low", "stop", "stop", "Previous candle-low stop",
              "stop at the signal session's low, less buffer", StopCandleLow, _stop_params(buffer=Fl(0.0, 0.0, 0.2))))
    a(ExitDef("stop_ma", "stop", "stop", "Moving-average stop", "stop at MA(n) of the signal session, less buffer",
              StopMa, _stop_params(n=I(50, 2, 400), ma=C("sma", ("sma", "ema")), buffer=Fl(0.0, 0.0, 0.2))))
    a(ExitDef("stop_volatility", "stop", "stop", "Volatility-adjusted stop",
              "stop at entry x (1 - k x sd of the n daily returns up to the signal session)", StopVolatility,
              _stop_params(n=I(20, 5, 252), k=Fl(2.0, 0.1, 20.0))))
    a(ExitDef("stop_structure", "stop", "stop", "Structure-based stop",
              "stop at the most recent confirmed swing low (pivot width w) before entry, less buffer", StopStructure,
              _stop_params(w=I(3, 1, 20), buffer=Fl(0.0, 0.0, 0.2))))
    # D. trailing
    a(ExitDef("trail_pct", "trailing", "stop", "Percentage trailing stop",
              "stop at the highest high (or close) since entry x (1 - pct); entry x (1 - pct) at entry", TrailPct,
              _stop_params(trailing=True, pct=Fl(0.10, 0.001, 0.9), basis=C("high", ("high", "close")))))
    a(ExitDef("trail_atr", "trailing", "stop", "ATR trailing stop",
              "stop at the highest close (or high) since entry - k x ATR(n) of the latest close", TrailAtr,
              _stop_params(trailing=True, n=I(14, 2, 100), k=Fl(3.0, 0.1, 20.0), basis=C("close", ("close", "high")))))
    a(ExitDef("chandelier", "trailing", "stop", "Chandelier Exit",
              "stop at the highest high of the last n sessions - k x ATR(n)", Chandelier,
              _stop_params(trailing=True, n=I(22, 2, 252), k=Fl(3.0, 0.1, 20.0))))
    a(ExitDef("trail_sma", "trailing", "stop", "Moving-average trailing exit", "stop at SMA(n) of the latest close",
              TrailMa, _stop_params(trigger="close", trailing=True, n=I(20, 2, 400))))
    a(ExitDef("trail_ema", "trailing", "stop", "EMA trailing exit", "stop at EMA(n) of the latest close", TrailEma,
              _stop_params(trigger="close", trailing=True, n=I(20, 2, 400))))
    a(ExitDef("trail_prior_low", "trailing", "stop", "Prior-bar low exit", "stop at the low of the latest bar",
              TrailPriorLow, _stop_params(trailing=True)))
    a(ExitDef("trail_swing_low", "trailing", "stop", "Trailing swing-low exit",
              "stop at the most recent confirmed swing low (pivot width w)", TrailSwingLow,
              _stop_params(trailing=True, w=I(3, 1, 20))))
    a(ExitDef("donchian_exit", "trailing", "stop", "Donchian channel exit",
              "stop at the lowest low of the last n sessions (the lower Donchian channel)", Donchian,
              _stop_params(trailing=True, n=I(20, 2, 252))))
    a(ExitDef("profit_lock", "trailing", "stop", "Profit-locking trailing stop",
              "once the high since entry reaches activate_r x R: stop at entry + lock_r x R, and (trail_r > 0) at "
              "the high since entry - trail_r x R if higher", ProfitLock,
              dict(activate_r=Fl(1.0, 0.0, 50.0), lock_r=Fl(0.5, -5.0, 50.0), trail_r=Fl(0.0, 0.0, 50.0),
                   trigger=TRIGGER)))
    a(ExitDef("breakeven_stop", "trailing", "stop", "Breakeven stop",
              "once the high since entry reaches activate_r x R: stop at entry + offset_r x R", Breakeven,
              dict(activate_r=Fl(1.0, 0.0, 50.0), offset_r=Fl(0.0, -5.0, 50.0), trigger=TRIGGER)))
    return reg


STYLE_OF = {
    # strength: sells into a run-up or a stretched price
    "rsi_overbought": "strength", "rsi_cross_below": "strength", "bb_upper_rejection": "strength",
    "bb_upper_close": "strength", "stoch_overbought": "strength", "first_bearish_after_momentum": "strength",
    "resistance_reversal": "strength",
    # weakness: sells after a drop or a break below an average, a channel or a low
    "macd_bearish_cross": "weakness", "ma_bearish_cross": "weakness", "death_cross": "weakness",
    "close_below_sma": "weakness", "close_below_sma200": "weakness", "close_below_ema": "weakness",
    "momentum_deterioration": "weakness", "rs_deterioration": "weakness", "volatility_shock": "weakness",
    "supertrend_exit": "weakness", "breakdown_low": "weakness", "breakdown_20d_low": "weakness",
    "breakdown_52w_low": "weakness", "keltner_breakdown": "weakness", "lower_high": "weakness",
    "structure_break": "weakness", "consecutive_bearish": "weakness", "loss_of_momentum": "weakness",
    "failed_breakout": "weakness", "failed_continuation": "weakness", "bearish_reversal_pattern": "neutral",
    "volatility_expansion": "neutral", "no_new_high": "neutral",
    "placebo_random_exit": "calibration", "oracle_next_day_drop": "calibration",
}


def _style(d: ExitDef) -> str:
    if d.id in STYLE_OF:
        return STYLE_OF[d.id]
    if d.kind == "target":
        return "target"
    if d.family in ("stop", "trailing"):
        return "protective"
    if d.family == "time":
        return "time"
    return "neutral"


_registry: ExitRegistry | None = None


def default_exit_registry() -> ExitRegistry:
    global _registry
    if _registry is None:
        reg = build_exit_registry()
        styled = ExitRegistry()
        for d in reg.all():
            styled.register(d if d.style != "neutral" or _style(d) == "neutral" else
                            ExitDef(**{**d.__dict__, "style": _style(d)}))
        _registry = styled
    return _registry


__all__ = ["Common", "Ctx", "Comp", "ExitDef", "ExitRegistry", "build_exit_registry", "default_exit_registry",
           "FAMILIES", "KINDS", "pivots"]
