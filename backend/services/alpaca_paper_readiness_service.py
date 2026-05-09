from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.services.serialization import serialize_value


RUNTIME_DIR = Path("runtime") / "alpaca-paper"
REJECTED_STATUSES = {"rejected", "expired", "canceled", "cancelled", "failed"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized == normalized else default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _frame_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [dict(item) for item in frame.to_dict(orient="records")]


def _status_from_row(row: dict[str, Any]) -> str:
    return str(row.get("broker_status") or row.get("status") or row.get("order_status") or "").strip().lower()


def _has_text(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text.lower() not in {"nan", "none", "null"})


def classify_alpaca_error(status_code: int | None = None, message: str | None = None) -> dict[str, Any]:
    code = _safe_int(status_code, 0) or None
    text = str(message or "").strip().lower()
    if code in {401, 403} or "auth" in text or "permission" in text:
        category = "credentials_or_permission"
        retryable = False
    elif code == 429 or "rate" in text:
        category = "rate_limited"
        retryable = True
    elif code and code >= 500:
        category = "provider_unavailable"
        retryable = True
    elif "buying power" in text or "insufficient" in text:
        category = "account_capacity"
        retryable = False
    elif "market closed" in text:
        category = "session_closed"
        retryable = False
    elif text:
        category = "provider_rejected"
        retryable = False
    else:
        category = "none"
        retryable = False
    return {"category": category, "retryable": retryable, "status_code": code}


def _duplicate_client_order_guard(
    rows: list[dict[str, Any]],
    *,
    historical_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    identifiers: list[str] = []
    for row in rows:
        for key in ("broker_client_order_id", "client_order_id", "order_id"):
            identifier = str(row.get(key) or "").strip()
            if identifier:
                identifiers.append(identifier)
                break
    counts = Counter(identifiers)
    duplicates = sorted(identifier for identifier, count in counts.items() if count > 1)
    historical_identifiers: list[str] = []
    for row in historical_rows or []:
        for key in ("broker_client_order_id", "client_order_id", "order_id"):
            identifier = str(row.get(key) or "").strip()
            if identifier:
                historical_identifiers.append(identifier)
                break
    historical_counts = Counter(historical_identifiers)
    historical_duplicates = sorted(identifier for identifier, count in historical_counts.items() if count > 1)
    return {
        "enabled": True,
        "duplicate_count": len(duplicates),
        "duplicates": duplicates[:25],
        "historical_duplicate_count": len(historical_duplicates),
        "historical_duplicates": historical_duplicates[:25],
        "status": "blocked" if duplicates else "ready",
    }


def _latency_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    samples: list[float] = []
    for row in rows:
        submitted = str(row.get("broker_submitted_at") or row.get("submitted_at") or "").strip()
        updated = str(row.get("broker_updated_at") or row.get("updated_at") or row.get("filled_at") or "").strip()
        if not submitted or not updated:
            continue
        try:
            submitted_at = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
            updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            continue
        samples.append(max((updated_at - submitted_at).total_seconds() * 1000.0, 0.0))
    if not samples:
        return {"sample_count": 0, "avg_ms": None, "p95_ms": None}
    sorted_samples = sorted(samples)
    p95_index = min(max(int(round((len(sorted_samples) - 1) * 0.95)), 0), len(sorted_samples) - 1)
    return {
        "sample_count": len(samples),
        "avg_ms": round(sum(samples) / len(samples), 3),
        "p95_ms": round(sorted_samples[p95_index], 3),
    }


def build_alpaca_paper_readiness_snapshot(*, account_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    open_rows = _frame_records(sdm.read_open_trades())
    pending_rows = _frame_records(sdm.read_pending_orders())
    closed_rows = _frame_records(sdm.read_closed_trades())
    all_rows = open_rows + pending_rows + closed_rows
    rejected_rows = [row for row in all_rows if _status_from_row(row) in REJECTED_STATUSES]
    missing_broker_order_id = [row for row in pending_rows if not _has_text(row.get("broker_order_id"))]
    status_counts = Counter(_status_from_row(row) or "unknown" for row in all_rows)
    credentials_present = bool(settings.alpaca_api_key_id and settings.alpaca_api_secret_key)
    paper_base_url = settings.alpaca_paper_trading_api_url
    account = dict(account_summary or {})
    equity = max(_safe_float(account.get("equity")), _safe_float(account.get("portfolio_value")))
    buying_power = _safe_float(account.get("buying_power"))
    route_ready = credentials_present and "paper" in paper_base_url.lower()
    return serialize_value(
        {
            "checked_at": _utc_now_iso(),
            "status": "ready" if route_ready else "degraded",
            "provider": "alpaca",
            "mode": "paper",
            "route": "broker_paper",
            "paper_mode_asserted": "paper" in paper_base_url.lower(),
            "credentials": {
                "api_key_present": bool(settings.alpaca_api_key_id),
                "secret_key_present": bool(settings.alpaca_api_secret_key),
                "secrets_exposed": False,
            },
            "account_heartbeat": {
                "available": bool(account),
                "equity": equity or None,
                "buying_power": buying_power or None,
                "buying_power_is_ceiling": True,
                "history_path": str(RUNTIME_DIR / "account_history.jsonl"),
            },
            "market_clock": {
                "status": "not_queried",
                "detail": "Readiness endpoint does not call Alpaca during operator polling; market-open tooling performs live checks.",
            },
            "tradeability": {
                "status": "not_queried",
                "detail": "Per-symbol tradeability is checked by the execution adapter before paper submit.",
            },
            "reconciliation": {
                "open_count": len(open_rows),
                "pending_count": len(pending_rows),
                "closed_count": len(closed_rows),
                "missing_broker_order_id_count": len(missing_broker_order_id),
                "needs_review": bool(missing_broker_order_id),
            },
            "duplicate_client_order_id_guard": _duplicate_client_order_guard(
                pending_rows + open_rows,
                historical_rows=closed_rows,
            ),
            "rejected_order_normalization": {
                "rejected_count": len(rejected_rows),
                "status_counts": dict(status_counts),
                "categories": [classify_alpaca_error(message=str(row.get("rejection_reason") or row.get("broker_message") or "")) for row in rejected_rows[:25]],
            },
            "latency": {
                "submit_to_update": _latency_summary(all_rows),
                "fill_latency": _latency_summary(closed_rows + open_rows),
            },
            "partial_fill_evidence": {
                "partial_fill_count": sum(1 for row in all_rows if str(row.get("broker_status") or "").strip().lower() == "partially_filled"),
            },
            "cancel_replace_evidence": {
                "cancel_replace_supported": True,
                "cancel_replace_events_in_local_books": sum(1 for row in all_rows if str(row.get("broker_replaced_at") or row.get("broker_canceled_at") or "").strip()),
            },
            "position_sync_guard": {
                "enabled": True,
                "detail": "Pending and open local books are reconciled before unattended entry cycles can submit more paper orders.",
            },
            "pdt_margin_warning": {
                "visible": True,
                "detail": "Buying power is treated as capacity, not the ticket-sizing base.",
            },
            "retry_backoff": {
                "retry_budget": 3,
                "backoff_policy": "bounded_exponential_for_retryable_provider_errors",
            },
            "paper_account_mismatch_blocker": None if route_ready else "alpaca_paper_credentials_missing",
            "runtime_paths": {
                "account_history": str(RUNTIME_DIR / "account_history.jsonl"),
                "reconciliation_summary": str(RUNTIME_DIR / "reconciliation_summary.json"),
            },
        }
    )
