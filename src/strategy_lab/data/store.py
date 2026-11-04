"""The local market-data cache: one immutable Parquet snapshot per dataset content version, written and read with
DuckDB (no pyarrow needed).

  <cache>/datasets/<id>/<version>/bars.parquet, actions.parquet, coverage.parquet, meta.json
  <cache>/datasets/<id>/index.json   fingerprint -> version (skips re-hashing unchanged sources)

The version is sha256(provider content hash + data policy + STORE_VERSION)[:16]. A version directory is never rewritten, so an
experiment that recorded a version can be re-run on exactly the same bars while that directory exists.

Validation at ingest: a bar with a non-finite or non-positive price, or high < low, or a close outside [low, high]
(0.5% tolerance), is marked invalid and turned into a missing bar, and every such bar is counted in the coverage
report. The session calendar is the reference symbol's sessions (dataset option calendar_symbol, default SPY) when
the dataset has it, else the days on which at least half of the symbols listed then have a bar: a union would let a
series that trades on exchange holidays (DX-Y.NYB on 2025-01-09 and 2025-05-26) give every stock phantom missing
sessions. Bars on other days are kept in the snapshot, counted, and never loaded. Missing sessions are measured
against that calendar. Nothing is forward-filled.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from strategy_lab.core.market import MarketData
from strategy_lab.data.policy import DataPolicy, to_date
from strategy_lab.data.providers import DatasetSpec, Provider, derive_dividends, make_provider

RANGE_TOL = 0.005
STORE_VERSION = "3"          # validation / derivation rules: part of every dataset version (bump on any change)
FREQUENCIES = {"1d": 252.0, "1w": 52.0, "1mo": 12.0}


class DataError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetVersion:
    dataset_id: str
    version: str
    path: Path
    meta: dict


def _q(p: Path) -> str:
    return str(p).replace("\\", "/").replace("'", "''")


def _query(sql: str) -> pd.DataFrame:
    """One connection per query: DuckDB's shared default connection is not safe across the job threads."""
    con = duckdb.connect()
    try:
        return con.execute(sql).df()
    finally:
        con.close()


class DataStore:
    def __init__(self, cache_dir: Path, policy: DataPolicy, specs: list[DatasetSpec]):
        self.cache_dir = Path(cache_dir)
        self.policy = policy
        self.specs = {s.id: s for s in specs}
        self._lock = threading.RLock()
        self._versions: dict[str, DatasetVersion] = {}

    # -- datasets -----------------------------------------------------------------------------------------------
    def spec(self, dataset_id: str) -> DatasetSpec:
        try:
            return self.specs[dataset_id]
        except KeyError:
            raise DataError(f"unknown dataset {dataset_id!r} (configured: {sorted(self.specs)})") from None

    def provider(self, dataset_id: str) -> Provider:
        return make_provider(self.spec(dataset_id))

    def _dir(self, dataset_id: str) -> Path:
        return self.cache_dir / "datasets" / dataset_id

    def cached_versions(self, dataset_id: str) -> list[dict]:
        d = self._dir(dataset_id)
        out = []
        for m in sorted(d.glob("*/meta.json")):
            out.append(json.loads(m.read_text(encoding="utf-8")))
        return sorted(out, key=lambda x: x["ingested_at_utc"])

    def ingest(self, dataset_id: str) -> DatasetVersion:
        """The current version of a dataset, ingesting it if its source changed or was never cached."""
        with self._lock:
            prov = self.provider(dataset_id)
            fp = prov.fingerprint()
            policy_key = json.dumps(dict(policy=self.policy.to_dict(), store=STORE_VERSION), sort_keys=True)
            index_path = self._dir(dataset_id) / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
            key = hashlib.sha256((fp + policy_key).encode()).hexdigest()
            if key in index:
                v = self._open(dataset_id, index[key])
                if v is not None:
                    self._versions[dataset_id] = v
                    return v
            version = hashlib.sha256((prov.content_hash() + policy_key).encode()).hexdigest()[:16]
            v = self._open(dataset_id, version) or self._write(dataset_id, prov, version)
            index[key] = version
            index_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(index, indent=1), encoding="utf-8")
            os.replace(tmp, index_path)
            self._versions[dataset_id] = v
            return v

    def current(self, dataset_id: str) -> DatasetVersion:
        return self._versions.get(dataset_id) or self.ingest(dataset_id)

    def version(self, dataset_id: str, version: str) -> DatasetVersion:
        v = self._open(dataset_id, version)
        if v is None:
            raise DataError(f"dataset {dataset_id} version {version} is not in the cache {self._dir(dataset_id)}")
        return v

    def _open(self, dataset_id: str, version: str) -> DatasetVersion | None:
        p = self._dir(dataset_id) / version
        meta = p / "meta.json"
        if not meta.exists():
            return None
        return DatasetVersion(dataset_id, version, p, json.loads(meta.read_text(encoding="utf-8")))

    def _write(self, dataset_id: str, prov: Provider, version: str) -> DatasetVersion:
        out = prov.read(self.policy)
        bars = out.bars.copy()
        bars = bars[(bars["date"] >= pd.Timestamp(self.policy.history_floor))
                    & (bars["date"] <= pd.Timestamp(self.policy.ceiling))]
        bars, invalid = _validate(bars)
        cal, cal_rule = _session_calendar(bars, self.spec(dataset_id).options.get("calendar_symbol", "SPY"))
        inside = bars["date"].isin(cal)
        coverage = _coverage(bars[inside], cal)
        outside = int((~inside & bars["valid"]).sum())
        acts = out.actions if out.actions is not None else derive_dividends(bars[bars["valid"]])
        if len(acts):
            acts = acts[(acts["date"] >= pd.Timestamp(self.policy.history_floor))
                        & (acts["date"] <= pd.Timestamp(self.policy.ceiling))]
        final = self._dir(dataset_id) / version
        tmp = final.with_name(version + ".partial")
        tmp.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        try:
            for name, df in (("bars", bars), ("actions", acts), ("coverage", coverage),
                             ("calendar", pd.DataFrame({"date": cal}))):
                con.register("t", df)
                con.execute(f"COPY (SELECT * FROM t) TO '{_q(tmp / (name + '.parquet'))}' (FORMAT parquet)")
                con.unregister("t")
        finally:
            con.close()
        spec = self.spec(dataset_id)
        meta = dict(dataset_id=dataset_id, version=version, name=spec.name, provider=spec.provider,
                    provider_version=prov.version, description=spec.description,
                    sources=[str(p) for p in spec.paths], policy=self.policy.to_dict(), store_version=STORE_VERSION,
                    ingested_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    n_symbols=int(bars["symbol"].nunique()), n_rows=int(len(bars)), invalid_bars=int(invalid),
                    first_date=cal[0].date().isoformat() if len(cal) else None,
                    last_date=cal[-1].date().isoformat() if len(cal) else None, n_sessions=int(len(cal)),
                    n_actions=int(len(acts)), frequency="1d", calendar=cal_rule, bars_outside_calendar=outside,
                    stats=out.stats)
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, final)
        return DatasetVersion(dataset_id, version, final, meta)

    # -- queries ------------------------------------------------------------------------------------------------
    def coverage(self, dataset_id: str, version: str | None = None) -> pd.DataFrame:
        v = self.version(dataset_id, version) if version else self.current(dataset_id)
        return _query(f"SELECT * FROM read_parquet('{_q(v.path / 'coverage.parquet')}') ORDER BY symbol")

    def symbols(self, dataset_id: str, version: str | None = None) -> list[str]:
        return self.coverage(dataset_id, version)["symbol"].tolist()

    def calendar(self, dataset_id: str, version: str | None = None) -> pd.DatetimeIndex:
        v = self.version(dataset_id, version) if version else self.current(dataset_id)
        if (v.path / "calendar.parquet").exists():
            d = _query(f"SELECT date FROM read_parquet('{_q(v.path / 'calendar.parquet')}') ORDER BY date")
        else:   # a snapshot written before STORE_VERSION 3 is read with its own rule (the union), so its runs reproduce
            d = _query(f"SELECT DISTINCT date FROM read_parquet('{_q(v.path / 'bars.parquet')}') WHERE valid "
                       "ORDER BY date")
        return pd.DatetimeIndex(pd.to_datetime(d["date"]))

    def bars(self, dataset_id: str, symbols: list[str], start, end, version: str | None = None) -> pd.DataFrame:
        v = self.version(dataset_id, version) if version else self.current(dataset_id)
        s, e = to_date(start), min(to_date(end), self.policy.ceiling)
        con = duckdb.connect()
        try:
            con.register("syms", pd.DataFrame({"symbol": list(symbols)}))
            df = con.execute(
                f"SELECT b.* FROM read_parquet('{_q(v.path / 'bars.parquet')}') b JOIN syms USING (symbol) "
                "WHERE b.date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) ORDER BY b.symbol, b.date",
                [s.isoformat(), e.isoformat()]).df()
            acts = con.execute(
                f"SELECT a.* FROM read_parquet('{_q(v.path / 'actions.parquet')}') a JOIN syms USING (symbol) "
                "WHERE a.date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE) ORDER BY a.date, a.symbol",
                [s.isoformat(), e.isoformat()]).df() if v.meta.get("n_actions") else None
        finally:
            con.close()
        df["date"] = pd.to_datetime(df["date"])
        df.attrs["actions"] = acts if acts is not None else pd.DataFrame(columns=["symbol", "date", "kind", "value"])
        return df

    def load(self, dataset_id: str, symbols: list[str], start, end, *, version: str | None = None,
             frequency: str = "1d", price_mode: str = "adjusted", fields: tuple[str, ...] | None = None,
             warmup_bars: int = 0) -> tuple[MarketData, dict]:
        """MarketData for symbols over [start - warmup, end] plus a data report. Raises DataError when the dataset
        does not cover the request: an unknown symbol, a window outside the dataset's sessions, or an unknown
        frequency. Symbols that start later or stop earlier than the window are reported, not filled."""
        if frequency not in FREQUENCIES:
            raise DataError(f"frequency {frequency!r} is not available (have {sorted(FREQUENCIES)}; "
                            "1w and 1mo are aggregated from daily bars)")
        if price_mode not in ("adjusted", "raw"):
            raise DataError(f"unknown price_mode {price_mode!r}")
        if price_mode == "raw" and frequency != "1d":
            raise DataError("raw price mode needs daily bars: an aggregated bar could straddle a split")
        v = self.version(dataset_id, version) if version else self.current(dataset_id)
        cov = self.coverage(dataset_id, v.version).set_index("symbol")
        unknown = sorted(set(symbols) - set(cov.index[cov["n_bars"] > 0]))
        if unknown:
            raise DataError(f"dataset {dataset_id} has no data for {unknown[:20]}{' ...' if len(unknown) > 20 else ''}")
        s, e = to_date(start), to_date(end)
        first, last = to_date(v.meta["first_date"]), to_date(v.meta["last_date"])
        if s < first or e > last:
            raise DataError(f"dataset {dataset_id}@{v.version} covers {first}..{last}; the request {s}..{e} is "
                            "outside it")
        cal = self.calendar(dataset_id, v.version)
        pos = int(np.searchsorted(cal, pd.Timestamp(s)))
        wpos = max(0, pos - int(warmup_bars) * (1 if frequency == "1d" else 5 if frequency == "1w" else 21))
        lo = cal[wpos]
        df = self.bars(dataset_id, symbols, lo, e, v.version)
        acts = df.attrs["actions"]
        df = df[df["valid"]]
        cal = cal[(cal >= lo) & (cal <= pd.Timestamp(e))]
        md = _to_market_data(df, acts, list(symbols), cal, frequency, price_mode, fields)
        md.dataset_id, md.dataset_version = dataset_id, v.version
        in_window = cov.loc[list(symbols)]
        report = dict(dataset_id=dataset_id, version=v.version, frequency=frequency, price_mode=price_mode,
                      requested=[s.isoformat(), e.isoformat()], loaded_from=lo.date().isoformat(),
                      sessions=int(((md.index >= pd.Timestamp(s)) & (md.index <= pd.Timestamp(e))).sum()),
                      late_start=sorted(x for x in symbols if to_date(in_window.at[x, "first_date"]) > s),
                      early_end=sorted(x for x in symbols if to_date(in_window.at[x, "last_date"]) < e),
                      missing_bars={x: int(np.isnan(md.fields["close"][x].loc[pd.Timestamp(s):].to_numpy()).sum())
                                    for x in symbols},
                      invalid_bars_in_dataset=int(in_window["invalid_bars"].sum()),
                      corporate_actions=int(len(md.actions)), splits=v.meta.get("stats", {}).get("splits"),
                      dividends=v.meta.get("stats", {}).get("dividends"))
        report["missing_bars"] = {k: n for k, n in report["missing_bars"].items() if n}
        return md, report


def _validate(bars: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    b = bars.copy()
    px = b[["open", "high", "low", "close"]].to_numpy(dtype=float)
    finite = np.isfinite(px).all(axis=1) & (px > 0).all(axis=1)
    hi, lo, c, o = b["high"].to_numpy(), b["low"].to_numpy(), b["close"].to_numpy(), b["open"].to_numpy()
    with np.errstate(invalid="ignore"):
        shape = (hi >= lo) & (c <= hi * (1 + RANGE_TOL)) & (c >= lo * (1 - RANGE_TOL)) \
            & (o <= hi * (1 + RANGE_TOL)) & (o >= lo * (1 - RANGE_TOL))
    b["valid"] = finite & shape
    return b, int((~b["valid"]).sum())


def _session_calendar(bars: pd.DataFrame, ref: str | None) -> tuple[pd.DatetimeIndex, str]:
    good = bars[bars["valid"]]
    if ref and (good["symbol"] == ref).any():
        return pd.DatetimeIndex(sorted(good.loc[good["symbol"] == ref, "date"].unique())), f"{ref} sessions"
    days = pd.DatetimeIndex(sorted(good["date"].unique()))
    span = good.groupby("symbol")["date"].agg(["min", "max"])
    listed = pd.Series([int(((span["min"] <= d) & (span["max"] >= d)).sum()) for d in days], index=days)
    have = good.groupby("date")["symbol"].nunique().reindex(days).fillna(0)
    return days[(have >= 0.5 * listed).to_numpy()], "days with bars for >= half of the listed symbols"


def _coverage(bars: pd.DataFrame, cal: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for sym, g in bars.groupby("symbol", sort=True):
        good = g[g["valid"]]
        if good.empty:
            rows.append(dict(symbol=sym, first_date=None, last_date=None, n_bars=0, expected=0, missing=0,
                             invalid_bars=int(len(g)), max_gap=0))
            continue
        f, l = good["date"].iloc[0], good["date"].iloc[-1]
        idx = cal.searchsorted(pd.DatetimeIndex(good["date"]))
        expected = int(idx[-1] - idx[0] + 1)
        gaps = np.diff(idx) - 1
        rows.append(dict(symbol=sym, first_date=f.date().isoformat(), last_date=l.date().isoformat(),
                         n_bars=int(len(good)), expected=int(expected), missing=int(expected - len(good)),
                         invalid_bars=int(len(g) - len(good)), max_gap=int(gaps.max()) if len(gaps) else 0))
    return pd.DataFrame(rows, columns=["symbol", "first_date", "last_date", "n_bars", "expected", "missing",
                                       "invalid_bars", "max_gap"])


def _to_market_data(df: pd.DataFrame, acts: pd.DataFrame, symbols: list[str], cal: pd.DatetimeIndex,
                    frequency: str, price_mode: str, fields: tuple[str, ...] | None) -> MarketData:
    wide = {}
    for f in ("open", "high", "low", "close", "volume", "close_raw", "factor"):
        wide[f] = df.pivot(index="date", columns="symbol", values=f).reindex(index=cal, columns=symbols)
    if price_mode == "raw":
        fac = wide["factor"]
        out = {"open": wide["open"] / fac, "high": wide["high"] / fac, "low": wide["low"] / fac,
               "close": wide["close_raw"], "volume": wide["volume"]}
        actions = acts.copy() if acts is not None else pd.DataFrame(columns=["symbol", "date", "kind", "value"])
    else:
        out = {k: wide[k] for k in ("open", "high", "low", "close", "volume")}
        actions = pd.DataFrame(columns=["symbol", "date", "kind", "value"])
    if not len(actions):
        actions = pd.DataFrame(columns=["symbol", "date", "kind", "value"])
    else:
        actions["date"] = pd.to_datetime(actions["date"])
    if frequency != "1d":
        out = _resample(out, frequency)
    if fields is not None:
        out = {k: v for k, v in out.items() if k in fields or k == "close"}
    for k in out:
        out[k] = out[k].astype(float)
        out[k].columns.name = None
        out[k].index.name = "date"
    return MarketData(out, actions.reset_index(drop=True), price_mode)


def _resample(out: dict[str, pd.DataFrame], frequency: str) -> dict[str, pd.DataFrame]:
    """Weekly (W-FRI) or monthly bars from daily ones, stamped with the period's last session (adjusted prices
    only, so no corporate action is lost inside a bar)."""
    idx = out["close"].index
    key = idx.to_period("W-FRI" if frequency == "1w" else "M")
    last_session = pd.Series(idx, index=idx).groupby(key).max()           # the bar's timestamp: its last session
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    res = {}
    for f, frame in out.items():
        g = frame.groupby(key)
        r = getattr(g, agg[f])() if f != "volume" else g.sum(min_count=1)
        r.index = pd.DatetimeIndex(last_session.loc[r.index].to_numpy())
        res[f] = r
    return res
