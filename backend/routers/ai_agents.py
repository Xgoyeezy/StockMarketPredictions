from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import ApiEnvelope
from backend.services.hedge_fund_ai_agents import (
    create_agent_proposal,
    decide_agent_proposal,
    get_agent_memo,
    get_ai_agents_safety,
    get_ai_agents_summary,
    get_ai_agents_llm_status,
    get_external_review_plan,
    get_latest_committee_report,
    get_readiness_backlog,
    list_agent_memos,
    list_agent_proposals,
    list_agent_roles,
    run_desk_agent,
    run_investment_committee,
    run_role_agent,
)

router = APIRouter(prefix="/ai-agents", tags=["ai-agents"])


@router.get("/summary", response_model=ApiEnvelope)
def get_summary(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_ai_agents_summary())


@router.get("/roles", response_model=ApiEnvelope)
def get_roles(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(list_agent_roles())


@router.get("/memos", response_model=ApiEnvelope)
def get_memos(
    agent_role: str | None = None,
    symbol: str | None = None,
    linked_candidate_id: str | None = None,
    date: str | None = None,
    desk: str | None = None,
    warning_type: str | None = None,
    _current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(
        list_agent_memos(
            agent_role=agent_role,
            symbol=symbol,
            linked_candidate_id=linked_candidate_id,
            date=date,
            desk=desk,
            warning_type=warning_type,
        )
    )


@router.get("/memos/{memo_id}", response_model=ApiEnvelope)
def get_memo(memo_id: str, _current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    record = get_agent_memo(memo_id)
    if record is None:
        raise HTTPException(status_code=404, detail="AI agent memo was not found.")
    return envelope(
        {
            "status": "ready",
            "generated_at": record.get("created_at"),
            "research_only": True,
            "authority_level": "research_only",
            "record": record,
            "warnings": [],
            "missing_fields": [],
            "safety_notes": record.get("safety_notes", []),
        }
    )


@router.get("/committee/latest", response_model=ApiEnvelope)
def get_committee_latest(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_latest_committee_report())


@router.get("/safety", response_model=ApiEnvelope)
def get_safety(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_ai_agents_safety())


@router.get("/llm-status", response_model=ApiEnvelope)
def get_llm_status(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_ai_agents_llm_status())


@router.get("/readiness-backlog", response_model=ApiEnvelope)
def get_backlog(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_readiness_backlog())


@router.get("/external-review", response_model=ApiEnvelope)
def get_external_review(_current_user: CurrentUser = Depends(get_current_user)) -> ApiEnvelope:
    return envelope(get_external_review_plan())


@router.get("/proposals", response_model=ApiEnvelope)
def get_proposals(
    status: str | None = None,
    proposal_type: str | None = None,
    linked_memo_id: str | None = None,
    _current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(list_agent_proposals(status=status, proposal_type=proposal_type, linked_memo_id=linked_memo_id))


@router.post("/proposals", response_model=ApiEnvelope)
def create_proposal(
    payload: dict[str, Any] = Body(default_factory=dict),
    _current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(create_agent_proposal(payload))


@router.post("/proposals/{proposal_id}/decision", response_model=ApiEnvelope)
def decide_proposal(
    proposal_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _current_user: CurrentUser = Depends(get_current_user),
) -> ApiEnvelope:
    return envelope(decide_agent_proposal(proposal_id, payload))


@router.post("/run-role/{role_name}", response_model=ApiEnvelope)
def run_role(
    role_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_role_agent(role_name, db=db, current_user=current_user))


@router.post("/run-committee", response_model=ApiEnvelope)
def run_committee(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_investment_committee(db=db, current_user=current_user))


@router.post("/run-desk/{desk_name}", response_model=ApiEnvelope)
def run_desk(
    desk_name: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(run_desk_agent(desk_name, db=db, current_user=current_user))
