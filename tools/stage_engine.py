"""Weinstein 4-stage classifier (TrendSpider-style), driven by the 30-week SMA.

Reproduces the stage logic from the TrendSpider article:
  * Stage 2 (Advancing):  close above a RISING 30-wk SMA  -> green
                          (close > +10% above SMA          -> bright green)
  * Stage 4 (Declining):  close below a FALLING 30-wk SMA  -> red
                          (close < -10% below SMA          -> bright red)
  * Stage 1/3 (sideways): close within +/-5% of a FLAT SMA
                          above -> light green, below -> orange
        Stage 1 vs Stage 3 is decided by context: a flat zone reached AFTER a
        decline is a Stage 1 base; after an advance it is a Stage 3 top.

Also computes the Mansfield Relative Strength (RP) and the Stage 1 -> Stage 2
"breakout emerged" trigger with relative-volume (RVOL) confirmation.
"""
from __future__ import annotations

import numpy as np

# Tunable thresholds
SLOPE_WIN = 10          # weeks used to measure the SMA slope
FLAT_SLOPE = 5.0        # |annualized SMA slope| <= this (%/yr) => "flat"
BAND_PCT = 5.0          # close within +/-5% of SMA => sideways (Stages 1/3)
STRONG_PCT = 10.0       # close >10% from SMA => "strong" Stage 2/4 shade
CONTEXT_WIN = 26        # weeks of pre-consolidation slope used for 1-vs-3
RVOL_LOOKBACK = 52      # weeks for the average-volume baseline (RVOL)

# Candle colors by stage shade (used by charts.py)
COLORS = {
    "stage2_strong": "#00c853",   # bright green
    "stage2": "#26a69a",          # green
    "above_flat": "#9ccc65",      # light green (sideways, above MA)
    "below_flat": "#ffa726",      # orange (sideways, below MA)
    "stage4": "#ef5350",          # red
    "stage4_strong": "#d50000",   # bright red
    "na": "#b0bec5",              # grey (SMA not yet defined)
}

STAGE_NAME = {1: "Stage 1 (base)", 2: "Stage 2 (advance)",
              3: "Stage 3 (top)", 4: "Stage 4 (decline)", 0: "n/a"}


def _ma_slope_ann(sma: np.ndarray, i: int, win: int = SLOPE_WIN) -> float:
    j = i - win
    if j < 0 or np.isnan(sma[i]) or np.isnan(sma[j]) or sma[j] == 0:
        return np.nan
    return (sma[i] / sma[j] - 1.0) * (52.0 / win) * 100.0


def classify(close: np.ndarray, sma30: np.ndarray) -> dict:
    """Classify every week into a stage (1-4) and assign a candle color.

    Returns dict with:
      stages : int array (0 where SMA undefined)
      colors : list[str] per-week candle colors
      shades : list[str] per-week shade keys
    """
    n = len(close)
    stages = np.zeros(n, dtype=int)
    shades: list[str] = ["na"] * n

    for i in range(n):
        if np.isnan(sma30[i]):
            continue
        pct = (close[i] / sma30[i] - 1.0) * 100.0
        slope = _ma_slope_ann(sma30, i)
        rising = (not np.isnan(slope)) and slope > FLAT_SLOPE
        falling = (not np.isnan(slope)) and slope < -FLAT_SLOPE

        if close[i] > sma30[i] and rising:
            stages[i] = 2
            shades[i] = "stage2_strong" if pct >= STRONG_PCT else "stage2"
        elif close[i] < sma30[i] and falling:
            stages[i] = 4
            shades[i] = "stage4_strong" if pct <= -STRONG_PCT else "stage4"
        else:
            # sideways / transition zone -> Stage 1 or 3 by context
            shades[i] = "above_flat" if pct >= 0 else "below_flat"
            stages[i] = _stage_1_or_3(sma30, i)

    colors = [COLORS[s] for s in shades]
    return {"stages": stages, "colors": colors, "shades": shades}


def _stage_1_or_3(sma30: np.ndarray, i: int) -> int:
    """A sideways zone after a decline is Stage 1; after an advance, Stage 3."""
    pre = _ma_slope_ann(sma30, max(i, 0), win=CONTEXT_WIN)
    if np.isnan(pre):
        # fall back to a 1-year MA comparison
        j = i - 52
        if j >= 0 and not np.isnan(sma30[j]) and sma30[j] > 0:
            return 1 if sma30[i] <= sma30[j] else 3
        return 1
    return 1 if pre <= 0 else 3


def mansfield_rs(close: np.ndarray, bench: np.ndarray, win: int = 52) -> np.ndarray:
    """Mansfield Relative Performance: (RS / SMA(RS, win) - 1) * 100.

    Positive and rising => outperforming the benchmark (bullish for Stage 2).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = close / bench
    rs = np.where(np.isfinite(rs), rs, np.nan)
    rs_ma = _rolling_mean(rs, win)
    with np.errstate(divide="ignore", invalid="ignore"):
        rp = (rs / rs_ma - 1.0) * 100.0
    return np.where(np.isfinite(rp), rp, np.nan)


def _rolling_mean(a: np.ndarray, win: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    for i in range(len(a)):
        if i + 1 >= win:
            window = a[i + 1 - win : i + 1]
            if not np.isnan(window).any():
                out[i] = window.mean()
    return out


def stage_summary(close: np.ndarray, sma30: np.ndarray, vol: np.ndarray) -> dict:
    """Current-stage summary + Stage 1->2 'breakout emerged' trigger.

    Returns:
      current_stage, current_stage_name, weeks_in_stage,
      stage2_emerged (bool, a 1/3 -> 2 transition within the last 4 weeks),
      weeks_since_emergence, rvol_at_emergence (volume / 52-wk avg at the cross)
    """
    cls = classify(close, sma30)
    stages = cls["stages"]
    n = len(stages)
    cur = int(stages[-1])

    # consecutive weeks in the current stage
    wis = 1
    for i in range(n - 2, -1, -1):
        if stages[i] == cur and cur != 0:
            wis += 1
        else:
            break

    # Stage 1/3 -> Stage 2 transition within the last 4 weeks that is still
    # holding (current week must be Stage 2, i.e. not a failed breakout).
    emerged = False
    weeks_since = None
    rvol = None
    for i in (range(n - 1, max(n - 5, 0), -1) if cur == 2 else []):
        if stages[i] == 2 and i - 1 >= 0 and stages[i - 1] in (1, 3, 0):
            emerged = True
            weeks_since = (n - 1) - i
            base_avg = (
                np.nanmean(vol[max(0, i - RVOL_LOOKBACK):i])
                if i > 0 else np.nan
            )
            rvol = float(vol[i] / base_avg) if base_avg and base_avg > 0 else None
            break

    return {
        "current_stage": cur,
        "current_stage_name": STAGE_NAME.get(cur, "n/a"),
        "weeks_in_stage": wis,
        "stage2_emerged": emerged,
        "weeks_since_emergence": weeks_since,
        "rvol_at_emergence": round(rvol, 2) if rvol is not None else None,
        "stages": stages,
        "colors": cls["colors"],
    }
