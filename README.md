# NSE Swing Scanner

Every weekday at 5:00 PM IST, a GitHub Action downloads end-of-day data for Nifty 500 stocks, runs all pattern scans on
daily, weekly and monthly bars, scores each stock from 1 to 10 and saves `docs/data.json`. The website in `docs/` reads that file.

## Setup (about 15 minutes, all free)
1. Create a GitHub account and a new **public** repository (for example `swing-scanner`).
2. Upload everything from this folder, keeping the structure (`.github/workflows/scan.yml`, `scanner/`, `docs/`). Drag and drop works on the repo page; if your computer hides the `.github` folder, enable "show hidden files" first.
3. **Settings > Actions > General > Workflow permissions:** choose "Read and write permissions" and save.
4. **Actions** tab > "Daily scan" > **Run workflow**. Wait 10 to 20 minutes for the first run.
5. **Settings > Pages:** Source "Deploy from a branch", branch `main`, folder `/docs`, then save.
6. Open `https://<your-username>.github.io/<repo-name>/` on any device.

## Scoring (1 to 10)
Pattern quality 25%, trend strength 20%, relative strength 20%, volume 15%, reward:risk 15%, market and sector 5%.
Extra points when different pattern types agree. Points are removed for extended price, low reward:risk and overbought RSI.
Targets are the next resistance levels (swing highs, 52-week high); if none exist above, a measured move is used and labelled.
Stop-loss is below the nearest swing low, limited to 3 ATR.

## Notes
- If the NSE site blocks the download, save the index CSVs from niftyindices.com into `scanner/universe/` using the same file names as in `scan.py`.
- Weights and thresholds are at the top of `scan.py` so you can tune them.
- Stocks with average daily turnover below Rs 5 crore are skipped.
