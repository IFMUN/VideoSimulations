"""Cross-sectional transforms applied between raw signals and portfolio weights.

Raw signals are not comparable: a 12-month return and a distance-to-52-week-high
live on different scales, have different outlier profiles, and carry different
amounts of unintended sector and beta exposure. These transforms put them on a
common footing so that a blend weight means what it says.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import TransformConfig
from ..utils import cross_sectional_rank, cross_sectional_z, demean_by_group, winsorize


def neutralise_beta(scores: pd.DataFrame, betas: pd.DataFrame) -> pd.DataFrame:
    """Remove the part of each cross-section explained by beta.

    Regress the score on beta row by row and keep the residual, so a "momentum"
    bet does not quietly become a leveraged market bet.
    """
    aligned = betas.reindex_like(scores)
    valid = scores.notna() & aligned.notna()
    s = scores.where(valid)
    b = aligned.where(valid)
    b_mean = b.mean(axis=1)
    s_mean = s.mean(axis=1)
    bc = b.sub(b_mean, axis=0)
    sc = s.sub(s_mean, axis=0)
    denom = (bc ** 2).sum(axis=1).replace(0.0, np.nan)
    slope = (bc * sc).sum(axis=1) / denom
    resid = sc.sub(bc.mul(slope, axis=0))
    return resid.where(valid)


def apply_transforms(
    scores: pd.DataFrame,
    cfg: TransformConfig,
    sectors: pd.Series | None = None,
    betas: pd.DataFrame | None = None,
    mask: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Winsorise -> standardise -> neutralise -> smooth, in that order.

    Order matters. Standardising before winsorising lets a single outlier set the
    scale; neutralising before standardising lets one sector's dispersion
    dominate; smoothing last means the turnover damping applies to the quantity
    actually traded.
    """
    out = scores
    if mask is not None:
        out = out.where(mask.reindex_like(out).astype(bool))

    out = winsorize(out, cfg.winsorize)

    if cfg.standardize == "z":
        out = cross_sectional_z(out)
    elif cfg.standardize == "rank":
        out = cross_sectional_z(cross_sectional_rank(out))
    elif cfg.standardize not in ("none", None):
        raise ValueError(f"unknown standardize option '{cfg.standardize}'")

    if cfg.sector_neutral and sectors is not None:
        out = demean_by_group(out, sectors)
    if cfg.beta_neutral_signal and betas is not None:
        out = neutralise_beta(out, betas)

    # re-standardise: neutralisation changes the scale
    if cfg.standardize in ("z", "rank") and (cfg.sector_neutral or cfg.beta_neutral_signal):
        out = cross_sectional_z(out)

    if cfg.smooth_halflife and cfg.smooth_halflife > 0:
        out = out.ewm(halflife=cfg.smooth_halflife, min_periods=1).mean()
        out = out.where(scores.notna())

    if mask is not None:
        out = out.where(mask.reindex_like(out).astype(bool))
    return out


def blend(components: dict[str, pd.DataFrame], weights: dict[str, float]) -> pd.DataFrame:
    """Weighted average of standardised components, renormalised by live weight.

    Renormalisation matters: when one component is missing for a name (short
    history, say), the blend should be the average of what *is* known rather
    than silently shrinking that name toward zero.
    """
    if not components:
        raise ValueError("no signal components to blend")
    total = None
    weight_sum = None
    for name, frame in components.items():
        w = float(weights.get(name, 0.0))
        if w == 0.0:
            continue
        contrib = frame.fillna(0.0) * w
        live = frame.notna().astype(float) * abs(w)
        total = contrib if total is None else total.add(contrib, fill_value=0.0)
        weight_sum = live if weight_sum is None else weight_sum.add(live, fill_value=0.0)
    if total is None:
        raise ValueError("all signal weights are zero")
    return (total / weight_sum.replace(0.0, np.nan)).where(weight_sum > 0)
