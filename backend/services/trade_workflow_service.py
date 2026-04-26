from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.saas import AuditEvent, Tenant, TradeApprovalIntent, User
from backend.schemas import (
    ControlChangeRequest,
    TradeDecisionReviewRequest,
    TradeEvidenceRegisterRequest,
    TradeScenarioSaveRequest,
)
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import NotFoundError, ValidationServiceError
from backend.services.serialization import serialize_value
from backend.services.storage_utils import read_json_file, write_json_file
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user

TRADE_DECISION_REVIEW_VERSION = "trade_decision_review_v1"
TRADE_EVIDENCE_REGISTER_VERSION = "trade_evidence_register_v1"
TRADE_SCENARIO_VERSION = "saved_trade_scenario_v1"
CONTROL_CHANGE_VERSION = "control_change_case_v1"
WORKFLOW_PACKET_VERSION = "trade_workflow_packet_v1"
_WORKFLOW_STORE_FILE = Path(settings.storage_dir) / "trade_workflow.json"
_DEFAULT_STORE: dict[str, Any] = {"scenarios": [], "control_changes": []}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean_text(value: Any, *, limit: int = 800) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    return cleaned[:limit]


def _normalize_text_list(values: Any, *, limit: int = 8, item_limit: int = 240) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in _safe_list(values):
        cleaned = _clean_text(value, limit=item_limit)
        key = cleaned.lower()
        if cleaned and key not in seen:
            normalized.append(cleaned)
            seen.add(key)
        if len(normalized) >= limit:
            break
    return normalized


def _canonical_hash(value: Any) -> str:
    canonical = json.dumps(serialize_value(value), sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _read_store() -> dict[str, Any]:
    payload = read_json_file(_WORKFLOW_STORE_FILE, dict(_DEFAULT_STORE))
    if not isinstance(payload, dict):
        payload = dict(_DEFAULT_STORE)
    payload["scenarios"] = [row for row in _safe_list(payload.get("scenarios")) if isinstance(row, dict)]
    payload["control_changes"] = [row for row in _safe_list(payload.get("control_changes")) if isinstance(row, dict)]
    return payload


def _write_store(payload: dict[str, Any]) -> None:
    write_json_file(_WORKFLOW_STORE_FILE, payload)


def _tenant_scope(current_user: Any | None, tenant: Tenant | None = None) -> dict[str, str]:
    return {
        "tenant_id": str(getattr(tenant, "id", None) or getattr(current_user, "tenant_id", "") or ""),
        "tenant_slug": str(getattr(tenant, "slug", None) or getattr(current_user, "tenant_slug", "") or settings.demo_tenant_slug),
        "user_id": str(getattr(current_user, "user_id", "") or getattr(current_user, "auth_subject", "") or settings.demo_user_id),
    }


def _resolve_intent_for_actor(
    *,
    db: Session | None,
    current_user: Any | None,
    intent_id: str,
) -> tuple[Tenant, User, TradeApprovalIntent]:
    if db is None or current_user is None:
        raise ValidationServiceError("Trade workflow actions require an authenticated tenant session.")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        raise ValidationServiceError("Trade workflow actions require an authenticated user.")
    row = db.execute(
        select(TradeApprovalIntent).where(TradeApprovalIntent.id == intent_id, TradeApprovalIntent.tenant_id == tenant.id)
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Trade intent was not found.")
    return tenant, actor, row


def _strategy_basis(intent: TradeApprovalIntent) -> dict[str, Any]:
    metadata = _safe_dict(intent.metadata_json)
    stored = _safe_dict(metadata.get("strategy_release_snapshot"))
    if stored:
        return serialize_value(stored)
    request = _safe_dict(intent.request_payload_json)
    return {
        "schema_version": "strategy_release_basis_v1",
        "release_id": f"{_clean_text(request.get('route_family')) or 'current'}:{_clean_text(request.get('route_version')) or 'unreleased'}",
        "route_family": _clean_text(request.get("route_family")) or "current",
        "route_version": _clean_text(request.get("route_version")) or None,
        "model_basis": _safe_dict(intent.analysis_json),
        "risk_basis": {
            "account_size": request.get("account_size"),
            "risk_percent": request.get("risk_percent"),
            "max_daily_loss_r": request.get("max_daily_loss_r"),
            "max_open_positions": request.get("max_open_positions"),
            "max_notional_per_trade": request.get("max_notional_per_trade"),
        },
    }


def _chart_levels(intent: TradeApprovalIntent) -> dict[str, Any]:
    request = _safe_dict(intent.request_payload_json)
    analysis = _safe_dict(intent.analysis_json)
    option_plan = _safe_dict(analysis.get("option_plan"))
    position = _safe_dict(intent.position_json)
    return {
        "ticker": intent.ticker,
        "entry": request.get("limit_price") or request.get("live_price"),
        "target": option_plan.get("expected_underlying_target") or position.get("target_price"),
        "stop": option_plan.get("stop_loss") or position.get("stop_loss"),
        "invalidation": option_plan.get("invalidation_price") or position.get("invalidation_price"),
        "contract_symbol": request.get("contract_symbol") or _safe_dict(intent.order_ticket_json).get("contract_symbol"),
    }


def build_evidence_register_snapshot(intent: TradeApprovalIntent) -> dict[str, Any]:
    metadata = _safe_dict(intent.metadata_json)
    stored = _safe_dict(metadata.get("evidence_register"))
    stored_items = [item for item in _safe_list(stored.get("items")) if isinstance(item, dict)]
    request = _safe_dict(intent.request_payload_json)
    analysis = _safe_dict(intent.analysis_json)
    option_plan = _safe_dict(analysis.get("option_plan"))
    generated_items = [
        {
            "key": "market_snapshot",
            "label": "Market snapshot",
            "status": "captured" if request.get("live_price") or metadata.get("live_price") else "missing",
            "detail": "Live price and request context are captured.",
            "source": "trade_request",
            "value": request.get("live_price") or metadata.get("live_price"),
        },
        {
            "key": "chart_levels",
            "label": "Chart levels",
            "status": "captured" if any(value for value in _chart_levels(intent).values()) else "missing",
            "detail": "Entry, target, stop, invalidation, and contract levels are frozen when available.",
            "source": "trade_ticket",
            "value": _chart_levels(intent),
        },
        {
            "key": "model_basis",
            "label": "Model basis",
            "status": "captured" if bool(analysis) else "missing",
            "detail": "Recommendation analysis payload is available for review.",
            "source": "analysis",
            "value": analysis,
        },
        {
            "key": "option_chain_quote",
            "label": "Option-chain quote",
            "status": "captured" if request.get("contract_mid") or request.get("contract_bid") or request.get("contract_ask") else "not_applicable",
            "detail": "Contract bid, ask, mid, spread, volume, and open interest are captured for listed options.",
            "source": "option_ticket",
            "value": {
                "bid": request.get("contract_bid"),
                "ask": request.get("contract_ask"),
                "mid": request.get("contract_mid"),
                "spread_pct": request.get("contract_spread_pct"),
                "volume": request.get("contract_volume"),
                "open_interest": request.get("contract_open_interest"),
                "quote_timestamp": request.get("contract_quote_timestamp"),
            },
        },
        {
            "key": "event_context",
            "label": "News and event context",
            "status": "captured" if option_plan or analysis.get("event_risk") is not None else "missing",
            "detail": "Event risk and recommendation context are captured when the analysis provides them.",
            "source": "analysis",
            "value": {
                "event_risk": analysis.get("event_risk"),
                "reject_reason": analysis.get("reject_reason"),
                "news_sentiment": analysis.get("news_sentiment"),
            },
        },
        {
            "key": "liquidity_execution",
            "label": "Liquidity and execution check",
            "status": "captured" if metadata.get("liquidity_execution") or metadata.get("route_eligibility") else "missing",
            "detail": "Execution route, liquidity, and blocker checks are attached when available.",
            "source": "risk_controls",
            "value": metadata.get("liquidity_execution") or metadata.get("route_eligibility") or {},
        },
        {
            "key": "strategy_release_basis",
            "label": "Strategy release basis",
            "status": "captured" if bool(_strategy_basis(intent)) else "missing",
            "detail": "Strategy, route, model, and risk basis are frozen for later review.",
            "source": "strategy_release",
            "value": _strategy_basis(intent),
        },
    ]
    manual_keys = {str(item.get("key") or "").strip() for item in stored_items}
    items = stored_items + [item for item in generated_items if item["key"] not in manual_keys]
    missing_items = [item["label"] for item in items if str(item.get("status")).lower() in {"missing", "blocked"}]
    packet = {
        "schema_version": TRADE_EVIDENCE_REGISTER_VERSION,
        "generated_at": _utc_now(),
        "items": serialize_value(items),
        "missing_items": missing_items,
        "notes": _clean_text(stored.get("notes")),
        "source_grounding": {
            "recommendation_sources": ["trade_request", "analysis", "trade_ticket", "risk_controls", "strategy_release"],
            "source_count": len(items),
        },
    }
    packet["fingerprint"] = _canonical_hash({key: value for key, value in packet.items() if key != "fingerprint"})
    return packet


def _review_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "standard_path": _clean_text(payload.get("standard_path")),
        "requested_deviation": _clean_text(payload.get("requested_deviation")),
        "thesis_rationale": _clean_text(payload.get("thesis_rationale")),
        "accepted_risk": bool(payload.get("accepted_risk")),
        "accepted_risk_owner": _clean_text(payload.get("accepted_risk_owner"), limit=160),
        "accepted_risk_note": _clean_text(payload.get("accepted_risk_note")),
        "challenge_raised": bool(payload.get("challenge_raised")),
        "challenge_notes": _clean_text(payload.get("challenge_notes")),
        "unresolved_conditions": _normalize_text_list(payload.get("unresolved_conditions")),
        "marked_decision_ready": bool(payload.get("marked_decision_ready")),
    }


def _decision_review_readiness(intent: TradeApprovalIntent, review: dict[str, Any]) -> dict[str, Any]:
    evidence = build_evidence_register_snapshot(intent)
    blockers: list[str] = []
    warnings: list[str] = []
    if not review.get("standard_path"):
        blockers.append("Standard path is missing.")
    if not review.get("requested_deviation"):
        blockers.append("Requested deviation is missing.")
    if not review.get("thesis_rationale") or len(str(review.get("thesis_rationale"))) < 20:
        blockers.append("Thesis rationale is missing or too thin.")
    if evidence.get("missing_items"):
        blockers.append("Evidence register has missing required sources.")
    if review.get("accepted_risk") and (not review.get("accepted_risk_owner") or not review.get("accepted_risk_note")):
        blockers.append("Accepted risk must have a named owner and explanation.")
    if review.get("challenge_raised") and not review.get("challenge_notes"):
        blockers.append("Challenge state requires challenge notes.")
    if review.get("unresolved_conditions"):
        blockers.append("Unresolved conditions must be cleared before final readiness.")
    if not _strategy_basis(intent):
        blockers.append("Strategy release basis is missing.")
    if intent.status in {"rejected", "expired", "submission_failed"}:
        warnings.append("Intent is not in an active approval state.")
    return {
        "status": "ready" if not blockers else "blocked",
        "ready_for_final_decision": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }


def build_decision_review_snapshot(intent: TradeApprovalIntent) -> dict[str, Any]:
    metadata = _safe_dict(intent.metadata_json)
    stored = _review_from_payload(_safe_dict(metadata.get("decision_review")))
    readiness = _decision_review_readiness(intent, stored)
    stored.update(
        {
            "schema_version": TRADE_DECISION_REVIEW_VERSION,
            "readiness": readiness,
            "updated_at": _safe_dict(metadata.get("decision_review")).get("updated_at"),
            "updated_by": _safe_dict(metadata.get("decision_review")).get("updated_by"),
        }
    )
    return serialize_value(stored)


def update_trade_intent_decision_review(
    intent_id: str,
    request: TradeDecisionReviewRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    payload = request.model_dump()
    review = _review_from_payload(payload)
    readiness = _decision_review_readiness(intent, review)
    if request.mark_decision_ready and readiness["blockers"]:
        raise ValidationServiceError("Decision review cannot be marked ready until blockers are resolved: " + "; ".join(readiness["blockers"]))
    review.update(
        {
            "schema_version": TRADE_DECISION_REVIEW_VERSION,
            "marked_decision_ready": bool(request.mark_decision_ready and readiness["ready_for_final_decision"]),
            "readiness": readiness,
            "updated_at": _utc_now(),
            "updated_by": actor.email,
        }
    )
    metadata = _safe_dict(intent.metadata_json)
    metadata["decision_review"] = serialize_value(review)
    intent.metadata_json = serialize_value(metadata)
    record_audit_event(
        db,
        event_type="trade_decision_review.updated",
        tenant=tenant,
        user=actor,
        payload={"intent_id": intent.id, "ticker": intent.ticker, "ready": readiness["ready_for_final_decision"]},
    )
    db.commit()
    db.refresh(intent)
    return {"decision_review": build_decision_review_snapshot(intent)}


def get_trade_intent_decision_review(
    intent_id: str,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    _, _, intent = _resolve_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    return {"decision_review": build_decision_review_snapshot(intent)}


def update_trade_intent_evidence_register(
    intent_id: str,
    request: TradeEvidenceRegisterRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    metadata = _safe_dict(intent.metadata_json)
    existing = _safe_dict(metadata.get("evidence_register"))
    items = [item for item in _safe_list(existing.get("items")) if isinstance(item, dict)]
    for item in request.items or []:
        row = {
            "key": _clean_text(item.get("key") or item.get("label") or f"manual-{uuid4().hex[:8]}", limit=80),
            "label": _clean_text(item.get("label") or item.get("key") or "Manual evidence", limit=160),
            "status": _clean_text(item.get("status") or "captured", limit=40),
            "detail": _clean_text(item.get("detail")),
            "source": _clean_text(item.get("source") or "manual_review", limit=80),
            "value": serialize_value(item.get("value")),
        }
        items = [existing_item for existing_item in items if existing_item.get("key") != row["key"]]
        items.append(row)
    existing["items"] = items
    existing["notes"] = _clean_text(request.notes)
    existing["updated_at"] = _utc_now()
    existing["updated_by"] = actor.email
    metadata["evidence_register"] = serialize_value(existing)
    intent.metadata_json = serialize_value(metadata)
    record_audit_event(
        db,
        event_type="trade_evidence_register.updated",
        tenant=tenant,
        user=actor,
        payload={"intent_id": intent.id, "ticker": intent.ticker, "manual_item_count": len(request.items or [])},
    )
    db.commit()
    db.refresh(intent)
    return {"evidence_register": build_evidence_register_snapshot(intent)}


def get_trade_intent_evidence_register(
    intent_id: str,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    _, _, intent = _resolve_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    return {"evidence_register": build_evidence_register_snapshot(intent)}


def _scenario_from_intent(
    intent: TradeApprovalIntent,
    *,
    tenant: Tenant,
    current_user: Any,
    request: TradeScenarioSaveRequest,
) -> dict[str, Any]:
    review = build_decision_review_snapshot(intent)
    evidence = build_evidence_register_snapshot(intent)
    outcome = _clean_text(request.outcome or intent.status or "current", limit=80).lower().replace(" ", "_")
    scenario = {
        "schema_version": TRADE_SCENARIO_VERSION,
        "id": str(uuid4()),
        "tenant_id": tenant.id,
        "tenant_slug": _tenant_scope(current_user, tenant)["tenant_slug"],
        "user_id": _tenant_scope(current_user, tenant)["user_id"],
        "source_intent_id": intent.id,
        "name": _clean_text(request.name, limit=120) or f"{intent.ticker} trade scenario",
        "ticker": intent.ticker,
        "instrument_type": intent.instrument_type,
        "decision_state": outcome,
        "status": intent.status,
        "release_status": _clean_text(request.release_status or "current_release", limit=80),
        "market_regime": _clean_text(request.market_regime or "not_captured", limit=120),
        "setup_label": _clean_text(request.setup_label or _safe_dict(intent.request_payload_json).get("thesis_direction") or intent.instrument_type, limit=120),
        "chart_levels": _chart_levels(intent),
        "risk_settings": _strategy_basis(intent).get("risk_basis", {}),
        "strategy_basis": _strategy_basis(intent),
        "decision_review": review,
        "evidence_fingerprint": evidence.get("fingerprint"),
        "notes": _clean_text(request.notes),
        "saved_at": _utc_now(),
    }
    scenario["fingerprint"] = _canonical_hash({key: value for key, value in scenario.items() if key != "fingerprint"})
    return serialize_value(scenario)


def save_trade_scenario_from_intent(
    intent_id: str,
    request: TradeScenarioSaveRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    tenant, actor, intent = _resolve_intent_for_actor(db=db, current_user=current_user, intent_id=intent_id)
    scenario = _scenario_from_intent(intent, tenant=tenant, current_user=current_user, request=request)
    store = _read_store()
    store["scenarios"].append(scenario)
    _write_store(store)
    record_audit_event(
        db,
        event_type="trade_scenario.saved",
        tenant=tenant,
        user=actor,
        payload={"intent_id": intent.id, "scenario_id": scenario["id"], "ticker": intent.ticker, "decision_state": scenario["decision_state"]},
    )
    db.commit()
    return {"saved": True, "scenario": scenario}


def _decision_contrast_rank(left: str, right: str) -> int:
    pair = {left, right}
    high_contrast = [
        {"win", "loss"},
        {"approved", "rejected"},
        {"submitted", "rejected"},
        {"current", "historical"},
        {"ready", "blocked"},
    ]
    for index, contrast in enumerate(high_contrast):
        if contrast.issubset(pair):
            return index
    return 20 if left != right else 99


def _sort_scenarios_for_group(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_items = sorted(items, key=lambda row: (str(row.get("release_status") != "current_release"), str(row.get("saved_at") or "")), reverse=False)
    best_pair: tuple[str, str] | None = None
    states = sorted({str(item.get("decision_state") or "unknown") for item in items})
    for left in states:
        for right in states:
            if left == right:
                continue
            pair = (left, right)
            if best_pair is None or _decision_contrast_rank(*pair) < _decision_contrast_rank(*best_pair):
                best_pair = pair
    if not best_pair:
        return sorted_items
    first = next((item for item in sorted_items if item.get("decision_state") == best_pair[0]), None)
    second = next((item for item in sorted_items if item.get("decision_state") == best_pair[1] and item.get("id") != (first or {}).get("id")), None)
    if not first or not second:
        return sorted_items
    remaining = [item for item in sorted_items if item.get("id") not in {first.get("id"), second.get("id")}]
    return [first, second, *remaining]


def list_trade_scenarios(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    if db is None or current_user is None:
        return {"items": [], "count": 0, "comparison_groups": []}
    tenant = _resolve_tenant_for_current_user(db, current_user)
    scope = _tenant_scope(current_user, tenant)
    store = _read_store()
    items = [
        row for row in store["scenarios"]
        if str(row.get("tenant_id") or "") == tenant.id or str(row.get("tenant_slug") or "") == scope["tenant_slug"]
    ]
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = f"{item.get('ticker') or 'UNKNOWN'}::{item.get('setup_label') or 'Setup'}"
        groups.setdefault(key, []).append(item)
    comparison_groups = [
        {
            "key": key,
            "label": key.replace("::", " / "),
            "contrast_ready": len({row.get("decision_state") for row in rows}) > 1,
            "items": _sort_scenarios_for_group(rows),
        }
        for key, rows in groups.items()
    ]
    comparison_groups.sort(key=lambda group: (not group["contrast_ready"], group["label"]))
    return {"items": items, "count": len(items), "comparison_groups": comparison_groups}


def _risk_for_control_change(control_type: str) -> tuple[int, str, list[dict[str, Any]]]:
    normalized = control_type.strip().lower()
    score = 25
    factors: list[dict[str, Any]] = [{"code": "manual_review", "label": "Manual review required", "points": 15}]
    if normalized in {"live_mode_activation", "api_key_rotation", "billing_payment_change"}:
        score += 45
        factors.append({"code": "funds_or_access", "label": "Funds or credential access can change", "points": 45})
    if normalized in {"automation_enablement", "risk_limit_change"}:
        score += 30
        factors.append({"code": "trading_control", "label": "Trading risk controls can change", "points": 30})
    if normalized in {"linked_account_change", "withdrawal_or_payment_change"}:
        score += 40
        factors.append({"code": "account_route", "label": "Account or payment route can change", "points": 40})
    band = "critical" if score >= 80 else "high" if score >= 60 else "moderate" if score >= 35 else "low"
    return score, band, factors


def request_control_change_case(
    request: ControlChangeRequest,
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    if db is None or current_user is None:
        raise ValidationServiceError("Control change requests require an authenticated tenant session.")
    tenant = _resolve_tenant_for_current_user(db, current_user)
    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        raise ValidationServiceError("Control change requests require an authenticated user.")
    scope = _tenant_scope(current_user, tenant)
    score, band, factors = _risk_for_control_change(request.control_type)
    case = {
        "schema_version": CONTROL_CHANGE_VERSION,
        "id": f"CTRL-{uuid4().hex[:10].upper()}",
        "tenant_id": tenant.id,
        "tenant_slug": scope["tenant_slug"],
        "requested_by": actor.email,
        "control_type": request.control_type,
        "summary": _clean_text(request.summary, limit=240),
        "applies_to": _clean_text(request.applies_to, limit=160),
        "current_value": _clean_text(request.current_value),
        "requested_value": _clean_text(request.requested_value),
        "rationale": _clean_text(request.rationale),
        "status": "pending_review",
        "risk_score": score,
        "risk_band": band,
        "risk_factors": factors,
        "verification_checklist": [
            {"key": "human_approval", "label": "Human approval", "status": "missing", "detail": "A reviewer must approve before this change is active."},
            {"key": "known_channel", "label": "Known-channel verification", "status": "missing", "detail": "Verify request through a known account or admin channel."},
            {"key": "audit_reason", "label": "Audit rationale", "status": "pass" if request.rationale else "missing", "detail": "Rationale must explain why the change is needed now."},
        ],
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    case["fingerprint"] = _canonical_hash({key: value for key, value in case.items() if key != "fingerprint"})
    store = _read_store()
    store["control_changes"].append(case)
    _write_store(store)
    record_audit_event(
        db,
        event_type="control_change.requested",
        tenant=tenant,
        user=actor,
        payload={"control_case_id": case["id"], "control_type": case["control_type"], "risk_band": case["risk_band"]},
    )
    db.commit()
    return {"created": True, "control_change": case}


def list_control_change_cases(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    if db is None or current_user is None:
        return {"items": [], "count": 0, "status_counts": {}}
    tenant = _resolve_tenant_for_current_user(db, current_user)
    scope = _tenant_scope(current_user, tenant)
    store = _read_store()
    items = [
        row for row in store["control_changes"]
        if str(row.get("tenant_id") or "") == tenant.id or str(row.get("tenant_slug") or "") == scope["tenant_slug"]
    ]
    items.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    return {
        "items": items,
        "count": len(items),
        "status_counts": dict(Counter(str(item.get("status") or "unknown") for item in items)),
        "high_risk_count": sum(1 for item in items if str(item.get("risk_band") or "").lower() in {"high", "critical"}),
    }


def build_workflow_ops_dashboard(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    if db is None or current_user is None:
        return {}
    tenant = _resolve_tenant_for_current_user(db, current_user)
    intents = db.execute(
        select(TradeApprovalIntent).where(TradeApprovalIntent.tenant_id == tenant.id).order_by(TradeApprovalIntent.created_at.desc()).limit(100)
    ).scalars().all()
    decision_snapshots = [build_decision_review_snapshot(intent) for intent in intents]
    evidence_snapshots = [build_evidence_register_snapshot(intent) for intent in intents]
    scenarios = list_trade_scenarios(db=db, current_user=current_user)
    controls = list_control_change_cases(db=db, current_user=current_user)
    audit_rows = db.execute(
        select(AuditEvent).where(AuditEvent.tenant_id == tenant.id).order_by(AuditEvent.created_at.desc()).limit(50)
    ).scalars().all()
    release_blockers = [
        item for item in decision_snapshots
        if _safe_dict(item.get("readiness")).get("blockers")
    ]
    return {
        "workflow_packet_version": WORKFLOW_PACKET_VERSION,
        "generated_at": _utc_now(),
        "trade_intent_count": len(intents),
        "decision_not_ready_count": sum(1 for item in decision_snapshots if not _safe_dict(item.get("readiness")).get("ready_for_final_decision")),
        "weak_rationale_count": sum(1 for item in decision_snapshots if "Thesis rationale is missing or too thin." in _safe_dict(item.get("readiness")).get("blockers", [])),
        "unowned_accepted_risk_count": sum(1 for item in decision_snapshots if "Accepted risk must have a named owner and explanation." in _safe_dict(item.get("readiness")).get("blockers", [])),
        "evidence_gap_count": sum(1 for item in evidence_snapshots if item.get("missing_items")),
        "strategy_release_blocker_count": len(release_blockers),
        "saved_scenario_count": scenarios.get("count", 0),
        "contrast_ready_group_count": sum(1 for item in scenarios.get("comparison_groups", []) if item.get("contrast_ready")),
        "pending_control_change_count": controls.get("status_counts", {}).get("pending_review", 0),
        "high_risk_control_change_count": controls.get("high_risk_count", 0),
        "recent_audit_events": [
            {
                "event_type": row.event_type,
                "actor_email": row.actor_email,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "payload": row.payload_json or {},
            }
            for row in audit_rows[:10]
        ],
    }
