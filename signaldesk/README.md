# SignalDesk

A personal crypto research screener and thesis tracker using public Coinbase Exchange data.

## Research workflow
- Choose Balanced, Momentum, or Lower volatility to apply documented factor weights.
- Screen seven non-stablecoin assets by historical momentum, Bitcoin-relative returns, annualized volatility, 90-close drawdown, and estimated venue activity.
- Save a shortlist and compare up to three assets.
- Record a thesis, invalidation criteria, review date, due-diligence checks, and market snapshot.
- Revisit date and market-threshold triggers, then export your research and source data.

Scores summarize historical behavior. They are not forecasts, calibrated probabilities, valuations, buy recommendations, or backtested strategies. Missing essential historical inputs produce no score. Token supply, unlocks, security history, fundamentals, and regulatory status are not automatically assessed.

## Data
Coinbase Exchange public `/products/{asset}-USD/stats` and daily `/candles?granularity=86400` endpoints. The current UTC daily candle is excluded. Missing intervals are not interpolated. A dated source snapshot is bundled for offline or failed-refresh fallback. USD activity is a last-price-times-base-volume proxy, not executable market depth.

## Privacy
No account, wallet or API key is connected. Research notes, thesis entries, review status and shortlists remain in browser local storage. Export for backup. Existing SignalDesk market journal entries are migrated on first use of the research workspace.

## Hosting
Static HTML, CSS and native JavaScript modules; no build step. Serve this directory over HTTP or publish on GitHub Pages. Keep all relative asset paths together.
