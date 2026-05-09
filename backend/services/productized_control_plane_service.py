from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.saas import (
    AuditEvent,
    AuditExport,
    DecisionReplayEvent,
    ExecutionQualitySnapshot,
    RiskEvent,
    RiskPolicy,
    StrategyDeployment,
    StrategyDesk,
    StrategyRun,
    StrategyVersion,
    TradeDecision,
)
from backend.services.audit_service import record_audit_event
from backend.services.billing_service import enforce_entitlement_limit, require_entitlement
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.permissions import require_current_user_permission
from backend.services.strategy_engine.service import ensure_strategy_desks
from backend.services.strategy_readiness_score_service import (
    create_strategy_promotion_gate,
    evaluate_strategy_readiness,
    latest_readiness_snapshot,
    load_strategy_or_raise,
)
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value or []) if isinstance(value, list) else []


def _normalize_symbol_list(items: list[str] | None) -> list[str]:
    return sorted({str(item or "").strip().upper() for item in list(items or []) if str(item or "").strip()})


def _assert_reader(current_user: Any) -> None:
    require_current_user_permission(current_user, "tenant.read", "You do not have permission to view this control plane.")


def _assert_strategy_manager(current_user: Any) -> None:
    require_current_user_permission(current_user, "strategy.manage", "You do not have permission to manage strategies.")


def _assert_risk_manager(current_user: Any) -> None:
    require_current_user_permission(current_user, "risk.manage", "You do not have permission to manage risk controls.")


def _serialize_version(version: StrategyVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "id": version.id,
        "version_number": version.version_number,
        "name": version.name,
        "description": version.description,
        "status": version.status,
        "source_type": version.source_type,
        "source_hash": version.source_hash,
        "config": dict(version.config_json or {}),
        "risk_profile": dict(version.risk_profile_json or {}),
        "created_by_user_id": version.created_by_user_id,
        "created_at": _iso(version.created_at),
        "activated_at": _iso(version.activated_at),
        "retired_at": _iso(version.retired_at),
    }


def _serialize_deployment(deployment: StrategyDeployment | None) -> dict[str, Any] | None:
    if deployment is None:
        return None
    return {
        "id": deployment.id,
        "strategy_id": deployment.strategy_desk_id,
        "strategy_version_id": deployment.strategy_version_id,
        "linked_account_id": deployment.linked_account_id,
        "mode": deployment.mode,
        "status": deployment.status,
        "allocation_cap": deployment.allocation_cap,
        "max_order_notional": deployment.max_order_notional,
        "max_daily_loss": deployment.max_daily_loss,
        "started_at": _iso(deployment.started_at),
        "stopped_at": _iso(deployment.stopped_at),
        "last_heartbeat_at": _iso(deployment.last_heartbeat_at),
        "metadata": dict(deployment.metadata_json or {}),
        "created_at": _iso(deployment.created_at),
        "updated_at": _iso(deployment.updated_at),
    }


def _latest_version(db: Session, *, tenant_id: str, strategy_id: str) -> StrategyVersion | None:
    return db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.tenant_id == tenant_id, StrategyVersion.strategy_desk_id == strategy_id)
        .order_by(StrategyVersion.version_number.desc())
    ).scalars().first()


def _active_version(db: Session, *, tenant_id: str, strategy_id: str) -> StrategyVersion | None:
    return db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.tenant_id == tenant_id, StrategyVersion.strategy_desk_id == strategy_id, StrategyVersion.status == "active")
        .order_by(StrategyVersion.version_number.desc())
    ).scalars().first() or _latest_version(db, tenant_id=tenant_id, strategy_id=strategy_id)


def _latest_deployment(db: Session, *, tenant_id: str, strategy_id: str) -> StrategyDeployment | None:
    return db.execute(
        select(StrategyDeployment)
        .where(StrategyDeployment.tenant_id == tenant_id, StrategyDeployment.strategy_desk_id == strategy_id)
        .order_by(StrategyDeployment.created_at.desc())
    ).scalars().first()


def _serialize_strategy(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    version = _active_version(db, tenant_id=tenant_id, strategy_id=strategy.id)
    deployment = _latest_deployment(db, tenant_id=tenant_id, strategy_id=strategy.id)
    runtime = _as_dict(strategy.runtime_json)
    metadata = _as_dict(strategy.metadata_json)
    return {
        "id": strategy.id,
        "strategy_desk_id": strategy.id,
        "desk_key": strategy.desk_key,
        "name": strategy.name,
        "description": metadata.get("description"),
        "status": metadata.get("status") or strategy.lifecycle_stage,
        "lifecycle_stage": strategy.lifecycle_stage,
        "category": strategy.category,
        "mode": deployment.mode if deployment else strategy.trading_mode,
        "trading_mode": strategy.trading_mode,
        "enabled": strategy.enabled,
        "paper_trading_enabled": strategy.paper_trading_enabled,
        "allocation_cap": float(runtime.get("allocation_cap") or strategy.config_json.get("allocation_cap") or 0.0),
        "symbols": list(strategy.config_json.get("symbols") or []),
        "config": dict(strategy.config_json or {}),
        "risk_profile": dict((version.risk_profile_json if version else {}) or strategy.config_json.get("risk_profile") or {}),
        "runtime": runtime,
        "metadata": metadata,
        "current_version_id": version.id if version else None,
        "current_version": _serialize_version(version),
        "latest_deployment": _serialize_deployment(deployment),
        "readiness": latest_readiness_snapshot(db, tenant_id=tenant_id, strategy_id=strategy.id),
        "created_at": _iso(strategy.created_at),
        "updated_at": _iso(strategy.updated_at),
    }


def list_strategies(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    rows = db.execute(select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id).order_by(StrategyDesk.created_at.asc())).scalars().all()
    return {"items": [_serialize_strategy(db, tenant_id=tenant.id, strategy=row) for row in rows], "count": len(rows)}


def create_strategy(db: Session, *, current_user: Any, request: Any) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    require_entitlement(db, current_user, "strategy_lifecycle")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    current_count = db.scalar(select(func.count(StrategyDesk.id)).where(StrategyDesk.tenant_id == tenant.id)) or 0
    enforce_entitlement_limit(db, current_user, "strategy_lifecycle", requested_total=int(current_count) + 1, resource_label="strategies")
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    desk_key = str(payload.get("desk_key") or "").strip().lower().replace(" ", "-")
    if not desk_key:
        raise ValidationError("Strategy desk key is required.")
    existing = db.execute(select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id, StrategyDesk.desk_key == desk_key)).scalar_one_or_none()
    if existing is not None:
        raise ValidationError("A strategy with this desk key already exists.")
    config = dict(payload.get("config") or {})
    config["symbols"] = _normalize_symbol_list(payload.get("symbols") or config.get("symbols"))
    config["allocation_cap"] = float(payload.get("allocation_cap") or config.get("allocation_cap") or 0.0)
    risk_profile = dict(payload.get("risk_profile") or {})
    config["risk_profile"] = risk_profile
    strategy = StrategyDesk(
        tenant_id=tenant.id,
        desk_key=desk_key,
        name=str(payload.get("name") or desk_key),
        category=str(config.get("category") or "productized"),
        lifecycle_stage="draft",
        run_mode="manual",
        trading_mode=str(payload.get("mode") or "paper"),
        enabled=True,
        paper_trading_enabled=str(payload.get("mode") or "paper") == "paper",
        config_json=config,
        runtime_json={"allocation_cap": config["allocation_cap"], "last_status": "draft"},
        metadata_json={"description": payload.get("description"), "status": "draft"},
    )
    db.add(strategy)
    db.flush()
    version = StrategyVersion(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id,
        version_number=1,
        name=str(payload.get("name") or strategy.name),
        description=payload.get("description"),
        status="draft",
        source_type="internal",
        config_json=config,
        risk_profile_json=risk_profile,
        created_by_user_id=user.id if user else None,
    )
    db.add(version)
    record_audit_event(db, event_type="strategy.created", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "desk_key": desk_key})
    db.commit()
    return {"strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def get_strategy(db: Session, *, current_user: Any, strategy_id: str) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    return {"strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def update_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    payload = request.model_dump(exclude_unset=True) if hasattr(request, "model_dump") else dict(request or {})
    if payload.get("name") is not None:
        strategy.name = str(payload["name"])
    if payload.get("lifecycle_stage") is not None or payload.get("status") is not None:
        strategy.lifecycle_stage = str(payload.get("lifecycle_stage") or payload.get("status") or strategy.lifecycle_stage).strip().lower()
    if payload.get("mode") is not None:
        strategy.trading_mode = str(payload["mode"]).strip().lower()
        strategy.paper_trading_enabled = strategy.trading_mode == "paper"
    config = dict(strategy.config_json or {})
    if isinstance(payload.get("config"), dict):
        config.update(payload["config"])
    if payload.get("symbols") is not None:
        config["symbols"] = _normalize_symbol_list(payload["symbols"])
    if payload.get("allocation_cap") is not None:
        config["allocation_cap"] = float(payload["allocation_cap"])
    if isinstance(payload.get("risk_profile"), dict):
        config["risk_profile"] = dict(payload["risk_profile"])
    metadata = dict(strategy.metadata_json or {})
    if payload.get("description") is not None:
        metadata["description"] = payload["description"]
    if isinstance(payload.get("metadata"), dict):
        metadata.update(payload["metadata"])
    strategy.config_json = config
    strategy.metadata_json = metadata
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "allocation_cap": config.get("allocation_cap", 0.0), "updated_at": _iso(_utc_now())}
    record_audit_event(db, event_type="strategy.updated", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "updates": payload})
    db.commit()
    return {"strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def create_strategy_version(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    require_entitlement(db, current_user, "strategy_versions")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    current_count = db.scalar(select(func.count(StrategyVersion.id)).where(StrategyVersion.tenant_id == tenant.id, StrategyVersion.strategy_desk_id == strategy.id)) or 0
    enforce_entitlement_limit(db, current_user, "strategy_versions", requested_total=int(current_count) + 1, resource_label="strategy versions")
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    latest = _latest_version(db, tenant_id=tenant.id, strategy_id=strategy.id)
    version_number = int(latest.version_number if latest else 0) + 1
    version = StrategyVersion(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id,
        version_number=version_number,
        name=str(payload.get("name") or f"{strategy.name} v{version_number}"),
        description=payload.get("description"),
        status="draft",
        source_type=str(payload.get("source_type") or "internal"),
        source_hash=payload.get("source_hash"),
        config_json=dict(payload.get("config") or strategy.config_json or {}),
        risk_profile_json=dict(payload.get("risk_profile") or strategy.config_json.get("risk_profile") or {}),
        created_by_user_id=user.id if user else None,
    )
    db.add(version)
    record_audit_event(db, event_type="strategy.version_created", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "version_number": version_number})
    db.commit()
    return {"version": _serialize_version(version)}


def list_strategy_versions(db: Session, *, current_user: Any, strategy_id: str) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    rows = db.execute(
        select(StrategyVersion)
        .where(StrategyVersion.tenant_id == tenant.id, StrategyVersion.strategy_desk_id == strategy.id)
        .order_by(StrategyVersion.version_number.desc())
    ).scalars().all()
    return {"items": [_serialize_version(row) for row in rows], "count": len(rows)}


def start_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any, mode: str | None = None) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    deployment_mode = str(mode or payload.get("deployment_mode") or payload.get("mode") or "paper").lower()
    if deployment_mode == "live":
        require_entitlement(db, current_user, "live_canary", message="Live requests require live canary entitlement.")
        status = "requested"
    else:
        require_entitlement(db, current_user, "automation_basic")
        status = "running"
    version = _active_version(db, tenant_id=tenant.id, strategy_id=strategy.id)
    if version is None:
        version = StrategyVersion(
            tenant_id=tenant.id,
            strategy_desk_id=strategy.id,
            version_number=1,
            name=strategy.name,
            status="active",
            config_json=dict(strategy.config_json or {}),
            risk_profile_json=dict(strategy.config_json.get("risk_profile") or {}),
            created_by_user_id=user.id if user else None,
            activated_at=_utc_now(),
        )
        db.add(version)
        db.flush()
    deployment = StrategyDeployment(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id,
        strategy_version_id=version.id,
        linked_account_id=payload.get("linked_account_id"),
        mode=deployment_mode,
        status=status,
        allocation_cap=float(strategy.config_json.get("allocation_cap") or strategy.runtime_json.get("allocation_cap") or 0.0),
        max_order_notional=float(strategy.config_json.get("risk_profile", {}).get("max_order_notional") or 0.0),
        max_daily_loss=float(strategy.config_json.get("risk_profile", {}).get("max_daily_loss") or 0.0),
        started_at=_utc_now() if status == "running" else None,
        metadata_json={"dry_run": bool(payload.get("dry_run")), "reason": payload.get("reason")},
    )
    db.add(deployment)
    strategy.lifecycle_stage = "paper" if deployment_mode == "paper" and status == "running" else strategy.lifecycle_stage
    strategy.paper_trading_enabled = deployment_mode == "paper" and status == "running"
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "last_deployment_id": deployment.id, "last_deployment_status": status}
    record_audit_event(db, event_type=f"strategy.{deployment_mode}_start_requested", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "deployment_id": deployment.id, "status": status})
    db.commit()
    return {"deployment": _serialize_deployment(deployment), "strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def stop_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    deployment = _latest_deployment(db, tenant_id=tenant.id, strategy_id=strategy.id)
    if deployment is None:
        raise NotFoundError("No deployment exists for this strategy.")
    deployment.status = "stopped"
    deployment.stopped_at = _utc_now()
    deployment.updated_at = _utc_now()
    strategy.paper_trading_enabled = False
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "last_deployment_status": "stopped"}
    record_audit_event(db, event_type="strategy.stopped", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "deployment_id": deployment.id})
    db.commit()
    return {"deployment": _serialize_deployment(deployment), "strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def promote_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    require_entitlement(db, current_user, "promotion_gates")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    from_stage = str(payload.get("from_stage") or strategy.lifecycle_stage or "draft")
    to_stage = str(payload.get("to_stage") or "validated")
    gate = create_strategy_promotion_gate(db, current_user=current_user, strategy_id=strategy.id, from_stage=from_stage, to_stage=to_stage, approved=True)
    if gate["status"] == "approved":
        strategy.lifecycle_stage = to_stage
        strategy.runtime_json = {**dict(strategy.runtime_json or {}), "last_promotion_gate_id": gate["id"], "last_promoted_at": _iso(_utc_now())}
        db.commit()
    return {"gate": gate, "strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def rollback_strategy(db: Session, *, current_user: Any, strategy_id: str, request: Any) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    target_version = None
    if payload.get("version_id"):
        target_version = db.execute(
            select(StrategyVersion).where(StrategyVersion.tenant_id == tenant.id, StrategyVersion.strategy_desk_id == strategy.id, StrategyVersion.id == payload["version_id"])
        ).scalar_one_or_none()
    if target_version is None:
        target_version = db.execute(
            select(StrategyVersion)
            .where(StrategyVersion.tenant_id == tenant.id, StrategyVersion.strategy_desk_id == strategy.id)
            .order_by(StrategyVersion.version_number.desc())
            .offset(1)
        ).scalars().first()
    if target_version is None:
        raise NotFoundError("No previous strategy version is available for rollback.")
    for version in db.execute(select(StrategyVersion).where(StrategyVersion.tenant_id == tenant.id, StrategyVersion.strategy_desk_id == strategy.id)).scalars().all():
        version.status = "retired" if version.id != target_version.id else "active"
        if version.id == target_version.id:
            version.activated_at = _utc_now()
    strategy.config_json = dict(target_version.config_json or {})
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "last_rollback_version_id": target_version.id, "last_rollback_reason": payload.get("reason")}
    record_audit_event(db, event_type="strategy.rollback", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "version_id": target_version.id, "reason": payload.get("reason")})
    db.commit()
    return {"version": _serialize_version(target_version), "strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def list_strategy_runs(db: Session, *, current_user: Any, strategy_id: str) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    rows = db.execute(
        select(StrategyRun).where(StrategyRun.tenant_id == tenant.id, StrategyRun.strategy_desk_id == strategy.id).order_by(StrategyRun.created_at.desc())
    ).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "desk_key": row.desk_key,
                "run_type": row.run_type,
                "status": row.status,
                "target_count": row.target_count,
                "metrics": dict(row.metrics_json or {}),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        "count": len(rows),
    }


def get_strategy_metrics(db: Session, *, current_user: Any, strategy_id: str) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    run_count = db.scalar(select(func.count(StrategyRun.id)).where(StrategyRun.tenant_id == tenant.id, StrategyRun.strategy_desk_id == strategy.id)) or 0
    decision_count = db.scalar(select(func.count(TradeDecision.id)).where(TradeDecision.tenant_id == tenant.id, TradeDecision.strategy_desk_id == strategy.id)) or 0
    latest = latest_readiness_snapshot(db, tenant_id=tenant.id, strategy_id=strategy.id)
    return {"strategy_id": strategy.id, "run_count": int(run_count), "decision_count": int(decision_count), "readiness": latest}


def automation_status(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(select(StrategyDeployment).where(StrategyDeployment.tenant_id == tenant.id).order_by(StrategyDeployment.created_at.desc())).scalars().all()
    return {"items": [_serialize_deployment(row) for row in rows], "count": len(rows)}


def strategy_automation_status(db: Session, *, current_user: Any, strategy_id: str) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    return {"strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy), "automation": _serialize_deployment(_latest_deployment(db, tenant_id=tenant.id, strategy_id=strategy.id))}


def kill_strategy(db: Session, *, current_user: Any, strategy_id: str, reason: str | None = None) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    strategy.runtime_json = {**dict(strategy.runtime_json or {}), "kill_switch_active": True, "kill_switch_reason": reason, "kill_switch_at": _iso(_utc_now())}
    record_audit_event(db, event_type="strategy.kill_switch", tenant=tenant, user=user, payload={"strategy_id": strategy.id, "reason": reason})
    db.commit()
    return {"strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy)}


def kill_all_strategies(db: Session, *, current_user: Any, reason: str | None = None) -> dict[str, Any]:
    _assert_strategy_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id)).scalars().all()
    for row in rows:
        row.runtime_json = {**dict(row.runtime_json or {}), "kill_switch_active": True, "kill_switch_reason": reason, "kill_switch_at": _iso(_utc_now())}
    db.commit()
    return {"killed_count": len(rows)}


def list_automation_events(db: Session, *, current_user: Any, limit: int = 100) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(
        select(AuditEvent)
        .where(AuditEvent.tenant_id == tenant.id, AuditEvent.event_type.like("strategy.%"))
        .order_by(AuditEvent.created_at.desc())
        .limit(max(1, min(limit, 500)))
    ).scalars().all()
    return {"items": [_serialize_audit_event(row) for row in rows], "count": len(rows)}


def _serialize_risk_policy(policy: RiskPolicy) -> dict[str, Any]:
    return {
        "id": policy.id,
        "strategy_id": policy.strategy_desk_id,
        "scope": policy.scope,
        "status": policy.status,
        "max_daily_loss": policy.max_daily_loss,
        "max_weekly_loss": policy.max_weekly_loss,
        "max_drawdown_pct": policy.max_drawdown_pct,
        "max_position_notional": policy.max_position_notional,
        "max_order_notional": policy.max_order_notional,
        "max_open_positions": policy.max_open_positions,
        "allowed_symbols": list((policy.allowed_symbols_json or {}).get("items") or []),
        "blocked_symbols": list((policy.blocked_symbols_json or {}).get("items") or []),
        "allowed_instruments": list((policy.allowed_instruments_json or {}).get("items") or []),
        "requires_approval_above": policy.requires_approval_above,
        "config": dict(policy.config_json or {}),
        "created_at": _iso(policy.created_at),
        "updated_at": _iso(policy.updated_at),
    }


def list_risk_policies(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(select(RiskPolicy).where(RiskPolicy.tenant_id == tenant.id).order_by(RiskPolicy.created_at.desc())).scalars().all()
    return {"items": [_serialize_risk_policy(row) for row in rows], "count": len(rows)}


def create_risk_policy(db: Session, *, current_user: Any, request: Any) -> dict[str, Any]:
    _assert_risk_manager(current_user)
    require_entitlement(db, current_user, "risk_engine")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=payload["strategy_id"]) if payload.get("strategy_id") else None
    policy = RiskPolicy(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id if strategy else None,
        scope=str(payload.get("scope") or "tenant"),
        status=str(payload.get("status") or "active"),
        max_daily_loss=float(payload.get("max_daily_loss") or 0.0),
        max_weekly_loss=float(payload.get("max_weekly_loss") or 0.0),
        max_drawdown_pct=float(payload.get("max_drawdown_pct") or 0.0),
        max_position_notional=float(payload.get("max_position_notional") or 0.0),
        max_order_notional=float(payload.get("max_order_notional") or 0.0),
        max_open_positions=int(payload.get("max_open_positions") or 0),
        allowed_symbols_json={"items": _normalize_symbol_list(payload.get("allowed_symbols"))},
        blocked_symbols_json={"items": _normalize_symbol_list(payload.get("blocked_symbols"))},
        allowed_instruments_json={"items": list(payload.get("allowed_instruments") or [])},
        requires_approval_above=payload.get("requires_approval_above"),
        config_json=dict(payload.get("config") or {}),
    )
    db.add(policy)
    db.commit()
    return {"policy": _serialize_risk_policy(policy)}


def get_risk_policy(db: Session, *, current_user: Any, policy_id: str) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    policy = db.execute(select(RiskPolicy).where(RiskPolicy.tenant_id == tenant.id, RiskPolicy.id == policy_id)).scalar_one_or_none()
    if policy is None:
        raise NotFoundError("The requested risk policy could not be found.")
    return {"policy": _serialize_risk_policy(policy)}


def update_risk_policy(db: Session, *, current_user: Any, policy_id: str, request: Any) -> dict[str, Any]:
    _assert_risk_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    policy = db.execute(select(RiskPolicy).where(RiskPolicy.tenant_id == tenant.id, RiskPolicy.id == policy_id)).scalar_one_or_none()
    if policy is None:
        raise NotFoundError("The requested risk policy could not be found.")
    payload = request.model_dump(exclude_unset=True) if hasattr(request, "model_dump") else dict(request or {})
    for key in ("status", "max_daily_loss", "max_weekly_loss", "max_drawdown_pct", "max_position_notional", "max_order_notional", "max_open_positions", "requires_approval_above"):
        if key in payload:
            setattr(policy, key, payload[key])
    if "allowed_symbols" in payload:
        policy.allowed_symbols_json = {"items": _normalize_symbol_list(payload["allowed_symbols"])}
    if "blocked_symbols" in payload:
        policy.blocked_symbols_json = {"items": _normalize_symbol_list(payload["blocked_symbols"])}
    if "allowed_instruments" in payload:
        policy.allowed_instruments_json = {"items": list(payload["allowed_instruments"] or [])}
    if "config" in payload and isinstance(payload["config"], dict):
        policy.config_json = dict(payload["config"])
    policy.updated_at = _utc_now()
    db.commit()
    return {"policy": _serialize_risk_policy(policy)}


def run_risk_check(db: Session, *, current_user: Any, request: Any) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=payload["strategy_id"]) if payload.get("strategy_id") else None
    policy = db.execute(
        select(RiskPolicy)
        .where(RiskPolicy.tenant_id == tenant.id, RiskPolicy.strategy_desk_id == (strategy.id if strategy else None), RiskPolicy.status == "active")
        .order_by(RiskPolicy.created_at.desc())
    ).scalars().first()
    checks: list[dict[str, Any]] = []
    allowed = True
    expected_notional = float(payload.get("expected_notional") or 0.0)
    symbol = str(payload.get("symbol") or "").upper()
    if policy is None:
        checks.append({"rule": "risk_policy", "status": "warn", "message": "No active policy matched; advisory default used."})
    else:
        blocked = set((policy.blocked_symbols_json or {}).get("items") or [])
        if symbol in blocked:
            allowed = False
            checks.append({"rule": "blocked_symbols", "status": "fail"})
        else:
            checks.append({"rule": "blocked_symbols", "status": "pass"})
        if policy.max_order_notional and expected_notional > policy.max_order_notional:
            allowed = False
            checks.append({"rule": "max_order_notional", "status": "fail"})
        else:
            checks.append({"rule": "max_order_notional", "status": "pass"})
        if policy.requires_approval_above is not None and expected_notional > policy.requires_approval_above:
            checks.append({"rule": "requires_approval_above", "status": "warn"})
    if not allowed:
        db.add(
            RiskEvent(
                tenant_id=tenant.id,
                strategy_desk_id=strategy.id if strategy else None,
                event_type="risk.check_failed",
                severity="high",
                breached_rule=";".join(item["rule"] for item in checks if item["status"] == "fail"),
                action_taken="blocked",
                payload_json=payload,
            )
        )
        db.commit()
    return {"allowed": allowed, "policy_id": policy.id if policy else None, "checks": checks}


def list_risk_events(db: Session, *, current_user: Any, limit: int = 100) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(select(RiskEvent).where(RiskEvent.tenant_id == tenant.id).order_by(RiskEvent.created_at.desc()).limit(max(1, min(limit, 500)))).scalars().all()
    return {
        "items": [
            {
                "id": row.id,
                "strategy_id": row.strategy_desk_id,
                "event_type": row.event_type,
                "severity": row.severity,
                "breached_rule": row.breached_rule,
                "action_taken": row.action_taken,
                "payload": dict(row.payload_json or {}),
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ],
        "count": len(rows),
    }


def set_risk_kill_switch(db: Session, *, current_user: Any, request: Any, active: bool) -> dict[str, Any]:
    _assert_risk_manager(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    if payload.get("strategy_id"):
        rows = [load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=payload["strategy_id"])]
    else:
        rows = db.execute(select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id)).scalars().all()
    for row in rows:
        row.runtime_json = {**dict(row.runtime_json or {}), "kill_switch_active": bool(active), "kill_switch_reason": payload.get("reason"), "kill_switch_at": _iso(_utc_now())}
    db.commit()
    return {"active": bool(active), "affected_count": len(rows)}


def _serialize_audit_event(row: AuditEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "actor_email": row.actor_email,
        "payload": dict(row.payload_json or {}),
        "created_at": _iso(row.created_at),
    }


def list_audit_events(db: Session, *, current_user: Any, limit: int = 100) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    rows = db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant.id).order_by(AuditEvent.created_at.desc()).limit(max(1, min(limit, 500)))).scalars().all()
    return {"items": [_serialize_audit_event(row) for row in rows], "count": len(rows)}


def _trade_decision_for(db: Session, *, tenant_id: str, trade_id: str) -> TradeDecision | None:
    return db.execute(
        select(TradeDecision)
        .where(TradeDecision.tenant_id == tenant_id, (TradeDecision.id == trade_id) | (TradeDecision.order_event_id == trade_id) | (TradeDecision.decision_hash == trade_id))
        .order_by(TradeDecision.created_at.desc())
    ).scalars().first()


def get_trade_audit(db: Session, *, current_user: Any, trade_id: str) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    decision = _trade_decision_for(db, tenant_id=tenant.id, trade_id=trade_id)
    return {"trade_id": trade_id, "decision": _serialize_trade_decision(decision), "replay": _replay_rows(db, tenant_id=tenant.id, decision=decision)}


def _serialize_trade_decision(decision: TradeDecision | None) -> dict[str, Any] | None:
    if decision is None:
        return None
    return {
        "id": decision.id,
        "symbol": decision.symbol,
        "instrument_type": decision.instrument_type,
        "side": decision.side,
        "quantity": decision.quantity,
        "confidence_score": decision.confidence_score,
        "decision_status": decision.decision_status,
        "decision_reason": decision.decision_reason,
        "signal_snapshot": dict(decision.signal_snapshot_json or {}),
        "risk_snapshot": dict(decision.risk_snapshot_json or {}),
        "readiness_snapshot": dict(decision.readiness_snapshot_json or {}),
        "market_snapshot": dict(decision.market_snapshot_json or {}),
        "broker_snapshot": dict(decision.broker_snapshot_json or {}),
        "created_at": _iso(decision.created_at),
    }


def _replay_rows(db: Session, *, tenant_id: str, decision: TradeDecision | None) -> list[dict[str, Any]]:
    if decision is None:
        return []
    rows = db.execute(
        select(DecisionReplayEvent)
        .where(DecisionReplayEvent.tenant_id == tenant_id, DecisionReplayEvent.trade_decision_id == decision.id)
        .order_by(DecisionReplayEvent.sequence_number.asc())
    ).scalars().all()
    return [
        {
            "id": row.id,
            "sequence_number": row.sequence_number,
            "event_type": row.event_type,
            "event_time": _iso(row.event_time),
            "payload": dict(row.payload_json or {}),
        }
        for row in rows
    ]


def get_strategy_audit(db: Session, *, current_user: Any, strategy_id: str) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    decisions = db.execute(select(TradeDecision).where(TradeDecision.tenant_id == tenant.id, TradeDecision.strategy_desk_id == strategy.id).order_by(TradeDecision.created_at.desc()).limit(100)).scalars().all()
    return {"strategy": _serialize_strategy(db, tenant_id=tenant.id, strategy=strategy), "decisions": [_serialize_trade_decision(row) for row in decisions]}


def create_audit_export(db: Session, *, current_user: Any, request: Any) -> dict[str, Any]:
    require_entitlement(db, current_user, "audit_exports")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    payload = request.model_dump() if hasattr(request, "model_dump") else dict(request or {})
    export = AuditExport(
        tenant_id=tenant.id,
        requested_by_user_id=user.id if user else None,
        status="queued",
        export_type=str(payload.get("export_type") or "audit_bundle"),
        file_path=None,
        checksum=None,
    )
    db.add(export)
    db.flush()
    record_audit_event(db, event_type="audit.export_queued", tenant=tenant, user=user, payload={"export_id": export.id, **payload})
    db.commit()
    return {"export": _serialize_audit_export(export)}


def _serialize_audit_export(row: AuditExport) -> dict[str, Any]:
    return {
        "id": row.id,
        "status": row.status,
        "export_type": row.export_type,
        "file_path": row.file_path,
        "checksum": row.checksum,
        "error_message": row.error_message,
        "created_at": _iso(row.created_at),
        "completed_at": _iso(row.completed_at),
    }


def get_audit_export(db: Session, *, current_user: Any, export_id: str) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    row = db.execute(select(AuditExport).where(AuditExport.tenant_id == tenant.id, AuditExport.id == export_id)).scalar_one_or_none()
    if row is None:
        raise NotFoundError("The requested audit export could not be found.")
    return {"export": _serialize_audit_export(row)}


def _execution_row(row: ExecutionQualitySnapshot) -> dict[str, Any]:
    return {
        "id": row.id,
        "order_event_id": row.order_event_id,
        "trade_id": row.trade_id,
        "symbol": row.symbol,
        "broker": row.broker,
        "route_state": row.route_state,
        "expected_price": row.expected_price,
        "submitted_price": row.submitted_price,
        "filled_price": row.filled_price,
        "spread_bps": row.spread_bps,
        "slippage_bps": row.slippage_bps,
        "estimated_cost_bps": row.estimated_cost_bps,
        "latency_ms": row.latency_ms,
        "liquidity_score": row.liquidity_score,
        "execution_score": row.execution_score,
        "payload": dict(row.payload_json or {}),
        "created_at": _iso(row.created_at),
    }


def execution_quality_summary(db: Session, *, current_user: Any, strategy_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    require_entitlement(db, current_user, "execution_quality")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    statement = select(ExecutionQualitySnapshot).where(ExecutionQualitySnapshot.tenant_id == tenant.id)
    if strategy_id:
        strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
        statement = statement.where(ExecutionQualitySnapshot.strategy_desk_id == strategy.id)
    rows = db.execute(statement.order_by(ExecutionQualitySnapshot.created_at.desc()).limit(max(1, min(limit, 500)))).scalars().all()
    count = len(rows)
    avg = lambda values: round(sum(values) / len(values), 4) if values else 0.0
    filled_count = len([row for row in rows if row.filled_price is not None])
    rejected_count = len([row for row in rows if row.route_state == "rejected"])
    summary = {
        "execution_score": avg([float(row.execution_score) for row in rows if row.execution_score is not None]),
        "avg_slippage_bps": avg([float(row.slippage_bps) for row in rows if row.slippage_bps is not None]),
        "avg_spread_bps": avg([float(row.spread_bps) for row in rows if row.spread_bps is not None]),
        "fill_rate": round(filled_count / count, 4) if count else 0.0,
        "reject_rate": round(rejected_count / count, 4) if count else 0.0,
        "avg_latency_ms": avg([float(row.latency_ms) for row in rows if row.latency_ms is not None]),
    }
    return {"summary": summary, "rows": [_execution_row(row) for row in rows], "count": count}
