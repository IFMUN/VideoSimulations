"""Covariance, volatility targeting, neutralisation and constraints."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import RiskConfig
from ..utils import TRADING_DAYS


def ewma_vol(returns: pd.DataFrame, window: int, halflife: float) -> pd.DataFrame:
    """Annualised EWMA volatility — responsive without the step artefacts of a
    flat rolling window."""
    mp = max(20, window // 4)
    var = (returns ** 2).ewm(halflife=halflife, min_periods=mp).mean()
    return np.sqrt(var * TRADING_DAYS)


def shrunk_covariance(window_returns: pd.DataFrame, shrinkage: float) -> pd.DataFrame:
    """Sample covariance shrunk toward a diagonal target.

    With a few hundred names and a few hundred days the sample covariance is
    near-singular, and an optimiser will happily lever up its smallest
    eigenvalue. Shrinking to the diagonal is the cheap, robust fix.
    """
    clean = window_returns.dropna(axis=1, how="all")
    if clean.shape[1] == 0:
        return pd.DataFrame()
    filled = clean.fillna(0.0)
    sample = filled.cov().to_numpy() * TRADING_DAYS
    if not np.isfinite(sample).all():
        sample = np.nan_to_num(sample)
    lam = float(np.clip(shrinkage, 0.0, 1.0))
    target = np.diag(np.diag(sample))
    shrunk = (1.0 - lam) * sample + lam * target
    shrunk += np.eye(len(shrunk)) * 1e-10
    return pd.DataFrame(shrunk, index=clean.columns, columns=clean.columns)


def portfolio_volatility(weights: pd.Series, cov: pd.DataFrame) -> float:
    """Annualised ex-ante volatility of a weight vector under ``cov``."""
    if cov.empty:
        return float("nan")
    names = [n for n in weights.index if n in cov.index]
    if not names:
        return float("nan")
    w = weights.reindex(names).fillna(0.0).to_numpy()
    variance = float(w @ cov.loc[names, names].to_numpy() @ w)
    return float(np.sqrt(max(variance, 0.0)))


def diagonal_volatility(weights: pd.Series, vols: pd.Series, avg_corr: float = 0.15) -> float:
    """Fallback ex-ante volatility from per-name vols and an assumed average
    correlation — used when there is not enough history for a covariance matrix."""
    w = weights.fillna(0.0)
    v = vols.reindex(w.index).fillna(vols.median() if len(vols.dropna()) else 0.25)
    idio = float(((w * v) ** 2).sum())
    common = float(avg_corr * ((w * v).sum() ** 2 - ((w * v) ** 2).sum()))
    return float(np.sqrt(max(idio + common, 1e-12)))


def beta_neutralise(weights: pd.Series, betas: pd.Series, held_only: bool = True) -> pd.Series:
    """Remove net beta by subtracting a beta-proportional hedge.

    The hedge is spread in proportion to beta, preserving the cross-sectional
    ordering of the bet while zeroing its market exposure. ``held_only`` confines
    the adjustment to names already in the book — otherwise the hedge sprays a
    dusting of weight across every name in the universe, which is both
    untradeable and makes position counts meaningless.
    """
    b = betas.reindex(weights.index).fillna(1.0)
    adjustable = weights.abs() > 1e-12 if held_only else pd.Series(True, index=weights.index)
    if not adjustable.any():
        return weights
    b_adj = b.where(adjustable, 0.0)
    denom = float((b_adj ** 2).sum())
    if denom <= 1e-12:
        return weights
    net_beta = float((weights * b).sum())
    hedged = weights - b_adj * (net_beta / denom)
    # A hedge may shrink or close a position but must never reverse it: flipping a
    # long into a short would invert the very view the signal expressed.
    crossed = np.sign(hedged) * np.sign(weights) < 0
    return hedged.where(~crossed, 0.0)


class SectorGrouping:
    """Pre-factorised sector labels.

    Re-deriving group codes inside the constraint loop turned out to dominate
    backtest runtime; factorising once per run makes the same constraint roughly
    an order of magnitude cheaper, which is what makes parameter sweeps and
    walk-forward validation practical rather than theoretical.
    """

    def __init__(self, sectors: pd.Series, index: pd.Index):
        aligned = sectors.reindex(index)
        codes, uniques = pd.factorize(aligned, use_na_sentinel=True)
        self.index = index
        self.codes = codes.astype(np.int64)
        self.n_groups = len(uniques)
        self.valid = self.codes >= 0
        self.safe_codes = np.where(self.valid, self.codes, 0)

    def sums(self, values: np.ndarray) -> np.ndarray:
        return np.bincount(
            self.safe_codes, weights=np.where(self.valid, values, 0.0), minlength=self.n_groups
        )


def cap_and_redistribute(weights: np.ndarray, max_weight: float, iterations: int = 12) -> np.ndarray:
    """Clip to a position cap, pushing the excess onto names that are not capped.

    Plain clipping silently shrinks the book, and it shrinks *concentrated* legs
    more than diffuse ones. With an asymmetric construction — a convex long leg
    and a concave short leg — that turns a deliberate design choice into an
    unintended net-short bet. Redistributing preserves each leg's intended gross
    whenever the cap is feasible.
    """
    if not max_weight or max_weight <= 0:
        return weights
    w = np.nan_to_num(weights, copy=True)
    for _ in range(iterations):
        magnitude = np.abs(w)
        over = magnitude > max_weight + 1e-12
        if not over.any():
            break
        signs = np.sign(w)
        excess = np.where(over, (magnitude - max_weight) * signs, 0.0)
        w = np.clip(w, -max_weight, max_weight)
        for sign in (1.0, -1.0):
            leg_excess = float(excess[signs == sign].sum())
            if abs(leg_excess) < 1e-12:
                continue
            room = np.where(np.sign(w) == sign, np.maximum(max_weight - np.abs(w), 0.0), 0.0)
            total_room = float(room.sum())
            if total_room <= 1e-12:
                break  # the cap is infeasible for this leg; the book stays smaller
            w = w + leg_excess * (room / total_room)
    return np.clip(w, -max_weight, max_weight)


def leg_feasibility(n_names: int, max_weight: float, leg_gross: float) -> bool:
    """Can ``n_names`` positions carry ``leg_gross`` under ``max_weight``?"""
    return bool(max_weight <= 0 or n_names * max_weight >= leg_gross - 1e-9)


def apply_constraints(
    weights: pd.Series, cfg: RiskConfig, grouping: "SectorGrouping | None" = None,
) -> pd.Series:
    """Enforce position, sector and gross limits.

    Sector capping is iterative because scaling one sector down changes every
    other sector's share of the book; the loop is bounded so it cannot hang on a
    pathological cross-section.
    """
    index = weights.index
    w = cap_and_redistribute(weights.to_numpy(dtype=float), cfg.max_weight)

    if grouping is not None and cfg.max_sector_net and cfg.max_sector_net > 0:
        cap = cfg.max_sector_net
        positives = np.where(w > 0, w, 0.0)
        negatives = np.where(w < 0, w, 0.0)
        for _ in range(5):
            net = grouping.sums(w)
            breached = np.where(np.abs(net) > cap + 1e-12)[0]
            if not len(breached):
                break
            long_sums = grouping.sums(positives)
            short_sums = grouping.sums(negatives)
            factors_long = np.ones(grouping.n_groups)
            factors_short = np.ones(grouping.n_groups)
            for g in breached:
                # Scale the dominant leg toward zero rather than subtracting a flat
                # amount: subtraction pushes small positions through zero, silently
                # turning a long into a short.
                if net[g] > 0 and long_sums[g] > 1e-12:
                    factors_long[g] = np.clip((cap - short_sums[g]) / long_sums[g], 0.0, 1.0)
                elif net[g] < 0 and short_sums[g] < -1e-12:
                    factors_short[g] = np.clip((-cap - long_sums[g]) / short_sums[g], 0.0, 1.0)
            scale = np.where(w > 0, factors_long[grouping.safe_codes],
                             factors_short[grouping.safe_codes])
            scale = np.where(grouping.valid, scale, 1.0)
            w = w * scale
            positives = np.where(w > 0, w, 0.0)
            negatives = np.where(w < 0, w, 0.0)

    gross = float(np.abs(w).sum())
    if cfg.max_gross and gross > cfg.max_gross > 0:
        w = w * (cfg.max_gross / gross)

    return pd.Series(w, index=index)


def apply_turnover_controls(
    target: pd.Series, previous: pd.Series, no_trade_band: float, max_turnover: float,
) -> pd.Series:
    """Damp trading with a no-trade band, then cap one-way turnover.

    Both controls are expressed **relative to gross exposure**, not in absolute
    weight units. That is not a detail: an absolute cap silently becomes a
    different (usually far tighter) constraint whenever leverage or universe
    size changes, so a config tuned at one gross quietly stops following its own
    signal at another. Relative units keep ``max_turnover = 0.35`` meaning
    "trade about a third of the book" everywhere.

    Names whose target has barely moved keep their existing weight — the classic
    way to stop a noisy score from generating turnover that costs more than the
    signal earns.
    """
    target = target.fillna(0.0)
    previous = previous.reindex(target.index).fillna(0.0)
    gross = max(float(target.abs().sum()), float(previous.abs().sum()))
    if gross <= 1e-12:
        return target
    delta = target - previous

    if no_trade_band > 0:
        held = target.abs() > 1e-12
        typical = float(target.abs()[held].mean()) if held.any() else 0.0
        if typical > 0:
            threshold = no_trade_band * typical
            hold = (delta.abs() < threshold) & (previous != 0.0)
            target = target.where(~hold, previous)
            delta = target - previous

    if max_turnover and max_turnover > 0:
        turnover = float(delta.abs().sum()) / gross
        if turnover > max_turnover:
            target = previous + delta * (max_turnover / turnover)

    return target


def scale_to_target_vol(
    weights: pd.Series, ex_ante_vol: float, target_vol: float,
    max_leverage_change: float, current_scale: float = 1.0,
) -> tuple[pd.Series, float]:
    """Scale the book to a volatility target, rate-limiting the leverage change.

    The rate limit matters: an unconstrained vol target reacts to a volatility
    spike by trading the whole book at the worst possible moment.
    """
    if not np.isfinite(ex_ante_vol) or ex_ante_vol <= 1e-8 or target_vol <= 0:
        return weights, current_scale
    desired = target_vol / ex_ante_vol
    if max_leverage_change and max_leverage_change > 0 and current_scale > 0:
        lo = current_scale * (1.0 - max_leverage_change)
        hi = current_scale * (1.0 + max_leverage_change)
        desired = float(np.clip(desired, lo, hi))
    desired = float(np.clip(desired, 0.05, 10.0))
    return weights * desired, desired
