from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, equity_snapshot_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.mappers import normalize_alpaca_status
from backend.services.execution.provider_registry import get_execution_adapter_for
from backend.services.serialization import serialize_value

PAPER_BROKER_NOTE_OWNER = "automation-ai"
PAPER_BROKER_HISTORY_LIMIT = 8
PAPER_BROKER_NOTE_LIMIT = 250
PAPER_BROKER_PERSONAL_PAPER_PROFILE = "personal_paper"
PAPER_BROKER_ORDER_LOOKBACK_DAYS = 7

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or PAPER_BROKER_PERSONAL_PAPER_PROFILE).strip().lower().replace(":", "-")


def normalize_paper_broker_reconciliation_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("paper_broker_reconciliation_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("paper_broker_reconciliation_history") or [])[:PAPER_BROKER_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "paper_broker_reconciliation_last_report": serialize_value(last_report),
        "paper_broker_reconciliation_last_note_id": str(
            runtime.get("paper_broker_reconciliation_last_note_id") or ""
        ).strip() or None,
        "paper_broker_reconciliation_note_session_day": str(
            runtime.get("paper_broker_reconciliation_note_session_day") or ""
        ).strip() or None,
        "paper_broker_reconciliation_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_broker_reconciliation_last_run_at"))
        ),
        "paper_broker_reconciliation_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_broker_reconciliation_last_scheduled_run_at"))
        ),
        "paper_broker_reconciliation_last_scheduled_session_day": str(
            runtime.get("paper_broker_reconciliation_last_scheduled_session_day") or ""
        ).strip() or None,
        "paper_broker_reconciliation_last_error": str(
            runtime.get("paper_broker_reconciliation_last_error") or ""
        ).strip() or None,
        "paper_broker_reconciliation_history": history,
    }


def build_paper_broker_reconciliation_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_paper_broker_reconciliation_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("paper_broker_reconciliation_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "checked_at": None,
            "matched_count": 0,
            "orphan_broker_order_count": 0,
            "orphan_local_order_count": 0,
            "position_mismatch_count": 0,
            "fill_mismatch_count": 0,
            "ledger_consistency": "unknown",
            "blockers": [],
            "warnings": [],
            "related_note_id": runtime.get("paper_broker_reconciliation_last_note_id"),
            "last_run_at": runtime.get("paper_broker_reconciliation_last_run_at"),
            "last_scheduled_run_at": runtime.get("paper_broker_reconciliation_last_scheduled_run_at"),
            "last_error": runtime.get("paper_broker_reconciliation_last_error"),
            "broker_available": False,
            "run_source": None,
        }
    report.setdefault("related_note_id", runtime.get("paper_broker_reconciliation_last_note_id"))
    report.setdefault("note_id", runtime.get("paper_broker_reconciliation_last_note_id"))
    report.setdefault("last_run_at", runtime.get("paper_broker_reconciliation_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("paper_broker_reconciliation_last_scheduled_run_at"))
    report.setdefault("last_error", runtime.get("paper_broker_reconciliation_last_error"))
    return serialize_value(report)


def _safe_frame(reader: Any) -> pd.DataFrame:
    try:
        frame = reader()
    except Exception:
        return pd.DataFrame()
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _owned_rows(frame: pd.DataFrame, *, tenant_id: str, profile_key: str) -> pd.DataFrame:
    return automation_ai_review_service._owned_automation_rows(
        frame,
        tenant_id=str(tenant_id),
        profile_key=profile_key,
    )


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [dict(item) for item in frame.to_dict(orient="records")]


def _normalized_id(value: Any) -> str:
    return str(value or "").strip()


def _broker_order_id(row: dict[str, Any]) -> str:
    for key in ("broker_order_id", "broker_close_order_id"):
        value = _normalized_id(row.get(key))
        if value:
            return value
    return ""


def _broker_order_ids(row: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            _normalized_id(row.get("broker_order_id")),
            _normalized_id(row.get("broker_close_order_id")),
        )
        if value
    }


def _broker_client_order_id(row: dict[str, Any]) -> str:
    for key in ("broker_client_order_id", "order_id", "client_order_id"):
        value = _normalized_id(row.get(key))
        if value:
            return value
    return ""


def _broker_client_order_ids(row: dict[str, Any]) -> set[str]:
    return {
        value
        for value in (
            _normalized_id(row.get("broker_client_order_id")),
            _normalized_id(row.get("order_id")),
            _normalized_id(row.get("client_order_id")),
        )
        if value
    }


def _order_identity(order: dict[str, Any]) -> tuple[str, str]:
    return _normalized_id(order.get("id")), _normalized_id(order.get("client_order_id"))


def _is_known_readiness_probe_order(order: dict[str, Any]) -> bool:
    client_id = _normalized_id(order.get("client_order_id")).lower()
    return client_id.startswith("codex-alpaca-paper-e2e-")


def _is_working_status(status: Any) -> bool:
    normalized = normalize_alpaca_status(status)
    return normalized in {"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding"}


def _is_filled_status(status: Any) -> bool:
    return normalize_alpaca_status(status) == "filled"


def _is_terminal_status(status: Any) -> bool:
    normalized = normalize_alpaca_status(status)
    return normalized in {"canceled", "cancelled", "expired", "rejected", "done_for_day", "stopped", "suspended"}


def _symbol_for_row(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or row.get("underlying_symbol") or "").strip().upper()


def _symbol_for_position(position: dict[str, Any]) -> str:
    return str(position.get("symbol") or position.get("ticker") or "").strip().upper()


def _quantity_for_row(row: dict[str, Any]) -> float:
    for key in ("suggested_contracts", "quantity", "qty", "broker_qty"):
        if row.get(key) not in (None, ""):
            return abs(_coerce_float(row.get(key), 0.0))
    return 0.0


def _quantity_for_position(position: dict[str, Any]) -> float:
    return abs(_coerce_float(position.get("qty") or position.get("quantity"), 0.0))


def _quantity_for_order(order: dict[str, Any]) -> float:
    for key in ("filled_qty", "qty", "quantity", "filled_quantity"):
        if order.get(key) not in (None, ""):
            return abs(_coerce_float(order.get(key), 0.0))
    return 0.0


def _timestamp_for_broker_order(order: dict[str, Any]) -> datetime | None:
    for key in ("filled_at", "updated_at", "submitted_at", "created_at"):
        parsed = _parse_datetime(order.get(key))
        if parsed is not None:
            return parsed
    return None


def _closed_trade_matches_filled_order(row: dict[str, Any], order: dict[str, Any]) -> bool:
    if _symbol_for_row(row) != str(order.get("symbol") or "").strip().upper():
        return False
    status = str(row.get("status") or "").strip().upper()
    if status not in {"CLOSED", "RECONCILED"}:
        return False
    order_qty = _quantity_for_order(order)
    row_qty_candidates = {
        _quantity_for_row(row),
        abs(_coerce_float(row.get("closed_contracts"), 0.0)),
        abs(_coerce_float(row.get("filled_contracts"), 0.0)),
    }
    row_qty_candidates = {qty for qty in row_qty_candidates if qty > 0}
    if order_qty > 0 and row_qty_candidates:
        quantity_matched = any(abs(order_qty - qty) <= max(0.001, order_qty * 0.001) for qty in row_qty_candidates)
        if not quantity_matched:
            return False
    closed_at = _parse_datetime(row.get("closed_at"))
    order_time = _timestamp_for_broker_order(order)
    if closed_at is None or order_time is None:
        return False
    return abs((closed_at - order_time).total_seconds()) <= 10 * 60


def _closed_trade_absorbs_unlinked_flat_close_order(
    row: dict[str, Any],
    order: dict[str, Any],
    *,
    broker_position_by_symbol: dict[str, float],
) -> bool:
    symbol = str(order.get("symbol") or "").strip().upper()
    if not symbol or _symbol_for_row(row) != symbol:
        return False
    if not _is_filled_status(order.get("status")):
        return False
    if str(order.get("side") or "").strip().lower() != "sell":
        return False
    if float(broker_position_by_symbol.get(symbol, 0.0)) != 0.0:
        return False

    status = str(row.get("status") or "").strip().upper()
    if status not in {"CLOSED", "RECONCILED", "PARTIAL"}:
        return False

    remaining_after_close = _coerce_float(row.get("remaining_contracts_after_close"), 0.0)
    source = str(row.get("reconciliation_source") or "").strip().lower()
    if remaining_after_close > 0.001:
        return False
    if source != "alpaca_paper_flat_account" and status not in {"CLOSED", "RECONCILED"}:
        return False

    order_qty = _quantity_for_order(order)
    row_qty_candidates = {
        abs(_coerce_float(row.get("closed_contracts"), 0.0)),
        abs(_coerce_float(row.get("filled_contracts"), 0.0)),
        _quantity_for_row(row),
    }
    row_qty_candidates = {qty for qty in row_qty_candidates if qty > 0}
    if order_qty > 0 and row_qty_candidates:
        quantity_matched = any(abs(order_qty - qty) <= max(0.001, order_qty * 0.001) for qty in row_qty_candidates)
        if not quantity_matched:
            return False

    closed_at = _parse_datetime(row.get("closed_at"))
    order_time = _timestamp_for_broker_order(order)
    if closed_at is None or order_time is None:
        return source == "alpaca_paper_flat_account"
    tolerance_seconds = 60 * 60 if source == "alpaca_paper_flat_account" else 10 * 60
    return abs((closed_at - order_time).total_seconds()) <= tolerance_seconds


def _open_trade_absorbs_unlinked_partial_close_order(
    row: dict[str, Any],
    order: dict[str, Any],
    *,
    broker_position_by_symbol: dict[str, float],
) -> bool:
    symbol = str(order.get("symbol") or "").strip().upper()
    if not symbol or _symbol_for_row(row) != symbol:
        return False
    if not _is_filled_status(order.get("status")):
        return False
    if str(order.get("side") or "").strip().lower() != "sell":
        return False
    source = str(row.get("reconciliation_source") or "").strip().lower()
    if source not in {"alpaca_paper_filled_close_order", "alpaca_paper_filled_close_order_deduped"}:
        return False
    status = str(row.get("status") or row.get("order_status") or "").strip().upper()
    book_state = str(row.get("book_state") or "").strip().lower()
    if status not in {"OPEN", "PARTIAL", "FILLED"} and book_state != "open":
        return False
    broker_qty = float(broker_position_by_symbol.get(symbol, 0.0))
    local_qty = _quantity_for_row(row)
    if broker_qty <= 0.0 or local_qty <= 0.0:
        return False
    tolerance = max(0.001, min(local_qty, broker_qty) * 0.001)
    if abs(local_qty - broker_qty) > tolerance:
        return False
    return _quantity_for_order(order) > 0.0


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _row_matches_event_trade(row: dict[str, Any], trade: dict[str, Any]) -> bool:
    row_trade_id = _normalized_id(row.get("trade_id"))
    row_order_id = _normalized_id(row.get("order_id"))
    event_trade_id = _normalized_id(trade.get("trade_id"))
    event_order_id = _normalized_id(trade.get("order_id"))
    return bool(
        (row_trade_id and event_trade_id and row_trade_id == event_trade_id)
        or (row_order_id and event_order_id and row_order_id == event_order_id)
    )


def _filled_close_order_matches_recorded_event(
    order: dict[str, Any],
    events: list[dict[str, Any]],
    local_rows: list[dict[str, Any]],
) -> bool:
    if not _is_filled_status(order.get("status")):
        return False
    if str(order.get("side") or "").strip().lower() != "sell":
        return False
    broker_id, client_id = _order_identity(order)
    if not broker_id and not client_id:
        return False
    symbol = str(order.get("symbol") or "").strip().upper()
    for event in events:
        payload = _event_payload(event)
        event_key = str(event.get("event_key") or "").strip().lower()
        route_state = str(event.get("route_state") or payload.get("route_state") or "").strip().lower()
        if event_key != "order.close_working" and route_state != "close_working":
            continue
        trade = dict(payload.get("trade") or {})
        event_broker_ids = {
            _normalized_id(payload.get("broker_order_id")),
            _normalized_id(payload.get("broker_close_order_id")),
            _normalized_id(trade.get("broker_close_order_id")),
        }
        event_client_ids = {
            _normalized_id(payload.get("broker_client_order_id")),
            _normalized_id(payload.get("broker_close_client_order_id")),
            _normalized_id(trade.get("broker_close_client_order_id")),
        }
        if broker_id and broker_id not in event_broker_ids:
            if not (client_id and client_id in event_client_ids):
                continue
        elif not broker_id and client_id and client_id not in event_client_ids:
            continue
        for row in local_rows:
            if symbol and _symbol_for_row(row) != symbol:
                continue
            if _row_matches_event_trade(row, trade):
                return True
    return False


def _local_order_lookup(local_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_broker_id: dict[str, dict[str, Any]] = {}
    by_client_id: dict[str, dict[str, Any]] = {}
    for row in local_rows:
        for broker_id in _broker_order_ids(row):
            by_broker_id[broker_id] = row
        for client_id in _broker_client_order_ids(row):
            by_client_id[client_id] = row
    return by_broker_id, by_client_id


def _match_local_order(
    order: dict[str, Any],
    *,
    by_broker_id: dict[str, dict[str, Any]],
    by_client_id: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    broker_id, client_id = _order_identity(order)
    return (
        (broker_id and by_broker_id.get(broker_id))
        or (client_id and by_client_id.get(client_id))
        or None
    )


def _broker_order_repair_updates(order: dict[str, Any]) -> dict[str, Any]:
    status = normalize_alpaca_status(order.get("status")) or str(order.get("status") or "").strip().lower()
    fill_price = _coerce_float(
        order.get("filled_avg_price") or order.get("average_fill_price") or order.get("filled_average_price"),
        0.0,
    )
    filled_qty = _coerce_float(order.get("filled_qty") or order.get("filled_quantity"), 0.0)
    qty = _coerce_float(order.get("qty") or order.get("quantity"), 0.0)
    updated_at = _serialize_datetime(_timestamp_for_broker_order(order) or _utc_now())
    broker_id, client_id = _order_identity(order)
    return {
        "broker_order_id": broker_id,
        "broker_client_order_id": client_id,
        "broker_status": status,
        "broker_filled_qty": filled_qty,
        "broker_qty": qty,
        "broker_filled_avg_price": fill_price if fill_price > 0 else 0.0,
        "broker_updated_at": updated_at,
        "last_broker_sync_at": _serialize_datetime(_utc_now()),
    }


def _row_owned_by_profile(row: dict[str, Any], *, tenant: Tenant, profile_key: str) -> bool:
    origin = str(row.get("automation_origin") or "").strip().lower()
    if origin and origin != "trade_automation":
        return False
    tenant_id = str(row.get("automation_tenant_id") or row.get("tenant_id") or "").strip()
    if tenant_id and tenant_id != str(tenant.id):
        return False
    normalized_profile = str(profile_key or PAPER_BROKER_PERSONAL_PAPER_PROFILE).strip().lower()
    row_profile = str(row.get("automation_profile_key") or "").strip().lower()
    if row_profile and row_profile != normalized_profile:
        return False
    if normalized_profile != PAPER_BROKER_PERSONAL_PAPER_PROFILE and not row_profile:
        return False
    return True


def _clear_close_fields_for_restored_open(row: dict[str, Any], *, broker_qty: float, close_order: dict[str, Any]) -> dict[str, Any]:
    restored = dict(row)
    status = normalize_alpaca_status(close_order.get("status")) or str(close_order.get("status") or "").strip().lower()
    broker_id, _client_id = _order_identity(close_order)
    restored.update(
        {
            "status": "OPEN",
            "route_state": "close_working",
            "book_state": "open",
            "suggested_contracts": float(broker_qty),
            "quantity": float(broker_qty),
            "remaining_contracts": 0.0,
            "closed_at": "",
            "live_price_at_close": "",
            "contract_mid_at_close": "",
            "closed_contracts": "",
            "remaining_contracts_after_close": "",
            "close_fraction": "",
            "pnl_per_contract": "",
            "realized_pnl": "",
            "broker_close_order_id": broker_id,
            "broker_close_status": status,
            "pending_close_quantity": _quantity_for_order(close_order),
            "pending_close_fraction": "",
            "last_broker_sync_at": _serialize_datetime(_utc_now()),
        }
    )
    return restored


def _restore_working_close_attempts(
    *,
    tenant: Tenant,
    profile_key: str,
    broker_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    orders = [dict(item) for item in list(broker_snapshot.get("orders") or []) if isinstance(item, dict)]
    positions = [dict(item) for item in list(broker_snapshot.get("positions") or []) if isinstance(item, dict)]
    working_close_by_id = {
        _order_identity(order)[0]: order
        for order in orders
        if _order_identity(order)[0]
        and _is_working_status(order.get("status"))
        and str(order.get("side") or "").strip().lower() in {"sell", "buy"}
    }
    if not working_close_by_id:
        return []

    external_probe_position_adjustments: Counter[str] = Counter()
    for order in orders:
        if _is_known_readiness_probe_order(order) and _is_filled_status(order.get("status")):
            symbol = str(order.get("symbol") or "").strip().upper()
            if symbol:
                external_probe_position_adjustments[symbol] += _quantity_for_order(order)
    broker_position_by_symbol = {
        _symbol_for_position(position): max(
            _quantity_for_position(position) - float(external_probe_position_adjustments.get(_symbol_for_position(position), 0.0)),
            0.0,
        )
        for position in positions
        if _symbol_for_position(position)
    }
    if not broker_position_by_symbol:
        return []

    open_df = _safe_frame(sdm.read_open_trades)
    closed_df = _safe_frame(sdm.read_closed_trades)
    if closed_df.empty:
        return []

    open_rows = open_df.to_dict(orient="records") if not open_df.empty else []
    closed_rows = closed_df.to_dict(orient="records")
    actions: list[dict[str, Any]] = []
    closed_indices_to_drop: set[int] = set()

    for closed_index, row in enumerate(closed_rows):
        if not _row_owned_by_profile(row, tenant=tenant, profile_key=profile_key):
            continue
        close_order_id = _normalized_id(row.get("broker_close_order_id"))
        if not close_order_id or close_order_id not in working_close_by_id:
            continue
        symbol = _symbol_for_row(row)
        broker_qty = float(broker_position_by_symbol.get(symbol, 0.0))
        if broker_qty <= 0:
            continue
        close_order = working_close_by_id[close_order_id]
        restored = _clear_close_fields_for_restored_open(row, broker_qty=broker_qty, close_order=close_order)
        match_index = None
        trade_id = _normalized_id(row.get("trade_id"))
        order_id = _normalized_id(row.get("order_id"))
        for index, open_row in enumerate(open_rows):
            if trade_id and _normalized_id(open_row.get("trade_id")) == trade_id:
                match_index = index
                break
            if order_id and _normalized_id(open_row.get("order_id")) == order_id:
                match_index = index
                break
        if match_index is None:
            open_rows.append(restored)
        else:
            open_rows[match_index].update(restored)
        closed_indices_to_drop.add(closed_index)
        actions.append(
            {
                "type": "restored_working_close_to_open",
                "trade_id": trade_id or None,
                "order_id": order_id or None,
                "broker_close_order_id": close_order_id,
                "symbol": symbol,
                "broker_status": normalize_alpaca_status(close_order.get("status")),
                "broker_qty": broker_qty,
            }
        )

    if actions:
        sdm.write_dataframe_csv(sdm.OPEN_TRADES_PATH, pd.DataFrame(open_rows))
        remaining_closed_rows = [row for index, row in enumerate(closed_rows) if index not in closed_indices_to_drop]
        sdm.write_dataframe_csv(sdm.CLOSED_TRADES_PATH, pd.DataFrame(remaining_closed_rows))
        sdm._invalidate_file_read_cache(sdm.OPEN_TRADES_PATH)
        sdm._invalidate_file_read_cache(sdm.CLOSED_TRADES_PATH)
    return serialize_value(actions)


def _open_row_matches_filled_close_order(row: dict[str, Any], order: dict[str, Any]) -> bool:
    broker_id, client_id = _order_identity(order)
    close_order_id = _normalized_id(row.get("broker_close_order_id"))
    close_client_order_id = _normalized_id(row.get("broker_close_client_order_id"))
    if broker_id and close_order_id and broker_id == close_order_id:
        return True
    if client_id and close_client_order_id and client_id == close_client_order_id:
        return True
    return False


def _closed_book_has_close_order(order: dict[str, Any]) -> bool:
    broker_id, client_id = _order_identity(order)
    if not broker_id and not client_id:
        return False
    closed_df = _safe_frame(sdm.read_closed_trades)
    if closed_df.empty:
        return False
    for row in closed_df.to_dict(orient="records"):
        close_order_id = _normalized_id(row.get("broker_close_order_id"))
        close_client_order_id = _normalized_id(row.get("broker_close_client_order_id"))
        if broker_id and close_order_id and broker_id == close_order_id:
            return True
        if client_id and close_client_order_id and client_id == close_client_order_id:
            return True
    return False


def _clear_stale_close_reference(row: dict[str, Any], order: dict[str, Any]) -> dict[str, Any] | None:
    broker_id, client_id = _order_identity(order)
    trade_id = _normalized_id(row.get("trade_id"))
    order_id = _normalized_id(row.get("order_id"))
    updated = sdm.update_open_trade(
        {
            "broker_close_order_id": "",
            "broker_close_client_order_id": "",
            "broker_close_status": "",
            "pending_close_quantity": 0.0,
            "pending_close_fraction": "",
            "route_state": "filled",
            "book_state": "open",
            "last_broker_sync_at": _serialize_datetime(_utc_now()),
            "reconciliation_note": "Cleared stale close-order reference after Alpaca confirmed that close fill was already recorded.",
            "reconciliation_source": "alpaca_paper_filled_close_order_deduped",
        },
        trade_id=trade_id or None,
        order_id=order_id or None,
    )
    if not updated:
        return None
    return {
        "type": "cleared_stale_filled_close_reference",
        "trade_id": trade_id or None,
        "order_id": order_id or None,
        "broker_close_order_id": broker_id or None,
        "client_order_id": client_id or None,
        "symbol": str(order.get("symbol") or _symbol_for_row(row) or "").strip().upper() or None,
        "broker_status": normalize_alpaca_status(order.get("status")) or str(order.get("status") or "").strip().lower(),
    }


def _close_open_trade_from_filled_close_order(order: dict[str, Any]) -> dict[str, Any] | None:
    broker_id, client_id = _order_identity(order)
    if not broker_id and not client_id:
        return None
    open_df = _safe_frame(sdm.read_open_trades)
    if open_df.empty:
        return None

    open_records = open_df.to_dict(orient="records")
    match_position: int | None = None
    match_row: dict[str, Any] | None = None
    for position, row in enumerate(open_records):
        if _open_row_matches_filled_close_order(dict(row), order):
            match_position = position
            match_row = dict(row)
            break
    if match_position is None or match_row is None:
        return None

    filled_qty = _quantity_for_order(order)
    local_qty = _quantity_for_row(match_row)
    if _closed_book_has_close_order(order) or (filled_qty > 0 and local_qty > 0 and filled_qty > local_qty + 0.001):
        return _clear_stale_close_reference(match_row, order)

    close_fraction = 1.0
    if local_qty > 0 and filled_qty > 0:
        close_fraction = min(1.0, max(0.0, filled_qty / local_qty))

    fill_price = _coerce_float(order.get("filled_avg_price") or order.get("avg_price"), 0.0)
    if fill_price <= 0:
        fill_price = _coerce_float(
            order.get("limit_price")
            or match_row.get("live_price_at_close")
            or match_row.get("live_price_at_open")
            or match_row.get("target_price"),
            0.0,
        )
    if fill_price <= 0:
        return None

    status = normalize_alpaca_status(order.get("status")) or str(order.get("status") or "").strip().lower()
    updated_at = _serialize_datetime(_timestamp_for_broker_order(order) or _utc_now())
    close_updates = {
        "order_status": "CLOSED" if close_fraction >= 1.0 else "PARTIAL",
        "route_state": "closed" if close_fraction >= 1.0 else "partial_close",
        "book_state": "flat" if close_fraction >= 1.0 else "open",
        "broker_close_order_id": broker_id or _normalized_id(match_row.get("broker_close_order_id")),
        "broker_close_client_order_id": client_id or _normalized_id(match_row.get("broker_close_client_order_id")),
        "broker_close_status": status,
        "pending_close_quantity": 0.0,
        "pending_close_fraction": "",
        "broker_updated_at": updated_at,
        "last_broker_sync_at": _serialize_datetime(_utc_now()),
        "reconciliation_note": "Closed local open trade after Alpaca confirmed the broker-paper close order filled.",
        "reconciliation_source": "alpaca_paper_filled_close_order",
    }
    closed = sdm.close_trade_by_index(
        int(match_position),
        close_underlying_price=fill_price,
        close_contract_mid=fill_price,
        close_fraction=close_fraction,
        close_updates=close_updates,
    )
    if not closed:
        return None
    if close_fraction < 1.0:
        _clear_stale_close_reference(match_row, order)
    return {
        "type": "closed_open_from_filled_close_order",
        "trade_id": _normalized_id(match_row.get("trade_id")) or None,
        "order_id": _normalized_id(match_row.get("order_id")) or None,
        "broker_close_order_id": broker_id or None,
        "client_order_id": client_id or None,
        "symbol": str(order.get("symbol") or _symbol_for_row(match_row) or "").strip().upper() or None,
        "broker_status": status,
        "closed_quantity": filled_qty,
        "close_fraction": close_fraction,
    }


def _repair_local_paper_books(
    *,
    tenant: Tenant,
    profile_key: str,
    broker_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    if not bool(broker_snapshot.get("broker_available")):
        return []
    pending_rows = _records(_owned_rows(_safe_frame(sdm.read_pending_orders), tenant_id=tenant.id, profile_key=profile_key))
    pending_rows = [
        row
        for row in pending_rows
        if str(row.get("status") or "").strip().upper() not in {"TERMINAL", "CANCELED", "CANCELLED", "EXPIRED", "REJECTED"}
        and str(row.get("book_state") or "").strip().lower() not in {"terminal", "canceled", "cancelled", "expired", "rejected"}
    ]
    open_rows = _records(_owned_rows(_safe_frame(sdm.read_open_trades), tenant_id=tenant.id, profile_key=profile_key))
    pending_broker_lookup, pending_client_lookup = _local_order_lookup(pending_rows)
    open_broker_lookup, open_client_lookup = _local_order_lookup(open_rows)
    repair_actions: list[dict[str, Any]] = _restore_working_close_attempts(
        tenant=tenant,
        profile_key=profile_key,
        broker_snapshot=broker_snapshot,
    )

    for order in [dict(item) for item in list(broker_snapshot.get("orders") or []) if isinstance(item, dict)]:
        status = normalize_alpaca_status(order.get("status")) or str(order.get("status") or "").strip().lower()
        updates = _broker_order_repair_updates(order)
        close_match = next(
            (
                row
                for row in open_rows
                if _is_filled_status(status) and _open_row_matches_filled_close_order(row, order)
            ),
            None,
        )
        if close_match:
            close_action = _close_open_trade_from_filled_close_order(order)
            if close_action:
                repair_actions.append(close_action)
                open_rows = _records(_owned_rows(_safe_frame(sdm.read_open_trades), tenant_id=tenant.id, profile_key=profile_key))
                open_broker_lookup, open_client_lookup = _local_order_lookup(open_rows)
                continue
        pending_match = _match_local_order(order, by_broker_id=pending_broker_lookup, by_client_id=pending_client_lookup)
        open_match = _match_local_order(order, by_broker_id=open_broker_lookup, by_client_id=open_client_lookup)
        if pending_match:
            order_id = str(pending_match.get("order_id") or "").strip()
            if not order_id:
                continue
            if _is_filled_status(status):
                fill_price = _coerce_float(updates.get("broker_filled_avg_price"), 0.0)
                if fill_price <= 0:
                    fill_price = _coerce_float(pending_match.get("entry_price") or pending_match.get("entry_reference_price"), 0.0)
                filled = sdm.fill_pending_order(order_id, fill_price or _coerce_float(pending_match.get("entry_price"), 0.0))
                if filled:
                    sdm.update_open_trade(
                        {
                            **updates,
                            "order_status": "FILLED",
                            "route_state": "filled",
                            "book_state": "open",
                            "status": "OPEN",
                        },
                        trade_id=str(filled.get("trade_id") or pending_match.get("trade_id") or "").strip() or None,
                        order_id=order_id,
                    )
                    repair_actions.append(
                        {
                            "type": "promoted_pending_to_open",
                            "order_id": order_id,
                            "broker_order_id": updates.get("broker_order_id"),
                            "symbol": order.get("symbol"),
                            "broker_status": status,
                        }
                    )
                continue
            if _is_terminal_status(status):
                sdm.update_pending_order(
                    order_id,
                    {
                        **updates,
                        "order_status": str(status or "terminal").upper(),
                        "route_state": "terminal",
                        "book_state": "terminal",
                        "status": "TERMINAL",
                    },
                )
                repair_actions.append(
                    {
                        "type": "marked_pending_terminal",
                        "order_id": order_id,
                        "broker_order_id": updates.get("broker_order_id"),
                        "symbol": order.get("symbol"),
                        "broker_status": status,
                    }
                )
                continue
            if _is_working_status(status):
                sdm.update_pending_order(
                    order_id,
                    {
                        **updates,
                        "order_status": "WORKING",
                        "route_state": "accepted",
                        "book_state": "pending",
                        "status": "PENDING",
                    },
                )
                repair_actions.append(
                    {
                        "type": "refreshed_working_pending",
                        "order_id": order_id,
                        "broker_order_id": updates.get("broker_order_id"),
                        "symbol": order.get("symbol"),
                        "broker_status": status,
                    }
                )
                continue
        if open_match and _is_filled_status(status):
            sdm.update_open_trade(
                {
                    **updates,
                    "order_status": "FILLED",
                    "route_state": "filled",
                    "book_state": "open",
                    "status": "OPEN",
                },
                trade_id=str(open_match.get("trade_id") or "").strip() or None,
                order_id=str(open_match.get("order_id") or "").strip() or None,
            )
            repair_actions.append(
                {
                    "type": "refreshed_open_fill_evidence",
                    "order_id": open_match.get("order_id"),
                    "broker_order_id": updates.get("broker_order_id"),
                    "symbol": order.get("symbol"),
                    "broker_status": status,
                }
            )
    return serialize_value(repair_actions)


def _recent_order_events(
    db: Session | None,
    *,
    tenant: Tenant,
    profile_key: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    if db is None:
        return []
    rows = (
        db.execute(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant.id)
            .where(OrderEventRecord.created_at >= start_at)
            .where(OrderEventRecord.created_at < end_at)
            .order_by(OrderEventRecord.created_at.desc())
            .limit(500)
        )
        .scalars()
        .all()
    )
    normalized_profile = str(profile_key or PAPER_BROKER_PERSONAL_PAPER_PROFILE).strip().lower()
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload_json or {})
        payload_trade = dict(payload.get("trade") or {})
        row_profile = str(
            payload.get("automation_profile_key")
            or payload_trade.get("automation_profile_key")
            or ""
        ).strip().lower()
        if row_profile and row_profile != normalized_profile:
            continue
        if not (
            row_profile
            or str(payload.get("automation_cycle_id") or "").strip()
            or str(payload_trade.get("automation_origin") or "").strip().lower() == "trade_automation"
        ):
            continue
        events.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "ticker": row.ticker,
                "detail": row.detail,
                "created_at": _serialize_datetime(row.created_at),
                "payload": serialize_value(payload),
            }
        )
    return events


def _fetch_broker_snapshot(now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    try:
        adapter = get_execution_adapter_for("alpaca_paper")
        ensure = getattr(adapter, "_ensure_credentials", None)
        if callable(ensure):
            ensure()
        client = getattr(adapter, "client", None)
        if client is None:
            raise ValidationServiceError("Alpaca paper adapter does not expose a readable trading client.")
        after = _serialize_datetime(now - timedelta(days=PAPER_BROKER_ORDER_LOOKBACK_DAYS))
        orders = client.list_orders(status="all", limit=100, after=after, nested=False)
        return {
            "broker_available": True,
            "account": serialize_value(client.get_account()),
            "orders": serialize_value(orders),
            "positions": serialize_value(client.list_positions()),
        }, warnings
    except Exception as exc:
        warnings.append(
            {
                "key": "broker_snapshot_unavailable",
                "detail": str(exc) or "Broker-paper snapshot is unavailable.",
            }
        )
        return {
            "broker_available": False,
            "account": {},
            "orders": [],
            "positions": [],
        }, warnings


def _latest_equity_snapshot(tenant: Tenant, profile_key: str) -> dict[str, Any] | None:
    try:
        snapshot = equity_snapshot_service.get_latest_trade_automation_equity_snapshot(
            tenant_id=str(tenant.id),
            tenant_slug=getattr(tenant, "slug", None),
            profile_key=profile_key,
        )
    except Exception:
        return None
    return serialize_value(snapshot) if isinstance(snapshot, dict) else None


def _compare_reconciliation(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    broker_snapshot: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    pending_rows = _records(_owned_rows(_safe_frame(sdm.read_pending_orders), tenant_id=tenant.id, profile_key=profile_key))
    open_rows = _records(_owned_rows(_safe_frame(sdm.read_open_trades), tenant_id=tenant.id, profile_key=profile_key))
    closed_rows = _records(_owned_rows(_safe_frame(sdm.read_closed_trades), tenant_id=tenant.id, profile_key=profile_key))
    local_rows = [*pending_rows, *open_rows, *closed_rows]
    local_broker_lookup, local_client_lookup = _local_order_lookup(local_rows)
    settled_broker_lookup, settled_client_lookup = _local_order_lookup([*open_rows, *closed_rows])
    broker_orders = [dict(item) for item in list(broker_snapshot.get("orders") or []) if isinstance(item, dict)]
    broker_positions = [dict(item) for item in list(broker_snapshot.get("positions") or []) if isinstance(item, dict)]
    broker_account = dict(broker_snapshot.get("account") or {})
    broker_available = bool(broker_snapshot.get("broker_available"))
    latest_equity_snapshot = _latest_equity_snapshot(tenant, profile_key)
    broker_position_by_symbol = (
        {
            _symbol_for_position(position): _quantity_for_position(position)
            for position in broker_positions
            if _symbol_for_position(position)
        }
        if broker_available
        else {}
    )

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    matched_count = 0
    inferred_close_match_count = 0
    orphan_broker_orders: list[dict[str, Any]] = []
    fill_mismatches: list[dict[str, Any]] = []
    external_probe_orders: list[dict[str, Any]] = []
    external_probe_position_adjustments: Counter[str] = Counter()
    broker_order_ids: set[str] = set()
    broker_client_ids: set[str] = set()

    if broker_available:
        for order in broker_orders:
            broker_id, client_id = _order_identity(order)
            status = normalize_alpaca_status(order.get("status")) or str(order.get("status") or "").strip().lower()
            if _is_known_readiness_probe_order(order):
                symbol = str(order.get("symbol") or "").strip().upper()
                qty = _quantity_for_order(order)
                external_probe_orders.append(
                    {
                        "broker_order_id": broker_id,
                        "client_order_id": client_id,
                        "symbol": symbol,
                        "status": status,
                        "quantity": qty,
                        "detail": "Known Alpaca paper readiness probe; excluded from automation-route reconciliation.",
                    }
                )
                if _is_filled_status(status) and symbol:
                    external_probe_position_adjustments[symbol] += qty
                continue
            if broker_id:
                broker_order_ids.add(broker_id)
            if client_id:
                broker_client_ids.add(client_id)
            local_match = (
                (broker_id and local_broker_lookup.get(broker_id))
                or (client_id and local_client_lookup.get(client_id))
            )
            settled_match = (
                (broker_id and settled_broker_lookup.get(broker_id))
                or (client_id and settled_client_lookup.get(client_id))
            )
            if _is_filled_status(status):
                if settled_match:
                    matched_count += 1
                    continue
                inferred_close_match = next(
                    (
                        row
                        for row in closed_rows
                        if (
                            _closed_trade_matches_filled_order(row, order)
                            and float(broker_position_by_symbol.get(_symbol_for_row(row), 0.0)) == 0.0
                        )
                        or _closed_trade_absorbs_unlinked_flat_close_order(
                            row,
                            order,
                            broker_position_by_symbol=broker_position_by_symbol,
                        )
                    ),
                    None,
                )
                if inferred_close_match:
                    matched_count += 1
                    inferred_close_match_count += 1
                    continue
                inferred_partial_close_match = next(
                    (
                        row
                        for row in open_rows
                        if _open_trade_absorbs_unlinked_partial_close_order(
                            row,
                            order,
                            broker_position_by_symbol=broker_position_by_symbol,
                        )
                    ),
                    None,
                )
                if inferred_partial_close_match:
                    matched_count += 1
                    inferred_close_match_count += 1
                    continue
                if _filled_close_order_matches_recorded_event(order, events, [*open_rows, *closed_rows]):
                    matched_count += 1
                    inferred_close_match_count += 1
                    continue
                fill_mismatches.append(
                    {
                        "broker_order_id": broker_id,
                        "client_order_id": client_id,
                        "symbol": order.get("symbol"),
                        "status": status,
                        "detail": "Broker shows a filled paper order with no matching local open or closed trade.",
                    }
                )
                continue
            if local_match:
                matched_count += 1
                continue
            if _is_working_status(status):
                orphan_broker_orders.append(
                    {
                        "broker_order_id": broker_id,
                        "client_order_id": client_id,
                        "symbol": order.get("symbol"),
                        "status": status,
                        "detail": "Broker shows a working paper order with no matching local pending order.",
                    }
                )

    orphan_local_orders = (
        [
            {
                "order_id": row.get("order_id"),
                "broker_order_id": _broker_order_id(row),
                "client_order_id": _broker_client_order_id(row),
                "ticker": _symbol_for_row(row),
                "detail": "Local pending order is missing from the broker-paper order snapshot.",
            }
            for row in pending_rows
            if _broker_order_id(row)
            and _broker_order_id(row) not in broker_order_ids
            and _broker_client_order_id(row) not in broker_client_ids
        ]
        if broker_available
        else []
    )

    local_open_by_symbol: dict[str, float] = Counter()
    for row in open_rows:
        symbol = _symbol_for_row(row)
        if symbol:
            local_open_by_symbol[symbol] += _quantity_for_row(row)
    position_mismatches: list[dict[str, Any]] = []
    if broker_available:
        for symbol in sorted(set(local_open_by_symbol.keys()) | set(broker_position_by_symbol.keys())):
            local_qty = float(local_open_by_symbol.get(symbol, 0.0))
            broker_raw_qty = float(broker_position_by_symbol.get(symbol, 0.0))
            external_probe_qty = float(external_probe_position_adjustments.get(symbol, 0.0))
            raw_tolerance = max(0.001, min(local_qty, broker_raw_qty) * 0.001)
            if external_probe_qty > 0 and abs(local_qty - broker_raw_qty) <= raw_tolerance:
                broker_qty = broker_raw_qty
            else:
                broker_qty = max(broker_raw_qty - external_probe_qty, 0.0)
            tolerance = max(0.001, min(local_qty, broker_qty) * 0.001)
            if abs(local_qty - broker_qty) > tolerance:
                position_mismatches.append(
                    {
                        "symbol": symbol,
                        "local_qty": local_qty,
                        "broker_qty": broker_qty,
                        "broker_raw_qty": broker_raw_qty,
                        "external_probe_qty": external_probe_qty,
                        "detail": "Local open position quantity does not match the broker-paper position quantity.",
                    }
                )

    runtime = dict(state.get("runtime") or {})
    runtime_consistency = str(runtime.get("ledger_snapshot_consistency") or "").strip().lower()
    runtime_reconciliation = str(runtime.get("current_route_reconciliation_status") or "").strip().lower()
    fresh_reconciliation_has_blocker = any(
        (item for item in (orphan_broker_orders, orphan_local_orders, fill_mismatches, position_mismatches) if item)
    )
    if runtime_consistency == "inconsistent":
        target = blockers if fresh_reconciliation_has_blocker else warnings
        target.append(
            {
                "key": "previous_ledger_snapshot_inconsistent",
                "detail": (
                    "Previous ledger and snapshot accounting was inconsistent; this run will recompute "
                    "the runtime safety fields from the fresh broker/local comparison."
                ),
            }
        )
    if runtime_reconciliation in {"failed", "issues_present", "orphaned", "inconsistent"}:
        target = blockers if fresh_reconciliation_has_blocker else warnings
        target.append(
            {
                "key": "previous_current_route_reconciliation_fault",
                "detail": (
                    "Previous current-route reconciliation had unresolved issues; this run will recompute "
                    "the runtime safety fields from the fresh broker/local comparison."
                ),
            }
        )

    if orphan_broker_orders:
        blockers.append({"key": "orphan_broker_order", "detail": f"{len(orphan_broker_orders)} broker-paper order(s) are missing locally."})
    if orphan_local_orders:
        blockers.append({"key": "orphan_local_order", "detail": f"{len(orphan_local_orders)} local pending order(s) are missing at the broker."})
    if fill_mismatches:
        blockers.append({"key": "fill_mismatch", "detail": f"{len(fill_mismatches)} filled broker-paper order(s) are missing local trade rows."})
    if position_mismatches:
        blockers.append({"key": "position_mismatch", "detail": f"{len(position_mismatches)} broker/local position mismatch(es) were found."})

    failed_event_count = sum(1 for event in events if str(event.get("status") or "").strip().lower() in {"failed", "error"})
    rejected_event_count = sum(1 for event in events if str(event.get("status") or "").strip().lower() == "rejected")
    if failed_event_count:
        blockers.append({"key": "order_event_failed", "detail": f"{failed_event_count} failed order event(s) were recorded."})
    if rejected_event_count:
        warnings.append({"key": "order_event_rejected", "detail": f"{rejected_event_count} rejected order event(s) were recorded."})
    if external_probe_orders:
        warnings.append(
            {
                "key": "external_readiness_probe_orders",
                "detail": f"{len(external_probe_orders)} known paper readiness probe order(s) were excluded from automation-route reconciliation.",
            }
        )
    if not broker_available:
        warnings.append({"key": "broker_snapshot_unavailable", "detail": "Broker-paper account, orders, and positions could not be read."})

    broker_equity = _coerce_float(
        broker_account.get("equity") or broker_account.get("portfolio_value") or broker_account.get("last_equity"),
        0.0,
    )
    snapshot_equity = _coerce_float((latest_equity_snapshot or {}).get("equity"), 0.0)
    equity_delta = abs(broker_equity - snapshot_equity) if broker_available and broker_equity > 0 and snapshot_equity > 0 else None
    equity_tolerance = max(10.0, broker_equity * 0.05) if broker_equity > 0 else 10.0
    equity_status = "missing"
    if latest_equity_snapshot and not broker_available:
        equity_status = "broker_unavailable"
    elif latest_equity_snapshot and equity_delta is not None and equity_delta <= equity_tolerance:
        equity_status = "matched"
    elif latest_equity_snapshot and equity_delta is not None:
        equity_status = "drift"
        warnings.append(
            {
                "key": "equity_snapshot_drift",
                "detail": (
                    f"Latest local equity snapshot differs from broker-paper equity by {equity_delta:.2f}, "
                    f"above the {equity_tolerance:.2f} advisory tolerance."
                ),
            }
        )
    elif latest_equity_snapshot:
        equity_status = "available"

    ledger_consistency = "consistent" if not blockers else "inconsistent"
    current_route_reconciliation_status = "clean" if broker_available and not blockers else "orphaned" if blockers else "waiting"
    current_route_orphan_order_event_count = (
        len(orphan_broker_orders) + len(orphan_local_orders) + len(fill_mismatches) + len(position_mismatches)
        if blockers
        else 0
    )
    return {
        "matched_count": matched_count,
        "orphan_broker_order_count": len(orphan_broker_orders),
        "orphan_local_order_count": len(orphan_local_orders),
        "position_mismatch_count": len(position_mismatches),
        "fill_mismatch_count": len(fill_mismatches),
        "inferred_close_match_count": inferred_close_match_count,
        "ledger_consistency": ledger_consistency,
        "current_route_reconciliation_status": current_route_reconciliation_status,
        "current_route_orphan_order_event_count": int(current_route_orphan_order_event_count),
        "blockers": blockers,
        "warnings": warnings,
        "orphan_broker_orders": orphan_broker_orders[:10],
        "orphan_local_orders": orphan_local_orders[:10],
        "position_mismatches": position_mismatches[:10],
        "fill_mismatches": fill_mismatches[:10],
        "external_probe_orders": external_probe_orders[:10],
        "local_counts": {
            "pending": len(pending_rows),
            "open": len(open_rows),
            "closed": len(closed_rows),
            "order_events": len(events),
        },
        "broker_counts": {
            "orders": len(broker_orders),
            "positions": len(broker_positions),
        },
        "equity_snapshot": {
            "status": equity_status,
            "available": bool(latest_equity_snapshot),
            "snapshot_at": (latest_equity_snapshot or {}).get("snapshot_at"),
            "cycle_at": (latest_equity_snapshot or {}).get("cycle_at"),
            "local_equity": snapshot_equity if latest_equity_snapshot else None,
            "broker_equity": broker_equity if broker_available and broker_equity > 0 else None,
            "delta": equity_delta,
            "tolerance": equity_tolerance if broker_equity > 0 else None,
        },
    }


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=PAPER_BROKER_NOTE_OWNER,
            limit=PAPER_BROKER_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "paper-broker",
        "reconciliation",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    equity_snapshot = dict(report.get("equity_snapshot") or {})
    lines = [
        f"Automation paper broker reconciliation for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Checked at: {report.get('checked_at')}",
        f"Matched orders: {report.get('matched_count', 0)}",
        f"Broker orphan orders: {report.get('orphan_broker_order_count', 0)}",
        f"Local orphan orders: {report.get('orphan_local_order_count', 0)}",
        f"Position mismatches: {report.get('position_mismatch_count', 0)}",
        f"Fill mismatches: {report.get('fill_mismatch_count', 0)}",
        f"Inferred close-side matches: {report.get('inferred_close_match_count', 0)}",
        f"Ledger consistency: {str(report.get('ledger_consistency') or 'unknown').replace('_', ' ')}",
        f"Equity snapshot: {str(equity_snapshot.get('status') or 'missing').replace('_', ' ')}",
        "",
        "This reconciliation is advisory and read-mostly. It does not place orders, cancel orders, tune baseline settings, enable live trading, arm automation, or clear locks.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    for heading, key in (
        ("Broker orphan orders", "orphan_broker_orders"),
        ("Local orphan orders", "orphan_local_orders"),
        ("Position mismatches", "position_mismatches"),
        ("Fill mismatches", "fill_mismatches"),
    ):
        items = [item for item in list(report.get(key) or []) if isinstance(item, dict)]
        if not items:
            continue
        lines.extend(["", heading])
        for item in items[:8]:
            identifier = item.get("broker_order_id") or item.get("order_id") or item.get("symbol") or item.get("ticker") or "--"
            lines.append(f"- {identifier}: {item.get('detail')}")
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = ["automation-ai", "paper-broker", "reconciliation", _profile_tag(profile_key), f"session-{session_day}"]
    title = f"Automation paper broker reconciliation - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": PAPER_BROKER_NOTE_OWNER,
        "note_type": "risk_review",
        "priority": "high" if report.get("blockers") else "medium",
    }
    if note_id:
        try:
            updated = notes_service.update_note(note_id, payload)
            return str(updated.get("id") or note_id)
        except Exception:
            note_id = None
    try:
        created = notes_service.create_note(**payload)
        return str(created.get("id") or "").strip() or None
    except Exception:
        return None


def run_paper_broker_reconciliation(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
    broker_snapshot: dict[str, Any] | None = None,
    repair_local_book: bool = False,
) -> dict[str, Any]:
    now = now or _utc_now()
    session_day = _session_day_for(now)
    start_at, end_at = _session_bounds_for_day(session_day)
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    fetched_warnings: list[dict[str, Any]] = []
    if broker_snapshot is None:
        broker_snapshot, fetched_warnings = _fetch_broker_snapshot(now)
    else:
        broker_snapshot = {
            "broker_available": bool(broker_snapshot.get("broker_available", True)),
            "account": serialize_value(broker_snapshot.get("account") or {}),
            "orders": serialize_value(list(broker_snapshot.get("orders") or [])),
            "positions": serialize_value(list(broker_snapshot.get("positions") or [])),
        }
    repair_actions: list[dict[str, Any]] = []
    if repair_local_book:
        try:
            repair_actions = _repair_local_paper_books(
                tenant=tenant,
                profile_key=profile_key,
                broker_snapshot=broker_snapshot,
            )
        except Exception as exc:
            fetched_warnings.append(
                {
                    "key": "paper_book_repair_failed",
                    "detail": str(exc) or "Local paper-order repair failed before reconciliation comparison.",
                }
            )
    events = _recent_order_events(db, tenant=tenant, profile_key=profile_key, start_at=start_at, end_at=end_at)
    compared = _compare_reconciliation(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        broker_snapshot=broker_snapshot,
        events=events,
    )
    warnings: list[dict[str, Any]] = []
    seen_warnings: set[tuple[str, str]] = set()
    for item in [*fetched_warnings, *list(compared.get("warnings") or [])]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        detail = str(item.get("detail") or "").strip()
        signature = (key, detail)
        if signature in seen_warnings:
            continue
        seen_warnings.add(signature)
        warnings.append(item)
    blockers = list(compared.get("blockers") or [])
    if profile_key != PAPER_BROKER_PERSONAL_PAPER_PROFILE:
        warnings.insert(
            0,
            {
                "key": "paper_profile_required",
                "detail": "V1 paper broker reconciliation targets the personal paper automation profile.",
            },
        )
    status = "blocked" if blockers else "warning" if warnings else "clean"
    report = serialize_value(
        {
            "status": status,
            "label": {
                "clean": "Paper broker clean",
                "warning": "Paper broker warning",
                "blocked": "Paper broker blocked",
            }.get(status, "Paper broker reconciliation"),
            "profile_key": profile_key,
            "linked_account_id": getattr(linked_account, "id", None),
            "session_day": session_day,
            "checked_at": _serialize_datetime(now),
            "run_source": normalized_run_source,
            "broker_available": bool(broker_snapshot.get("broker_available")),
            "broker_account": broker_snapshot.get("account") or {},
            "repair_local_book": bool(repair_local_book),
            "repair_actions": repair_actions[:20],
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            **{key: value for key, value in compared.items() if key not in {"blockers", "warnings"}},
        }
    )
    note_id = _sync_note(tenant=tenant, profile_key=profile_key, report=report)
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id

    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "linked_account_id",
        "session_day",
        "checked_at",
        "run_source",
        "broker_available",
        "matched_count",
        "orphan_broker_order_count",
        "orphan_local_order_count",
        "position_mismatch_count",
        "fill_mismatch_count",
        "inferred_close_match_count",
        "ledger_consistency",
        "blockers",
        "warnings",
        "local_counts",
        "broker_counts",
        "equity_snapshot",
        "current_route_reconciliation_status",
        "current_route_orphan_order_event_count",
        "repair_local_book",
        "repair_actions",
        "note_id",
        "related_note_id",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["paper_broker_reconciliation_last_report"] = serialize_value(summary)
    runtime["paper_broker_reconciliation_last_note_id"] = note_id
    runtime["paper_broker_reconciliation_note_session_day"] = session_day
    runtime["paper_broker_reconciliation_last_run_at"] = report.get("checked_at")
    runtime["paper_broker_reconciliation_last_error"] = None
    runtime["current_route_reconciliation_status"] = report.get("current_route_reconciliation_status")
    runtime["current_route_orphan_order_event_count"] = int(report.get("current_route_orphan_order_event_count") or 0)
    runtime["ledger_snapshot_consistency"] = report.get("ledger_consistency") or "unknown"
    runtime["current_route_latest_event_at"] = report.get("checked_at")
    if normalized_run_source == "scheduled":
        runtime["paper_broker_reconciliation_last_scheduled_run_at"] = report.get("checked_at")
        runtime["paper_broker_reconciliation_last_scheduled_session_day"] = session_day
    history = list(runtime.get("paper_broker_reconciliation_history") or [])
    history.insert(
        0,
        {
            "at": report.get("checked_at"),
            "session_day": session_day,
            "status": report.get("status"),
            "matched_count": report.get("matched_count"),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["paper_broker_reconciliation_history"] = serialize_value(history[:PAPER_BROKER_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.paper_broker_reconciled",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
                "status": report.get("status"),
                "matched_count": report.get("matched_count"),
                "orphan_broker_order_count": report.get("orphan_broker_order_count"),
                "orphan_local_order_count": report.get("orphan_local_order_count"),
                "position_mismatch_count": report.get("position_mismatch_count"),
                "fill_mismatch_count": report.get("fill_mismatch_count"),
                "current_route_reconciliation_status": report.get("current_route_reconciliation_status"),
                "current_route_orphan_order_event_count": report.get("current_route_orphan_order_event_count"),
                "repair_local_book": bool(repair_local_book),
                "repair_action_count": len(repair_actions),
                "note_id": note_id,
                "run_source": normalized_run_source,
            },
        )
    return serialize_value(report)
