from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.schemas import CloseTradeRequest, OpenTradeRequest
from backend.services import automation_ai_review_service, automation_paper_broker_reconciliation_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.provider_registry import get_execution_adapter_for
from backend.services.serialization import serialize_value
from backend.services.trade_service import _record_order_event, sync_pending_orders_from_broker

PAPER_ORDER_LIFECYCLE_NOTE_OWNER = "automation-ai"
PAPER_ORDER_LIFECYCLE_HISTORY_LIMIT = 8
PAPER_ORDER_LIFECYCLE_NOTE_LIMIT = 250
PAPER_ORDER_LIFECYCLE_PERSONAL_PAPER_PROFILE = "personal_paper"
PAPER_ORDER_LIFECYCLE_TICKER = "SPY"
PAPER_ORDER_LIFECYCLE_QUANTITY = 0.001
PAPER_ORDER_LIFECYCLE_LIMIT_PRICE = 0.01

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


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


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or PAPER_ORDER_LIFECYCLE_PERSONAL_PAPER_PROFILE).strip().lower().replace(":", "-")


def normalize_paper_order_lifecycle_soak_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("paper_order_lifecycle_soak_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("paper_order_lifecycle_soak_history") or [])[:PAPER_ORDER_LIFECYCLE_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "paper_order_lifecycle_soak_last_report": serialize_value(last_report),
        "paper_order_lifecycle_soak_last_note_id": str(runtime.get("paper_order_lifecycle_soak_last_note_id") or "").strip() or None,
        "paper_order_lifecycle_soak_note_session_day": str(runtime.get("paper_order_lifecycle_soak_note_session_day") or "").strip() or None,
        "paper_order_lifecycle_soak_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("paper_order_lifecycle_soak_last_run_at"))),
        "paper_order_lifecycle_soak_last_error": str(runtime.get("paper_order_lifecycle_soak_last_error") or "").strip() or None,
        "paper_order_lifecycle_soak_history": history,
    }


def build_paper_order_lifecycle_soak_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_paper_order_lifecycle_soak_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("paper_order_lifecycle_soak_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "current_step": "idle",
            "checked_at": None,
            "broker_order_id": None,
            "local_order_id": None,
            "fill_evidence": {},
            "cancel_evidence": {},
            "close_evidence": {},
            "reconciliation_status": "not_run",
            "blockers": [],
            "warnings": [],
            "related_note_id": runtime.get("paper_order_lifecycle_soak_last_note_id"),
            "last_run_at": runtime.get("paper_order_lifecycle_soak_last_run_at"),
            "last_error": runtime.get("paper_order_lifecycle_soak_last_error"),
            "manual_action_required": False,
        }
    report.setdefault("related_note_id", runtime.get("paper_order_lifecycle_soak_last_note_id"))
    report.setdefault("note_id", runtime.get("paper_order_lifecycle_soak_last_note_id"))
    report.setdefault("last_run_at", runtime.get("paper_order_lifecycle_soak_last_run_at"))
    report.setdefault("last_error", runtime.get("paper_order_lifecycle_soak_last_error"))
    return serialize_value(report)


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=PAPER_ORDER_LIFECYCLE_NOTE_OWNER,
            limit=PAPER_ORDER_LIFECYCLE_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "paper-broker",
        "order-lifecycle-soak",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Automation paper order lifecycle soak for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Current step: {str(report.get('current_step') or '').replace('_', ' ')}",
        f"Ticker: {report.get('ticker') or PAPER_ORDER_LIFECYCLE_TICKER}",
        f"Local order id: {report.get('local_order_id') or '--'}",
        f"Broker order id: {report.get('broker_order_id') or '--'}",
        f"Broker status: {report.get('broker_status') or '--'}",
        f"Reconciliation status: {report.get('reconciliation_status') or 'not_run'}",
        "",
        "This lifecycle soak is paper-only. It does not place live orders, clear locks, enable trading, arm automation, tune baseline settings, or change broker-live gates.",
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
    steps = [item for item in list(report.get("steps") or []) if isinstance(item, dict)]
    lines.extend(["", "Steps"])
    if steps:
        lines.extend(f"- {item.get('step')}: {item.get('status')}. {item.get('detail')}" for item in steps[:16])
    else:
        lines.append("- No lifecycle steps ran.")
    for heading, key in (
        ("Fill evidence", "fill_evidence"),
        ("Cancel evidence", "cancel_evidence"),
        ("Close evidence", "close_evidence"),
    ):
        evidence = dict(report.get(key) or {})
        if not evidence:
            continue
        lines.extend(["", heading])
        for item_key, item_value in evidence.items():
            lines.append(f"- {str(item_key).replace('_', ' ')}: {item_value}")
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "paper-broker",
        "order-lifecycle-soak",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Automation paper order lifecycle soak - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": PAPER_ORDER_LIFECYCLE_NOTE_OWNER,
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


def _safe_step(report: dict[str, Any], step: str, status: str, detail: str, **extra: Any) -> None:
    report.setdefault("steps", []).append(
        serialize_value(
            {
                "step": step,
                "status": status,
                "detail": detail,
                **extra,
            }
        )
    )
    report["current_step"] = step


def _build_soak_report(ticker: str, interval: str, live_price: float) -> dict[str, Any]:
    target = live_price + 0.01
    invalidation = max(0.0001, live_price - 0.005)
    return {
        "ticker": ticker,
        "interval": interval,
        "verdict": "BULLISH",
        "alignment_label": "paper_lifecycle_soak",
        "conviction_label": "validation",
        "setup_score": 1.0,
        "alpha_score": 1.0,
        "execution_score": 1.0,
        "portfolio_score": 1.0,
        "edge_to_cost_ratio": 1.0,
        "proxy_correlation_bucket": "paper_lifecycle_soak",
        "portfolio_rank": 1,
        "auto_entry_eligible": False,
        "setup_grade": "SOAK",
        "trade_decision": "Paper lifecycle soak.",
        "reject_reason": "",
        "event_risk": False,
        "option_plan": {
            "recommended_contract": None,
            "expected_underlying_target": target,
            "invalidation_price": invalidation,
            "take_profit_1": 0.01,
            "take_profit_2": 0.02,
        },
        "forecast": {"forecast_horizon_bars": 1},
    }


def _find_open_trade(order_id: str) -> tuple[int | None, dict[str, Any] | None]:
    open_trades = sdm.read_open_trades()
    if open_trades.empty or "order_id" not in open_trades.columns:
        return None, None
    matches = open_trades["order_id"].astype(str).str.strip() == str(order_id or "").strip()
    if not matches.any():
        return None, None
    index = int(open_trades.index[matches][0])
    return index, open_trades.loc[index].to_dict()


def _apply_automation_markers(
    *,
    tenant: Tenant,
    profile_key: str,
    lifecycle_id: str,
    trade_id: str,
    order_id: str,
    position_opened: bool,
) -> dict[str, Any] | None:
    markers = {
        "automation_origin": "trade_automation",
        "automation_tenant_id": tenant.id,
        "automation_tenant_slug": tenant.slug,
        "automation_profile_key": profile_key,
        "automation_execution_intent": "broker_paper",
        "paper_order_lifecycle_soak_id": lifecycle_id,
    }
    if position_opened:
        return sdm.update_open_trade(markers, trade_id=trade_id, order_id=order_id)
    return sdm.update_pending_order(order_id, markers)


def _finalize_report(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None,
    actor: Any,
    report: dict[str, Any],
) -> dict[str, Any]:
    blockers = list(report.get("blockers") or [])
    warnings = list(report.get("warnings") or [])
    status = "blocked" if blockers else "warning" if warnings else "completed"
    report["status"] = status
    report["label"] = {
        "completed": "Paper order lifecycle complete",
        "warning": "Paper order lifecycle warning",
        "blocked": "Paper order lifecycle blocked",
    }.get(status, "Paper order lifecycle soak")
    report["manual_action_required"] = bool(blockers)
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
        "current_step",
        "ticker",
        "local_order_id",
        "local_trade_id",
        "broker_order_id",
        "broker_status",
        "terminal_state",
        "reconciliation_status",
        "reconciliation_note_id",
        "fill_evidence",
        "cancel_evidence",
        "close_evidence",
        "blockers",
        "warnings",
        "note_id",
        "related_note_id",
        "manual_action_required",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["paper_order_lifecycle_soak_last_report"] = serialize_value(summary)
    runtime["paper_order_lifecycle_soak_last_note_id"] = note_id
    runtime["paper_order_lifecycle_soak_note_session_day"] = report.get("session_day")
    runtime["paper_order_lifecycle_soak_last_run_at"] = report.get("checked_at")
    runtime["paper_order_lifecycle_soak_last_error"] = None
    history = list(runtime.get("paper_order_lifecycle_soak_history") or [])
    history.insert(
        0,
        {
            "at": report.get("checked_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "terminal_state": report.get("terminal_state"),
            "broker_order_id": report.get("broker_order_id"),
            "order_id": report.get("local_order_id"),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "note_id": note_id,
        },
    )
    runtime["paper_order_lifecycle_soak_history"] = serialize_value(history[:PAPER_ORDER_LIFECYCLE_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.paper_order_lifecycle_soaked",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "terminal_state": report.get("terminal_state"),
                "broker_order_id": report.get("broker_order_id"),
                "order_id": report.get("local_order_id"),
                "note_id": note_id,
            },
        )
    return serialize_value(report)


def run_paper_order_lifecycle_soak(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    current_user: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    session_day = _session_day_for(now)
    lifecycle_id = str(uuid4())
    ticker = PAPER_ORDER_LIFECYCLE_TICKER
    interval = "1m"
    quantity = PAPER_ORDER_LIFECYCLE_QUANTITY
    limit_price = PAPER_ORDER_LIFECYCLE_LIMIT_PRICE
    trade_id = str(uuid4())
    order_id = str(uuid4())
    route_correlation_id = str(uuid4())
    report: dict[str, Any] = {
        "status": "running",
        "profile_key": profile_key,
        "linked_account_id": getattr(linked_account, "id", None),
        "session_day": session_day,
        "checked_at": _serialize_datetime(now),
        "current_step": "preflight",
        "ticker": ticker,
        "quantity": quantity,
        "limit_price": limit_price,
        "local_order_id": order_id,
        "local_trade_id": trade_id,
        "route_correlation_id": route_correlation_id,
        "broker_order_id": None,
        "broker_status": None,
        "terminal_state": None,
        "reconciliation_status": "not_run",
        "fill_evidence": {},
        "cancel_evidence": {},
        "close_evidence": {},
        "blockers": [],
        "warnings": [],
        "steps": [],
    }

    if profile_key != PAPER_ORDER_LIFECYCLE_PERSONAL_PAPER_PROFILE:
        raise ValidationServiceError("Paper order lifecycle soak is only available for the personal paper automation profile.")
    settings = dict(state.get("settings") or {})
    if str(settings.get("execution_intent") or "broker_paper").strip().lower() != "broker_paper":
        report["blockers"].append({"key": "paper_route_required", "detail": "Lifecycle soak requires the personal paper profile to use broker paper routing."})
        _safe_step(report, "preflight", "blocked", "Profile is not configured for broker-paper routing.")
        return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
    if bool(settings.get("kill_switch")):
        report["blockers"].append({"key": "safety_lock_active", "detail": "Lifecycle soak will not submit paper orders while the profile kill switch is active."})
        _safe_step(report, "preflight", "blocked", "Kill switch is active; no order was submitted.")
        return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)

    adapter = get_execution_adapter_for("alpaca_paper")
    try:
        ensure = getattr(adapter, "_ensure_credentials", None)
        if callable(ensure):
            ensure()
    except Exception as exc:
        report["warnings"].append({"key": "paper_credentials_missing", "detail": str(exc) or "Alpaca paper credentials are not configured."})
        _safe_step(report, "preflight", "warning", "Alpaca paper credentials are unavailable; no order was submitted.")
        return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)

    request = OpenTradeRequest(
        ticker=ticker,
        interval=interval,
        horizon=1,
        live_price=limit_price,
        account_size=10000.0,
        risk_percent=0.01,
        requested_quantity=quantity,
        instrument_type="equity",
        broker_side="buy",
        execution_intent="broker_paper",
        order_type="limit",
        time_in_force="day",
        limit_price=limit_price,
        fractional_shares_only=True,
        regular_hours_only=True,
        route_family="paper_order_lifecycle_soak",
        route_version="v1",
        automation_entry_reason="paper_order_lifecycle_soak",
        thesis_direction="bullish",
        source="paper_order_lifecycle_soak",
    )
    analysis = _build_soak_report(ticker, interval, limit_price)
    position = {
        "suggested_contracts": quantity,
        "total_position_cost": quantity * limit_price,
        "max_risk_dollars": quantity * limit_price,
    }
    order_ticket = {
        "trade_id": trade_id,
        "order_id": order_id,
        "route_correlation_id": route_correlation_id,
        "instrument_type": "equity",
        "contract_symbol": f"EQUITY:{ticker}",
        "broker_side": "BUY",
        "order_type": "limit",
        "time_in_force": "day",
        "limit_price": limit_price,
        "fractional_shares_only": True,
        "route_family": "paper_order_lifecycle_soak",
        "route_version": "v1",
        "source": "paper_order_lifecycle_soak",
        "automation_entry_reason": "paper_order_lifecycle_soak",
        "tenant_id": tenant.id,
        "tenant_slug": tenant.slug,
    }

    _safe_step(report, "submit", "running", "Submitting one tiny broker-paper limit order.")
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=ticker,
        event_key="order.submitted",
        status="submitting",
        order_type="limit",
        time_in_force="day",
        route_state="submitting",
        book_state="pending",
        detail="Submitting paper order lifecycle soak limit order.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "automation_profile_key": profile_key,
            "paper_order_lifecycle_soak_id": lifecycle_id,
            "request": serialize_value(request.model_dump()),
        },
    )
    try:
        submit_result = adapter.submit_order(
            request=request,
            report=analysis,
            live_price=limit_price,
            position=position,
            trade_id=trade_id,
            order_id=order_id,
            order_ticket=order_ticket,
        )
    except Exception as exc:
        report["blockers"].append({"key": "submit_failed", "detail": str(exc) or "Broker-paper order submission failed."})
        _safe_step(report, "submit", "blocked", "Broker-paper order submission failed.")
        return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)

    marker_row = _apply_automation_markers(
        tenant=tenant,
        profile_key=profile_key,
        lifecycle_id=lifecycle_id,
        trade_id=trade_id,
        order_id=order_id,
        position_opened=bool(submit_result.position_opened),
    )
    if marker_row is None:
        report["warnings"].append({"key": "marker_update_missing", "detail": "Lifecycle soak order was submitted but automation marker metadata could not be written."})
    report["broker_order_id"] = submit_result.broker_order_id
    report["broker_status"] = submit_result.broker_status
    _safe_step(
        report,
        "submit",
        "filled" if submit_result.position_opened else "accepted",
        "Broker-paper order filled immediately." if submit_result.position_opened else "Broker-paper order accepted as working.",
        broker_order_id=submit_result.broker_order_id,
        broker_status=submit_result.broker_status,
    )
    _record_order_event(
        db,
        tenant=tenant,
        actor=actor,
        trade_id=trade_id,
        ticker=ticker,
        event_key="order.filled" if submit_result.position_opened else "order.accepted",
        status="filled" if submit_result.position_opened else "working",
        order_type="limit",
        time_in_force="day",
        route_state="filled" if submit_result.position_opened else "accepted",
        book_state="open" if submit_result.position_opened else "pending",
        detail="Paper lifecycle soak order filled immediately." if submit_result.position_opened else "Paper lifecycle soak order is working.",
        payload={
            "order_id": order_id,
            "route_correlation_id": route_correlation_id,
            "automation_profile_key": profile_key,
            "paper_order_lifecycle_soak_id": lifecycle_id,
            "execution": {
                "adapter": submit_result.broker_name,
                "broker_order_id": submit_result.broker_order_id,
                "broker_status": submit_result.broker_status,
            },
            "record": serialize_value(submit_result.record),
        },
    )

    opened_record = submit_result.record if submit_result.position_opened else None
    if not submit_result.position_opened:
        _safe_step(report, "sync", "running", "Syncing the working paper order from broker state.")
        sync_result = sync_pending_orders_from_broker(db=db, current_user=current_user, order_id=order_id)
        sync_items = list((sync_result or {}).get("items") or [])
        sync_item = sync_items[0] if sync_items else {}
        sync_state = str(sync_item.get("state") or "").strip().lower()
        _safe_step(report, "sync", sync_state or "checked", sync_item.get("detail") or "Broker sync completed.")
        if sync_state == "filled":
            report["fill_evidence"] = {
                "state": "filled",
                "broker_order_id": sync_item.get("broker_order_id"),
                "broker_status": sync_item.get("broker_status"),
                "slippage_bps": sync_item.get("slippage_bps"),
            }
            _, opened_record = _find_open_trade(order_id)
        else:
            _safe_step(report, "cancel", "running", "Canceling the unfilled paper lifecycle soak order.")
            try:
                cancel_result = adapter.cancel_order(order_id=order_id)
            except Exception as exc:
                report["blockers"].append({"key": "cancel_failed", "detail": str(exc) or "Broker-paper cancellation failed."})
                _safe_step(report, "cancel", "blocked", "Broker-paper cancellation failed.")
                return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
            if cancel_result is None:
                report["blockers"].append({"key": "cancel_missing", "detail": "Broker-paper cancellation returned no local terminal order evidence."})
                _safe_step(report, "cancel", "blocked", "Broker-paper cancellation did not produce local terminal evidence.")
                return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
            report["terminal_state"] = "canceled"
            report["broker_status"] = cancel_result.broker_status or report.get("broker_status")
            report["cancel_evidence"] = {
                "canceled": True,
                "broker_order_id": cancel_result.broker_order_id,
                "broker_status": cancel_result.broker_status,
                "order_id": order_id,
            }
            _safe_step(report, "cancel", "canceled", "Unfilled paper lifecycle soak order was canceled.")
            _record_order_event(
                db,
                tenant=tenant,
                actor=actor,
                trade_id=trade_id,
                ticker=ticker,
                event_key="order.canceled",
                status="canceled",
                order_type="limit",
                time_in_force="day",
                route_state="canceled",
                book_state="flat",
                detail="Canceled the unfilled paper lifecycle soak order.",
                payload={
                    "order_id": order_id,
                    "route_correlation_id": route_correlation_id,
                    "automation_profile_key": profile_key,
                    "paper_order_lifecycle_soak_id": lifecycle_id,
                    "order": serialize_value(cancel_result.canceled_order),
                    "execution": {
                        "adapter": cancel_result.broker_name,
                        "broker_order_id": cancel_result.broker_order_id,
                        "broker_status": cancel_result.broker_status,
                    },
                },
                audit_event_type="trade.order_canceled",
            )

    if opened_record:
        _, opened_record = _find_open_trade(order_id)
        if not opened_record:
            report["blockers"].append({"key": "fill_ledger_missing", "detail": "Broker-paper fill was observed but no local open trade row was found."})
            _safe_step(report, "fill", "blocked", "Local open trade evidence is missing after broker-paper fill.")
            return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
        report["fill_evidence"] = {
            "state": "filled",
            "broker_order_id": opened_record.get("broker_order_id"),
            "broker_status": opened_record.get("broker_status"),
            "order_id": order_id,
            "trade_id": trade_id,
        }
        open_index, target_trade = _find_open_trade(order_id)
        if open_index is None or target_trade is None:
            report["blockers"].append({"key": "close_target_missing", "detail": "Could not resolve the local open trade index for the paper lifecycle close."})
            _safe_step(report, "close", "blocked", "Local close target is missing.")
            return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
        close_price = _coerce_float(target_trade.get("actual_fill_price") or target_trade.get("broker_filled_avg_price") or target_trade.get("live_price_at_open"), limit_price)
        _safe_step(report, "close", "running", "Closing the filled paper lifecycle soak position with a quantity-scoped paper order.")
        try:
            close_result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=open_index,
                    close_underlying_price=max(close_price, 0.0001),
                    close_contract_mid=max(close_price / 100.0, 0.0001),
                    close_fraction=0.999,
                ),
                target_trade=target_trade,
            )
        except Exception as exc:
            report["blockers"].append({"key": "close_failed", "detail": str(exc) or "Broker-paper close failed after the lifecycle soak fill."})
            _safe_step(report, "close", "blocked", "Broker-paper close failed after fill.")
            return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
        report["terminal_state"] = "closed"
        report["close_evidence"] = {
            "closed": True,
            "broker_order_id": close_result.broker_order_id,
            "broker_status": close_result.broker_status,
            "order_id": order_id,
            "trade_id": trade_id,
        }
        _safe_step(report, "close", "closed", "Filled paper lifecycle soak position was closed.")
        _record_order_event(
            db,
            tenant=tenant,
            actor=actor,
            trade_id=trade_id,
            ticker=ticker,
            event_key="order.closed",
            status="closed",
            order_type="market",
            time_in_force="day",
            route_state="closed",
            book_state="flat",
            detail="Closed the filled paper lifecycle soak position.",
            payload={
                "order_id": order_id,
                "route_correlation_id": route_correlation_id,
                "automation_profile_key": profile_key,
                "paper_order_lifecycle_soak_id": lifecycle_id,
                "trade": serialize_value(close_result.closed_trade),
                "execution": {
                    "adapter": close_result.broker_name,
                    "broker_order_id": close_result.broker_order_id,
                    "broker_status": close_result.broker_status,
                },
            },
            audit_event_type="trade.order_closed",
        )

    _safe_step(report, "reconciliation", "running", "Running paper broker reconciliation after lifecycle terminal state.")
    reconciliation = automation_paper_broker_reconciliation_service.run_paper_broker_reconciliation(
        db,
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        linked_account=linked_account,
        actor=actor,
        now=now,
        run_source="lifecycle_soak",
    )
    report["reconciliation_status"] = reconciliation.get("status") or "not_run"
    report["reconciliation_note_id"] = reconciliation.get("related_note_id") or reconciliation.get("note_id")
    if str(report["reconciliation_status"]).lower() == "blocked" or reconciliation.get("blockers"):
        report["blockers"].append({"key": "reconciliation_blocked", "detail": "Paper broker reconciliation found unresolved lifecycle mismatches."})
    elif str(report["reconciliation_status"]).lower() == "warning" or reconciliation.get("warnings"):
        report["warnings"].append({"key": "reconciliation_warning", "detail": "Paper broker reconciliation completed with advisory warnings."})
    _safe_step(report, "reconciliation", str(report["reconciliation_status"]), "Paper broker reconciliation completed.")
    if not report.get("terminal_state"):
        report["terminal_state"] = "checked"
    return _finalize_report(db, tenant=tenant, state=state, profile_key=profile_key, linked_account=linked_account, actor=actor, report=report)
