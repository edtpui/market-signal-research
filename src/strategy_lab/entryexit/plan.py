"""Exit plans: one or more exit components with deterministic trigger semantics, and the vectorized position runtime
that both the trade replay (entryexit/replay.py) and the event-driven strategy (strategies/entry_exit.py) drive.

Combination semantics
  conditions        combined by condition_mode: or (any), and (all), k_of_n (at least k), weighted (sum of weights of
                    the true conditions >= threshold). They are suppressed until min_bars sessions are held.
  stops             always protective and combined as the tightest level: the effective intrabar stop is the maximum
                    of the intrabar stop components (and of the floor set by partial exits); every close-triggered
                    stop sells on its own (OR).
  targets           each target is its own tranche (a fraction of the original position) and fills independently.
  max_hold          the experiment's holding cap applies to every plan: exit at the open after max_hold sessions.

Same-session priority (the order of intrabar events on a daily bar is unknowable, so the conservative order is used):
  1. a market exit decided at the previous close fills at the open (before anything else that session);
  2. the protective stop (at the open if the bar gaps through it, else at the level);
  3. targets, in plan order, only for what the stop left (if a stop and a target are both reached in one bar, the
     stop wins: worst case);
  4. on the entry session the stop and targets are attached to the entry and evaluated after the entry fill at the
     open, in the same order (core.types.AttachedExit).
The recorded exit reason is the first firing component in this order (component order within a group).

State per position is a dict of float arrays (one value per position; flags 0/1), so the live strategy can keep it
in its JSON state and the replay can hold a million trades in the same layout.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal, Union

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from strategy_lab.core.strategy import ParamError, canonical_hash
from strategy_lab.entryexit.components import Common, Ctx, ExitRegistry, default_exit_registry
from strategy_lab.signals.base import CombinationSpec, SignalSpec
from strategy_lab.signals.indicators import Indicators

LIBRARY_VERSION = "1.0"
SignalItem = Annotated[Union[SignalSpec, CombinationSpec], Field(discriminator="kind")]

# reason codes (component index >= 0)
R_NONE, R_MAX_HOLD, R_PERIOD_END, R_FLOOR, R_COMBINED, R_DATA_END = -9, -1, -2, -3, -4, -6
SPECIAL_REASONS = {R_MAX_HOLD: "max_hold", R_PERIOD_END: "period_end", R_FLOOR: "stop_floor",
                   R_COMBINED: "conditions", R_DATA_END: "data_end", R_NONE: "none"}


class ExitComponentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    params: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(1.0, ge=-10, le=10, description="for condition_mode weighted")
    signal: SignalItem | None = Field(None, description="for signal_exit: the catalog signal or combination")
    label: str = Field("", max_length=120)


class ExitPlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["plan"] = "plan"
    name: str = Field("", max_length=120)
    components: list[ExitComponentSpec] = Field(min_length=1, max_length=10)
    condition_mode: Literal["or", "and", "k_of_n", "weighted"] = "or"
    k: int | None = Field(None, ge=1, le=10)
    threshold: float | None = None
    min_bars: int = Field(0, ge=0, le=252, description="conditions are suppressed until this many sessions are held")
    label: str = Field("", max_length=120)

    @model_validator(mode="after")
    def _check(self):
        if self.condition_mode == "k_of_n" and self.k is None:
            raise ValueError("k_of_n needs k")
        if self.condition_mode == "weighted" and self.threshold is None:
            raise ValueError("weighted needs a threshold")
        return self


def single(exit_id: str, **params) -> ExitPlanSpec:
    return ExitPlanSpec(components=[ExitComponentSpec(id=exit_id, params=params)])


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48] or "plan"


@dataclass
class ResolvedPlan:
    spec: ExitPlanSpec
    key: str
    name: str
    family: str
    families: list[str]
    kinds: list[str]
    components: list[dict]
    fields: tuple[str, ...]
    intrabar: bool
    needs_benchmark: bool
    trigger: str
    deployable: bool
    problems: list[str] = field(default_factory=list)

    def describe(self) -> dict:
        return dict(key=self.key, name=self.name, family=self.family, families=self.families, kinds=self.kinds,
                    components=self.components, fields=list(self.fields), intrabar=self.intrabar,
                    needs_benchmark=self.needs_benchmark, trigger=self.trigger, deployable=self.deployable,
                    deploy_problems=self.problems, spec=self.spec.model_dump(mode="json"))


def resolve_plan(spec: ExitPlanSpec, registry: ExitRegistry | None = None) -> ResolvedPlan:
    reg = registry or default_exit_registry()
    comps, fams, kinds, fields, parts = [], [], [], set(), []
    intrabar = needs_bench = False
    for c in spec.components:
        d = reg.get(c.id)
        p = d.resolve_params(c.params)
        if d.id == "signal_exit" and c.signal is None:
            raise ParamError("signal_exit needs a signal")
        if d.id != "signal_exit" and c.signal is not None:
            raise ParamError(f"{d.id}: only signal_exit takes a signal")
        comp = d.build("c0", p, Common(), {"signal": c.signal} if d.id == "signal_exit" else None)
        sig = None
        if c.signal is not None:
            from strategy_lab.signals.base import resolve_spec
            from strategy_lab.signals.library import default_signal_registry
            sig = resolve_spec(c.signal, default_signal_registry()).key
        ib = d.kind == "target" or (d.kind == "stop" and p.get("trigger", "intrabar") == "intrabar")
        intrabar |= ib
        needs_bench |= bool(comp.needs_benchmark)
        f = set(comp.fields) | ({"open", "high", "low", "close"} if ib else set())
        fields |= f
        changed = {k: v for k, v in p.items() if v != d.params[k].default}
        shown = c.label or d.name + (" (" + ", ".join(f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
                                                      for k, v in changed.items()) + ")" if changed else "")
        if sig:
            shown += f" [{sig.split('@')[0]}]"
        comps.append(dict(id=d.id, version=d.version, family=d.family, kind=d.kind, name=shown,
                          params=p, weight=c.weight, signal=sig, intrabar=ib, rule=d.rule))
        fams.append(d.family)
        kinds.append(d.kind)
        label = d.id + ("(" + ", ".join(f"{k}={v}" for k, v in changed.items()) + ")" if changed else "")
        parts.append(label + (f"[{sig}]" if sig else ""))
    ident = dict(library=LIBRARY_VERSION, mode=spec.condition_mode, k=spec.k, threshold=spec.threshold,
                 min_bars=spec.min_bars,
                 components=[dict(id=c["id"], version=c["version"], params=c["params"], weight=c["weight"],
                                  signal=c["signal"]) for c in comps])
    h = canonical_hash(ident)[:8]
    name = spec.label or spec.name or " + ".join(c["name"] for c in comps)
    key = f"exit_{_slug(spec.name or '_'.join(parts))}@{LIBRARY_VERSION}:{h}"
    family = fams[0] if len(set(fams)) == 1 else "combined"
    conds = [p for p, k in zip(parts, kinds) if k == "condition"]
    joiner = {"or": " OR ", "and": " AND "}.get(spec.condition_mode, ", ")
    head = {"k_of_n": f"at least {spec.k} of: ", "weighted": f"weighted score >= {spec.threshold}: "}.get(
        spec.condition_mode, "")
    trig = []
    if conds:
        trig.append(f"conditions: {head}{joiner.join(conds)}" + (f" (after {spec.min_bars} sessions)"
                                                                  if spec.min_bars else "") + " -> next open")
    stops = [p for p, k in zip(parts, kinds) if k == "stop"]
    if stops:
        trig.append("protective stop: tightest of " + ", ".join(stops))
    tg = [p for p, k in zip(parts, kinds) if k == "target"]
    if tg:
        trig.append("targets: " + ", ".join(tg))
    problems = []
    if intrabar:
        problems.append("uses intrabar stop or limit orders (needs high/low bars and stop/limit orders)")
    if not fields <= {"open", "close"}:
        problems.append(f"reads bar fields {sorted(fields - {'open', 'close'})}")
    if needs_bench:
        problems.append("reads a benchmark series")
    return ResolvedPlan(spec=spec, key=key, name=name, family=family, families=sorted(set(fams)),
                        kinds=sorted(set(kinds)), components=comps, fields=tuple(sorted(fields | {"open", "close"})),
                        intrabar=intrabar, needs_benchmark=needs_bench, trigger="; ".join(trig),
                        deployable=not problems, problems=problems)


@dataclass
class Decision:
    exit_now: np.ndarray        # bool: sell everything at the next open
    reason: np.ndarray          # int reason code for exit_now
    stop: np.ndarray            # float: the effective intrabar stop for the next session (NaN: none)
    stop_reason: np.ndarray     # int: the component (or R_FLOOR) that sets it
    targets: list[tuple[int, np.ndarray, np.ndarray]]   # (component index, level, fraction of the original)


BASE_KEYS = ("P", "R", "Rown", "Rref", "Rm", "Ra", "Rhat", "S0", "ibm", "iba", "sig_close", "pc", "hh", "ll", "hc",
             "hh_age", "up_run", "mfe_r", "bars", "rem", "floor", "partial", "valid", "breach")


class PlanRuntime:
    """Vectorized lifecycle of positions managed by one plan."""

    def __init__(self, plan: ResolvedPlan, common: Common, max_hold: int | None = None,
                 registry: ExitRegistry | None = None):
        reg = registry or default_exit_registry()
        # the path state (high/low since entry) reads high and low only when the plan does; a close-only plan
        # tracks its path with closes, so it runs on open/close bars (the ptrade host)
        common = Common(common.ref_atr_n, common.ref_atr_mult, has_high_low=bool({"high", "low"} & set(plan.fields)))
        self.plan, self.common, self.max_hold = plan, common, max_hold
        self.comps = []
        for k, c in enumerate(plan.spec.components):
            d = reg.get(c.id)
            self.comps.append(d.build(f"c{k}", c.params, common, {"signal": c.signal} if c.id == "signal_exit"
                                      else None))
        self.kinds = [c.kind for c in self.comps]
        self.cond = [k for k, c in enumerate(self.comps) if c.kind == "condition"]
        self.stops = [k for k, c in enumerate(self.comps) if c.kind == "stop"]
        self.targets = [k for k, c in enumerate(self.comps) if c.kind == "target"]
        self.weights = [plan.spec.components[k].weight for k in self.cond]
        self.intrabar = plan.intrabar

    # -- data -----------------------------------------------------------------------------------------------------
    def series(self, ind) -> dict[str, pd.DataFrame]:
        n = self.common.ref_atr_n
        if self.common.has_high_low:
            ref = ind.atr(n)
        else:      # open/close bars only: Wilder average of the absolute close-to-close change
            ref = Indicators.wilder(ind.close.diff().abs(), n)
        out = {"ref_atr": ref}
        for c in self.comps:
            out.update(c.series(ind))
        return out

    def warmup(self) -> int:
        return int(max([c.warmup() for c in self.comps] + [5 * self.common.ref_atr_n]))

    def keys(self) -> list[str]:
        ks = list(BASE_KEYS)
        for k, c in enumerate(self.comps):
            ks += [c.st(s) for s in c.state]
            if c.kind in ("stop", "target"):
                ks += [c.st("m"), c.st("a"), c.st("level")]
            if c.kind == "target":
                ks += [c.st("filled"), c.st("frac")]
        return list(dict.fromkeys(ks))

    def new_batch(self, n: int) -> dict[str, np.ndarray]:
        B = {k: np.full(n, np.nan) for k in self.keys()}
        for k in ("hh_age", "up_run", "mfe_r", "bars", "rem", "partial", "valid", "breach", "Rown"):
            B[k][:] = 0.0
        for c in self.comps:
            for s in c.state:
                if s in ("armed", "fire", "checked", "run"):
                    B[c.st(s)][:] = 0.0
            if c.kind == "target":
                B[c.st("filled")][:] = 0.0
        return B

    # -- the signal close -----------------------------------------------------------------------------------------
    def at_signal(self, c: Ctx, B: dict, ix: np.ndarray) -> None:
        """Entry-time references, the stop and target terms, R terms and the sizing risk estimate Rhat."""
        cl = c("close")
        ref = c("ref_atr") * self.common.ref_atr_mult
        n = len(ix)
        B["Rref"][ix], B["sig_close"][ix], B["pc"][ix] = ref, cl, cl
        with np.errstate(invalid="ignore"):
            valid = np.isfinite(cl) & (cl > 0) & np.isfinite(ref) & (ref > 0)
        best, bm, ba = np.full(n, -np.inf), np.full(n, np.nan), np.full(n, np.nan)
        ibest, im, ia = np.full(n, -np.inf), np.full(n, np.nan), np.full(n, np.nan)
        for k in self.stops:
            comp = self.comps[k]
            m, a = comp.at_signal(c, B, ix)
            m, a = np.broadcast_to(m, (n,)).astype(float), np.broadcast_to(a, (n,)).astype(float)
            if comp.arm:                          # a trail starts only below the price (components._Stop)
                with np.errstate(invalid="ignore"):
                    armed = m * cl + a < cl
                m, a = np.where(armed, m, np.nan), np.where(armed, a, np.nan)
                B[comp.st("armed")][ix] = armed.astype(float)
            B[comp.st("m")][ix], B[comp.st("a")][ix] = m, a
            est = m * cl + a
            with np.errstate(invalid="ignore"):
                better = np.isfinite(est) & (est > best)
            best, bm, ba = np.where(better, est, best), np.where(better, m, bm), np.where(better, a, ba)
            if comp.intrabar:
                with np.errstate(invalid="ignore"):
                    ib = np.isfinite(est) & (est > ibest)
                ibest, im, ia = np.where(ib, est, ibest), np.where(ib, m, im), np.where(ib, a, ia)
        with np.errstate(invalid="ignore"):
            own = np.isfinite(bm) & (cl - best > 0)
        B["Rm"][ix] = np.where(own, 1.0 - bm, 0.0)
        B["Ra"][ix] = np.where(own, -ba, ref)
        B["Rown"][ix] = own.astype(float)
        B["Rhat"][ix] = B["Rm"][ix] * cl + B["Ra"][ix]
        B["ibm"][ix], B["iba"][ix] = im, ia
        for k in self.targets:
            comp = self.comps[k]
            m, a = comp.at_signal(c, B, ix)
            m, a = np.broadcast_to(m, (n,)).astype(float), np.broadcast_to(a, (n,)).astype(float)
            with np.errstate(invalid="ignore"):
                ok = np.isfinite(m) & np.isfinite(a) & (m * cl + a > cl)   # a target at or below the close is off
            B[comp.st("m")][ix] = np.where(ok, m, np.nan)
            B[comp.st("a")][ix] = np.where(ok, a, np.nan)
            B[comp.st("frac")][ix] = comp.p["fraction"]
            B[comp.st("filled")][ix] = np.where(ok, 0.0, 1.0)
        for k in self.cond:
            self.comps[k].at_signal(c, B, ix)
        B["valid"][ix] = valid.astype(float)

    def legs(self, B: dict, i: int) -> list[tuple[str, float, float, float, str]]:
        """Attached exits of position i's entry order: (order type, fraction, mult, add, tag), stop first."""
        out = []
        if np.isfinite(B["ibm"][i]) and np.isfinite(B["iba"][i]):
            out.append(("stop", 1.0, float(B["ibm"][i]), float(B["iba"][i]), "stop"))
        for k in self.targets:
            comp = self.comps[k]
            m, a = B[comp.st("m")][i], B[comp.st("a")][i]
            if np.isfinite(m) and np.isfinite(a):
                out.append(("limit", float(B[comp.st("frac")][i]), float(m), float(a), f"target:{k}"))
        return out

    # -- the entry fill -------------------------------------------------------------------------------------------
    def at_fill(self, B: dict, ix: np.ndarray, P: np.ndarray) -> None:
        B["P"][ix] = P
        s0 = np.full(len(ix), np.nan)
        for k in self.stops:
            comp = self.comps[k]
            lvl = B[comp.st("m")][ix] * P + B[comp.st("a")][ix]
            B[comp.st("level")][ix] = lvl
            s0 = np.fmax(s0, lvl)
        for k in self.targets:
            comp = self.comps[k]
            B[comp.st("level")][ix] = B[comp.st("m")][ix] * P + B[comp.st("a")][ix]
        r_own = B["Rm"][ix] * P + B["Ra"][ix]
        with np.errstate(invalid="ignore"):
            ok = np.isfinite(r_own) & (r_own > 0)
        own = (B["Rown"][ix] > 0.5) & ok
        with np.errstate(invalid="ignore"):
            at_or_above = np.isfinite(s0) & (s0 >= P)         # the protective level is already reached at entry
        B["breach"][ix] = (((B["Rown"][ix] > 0.5) & ~ok) | at_or_above).astype(float)
        B["R"][ix] = np.where(ok, r_own, B["Rref"][ix])
        B["Rown"][ix] = own.astype(float)
        B["S0"][ix] = s0
        B["rem"][ix] = 1.0
        B["bars"][ix] = 0.0

    # -- each close with a valid bar ------------------------------------------------------------------------------
    def observe(self, c: Ctx, B: dict, ix: np.ndarray) -> None:
        cl = c("close")
        hi = c("high") if self.common.has_high_low else cl
        lo = c("low") if self.common.has_high_low else cl
        hh = B["hh"][ix]
        with np.errstate(invalid="ignore", divide="ignore"):
            new_high = ~(hi <= hh)
            B["hh_age"][ix] = np.where(new_high, 0.0, B["hh_age"][ix] + 1)
            B["hh"][ix] = np.fmax(hh, hi)
            B["ll"][ix] = np.fmin(B["ll"][ix], lo)
            B["hc"][ix] = np.fmax(B["hc"][ix], cl)
            up = cl > B["pc"][ix]
            B["up_run"][ix] = np.where(up, B["up_run"][ix] + 1, 0.0)
            B["pc"][ix] = cl
            B["mfe_r"][ix] = (B["hh"][ix] - B["P"][ix]) / B["R"][ix]
        for comp in self.comps:
            comp.observe(c, B, ix)
        for k in self.stops:
            comp = self.comps[k]
            cand = comp.value(c, B, ix)
            if comp.arm:
                with np.errstate(invalid="ignore"):
                    armed = (B[comp.st("armed")][ix] > 0.5) | (cand < cl)
                B[comp.st("armed")][ix] = armed.astype(float)
                cand = np.where(armed, cand, np.nan)
            B[comp.st("level")][ix] = np.fmax(B[comp.st("level")][ix], cand)

    def decide(self, c: Ctx, B: dict, ix: np.ndarray) -> Decision:
        n = len(ix)
        cl = c("close")
        reason = np.full(n, R_NONE, dtype=np.int64)
        exit_now = np.zeros(n, bool)

        def mark(hit, code):
            nonlocal exit_now
            new = hit & ~exit_now
            reason[new] = code
            exit_now = exit_now | hit

        floor = B["floor"][ix]
        for k in self.stops:                                   # protective close-triggered stops first
            comp = self.comps[k]
            if not comp.intrabar:
                with np.errstate(invalid="ignore"):
                    mark(cl <= B[comp.st("level")][ix], k)
        if not self.intrabar:
            with np.errstate(invalid="ignore"):
                mark(cl <= floor, R_FLOOR)
        if self.cond:
            active = B["bars"][ix] >= self.plan.spec.min_bars
            vals = [np.asarray(self.comps[k].value(c, B, ix), bool) for k in self.cond]
            mode = self.plan.spec.condition_mode
            if mode == "or":
                for k, v in zip(self.cond, vals):
                    mark(v & active, k)
            else:
                if mode == "and":
                    fire = np.logical_and.reduce(vals)
                elif mode == "k_of_n":
                    fire = np.sum(vals, axis=0) >= self.plan.spec.k
                else:
                    fire = np.sum([w * v for w, v in zip(self.weights, vals)], axis=0) >= self.plan.spec.threshold
                mark(fire & active, R_COMBINED if len(self.cond) > 1 else self.cond[0])
        if self.max_hold:
            mark(B["bars"][ix] >= self.max_hold - 1, R_MAX_HOLD)
        stop = np.full(n, np.nan)
        sreason = np.full(n, R_NONE, dtype=np.int64)
        for k in self.stops:
            comp = self.comps[k]
            if comp.intrabar:
                lvl = B[comp.st("level")][ix]
                with np.errstate(invalid="ignore"):
                    up = np.isfinite(lvl) & ~(lvl <= stop)
                stop, sreason = np.where(up, lvl, stop), np.where(up, k, sreason)
        if self.intrabar:
            with np.errstate(invalid="ignore"):
                up = np.isfinite(floor) & ~(floor <= stop)
            stop, sreason = np.where(up, floor, stop), np.where(up, R_FLOOR, sreason)
        with np.errstate(invalid="ignore"):
            none = ~(stop > 0)            # a level at or below zero (e.g. entry - 3 ATR on a very volatile stock)
        stop, sreason = np.where(none, np.nan, stop), np.where(none, R_NONE, sreason)    # can never fill: no stop
        targets = []
        rem = B["rem"][ix]
        for k in self.targets:
            comp = self.comps[k]
            open_ = B[comp.st("filled")][ix] < 0.5
            frac = np.where(open_, np.minimum(B[comp.st("frac")][ix], rem), 0.0)
            targets.append((k, B[comp.st("level")][ix], frac))
        return Decision(exit_now=exit_now, reason=reason, stop=stop, stop_reason=sreason, targets=targets)

    # -- fills ----------------------------------------------------------------------------------------------------
    def tranche_filled(self, B: dict, ix: np.ndarray, k: int, frac: np.ndarray) -> None:
        comp = self.comps[k]
        B["rem"][ix] = np.maximum(B["rem"][ix] - frac, 0.0)
        B[comp.st("filled")][ix] = 1.0
        B["partial"][ix] = 1.0
        then = comp.p.get("then", "none")
        if then != "none":
            lock = 0.0 if then == "breakeven" else comp.p.get("lock_r", 0.0)
            B["floor"][ix] = np.fmax(B["floor"][ix], B["P"][ix] + lock * B["R"][ix])

    def reason_label(self, code: int) -> str:
        if code >= 0:
            c = self.plan.components[code]
            return f"{c['kind']}:{c['id']}"
        return SPECIAL_REASONS.get(int(code), str(code))


def plan_hash(plan: ResolvedPlan) -> str:
    return hashlib.sha256(plan.key.encode()).hexdigest()[:10]


__all__ = ["ExitComponentSpec", "ExitPlanSpec", "ResolvedPlan", "resolve_plan", "PlanRuntime", "Decision", "single",
           "SPECIAL_REASONS", "R_NONE", "R_MAX_HOLD", "R_PERIOD_END", "R_FLOOR", "R_COMBINED", "R_DATA_END",
           "LIBRARY_VERSION"]
