"""Signal definitions, the signal registry, and the specs an experiment (or an imported strategy) uses to name a signal
instance: one catalog signal with parameters, or a combination of several.

A SignalDef is a pure function of a Panel's indicators: fn(ind, params) -> boolean frame (sessions x symbols), or a
boolean Series for market-level signals (broadcast to every symbol). evaluate() is the only way signals are computed,
so the event-study engine and the imported-signal strategy see identical triggers:

  1. the definition's frame (or the combination of its components' frames),
  2. AND a regime filter, when the spec has one,
  3. AND every bar field the signal reads is present and valid on t (a missing bar never triggers).
"""
from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

from strategy_lab.core.strategy import Param, ParamError, canonical_hash
from strategy_lab.signals.indicators import Indicators, recently, rising_edge

DIRECTIONS = ("bullish", "bearish")
SCOPES = ("stock", "cross_section", "market")


@dataclass(frozen=True)
class Family:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class SignalDef:
    """One independently testable signal.

    direction: the hypothesis being tested (bullish: higher subsequent returns than comparable stocks; bearish: lower).
    scope: stock (from the symbol's own bars), cross_section (ranks the symbol against the panel's universe on t),
    market (one value per session for every symbol: matched-stock controls cannot test it, see docs/SIGNAL_RESEARCH.md).
    warmup: sessions of history needed before the first possible trigger (historical availability requirement).
    """
    id: str
    family: str
    name: str
    direction: str
    fn: Callable[[Indicators, dict], pd.DataFrame | pd.Series]
    trigger: str
    doc: str = ""
    params: dict[str, Param] = field(default_factory=dict)
    fields: tuple[str, ...] = ("close",)
    warmup_fn: Callable[[dict], int] | None = None
    version: str = "1.0"
    scope: str = "stock"
    needs_benchmark: bool = False
    show: Callable[[Indicators, dict], dict[str, pd.DataFrame | pd.Series]] | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9_]+", self.id):
            raise ValueError(f"signal id {self.id!r}: use lowercase letters, digits and _")
        if self.direction not in DIRECTIONS:
            raise ValueError(f"{self.id}: direction must be one of {DIRECTIONS}")
        if self.scope not in SCOPES:
            raise ValueError(f"{self.id}: scope must be one of {SCOPES}")
        if not self.trigger:
            raise ValueError(f"{self.id}: needs a trigger definition")

    def resolve_params(self, params: dict | None) -> dict[str, Any]:
        params = dict(params or {})
        unknown = set(params) - set(self.params)
        if unknown:
            raise ParamError(f"signal {self.id}: unknown parameters {sorted(unknown)}")
        return {k: spec.validate(k, params[k] if k in params else spec.default) for k, spec in self.params.items()}

    def warmup(self, params: dict) -> int:
        return int(self.warmup_fn(params)) if self.warmup_fn else 1

    def source_sha256(self) -> str:
        try:
            return hashlib.sha256(Path(inspect.getfile(self.fn)).read_bytes()).hexdigest()
        except (TypeError, OSError):
            return hashlib.sha256(inspect.getsource(self.fn).encode()).hexdigest()

    def source(self) -> str:
        try:
            return inspect.getsource(self.fn)
        except (TypeError, OSError):
            return ""

    def describe(self) -> dict:
        defaults = self.resolve_params({})
        return dict(id=self.id, version=self.version, family=self.family, name=self.name, direction=self.direction,
                    scope=self.scope, trigger=self.trigger, doc=self.doc, fields=list(self.fields),
                    needs_benchmark=self.needs_benchmark, params={k: v.describe() for k, v in self.params.items()},
                    warmup=self.warmup(defaults), has_diagnostics=self.show is not None,
                    source_sha256=self.source_sha256())


class SignalNotFound(KeyError):
    pass


class SignalRegistry:
    """Keyed by (id, version); the latest registered version is the default."""

    def __init__(self):
        self._defs: dict[tuple[str, str], SignalDef] = {}
        self._order: list[tuple[str, str]] = []
        self.families: dict[str, Family] = {}

    def add_family(self, fam: Family) -> None:
        if fam.id in self.families and self.families[fam.id] != fam:
            raise ValueError(f"family {fam.id} defined twice")
        self.families[fam.id] = fam

    def register(self, d: SignalDef) -> None:
        if d.family not in self.families:
            raise ValueError(f"{d.id}: unknown family {d.family!r}")
        key = (d.id, d.version)
        if key in self._defs:
            raise ValueError(f"signal {d.id} version {d.version} is defined twice")
        self._defs[key] = d
        self._order.append(key)

    def get(self, signal_id: str, version: str | None = None) -> SignalDef:
        if version:
            try:
                return self._defs[(signal_id, version)]
            except KeyError:
                have = [v for i, v in self._order if i == signal_id]
                raise SignalNotFound(f"signal {signal_id!r} version {version!r} is not available "
                                     f"(versions: {have or 'none'})") from None
        keys = [k for k in self._order if k[0] == signal_id]
        if not keys:
            raise SignalNotFound(f"no signal {signal_id!r}")
        return self._defs[keys[-1]]

    def all(self) -> list[SignalDef]:
        latest: dict[str, tuple[str, str]] = {}
        for k in self._order:
            latest[k[0]] = k
        return [self._defs[k] for k in self._order if latest[k[0]] == k]

    def in_family(self, family: str) -> list[SignalDef]:
        if family not in self.families:
            raise SignalNotFound(f"no signal family {family!r} (have {sorted(self.families)})")
        return [d for d in self.all() if d.family == family]

    def describe(self) -> dict:
        defs = self.all()
        counts: dict[str, int] = {}
        for d in defs:
            counts[d.family] = counts.get(d.family, 0) + 1
        return dict(families=[dict(id=f.id, name=f.name, description=f.description, n_signals=counts.get(f.id, 0))
                              for f in self.families.values()],
                    signals=[d.describe() for d in defs], n_signals=len(defs), n_families=len(self.families))


# -- specs: which signal instance an experiment or strategy uses ------------------------------------------------------
class RegimeFilter(BaseModel):
    """Keep triggers only in a market or stock-volatility regime, known at t. market_trend: the benchmark close
    against its 200-session SMA. stock_vol: the symbol's trailing realized volatility tercile across the panel."""
    model_config = ConfigDict(extra="forbid")
    market_trend: Literal["any", "up", "down"] = "any"
    stock_vol: Literal["any", "low", "mid", "high"] = "any"
    vol_window: int = Field(63, ge=10, le=252)

    def active(self) -> bool:
        return self.market_trend != "any" or self.stock_vol != "any"


class SignalSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["signal"] = "signal"
    id: str
    version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    regime: RegimeFilter | None = None
    label: str = Field("", max_length=120)


class ComponentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    weight: float = Field(1.0, ge=-10, le=10)
    negate: bool = False


class CombinationSpec(BaseModel):
    """Several catalog signals combined into one trigger. A component is active on t when it triggered on any of the
    last `window` sessions (window 1 = the same session). mode and: all active; or: any; k_of_n: at least k;
    weighted: sum of weight x active >= threshold. negate turns a component into "not active" (a confirmation that
    an opposite signal is absent). edge keeps only the first session of each run of the combined condition."""
    model_config = ConfigDict(extra="forbid")
    kind: Literal["combination"] = "combination"
    name: str = Field(min_length=1, max_length=80)
    direction: Literal["bullish", "bearish"]
    mode: Literal["and", "or", "k_of_n", "weighted"] = "and"
    components: list[ComponentSpec] = Field(min_length=2, max_length=8)
    k: int | None = Field(None, ge=1, le=8)
    threshold: float | None = None
    window: int = Field(1, ge=1, le=20)
    edge: bool = True
    regime: RegimeFilter | None = None
    label: str = Field("", max_length=120)

    @model_validator(mode="after")
    def _check(self):
        if self.mode == "k_of_n" and (self.k is None or self.k > len(self.components)):
            raise ValueError("k_of_n needs 1 <= k <= number of components")
        if self.mode == "weighted" and self.threshold is None:
            raise ValueError("weighted needs a threshold")
        return self


AnySpec = SignalSpec | CombinationSpec


@dataclass
class Resolved:
    """A spec with its definition(s) and parameters resolved: everything the engine and the reports need."""
    spec: AnySpec
    key: str
    id: str
    version: str
    family: str
    name: str
    direction: str
    scope: str
    fields: tuple[str, ...]
    warmup: int
    needs_benchmark: bool
    params: dict
    source_sha256: str
    trigger: str
    components: list[tuple[SignalDef, dict, ComponentSpec]] = field(default_factory=list)
    definition: SignalDef | None = None

    def describe(self) -> dict:
        return dict(key=self.key, kind=self.spec.kind, id=self.id, version=self.version, family=self.family,
                    name=self.name, direction=self.direction, scope=self.scope, fields=list(self.fields),
                    warmup=self.warmup, needs_benchmark=self.needs_benchmark, params=self.params,
                    source_sha256=self.source_sha256, trigger=self.trigger,
                    spec=self.spec.model_dump(mode="json"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40] or "combo"


def resolve_spec(spec: AnySpec, registry: SignalRegistry) -> Resolved:
    regime = spec.regime.model_dump() if spec.regime is not None and spec.regime.active() else None
    if isinstance(spec, SignalSpec):
        d = registry.get(spec.id, spec.version)
        params = d.resolve_params(spec.params)
        ident = dict(id=d.id, version=d.version, params=params, regime=regime)
        key = f"{d.id}@{d.version}:{canonical_hash(ident)[:8]}"
        vw = spec.regime.vol_window if regime else 0
        return Resolved(spec=spec, key=key, id=d.id, version=d.version, family=d.family,
                        name=spec.label or d.name, direction=d.direction, scope=d.scope, fields=tuple(d.fields),
                        warmup=max(d.warmup(params), vw + 1, 201 if regime and regime["market_trend"] != "any" else 0),
                        needs_benchmark=d.needs_benchmark or bool(regime and regime["market_trend"] != "any"),
                        params=params, source_sha256=d.source_sha256(), trigger=d.trigger, definition=d)
    comps = []
    for c in spec.components:
        d = registry.get(c.id, c.version)
        comps.append((d, d.resolve_params(c.params), c))
    ident = dict(name=spec.name, direction=spec.direction, mode=spec.mode, k=spec.k, threshold=spec.threshold,
                 window=spec.window, edge=spec.edge, regime=regime,
                 components=[dict(id=d.id, version=d.version, params=p, weight=c.weight, negate=c.negate)
                             for d, p, c in comps])
    h = canonical_hash(ident)
    key = f"combo_{_slug(spec.name)}@1.0:{h[:8]}"
    fields = tuple(sorted({f for d, _, _ in comps for f in d.fields}))
    src = hashlib.sha256("".join(sorted(d.source_sha256() for d, _, _ in comps)).encode()).hexdigest()
    parts = [f"{'NOT ' if c.negate else ''}{d.id}" + (f" (w={c.weight:g})" if spec.mode == "weighted" else "")
             for d, _, c in comps]
    joiner = {"and": " AND ", "or": " OR "}.get(spec.mode, ", ")
    head = {"k_of_n": f"at least {spec.k} of: ", "weighted": f"weighted score >= {spec.threshold}: "}.get(spec.mode, "")
    trig = f"{head}{joiner.join(parts)}" + (f", each within {spec.window} sessions" if spec.window > 1 else "") + \
        ("; first session of each run" if spec.edge else "")
    scope = "market" if all(d.scope == "market" for d, _, _ in comps) else \
        "cross_section" if any(d.scope != "stock" for d, _, _ in comps) else "stock"
    return Resolved(spec=spec, key=key, id=f"combo_{_slug(spec.name)}", version="1.0", family="combination",
                    name=spec.label or spec.name, direction=spec.direction, scope=scope, fields=fields,
                    warmup=max(max(d.warmup(p) for d, p, _ in comps) + spec.window,
                               (max(spec.regime.vol_window + 1, 201) if regime else 0)),
                    needs_benchmark=any(d.needs_benchmark for d, _, _ in comps) or
                    bool(regime and regime["market_trend"] != "any"),
                    params=ident, source_sha256=src, trigger=trig, components=comps)


def _frame(x, ind: Indicators) -> pd.DataFrame:
    """A definition's output as a boolean frame aligned to the panel (a market-level Series is broadcast)."""
    idx, cols = ind.index, ind.panel.fields["close"].columns
    if isinstance(x, pd.Series):
        v = x.reindex(idx).fillna(False).astype(bool).to_numpy()
        return pd.DataFrame(np.repeat(v[:, None], len(cols), axis=1), index=idx, columns=cols)
    if not isinstance(x, pd.DataFrame):
        raise TypeError(f"a signal must return a DataFrame or Series, got {type(x).__name__}")
    return x.reindex(index=idx, columns=cols).fillna(False).astype(bool)


def regime_mask(regime: dict | None, ind: Indicators) -> pd.DataFrame | None:
    if not regime:
        return None
    out = None
    if regime.get("market_trend", "any") != "any":
        up = ind.market_trend_up(200)
        want = up == (1.0 if regime["market_trend"] == "up" else 0.0)
        out = _frame(want & up.notna(), ind)
    if regime.get("stock_vol", "any") != "any":
        r = Indicators.xs_rank(ind.realized_vol(int(regime.get("vol_window", 63))))
        lo, hi = {"low": (0.0, 1 / 3), "mid": (1 / 3, 2 / 3), "high": (2 / 3, 1.0)}[regime["stock_vol"]]
        m = (r > lo) & (r <= hi) if lo > 0 else (r <= hi)
        out = m.fillna(False) if out is None else out & m.fillna(False)
    return out


def evaluate(r: Resolved, ind: Indicators) -> pd.DataFrame:
    """The trigger frame of a resolved spec: True where the signal fires at the close of t."""
    if r.definition is not None:
        mask = _frame(r.definition.fn(ind, r.params), ind)
    else:
        spec: CombinationSpec = r.spec
        active = []
        for d, p, c in r.components:
            m = _frame(d.fn(ind, p), ind)
            on = recently(m, spec.window) if spec.window > 1 else m
            active.append(~on if c.negate else on)
        if spec.mode == "and":
            mask = active[0]
            for a in active[1:]:
                mask = mask & a
        elif spec.mode == "or":
            mask = active[0]
            for a in active[1:]:
                mask = mask | a
        else:
            score = sum((a.astype(float) * (1.0 if spec.mode == "k_of_n" else c.weight))
                        for a, (_, _, c) in zip(active, r.components))
            mask = score >= (spec.k if spec.mode == "k_of_n" else spec.threshold)
        if spec.edge:
            mask = rising_edge(mask)
    reg = regime_mask(r.spec.regime.model_dump() if r.spec.regime is not None and r.spec.regime.active() else None,
                      ind)
    if reg is not None:
        mask = mask & reg
    return mask & ind.valid(tuple(f for f in r.fields if f in ("open", "high", "low", "close", "volume")) or ("close",))
