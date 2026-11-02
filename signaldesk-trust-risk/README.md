# SignalDesk: research to Robinhood, with guardrails

SignalDesk ranks crypto assets under a research style you choose and links every buy to a written thesis. Each order passes a configurable set of risk rules and your confirmation before it reaches Robinhood's official **Crypto Trading API**. Every step is audited and reconciled.

- `index.html`: the app (research, theses, trade, review queue, audit log, account & keys, risk rules)
- `bridge.py`: a local service that signs and sends orders to Robinhood. Your private key stays on your machine.

Robinhood only offers an official API for **crypto**. Stocks and options aren't supported, so SignalDesk doesn't trade them.

## Run it

```bash
pip install cryptography

# 1. Make a key pair, then paste the PUBLIC key into Robinhood's API credentials portal
#    (Robinhood web classic → crypto account settings → Add key; choose only the permissions you need)
python bridge.py --gen-key

# 2. Give the bridge your API key and PRIVATE key (never commit these)
export RH_API_KEY="rh-api-..."
export RH_PRIVATE_KEY_B64="..."

# 3. Start in DRY RUN (default): orders are signed and previewed, not sent
python bridge.py
#    open http://127.0.0.1:8787/

# 4. When you're ready to trade for real
SIGNALDESK_LIVE=1 python bridge.py
```

Without keys, the app runs in simulation mode, which is also how the hosted demo runs.

## Safety layers

| Layer | Where | What it does |
| --- | --- | --- |
| Risk rules | App | 11 configurable rules (thesis current, position, size, daily spend, velocity, price deviation, spread, funds, research fit, quiet hours, kill switch). Each can be Enforce or Shadow. |
| Loosening cooldown | App | Tightening a rule applies at once; loosening waits 24 hours. |
| Step-up | App | A code is required above the size cap, after settings changes, and to turn the kill switch off. |
| Hard caps | Bridge | Max order value (`SIGNALDESK_MAX_ORDER_USD`, default $2,000), symbol allowlist, and a `KILL` file that refuses all orders. |
| Idempotency | Bridge | The same `client_order_id` is never sent twice. |
| Local-only access | Bridge | Binds to 127.0.0.1, requires a per-run session token, and rejects other origins and host names (DNS rebinding). |
| Rate limit | Bridge | 90 requests/min (Robinhood allows about 100). Reads retry once on 429; orders never retry blindly. |
| Audit | Both | In-app audit log (copy as CSV) plus `bridge_audit.jsonl`. |
| Reconciliation | App | Matches SignalDesk orders and holdings to the broker, and opens a case on any break, such as an order SignalDesk didn't send. |

## Before going live

- Confirm the endpoint paths in [Robinhood's docs](https://docs.robinhood.com/crypto/trading/). To get fee-tier pricing, set `RH_ORDER_PATH` to the v2 "place crypto orders with fee tiers" path.
- Start with tiny orders in dry run, check the signed request preview in each order's Details, then go live with a low `SIGNALDESK_MAX_ORDER_USD`.
- Research inputs in the app are sample values. Replace them with your own research (Research → Edit inputs).

Not affiliated with Robinhood. This is not investment advice; you're responsible for your orders.
