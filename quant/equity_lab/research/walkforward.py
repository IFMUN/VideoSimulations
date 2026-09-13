"""Walk-forward validation with purging and embargo.

Plain k-fold cross-validation is invalid on financial panels for two reasons,
both of which this module fixes explicitly:

* **Overlap.** A signal formed on date ``t`` is evaluated against a return that
  extends past ``t``. If the fold boundary falls inside that horizon, training
  and test share information. The fix is *purging*: drop training observations
  whose evaluation window reaches into the test set.
* **Serial correlation.** Even after purging, observations adjacent to the
  boundary are close to dependent. The fix is an *embargo*: additionally drop a
  buffer of training days immediately after the test block.

On top of that, the split is walk-forward — training always precedes testing —
because a strategy selected using the future is not a strategy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..backtest import run_backtest
from ..config import Config
from ..data import MarketData
from ..evaluate import metrics as metric_mod
from ..utils import TRADING_DAYS, get_logger

LOG = get_logger(__name__)


@dataclass
class Split:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    def describe(self) -> dict[str, str]:
        return {
            "train": f"{self.train_start.date()} → {self.train_end.date()}",
            "test": f"{self.test_start.date()} → {self.test_end.date()}",
        }


def make_splits(
    dates: pd.DatetimeIndex, n_splits: int, train_years: float, test_years: float,
    embargo_days: int, anchored: bool = True,
) -> list[Split]:
    """Build walk-forward windows, embargoing the gap between train and test.

    ``anchored`` keeps every training window starting at the beginning of the
    sample (an expanding window); otherwise the window rolls with fixed length.
    """
    dates = pd.DatetimeIndex(dates)
    train_len = int(train_years * TRADING_DAYS)
    test_len = int(test_years * TRADING_DAYS)
    if train_len + test_len + embargo_days >= len(dates):
        raise ValueError(
            f"sample of {len(dates)} days is too short for {train_years}y train + "
            f"{test_years}y test; shorten the windows or lengthen the sample"
        )

    splits: list[Split] = []
    usable = len(dates) - train_len - embargo_days - test_len
    stride = max(usable // max(n_splits - 1, 1), 1) if n_splits > 1 else 0
    for k in range(n_splits):
        offset = k * stride
        train_end_idx = train_len + offset
        test_start_idx = train_end_idx + embargo_days
        test_end_idx = min(test_start_idx + test_len, len(dates) - 1)
        if test_start_idx >= len(dates) - 20:
            break
        train_start_idx = 0 if anchored else max(0, train_end_idx - train_len)
        splits.append(Split(
            dates[train_start_idx], dates[train_end_idx - 1],
            dates[test_start_idx], dates[test_end_idx],
        ))
    return splits


def purge_mask(
    dates: pd.DatetimeIndex, test_start: pd.Timestamp, test_end: pd.Timestamp,
    horizon_days: int, embargo_days: int,
) -> pd.Series:
    """Boolean mask of dates usable for training given one test block.

    A training date is dropped when its forward evaluation window
    (``horizon_days``) reaches into the test block, and for ``embargo_days``
    after the block ends.
    """
    dates = pd.DatetimeIndex(dates)
    positions = pd.Series(np.arange(len(dates)), index=dates)
    test_lo = int(positions.loc[positions.index >= test_start].iloc[0])
    test_hi = int(positions.loc[positions.index <= test_end].iloc[-1])
    idx = positions.to_numpy()
    usable = (idx + horizon_days < test_lo) | (idx > test_hi + embargo_days)
    return pd.Series(usable, index=dates)


def run_walkforward(
    base: Config, data: MarketData, grid: Iterable[dict[str, Any]] | None = None,
    selection_metric: str = "sharpe",
) -> dict[str, Any]:
    """Select parameters on each training window, then apply them out of sample.

    The stitched out-of-sample curve is the only performance number here worth
    quoting: the in-sample selection Sharpe is, by construction, the maximum of
    however many candidates were tried, and is therefore biased upward.
    """
    candidates = list(grid) if grid else [{}]
    cfg_wf = base.walkforward
    splits = make_splits(
        data.dates, cfg_wf.n_splits, cfg_wf.train_years, cfg_wf.test_years,
        cfg_wf.embargo_days, cfg_wf.anchored,
    )
    if not splits:
        raise ValueError("no usable walk-forward splits — sample is too short")

    LOG.info("walk-forward: %d splits x %d candidates", len(splits), len(candidates))
    oos_chunks: list[pd.Series] = []
    rows: list[dict[str, Any]] = []

    for k, split in enumerate(splits):
        scored = []
        for candidate in candidates:
            cfg = base.with_overrides(candidate or {})
            train_cfg = _restrict(cfg, split.train_start, split.train_end)
            try:
                result = run_backtest(train_cfg, data.slice(split.train_start, split.train_end))
            except ValueError as exc:
                LOG.warning("split %d candidate skipped: %s", k, exc)
                continue
            score = metric_mod.summarise(result.returns, result.equity).get(
                selection_metric, float("nan")
            )
            scored.append((score if np.isfinite(score) else -np.inf, candidate))

        if not scored:
            LOG.warning("split %d produced no usable candidate", k)
            continue
        best_score, best = max(scored, key=lambda item: item[0])

        test_cfg = _restrict(base.with_overrides(best or {}), split.train_start, split.test_end)
        test_result = run_backtest(test_cfg, data.slice(split.train_start, split.test_end))
        oos = test_result.returns.loc[split.test_start:split.test_end]
        oos_chunks.append(oos)

        oos_equity = (1 + oos).cumprod()
        rows.append({
            "split": k,
            **split.describe(),
            "selected": str(best) if best else "base",
            "is_" + selection_metric: best_score,
            "oos_sharpe": metric_mod.sharpe(oos),
            "oos_ann_return": metric_mod.annualised_return(oos),
            "oos_max_dd": float(metric_mod.drawdown_series(oos_equity).min()),
            "oos_days": len(oos),
        })
        LOG.info("split %d: IS %s %.2f -> OOS sharpe %.2f",
                 k, selection_metric, best_score, rows[-1]["oos_sharpe"])

    if not oos_chunks:
        raise RuntimeError("walk-forward produced no out-of-sample returns")

    stitched = pd.concat(oos_chunks).sort_index()
    stitched = stitched[~stitched.index.duplicated(keep="first")]
    equity = (1 + stitched).cumprod()
    folds = pd.DataFrame(rows)

    is_col = "is_" + selection_metric
    degradation = float(folds[is_col].mean() - folds["oos_sharpe"].mean()) \
        if selection_metric == "sharpe" else float("nan")

    return {
        "folds": folds,
        "oos_returns": stitched,
        "oos_equity": equity,
        "oos_metrics": metric_mod.summarise(stitched, equity),
        "selection_degradation": degradation,
        "n_candidates": len(candidates),
    }


def _restrict(cfg: Config, start: pd.Timestamp, end: pd.Timestamp) -> Config:
    return cfg.with_overrides({
        "data.start": str(pd.Timestamp(start).date()),
        "data.end": str(pd.Timestamp(end).date()),
    })
