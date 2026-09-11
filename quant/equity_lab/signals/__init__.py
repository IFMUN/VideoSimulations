"""Signal computation: raw scores in, one blended, neutralised score out."""
from __future__ import annotations

import pandas as pd

from ..config import SignalSpec, TransformConfig
from ..data.panel import MarketData
from ..utils import get_logger
from .momentum import REGISTRY, rolling_beta
from .transforms import apply_transforms, blend, neutralise_beta

LOG = get_logger(__name__)

__all__ = [
    "REGISTRY", "apply_transforms", "blend", "neutralise_beta", "rolling_beta",
    "compute_signal", "build_score", "signal_diagnostics",
]


def compute_signal(name: str, data: MarketData, params: dict | None = None) -> pd.DataFrame:
    if name not in REGISTRY:
        raise KeyError(f"unknown signal '{name}'; available: {sorted(REGISTRY)}")
    return REGISTRY[name](data, **(params or {}))


def build_score(
    data: MarketData,
    specs: list[SignalSpec],
    transforms: TransformConfig,
    mask: pd.DataFrame,
    betas: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Compute each component, standardise it, and blend into a single score.

    Returns both the blend and the standardised components, because attributing
    a result to its parts is the whole point of keeping them separate.
    """
    if not specs:
        raise ValueError("at least one signal must be configured")

    components: dict[str, pd.DataFrame] = {}
    weights: dict[str, float] = {}
    for i, spec in enumerate(specs):
        key = f"{spec.name}#{i}" if spec.name in components else spec.name
        raw = compute_signal(spec.name, data, spec.params)
        components[key] = apply_transforms(raw, transforms, data.sectors, betas, mask)
        weights[key] = spec.weight
        LOG.debug("signal %s: %.1f%% coverage", key, 100 * components[key].notna().mean().mean())

    score = blend(components, weights)
    return score.where(mask.astype(bool)), components


def signal_diagnostics(
    score: pd.DataFrame, data: MarketData, horizons: tuple[int, ...] = (21, 63), step: int = 21
) -> pd.DataFrame:
    """Information coefficients: rank correlation of score with forward returns.

    The IC is the honest first look at a signal — it is computed before any
    portfolio construction, so it cannot be flattered by leverage or sizing.
    """
    close = data.close
    rows = []
    for horizon in horizons:
        forward = close.shift(-horizon) / close - 1.0
        sample = score.index[::step]
        ics = []
        for date in sample:
            a, b = score.loc[date], forward.loc[date]
            both = a.notna() & b.notna()
            if both.sum() >= 20:
                ics.append(a[both].rank().corr(b[both].rank()))
        series = pd.Series(ics, dtype=float).dropna()
        if series.empty:
            continue
        n = len(series)
        rows.append({
            "horizon_days": horizon,
            "mean_ic": series.mean(),
            "ic_std": series.std(),
            "ic_ir": series.mean() / series.std() if series.std() else float("nan"),
            "t_stat": series.mean() / series.std() * (n ** 0.5) if series.std() else float("nan"),
            "hit_rate": (series > 0).mean(),
            "n_periods": n,
        })
    return pd.DataFrame(rows)
