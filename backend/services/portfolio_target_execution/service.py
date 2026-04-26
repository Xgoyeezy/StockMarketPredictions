from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.saas import (
    PortfolioTargetExecutionItem,
    PortfolioTargetExecutionRun,
    PortfolioTargetRun,
    StrategyDesk,
)
from backend.schemas import AnalyzeRequest, CloseTradeRequest, OpenTradeRequest, PortfolioTargetExecutionRequest
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.market_service import analyze_market
from backend.services.permissions import require_current_user_permission
from backend.services.strategy_engine.events import record_domain_event
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user
from backend.services.trade_service import (
    _scoped_closed_trades,
    _scoped_open_trades,
    _scoped_pending_orders,
    close_trade_from_request,
    get_order_events_snapshot,
    open_trade_from_request,
    sync_pending_orders_from_broker,
)

EXECUTABLE_DESK_KEYS = {"macro_trend", "stat_arb"}
DELTA_QUANTITY_TOLERANCE = 0.001
TERMINAL_RECONCILIATION_STATUSES = {"filled", "canceled", "rejected", "expired", "orphan", "blocked", "skipped", "dry_run"}
BLOCKING_EXECUTION_STATUSES = {"blocked", "rejected", "canceled", "expired", "completed_with_errors", "reconciliation_warning"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _coerce_float(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if pd.notna(numeric) else 0.0


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _normalize_side(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"sell", "sell_short", "short"}:
        return "sell"
    return "buy"


def _signed_quantity(quantity: float, side: str) -> float:
    return -abs(float(quantity)) if _normalize_side(side) == "sell" else abs(float(quantity))


def _quantity_from_row(row: dict[str, Any]) -> float:
    return abs(_coerce_float(row.get("suggested_contracts")))


def _signed_quantity_from_row(row: dict[str, Any]) -> float:
    return _signed_quantity(_quantity_from_row(row), row.get("broker_side"))


def _desk_key_for_item(desk_contributions: list[dict[str, Any]]) -> str | None:
    keys = sorted(
        {
            str(item.get("desk_key") or "").strip().lower()
            for item in list(desk_contributions or [])
            if str(item.get("desk_key") or "").strip()
        }
    )
    if not keys:
        return None
    if len(keys) == 1:
        return keys[0]
    return "multi_desk"


def _is_executable_desk(desk: StrategyDesk | None) -> bool:
    if desk is None:
        return False
    return (
        desk.desk_key in EXECUTABLE_DESK_KEYS
        and bool(desk.enabled)
        and bool(desk.paper_trading_enabled)
        and str(desk.trading_mode or "").strip().lower() == "paper"
    )


def _serialize_execution_item(item: PortfolioTargetExecutionItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "symbol": item.symbol,
        "action": item.action,
        "status": item.status,
        "broker_order_id": item.broker_order_id,
        "broker_status": item.broker_status,
        "filled_quantity": item.filled_quantity,
        "remaining_quantity": item.remaining_quantity,
        "average_fill_price": item.average_fill_price,
        "reconciliation_status": item.reconciliation_status,
        "requested_target_weight": item.requested_target_weight,
        "requested_target_notional": item.requested_target_notional,
        "requested_delta_quantity": item.requested_delta_quantity,
        "resolved_route": item.resolved_route,
        "strategy_desk_key": item.strategy_desk_key,
        "submitted_trade_id": item.submitted_trade_id,
        "submitted_order_id": item.submitted_order_id,
        "reason": item.reason,
        "source_metadata": dict(item.source_metadata_json or {}),
        "result": dict(item.result_json or {}),
        "terminal_at": _iso(item.terminal_at),
        "last_seen_at": _iso(item.last_seen_at),
        "created_at": _iso(item.created_at),
        "updated_at": _iso(item.updated_at),
    }


def _serialize_execution_run(run: PortfolioTargetExecutionRun | None) -> dict[str, Any]:
    if run is None:
        validation_artifact = _build_lifecycle_validation_artifact(
            status="idle",
            rows=[],
            blocked_reason=None,
            orphan_event_count=0,
        )
        return {
            "latest_execution_run_id": None,
            "portfolio_target_run_id": None,
            "status": "idle",
            "execution_intent": "broker_paper",
            "dry_run": False,
            "working_count": 0,
            "partial_fill_count": 0,
            "filled_count": 0,
            "canceled_count": 0,
            "rejected_count": 0,
            "orphan_event_count": 0,
            "last_sync_at": None,
            "validation_artifact": validation_artifact,
            "summary": {},
            "items": [],
            "created_at": None,
            "completed_at": None,
        }
    items = sorted(list(run.execution_items or []), key=lambda row: row.created_at or _utc_now())
    summary = dict(run.summary_json or {})
    validation_artifact = dict(summary.get("validation_artifact") or {})
    if not validation_artifact:
        validation_artifact = _build_lifecycle_validation_artifact(
            status=str(run.status or "idle"),
            rows=items,
            blocked_reason=str(summary.get("blocked_reason") or "").strip() or None,
            orphan_event_count=int(run.orphan_event_count or 0),
            orphan_events=list(summary.get("orphan_events") or []),
        )
    return {
        "latest_execution_run_id": run.id,
        "portfolio_target_run_id": run.portfolio_target_run_id,
        "status": run.status,
        "execution_intent": run.execution_intent,
        "dry_run": bool(run.dry_run),
        "working_count": int(run.working_count or 0),
        "partial_fill_count": int(run.partial_fill_count or 0),
        "filled_count": int(run.filled_count or 0),
        "canceled_count": int(run.canceled_count or 0),
        "rejected_count": int(run.rejected_count or 0),
        "orphan_event_count": int(run.orphan_event_count or 0),
        "last_sync_at": _iso(run.last_sync_at),
        "validation_artifact": validation_artifact,
        "summary": summary,
        "metadata": dict(run.metadata_json or {}),
        "items": [_serialize_execution_item(item) for item in items],
        "created_at": _iso(run.created_at),
        "completed_at": _iso(run.completed_at),
    }


def _normalize_identifier(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _extract_identifier_sets(value: Any) -> dict[str, set[str]]:
    identifiers = {
        "trade_ids": set(),
        "order_ids": set(),
        "broker_order_ids": set(),
        "route_correlation_ids": set(),
    }

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                normalized_key = str(key or "").strip().lower()
                normalized_value = _normalize_identifier(child)
                if normalized_key == "trade_id" and normalized_value:
                    identifiers["trade_ids"].add(normalized_value)
                elif normalized_key == "order_id" and normalized_value:
                    identifiers["order_ids"].add(normalized_value)
                elif normalized_key == "broker_order_id" and normalized_value:
                    identifiers["broker_order_ids"].add(normalized_value)
                elif normalized_key == "route_correlation_id" and normalized_value:
                    identifiers["route_correlation_ids"].add(normalized_value)
                _walk(child)
        elif isinstance(node, list):
            for child in node:
                _walk(child)

    _walk(value)
    return identifiers


def _collect_item_identifiers(item: PortfolioTargetExecutionItem) -> dict[str, set[str]]:
    identifiers = _extract_identifier_sets({"source_metadata": item.source_metadata_json or {}, "result": item.result_json or {}})
    for key, value in (
        ("trade_ids", item.submitted_trade_id),
        ("order_ids", item.submitted_order_id),
        ("broker_order_ids", item.broker_order_id),
    ):
        normalized = _normalize_identifier(value)
        if normalized:
            identifiers[key].add(normalized)
    return identifiers


def _extract_row_identifiers(row: Any) -> dict[str, set[str]]:
    if hasattr(row, "_mapping"):
        payload = dict(row._mapping)
    elif isinstance(row, dict):
        payload = dict(row)
    else:
        payload = {}
    return _extract_identifier_sets(payload)


def _matches_identifiers(row: Any, identifiers: dict[str, set[str]]) -> bool:
    row_ids = _extract_row_identifiers(row)
    return any(row_ids[key] & identifiers.get(key, set()) for key in row_ids)


def _filter_matching_rows(rows: list[dict[str, Any]], identifiers: dict[str, set[str]]) -> list[dict[str, Any]]:
    return [row for row in rows if _matches_identifiers(row, identifiers)]


def _build_run_status_from_items(
    *,
    items: list[PortfolioTargetExecutionItem],
    orphan_event_count: int,
) -> str:
    lifecycle_counts = Counter(
        str(item.reconciliation_status or item.status or "queued").strip().lower() or "queued"
        for item in items
    )
    if lifecycle_counts.get("working"):
        return "working"
    if lifecycle_counts.get("partially_filled"):
        return "partially_filled"
    if orphan_event_count:
        return "reconciliation_warning"
    if lifecycle_counts.get("rejected") and lifecycle_counts.get("filled"):
        return "completed_with_errors"
    if lifecycle_counts.get("rejected"):
        return "rejected"
    if lifecycle_counts.get("canceled") or lifecycle_counts.get("expired"):
        return "canceled"
    if lifecycle_counts.get("filled"):
        return "filled"
    if lifecycle_counts.get("blocked"):
        return "blocked"
    if lifecycle_counts.get("dry_run"):
        return "dry_run"
    if lifecycle_counts.get("skipped") and len(lifecycle_counts) == 1:
        return "completed"
    return "completed"


def _assert_strategy_executor(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "trade.execute",
        "Only traders, admins, or owners can execute portfolio targets.",
    )


def _load_latest_target_runs(db: Session, *, tenant_id: str) -> tuple[PortfolioTargetRun | None, PortfolioTargetRun | None]:
    latest_overall = db.execute(
        select(PortfolioTargetRun)
        .where(PortfolioTargetRun.tenant_id == tenant_id)
        .order_by(PortfolioTargetRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    latest_accepted = db.execute(
        select(PortfolioTargetRun)
        .where(PortfolioTargetRun.tenant_id == tenant_id, PortfolioTargetRun.status == "accepted")
        .order_by(PortfolioTargetRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return latest_overall, latest_accepted


def _build_lifecycle_validation_artifact(
    *,
    status: str,
    rows: list[PortfolioTargetExecutionItem],
    blocked_reason: str | None,
    orphan_event_count: int,
    orphan_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    status_counts: Counter[str] = Counter(str(row.status or "queued").strip().lower() or "queued" for row in rows)
    reconciliation_counts: Counter[str] = Counter(
        str(row.reconciliation_status or row.status or "queued").strip().lower() or "queued"
        for row in rows
    )
    executed_rows = [
        row
        for row in rows
        if row.action in {"open", "add", "reduce", "close"}
        and str(row.status or "").strip().lower() not in {"skipped", "blocked", "dry_run"}
    ]
    broker_linked_item_count = sum(
        1
        for row in executed_rows
        if str(row.submitted_order_id or row.submitted_trade_id or row.broker_order_id or "").strip()
    )
    submitted_count = status_counts.get("submitted", 0)
    active_submitted_count = sum(
        1
        for row in rows
        if str(row.status or "").strip().lower() == "submitted"
        and str(row.reconciliation_status or row.status or "").strip().lower() not in TERMINAL_RECONCILIATION_STATUSES
    )
    working_count = reconciliation_counts.get("working", 0)
    partial_fill_count = reconciliation_counts.get("partially_filled", 0)
    filled_count = reconciliation_counts.get("filled", 0)
    canceled_count = reconciliation_counts.get("canceled", 0) + reconciliation_counts.get("expired", 0)
    rejected_count = reconciliation_counts.get("rejected", 0)
    blocked_count = sum(
        1
        for row in rows
        if str(row.status or "").strip().lower() == "blocked"
        or str(row.reconciliation_status or "").strip().lower() == "blocked"
    )
    skipped_count = status_counts.get("skipped", 0)
    blockers: list[str] = []
    normalized_status = str(status or "idle").strip().lower() or "idle"
    if blocked_reason:
        blockers.append(blocked_reason)
    if int(orphan_event_count or 0) > 0:
        blockers.append("Unmatched broker/local order events need operator review before lifecycle validation is clean.")
    if rejected_count:
        blockers.append("One or more paper basket orders were rejected.")
    if canceled_count:
        blockers.append("One or more paper basket orders were canceled or expired.")
    if blocked_count:
        blockers.append("One or more basket items were blocked before submission.")
    if normalized_status in BLOCKING_EXECUTION_STATUSES and not blockers:
        blockers.append(f"Execution run status is {normalized_status}.")

    if blockers:
        readiness_state = "blocked"
        readiness_label = "blocked"
        next_step = "Review the blocker, sync again if needed, then rerun a fresh paper basket only after the route is clean."
    elif active_submitted_count or working_count or partial_fill_count or normalized_status in {"running", "working", "partially_filled"}:
        readiness_state = "collecting_lifecycle_evidence"
        readiness_label = "collecting lifecycle evidence"
        next_step = "Keep the basket unchanged and refresh execution until the broker-paper lifecycle reaches a terminal clean state."
    elif filled_count > 0 and broker_linked_item_count >= len(executed_rows):
        readiness_state = "ready"
        readiness_label = "multi-desk paper execution ready"
        next_step = "Keep collecting unchanged; scheduling remains blocked until additional clean lifecycle samples are gathered."
    else:
        readiness_state = "collecting_lifecycle_evidence"
        readiness_label = "collecting lifecycle evidence"
        next_step = "Run macro/stat-arb desks, execute a personal-paper basket, then refresh execution to collect lifecycle evidence."

    return {
        "validation_scope": "personal_paper",
        "readiness_state": readiness_state,
        "readiness_label": readiness_label,
        "status": normalized_status,
        "item_count": len(rows),
        "submitted_count": submitted_count,
        "active_submitted_count": active_submitted_count,
        "working_count": working_count,
        "partial_fill_count": partial_fill_count,
        "filled_count": filled_count,
        "canceled_count": canceled_count,
        "rejected_count": rejected_count,
        "skipped_count": skipped_count,
        "blocked_count": blocked_count,
        "orphan_event_count": int(orphan_event_count or 0),
        "broker_linked_item_count": broker_linked_item_count,
        "clean_run": readiness_state == "ready",
        "blockers": blockers,
        "next_step": next_step,
        "orphan_events": list(orphan_events or []),
    }


def _build_run_summary(
    *,
    status: str,
    blocked_reason: str | None = None,
    items: list[PortfolioTargetExecutionItem] | None = None,
    orphan_event_count: int = 0,
    orphan_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    rows = list(items or [])
    action_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    reconciliation_counts: dict[str, int] = {}
    for row in rows:
        action_counts[row.action] = action_counts.get(row.action, 0) + 1
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        reconciliation_key = str(row.reconciliation_status or row.status or "queued").strip().lower() or "queued"
        reconciliation_counts[reconciliation_key] = reconciliation_counts.get(reconciliation_key, 0) + 1
    executed_count = sum(
        1
        for row in rows
        if row.action in {"open", "add", "reduce", "close"}
        and str(row.status or "").strip().lower() not in {"skipped", "blocked", "dry_run"}
    )
    skipped_count = sum(1 for row in rows if row.action == "skip" or row.status == "skipped")
    blocked_count = sum(1 for row in rows if row.action == "blocked" or row.status == "blocked")
    validation_artifact = _build_lifecycle_validation_artifact(
        status=status,
        rows=rows,
        blocked_reason=blocked_reason,
        orphan_event_count=orphan_event_count,
        orphan_events=orphan_events,
    )
    return {
        "status": status,
        "blocked_reason": blocked_reason,
        "item_count": len(rows),
        "executed_count": executed_count,
        "skipped_count": skipped_count,
        "blocked_count": blocked_count,
        "action_counts": action_counts,
        "status_counts": status_counts,
        "reconciliation_counts": reconciliation_counts,
        "working_count": reconciliation_counts.get("working", 0),
        "partial_fill_count": reconciliation_counts.get("partially_filled", 0),
        "filled_count": reconciliation_counts.get("filled", 0),
        "canceled_count": reconciliation_counts.get("canceled", 0) + reconciliation_counts.get("expired", 0),
        "rejected_count": reconciliation_counts.get("rejected", 0),
        "orphan_event_count": int(orphan_event_count),
        "orphan_events": list(orphan_events or []),
        "validation_artifact": validation_artifact,
    }


def _apply_run_rollups(
    run: PortfolioTargetExecutionRun,
    *,
    status: str,
    items: list[PortfolioTargetExecutionItem],
    orphan_event_count: int = 0,
    orphan_events: list[dict[str, Any]] | None = None,
    last_sync_at: datetime | None = None,
) -> None:
    summary = _build_run_summary(
        status=status,
        blocked_reason=str((run.summary_json or {}).get("blocked_reason") or "").strip() or None,
        items=items,
        orphan_event_count=orphan_event_count,
        orphan_events=orphan_events,
    )
    run.status = status
    run.working_count = int(summary["working_count"])
    run.partial_fill_count = int(summary["partial_fill_count"])
    run.filled_count = int(summary["filled_count"])
    run.canceled_count = int(summary["canceled_count"])
    run.rejected_count = int(summary["rejected_count"])
    run.orphan_event_count = int(orphan_event_count)
    run.last_sync_at = last_sync_at
    run.summary_json = summary


def _create_execution_run(
    db: Session,
    *,
    tenant_id: str,
    portfolio_target_run: PortfolioTargetRun | None,
    status: str,
    execution_intent: str,
    dry_run: bool,
    summary: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> PortfolioTargetExecutionRun:
    now = _utc_now()
    row = PortfolioTargetExecutionRun(
        tenant_id=tenant_id,
        portfolio_target_run_id=portfolio_target_run.id if portfolio_target_run is not None else None,
        status=status,
        execution_intent=execution_intent,
        dry_run=dry_run,
        summary_json=dict(summary or {}),
        metadata_json=dict(metadata or {}),
        started_at=now,
        completed_at=now if status in {"blocked", "completed", "completed_with_errors", "dry_run"} else None,
    )
    db.add(row)
    db.flush()
    return row


def _append_execution_item(
    db: Session,
    *,
    execution_run: PortfolioTargetExecutionRun,
    symbol: str,
    action: str,
    status: str,
    requested_target_weight: float,
    requested_target_notional: float,
    requested_delta_quantity: float,
    resolved_route: str,
    strategy_desk_key: str | None,
    reason: str | None,
    source_metadata: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> PortfolioTargetExecutionItem:
    row = PortfolioTargetExecutionItem(
        execution_run_id=execution_run.id,
        symbol=symbol,
        action=action,
        status=status,
        requested_target_weight=float(requested_target_weight),
        requested_target_notional=float(requested_target_notional),
        requested_delta_quantity=float(requested_delta_quantity),
        resolved_route=resolved_route,
        strategy_desk_key=strategy_desk_key,
        filled_quantity=0.0,
        remaining_quantity=abs(float(requested_delta_quantity)),
        reconciliation_status=str(status or "queued").strip().lower() or "queued",
        reason=reason,
        source_metadata_json=dict(source_metadata or {}),
        result_json=dict(result or {}),
    )
    db.add(row)
    db.flush()
    return row


def _resolve_initial_reconciliation_status(
    *,
    item_status: str,
    broker_status: str | None,
    has_pending_row: bool,
) -> str:
    normalized_item_status = str(item_status or "queued").strip().lower() or "queued"
    normalized_broker_status = str(broker_status or "").strip().lower()
    if normalized_item_status in {"blocked", "skipped", "dry_run"}:
        return normalized_item_status
    if normalized_broker_status in {"rejected"}:
        return "rejected"
    if normalized_broker_status in {"canceled", "cancelled", "expired"}:
        return "canceled"
    if normalized_broker_status in {"filled"} and not has_pending_row:
        return "filled"
    if normalized_broker_status in {"partially_filled"}:
        return "partially_filled"
    if normalized_item_status == "submitted":
        return "working" if has_pending_row else "filled"
    return normalized_item_status


def _apply_submission_lifecycle(
    item: PortfolioTargetExecutionItem,
    *,
    item_status: str,
    requested_quantity: float,
    submitted_row: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> None:
    submitted_row = dict(submitted_row or {})
    result = dict(result or {})
    execution_payload = _coerce_dict(result.get("execution"))
    broker_response = _coerce_dict(execution_payload.get("broker_response"))
    broker_order_id = (
        _normalize_identifier(execution_payload.get("broker_order_id"))
        or _normalize_identifier(submitted_row.get("broker_order_id"))
        or _normalize_identifier(broker_response.get("id"))
    )
    broker_status = (
        _normalize_identifier(execution_payload.get("broker_status"))
        or _normalize_identifier(submitted_row.get("broker_status"))
        or _normalize_identifier(broker_response.get("status"))
    )
    filled_quantity = (
        _coerce_float(submitted_row.get("broker_filled_qty"))
        or _coerce_float(broker_response.get("filled_qty"))
        or (abs(float(requested_quantity)) if _resolve_initial_reconciliation_status(item_status=item_status, broker_status=broker_status, has_pending_row=bool(result.get("pending_order"))) == "filled" else 0.0)
    )
    item.broker_order_id = broker_order_id
    item.broker_status = str(broker_status or "").strip().lower() or None
    item.filled_quantity = float(filled_quantity)
    item.remaining_quantity = max(0.0, abs(float(requested_quantity)) - float(filled_quantity))
    item.average_fill_price = (
        _coerce_float(submitted_row.get("actual_fill_price"))
        or _coerce_float(submitted_row.get("broker_filled_avg_price"))
        or _coerce_float(broker_response.get("filled_avg_price"))
        or None
    )
    item.reconciliation_status = _resolve_initial_reconciliation_status(
        item_status=item_status,
        broker_status=item.broker_status,
        has_pending_row=bool(result.get("pending_order")),
    )
    item.last_seen_at = _utc_now()
    if item.reconciliation_status in {"filled", "canceled", "rejected", "expired"}:
        item.terminal_at = item.last_seen_at


def _filter_symbol_rows(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    normalized_symbol = _normalize_symbol(symbol)
    mask = frame.get("ticker", pd.Series("", index=frame.index)).astype(str).str.upper() == normalized_symbol
    return frame.loc[mask].copy()


def _sum_signed_quantity(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 0.0
    total = 0.0
    for _, row in frame.iterrows():
        total += _signed_quantity_from_row(row.to_dict())
    return float(total)


def _find_trade_index_for_trade_id(current_user: Any, *, trade_id: str) -> int | None:
    open_trades = _scoped_open_trades(current_user)
    if open_trades.empty:
        return None
    normalized_trade_id = str(trade_id or "").strip()
    if not normalized_trade_id:
        return None
    for position in range(len(open_trades)):
        row = open_trades.iloc[position].to_dict()
        if str(row.get("trade_id") or "").strip() == normalized_trade_id:
            return position
    return None


def _resolve_symbol_context(
    *,
    target: dict[str, Any],
    desk_lookup: dict[str, StrategyDesk],
    current_user: Any,
) -> tuple[dict[str, Any], str | None, str | None]:
    contributions = list(target.get("desk_contributions") or [])
    eligible = []
    ineligible = []
    for item in contributions:
        desk_key = str(item.get("desk_key") or "").strip().lower()
        if _is_executable_desk(desk_lookup.get(desk_key)):
            eligible.append(item)
        else:
            ineligible.append(item)
    if not eligible:
        return {"reason": "Research desks are not executable in the current portfolio-target route."}, None, None
    if ineligible:
        return {"reason": "Mixed executable and research desk contributions must be promoted before execution."}, None, None

    primary_desk_key = str(eligible[0].get("desk_key") or "").strip().lower()
    desk_row = desk_lookup.get(primary_desk_key)
    interval = str((desk_row.config_json or {}).get("interval") or "1d").strip() or "1d"
    analysis = analyze_market(
        AnalyzeRequest(
            ticker=_normalize_symbol(target.get("symbol")),
            interval=interval,  # type: ignore[arg-type]
            horizon=5,
            include_history=False,
            include_live_price=True,
        ),
        current_user=current_user,
    )
    live_price = analysis.get("live_price")
    if live_price in (None, "", "nan") or float(live_price) <= 0:
        raise ValidationError(f"A live price is required to route {target.get('symbol')}.")
    return analysis, interval, primary_desk_key


def _build_open_request(
    *,
    target: dict[str, Any],
    analysis: dict[str, Any],
    interval: str | None,
    primary_desk_key: str | None,
    portfolio_target_run_id: str,
    delta_quantity: float,
) -> OpenTradeRequest:
    live_price = float(analysis.get("live_price") or 0.0)
    target_weight = _coerce_float(target.get("target_weight"))
    target_notional = _coerce_float(target.get("target_notional"))
    side = "sell" if target_notional < 0 or target_weight < 0 else "buy"
    normalized_quantity = abs(float(delta_quantity))
    fractional_shares_only = normalized_quantity < 1.0
    order_plan = _coerce_dict(target.get("order_plan"))
    allow_extended_hours = bool(order_plan.get("extended_hours", True))
    order_type = str(order_plan.get("order_type") or ("limit" if allow_extended_hours else "market")).strip().lower()
    if order_type not in {"market", "limit"}:
        order_type = "limit" if allow_extended_hours else "market"
    if allow_extended_hours and order_type != "limit":
        order_type = "limit"
    return OpenTradeRequest(
        ticker=_normalize_symbol(target.get("symbol")),
        interval=interval or "1d",
        horizon=5,
        execution_mode="portfolio_target_execution",
        live_price=live_price,
        account_size=max(abs(target_notional), normalized_quantity * live_price, 1.0),
        risk_percent=1.0,
        requested_quantity=normalized_quantity,
        instrument_type="equity",
        broker_side=side,
        execution_intent="broker_paper",
        order_type=order_type,  # type: ignore[arg-type]
        time_in_force="day_ext" if allow_extended_hours else "day",
        limit_price=live_price if order_type == "limit" else None,
        extended_hours=allow_extended_hours,
        fractional_shares_only=fractional_shares_only,
        regular_hours_only=False,
        automation_entry_reason="portfolio_target_execution",
        thesis_direction="BEARISH" if side == "sell" else "BULLISH",
        source="strategy_allocator",
        portfolio_target_run_id=portfolio_target_run_id,
        strategy_desk_key=primary_desk_key,
        desk_contributions=list(target.get("desk_contributions") or []),
    )


def _execute_close_quantity(
    *,
    symbol: str,
    signed_open_quantity: float,
    quantity_to_close: float,
    live_price: float,
    current_user: Any,
    db: Session,
) -> list[dict[str, Any]]:
    remaining = abs(float(quantity_to_close))
    results: list[dict[str, Any]] = []
    expected_side = "sell" if signed_open_quantity < 0 else "buy"
    while remaining > DELTA_QUANTITY_TOLERANCE:
        open_trades = _scoped_open_trades(current_user)
        symbol_rows = _filter_symbol_rows(open_trades, symbol)
        matched_position: int | None = None
        matched_row: dict[str, Any] | None = None
        for position in range(len(symbol_rows)):
            candidate = symbol_rows.iloc[position].to_dict()
            if _normalize_side(candidate.get("broker_side")) != expected_side:
                continue
            trade_id = str(candidate.get("trade_id") or "").strip()
            matched_position = _find_trade_index_for_trade_id(current_user, trade_id=trade_id)
            if matched_position is None:
                continue
            matched_row = candidate
            break
        if matched_position is None or matched_row is None:
            break

        row_quantity = _quantity_from_row(matched_row)
        if row_quantity <= 0:
            break
        close_quantity = min(remaining, row_quantity)
        fraction = min(1.0, close_quantity / row_quantity)
        close_request = CloseTradeRequest(
            trade_index=matched_position,
            close_underlying_price=float(live_price),
            close_contract_mid=float(live_price) / 100.0,
            close_fraction=float(fraction),
        )
        result = close_trade_from_request(close_request, db=db, current_user=current_user)
        results.append(result)
        remaining = max(0.0, remaining - close_quantity)
        if fraction >= 1.0 and remaining <= DELTA_QUANTITY_TOLERANCE:
            break
    return results


def _load_execution_run(db: Session, *, tenant_id: str, execution_run_id: str) -> PortfolioTargetExecutionRun:
    row = db.execute(
        select(PortfolioTargetExecutionRun).where(
            PortfolioTargetExecutionRun.tenant_id == tenant_id,
            PortfolioTargetExecutionRun.id == execution_run_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("The requested portfolio target execution run could not be found.")
    return row


def _sort_event_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""),
        reverse=True,
    )


def _resolve_item_reconciliation(
    *,
    item: PortfolioTargetExecutionItem,
    pending_rows: list[dict[str, Any]],
    open_rows: list[dict[str, Any]],
    closed_rows: list[dict[str, Any]],
    order_events: list[dict[str, Any]],
) -> dict[str, Any]:
    identifiers = _collect_item_identifiers(item)
    matching_pending = _filter_matching_rows(pending_rows, identifiers)
    matching_open = _filter_matching_rows(open_rows, identifiers)
    matching_closed = _filter_matching_rows(closed_rows, identifiers)
    matching_events = _sort_event_rows(_filter_matching_rows(order_events, identifiers))
    latest_event = matching_events[0] if matching_events else None
    requested_quantity = abs(float(item.requested_delta_quantity or 0.0))

    base_state = str(item.reconciliation_status or item.status or "queued").strip().lower() or "queued"
    broker_order_id = (
        _normalize_identifier(item.broker_order_id)
        or (_normalize_identifier((matching_pending[0] if matching_pending else {}).get("broker_order_id")))
        or (_normalize_identifier((matching_open[0] if matching_open else {}).get("broker_order_id")))
        or (_normalize_identifier((matching_closed[0] if matching_closed else {}).get("broker_order_id")))
    )
    broker_status = (
        _normalize_identifier((matching_pending[0] if matching_pending else {}).get("broker_status"))
        or _normalize_identifier((matching_open[0] if matching_open else {}).get("broker_status"))
        or _normalize_identifier((matching_closed[0] if matching_closed else {}).get("broker_status"))
        or _normalize_identifier((latest_event or {}).get("status"))
        or _normalize_identifier(item.broker_status)
    )

    if base_state in {"blocked", "skipped", "dry_run"}:
        return {
            "status": base_state,
            "reconciliation_status": base_state,
            "broker_order_id": broker_order_id,
            "broker_status": broker_status,
            "filled_quantity": float(item.filled_quantity or 0.0),
            "remaining_quantity": max(0.0, requested_quantity - float(item.filled_quantity or 0.0)),
            "average_fill_price": item.average_fill_price,
            "terminal_at": item.terminal_at,
            "last_seen_at": _utc_now(),
            "reason": item.reason,
            "matched_event_ids": {str(row.get("id") or "").strip() for row in matching_events if str(row.get("id") or "").strip()},
        }

    if matching_pending:
        pending_row = dict(matching_pending[0] or {})
        filled_quantity = (
            _coerce_float(pending_row.get("broker_filled_qty"))
            or _coerce_float((latest_event or {}).get("payload", {}).get("synced_order", {}).get("broker_filled_qty"))
            or float(item.filled_quantity or 0.0)
        )
        status = "partially_filled" if str(broker_status or "").strip().lower() == "partially_filled" or (
            filled_quantity > DELTA_QUANTITY_TOLERANCE and requested_quantity > filled_quantity + DELTA_QUANTITY_TOLERANCE
        ) else "working"
        return {
            "status": status,
            "reconciliation_status": status,
            "broker_order_id": broker_order_id,
            "broker_status": str(broker_status or "").strip().lower() or "working",
            "filled_quantity": float(filled_quantity),
            "remaining_quantity": max(0.0, requested_quantity - float(filled_quantity)),
            "average_fill_price": (
                _coerce_float(pending_row.get("actual_fill_price"))
                or _coerce_float(pending_row.get("broker_filled_avg_price"))
                or item.average_fill_price
            ),
            "terminal_at": None,
            "last_seen_at": _utc_now(),
            "reason": item.reason,
            "matched_event_ids": {str(row.get("id") or "").strip() for row in matching_events if str(row.get("id") or "").strip()},
        }

    if matching_open or matching_closed:
        filled_quantity = requested_quantity if requested_quantity > 0 else float(item.filled_quantity or 0.0)
        status = "filled"
        average_fill_price = None
        for candidate in [*(matching_open or []), *(matching_closed or [])]:
            average_fill_price = (
                _coerce_float(candidate.get("actual_fill_price"))
                or _coerce_float(candidate.get("fill_price"))
                or _coerce_float(candidate.get("contract_mid_at_open"))
                or average_fill_price
            )
        return {
            "status": status,
            "reconciliation_status": status,
            "broker_order_id": broker_order_id,
            "broker_status": str(broker_status or "filled").strip().lower() or "filled",
            "filled_quantity": float(filled_quantity),
            "remaining_quantity": 0.0 if status == "filled" else max(0.0, requested_quantity - float(filled_quantity)),
            "average_fill_price": average_fill_price or item.average_fill_price,
            "terminal_at": item.terminal_at or _utc_now(),
            "last_seen_at": _utc_now(),
            "reason": item.reason,
            "matched_event_ids": {str(row.get("id") or "").strip() for row in matching_events if str(row.get("id") or "").strip()},
        }

    normalized_broker_status = str(broker_status or "").strip().lower()
    if normalized_broker_status in {"rejected"} or str((latest_event or {}).get("event_key") or "").strip().lower() == "order.rejected":
        status = "rejected"
    elif normalized_broker_status in {"canceled", "cancelled", "expired"} or str((latest_event or {}).get("event_key") or "").strip().lower() in {"order.canceled", "order.expired"}:
        status = "canceled"
    elif str((latest_event or {}).get("event_key") or "").strip().lower() == "order.filled":
        status = "filled"
    elif latest_event is not None:
        status = "working"
    else:
        status = "orphan"

    return {
        "status": status,
        "reconciliation_status": status,
        "broker_order_id": broker_order_id,
        "broker_status": normalized_broker_status or None,
        "filled_quantity": float(item.filled_quantity or 0.0),
        "remaining_quantity": max(0.0, requested_quantity - float(item.filled_quantity or 0.0)),
        "average_fill_price": item.average_fill_price,
        "terminal_at": item.terminal_at or (_utc_now() if status in {"filled", "canceled", "rejected", "orphan"} else None),
        "last_seen_at": _utc_now(),
        "reason": item.reason,
        "matched_event_ids": {str(row.get("id") or "").strip() for row in matching_events if str(row.get("id") or "").strip()},
    }


def _collect_orphan_events(
    *,
    run: PortfolioTargetExecutionRun,
    items: list[PortfolioTargetExecutionItem],
    order_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    tracked_symbols = {str(item.symbol or "").strip().upper() for item in items if str(item.symbol or "").strip()}
    tracked_event_ids: set[str] = set()
    tracked_identifiers: list[dict[str, set[str]]] = [_collect_item_identifiers(item) for item in items]
    for event in order_events:
        event_id = str(event.get("id") or "").strip()
        if not event_id:
            continue
        if any(_matches_identifiers(event, identifiers) for identifiers in tracked_identifiers):
            tracked_event_ids.add(event_id)

    started_at = run.started_at or run.created_at
    orphan_events: list[dict[str, Any]] = []
    for event in order_events:
        event_id = str(event.get("id") or "").strip()
        if not event_id or event_id in tracked_event_ids:
            continue
        symbol = _normalize_symbol(event.get("ticker"))
        if tracked_symbols and symbol not in tracked_symbols:
            continue
        created_at = str(event.get("created_at") or "")
        if started_at is not None and created_at and created_at < started_at.isoformat():
            continue
        orphan_events.append(
            {
                "id": event_id,
                "symbol": symbol,
                "event_key": str(event.get("event_key") or "").strip() or None,
                "status": str(event.get("status") or event.get("broker_status") or "").strip() or None,
                "created_at": created_at or None,
                "reason": "Unmatched broker/local order event for a symbol in this execution run.",
            }
        )
    return orphan_events


def _count_orphan_events(
    *,
    run: PortfolioTargetExecutionRun,
    items: list[PortfolioTargetExecutionItem],
    order_events: list[dict[str, Any]],
) -> int:
    return len(_collect_orphan_events(run=run, items=items, order_events=order_events))


def get_portfolio_target_execution(
    db: Session,
    *,
    current_user: Any,
    execution_run_id: str,
) -> dict[str, Any]:
    _assert_strategy_executor(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    run = _load_execution_run(db, tenant_id=tenant.id, execution_run_id=execution_run_id)
    return _serialize_execution_run(run)


def sync_portfolio_target_execution(
    db: Session,
    *,
    current_user: Any,
    execution_run_id: str,
) -> dict[str, Any]:
    _assert_strategy_executor(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    run = _load_execution_run(db, tenant_id=tenant.id, execution_run_id=execution_run_id)

    synced_order_ids: set[str] = set()
    for item in list(run.execution_items or []):
        if str(item.reconciliation_status or "").strip().lower() in TERMINAL_RECONCILIATION_STATUSES:
            continue
        normalized_order_id = _normalize_identifier(item.submitted_order_id)
        if not normalized_order_id or normalized_order_id in synced_order_ids:
            continue
        sync_pending_orders_from_broker(
            db=db,
            current_user=current_user,
            order_id=normalized_order_id,
        )
        synced_order_ids.add(normalized_order_id)

    open_rows = _scoped_open_trades(current_user).to_dict(orient="records")
    pending_rows = _scoped_pending_orders(current_user).to_dict(orient="records")
    closed_rows = _scoped_closed_trades(current_user).to_dict(orient="records")
    order_events = list(get_order_events_snapshot(db, current_user, limit=250).get("items") or [])

    for item in list(run.execution_items or []):
        reconciliation = _resolve_item_reconciliation(
            item=item,
            pending_rows=pending_rows,
            open_rows=open_rows,
            closed_rows=closed_rows,
            order_events=order_events,
        )
        item.status = str(reconciliation["status"] or item.status or "queued")
        item.reconciliation_status = str(reconciliation["reconciliation_status"] or item.reconciliation_status or item.status)
        item.broker_order_id = reconciliation["broker_order_id"]
        item.broker_status = reconciliation["broker_status"]
        item.filled_quantity = float(reconciliation["filled_quantity"] or 0.0)
        item.remaining_quantity = float(reconciliation["remaining_quantity"] or 0.0)
        item.average_fill_price = reconciliation["average_fill_price"]
        item.terminal_at = reconciliation["terminal_at"]
        item.last_seen_at = reconciliation["last_seen_at"]

    orphan_events = _collect_orphan_events(run=run, items=list(run.execution_items or []), order_events=order_events)
    orphan_event_count = len(orphan_events)
    next_status = _build_run_status_from_items(items=list(run.execution_items or []), orphan_event_count=orphan_event_count)
    _apply_run_rollups(
        run,
        status=next_status,
        items=list(run.execution_items or []),
        orphan_event_count=orphan_event_count,
        orphan_events=orphan_events,
        last_sync_at=_utc_now(),
    )
    if next_status in {"filled", "canceled", "rejected", "completed", "completed_with_errors", "reconciliation_warning"} and run.completed_at is None:
        run.completed_at = _utc_now()

    record_audit_event(
        db,
        event_type="portfolio_target_execution.synced",
        tenant=tenant,
        user=actor,
        payload={
            "execution_run_id": run.id,
            "portfolio_target_run_id": run.portfolio_target_run_id,
            "status": run.status,
            "working_count": run.working_count,
            "partial_fill_count": run.partial_fill_count,
            "filled_count": run.filled_count,
            "canceled_count": run.canceled_count,
            "rejected_count": run.rejected_count,
            "orphan_event_count": run.orphan_event_count,
        },
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="orders.filled" if run.filled_count else "orders.partially_filled" if run.partial_fill_count else "positions.updated",
        aggregate_type="portfolio_target_execution_run",
        aggregate_id=run.id,
        payload=dict(run.summary_json or {}),
    )
    db.commit()
    db.refresh(run)
    return _serialize_execution_run(run)


def get_latest_portfolio_target_execution(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_strategy_executor(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    latest = db.execute(
        select(PortfolioTargetExecutionRun)
        .where(PortfolioTargetExecutionRun.tenant_id == tenant.id)
        .order_by(PortfolioTargetExecutionRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return _serialize_execution_run(latest)


def execute_portfolio_targets(
    db: Session,
    *,
    current_user: Any,
    request: PortfolioTargetExecutionRequest,
) -> dict[str, Any]:
    _assert_strategy_executor(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    latest_overall, latest_accepted = _load_latest_target_runs(db, tenant_id=tenant.id)

    if request.portfolio_target_run_id:
        portfolio_target_run = db.execute(
            select(PortfolioTargetRun).where(
                PortfolioTargetRun.tenant_id == tenant.id,
                PortfolioTargetRun.id == request.portfolio_target_run_id,
            )
        ).scalar_one_or_none()
        if portfolio_target_run is None:
            raise NotFoundError("The requested portfolio target run could not be found.")
    else:
        portfolio_target_run = latest_accepted

    blocked_reason: str | None = None
    if portfolio_target_run is None:
        if latest_overall is not None and str(latest_overall.status or "").strip().lower() == "blocked":
            blocked_reason = "The latest allocator or risk snapshot is blocked. Resolve that blocker before paper execution."
        else:
            blocked_reason = "No accepted portfolio-target run is available for paper execution."
    elif portfolio_target_run.status != "accepted":
        blocked_reason = "The selected portfolio-target run is not accepted for paper execution."
    elif request.portfolio_target_run_id is None and latest_overall is not None and latest_overall.id != portfolio_target_run.id:
        if str(latest_overall.status or "").strip().lower() != "accepted":
            blocked_reason = "The latest allocator or risk snapshot is blocked. Resolve that blocker before paper execution."

    if blocked_reason is not None:
        summary = _build_run_summary(status="blocked", blocked_reason=blocked_reason)
        run = _create_execution_run(
            db,
            tenant_id=tenant.id,
            portfolio_target_run=portfolio_target_run,
            status="blocked",
            execution_intent=request.execution_intent,
            dry_run=bool(request.dry_run),
            summary=summary,
            metadata={"scope": "personal_paper"},
        )
        record_audit_event(
            db,
            event_type="portfolio_target_execution.blocked",
            tenant=tenant,
            user=actor,
            payload={
                "portfolio_target_run_id": portfolio_target_run.id if portfolio_target_run is not None else None,
                "blocked_reason": blocked_reason,
            },
        )
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="risk.pretrade_rejected",
            aggregate_type="portfolio_target_execution_run",
            aggregate_id=run.id,
            payload={"blocked_reason": blocked_reason},
        )
        _apply_run_rollups(run, status="blocked", items=[], orphan_event_count=0, last_sync_at=_utc_now())
        db.commit()
        db.refresh(run)
        return _serialize_execution_run(run)

    strategy_desks = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id)
    ).scalars().all()
    desk_lookup = {desk.desk_key: desk for desk in strategy_desks}
    run = _create_execution_run(
        db,
        tenant_id=tenant.id,
        portfolio_target_run=portfolio_target_run,
        status="running",
        execution_intent=request.execution_intent,
        dry_run=bool(request.dry_run),
        summary={"status": "running"},
        metadata={"scope": "personal_paper"},
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="risk.pretrade_passed",
        aggregate_type="portfolio_target_execution_run",
        aggregate_id=run.id,
        payload={"portfolio_target_run_id": portfolio_target_run.id, "execution_intent": request.execution_intent},
    )
    db.commit()

    created_items: list[PortfolioTargetExecutionItem] = []
    item_errors = 0
    targets = list((portfolio_target_run.portfolio_targets_json or {}).get("targets") or [])
    for target in targets:
        symbol = _normalize_symbol(target.get("symbol"))
        target_weight = _coerce_float(target.get("target_weight"))
        target_notional = _coerce_float(target.get("target_notional"))
        desk_contributions = list(target.get("desk_contributions") or [])
        strategy_desk_key = _desk_key_for_item(desk_contributions)
        source_metadata = {
            "target": dict(target or {}),
            "desk_contributions": desk_contributions,
        }

        try:
            context, interval, primary_desk_key = _resolve_symbol_context(
                target=target,
                desk_lookup=desk_lookup,
                current_user=current_user,
            )
            if "reason" in context:
                item = _append_execution_item(
                    db,
                    execution_run=run,
                    symbol=symbol,
                    action="skip",
                    status="skipped",
                    requested_target_weight=target_weight,
                    requested_target_notional=target_notional,
                    requested_delta_quantity=0.0,
                    resolved_route=request.execution_intent,
                    strategy_desk_key=strategy_desk_key,
                    reason=str(context["reason"]),
                    source_metadata=source_metadata,
                    result={},
                )
                created_items.append(item)
                continue

            analysis = dict(context or {})
            live_price = float(analysis.get("live_price") or 0.0)
            if live_price <= 0:
                raise ValidationError(f"A live price is required to route {symbol}.")

            open_rows = _filter_symbol_rows(_scoped_open_trades(current_user), symbol)
            pending_rows = _filter_symbol_rows(_scoped_pending_orders(current_user), symbol)
            signed_open_quantity = _sum_signed_quantity(open_rows)
            signed_pending_quantity = _sum_signed_quantity(pending_rows)
            signed_target_quantity = 0.0 if abs(target_notional) <= 0 else float(target_notional / live_price)
            signed_effective_quantity = float(signed_open_quantity + signed_pending_quantity)
            delta_quantity = float(signed_target_quantity - signed_effective_quantity)

            if not pending_rows.empty:
                reason = (
                    "Pending orders already satisfy the current target delta."
                    if abs(delta_quantity) <= DELTA_QUANTITY_TOLERANCE
                    else "Pending orders are still working for this symbol after netting, so the basket route will not double-submit."
                )
                item = _append_execution_item(
                    db,
                    execution_run=run,
                    symbol=symbol,
                    action="skip",
                    status="skipped",
                    requested_target_weight=target_weight,
                    requested_target_notional=target_notional,
                    requested_delta_quantity=delta_quantity,
                    resolved_route=request.execution_intent,
                    strategy_desk_key=strategy_desk_key,
                    reason=reason,
                    source_metadata=source_metadata,
                    result={},
                )
                created_items.append(item)
                continue

            if abs(delta_quantity) <= DELTA_QUANTITY_TOLERANCE and abs(signed_open_quantity - signed_target_quantity) <= DELTA_QUANTITY_TOLERANCE:
                item = _append_execution_item(
                    db,
                    execution_run=run,
                    symbol=symbol,
                    action="skip",
                    status="skipped",
                    requested_target_weight=target_weight,
                    requested_target_notional=target_notional,
                    requested_delta_quantity=0.0,
                    resolved_route=request.execution_intent,
                    strategy_desk_key=strategy_desk_key,
                    reason="Current paper exposure already matches the requested target.",
                    source_metadata=source_metadata,
                    result={},
                )
                created_items.append(item)
                continue

            if abs(signed_target_quantity) <= DELTA_QUANTITY_TOLERANCE and abs(signed_open_quantity) > DELTA_QUANTITY_TOLERANCE:
                action = "close"
                quantity_to_close = abs(signed_open_quantity)
            elif abs(signed_open_quantity) <= DELTA_QUANTITY_TOLERANCE:
                action = "open"
                quantity_to_close = 0.0
            elif signed_open_quantity * signed_target_quantity < 0:
                action = "close"
                quantity_to_close = abs(signed_open_quantity)
            elif abs(signed_target_quantity) > abs(signed_open_quantity):
                action = "add"
                quantity_to_close = 0.0
            else:
                action = "reduce"
                quantity_to_close = abs(signed_open_quantity) - abs(signed_target_quantity)

            if request.dry_run:
                item = _append_execution_item(
                    db,
                    execution_run=run,
                    symbol=symbol,
                    action=action,
                    status="dry_run",
                    requested_target_weight=target_weight,
                    requested_target_notional=target_notional,
                    requested_delta_quantity=delta_quantity if action in {"open", "add"} else quantity_to_close,
                    resolved_route=request.execution_intent,
                    strategy_desk_key=strategy_desk_key,
                    reason="Dry run only; no broker-paper orders were submitted.",
                    source_metadata=source_metadata,
                    result={
                        "signed_open_quantity": signed_open_quantity,
                        "signed_target_quantity": signed_target_quantity,
                        "signed_pending_quantity": signed_pending_quantity,
                    },
                )
                created_items.append(item)
                continue

            if action in {"open", "add"}:
                open_request = _build_open_request(
                    target=target,
                    analysis=analysis,
                    interval=interval,
                    primary_desk_key=primary_desk_key,
                    portfolio_target_run_id=portfolio_target_run.id,
                    delta_quantity=abs(delta_quantity),
                )
                result = open_trade_from_request(open_request, db=db, current_user=current_user)
                submitted_row = dict(result.get("record") or result.get("pending_order") or {})
                item = _append_execution_item(
                    db,
                    execution_run=run,
                    symbol=symbol,
                    action=action,
                    status="submitted",
                    requested_target_weight=target_weight,
                    requested_target_notional=target_notional,
                    requested_delta_quantity=abs(delta_quantity),
                    resolved_route=request.execution_intent,
                    strategy_desk_key=strategy_desk_key,
                    reason=None,
                    source_metadata=source_metadata,
                    result=result,
                )
                item.submitted_trade_id = str(submitted_row.get("trade_id") or "").strip() or None
                item.submitted_order_id = str(submitted_row.get("order_id") or "").strip() or None
                _apply_submission_lifecycle(
                    item,
                    item_status="submitted",
                    requested_quantity=abs(delta_quantity),
                    submitted_row=submitted_row,
                    result=result,
                )
                created_items.append(item)
                record_domain_event(
                    db,
                    tenant_id=tenant.id,
                    event_type="orders.submitted",
                    aggregate_type="portfolio_target_execution_run",
                    aggregate_id=run.id,
                    payload={"symbol": symbol, "action": action, "strategy_desk_key": strategy_desk_key},
                )
                db.commit()
                continue

            close_results = _execute_close_quantity(
                symbol=symbol,
                signed_open_quantity=signed_open_quantity,
                quantity_to_close=quantity_to_close,
                live_price=live_price,
                current_user=current_user,
                db=db,
            )
            close_reason = None
            if signed_open_quantity * signed_target_quantity < 0:
                close_reason = "Sign flip detected. Existing paper exposure was flattened only; the reverse side was deferred to a future execution batch."
            item = _append_execution_item(
                db,
                execution_run=run,
                symbol=symbol,
                action=action,
                status="submitted" if close_results else "blocked",
                requested_target_weight=target_weight,
                requested_target_notional=target_notional,
                requested_delta_quantity=quantity_to_close,
                resolved_route=request.execution_intent,
                strategy_desk_key=strategy_desk_key,
                reason=close_reason if close_results else "No matching open paper position was available to close.",
                source_metadata=source_metadata,
                result={"close_results": close_results},
            )
            if close_results:
                latest_close = dict(close_results[-1].get("closed_trade_preview") or {})
                item.submitted_trade_id = str(latest_close.get("trade_id") or "").strip() or None
                item.submitted_order_id = str(latest_close.get("broker_close_order_id") or "").strip() or None
                _apply_submission_lifecycle(
                    item,
                    item_status="submitted",
                    requested_quantity=quantity_to_close,
                    submitted_row=latest_close,
                    result={"close_results": close_results, "execution": close_results[-1].get("execution")},
                )
                record_domain_event(
                    db,
                    tenant_id=tenant.id,
                    event_type="orders.submitted",
                    aggregate_type="portfolio_target_execution_run",
                    aggregate_id=run.id,
                    payload={"symbol": symbol, "action": action, "strategy_desk_key": strategy_desk_key},
                )
            else:
                item_errors += 1
            created_items.append(item)
            db.commit()
        except Exception as exc:
            item = _append_execution_item(
                db,
                execution_run=run,
                symbol=symbol,
                action="blocked",
                status="blocked",
                requested_target_weight=target_weight,
                requested_target_notional=target_notional,
                requested_delta_quantity=0.0,
                resolved_route=request.execution_intent,
                strategy_desk_key=strategy_desk_key,
                reason=str(exc),
                source_metadata=source_metadata,
                result={"error": str(exc)},
            )
            created_items.append(item)
            item_errors += 1
            db.commit()

    if request.dry_run:
        run.status = "dry_run"
    elif item_errors:
        run.status = "completed_with_errors"
    else:
        run.status = _build_run_status_from_items(items=created_items, orphan_event_count=0)
    _apply_run_rollups(
        run,
        status=run.status,
        items=created_items,
        orphan_event_count=0,
        last_sync_at=_utc_now(),
    )
    if run.status in {"working", "partially_filled"}:
        run.completed_at = None
    else:
        run.completed_at = _utc_now()
    record_audit_event(
        db,
        event_type="portfolio_target_execution.run",
        tenant=tenant,
        user=actor,
        payload={
            "execution_run_id": run.id,
            "portfolio_target_run_id": portfolio_target_run.id,
            "status": run.status,
            "dry_run": bool(request.dry_run),
        },
    )
    db.commit()
    db.refresh(run)
    return _serialize_execution_run(run)
