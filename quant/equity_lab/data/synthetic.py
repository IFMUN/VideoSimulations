"""A generative market used when vendor data is unavailable (CI, sandboxes, demos).

This is not noise dressed up as prices. It is built so that the phenomena the
strategy layer claims to exploit — and the pathologies it claims to defend
against — are actually present, and are present for stated reasons:

* **Cross-sectional momentum** comes from a persistent latent drift ``a[i,t]``
  following an AR(1) with a multi-month half-life. Sorting on trailing return
  therefore recovers a genuinely predictive state variable.
* **Short-horizon reversal** comes from additive microstructure noise in the
  *observed* log price, which induces negative first-order autocorrelation
  exactly the way bid-ask bounce does.
* **Volatility clustering and fat tails** come from a GARCH(1,1) market
  variance driven by Student-t shocks.
* **Momentum crashes** come from a rebound regime: once the market is deep in
  drawdown, up-days pay prior losers disproportionately. This is the event that
  destroys a naive short leg, so it must exist for the asymmetric construction
  to be testable rather than decorative.
* **Downside beta asymmetry** comes from per-name betas that differ in up and
  down markets.
* **Survivorship** is handled by listing and delisting names through time.

Everything is seeded; the same seed gives the same panel on any machine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import SyntheticConfig
from ..utils import TRADING_DAYS, get_logger
from .panel import MarketData

LOG = get_logger(__name__)

_SECTOR_NAMES = [
    "Technology", "Financials", "Healthcare", "Industrials",
    "Energy", "Staples", "Discretionary", "Utilities",
    "Materials", "RealEstate", "Communications",
]


def _standardise_t(rng: np.random.Generator, df: float, size) -> np.ndarray:
    """Student-t shocks rescaled to unit variance (so vol targets mean what they say)."""
    raw = rng.standard_t(df, size=size)
    return raw / np.sqrt(df / (df - 2.0))


def generate(cfg: SyntheticConfig, start: str, end: str, seed: int = 7) -> MarketData:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start=start, end=end)
    n_days, n_assets = len(dates), cfg.n_assets
    if n_days < 300:
        raise ValueError("synthetic panel needs at least ~300 business days")

    n_sectors = min(cfg.n_sectors, len(_SECTOR_NAMES))
    sector_idx = rng.integers(0, n_sectors, size=n_assets)
    tickers = [f"SY{i:04d}" for i in range(n_assets)]
    sectors = pd.Series([_SECTOR_NAMES[s] for s in sector_idx], index=tickers, name="sector")

    # --- static cross-sectional characteristics ---------------------------
    beta = np.clip(rng.normal(1.0, 0.32, n_assets), 0.25, 2.2)
    beta_asym = rng.normal(0.0, 0.30, n_assets)          # extra beta on down days
    beta_down = np.clip(beta * (1.0 + beta_asym), 0.2, 3.0)
    sector_load = np.clip(rng.normal(1.0, 0.3, n_assets), 0.0, 2.5)
    idio_vol = np.clip(
        rng.lognormal(np.log(cfg.idio_vol_mean), cfg.idio_vol_disp, n_assets), 0.10, 1.2
    ) / np.sqrt(TRADING_DAYS)

    # --- listing / delisting windows (survivorship is not assumed away) ----
    listed_from = np.zeros(n_assets, dtype=int)
    listed_to = np.full(n_assets, n_days, dtype=int)
    n_churn = int(cfg.listing_turnover * n_assets * n_days / TRADING_DAYS)
    for i in rng.choice(n_assets, size=min(n_churn, n_assets // 2), replace=False):
        if rng.random() < 0.5:
            listed_from[i] = int(rng.integers(0, max(1, n_days - TRADING_DAYS)))
        else:
            listed_to[i] = int(rng.integers(TRADING_DAYS * 2, n_days))

    # --- factor paths ------------------------------------------------------
    daily_mkt_var = (cfg.market_vol ** 2) / TRADING_DAYS
    omega = daily_mkt_var * (1.0 - cfg.garch_alpha - cfg.garch_beta)
    h = daily_mkt_var
    mkt_shocks = _standardise_t(rng, cfg.idio_tail_df, n_days)
    sector_shocks = rng.standard_normal((n_days, n_sectors)) * cfg.sector_vol / np.sqrt(TRADING_DAYS)
    idio_shocks = _standardise_t(rng, cfg.idio_tail_df, (n_days, n_assets)) * idio_vol

    # latent drift: stationary AR(1) with annualised dispersion ``alpha_vol``
    phi = cfg.alpha_persistence
    drift_sd = cfg.alpha_vol / TRADING_DAYS
    alpha = rng.normal(0.0, drift_sd, n_assets)
    alpha_innov = rng.standard_normal((n_days, n_assets)) * drift_sd * np.sqrt(1.0 - phi ** 2)

    mkt_ret = np.zeros(n_days)
    asset_ret = np.zeros((n_days, n_assets))
    rel_perf = np.zeros(n_assets)      # EWMA of relative return ~ trailing momentum
    rel_decay = 1.0 - 1.0 / 120.0
    regime = np.zeros(n_days, dtype=np.int8)

    mkt_level = 1.0
    mu_mkt = cfg.market_drift / TRADING_DAYS
    # Drawdown is measured against a *trailing* high-water mark: a peak from a
    # decade ago should not make today a bear market. A monotonic deque keeps the
    # rolling max exact in O(1) amortised.
    from collections import deque

    peak_window = 756
    peak_q: deque[tuple[int, float]] = deque()

    for t in range(n_days):
        h = omega + cfg.garch_alpha * (mkt_ret[t - 1] ** 2 if t else daily_mkt_var) + cfg.garch_beta * h
        rm = mu_mkt + np.sqrt(h) * mkt_shocks[t]
        rm = float(np.clip(rm, -0.20, 0.20))
        mkt_ret[t] = rm
        mkt_level *= 1.0 + rm
        while peak_q and peak_q[0][0] <= t - peak_window:
            peak_q.popleft()
        while peak_q and peak_q[-1][1] <= mkt_level:
            peak_q.pop()
        peak_q.append((t, mkt_level))
        drawdown = mkt_level / peak_q[0][1] - 1.0

        alpha = phi * alpha + alpha_innov[t]

        b = beta_down if rm < 0 else beta
        r = b * rm + sector_load * sector_shocks[t, sector_idx] + alpha + idio_shocks[t]

        # momentum crash: deep drawdown + an up day pays prior losers
        stressed = drawdown < cfg.bear_threshold
        regime[t] = 2 if (stressed and rm > 0) else (1 if stressed else 0)
        if regime[t] == 2:
            sd = rel_perf.std()
            if sd > 1e-9:
                z = (rel_perf - rel_perf.mean()) / sd
                r = r + cfg.crash_intensity * np.clip(-z, -3.0, 3.0) * rm

        r = np.clip(r, -0.40, 0.40)
        asset_ret[t] = r
        rel_perf = rel_decay * rel_perf + (r - rm)

    # --- prices, microstructure noise, listing masks -----------------------
    true_price = 100.0 * np.cumprod(1.0 + asset_ret, axis=0)
    noise_sd = cfg.reversal_noise_bps / 1e4
    observed = true_price * np.exp(rng.normal(0.0, noise_sd, (n_days, n_assets)))

    row = np.arange(n_days)[:, None]
    live = (row >= listed_from[None, :]) & (row < listed_to[None, :])
    observed = np.where(live, observed, np.nan)

    # --- dollar volume: level per name, with a plausible vol/volume link ---
    adv_level = np.exp(rng.normal(cfg.adv_log_mean, cfg.adv_log_disp, n_assets))
    vol_kick = 1.0 + 3.0 * np.abs(asset_ret)
    noise = np.exp(rng.normal(0.0, 0.35, (n_days, n_assets)))
    dollar_volume = np.where(live, adv_level[None, :] * vol_kick * noise, np.nan)

    close = pd.DataFrame(observed, index=dates, columns=tickers)
    dvol = pd.DataFrame(dollar_volume, index=dates, columns=tickers)

    meta = {
        "source": "synthetic",
        "seed": seed,
        "config": cfg.__dict__.copy(),
        "true_market": pd.Series(mkt_ret, index=dates),
        "regime": pd.Series(regime, index=dates).map({0: "calm", 1: "bear", 2: "rebound"}),
        "true_beta": pd.Series(beta, index=tickers),
        "true_beta_down": pd.Series(beta_down, index=tickers),
        "synthetic": True,
    }
    LOG.info(
        "synthetic panel: %d days x %d names, %.1f%% of days in a stressed regime",
        n_days, n_assets, 100.0 * float((regime > 0).mean()),
    )
    return MarketData(close=close, dollar_volume=dvol, sectors=sectors, meta=meta)
