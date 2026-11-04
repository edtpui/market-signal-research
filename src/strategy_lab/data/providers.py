"""Pluggable data providers. Each returns the normalized long schema, already clipped to the data policy:

  bars:    symbol, date, open, high, low, close (adjusted), volume, close_raw, factor (close / close_raw)
  actions: symbol, date, kind ('split' | 'dividend'), value

yfinance_csv: the workspace research downloads (<SYM>_1d.csv adjusted OHLCV, <SYM>_1d_raw.csv Close/Volume with
auto_adjust=False). yfinance's "raw" close is split-adjusted but not dividend-adjusted, so splits are embedded in
every series and cannot be recovered, and dividends are derived from the adjustment factor:
D(t) = close_raw(t-1) * (1 - factor(t-1) / factor(t)). Volume comes from the raw file (review item A9: the
adjusted file's volume can be split-adjusted twice).

synthetic: deterministic random-walk prices with explicit splits and dividends, labelled SYNTHETIC (tests, demos).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from strategy_lab.data.policy import DataPolicy

BAR_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "volume", "close_raw", "factor"]
ACTION_COLUMNS = ["symbol", "date", "kind", "value"]
DIVIDEND_TOL = 1e-5          # factor ratio below 1 - tol counts as a dividend; smaller moves are float noise


@dataclass
class DatasetSpec:
    id: str
    name: str
    provider: str
    paths: list[Path] = field(default_factory=list)
    description: str = ""
    options: dict = field(default_factory=dict)

    @classmethod
    def from_settings(cls, d: dict, resolve) -> "DatasetSpec":
        known = {"id", "name", "provider", "paths", "description"}
        return cls(id=d["id"], name=d.get("name", d["id"]), provider=d["provider"],
                   paths=[resolve(p) for p in d.get("paths", [])], description=d.get("description", ""),
                   options={k: v for k, v in d.items() if k not in known})


@dataclass
class ProviderOutput:
    bars: pd.DataFrame
    actions: pd.DataFrame | None          # None: derive dividends from the factor after validation (DataStore)
    stats: dict


class Provider:
    kind = "base"
    version = "1"

    def __init__(self, spec: DatasetSpec):
        self.spec = spec

    def fingerprint(self) -> str:
        """Cheap change detector (file names, sizes, mtimes). Equal fingerprints reuse the cached content version."""
        raise NotImplementedError

    def content_hash(self) -> str:
        """sha256 over the source content; the dataset version derives from it."""
        raise NotImplementedError

    def read(self, policy: DataPolicy) -> ProviderOutput:
        raise NotImplementedError


class YFinanceCsvProvider(Provider):
    kind = "yfinance_csv"
    version = "1"

    def files(self) -> dict[str, dict[str, Path]]:
        """symbol -> {'adj': path, 'raw': path}; the first directory listing a symbol wins."""
        out: dict[str, dict[str, Path]] = {}
        for d in self.spec.paths:
            if not d.is_dir():
                raise FileNotFoundError(f"dataset {self.spec.id}: directory {d} does not exist")
            local: dict[str, dict[str, Path]] = {}
            for p in sorted(d.glob("*_1d.csv")):
                local.setdefault(p.name[: -len("_1d.csv")], {})["adj"] = p
            for p in sorted(d.glob("*_1d_raw.csv")):
                local.setdefault(p.name[: -len("_1d_raw.csv")], {})["raw"] = p
            for sym, f in local.items():
                if sym not in out and "adj" in f:
                    out[sym] = f
        return out

    def _all_paths(self) -> list[Path]:
        return sorted(p for f in self.files().values() for p in f.values())

    def fingerprint(self) -> str:
        h = hashlib.sha256(f"{self.kind}:{self.version}".encode())
        for p in self._all_paths():
            st = p.stat()
            h.update(f"{p.name}:{st.st_size}:{st.st_mtime_ns}\n".encode())
        return h.hexdigest()

    def content_hash(self) -> str:
        h = hashlib.sha256(f"{self.kind}:{self.version}".encode())
        for p in self._all_paths():
            h.update(p.name.encode())
            h.update(hashlib.sha256(p.read_bytes()).digest())
        return h.hexdigest()

    def read(self, policy: DataPolicy) -> ProviderOutput:
        files = self.files()
        if not files:
            raise FileNotFoundError(f"dataset {self.spec.id}: no *_1d.csv files in {self.spec.paths}")
        con = duckdb.connect()
        try:
            adj = self._load(con, [(s, f["adj"]) for s, f in files.items()],
                             "Date DATE, Open DOUBLE, High DOUBLE, Low DOUBLE, Close DOUBLE, Volume DOUBLE")
            raw = self._load(con, [(s, f["raw"]) for s, f in files.items() if "raw" in f],
                             "Date DATE, Close DOUBLE, Volume DOUBLE")
            con.register("adj_df", adj)
            con.register("raw_df", raw)
            lo, hi = policy.history_floor.isoformat(), policy.ceiling.isoformat()
            beyond = con.execute("SELECT count(*) FROM adj_df WHERE Date > CAST(? AS DATE)", [hi]).fetchone()[0]
            before = con.execute("SELECT count(*) FROM adj_df WHERE Date < CAST(? AS DATE)", [lo]).fetchone()[0]
            df = con.execute(
                "SELECT a.symbol, a.Date AS date, a.Open AS open, a.High AS high, a.Low AS low, a.Close AS close, "
                "coalesce(r.Volume, a.Volume) AS volume, r.Close AS close_raw, (r.Volume IS NULL) AS volume_from_adj "
                "FROM adj_df a LEFT JOIN raw_df r ON r.symbol = a.symbol AND r.Date = a.Date "
                "WHERE a.Date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) ORDER BY a.symbol, a.Date", [lo, hi]).df()
        finally:
            con.close()
        df["date"] = pd.to_datetime(df["date"])
        dup = int(df.duplicated(["symbol", "date"]).sum())
        df = df.drop_duplicates(["symbol", "date"], keep="first")
        df["factor"] = df["close"] / df["close_raw"]
        stats = dict(rows_beyond_ceiling=int(beyond), rows_before_floor=int(before), duplicate_rows=dup,
                     volume_from_adjusted_file=int(df["volume_from_adj"].sum()),
                     symbols_without_raw=sorted(s for s, f in files.items() if "raw" not in f),
                     splits="embedded: yfinance raw closes are split-adjusted, so no split events are emitted",
                     dividends="derived from the adjusted/raw close factor")
        return ProviderOutput(bars=df[BAR_COLUMNS], actions=None, stats=stats)

    @staticmethod
    def _load(con, items: list[tuple[str, Path]], columns: str) -> pd.DataFrame:
        """All files of one kind in one scan; the symbol comes from the file name."""
        if not items:
            cols = [c.split()[0] for c in columns.split(",")]
            return pd.DataFrame(columns=["symbol", *[c.strip() for c in cols]])
        by_path = {str(p): s for s, p in items}
        cols = ", ".join(f"'{c.split()[0]}': '{c.split()[1]}'" for c in (x.strip() for x in columns.split(",")))
        df = con.execute(
            f"SELECT * FROM read_csv(?, header=true, filename=true, columns={{{cols}}}, nullstr=['', 'nan', 'NaN'])",
            [list(by_path)]).df()
        df["symbol"] = df.pop("filename").map(lambda f: by_path[str(Path(f))] if str(Path(f)) in by_path
                                               else by_path[f])
        return df


class SyntheticProvider(Provider):
    """Deterministic: seed, symbols, start/end; options: drift, vol, splits [[sym, date, ratio]],
    dividends [[sym, date, amount]], gaps [[sym, date]] (a missing bar). Prices are raw; adjusted closes are derived
    the yfinance way (splits and dividends backward-adjusted), so both price modes and the actions can be tested."""
    kind = "synthetic"
    version = "1"

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(dict(k=self.kind, v=self.version, o=self.spec.options),
                                         sort_keys=True, default=str).encode()).hexdigest()

    def content_hash(self) -> str:
        return self.fingerprint()

    def read(self, policy: DataPolicy) -> ProviderOutput:
        o = self.spec.options
        start = pd.Timestamp(o.get("start", policy.history_floor))
        end = min(pd.Timestamp(o.get("end", policy.ceiling)), pd.Timestamp(policy.ceiling))
        days = pd.bdate_range(max(start, pd.Timestamp(policy.history_floor)), end)
        rng = np.random.default_rng(int(o.get("seed", 7)))
        symbols = list(o.get("symbols", ["SYNA", "SYNB", "SYNC", "SYNM"]))
        drift, vol = float(o.get("drift", 0.0003)), float(o.get("vol", 0.015))
        splits = {(s, pd.Timestamp(d)): float(r) for s, d, r in o.get("splits", [])}
        divs = {(s, pd.Timestamp(d)): float(a) for s, d, a in o.get("dividends", [])}
        gaps = {(s, pd.Timestamp(d)) for s, d in o.get("gaps", [])}
        frames, actions = [], []
        for k, sym in enumerate(symbols):
            r = rng.normal(drift * (1 + 0.3 * k), vol * (1 + 0.2 * k), len(days))
            close = 50.0 * (1 + k) * np.exp(np.cumsum(r))
            for i, d in enumerate(days):              # an as-traded split divides the price from its ex-date on
                if (sym, d) in splits:
                    close[i:] /= splits[(sym, d)]
                    actions.append((sym, d, "split", splits[(sym, d)]))
                if (sym, d) in divs:
                    actions.append((sym, d, "dividend", divs[(sym, d)]))
            gap = rng.normal(0, vol / 3, len(days))
            opn = close * np.exp(gap) / np.exp(r * 0.5)
            hi = np.maximum(opn, close) * (1 + np.abs(rng.normal(0, vol / 2, len(days))))
            lo = np.minimum(opn, close) * (1 - np.abs(rng.normal(0, vol / 2, len(days))))
            volu = rng.integers(200_000, 2_000_000, len(days)).astype(float)
            df = pd.DataFrame(dict(symbol=sym, date=days, open=opn, high=hi, low=lo, close_raw=close, volume=volu))
            df = df[[(sym, d) not in gaps for d in df["date"]]].reset_index(drop=True)
            factor = _backward_factor(df, [(a[1], a[2], a[3]) for a in actions if a[0] == sym])
            df["factor"] = factor
            for c in ("open", "high", "low"):
                df[c] = df[c] * factor
            df["close"] = df["close_raw"] * factor
            frames.append(df)
        bars = pd.concat(frames, ignore_index=True)
        acts = pd.DataFrame(actions, columns=ACTION_COLUMNS).sort_values(["date", "symbol"]).reset_index(drop=True)
        return ProviderOutput(bars=bars[BAR_COLUMNS], actions=acts,
                              stats=dict(rows_beyond_ceiling=0, rows_before_floor=0, duplicate_rows=0,
                                         label="SYNTHETIC", splits="explicit events", dividends="explicit events"))


def _backward_factor(df: pd.DataFrame, acts: list) -> np.ndarray:
    """yfinance-style total adjustment (raw * factor = adjusted close, factor 1.0 on the last bar): before a split's
    ex-date multiply by 1/ratio; before a dividend's ex-date by 1 - D / close_raw(day before)."""
    f = np.ones(len(df))
    dates = df["date"].to_numpy()
    raw = df["close_raw"].to_numpy()
    for d, kind, v in acts:
        pos = int(np.searchsorted(dates, np.datetime64(d)))
        if pos == 0 or pos >= len(df):
            continue
        m = 1.0 / v if kind == "split" else 1.0 - v / raw[pos - 1]
        f[:pos] *= m
    return f


def derive_dividends(bars: pd.DataFrame) -> pd.DataFrame:
    """Dividends from consecutive usable bars (finite, positive factor and raw close). Pass validated bars only: a
    bad price would otherwise look like a factor change. A dividend whose ex-date has no usable bar is dated at the
    next usable one (when a position can be credited) and priced off the previous usable raw close."""
    out = []
    for sym, g in bars.groupby("symbol", sort=True):
        f = g["factor"].to_numpy(dtype=float)
        cr = g["close_raw"].to_numpy(dtype=float)
        d = g["date"].to_numpy()
        ok = np.isfinite(f) & np.isfinite(cr) & (f > 0) & (cr > 0)
        f, cr, d = f[ok], cr[ok], d[ok]
        for i in range(1, len(f)):
            ratio = f[i - 1] / f[i]
            if ratio < 1 - DIVIDEND_TOL:
                out.append((sym, pd.Timestamp(d[i]), "dividend", float(cr[i - 1] * (1 - ratio))))
    return pd.DataFrame(out, columns=ACTION_COLUMNS)


PROVIDERS = {p.kind: p for p in (YFinanceCsvProvider, SyntheticProvider)}


def make_provider(spec: DatasetSpec) -> Provider:
    try:
        return PROVIDERS[spec.provider](spec)
    except KeyError:
        raise ValueError(f"dataset {spec.id}: unknown provider {spec.provider!r} (have {sorted(PROVIDERS)})") from None
