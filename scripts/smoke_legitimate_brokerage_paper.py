from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest


ROOT = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _request(method: str, path: str, *, payload: dict | None = None, api_key: str | None = None) -> dict:
    base_url = os.getenv("LEGITIMATE_BROKERAGE_API_URL", "http://127.0.0.1:8001").rstrip("/")
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urlrequest.Request(f"{base_url}{path}", data=body, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=int(os.getenv("LEGITIMATE_BROKERAGE_TIMEOUT_SECONDS", "10"))) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urlerror.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned {exc.code}: {raw}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"{method} {path} could not reach brokerage service: {exc.reason}") from exc


def main() -> int:
    _load_env_file()
    api_key = os.getenv("LEGITIMATE_BROKERAGE_API_KEY", "").strip()
    account_id = os.getenv("LEGITIMATE_BROKERAGE_ACCOUNT_ID", "").strip()
    if not api_key or not account_id:
        raise RuntimeError("LEGITIMATE_BROKERAGE_API_KEY and LEGITIMATE_BROKERAGE_ACCOUNT_ID must be configured.")

    readiness = _request("GET", "/v1/readiness")
    quotes = _request("GET", "/v1/market/quotes?symbols=AAPL,SPY", api_key=api_key)
    summary = _request("GET", f"/v1/accounts/{account_id}/summary", api_key=api_key)
    order = _request(
        "POST",
        "/v1/orders",
        api_key=api_key,
        payload={
            "account_id": account_id,
            "client_order_id": f"smp-smoke-{int(time.time())}",
            "symbol": "AAPL",
            "asset_class": "equity",
            "side": "buy",
            "order_type": "market",
            "quantity": 1,
            "time_in_force": "day",
            "session": "regular",
            "extended_hours": False,
            "execution_mode": "paper",
            "execution_route": "internal_paper",
        },
    )
    result = {
        "readiness": readiness.get("status"),
        "live_order_routing": readiness.get("live_order_routing"),
        "quote_count": len(quotes.get("quotes") or []),
        "quote_source": quotes.get("source"),
        "account_id": account_id,
        "cash": (summary.get("balance") or {}).get("cash"),
        "paper_order_status": order.get("status"),
        "paper_order_id": order.get("id"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["readiness"] != "not_ready_for_live_trading" or result["live_order_routing"] != "blocked":
        raise RuntimeError("Live routing gate unexpectedly changed.")
    if result["paper_order_status"] != "filled":
        raise RuntimeError(f"Paper order did not fill: {result['paper_order_status']}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
