#!/usr/bin/env python3
"""
chk_server.py — Shopify card-check HTTP API
Runs as a standalone Flask server on port 5002 (managed by supervisor.py).
Exposes the same interface as the Vercel /chk endpoints:
  GET /?cc=num|mm|yy|cvv&site=https://...&proxy=ip:port:user:pass
  GET /chk?...   (same)
Response (plain text, same format as Vercel handler):
  Cc: ...
  Response: CARD_DECLINED / charged / 3D SECURE / ...
  Amount: 1.00 USD
  Site: https://...
  Proxy: ...
  Time: 12.3s
"""
import asyncio
import os
import sys
import time
import threading

from flask import Flask, request, Response

# sh_checker.py lives in vercel_chk/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sh_checker import process_card, parse_cc_string, extract_clean_response

app = Flask(__name__)

# One asyncio event loop per thread — Flask uses a thread pool under threaded=True
_tls = threading.local()

def _get_loop():
    if not getattr(_tls, "loop", None) or _tls.loop.is_closed():
        _tls.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_tls.loop)
    return _tls.loop


def _run(coro, timeout=55):
    loop = _get_loop()
    return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))


@app.route("/", methods=["GET", "POST"])
@app.route("/chk", methods=["GET", "POST"])
def chk():
    cc_raw = request.args.get("cc", "").strip()
    site   = request.args.get("site", "").strip()
    proxy  = request.args.get("proxy", "").strip()

    if not cc_raw:
        return Response("Error: missing 'cc' parameter (format: num|mm|yy|cvv)",
                        status=400, mimetype="text/plain")
    if not site:
        return Response("Error: missing 'site' parameter (Shopify store URL)",
                        status=400, mimetype="text/plain")

    try:
        parts = parse_cc_string(cc_raw)
        cc  = parts["cc"]
        mes = parts["mes"]
        ano = parts["ano"]
        cvv = parts["cvv"]
    except Exception as e:
        return Response(f"Error: invalid cc format — {e}", status=400, mimetype="text/plain")

    t0 = time.time()
    success = False
    message = "ERROR"
    total_price = "0"
    currency = "USD"

    try:
        success, message, _gw, total_price, currency = _run(
            process_card(cc, mes, ano, cvv, site, proxy_str=proxy or None),
            timeout=55,
        )
    except asyncio.TimeoutError:
        message = "TIMEOUT"
    except Exception as e:
        message = str(e) or type(e).__name__

    elapsed = round(time.time() - t0, 2)
    clean_msg = extract_clean_response(message)
    amount_str = (
        f"{total_price} {currency.upper()}"
        if total_price and str(total_price) not in ("0", "0.0", "0.00")
        else (total_price or "0")
    )

    body = (
        f"Cc: {cc_raw}\n"
        f"Response: {clean_msg}\n"
        f"Amount: {amount_str}\n"
        f"Site: {site}\n"
        f"Proxy: {proxy or 'None'}\n"
        f"Time: {elapsed}s"
    )
    return Response(body, status=200, mimetype="text/plain; charset=utf-8")


@app.route("/health", methods=["GET"])
def health():
    return Response("OK", status=200, mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("CHK_PORT", 5002))
    print(f"[CHK] Shopify checker API starting on port {port}", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True)
