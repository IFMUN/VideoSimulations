"""Transaction and financing costs.

A momentum book turns over a large fraction of itself every month, so cost
modelling is not a refinement here — it is frequently the difference between a
strategy and an artefact. Three pieces are modelled:

* **Spread**: a fixed cost per unit traded.
* **Impact**: the square-root law, ``k * sigma * sqrt(participation)``, where
  participation is the order as a fraction of that name's ADV. Impact grows
  super-linearly in size, which is what makes capacity finite.
* **Carry**: stock borrow on the short leg, financing on gross above equity, and
  interest earned on idle cash.

The interface is numpy-shaped because it is called once per trading day for the
length of the backtest.
"""
from __future__ import annotations

import numpy as np

from ..config import CostConfig
from ..utils import TRADING_DAYS


class CostModel:
    def __init__(self, cfg: CostConfig):
        self.cfg = cfg

    def apply_participation_cap(
        self, desired_notional: np.ndarray, adv: np.ndarray
    ) -> tuple[np.ndarray, float]:
        """Clip each order to a fraction of that name's ADV.

        This is the mechanism that makes the backtest disagree with itself at
        different AUM: identical weights become unreachable as capital grows,
        which is exactly what a capacity analysis needs to see.
        """
        cap = self.cfg.adv_participation_cap
        if not cap or cap <= 0:
            return desired_notional, 0.0
        limit = np.where(np.isfinite(adv), adv * cap, np.inf)
        filled = np.clip(desired_notional, -limit, limit)
        unfilled = float(np.abs(desired_notional - filled).sum())
        return filled, unfilled

    def trade_costs(
        self, filled_notional: np.ndarray, adv: np.ndarray, daily_vol: np.ndarray
    ) -> tuple[float, float]:
        """Spread and market-impact cost, in currency units."""
        traded = np.abs(filled_notional)
        spread = float(traded.sum()) * self.cfg.spread_bps / 1e4

        with np.errstate(invalid="ignore", divide="ignore"):
            participation = np.where(adv > 0, traded / adv, 0.0)
        participation = np.clip(np.nan_to_num(participation), 0.0, 1.0)
        vol = np.nan_to_num(daily_vol, nan=float(np.nanmedian(daily_vol)) if
                            np.isfinite(daily_vol).any() else 0.02)
        impact = float((self.cfg.impact_coef * vol * np.sqrt(participation) * traded).sum())
        return spread, impact

    def carry(self, short_notional: float, gross: float, equity: float, cash: float) -> float:
        """One day of borrow, financing and cash interest. Negative is a cost."""
        daily = 1.0 / TRADING_DAYS
        borrow = short_notional * self.cfg.borrow_bps / 1e4 * daily
        financing = max(gross - equity, 0.0) * self.cfg.financing_bps / 1e4 * daily
        # Interest is earned on cash up to the book's own equity. Short sales
        # inflate the cash balance, and paying a full rate on those proceeds while
        # separately charging borrow would double-count the same financing leg.
        interest = max(min(cash, equity), 0.0) * self.cfg.cash_yield_bps / 1e4 * daily
        return interest - borrow - financing
