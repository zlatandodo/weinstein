"""Stan Weinstein Stage 1 -> Stage 2 breakout scanner (main entry point).

Evaluates the 6 core Stage 1 criteria on weekly OHLCV, scores 0-100, exports
ranked CSVs, prints the top 20, and charts the top N.

See workflows/scan_stage1_breakouts.md for the full SOP.

Usage:
    python tools/stage_scanner.py --universe sp500 --top 10
"""
from __future__ import annotations

import argparse
import sys
import warnings

import numpy as np
import pandas as pd

from config import CHARTS_DIR, OUTPUT_DIR, load_env
from data_fetch import fetch_many, fetch_many_meta, fetch_one
from stage_engine import mansfield_rs, stage_summary
from universe import get_universe

warnings.simplefilter("ignore", category=FutureWarning)

# Force UTF-8 stdout so redirected output (Windows cp1252 / CI) never crashes.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

MIN_WEEKS = 156          # require >= 3 years of weekly data
BASE_MIN_WEEKS = 20
BASE_BAND = 0.35         # max_close/min_close - 1 must stay below this (price band)
# --- The defining Weinstein Stage 1 signature: a FLAT 30-week MA. ---
# A base is only a base if the 30-wk MA went sideways across it; and the MA must
# still be flat-to-just-turning today (NOT a steep Stage 2 advance). Tunable.
MA_FLAT_BASE_DRIFT = 0.10   # max total drift of the 30-wk MA across the base (10%)
C2_SLOPE_MIN = -0.5         # 30-wk MA 10-wk annualized slope, lower bound (%/yr)
C2_SLOPE_MAX = 15.0         # upper bound: flat-to-turning, rejects steep Stage 2
BREAKOUT_LOOKBACK = 4    # weeks within which an early breakout still counts


# --------------------------------------------------------------------------- #
# Base detection
# --------------------------------------------------------------------------- #
def find_base(close: np.ndarray, sma30: np.ndarray) -> tuple[int, int] | None:
    """Most recent genuine Stage 1 base ending at (or just before) the last week.

    A window qualifies as a base only if BOTH hold across it:
      * price stays within the 35% band (max_close/min_close - 1 < BASE_BAND), and
      * the 30-week MA is FLAT (total drift < MA_FLAT_BASE_DRIFT) -- this is the
        Weinstein signature that distinguishes a sideways base from a trend.

    Tries base end-points from the latest week back through BREAKOUT_LOOKBACK
    weeks (so a recent breakout doesn't hide the base), then extends backward as
    far as both conditions hold. Returns (start_idx, end_idx) or None.
    """
    n = len(close)
    for end in range(n - 1, n - 1 - BREAKOUT_LOOKBACK, -1):
        if end < BASE_MIN_WEEKS - 1:
            break
        start = end
        while start - 1 >= 0:
            cw = close[start - 1 : end + 1]
            mw = sma30[start - 1 : end + 1]
            if np.isnan(mw).any():
                break
            band_ok = cw.max() / cw.min() - 1 < BASE_BAND
            ma_flat = (mw.max() / mw.min() - 1) < MA_FLAT_BASE_DRIFT
            if band_ok and ma_flat:
                start -= 1
            else:
                break
        if end - start + 1 >= BASE_MIN_WEEKS:
            return start, end
    return None


def _slope(y: np.ndarray) -> float:
    """Least-squares slope of y vs index 0..len-1."""
    x = np.arange(len(y), dtype=float)
    return float(np.polyfit(x, y, 1)[0])


# --------------------------------------------------------------------------- #
# Per-ticker analysis
# --------------------------------------------------------------------------- #
def analyze(ticker: str, df: pd.DataFrame, spy_close: pd.Series) -> dict | None:
    """Evaluate all criteria for one ticker. Returns a metrics dict or None."""
    if df is None or len(df) < MIN_WEEKS:
        return None

    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    if len(df) < MIN_WEEKS:
        return None

    close = df["Close"].to_numpy(dtype=float)
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    open_ = df["Open"].to_numpy(dtype=float)
    vol = df["Volume"].to_numpy(dtype=float)
    n = len(close)
    price = close[-1]

    # Indicators
    sma10 = pd.Series(close).rolling(10).mean().to_numpy()
    sma30 = pd.Series(close).rolling(30).mean().to_numpy()

    # Relative strength vs SPY (aligned to this ticker's index)
    spy = spy_close.reindex(df.index).ffill().to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = close / spy
    rs = np.where(np.isfinite(rs), rs, np.nan)

    # ---- 4-stage classification (always, even without a strict base) ----
    stg = stage_summary(close, sma30, vol)
    rp = mansfield_rs(close, spy)
    mansfield_rp = round(float(rp[-1]), 2) if np.isfinite(rp[-1]) else None
    stage_fields = {
        "current_stage": stg["current_stage"],
        "current_stage_name": stg["current_stage_name"],
        "weeks_in_stage": stg["weeks_in_stage"],
        "stage2_emerged": stg["stage2_emerged"],
        "weeks_since_emergence": stg["weeks_since_emergence"],
        "rvol_at_emergence": stg["rvol_at_emergence"],
        "mansfield_rp": mansfield_rp,
    }

    # ---- Base ----
    base = find_base(close, sma30)
    if base is None:
        # still emit a stage-only row so the 4-stage view covers every name
        return {
            "ticker": ticker, "passed": False, "score": 0,
            "base_duration_weeks": 0, "base_range_pct": None,
            "vol_ratio_4w": None, "rs_trend": "", "sma30w_slope_pct": None,
            "price_vs_base_high_pct": None, "current_price": round(price, 2),
            "c1_base_downtrend": False, "c2_moving_avg": False,
            "c3_volume": False, "c4_rel_strength": False,
            "c5_proximity": False, "c6_no_overhead": False,
            "up_high_vol_weeks": 0, "overhead_count": 0, "rs_at_52w_high": False,
            "_base_start": None, "_base_end": None,
            "_base_high": None, "_base_low": None,
            **stage_fields,
        }
    bstart, bend = base
    base_close = close[bstart : bend + 1]
    base_duration = bend - bstart + 1
    base_high = float(high[bstart : bend + 1].max())
    base_low = float(low[bstart : bend + 1].min())
    base_range_pct = float(base_close.max() / base_close.min() - 1) * 100

    # ---- 1. Base duration + prior downtrend ----
    prior_lookback = 52
    pre_start = max(0, bstart - prior_lookback)
    prior_downtrend = False
    if bstart - pre_start >= 10:  # need some pre-base history
        pre_high = float(high[pre_start:bstart].max())
        drawdown = 1 - base_low / pre_high if pre_high > 0 else 0.0
        prior_downtrend = drawdown >= 0.30
    c1 = base_duration >= BASE_MIN_WEEKS and prior_downtrend

    # ---- 2. Moving averages ----
    sma30_now, sma30_prev = sma30[-1], sma30[-11]
    if np.isnan(sma30_now) or np.isnan(sma30_prev) or sma30_prev == 0:
        return None
    sma30_slope_annual = (sma30_now / sma30_prev - 1) * (52 / 10) * 100
    c2 = (
        C2_SLOPE_MIN <= sma30_slope_annual <= C2_SLOPE_MAX  # flat-to-just-turning
        and price > sma30_now                               # (NOT a steep Stage 2)
        and price > sma10[-1]
    )

    # ---- 3. Volume ----
    avg_vol_4w = float(vol[-4:].mean())
    avg_vol_52w = float(vol[-52:].mean())
    vol_ratio_4w = avg_vol_4w / avg_vol_52w if avg_vol_52w > 0 else 0.0
    up_high_vol_weeks = int(
        sum(
            (close[i] > open_[i]) and (vol[i] > avg_vol_52w)
            for i in range(n - 4, n)
        )
    )
    vol_slope_8w = _slope(vol[-8:])
    c3 = vol_ratio_4w > 1.2 and up_high_vol_weeks >= 2 and vol_slope_8w > 0

    # ---- 4. Relative strength ----
    rs_base = rs[bstart : bend + 1]
    rs_now = rs[-1]
    rs_12w_ago = rs[-13]
    rs_last4_mean = float(np.nanmean(rs[-4:]))
    rs_not_new_low = (
        np.isfinite(rs_now)
        and np.isfinite(np.nanmin(rs_base))
        and rs_now > np.nanmin(rs_base)
    )
    rs_rising_base = (
        np.isfinite(rs_now)
        and np.isfinite(rs[bstart])
        and rs_now >= rs[bstart] * 0.97
    )
    rs_recent_up = (
        np.isfinite(rs_last4_mean)
        and np.isfinite(rs_12w_ago)
        and rs_last4_mean > rs_12w_ago
    )
    c4 = rs_not_new_low and rs_rising_base and rs_recent_up

    rs_change_12w = (
        (rs_last4_mean / rs_12w_ago - 1) * 100
        if np.isfinite(rs_12w_ago) and rs_12w_ago != 0
        else 0.0
    )
    rs_trend = (
        "rising" if rs_change_12w > 2
        else "falling" if rs_change_12w < -2
        else "flat"
    )

    # ---- 5. Proximity to breakout ----
    price_vs_base_high_pct = (price / base_high - 1) * 100
    # most-recent week where close crossed above the base high
    crossed = [
        i for i in range(1, n)
        if close[i] > base_high and close[i - 1] <= base_high
    ]
    recent_breakout = bool(crossed) and crossed[-1] >= n - BREAKOUT_LOOKBACK
    approaching = -5.0 <= price_vs_base_high_pct <= 0.0
    c5 = approaching or (price_vs_base_high_pct > 0 and recent_breakout)

    # ---- 6. No overhead supply (2-3 years ago) ----
    # weekly highs from the 104..156-week-ago window sitting just overhead
    old_lo, old_hi = max(0, n - 156), max(0, n - 104)
    overhead_count = 0
    if old_hi - old_lo >= 10:
        old_highs = high[old_lo:old_hi]
        overhead_count = int(
            np.sum((old_highs > price) & (old_highs <= price * 1.15))
        )
    c6 = overhead_count < 3

    passed = all([c1, c2, c3, c4, c5, c6])

    # ---- Scoring ----
    rs_52w = rs[-52:]
    rs_at_52w_high = (
        np.isfinite(rs_now)
        and np.isfinite(np.nanmax(rs_52w))
        and rs_now >= np.nanmax(rs_52w) * 0.995
    )
    score = 0
    if base_duration > 30:
        score += 20
    if base_duration > 52:
        score += 10
    if vol_ratio_4w > 1.5:
        score += 20
    if rs_at_52w_high:
        score += 20
    if sma30_slope_annual > 0:
        score += 15
    if price_vs_base_high_pct >= -3:
        score += 15

    return {
        "ticker": ticker,
        "passed": passed,
        "score": score,
        "base_duration_weeks": base_duration,
        "base_range_pct": round(base_range_pct, 2),
        "vol_ratio_4w": round(vol_ratio_4w, 2),
        "rs_trend": rs_trend,
        "sma30w_slope_pct": round(sma30_slope_annual, 2),
        "price_vs_base_high_pct": round(price_vs_base_high_pct, 2),
        "current_price": round(price, 2),
        # diagnostics
        "c1_base_downtrend": c1,
        "c2_moving_avg": c2,
        "c3_volume": c3,
        "c4_rel_strength": c4,
        "c5_proximity": c5,
        "c6_no_overhead": c6,
        "up_high_vol_weeks": up_high_vol_weeks,
        "overhead_count": overhead_count,
        "rs_at_52w_high": rs_at_52w_high,
        # 4-stage classification
        **stage_fields,
        # for charting
        "_base_start": bstart,
        "_base_end": bend,
        "_base_high": round(base_high, 2),
        "_base_low": round(base_low, 2),
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
SPEC_COLUMNS = [
    "ticker", "score", "base_duration_weeks", "base_range_pct", "vol_ratio_4w",
    "rs_trend", "sma30w_slope_pct", "price_vs_base_high_pct", "current_price",
]


def run(args: argparse.Namespace) -> int:
    load_env()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Resolving universe: {args.universe}")
    tickers = get_universe(args.universe, args.tickers_file)
    if args.limit:
        tickers = tickers[: args.limit]
    print(f"  {len(tickers)} tickers")

    print("Fetching SPY benchmark ...")
    spy_df = fetch_one("SPY", args.period, args.max_age_days)
    if spy_df is None or spy_df.empty:
        print("ERROR: could not fetch SPY benchmark; aborting.", file=sys.stderr)
        return 1
    spy_close = spy_df["Close"]

    print(f"Downloading weekly data ({args.max_workers} workers) ...")
    data = fetch_many(
        tickers,
        period=args.period,
        max_age_days=args.max_age_days,
        max_workers=args.max_workers,
    )

    print("Analyzing ...")
    rows = []
    for ticker, df in data.items():
        try:
            res = analyze(ticker, df, spy_close)
        except Exception as exc:  # never let one ticker kill the run
            if args.verbose:
                print(f"  {ticker}: error {exc}")
            res = None
        if res and "score" in res:
            rows.append(res)

    if not rows:
        print("No analyzable tickers (insufficient data?).")
        return 0

    # Attach market cap + 10-day average daily volume (for filtering).
    print("Fetching market cap / avg volume ...")
    metas = fetch_many_meta(
        [r["ticker"] for r in rows],
        max_age_days=max(args.max_age_days, 7),
        max_workers=args.max_workers,
    )
    for r in rows:
        m = metas.get(r["ticker"], {}) or {}
        adv = m.get("avg_daily_vol")
        price = r.get("current_price")
        r["market_cap"] = m.get("market_cap")
        r["avg_daily_vol"] = adv
        r["avg_daily_dollar_vol"] = (
            adv * price if (adv is not None and price) else None
        )

    full = pd.DataFrame(rows)
    diag_path = OUTPUT_DIR / "stage1_diagnostics.csv"
    full.drop(columns=[c for c in full.columns if c.startswith("_")]).to_csv(
        diag_path, index=False
    )

    qualifiers = full[full["passed"]].sort_values(
        "score", ascending=False
    ).reset_index(drop=True)

    out = qualifiers[SPEC_COLUMNS] if not qualifiers.empty else pd.DataFrame(
        columns=SPEC_COLUMNS
    )
    cand_path = OUTPUT_DIR / "stage1_candidates.csv"
    out.to_csv(cand_path, index=False)

    # ---- Stage 2 "emerged" alert watchlist (the article's ideal buy point) ----
    alert_path = OUTPUT_DIR / "stage2_emerged.csv"
    emerged = full[full["stage2_emerged"] == True].copy()
    prior = set()
    if alert_path.exists():
        try:
            prior = set(pd.read_csv(alert_path)["ticker"].astype(str))
        except Exception:
            prior = set()
    alert_cols = [
        "ticker", "current_stage_name", "weeks_since_emergence",
        "rvol_at_emergence", "mansfield_rp", "vol_ratio_4w",
        "market_cap", "avg_daily_vol", "current_price",
    ]
    alert_cols = [c for c in alert_cols if c in emerged.columns]
    if not emerged.empty:
        emerged = emerged.sort_values("rvol_at_emergence", ascending=False)
        emerged = emerged[alert_cols].copy()
        emerged["is_new"] = ~emerged["ticker"].astype(str).isin(prior)
    else:
        emerged = pd.DataFrame(columns=alert_cols + ["is_new"])
    emerged.to_csv(alert_path, index=False)
    n_new = int(emerged["is_new"].sum()) if not emerged.empty else 0
    print(f"\n[ALERT] Stage 2 EMERGED: {len(emerged)} names "
          f"({n_new} new since last scan)  ->  {alert_path}")

    print(f"\n{'='*70}")
    print(f"Evaluated {len(full)} tickers | {len(qualifiers)} passed all 6 criteria")
    print(f"Candidates : {cand_path}")
    print(f"Diagnostics: {diag_path}")
    print(f"{'='*70}\n")

    if qualifiers.empty:
        print("No qualifiers. Inspect diagnostics CSV to see which criterion filters.")
        return 0

    print("TOP CANDIDATES")
    for _, r in qualifiers.head(20).iterrows():
        print(
            f"  {r['ticker']:<6} score {r['score']:>3} | "
            f"base {r['base_duration_weeks']:>3}w (range {r['base_range_pct']:.0f}%) | "
            f"vol x{r['vol_ratio_4w']:.2f} | RS {r['rs_trend']:<7} | "
            f"SMA30 slope {r['sma30w_slope_pct']:+.1f}% | "
            f"{r['price_vs_base_high_pct']:+.1f}% vs base-high | ${r['current_price']:.2f}"
        )

    # ---- Charts for top N ----
    if not args.no_charts and len(qualifiers):
        try:
            from charts import chart_candidate

            CHARTS_DIR.mkdir(parents=True, exist_ok=True)
            top = qualifiers.head(args.top)
            print(f"\nGenerating {len(top)} charts -> {CHARTS_DIR}")
            for _, r in top.iterrows():
                t = r["ticker"]
                meta = full[full["ticker"] == t].iloc[0]
                try:
                    path = chart_candidate(
                        t, data[t], spy_close,
                        base_high=meta["_base_high"],
                        base_low=meta["_base_low"],
                    )
                    print(f"  {t} -> {path}")
                except Exception as exc:
                    print(f"  {t}: chart failed ({exc})")
        except ImportError as exc:
            print(f"Charting skipped (missing dependency: {exc}).")

    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Weinstein Stage 1->2 breakout scanner")
    ap.add_argument("--universe", default="sp500",
                    help="sp500 | sp400 | sp600 | sp1500")
    ap.add_argument("--tickers-file", default=None,
                    help="file of tickers (one per line) overriding --universe")
    ap.add_argument("--period", default="5y", help="yfinance history span")
    ap.add_argument("--max-workers", type=int, default=10)
    ap.add_argument("--max-age-days", type=float, default=1.0)
    ap.add_argument("--top", type=int, default=10, help="how many to chart")
    ap.add_argument("--limit", type=int, default=None,
                    help="only scan the first N tickers (testing)")
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    return ap


if __name__ == "__main__":
    sys.exit(run(build_parser().parse_args()))
