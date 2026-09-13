"""The single data container every other layer consumes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class MarketData:
    """A rectangular, point-in-time-safe view of an equity cross-section.

    All frames share the same ``DatetimeIndex`` and column order. A name that is
    not yet listed (or already delisted) is ``NaN`` everywhere rather than being
    back-filled — that is what keeps the universe honest about survivorship.
    """

    close: pd.DataFrame                      # total-return adjusted price
    dollar_volume: pd.DataFrame              # traded notional, for costs/capacity
    sectors: pd.Series                       # ticker -> sector label
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.close = self.close.sort_index()
        self.dollar_volume = self.dollar_volume.reindex_like(self.close)
        self.sectors = self.sectors.reindex(self.close.columns)

    # -- derived views ------------------------------------------------------

    @property
    def dates(self) -> pd.DatetimeIndex:
        return pd.DatetimeIndex(self.close.index)

    @property
    def tickers(self) -> pd.Index:
        return self.close.columns

    def returns(self) -> pd.DataFrame:
        """Simple daily returns. The first observation of each name is dropped."""
        rets = self.close.pct_change()
        # ``fill_value`` keeps the shifted frame boolean; see universe_summary.
        first_valid = self.close.notna() & ~self.close.notna().shift(1, fill_value=False)
        return rets.mask(first_valid)

    def adv(self, window: int = 21) -> pd.DataFrame:
        """Trailing average daily dollar volume, known as of each date."""
        return self.dollar_volume.rolling(window, min_periods=max(2, window // 3)).mean()

    def market_return(self, weights: pd.DataFrame | None = None) -> pd.Series:
        """Equal-weighted (or supplied-weight) market proxy of the live universe."""
        rets = self.returns()
        if weights is None:
            return rets.mean(axis=1)
        aligned = weights.reindex_like(rets).fillna(0.0)
        norm = aligned.abs().sum(axis=1).replace(0.0, np.nan)
        return (aligned * rets).sum(axis=1) / norm

    def slice(self, start: str | pd.Timestamp | None, end: str | pd.Timestamp | None) -> "MarketData":
        """Restrict to a date window without disturbing column alignment."""
        idx = self.close.index
        mask = np.ones(len(idx), dtype=bool)
        if start is not None:
            mask &= idx >= pd.Timestamp(start)
        if end is not None:
            mask &= idx <= pd.Timestamp(end)
        return MarketData(
            close=self.close.loc[mask],
            dollar_volume=self.dollar_volume.loc[mask],
            sectors=self.sectors,
            meta=dict(self.meta),
        )

    def describe(self) -> dict[str, Any]:
        live = self.close.notna().sum(axis=1)
        return {
            "start": str(self.dates[0].date()),
            "end": str(self.dates[-1].date()),
            "n_days": int(len(self.dates)),
            "n_tickers": int(self.close.shape[1]),
            "avg_live_names": float(live.mean()),
            "min_live_names": int(live.min()),
            "n_sectors": int(self.sectors.nunique()),
        }
