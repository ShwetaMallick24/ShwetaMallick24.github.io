# SignalDesk

A personal workspace for monitoring public crypto market data, reviewing threshold-based signals, and keeping a decision journal.

## Features
- Six-asset watchlist using public Coinbase Exchange market statistics
- Hourly close charts, retrieval timestamps, and clearly labeled stale-data fallback
- Adjustable price-move, trading-range, and stablecoin-deviation thresholds
- Review states, local notes, and JSON journal export

No API key or account connection is required. Notes remain in the browser's local storage and do not sync across devices. Monitoring runs only while the app is open. Market movements are not fraud classifications or investment recommendations.

## Run
Serve this folder with a static web server, for example `python3 -m http.server 8000`.

## GitHub Pages
Set Settings → Pages → Source to **GitHub Actions**, then push to the main branch or run the Publish SignalDesk workflow. The workflow publishes the static app without a build step.

## Sources
Coinbase Exchange public product statistics and hourly candle endpoints. See the app's Sources & methods page for formulas and provenance.
