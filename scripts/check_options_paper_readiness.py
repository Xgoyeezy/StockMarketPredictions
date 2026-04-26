from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.manage_api_runtime import bool_from_env, build_runtime_config, probe_url


def _request_json(url: str, *, headers: dict[str, str] | None = None, timeout_seconds: int = 10) -> dict[str, Any]:
    request = Request(url, headers=headers or {}, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return {
                "reachable": True,
                "status_code": int(getattr(response, "status", 0) or 0),
                "payload": json.loads(raw) if raw else None,
                "message": None,
            }
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = None
        try:
            payload = json.loads(raw) if raw else None
        except Exception:
            payload = None
        message = None
        if isinstance(payload, dict):
            for key in ("message", "detail", "description", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    message = value.strip()
                    break
        return {
            "reachable": True,
            "status_code": int(exc.code),
            "payload": payload,
            "message": message or raw or None,
        }
    except URLError as exc:
        return {
            "reachable": False,
            "status_code": None,
            "payload": None,
            "message": str(exc.reason or exc),
        }
    except Exception as exc:  # pragma: no cover - defensive guard
        return {
            "reachable": False,
            "status_code": None,
            "payload": None,
            "message": str(exc),
        }


def _alpaca_base_url(*, use_sandbox: bool) -> str:
    return "https://data.sandbox.alpaca.markets" if use_sandbox else "https://data.alpaca.markets"


def _probe_options_feed(
    *,
    base_url: str,
    api_key_id: str,
    api_secret_key: str,
    feed: str,
    ticker: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    headers = {
        "APCA-API-KEY-ID": api_key_id,
        "APCA-API-SECRET-KEY": api_secret_key,
        "Accept": "application/json",
        "User-Agent": "StockMarketPredictions/options-paper-readiness",
    }
    result = _request_json(
        f"{base_url}/v1beta1/options/snapshots/{ticker}?feed={feed}&limit=1",
        headers=headers,
        timeout_seconds=timeout_seconds,
    )
    result["feed"] = feed
    return result


def classify_readiness(
    *,
    feed: str,
    use_sandbox: bool,
    paper_keys_present: bool,
    opra_probe: dict[str, Any],
    indicative_probe: dict[str, Any],
    backend_running: bool,
    env_file_name: str = ".env",
) -> dict[str, Any]:
    normalized_feed = str(feed or "").strip().lower()
    if not paper_keys_present:
        broker_code = "credentials_missing"
        broker_message = "Paper Alpaca market-data credentials are not configured."
    elif normalized_feed != "opra":
        broker_code = "wrong_feed"
        broker_message = "ALPACA_OPTIONS_FEED must be set to opra for option-native paper validation."
    elif use_sandbox:
        broker_code = "sandbox_mismatch"
        broker_message = "ALPACA_USE_SANDBOX is enabled. Real listed-option validation requires live market data with the paper trading account."
    elif opra_probe.get("status_code") == 401:
        broker_code = "credentials_rejected"
        broker_message = "Alpaca rejected the configured paper market-data credentials."
    elif (
        opra_probe.get("status_code") == 403
        and indicative_probe.get("status_code") == 200
        and "subscription does not permit querying opra data" in str(opra_probe.get("message") or "").strip().lower()
    ):
        broker_code = "opra_not_entitled"
        broker_message = "The paper credentials are valid, but the Alpaca market-data subscription does not include OPRA."
    elif opra_probe.get("status_code") == 200:
        broker_code = "ready"
        broker_message = "Alpaca OPRA access is available for option-native paper validation."
    else:
        broker_code = "provider_unreachable"
        broker_message = str(opra_probe.get("message") or "Could not reach Alpaca options market data.")

    backend_code = "running" if backend_running else "backend_not_running"
    if broker_code != "ready":
        status = "blocked"
        next_action = (
            "Enable an Alpaca market-data plan that includes OPRA for these paper credentials."
            if broker_code == "opra_not_entitled"
            else "Set ALPACA_USE_SANDBOX=false for real options market data."
            if broker_code == "sandbox_mismatch"
            else "Set ALPACA_OPTIONS_FEED=opra."
            if broker_code == "wrong_feed"
            else "Configure valid Alpaca paper market-data credentials."
            if broker_code in {"credentials_missing", "credentials_rejected"}
            else "Restore the Alpaca options market-data path."
        )
    elif not backend_running:
        status = "warning"
        next_action = f"Start the backend with scripts/manage_api_runtime.py start --env-file {env_file_name} before running the options lane."
    else:
        status = "ready"
        next_action = "Run the dedicated options lane: scan -> execute-paper -> refresh-positions -> close-paper during regular market hours."
    return {
        "status": status,
        "broker_code": broker_code,
        "broker_message": broker_message,
        "backend_code": backend_code,
        "next_action": next_action,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose local options paper-readiness without changing broker or runtime state.")
    parser.add_argument("env_file", nargs="?", default=".env", help="Env file to inspect.")
    parser.add_argument("--ticker", default="SPY", help="Ticker to use for direct options data probes.")
    args = parser.parse_args(argv)

    config = build_runtime_config(args.env_file)
    env = config["env_values"]
    feed = str(env.get("ALPACA_OPTIONS_FEED", "opra") or "opra").strip().lower() or "opra"
    use_sandbox = bool_from_env(env.get("ALPACA_USE_SANDBOX"), False)
    api_key_id = str(env.get("APCA_API_KEY_ID", env.get("ALPACA_API_KEY_ID", "")) or "").strip()
    api_secret_key = str(env.get("APCA_API_SECRET_KEY", env.get("ALPACA_API_SECRET_KEY", "")) or "").strip()
    paper_keys_present = bool(api_key_id and api_secret_key)

    base_url = _alpaca_base_url(use_sandbox=use_sandbox)
    timeout_seconds = int(env.get("ALPACA_MARKET_DATA_REQUEST_TIMEOUT_SECONDS", "10") or 10)
    if paper_keys_present:
        opra_probe = _probe_options_feed(
            base_url=base_url,
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            feed="opra",
            ticker=args.ticker,
            timeout_seconds=timeout_seconds,
        )
        indicative_probe = _probe_options_feed(
            base_url=base_url,
            api_key_id=api_key_id,
            api_secret_key=api_secret_key,
            feed="indicative",
            ticker=args.ticker,
            timeout_seconds=timeout_seconds,
        )
    else:
        opra_probe = {"reachable": False, "status_code": None, "payload": None, "message": None, "feed": "opra"}
        indicative_probe = {"reachable": False, "status_code": None, "payload": None, "message": None, "feed": "indicative"}

    health = probe_url(config["health_url"])
    ready = probe_url(config["ready_url"], timeout_seconds=10.0)
    backend_running = health.get("status_code") == 200
    options_snapshot = None
    options_snapshot_probe = None
    if backend_running:
        options_snapshot_probe = probe_url(f"{config['api_base_url']}/orgs/options-automation", timeout_seconds=10)
        options_snapshot = (options_snapshot_probe.get("payload") or {}).get("data") if options_snapshot_probe.get("reachable") else None
    summary = classify_readiness(
        feed=feed,
        use_sandbox=use_sandbox,
        paper_keys_present=paper_keys_present,
        opra_probe=opra_probe,
        indicative_probe=indicative_probe,
        backend_running=backend_running,
        env_file_name=config["env_path"].name,
    )

    payload = {
        **summary,
        "env_file": str(config["env_path"]),
        "api_base_url": config["api_base_url"],
        "paper_keys_present": paper_keys_present,
        "feed": feed,
        "use_sandbox": use_sandbox,
        "ticker": str(args.ticker or "SPY").strip().upper(),
        "direct_probes": {
            "opra": opra_probe,
            "indicative": indicative_probe,
        },
        "backend": {
            "running": backend_running,
            "health": health,
            "ready": ready,
            "options_automation_probe": options_snapshot_probe,
            "options_automation_snapshot": options_snapshot,
        },
    }
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] in {"ready", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
