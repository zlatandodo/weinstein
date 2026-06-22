"""Local web dashboard for the Weinstein Stage 1 scanner.

Thin orchestration layer (WAT principle): the dashboard does no analysis itself.
It launches `stage_scanner.py` as a subprocess, streams its progress, then reads
the diagnostics CSV and renders a ranked table with per-criterion badges plus an
embedded TradingView weekly chart and the locally generated PNG per ticker.

Run:
    python tools/dashboard.py
    # then open http://127.0.0.1:5000
"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, request, send_file, send_from_directory

from config import CHARTS_DIR, DATA_DIR, OUTPUT_DIR, PROJECT_ROOT, TOOLS_DIR

ASSETS_DIR = TOOLS_DIR / "dashboard_assets"
SCANNER = TOOLS_DIR / "stage_scanner.py"
DIAG_CSV = OUTPUT_DIR / "stage1_diagnostics.csv"
ALERTS_CSV = OUTPUT_DIR / "stage2_emerged.csv"

CRITERIA = [
    ("c1_base_downtrend", "Base+Downtrend"),
    ("c2_moving_avg", "Moving Avg"),
    ("c3_volume", "Volume"),
    ("c4_rel_strength", "Rel.Strength"),
    ("c5_proximity", "Proximity"),
    ("c6_no_overhead", "No Overhead"),
]

app = Flask(__name__, static_folder=None)

# ---- background scan job state ----
_LOCK = threading.Lock()
JOB: dict = {"running": False, "log": [], "returncode": None}


def _run_scan(cmd: list[str]) -> None:
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    for line in proc.stdout:  # type: ignore[union-attr]
        with _LOCK:
            JOB["log"].append(line.rstrip())
            JOB["log"] = JOB["log"][-400:]
    proc.wait()
    with _LOCK:
        JOB["returncode"] = proc.returncode
        JOB["running"] = False


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/")
def index():
    return send_from_directory(ASSETS_DIR, "index.html")


@app.post("/api/scan")
def api_scan():
    with _LOCK:
        if JOB["running"]:
            return jsonify({"error": "a scan is already running"}), 409
        JOB.update(running=True, log=[], returncode=None)

    body = request.get_json(silent=True) or {}
    cmd = [
        sys.executable, str(SCANNER),
        "--universe", str(body.get("universe", "sp500")),
        "--period", str(body.get("period", "5y")),
        "--max-workers", str(int(body.get("max_workers", 10))),
        "--max-age-days", str(float(body.get("max_age_days", 1))),
        "--top", str(int(body.get("top", 10))),
    ]
    if body.get("limit"):
        cmd += ["--limit", str(int(body["limit"]))]
    if body.get("tickers_file"):
        cmd += ["--tickers-file", str(body["tickers_file"])]
    if body.get("no_charts"):
        cmd += ["--no-charts"]

    threading.Thread(target=_run_scan, args=(cmd,), daemon=True).start()
    return jsonify({"started": True, "cmd": " ".join(cmd)})


@app.get("/api/status")
def api_status():
    with _LOCK:
        return jsonify({
            "running": JOB["running"],
            "returncode": JOB["returncode"],
            "log": JOB["log"][-60:],
            "has_results": DIAG_CSV.exists(),
        })


@app.get("/api/results")
def api_results():
    if not DIAG_CSV.exists():
        return jsonify({"rows": [], "evaluated": 0, "qualifiers": 0})
    df = pd.read_csv(DIAG_CSV)
    crit_keys = [k for k, _ in CRITERIA]
    for k in crit_keys:
        if k not in df.columns:
            df[k] = False
        df[k] = df[k].astype(bool)
    df["npass"] = df[crit_keys].sum(axis=1)
    df["qualifies"] = df[crit_keys].all(axis=1)
    df = df.sort_values(["npass", "score"], ascending=False)

    rows = []
    for _, r in df.iterrows():
        ticker = str(r["ticker"])
        rows.append({
            "ticker": ticker,
            "score": int(r.get("score", 0)),
            "npass": int(r["npass"]),
            "qualifies": bool(r["qualifies"]),
            "base_duration_weeks": int(r.get("base_duration_weeks", 0)),
            "base_range_pct": _num(r.get("base_range_pct")),
            "vol_ratio_4w": _num(r.get("vol_ratio_4w")),
            "rs_trend": str(r.get("rs_trend", "")),
            "sma30w_slope_pct": _num(r.get("sma30w_slope_pct")),
            "price_vs_base_high_pct": _num(r.get("price_vs_base_high_pct")),
            "current_price": _num(r.get("current_price")),
            "market_cap": _num(r.get("market_cap")),
            "avg_daily_vol": _num(r.get("avg_daily_vol")),
            "avg_daily_dollar_vol": _num(r.get("avg_daily_dollar_vol")),
            "current_stage": int(r["current_stage"]) if pd.notna(r.get("current_stage")) else 0,
            "current_stage_name": str(r.get("current_stage_name", "")),
            "weeks_in_stage": int(r["weeks_in_stage"]) if pd.notna(r.get("weeks_in_stage")) else 0,
            "stage2_emerged": bool(r.get("stage2_emerged")),
            "rvol_at_emergence": _num(r.get("rvol_at_emergence")),
            "mansfield_rp": _num(r.get("mansfield_rp")),
            "criteria": {k: bool(r[k]) for k in crit_keys},
            "has_png": (CHARTS_DIR / f"{ticker}.png").exists(),
        })
    return jsonify({
        "rows": rows,
        "evaluated": len(df),
        "qualifiers": int(df["qualifies"].sum()),
        "criteria_labels": dict(CRITERIA),
    })


@app.get("/api/alerts")
def api_alerts():
    """Stage 2 'emerged' watchlist (the breakout buy-point alert)."""
    if not ALERTS_CSV.exists():
        return jsonify({"rows": [], "count": 0, "new": 0})
    df = pd.read_csv(ALERTS_CSV)
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "ticker": str(r["ticker"]),
            "rvol_at_emergence": _num(r.get("rvol_at_emergence")),
            "mansfield_rp": _num(r.get("mansfield_rp")),
            "weeks_since_emergence": _num(r.get("weeks_since_emergence")),
            "market_cap": _num(r.get("market_cap")),
            "avg_daily_vol": _num(r.get("avg_daily_vol")),
            "current_price": _num(r.get("current_price")),
            "is_new": bool(r.get("is_new", False)),
        })
    return jsonify({
        "rows": rows,
        "count": len(rows),
        "new": int(df["is_new"].sum()) if "is_new" in df.columns else 0,
    })


@app.get("/api/alerts.csv")
def api_alerts_csv():
    if not ALERTS_CSV.exists():
        return jsonify({"error": "no alerts file yet"}), 404
    return send_file(ALERTS_CSV, mimetype="text/csv",
                     as_attachment=True, download_name="stage2_emerged.csv")


@app.get("/api/chart/<ticker>.png")
def api_chart(ticker: str):
    png = CHARTS_DIR / f"{ticker.upper()}.png"
    if not png.exists():
        return jsonify({"error": "no chart"}), 404
    return send_file(png, mimetype="image/png")


@app.post("/api/generate_chart/<ticker>")
def api_generate_chart(ticker: str):
    """Generate our PNG on demand for any cached ticker (for near-misses)."""
    ticker = ticker.upper()
    data_csv = DATA_DIR / f"{ticker}.csv"
    spy_csv = DATA_DIR / "SPY.csv"
    if not data_csv.exists() or not spy_csv.exists():
        return jsonify({"error": "no cached data"}), 404
    try:
        from charts import chart_candidate
        from stage_scanner import analyze

        df = pd.read_csv(data_csv, index_col=0, parse_dates=True)
        spy = pd.read_csv(spy_csv, index_col=0, parse_dates=True)["Close"]
        res = analyze(ticker, df, spy)
        bh = res.get("_base_high") if res else None
        bl = res.get("_base_low") if res else None
        if bh is None:  # fall back to simple range if no base detected
            bh = float(df["High"].tail(160).max())
            bl = float(df["Low"].tail(160).min())
        path = chart_candidate(ticker, df, spy, base_high=bh, base_low=bl)
        return jsonify({"ok": True, "path": path})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def _num(v):
    try:
        f = float(v)
        return None if pd.isna(f) else f
    except Exception:
        return None


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 5000
    print(f"Stage 1 dashboard -> http://{host}:{port}")
    app.run(host=host, port=port, debug=False, threaded=True)
