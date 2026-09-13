"""Score -> target weights.

This module owns the whole decision, in one readable sequence, so that every
transformation between a raw cross-sectional score and a tradeable weight vector
is visible in one place and testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from ..config import PortfolioConfig
from ..data.panel import MarketData
from ..signals.momentum import rolling_beta
from ..utils import get_logger
from . import risk as risk_mod
from .risk import SectorGrouping
from .asymmetry import (
    conditional_betas,
    convex_response,
    downside_deviation,
    market_state,
    risk_scaled,
)

LOG = get_logger(__name__)


@dataclass
class ConstructionInputs:
    """Everything panel-shaped the constructor needs, computed once up front."""

    score: pd.DataFrame
    mask: pd.DataFrame
    returns: pd.DataFrame
    vol: pd.DataFrame
    downside: pd.DataFrame
    beta: pd.DataFrame
    beta_up: pd.DataFrame
    beta_down: pd.DataFrame
    state: pd.DataFrame
    sectors: pd.Series
    adv: pd.DataFrame

    @classmethod
    def build(
        cls, data: MarketData, score: pd.DataFrame, mask: pd.DataFrame, cfg: PortfolioConfig,
    ) -> "ConstructionInputs":
        returns = data.returns()
        market = data.market_return()
        asym = cfg.asymmetry
        beta_up, beta_down = conditional_betas(returns, market, asym.downside_window)
        return cls(
            score=score,
            mask=mask.astype(bool),
            returns=returns,
            vol=risk_mod.ewma_vol(returns, cfg.risk.vol_window, cfg.risk.vol_halflife),
            downside=downside_deviation(returns, asym.downside_window),
            beta=rolling_beta(returns, market, cfg.risk.beta_window),
            beta_up=beta_up,
            beta_down=beta_down,
            state=market_state(market, asym.crash_overlay),
            sectors=data.sectors,
            adv=data.adv(21).shift(1),
        )


def ex_ante_volatility(
    weights: pd.Series, window_returns: pd.DataFrame, vols: pd.Series, shrinkage: float,
) -> float:
    """Ex-ante portfolio volatility, degrading gracefully as data thins.

    Names with enough history go through a shrunk covariance; the rest are
    handled diagonally with an assumed average correlation, and the two blocks
    are combined rather than one being silently dropped.
    """
    held = weights[weights.abs() > 1e-12]
    if held.empty:
        return 0.0
    coverage = window_returns[held.index.intersection(window_returns.columns)].notna().mean()
    covered = coverage[coverage >= 0.6].index
    uncovered = held.index.difference(covered)

    vol_cov = 0.0
    if len(covered) >= 2:
        cov = risk_mod.shrunk_covariance(window_returns[covered], shrinkage)
        vol_cov = risk_mod.portfolio_volatility(held.reindex(covered), cov)
    if not np.isfinite(vol_cov):
        vol_cov = 0.0

    vol_rest = 0.0
    if len(uncovered):
        vol_rest = risk_mod.diagonal_volatility(held.reindex(uncovered), vols)

    if vol_rest == 0.0:
        return float(vol_cov)
    rho = 0.15
    return float(np.sqrt(max(vol_cov ** 2 + vol_rest ** 2 + 2 * rho * vol_cov * vol_rest, 0.0)))


class PortfolioConstructor:
    """Turns one row of scores into one vector of target weights."""

    def __init__(self, cfg: PortfolioConfig, inputs: ConstructionInputs):
        self.cfg = cfg
        self.inputs = inputs
        self._vol_scale = 1.0
        self._warned_infeasible = False
        self._grouping = SectorGrouping(inputs.sectors, inputs.score.columns)

    # -- helpers ------------------------------------------------------------

    def _thresholds(self, scores: pd.Series) -> tuple[float, float]:
        long_q = 1.0 - self.cfg.long_quantile
        short_q = self.cfg.short_quantile
        return float(scores.quantile(long_q)), float(scores.quantile(short_q))

    def _normalise_legs(self, weights: pd.Series, short_scale: float) -> pd.Series:
        """Set each leg's gross notional independently.

        Doing this per leg (rather than normalising the whole vector) is what
        makes ``base_short_ratio`` and the crash overlay mean anything: the long
        leg keeps its size while the short leg is dialled up and down.
        """
        out = weights.copy()
        longs, shorts = out > 0, out < 0

        long_gross = float(out[longs].sum())
        if long_gross > 0:
            out[longs] = out[longs] / long_gross

        if self.cfg.style == "long_only":
            out[shorts] = 0.0
            return out

        short_gross = float(-out[shorts].sum())
        if short_gross > 0:
            target = self.cfg.asymmetry.base_short_ratio * short_scale
            out[shorts] = out[shorts] / short_gross * target
        return out

    def _liquidity_cap(self, date: pd.Timestamp, equity: float | None) -> pd.Series | None:
        """Per-name weight cap implied by tradable volume.

        A weight is only meaningful if it can be entered and — more importantly —
        *exited*. Capping each position at a multiple of ADV converts the abstract
        weight into something the execution model can actually fill, and is what
        keeps a backtest from quietly accumulating positions it could never unwind.
        """
        multiple = self.cfg.risk.max_adv_multiple
        if not multiple or multiple <= 0 or not equity or equity <= 0:
            return None
        adv = self.inputs.adv.loc[date]
        return (adv * multiple / equity).replace([np.inf, -np.inf], np.nan)

    def _condition(
        self, weights: pd.Series, betas: pd.Series, short_scale: float,
        liquidity_cap: pd.Series | None = None,
    ) -> pd.Series:
        """Reconcile leg sizing, beta neutrality and position/sector caps.

        These three demands can genuinely conflict, so they are applied in a
        short fixed-point loop rather than once each. Priority when they cannot
        all be met: caps bind absolutely, beta neutrality next, exact leg gross
        last — a book that breaches its position limit is untradeable, whereas
        a book whose short leg is 0.72 instead of 0.75 is merely off-target.
        """
        cfg = self.cfg
        neutralise = cfg.risk.beta_neutral and cfg.style != "long_only"
        out = weights
        for i in range(3):
            if neutralise:
                out = risk_mod.beta_neutralise(out, betas, held_only=True)
            out = risk_mod.apply_constraints(out, cfg.risk, self._grouping)
            if liquidity_cap is not None:
                cap = liquidity_cap.reindex(out.index)
                cap = cap.fillna(cap.median() if cap.notna().any() else np.inf)
                out = out.clip(lower=-cap, upper=cap)
            if i < 2:
                out = self._normalise_legs(out, short_scale)
        self._check_feasibility(out)
        return out

    def _check_feasibility(self, weights: pd.Series) -> None:
        """Warn once if the position cap cannot carry the intended leg size."""
        if self._warned_infeasible or not self.cfg.risk.max_weight:
            return
        longs = weights[weights > 0]
        if len(longs) and not risk_mod.leg_feasibility(
            len(longs), self.cfg.risk.max_weight, float(longs.sum())
        ):
            LOG.warning(
                "position cap %.3f cannot carry a long leg of %.2f across %d names — "
                "widen max_weight, widen the universe, or raise long_quantile",
                self.cfg.risk.max_weight, float(longs.sum()), len(longs),
            )
            self._warned_infeasible = True

    # -- main entry point ---------------------------------------------------

    def target_weights(
        self, date: pd.Timestamp, previous: pd.Series, blocked: set[str] | None = None,
        exposure_scale: float = 1.0, equity: float | None = None,
    ) -> tuple[pd.Series, dict[str, Any]]:
        inp = self.inputs
        cfg = self.cfg
        universe = inp.mask.loc[date]
        scores = inp.score.loc[date].where(universe).dropna()
        if blocked:
            scores = scores.drop(labels=[t for t in blocked if t in scores.index])

        empty = pd.Series(0.0, index=inp.score.columns, dtype=float)  # flat book
        if len(scores) < 20:
            LOG.debug("%s: only %d scored names, staying flat", date.date(), len(scores))
            return empty, {"date": date, "n_long": 0, "n_short": 0, "reason": "thin_universe"}

        long_threshold, short_threshold = self._thresholds(scores)
        if cfg.style == "long_only":
            short_threshold = float("-inf")

        asym = cfg.asymmetry
        if cfg.weighting == "equal":
            long_conv = short_conv = 0.0
        elif cfg.weighting == "rank":
            long_conv = short_conv = 1.0
        elif cfg.weighting == "convex_z":
            long_conv, short_conv = asym.long_convexity, asym.short_convexity
        else:
            raise ValueError(f"unknown weighting '{cfg.weighting}'")

        raw = convex_response(scores, long_conv, short_conv, long_threshold, short_threshold)
        if cfg.weighting == "equal":
            raw = np.sign(raw)

        weights = risk_scaled(
            raw,
            vol=inp.vol.loc[date].reindex(scores.index),
            downside=inp.downside.loc[date].reindex(scores.index),
            beta_up=inp.beta_up.loc[date].reindex(scores.index),
            beta_down=inp.beta_down.loc[date].reindex(scores.index),
            downside_risk_weight=asym.downside_risk_weight,
            downside_beta_penalty=asym.downside_beta_penalty,
        )

        # Widen to the full universe here, once. Everything downstream — leg
        # normalisation, the beta hedge, sector caps — then operates on one fixed
        # vector layout, which is what lets the constraint path be pre-factorised.
        weights = weights.reindex(inp.score.columns).fillna(0.0)

        state = inp.state.loc[date]
        short_scale = float(state.get("short_scale", 1.0))
        if not np.isfinite(short_scale):
            short_scale = 1.0
        weights = self._normalise_legs(weights, short_scale)

        liquidity_cap = self._liquidity_cap(date, equity)
        weights = self._condition(weights, inp.beta.loc[date], short_scale, liquidity_cap)

        window = inp.returns.loc[:date].tail(max(cfg.risk.beta_window, 126))
        ante_vol = ex_ante_volatility(
            weights, window, inp.vol.loc[date], cfg.risk.cov_shrinkage
        )
        weights, self._vol_scale = risk_mod.scale_to_target_vol(
            weights, ante_vol, cfg.risk.target_vol,
            cfg.risk.max_leverage_change, self._vol_scale,
        )

        # long-only books cannot cut a short leg, so the crash state de-risks gross
        overlay = exposure_scale * (short_scale if cfg.style == "long_only" else 1.0)
        weights = weights * overlay

        weights = risk_mod.apply_turnover_controls(
            weights, previous, cfg.turnover.no_trade_band, cfg.turnover.max_turnover
        )
        weights = risk_mod.apply_constraints(weights, cfg.risk, self._grouping)
        if liquidity_cap is not None:
            cap = liquidity_cap.reindex(weights.index)
            cap = cap.fillna(cap.median() if cap.notna().any() else np.inf)
            weights = weights.clip(lower=-cap, upper=cap)
        weights[weights.abs() < 1e-7] = 0.0   # drop untradeable dust

        diagnostics = {
            "date": date,
            "n_long": int((weights > 1e-9).sum()),
            "n_short": int((weights < -1e-9).sum()),
            "gross": float(weights.abs().sum()),
            "net": float(weights.sum()),
            "net_beta": float((weights * inp.beta.loc[date].reindex(weights.index).fillna(1.0)).sum()),
            "ex_ante_vol": float(ante_vol),
            "vol_scale": float(self._vol_scale),
            "short_scale": short_scale,
            "exposure_scale": float(exposure_scale),
            "crash_risk": float(state.get("crash_risk", 0.0)),
            "turnover": float((weights - previous.reindex(weights.index).fillna(0.0)).abs().sum()),
            "liquidity_capped": int(
                0 if liquidity_cap is None
                else (weights.abs() >= liquidity_cap.reindex(weights.index).fillna(np.inf) - 1e-9).sum()
            ),
        }
        return weights, diagnostics
