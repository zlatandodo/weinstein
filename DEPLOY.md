# Deploy to Streamlit Community Cloud (streamlit.app)

The cloud app (`streamlit_app.py`) follows a **precompute** model: it reads the
scan outputs in `output/` and renders them. The heavy scan runs *outside* the
app — locally or via the scheduled **GitHub Action** — so the cloud stays fast
and never hits Yahoo rate limits.

## One-time setup

1. **Make sure fresh outputs are committed.** Run a scan locally first:
   ```
   python tools/stage_scanner.py --universe sp1500 --period 5y --no-charts
   ```
   This writes `output/stage1_diagnostics.csv` and `output/stage2_emerged.csv`
   (these ARE committed; `data/` and `charts/` are gitignored).

2. **Create a GitHub repo and push.** With the GitHub CLI:
   ```
   gh repo create weinstein-stage-scanner --public --source . --remote origin
   git add -A
   git commit -m "Weinstein stage scanner + Streamlit app"
   git push -u origin master
   ```
   (or create the repo in the web UI and `git remote add origin <url>` + push.)

3. **Deploy on Streamlit Cloud.**
   - Go to https://share.streamlit.io → **New app**.
   - Pick the repo/branch, set **Main file path** = `streamlit_app.py`.
   - Advanced settings → **Python 3.12**.
   - Click **Deploy**. First build installs `requirements.txt` (~2-3 min).

## Automatic weekly refresh (GitHub Action)

`.github/workflows/scan.yml` re-runs the scan and commits refreshed
`output/*.csv` every **Friday 23:00 UTC** (after the US close), and on manual
**Run workflow** (with a universe dropdown). When it commits, Streamlit Cloud
auto-redeploys with the new data.

- Requires **Actions enabled** on the repo (Settings → Actions → Allow).
- Uses the built-in `GITHUB_TOKEN` (the workflow already grants
  `contents: write`) — no extra secrets needed.

## Notes / limits
- Free tier: ~1 GB RAM, app sleeps when idle, ephemeral disk. The precompute
  model means none of that matters for normal use.
- The **TradingView** chart in the detail pane needs no data. The **local
  stage-colored chart** is generated on demand (one yfinance fetch per ticker);
  if Yahoo blocks the cloud IP, just use the TradingView chart.
- To scan a real Russell 2000 list instead of the S&P 600 proxy, commit a
  tickers file and change the Action's run step to `--tickers-file <path>`.
