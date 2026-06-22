"""Weinstein Stage Analysis — Streamlit dashboard (deployable on streamlit.app).

PRECOMPUTE model: this app READS the scan outputs committed under output/ and
renders them. The heavy scan (yfinance over ~1500 tickers) runs OUTSIDE the app
— locally or via the scheduled GitHub Action (.github/workflows/scan.yml) — so
the cloud app stays fast and reliable.

Primary chart is the interactive TradingView widget (no data needed). The local
stage-colored matplotlib chart is generated on demand for the selected ticker.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))  # reuse the WAT tool modules

OUTPUT = ROOT / "output"
DIAG = OUTPUT / "stage1_diagnostics.csv"
ALERTS = OUTPUT / "stage2_emerged.csv"

CRIT = ["c1_base_downtrend", "c2_moving_avg", "c3_volume",
        "c4_rel_strength", "c5_proximity", "c6_no_overhead"]
STAGE_EMOJI = {1: "🟠 S1", 2: "🟢 S2", 3: "🟡 S3", 4: "🔴 S4", 0: "⚪ —"}
STAGE_LABEL = {1: "Stage 1 (base)", 2: "Stage 2 (advance)",
               3: "Stage 3 (top)", 4: "Stage 4 (decline)", 0: "n/a"}

st.set_page_config(page_title="Weinstein Stage Scanner", layout="wide",
                   page_icon="📈")


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_diag(mtime: float) -> pd.DataFrame:
    df = pd.read_csv(DIAG)
    for c in CRIT:
        if c not in df.columns:
            df[c] = False
        df[c] = df[c].astype(bool)
    df["npass"] = df[CRIT].sum(axis=1)
    df["qualifies"] = df[CRIT].all(axis=1)
    if "current_stage" not in df.columns:
        df["current_stage"] = 0
    df["current_stage"] = df["current_stage"].fillna(0).astype(int)
    return df


def fmt_cap(v):
    if pd.isna(v):
        return "–"
    v = float(v)
    if v >= 1e12: return f"{v/1e12:.2f}T"
    if v >= 1e9:  return f"{v/1e9:.2f}B"
    if v >= 1e6:  return f"{v/1e6:.0f}M"
    return f"{v:.0f}"


def fmt_vol(v):
    return "–" if pd.isna(v) else f"{float(v)/1e6:.2f}M"


if not DIAG.exists():
    st.title("📈 Weinstein Stage Scanner")
    st.warning("No scan output found yet. Run `python tools/stage_scanner.py "
               "--universe sp1500` (or wait for the scheduled GitHub Action) so "
               "`output/stage1_diagnostics.csv` is created.")
    st.stop()

updated = datetime.fromtimestamp(DIAG.stat().st_mtime, tz=timezone.utc)
df = load_diag(DIAG.stat().st_mtime)

# --------------------------------------------------------------------------- #
# Header + KPIs
# --------------------------------------------------------------------------- #
st.title("📈 Weinstein Stage Analysis Scanner")
st.caption(f"Stage 1 base → Stage 2 breakout · weekly · vs SPY · "
           f"data updated {updated:%Y-%m-%d %H:%M UTC}")

counts = df["current_stage"].value_counts().to_dict()
k = st.columns(6)
k[0].metric("Evaluated", len(df))
k[1].metric("🟠 Stage 1", counts.get(1, 0))
k[2].metric("🟢 Stage 2", counts.get(2, 0))
k[3].metric("🟡 Stage 3", counts.get(3, 0))
k[4].metric("🔴 Stage 4", counts.get(4, 0))
k[5].metric("⭐ S2 emerged", int(df.get("stage2_emerged", pd.Series(dtype=bool)).sum()))

# --------------------------------------------------------------------------- #
# Stage 2 "emerged" alert
# --------------------------------------------------------------------------- #
if ALERTS.exists():
    al = pd.read_csv(ALERTS)
    if len(al):
        n_new = int(al["is_new"].sum()) if "is_new" in al.columns else 0
        st.success(f"🔔 **{len(al)} Stage 2 just-emerged** breakouts"
                   f"{f' · {n_new} new since last scan' if n_new else ''} "
                   "— the ideal Weinstein buy point.")
        with st.expander("Show Stage 2 emerged watchlist (sorted by RVOL)"):
            show = al.copy()
            if "market_cap" in show:
                show["market_cap"] = show["market_cap"].map(fmt_cap)
            if "avg_daily_vol" in show:
                show["avg_daily_vol"] = show["avg_daily_vol"].map(fmt_vol)
            st.dataframe(show, use_container_width=True, hide_index=True)
        st.download_button("⬇ Export emerged watchlist (CSV)",
                           ALERTS.read_bytes(), file_name="stage2_emerged.csv",
                           mime="text/csv")

# --------------------------------------------------------------------------- #
# Sidebar filters
# --------------------------------------------------------------------------- #
st.sidebar.header("Filters")
stage_opt = st.sidebar.selectbox(
    "Stage", ["all", "Stage 2 just emerged ⭐", "1 — base", "2 — advance",
              "3 — top", "4 — decline"])
only_base = st.sidebar.checkbox("Stage 1 base only (downtrend + flat MA)")
only_q = st.sidebar.checkbox("Only 6/6 qualifiers")
min_cap = st.sidebar.number_input("Min market cap ($B)", 0.0, step=0.5, value=0.0)
min_vol = st.sidebar.number_input("Min avg daily vol (M shares)", 0.0, step=0.1,
                                  value=0.0)
sort_by = st.sidebar.selectbox(
    "Sort by", ["npass", "score", "mansfield_rp", "rvol_at_emergence",
                "vol_ratio_4w", "market_cap", "base_duration_weeks"])

f = df.copy()
if stage_opt.startswith("Stage 2 just"):
    f = f[f.get("stage2_emerged", False) == True]
elif stage_opt[0] in "1234":
    f = f[f["current_stage"] == int(stage_opt[0])]
if only_base:
    f = f[f["c1_base_downtrend"] & f["c2_moving_avg"]]
if only_q:
    f = f[f["qualifies"]]
if min_cap > 0 and "market_cap" in f:
    f = f[f["market_cap"].fillna(0) >= min_cap * 1e9]
if min_vol > 0 and "avg_daily_vol" in f:
    f = f[f["avg_daily_vol"].fillna(0) >= min_vol * 1e6]
if sort_by in f.columns:
    f = f.sort_values(sort_by, ascending=False, na_position="last")

# --------------------------------------------------------------------------- #
# Results table
# --------------------------------------------------------------------------- #
st.subheader(f"{len(f)} matches")

disp = pd.DataFrame({
    "Ticker": f["ticker"],
    "Stage": f["current_stage"].map(STAGE_EMOJI)
        + f.get("stage2_emerged", False).map({True: " ⭐", False: ""}),
    "Crit": f["npass"].astype(str) + "/6",
    "Score": f["score"],
    "Base wk": f["base_duration_weeks"],
    "Vol×": f["vol_ratio_4w"],
    "RS": f["rs_trend"],
    "Mansf": f.get("mansfield_rp"),
    "SMA30%": f["sma30w_slope_pct"],
    "vsHigh%": f["price_vs_base_high_pct"],
    "Price": f["current_price"],
    "MktCap": f.get("market_cap", pd.Series(index=f.index)).map(fmt_cap),
    "AvgVol": f.get("avg_daily_vol", pd.Series(index=f.index)).map(fmt_vol),
})
st.dataframe(disp, use_container_width=True, hide_index=True, height=430)

# --------------------------------------------------------------------------- #
# Ticker detail: TradingView + local stage chart
# --------------------------------------------------------------------------- #
st.divider()
tickers = f["ticker"].tolist() or df["ticker"].tolist()
sel = st.selectbox("Inspect a ticker", tickers)
row = df[df["ticker"] == sel].iloc[0]

c1, c2 = st.columns([3, 2])
with c1:
    st.markdown(f"### {sel} — {STAGE_LABEL.get(int(row['current_stage']), '')}"
                + (" ⭐" if row.get("stage2_emerged") else ""))
    components.html(f"""
      <div class="tradingview-widget-container"><div id="tv"></div></div>
      <script src="https://s3.tradingview.com/tv.js"></script>
      <script>new TradingView.widget({{
        "width":"100%","height":500,"symbol":"{sel}","interval":"W",
        "timezone":"Etc/UTC","theme":"dark","style":"1","locale":"en",
        "allow_symbol_change":true,"container_id":"tv",
        "studies":[{{"id":"MASimple@tv-basic","inputs":{{"length":10}}}},
                   {{"id":"MASimple@tv-basic","inputs":{{"length":30}}}}]
      }});</script>""", height=520)

with c2:
    st.metric("Mansfield RS", f"{row.get('mansfield_rp', float('nan')):+.1f}"
              if pd.notna(row.get("mansfield_rp")) else "–")
    st.write({
        "Stage": STAGE_LABEL.get(int(row["current_stage"]), ""),
        "Criteria passed": f"{int(row['npass'])}/6",
        "Score": int(row["score"]),
        "Base weeks": int(row["base_duration_weeks"]),
        "Vol ratio 4w": row["vol_ratio_4w"],
        "RS trend": row["rs_trend"],
        "vs base-high %": row["price_vs_base_high_pct"],
        "Price": row["current_price"],
        "Market cap": fmt_cap(row.get("market_cap")),
        "Avg daily vol": fmt_vol(row.get("avg_daily_vol")),
    })

with st.expander("Local stage-colored weekly chart (generated on demand)"):
    if st.button(f"Generate stage chart for {sel}"):
        with st.spinner("Fetching data and rendering…"):
            try:
                from charts import chart_candidate
                from data_fetch import fetch_one
                dfw = fetch_one(sel, "5y", max_age_days=1)
                spy = fetch_one("SPY", "5y", max_age_days=1)
                if dfw is None or spy is None:
                    st.error("Could not fetch data for this ticker.")
                else:
                    p = chart_candidate(sel, dfw, spy["Close"],
                                        base_high=None, base_low=None)
                    st.image(p, use_container_width=True)
            except Exception as e:
                st.error(f"Chart generation failed: {e}")
