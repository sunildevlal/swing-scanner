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

## Charts, watchlist, journal
- Tap a row to open its candlestick chart with moving averages and Entry, Stop, Target 1 and Target 2 lines.
- Tap the star next to a stock to add it to the Watchlist tab. Tap "I bought this" in the details to add it to the Journal tab.
  Both are saved in your browser only. To sync them with Telegram alerts, use the "Copy" buttons and paste into `watchlist.txt` (one symbol per line) and `journal.txt` (`SYMBOL,buy,stop,target1,target2`) in the repo.

## Telegram alerts (one-time setup)
1. In Telegram, open **@BotFather**, send `/newbot`, follow the steps and copy the **token**.
2. Open your new bot and send it any message. Then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy the number after `"chat":{"id":`.
3. In GitHub: **Settings > Secrets and variables > Actions > New repository secret.** Add `TG_TOKEN` (the token) and `TG_CHAT` (the chat id).
4. Run the "Daily scan" action. You get the market mood, up to 10 new setups scoring 7+, watchlist setups, and journal stop or target hits.
Weekly setups arrive on the last trading day of the week, monthly setups on the last trading day of the month.

## Bearish setups and Options (new)
- **Bearish setups:** in the Scanner tab, use the first dropdown to switch to bearish setups (breakdowns, rallies into falling averages, double tops and so on). Same score, reasons, targets (next support below) and stop (above resistance).
- **Options tab:** for the best bullish and bearish setups in the F&O list, plus Nifty and Bank Nifty, it compares a long call or put, a debit spread and a credit spread, and highlights the best one with a 1 to 10 score. It uses free NSE end-of-day option data, so no Zerodha API or login is needed. Premiums after 3 days (at Target 1) and 2 days (at the stop) are estimated with the Black-Scholes model.
- **IV percentile** needs about 20 trading days of history. The scanner saves `docs/ivhist.json` every day, so it becomes available automatically.
- If NSE blocks the F&O download, save the day's F&O bhavcopy CSV as `scanner/universe/fo_bhavcopy.csv`.
- Journal lines for bearish trades end with `,bear` (the Copy button adds this for you).
