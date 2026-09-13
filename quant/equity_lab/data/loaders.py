"""Pluggable price sources.

The backtest never learns where prices came from: every loader returns the same
:class:`MarketData`. That indirection is what lets the identical research run
against a synthetic panel in CI and against vendor data on a desk.

Available sources
-----------------
``synthetic``  deterministic generative market (always available)
``csv``        a directory of ``<TICKER>.csv`` files (your own vendor dump)
``stooq``      free daily CSV endpoint, no API key
``yfinance``   the ``yfinance`` package, if installed

Loaded panels are cached as parquet keyed by a hash of the request, so a sweep
over 200 configurations hits the network exactly once.
"""
from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

from ..config import DataConfig
from ..utils import ensure_dir, get_logger, stable_hash
from .panel import MarketData
from .synthetic import generate

LOG = get_logger(__name__)


class DataError(RuntimeError):
    """Raised when a source cannot produce a usable panel."""


# --------------------------------------------------------------------------- cache


def _cache_path(cfg: DataConfig, seed: int) -> Path:
    key = stable_hash(
        {
            "source": cfg.source,
            "start": cfg.start,
            "end": cfg.end,
            "tickers": sorted(cfg.tickers),
            "csv_dir": cfg.csv_dir,
            "synthetic": cfg.synthetic.__dict__ if cfg.source == "synthetic" else None,
            "seed": seed if cfg.source == "synthetic" else None,
        }
    )
    return ensure_dir(Path(cfg.cache_dir)) / f"panel-{cfg.source}-{key}.parquet"


def _write_cache(path: Path, data: MarketData) -> None:
    try:
        payload = pd.concat(
            {"close": data.close, "dollar_volume": data.dollar_volume}, axis=1
        )
        payload.to_parquet(path)
        data.sectors.to_frame("sector").to_parquet(path.with_suffix(".sectors.parquet"))
    except Exception as exc:  # pragma: no cover - caching is an optimisation
        LOG.warning("could not cache panel (%s); continuing without cache", exc)


def _read_cache(path: Path) -> MarketData | None:
    if not path.exists():
        return None
    try:
        payload = pd.read_parquet(path)
        sectors = pd.read_parquet(path.with_suffix(".sectors.parquet"))["sector"]
        return MarketData(
            close=payload["close"],
            dollar_volume=payload["dollar_volume"],
            sectors=sectors,
            meta={"source": "cache", "cache_path": str(path)},
        )
    except Exception as exc:  # pragma: no cover
        LOG.warning("ignoring unreadable cache %s (%s)", path, exc)
        return None


# ------------------------------------------------------------------------- sources


def _from_csv_dir(cfg: DataConfig) -> MarketData:
    """Read ``<csv_dir>/<TICKER>.csv``; optional ``sectors.csv`` maps ticker->sector.

    Columns are matched case-insensitively. ``adj_close`` is preferred over
    ``close`` because momentum on unadjusted prices is a dividend artefact.
    """
    root = Path(cfg.csv_dir)
    if not root.exists():
        raise DataError(f"csv_dir {root} does not exist")
    files = sorted(root.glob("*.csv"))
    wanted = {t.upper() for t in cfg.tickers}
    closes, volumes = {}, {}
    for path in files:
        ticker = path.stem.upper()
        if ticker == "SECTORS" or (wanted and ticker not in wanted):
            continue
        frame = pd.read_csv(path)
        frame.columns = [c.strip().lower().replace(" ", "_") for c in frame.columns]
        if "date" not in frame:
            raise DataError(f"{path} has no 'date' column")
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.set_index("date").sort_index()
        price_col = next((c for c in ("adj_close", "adjclose", "close") if c in frame), None)
        if price_col is None:
            raise DataError(f"{path} has no close/adj_close column")
        closes[ticker] = frame[price_col].astype(float)
        if "volume" in frame:
            volumes[ticker] = frame["volume"].astype(float) * frame[price_col].astype(float)
    if not closes:
        raise DataError(f"no usable csv files in {root}")

    close = pd.DataFrame(closes).sort_index()
    dvol = pd.DataFrame(volumes).reindex_like(close) if volumes else close * 0.0 + 1e7
    sector_file = root / "sectors.csv"
    if sector_file.exists():
        table = pd.read_csv(sector_file)
        table.columns = [c.strip().lower() for c in table.columns]
        sectors = table.set_index(table.columns[0])[table.columns[1]]
        sectors.index = sectors.index.str.upper()
    else:
        sectors = pd.Series("Unknown", index=close.columns)
    return MarketData(close, dvol, sectors.reindex(close.columns).fillna("Unknown"),
                      meta={"source": "csv", "csv_dir": str(root)})


def _fetch_stooq(ticker: str) -> pd.DataFrame:
    symbol = ticker.lower()
    if "." not in symbol:
        symbol = f"{symbol}.us"
    url = f"https://stooq.com/q/d/l/?s={symbol}&i=d"
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed host
        raw = response.read().decode("utf-8", "replace")
    if "Date" not in raw.split("\n", 1)[0]:
        raise DataError(f"stooq returned no data for {ticker}")
    frame = pd.read_csv(io.StringIO(raw))
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame.set_index("Date").sort_index()


def _from_stooq(cfg: DataConfig) -> MarketData:
    if not cfg.tickers:
        raise DataError("data.tickers must be set for the stooq source")
    closes, volumes, failed = {}, {}, []
    for ticker in cfg.tickers:
        try:
            frame = _fetch_stooq(ticker)
        except Exception as exc:
            failed.append(f"{ticker}: {exc}")
            continue
        closes[ticker.upper()] = frame["Close"].astype(float)
        if "Volume" in frame:
            volumes[ticker.upper()] = frame["Volume"].astype(float) * frame["Close"].astype(float)
    if not closes:
        raise DataError(
            "stooq fetch failed for every ticker; if this environment blocks outbound "
            "network access, use source: csv or source: synthetic.\n  " + "\n  ".join(failed[:5])
        )
    if failed:
        LOG.warning("stooq: %d tickers failed, continuing with %d", len(failed), len(closes))
    close = pd.DataFrame(closes).sort_index()
    dvol = pd.DataFrame(volumes).reindex_like(close) if volumes else close * 0.0 + 1e7
    return MarketData(close, dvol, pd.Series("Unknown", index=close.columns),
                      meta={"source": "stooq", "failed": failed})


def _from_yfinance(cfg: DataConfig) -> MarketData:
    try:
        import yfinance  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise DataError("source 'yfinance' requires `pip install yfinance`") from exc
    if not cfg.tickers:
        raise DataError("data.tickers must be set for the yfinance source")
    raw = yfinance.download(
        cfg.tickers, start=cfg.start, end=cfg.end,
        auto_adjust=True, progress=False, group_by="column",
    )
    if raw.empty:
        raise DataError("yfinance returned an empty frame")
    close = raw["Close"].copy()
    volume = raw["Volume"].copy() if "Volume" in raw else None
    dvol = (volume * close) if volume is not None else close * 0.0 + 1e7
    sectors = pd.Series("Unknown", index=close.columns)
    try:  # sector metadata is nice-to-have, never load-bearing
        sectors = pd.Series(
            {t: (yfinance.Ticker(t).info or {}).get("sector", "Unknown") for t in close.columns}
        )
    except Exception:  # pragma: no cover
        pass
    return MarketData(close, dvol, sectors, meta={"source": "yfinance"})


_SOURCES = {"csv": _from_csv_dir, "stooq": _from_stooq, "yfinance": _from_yfinance}


# ------------------------------------------------------------------------ entrypoint


def load_market_data(cfg: DataConfig, seed: int = 7, use_cache: bool = True) -> MarketData:
    """Resolve ``cfg.source`` into a :class:`MarketData` panel."""
    path = _cache_path(cfg, seed)
    if use_cache:
        cached = _read_cache(path)
        if cached is not None:
            LOG.info("loaded cached panel %s", path.name)
            return cached.slice(cfg.start, cfg.end)

    if cfg.source == "synthetic":
        data = generate(cfg.synthetic, cfg.start, cfg.end, seed=seed)
    elif cfg.source in _SOURCES:
        data = _SOURCES[cfg.source](cfg).slice(cfg.start, cfg.end)
    else:
        raise DataError(f"unknown data source '{cfg.source}'; expected one of "
                        f"{['synthetic', *_SOURCES]}")

    if data.close.shape[1] < 20:
        LOG.warning("panel has only %d names — cross-sectional signals will be noisy",
                    data.close.shape[1])
    if use_cache:
        _write_cache(path, data)
    return data
