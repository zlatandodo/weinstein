"""Resolve the scanning universe (index constituents) for the Stage 1 scanner.

Scrapes constituent tables from Wikipedia and caches them in .tmp/. The full
Russell 2000 list is not reliably available on Wikipedia, so the S&P 400 (mid)
and S&P 600 (small) indices serve as a broad small/mid-cap proxy. Combine them
with the S&P 500 via the `sp1500` key, or supply your own ticker file.

Usage (standalone):
    python tools/universe.py --universe sp1500
"""
from __future__ import annotations

import argparse
import sys
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

from config import TMP_DIR, ensure_tmp

# Some systems have a stale OS CA bundle; pandas.read_html (urllib) then fails
# SSL verification. Fetch via requests (uses certifi) and parse the HTML text.
_HEADERS = {"User-Agent": "Mozilla/5.0 (WAT-scanner; +https://example.local)"}

# Wikipedia constituent pages and the column holding the ticker symbol.
WIKI_SOURCES = {
    "sp500": (
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "Symbol",
    ),
    "sp400": (
        "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
        "Symbol",
    ),
    "sp600": (
        "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
        "Symbol",
    ),
}


def _normalize(symbol: str) -> str:
    """Yahoo Finance uses dashes for class shares (BRK.B -> BRK-B)."""
    return str(symbol).strip().upper().replace(".", "-")


def _scrape_index(key: str, max_age_days: int = 7) -> list[str]:
    """Scrape one index's tickers, caching the raw symbol list in .tmp/."""
    ensure_tmp()
    cache = TMP_DIR / f"universe_{key}.csv"
    if cache.exists():
        import time

        age_days = (time.time() - cache.stat().st_mtime) / 86400
        if age_days < max_age_days:
            return cache.read_text(encoding="utf-8").split()

    url, col = WIKI_SOURCES[key]
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    # Find the table that actually contains the symbol column.
    symbols: list[str] = []
    for tbl in tables:
        if col in tbl.columns:
            symbols = [_normalize(s) for s in tbl[col].dropna().tolist()]
            break
    if not symbols:
        raise RuntimeError(f"Could not find '{col}' column on {url}")

    # de-dup, keep order
    seen: set[str] = set()
    symbols = [s for s in symbols if not (s in seen or seen.add(s))]
    cache.write_text("\n".join(symbols), encoding="utf-8")
    return symbols


def get_universe(name: str = "sp500", tickers_file: str | None = None) -> list[str]:
    """Return the list of tickers for the requested universe.

    `name` is one of: sp500, sp400, sp600, sp1500 (= 500+400+600).
    `tickers_file`, if given, overrides everything: one ticker per line.
    """
    if tickers_file:
        path = Path(tickers_file)
        raw = path.read_text(encoding="utf-8").split()
        out, seen = [], set()
        for s in raw:
            t = _normalize(s)
            if t and not t.startswith("#") and t not in seen:
                seen.add(t)
                out.append(t)
        return out

    name = name.lower()
    if name in WIKI_SOURCES:
        return _scrape_index(name)
    if name == "sp1500":
        out, seen = [], set()
        for key in ("sp500", "sp400", "sp600"):
            for t in _scrape_index(key):
                if t not in seen:
                    seen.add(t)
                    out.append(t)
        return out
    raise ValueError(f"Unknown universe '{name}'. Use sp500/sp400/sp600/sp1500.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve scanning universe")
    ap.add_argument("--universe", default="sp500")
    ap.add_argument("--tickers-file", default=None)
    args = ap.parse_args()
    tickers = get_universe(args.universe, args.tickers_file)
    print(f"{len(tickers)} tickers")
    print(", ".join(tickers[:30]) + (" ..." if len(tickers) > 30 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
