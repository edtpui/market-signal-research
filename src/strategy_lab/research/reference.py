"""Reference data for matching and benchmarks: sectors and (optionally) market capitalization.

Sectors come from SEC SIC codes (settings [signal_research] sector_file, a CSV with ticker and sic columns, e.g.
Lab/data/sec_edgar/companies.csv). SIC is not GICS: the mapping below is an approximation at the level
of SIC major groups and is CURRENT, not point-in-time (a company's SIC code today is applied to every date). Every
result that uses sectors says so.

Market capitalization needs a point-in-time file (settings market_cap_file: CSV ticker, date, market_cap). None is
configured in this workspace; matching on market cap is then refused, never approximated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

SECTORS = ("Communication Services", "Consumer Discretionary", "Consumer Staples", "Energy", "Financials",
           "Health Care", "Industrials", "Information Technology", "Materials", "Real Estate", "Utilities")
SECTOR_ETF = {"Communication Services": "XLC", "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
              "Energy": "XLE", "Financials": "XLF", "Health Care": "XLV", "Industrials": "XLI",
              "Information Technology": "XLK", "Materials": "XLB", "Real Estate": "XLRE", "Utilities": "XLU"}

# (first SIC, last SIC, sector): checked in order, so specific ranges come before their major group.
SIC_RULES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Consumer Staples"),
    (1000, 1099, "Materials"), (1200, 1299, "Energy"), (1300, 1399, "Energy"), (1400, 1499, "Materials"),
    (1500, 1799, "Industrials"),
    (2000, 2199, "Consumer Staples"), (2200, 2399, "Consumer Discretionary"), (2400, 2499, "Materials"),
    (2500, 2599, "Consumer Discretionary"), (2600, 2699, "Materials"), (2700, 2799, "Communication Services"),
    (2830, 2839, "Health Care"), (2840, 2849, "Consumer Staples"), (2800, 2899, "Materials"),
    (2900, 2999, "Energy"), (3000, 3099, "Materials"), (3100, 3199, "Consumer Discretionary"),
    (3200, 3399, "Materials"), (3400, 3499, "Industrials"), (3570, 3579, "Information Technology"),
    (3500, 3599, "Industrials"), (3630, 3639, "Consumer Discretionary"), (3600, 3699, "Information Technology"),
    (3710, 3719, "Consumer Discretionary"), (3750, 3759, "Consumer Discretionary"), (3700, 3799, "Industrials"),
    (3826, 3826, "Health Care"), (3840, 3859, "Health Care"), (3800, 3899, "Information Technology"),
    (3900, 3999, "Consumer Discretionary"),
    (4000, 4799, "Industrials"), (4800, 4899, "Communication Services"), (4950, 4959, "Industrials"),
    (4900, 4999, "Utilities"),
    (5120, 5129, "Health Care"), (5000, 5099, "Industrials"), (5100, 5199, "Consumer Staples"),
    (5400, 5499, "Consumer Staples"), (5910, 5912, "Consumer Staples"), (5200, 5999, "Consumer Discretionary"),
    (6798, 6798, "Real Estate"), (6500, 6599, "Real Estate"), (6000, 6799, "Financials"),
    (7000, 7099, "Consumer Discretionary"), (7200, 7299, "Consumer Discretionary"),
    (7370, 7379, "Information Technology"), (7300, 7399, "Industrials"), (7500, 7599, "Consumer Discretionary"),
    (7800, 7899, "Communication Services"), (7900, 7999, "Consumer Discretionary"), (8000, 8099, "Health Care"),
    (8200, 8299, "Consumer Discretionary"), (8300, 8399, "Health Care"), (8731, 8731, "Health Care"),
    (8700, 8799, "Industrials"),
)


def sic_to_sector(sic) -> str | None:
    try:
        s = int(float(sic))
    except (TypeError, ValueError):
        return None
    for lo, hi, sector in SIC_RULES:
        if lo <= s <= hi:
            return sector
    return None


@dataclass
class ReferenceData:
    sector_file: Path | None = None
    market_cap_file: Path | None = None
    _sectors: dict[str, str] | None = field(default=None, repr=False)
    _mcap: pd.DataFrame | None = field(default=None, repr=False)

    @property
    def has_sectors(self) -> bool:
        return self.sector_file is not None and Path(self.sector_file).is_file()

    @property
    def has_market_cap(self) -> bool:
        return self.market_cap_file is not None and Path(self.market_cap_file).is_file()

    def availability(self) -> dict:
        return dict(sector=dict(available=self.has_sectors, source=str(self.sector_file) if self.sector_file else None,
                                note="SEC SIC codes mapped to approximate GICS-like sectors; current, not "
                                     "point-in-time"),
                    market_cap=dict(available=self.has_market_cap,
                                    source=str(self.market_cap_file) if self.market_cap_file else None,
                                    note="needs a point-in-time CSV (ticker, date, market_cap); none is configured"
                                    if not self.has_market_cap else "point-in-time file, as-of joined"))

    def sectors(self) -> dict[str, str]:
        if self._sectors is None:
            if not self.has_sectors:
                raise ValueError("sector reference data is not configured ([signal_research] sector_file)")
            df = pd.read_csv(self.sector_file, dtype={"ticker": str})
            cols = {c.lower(): c for c in df.columns}
            if "ticker" not in cols or "sic" not in cols:
                raise ValueError(f"{self.sector_file}: needs ticker and sic columns")
            out = {}
            for t, sic in zip(df[cols["ticker"]], df[cols["sic"]]):
                sec = sic_to_sector(sic)
                if isinstance(t, str) and sec:
                    out[t.strip().upper()] = sec
            self._sectors = out
        return self._sectors

    def sector_of(self, symbols: list[str]) -> list[str | None]:
        m = self.sectors()
        return [m.get(s.upper()) for s in symbols]

    def market_cap(self, symbols: list[str], index: pd.DatetimeIndex) -> np.ndarray:
        """(sessions x symbols) market cap as known on each session (the latest value dated on or before it)."""
        if not self.has_market_cap:
            raise ValueError("market-cap reference data is not configured ([signal_research] market_cap_file); "
                             "matching on market cap is refused rather than approximated")
        if self._mcap is None:
            df = pd.read_csv(self.market_cap_file, dtype={"ticker": str})
            need = {"ticker", "date", "market_cap"}
            if not need <= set(df.columns):
                raise ValueError(f"{self.market_cap_file}: needs columns {sorted(need)}")
            df["date"] = pd.to_datetime(df["date"])
            self._mcap = df
        wide = self._mcap.pivot_table(index="date", columns="ticker", values="market_cap", aggfunc="last")
        wide = wide.reindex(columns=symbols)
        out = wide.reindex(wide.index.union(index)).sort_index().ffill().reindex(index)
        return out.to_numpy(dtype=float)
