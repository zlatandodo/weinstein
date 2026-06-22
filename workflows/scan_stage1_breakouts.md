# Workflow: Scan for Stage 1 → Stage 2 Breakout Candidates

## Objective
Identify equities that are completing a **Stage 1 base** and beginning the
transition to a **Stage 2 advance**, per Stan Weinstein's Stage Analysis
("Secrets for Profiting in Bull and Bear Markets"). Output a ranked, scored
shortlist plus annotated charts for the top candidates.

## Inputs
- `--universe` (str) — `sp500` (default), `sp400`, `sp600`, or `sp1500`
  (S&P 500 + 400 + 600). S&P 400/600 act as the broad small/mid-cap proxy for
  "Russell 2000" since the full Russell 2000 constituent list is not reliably
  scrapable from Wikipedia. To scan a real Russell 2000 list, drop a file of
  tickers (one per line) and pass `--tickers-file path`.
- `--period` (str) — yfinance history span, default `5y` (>= 3y required by the
  methodology for prior-downtrend and overhead-supply lookbacks).
- `--max-workers` (int) — download concurrency, default `10`.
- `--max-age-days` (int) — re-download cached data older than this, default `1`.
- `--top` (int) — how many top candidates to chart, default `10`.
- `--no-charts` — skip chart generation.

## Tools used
- `tools/universe.py` — scrapes index constituents from Wikipedia (cached in
  `.tmp/`); normalizes tickers for yfinance (e.g. `BRK.B` → `BRK-B`).
- `tools/stage_scanner.py` — main entry point. Downloads weekly OHLCV (cached in
  `data/`, parallelized), evaluates the 6 core Stage 1 criteria, scores 0–100,
  writes CSVs to `output/`, prints the top 20, and triggers charts.
- `tools/charts.py` — mplfinance candlestick + 10/30-wk SMA + colored volume +
  RS subplot + base high/low lines; PNGs to `charts/`.

Run:

    python tools/stage_scanner.py --universe sp500 --top 10

## Steps
1. Resolve the universe (scrape or load tickers file).
2. Download/refresh weekly OHLCV for each ticker (ThreadPoolExecutor + cache).
   Download SPY once for the relative-strength benchmark.
3. For each ticker with >= 3y of data, evaluate the 6 core criteria:
   1. **Base duration** — most recent 35%-band consolidation >= 20 weeks, after
      a prior >= 30% decline from a 52-week high.
   2. **Moving average** — 30-wk SMA flat/rising (10-wk slope >= -0.5% annualized);
      price above both the 30-wk and 10-wk SMA.
   3. **Volume** — last-4-wk avg vol > 1.2x the 52-wk avg; >= 2 of last 4 weeks
      up on above-average volume; positive 8-wk volume regression slope.
   4. **Relative strength** — RS = close/SPY flat-or-rising over the base
      (no new lows) and last-4-wk RS > RS 12 weeks ago.
   5. **Proximity** — within 5% below the base high, OR broke out < 4 weeks ago.
   6. **No overhead supply** — no major resistance cluster within 15% above
      price coming from the 2–3-year-ago history.
4. Keep only tickers passing all 6; compute the 0–100 score.
5. Sort by score desc, export `output/stage1_candidates.csv` (and a full
   `output/stage1_diagnostics.csv` with per-criterion flags for tuning).
6. Print top 20 with a one-line summary each; chart the top N.

## Expected output
- `output/stage1_candidates.csv` — ranked qualifiers with the spec columns:
  `ticker, score, base_duration_weeks, base_range_pct, vol_ratio_4w, rs_trend,
  sma30w_slope_pct, price_vs_base_high_pct, current_price`.
- `output/stage1_diagnostics.csv` — every evaluated ticker + per-criterion pass flags.
- `charts/<TICKER>.png` — annotated weekly chart for each top candidate.

## Edge cases & lessons learned
- yfinance rate-limits aggressive parallelism; keep `--max-workers` ~10 and rely
  on the local `data/` cache for re-runs.
- Class-share tickers use dots on Wikipedia but dashes on Yahoo (`BRK.B`→`BRK-B`).
- `pandas.read_html(url)` uses urllib and can fail with
  `CERTIFICATE_VERIFY_FAILED: certificate has expired` on machines with a stale
  OS CA bundle. `universe.py` works around this by fetching the page with
  `requests` (certifi bundle) and parsing `read_html(StringIO(resp.text))`.
- Delisted / data-poor tickers (< 3y weekly bars) are skipped and logged, not fatal.
- Scoring/threshold tuning is expected — inspect `stage1_diagnostics.csv` to see
  which criterion is filtering out names before loosening a threshold here.
- Requiring all 6 criteria simultaneously is very restrictive: on a full S&P 500
  scan (June 2026) 346 names were analyzable but **0 passed all 6**. Bottlenecks:
  volume (~12% pass), relative strength (~10%), prior-downtrend (~33%). This is
  faithful to the spec ("all must be true") — pure Stage 1→2 setups are rare.
  The dashboard therefore ranks by *number of criteria passed + score* and shows
  per-criterion badges, so near-misses (e.g. FRT/SPG/CARR at 5/6) stay visible.

- **Methodology fix (critical):** the literal spec wording "30-wk SMA flat *or
  rising*" with no upper bound let full **Stage 2** names through — a first S&P 500
  run surfaced stocks whose 30-wk MA was rising +27%/yr median (FRT +38%, CMI +78%),
  the opposite of a Stage 1 base. Two corrections in `stage_scanner.py`:
  1. `find_base` now also requires the 30-wk MA to be **flat across the base**
     (`MA_FLAT_BASE_DRIFT`, total drift < 10%) — the true Weinstein signature that
     separates a sideways base from a trend.
  2. Criterion 2 now bounds the slope on BOTH sides (`C2_SLOPE_MIN..C2_SLOPE_MAX`,
     -0.5%..+15%/yr = flat-to-just-turning), rejecting steep Stage 2 advances.
  After the fix, genuine bases appear (e.g. SJM: 117-wk base, MA dead flat ~105
  after a decline from 140). Classic post-decline Stage 1 bottoms are rare in the
  S&P 500 during a bull market — the small-cap universe (`--universe sp600`) is a
  richer hunting ground. The defining pair is **c1 (prior downtrend) + c2 (flat
  MA)**; volume/RS/proximity (c3/c4/c5) only fire at the actual breakout trigger,
  so still-basing names correctly sit at 3-4/6 until they break out.

## 4-stage engine (TrendSpider-style) — `tools/stage_engine.py`
Reproduces the TrendSpider article's full Weinstein stage framework on top of the
30-week SMA, classifying **every** weekly bar (not just strict bases):
- **Stage 2 (advance):** close above a *rising* 30-wk SMA → green (bright if >10% above).
- **Stage 4 (decline):** close below a *falling* 30-wk SMA → red (bright if >10% below).
- **Stage 1/3 (sideways):** close within ±5% of a *flat* SMA → light green (above) /
  orange (below). Stage 1 vs 3 is decided by context (the pre-consolidation MA
  slope: down → base/Stage 1, up → top/Stage 3).
- **Mansfield RS** = (RS / SMA(RS,52) − 1)·100, with its zero line (relative
  performance vs SPY).
- **"Stage 2 emerged"** trigger = a 1/3→2 transition within the last 4 weeks that
  is still holding, with **RVOL** (volume vs 52-wk average) — this is the article's
  ideal buy point (Stage 1→2 breakout). Surfaced via the dashboard "Stage 2 just
  emerged" filter and the `stage2_emerged`/`rvol_at_emergence` columns.

Charts (`charts.py`) color every candle by its stage and plot the Mansfield RS in
a subplot. Thresholds live at the top of `stage_engine.py` (FLAT_SLOPE, BAND_PCT,
STRONG_PCT, CONTEXT_WIN). The scanner now emits a row for every analyzable ticker
(stage classification), while the strict 6-criteria base scan still flags the
tightest Stage 1 bases (`c1`+`c2`).

## Dashboard (local)
- `tools/dashboard.py` — Flask app: launches `stage_scanner.py` as a subprocess,
  streams progress, renders a ranked table with criterion badges, and embeds a
  TradingView weekly chart per ticker plus the locally generated PNG.
- Run: `python tools/dashboard.py` then open http://127.0.0.1:5000
- Deploy later (note): use a WSGI server (waitress/gunicorn) instead of the Flask
  dev server; the subprocess scan model stays the same.
