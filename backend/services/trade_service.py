from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant, TradeApprovalIntent, User
from backend.schemas import (
    AnalyzeRequest,
    CancelOrderRequest,
    CloseTradeRequest,
    FillOrderRequest,
    OpenTradeRequest,
    ReplaceOrderRequest,
)
from backend.services.audit_service import record_audit_event
from backend.services.brokerage_account_service import (
    build_linked_client_automation_summary,
    build_linked_account_execution_client,
    get_linked_account_automation_profile,
    list_trade_intent_audit_events,
    mark_linked_account_execution_failure,
    record_linked_account_automation_submission,
)
from backend.services.desk_service import apply_tenant_scope_to_record, filter_frame_to_current_user
from backend.services.execution import get_execution_adapter, get_execution_adapter_for
from backend.services.execution.alpaca_client import AlpacaApiError
from backend.services.execution.base import ExecutionAdapter
from backend.services.execution.mappers import (
    build_alpaca_equity_order_payload,
    build_alpaca_option_order_payload,
    is_filled_alpaca_status,
    normalize_alpaca_status,
)
from backend.services.exceptions import ValidationServiceError
from backend.services.market_service import analyze_market
from backend.services.serialization import serialize_dataframe, serialize_value
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user
from backend.services.trade_workflow_service import (
    build_decision_review_snapshot,
    build_evidence_register_snapshot,
)

_DAY_ORDER_STALE_MINUTES = 60 * 24
_GTC_ORDER_STALE_MINUTES = 60 * 24 * 7
_CAPITAL_PRESERVATION_TIMEZONE = "America/New_York"
_OPTION_MAX_SPREAD_PCT = 0.15
_OPTION_MAX_QUOTE_AGE_SECONDS = 180.0
_OPTION_MIN_VOLUME = 25
_OPTION_MIN_OPEN_INTEREST = 100
_BROKER_TRUST_PACKET_VERSION = "1.0"
_BROKER_STRATEGY_RELEASE_VERSION = "broker_strategy_release_v1"
_BROKER_CASE_SCHEMA_VERSION = "client_trade_case_v1"
_BROKER_STALE_RECOMMENDATION_HOURS = 24


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def resolve_trade_identifier(row: dict[str, Any] | pd.Series | None) -> str:
    if row is None:
        return f"legacy-unknown-{uuid4().hex[:12]}"

    if isinstance(row, pd.Series):
        trade_id = str(row.get("trade_id", "") or "").strip()
        ticker = str(row.get("ticker", "") or "").strip().lower() or "unknown"
        opened_at = str(row.get("opened_at", "") or "").strip()
    else:
        trade_id = str(row.get("trade_id", "") or "").strip()
        ticker = str(row.get("ticker", "") or "").strip().lower() or "unknown"
        opened_at = str(row.get("opened_at", "") or "").strip()

    if trade_id:
        return trade_id

    opened_token = (
        opened_at.replace(":", "")
        .replace("-", "")
        .replace("T", "")
        .replace("Z", "")
        .replace(".", "")
    )
    return f"legacy-{ticker}-{(opened_token or 'unknown')[:18]}"


def _normalize_route_correlation_id(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _resolve_trade_actor_context(db: Session | None, current_user: Any) -> tuple[Tenant | None, User | None]:
    if db is None or current_user is None:
        return None, None
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    return tenant, actor


def _scoped_open_trades(current_user: Any | None) -> pd.DataFrame:
    return filter_frame_to_current_user(sdm.read_open_trades(), current_user)


def _scoped_pending_orders(current_user: Any | None) -> pd.DataFrame:
    return filter_frame_to_current_user(sdm.read_pending_orders(), current_user)


def _scoped_closed_trades(current_user: Any | None) -> pd.DataFrame:
    return filter_frame_to_current_user(sdm.read_closed_trades(), current_user)


def _with_current_tenant_scope(record: dict[str, Any] | None, current_user: Any | None) -> dict[str, Any]:
    return apply_tenant_scope_to_record(
        record,
        tenant_id=getattr(current_user, "tenant_id", None),
        tenant_slug=getattr(current_user, "tenant_slug", None),
    )


def _is_linked_client_trade_request(request: OpenTradeRequest) -> bool:
    return str(getattr(request, "account_target_type", "personal") or "personal").strip().lower() == "linked_client"


def _resolve_linked_brokerage_account(
    db: Session | None,
    current_user: Any | None,
    *,
    linked_account_id: str | None,
    execution_mode: str = "manual_approval",
) -> tuple[Tenant, User, BrokerageLinkedAccount]:
    if db is None or current_user is None:
        raise ValidationServiceError("Linked client routing requires an authenticated tenant session.")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        raise ValidationServiceError("Linked client routing requires an authenticated user.")
    normalized_account_id = str(linked_account_id or "").strip()
    if not normalized_account_id:
        raise ValidationServiceError("Choose a linked client account before creating a trade approval request.")
    linked_account = db.execute(
        select(BrokerageLinkedAccount).where(
            BrokerageLinkedAccount.id == normalized_account_id,
            BrokerageLinkedAccount.tenant_id == tenant.id,
            BrokerageLinkedAccount.provider == "alpaca",
        )
    ).scalar_one_or_none()
    if linked_account is None:
        raise ValidationServiceError("The selected linked client account could not be found.")
    normalized_execution_mode = str(execution_mode or "manual_approval").strip().lower() or "manual_approval"
    if normalized_execution_mode == "manual_approval" and linked_account.approval_policy != "approval_required":
        raise ValidationServiceError("Linked client trading is restricted to approval-based accounts in this release.")
    if linked_account.connection_status not in {"connected"} or linked_account.token_health not in {"healthy", "unknown"}:
        raise ValidationServiceError("The selected linked client account needs to be relinked or refreshed before trading.")
    if normalized_execution_mode == "automated_entry":
        automation = get_linked_account_automation_profile(linked_account)
        if not bool(automation.get("entries_only")):
            raise ValidationServiceError("Linked client auto-trading is limited to entries-only in this release.")
        if not bool(automation.get("automation_eligible")):
            block_label = str(automation.get("automation_block_label") or "Automation is blocked").strip()
            raise ValidationServiceError(f"{block_label} for this linked client account.")
    return tenant, actor, linked_account


def _serialize_trade_intent(
    row: TradeApprovalIntent,
    *,
    audit_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    linked_account = row.linked_account
    metadata = dict(row.metadata_json or {})
    serialized = {
        "id": row.id,
        "status": row.status,
        "ticker": row.ticker,
        "instrument_type": row.instrument_type,
        "account_environment": row.account_environment,
        "provider": row.provider,
        "execution_lane": row.execution_lane,
        "trade_id": row.trade_id,
        "order_id": row.order_id,
        "route_correlation_id": row.route_correlation_id,
        "linked_account_id": row.linked_account_id,
        "account_label": linked_account.label or linked_account.linked_identity_label or f"Alpaca {linked_account.account_environment}",
        "linked_account": {
            "id": linked_account.id,
            "label": linked_account.label or linked_account.linked_identity_label or f"Alpaca {linked_account.account_environment}",
            "account_environment": linked_account.account_environment,
            "connection_status": linked_account.connection_status,
            "token_health": linked_account.token_health,
            "approval_policy": linked_account.approval_policy,
        },
        "request_payload": serialize_value(row.request_payload_json or {}),
        "analysis": serialize_value(row.analysis_json or {}),
        "position": serialize_value(row.position_json or {}),
        "order_ticket": serialize_value(row.order_ticket_json or {}),
        "broker_submit_payload": serialize_value(row.broker_submit_payload_json or {}),
        "broker_order_id": row.broker_order_id,
        "broker_status": row.broker_status,
        "broker_response": serialize_value(row.broker_response_json or {}),
        "execution_mode": str(metadata.get("execution_mode") or "manual_approval").strip() or "manual_approval",
        "auto_submitted": bool(metadata.get("auto_submitted")),
        "approval_note": row.approval_note,
        "rejection_reason": row.rejection_reason,
        "requester_user_id": row.requester_user_id,
        "approver_user_id": row.approver_user_id,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "rejected_at": row.rejected_at.isoformat() if row.rejected_at else None,
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "expired_at": row.expired_at.isoformat() if row.expired_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "audit_events": list(audit_events or []),
    }
    broker_case = _build_client_trade_case_snapshot(row, audit_events=audit_events)
    decision_review = build_decision_review_snapshot(row)
    evidence_register = build_evidence_register_snapshot(row)
    serialized["broker_case"] = broker_case
    serialized["decision_review"] = decision_review
    serialized["evidence_register"] = evidence_register
    serialized["strategy_release_snapshot"] = _build_strategy_release_snapshot(row)
    serialized["trust_packet_summary"] = _build_trust_packet_summary(row, broker_case=broker_case, audit_events=audit_events)
    return serialized


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_items(values: list[str] | None) -> list[str]:
    if not values:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _clean_text(value)
        key = item.lower()
        if item and key not in seen:
            cleaned.append(item[:240])
            seen.add(key)
    return cleaned[:8]


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return _clean_text(value) or None


def _intent_age_hours(row: TradeApprovalIntent) -> float | None:
    created_at = row.created_at
    if created_at is None:
        return None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max((_utc_now() - created_at).total_seconds() / 3600.0, 0.0)


def _broker_case_id(row: TradeApprovalIntent) -> str:
    metadata = _safe_dict(row.metadata_json)
    broker_case = _safe_dict(metadata.get("broker_case"))
    stored = _clean_text(broker_case.get("case_id"))
    if stored:
        return stored
    return f"CTC-{str(row.id or row.trade_id or uuid4()).replace('-', '')[:10].upper()}"


def _build_rationale_quality(note: str | None, reason: str | None = None, conditions: list[str] | None = None) -> dict[str, Any]:
    text = " ".join([_clean_text(note), _clean_text(reason), " ".join(_normalize_text_items(conditions))]).strip()
    lowered = text.lower()
    missing: list[str] = []
    if len(text) < 32:
        missing.append("Add a concise decision rationale.")
    if not any(term in lowered for term in ("risk", "size", "loss", "stop", "invalidation")):
        missing.append("Address risk, sizing, or invalidation.")
    if not any(term in lowered for term in ("client", "account", "approval", "authorization", "paper", "live")):
        missing.append("Tie the decision back to the client account context.")
    if not any(term in lowered for term in ("entry", "target", "exit", "submit", "condition", "reject")):
        missing.append("State the intended trade action or exit frame.")
    return {
        "status": "pass" if not missing else ("warning" if text else "missing"),
        "score": max(0, 100 - len(missing) * 25),
        "missing_items": missing,
        "character_count": len(text),
    }


def _build_strategy_release_snapshot(
    row: TradeApprovalIntent | None = None,
    *,
    request_payload: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    order_ticket: dict[str, Any] | None = None,
    option_execution_review: dict[str, Any] | None = None,
    live_price: float | None = None,
) -> dict[str, Any]:
    metadata = _safe_dict(getattr(row, "metadata_json", None))
    stored = _safe_dict(metadata.get("strategy_release_snapshot"))
    if stored:
        return serialize_value(stored)

    request_state = _safe_dict(request_payload if request_payload is not None else getattr(row, "request_payload_json", None))
    report_state = _safe_dict(report if report is not None else getattr(row, "analysis_json", None))
    position_state = _safe_dict(position if position is not None else getattr(row, "position_json", None))
    ticket_state = _safe_dict(order_ticket if order_ticket is not None else getattr(row, "order_ticket_json", None))
    review_state = _safe_dict(option_execution_review)
    if not review_state and row is not None:
        review_state = _safe_dict(metadata.get("option_execution_review"))
    option_plan = _safe_dict(report_state.get("option_plan"))
    forecast = _safe_dict(report_state.get("forecast"))
    route_family = _clean_text(ticket_state.get("route_family") or request_state.get("route_family") or "legacy").lower() or "legacy"
    route_version = _clean_text(ticket_state.get("route_version") or request_state.get("route_version"))
    if route_family == "current" and not route_version:
        route_version = "ranked_entry_v1"
    return serialize_value(
        {
            "schema_version": _BROKER_STRATEGY_RELEASE_VERSION,
            "release_id": f"{route_family}:{route_version or 'legacy'}",
            "app_version": settings.app_version,
            "prediction_stack_version": _clean_text(report_state.get("prediction_stack_version") or getattr(sdm, "INTRADAY_PREDICTION_STACK_VERSION", "")) or None,
            "route_family": route_family,
            "route_version": route_version or None,
            "strategy_desk_key": _clean_text(ticket_state.get("strategy_desk_key") or request_state.get("strategy_desk_key")) or None,
            "portfolio_target_run_id": _clean_text(ticket_state.get("portfolio_target_run_id") or request_state.get("portfolio_target_run_id")) or None,
            "basis": {
                "ticker": _clean_text(getattr(row, "ticker", None) or request_state.get("ticker")).upper() or None,
                "interval": request_state.get("interval"),
                "horizon": request_state.get("horizon"),
                "source": ticket_state.get("source") or request_state.get("source") or report_state.get("source"),
                "created_at": _iso_or_none(getattr(row, "created_at", None)) if row is not None else _utc_now().isoformat(),
            },
            "recommendation_basis": {
                "trade_decision": report_state.get("trade_decision"),
                "verdict": report_state.get("verdict"),
                "setup_score": report_state.get("setup_score"),
                "ranking_score": report_state.get("ranking_score"),
                "probability_up": report_state.get("probability_up"),
                "expected_underlying_target": option_plan.get("expected_underlying_target"),
                "invalidation_price": option_plan.get("invalidation_price"),
                "live_price": live_price if live_price is not None else metadata.get("live_price"),
            },
            "risk_basis": {
                "account_size": request_state.get("account_size"),
                "risk_percent": request_state.get("risk_percent"),
                "position_cost": position_state.get("total_position_cost") or position_state.get("position_cost"),
                "suggested_units": position_state.get("suggested_contracts") or position_state.get("quantity"),
                "max_notional_per_trade": request_state.get("max_notional_per_trade"),
                "max_open_positions": request_state.get("max_open_positions"),
            },
            "execution_basis": {
                "instrument_type": getattr(row, "instrument_type", None) or request_state.get("instrument_type"),
                "order_type": ticket_state.get("order_type") or request_state.get("order_type"),
                "time_in_force": ticket_state.get("time_in_force") or request_state.get("time_in_force"),
                "contract_symbol": ticket_state.get("contract_symbol") or request_state.get("contract_symbol"),
                "quote_status": review_state.get("status"),
                "quote_age_seconds": review_state.get("quote_age_seconds"),
            },
        }
    )


def _build_broker_case_metadata(
    row: TradeApprovalIntent | None,
    *,
    request_payload: dict[str, Any] | None = None,
    report: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    linked_account: BrokerageLinkedAccount | None = None,
    pre_trade_risk: dict[str, Any] | None = None,
    liquidity_execution: dict[str, Any] | None = None,
    route_eligibility: dict[str, Any] | None = None,
    strategy_release_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_state = _safe_dict(request_payload if request_payload is not None else getattr(row, "request_payload_json", None))
    report_state = _safe_dict(report if report is not None else getattr(row, "analysis_json", None))
    position_state = _safe_dict(position if position is not None else getattr(row, "position_json", None))
    route_state = _safe_dict(route_eligibility)
    liquidity_state = _safe_dict(liquidity_execution)
    risk_state = _safe_dict(pre_trade_risk)
    account = linked_account or getattr(row, "linked_account", None)

    factors: list[dict[str, Any]] = []
    score = 10

    def add_factor(code: str, label: str, points: int, severity: str = "moderate") -> None:
        nonlocal score
        score += int(points)
        factors.append({"code": code, "label": label, "points": int(points), "severity": severity})

    account_environment = _clean_text(getattr(account, "account_environment", None) or getattr(row, "account_environment", None) or request_state.get("account_environment") or "paper").lower()
    if account_environment == "live":
        add_factor("live_account", "Live linked account requires stronger review.", 18, "high")

    instrument_type = _clean_text(getattr(row, "instrument_type", None) or request_state.get("instrument_type") or "listed_option").lower()
    if instrument_type == "listed_option":
        add_factor("listed_option", "Listed options require quote, liquidity, and max-loss review.", 8, "moderate")

    for blocker in _safe_list(route_state.get("block_reasons"))[:4]:
        add_factor("route_blocker", _clean_text(blocker) or "Route blocker present.", 16, "high")

    if _clean_text(liquidity_state.get("status")).lower() == "blocked":
        add_factor("liquidity_blocked", "Liquidity or quote review is blocked.", 14, "high")
    for check in _safe_list(liquidity_state.get("failed_checks"))[:3]:
        add_factor("option_quality_check", f"Option quality check failed: {_clean_text(check)}", 8, "high")

    risk_percent = _coerce_trade_float(request_state.get("risk_percent"))
    if risk_percent is not None and risk_percent > 1.0:
        add_factor("risk_percent", f"Risk percent is above 1%: {risk_percent:g}%.", 10 if risk_percent <= 2 else 18, "moderate" if risk_percent <= 2 else "high")

    account_size = _coerce_trade_float(request_state.get("account_size"))
    position_cost = _coerce_trade_float(risk_state.get("position_cost") or position_state.get("total_position_cost") or position_state.get("position_cost"))
    if account_size and position_cost and position_cost / account_size > 0.10:
        add_factor("position_size", "Position cost is more than 10% of stated account size.", 12, "high")

    if bool(report_state.get("event_risk")):
        add_factor("event_risk", "Recommendation carries event-risk context.", 10, "moderate")

    if getattr(row, "status", "") == "submission_failed":
        add_factor("submission_failed", "Prior submission failed and needs cleanup review.", 18, "high")

    score = max(0, min(score, 100))
    band = "low" if score < 35 else "moderate" if score < 65 else "high" if score < 85 else "critical"
    checklist = [
        {
            "key": "linked_account_authorized",
            "label": "Linked account authorization is active",
            "status": "pass" if getattr(account, "connection_status", None) == "connected" else "missing",
            "detail": "Client account is connected through the linked-account lane.",
        },
        {
            "key": "order_ticket_frozen",
            "label": "Order ticket is frozen",
            "status": "pass" if bool(_safe_dict(getattr(row, "order_ticket_json", None))) or bool(request_state) else "missing",
            "detail": "Ticker, instrument, order type, and sizing are preserved with the case.",
        },
        {
            "key": "strategy_basis_frozen",
            "label": "Strategy release basis is frozen",
            "status": "pass" if bool(strategy_release_snapshot) else "missing",
            "detail": "Model, route, and risk basis are captured for later review.",
        },
        {
            "key": "risk_snapshot_captured",
            "label": "Pre-trade risk snapshot is captured",
            "status": "pass" if bool(risk_state) else "missing",
            "detail": "Account size, risk percent, cost, target, and invalidation are included.",
        },
        {
            "key": "route_review_clean",
            "label": "Route blockers reviewed",
            "status": "pass" if not _safe_list(route_state.get("block_reasons")) else "blocked",
            "detail": route_state.get("detail") or "No route blockers captured.",
        },
        {
            "key": "liquidity_review",
            "label": "Liquidity and quote review",
            "status": "pass" if _clean_text(liquidity_state.get("status")).lower() in {"pass", "review", ""} else "blocked",
            "detail": "Option quote and spread checks are captured when relevant.",
        },
        {
            "key": "human_rationale",
            "label": "Human decision rationale",
            "status": "missing",
            "detail": "Approval, rejection, or conditional approval should include human rationale.",
        },
    ]
    return serialize_value(
        {
            "schema_version": _BROKER_CASE_SCHEMA_VERSION,
            "case_id": _broker_case_id(row) if row is not None else f"CTC-{uuid4().hex[:10].upper()}",
            "risk_score": score,
            "risk_band": band,
            "risk_factors": factors,
            "verification_checklist": checklist,
            "source_inspirations": ["vendor_risk_controls", "deal_exception_review", "covenantos_release_basis"],
        }
    )


def _build_client_trade_case_snapshot(
    row: TradeApprovalIntent,
    *,
    audit_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = _safe_dict(row.metadata_json)
    stored_case = _safe_dict(metadata.get("broker_case"))
    pre_trade_risk = _safe_dict(metadata.get("pre_trade_risk"))
    route_eligibility = _safe_dict(metadata.get("route_eligibility"))
    liquidity_execution = _safe_dict(metadata.get("liquidity_execution"))
    strategy_snapshot = _build_strategy_release_snapshot(row)
    generated_case = _build_broker_case_metadata(
        row,
        pre_trade_risk=pre_trade_risk,
        liquidity_execution=liquidity_execution,
        route_eligibility=route_eligibility,
        strategy_release_snapshot=strategy_snapshot,
    )
    stored_case = {**generated_case, **stored_case}
    checklist = [dict(item) for item in _safe_list(stored_case.get("verification_checklist"))]
    decision_conditions = _normalize_text_items(_safe_dict(stored_case.get("latest_decision")).get("conditions") or metadata.get("conditions"))
    rationale_quality = _build_rationale_quality(row.approval_note, row.rejection_reason, decision_conditions)
    for item in checklist:
        if item.get("key") == "human_rationale":
            item["status"] = "pass" if rationale_quality.get("status") == "pass" else rationale_quality.get("status")
            item["detail"] = "; ".join(rationale_quality.get("missing_items") or []) or "Human rationale is sufficient for the current decision state."
    missing_items = [
        str(item.get("label") or item.get("key"))
        for item in checklist
        if str(item.get("status") or "").lower() in {"missing", "blocked", "warning"}
    ]
    age_hours = _intent_age_hours(row)
    stale = bool(age_hours is not None and age_hours >= _BROKER_STALE_RECOMMENDATION_HOURS and row.status in {"pending_approval", "conditionally_approved"})
    status = str(row.status or "").strip().lower()
    if status == "submitted":
        next_action = "Monitor broker order status and preserve the submitted packet."
    elif status == "conditionally_approved":
        next_action = "Resolve the listed conditions, then approve and submit or reject."
    elif status == "rejected":
        next_action = "Archive the rejected case with rationale and audit history."
    elif status == "expired":
        next_action = "Restage the trade if the recommendation is still current."
    elif stale:
        next_action = "Refresh market data and restage before client submission."
    elif missing_items:
        next_action = "Complete review items before approving or rejecting."
    else:
        next_action = "Approve and submit, reject, or add conditional approval."
    return serialize_value(
        {
            **stored_case,
            "case_id": stored_case.get("case_id") or _broker_case_id(row),
            "verification_checklist": checklist,
            "missing_items": missing_items,
            "rationale_quality": rationale_quality,
            "conditions": decision_conditions,
            "queue_priority": _broker_queue_priority(stored_case, stale=stale, status=status),
            "stale": stale,
            "age_hours": round(age_hours, 2) if age_hours is not None else None,
            "next_action": next_action,
            "latest_decision": stored_case.get("latest_decision") or {},
            "audit_event_count": len(audit_events or []),
            "pre_trade_risk": pre_trade_risk,
            "route_eligibility": route_eligibility,
            "liquidity_execution": liquidity_execution,
        }
    )


def _broker_queue_priority(case: dict[str, Any], *, stale: bool, status: str) -> str:
    band = _clean_text(case.get("risk_band")).lower()
    if status == "submission_failed":
        return "submit_repair"
    if stale:
        return "refresh_required"
    if band in {"critical", "high"} and status in {"pending_approval", "conditionally_approved"}:
        return "senior_review"
    if status == "pending_approval":
        return "ready_now"
    if status == "conditionally_approved":
        return "condition_follow_up"
    return "monitor"


def _build_trust_packet_summary(
    row: TradeApprovalIntent,
    *,
    broker_case: dict[str, Any],
    audit_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = _safe_dict(row.metadata_json)
    decision_review = build_decision_review_snapshot(row)
    evidence_register = build_evidence_register_snapshot(row)
    decision_readiness = _safe_dict(decision_review.get("readiness"))
    required = {
        "strategy_release": bool(_build_strategy_release_snapshot(row)),
        "risk_snapshot": bool(metadata.get("pre_trade_risk") or broker_case.get("pre_trade_risk")),
        "order_ticket": bool(row.order_ticket_json or row.broker_submit_payload_json),
        "decision_review": bool(decision_readiness.get("ready_for_final_decision")),
        "evidence_register": not bool(evidence_register.get("missing_items")),
        "audit_timeline": bool(audit_events),
    }
    packet_ready = all(required.values())
    return {
        "packet_version": _BROKER_TRUST_PACKET_VERSION,
        "packet_ready": packet_ready,
        "missing_sections": [key for key, ready in required.items() if not ready],
        "export_filename": f"broker-trust-packet-{row.ticker}-{_broker_case_id(row)}.json".lower(),
    }


def _build_similar_case_signals(row: TradeApprovalIntent, peers: list[TradeApprovalIntent]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    for peer in peers:
        if peer.id == row.id:
            continue
        same_ticker = str(peer.ticker or "").upper() == str(row.ticker or "").upper()
        same_instrument = str(peer.instrument_type or "") == str(row.instrument_type or "")
        if not same_ticker and not same_instrument:
            continue
        signals.append(
            {
                "intent_id": peer.id,
                "case_id": _broker_case_id(peer),
                "ticker": peer.ticker,
                "status": peer.status,
                "relationship": "same_ticker" if same_ticker else "same_instrument",
                "created_at": peer.created_at.isoformat() if peer.created_at else None,
            }
        )
        if len(signals) >= 3:
            break
    return signals


def _build_broker_ops_dashboard(items: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(item.get("status") or "").strip().lower() for item in items)
    risk_counts = Counter(str(_safe_dict(item.get("broker_case")).get("risk_band") or "unknown").strip().lower() for item in items)
    stale_items = [item for item in items if _safe_dict(item.get("broker_case")).get("stale")]
    packet_ready_items = [item for item in items if _safe_dict(item.get("trust_packet_summary")).get("packet_ready")]
    blocked_items = [
        item for item in items
        if str(_safe_dict(item.get("broker_case")).get("risk_band") or "").lower() in {"high", "critical"}
        or bool(_safe_dict(_safe_dict(item.get("broker_case")).get("route_eligibility")).get("block_reasons"))
    ]
    return {
        "pending_approval_count": status_counts.get("pending_approval", 0),
        "conditionally_approved_count": status_counts.get("conditionally_approved", 0),
        "submitted_count": status_counts.get("submitted", 0),
        "rejected_count": status_counts.get("rejected", 0),
        "submission_failed_count": status_counts.get("submission_failed", 0),
        "stale_recommendation_count": len(stale_items),
        "blocked_or_high_risk_count": len(blocked_items),
        "audit_export_ready_count": len(packet_ready_items),
        "risk_band_counts": dict(risk_counts),
        "next_actions": [
            {
                "intent_id": item.get("id"),
                "case_id": _safe_dict(item.get("broker_case")).get("case_id"),
                "ticker": item.get("ticker"),
                "priority": _safe_dict(item.get("broker_case")).get("queue_priority"),
                "detail": _safe_dict(item.get("broker_case")).get("next_action"),
            }
            for item in items[:6]
        ],
    }


def _store_broker_case_review(
    intent: TradeApprovalIntent,
    *,
    action: str,
    actor: User | None,
    note: str | None = None,
    reason: str | None = None,
    conditions: list[str] | None = None,
) -> None:
    metadata = _safe_dict(intent.metadata_json)
    broker_case = _safe_dict(metadata.get("broker_case"))
    broker_case.setdefault("case_id", _broker_case_id(intent))
    broker_case["latest_decision"] = {
        "action": action,
        "actor_email": getattr(actor, "email", None),
        "note": _clean_text(note) or None,
        "reason": _clean_text(reason) or None,
        "conditions": _normalize_text_items(conditions),
        "recorded_at": _utc_now().isoformat(),
    }
    metadata["broker_case"] = broker_case
    intent.metadata_json = serialize_value(metadata)


def _build_trade_intent_broker_payload(
    *,
    request: OpenTradeRequest,
    position: dict[str, Any],
    order_ticket: dict[str, Any],
) -> dict[str, Any]:
    quantity = float(position.get("suggested_contracts") or 0.0)
    side = str(getattr(request, "broker_side", "buy") or "buy").strip().lower() or "buy"
    if str(request.instrument_type or "listed_option").strip().lower() == "equity":
        return build_alpaca_equity_order_payload(
            request,
            ticker=str(request.ticker or "").strip().upper(),
            quantity=quantity,
            client_order_id=str(order_ticket.get("order_id") or "").strip() or None,
            side=side,
        )
    option_strategy = _normalize_option_strategy(getattr(request, "option_strategy", None))
    if option_strategy != "long_option":
        raise ValidationServiceError("Only long-option single-leg tickets can be converted to Alpaca option orders.")
    if side != "buy":
        raise ValidationServiceError("Long-option tickets must use buy-to-open routing.")
    contract_symbol = str(
        request.contract_symbol
        or order_ticket.get("contract_symbol")
        or ""
    ).strip().upper()
    return build_alpaca_option_order_payload(
        request,
        contract_symbol=contract_symbol,
        quantity=quantity,
        client_order_id=str(order_ticket.get("order_id") or "").strip() or None,
        side=side,
    )


def _normalize_option_strategy(value: Any) -> str:
    normalized = str(value or "long_option").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"", "single_leg", "long_debit", "long_single_leg", "buy_to_open"}:
        return "long_option"
    if normalized in {"long_option", "short_premium", "vertical_spread"}:
        return normalized
    raise ValidationServiceError(f"Unsupported option strategy '{value}'.")


def _validate_instrument_strategy_request(request: OpenTradeRequest | ReplaceOrderRequest) -> None:
    instrument_type = str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower()
    if instrument_type == "equity":
        if getattr(request, "option_strategy", None):
            raise ValidationServiceError("Option structure fields are only valid for listed-option tickets.")
        return
    if instrument_type != "listed_option":
        raise ValidationServiceError("Unsupported instrument type for this trade ticket.")

    option_strategy = _normalize_option_strategy(getattr(request, "option_strategy", None))
    if option_strategy == "short_premium":
        raise ValidationServiceError(
            "Short premium option tickets are review-only until margin, assignment, and buy-to-close controls are enabled."
        )
    if option_strategy == "vertical_spread":
        raise ValidationServiceError(
            "Vertical spread tickets require multi-leg validation and routing before submission."
        )
    if str(getattr(request, "broker_side", "buy") or "buy").strip().lower() != "buy":
        raise ValidationServiceError("Long option tickets must use buy-to-open routing.")


def _build_explicit_contract_override(request: OpenTradeRequest) -> dict[str, Any] | None:
    if str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower() != "listed_option":
        return None
    contract_symbol = str(getattr(request, "contract_symbol", None) or "").strip().upper()
    contract_mid = _coerce_trade_float(getattr(request, "contract_mid", None))
    if not contract_symbol or contract_mid is None or contract_mid <= 0:
        return None
    bid = _coerce_trade_float(getattr(request, "contract_bid", None))
    ask = _coerce_trade_float(getattr(request, "contract_ask", None))
    spread_pct = _coerce_trade_float(getattr(request, "contract_spread_pct", None))
    if spread_pct is None and bid is not None and ask is not None and contract_mid > 0:
        spread_pct = float((ask - bid) / contract_mid)
    return {
        "contract_symbol": contract_symbol,
        "expiration": str(getattr(request, "contract_expiration", None) or "").strip() or None,
        "strike": _coerce_trade_float(getattr(request, "contract_strike", None)),
        "bid": bid,
        "ask": ask,
        "mid": contract_mid,
        "last_price": contract_mid,
        "implied_volatility": float("nan"),
        "volume": int(_coerce_trade_float(getattr(request, "contract_volume", None)) or 0),
        "open_interest": int(_coerce_trade_float(getattr(request, "contract_open_interest", None)) or 0),
        "in_the_money": False,
        "spread_pct": spread_pct,
        "quote_timestamp": str(getattr(request, "contract_quote_timestamp", None) or "").strip(),
    }


def _apply_explicit_contract_override(report: dict[str, Any], request: OpenTradeRequest) -> dict[str, Any]:
    override = _build_explicit_contract_override(request)
    if override is None:
        return report
    report = dict(report or {})
    option_plan = dict(report.get("option_plan") or {})
    option_plan["recommended_contract"] = override
    if getattr(request, "option_right", None):
        option_plan["option_side"] = str(request.option_right or "").strip().upper()
    report["option_plan"] = option_plan
    return report


def _prepare_trade_request_context(
    request: OpenTradeRequest,
    *,
    current_user: Any | None = None,
    enforce_option_execution_quality: bool = True,
) -> dict[str, Any]:
    open_trades = _scoped_open_trades(current_user)
    existing_pending = _find_existing_pending_order_for_ticker(request.ticker, current_user=current_user)
    if existing_pending is not None:
        raise ValidationServiceError(
            f"A working order already exists for {request.ticker}. Replace, fill, or cancel it before routing another one."
        )
    pending_orders = _scoped_pending_orders(current_user)
    closed_trades = _scoped_closed_trades(current_user)
    analysis = analyze_market(
        AnalyzeRequest(
            ticker=request.ticker,
            interval=request.interval,
            horizon=request.horizon,
            include_history=False,
            include_live_price=True,
        ),
        current_user=current_user,
    )
    report = _apply_explicit_contract_override(analysis["report"], request)
    analysis = {**analysis, "report": report}
    live_price = request.live_price if request.live_price is not None else analysis.get("live_price")
    if live_price is None or float(live_price) <= 0:
        raise ValidationServiceError("A valid live price is required to open a trade.")
    _validate_instrument_strategy_request(request)
    _validate_directional_entry_request(request, report)

    if request.instrument_type == "equity":
        if request.requested_quantity is not None:
            position = _build_requested_equity_position_preview(
                report,
                float(live_price),
                float(request.requested_quantity),
                fractional_shares_only=bool(request.fractional_shares_only),
            )
        else:
            position = _build_equity_position_preview(
                report,
                float(live_price),
                float(request.account_size),
                float(request.risk_percent),
                fractional_shares_only=bool(request.fractional_shares_only),
                max_notional_per_trade=request.max_notional_per_trade,
            )
    else:
        if request.requested_quantity is not None:
            raise ValidationServiceError("Requested quantity overrides are only supported for equity tickets.")
        position = sdm.calculate_position_sizing(report, request.account_size, request.risk_percent)
    if float(position.get("suggested_contracts", 0) or 0.0) <= 0:
        raise ValidationServiceError(str(position.get("reason", "Trade sizing did not produce a valid position.")))

    if enforce_option_execution_quality:
        option_execution_review = _validate_option_execution_request(
            request,
            report=report,
            position=position,
        )
    else:
        option_execution_review = _build_option_execution_review(
            request,
            report=report,
            position=position,
        )
    capital_preservation = _validate_capital_preservation_request(
        request,
        position=position,
        open_trades=open_trades,
        pending_orders=pending_orders,
        closed_trades=closed_trades,
    )
    return {
        "analysis": analysis,
        "report": report,
        "live_price": float(live_price),
        "position": position,
        "option_execution_review": option_execution_review,
        "capital_preservation": capital_preservation,
        "open_trades": open_trades,
        "pending_orders": pending_orders,
        "closed_trades": closed_trades,
    }


def _build_pre_trade_risk_snapshot(
    request: OpenTradeRequest,
    *,
    report: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
    live_price: float | None = None,
    option_execution_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    position = dict(position or {})
    review = dict(option_execution_review or {})
    instrument_type = str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower()
    option_strategy = _normalize_option_strategy(getattr(request, "option_strategy", None)) if instrument_type == "listed_option" else None
    units = _coerce_trade_float(position.get("suggested_contracts"))
    total_position_cost = _coerce_trade_float(position.get("total_position_cost"))
    planned_max_loss = _coerce_trade_float(position.get("total_max_loss"))
    effective_risk = _coerce_trade_float(position.get("effective_max_risk_dollars"))
    entry_unit_price = (
        _coerce_trade_float(getattr(request, "limit_price", None))
        if str(getattr(request, "order_type", "") or "").strip().lower() == "limit"
        else None
    )
    entry_unit_price = (
        entry_unit_price
        or _coerce_trade_float(review.get("expected_fill_price"))
        or _coerce_trade_float(review.get("mid"))
        or _coerce_trade_float(position.get("entry_unit_price"))
        or _coerce_trade_float(position.get("contract_mid"))
        or _coerce_trade_float(live_price)
    )
    option_plan = dict((report or {}).get("option_plan") or {})
    invalidation_price = _coerce_trade_float(option_plan.get("invalidation_price"))
    target_price = _coerce_trade_float(option_plan.get("expected_underlying_target"))

    snapshot: dict[str, Any] = {
        "instrument_type": instrument_type,
        "option_strategy": option_strategy,
        "units": units,
        "unit_label": position.get("unit_label") or ("contracts" if instrument_type == "listed_option" else "shares"),
        "entry_unit_price": entry_unit_price,
        "position_cost": total_position_cost,
        "planned_max_loss": planned_max_loss,
        "effective_risk_budget": effective_risk,
        "account_size": _coerce_trade_float(getattr(request, "account_size", None)),
        "risk_percent": _coerce_trade_float(getattr(request, "risk_percent", None)),
        "invalidation_price": invalidation_price,
        "target_price": target_price,
        "contract_multiplier": 100 if instrument_type == "listed_option" else 1,
        "expected_fill_basis": (
            "limit"
            if str(getattr(request, "order_type", "") or "").strip().lower() == "limit"
            else ("contract_quote" if instrument_type == "listed_option" else "live_price")
        ),
    }

    if instrument_type == "listed_option":
        strike = _coerce_trade_float(review.get("strike") or getattr(request, "contract_strike", None))
        option_right = str(
            review.get("option_right") or getattr(request, "option_right", None) or option_plan.get("option_side") or ""
        ).strip().lower()
        premium = entry_unit_price
        breakeven = None
        if strike is not None and premium is not None:
            breakeven = strike - premium if option_right == "put" else strike + premium
        snapshot.update(
            {
                "option_right": option_right or None,
                "contract_symbol": review.get("contract_symbol") or getattr(request, "contract_symbol", None),
                "expiration": review.get("expiration") or getattr(request, "contract_expiration", None),
                "strike": strike,
                "breakeven": breakeven,
                "premium_at_risk": total_position_cost,
                "theoretical_max_loss": total_position_cost if option_strategy == "long_option" else None,
            }
        )
    else:
        snapshot.update(
            {
                "equity_notional": total_position_cost,
                "stop_risk": planned_max_loss,
                "breakeven": entry_unit_price,
            }
        )

    return snapshot


def _build_route_eligibility_snapshot(
    request: OpenTradeRequest,
    *,
    position: dict[str, Any] | None = None,
    option_execution_review: dict[str, Any] | None = None,
    capital_preservation: dict[str, Any] | None = None,
    blocked_reason: str | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    instrument_type = str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower()
    option_strategy = _normalize_option_strategy(getattr(request, "option_strategy", None)) if instrument_type == "listed_option" else None
    order_type = str(getattr(request, "order_type", "market") or "market").strip().lower()
    time_in_force = str(getattr(request, "time_in_force", "day") or "day").strip().lower()

    if blocked_reason:
        blockers.append(str(blocked_reason))
    if instrument_type == "listed_option":
        if option_strategy != "long_option":
            blockers.append("Only single-leg long calls and puts are executable in v1.")
        if str(getattr(request, "broker_side", "buy") or "buy").strip().lower() != "buy":
            blockers.append("Option entries must be buy-to-open; sells are reserved for sell-to-close exits.")
        if time_in_force == "day_ext":
            blockers.append("Listed options are regular-session only in this flow.")
        if order_type == "trailing_stop":
            blockers.append("Listed-option trailing stops are not enabled for paper routing.")
        if option_execution_review is None:
            blockers.append("Listed-option routing needs a refreshed contract quote.")
        else:
            failed_checks = [
                str(check.get("label") or check.get("key") or "Contract check")
                for check in option_execution_review.get("checks", [])
                if str(check.get("status") or "").lower() == "fail"
            ]
            blockers.extend(f"Option quality check failed: {label}." for label in failed_checks)
    else:
        if order_type == "market":
            warnings.append("Market equity orders can consume the spread; use a limit when quote quality is uncertain.")
        if time_in_force == "day_ext":
            warnings.append("Extended-hours equity routing depends on thinner displayed liquidity.")

    if position:
        if not bool(position.get("affordable", True)):
            blockers.append(str(position.get("reason") or "Sizing is not affordable under the current ticket rules."))
        if _coerce_trade_float(position.get("suggested_contracts")) is None or float(position.get("suggested_contracts") or 0.0) <= 0:
            blockers.append(str(position.get("reason") or "Sizing did not produce a routeable quantity."))

    if capital_preservation:
        projected_cost = _coerce_trade_float(capital_preservation.get("projected_position_cost"))
        max_notional = _coerce_trade_float(getattr(request, "max_notional_per_trade", None))
        if max_notional is not None and projected_cost is not None and projected_cost > max_notional:
            blockers.append("Projected notional exceeds the ticket maximum.")

    deduped_blockers = list(dict.fromkeys([item for item in blockers if item]))
    deduped_warnings = list(dict.fromkeys([item for item in warnings if item]))
    allowed = not deduped_blockers
    return {
        "allowed": allowed,
        "status": "ready" if allowed else "blocked",
        "block_reasons": deduped_blockers,
        "warnings": deduped_warnings,
        "detail": "Pre-trade route checks passed." if allowed else deduped_blockers[0],
    }


def _build_liquidity_execution_snapshot(
    request: OpenTradeRequest,
    *,
    option_execution_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instrument_type = str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower()
    order_type = str(getattr(request, "order_type", "market") or "market").strip().lower()
    time_in_force = str(getattr(request, "time_in_force", "day") or "day").strip().lower()
    if instrument_type == "listed_option":
        review = dict(option_execution_review or {})
        failed = [
            str(check.get("key") or check.get("label") or "quality_check")
            for check in review.get("checks", [])
            if str(check.get("status") or "").lower() == "fail"
        ]
        return {
            "status": "pass" if review.get("status") == "pass" else "blocked",
            "quote_age_seconds": review.get("quote_age_seconds"),
            "spread_pct": review.get("spread_pct"),
            "volume": review.get("volume"),
            "open_interest": review.get("open_interest"),
            "contract_symbol": review.get("contract_symbol"),
            "quote_source": "provider_chain_refresh" if review.get("contract_symbol") else "missing",
            "failed_checks": failed,
            "order_type": order_type,
            "time_in_force": time_in_force,
        }
    warnings = []
    if order_type == "market":
        warnings.append("market_order_spread_consumption")
    if time_in_force == "day_ext":
        warnings.append("extended_hours_liquidity")
    return {
        "status": "review" if warnings else "pass",
        "warnings": warnings,
        "order_type": order_type,
        "time_in_force": time_in_force,
    }


def preview_trade_from_request(
    request: OpenTradeRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    del db
    try:
        context = _prepare_trade_request_context(
            request,
            current_user=current_user,
            enforce_option_execution_quality=False,
        )
    except ValidationServiceError as exc:
        return {
            "preview": True,
            "blocked": True,
            "ticker": str(request.ticker or "").strip().upper(),
            "instrument_type": request.instrument_type,
            "option_strategy": request.option_strategy,
            "route_eligibility": _build_route_eligibility_snapshot(
                request,
                blocked_reason=str(exc),
            ),
            "pre_trade_risk": _build_pre_trade_risk_snapshot(request),
            "liquidity_execution": _build_liquidity_execution_snapshot(request),
            "option_execution_review": None,
            "capital_preservation": None,
        }

    report = context["report"]
    position = context["position"]
    option_execution_review = context["option_execution_review"]
    capital_preservation = context["capital_preservation"]
    route_eligibility = _build_route_eligibility_snapshot(
        request,
        position=position,
        option_execution_review=option_execution_review,
        capital_preservation=capital_preservation,
    )
    order_ticket = _build_order_ticket_payload(request, report=report)
    return {
        "preview": True,
        "blocked": not route_eligibility["allowed"],
        "ticker": str(request.ticker or "").strip().upper(),
        "instrument_type": request.instrument_type,
        "option_strategy": request.option_strategy,
        "live_price": context["live_price"],
        "analysis": serialize_value(report),
        "position": serialize_value(position),
        "order_ticket": serialize_value(order_ticket),
        "option_execution_review": serialize_value(option_execution_review),
        "capital_preservation": serialize_value(capital_preservation),
        "pre_trade_risk": serialize_value(
            _build_pre_trade_risk_snapshot(
                request,
                report=report,
                position=position,
                live_price=context["live_price"],
                option_execution_review=option_execution_review,
            )
        ),
        "liquidity_execution": serialize_value(
            _build_liquidity_execution_snapshot(
                request,
                option_execution_review=option_execution_review,
            )
        ),
        "route_eligibility": serialize_value(route_eligibility),
    }


def _build_order_event_label(event_key: str, status: str) -> str:
    normalized_key = str(event_key or "").strip().lower()
    normalized_status = str(status or "").strip().lower()
    if normalized_key == "order.submitted":
        return "Submitted"
    if normalized_key == "order.accepted":
        return "Working"
    if normalized_key == "order.replaced":
        return "Replaced"
    if normalized_key == "order.canceled" or normalized_status == "canceled":
        return "Canceled"
    if normalized_key == "order.filled":
        return "Filled"
    if normalized_key == "order.rejected" or normalized_status == "rejected":
        return "Rejected"
    if normalized_key == "order.closed" or normalized_status == "closed":
        return "Closed"
    if normalized_status == "working":
        return "Working"
    if normalized_status == "filled":
        return "Filled"
    if normalized_status == "open":
        return "Accepted"
    return normalized_status.replace("_", " ").title() or "Recorded"


def _serialize_order_event(row: OrderEventRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "trade_id": row.trade_id,
        "ticker": row.ticker,
        "event_key": row.event_key,
        "status": row.status,
        "label": _build_order_event_label(row.event_key, row.status),
        "order_type": row.order_type,
        "time_in_force": row.time_in_force,
        "route_state": row.route_state,
        "book_state": row.book_state,
        "detail": row.detail,
        "payload": serialize_value(row.payload_json or {}),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _serialize_pending_orders(
    rows: pd.DataFrame,
    latest_events: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    items = serialize_dataframe(rows)
    for row in items:
        trade_id = resolve_trade_identifier(row)
        row["trade_id"] = trade_id
        latest_event = latest_events.get(trade_id)
        if latest_event is not None:
            row["latest_order_event"] = latest_event
    items.sort(
        key=lambda row: str(
            row.get("updated_at")
            or row.get("submitted_at")
            or row.get("opened_at")
            or ""
        ),
        reverse=True,
    )
    return items


def _build_latest_order_event_lookup(order_events: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for item in list(order_events.get("items") or []):
        trade_id = str(item.get("trade_id") or "").strip()
        if trade_id and trade_id not in lookup:
            lookup[trade_id] = item
    return lookup


def get_pending_orders_snapshot(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
    ticker: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    pending_orders = _scoped_pending_orders(current_user)
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_order_id = str(order_id or "").strip()

    if not pending_orders.empty and normalized_ticker:
        pending_orders = pending_orders[
            pending_orders.get("ticker", pd.Series(dtype=str)).astype(str).str.upper() == normalized_ticker
        ]
    if not pending_orders.empty and normalized_order_id and "order_id" in pending_orders.columns:
        pending_orders = pending_orders[
            pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
        ]

    order_events = get_order_events_snapshot(db, current_user, limit=60, ticker=normalized_ticker or None)
    latest_lookup = _build_latest_order_event_lookup(order_events)
    items = _serialize_pending_orders(pending_orders, latest_lookup) if not pending_orders.empty else []
    return {
        "items": items,
        "count": len(items),
        "order_events": order_events,
    }


def _get_pending_order_row(order_id: str, *, current_user: Any | None = None) -> dict[str, Any] | None:
    pending_orders = _scoped_pending_orders(current_user)
    if pending_orders.empty or "order_id" not in pending_orders.columns:
        return None
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
        return None
    matches = pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
    if not matches.any():
        return None
    return pending_orders.loc[pending_orders.index[matches][0]].to_dict()


def _find_existing_pending_order_for_ticker(ticker: str, *, current_user: Any | None = None) -> dict[str, Any] | None:
    pending_orders = _scoped_pending_orders(current_user)
    if pending_orders.empty:
        return None
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return None
    matches = pending_orders.get("ticker", pd.Series(dtype=str)).astype(str).str.upper() == normalized_ticker
    if not matches.any():
        return None
    pending_orders = pending_orders.loc[matches]
    sort_columns = [column for column in ("updated_at", "submitted_at") if column in pending_orders.columns]
    if sort_columns:
        pending_orders = pending_orders.sort_values(by=sort_columns, ascending=False)
    return pending_orders.iloc[0].to_dict()


def _build_order_sync_change_flag(previous_order: dict[str, Any], next_order: dict[str, Any] | None, broker_status: str | None) -> bool:
    previous_status = str(previous_order.get("broker_status") or "").strip().lower()
    next_status = str(broker_status or "").strip().lower()
    previous_filled = serialize_value(previous_order.get("broker_filled_qty"))
    next_filled = serialize_value((next_order or {}).get("broker_filled_qty"))
    previous_price = serialize_value(previous_order.get("broker_filled_avg_price"))
    next_price = serialize_value((next_order or {}).get("broker_filled_avg_price"))
    return (
        previous_status != next_status
        or previous_filled != next_filled
        or previous_price != next_price
    )


def _build_sync_item(
    *,
    row: dict[str, Any],
    state: str,
    changed: bool,
    detail: str,
    broker_status: str | None = None,
    broker_order_id: str | None = None,
    slippage_dollars: float | None = None,
    slippage_bps: float | None = None,
) -> dict[str, Any]:
    return {
        "order_id": str(row.get("order_id") or "").strip() or None,
        "trade_id": resolve_trade_identifier(row),
        "ticker": str(row.get("ticker") or "").strip().upper() or "UNKNOWN",
        "state": state,
        "changed": bool(changed),
        "detail": detail,
        "broker_name": str(row.get("broker_name") or "").strip().lower() or None,
        "broker_order_id": broker_order_id or str(row.get("broker_order_id") or "").strip() or None,
        "broker_status": broker_status or str(row.get("broker_status") or "").strip().lower() or None,
        "slippage_dollars": slippage_dollars,
        "slippage_bps": slippage_bps,
    }


def get_order_events_snapshot(
    db: Session | None,
    current_user: Any | None,
    *,
    limit: int = 30,
    ticker: str | None = None,
    trade_id: str | None = None,
) -> dict[str, Any]:
    if db is None or current_user is None:
        return {"items": [], "count": 0, "status_counts": {}}

    tenant, _actor = _resolve_trade_actor_context(db, current_user)
    statement = (
        select(OrderEventRecord)
        .where(OrderEventRecord.tenant_id == tenant.id)
        .order_by(OrderEventRecord.created_at.desc(), OrderEventRecord.updated_at.desc())
        .limit(max(1, min(int(limit or 30), 100)))
    )
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_trade_id = str(trade_id or "").strip()
    if normalized_ticker:
        statement = statement.where(OrderEventRecord.ticker == normalized_ticker)
    if normalized_trade_id:
        statement = statement.where(OrderEventRecord.trade_id == normalized_trade_id)

    rows = db.execute(statement).scalars().all()
    items = [_serialize_order_event(row) for row in rows]
    return {
        "items": items,
        "count": len(items),
        "status_counts": dict(Counter(item["status"] for item in items)),
    }


def _record_order_event(
    db: Session | None,
    *,
    tenant: Tenant | None,
    actor: User | None,
    trade_id: str | None,
    ticker: str,
    event_key: str,
    status: str,
    order_type: str | None,
    time_in_force: str | None,
    route_state: str | None,
    book_state: str | None,
    detail: str,
    payload: dict[str, Any] | None = None,
    audit_event_type: str | None = None,
) -> OrderEventRecord | None:
    if db is None or tenant is None:
        return None

    row = OrderEventRecord(
        tenant=tenant,
        trade_id=str(trade_id or "").strip() or None,
        ticker=str(ticker or "").strip().upper() or "UNKNOWN",
        event_key=str(event_key or "").strip().lower() or "order.recorded",
        status=str(status or "").strip().lower() or "recorded",
        order_type=str(order_type or "").strip().lower() or None,
        time_in_force=str(time_in_force or "").strip().lower() or None,
        route_state=str(route_state or "").strip().lower() or None,
        book_state=str(book_state or "").strip().lower() or None,
        detail=str(detail or "").strip() or None,
        payload_json=serialize_value(payload or {}),
    )
    db.add(row)
    db.flush()

    if audit_event_type:
        record_audit_event(
            db,
            event_type=audit_event_type,
            tenant=tenant,
            user=actor,
            payload={
                "trade_id": row.trade_id,
                "ticker": row.ticker,
                "event_key": row.event_key,
                "status": row.status,
                "order_type": row.order_type,
                "time_in_force": row.time_in_force,
                "route_state": row.route_state,
                "book_state": row.book_state,
                "detail": row.detail,
            },
    )
    return row


def _build_order_ticket_payload(
    request: OpenTradeRequest | ReplaceOrderRequest,
    *,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instrument_type = str(getattr(request, "instrument_type", None) or "listed_option").strip().lower()
    option_plan = dict((report or {}).get("option_plan") or {})
    recommended_contract = dict(option_plan.get("recommended_contract") or {})
    option_side = str(option_plan.get("option_side") or "").strip().lower()
    contract_symbol = getattr(request, "contract_symbol", None)
    if not contract_symbol:
        contract_symbol = recommended_contract.get("contract_symbol")
    if instrument_type == "equity" and not contract_symbol:
        contract_symbol = f"EQUITY:{str(getattr(request, 'ticker', '') or '').strip().upper()}"
    route_family = str(getattr(request, "route_family", None) or "").strip().lower()
    if not route_family:
        route_family = "legacy"
    route_version = str(getattr(request, "route_version", None) or "").strip()
    if route_family == "current" and not route_version:
        route_version = "ranked_entry_v1"
    thesis_direction = str(getattr(request, "thesis_direction", None) or (report or {}).get("verdict") or "").strip().upper()
    route_correlation_id = _normalize_route_correlation_id(getattr(request, "route_correlation_id", None))
    broker_side = str(getattr(request, "broker_side", "buy") or "buy").strip().lower()
    if broker_side not in {"buy", "sell"}:
        broker_side = "buy"
    option_strategy = _normalize_option_strategy(getattr(request, "option_strategy", None)) if instrument_type == "listed_option" else None

    return {
        "instrument_type": instrument_type,
        "broker_side": broker_side,
        "option_strategy": option_strategy,
        "option_right": getattr(request, "option_right", None) or (option_side if option_side in {"call", "put"} else None),
        "contract_symbol": contract_symbol,
        "contract_expiration": getattr(request, "contract_expiration", None) or recommended_contract.get("expiration"),
        "contract_strike": getattr(request, "contract_strike", None) or recommended_contract.get("strike"),
        "contract_bid": recommended_contract.get("bid"),
        "contract_ask": recommended_contract.get("ask"),
        "contract_mid": recommended_contract.get("mid"),
        "contract_spread_pct": recommended_contract.get("spread_pct"),
        "contract_volume": recommended_contract.get("volume"),
        "contract_open_interest": recommended_contract.get("open_interest"),
        "contract_quote_timestamp": recommended_contract.get("quote_timestamp"),
        "order_type": request.order_type,
        "time_in_force": request.time_in_force,
        "limit_price": request.limit_price,
        "stop_price": request.stop_price,
        "trailing_percent": request.trailing_percent,
        "extended_hours": request.extended_hours,
        "tiny_account_mode": getattr(request, "tiny_account_mode", False),
        "fractional_shares_only": getattr(request, "fractional_shares_only", False),
        "route_family": route_family,
        "route_version": route_version,
        "route_correlation_id": route_correlation_id,
        "automation_entry_reason": str(getattr(request, "automation_entry_reason", None) or "").strip(),
        "thesis_direction": thesis_direction,
        "source": str(getattr(request, "source", None) or "").strip() or None,
        "portfolio_target_run_id": str(getattr(request, "portfolio_target_run_id", None) or "").strip() or None,
        "strategy_desk_key": str(getattr(request, "strategy_desk_key", None) or "").strip() or None,
        "desk_contributions": serialize_value(getattr(request, "desk_contributions", None) or []),
    }


def _coerce_trade_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if pd.notna(normalized) else None


def _parse_trade_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, "", "nan"):
        return None
    try:
        timestamp = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is None:
        return timestamp.tz_localize("UTC")
    return timestamp.tz_convert("UTC")


def _build_option_execution_review(
    request: OpenTradeRequest,
    *,
    report: dict[str, Any],
    position: dict[str, Any],
) -> dict[str, Any] | None:
    instrument_type = str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower()
    if instrument_type != "listed_option":
        return None

    option_plan = dict((report or {}).get("option_plan") or {})
    recommended_contract = dict(option_plan.get("recommended_contract") or {})
    requested_contract_symbol = str(getattr(request, "contract_symbol", None) or "").strip().upper()
    option_right = str(
        getattr(request, "option_right", None) or option_plan.get("option_side") or ""
    ).strip().lower()
    if requested_contract_symbol:
        refreshed_contract = sdm.get_contract_quote_from_chain(
            str(getattr(request, "ticker", "") or "").strip().upper(),
            requested_contract_symbol,
            option_side=option_right,
            expiration=getattr(request, "contract_expiration", None),
        )
        if refreshed_contract is not None:
            recommended_contract = dict(refreshed_contract)
            option_plan["recommended_contract"] = recommended_contract
            report["option_plan"] = option_plan
    contract_symbol = str(
        requested_contract_symbol
        or recommended_contract.get("contract_symbol")
        or ""
    ).strip()
    bid = _coerce_trade_float(recommended_contract.get("bid"))
    ask = _coerce_trade_float(recommended_contract.get("ask"))
    contract_mid = (
        _coerce_trade_float(position.get("contract_mid"))
        or _coerce_trade_float(recommended_contract.get("mid"))
    )
    spread_pct = _coerce_trade_float(recommended_contract.get("spread_pct"))
    volume = int(_coerce_trade_float(recommended_contract.get("volume")) or 0)
    open_interest = int(_coerce_trade_float(recommended_contract.get("open_interest")) or 0)
    quote_timestamp = _parse_trade_timestamp(recommended_contract.get("quote_timestamp"))
    quote_age_seconds = None
    if quote_timestamp is not None:
        quote_age_seconds = max(
            0.0,
            round(float((pd.Timestamp.now(tz="UTC") - quote_timestamp).total_seconds()), 1),
        )

    expected_fill_price = (
        _coerce_trade_float(getattr(request, "limit_price", None))
        if str(getattr(request, "order_type", "") or "").strip().lower() == "limit"
        else contract_mid
    ) or contract_mid

    checks = [
        {
            "key": "spread",
            "label": "Spread",
            "status": "pass" if spread_pct is not None and spread_pct <= _OPTION_MAX_SPREAD_PCT else "fail",
            "value": spread_pct,
            "threshold": _OPTION_MAX_SPREAD_PCT,
        },
        {
            "key": "quote_age_seconds",
            "label": "Quote age",
            "status": (
                "pass"
                if quote_age_seconds is not None and quote_age_seconds <= _OPTION_MAX_QUOTE_AGE_SECONDS
                else "fail"
            ),
            "value": quote_age_seconds,
            "threshold": _OPTION_MAX_QUOTE_AGE_SECONDS,
        },
        {
            "key": "volume",
            "label": "Volume",
            "status": "pass" if volume >= _OPTION_MIN_VOLUME else "fail",
            "value": volume,
            "threshold": _OPTION_MIN_VOLUME,
        },
        {
            "key": "open_interest",
            "label": "Open interest",
            "status": "pass" if open_interest >= _OPTION_MIN_OPEN_INTEREST else "fail",
            "value": open_interest,
            "threshold": _OPTION_MIN_OPEN_INTEREST,
        },
    ]
    failures = [check for check in checks if check["status"] == "fail"]
    review_status = "pass" if not failures else "fail"
    detail = "Contract spread, freshness, and participation are inside the enforced option route limits."
    if failures:
        detail = "Option route blocked because at least one contract quality check failed."

    return {
        "status": review_status,
        "detail": detail,
        "contract_symbol": contract_symbol or None,
        "expiration": str(
            getattr(request, "contract_expiration", None)
            or recommended_contract.get("expiration")
            or ""
        ).strip()
        or None,
        "strike": _coerce_trade_float(
            getattr(request, "contract_strike", None) or recommended_contract.get("strike")
        ),
        "option_right": option_right or None,
        "bid": bid,
        "ask": ask,
        "mid": contract_mid,
        "spread_pct": spread_pct,
        "volume": volume,
        "open_interest": open_interest,
        "quote_timestamp": quote_timestamp.isoformat() if quote_timestamp is not None else None,
        "quote_age_seconds": quote_age_seconds,
        "expected_fill_price": expected_fill_price,
        "actual_fill_price": None,
        "fill_slippage_bps": None,
        "fill_slippage_dollars": None,
        "thresholds": {
            "max_spread_pct": _OPTION_MAX_SPREAD_PCT,
            "max_quote_age_seconds": _OPTION_MAX_QUOTE_AGE_SECONDS,
            "min_volume": _OPTION_MIN_VOLUME,
            "min_open_interest": _OPTION_MIN_OPEN_INTEREST,
        },
        "checks": checks,
    }


def _validate_option_execution_request(
    request: OpenTradeRequest,
    *,
    report: dict[str, Any],
    position: dict[str, Any],
) -> dict[str, Any] | None:
    review = _build_option_execution_review(request, report=report, position=position)
    if review is None:
        return None

    if not review.get("contract_symbol"):
        raise ValidationServiceError("Listed-option routing requires a resolved contract symbol.")
    if _coerce_trade_float(review.get("mid")) is None or float(review["mid"]) <= 0:
        raise ValidationServiceError("Listed-option routing requires a valid contract mid price.")
    if _coerce_trade_float(review.get("spread_pct")) is None:
        raise ValidationServiceError("Listed-option routing requires a valid contract spread.")
    if float(review["spread_pct"]) > _OPTION_MAX_SPREAD_PCT:
        raise ValidationServiceError(
            f"Listed-option routing blocks contracts wider than {_OPTION_MAX_SPREAD_PCT:.0%} spread."
        )
    if _coerce_trade_float(review.get("quote_age_seconds")) is None:
        raise ValidationServiceError("Listed-option routing requires a fresh contract quote timestamp.")
    if float(review["quote_age_seconds"]) > _OPTION_MAX_QUOTE_AGE_SECONDS:
        raise ValidationServiceError(
            "Listed-option routing blocked a stale contract quote. Refresh the contract before routing."
        )
    if int(review.get("volume") or 0) < _OPTION_MIN_VOLUME:
        raise ValidationServiceError(
            f"Listed-option routing requires at least {_OPTION_MIN_VOLUME} contracts of volume."
        )
    if int(review.get("open_interest") or 0) < _OPTION_MIN_OPEN_INTEREST:
        raise ValidationServiceError(
            f"Listed-option routing requires at least {_OPTION_MIN_OPEN_INTEREST} open interest."
        )
    return review


def _floor_units_to_increment(value: float, increment: float) -> float:
    normalized_value = float(value or 0.0)
    normalized_increment = float(increment or 0.0)
    if normalized_value <= 0 or normalized_increment <= 0:
        return 0.0
    steps = int(normalized_value / normalized_increment)
    return float(steps * normalized_increment)


def _build_equity_position_preview(
    report: dict[str, Any],
    live_price: float,
    account_size: float,
    risk_percent: float,
    *,
    fractional_shares_only: bool = False,
    max_notional_per_trade: float | None = None,
) -> dict[str, Any]:
    option_plan = report.get("option_plan") or {}
    invalidation_price = serialize_value(option_plan.get("invalidation_price"))
    invalidation_price = float(invalidation_price) if invalidation_price not in (None, "", "nan") else float("nan")
    regime_strength_score = serialize_value((report.get("forecast") or {}).get("regime_strength_score"))
    try:
        regime_strength_score = float(regime_strength_score)
    except (TypeError, ValueError):
        regime_strength_score = 0.5

    if not pd.notna(invalidation_price):
        raise ValidationServiceError("An invalidation price is required to size an equity ticket.")

    max_risk_dollars = float(account_size) * (float(risk_percent) / 100.0)
    risk_budget_multiplier = float(max(0.7, min(1.0, 0.65 + regime_strength_score * 0.5)))
    effective_max_risk_dollars = float(max_risk_dollars * risk_budget_multiplier)
    max_loss_per_share = abs(float(live_price) - float(invalidation_price))
    if max_loss_per_share <= 0:
        raise ValidationServiceError("Equity sizing requires live price and invalidation to be different.")

    if fractional_shares_only:
        suggested_shares = float(effective_max_risk_dollars / max_loss_per_share)
        suggested_shares = min(suggested_shares, float(account_size) / float(live_price))
        if max_notional_per_trade is not None and float(max_notional_per_trade) > 0:
            suggested_shares = min(suggested_shares, float(max_notional_per_trade) / float(live_price))
        suggested_shares = _floor_units_to_increment(suggested_shares, 0.001)
        minimum_units = 0.001
    else:
        suggested_shares = float(int(effective_max_risk_dollars // max_loss_per_share))
        minimum_units = 1.0

    total_position_cost = float(suggested_shares * float(live_price))
    total_max_loss = float(suggested_shares * max_loss_per_share)
    affordable = suggested_shares >= minimum_units and total_position_cost <= float(account_size)
    if max_notional_per_trade is not None and float(max_notional_per_trade) > 0:
        affordable = affordable and total_position_cost <= float(max_notional_per_trade)

    if report.get("trade_decision") == "REJECT":
        status = "SKIP TRADE"
        reason = str(report.get("reject_reason") or "Setup rejected.")
        affordable = False
    elif suggested_shares < minimum_units:
        status = "SKIP TRADE"
        reason = (
            "Fractional share sizing does not fit the current risk rule."
            if fractional_shares_only
            else "Share sizing does not fit the current risk rule."
        )
    elif not fractional_shares_only and total_position_cost > float(account_size):
        status = "TOO LARGE"
        reason = "Share cost exceeds the account size."
        affordable = False
    elif report.get("trade_decision") == "PASS":
        status = "PASS"
        reason = str(report.get("reject_reason") or "Setup is not strong enough.")
        affordable = False
    else:
        status = "VALID TRADE"
        reason = "Sizing is ready for review."

    return {
        "contract_mid": float(live_price) / 100.0,
        "estimated_cost_per_contract": float(live_price),
        "max_loss_per_contract": float(max_loss_per_share),
        "suggested_contracts": float(suggested_shares if suggested_shares > 0 else 0.0),
        "total_position_cost": float(total_position_cost if suggested_shares > 0 else 0.0),
        "total_max_loss": float(total_max_loss if suggested_shares > 0 else 0.0),
        "affordable": bool(affordable),
        "status": status,
        "reason": reason,
        "max_risk_dollars": float(max_risk_dollars),
        "risk_budget_multiplier": float(risk_budget_multiplier),
        "effective_max_risk_dollars": float(effective_max_risk_dollars),
        "unit_label": "shares",
        "instrument_type": "equity",
        "fractional_shares_only": bool(fractional_shares_only),
    }


def _build_requested_equity_position_preview(
    report: dict[str, Any],
    live_price: float,
    requested_quantity: float,
    *,
    fractional_shares_only: bool = False,
) -> dict[str, Any]:
    option_plan = report.get("option_plan") or {}
    invalidation_price = serialize_value(option_plan.get("invalidation_price"))
    invalidation_price = float(invalidation_price) if invalidation_price not in (None, "", "nan") else float("nan")
    if not pd.notna(invalidation_price):
        raise ValidationServiceError("An invalidation price is required to size an equity ticket.")

    increment = 0.001 if fractional_shares_only else 1.0
    normalized_quantity = _floor_units_to_increment(float(requested_quantity or 0.0), increment)
    minimum_units = 0.001 if fractional_shares_only else 1.0
    if normalized_quantity < minimum_units:
        raise ValidationServiceError("Requested quantity does not satisfy the current equity unit rules.")

    max_loss_per_share = abs(float(live_price) - float(invalidation_price))
    if max_loss_per_share <= 0:
        raise ValidationServiceError("Equity sizing requires live price and invalidation to be different.")

    total_position_cost = float(normalized_quantity * float(live_price))
    total_max_loss = float(normalized_quantity * max_loss_per_share)
    return {
        "contract_mid": float(live_price) / 100.0,
        "estimated_cost_per_contract": float(live_price),
        "max_loss_per_contract": float(max_loss_per_share),
        "suggested_contracts": float(normalized_quantity),
        "total_position_cost": float(total_position_cost),
        "total_max_loss": float(total_max_loss),
        "affordable": True,
        "status": "VALID TRADE",
        "reason": "Allocator sizing is ready for paper execution.",
        "max_risk_dollars": float(total_max_loss),
        "risk_budget_multiplier": 1.0,
        "effective_max_risk_dollars": float(total_max_loss),
        "unit_label": "shares",
        "instrument_type": "equity",
        "fractional_shares_only": bool(fractional_shares_only),
    }


def _parse_order_datetime(value: Any) -> pd.Timestamp | None:
    if value in (None, "", 0):
        return None
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    if pd.isna(parsed):
        return None
    return parsed


def _pending_order_stale_minutes(row: dict[str, Any]) -> int:
    tif = str(row.get("time_in_force") or "").strip().lower()
    if tif == "gtc_90d":
        return _GTC_ORDER_STALE_MINUTES
    return _DAY_ORDER_STALE_MINUTES


def _coerce_trade_timestamp_series(rows: pd.DataFrame) -> pd.Series:
    if rows.empty:
        return pd.Series(dtype="datetime64[ns, UTC]")

    for candidate in ("closed_at", "close_time", "exit_time", "timestamp", "opened_at"):
        if candidate in rows.columns:
            return pd.to_datetime(rows[candidate], errors="coerce", utc=True)

    return pd.Series([pd.NaT] * len(rows), index=rows.index)


def _build_capital_preservation_snapshot(
    open_trades: pd.DataFrame,
    pending_orders: pd.DataFrame,
    closed_trades: pd.DataFrame,
) -> dict[str, Any]:
    open_position_count = int(len(open_trades))
    pending_order_count = int(len(pending_orders))
    active_ticket_count = int(open_position_count + pending_order_count)

    timestamps = _coerce_trade_timestamp_series(closed_trades)
    pnl = (
        pd.to_numeric(closed_trades.get("realized_pnl", pd.Series(dtype=float)), errors="coerce")
        .fillna(0.0)
        if not closed_trades.empty
        else pd.Series(dtype=float)
    )

    today_realized_pnl = 0.0
    today_closed_trades = 0
    if not closed_trades.empty and len(timestamps) == len(closed_trades):
        today_et = pd.Timestamp.now(tz=_CAPITAL_PRESERVATION_TIMEZONE).normalize()
        localized_days = timestamps.dt.tz_convert(_CAPITAL_PRESERVATION_TIMEZONE).dt.normalize()
        today_mask = localized_days == today_et
        today_closed_trades = int(today_mask.fillna(False).sum())
        if not pnl.empty:
            today_realized_pnl = float(pnl.loc[today_mask.fillna(False)].sum())

    consecutive_losses = 0
    if not closed_trades.empty and not pnl.empty:
        ordered = (
            pd.DataFrame({"_timestamp": timestamps, "_pnl": pnl})
            .sort_values(by="_timestamp", ascending=True, na_position="last", kind="stable")
        )
        for value in reversed(ordered["_pnl"].tolist()):
            numeric = float(value)
            if numeric < 0:
                consecutive_losses += 1
                continue
            break

    return {
        "today_realized_pnl": round(float(today_realized_pnl), 2),
        "today_closed_trades": today_closed_trades,
        "consecutive_losses": int(consecutive_losses),
        "open_position_count": open_position_count,
        "pending_order_count": pending_order_count,
        "active_ticket_count": active_ticket_count,
    }


def _count_ticker_rows(rows: pd.DataFrame, ticker: str) -> int:
    if rows.empty or "ticker" not in rows.columns:
        return 0
    normalized_ticker = str(ticker or "").strip().upper()
    if not normalized_ticker:
        return 0
    return int((rows["ticker"].astype(str).str.strip().str.upper() == normalized_ticker).sum())


def _validate_capital_preservation_request(
    request: OpenTradeRequest,
    *,
    position: dict[str, Any],
    open_trades: pd.DataFrame,
    pending_orders: pd.DataFrame,
    closed_trades: pd.DataFrame,
) -> dict[str, Any]:
    snapshot = _build_capital_preservation_snapshot(open_trades, pending_orders, closed_trades)
    preservation_enabled = bool(request.capital_preservation_mode or request.tiny_account_mode)
    snapshot["enabled"] = preservation_enabled

    normalized_ticker = str(request.ticker or "").strip().upper()
    same_ticker_open_count = _count_ticker_rows(open_trades, normalized_ticker)
    same_ticker_pending_count = _count_ticker_rows(pending_orders, normalized_ticker)
    snapshot["same_ticker_open_count"] = same_ticker_open_count
    snapshot["same_ticker_pending_count"] = same_ticker_pending_count

    risk_unit_dollars = float(request.account_size) * (float(request.risk_percent) / 100.0)
    max_daily_loss_dollars = (
        float(request.max_daily_loss_r) * risk_unit_dollars
        if preservation_enabled and request.max_daily_loss_r is not None and risk_unit_dollars > 0
        else None
    )
    snapshot["risk_unit_dollars"] = round(float(risk_unit_dollars), 2)
    snapshot["max_daily_loss_dollars"] = (
        round(float(max_daily_loss_dollars), 2) if max_daily_loss_dollars is not None else None
    )

    position_cost = pd.to_numeric(position.get("total_position_cost", 0.0), errors="coerce")
    normalized_position_cost = float(position_cost) if pd.notna(position_cost) else 0.0
    snapshot["projected_position_cost"] = round(normalized_position_cost, 2)

    if not preservation_enabled:
        return snapshot

    if request.equities_only and request.instrument_type != "equity":
        raise ValidationServiceError("Capital preservation mode only allows equity tickets.")

    if request.tiny_account_mode and request.instrument_type != "equity":
        raise ValidationServiceError("Tiny-account mode is restricted to equity tickets only.")

    if request.tiny_account_mode and not request.fractional_shares_only:
        raise ValidationServiceError("Tiny-account mode requires fractional-share sizing.")

    if request.fractional_shares_only and request.instrument_type != "equity":
        raise ValidationServiceError("Fractional-share sizing is only available for equity tickets.")

    if request.limit_orders_only and request.order_type != "limit":
        raise ValidationServiceError("Capital preservation mode only allows limit orders.")

    if request.regular_hours_only and (request.extended_hours or request.time_in_force == "day_ext"):
        raise ValidationServiceError("Capital preservation mode blocks extended-hours routing.")

    if same_ticker_open_count > 0 or same_ticker_pending_count > 0:
        raise ValidationServiceError(
            f"Capital preservation mode only allows one layer per symbol. {normalized_ticker} already has "
            f"{same_ticker_open_count} open and {same_ticker_pending_count} working ticket(s)."
        )

    if request.max_open_positions is not None:
        max_open_positions = int(request.max_open_positions)
        snapshot["max_open_positions"] = max_open_positions
        if snapshot["active_ticket_count"] >= max_open_positions:
            raise ValidationServiceError(
                "Capital preservation mode only allows "
                f"{max_open_positions} active ticket{'s' if max_open_positions != 1 else ''}. "
                f"You already have {snapshot['open_position_count']} open and "
                f"{snapshot['pending_order_count']} working."
            )

    if request.max_notional_per_trade is not None and normalized_position_cost > float(request.max_notional_per_trade):
        raise ValidationServiceError(
            "Capital preservation mode caps each ticket at "
            f"${float(request.max_notional_per_trade):,.2f} notional. This ticket maps to "
            f"${normalized_position_cost:,.2f}."
        )

    if max_daily_loss_dollars is not None and snapshot["today_realized_pnl"] <= (-1.0 * max_daily_loss_dollars):
        raise ValidationServiceError(
            "Capital preservation mode is locked for the day. Today's realized PnL is "
            f"${snapshot['today_realized_pnl']:,.2f}, beyond the -${float(max_daily_loss_dollars):,.2f} loss limit."
        )

    if request.max_consecutive_losses is not None:
        max_consecutive_losses = int(request.max_consecutive_losses)
        snapshot["max_consecutive_losses"] = max_consecutive_losses
        if snapshot["consecutive_losses"] >= max_consecutive_losses:
            raise ValidationServiceError(
                "Capital preservation mode is locked after "
                f"{snapshot['consecutive_losses']} consecutive losing close{'s' if snapshot['consecutive_losses'] != 1 else ''}."
            )

    return snapshot


def _validate_directional_entry_request(request: OpenTradeRequest, report: dict[str, Any]) -> None:
    if not bool(getattr(request, "long_only", False)):
        return
    instrument_type = str(getattr(request, "instrument_type", "listed_option") or "listed_option").strip().lower()
    if instrument_type == "listed_option":
        return
    if instrument_type == "equity":
        if str(getattr(request, "broker_side", "buy") or "buy").strip().lower() == "sell":
            raise ValidationServiceError("Long-only mode blocks short equity entries.")
        verdict = str((report or {}).get("verdict") or "").strip().upper()
        if verdict and verdict != "BULLISH":
            raise ValidationServiceError("Long-only mode blocks bearish or mixed equity entries.")


def get_order_lifecycle_health_snapshot(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    pending_snapshot = get_pending_orders_snapshot(db=db, current_user=current_user)
    event_snapshot = get_order_events_snapshot(db, current_user, limit=40)
    pending_items = list(pending_snapshot.get("items") or [])
    event_items = list(event_snapshot.get("items") or [])

    now = pd.Timestamp.now(tz="UTC")
    stale_pending: list[dict[str, Any]] = []
    for row in pending_items:
        reference_at = (
            _parse_order_datetime(row.get("updated_at"))
            or _parse_order_datetime(row.get("submitted_at"))
            or _parse_order_datetime(row.get("opened_at"))
        )
        stale_after_minutes = _pending_order_stale_minutes(row)
        if reference_at is None:
            continue
        age_minutes = round(float((now - reference_at).total_seconds()) / 60.0, 1)
        if age_minutes >= stale_after_minutes:
            stale_pending.append(
                {
                    "order_id": row.get("order_id"),
                    "trade_id": row.get("trade_id"),
                    "ticker": row.get("ticker"),
                    "order_type": row.get("order_type"),
                    "time_in_force": row.get("time_in_force"),
                    "updated_at": row.get("updated_at") or row.get("submitted_at") or row.get("opened_at"),
                    "age_minutes": age_minutes,
                    "stale_after_minutes": stale_after_minutes,
                }
            )

    recent_rejections = [item for item in event_items if str(item.get("status") or "").strip().lower() == "rejected"][:6]
    recent_fills = [item for item in event_items if str(item.get("status") or "").strip().lower() == "filled"][:6]
    recent_closed = [item for item in event_items if str(item.get("status") or "").strip().lower() == "closed"][:6]
    resolved_statuses = {"working", "filled", "closed"}

    def _is_rejection_resolved(rejection: dict[str, Any]) -> bool:
        rejection_at = _parse_order_datetime(rejection.get("created_at"))
        rejection_ticker = str(rejection.get("ticker") or "").strip().upper()
        rejection_trade_id = str(rejection.get("trade_id") or "").strip()
        if rejection_at is None:
            return False
        for event in event_items:
            event_status = str(event.get("status") or "").strip().lower()
            if event_status not in resolved_statuses:
                continue
            event_at = _parse_order_datetime(event.get("created_at"))
            if event_at is None or event_at <= rejection_at:
                continue
            event_trade_id = str(event.get("trade_id") or "").strip()
            event_ticker = str(event.get("ticker") or "").strip().upper()
            if rejection_trade_id and event_trade_id and rejection_trade_id == event_trade_id:
                return True
            if rejection_ticker and event_ticker and rejection_ticker == event_ticker:
                return True
        return False

    unresolved_rejections = [item for item in recent_rejections if not _is_rejection_resolved(item)]
    latest_event = event_items[0] if event_items else None
    last_reject = unresolved_rejections[0] if unresolved_rejections else None
    last_fill = recent_fills[0] if recent_fills else None

    checks = [
        {
            "key": "pending_orders",
            "label": "Pending orders",
            "status": "warning" if stale_pending else "healthy",
            "count": len(pending_items),
            "message": "Pending orders include stale working tickets." if stale_pending else "Pending orders are within their expected working window.",
        },
        {
            "key": "rejections",
            "label": "Rejected orders",
            "status": "warning" if unresolved_rejections else "healthy",
            "count": len(unresolved_rejections),
            "message": (
                "Unresolved rejects still need review before pilot go-live."
                if unresolved_rejections
                else "No unresolved rejects recorded."
            ),
        },
        {
            "key": "fills",
            "label": "Filled orders",
            "status": "healthy",
            "count": len(recent_fills),
            "message": "Recent fills are flowing through the desk lifecycle." if recent_fills else "No recent fills recorded yet.",
        },
    ]
    summary_status = "warning" if stale_pending or unresolved_rejections else "healthy"
    summary_message = (
        "Order lifecycle needs attention: stale working orders or rejects are present."
        if summary_status == "warning"
        else "Order lifecycle is healthy for the current pilot snapshot."
    )
    return {
        "summary": {
            "status": summary_status,
            "message": summary_message,
            "pending_order_count": len(pending_items),
            "stale_pending_count": len(stale_pending),
            "reject_count": len(unresolved_rejections),
            "fill_count": len(recent_fills),
            "closed_count": len(recent_closed),
            "last_event_at": latest_event.get("created_at") if latest_event else None,
            "last_reject_at": last_reject.get("created_at") if last_reject else None,
            "last_fill_at": last_fill.get("created_at") if last_fill else None,
        },
        "checks": checks,
        "stale_pending_orders": stale_pending[:6],
        "recent_rejections": recent_rejections,
        "unresolved_rejections": unresolved_rejections,
        "recent_fills": recent_fills,
        "recent_closed": recent_closed,
    }


def _coerce_rollout_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if pd.notna(normalized) else None


def _build_rollout_readiness_check(
    *,
    key: str,
    label: str,
    tone: str,
    value: str,
    helper: str,
    message: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "tone": tone,
        "value": value,
        "helper": helper,
        "message": message,
    }


def _coerce_rollout_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, "", "nan"):
        return None
    parsed = pd.to_datetime(value, utc=True, errors="coerce")
    if parsed is None or pd.isna(parsed):
        return None
    return parsed


def _build_rollout_history_point(
    *,
    checkpoint_label: str,
    recorded_at: str | None,
    sample_count: int,
    resolved_count: int,
    open_count: int,
    win_count: int,
    replay_win_rate: float | None,
    slippage_sample_count: int,
    average_abs_slippage_bps: float | None,
    worst_abs_slippage_bps: float | None,
) -> dict[str, Any]:
    if resolved_count >= 4:
        sample_tone = "positive"
    elif resolved_count >= 2:
        sample_tone = "warning"
    else:
        sample_tone = "negative"

    if replay_win_rate is None:
        replay_tone = "negative"
    elif replay_win_rate >= 0.55:
        replay_tone = "positive"
    elif replay_win_rate >= 0.45:
        replay_tone = "warning"
    else:
        replay_tone = "negative"

    if slippage_sample_count < 2:
        drift_tone = "negative"
    elif (
        average_abs_slippage_bps is not None
        and average_abs_slippage_bps <= 12.0
        and worst_abs_slippage_bps is not None
        and worst_abs_slippage_bps <= 25.0
    ):
        drift_tone = "positive"
    elif (
        average_abs_slippage_bps is not None
        and average_abs_slippage_bps <= 20.0
        and worst_abs_slippage_bps is not None
        and worst_abs_slippage_bps <= 40.0
    ):
        drift_tone = "warning"
    else:
        drift_tone = "negative"

    tones = [sample_tone, replay_tone, drift_tone]
    if "negative" in tones:
        status = "locked"
        tone = "negative"
        label = "Paper first"
    elif "warning" in tones:
        status = "review"
        tone = "warning"
        label = "Pilot with caution"
    else:
        status = "ready"
        tone = "positive"
        label = "Pilot live ready"

    win_rate_label = "--" if replay_win_rate is None else f"{round(replay_win_rate * 100)}%"
    drift_label = (
        "--"
        if average_abs_slippage_bps is None
        else f"{average_abs_slippage_bps:.1f} bps"
    )
    detail = (
        f"{resolved_count}/{sample_count} resolved"
        f" | {win_rate_label} replay"
        f" | {drift_label} avg drift"
    )

    return {
        "checkpoint_label": checkpoint_label,
        "recorded_at": recorded_at,
        "status": status,
        "tone": tone,
        "label": label,
        "detail": detail,
        "sample_count": sample_count,
        "resolved_count": resolved_count,
        "open_count": open_count,
        "win_count": win_count,
        "replay_win_rate": replay_win_rate,
        "slippage_sample_count": slippage_sample_count,
        "average_abs_slippage_bps": average_abs_slippage_bps,
        "worst_abs_slippage_bps": worst_abs_slippage_bps,
    }


def _build_rollout_readiness_history(
    *,
    board_snapshot_history: dict[str, Any] | None,
    board_outcomes: dict[str, Any] | None,
    paper_live_slippage: dict[str, Any] | None,
    current_status: str,
    current_tone: str,
    current_label: str,
    current_metrics: dict[str, Any],
) -> dict[str, Any]:
    board_items = list((board_snapshot_history or {}).get("items") or [])
    board_items.sort(
        key=lambda item: _coerce_rollout_timestamp(item.get("updated_at")) or pd.Timestamp(0, tz="UTC")
    )

    resolved_outcomes = []
    for item in list((board_outcomes or {}).get("items") or []):
        if str(item.get("status") or "").strip().lower() != "resolved":
            continue
        resolved_outcomes.append(
            {
                "recorded_at": _coerce_rollout_timestamp(item.get("resolved_at") or item.get("saved_at")),
                "pnl_dollars": _coerce_rollout_float(item.get("pnl_dollars")),
            }
        )
    resolved_outcomes.sort(
        key=lambda item: item.get("recorded_at") or pd.Timestamp(0, tz="UTC")
    )

    slippage_items = []
    for item in list((paper_live_slippage or {}).get("items") or []):
        slippage_items.append(
            {
                "recorded_at": _coerce_rollout_timestamp(item.get("closed_at")),
                "slippage_bps": _coerce_rollout_float(item.get("slippage_bps")),
            }
        )
    slippage_items.sort(
        key=lambda item: item.get("recorded_at") or pd.Timestamp(0, tz="UTC")
    )

    checkpoints: list[dict[str, Any]] = []
    for index, item in enumerate(board_items, start=1):
        recorded_at = _coerce_rollout_timestamp(item.get("updated_at"))
        if recorded_at is None:
            continue
        resolved_for_point = [
            row for row in resolved_outcomes if row.get("recorded_at") is not None and row["recorded_at"] <= recorded_at
        ]
        win_count = sum(1 for row in resolved_for_point if (row.get("pnl_dollars") or 0) > 0)
        replay_win_rate = (win_count / len(resolved_for_point)) if resolved_for_point else None
        slippage_for_point = [
            row for row in slippage_items if row.get("recorded_at") is not None and row["recorded_at"] <= recorded_at
        ]
        slippage_values = [
            abs(row["slippage_bps"])
            for row in slippage_for_point
            if row.get("slippage_bps") is not None
        ]
        checkpoints.append(
            _build_rollout_history_point(
                checkpoint_label=str(item.get("leader_ticker") or item.get("board_name") or f"Snapshot {index}"),
                recorded_at=recorded_at.isoformat(),
                sample_count=index,
                resolved_count=len(resolved_for_point),
                open_count=max(index - len(resolved_for_point), 0),
                win_count=win_count,
                replay_win_rate=replay_win_rate,
                slippage_sample_count=len(slippage_values),
                average_abs_slippage_bps=float(sum(slippage_values) / len(slippage_values)) if slippage_values else None,
                worst_abs_slippage_bps=max(slippage_values) if slippage_values else None,
            )
        )

    latest_recorded_at = max(
        [
            timestamp
            for timestamp in [
                *[_coerce_rollout_timestamp(item.get("updated_at")) for item in board_items],
                *[item.get("recorded_at") for item in resolved_outcomes],
                *[item.get("recorded_at") for item in slippage_items],
            ]
            if timestamp is not None
        ],
        default=None,
    )
    current_point = _build_rollout_history_point(
        checkpoint_label="Current",
        recorded_at=latest_recorded_at.isoformat() if latest_recorded_at is not None else None,
        sample_count=int((current_metrics or {}).get("resolved_count", 0) or 0)
        + int((current_metrics or {}).get("open_count", 0) or 0),
        resolved_count=int((current_metrics or {}).get("resolved_count", 0) or 0),
        open_count=int((current_metrics or {}).get("open_count", 0) or 0),
        win_count=int((current_metrics or {}).get("win_count", 0) or 0),
        replay_win_rate=_coerce_rollout_float((current_metrics or {}).get("replay_win_rate")),
        slippage_sample_count=int((current_metrics or {}).get("slippage_sample_count", 0) or 0),
        average_abs_slippage_bps=_coerce_rollout_float((current_metrics or {}).get("average_abs_slippage_bps")),
        worst_abs_slippage_bps=_coerce_rollout_float((current_metrics or {}).get("worst_abs_slippage_bps")),
    )
    current_point["status"] = current_status
    current_point["tone"] = current_tone
    current_point["label"] = current_label

    if not checkpoints or any(
        checkpoints[-1].get(key) != current_point.get(key)
        for key in (
            "status",
            "resolved_count",
            "open_count",
            "win_count",
            "replay_win_rate",
            "slippage_sample_count",
            "average_abs_slippage_bps",
            "worst_abs_slippage_bps",
        )
    ):
        checkpoints.append(current_point)

    checkpoints = checkpoints[-5:]
    if not checkpoints:
        return {
            "count": 0,
            "trend": "unknown",
            "label": "No rollout history",
            "tone": "info",
            "detail": "Saved board snapshots and fill replay will populate rollout history once the desk records a few validation checkpoints.",
            "items": [],
        }

    if len(checkpoints) == 1:
        return {
            "count": 1,
            "trend": "starting",
            "label": "Starting history",
            "tone": checkpoints[-1]["tone"],
            "detail": "Only one rollout checkpoint is available so far. Save more board snapshots and replay outcomes to see a trend.",
            "items": checkpoints,
        }

    previous_point = checkpoints[-2]
    current_point = checkpoints[-1]
    status_rank = {"locked": 0, "review": 1, "ready": 2}
    previous_rank = status_rank.get(str(previous_point.get("status") or "locked"), 0)
    current_rank = status_rank.get(str(current_point.get("status") or "locked"), 0)
    previous_win_rate = _coerce_rollout_float(previous_point.get("replay_win_rate"))
    current_win_rate = _coerce_rollout_float(current_point.get("replay_win_rate"))
    previous_drift = _coerce_rollout_float(previous_point.get("average_abs_slippage_bps"))
    current_drift = _coerce_rollout_float(current_point.get("average_abs_slippage_bps"))

    trend = "steady"
    trend_label = "Holding steady"
    trend_tone = current_point["tone"]
    if current_rank > previous_rank:
        trend = "improving"
        trend_label = "Improving"
        trend_tone = "positive"
    elif current_rank < previous_rank:
        trend = "regressing"
        trend_label = "Regressing"
        trend_tone = "negative"
    elif (
        int(current_point.get("resolved_count") or 0) > int(previous_point.get("resolved_count") or 0)
        or (
            previous_drift is not None
            and current_drift is not None
            and current_drift + 1.0 < previous_drift
        )
        or (
            previous_win_rate is not None
            and current_win_rate is not None
            and current_win_rate > previous_win_rate + 0.05
        )
    ):
        trend = "improving"
        trend_label = "Improving"
        trend_tone = "positive"
    elif (
        (
            previous_drift is not None
            and current_drift is not None
            and current_drift > previous_drift + 2.0
        )
        or (
            previous_win_rate is not None
            and current_win_rate is not None
            and current_win_rate + 0.05 < previous_win_rate
        )
    ):
        trend = "regressing"
        trend_label = "Regressing"
        trend_tone = "negative"

    drift_phrase = (
        "no saved drift yet"
        if current_drift is None
        else f"{current_drift:.1f} bps avg drift now"
    )
    detail = (
        f"Resolved sample moved from {int(previous_point.get('resolved_count') or 0)} to "
        f"{int(current_point.get('resolved_count') or 0)} with "
        f"{'--' if current_win_rate is None else f'{round(current_win_rate * 100)}%'} replay and {drift_phrase}."
    )

    return {
        "count": len(checkpoints),
        "trend": trend,
        "label": trend_label,
        "tone": trend_tone,
        "detail": detail,
        "items": checkpoints,
    }


def _resolve_execution_adapter_for_open_request(
    request: OpenTradeRequest,
    *,
    rollout_readiness: dict[str, Any] | None = None,
) -> tuple[ExecutionAdapter, str, str]:
    requested_intent = str(getattr(request, "execution_intent", "default") or "default").strip().lower() or "default"
    rollout_readiness = dict(rollout_readiness or {})
    allows_live_rollout = bool(rollout_readiness.get("allows_live_rollout"))
    rollout_basis = str(rollout_readiness.get("basis") or "").strip()

    if requested_intent == "desk":
        adapter = get_execution_adapter_for("desk")
        return adapter, adapter.adapter_name, requested_intent

    if requested_intent == "broker_paper":
        adapter = get_execution_adapter_for("alpaca_paper")
        return adapter, adapter.adapter_name, requested_intent

    if requested_intent == "broker_live":
        if not allows_live_rollout:
            raise ValidationServiceError(
                "Broker-live routing is still locked. "
                f"{rollout_basis or 'Paper stability needs more resolved replay and cleaner execution drift before live rollout.'}"
            )
        adapter = get_execution_adapter_for("alpaca_live")
        return adapter, adapter.adapter_name, requested_intent

    adapter = get_execution_adapter()
    adapter_name = adapter.adapter_name
    if adapter_name == "alpaca_live" and not allows_live_rollout:
        raise ValidationServiceError(
            "Broker-live routing is still locked. "
            f"{rollout_basis or 'Paper stability needs more resolved replay and cleaner execution drift before live rollout.'}"
        )
    return adapter, adapter_name, requested_intent


def _build_rollout_readiness_snapshot(
    *,
    validation_snapshot: dict[str, Any] | None,
    order_lifecycle_health: dict[str, Any] | None,
) -> dict[str, Any]:
    validation_snapshot = dict(validation_snapshot or {})
    replay_comparisons = dict(validation_snapshot.get("replay_comparisons") or {})
    board_outcomes = dict(replay_comparisons.get("board_outcomes") or {})
    paper_live_slippage = dict(replay_comparisons.get("paper_live_slippage") or {})
    ranked_entry_rollout = dict(validation_snapshot.get("ranked_entry_rollout") or {})
    route_quality = dict(validation_snapshot.get("route_quality") or {})
    lifecycle_summary = dict((order_lifecycle_health or {}).get("summary") or {})

    replay_items = list(board_outcomes.get("items") or [])
    resolved_replay_items = [
        item for item in replay_items if str(item.get("status") or "").strip().lower() == "resolved"
    ]
    resolved_count = int(board_outcomes.get("resolved_count") or 0)
    win_count = 0
    for item in resolved_replay_items:
        pnl_dollars = _coerce_rollout_float(item.get("pnl_dollars"))
        if pnl_dollars is not None and pnl_dollars > 0:
            win_count += 1
    replay_win_rate = (win_count / resolved_count) if resolved_count > 0 else None

    average_abs_slippage_bps = _coerce_rollout_float(paper_live_slippage.get("average_abs_slippage_bps"))
    worst_abs_slippage_bps = _coerce_rollout_float(paper_live_slippage.get("worst_abs_slippage_bps"))
    slippage_sample_count = int(paper_live_slippage.get("count") or 0)
    stale_pending_count = int(lifecycle_summary.get("stale_pending_count") or 0)
    reject_count = int(lifecycle_summary.get("reject_count") or 0)
    fragile_route_count = int(route_quality.get("fragile_fill_count") or 0)
    rejected_route_count = int(route_quality.get("rejected_route_count") or 0)
    partial_fill_count = int(route_quality.get("partial_fill_count") or 0)
    route_break_count = fragile_route_count + rejected_route_count + partial_fill_count
    ranked_entry_status = str(ranked_entry_rollout.get("status") or "missing").strip().lower() or "missing"
    ranked_entry_accepted = bool(ranked_entry_rollout.get("accepted"))
    ranked_entry_basis = (
        str(ranked_entry_rollout.get("basis") or ranked_entry_rollout.get("detail") or "").strip()
        or "Ranked-entry promotion is still validation-only."
    )
    ranked_entry_candidate_key = str(ranked_entry_rollout.get("candidate_key") or "M").strip().upper() or "M"
    ranked_entry_baseline_key = str(ranked_entry_rollout.get("baseline_key") or "A").strip().upper() or "A"
    all_history_validation_integrity = dict(ranked_entry_rollout.get("all_history_validation_integrity") or {})
    current_route_validation_integrity = dict(ranked_entry_rollout.get("current_route_validation_integrity") or {})
    current_route_fill_count = int(
        ranked_entry_rollout.get("current_route_fill_count")
        or ranked_entry_rollout.get("current_route_directional_fill_count")
        or 0
    )
    current_route_closed_trade_count = int(ranked_entry_rollout.get("current_route_closed_trade_count") or 0)
    metrics_source = str(
        current_route_validation_integrity.get("metrics_source")
        or ranked_entry_rollout.get("metrics_source")
        or ""
    ).strip().lower() or None
    mark_to_market_coverage_status = str(
        current_route_validation_integrity.get("mark_to_market_coverage_status")
        or ranked_entry_rollout.get("mark_to_market_coverage_status")
        or ""
    ).strip().lower() or None
    ledger_snapshot_consistency = str(
        current_route_validation_integrity.get("ledger_snapshot_consistency")
        or ranked_entry_rollout.get("ledger_snapshot_consistency")
        or ""
    ).strip().lower() or None
    current_route_sample_status = str(
        current_route_validation_integrity.get("current_route_sample_status")
        or ranked_entry_rollout.get("current_route_sample_status")
        or ""
    ).strip().lower() or "insufficient"
    route_window_start = current_route_validation_integrity.get("route_window_start") or ranked_entry_rollout.get("route_window_start")
    route_window_end = current_route_validation_integrity.get("route_window_end") or ranked_entry_rollout.get("route_window_end")
    route_window_snapshot_count = int(
        current_route_validation_integrity.get("route_window_snapshot_count")
        or ranked_entry_rollout.get("route_window_snapshot_count")
        or 0
    )
    current_route_reconciliation_status = str(
        current_route_validation_integrity.get("current_route_reconciliation_status")
        or ranked_entry_rollout.get("current_route_reconciliation_status")
        or ""
    ).strip().lower() or None
    current_route_orphan_order_event_count = int(
        current_route_validation_integrity.get("current_route_orphan_order_event_count")
        or ranked_entry_rollout.get("current_route_orphan_order_event_count")
        or 0
    )
    last_submitted_current_route_order_at = (
        current_route_validation_integrity.get("last_submitted_current_route_order_at")
        or ranked_entry_rollout.get("last_submitted_current_route_order_at")
    )
    last_current_route_fill_at = (
        current_route_validation_integrity.get("last_current_route_fill_at")
        or ranked_entry_rollout.get("last_current_route_fill_at")
    )
    last_current_route_close_at = (
        current_route_validation_integrity.get("last_current_route_close_at")
        or ranked_entry_rollout.get("last_current_route_close_at")
    )
    audit_metrics_source = str(all_history_validation_integrity.get("metrics_source") or "").strip().lower() or None
    audit_mark_to_market_coverage_status = str(all_history_validation_integrity.get("mark_to_market_coverage_status") or "").strip().lower() or None
    audit_ledger_snapshot_consistency = str(all_history_validation_integrity.get("ledger_snapshot_consistency") or "").strip().lower() or None

    if resolved_count >= 4:
        sample_tone = "positive"
        sample_message = "Resolved board leaders now provide enough paper history for a tightly scoped broker pilot."
    elif resolved_count >= 2:
        sample_tone = "warning"
        sample_message = "Replay depth is improving, but the paper sample is still thin for broker rollout."
    else:
        sample_tone = "negative"
        sample_message = "Keep rollout on paper until more board leaders resolve into closed trades."

    if replay_win_rate is None:
        replay_tone = "negative"
        replay_helper = "No resolved replay wins yet"
        replay_message = "Broker rollout should stay paper-only until replayed board leaders resolve into real outcomes."
    elif replay_win_rate >= 0.55:
        replay_tone = "positive"
        replay_helper = f"{win_count}/{resolved_count} wins"
        replay_message = "Replay win rate is supportive of a narrowly scoped live pilot."
    elif replay_win_rate >= 0.45:
        replay_tone = "warning"
        replay_helper = f"{win_count}/{resolved_count} wins"
        replay_message = "Replay results are mixed. Review board promotion rules before expanding broker exposure."
    else:
        replay_tone = "negative"
        replay_helper = f"{win_count}/{resolved_count} wins"
        replay_message = "Resolved board leaders are not clearing a stable replay win rate yet."

    if slippage_sample_count < 2:
        drift_tone = "negative"
        drift_helper = f"{slippage_sample_count} saved fill review{'s' if slippage_sample_count != 1 else ''}"
        drift_message = "Saved paper-versus-realized fill comparisons are still too thin for broker rollout."
    elif (
        average_abs_slippage_bps is not None
        and average_abs_slippage_bps <= 12.0
        and worst_abs_slippage_bps is not None
        and worst_abs_slippage_bps <= 25.0
    ):
        drift_tone = "positive"
        drift_helper = (
            f"Avg {average_abs_slippage_bps:.1f} bps | Worst {worst_abs_slippage_bps:.1f} bps"
        )
        drift_message = "Paper-versus-realized fill drift is staying inside a controlled range."
    elif (
        average_abs_slippage_bps is not None
        and average_abs_slippage_bps <= 20.0
        and worst_abs_slippage_bps is not None
        and worst_abs_slippage_bps <= 40.0
    ):
        drift_tone = "warning"
        drift_helper = (
            f"Avg {average_abs_slippage_bps:.1f} bps | Worst {worst_abs_slippage_bps:.1f} bps"
        )
        drift_message = "Fill drift is improving, but it still deserves paper review before wider broker rollout."
    else:
        drift_tone = "negative"
        drift_helper = (
            "No stable fill drift yet"
            if average_abs_slippage_bps is None or worst_abs_slippage_bps is None
            else f"Avg {average_abs_slippage_bps:.1f} bps | Worst {worst_abs_slippage_bps:.1f} bps"
        )
        drift_message = "Paper-versus-realized fill drift is still too unstable for broker rollout."

    if reject_count == 0 and stale_pending_count == 0 and route_break_count == 0:
        lifecycle_tone = "positive"
        lifecycle_helper = "No rejects, stale orders, or fragile routes"
        lifecycle_message = "Order lifecycle health is stable enough for a tightly scoped live pilot."
    elif reject_count <= 1 and stale_pending_count <= 1 and route_break_count <= 1:
        lifecycle_tone = "warning"
        lifecycle_helper = (
            f"{reject_count} reject{'s' if reject_count != 1 else ''} | "
            f"{stale_pending_count} stale | {route_break_count} fragile route{'s' if route_break_count != 1 else ''}"
        )
        lifecycle_message = "A small number of lifecycle issues still need review before expanding broker rollout."
    else:
        lifecycle_tone = "negative"
        lifecycle_helper = (
            f"{reject_count} reject{'s' if reject_count != 1 else ''} | "
            f"{stale_pending_count} stale | {route_break_count} fragile route{'s' if route_break_count != 1 else ''}"
        )
        lifecycle_message = "Order lifecycle instability is still present, so rollout should remain paper-only."

    if current_route_sample_status != "sufficient":
        validation_sample_tone = "negative"
        validation_sample_helper = f"{current_route_fill_count} fills | {current_route_closed_trade_count} closes"
        validation_sample_message = "Collecting current-route paper evidence before the next validation export."
    elif current_route_orphan_order_event_count > 0 or current_route_reconciliation_status not in {None, "", "clean"}:
        validation_sample_tone = "negative"
        validation_sample_helper = (
            f"{current_route_orphan_order_event_count} orphan event"
            f"{'s' if current_route_orphan_order_event_count != 1 else ''}"
        )
        validation_sample_message = "Current-route broker reconciliation is still unresolved, so rollout remains locked."
    elif mark_to_market_coverage_status != "complete":
        validation_sample_tone = "negative"
        validation_sample_helper = (mark_to_market_coverage_status or "missing").replace("_", " ")
        validation_sample_message = "Current-route sample exists, but snapshot coverage is still incomplete for validation."
    elif ledger_snapshot_consistency not in {None, "", "consistent"}:
        validation_sample_tone = "negative"
        validation_sample_helper = (ledger_snapshot_consistency or "unavailable").replace("_", " ")
        validation_sample_message = "Ledger and snapshot metrics are not yet consistent enough to rerun validation."
    else:
        validation_sample_tone = "positive"
        validation_sample_helper = f"{current_route_fill_count} fills | {current_route_closed_trade_count} closes"
        validation_sample_message = "Current-route sample and snapshot integrity are ready for a fresh validation export."

    if ranked_entry_accepted:
        ranked_entry_tone = "positive"
        ranked_entry_helper = f"{ranked_entry_baseline_key} -> {ranked_entry_candidate_key}"
        ranked_entry_message = (
            f"Ranked-entry validation accepts profile {ranked_entry_candidate_key} for live-gate review."
        )
    elif current_route_sample_status != "sufficient":
        ranked_entry_tone = "warning"
        ranked_entry_helper = f"{ranked_entry_baseline_key} -> {ranked_entry_candidate_key}"
        ranked_entry_message = "Ranked-entry promotion stays in validation-only mode until the current-route sample is large enough to score the widened profile."
    elif ranked_entry_status == "rejected":
        ranked_entry_tone = "negative"
        ranked_entry_helper = f"{ranked_entry_baseline_key} -> {ranked_entry_candidate_key}"
        ranked_entry_message = ranked_entry_basis
    elif ranked_entry_status == "invalid":
        ranked_entry_tone = "negative"
        ranked_entry_helper = "Validation artifact invalid"
        ranked_entry_message = ranked_entry_basis
    else:
        ranked_entry_tone = "negative"
        ranked_entry_helper = "Validation artifact missing"
        ranked_entry_message = ranked_entry_basis

    checks = [
        _build_rollout_readiness_check(
            key="paper_sample",
            label="Paper sample",
            tone=sample_tone,
            value=str(resolved_count),
            helper=f"{int(board_outcomes.get('open_count') or 0)} awaiting resolution",
            message=sample_message,
        ),
        _build_rollout_readiness_check(
            key="replay_win_rate",
            label="Replay win rate",
            tone=replay_tone,
            value="--" if replay_win_rate is None else f"{round(replay_win_rate * 100)}%",
            helper=replay_helper,
            message=replay_message,
        ),
        _build_rollout_readiness_check(
            key="paper_live_drift",
            label="Paper/live drift",
            tone=drift_tone,
            value="--" if average_abs_slippage_bps is None else f"{average_abs_slippage_bps:.1f} bps",
            helper=drift_helper,
            message=drift_message,
        ),
        _build_rollout_readiness_check(
            key="order_lifecycle",
            label="Order lifecycle",
            tone=lifecycle_tone,
            value="Clean" if lifecycle_tone == "positive" else "Review",
            helper=lifecycle_helper,
            message=lifecycle_message,
        ),
        _build_rollout_readiness_check(
            key="validation_sample",
            label="Validation sample",
            tone=validation_sample_tone,
            value="Ready" if validation_sample_tone == "positive" else "Collecting",
            helper=validation_sample_helper,
            message=validation_sample_message,
        ),
        _build_rollout_readiness_check(
            key="ranked_entry_rollout",
            label="Ranked-entry gate",
            tone=ranked_entry_tone,
            value="Accepted" if ranked_entry_accepted else "Blocked",
            helper=ranked_entry_helper,
            message=ranked_entry_message,
        ),
    ]

    negative_checks = [check for check in checks if check["tone"] == "negative"]
    warning_checks = [check for check in checks if check["tone"] == "warning"]
    if negative_checks:
        status = "locked"
        tone = "negative"
        if any(check.get("key") == "ranked_entry_rollout" for check in negative_checks):
            label = "Validation only"
            detail = (
                "Broker-live rollout remains blocked until the ranked-entry validation export accepts the wider-cap promotion profile."
            )
        else:
            label = "Paper first"
            detail = "Keep broker rollout locked to paper until replay depth, fill drift, and order lifecycle stabilize."
    elif warning_checks:
        status = "review"
        tone = "warning"
        label = "Pilot with caution"
        detail = "Paper stability is improving, but at least one control still needs review before broader broker rollout."
    else:
        status = "ready"
        tone = "positive"
        label = "Pilot live ready"
        detail = "Paper replay, fill drift, and order lifecycle are stable enough for a tightly scoped broker pilot."

    basis_checks = negative_checks or warning_checks or checks[:1]
    basis = " | ".join(check.get("message") or "" for check in basis_checks if check.get("message"))
    history = _build_rollout_readiness_history(
        board_snapshot_history=validation_snapshot.get("board_snapshot_history"),
        board_outcomes=board_outcomes,
        paper_live_slippage=paper_live_slippage,
        current_status=status,
        current_tone=tone,
        current_label=label,
        current_metrics={
            "resolved_count": resolved_count,
            "open_count": int(board_outcomes.get("open_count") or 0),
            "win_count": win_count,
            "replay_win_rate": replay_win_rate,
            "slippage_sample_count": slippage_sample_count,
            "average_abs_slippage_bps": average_abs_slippage_bps,
            "worst_abs_slippage_bps": worst_abs_slippage_bps,
        },
    )

    return {
        "status": status,
        "tone": tone,
        "label": label,
        "detail": detail,
        "basis": basis,
        "allows_live_rollout": not negative_checks and not warning_checks and ranked_entry_accepted,
        "metrics": {
            "resolved_count": resolved_count,
            "open_count": int(board_outcomes.get("open_count") or 0),
            "win_count": win_count,
            "replay_win_rate": replay_win_rate,
            "slippage_sample_count": slippage_sample_count,
            "average_abs_slippage_bps": average_abs_slippage_bps,
            "worst_abs_slippage_bps": worst_abs_slippage_bps,
            "stale_pending_count": stale_pending_count,
            "reject_count": reject_count,
            "fragile_route_count": route_break_count,
            "ranked_entry_accepted": ranked_entry_accepted,
            "ranked_entry_status": ranked_entry_status,
            "baseline_profile_key": ranked_entry_baseline_key,
            "candidate_profile_key": ranked_entry_candidate_key,
            "current_route_fill_count": current_route_fill_count,
            "current_route_closed_trade_count": current_route_closed_trade_count,
            "metrics_source": metrics_source,
            "mark_to_market_coverage_status": mark_to_market_coverage_status,
            "ledger_snapshot_consistency": ledger_snapshot_consistency,
            "current_route_sample_status": current_route_sample_status,
            "route_window_start": route_window_start,
            "route_window_end": route_window_end,
            "route_window_snapshot_count": route_window_snapshot_count,
            "current_route_reconciliation_status": current_route_reconciliation_status,
            "current_route_orphan_order_event_count": current_route_orphan_order_event_count,
            "last_submitted_current_route_order_at": last_submitted_current_route_order_at,
            "last_current_route_fill_at": last_current_route_fill_at,
            "last_current_route_close_at": last_current_route_close_at,
            "audit_metrics_source": audit_metrics_source,
            "audit_mark_to_market_coverage_status": audit_mark_to_market_coverage_status,
            "audit_ledger_snapshot_consistency": audit_ledger_snapshot_consistency,
        },
        "checks": checks,
        "history": history,
        "ranked_entry_rollout": serialize_value(ranked_entry_rollout),
        "all_history_validation_integrity": serialize_value(all_history_validation_integrity),
        "current_route_validation_integrity": serialize_value(current_route_validation_integrity),
        "order_lifecycle": serialize_value(order_lifecycle_health or {}),
    }


def _format_execution_intent_label(intent: str | None) -> str:
    normalized_intent = str(intent or "").strip().lower()
    if normalized_intent == "broker_live":
        return "Broker live"
    if normalized_intent == "broker_paper":
        return "Broker paper"
    if normalized_intent == "desk":
        return "Desk route"
    return normalized_intent.replace("_", " ").title() or "Execution route"


def _build_rollout_audit_payload(
    *,
    execution_intent: str | None,
    execution_adapter_name: str | None,
    rollout_readiness: dict[str, Any] | None,
) -> dict[str, Any] | None:
    normalized_intent = str(execution_intent or "").strip().lower()
    if normalized_intent != "broker_live":
        return None

    readiness = dict(rollout_readiness or {})
    metrics = dict(readiness.get("metrics") or {})
    history = dict(readiness.get("history") or {})
    return {
        "execution_intent": normalized_intent,
        "route_label": _format_execution_intent_label(normalized_intent),
        "adapter": str(execution_adapter_name or "").strip().lower() or None,
        "allows_live_rollout": bool(readiness.get("allows_live_rollout")),
        "gate_status": str(readiness.get("status") or "").strip().lower() or "unknown",
        "gate_tone": str(readiness.get("tone") or "").strip().lower() or "neutral",
        "gate_label": str(readiness.get("label") or "").strip() or "Pilot gate",
        "basis": str(readiness.get("basis") or "").strip() or None,
        "detail": str(readiness.get("detail") or "").strip() or None,
        "history_trend": str(history.get("trend") or "").strip().lower() or "unknown",
        "history_label": str(history.get("label") or "").strip() or None,
        "resolved_count": int(metrics.get("resolved_count") or 0),
        "open_count": int(metrics.get("open_count") or 0),
        "replay_win_rate": _coerce_rollout_float(metrics.get("replay_win_rate")),
        "slippage_sample_count": int(metrics.get("slippage_sample_count") or 0),
        "average_abs_slippage_bps": _coerce_rollout_float(metrics.get("average_abs_slippage_bps")),
        "worst_abs_slippage_bps": _coerce_rollout_float(metrics.get("worst_abs_slippage_bps")),
        "reject_count": int(metrics.get("reject_count") or 0),
        "fragile_route_count": int(metrics.get("fragile_route_count") or 0),
        "ranked_entry_status": str(metrics.get("ranked_entry_status") or "").strip().lower() or None,
        "ranked_entry_accepted": bool(metrics.get("ranked_entry_accepted")),
        "baseline_profile_key": str(metrics.get("baseline_profile_key") or "").strip().upper() or None,
        "candidate_profile_key": str(metrics.get("candidate_profile_key") or "").strip().upper() or None,
    }


def _build_live_pilot_audit_summary(order_events: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(order_events or {})
    items: list[dict[str, Any]] = []
    for event in list(payload.get("items") or []):
        event_payload = dict(event.get("payload") or {})
        rollout_audit = dict(event_payload.get("rollout_audit") or {})
        if not rollout_audit:
            continue
        normalized_item = {
            "event_id": event.get("id"),
            "trade_id": event.get("trade_id"),
            "ticker": event.get("ticker"),
            "event_key": event.get("event_key"),
            "event_label": event.get("label") or _build_order_event_label(event.get("event_key"), event.get("status")),
            "status": event.get("status"),
            "detail": event.get("detail"),
            "created_at": event.get("created_at"),
            "execution_intent": rollout_audit.get("execution_intent") or "broker_live",
            "route_label": rollout_audit.get("route_label") or _format_execution_intent_label(rollout_audit.get("execution_intent")),
            "adapter": rollout_audit.get("adapter"),
            "allows_live_rollout": bool(rollout_audit.get("allows_live_rollout")),
            "gate_status": rollout_audit.get("gate_status") or "unknown",
            "gate_tone": rollout_audit.get("gate_tone") or "neutral",
            "gate_label": rollout_audit.get("gate_label") or "Pilot gate",
            "basis": rollout_audit.get("basis") or "",
            "history_trend": rollout_audit.get("history_trend") or "unknown",
            "history_label": rollout_audit.get("history_label") or "",
            "resolved_count": int(rollout_audit.get("resolved_count") or 0),
            "open_count": int(rollout_audit.get("open_count") or 0),
            "replay_win_rate": _coerce_rollout_float(rollout_audit.get("replay_win_rate")),
            "slippage_sample_count": int(rollout_audit.get("slippage_sample_count") or 0),
            "average_abs_slippage_bps": _coerce_rollout_float(rollout_audit.get("average_abs_slippage_bps")),
            "worst_abs_slippage_bps": _coerce_rollout_float(rollout_audit.get("worst_abs_slippage_bps")),
            "reject_count": int(rollout_audit.get("reject_count") or 0),
            "fragile_route_count": int(rollout_audit.get("fragile_route_count") or 0),
        }
        items.append(normalized_item)

    latest = items[0] if items else None
    allowed_count = sum(1 for item in items if item.get("allows_live_rollout"))
    blocked_count = sum(1 for item in items if not item.get("allows_live_rollout"))

    if latest is None:
        label = "No live pilot yet"
        tone = "info"
        detail = "Broker-live attempts will be recorded here once the desk clears the paper-to-live gate and routes a pilot order."
    elif not latest.get("allows_live_rollout"):
        label = "Last live pilot blocked"
        tone = "negative"
        detail = "The latest broker-live attempt stayed blocked by the paper-to-live gate and did not route live."
    elif str(latest.get("status") or "").strip().lower() == "rejected":
        label = "Last live pilot rejected"
        tone = "warning"
        detail = "The latest broker-live attempt cleared the gate but was still rejected, so route health needs review before the next pilot."
    elif str(latest.get("status") or "").strip().lower() == "filled":
        label = "Last live pilot filled"
        tone = "positive"
        detail = "The latest broker-live attempt cleared the gate and filled live, so the pilot can now be reviewed against its saved evidence."
    else:
        label = "Last live pilot working"
        tone = "warning"
        detail = "The latest broker-live attempt cleared the gate and is now working, so the next review is route behavior and fill quality."

    return {
        "count": len(items),
        "allowed_count": allowed_count,
        "blocked_count": blocked_count,
        "label": label,
        "tone": tone,
        "detail": detail,
        "latest": latest,
        "items": items,
    }


def sync_pending_orders_from_broker(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
    ticker: str | None = None,
    order_id: str | None = None,
) -> dict[str, Any]:
    pending_orders = _scoped_pending_orders(current_user)
    normalized_ticker = str(ticker or "").strip().upper()
    normalized_order_id = str(order_id or "").strip()
    if not pending_orders.empty and normalized_ticker:
        pending_orders = pending_orders[
            pending_orders.get("ticker", pd.Series(dtype=str)).astype(str).str.upper() == normalized_ticker
        ]
    if not pending_orders.empty and normalized_order_id and "order_id" in pending_orders.columns:
        pending_orders = pending_orders[
            pending_orders["order_id"].astype(str).str.strip() == normalized_order_id
        ]

    tenant, actor = _resolve_trade_actor_context(db, current_user)
    items: list[dict[str, Any]] = []
    counts = Counter()

    for row in pending_orders.to_dict(orient="records") if not pending_orders.empty else []:
        broker_name = str(row.get("broker_name") or "").strip().lower()
        if not broker_name:
            counts["skipped"] += 1
            items.append(
                _build_sync_item(
                    row=row,
                    state="skipped",
                    changed=False,
                    detail="Working order is local-only and does not have a broker to sync against.",
                )
            )
            continue

        try:
            adapter = get_execution_adapter_for(broker_name)
        except ValueError:
            counts["failed"] += 1
            items.append(
                _build_sync_item(
                    row=row,
                    state="failed",
                    changed=False,
                    detail=f"Execution adapter '{broker_name}' is not available for broker sync.",
                    broker_status=str(row.get("broker_status") or "").strip().lower() or None,
                    broker_order_id=str(row.get("broker_order_id") or "").strip() or None,
                )
            )
            continue

        try:
            sync_result = adapter.sync_order(pending_order=row)
        except Exception as exc:
            counts["failed"] += 1
            items.append(
                _build_sync_item(
                    row=row,
                    state="failed",
                    changed=False,
                    detail=str(exc),
                    broker_status=str(row.get("broker_status") or "").strip().lower() or None,
                    broker_order_id=str(row.get("broker_order_id") or "").strip() or None,
                )
            )
            continue

        if sync_result is None:
            counts["skipped"] += 1
            items.append(
                _build_sync_item(
                    row=row,
                    state="skipped",
                    changed=False,
                    detail="Broker sync returned no update for this order.",
                    broker_status=str(row.get("broker_status") or "").strip().lower() or None,
                    broker_order_id=str(row.get("broker_order_id") or "").strip() or None,
                )
            )
            continue

        counts["processed"] += 1
        state = str(sync_result.state or "working").strip().lower() or "working"
        result_row = sync_result.pending_order or sync_result.opened_record or sync_result.terminal_order or row
        changed = state != "working" or _build_order_sync_change_flag(row, sync_result.pending_order, sync_result.broker_status)
        counts[state] += 1
        if changed:
            counts["changed"] += 1

        if changed:
            trade_id = resolve_trade_identifier(result_row)
            ticker_value = str(result_row.get("ticker") or row.get("ticker") or "").strip().upper() or "UNKNOWN"
            order_type = str(result_row.get("order_type") or row.get("order_type") or "")
            time_in_force = str(result_row.get("time_in_force") or row.get("time_in_force") or "")
            detail = sync_result.detail or "Broker reconciliation updated the order lifecycle."
            payload = {
                "previous_order": serialize_value(row),
                "synced_order": serialize_value(result_row),
                "broker_status": sync_result.broker_status,
                "broker_order_id": sync_result.broker_order_id,
                "slippage_dollars": sync_result.slippage_dollars,
                "slippage_bps": sync_result.slippage_bps,
            }

            event_key = "order.accepted"
            status = "working"
            route_state = "accepted"
            book_state = "pending"
            audit_event_type = "trade.order_synced"

            if state == "filled":
                fill_price = serialize_value((sync_result.opened_record or {}).get("actual_fill_price"))
                expected_price = serialize_value((sync_result.opened_record or {}).get("expected_fill_price"))
                detail = (
                    f"{detail} Filled at {fill_price} versus expected {expected_price}."
                    if fill_price not in (None, "")
                    and expected_price not in (None, "")
                    else detail
                )
                event_key = "order.filled"
                status = "filled"
                route_state = "filled"
                book_state = "open"
                audit_event_type = "trade.order_filled"
            elif state == "canceled":
                event_key = "order.canceled"
                status = "canceled"
                route_state = "canceled"
                book_state = "flat"
                audit_event_type = "trade.order_canceled"
            elif state == "expired":
                event_key = "order.expired"
                status = "expired"
                route_state = "expired"
                book_state = "flat"
                audit_event_type = "trade.order_expired"
            elif state == "rejected":
                event_key = "order.rejected"
                status = "rejected"
                route_state = "rejected"
                book_state = "flat"
                audit_event_type = "trade.order_rejected"
            elif sync_result.broker_status == "partially_filled":
                detail = "Broker order is partially filled and still working."

            _record_order_event(
                db,
                tenant=tenant,
                actor=actor,
                trade_id=trade_id,
                ticker=ticker_value,
                event_key=event_key,
                status=status,
                order_type=order_type,
                time_in_force=time_in_force,
                route_state=route_state,
                book_state=book_state,
                detail=detail,
                payload=payload,
                audit_event_type=audit_event_type,
            )

        items.append(
            _build_sync_item(
                row=result_row,
                state=state,
                changed=changed,
                detail=sync_result.detail or "Broker reconciliation completed.",
                broker_status=sync_result.broker_status,
                broker_order_id=sync_result.broker_order_id,
                slippage_dollars=sync_result.slippage_dollars,
                slippage_bps=sync_result.slippage_bps,
            )
        )

    if db is not None and counts.get("changed"):
        db.commit()

    order_events = get_order_events_snapshot(db, current_user, limit=20, ticker=normalized_ticker or None)
    pending_snapshot = get_pending_orders_snapshot(
        db=db,
        current_user=current_user,
        ticker=normalized_ticker or None,
        order_id=normalized_order_id or None,
    )
    return {
        "synced": True,
        "summary": {
            "processed": counts.get("processed", 0),
            "changed": counts.get("changed", 0),
            "working": counts.get("working", 0),
            "filled": counts.get("filled", 0),
            "canceled": counts.get("canceled", 0),
            "expired": counts.get("expired", 0),
            "rejected": counts.get("rejected", 0),
            "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0),
        },
        "items": items,
        "pending_orders": pending_snapshot,
        "order_events": order_events,
        "latest_order_event": order_events["items"][0] if order_events["items"] else None,
    }


def create_trade_intent_from_request(
    request: OpenTradeRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, linked_account, prepared = _prepare_linked_client_trade_submission(
        request,
        db=db,
        current_user=current_user,
        execution_mode="manual_approval",
    )
    pre_trade_risk = _build_pre_trade_risk_snapshot(
        request,
        report=prepared["report"],
        position=prepared["position"],
        live_price=prepared["live_price"],
        option_execution_review=prepared["option_execution_review"],
    )
    route_eligibility = _build_route_eligibility_snapshot(
        request,
        position=prepared["position"],
        option_execution_review=prepared["option_execution_review"],
        capital_preservation=prepared["capital_preservation"],
    )
    liquidity_execution = _build_liquidity_execution_snapshot(
        request,
        option_execution_review=prepared["option_execution_review"],
    )
    strategy_release_snapshot = _build_strategy_release_snapshot(
        request_payload=request.model_dump(),
        report=prepared["report"],
        position=prepared["position"],
        order_ticket=prepared["order_ticket"],
        option_execution_review=prepared["option_execution_review"],
        live_price=prepared["live_price"],
    )
    broker_case = _build_broker_case_metadata(
        None,
        request_payload=request.model_dump(),
        report=prepared["report"],
        position=prepared["position"],
        linked_account=linked_account,
        pre_trade_risk=pre_trade_risk,
        liquidity_execution=liquidity_execution,
        route_eligibility=route_eligibility,
        strategy_release_snapshot=strategy_release_snapshot,
    )
    broker_case["case_id"] = f"CTC-{prepared['trade_id'].replace('-', '')[:10].upper()}"
    intent = TradeApprovalIntent(
        tenant=tenant,
        requester_user=actor,
        linked_account=linked_account,
        provider="alpaca",
        execution_lane="linked_client",
        status="pending_approval",
        ticker=request.ticker,
        instrument_type=request.instrument_type,
        account_environment=linked_account.account_environment,
        trade_id=prepared["trade_id"],
        order_id=prepared["order_id"],
        route_correlation_id=prepared["route_correlation_id"],
        request_payload_json=serialize_value(request.model_dump()),
        analysis_json=serialize_value(prepared["report"]),
        position_json=serialize_value(prepared["position"]),
        order_ticket_json=serialize_value(prepared["order_ticket"]),
        broker_submit_payload_json=serialize_value(prepared["broker_submit_payload"]),
        metadata_json={
            "linked_account_label": linked_account.label,
            "requester_email": actor.email,
            "live_price": prepared["live_price"],
            "execution_mode": "manual_approval",
            "broker_case": broker_case,
            "strategy_release_snapshot": strategy_release_snapshot,
            "pre_trade_risk": serialize_value(pre_trade_risk),
            "route_eligibility": serialize_value(route_eligibility),
            "liquidity_execution": serialize_value(liquidity_execution),
            "option_execution_review": serialize_value(prepared["option_execution_review"]),
            "capital_preservation": serialize_value(prepared["capital_preservation"]),
        },
    )
    if db is None:
        raise ValidationServiceError("Client trade intents require database persistence.")
    db.add(intent)
    db.flush()
    record_audit_event(
        db,
        event_type="client_trade_intent.created",
        tenant=tenant,
        user=actor,
        payload={
            "intent_id": intent.id,
            "linked_account_id": linked_account.id,
            "ticker": request.ticker,
            "account_environment": linked_account.account_environment,
            "provider": "alpaca",
        },
    )
    db.commit()
    db.refresh(intent)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    return {
        "opened": False,
        "intent_created": True,
        "trade_intent": _serialize_trade_intent(intent, audit_events=audit_lookup.get(intent.id)),
        "execution": {
            "lane": "linked_client",
            "provider": "alpaca",
            "account_target_type": "linked_client",
            "execution_mode": "manual_approval",
            "linked_account_id": linked_account.id,
            "approval_policy": linked_account.approval_policy,
            "account_environment": linked_account.account_environment,
        },
        "position": serialize_value(prepared["position"]),
        "analysis": serialize_value(prepared["report"]),
        "option_execution_review": serialize_value(prepared["option_execution_review"]),
        "capital_preservation": serialize_value(prepared["capital_preservation"]),
    }


def _prepare_linked_client_trade_submission(
    request: OpenTradeRequest,
    *,
    db: Session | None,
    current_user: Any | None,
    execution_mode: str,
) -> tuple[Tenant, User, BrokerageLinkedAccount, dict[str, Any]]:
    tenant, actor, linked_account = _resolve_linked_brokerage_account(
        db,
        current_user,
        linked_account_id=request.linked_account_id,
        execution_mode=execution_mode,
    )
    trade_id = str(uuid4())
    order_id = str(uuid4())
    route_correlation_id = str(uuid4())
    context = _prepare_trade_request_context(request, current_user=current_user)
    report = context["report"]
    position = context["position"]
    option_execution_review = context["option_execution_review"]
    capital_preservation = context["capital_preservation"]
    live_price = float(context["live_price"])

    order_ticket = _build_order_ticket_payload(request, report=report)
    order_ticket = _with_current_tenant_scope(order_ticket, current_user)
    order_ticket["trade_id"] = trade_id
    order_ticket["order_id"] = order_id
    order_ticket["route_correlation_id"] = route_correlation_id
    order_ticket["linked_account_id"] = linked_account.id
    order_ticket["account_target_type"] = "linked_client"
    order_ticket["execution_mode"] = execution_mode
    if option_execution_review is not None:
        order_ticket["option_execution_review"] = serialize_value(option_execution_review)

    broker_submit_payload = _build_trade_intent_broker_payload(
        request=request,
        position=position,
        order_ticket=order_ticket,
    )
    return tenant, actor, linked_account, {
        "trade_id": trade_id,
        "order_id": order_id,
        "route_correlation_id": route_correlation_id,
        "report": report,
        "position": position,
        "option_execution_review": option_execution_review,
        "capital_preservation": capital_preservation,
        "live_price": live_price,
        "order_ticket": order_ticket,
        "broker_submit_payload": broker_submit_payload,
    }


def _submit_automated_linked_client_entry(
    request: OpenTradeRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, linked_account, prepared = _prepare_linked_client_trade_submission(
        request,
        db=db,
        current_user=current_user,
        execution_mode="automated_entry",
    )
    if db is None:
        raise ValidationServiceError("Automated linked-client entries require database persistence.")

    client = build_linked_account_execution_client(linked_account)
    automation_profile = get_linked_account_automation_profile(linked_account)
    current_positions = client.list_positions()
    open_position_count = len(current_positions)
    max_open_positions = int(automation_profile.get("max_open_positions") or 0)
    if max_open_positions > 0 and open_position_count >= max_open_positions:
        raise ValidationServiceError(
            f"Linked client account already has {open_position_count} open position"
            f"{'s' if open_position_count != 1 else ''}, which reaches the configured automation cap."
        )
    current_symbol = str(request.ticker or "").strip().upper()
    if any(str(item.get("symbol") or "").strip().upper() == current_symbol for item in current_positions):
        raise ValidationServiceError(f"The linked client account already holds {current_symbol}.")

    pre_trade_risk = _build_pre_trade_risk_snapshot(
        request,
        report=prepared["report"],
        position=prepared["position"],
        live_price=prepared["live_price"],
        option_execution_review=prepared["option_execution_review"],
    )
    route_eligibility = _build_route_eligibility_snapshot(
        request,
        position=prepared["position"],
        option_execution_review=prepared["option_execution_review"],
        capital_preservation=prepared["capital_preservation"],
    )
    liquidity_execution = _build_liquidity_execution_snapshot(
        request,
        option_execution_review=prepared["option_execution_review"],
    )
    strategy_release_snapshot = _build_strategy_release_snapshot(
        request_payload=request.model_dump(),
        report=prepared["report"],
        position=prepared["position"],
        order_ticket=prepared["order_ticket"],
        option_execution_review=prepared["option_execution_review"],
        live_price=prepared["live_price"],
    )
    broker_case = _build_broker_case_metadata(
        None,
        request_payload=request.model_dump(),
        report=prepared["report"],
        position=prepared["position"],
        linked_account=linked_account,
        pre_trade_risk=pre_trade_risk,
        liquidity_execution=liquidity_execution,
        route_eligibility=route_eligibility,
        strategy_release_snapshot=strategy_release_snapshot,
    )
    broker_case["case_id"] = f"CTC-{prepared['trade_id'].replace('-', '')[:10].upper()}"

    intent = TradeApprovalIntent(
        tenant=tenant,
        requester_user=actor,
        approver_user=actor,
        linked_account=linked_account,
        provider="alpaca",
        execution_lane="linked_client",
        status="submission_failed",
        ticker=request.ticker,
        instrument_type=request.instrument_type,
        account_environment=linked_account.account_environment,
        trade_id=prepared["trade_id"],
        order_id=prepared["order_id"],
        route_correlation_id=prepared["route_correlation_id"],
        request_payload_json=serialize_value(request.model_dump()),
        analysis_json=serialize_value(prepared["report"]),
        position_json=serialize_value(prepared["position"]),
        order_ticket_json=serialize_value(prepared["order_ticket"]),
        broker_submit_payload_json=serialize_value(prepared["broker_submit_payload"]),
        metadata_json={
            "linked_account_label": linked_account.label,
            "requester_email": actor.email,
            "live_price": prepared["live_price"],
            "execution_mode": "automated_entry",
            "auto_submitted": True,
            "strategy_binding": automation_profile.get("strategy_binding"),
            "entries_only": bool(automation_profile.get("entries_only")),
            "broker_case": broker_case,
            "strategy_release_snapshot": strategy_release_snapshot,
            "pre_trade_risk": serialize_value(pre_trade_risk),
            "route_eligibility": serialize_value(route_eligibility),
            "liquidity_execution": serialize_value(liquidity_execution),
            "option_execution_review": serialize_value(prepared["option_execution_review"]),
            "capital_preservation": serialize_value(prepared["capital_preservation"]),
        },
    )
    db.add(intent)
    db.flush()

    broker_submit_payload = dict(prepared["broker_submit_payload"] or {})
    try:
        broker_order = client.submit_order(broker_submit_payload)
    except Exception as exc:
        mark_linked_account_execution_failure(db=db, linked_account=linked_account, error=exc)
        intent.status = "submission_failed"
        intent.rejection_reason = str(exc)
        intent.metadata_json = {
            **dict(intent.metadata_json or {}),
            "automation_error": {"message": str(exc), "status_code": getattr(exc, "status_code", None)},
        }
        record_audit_event(
            db,
            event_type="client_trade_intent.automated_submit_failed",
            tenant=tenant,
            user=actor,
            payload={
                "intent_id": intent.id,
                "linked_account_id": linked_account.id,
                "message": str(exc),
            },
        )
        db.commit()
        raise

    broker_status = normalize_alpaca_status(broker_order.get("status"))
    now = _utc_now()
    intent.status = "submitted"
    intent.approved_at = now
    intent.submitted_at = now
    intent.approval_note = "Automated paper entry submitted."
    intent.broker_order_id = str(broker_order.get("id") or "").strip() or None
    intent.broker_status = broker_status or None
    intent.broker_response_json = serialize_value(broker_order)
    linked_account.last_synced_at = now
    linked_account.last_refreshed_at = now
    linked_account.connection_status = "connected"
    linked_account.token_health = "healthy"
    record_linked_account_automation_submission(
        linked_account=linked_account,
        submitted_at=now,
        order_payload={
            "intent_id": intent.id,
            "ticker": request.ticker,
            "broker_order_id": intent.broker_order_id,
            "broker_status": broker_status,
        },
    )

    record_audit_event(
        db,
        event_type="client_trade_intent.automated_submitted",
        tenant=tenant,
        user=actor,
        payload={
            "intent_id": intent.id,
            "linked_account_id": linked_account.id,
            "broker_order_id": intent.broker_order_id,
            "broker_status": broker_status,
            "account_environment": linked_account.account_environment,
        },
    )
    db.commit()
    db.refresh(intent)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    return {
        "opened": False,
        "intent_created": True,
        "automated_submitted": True,
        "trade_intent": _serialize_trade_intent(intent, audit_events=audit_lookup.get(intent.id)),
        "execution": {
            "lane": "linked_client",
            "provider": "alpaca",
            "account_target_type": "linked_client",
            "execution_mode": "automated_entry",
            "linked_account_id": linked_account.id,
            "account_environment": linked_account.account_environment,
            "status": broker_status,
        },
        "position": serialize_value(prepared["position"]),
        "analysis": serialize_value(prepared["report"]),
        "option_execution_review": serialize_value(prepared["option_execution_review"]),
        "capital_preservation": serialize_value(prepared["capital_preservation"]),
    }


def list_trade_intents(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
    status_filter: str = "pending_approval",
) -> dict[str, Any]:
    if db is None or current_user is None:
        return {"items": [], "count": 0, "status_counts": {}}
    tenant = _resolve_tenant_for_current_user(db, current_user)
    statement = (
        select(TradeApprovalIntent)
        .where(TradeApprovalIntent.tenant_id == tenant.id)
        .order_by(TradeApprovalIntent.created_at.desc())
    )
    normalized_status_filter = str(status_filter or "pending_approval").strip().lower()
    if normalized_status_filter and normalized_status_filter != "all":
        statement = statement.where(TradeApprovalIntent.status == normalized_status_filter)
    rows = db.execute(statement).scalars().all()
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[row.id for row in rows])
    items = [_serialize_trade_intent(row, audit_events=audit_lookup.get(row.id)) for row in rows]
    for item, row in zip(items, rows, strict=False):
        item["similar_cases"] = _build_similar_case_signals(row, rows)
    return {
        "items": items,
        "count": len(items),
        "status_counts": dict(Counter(str(item.get("status") or "") for item in items)),
        "broker_ops": _build_broker_ops_dashboard(items),
    }


def _resolve_trade_intent_for_actor(
    *,
    db: Session | None,
    current_user: Any | None,
    intent_id: str,
) -> tuple[Tenant, User, TradeApprovalIntent]:
    if db is None or current_user is None:
        raise ValidationServiceError("Trade intent actions require an authenticated tenant session.")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        raise ValidationServiceError("Trade intent actions require an authenticated user.")
    row = db.execute(
        select(TradeApprovalIntent).where(
            TradeApprovalIntent.id == str(intent_id or "").strip(),
            TradeApprovalIntent.tenant_id == tenant.id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValidationServiceError("Trade approval request was not found.")
    return tenant, actor, row


def conditionally_approve_trade_intent(
    intent_id: str,
    *,
    note: str | None = None,
    conditions: list[str] | None = None,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_trade_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    if intent.status not in {"pending_approval", "conditionally_approved"}:
        raise ValidationServiceError("Only pending client trade approvals can receive conditional approval.")
    cleaned_conditions = _normalize_text_items(conditions)
    if not cleaned_conditions:
        raise ValidationServiceError("Conditional approval requires at least one condition.")
    intent.status = "conditionally_approved"
    intent.approver_user = actor
    intent.approval_note = _clean_text(note) or intent.approval_note
    _store_broker_case_review(
        intent,
        action="conditionally_approved",
        actor=actor,
        note=intent.approval_note,
        conditions=cleaned_conditions,
    )
    record_audit_event(
        db,
        event_type="client_trade_intent.conditionally_approved",
        tenant=tenant,
        user=actor,
        payload={
            "intent_id": intent.id,
            "linked_account_id": intent.linked_account_id,
            "note": intent.approval_note,
            "conditions": cleaned_conditions,
        },
    )
    db.commit()
    db.refresh(intent)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    return {
        "conditionally_approved": True,
        "trade_intent": _serialize_trade_intent(intent, audit_events=audit_lookup.get(intent.id)),
    }


def approve_trade_intent(
    intent_id: str,
    *,
    note: str | None = None,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_trade_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    if intent.status not in {"pending_approval", "conditionally_approved"}:
        raise ValidationServiceError("Only pending or conditionally approved client trade approvals can be submitted.")
    linked_account = intent.linked_account
    if linked_account.connection_status != "connected":
        raise ValidationServiceError("The linked client account must be connected before approval submission.")
    request_payload = OpenTradeRequest.model_validate(intent.request_payload_json or {})
    broker_submit_payload = dict(intent.broker_submit_payload_json or {})
    if not broker_submit_payload:
        broker_submit_payload = _build_trade_intent_broker_payload(
            request=request_payload,
            position=dict(intent.position_json or {}),
            order_ticket=dict(intent.order_ticket_json or {}),
        )

    client = build_linked_account_execution_client(linked_account)
    try:
        broker_order = client.submit_order(broker_submit_payload)
    except Exception as exc:
        mark_linked_account_execution_failure(db=db, linked_account=linked_account, error=exc)
        intent.status = "submission_failed"
        intent.rejection_reason = str(exc)
        _store_broker_case_review(
            intent,
            action="submit_failed",
            actor=actor,
            note=note,
            reason=str(exc),
        )
        record_audit_event(
            db,
            event_type="client_trade_intent.submit_failed",
            tenant=tenant,
            user=actor,
            payload={
                "intent_id": intent.id,
                "linked_account_id": linked_account.id,
                "message": str(exc),
            },
        )
        db.commit()
        raise

    broker_status = normalize_alpaca_status(broker_order.get("status"))
    intent.status = "submitted"
    intent.approver_user = actor
    intent.approval_note = str(note or "").strip() or intent.approval_note
    intent.approved_at = _utc_now()
    intent.submitted_at = intent.approved_at
    intent.broker_order_id = str(broker_order.get("id") or "").strip() or None
    intent.broker_status = broker_status or None
    intent.broker_response_json = serialize_value(broker_order)
    linked_account.last_synced_at = _utc_now()
    linked_account.last_refreshed_at = linked_account.last_synced_at
    linked_account.connection_status = "connected"
    linked_account.token_health = "healthy"
    _store_broker_case_review(
        intent,
        action="approved_submitted",
        actor=actor,
        note=intent.approval_note,
    )

    record_audit_event(
        db,
        event_type="client_trade_intent.approved",
        tenant=tenant,
        user=actor,
        payload={
            "intent_id": intent.id,
            "linked_account_id": linked_account.id,
            "note": intent.approval_note,
        },
    )
    record_audit_event(
        db,
        event_type="client_trade_intent.submitted",
        tenant=tenant,
        user=actor,
        payload={
            "intent_id": intent.id,
            "linked_account_id": linked_account.id,
            "broker_order_id": intent.broker_order_id,
            "broker_status": broker_status,
            "filled_immediately": is_filled_alpaca_status(broker_status),
            "broker_payload": serialize_value(broker_submit_payload),
        },
    )
    db.commit()
    db.refresh(intent)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    return {
        "approved": True,
        "submitted": True,
        "trade_intent": _serialize_trade_intent(intent, audit_events=audit_lookup.get(intent.id)),
    }


def reject_trade_intent(
    intent_id: str,
    *,
    note: str | None = None,
    reason: str | None = None,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_trade_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    if intent.status not in {"pending_approval", "conditionally_approved", "submission_failed"}:
        raise ValidationServiceError("Only pending or failed client trade approvals can be rejected.")
    intent.status = "rejected"
    intent.approver_user = actor
    intent.approval_note = str(note or "").strip() or intent.approval_note
    intent.rejection_reason = str(reason or "").strip() or intent.rejection_reason
    intent.rejected_at = _utc_now()
    _store_broker_case_review(
        intent,
        action="rejected",
        actor=actor,
        note=intent.approval_note,
        reason=intent.rejection_reason,
    )
    record_audit_event(
        db,
        event_type="client_trade_intent.rejected",
        tenant=tenant,
        user=actor,
        payload={
            "intent_id": intent.id,
            "linked_account_id": intent.linked_account_id,
            "note": intent.approval_note,
            "reason": intent.rejection_reason,
        },
    )
    db.commit()
    db.refresh(intent)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    return {
        "rejected": True,
        "trade_intent": _serialize_trade_intent(intent, audit_events=audit_lookup.get(intent.id)),
    }


def expire_trade_intent(
    intent_id: str,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_trade_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    if intent.status not in {"pending_approval", "conditionally_approved", "submission_failed"}:
        raise ValidationServiceError("Only pending or failed client trade approvals can be expired.")
    intent.status = "expired"
    intent.expired_at = _utc_now()
    _store_broker_case_review(
        intent,
        action="expired",
        actor=actor,
    )
    record_audit_event(
        db,
        event_type="client_trade_intent.expired",
        tenant=tenant,
        user=actor,
        payload={"intent_id": intent.id, "linked_account_id": intent.linked_account_id},
    )
    db.commit()
    db.refresh(intent)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    return {
        "expired": True,
        "trade_intent": _serialize_trade_intent(intent, audit_events=audit_lookup.get(intent.id)),
    }


def get_trade_intent_trust_packet(
    intent_id: str,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, _actor, intent = _resolve_trade_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[intent.id])
    audit_events = audit_lookup.get(intent.id) or []
    serialized = _serialize_trade_intent(intent, audit_events=audit_events)
    metadata = _safe_dict(intent.metadata_json)
    broker_case = _safe_dict(serialized.get("broker_case"))
    decision_review = build_decision_review_snapshot(intent)
    evidence_register = build_evidence_register_snapshot(intent)
    packet = {
        "packet_type": "broker_trust_packet",
        "packet_version": _BROKER_TRUST_PACKET_VERSION,
        "generated_at": _utc_now().isoformat(),
        "case": {
            "case_id": broker_case.get("case_id"),
            "intent_id": intent.id,
            "trade_id": intent.trade_id,
            "order_id": intent.order_id,
            "route_correlation_id": intent.route_correlation_id,
            "status": intent.status,
            "created_at": _iso_or_none(intent.created_at),
            "updated_at": _iso_or_none(intent.updated_at),
        },
        "client_account": {
            "linked_account_id": intent.linked_account_id,
            "label": serialized.get("account_label"),
            "provider": intent.provider,
            "account_environment": intent.account_environment,
            "approval_policy": _safe_dict(serialized.get("linked_account")).get("approval_policy"),
            "connection_status": _safe_dict(serialized.get("linked_account")).get("connection_status"),
        },
        "recommendation": {
            "ticker": intent.ticker,
            "instrument_type": intent.instrument_type,
            "request_payload": serialize_value(intent.request_payload_json or {}),
            "analysis": serialize_value(intent.analysis_json or {}),
            "position": serialize_value(intent.position_json or {}),
            "order_ticket": serialize_value(intent.order_ticket_json or {}),
            "broker_submit_payload": serialize_value(intent.broker_submit_payload_json or {}),
        },
        "strategy_release_snapshot": serialized.get("strategy_release_snapshot"),
        "broker_review_case": broker_case,
        "decision_review": decision_review,
        "evidence_register": evidence_register,
        "pre_trade_risk": metadata.get("pre_trade_risk") or broker_case.get("pre_trade_risk") or {},
        "route_eligibility": metadata.get("route_eligibility") or broker_case.get("route_eligibility") or {},
        "liquidity_execution": metadata.get("liquidity_execution") or broker_case.get("liquidity_execution") or {},
        "capital_preservation": metadata.get("capital_preservation") or {},
        "decision_history": {
            "approval_note": intent.approval_note,
            "rejection_reason": intent.rejection_reason,
            "approved_at": _iso_or_none(intent.approved_at),
            "rejected_at": _iso_or_none(intent.rejected_at),
            "submitted_at": _iso_or_none(intent.submitted_at),
            "expired_at": _iso_or_none(intent.expired_at),
            "latest_decision": broker_case.get("latest_decision") or {},
        },
        "broker_response": {
            "broker_order_id": intent.broker_order_id,
            "broker_status": intent.broker_status,
            "broker_response": serialize_value(intent.broker_response_json or {}),
        },
        "audit_timeline": audit_events,
        "records_posture": {
            "supervision_reference": "FINRA Rule 3110 supervision support",
            "books_records_reference": "FINRA Rule 4511 books and records support",
            "best_interest_reference": "SEC Regulation Best Interest recommendation support",
            "note": "This packet preserves review evidence and does not by itself establish registration or legal compliance.",
        },
        "summary": serialized.get("trust_packet_summary"),
    }
    packet["packet_fingerprint"] = hashlib.sha256(
        json.dumps(serialize_value(packet), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return serialize_value(packet)


def build_trade_intent_trust_packet_json(
    intent_id: str,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> str:
    packet = get_trade_intent_trust_packet(intent_id, db=db, current_user=current_user)
    return json.dumps(packet, indent=2, sort_keys=True, default=str)


def open_trade_from_request(
    request: OpenTradeRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    if _is_linked_client_trade_request(request):
        normalized_execution_mode = str(getattr(request, "execution_mode", "manual_approval") or "manual_approval").strip().lower()
        if normalized_execution_mode == "automated_entry":
            return _submit_automated_linked_client_entry(request, db=db, current_user=current_user)
        return create_trade_intent_from_request(request, db=db, current_user=current_user)

    trade_id = str(uuid4())
    order_id = str(uuid4())
    route_correlation_id = str(uuid4())
    tenant, actor = _resolve_trade_actor_context(db, current_user)
    rollout_readiness: dict[str, Any] | None = None
    option_execution_review: dict[str, Any] | None = None
    execution_intent = str(getattr(request, "execution_intent", "") or "").strip().lower() or "desk"
    execution_adapter_name: str | None = None

    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=request.ticker,
        event_key="order.submitted",
        status="submitting",
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        route_state="submitting",
        book_state="pending",
        detail=f"Submitting {request.order_type.replace('_', ' ')} order with {request.time_in_force.upper()} rules.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "request": serialize_value(request.model_dump()),
        },
    )

    try:
        if execution_intent == "broker_live":
            from backend.services.portfolio_service import _build_validation_snapshot, _normalize_closed_trades_for_journal

            scoped_closed_trades = _scoped_closed_trades(current_user)
            validation_snapshot = _build_validation_snapshot(
                _normalize_closed_trades_for_journal(scoped_closed_trades),
                current_user=current_user,
            )
            rollout_readiness = _build_rollout_readiness_snapshot(
                validation_snapshot=validation_snapshot,
                order_lifecycle_health=get_order_lifecycle_health_snapshot(db=db, current_user=current_user),
            )
            _resolve_execution_adapter_for_open_request(request, rollout_readiness=rollout_readiness)

        context = _prepare_trade_request_context(request, current_user=current_user)
        open_trades = context["open_trades"]
        pending_orders = context["pending_orders"]
        closed_trades = context["closed_trades"]
        from backend.services.portfolio_service import _build_validation_snapshot, _normalize_closed_trades_for_journal

        normalized_closed_trades = _normalize_closed_trades_for_journal(closed_trades)
        validation_snapshot = _build_validation_snapshot(normalized_closed_trades, current_user=current_user)
        order_lifecycle_health = get_order_lifecycle_health_snapshot(db=db, current_user=current_user)
        rollout_readiness = _build_rollout_readiness_snapshot(
            validation_snapshot=validation_snapshot,
            order_lifecycle_health=order_lifecycle_health,
        )
        execution_adapter, execution_adapter_name, execution_intent = _resolve_execution_adapter_for_open_request(
            request,
            rollout_readiness=rollout_readiness,
        )
        report = context["report"]
        live_price = context["live_price"]
        position = context["position"]
        option_execution_review = context["option_execution_review"]
        capital_preservation = context["capital_preservation"]

        order_ticket = _build_order_ticket_payload(request, report=report)
        order_ticket = _with_current_tenant_scope(order_ticket, current_user)
        order_ticket["trade_id"] = trade_id
        order_ticket["order_id"] = order_id
        order_ticket["route_correlation_id"] = route_correlation_id
        if option_execution_review is not None:
            order_ticket["option_execution_review"] = serialize_value(option_execution_review)
        execution_result = execution_adapter.submit_order(
            request=request,
            report=report,
            live_price=float(live_price),
            position=position,
            trade_id=trade_id,
            order_id=order_id,
            order_ticket=order_ticket,
        )
        position_opened = execution_result.position_opened
        record = execution_result.record
        pending_order = serialize_value(execution_result.pending_order) if execution_result.pending_order is not None else None
        if position_opened:
            event_key = "order.filled"
            final_status = "filled"
            route_state = "filled"
            book_state = "open"
            detail = "Market order filled and opened a live desk-tracked position."
            audit_event_type = "trade.order_filled"
        else:
            event_key = "order.accepted"
            final_status = "working"
            route_state = "accepted"
            book_state = "pending"
            detail = f"{request.order_type.replace('_', ' ').title()} order accepted and is now working on the desk."
            audit_event_type = "trade.order_accepted"

        _record_order_event(
            db,
            tenant=tenant,
            actor=actor,
            trade_id=trade_id,
            ticker=request.ticker,
            event_key=event_key,
            status=final_status,
            order_type=request.order_type,
            time_in_force=request.time_in_force,
            route_state=route_state,
            book_state=book_state,
            detail=detail,
            payload={
                "order_id": order_id,
                "route_correlation_id": route_correlation_id,
                "request": serialize_value(request.model_dump()),
                "execution": {
                    "adapter": execution_result.broker_name or execution_adapter_name,
                    "intent": execution_intent,
                    "broker_order_id": execution_result.broker_order_id,
                    "broker_status": execution_result.broker_status,
                },
                "rollout_audit": _build_rollout_audit_payload(
                    execution_intent=execution_intent,
                    execution_adapter_name=execution_result.broker_name or execution_adapter_name,
                    rollout_readiness=rollout_readiness,
                ),
                "position": serialize_value(position),
                "record": serialize_value(record),
                "analysis": serialize_value(report),
                "option_execution_review": serialize_value(option_execution_review),
            },
            audit_event_type=audit_event_type,
        )
        if db is not None:
            db.commit()

        order_events = get_order_events_snapshot(db, current_user, trade_id=trade_id, limit=6)
        return {
            "opened": True,
            "position_opened": position_opened,
            "record": serialize_value(record),
            "pending_order": pending_order,
            "execution": {
                "adapter": execution_result.broker_name or execution_adapter_name,
                "intent": execution_intent,
                "broker_order_id": execution_result.broker_order_id,
                "broker_status": execution_result.broker_status,
                "broker_response": serialize_value(execution_result.broker_response),
            },
            "position": serialize_value(position),
            "analysis": serialize_value(report),
            "option_execution_review": serialize_value(option_execution_review),
            "capital_preservation": serialize_value(capital_preservation),
            "rollout_readiness": serialize_value(rollout_readiness),
            "live_pilot_audit": serialize_value(_build_live_pilot_audit_summary(order_events)),
            "order_events": order_events,
            "pending_orders": get_pending_orders_snapshot(db=db, current_user=current_user, ticker=request.ticker),
            "latest_order_event": order_events["items"][0] if order_events["items"] else None,
        }
    except Exception as exc:
        _record_order_event(
            db,
            tenant=tenant,
            actor=actor,
            trade_id=trade_id,
            ticker=request.ticker,
            event_key="order.rejected",
            status="rejected",
            order_type=request.order_type,
            time_in_force=request.time_in_force,
            route_state="rejected",
            book_state="flat",
            detail=str(exc),
            payload={
                "order_id": order_id,
                "route_correlation_id": route_correlation_id,
                "request": serialize_value(request.model_dump()),
                "execution": {
                    "adapter": execution_adapter_name,
                    "intent": execution_intent,
                },
                "rollout_audit": _build_rollout_audit_payload(
                    execution_intent=execution_intent,
                    execution_adapter_name=execution_adapter_name,
                    rollout_readiness=rollout_readiness,
                ),
                "error": str(exc),
            },
            audit_event_type="trade.order_rejected",
        )
        if db is not None:
            db.commit()
        raise


def close_trade_from_request(
    request: CloseTradeRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    open_trades = _scoped_open_trades(current_user)
    if open_trades.empty:
        raise ValidationServiceError("There are no open trades to close.")
    if request.trade_index >= len(open_trades):
        raise ValidationServiceError("Trade index is out of range.")

    global_trade_index = int(open_trades.index[request.trade_index])
    target_trade = open_trades.iloc[request.trade_index].to_dict()
    trade_id = resolve_trade_identifier(target_trade)
    route_correlation_id = _normalize_route_correlation_id(target_trade.get("route_correlation_id"))
    execution_adapter = get_execution_adapter()
    tenant, actor = _resolve_trade_actor_context(db, current_user)
    execution_result = execution_adapter.close_position(
        request=request.model_copy(update={"trade_index": global_trade_index}),
        target_trade=target_trade,
    )

    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=str(target_trade.get("ticker") or ""),
        event_key="order.closed",
        status="closed",
        order_type=str(target_trade.get("order_type") or ""),
        time_in_force=str(target_trade.get("time_in_force") or ""),
        route_state="closed",
        book_state="flat",
        detail="Closed the desk-tracked position at the requested market inputs.",
        payload={
            "route_correlation_id": route_correlation_id,
            "close_underlying_price": float(request.close_underlying_price),
            "close_contract_mid": float(request.close_contract_mid),
            "close_limit_price": float(getattr(request, "close_limit_price", None) or request.close_contract_mid),
            "trade": serialize_value(execution_result.closed_trade),
        },
        audit_event_type="trade.order_closed",
    )
    if db is not None:
        db.commit()

    order_events = get_order_events_snapshot(db, current_user, trade_id=trade_id, limit=6)
    return {
        "closed": True,
        "closed_trade_index": int(request.trade_index),
        "closed_trade_preview": serialize_value(execution_result.closed_trade),
        "execution": {
            "adapter": execution_result.broker_name or execution_adapter.adapter_name,
            "broker_order_id": execution_result.broker_order_id,
            "broker_status": execution_result.broker_status,
            "broker_response": serialize_value(execution_result.broker_response),
        },
        "close_underlying_price": float(request.close_underlying_price),
        "close_contract_mid": float(request.close_contract_mid),
        "close_limit_price": float(getattr(request, "close_limit_price", None) or request.close_contract_mid),
        "order_events": order_events,
        "latest_order_event": order_events["items"][0] if order_events["items"] else None,
    }


def cancel_pending_order_from_request(
    order_id: str,
    request: CancelOrderRequest | None = None,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    pending_order = _get_pending_order_row(order_id, current_user=current_user)
    if pending_order is None:
        raise ValidationServiceError("Working order was not found.")

    execution_adapter = get_execution_adapter()
    tenant, actor = _resolve_trade_actor_context(db, current_user)
    execution_result = execution_adapter.cancel_order(order_id=order_id)
    if execution_result is None:
        raise ValidationServiceError("Working order could not be canceled.")
    canceled = execution_result.canceled_order

    detail = request.reason if request and request.reason else "Canceled the working desk-tracked order."
    trade_id = resolve_trade_identifier(canceled)
    route_correlation_id = _normalize_route_correlation_id(canceled.get("route_correlation_id"))
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=str(canceled.get("ticker") or ""),
        event_key="order.canceled",
        status="canceled",
        order_type=str(canceled.get("order_type") or ""),
        time_in_force=str(canceled.get("time_in_force") or ""),
        route_state="canceled",
        book_state="flat",
        detail=detail,
        payload={
            "route_correlation_id": route_correlation_id,
            "order": serialize_value(canceled),
            "reason": request.reason if request else None,
        },
        audit_event_type="trade.order_canceled",
    )
    if db is not None:
        db.commit()

    order_events = get_order_events_snapshot(db, current_user, trade_id=trade_id, limit=8)
    return {
        "canceled": True,
        "pending_order": serialize_value(canceled),
        "execution": {
            "adapter": execution_result.broker_name or execution_adapter.adapter_name,
            "broker_order_id": execution_result.broker_order_id,
            "broker_status": execution_result.broker_status,
            "broker_response": serialize_value(execution_result.broker_response),
        },
        "order_events": order_events,
        "pending_orders": get_pending_orders_snapshot(db=db, current_user=current_user, ticker=str(canceled.get("ticker") or "")),
        "latest_order_event": order_events["items"][0] if order_events["items"] else None,
    }


def replace_pending_order_from_request(
    order_id: str,
    request: ReplaceOrderRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    current_order = _get_pending_order_row(order_id, current_user=current_user)
    if current_order is None:
        raise ValidationServiceError("Working order was not found.")
    _validate_instrument_strategy_request(request)

    execution_adapter = get_execution_adapter()
    execution_result = execution_adapter.replace_order(
        order_id=order_id,
        request=request,
        order_ticket=_build_order_ticket_payload(request),
    )
    if execution_result is None:
        raise ValidationServiceError("Working order could not be updated.")
    updated_order = execution_result.updated_order

    tenant, actor = _resolve_trade_actor_context(db, current_user)
    trade_id = resolve_trade_identifier(updated_order)
    route_correlation_id = _normalize_route_correlation_id(
        updated_order.get("route_correlation_id") or current_order.get("route_correlation_id")
    )
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=str(updated_order.get("ticker") or ""),
        event_key="order.replaced",
        status="working",
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        route_state="accepted",
        book_state="pending",
        detail=f"Replaced the working order with {request.order_type.replace('_', ' ')} instructions.",
        payload={
            "route_correlation_id": route_correlation_id,
            "previous_order": serialize_value(current_order),
            "updated_order": serialize_value(updated_order),
            "request": serialize_value(request.model_dump()),
        },
        audit_event_type="trade.order_replaced",
    )
    if db is not None:
        db.commit()

    order_events = get_order_events_snapshot(db, current_user, trade_id=trade_id, limit=8)
    return {
        "updated": True,
        "pending_order": serialize_value(updated_order),
        "execution": {
            "adapter": execution_result.broker_name or execution_adapter.adapter_name,
            "broker_order_id": execution_result.broker_order_id,
            "broker_status": execution_result.broker_status,
            "broker_response": serialize_value(execution_result.broker_response),
        },
        "order_events": order_events,
        "pending_orders": get_pending_orders_snapshot(db=db, current_user=current_user, ticker=str(updated_order.get("ticker") or "")),
        "latest_order_event": order_events["items"][0] if order_events["items"] else None,
    }


def fill_pending_order_from_request(
    order_id: str,
    request: FillOrderRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    pending_order = _get_pending_order_row(order_id, current_user=current_user)
    if pending_order is None:
        raise ValidationServiceError("Working order was not found.")

    execution_adapter = get_execution_adapter()
    execution_result = execution_adapter.fill_order(order_id=order_id, live_price=float(request.live_price))
    if execution_result is None:
        raise ValidationServiceError("Working order could not be filled.")
    filled_record = execution_result.filled_record

    tenant, actor = _resolve_trade_actor_context(db, current_user)
    trade_id = resolve_trade_identifier(filled_record)
    route_correlation_id = _normalize_route_correlation_id(
        filled_record.get("route_correlation_id") or pending_order.get("route_correlation_id")
    )
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=str(filled_record.get("ticker") or ""),
        event_key="order.filled",
        status="filled",
        order_type=str(filled_record.get("order_type") or ""),
        time_in_force=str(filled_record.get("time_in_force") or ""),
        route_state="filled",
        book_state="open",
        detail="Filled the working order and opened a live desk-tracked position.",
        payload={
            "route_correlation_id": route_correlation_id,
            "pending_order": serialize_value(pending_order),
            "filled_record": serialize_value(filled_record),
            "live_price": float(request.live_price),
        },
        audit_event_type="trade.order_filled",
    )
    if db is not None:
        db.commit()

    order_events = get_order_events_snapshot(db, current_user, trade_id=trade_id, limit=8)
    return {
        "filled": True,
        "record": serialize_value(filled_record),
        "execution": {
            "adapter": execution_result.broker_name or execution_adapter.adapter_name,
            "broker_order_id": execution_result.broker_order_id,
            "broker_status": execution_result.broker_status,
            "broker_response": serialize_value(execution_result.broker_response),
        },
        "order_events": order_events,
        "pending_orders": get_pending_orders_snapshot(db=db, current_user=current_user, ticker=str(filled_record.get("ticker") or "")),
        "latest_order_event": order_events["items"][0] if order_events["items"] else None,
    }


def _build_client_trade_intent_summary(
    *,
    db: Session | None,
    current_user: Any | None,
) -> dict[str, Any]:
    if db is None or current_user is None:
        return {
            "count": 0,
            "pending_approval_count": 0,
            "conditionally_approved_count": 0,
            "submitted_count": 0,
            "rejected_count": 0,
            "submission_failed_count": 0,
            "automated_entry_count": 0,
            "broker_ops": _build_broker_ops_dashboard([]),
            "items": [],
        }
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(
        select(TradeApprovalIntent)
        .where(TradeApprovalIntent.tenant_id == tenant.id)
        .order_by(
            case(
                (TradeApprovalIntent.status == "submitted", 0),
                (TradeApprovalIntent.status == "submission_failed", 1),
                (TradeApprovalIntent.status == "conditionally_approved", 2),
                (TradeApprovalIntent.status == "pending_approval", 3),
                else_=4,
            ),
            TradeApprovalIntent.created_at.desc(),
        )
        .limit(12)
    ).scalars().all()
    audit_lookup = list_trade_intent_audit_events(db=db, tenant_id=tenant.id, intent_ids=[row.id for row in rows])
    items = [_serialize_trade_intent(row, audit_events=audit_lookup.get(row.id)) for row in rows]
    counts = Counter(str(item.get("status") or "").strip().lower() for item in items)
    return {
        "count": len(rows),
        "pending_approval_count": counts.get("pending_approval", 0),
        "conditionally_approved_count": counts.get("conditionally_approved", 0),
        "submitted_count": counts.get("submitted", 0),
        "rejected_count": counts.get("rejected", 0),
        "submission_failed_count": counts.get("submission_failed", 0),
        "automated_entry_count": sum(1 for item in items if str(item.get("execution_mode") or "") == "automated_entry"),
        "broker_ops": _build_broker_ops_dashboard(items),
        "items": items,
    }


def get_trade_summary(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, object]:
    open_trades = _scoped_open_trades(current_user)
    pending_orders = _scoped_pending_orders(current_user)
    closed_trades = _scoped_closed_trades(current_user)
    from backend.services.portfolio_service import (
        _build_attribution_summary,
        _build_validation_snapshot,
        _normalize_closed_trades_for_journal,
    )

    normalized_closed_trades = _normalize_closed_trades_for_journal(closed_trades)
    validation_snapshot = _build_validation_snapshot(normalized_closed_trades, current_user=current_user)
    order_lifecycle_health = get_order_lifecycle_health_snapshot(db=db, current_user=current_user)
    rollout_readiness = _build_rollout_readiness_snapshot(
        validation_snapshot=validation_snapshot,
        order_lifecycle_health=order_lifecycle_health,
    )
    order_events = get_order_events_snapshot(db, current_user, limit=12)
    live_pilot_audit = _build_live_pilot_audit_summary(order_events)
    monitored = filter_frame_to_current_user(sdm.monitor_open_trades(), current_user)
    total_open = int(len(open_trades))
    tracked_premium = float(open_trades.get("entry_contract_mid", pd.Series(dtype=float)).fillna(0).astype(float).sum()) if not open_trades.empty else 0.0
    urgent_actions = 0
    if not monitored.empty and "monitor_action" in monitored.columns:
        urgent_actions = int((monitored["monitor_action"].astype(str).str.upper() != "HOLD").sum())
    bullish = 0
    bearish = 0
    if not open_trades.empty and "direction" in open_trades.columns:
        directions = open_trades["direction"].astype(str).str.upper()
        bullish = int((directions == "CALL").sum())
        bearish = int((directions == "PUT").sum())
    return {
        "open_trades": total_open,
        "pending_orders": int(len(pending_orders)),
        "tracked_premium": round(tracked_premium, 2),
        "urgent_actions": urgent_actions,
        "call_positions": bullish,
        "put_positions": bearish,
        "trade_summary": serialize_value(sdm.trade_summary(closed_trades)),
        "attribution_summary": serialize_value(_build_attribution_summary(normalized_closed_trades)),
        "capital_preservation": _build_capital_preservation_snapshot(open_trades, pending_orders, closed_trades),
        "validation_snapshot": serialize_value(validation_snapshot),
        "rollout_readiness": serialize_value(rollout_readiness),
        "live_pilot_audit": serialize_value(live_pilot_audit),
        "client_trade_intents": _build_client_trade_intent_summary(db=db, current_user=current_user),
        "client_automation": build_linked_client_automation_summary(db=db, current_user=current_user)
        if db is not None and current_user is not None
        else {
            "eligible_linked_account_count": 0,
            "automated_linked_account_count": 0,
            "blocked_linked_account_count": 0,
            "last_automated_client_order": None,
            "block_reasons_by_account": {},
            "items": [],
        },
        "working_orders": get_pending_orders_snapshot(db=db, current_user=current_user),
        "order_events": order_events,
    }
