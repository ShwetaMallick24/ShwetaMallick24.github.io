#!/usr/bin/env python3
"""
SignalDesk bridge: a small local service that lets the SignalDesk app trade
crypto through Robinhood's official Crypto Trading API.

Why a bridge?
  * Your Ed25519 private key never goes into a browser. It stays on your machine.
  * The bridge enforces its own hard limits (defense in depth), even if the UI is bypassed.
  * It only listens on 127.0.0.1 and requires a per-run session token.

Safe by default: DRY RUN. Requests are signed and previewed but NOT sent to Robinhood
until you start the bridge with SIGNALDESK_LIVE=1.

Usage
  pip install cryptography
  python bridge.py --gen-key            # make a key pair; upload the PUBLIC key in Robinhood
  export RH_API_KEY="rh-api-..."        # from Robinhood's API credentials portal
  export RH_PRIVATE_KEY_B64="..."       # the PRIVATE key printed by --gen-key
  python bridge.py                      # dry run; open the printed URL
  SIGNALDESK_LIVE=1 python bridge.py    # live trading

Optional settings (environment variables)
  SIGNALDESK_MAX_ORDER_USD  hard cap per order, default 2000
  SIGNALDESK_SYMBOLS        allowed assets, default BTC,ETH,SOL,XRP,ADA,AVAX,LINK
  SIGNALDESK_PORT           default 8787
  RH_ORDER_PATH             default /api/v1/crypto/trading/orders/
                            (Robinhood's v2 "place crypto orders with fee tiers" endpoint
                             is what counts toward fee tiers; set its path here from the docs)
  Create a file named KILL next to this script to refuse all new orders.

Robinhood docs: https://docs.robinhood.com/crypto/trading/
Not affiliated with Robinhood. Crypto trading involves risk; you are responsible for your orders.
"""
import base64
import collections
import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
except ImportError:  # pragma: no cover
    sys.exit("Missing dependency: run  pip install cryptography")

HERE = Path(__file__).resolve().parent
BASE_URL = os.environ.get("RH_BASE_URL", "https://trading.robinhood.com")
API_KEY = os.environ.get("RH_API_KEY", "").strip()
PRIVATE_KEY_B64 = os.environ.get("RH_PRIVATE_KEY_B64", "").strip()
LIVE = os.environ.get("SIGNALDESK_LIVE", "") == "1"
PORT = int(os.environ.get("SIGNALDESK_PORT", "8787"))
MAX_ORDER_USD = float(os.environ.get("SIGNALDESK_MAX_ORDER_USD", "2000"))
ALLOWED = [s.strip().upper() for s in os.environ.get("SIGNALDESK_SYMBOLS", "BTC,ETH,SOL,XRP,ADA,AVAX,LINK").split(",") if s.strip()]
ORDER_PATH = os.environ.get("RH_ORDER_PATH", "/api/v1/crypto/trading/orders/")
RATE_LIMIT_PER_MIN = 90  # stay under Robinhood's documented ~100/min
SESSION_TOKEN = secrets.token_urlsafe(24)
AUDIT_FILE = HERE / "bridge_audit.jsonl"
KILL_FILE = HERE / "KILL"


def mask(s):
    return (s[:7] + "…" + s[-4:]) if s and len(s) > 12 else ("set" if s else "")


def audit(event, **fields):
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "event": event, **fields}
    with AUDIT_FILE.open("a") as f:
        f.write(json.dumps(rec) + "\n")


# ---------------------------------------------------------------- signing
class Signer:
    def __init__(self, api_key, private_key_b64):
        self.api_key = api_key
        seed = base64.b64decode(private_key_b64)
        if len(seed) == 64:  # some tools export seed+public; the seed is the first 32 bytes
            seed = seed[:32]
        if len(seed) != 32:
            raise ValueError("RH_PRIVATE_KEY_B64 must decode to a 32-byte Ed25519 seed")
        self.key = Ed25519PrivateKey.from_private_bytes(seed)

    def headers(self, method, path_with_query, body_str):
        ts = str(int(time.time()))
        message = f"{self.api_key}{ts}{path_with_query}{method}{body_str}"
        sig = base64.b64encode(self.key.sign(message.encode("utf-8"))).decode()
        return {"x-api-key": self.api_key, "x-timestamp": ts, "x-signature": sig,
                "Content-Type": "application/json; charset=utf-8"}


SIGNER = None
if API_KEY and PRIVATE_KEY_B64:
    SIGNER = Signer(API_KEY, PRIVATE_KEY_B64)


# ---------------------------------------------------------------- rate limit + client
class RateLimiter:
    def __init__(self, per_min):
        self.per_min, self.calls, self.lock = per_min, collections.deque(), threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_min:
                time.sleep(max(0, 60 - (now - self.calls[0])) + 0.05)
            self.calls.append(time.time())


LIMITER = RateLimiter(RATE_LIMIT_PER_MIN)
STATS = {"auth_failures": 0, "rate_limited": 0}


class RobinhoodError(Exception):
    def __init__(self, status, payload):
        super().__init__(f"Robinhood returned HTTP {status}")
        self.status, self.payload = status, payload


def rh_request(method, path, query=None, body=None, retry_get=True):
    if not SIGNER:
        raise RobinhoodError(503, {"error": "Bridge has no API key configured"})
    qs = ("?" + urllib.parse.urlencode(query, doseq=True)) if query else ""
    path_q = path + qs
    body_str = json.dumps(body) if body is not None else ""
    LIMITER.wait()
    req = urllib.request.Request(BASE_URL + path_q, method=method,
                                 data=body_str.encode() if body is not None else None,
                                 headers=SIGNER.headers(method, path_q, body_str))
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        payload = _safe_json(e.read().decode(errors="replace"))
        if e.code == 401:
            STATS["auth_failures"] += 1
            audit("auth_failure", path=path)
        if e.code == 429:
            STATS["rate_limited"] += 1
            if method == "GET" and retry_get:  # reads are safe to retry once; orders are not
                time.sleep(min(float(e.headers.get("Retry-After", "2") or 2), 5))
                return rh_request(method, path, query, body, retry_get=False)
        raise RobinhoodError(e.code, payload)


def _safe_json(s):
    try:
        return json.loads(s)
    except Exception:
        return {"raw": s[:500]}


# ---------------------------------------------------------------- order handling
IDEMPOTENCY = {}  # client_order_id -> response, so a retried submit can't double-order
IDEMP_LOCK = threading.Lock()


def build_order_body(req):
    sym = str(req.get("symbol", "")).upper()
    base = sym.replace("-USD", "")
    if base not in ALLOWED:
        raise ValueError(f"Symbol {sym} is not in the bridge allowlist")
    side = req.get("side")
    if side not in ("buy", "sell"):
        raise ValueError("side must be buy or sell")
    otype = req.get("type")
    if otype not in ("market", "limit"):
        raise ValueError("type must be market or limit")
    qty = str(req.get("asset_quantity", ""))
    if not qty or float(qty) <= 0:
        raise ValueError("asset_quantity must be positive")
    coid = str(req.get("client_order_id", ""))
    uuid.UUID(coid)  # raises if malformed
    notional = float(req.get("notional_usd") or 0)
    if notional > MAX_ORDER_USD:
        raise ValueError(f"Order value ${notional:,.2f} exceeds bridge cap ${MAX_ORDER_USD:,.0f}")
    body = {"client_order_id": coid, "side": side, "type": otype, "symbol": f"{base}-USD"}
    if otype == "market":
        body["market_order_config"] = {"asset_quantity": qty}
    else:
        lp = str(req.get("limit_price") or "")
        if not lp or float(lp) <= 0:
            raise ValueError("limit_price required for limit orders")
        if float(qty) * float(lp) > MAX_ORDER_USD:
            raise ValueError(f"Order value exceeds bridge cap ${MAX_ORDER_USD:,.0f}")
        body["limit_order_config"] = {"asset_quantity": qty, "limit_price": lp, "time_in_force": "gtc"}
    return body


def place_order(req):
    if KILL_FILE.exists():
        raise PermissionError("Bridge kill switch is on (KILL file present)")
    body = build_order_body(req)
    coid = body["client_order_id"]
    with IDEMP_LOCK:
        if coid in IDEMPOTENCY:
            return {**IDEMPOTENCY[coid], "idempotent_replay": True}
    if not LIVE:
        preview = {"method": "POST", "url": BASE_URL + ORDER_PATH,
                   "headers": {k: (v if k != "x-signature" else v[:16] + "…") for k, v in
                               (SIGNER.headers("POST", ORDER_PATH, json.dumps(body)) if SIGNER else {}).items()
                               if k != "x-api-key"} | {"x-api-key": mask(API_KEY)},
                   "body": body}
        resp = {"ok": True, "dry_run": True, "order": {"id": "dryrun-" + coid[:8], "state": "dry_run"},
                "request_preview": preview}
    else:
        try:
            order = rh_request("POST", ORDER_PATH, body=body)
        except urllib.error.URLError as e:
            audit("order_timeout", client_order_id=coid, error=str(e))
            raise TimeoutError("No response from Robinhood. Check order status before retrying.")
        resp = {"ok": True, "dry_run": False, "order": order}
    with IDEMP_LOCK:
        IDEMPOTENCY[coid] = resp
    audit("order", client_order_id=coid, signaldesk_id=req.get("signaldesk_order_id"), live=LIVE,
          symbol=body["symbol"], side=body["side"], type=body["type"],
          state=resp["order"].get("state"), broker_id=resp["order"].get("id"))
    return resp


# ---------------------------------------------------------------- HTTP server
class Handler(BaseHTTPRequestHandler):
    server_version = "SignalDeskBridge/1.0"

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s %s\n" % (self.command, self.path.split("?")[0]))

    def _send(self, code, obj, ctype="application/json"):
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")  # blocks DNS-rebinding

    def _authorized(self):
        if not self._host_ok():
            return False
        origin = self.headers.get("Origin")
        if origin and urllib.parse.urlparse(origin).hostname not in ("127.0.0.1", "localhost"):
            return False  # other websites can't drive your bridge
        return secrets.compare_digest(self.headers.get("X-SignalDesk-Token", ""), SESSION_TOKEN)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/index.html"):
            if not self._host_ok():
                return self._send(403, {"error": "bad host"})
            html = (HERE / "index.html").read_text().replace("__SIGNALDESK_TOKEN__", SESSION_TOKEN)
            return self._send(200, html.encode(), "text/html; charset=utf-8")
        if not url.path.startswith("/api/"):
            return self._send(404, {"error": "not found"})
        if not self._authorized():
            return self._send(403, {"error": "forbidden"})
        q = urllib.parse.parse_qs(url.query)
        try:
            if url.path == "/api/status":
                return self._send(200, {"configured": bool(SIGNER), "dry_run": not LIVE,
                                        "api_key_masked": mask(API_KEY), "order_path": ORDER_PATH,
                                        "max_order_usd": MAX_ORDER_USD, "allowed_symbols": ALLOWED,
                                        "rate_limit_per_min": RATE_LIMIT_PER_MIN, "kill": KILL_FILE.exists(),
                                        **STATS})
            if url.path == "/api/account":
                return self._send(200, rh_request("GET", "/api/v1/crypto/trading/accounts/"))
            if url.path == "/api/holdings":
                return self._send(200, rh_request("GET", "/api/v1/crypto/trading/holdings/"))
            if url.path == "/api/quotes":
                syms = [s.upper() for s in q.get("symbols", [""])[0].split(",") if s.upper() in ALLOWED]
                data = rh_request("GET", "/api/v1/crypto/marketdata/best_bid_ask/",
                                  query={"symbol": [f"{s}-USD" for s in syms]})
                quotes = [{"symbol": r.get("symbol"), "bid": r.get("bid_inclusive_of_sell_spread"),
                           "ask": r.get("ask_inclusive_of_buy_spread"), "price": r.get("price")}
                          for r in data.get("results", [])]
                return self._send(200, {"quotes": quotes})
            if url.path.startswith("/api/orders/"):
                oid = url.path.split("/")[3]
                return self._send(200, rh_request("GET", f"/api/v1/crypto/trading/orders/{urllib.parse.quote(oid)}/"))
            return self._send(404, {"error": "not found"})
        except RobinhoodError as e:
            return self._send(e.status if e.status < 600 else 502, {"error": str(e), "detail": e.payload})

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        if not self._authorized():
            return self._send(403, {"error": "forbidden"})
        length = min(int(self.headers.get("Content-Length") or 0), 20000)
        payload = _safe_json(self.rfile.read(length).decode() or "{}")
        try:
            if url.path == "/api/orders":
                return self._send(200, place_order(payload))
            parts = url.path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "orders"] and parts[3] == "cancel":
                oid = parts[2]
                if not LIVE or oid.startswith("dryrun-"):
                    audit("cancel", broker_id=oid, live=False)
                    return self._send(200, {"ok": True, "dry_run": True})
                r = rh_request("POST", f"/api/v1/crypto/trading/orders/{urllib.parse.quote(oid)}/cancel/", body={})
                audit("cancel", broker_id=oid, live=True)
                return self._send(200, {"ok": True, "result": r})
            return self._send(404, {"error": "not found"})
        except (ValueError, PermissionError) as e:
            audit("order_rejected_by_bridge", reason=str(e))
            return self._send(400, {"error": str(e)})
        except TimeoutError as e:
            return self._send(504, {"error": str(e)})
        except RobinhoodError as e:
            audit("robinhood_error", status=e.status, detail=e.payload)
            return self._send(e.status if e.status < 600 else 502, {"error": str(e), "detail": e.payload})


def gen_key():
    key = Ed25519PrivateKey.generate()
    priv = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print("PRIVATE key (keep secret, set as RH_PRIVATE_KEY_B64):\n  " + base64.b64encode(priv).decode())
    print("PUBLIC key (paste into Robinhood's API credentials portal):\n  " + base64.b64encode(pub).decode())


def main():
    if "--gen-key" in sys.argv:
        return gen_key()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    mode = "LIVE: confirmed orders go to Robinhood" if LIVE else "DRY RUN: orders are signed but not sent"
    print(f"\nSignalDesk bridge on http://127.0.0.1:{PORT}/")
    print(f"  Robinhood key: {mask(API_KEY) or 'NOT SET (app runs in simulation)'}")
    print(f"  Mode: {mode}")
    print(f"  Hard cap per order: ${MAX_ORDER_USD:,.0f} · symbols: {', '.join(ALLOWED)}")
    print(f"  Kill switch: create {KILL_FILE.name} in this folder to refuse new orders")
    print(f"  Audit log: {AUDIT_FILE.name}\n")
    audit("bridge_start", live=LIVE, key=mask(API_KEY))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
