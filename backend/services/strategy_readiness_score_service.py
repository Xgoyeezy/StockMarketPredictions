from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.models.saas import (
    AuditEvent,
    BrokerageLinkedAccount,
    DecisionReplayEvent,
    ExecutionQualitySnapshot,
    ReadinessBlocker,
    ReadinessSnapshot,
    RiskEvent,
    RiskPolicy,
    StrategyDesk,
    StrategyPromotionGate,
    StrategyRun,
)
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import NotFoundError
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user

READINESS_WEIGHTS: dict[str, float] = {
    "broker_connectivity": 0.15,
    "data_freshness": 0.15,
    "capital_exposure": 0.15,
    "risk_controls": 0.20,
    "paper_evidence": 0.15,
    "execution_quality": 0.10,
    "audit_integrity": 0.10,
}

HARD_BLOCKER_CAPS: dict[str, int] = {
    "no_linked_broker": 45,
    "broker_disconnected": 50,
    "stale_data": 55,
    "no_paper_evidence": 60,
    "kill_switch_active": 35,
    "daily_loss_breached": 40,
    "max_drawdown_breached": 45,
    "audit_logging_unavailable": 50,
    "live_trading_disabled": 65,
    "options_liquidity_failure": 60,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clamp_score(value: Any) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.0
    return int(round(max(0.0, min(100.0, numeric))))


def blocker(key: str, severity: str, source: str, message: str) -> dict[str, str]:
    return {"key": key, "severity": severity, "source": source, "message": message}


def calculate_weighted_score(components: dict[str, Any]) -> int:
    return clamp_score(sum(clamp_score(components.get(key)) * weight for key, weight in READINESS_WEIGHTS.items()))


def apply_hard_blocker_caps(score: int, blockers: list[dict[str, Any]]) -> int:
    capped = clamp_score(score)
    for item in blockers:
        cap = HARD_BLOCKER_CAPS.get(str(item.get("key") or ""))
        if cap is not None:
            capped = min(capped, cap)
    return capped


def derive_readiness_status(score: int, blockers: list[dict[str, Any]], *, trading_mode: str = "research") -> str:
    if any(str(item.get("severity") or "").lower() == "critical" for item in blockers):
        return "blocked"
    if score >= 92:
        return "scale_ready"
    if score >= 85:
        return "live_ready" if str(trading_mode or "").lower() == "live_enabled" else "live_candidate"
    if score >= 75:
        return "validated"
    if score >= 60:
        return "paper_ready"
    if score > 0:
        return "paper_only"
    return "blocked"


def evaluate_promotion_rules(
    *,
    from_stage: str,
    to_stage: str,
    score: int,
    blockers: list[dict[str, Any]],
    paper_state: dict[str, Any],
    broker_state: dict[str, Any],
    risk_state: dict[str, Any],
    audit_state: dict[str, Any],
    execution_quality_score: int,
    version_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_from = str(from_stage or "draft").strip().lower()
    normalized_to = str(to_stage or "").strip().lower()
    version_state = dict(version_state or {})
    critical = [item for item in blockers if str(item.get("severity") or "").lower() == "critical"]
    closed_trade_count = int(paper_state.get("closed_trade_count") or 0)
    session_count = int(paper_state.get("session_count") or 0)
    coverage = float(paper_state.get("evidence_coverage_pct") or 0.0)
    warnings: list[dict[str, str]] = []
    requirements: list[dict[str, Any]] = []

    def add_req(key: str, passed: bool, message: str) -> None:
        requirements.append({"key": key, "passed": bool(passed), "message": message})
        if not passed:
            warnings.append({"key": key, "message": message})

    if normalized_from == "draft" and normalized_to in {"paper", "paper_ready"}:
        add_req("score_at_least_60", score >= 60, "Readiness score must be at least 60.")
        add_req("risk_policy_required", bool(risk_state.get("policy_active")), "An active risk policy is required.")
        add_req("audit_required", bool(audit_state.get("decision_logging_ok")), "Audit logging must be available.")
    elif normalized_from in {"paper", "paper_ready"} and normalized_to == "validated":
        add_req("score_at_least_75", score >= 75, "Readiness score must be at least 75.")
        add_req("paper_trade_count", closed_trade_count >= 20, "At least 20 closed paper trades are required.")
        add_req("paper_sessions", session_count >= 3, "At least 3 paper sessions are required.")
        add_req("no_critical_blocker", not critical, "Critical blockers must be resolved.")
        add_req("evidence_coverage", coverage > 80.0, "Paper evidence coverage must exceed 80 percent.")
    elif normalized_from == "validated" and normalized_to == "live_candidate":
        add_req("score_at_least_85", score >= 85, "Readiness score must be at least 85.")
        add_req("broker_connected", broker_state.get("connection_status") == "active", "Broker must be connected.")
        add_req("balance_verified", bool(broker_state.get("balance_verified")), "Broker balance must be verified.")
        add_req("risk_policy_active", bool(risk_state.get("policy_active")), "Risk policy must be active.")
        add_req("audit_replay_active", bool(audit_state.get("audit_replay_active")), "Audit replay must be active.")
        add_req("execution_quality", execution_quality_score > 70, "Execution quality score must be above 70.")
    elif normalized_from == "live_candidate" and normalized_to in {"scaled_live", "scale_ready"}:
        add_req("score_at_least_92", score >= 92, "Readiness score must be at least 92.")
        add_req("live_candidate_sessions", int(paper_state.get("live_candidate_sessions") or 0) >= 10, "At least 10 live-candidate sessions are required.")
        add_req("no_critical_risk_breach", not bool(risk_state.get("critical_risk_breach")), "No critical risk breach may be unresolved.")
        add_req("slippage_inside_policy", bool(risk_state.get("slippage_inside_policy", True)), "Slippage must remain inside policy.")
        add_req("version_locked", bool(version_state.get("version_locked")), "Strategy version must be locked.")
        add_req("manual_approval_recorded", bool(version_state.get("manual_approval_recorded")), "Manual approval must be recorded.")
    else:
        add_req("supported_transition", False, f"Unsupported promotion transition {normalized_from} to {normalized_to}.")

    can_promote = bool(requirements) and all(item["passed"] for item in requirements)
    return {
        "from_stage": normalized_from,
        "to_stage": normalized_to,
        "can_promote": can_promote,
        "status": "approved" if can_promote else "blocked",
        "requirements": requirements,
        "warnings": warnings,
        "next_actions": [item["message"] for item in warnings],
        "recommendation": "Promotion requirements passed." if can_promote else "Resolve promotion blockers before advancing.",
    }


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _latest_strategy_run(db: Session, *, tenant_id: str, strategy_desk_id: str) -> StrategyRun | None:
    return db.execute(
        select(StrategyRun)
        .where(StrategyRun.tenant_id == tenant_id, StrategyRun.strategy_desk_id == strategy_desk_id)
        .order_by(StrategyRun.created_at.desc())
    ).scalars().first()


def load_strategy_or_raise(db: Session, *, tenant_id: str, strategy_id: str) -> StrategyDesk:
    strategy = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant_id, StrategyDesk.id == strategy_id)
    ).scalar_one_or_none()
    if strategy is None:
        strategy = db.execute(
            select(StrategyDesk).where(StrategyDesk.tenant_id == tenant_id, StrategyDesk.desk_key == strategy_id)
        ).scalar_one_or_none()
    if strategy is None:
        raise NotFoundError("The requested strategy could not be found.")
    return strategy


def _broker_state(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    linked = db.execute(
        select(BrokerageLinkedAccount).where(BrokerageLinkedAccount.tenant_id == tenant_id).order_by(BrokerageLinkedAccount.updated_at.desc())
    ).scalars().all()
    active = next((row for row in linked if row.connection_status == "active"), linked[0] if linked else None)
    runtime = _as_dict(strategy.runtime_json)
    return {
        "linked_account_present": bool(linked),
        "connection_status": active.connection_status if active else "missing",
        "balance_verified": bool(runtime.get("balance_verified", bool(active))),
        "linked_account_id": active.id if active else None,
    }


def _risk_state(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    policy = db.execute(
        select(RiskPolicy)
        .where(RiskPolicy.tenant_id == tenant_id, RiskPolicy.strategy_desk_id == strategy.id)
        .order_by(RiskPolicy.created_at.desc())
    ).scalars().first()
    runtime = _as_dict(strategy.runtime_json)
    return {
        "policy_active": bool(policy and policy.status == "active"),
        "daily_loss_breached": bool(runtime.get("daily_loss_breached")),
        "max_drawdown_breached": bool(runtime.get("max_drawdown_breached")),
        "critical_risk_breach": bool(runtime.get("critical_risk_breach")),
        "slippage_inside_policy": bool(runtime.get("slippage_inside_policy", True)),
        "policy_id": policy.id if policy else None,
    }


def _paper_state(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    runtime = _as_dict(strategy.runtime_json)
    evidence = _as_dict(runtime.get("paper_evidence"))
    run_count = db.scalar(select(func.count(StrategyRun.id)).where(StrategyRun.tenant_id == tenant_id, StrategyRun.strategy_desk_id == strategy.id)) or 0
    return {
        "closed_trade_count": int(evidence.get("closed_trade_count", evidence.get("closed_trades", run_count) or 0)),
        "session_count": int(evidence.get("session_count", evidence.get("sessions", min(int(run_count), 3)) or 0)),
        "evidence_coverage_pct": float(evidence.get("evidence_coverage_pct", evidence.get("coverage_pct", 0.0)) or 0.0),
        "live_candidate_sessions": int(evidence.get("live_candidate_sessions") or 0),
    }


def _audit_state(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    runtime = _as_dict(strategy.runtime_json)
    event_count = db.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.tenant_id == tenant_id)) or 0
    replay_count = db.scalar(select(func.count(DecisionReplayEvent.id)).where(DecisionReplayEvent.tenant_id == tenant_id)) or 0
    return {
        "decision_logging_ok": bool(runtime.get("audit_logging_available", True)),
        "audit_replay_active": bool(runtime.get("audit_replay_active", replay_count > 0)),
        "event_count": int(event_count),
        "replay_count": int(replay_count),
    }


def _execution_quality_state(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    latest = db.execute(
        select(ExecutionQualitySnapshot)
        .where(ExecutionQualitySnapshot.tenant_id == tenant_id, ExecutionQualitySnapshot.strategy_desk_id == strategy.id)
        .order_by(ExecutionQualitySnapshot.created_at.desc())
    ).scalars().first()
    runtime = _as_dict(strategy.runtime_json)
    quality = runtime.get("execution_quality_score", latest.execution_score if latest else 0)
    slippage = runtime.get("avg_slippage_bps", latest.slippage_bps if latest else None)
    return {"score": clamp_score(quality), "avg_slippage_bps": slippage, "snapshot_id": latest.id if latest else None}


def build_strategy_readiness_inputs(db: Session, *, tenant_id: str, strategy: StrategyDesk) -> dict[str, Any]:
    broker_state = _broker_state(db, tenant_id=tenant_id, strategy=strategy)
    risk_state = _risk_state(db, tenant_id=tenant_id, strategy=strategy)
    paper_state = _paper_state(db, tenant_id=tenant_id, strategy=strategy)
    audit_state = _audit_state(db, tenant_id=tenant_id, strategy=strategy)
    execution_state = _execution_quality_state(db, tenant_id=tenant_id, strategy=strategy)
    runtime = _as_dict(strategy.runtime_json)
    data_fresh = not bool(runtime.get("stale_data"))
    options_ok = bool(runtime.get("options_quotes_and_liquidity_ok", True))
    components = {
        "broker_connectivity": 100 if broker_state["connection_status"] == "active" else (45 if broker_state["linked_account_present"] else 0),
        "data_freshness": 100 if data_fresh and options_ok else 35,
        "capital_exposure": clamp_score(runtime.get("capital_exposure_score", 80)),
        "risk_controls": 100 if risk_state["policy_active"] and not risk_state["daily_loss_breached"] and not risk_state["max_drawdown_breached"] else 45,
        "paper_evidence": clamp_score(min(100, paper_state["closed_trade_count"] * 3 + paper_state["session_count"] * 10 + paper_state["evidence_coverage_pct"] * 0.4)),
        "execution_quality": execution_state["score"],
        "audit_integrity": 100 if audit_state["decision_logging_ok"] else 0,
    }
    blockers: list[dict[str, str]] = []
    if not broker_state["linked_account_present"]:
        blockers.append(blocker("no_linked_broker", "critical", "brokerage_account_service", "No linked broker account."))
    elif broker_state["connection_status"] != "active":
        blockers.append(blocker("broker_disconnected", "critical", "brokerage_account_service", "Broker disconnected."))
    if not data_fresh:
        blockers.append(blocker("stale_data", "critical", "automation_trade_readiness_service", "Market data is stale."))
    if paper_state["closed_trade_count"] <= 0:
        blockers.append(blocker("no_paper_evidence", "critical", "automation_paper_evidence_service", "No paper evidence."))
    if bool(runtime.get("kill_switch_active")):
        blockers.append(blocker("kill_switch_active", "critical", "automation_state_control_service", "Kill switch is active."))
    if risk_state["daily_loss_breached"]:
        blockers.append(blocker("daily_loss_breached", "critical", "risk_control_service", "Daily loss limit breached."))
    if risk_state["max_drawdown_breached"]:
        blockers.append(blocker("max_drawdown_breached", "critical", "risk_control_service", "Max drawdown breached."))
    if not audit_state["decision_logging_ok"]:
        blockers.append(blocker("audit_logging_unavailable", "critical", "audit_service", "Decision logging unavailable."))
    if str(strategy.trading_mode or "").lower() != "live_enabled":
        blockers.append(blocker("live_trading_disabled", "info", "strategy", "Live trading still gated."))
    if strategy.category == "options" and not options_ok:
        blockers.append(blocker("options_liquidity_failure", "critical", "options_validation_service", "Options quotes or liquidity failed."))
    return {
        "broker_state": broker_state,
        "risk_state": risk_state,
        "paper_state": paper_state,
        "audit_state": audit_state,
        "execution_state": execution_state,
        "components": components,
        "blockers": blockers,
    }


def evaluate_strategy_readiness(db: Session, *, current_user: Any, strategy_id: str, force_refresh: bool = False) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    inputs = build_strategy_readiness_inputs(db, tenant_id=tenant.id, strategy=strategy)
    weighted = calculate_weighted_score(inputs["components"])
    final_score = apply_hard_blocker_caps(weighted, inputs["blockers"])
    status = derive_readiness_status(final_score, inputs["blockers"], trading_mode=strategy.trading_mode)
    current_stage = str(strategy.lifecycle_stage or "draft").lower()
    target_stage = "paper" if current_stage == "draft" else ("validated" if current_stage in {"paper", "paper_ready"} else "live_candidate")
    promotion = evaluate_promotion_rules(
        from_stage=current_stage,
        to_stage=target_stage,
        score=final_score,
        blockers=inputs["blockers"],
        paper_state=inputs["paper_state"],
        broker_state=inputs["broker_state"],
        risk_state=inputs["risk_state"],
        audit_state=inputs["audit_state"],
        execution_quality_score=inputs["components"]["execution_quality"],
        version_state=_as_dict(strategy.runtime_json).get("version_state") or {},
    )
    latest_run = _latest_strategy_run(db, tenant_id=tenant.id, strategy_desk_id=strategy.id)
    snapshot = ReadinessSnapshot(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id,
        strategy_run_id=latest_run.id if latest_run else None,
        score=final_score,
        status=status,
        recommendation=promotion["recommendation"],
        components_json=inputs["components"],
        hard_blockers_json=[item for item in inputs["blockers"] if item["severity"] == "critical"],
        warnings_json=promotion["warnings"],
        evaluated_at=utc_now(),
    )
    db.add(snapshot)
    db.flush()
    for item in inputs["blockers"]:
        db.add(
            ReadinessBlocker(
                tenant_id=tenant.id,
                readiness_snapshot_id=snapshot.id,
                strategy_desk_id=strategy.id,
                blocker_key=item["key"],
                severity=item["severity"],
                source=item["source"],
                message=item["message"],
            )
        )
    record_audit_event(
        db,
        event_type="strategy.readiness_evaluated",
        tenant=tenant,
        user=user,
        payload={"strategy_id": strategy.id, "score": final_score, "status": status, "force_refresh": bool(force_refresh)},
    )
    db.commit()
    return {
        "strategy_id": strategy.id,
        "score": final_score,
        "status": status,
        "recommendation": promotion["recommendation"],
        "can_start_paper": current_stage == "draft" and bool(evaluate_promotion_rules(
            from_stage="draft",
            to_stage="paper",
            score=final_score,
            blockers=inputs["blockers"],
            paper_state=inputs["paper_state"],
            broker_state=inputs["broker_state"],
            risk_state=inputs["risk_state"],
            audit_state=inputs["audit_state"],
            execution_quality_score=inputs["components"]["execution_quality"],
        )["can_promote"]),
        "can_request_live": bool(evaluate_promotion_rules(
            from_stage="validated",
            to_stage="live_candidate",
            score=final_score,
            blockers=inputs["blockers"],
            paper_state=inputs["paper_state"],
            broker_state=inputs["broker_state"],
            risk_state=inputs["risk_state"],
            audit_state=inputs["audit_state"],
            execution_quality_score=inputs["components"]["execution_quality"],
        )["can_promote"]),
        "can_promote": promotion["can_promote"],
        "components": inputs["components"],
        "blockers": inputs["blockers"],
        "warnings": promotion["warnings"],
        "next_actions": promotion["next_actions"],
        "promotion": promotion,
        "evaluated_at": _iso(snapshot.evaluated_at),
        "snapshot_id": snapshot.id,
    }


def latest_readiness_snapshot(db: Session, *, tenant_id: str, strategy_id: str) -> dict[str, Any] | None:
    strategy = load_strategy_or_raise(db, tenant_id=tenant_id, strategy_id=strategy_id)
    snapshot = db.execute(
        select(ReadinessSnapshot)
        .where(ReadinessSnapshot.tenant_id == tenant_id, ReadinessSnapshot.strategy_desk_id == strategy.id)
        .order_by(ReadinessSnapshot.evaluated_at.desc())
    ).scalars().first()
    if snapshot is None:
        return None
    blockers = db.execute(
        select(ReadinessBlocker)
        .where(ReadinessBlocker.readiness_snapshot_id == snapshot.id)
        .order_by(ReadinessBlocker.created_at.asc())
    ).scalars().all()
    return {
        "strategy_id": strategy.id,
        "score": snapshot.score,
        "status": snapshot.status,
        "recommendation": snapshot.recommendation,
        "components": dict(snapshot.components_json or {}),
        "blockers": [
            {
                "id": item.id,
                "key": item.blocker_key,
                "severity": item.severity,
                "source": item.source,
                "message": item.message,
                "resolved": item.resolved,
                "created_at": _iso(item.created_at),
            }
            for item in blockers
        ],
        "warnings": list(snapshot.warnings_json or []),
        "evaluated_at": _iso(snapshot.evaluated_at),
        "snapshot_id": snapshot.id,
    }


def create_strategy_promotion_gate(
    db: Session,
    *,
    current_user: Any,
    strategy_id: str,
    from_stage: str,
    to_stage: str,
    approved: bool = False,
) -> dict[str, Any]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    strategy = load_strategy_or_raise(db, tenant_id=tenant.id, strategy_id=strategy_id)
    readiness = evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy.id, force_refresh=True)
    promotion = evaluate_promotion_rules(
        from_stage=from_stage,
        to_stage=to_stage,
        score=int(readiness["score"]),
        blockers=list(readiness.get("blockers") or []),
        paper_state=build_strategy_readiness_inputs(db, tenant_id=tenant.id, strategy=strategy)["paper_state"],
        broker_state=build_strategy_readiness_inputs(db, tenant_id=tenant.id, strategy=strategy)["broker_state"],
        risk_state=build_strategy_readiness_inputs(db, tenant_id=tenant.id, strategy=strategy)["risk_state"],
        audit_state=build_strategy_readiness_inputs(db, tenant_id=tenant.id, strategy=strategy)["audit_state"],
        execution_quality_score=int((readiness.get("components") or {}).get("execution_quality") or 0),
        version_state=_as_dict(strategy.runtime_json).get("version_state") or {},
    )
    gate = StrategyPromotionGate(
        tenant_id=tenant.id,
        strategy_desk_id=strategy.id,
        from_stage=str(from_stage or strategy.lifecycle_stage or "draft"),
        to_stage=str(to_stage or ""),
        status="approved" if approved and promotion["can_promote"] else promotion["status"],
        required_score=60 if to_stage in {"paper", "paper_ready"} else 75 if to_stage == "validated" else 85,
        actual_score=int(readiness["score"]),
        requirements_json={"items": promotion["requirements"]},
        blockers_json={"items": promotion["warnings"]},
        evaluated_at=utc_now(),
        approved_by_user_id=user.id if user and approved and promotion["can_promote"] else None,
    )
    db.add(gate)
    db.flush()
    db.commit()
    return {
        "id": gate.id,
        "from_stage": gate.from_stage,
        "to_stage": gate.to_stage,
        "status": gate.status,
        "required_score": gate.required_score,
        "actual_score": gate.actual_score,
        "requirements": promotion["requirements"],
        "blockers": promotion["warnings"],
        "evaluated_at": _iso(gate.evaluated_at),
    }
