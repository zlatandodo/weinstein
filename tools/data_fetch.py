"""Weekly OHLCV download with local caching and parallel fetching.

Caches each ticker as data/<TICKER>.csv. Re-downloads only when the cache is
older than `max_age_days`. Uses a ThreadPoolExecutor to parallelize yfinance.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from config import DATA_DIR

_OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _cache_path(ticker: str, interval: str = "1wk") -> Path:
    # weekly keeps the bare name (back-compat); other intervals get a suffix
    return DATA_DIR / (f"{ticker}.csv" if interval == "1wk"
                       else f"{ticker}.{interval}.csv")


def _is_fresh(path: Path, max_age_days: float) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days < max_age_days


def _read_cache(path: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if df.empty or not set(_OHLCV).issubset(df.columns):
            return None
        return df[_OHLCV]
    except Exception:
        return None


def _download(ticker: str, period: str, retries: int = 2,
              interval: str = "1wk") -> pd.DataFrame | None:
    import yfinance as yf

    for attempt in range(retries + 1):
        try:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                auto_adjust=True,
                progress=False,
                threads=False,
            )
            if df is None or df.empty:
                return None
            # yfinance may return MultiIndex columns for a single ticker.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[[c for c in _OHLCV if c in df.columns]].dropna()
            return df if not df.empty else None
        except Exception:
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
            else:
                return None
    return None


def fetch_one(ticker: str, period: str, max_age_days: float,
              interval: str = "1wk") -> pd.DataFrame | None:
    """Return OHLCV for one ticker (weekly by default), from cache or download."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker, interval)
    if _is_fresh(path, max_age_days):
        cached = _read_cache(path)
        if cached is not None:
            return cached
    df = _download(ticker, period, interval=interval)
    if df is not None:
        try:
            df.to_csv(path)
        except Exception:
            pass
        return df
    # Fall back to a stale cache rather than failing outright.
    return _read_cache(path)


def fetch_many(
    tickers: list[str],
    period: str = "5y",
    max_age_days: float = 1.0,
    max_workers: int = 10,
    progress_every: int = 50,
) -> dict[str, pd.DataFrame]:
    """Download many tickers in parallel. Returns {ticker: df} for successes."""
    results: dict[str, pd.DataFrame] = {}
    done = 0
    total = len(tickers)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(fetch_one, t, period, max_age_days): t for t in tickers
        }
        for fut in as_completed(futures):
            ticker = futures[fut]
            done += 1
            try:
                df = fut.result()
            except Exception:
                df = None
            if df is not None and not df.empty:
                results[ticker] = df
            if progress_every and done % progress_every == 0:
                print(f"  downloaded {done}/{total} "
                      f"({len(results)} ok, {done - len(results)} skipped)")
    print(f"  downloaded {done}/{total} ({len(results)} ok, "
          f"{done - len(results)} skipped)")
    return results


# --------------------------------------------------------------------------- #
# Metadata: market cap + average daily volume (last 10 trading days)
# --------------------------------------------------------------------------- #
def _meta_path(ticker: str) -> Path:
    return DATA_DIR / f"{ticker}.meta.json"


def _g(fi, *names):
    """Read the first available attribute/key from a yfinance fast_info object."""
    for n in names:
        try:
            v = fi[n]
            if v is not None:
                return v
        except Exception:
            pass
        try:
            v = getattr(fi, n)
            if v is not None:
                return v
        except Exception:
            pass
    return None


def fetch_meta(ticker: str, max_age_days: float = 7.0) -> dict:
    """Return {market_cap, avg_daily_vol, last_price} for a ticker (cached JSON).

    avg_daily_vol is the 10-day average daily share volume (falls back to the
    3-month average). Returns {} on failure.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _meta_path(ticker)
    if _is_fresh(path, max_age_days):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    import yfinance as yf

    meta: dict = {}
    try:
        fi = yf.Ticker(ticker).fast_info
        meta = {
            "market_cap": _num(_g(fi, "market_cap", "marketCap")),
            "avg_daily_vol": _num(_g(
                fi, "ten_day_average_volume", "tenDayAverageVolume",
                "three_month_average_volume", "threeMonthAverageVolume",
            )),
            "last_price": _num(_g(fi, "last_price", "lastPrice")),
        }
        path.write_text(json.dumps(meta), encoding="utf-8")
    except Exception:
        # fall back to a possibly-stale cache rather than failing
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return meta


def fetch_many_meta(
    tickers: list[str], max_age_days: float = 7.0, max_workers: int = 10
) -> dict[str, dict]:
    """Fetch metadata for many tickers in parallel. Returns {ticker: meta}."""
    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(fetch_meta, t, max_age_days): t for t in tickers}
        for fut in as_completed(futs):
            try:
                out[futs[fut]] = fut.result() or {}
            except Exception:
                out[futs[fut]] = {}
    return out


def _num(v):
    try:
        f = float(v)
        return None if (f != f) else f  # NaN guard
    except Exception:
        return None
