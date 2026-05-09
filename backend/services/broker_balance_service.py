from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.core.config import settings
from backend.services.execution.alpaca_client import AlpacaApiError, build_alpaca_paper_client
from backend.services.execution.tradier_client import (
    TradierApiError,
    build_tradier_paper_client,
    normalize_tradier_balances,
)
from backend.services.serialization import serialize_value


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _empty_snapshot(*, provider: str, status: str, detail: str) -> dict[str, Any]:
    return {
        "provider": provider,
        "equity": None,
        "cash": None,
        "buying_power": None,
        "option_buying_power": None,
        "status": status,
        "detail": detail,
        "last_refreshed_at": None,
    }


def _disabled_snapshot(*, provider: str, detail: str) -> dict[str, Any]:
    return _empty_snapshot(provider=provider, status="disabled", detail=detail)


def _internal_paper_mode() -> bool:
    broker_mode = str(getattr(settings, "broker_mode", "internal_paper") or "internal_paper").strip().lower()
    paper_provider = str(getattr(settings, "paper_broker_provider", "internal_paper") or "internal_paper").strip().lower()
    return broker_mode in {"internal", "internal_paper", "internal_simulator"} or paper_provider in {
        "internal",
        "internal_paper",
        "internal_simulator",
    }


def get_alpaca_paper_balance_snapshot() -> dict[str, Any]:
    if _internal_paper_mode():
        return _disabled_snapshot(
            provider="alpaca",
            detail="Alpaca paper is deprecated in internal-paper mode and is not queried.",
        )
    if not settings.alpaca_api_key_id or not settings.alpaca_api_secret_key:
        return _empty_snapshot(
            provider="alpaca",
            status="missing_credentials",
            detail="Alpaca paper credentials are not configured.",
        )
    try:
        account = build_alpaca_paper_client().get_account()
    except AlpacaApiError as exc:
        return {
            **_empty_snapshot(provider="alpaca", status="error", detail=str(exc)),
            "status_code": exc.status_code,
        }
    equity = _coerce_float(account.get("equity") or account.get("portfolio_value"))
    cash = _coerce_float(account.get("cash"))
    buying_power = _coerce_float(account.get("buying_power"))
    return {
        "provider": "alpaca",
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "option_buying_power": _coerce_float(account.get("options_buying_power") or account.get("option_buying_power")),
        "status": "ready",
        "detail": "Alpaca paper account connected.",
        "last_refreshed_at": _utc_iso(),
    }


def get_tradier_paper_balance_snapshot() -> dict[str, Any]:
    if _internal_paper_mode():
        return _disabled_snapshot(
            provider="tradier",
            detail="Tradier paper is deprecated in internal-paper mode and is not queried.",
        )
    if not settings.tradier_paper_token or not settings.tradier_paper_account_id:
        return _empty_snapshot(
            provider="tradier",
            status="missing_credentials",
            detail="Tradier paper credentials are not configured.",
        )
    try:
        client = build_tradier_paper_client()
        balances = normalize_tradier_balances(client.get_account_balances())
    except TradierApiError as exc:
        return {
            **_empty_snapshot(provider="tradier", status="error", detail=str(exc)),
            "status_code": exc.status_code,
        }
    return {
        "provider": "tradier",
        "equity": _coerce_float(balances.get("equity")),
        "cash": _coerce_float(balances.get("cash")),
        "buying_power": _coerce_float(balances.get("buying_power")),
        "option_buying_power": _coerce_float(balances.get("option_buying_power")),
        "status": "ready",
        "detail": "Tradier paper account connected.",
        "last_refreshed_at": _utc_iso(),
        "account_type": balances.get("account_type"),
    }


def _sum_present(*values: Any) -> float | None:
    numbers = [_coerce_float(value) for value in values]
    present = [value for value in numbers if value is not None]
    if not present:
        return None
    return float(sum(present))


def get_paper_broker_balance_snapshot() -> dict[str, Any]:
    if _internal_paper_mode():
        alpaca = get_alpaca_paper_balance_snapshot()
        tradier = get_tradier_paper_balance_snapshot()
        return serialize_value(
            {
                "alpaca_paper": alpaca,
                "tradier_paper": tradier,
                "combined_paper": {
                    "provider": "internal_paper",
                    "equity": None,
                    "cash": None,
                    "buying_power": None,
                    "option_buying_power": None,
                    "status": "disabled",
                    "detail": "Use the internal broker/router balance snapshot in internal-paper mode.",
                    "last_refreshed_at": None,
                },
                "routing": {
                    "broker_mode": "internal_paper",
                    "equities": "internal_paper",
                    "options": "internal_paper",
                    "options_data": "free_delayed",
                },
            }
        )
    alpaca = get_alpaca_paper_balance_snapshot()
    tradier = get_tradier_paper_balance_snapshot()
    combined = {
        "provider": "combined",
        "equity": _sum_present(alpaca.get("equity"), tradier.get("equity")),
        "cash": _sum_present(alpaca.get("cash"), tradier.get("cash")),
        "buying_power": _sum_present(alpaca.get("buying_power"), tradier.get("buying_power")),
        "option_buying_power": tradier.get("option_buying_power"),
        "status": "ready" if alpaca.get("status") == "ready" or tradier.get("status") == "ready" else "missing_credentials",
        "detail": "Combined paper balances across configured brokers.",
        "last_refreshed_at": _utc_iso(),
    }
    return serialize_value(
        {
            "alpaca_paper": alpaca,
            "tradier_paper": tradier,
            "combined_paper": combined,
            "routing": {
                "equities": "alpaca",
                "options": settings.options_broker_provider,
                "options_data": settings.options_data_provider,
            },
        }
    )
