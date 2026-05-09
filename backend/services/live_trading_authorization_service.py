from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.saas import BrokerageLinkedAccount, LiveTradingAuthorization, StrategyVersion
from backend.services.billing_service import enforce_entitlement_limit, increment_entitlement_usage, require_entitlement
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.live_trading_common import (
    assert_live_manager,
    assert_live_reader,
    ensure_strategy_version,
    load_authorization_or_raise,
    normalize_symbols,
    record_live_evidence,
    resolve_tenant_and_user,
    serialize_authorization,
    utc_now,
)
from backend.services.strategy_readiness_score_service import load_strategy_or_raise


def _payload(request: Any) -> dict[str, Any]:
    return request.model_dump() if hasattr(request, "model_dump") else dict(request or {})


def create_live_authorization(db: Session, *, current_user: Any, request: Any) -> dict[str, Any]:
    assert_live_manager(current_user)
    require_entitlement(db, current_user, "live_canary", message="Live authorizations require the live canary entitlement.")
    require_entitlement(db, current_user, "live_authorizations", message="This plan does not allow live authorization records.")
    tenant, user = resolve_tenant_and_user(db, current_user)
    if user is None:
        raise ValidationError("A persisted user is required before signing a live authorization.")
    data = _payload(request)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=str(data.get("strategy_id") or ""))
    version = ensure_strategy_version(db, tenant_id=tenant.id, strategy=strategy, user_id=user.id)
    if data.get("strategy_version_id"):
        selected = db.execute(
            select(StrategyVersion).where(
                StrategyVersion.tenant_id == tenant.id,
                StrategyVersion.strategy_desk_id == strategy.id,
                StrategyVersion.id == data["strategy_version_id"],
            )
        ).scalar_one_or_none()
        if selected is None:
            raise NotFoundError("The requested strategy version could not be found.")
        version = selected
    linked_account = db.execute(
        select(BrokerageLinkedAccount).where(
            BrokerageLinkedAccount.tenant_id == tenant.id,
            BrokerageLinkedAccount.id == str(data.get("linked_account_id") or ""),
        )
    ).scalar_one_or_none()
    if linked_account is None:
        raise NotFoundError("The requested linked brokerage account could not be found.")
    current_count = db.scalar(
        select(func.count(LiveTradingAuthorization.id)).where(
            LiveTradingAuthorization.tenant_id == tenant.id,
            LiveTradingAuthorization.status != "revoked",
        )
    ) or 0
    enforce_entitlement_limit(
        db,
        current_user,
        "live_authorizations",
        requested_total=int(current_count) + 1,
        resource_label="live authorizations",
    )
    now = utc_now()
    authorization = LiveTradingAuthorization(
        tenant_id=tenant.id,
        user_id=user.id,
        strategy_desk_id=strategy.id,
        strategy_version_id=version.id,
        linked_account_id=linked_account.id,
        authorization_type=str(data.get("authorization_type") or "supervised_live"),
        authorized_mode=str(data.get("authorized_mode") or "approval_required"),
        max_capital_allocation=float(data.get("max_capital_allocation") or 0),
        max_daily_loss=float(data.get("max_daily_loss") or 0),
        max_order_notional=float(data.get("max_order_notional") or 0),
        allowed_symbols_json=normalize_symbols(data.get("allowed_symbols") or []),
        allowed_instruments_json=normalize_symbols(data.get("allowed_instruments") or []),
        risk_acknowledgement_version=data.get("risk_acknowledgement_version"),
        status="signed" if bool(data.get("signed")) else "pending_signature",
        signed_at=now if bool(data.get("signed")) else None,
    )
    db.add(authorization)
    db.flush()
    increment_entitlement_usage(db, current_user, "live_authorizations")
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.authorization_created",
        aggregate_type="live_authorization",
        aggregate_id=authorization.id,
        payload={"strategy_id": strategy.id, "linked_account_id": linked_account.id, "status": authorization.status},
    )
    db.commit()
    return {"authorization": serialize_authorization(authorization), "next_action": "Arm the strategy after readiness and risk gates pass."}


def list_live_authorizations(db: Session, *, current_user: Any, strategy_id: str | None = None) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    statement = select(LiveTradingAuthorization).where(LiveTradingAuthorization.tenant_id == tenant.id)
    if strategy_id:
        strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
        statement = statement.where(LiveTradingAuthorization.strategy_desk_id == strategy.id)
    rows = db.execute(statement.order_by(LiveTradingAuthorization.created_at.desc())).scalars().all()
    return {"items": [serialize_authorization(row) for row in rows], "count": len(rows)}


def get_live_authorization(db: Session, *, current_user: Any, authorization_id: str) -> dict[str, Any]:
    assert_live_reader(current_user)
    tenant, _user = resolve_tenant_and_user(db, current_user)
    authorization = load_authorization_or_raise(db, tenant_id=tenant.id, authorization_id=authorization_id)
    return {"authorization": serialize_authorization(authorization)}


def revoke_live_authorization(db: Session, *, current_user: Any, authorization_id: str, request: Any | None = None) -> dict[str, Any]:
    assert_live_manager(current_user)
    tenant, user = resolve_tenant_and_user(db, current_user)
    authorization = load_authorization_or_raise(db, tenant_id=tenant.id, authorization_id=authorization_id)
    data = _payload(request)
    authorization.status = "revoked"
    authorization.revoked_at = utc_now()
    authorization.updated_at = utc_now()
    record_live_evidence(
        db,
        tenant=tenant,
        user=user,
        event_type="live.authorization_revoked",
        aggregate_type="live_authorization",
        aggregate_id=authorization.id,
        payload={"strategy_id": authorization.strategy_desk_id, "reason": data.get("reason")},
    )
    db.commit()
    return {"authorization": serialize_authorization(authorization), "next_action": "Create a new authorization before live trading can resume."}
