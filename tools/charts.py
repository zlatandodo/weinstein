"""Annotated weekly charts for the scanner.

Renders a candlestick chart with candles COLOR-CODED BY WEINSTEIN STAGE
(green = Stage 2, light green/orange = sideways Stage 1/3, red = Stage 4),
10/30-week SMA overlays, color-coded volume, the Mansfield Relative Strength
line (with its zero line) in a subplot, and horizontal base high/low markers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from config import CHARTS_DIR
from stage_engine import classify, mansfield_rs

# Show roughly the last ~3 years on the chart for readability.
_CHART_WEEKS = 160

# Gallery thumbnails: daily candles + 50/200-day SMA (blue / yellow).
SMA50_COLOR = "#2f81f7"    # blue
SMA200_COLOR = "#f5c518"   # yellow
_GALLERY_DAYS = 400        # ~18 months of daily bars shown


def gallery_chart(
    ticker: str,
    df_daily,
    title_suffix: str = "",
    out_dir=CHARTS_DIR,
) -> str:
    """Compact DAILY candlestick thumbnail with 50-day (blue) & 200-day (yellow)
    SMA, for the gallery grid. Returns the saved PNG path."""
    import matplotlib

    matplotlib.use("Agg")
    import mplfinance as mpf

    sub = (out_dir / "gallery")
    sub.mkdir(parents=True, exist_ok=True)

    data = df_daily.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    data = data[["Open", "High", "Low", "Close", "Volume"]]
    if not isinstance(data.index, pd.DatetimeIndex):
        data.index = pd.to_datetime(data.index)
    data = data.tail(_GALLERY_DAYS)

    out_path = str((sub / f"{ticker}.png").resolve())
    mpf.plot(
        data,
        type="candle",
        style="yahoo",
        title=f"{ticker}{(' · ' + title_suffix) if title_suffix else ''}",
        mav=(50, 200),
        mavcolors=[SMA50_COLOR, SMA200_COLOR],
        volume=False,
        figratio=(4, 3),
        figscale=0.7,
        tight_layout=True,
        savefig=dict(fname=out_path, dpi=90, bbox_inches="tight"),
    )
    return out_path


def chart_candidate(
    ticker: str,
    df: pd.DataFrame,
    spy_close: pd.Series,
    base_high: float | None,
    base_low: float | None,
    out_dir=CHARTS_DIR,
) -> str:
    """Generate and save a stage-colored PNG chart. Returns the file path."""
    import matplotlib

    matplotlib.use("Agg")  # headless / no display
    import mplfinance as mpf

    out_dir.mkdir(parents=True, exist_ok=True)

    data = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    data = data[["Open", "High", "Low", "Close", "Volume"]]
    if not isinstance(data.index, pd.DatetimeIndex):
        data.index = pd.to_datetime(data.index)

    close_full = data["Close"].to_numpy(dtype=float)
    sma30_full = data["Close"].rolling(30).mean().to_numpy()
    spy_full = spy_close.reindex(data.index).ffill().to_numpy(dtype=float)

    # Stage classification + Mansfield RP over the full series, then trim.
    colors_full = classify(close_full, sma30_full)["colors"]
    rp_full = mansfield_rs(close_full, spy_full)

    data = data.tail(_CHART_WEEKS)
    colors = colors_full[-len(data):]
    rp = pd.Series(rp_full[-len(data):], index=data.index, name="Mansfield RS")
    zero = pd.Series(0.0, index=data.index)

    addplots = [
        mpf.make_addplot(rp, panel=2, color="#1565c0", width=1.3,
                         ylabel="Mansfield RS"),
        mpf.make_addplot(zero, panel=2, color="#888888", width=0.8),
    ]

    hlines = {"hlines": [], "colors": [], "linestyle": "--", "linewidths": 1.0}
    if base_high is not None:
        hlines["hlines"].append(base_high); hlines["colors"].append("green")
    if base_low is not None:
        hlines["hlines"].append(base_low); hlines["colors"].append("red")

    kwargs = dict(
        type="candle",
        style="yahoo",
        title=f"\n{ticker} — Weekly · candles colored by Weinstein stage",
        ylabel="Price",
        mav=(10, 30),                  # 10-week and 30-week SMA overlays
        volume=True,
        volume_panel=1,
        addplot=addplots,
        panel_ratios=(6, 2, 2),
        figratio=(16, 10),
        figscale=1.2,
    )
    if hlines["hlines"]:
        kwargs["hlines"] = hlines

    out_path = str((out_dir / f"{ticker}.png").resolve())
    # Color each candle by its stage (graceful fallback if unsupported).
    try:
        mpf.plot(data, marketcolor_overrides=colors,
                 savefig=dict(fname=out_path, dpi=120, bbox_inches="tight"),
                 **kwargs)
    except TypeError:
        mpf.plot(data,
                 savefig=dict(fname=out_path, dpi=120, bbox_inches="tight"),
                 **kwargs)
    return out_path
