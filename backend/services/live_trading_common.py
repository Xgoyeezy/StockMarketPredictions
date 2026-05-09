from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import (
    BrokerageLinkedAccount,
    BrokerExecutionReceipt,
    DomainEventLog,
    LiveKillSwitchEvent,
    LiveOrderIntent,
    LiveRiskCheck,
    LiveTradingAuthorization,
    LiveTradingSession,
    RiskPolicy,
    StrategyDesk,
    StrategyVersion,
)
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import NotFoundError
from backend.services.permissions import require_current_user_permission
from backend.services.strategy_readiness_score_service import latest_readiness_snapshot, load_strategy_or_raise
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user

LIVE_SESSION_ACTIVE_STATUSES = {"armed", "live", "paused"}
LIVE_SESSION_ORDERABLE_STATUSES = {"armed", "live"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def as_float(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def normalize_symbols(items: Any) -> list[str]:
    return sorted({str(item or "").strip().upper() for item in list(items or []) if str(item or "").strip()})


def assert_live_reader(current_user: Any) -> None:
    require_current_user_permission(current_user, "live.read", "You do not have permission to view live trading controls.")


def assert_live_manager(current_user: Any) -> None:
    require_current_user_permission(current_user, "live.manage", "You do not have permission to manage live trading controls.")


def assert_live_approver(current_user: Any) -> None:
    require_current_user_permission(current_user, "live.approve", "You do not have permission to approve live orders.")


def resolve_tenant_and_user(db: Session, current_user: Any):
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    return tenant, user


def ensure_strategy_version(db: Session, *, tenant_id: str, strategy: StrategyDesk, user_id: str | None = None) -> StrategyVersion:
    version = db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.tenant_id == tenant_id, StrategyVersion.strategy_desk_id == strategy.id, StrategyVersion.status == "active")
        .order_by(StrategyVersion.version_number.desc())
    ).scalars().first()
    if version is not None:
        return version
    version = db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.tenant_id == tenant_id, StrategyVersion.strategy_desk_id == strategy.id)
        .order_by(StrategyVersion.version_number.desc())
    ).scalars().first()
    if version is not None:
        return version
    version = StrategyVersion(
        tenant_id=tenant_id,
        strategy_desk_id=strategy.id,
        version_number=1,
        name=strategy.name,
        status="active",
        config_json=dict(strategy.config_json or {}),
        risk_profile_json=dict((strategy.config_json or {}).get("risk_profile") or {}),
        created_by_user_id=user_id,
        activated_at=utc_now(),
    )
    db.add(version)
    db.flush()
    return version


def load_authorization_or_raise(db: Session, *, tenant_id: str, authorization_id: str) -> LiveTradingAuthorization:
    row = db.execute(
        select(LiveTradingAuthorization).where(
            LiveTradingAuthorization.tenant_id == tenant_id,
            LiveTradingAuthorization.id == authorization_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("The requested live authorization could not be found.")
    return row


def load_live_session_or_raise(db: Session, *, tenant_id: str, session_id: str) -> LiveTradingSession:
    row = db.execute(
        select(LiveTradingSession).where(LiveTradingSession.tenant_id == tenant_id, LiveTradingSession.id == session_id)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("The requested live session could not be found.")
    return row


def load_live_order_or_raise(db: Session, *, tenant_id: str, order_intent_id: str) -> LiveOrderIntent:
    row = db.execute(
        select(LiveOrderIntent).where(LiveOrderIntent.tenant_id == tenant_id, LiveOrderIntent.id == order_intent_id)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("The requested live order intent could not be found.")
    return row


def latest_live_session(db: Session, *, tenant_id: str, strategy_id: str, statuses: set[str] | None = None) -> LiveTradingSession | None:
    statement = select(LiveTradingSession).where(
        LiveTradingSession.tenant_id == tenant_id,
        LiveTradingSession.strategy_desk_id == strategy_id,
    )
    if statuses:
        statement = statement.where(LiveTradingSession.status.in_(statuses))
    return db.execute(statement.order_by(LiveTradingSession.created_at.desc())).scalars().first()


def latest_active_risk_policy(db: Session, *, tenant_id: str, strategy_id: str) -> RiskPolicy | None:
    return db.execute(
        select(RiskPolicy)
        .where(RiskPolicy.tenant_id == tenant_id, RiskPolicy.strategy_desk_id == strategy_id, RiskPolicy.status == "active")
        .order_by(RiskPolicy.updated_at.desc())
    ).scalars().first()


def active_live_kill_switches(
    db: Session,
    *,
    tenant_id: str,
    strategy_id: str | None = None,
    session_id: str | None = None,
) -> list[LiveKillSwitchEvent]:
    statement = select(LiveKillSwitchEvent).where(
        LiveKillSwitchEvent.tenant_id == tenant_id,
        LiveKillSwitchEvent.status == "active",
    )
    if strategy_id:
        statement = statement.where(
            (LiveKillSwitchEvent.scope == "tenant")
            | (LiveKillSwitchEvent.strategy_desk_id == strategy_id)
        )
    if session_id:
        statement = statement.where(
            (LiveKillSwitchEvent.scope == "tenant")
            | (LiveKillSwitchEvent.live_trading_session_id == session_id)
        )
    return db.execute(statement.order_by(LiveKillSwitchEvent.triggered_at.desc())).scalars().all()


def provider_live_gate(linked_account: BrokerageLinkedAccount | None) -> dict[str, Any]:
    blockers: list[dict[str, str]] = []
    if not getattr(settings, "feature_live_trading", False):
        blockers.append({"key": "live_feature_disabled", "message": "FEATURE_LIVE_TRADING is false."})
    provider = str(getattr(linked_account, "provider", "") or "unknown").strip().lower()
    if provider == "alpaca" and not getattr(settings, "alpaca_live_trading_enabled", False):
        blockers.append({"key": "alpaca_live_disabled", "message": "ALPACA_LIVE_TRADING_ENABLED is false."})
    elif provider == "tradier" and not (getattr(settings, "tradier_live_token", "") and getattr(settings, "tradier_live_account_id", "")):
        blockers.append({"key": "tradier_live_disabled", "message": "Tradier live credentials are not configured."})
    elif provider in {"internal", "internal_paper", "paper", "unknown", ""}:
        blockers.append({"key": "live_provider_unavailable", "message": "The linked account is not configured for a live broker route."})
    return {"provider": provider or "unknown", "enabled": not blockers, "blockers": blockers}


def current_readiness(db: Session, *, tenant_id: str, strategy_id: str) -> dict[str, Any] | None:
    return latest_readiness_snapshot(db, tenant_id=tenant_id, strategy_id=strategy_id)


def record_live_evidence(
    db: Session,
    *,
    tenant: Any,
    user: Any,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str | None,
    payload: dict[str, Any] | None = None,
) -> None:
    payload = dict(payload or {})
    record_audit_event(db, event_type=event_type, tenant=tenant, user=user, payload=payload)
    db.add(
        DomainEventLog(
            tenant_id=tenant.id if tenant else None,
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            status="recorded",
            payload_json=payload,
            metadata_json={"source": "live_control_plane"},
            processed_at=utc_now(),
        )
    )


def serialize_authorization(row: LiveTradingAuthorization | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "user_id": row.user_id,
        "strategy_id": row.strategy_desk_id,
        "strategy_version_id": row.strategy_version_id,
        "linked_account_id": row.linked_account_id,
        "authorization_type": row.authorization_type,
        "authorized_mode": row.authorized_mode,
        "max_capital_allocation": as_float(row.max_capital_allocation),
        "max_daily_loss": as_float(row.max_daily_loss),
        "max_order_notional": as_float(row.max_order_notional),
        "allowed_symbols": as_list(row.allowed_symbols_json),
        "allowed_instruments": as_list(row.allowed_instruments_json),
        "risk_acknowledgement_version": row.risk_acknowledgement_version,
        "status": row.status,
        "signed_at": iso(row.signed_at),
        "revoked_at": iso(row.revoked_at),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def serialize_session(row: LiveTradingSession | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "strategy_id": row.strategy_desk_id,
        "strategy_version_id": row.strategy_version_id,
        "linked_account_id": row.linked_account_id,
        "authorization_id": row.authorization_id,
        "status": row.status,
        "started_at": iso(row.started_at),
        "paused_at": iso(row.paused_at),
        "stopped_at": iso(row.stopped_at),
        "killed_at": iso(row.killed_at),
        "last_heartbeat_at": iso(row.last_heartbeat_at),
        "realized_pnl": as_float(row.realized_pnl),
        "unrealized_pnl": as_float(row.unrealized_pnl),
        "max_drawdown": as_float(row.max_drawdown),
        "order_count": int(row.order_count or 0),
        "blocked_order_count": int(row.blocked_order_count or 0),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def serialize_risk_check(row: LiveRiskCheck | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "order_intent_id": row.live_order_intent_id,
        "strategy_id": row.strategy_desk_id,
        "risk_policy_id": row.risk_policy_id,
        "status": row.status,
        "score": int(row.score or 0),
        "checks": as_list(as_dict(row.checks_json).get("items")) or row.checks_json or [],
        "blockers": as_list(row.blockers_json),
        "warnings": as_list(row.warnings_json),
        "created_at": iso(row.created_at),
    }


def serialize_receipt(row: BrokerExecutionReceipt | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "order_intent_id": row.live_order_intent_id,
        "broker": row.broker,
        "broker_account_id": row.broker_account_id,
        "broker_order_id": row.broker_order_id,
        "status": row.status,
        "submitted_payload": dict(row.submitted_payload_json or {}),
        "response_payload": dict(row.response_payload_json or {}),
        "filled_quantity": as_float(row.filled_quantity),
        "average_fill_price": as_float(row.average_fill_price),
        "fees": as_float(row.fees),
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }


def serialize_order_intent(row: LiveOrderIntent | None, *, include_children: bool = True) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = {
        "id": row.id,
        "live_trading_session_id": row.live_trading_session_id,
        "strategy_id": row.strategy_desk_id,
        "trade_decision_id": row.trade_decision_id,
        "symbol": row.symbol,
        "instrument_type": row.instrument_type,
        "side": row.side,
        "quantity": as_float(row.quantity),
        "order_type": row.order_type,
        "limit_price": as_float(row.limit_price) if row.limit_price is not None else None,
        "stop_price": as_float(row.stop_price) if row.stop_price is not None else None,
        "time_in_force": row.time_in_force,
        "notional_value": as_float(row.notional_value),
        "status": row.status,
        "requires_user_approval": bool(row.requires_user_approval),
        "approved_by_user_id": row.approved_by_user_id,
        "approved_at": iso(row.approved_at),
        "submitted_at": iso(row.submitted_at),
        "rejected_at": iso(row.rejected_at),
        "rejection_reason": row.rejection_reason,
        "duplicate_key": row.duplicate_key,
        "created_at": iso(row.created_at),
        "updated_at": iso(row.updated_at),
    }
    if include_children:
        risk_checks = sorted(list(row.risk_checks or []), key=lambda item: item.created_at or utc_now(), reverse=True)
        receipts = sorted(list(row.broker_execution_receipts or []), key=lambda item: item.created_at or utc_now(), reverse=True)
        payload["latest_risk_check"] = serialize_risk_check(risk_checks[0]) if risk_checks else None
        payload["latest_receipt"] = serialize_receipt(receipts[0]) if receipts else None
    return payload


def serialize_kill_switch(row: LiveKillSwitchEvent | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "strategy_id": row.strategy_desk_id,
        "live_trading_session_id": row.live_trading_session_id,
        "scope": row.scope,
        "reason": row.reason,
        "triggered_by": row.triggered_by,
        "triggered_by_user_id": row.triggered_by_user_id,
        "triggered_at": iso(row.triggered_at),
        "cleared_by_user_id": row.cleared_by_user_id,
        "cleared_at": iso(row.cleared_at),
        "status": row.status,
        "created_at": iso(row.created_at),
    }
