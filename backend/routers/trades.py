from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.core.auth import CurrentUser, get_current_user
from backend.core.database import get_db
from backend.core.responses import envelope
from backend.schemas import (
    ApiEnvelope,
    CancelOrderRequest,
    CloseTradeRequest,
    ControlChangeRequest,
    FillOrderRequest,
    OpenTradeRequest,
    ReplaceOrderRequest,
    TradeDecisionReviewRequest,
    TradeEvidenceRegisterRequest,
    TradeIntentDecisionRequest,
    TradeScenarioSaveRequest,
)
from backend.services.portfolio_service import get_open_trades, get_trade_journal
from backend.services.trade_service import (
    approve_trade_intent,
    build_trade_intent_trust_packet_json,
    cancel_pending_order_from_request,
    close_trade_from_request,
    conditionally_approve_trade_intent,
    create_trade_intent_from_request,
    expire_trade_intent,
    fill_pending_order_from_request,
    get_pending_orders_snapshot,
    get_trade_summary,
    get_trade_intent_trust_packet,
    list_trade_intents,
    open_trade_from_request,
    preview_trade_from_request,
    reject_trade_intent,
    replace_pending_order_from_request,
    sync_pending_orders_from_broker,
)
from backend.services.trade_workflow_service import (
    build_workflow_ops_dashboard,
    get_trade_intent_decision_review,
    get_trade_intent_evidence_register,
    list_control_change_cases,
    list_trade_scenarios,
    request_control_change_case,
    save_trade_scenario_from_intent,
    update_trade_intent_decision_review,
    update_trade_intent_evidence_register,
)
from sqlalchemy.orm import Session

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/open", response_model=ApiEnvelope)
def open_trades(
    limit: int = Query(default=250, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=""),
    action_filter: str = Query(default="all"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_open_trades(search=search, limit=limit, offset=offset, action_filter=action_filter, db=db, current_user=current_user))


@router.post("/open", response_model=ApiEnvelope)
def open_trade(
    request: OpenTradeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(open_trade_from_request(request, db=db, current_user=current_user))


@router.post("/preview", response_model=ApiEnvelope)
def preview_trade(
    request: OpenTradeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(preview_trade_from_request(request, db=db, current_user=current_user))


@router.post("/intents", response_model=ApiEnvelope)
def create_trade_intent(
    request: OpenTradeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(create_trade_intent_from_request(request, db=db, current_user=current_user))


@router.get("/intents", response_model=ApiEnvelope)
def trade_intents(
    status: str = Query(default="pending_approval"),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_trade_intents(db=db, current_user=current_user, status_filter=status))


@router.get("/workflow-ops", response_model=ApiEnvelope)
def trade_workflow_ops(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(build_workflow_ops_dashboard(db=db, current_user=current_user))


@router.get("/scenarios", response_model=ApiEnvelope)
def trade_scenarios(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_trade_scenarios(db=db, current_user=current_user))


@router.get("/control-changes", response_model=ApiEnvelope)
def trade_control_changes(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(list_control_change_cases(db=db, current_user=current_user))


@router.post("/control-changes", response_model=ApiEnvelope)
def create_trade_control_change(
    request: ControlChangeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(request_control_change_case(request, db=db, current_user=current_user))


@router.get("/intents/{intent_id}/decision-review", response_model=ApiEnvelope)
def trade_intent_decision_review(
    intent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_trade_intent_decision_review(intent_id, db=db, current_user=current_user))


@router.post("/intents/{intent_id}/decision-review", response_model=ApiEnvelope)
def update_trade_intent_review(
    intent_id: str,
    request: TradeDecisionReviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_trade_intent_decision_review(intent_id, request, db=db, current_user=current_user))


@router.get("/intents/{intent_id}/evidence-register", response_model=ApiEnvelope)
def trade_intent_evidence_register(
    intent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_trade_intent_evidence_register(intent_id, db=db, current_user=current_user))


@router.post("/intents/{intent_id}/evidence-register", response_model=ApiEnvelope)
def update_trade_intent_evidence(
    intent_id: str,
    request: TradeEvidenceRegisterRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(update_trade_intent_evidence_register(intent_id, request, db=db, current_user=current_user))


@router.post("/intents/{intent_id}/scenarios", response_model=ApiEnvelope)
def save_trade_intent_scenario(
    intent_id: str,
    request: TradeScenarioSaveRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(save_trade_scenario_from_intent(intent_id, request, db=db, current_user=current_user))


@router.post("/intents/{intent_id}/approve", response_model=ApiEnvelope)
def approve_intent(
    intent_id: str,
    request: TradeIntentDecisionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        approve_trade_intent(
            intent_id,
            note=(request.note if request else None),
            db=db,
            current_user=current_user,
        )
    )


@router.post("/intents/{intent_id}/conditional-approve", response_model=ApiEnvelope)
def conditional_approve_intent(
    intent_id: str,
    request: TradeIntentDecisionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        conditionally_approve_trade_intent(
            intent_id,
            note=(request.note if request else None),
            conditions=(request.conditions if request else None),
            db=db,
            current_user=current_user,
        )
    )


@router.post("/intents/{intent_id}/reject", response_model=ApiEnvelope)
def reject_intent(
    intent_id: str,
    request: TradeIntentDecisionRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(
        reject_trade_intent(
            intent_id,
            note=(request.note if request else None),
            reason=(request.reason if request else None),
            db=db,
            current_user=current_user,
        )
    )


@router.post("/intents/{intent_id}/expire", response_model=ApiEnvelope)
def expire_intent(
    intent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(expire_trade_intent(intent_id, db=db, current_user=current_user))


@router.get("/intents/{intent_id}/trust-packet", response_model=ApiEnvelope)
def trade_intent_trust_packet(
    intent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_trade_intent_trust_packet(intent_id, db=db, current_user=current_user))


@router.get("/intents/{intent_id}/trust-packet/export")
def export_trade_intent_trust_packet(
    intent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    json_data = build_trade_intent_trust_packet_json(intent_id, db=db, current_user=current_user)
    return StreamingResponse(
        iter([json_data]),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=broker-trust-packet-{intent_id}.json"},
    )


@router.post("/close", response_model=ApiEnvelope)
def close_trade(
    request: CloseTradeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(close_trade_from_request(request, db=db, current_user=current_user))


@router.get("/orders", response_model=ApiEnvelope)
def pending_orders(
    ticker: str = Query(default=""),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_pending_orders_snapshot(db=db, current_user=current_user, ticker=ticker))


@router.post("/orders/sync", response_model=ApiEnvelope)
def sync_orders(
    ticker: str = Query(default=""),
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(sync_pending_orders_from_broker(db=db, current_user=current_user, ticker=ticker))


@router.post("/orders/{order_id}/replace", response_model=ApiEnvelope)
def replace_order(
    order_id: str,
    request: ReplaceOrderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(replace_pending_order_from_request(order_id, request, db=db, current_user=current_user))


@router.post("/orders/{order_id}/cancel", response_model=ApiEnvelope)
def cancel_order(
    order_id: str,
    request: CancelOrderRequest | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(cancel_pending_order_from_request(order_id, request, db=db, current_user=current_user))


@router.post("/orders/{order_id}/fill", response_model=ApiEnvelope)
def fill_order(
    order_id: str,
    request: FillOrderRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(fill_pending_order_from_request(order_id, request, db=db, current_user=current_user))


@router.get("/summary", response_model=ApiEnvelope)
def trade_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiEnvelope:
    return envelope(get_trade_summary(db=db, current_user=current_user))


@router.get("/journal/export")
def export_trade_journal(
    search: str = Query(default=""),
    result_filter: str = Query(default="all"),
    direction_filter: str = Query(default="all"),
    attribution_filter: str = Query(default="all"),
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    payload = get_trade_journal(
        limit=5000,
        offset=0,
        search=search,
        result_filter=result_filter,
        direction_filter=direction_filter,
        attribution_filter=attribution_filter,
        current_user=current_user,
    )
    rows = payload.get("journal", [])
    headers = [
        "ticker",
        "instrument_label",
        "direction",
        "verdict",
        "setup_grade",
        "contract_symbol",
        "order_type",
        "time_in_force",
        "entry_contract_mid",
        "close_contract_mid",
        "max_risk_dollars",
        "pnl_dollars",
        "result_label",
        "attribution_label",
        "execution_review_label",
        "fill_slippage_bps",
        "closed_at",
    ]
    lines = [",".join(headers)]
    for row in rows:
        values = []
        for header in headers:
            value = str(row.get(header, "")).replace('"', '""')
            values.append(f'"{value}"')
        lines.append(",".join(values))
    csv_data = "\n".join(lines)
    return StreamingResponse(iter([csv_data]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=trade_journal.csv"})
