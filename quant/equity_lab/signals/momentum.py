"""The momentum family, plus the short-horizon effects that interact with it.

Every function returns a ``dates x tickers`` frame whose value on date ``t`` uses
**only** information observable at the close of ``t``. Execution lag is applied
later, in the engine, so the lag policy lives in exactly one place.

References (paraphrased, implemented rather than cited verbatim):
  Jegadeesh & Titman (1993)      12-1 cross-sectional momentum
  Blitz, Huij & Martens (2011)   residual momentum
  George & Hwang (2004)          52-week-high proximity
  Da, Gurun & Warachka (2014)    information discreteness ("frog in the pan")
  Lehmann (1990) / Jegadeesh (1990)  short-horizon reversal
  Moskowitz, Ooi & Pedersen (2012)   time-series momentum
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.panel import MarketData
from ..utils import TRADING_DAYS


def _min_periods(window: int, frac: float = 0.75) -> int:
    return max(2, int(window * frac))


def realised_vol(returns: pd.DataFrame, window: int = 63) -> pd.DataFrame:
    """Annualised trailing standard deviation of daily returns."""
    return returns.rolling(window, min_periods=_min_periods(window)).std() * np.sqrt(TRADING_DAYS)


def rolling_beta(returns: pd.DataFrame, market: pd.Series, window: int = 252) -> pd.DataFrame:
    """Trailing OLS beta of each name against the market proxy."""
    mp = _min_periods(window)
    market = market.reindex(returns.index)
    cov = returns.rolling(window, min_periods=mp).cov(market)
    var = market.rolling(window, min_periods=mp).var()
    return cov.div(var.replace(0.0, np.nan), axis=0)


# --------------------------------------------------------------------- core family


def momentum(data: MarketData, lookback: int = 252, gap: int = 21) -> pd.DataFrame:
    """Classic cumulative return from ``t-lookback`` to ``t-gap``.

    The gap skips the most recent month, whose short-horizon reversal otherwise
    contaminates the continuation signal.
    """
    close = data.close
    return close.shift(gap) / close.shift(lookback) - 1.0


def risk_adjusted_momentum(
    data: MarketData, lookback: int = 252, gap: int = 21, vol_window: int = 126
) -> pd.DataFrame:
    """Momentum divided by trailing volatility — a cross-sectional Sharpe sort.

    Ranking on raw return systematically over-weights high-volatility names,
    which is a volatility bet wearing a momentum costume.
    """
    raw = momentum(data, lookback, gap)
    vol = realised_vol(data.returns(), vol_window).shift(gap)
    return raw / vol.replace(0.0, np.nan)


def residual_momentum(
    data: MarketData, lookback: int = 252, gap: int = 21, beta_window: int = 252
) -> pd.DataFrame:
    """Momentum of market-residual returns, scaled by residual volatility.

    Stripping the market component removes the dynamic beta exposure that makes
    plain momentum crash when the market reverses — it attacks the same problem
    the risk layer attacks, one level earlier.
    """
    returns = data.returns()
    market = data.market_return()
    beta = rolling_beta(returns, market, beta_window)

    mp = _min_periods(beta_window)
    mean_asset = returns.rolling(beta_window, min_periods=mp).mean()
    mean_market = market.rolling(beta_window, min_periods=mp).mean()
    alpha = mean_asset.sub(beta.mul(mean_market, axis=0))
    resid = returns.sub(alpha).sub(beta.mul(market, axis=0))

    window = lookback - gap
    if window <= 0:
        raise ValueError("lookback must exceed gap")
    shifted = resid.shift(gap)
    total = shifted.rolling(window, min_periods=_min_periods(window)).sum()
    dispersion = shifted.rolling(window, min_periods=_min_periods(window)).std()
    return total / dispersion.replace(0.0, np.nan)


def pct_52w_high(data: MarketData, window: int = 252) -> pd.DataFrame:
    """Price relative to its trailing high; near-highs keep drifting up.

    A useful complement to return-based momentum because it is anchor-based: it
    fires on names that have *held* their gains rather than merely made them.
    """
    close = data.close
    high = close.rolling(window, min_periods=_min_periods(window)).max()
    return close / high.replace(0.0, np.nan) - 1.0


def information_discreteness(
    data: MarketData, lookback: int = 252, gap: int = 21
) -> pd.DataFrame:
    """Signed continuity of the path that produced the momentum.

    ``ID = sign(cumulative return) x (%negative days - %positive days)``.
    Momentum built from many small, same-direction moves (``ID`` very negative)
    is absorbed slowly by investors and continues more reliably than the same
    total return delivered by a few jumps. Returned so that **higher is more
    continuous**, ready to be used on its own or multiplied onto momentum.
    """
    returns = data.returns()
    window = lookback - gap
    shifted = returns.shift(gap)
    mp = _min_periods(window)
    pos = (shifted > 0).where(shifted.notna()).rolling(window, min_periods=mp).mean()
    neg = (shifted < 0).where(shifted.notna()).rolling(window, min_periods=mp).mean()
    sign = np.sign(momentum(data, lookback, gap))
    return -(sign * (neg - pos))


def frog_in_pan_momentum(
    data: MarketData, lookback: int = 252, gap: int = 21, tilt: float = 0.5
) -> pd.DataFrame:
    """Momentum modulated by information continuity.

    ``tilt`` controls how much the continuity term reshapes the momentum sort;
    at 0 it degenerates to plain momentum, which makes it a clean A/B knob.
    """
    from ..utils import cross_sectional_z

    base = cross_sectional_z(momentum(data, lookback, gap))
    continuity = cross_sectional_z(information_discreteness(data, lookback, gap))
    return base * (1.0 + tilt * continuity.clip(-3.0, 3.0))


def momentum_consistency(
    data: MarketData, lookback: int = 252, gap: int = 21, step: int = 21
) -> pd.DataFrame:
    """Fraction of trailing sub-periods with a positive return, centred at zero.

    Twelve up-months and one flat month is a different object from one +60%
    month and eleven flat ones, even when the cumulative returns match.
    """
    close = data.close
    blocks = []
    start = gap
    while start + step <= lookback:
        blocks.append((close.shift(start) / close.shift(start + step) - 1.0) > 0)
        start += step
    if not blocks:
        raise ValueError("lookback/step produce no sub-periods")
    stacked = sum(block.astype(float) for block in blocks) / len(blocks)
    valid = close.shift(lookback).notna()
    return (stacked - 0.5).where(valid)


# ------------------------------------------------------- short horizon & time series


def short_term_reversal(data: MarketData, window: int = 21, skip: int = 0) -> pd.DataFrame:
    """Negative of the recent return: crowded short-horizon moves mean-revert."""
    close = data.close
    return -(close.shift(skip) / close.shift(skip + window) - 1.0)


def time_series_trend(data: MarketData, fast: int = 50, slow: int = 200) -> pd.DataFrame:
    """Fast/slow moving-average spread, normalised by volatility.

    Unlike the cross-sectional signals this one has a meaningful *sign* per name,
    which is what a long-only or trend-filtered variant needs.
    """
    close = data.close
    fast_ma = close.rolling(fast, min_periods=_min_periods(fast)).mean()
    slow_ma = close.rolling(slow, min_periods=_min_periods(slow)).mean()
    vol = realised_vol(data.returns(), slow).replace(0.0, np.nan)
    return (fast_ma / slow_ma.replace(0.0, np.nan) - 1.0) / vol


def low_volatility(data: MarketData, window: int = 126) -> pd.DataFrame:
    """Negative trailing volatility — the defensive leg of a momentum blend."""
    return -realised_vol(data.returns(), window)


REGISTRY = {
    "momentum": momentum,
    "risk_adjusted_momentum": risk_adjusted_momentum,
    "residual_momentum": residual_momentum,
    "pct_52w_high": pct_52w_high,
    "information_discreteness": information_discreteness,
    "frog_in_pan_momentum": frog_in_pan_momentum,
    "momentum_consistency": momentum_consistency,
    "short_term_reversal": short_term_reversal,
    "time_series_trend": time_series_trend,
    "low_volatility": low_volatility,
}
