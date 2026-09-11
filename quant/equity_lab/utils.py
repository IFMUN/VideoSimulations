"""Small shared helpers: hashing, logging, calendars, frame hygiene."""
from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

TRADING_DAYS = 252

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-22s %(message)s"


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logging.getLogger().handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
    return logger


def to_jsonable(obj: Any) -> Any:
    """Recursively coerce dataclasses / numpy / pandas objects into JSON types."""
    if is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        value = float(obj)
        return value if np.isfinite(value) else None
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, pd.Series):
        return to_jsonable(obj.to_dict())
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    return obj


def stable_hash(obj: Any, length: int = 10) -> str:
    """Deterministic short hash of any JSON-able object.

    Used to give every experiment an identity that depends only on its inputs,
    so re-running the same config lands in the same place.
    """
    blob = json.dumps(to_jsonable(obj), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:length]


def git_sha(repo_root: Path | None = None) -> str | None:
    """Best-effort current commit, recorded in run manifests for provenance."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root or Path(__file__).resolve().parents[2]),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # pragma: no cover - provenance is best effort
        return None


def rebalance_dates(index: pd.DatetimeIndex, rule: str) -> pd.DatetimeIndex:
    """Trading dates on which the portfolio is allowed to re-target.

    ``rule`` is either a pandas offset alias understood as "last trading day in
    each period" (``M``, ``W-FRI``, ``Q``) or ``every:N`` for a fixed stride.
    """
    index = pd.DatetimeIndex(index)
    if not len(index):
        return index
    if rule.startswith("every:"):
        stride = int(rule.split(":", 1)[1])
        return index[::stride]
    alias = {"M": "ME", "Q": "QE", "A": "YE", "Y": "YE"}.get(rule.upper(), rule)
    grouped = pd.Series(index, index=index).resample(alias).last().dropna()
    dates = pd.DatetimeIndex(grouped.values)

    # Drop a trailing rebalance that only exists because the sample stops there.
    # "Last trading day of the month" is a calendar fact; if the sample ends
    # mid-month, resampling invents a month-end on the cut date. That is not a
    # data leak, but it does mean a backtest ending mid-period rebalances on a
    # day the live strategy never would — and it is exactly the kind of end-of-
    # sample artefact that quietly flatters a final partial period.
    if len(dates) and dates[-1] == index[-1] and _period_is_incomplete(index[-1], alias):
        dates = dates[:-1]
    return dates


def _period_is_incomplete(last_date: pd.Timestamp, alias: str) -> bool:
    """True when the calendar period containing ``last_date`` extends past it."""
    freq = {"ME": "M", "QE": "Q", "YE": "Y"}.get(alias, alias)
    try:
        current = pd.Period(last_date, freq=freq)
        following = pd.Period(last_date + pd.offsets.BDay(1), freq=freq)
    except ValueError:  # a frequency pandas cannot express as a period
        return False
    return current == following


def winsorize(frame: pd.DataFrame, limit: float) -> pd.DataFrame:
    """Clip each cross-section to its [limit, 1-limit] quantiles."""
    if limit <= 0:
        return frame
    lo = frame.quantile(limit, axis=1)
    hi = frame.quantile(1.0 - limit, axis=1)
    return frame.clip(lower=lo, upper=hi, axis=0)


def cross_sectional_z(frame: pd.DataFrame, min_names: int = 10) -> pd.DataFrame:
    """Z-score each row; rows with too few observations are blanked, not faked."""
    counts = frame.count(axis=1)
    mean = frame.mean(axis=1)
    std = frame.std(axis=1, ddof=0).replace(0.0, np.nan)
    out = frame.sub(mean, axis=0).div(std, axis=0)
    return out.where(counts >= min_names)


def cross_sectional_rank(frame: pd.DataFrame) -> pd.DataFrame:
    """Uniform [-0.5, 0.5] rank per row — robust to outliers and scale."""
    ranks = frame.rank(axis=1, pct=True)
    return ranks - 0.5


def demean_by_group(frame: pd.DataFrame, groups: pd.Series) -> pd.DataFrame:
    """Subtract the group (e.g. sector) mean from each cross-section."""
    groups = groups.reindex(frame.columns)
    known = groups.dropna()
    if known.empty:
        return frame
    out = frame.copy()
    sub = frame[known.index]
    means = sub.T.groupby(known).transform("mean").T
    out[known.index] = sub - means
    return out


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def chunked(items: Iterable, size: int) -> Iterable[list]:
    batch: list = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
