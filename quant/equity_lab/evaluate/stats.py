"""Statistics for deciding whether a backtest result means anything.

The default failure mode of quantitative research is not a coding error, it is
a *selection* error: try enough variants and one of them will look excellent on
any sample. Three defences are implemented here, deliberately kept independent
of the strategy code so they can be pointed at any set of returns:

* **Probabilistic / Deflated Sharpe** (Bailey & Lopez de Prado) — how likely the
  observed Sharpe exceeds a threshold, given sample length, skew, kurtosis, and
  *how many configurations were tried* to find it.
* **Stationary block bootstrap** — confidence intervals that keep the serial
  dependence real return series have.
* **PBO via CSCV** — the probability that the configuration chosen in-sample
  underperforms the median out-of-sample.

Implemented with numpy alone so the statistical core has no heavy dependency.
"""
from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pandas as pd

from ..utils import TRADING_DAYS

EULER_MASCHERONI = 0.5772156649015329


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation).

    Accurate to ~1e-9 in the central region, which is far beyond what these
    statistics need, and avoids taking on a scipy dependency for one function.
    """
    if not 0.0 < p < 1.0:
        return float("nan")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q, r = p - 0.5, (p - 0.5) ** 2
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def probabilistic_sharpe_ratio(
    observed_sr: float, n_obs: int, skew: float, excess_kurtosis: float,
    benchmark_sr: float = 0.0, annualised: bool = True,
) -> float:
    """P(true Sharpe > benchmark), correcting for non-normal, finite samples.

    Negative skew and fat tails — momentum's signature — inflate a naive Sharpe's
    apparent significance. This is the correction for that.
    """
    if n_obs < 10 or not np.isfinite(observed_sr):
        return float("nan")
    sr = observed_sr / np.sqrt(TRADING_DAYS) if annualised else observed_sr
    bench = benchmark_sr / np.sqrt(TRADING_DAYS) if annualised else benchmark_sr
    denom = 1.0 - skew * sr + 0.25 * excess_kurtosis * sr ** 2
    if denom <= 0:
        return float("nan")
    z = (sr - bench) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return norm_cdf(z)


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum Sharpe from ``n_trials`` *independent, worthless* strategies.

    This is the bar a result has to clear to be interesting. It rises with the
    number of things you tried — which is why the sweep records its trial count.
    """
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    sd = math.sqrt(sr_variance)
    a = norm_ppf(1.0 - 1.0 / n_trials)
    b = norm_ppf(1.0 - 1.0 / (n_trials * math.e))
    return sd * ((1.0 - EULER_MASCHERONI) * a + EULER_MASCHERONI * b)


def deflated_sharpe_ratio(
    observed_sr: float, n_obs: int, skew: float, excess_kurtosis: float,
    n_trials: int, sr_variance: float, annualised: bool = True,
) -> dict[str, float]:
    """PSR measured against the Sharpe selection alone would be expected to produce."""
    threshold_daily = expected_max_sharpe(n_trials, sr_variance)
    threshold = threshold_daily * (np.sqrt(TRADING_DAYS) if annualised else 1.0)
    return {
        "deflated_sharpe": probabilistic_sharpe_ratio(
            observed_sr, n_obs, skew, excess_kurtosis, threshold, annualised
        ),
        "sharpe_threshold": float(threshold),
        "n_trials": float(n_trials),
    }


def stationary_bootstrap(
    returns: pd.Series, n_samples: int = 500, block_size: int = 21,
    statistic=None, seed: int = 7,
) -> dict[str, float]:
    """Politis-Romano stationary bootstrap confidence interval.

    Blocks of geometrically distributed length preserve volatility clustering
    and autocorrelation; an i.i.d. bootstrap would destroy both and report
    intervals that are far too tight.
    """
    values = returns.dropna().to_numpy()
    n = len(values)
    if n < 60:
        return {}
    if statistic is None:
        def statistic(sample: np.ndarray) -> float:
            sd = sample.std(ddof=1)
            return float(sample.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else np.nan

    rng = np.random.default_rng(seed)
    p = 1.0 / max(block_size, 1)
    stats = np.empty(n_samples)
    for s in range(n_samples):
        idx = np.empty(n, dtype=int)
        i = rng.integers(0, n)
        for t in range(n):
            idx[t] = i
            i = rng.integers(0, n) if rng.random() < p else (i + 1) % n
        stats[s] = statistic(values[idx])

    stats = stats[np.isfinite(stats)]
    if not len(stats):
        return {}
    point = statistic(values)
    return {
        "bootstrap_mean": float(stats.mean()),
        "bootstrap_std": float(stats.std(ddof=1)),
        "ci_low_5": float(np.percentile(stats, 5)),
        "ci_high_95": float(np.percentile(stats, 95)),
        "p_value_leq_zero": float((stats <= 0).mean()),
        "point_estimate": float(point),
    }


def probability_of_backtest_overfitting(
    trial_returns: pd.DataFrame, n_splits: int = 8, seed: int = 7,
) -> dict[str, float]:
    """PBO by combinatorially symmetric cross-validation (Bailey et al.).

    Split the sample into ``n_splits`` blocks; for every way of assigning half
    the blocks to in-sample, pick the configuration with the best in-sample
    Sharpe and record where it ranks out-of-sample. If the winner is routinely
    below the out-of-sample median, the selection procedure — not the strategy —
    is producing the result.

    ``trial_returns`` is ``dates x configurations``.
    """
    data = trial_returns.dropna(how="all").fillna(0.0)
    # Identical candidates are one trial, not many. A grid that toggles a
    # parameter which is inert under some other setting produces duplicate return
    # series, and duplicates make the in-sample "winner" a coin flip between
    # equals — inflating PBO toward 0.5 for reasons that have nothing to do with
    # overfitting.
    data = data.T.drop_duplicates().T
    n_trials = data.shape[1]
    if n_trials < 2:
        return {"pbo": float("nan"), "n_trials": float(n_trials)}
    if n_splits % 2:
        n_splits += 1
    rows = len(data)
    if rows < n_splits * 20:
        n_splits = max(2, (rows // 20) // 2 * 2)
        if n_splits < 2:
            return {"pbo": float("nan"), "n_trials": float(n_trials)}

    blocks = np.array_split(np.arange(rows), n_splits)
    values = data.to_numpy()
    half = n_splits // 2
    logits = []
    for combo in combinations(range(n_splits), half):
        in_idx = np.concatenate([blocks[b] for b in combo])
        out_idx = np.concatenate([blocks[b] for b in range(n_splits) if b not in combo])

        def sharpes(idx: np.ndarray) -> np.ndarray:
            block = values[idx]
            sd = block.std(axis=0, ddof=1)
            with np.errstate(invalid="ignore", divide="ignore"):
                return np.where(sd > 0, block.mean(axis=0) / sd, np.nan)

        in_sr, out_sr = sharpes(in_idx), sharpes(out_idx)
        if not np.isfinite(in_sr).any():
            continue
        best = int(np.nanargmax(in_sr))
        finite = np.isfinite(out_sr)
        if finite.sum() < 2 or not finite[best]:
            continue
        # relative rank of the in-sample winner within the out-of-sample results
        rank = (out_sr[finite] <= out_sr[best]).sum() / (finite.sum() + 1.0)
        rank = min(max(rank, 1e-6), 1 - 1e-6)
        logits.append(math.log(rank / (1.0 - rank)))

    if not logits:
        return {"pbo": float("nan"), "n_trials": float(n_trials)}
    arr = np.asarray(logits)
    return {
        "pbo": float((arr <= 0).mean()),
        "median_logit": float(np.median(arr)),
        "n_combinations": float(len(arr)),
        "n_trials": float(n_trials),
    }


def haircut_summary(
    returns: pd.Series, n_trials: int, trial_sharpes: pd.Series | None = None,
    bootstrap_samples: int = 500, block_size: int = 21,
) -> dict[str, float]:
    """One call that produces every significance number the report shows."""
    clean = returns.dropna()
    if len(clean) < 60:
        return {}
    sd = clean.std(ddof=1)
    observed = float(clean.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else float("nan")
    variance = (
        float(np.var(trial_sharpes.dropna().to_numpy() / np.sqrt(TRADING_DAYS), ddof=1))
        if trial_sharpes is not None and trial_sharpes.notna().sum() > 1
        else (observed / np.sqrt(TRADING_DAYS)) ** 2 / 4.0
    )
    out = {
        "observed_sharpe": observed,
        "psr_vs_zero": probabilistic_sharpe_ratio(
            observed, len(clean), float(clean.skew()), float(clean.kurt())
        ),
    }
    out.update(deflated_sharpe_ratio(
        observed, len(clean), float(clean.skew()), float(clean.kurt()), n_trials, variance
    ))
    out.update(stationary_bootstrap(clean, bootstrap_samples, block_size))
    return out
