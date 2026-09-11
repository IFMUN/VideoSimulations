"""Asymmetric risk construction.

Momentum's return distribution is not symmetric, so sizing it symmetrically is a
modelling error rather than a stylistic choice. Four distinct asymmetries are
implemented here, each attacking a different part of the left tail:

1. **Asymmetric signal response** — the long leg is *convex* in the score
   (concentrate in the strongest winners) while the short leg is *concave*
   (spread out, avoid the extreme losers). Crashes are concentrated in the most
   distressed names, which rebound hardest; a concave short leg simply owns less
   of them.
2. **Downside risk in the sizing denominator** — positions are scaled by a blend
   of volatility and *downside* deviation, and penalised for conditional beta
   asymmetry: a long is shrunk when it falls more than it rises, a short is
   shrunk when it rises more than it falls.
3. **Crash-state leg scaling** — after a sustained market decline *and* a
   volatility spike, the short leg is cut to a fraction of its normal size.
   That joint condition, not either one alone, is what precedes momentum
   crashes.
4. **Hysteretic drawdown throttle** — exposure is cut quickly into the
   strategy's own drawdown and restored slowly out of it.

To these is added constant-volatility scaling of the whole book, which is the
single most effective known fix for momentum's negative skew.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import CrashOverlayConfig, DrawdownThrottleConfig, StopConfig
from ..utils import TRADING_DAYS


# ------------------------------------------------------------------ risk measures


def downside_deviation(
    returns: pd.DataFrame, window: int = 252, mar: float = 0.0
) -> pd.DataFrame:
    """Annualised root-mean-square of returns below ``mar``.

    Unlike volatility this does not punish a name for its upside, which is the
    half of the distribution a momentum long is deliberately buying.
    """
    below = (returns - mar).clip(upper=0.0)
    mp = max(20, window // 4)
    msd = (below ** 2).rolling(window, min_periods=mp).mean()
    return np.sqrt(msd) * np.sqrt(TRADING_DAYS)


def conditional_betas(
    returns: pd.DataFrame, market: pd.Series, window: int = 252
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling betas estimated separately on up-market and down-market days.

    Returns ``(beta_up, beta_down)``. Their difference is the beta asymmetry that
    Ang, Chen & Xing show is priced — and that a symmetric risk model cannot see.
    """
    market = market.reindex(returns.index)
    mp = max(20, window // 4)
    out: list[pd.DataFrame] = []
    for up in (True, False):
        day_mask = (market > 0) if up else (market < 0)
        broadcast = pd.DataFrame(
            np.broadcast_to(day_mask.to_numpy()[:, None], returns.shape),
            index=returns.index, columns=returns.columns,
        )
        keep = broadcast & returns.notna()
        y = returns.where(keep)
        x = pd.DataFrame(
            np.broadcast_to(market.to_numpy()[:, None], returns.shape),
            index=returns.index, columns=returns.columns,
        ).where(keep)

        mean_x = x.rolling(window, min_periods=mp).mean()
        mean_y = y.rolling(window, min_periods=mp).mean()
        mean_xy = (x * y).rolling(window, min_periods=mp).mean()
        mean_xx = (x * x).rolling(window, min_periods=mp).mean()
        cov = mean_xy - mean_x * mean_y
        var = (mean_xx - mean_x ** 2).replace(0.0, np.nan)
        out.append((cov / var).clip(-5.0, 5.0))
    return out[0], out[1]


# ------------------------------------------------------------- asymmetric response


def convex_response(
    scores: pd.Series, long_convexity: float, short_convexity: float,
    long_threshold: float, short_threshold: float,
) -> pd.Series:
    """Map a standardised cross-section to signed, unnormalised raw weights.

    ``|distance from threshold| ** convexity``, signed. An exponent above one is
    convex (concentrating), below one is concave (spreading). Using different
    exponents per leg is the cheapest available asymmetry: it costs nothing in
    turnover and changes the shape of the tail you are exposed to.
    """
    raw = pd.Series(0.0, index=scores.index, dtype=float)

    longs = scores >= long_threshold
    if longs.any():
        raw[longs] = np.power((scores[longs] - long_threshold).clip(lower=0.0), long_convexity)

    shorts = scores <= short_threshold
    if shorts.any():
        raw[shorts] = -np.power((short_threshold - scores[shorts]).clip(lower=0.0), short_convexity)

    return raw


def risk_scaled(
    raw: pd.Series, vol: pd.Series, downside: pd.Series, beta_up: pd.Series,
    beta_down: pd.Series, downside_risk_weight: float, downside_beta_penalty: float,
) -> pd.Series:
    """Divide by a blended risk measure and apply the beta-asymmetry penalty.

    The penalty is deliberately one-sided per leg:

    * a **long** is shrunk when ``beta_down > beta_up`` (it participates more in
      falls than in rallies);
    * a **short** is shrunk when ``beta_up > beta_down`` (it rallies harder than
      it falls) — precisely the profile that blows up a short book in a rebound.
    """
    # downside deviation of a symmetric distribution is vol/sqrt(2); rescale so the
    # blend weight is a genuine interpolation rather than a hidden leverage change.
    comparable_downside = downside * np.sqrt(2.0)
    blend_weight = float(np.clip(downside_risk_weight, 0.0, 1.0))
    risk = (1.0 - blend_weight) * vol + blend_weight * comparable_downside
    risk = risk.replace(0.0, np.nan)
    risk = risk.fillna(risk.median())
    if not np.isfinite(risk).all() or risk.isna().all():
        risk = pd.Series(1.0, index=raw.index)

    scaled = raw / risk.reindex(raw.index)

    if downside_beta_penalty > 0:
        gap = (beta_down - beta_up).reindex(raw.index).fillna(0.0)
        long_penalty = np.exp(-downside_beta_penalty * gap.clip(lower=0.0))
        short_penalty = np.exp(-downside_beta_penalty * (-gap).clip(lower=0.0))
        penalty = pd.Series(np.where(scaled >= 0, long_penalty, short_penalty), index=raw.index)
        scaled = scaled * penalty

    return scaled.fillna(0.0)


# ----------------------------------------------------------------- market regimes


def market_state(market_returns: pd.Series, cfg: CrashOverlayConfig) -> pd.DataFrame:
    """Classify each date using only trailing market information.

    ``short_scale`` is the multiplier applied to the short leg: full size in
    normal conditions, cut to ``bear_short_scale`` when the market is both weak
    *and* running hot on volatility. Requiring both avoids cutting the short leg
    through every ordinary volatility spike.

    Two definitions of "weak" are offered because they disagree exactly when it
    matters. ``cumulative`` (negative two-year return) is the classic
    formulation, but it misses a violent drawdown inside a strong multi-year
    bull run — which is precisely the setup for a sharp momentum reversal.
    ``drawdown`` (below a trailing peak) catches those and is the default; the
    ablation harness exists to check whether that choice pays.
    """
    index = (1.0 + market_returns.fillna(0.0)).cumprod()
    cum = index / index.shift(cfg.bear_lookback) - 1.0
    if cfg.bear_rule == "cumulative":
        bear = (cum < 0.0).fillna(False)
    elif cfg.bear_rule == "drawdown":
        peak = index.rolling(cfg.bear_lookback, min_periods=60).max()
        bear = ((index / peak - 1.0) < -abs(cfg.bear_drawdown)).fillna(False)
    else:
        raise ValueError(f"unknown bear_rule '{cfg.bear_rule}'")

    vol = market_returns.rolling(
        cfg.market_vol_window, min_periods=max(20, cfg.market_vol_window // 4)
    ).std() * np.sqrt(TRADING_DAYS)
    baseline = vol.expanding(min_periods=cfg.market_vol_window).median()
    stressed = (vol > cfg.market_vol_threshold * baseline).fillna(False)

    crash_risk = (bear & stressed).astype(float)
    # a short EWMA prevents a single day flipping the book in and out of defence
    smoothed = crash_risk.ewm(halflife=5, min_periods=1).mean()
    short_scale = 1.0 - smoothed * (1.0 - cfg.bear_short_scale)

    return pd.DataFrame({
        "market_cum": cum,
        "market_vol": vol,
        "bear": bear,
        "vol_stressed": stressed,
        "crash_risk": smoothed,
        "short_scale": short_scale.clip(lower=cfg.bear_short_scale, upper=1.0),
    })


# ------------------------------------------------------------- sequential overlays


@dataclass
class DrawdownThrottle:
    """Exposure multiplier driven by the strategy's own equity curve.

    Deliberately hysteretic: the move *down* uses ``cut_halflife`` (fast) and the
    move *up* uses ``recover_halflife`` (slow). Symmetric de-risking sells the
    bottom and buys back into the next leg down; this does not.
    """

    cfg: DrawdownThrottleConfig
    level: float = 1.0
    peak: float = -np.inf
    history: list[float] = field(default_factory=list)
    equity_history: list[float] = field(default_factory=list)

    def update(self, equity: float) -> float:
        if not self.cfg.enabled:
            self.history.append(1.0)
            return 1.0
        self.equity_history.append(equity)
        window = self.cfg.peak_window_days
        if window and window > 0:
            # A trailing high-water mark. Against an all-time peak a single deep
            # crash pins the book at its floor for years, which converts a risk
            # control into a permanent handicap; against a trailing peak the
            # strategy is allowed to re-risk once the damage is old news.
            self.peak = max(self.equity_history[-window:])
            if len(self.equity_history) > 4 * window:
                del self.equity_history[: 2 * window]
        else:
            self.peak = max(self.peak, equity)
        drawdown = equity / self.peak - 1.0 if self.peak > 0 else 0.0

        span = max(self.cfg.full_dd - self.cfg.start_dd, 1e-9)
        severity = float(np.clip((-drawdown - self.cfg.start_dd) / span, 0.0, 1.0))
        target = 1.0 - severity * (1.0 - self.cfg.floor)

        halflife = self.cfg.cut_halflife if target < self.level else self.cfg.recover_halflife
        alpha = 1.0 - 0.5 ** (1.0 / max(halflife, 1e-6))
        self.level += alpha * (target - self.level)
        self.level = float(np.clip(self.level, self.cfg.floor, 1.0))
        self.history.append(self.level)
        return self.level


@dataclass
class VolatilityScaler:
    """Constant-volatility scaling of the whole book (Barroso & Santa-Clara).

    Momentum's volatility is highly forecastable from its own recent realised
    volatility; targeting it removes most of the negative skew that makes the
    raw strategy uninvestable.
    """

    cfg: CrashOverlayConfig
    returns: list[float] = field(default_factory=list)

    def update(self, strategy_return: float) -> None:
        self.returns.append(float(strategy_return))

    def scale(self) -> float:
        if not self.cfg.enabled:
            return 1.0
        window = self.cfg.vol_window
        if len(self.returns) < max(20, window // 4):
            return 1.0
        recent = np.asarray(self.returns[-window:], dtype=float)
        realised = float(np.std(recent, ddof=1)) * np.sqrt(TRADING_DAYS)
        if realised <= 1e-8:
            return self.cfg.max_scale
        return float(np.clip(self.cfg.target_strategy_vol / realised,
                             self.cfg.min_scale, self.cfg.max_scale))


class StopTracker:
    """Per-name trailing stop that fires only on positions that are *losing*.

    The trailing breach alone would also cut winners that gave back part of a
    large gain — truncating exactly the right tail momentum exists to harvest.
    Requiring the position to be underwater as well makes the rule one-sided.

    State is held in numpy arrays aligned to a fixed ticker index rather than in
    dicts: the engine calls ``mark`` once per day for the life of the backtest,
    so a per-name Python loop here dominates total runtime.
    """

    def __init__(self, cfg: StopConfig, tickers: pd.Index):
        self.cfg = cfg
        self.tickers = pd.Index(tickers)
        n = len(self.tickers)
        self.side = np.zeros(n, dtype=np.int8)
        self.entry = np.full(n, np.nan)
        self.best = np.full(n, np.nan)
        self.cooldown = np.zeros(n, dtype=np.int32)
        self.stops_fired = 0

    # -- state updates ------------------------------------------------------

    def sync(self, notional: np.ndarray, prices: np.ndarray) -> None:
        """Record entries/exits after a fill, given post-trade signed notionals."""
        new_side = np.sign(notional).astype(np.int8)
        new_side[~np.isfinite(prices) | (np.abs(notional) < 1e-9)] = 0

        closed = (self.side != 0) & (new_side == 0)
        self.side[closed] = 0
        self.entry[closed] = np.nan
        self.best[closed] = np.nan

        opened = (new_side != 0) & (new_side != self.side)
        self.side[opened] = new_side[opened]
        self.entry[opened] = prices[opened]
        self.best[opened] = prices[opened]

    def mark(self, prices: np.ndarray) -> np.ndarray:
        """Advance high/low-water marks; return a boolean mask of names to exit."""
        np.subtract(self.cooldown, 1, out=self.cooldown, where=self.cooldown > 0)

        active = (self.side != 0) & np.isfinite(prices) & (prices > 0)
        if not active.any():
            return np.zeros(len(self.side), dtype=bool)

        longs = active & (self.side > 0)
        shorts = active & (self.side < 0)
        self.best[longs] = np.maximum(self.best[longs], prices[longs])
        self.best[shorts] = np.minimum(self.best[shorts], prices[shorts])

        if not self.cfg.enabled:
            return np.zeros(len(self.side), dtype=bool)

        with np.errstate(invalid="ignore", divide="ignore"):
            adverse = np.where(
                self.side > 0,
                (self.best - prices) / self.best,
                (prices - self.best) / self.best,
            )
            unrealised = (prices / self.entry - 1.0) * self.side

        triggered = active & (adverse >= self.cfg.trailing) & (unrealised < 0.0)
        if triggered.any():
            self.stops_fired += int(triggered.sum())
            self.cooldown[triggered] = self.cfg.cooldown_days
            self.side[triggered] = 0
            self.entry[triggered] = np.nan
            self.best[triggered] = np.nan
        return triggered

    def blocked(self) -> set[str]:
        """Names in post-stop cooldown, which the constructor must not re-enter."""
        if not self.cfg.enabled:
            return set()
        return set(self.tickers[self.cooldown > 0])
