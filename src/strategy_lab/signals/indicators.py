"""Vectorized, causal indicators over wide frames (sessions x symbols), memoized per Panel.

Every value at row t depends only on rows <= t: rolling windows end at t, exponential averages are recursive
(adjust=False), and nothing is ever shifted backwards. tests/test_signals.py poisons future bars and checks that no
past value of any catalog signal changes. A missing bar is NaN in every field and is never filled; rolling windows
that contain one are NaN (min_periods = the window), and the engine never lets a signal trigger on a missing bar.

The cache key is (indicator, arguments), so a batch of signals that share SMA(50) or RSI(14) computes it once.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

FIELDS = ("open", "high", "low", "close", "volume")


class MissingField(ValueError):
    """A signal asked for a bar field the data source does not provide."""


@dataclass
class Panel:
    """The inputs a signal may read: aligned wide frames plus an optional benchmark close (for relative strength and
    market-regime filters). Frames are not copied; treat them as read-only."""
    fields: dict[str, pd.DataFrame]
    benchmark: pd.Series | None = None
    benchmark_symbol: str | None = None
    periods_per_year: float = 252.0

    def __post_init__(self):
        if "close" not in self.fields:
            raise ValueError("Panel needs a close field")
        idx, cols = self.fields["close"].index, self.fields["close"].columns
        for k, f in self.fields.items():
            if not f.index.equals(idx) or not f.columns.equals(cols):
                raise ValueError(f"Panel field {k!r} is not aligned with close")
        if self.benchmark is not None and not self.benchmark.index.equals(idx):
            raise ValueError("Panel benchmark is not aligned with the session index")

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.fields["close"].index

    @property
    def symbols(self) -> list[str]:
        return list(self.fields["close"].columns)

    def field(self, name: str) -> pd.DataFrame:
        try:
            return self.fields[name]
        except KeyError:
            raise MissingField(f"field {name!r} is not provided by this data source "
                               f"(available: {sorted(self.fields)})") from None


def _rolling_window_apply(df: pd.DataFrame, n: int, fn: Callable[[np.ndarray], np.ndarray],
                          chunk_cols: int = 256) -> pd.DataFrame:
    """fn over trailing windows of length n (axis -1 of a (T-n+1, cols, n) view), chunked over columns to bound
    memory. NaN anywhere in a window gives NaN. Rows before the first full window are NaN."""
    a = df.to_numpy(dtype=float)
    out = np.full(a.shape, np.nan)
    T = a.shape[0]
    if T >= n:
        for c0 in range(0, a.shape[1], chunk_cols):
            sub = a[:, c0:c0 + chunk_cols]
            w = sliding_window_view(sub, n, axis=0)                      # (T-n+1, cols, n)
            with np.errstate(invalid="ignore"):
                r = fn(w)
            r[np.isnan(w).any(axis=-1)] = np.nan
            out[n - 1:, c0:c0 + chunk_cols] = r
    return pd.DataFrame(out, index=df.index, columns=df.columns)


class Indicators:
    """Memoized indicator calculations over one Panel. Thread-safe: concurrent signals may share it (a race only
    computes the same value twice)."""

    def __init__(self, panel: Panel):
        self.panel = panel
        self._cache: dict[tuple, object] = {}
        self._lock = threading.Lock()

    # -- plumbing -------------------------------------------------------------------------------------------------
    def _memo(self, key: tuple, fn: Callable[[], object]):
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        val = fn()
        with self._lock:
            return self._cache.setdefault(key, val)

    @property
    def index(self) -> pd.DatetimeIndex:
        return self.panel.index

    def f(self, name: str) -> pd.DataFrame:
        return self.panel.field(name)

    @property
    def open(self) -> pd.DataFrame:
        return self.f("open")

    @property
    def high(self) -> pd.DataFrame:
        return self.f("high")

    @property
    def low(self) -> pd.DataFrame:
        return self.f("low")

    @property
    def close(self) -> pd.DataFrame:
        return self.f("close")

    @property
    def volume(self) -> pd.DataFrame:
        return self.f("volume")

    def valid(self, fields: tuple[str, ...] = ("close",)) -> pd.DataFrame:
        """True where every listed field has a finite, positive value (volume may be zero)."""
        def calc():
            ok = None
            for name in fields:
                x = self.f(name)
                m = np.isfinite(x) & ((x >= 0) if name == "volume" else (x > 0))
                ok = m if ok is None else ok & m
            return ok
        return self._memo(("valid", tuple(sorted(fields))), calc)

    def bench(self) -> pd.Series:
        if self.panel.benchmark is None:
            raise MissingField(f"this signal needs a benchmark series ({self.panel.benchmark_symbol or 'SPY'}), "
                               "which is not loaded")
        return self.panel.benchmark

    # -- returns ----------------------------------------------------------------------------------------------------
    def ret(self, n: int = 1, field: str = "close") -> pd.DataFrame:
        return self._memo(("ret", n, field), lambda: self.f(field) / self.f(field).shift(n) - 1.0)

    def logret(self) -> pd.DataFrame:
        return self._memo(("logret",), lambda: np.log(self.close).diff())

    def realized_vol(self, n: int) -> pd.DataFrame:
        """Annualized standard deviation of daily log returns over the last n returns (ending at t)."""
        return self._memo(("rvol", n), lambda: self.logret().rolling(n, min_periods=n).std(ddof=1)
                          * math.sqrt(self.panel.periods_per_year))

    # -- averages ---------------------------------------------------------------------------------------------------
    def sma(self, n: int, field: str = "close") -> pd.DataFrame:
        return self._memo(("sma", n, field), lambda: self.f(field).rolling(n, min_periods=n).mean())

    def ema(self, n: int, field: str = "close") -> pd.DataFrame:
        return self._memo(("ema", n, field),
                          lambda: self.f(field).ewm(span=n, adjust=False, min_periods=n).mean())

    @staticmethod
    def wilder(x: pd.DataFrame, n: int) -> pd.DataFrame:
        return x.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()

    def std(self, n: int, field: str = "close") -> pd.DataFrame:
        return self._memo(("std", n, field), lambda: self.f(field).rolling(n, min_periods=n).std(ddof=0))

    def highest(self, n: int, field: str = "high") -> pd.DataFrame:
        """Max of field over the last n sessions including t."""
        return self._memo(("hh", n, field), lambda: self.f(field).rolling(n, min_periods=n).max())

    def lowest(self, n: int, field: str = "low") -> pd.DataFrame:
        return self._memo(("ll", n, field), lambda: self.f(field).rolling(n, min_periods=n).min())

    def prior_highest(self, n: int, field: str = "high") -> pd.DataFrame:
        """Max over the n sessions before t (excludes t): the level a breakout on t must clear."""
        return self._memo(("phh", n, field), lambda: self.highest(n, field).shift(1))

    def prior_lowest(self, n: int, field: str = "low") -> pd.DataFrame:
        return self._memo(("pll", n, field), lambda: self.lowest(n, field).shift(1))

    # -- oscillators ------------------------------------------------------------------------------------------------
    def rsi(self, n: int = 14) -> pd.DataFrame:
        """Wilder RSI. 100 when there were only gains in the smoothing, 50 when price did not move."""
        def calc():
            d = self.close.diff()
            up = self.wilder(d.clip(lower=0), n)
            dn = self.wilder((-d).clip(lower=0), n)
            with np.errstate(divide="ignore", invalid="ignore"):
                r = 100.0 - 100.0 / (1.0 + up / dn)
            r = r.mask((dn == 0) & (up > 0), 100.0).mask((dn == 0) & (up == 0), 50.0)
            return r
        return self._memo(("rsi", n), calc)

    def macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        def calc():
            line = self.ema(fast) - self.ema(slow)
            sig = line.ewm(span=signal, adjust=False, min_periods=signal).mean()
            return line, sig, line - sig
        return self._memo(("macd", fast, slow, signal), calc)

    def bbands(self, n: int = 20, k: float = 2.0) -> dict[str, pd.DataFrame]:
        def calc():
            mid, sd = self.sma(n), self.std(n)
            up, lo = mid + k * sd, mid - k * sd
            with np.errstate(divide="ignore", invalid="ignore"):
                width = (up - lo) / mid
                pctb = (self.close - lo) / (up - lo)
            return dict(mid=mid, upper=up, lower=lo, width=width, pctb=pctb)
        return self._memo(("bb", n, k), calc)

    def zscore(self, n: int = 20) -> pd.DataFrame:
        def calc():
            with np.errstate(divide="ignore", invalid="ignore"):
                return (self.close - self.sma(n)) / self.std(n)
        return self._memo(("z", n), calc)

    def stoch(self, k: int = 14, smooth: int = 3, d: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Slow stochastic: %K = SMA(smooth) of the fast %K, %D = SMA(d) of %K."""
        def calc():
            hh, ll = self.highest(k), self.lowest(k)
            with np.errstate(divide="ignore", invalid="ignore"):
                fast = 100.0 * (self.close - ll) / (hh - ll)
            kk = fast.rolling(smooth, min_periods=smooth).mean()
            return kk, kk.rolling(d, min_periods=d).mean()
        return self._memo(("stoch", k, smooth, d), calc)

    def willr(self, n: int = 14) -> pd.DataFrame:
        def calc():
            hh, ll = self.highest(n), self.lowest(n)
            with np.errstate(divide="ignore", invalid="ignore"):
                return -100.0 * (hh - self.close) / (hh - ll)
        return self._memo(("willr", n), calc)

    def typical(self) -> pd.DataFrame:
        return self._memo(("tp",), lambda: (self.high + self.low + self.close) / 3.0)

    def cci(self, n: int = 20) -> pd.DataFrame:
        def calc():
            tp = self.typical()
            ma = tp.rolling(n, min_periods=n).mean()
            mad = _rolling_window_apply(tp, n, lambda w: np.abs(w - w.mean(axis=-1, keepdims=True)).mean(axis=-1))
            with np.errstate(divide="ignore", invalid="ignore"):
                return (tp - ma) / (0.015 * mad)
        return self._memo(("cci", n), calc)

    def roc(self, n: int) -> pd.DataFrame:
        return self._memo(("roc", n), lambda: 100.0 * self.ret(n))

    def trix(self, n: int = 15) -> pd.DataFrame:
        def calc():
            e = self.close
            for _ in range(3):
                e = e.ewm(span=n, adjust=False, min_periods=n).mean()
            return 100.0 * (e / e.shift(1) - 1.0)
        return self._memo(("trix", n), calc)

    def ultimate(self, a: int = 7, b: int = 14, c: int = 28) -> pd.DataFrame:
        def calc():
            pc = self.close.shift(1)
            bp = self.close - np.minimum(self.low, pc)
            tr = np.maximum(self.high, pc) - np.minimum(self.low, pc)
            def avg(n):
                with np.errstate(divide="ignore", invalid="ignore"):
                    return bp.rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum()
            return 100.0 * (4 * avg(a) + 2 * avg(b) + avg(c)) / 7.0
        return self._memo(("uo", a, b, c), calc)

    # -- ranges and trend -------------------------------------------------------------------------------------------
    def true_range(self) -> pd.DataFrame:
        def calc():
            pc = self.close.shift(1)
            return np.maximum(self.high - self.low, np.maximum((self.high - pc).abs(), (self.low - pc).abs()))
        return self._memo(("tr",), calc)

    def atr(self, n: int = 14) -> pd.DataFrame:
        return self._memo(("atr", n), lambda: self.wilder(self.true_range(), n))

    def dmi(self, n: int = 14) -> dict[str, pd.DataFrame]:
        """Wilder's directional movement: +DI, -DI and ADX."""
        def calc():
            up = self.high - self.high.shift(1)
            dn = self.low.shift(1) - self.low
            pdm = up.where((up > dn) & (up > 0), 0.0).where(up.notna() & dn.notna())
            mdm = dn.where((dn > up) & (dn > 0), 0.0).where(up.notna() & dn.notna())
            atr = self.atr(n)
            with np.errstate(divide="ignore", invalid="ignore"):
                pdi = 100.0 * self.wilder(pdm, n) / atr
                mdi = 100.0 * self.wilder(mdm, n) / atr
                dx = 100.0 * (pdi - mdi).abs() / (pdi + mdi)
            return dict(pdi=pdi, mdi=mdi, adx=self.wilder(dx, n))
        return self._memo(("dmi", n), calc)

    def aroon(self, n: int = 25) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Aroon up/down over the last n+1 sessions: 100 * (n - sessions since the high/low) / n."""
        def calc():
            up = _rolling_window_apply(self.high, n + 1, lambda w: n - _last_argmax(w))
            dn = _rolling_window_apply(self.low, n + 1, lambda w: n - _last_argmax(-w))
            return 100.0 * up / n, 100.0 * dn / n
        return self._memo(("aroon", n), calc)

    def keltner(self, n: int = 20, mult: float = 2.0) -> dict[str, pd.DataFrame]:
        def calc():
            mid, a = self.ema(n), self.atr(n)
            return dict(mid=mid, upper=mid + mult * a, lower=mid - mult * a)
        return self._memo(("kc", n, mult), calc)

    def psar(self, af0: float = 0.02, step: float = 0.02, af_max: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Wilder's parabolic SAR, iterated over time and vectorized across symbols. Returns (sar, trend) with
        trend +1 (long) / -1 (short). A missing bar leaves the state unchanged and outputs NaN."""
        def calc():
            h, l = self.high.to_numpy(float), self.low.to_numpy(float)
            T, S = h.shape
            sar = np.full((T, S), np.nan)
            trend = np.full((T, S), np.nan)
            st_sar = np.full(S, np.nan)
            st_tr = np.zeros(S)
            st_ep = np.full(S, np.nan)
            st_af = np.full(S, af0)
            prev_h = np.full(S, np.nan)
            prev_l = np.full(S, np.nan)
            prev2_h = np.full(S, np.nan)
            prev2_l = np.full(S, np.nan)
            for t in range(T):
                ok = np.isfinite(h[t]) & np.isfinite(l[t])
                init = ok & (st_tr == 0) & np.isfinite(prev_h)
                # initialise on the second valid bar: long if the bar rose
                if init.any():
                    up = h[t] + l[t] >= prev_h + prev_l
                    st_tr = np.where(init, np.where(up, 1.0, -1.0), st_tr)
                    st_sar = np.where(init, np.where(up, prev_l, prev_h), st_sar)
                    st_ep = np.where(init, np.where(up, h[t], l[t]), st_ep)
                    st_af = np.where(init, af0, st_af)
                run = ok & (st_tr != 0) & ~init
                if run.any():
                    nxt = st_sar + st_af * (st_ep - st_sar)
                    long_ = st_tr > 0
                    lim_lo = np.fmin(prev_l, np.where(np.isfinite(prev2_l), prev2_l, prev_l))
                    lim_hi = np.fmax(prev_h, np.where(np.isfinite(prev2_h), prev2_h, prev_h))
                    nxt = np.where(long_, np.fmin(nxt, lim_lo), np.fmax(nxt, lim_hi))
                    flip_dn = long_ & (l[t] < nxt)
                    flip_up = ~long_ & (h[t] > nxt)
                    new_ep_long = np.fmax(st_ep, h[t])
                    new_ep_short = np.fmin(st_ep, l[t])
                    ep_moved = np.where(long_, h[t] > st_ep, l[t] < st_ep)
                    af = np.where(ep_moved, np.minimum(st_af + step, af_max), st_af)
                    ep = np.where(long_, new_ep_long, new_ep_short)
                    n_sar = np.where(flip_dn, st_ep, np.where(flip_up, st_ep, nxt))
                    n_tr = np.where(flip_dn, -1.0, np.where(flip_up, 1.0, st_tr))
                    n_ep = np.where(flip_dn, l[t], np.where(flip_up, h[t], ep))
                    n_af = np.where(flip_dn | flip_up, af0, af)
                    st_sar = np.where(run, n_sar, st_sar)
                    st_tr = np.where(run, n_tr, st_tr)
                    st_ep = np.where(run, n_ep, st_ep)
                    st_af = np.where(run, n_af, st_af)
                    sar[t] = np.where(run, st_sar, np.nan)
                    trend[t] = np.where(run, st_tr, np.nan)
                prev2_h = np.where(ok, prev_h, prev2_h)
                prev2_l = np.where(ok, prev_l, prev2_l)
                prev_h = np.where(ok, h[t], prev_h)
                prev_l = np.where(ok, l[t], prev_l)
            idx, cols = self.high.index, self.high.columns
            return pd.DataFrame(sar, index=idx, columns=cols), pd.DataFrame(trend, index=idx, columns=cols)
        return self._memo(("psar", af0, step, af_max), calc)

    def ichimoku(self, a: int = 9, b: int = 26, c: int = 52) -> dict[str, pd.DataFrame]:
        """Tenkan, kijun and the cloud as it stands at t: the spans plotted at t were computed b sessions earlier."""
        def calc():
            tenkan = (self.highest(a) + self.lowest(a)) / 2.0
            kijun = (self.highest(b) + self.lowest(b)) / 2.0
            span_a = ((tenkan + kijun) / 2.0).shift(b)
            span_b = ((self.highest(c) + self.lowest(c)) / 2.0).shift(b)
            return dict(tenkan=tenkan, kijun=kijun, span_a=span_a, span_b=span_b,
                        cloud_top=np.fmax(span_a, span_b).where(span_a.notna() & span_b.notna()),
                        cloud_bottom=np.fmin(span_a, span_b).where(span_a.notna() & span_b.notna()))
        return self._memo(("ichi", a, b, c), calc)

    def vortex(self, n: int = 14) -> tuple[pd.DataFrame, pd.DataFrame]:
        def calc():
            vmp = (self.high - self.low.shift(1)).abs()
            vmm = (self.low - self.high.shift(1)).abs()
            tr = self.true_range().rolling(n, min_periods=n).sum()
            with np.errstate(divide="ignore", invalid="ignore"):
                return vmp.rolling(n, min_periods=n).sum() / tr, vmm.rolling(n, min_periods=n).sum() / tr
        return self._memo(("vortex", n), calc)

    def heikin_ashi(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(ha_open, ha_close). ha_open is the recursive average of the previous ha_open and ha_close, i.e. an
        exponential average (alpha 1/2) of the previous ha_close; its seed decays by half every session."""
        def calc():
            hc = (self.open + self.high + self.low + self.close) / 4.0
            ho = hc.shift(1).ewm(alpha=0.5, adjust=False, min_periods=5).mean()
            return ho, hc
        return self._memo(("ha",), calc)

    def linreg(self, n: int, field: str = "close", log: bool = True) -> dict[str, pd.DataFrame]:
        """Least-squares line through the last n values: slope (per session, of log price when log) and r^2."""
        def calc():
            y = np.log(self.f(field)) if log else self.f(field)
            t = pd.Series(np.arange(len(y), dtype=float), index=y.index)
            ty = y.mul(t, axis=0)
            sy = y.rolling(n, min_periods=n).sum()
            syy = (y * y).rolling(n, min_periods=n).sum()
            sty = ty.rolling(n, min_periods=n).sum()
            st = t.rolling(n, min_periods=n).sum()
            stt = (t * t).rolling(n, min_periods=n).sum()
            cov = sty - sy.mul(st, axis=0) / n
            vt = stt - st * st / n
            vy = syy - sy * sy / n
            with np.errstate(divide="ignore", invalid="ignore"):
                slope = cov.div(vt, axis=0)
                r2 = (cov * cov).div(vt, axis=0) / vy
            mean_y = sy / n
            fit_end = mean_y + slope.mul((n - 1) / 2.0)
            resid_sd = np.sqrt(np.maximum(vy - (cov * cov).div(vt, axis=0), 0) / n)
            return dict(slope=slope, r2=r2.clip(upper=1.0), fit=fit_end, resid_sd=resid_sd)
        return self._memo(("linreg", n, field, log), calc)

    # -- volume -----------------------------------------------------------------------------------------------------
    def rel_volume(self, n: int = 20) -> pd.DataFrame:
        """Volume on t over the mean volume of the n sessions before t."""
        def calc():
            base = self.volume.rolling(n, min_periods=n).mean().shift(1)
            with np.errstate(divide="ignore", invalid="ignore"):
                return self.volume / base.where(base > 0)
        return self._memo(("rvolu", n), calc)

    def obv(self) -> pd.DataFrame:
        def calc():
            d = np.sign(self.close.diff())
            contrib = (d * self.volume).fillna(0.0)
            return contrib.cumsum().where(self.close.notna() & self.volume.notna())
        return self._memo(("obv",), calc)

    def mfi(self, n: int = 14) -> pd.DataFrame:
        def calc():
            tp = self.typical()
            flow = tp * self.volume
            up = flow.where(tp > tp.shift(1), 0.0).where(tp.notna() & tp.shift(1).notna())
            dn = flow.where(tp < tp.shift(1), 0.0).where(tp.notna() & tp.shift(1).notna())
            pos, neg = up.rolling(n, min_periods=n).sum(), dn.rolling(n, min_periods=n).sum()
            with np.errstate(divide="ignore", invalid="ignore"):
                out = 100.0 - 100.0 / (1.0 + pos / neg)
            return out.mask((neg == 0) & (pos > 0), 100.0).mask((neg == 0) & (pos == 0), 50.0)
        return self._memo(("mfi", n), calc)

    def money_flow_volume(self) -> pd.DataFrame:
        def calc():
            rng = self.high - self.low
            with np.errstate(divide="ignore", invalid="ignore"):
                mult = ((self.close - self.low) - (self.high - self.close)) / rng
            return (mult.where(rng > 0, 0.0) * self.volume)
        return self._memo(("mfv",), calc)

    def cmf(self, n: int = 20) -> pd.DataFrame:
        def calc():
            with np.errstate(divide="ignore", invalid="ignore"):
                return (self.money_flow_volume().rolling(n, min_periods=n).sum()
                        / self.volume.rolling(n, min_periods=n).sum())
        return self._memo(("cmf", n), calc)

    def ad_line(self) -> pd.DataFrame:
        return self._memo(("adl",), lambda: self.money_flow_volume().fillna(0.0).cumsum()
                          .where(self.close.notna()))

    def dollar_volume(self, n: int = 21) -> pd.DataFrame:
        """Median of close x volume over the last n sessions (liquidity, on this panel's prices)."""
        return self._memo(("dvol", n), lambda: (self.close * self.volume).rolling(n, min_periods=n).median())

    # -- cross-section and market ---------------------------------------------------------------------------------
    @staticmethod
    def xs_rank(x: pd.DataFrame) -> pd.DataFrame:
        """Cross-sectional percentile rank per session (0..1], among symbols with a value that session."""
        return x.rank(axis=1, pct=True)

    def ts_rank(self, key: str, x: pd.DataFrame, n: int) -> pd.DataFrame:
        """Percentile of x[t] within its own last n values (including t)."""
        return self._memo(("tsrank", key, n), lambda: x.rolling(n, min_periods=n).rank(pct=True))

    def breadth_above_sma(self, n: int = 50) -> pd.Series:
        """Share of symbols with a bar on t whose close is above their SMA(n)."""
        def calc():
            sma = self.sma(n)
            have = sma.notna() & self.close.notna()
            above = (self.close > sma) & have
            cnt = have.sum(axis=1)
            return (above.sum(axis=1) / cnt.where(cnt > 0)).astype(float)
        return self._memo(("br_sma", n), calc)

    def advance_share(self) -> pd.Series:
        def calc():
            r = self.ret(1)
            have = r.notna()
            cnt = have.sum(axis=1)
            return ((r > 0).sum(axis=1) / cnt.where(cnt > 0)).astype(float)
        return self._memo(("adv",), calc)

    def ad_cumulative(self) -> pd.Series:
        def calc():
            r = self.ret(1)
            return ((r > 0).sum(axis=1) - (r < 0).sum(axis=1)).astype(float).cumsum()
        return self._memo(("adcum",), calc)

    def rs_line(self) -> pd.DataFrame:
        """close / benchmark close (relative strength line)."""
        def calc():
            b = self.bench()
            return self.close.div(b.where(b > 0), axis=0)
        return self._memo(("rs",), calc)

    def market_trend_up(self, n: int = 200) -> pd.Series:
        """Benchmark close above its SMA(n); NaN until n sessions exist."""
        def calc():
            b = self.bench()
            s = b.rolling(n, min_periods=n).mean()
            return (b > s).astype(float).where(s.notna() & b.notna())
        return self._memo(("mkt_up", n), calc)


def _last_argmax(w: np.ndarray) -> np.ndarray:
    """Distance (in sessions) from the end of each window to the latest maximum."""
    rev = w[..., ::-1]
    return np.argmax(rev, axis=-1).astype(float)


# -- trigger helpers ---------------------------------------------------------------------------------------------------
def cross_above(a, b) -> pd.DataFrame:
    """a crosses above b on t: a > b on t and a <= b on t-1 (NaN on either session: no cross)."""
    a_prev = a.shift(1)
    b_prev = b.shift(1) if isinstance(b, (pd.DataFrame, pd.Series)) else b
    return _aligned(a > b) & _aligned(a_prev <= b_prev)


def cross_below(a, b) -> pd.DataFrame:
    a_prev = a.shift(1)
    b_prev = b.shift(1) if isinstance(b, (pd.DataFrame, pd.Series)) else b
    return _aligned(a < b) & _aligned(a_prev >= b_prev)


def rising_edge(mask: pd.DataFrame) -> pd.DataFrame:
    """True on the first session of each run of True."""
    m = mask.fillna(False).astype(bool)
    return m & ~m.shift(1, fill_value=False)


def recently(mask: pd.DataFrame, n: int) -> pd.DataFrame:
    """mask was True on at least one of the last n sessions (including t)."""
    m = mask.fillna(False).astype(float)
    return m.rolling(n, min_periods=1).max() > 0


def consecutive(mask: pd.DataFrame, n: int) -> pd.DataFrame:
    """mask True on each of the last n sessions."""
    m = mask.fillna(False).astype(float)
    return m.rolling(n, min_periods=n).sum() >= n


def _aligned(x):
    return x.fillna(False).astype(bool) if hasattr(x, "fillna") else x
