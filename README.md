# Stock Logo Downloader

Downloads company logos for stock tickers (trending, most active, S&P 500, your own
list...) as big transparent PNGs, and as scalable SVGs wherever one exists.

## Get it (Windows)

1. **[Download StockLogoDownloader.exe](https://github.com/kefirman9000/tickermark/releases/latest/download/StockLogoDownloader.exe)**
2. Double-click it. A page opens in your browser. Press **Start download**.
3. Logos are saved in `Documents\StockLogos` (you can change this on the page).

Nothing to install. Keep the small black window open while you use it, and press
**Quit** on the page when you're done.

**"Windows protected your PC"?** That warning appears for any program that isn't
signed with a paid certificate. Click **More info** and then **Run anyway**. You only
need to do this once.

## Tips

- **My tickers** tab: type `AAPL, NVDA, TSLA`, or import a CSV from your broker.
  Add `=website` to point at the right company: `BRK-B=berkshirehathaway.com`.
- Click any logo to fix it: try another source, set the website, or use your own file.
- The gear icon opens Settings: black/white versions, padding, extra sizes, WebP, and
  optional Brandfetch / logo.dev keys for better results.

More detail is in [README.txt](README.txt).

## Run from source (instead of the EXE)

Double-click `Install.bat` once (it sets up Python and makes a desktop shortcut), then
use `Run.bat`. Offline tests: `.venv\Scripts\python.exe tests\test_offline.py`.

Every push to `main` builds the EXE on GitHub, tests it, and publishes it as a Release.
