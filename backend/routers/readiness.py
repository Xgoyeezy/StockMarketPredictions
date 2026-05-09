from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope, ReadinessEvaluateRequest
from backend.services.category_upgrade_readiness_service import (
    build_category_upgrade_proof_chain,
    build_category_upgrade_support_export,
    get_category_upgrade_readiness_summary,
    write_category_upgrade_readiness_export,
)
from backend.services.productized_control_plane_service import list_strategies
from backend.services.strategy_readiness_score_service import evaluate_strategy_readiness, latest_readiness_snapshot
from backend.services.tenant_service import _resolve_tenant_for_current_user

router = APIRouter(prefix="/readiness", tags=["readiness"])


def _report_safety_fields(report: dict) -> dict:
    return {
        "research_only": bool(report.get("research_only", True)),
        "read_only": bool(report.get("read_only", True)),
        "paper_route_only": bool(report.get("paper_route_only", True)),
        "can_submit_orders": bool(report.get("can_submit_orders", False)),
        "can_submit_live_orders": bool(report.get("can_submit_live_orders", False)),
        "can_change_broker_routes": bool(report.get("can_change_broker_routes", False)),
        "can_bypass_risk_gates": bool(report.get("can_bypass_risk_gates", False)),
        "can_clear_kill_switch": bool(report.get("can_clear_kill_switch", False)),
        "can_change_ranking_weights": bool(report.get("can_change_ranking_weights", False)),
        "mutation": report.get("mutation", "none"),
        "safety_notes": list(report.get("safety_notes") or []),
    }


@router.get("/category-upgrade", response_model=ApiEnvelope)
def get_category_upgrade_readiness(
    include_slow_sources: bool = Query(default=False, description="Run slower proof collectors instead of the fast readiness view."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_category_upgrade_readiness_summary(db=db, current_user=current_user, include_slow_sources=include_slow_sources))


@router.get("/category-upgrade/proof-gates", response_model=ApiEnvelope)
def get_category_upgrade_proof_gates(
    include_slow_sources: bool = Query(default=False, description="Run slower proof collectors instead of the fast readiness view."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    report = get_category_upgrade_readiness_summary(db=db, current_user=current_user, include_slow_sources=include_slow_sources)
    gates = list(report.get("gates") or [])
    return envelope(
        {
            "status": report.get("status"),
            "generated_at": report.get("generated_at"),
            "summary": {
                "gate_count": len(gates),
                "passed_gate_count": int((report.get("summary") or {}).get("passed_gate_count") or 0),
                "blocked_gate_count": int((report.get("summary") or {}).get("blocked_gate_count") or 0),
                "top_blockers": list((report.get("summary") or {}).get("top_blockers") or []),
            },
            "records": gates,
            "claims_to_avoid": list(report.get("claims_to_avoid") or []),
            **_report_safety_fields(report),
        }
    )


@router.get("/category-upgrade/proof-chain", response_model=ApiEnvelope)
def get_category_upgrade_proof_chain(
    include_slow_sources: bool = Query(default=False, description="Run slower proof collectors instead of the fast readiness view."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    report = get_category_upgrade_readiness_summary(db=db, current_user=current_user, include_slow_sources=include_slow_sources)
    return envelope(build_category_upgrade_proof_chain(report))


@router.get("/category-upgrade/backlog", response_model=ApiEnvelope)
def get_category_upgrade_backlog(
    include_slow_sources: bool = Query(default=False, description="Run slower proof collectors instead of the fast readiness view."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    report = get_category_upgrade_readiness_summary(db=db, current_user=current_user, include_slow_sources=include_slow_sources)
    backlog = list(report.get("backlog") or [])
    return envelope(
        {
            "status": report.get("status"),
            "generated_at": report.get("generated_at"),
            "summary": {
                "backlog_count": len(backlog),
                "priority_backlog": list((report.get("summary") or {}).get("priority_backlog") or []),
                "highest_priority_build": (report.get("summary") or {}).get("highest_priority_build"),
            },
            "records": backlog,
            **_report_safety_fields(report),
        }
    )


@router.get("/category-upgrade/support-export", response_model=ApiEnvelope)
def get_category_upgrade_support_export(
    include_slow_sources: bool = Query(default=False, description="Run slower proof collectors instead of the fast readiness view."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    report = get_category_upgrade_readiness_summary(db=db, current_user=current_user, include_slow_sources=include_slow_sources)
    return envelope(build_category_upgrade_support_export(report))


@router.post("/category-upgrade/support-export", response_model=ApiEnvelope)
def post_category_upgrade_support_export(
    include_slow_sources: bool = Query(default=False, description="Run slower proof collectors instead of the fast readiness view."),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    report = get_category_upgrade_readiness_summary(db=db, current_user=current_user, include_slow_sources=include_slow_sources)
    return envelope(write_category_upgrade_readiness_export(report))


@router.get("/desk", response_model=ApiEnvelope)
def get_desk_readiness(current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    strategies = list_strategies(db, current_user=current_user)["items"]
    scores = [int((item.get("readiness") or {}).get("score") or 0) for item in strategies if item.get("readiness")]
    return envelope({"strategy_count": len(strategies), "average_score": round(sum(scores) / len(scores), 2) if scores else 0, "items": strategies})


@router.get("/strategies/{strategy_id}", response_model=ApiEnvelope)
def get_strategy_readiness(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = latest_readiness_snapshot(db, tenant_id=tenant.id, strategy_id=strategy_id)
    return envelope(snapshot or evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id))


@router.post("/strategies/{strategy_id}/evaluate", response_model=ApiEnvelope)
def post_strategy_readiness(strategy_id: str, request: ReadinessEvaluateRequest | None = None, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    payload = request or ReadinessEvaluateRequest()
    return envelope(evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id, force_refresh=payload.force_refresh))


@router.get("/strategies/{strategy_id}/blockers", response_model=ApiEnvelope)
def get_strategy_readiness_blockers(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    snapshot = latest_readiness_snapshot(db, tenant_id=tenant.id, strategy_id=strategy_id) or evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id)
    return envelope({"items": list(snapshot.get("blockers") or []), "count": len(list(snapshot.get("blockers") or []))})


@router.get("/strategies/{strategy_id}/promotion", response_model=ApiEnvelope)
def get_strategy_readiness_promotion(strategy_id: str, current_user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> ApiEnvelope:
    return envelope(evaluate_strategy_readiness(db, current_user=current_user, strategy_id=strategy_id).get("promotion") or {})
