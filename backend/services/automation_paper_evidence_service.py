from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, Tenant
from backend.services import (
    automation_accuracy_calibration_service,
    automation_daily_objective_service,
    notes_service,
)
from backend.services.audit_service import record_audit_event
from backend.services.serialization import serialize_value

MARKET_TIMEZONE = ZoneInfo("America/New_York")
PAPER_EVIDENCE_NOTE_OWNER = "automation-ai"
PAPER_EVIDENCE_HISTORY_LIMIT = 12
PAPER_EVIDENCE_NOTE_LIMIT = 250
PAPER_EVIDENCE_PERSONAL_PAPER_PROFILE = "personal_paper"

PAPER_EVIDENCE_SETTINGS_DEFAULTS: dict[str, Any] = {
    "paper_evidence_collection_enabled": True,
    "paper_evidence_auto_review_enabled": True,
    "paper_evidence_require_edge_telemetry": True,
    "paper_evidence_require_spread_telemetry": True,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


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


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on"}:
            return True
        if cleaned in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _normalize_profile_key(profile_key: str | None) -> str:
    return str(profile_key or PAPER_EVIDENCE_PERSONAL_PAPER_PROFILE).strip().lower()


def _profile_tag(profile_key: str) -> str:
    cleaned = _normalize_profile_key(profile_key).replace(":", "-") or PAPER_EVIDENCE_PERSONAL_PAPER_PROFILE
    return f"profile-{cleaned}"


def _session_day_for(now: datetime | None = None) -> str:
    current = now or _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(MARKET_TIMEZONE).date().isoformat()


def _session_bounds_utc(session_day: str) -> tuple[datetime, datetime]:
    day = datetime.strptime(session_day, "%Y-%m-%d").date()
    start_local = datetime.combine(day, time.min, tzinfo=MARKET_TIMEZONE)
    end_local = datetime.combine(day, time.max, tzinfo=MARKET_TIMEZONE)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def normalize_paper_evidence_settings(settings_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(settings_state or {})
    return {
        "paper_evidence_collection_enabled": _coerce_bool(
            state.get("paper_evidence_collection_enabled"),
            bool(PAPER_EVIDENCE_SETTINGS_DEFAULTS["paper_evidence_collection_enabled"]),
        ),
        "paper_evidence_auto_review_enabled": _coerce_bool(
            state.get("paper_evidence_auto_review_enabled"),
            bool(PAPER_EVIDENCE_SETTINGS_DEFAULTS["paper_evidence_auto_review_enabled"]),
        ),
        "paper_evidence_require_edge_telemetry": _coerce_bool(
            state.get("paper_evidence_require_edge_telemetry"),
            bool(PAPER_EVIDENCE_SETTINGS_DEFAULTS["paper_evidence_require_edge_telemetry"]),
        ),
        "paper_evidence_require_spread_telemetry": _coerce_bool(
            state.get("paper_evidence_require_spread_telemetry"),
            bool(PAPER_EVIDENCE_SETTINGS_DEFAULTS["paper_evidence_require_spread_telemetry"]),
        ),
    }


def normalize_paper_evidence_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    history = [
        serialize_value(item)
        for item in list(runtime.get("paper_evidence_history") or [])[:PAPER_EVIDENCE_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "paper_evidence_last_report": serialize_value(runtime.get("paper_evidence_last_report") or {}),
        "paper_evidence_last_note_id": str(runtime.get("paper_evidence_last_note_id") or "").strip() or None,
        "paper_evidence_note_session_day": str(runtime.get("paper_evidence_note_session_day") or "").strip() or None,
        "paper_evidence_last_run_at": _serialize_datetime(_parse_datetime(runtime.get("paper_evidence_last_run_at"))),
        "paper_evidence_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_evidence_last_scheduled_run_at"))
        ),
        "paper_evidence_last_scheduled_session_day": str(
            runtime.get("paper_evidence_last_scheduled_session_day") or ""
        ).strip()
        or None,
        "paper_evidence_last_error": str(runtime.get("paper_evidence_last_error") or "").strip() or None,
        "paper_evidence_history": history,
    }


def build_paper_evidence_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    state = state or {}
    settings = normalize_paper_evidence_settings(state.get("settings"))
    runtime = normalize_paper_evidence_runtime(state.get("runtime"))
    report = dict(runtime.get("paper_evidence_last_report") or {})
    if not report:
        return {
            "status": "not_run" if settings["paper_evidence_collection_enabled"] else "disabled",
            "label": "Not run" if settings["paper_evidence_collection_enabled"] else "Disabled",
            "enabled": settings["paper_evidence_collection_enabled"],
            "auto_review_enabled": settings["paper_evidence_auto_review_enabled"],
            "candidate_count": 0,
            "selected_candidate_count": 0,
            "rejected_candidate_count": 0,
            "edge_coverage_pct": 0.0,
            "spread_coverage_pct": 0.0,
            "liquidity_coverage_pct": 0.0,
            "rank_coverage_pct": 0.0,
            "objective_coverage_pct": 0.0,
            "note_coverage": False,
            "related_note_id": runtime.get("paper_evidence_last_note_id"),
            "history": runtime.get("paper_evidence_history") or [],
        }
    report.setdefault("enabled", settings["paper_evidence_collection_enabled"])
    report.setdefault("auto_review_enabled", settings["paper_evidence_auto_review_enabled"])
    report["related_note_id"] = report.get("related_note_id") or runtime.get("paper_evidence_last_note_id")
    report["history"] = runtime.get("paper_evidence_history") or []
    return serialize_value(report)


def _owned_rows(frame: pd.DataFrame | None, *, tenant_id: str | None, profile_key: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    result = frame.copy()
    if "automation_origin" in result.columns:
        marker = result["automation_origin"].astype(str).str.strip().str.lower()
        result = result[marker.eq("trade_automation")]
    if tenant_id and "automation_tenant_id" in result.columns:
        scope = result["automation_tenant_id"].astype(str).str.strip()
        result = result[scope.eq(str(tenant_id).strip())]
    if profile_key and "automation_profile_key" in result.columns:
        profile = result["automation_profile_key"].astype(str).str.strip().str.lower()
        result = result[profile.eq(profile_key)]
    return result.copy()


def _session_rows(frame: pd.DataFrame, *, session_day: str) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    for column in ("created_at", "opened_at", "submitted_at", "closed_at", "updated_at"):
        if column not in frame.columns:
            continue
        timestamps = pd.to_datetime(frame[column], errors="coerce", utc=True)
        if not timestamps.notna().any():
            continue
        start, end = _session_bounds_utc(session_day)
        return frame[timestamps.ge(start) & timestamps.le(end)].copy()
    return frame.copy()


def _history_for_session(runtime: dict[str, Any], *, session_day: str) -> list[dict[str, Any]]:
    rows = [item for item in list(runtime.get("accuracy_candidate_history") or []) if isinstance(item, dict)]
    if not rows:
        return []
    exact = [item for item in rows if str(item.get("at") or "")[:10] == session_day]
    return exact or rows[:50]


def _has_positive_number(item: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = item.get(key)
        if value is None or str(value).strip() == "":
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if pd.notna(parsed) and parsed > 0:
            return True
    return False


def _coverage(rows: list[dict[str, Any]], predicate) -> tuple[int, float]:
    total = len(rows)
    if total <= 0:
        return 0, 0.0
    covered = sum(1 for item in rows if predicate(item))
    return covered, round(covered / total * 100.0, 2)


def _note_coverage(runtime: dict[str, Any], *, session_day: str) -> dict[str, Any]:
    related = {
        "paper_evidence_note_id": runtime.get("paper_evidence_last_note_id"),
        "daily_objective_note_id": runtime.get("daily_objective_last_note_id")
        if str(runtime.get("daily_objective_note_session_day") or "") == session_day
        else None,
        "accuracy_calibration_note_id": runtime.get("accuracy_calibration_last_note_id")
        if str(runtime.get("accuracy_calibration_note_session_day") or "") == session_day
        else None,
        "paper_broker_note_id": runtime.get("paper_broker_reconciliation_last_note_id")
        if str(runtime.get("paper_broker_reconciliation_note_session_day") or "") == session_day
        else None,
    }
    present = {key: value for key, value in related.items() if value}
    return {
        "present": bool(present),
        "covered_note_count": len(present),
        "related_notes": present,
    }


def build_paper_evidence_report(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_open: pd.DataFrame | None = None,
    owned_pending: pd.DataFrame | None = None,
    owned_closed: pd.DataFrame | None = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    settings = normalize_paper_evidence_settings(state.get("settings"))
    runtime = dict(state.get("runtime") or {})
    session_day = _session_day_for(now)
    candidate_history = _history_for_session(runtime, session_day=session_day)
    candidate_count = len(candidate_history)
    selected = [item for item in candidate_history if _coerce_bool(item.get("selected"), False)]
    rejected = [item for item in candidate_history if not _coerce_bool(item.get("selected"), False)]
    edge_count, edge_coverage = _coverage(
        candidate_history,
        lambda item: _has_positive_number(item, "expected_edge_bps")
        and _has_positive_number(item, "edge_to_cost_ratio"),
    )
    spread_count, spread_coverage = _coverage(candidate_history, lambda item: _has_positive_number(item, "spread_bps"))
    liquidity_count, liquidity_coverage = _coverage(candidate_history, lambda item: _has_positive_number(item, "liquidity"))
    rank_count, rank_coverage = _coverage(candidate_history, lambda item: _has_positive_number(item, "rank"))
    objective_count, objective_coverage = _coverage(
        candidate_history,
        lambda item: _has_positive_number(item, "daily_objective_expected_pnl", "expected_pnl"),
    )
    session_bucket_count, session_bucket_coverage = _coverage(
        candidate_history,
        lambda item: bool(str(item.get("session_bucket") or item.get("pattern_key") or "").strip()),
    )

    tenant_id = str(getattr(tenant, "id", "") or "").strip()
    open_frame = _session_rows(_owned_rows(owned_open, tenant_id=tenant_id, profile_key=normalized_profile_key), session_day=session_day)
    pending_frame = _session_rows(
        _owned_rows(owned_pending, tenant_id=tenant_id, profile_key=normalized_profile_key),
        session_day=session_day,
    )
    closed_frame = _session_rows(
        _owned_rows(owned_closed, tenant_id=tenant_id, profile_key=normalized_profile_key),
        session_day=session_day,
    )
    paper_fill_count = int(len(open_frame) + len(closed_frame))
    pending_order_count = int(len(pending_frame))
    closed_trade_count = int(len(closed_frame))
    notes = _note_coverage(runtime, session_day=session_day)
    daily_objective = dict(runtime.get("daily_objective_last_report") or {})
    accuracy = dict(runtime.get("accuracy_calibration_last_report") or {})
    paper_broker = dict(runtime.get("paper_broker_reconciliation_last_report") or {})

    warnings: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    if not settings["paper_evidence_collection_enabled"]:
        status = "disabled"
        label = "Paper evidence disabled"
    elif normalized_profile_key != PAPER_EVIDENCE_PERSONAL_PAPER_PROFILE:
        status = "not_applicable"
        label = "Paper scope only"
    elif candidate_count <= 0:
        status = "collecting"
        label = "Waiting for candidate telemetry"
        warnings.append(
            {
                "key": "candidate_history_missing",
                "detail": "No ranked paper candidates have been captured for this session yet.",
            }
        )
    else:
        if settings["paper_evidence_require_edge_telemetry"] and edge_coverage < 100.0:
            blockers.append(
                {
                    "key": "edge_telemetry_incomplete",
                    "detail": f"{edge_count}/{candidate_count} candidates have edge and edge-to-cost telemetry.",
                }
            )
        if settings["paper_evidence_require_spread_telemetry"] and spread_coverage < 100.0:
            blockers.append(
                {
                    "key": "spread_telemetry_incomplete",
                    "detail": f"{spread_count}/{candidate_count} candidates have spread telemetry.",
                }
            )
        if liquidity_coverage < 100.0:
            warnings.append(
                {
                    "key": "liquidity_telemetry_incomplete",
                    "detail": f"{liquidity_count}/{candidate_count} candidates have liquidity telemetry.",
                }
            )
        if not daily_objective:
            warnings.append(
                {
                    "key": "daily_objective_not_reviewed",
                    "detail": "Daily objective has not reviewed this paper evidence session yet.",
                }
            )
        if not accuracy:
            warnings.append(
                {
                    "key": "accuracy_calibration_not_reviewed",
                    "detail": "Decision-PnL accuracy has not reviewed this paper evidence session yet.",
                }
            )
        if str(paper_broker.get("status") or "").lower() in {"blocked", "mismatch", "error"}:
            blockers.append(
                {
                    "key": "paper_broker_reconciliation_blocked",
                    "detail": "Paper broker reconciliation is not clean for the latest evidence window.",
                }
            )
        if blockers:
            status = "blocked"
            label = "Paper evidence blocked"
        elif warnings:
            status = "warning"
            label = "Paper evidence collecting"
        else:
            status = "ready"
            label = "Paper evidence ready"

    return serialize_value(
        {
            "status": status,
            "label": label,
            "profile_key": normalized_profile_key,
            "session_day": session_day,
            "evaluated_at": _serialize_datetime(now),
            "run_source": str(run_source or "manual").strip().lower() or "manual",
            "candidate_count": candidate_count,
            "selected_candidate_count": len(selected),
            "rejected_candidate_count": len(rejected),
            "edge_coverage_count": edge_count,
            "edge_coverage_pct": edge_coverage,
            "spread_coverage_count": spread_count,
            "spread_coverage_pct": spread_coverage,
            "liquidity_coverage_count": liquidity_count,
            "liquidity_coverage_pct": liquidity_coverage,
            "rank_coverage_count": rank_count,
            "rank_coverage_pct": rank_coverage,
            "objective_coverage_count": objective_count,
            "objective_coverage_pct": objective_coverage,
            "session_bucket_coverage_count": session_bucket_count,
            "session_bucket_coverage_pct": session_bucket_coverage,
            "paper_fill_count": paper_fill_count,
            "pending_order_count": pending_order_count,
            "closed_trade_count": closed_trade_count,
            "daily_objective_status": daily_objective.get("status"),
            "accuracy_calibration_status": accuracy.get("status"),
            "paper_broker_reconciliation_status": paper_broker.get("status"),
            "note_coverage": notes["present"],
            "covered_note_count": notes["covered_note_count"],
            "related_notes": notes["related_notes"],
            "blockers": blockers,
            "warnings": warnings,
            "related_note_id": runtime.get("paper_evidence_last_note_id"),
            "enabled": settings["paper_evidence_collection_enabled"],
            "auto_review_enabled": settings["paper_evidence_auto_review_enabled"],
        }
    )


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=PAPER_EVIDENCE_NOTE_OWNER,
            limit=PAPER_EVIDENCE_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "paper-evidence",
        "monday-evidence",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags") or []}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _format_rows(items: list[dict[str, Any]], *, empty: str) -> list[str]:
    if not items:
        return [f"- {empty}"]
    rows = []
    for item in items[:10]:
        key = str(item.get("key") or item.get("field") or "item").replace("_", " ")
        detail = str(item.get("detail") or item.get("reason") or "").strip()
        rows.append(f"- {key}: {detail}" if detail else f"- {key}")
    return rows


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    lines = [
        f"Monday paper evidence review for {getattr(tenant, 'name', None) or getattr(tenant, 'slug', '') or 'tenant'}",
        "",
        f"- Profile: {profile_key}",
        f"- Session: {report.get('session_day')}",
        f"- Status: {report.get('status')}",
        f"- Candidates: {report.get('candidate_count')} total, {report.get('selected_candidate_count')} selected, {report.get('rejected_candidate_count')} rejected",
        f"- Edge coverage: {float(report.get('edge_coverage_pct') or 0.0):.1f}%",
        f"- Spread coverage: {float(report.get('spread_coverage_pct') or 0.0):.1f}%",
        f"- Liquidity coverage: {float(report.get('liquidity_coverage_pct') or 0.0):.1f}%",
        f"- Objective coverage: {float(report.get('objective_coverage_pct') or 0.0):.1f}%",
        f"- Paper fills: {report.get('paper_fill_count')} | pending: {report.get('pending_order_count')} | closed: {report.get('closed_trade_count')}",
        f"- Daily objective: {report.get('daily_objective_status') or 'not_run'}",
        f"- Accuracy calibration: {report.get('accuracy_calibration_status') or 'not_run'}",
        f"- Paper broker reconciliation: {report.get('paper_broker_reconciliation_status') or 'not_run'}",
        f"- Note coverage: {'yes' if report.get('note_coverage') else 'no'}",
        "",
        "Blockers",
    ]
    lines.extend(_format_rows(list(report.get("blockers") or []), empty="No blockers."))
    lines.extend(["", "Warnings"])
    lines.extend(_format_rows(list(report.get("warnings") or []), empty="No warnings."))
    lines.extend(
        [
            "",
            "Safety scope",
            "- Paper-only evidence collection. Live settings, live gates, live allowances, and baseline live routing were not changed.",
        ]
    )
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = [
        "automation-ai",
        "paper-evidence",
        "monday-evidence",
        "accuracy-calibration",
        "daily-objective",
        _profile_tag(profile_key),
        f"session-{session_day}",
    ]
    title = f"Monday paper evidence - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = (
        str(report.get("related_note_id") or report.get("note_id") or "").strip()
        or _find_existing_note_id(profile_key, session_day)
    )
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": PAPER_EVIDENCE_NOTE_OWNER,
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


def _persist_report(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    report: dict[str, Any],
    actor: Any = None,
    write_note: bool = False,
) -> dict[str, Any]:
    normalized_profile_key = _normalize_profile_key(profile_key)
    if write_note:
        note_id = _sync_note(tenant=tenant, profile_key=normalized_profile_key, report=report)
        if note_id:
            report["note_id"] = note_id
            report["related_note_id"] = note_id
            note_map = dict(report.get("related_notes") or {})
            note_map["paper_evidence_note_id"] = note_id
            report["related_notes"] = note_map
            report["note_coverage"] = True
            report["covered_note_count"] = len({key: value for key, value in note_map.items() if value})
    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "session_day",
        "evaluated_at",
        "run_source",
        "candidate_count",
        "selected_candidate_count",
        "rejected_candidate_count",
        "edge_coverage_count",
        "edge_coverage_pct",
        "spread_coverage_count",
        "spread_coverage_pct",
        "liquidity_coverage_count",
        "liquidity_coverage_pct",
        "rank_coverage_count",
        "rank_coverage_pct",
        "objective_coverage_count",
        "objective_coverage_pct",
        "session_bucket_coverage_count",
        "session_bucket_coverage_pct",
        "paper_fill_count",
        "pending_order_count",
        "closed_trade_count",
        "daily_objective_status",
        "accuracy_calibration_status",
        "paper_broker_reconciliation_status",
        "note_coverage",
        "covered_note_count",
        "related_notes",
        "blockers",
        "warnings",
        "note_id",
        "related_note_id",
        "enabled",
        "auto_review_enabled",
    }
    runtime["paper_evidence_last_report"] = serialize_value(
        {key: report.get(key) for key in summary_keys if key in report}
    )
    runtime["paper_evidence_last_run_at"] = report.get("evaluated_at")
    runtime["paper_evidence_last_note_id"] = report.get("related_note_id") or report.get("note_id")
    runtime["paper_evidence_note_session_day"] = report.get("session_day")
    runtime["paper_evidence_last_error"] = None
    history = list(runtime.get("paper_evidence_history") or [])
    history.insert(
        0,
        {
            "at": report.get("evaluated_at"),
            "session_day": report.get("session_day"),
            "status": report.get("status"),
            "candidate_count": report.get("candidate_count"),
            "edge_coverage_pct": report.get("edge_coverage_pct"),
            "spread_coverage_pct": report.get("spread_coverage_pct"),
            "paper_fill_count": report.get("paper_fill_count"),
            "note_id": report.get("related_note_id") or report.get("note_id"),
            "run_source": report.get("run_source"),
        },
    )
    runtime["paper_evidence_history"] = serialize_value(history[:PAPER_EVIDENCE_HISTORY_LIMIT])
    if db is not None and write_note:
        record_audit_event(
            db,
            event_type="trade_automation.paper_evidence_reviewed",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": normalized_profile_key,
                "session_day": report.get("session_day"),
                "status": report.get("status"),
                "candidate_count": report.get("candidate_count"),
                "edge_coverage_pct": report.get("edge_coverage_pct"),
                "spread_coverage_pct": report.get("spread_coverage_pct"),
                "note_id": report.get("related_note_id") or report.get("note_id"),
                "run_source": report.get("run_source"),
            },
        )
    return serialize_value(report)


def evaluate_paper_evidence_cycle(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    owned_open: pd.DataFrame | None = None,
    owned_pending: pd.DataFrame | None = None,
    owned_closed: pd.DataFrame | None = None,
    now: datetime | None = None,
    actor: Any = None,
) -> dict[str, Any]:
    report = build_paper_evidence_report(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        owned_open=owned_open,
        owned_pending=owned_pending,
        owned_closed=owned_closed,
        now=now,
        run_source="cycle",
    )
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        report=report,
        actor=actor,
        write_note=False,
    )


def run_paper_evidence_review(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
) -> dict[str, Any]:
    now = now or _utc_now()
    normalized_profile_key = _normalize_profile_key(profile_key)
    if normalized_profile_key == PAPER_EVIDENCE_PERSONAL_PAPER_PROFILE:
        try:
            automation_daily_objective_service.run_daily_objective_review(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=linked_account,
                actor=actor,
                now=now,
                run_source=run_source,
            )
        except Exception as exc:
            state.setdefault("runtime", {})["daily_objective_last_error"] = str(exc)
        try:
            automation_accuracy_calibration_service.run_accuracy_calibration_review(
                db,
                tenant=tenant,
                state=state,
                profile_key=normalized_profile_key,
                linked_account=linked_account,
                actor=actor,
                now=now,
                run_source=run_source,
            )
        except Exception as exc:
            state.setdefault("runtime", {})["accuracy_calibration_last_error"] = str(exc)

    tenant_id = str(getattr(tenant, "id", "") or "").strip()
    open_frame = _owned_rows(sdm.read_open_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    pending_frame = _owned_rows(sdm.read_pending_orders(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    closed_frame = _owned_rows(sdm.read_closed_trades(), tenant_id=tenant_id, profile_key=normalized_profile_key)
    report = build_paper_evidence_report(
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        owned_open=open_frame,
        owned_pending=pending_frame,
        owned_closed=closed_frame,
        now=now,
        run_source=run_source,
    )
    return _persist_report(
        db,
        tenant=tenant,
        state=state,
        profile_key=normalized_profile_key,
        report=report,
        actor=actor,
        write_note=True,
    )
