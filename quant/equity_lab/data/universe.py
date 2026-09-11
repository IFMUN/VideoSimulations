"""Point-in-time universe construction.

Every screen here answers one question: *on date t, using only information
available at t, is this name investable?* Nothing in this module may look
forward — the resulting boolean mask is the contract the rest of the stack
relies on for survivorship-bias-free research.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import UniverseConfig
from .panel import MarketData


def build_universe(data: MarketData, cfg: UniverseConfig) -> pd.DataFrame:
    """Return a boolean ``dates x tickers`` investability mask.

    Screens, in order:

    1. **Listed** — a price prints today and yesterday.
    2. **History** — at least ``min_history_days`` of prior prices, so trailing
       signals are computed on real data rather than on a stub.
    3. **Price** — above ``min_price``; sub-dollar names are a different asset
       class in cost terms.
    4. **Liquidity** — trailing ADV above ``min_adv_usd`` and, if
       ``top_n_by_adv`` is set, inside the top N by ADV *on that date*.
    """
    close = data.close
    listed = close.notna() & close.shift(1).notna()

    history = close.notna().rolling(cfg.min_history_days, min_periods=1).sum()
    has_history = history.shift(1) >= cfg.min_history_days

    above_price = close > cfg.min_price

    adv = data.adv(21).shift(1)          # yesterday's ADV is knowable today
    liquid = adv >= cfg.min_adv_usd

    mask = listed & has_history & above_price & liquid
    if cfg.top_n_by_adv and cfg.top_n_by_adv < close.shape[1]:
        rank = adv.where(mask).rank(axis=1, ascending=False, method="first")
        mask = _buffered_membership(mask, rank, cfg.top_n_by_adv, cfg.rank_buffer)

    return mask.fillna(False)


def _buffered_membership(
    eligible: pd.DataFrame, rank: pd.DataFrame, top_n: int, buffer: float
) -> pd.DataFrame:
    """Apply hysteresis to the liquidity cut-off.

    A hard "top N by ADV" rule churns badly: ADV is noisy day to day, so names
    sitting near the boundary flip in and out constantly, and every flip is a
    forced round-trip that the signal never asked for. Entering requires a rank
    inside ``top_n``; *staying* only requires a rank inside ``top_n * (1 +
    buffer)``. Index providers do the same thing, for the same reason.
    """
    enter = (rank <= top_n).fillna(False).to_numpy()
    stay_limit = top_n * (1.0 + max(buffer, 0.0))
    stay = (rank <= stay_limit).fillna(False).to_numpy()
    base = eligible.to_numpy()

    out = np.zeros_like(base, dtype=bool)
    previous = np.zeros(base.shape[1], dtype=bool)
    for i in range(base.shape[0]):
        member = base[i] & (enter[i] | (previous & stay[i]))
        out[i] = member
        previous = member
    return pd.DataFrame(out, index=eligible.index, columns=eligible.columns)


def universe_summary(mask: pd.DataFrame) -> pd.DataFrame:
    """Per-year diagnostics — a universe that silently collapses invalidates a backtest.

    Note the ``fill_value=False``: shifting a boolean frame without it upcasts to
    ``object``, and ``~`` on an object frame is a *bitwise* not on Python bools
    (``~True == -2``, which is truthy). That silently inverts the logic.
    """
    mask = mask.astype(bool)
    prev = mask.shift(1, fill_value=False)
    counts = mask.sum(axis=1)
    entries = (mask & ~prev).sum(axis=1)
    exits = (~mask & prev).sum(axis=1)
    frame = pd.DataFrame({"n_names": counts, "entries": entries, "exits": exits})
    return frame.groupby(frame.index.year).agg(
        n_names=("n_names", "mean"),
        min_names=("n_names", "min"),
        entries=("entries", "sum"),
        exits=("exits", "sum"),
    ).round(1)
