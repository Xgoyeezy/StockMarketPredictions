from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "audit_only": True,
    "paper_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "can_grant_ai_order_authority": False,
    "mutation": "none",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_risk_limits": False,
    "writes_ranking_config": False,
}

SAFETY_NOTES: tuple[str, ...] = (
    "Read-only risk and audit proof review.",
    "Does not place orders.",
    "Does not change broker routes.",
    "Does not bypass or loosen risk gates.",
    "Does not clear kill switches.",
    "Does not change ranking weights automatically.",
    "Does not grant live-trading readiness.",
)

BLOCKED_CLAIMS: tuple[str, ...] = (
    "risk_gate_authority_claim",
    "audit_completeness_claim",
    "kill_switch_recovery_claim",
    "paper_to_live_readiness",
    "broker_route_safety_claim",
    "compliance_approval_claim",
    "live_trading_readiness",
)

SECRET_KEY_MARKERS = (
    "secret",
    "token",
    "password",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "account_id",
)

RISK_AUDIT_HARDENING_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "active_risk_policy",
        "title": "Active risk policy evidence",
        "priority": "critical",
        "metric": "active_policy_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "missing_fields": ("active_policy", "scope", "risk_limits"),
        "blocked_claims": ("risk_gate_authority_claim", "promotion_review", "paper_to_live_readiness"),
        "safe_next_action": "Keep at least one active tenant or strategy risk policy visible before treating risk gates as reviewable evidence.",
        "done_when": "An active policy with explicit scope and limits is visible in the report.",
    },
    {
        "key": "risk_event_lineage",
        "title": "Risk event lineage",
        "priority": "critical",
        "metric": "risk_event_lineage_coverage",
        "threshold": 1.0,
        "comparison": "greater_or_equal",
        "missing_fields": ("event_type", "severity", "action_taken", "created_at", "payload"),
        "blocked_claims": ("risk_breach_auditability", "blocked_order_review", "promotion_review"),
        "safe_next_action": "Record risk check failures and blocked actions with event type, severity, action, payload, and timestamp.",
        "done_when": "Risk events have complete lineage fields for recent blocked checks or breaches.",
    },
    {
        "key": "kill_switch_auditability",
        "title": "Kill-switch auditability",
        "priority": "critical",
        "metric": "kill_switch_audit_event_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "missing_fields": ("kill_switch_audit_event", "actor_email", "reason", "affected_count"),
        "blocked_claims": ("kill_switch_recovery_claim", "operator_control_claim", "paper_to_live_readiness"),
        "safe_next_action": "Ensure each kill-switch activation and clear operation writes an audit event with actor, reason, affected scope, and timestamp.",
        "done_when": "Kill-switch changes are visible as audit events with actor and scope context.",
    },
    {
        "key": "audit_event_lineage",
        "title": "Audit event lineage",
        "priority": "high",
        "metric": "audit_event_lineage_coverage",
        "threshold": 1.0,
        "comparison": "greater_or_equal",
        "missing_fields": ("event_type", "actor_email", "created_at", "payload"),
        "blocked_claims": ("audit_completeness_claim", "support_review", "operator_accountability"),
        "safe_next_action": "Keep audit events actor-stamped, timestamped, typed, and payload-backed before treating the audit trail as complete.",
        "done_when": "Recent audit events include actor, event type, payload, and timestamp fields.",
    },
    {
        "key": "decision_replay_traceability",
        "title": "Decision replay traceability",
        "priority": "high",
        "metric": "decision_replay_traceability_coverage",
        "threshold": 1.0,
        "comparison": "greater_or_equal",
        "missing_fields": ("risk_snapshot", "readiness_snapshot", "market_snapshot", "broker_snapshot", "replay_events"),
        "blocked_claims": ("decision_replay_claim", "promotion_traceability", "paper_to_live_readiness"),
        "safe_next_action": "Link trade decisions to risk, readiness, market, broker, and ordered replay snapshots before using replay as proof.",
        "done_when": "Recent replayable decisions include required snapshots and ordered replay events.",
    },
    {
        "key": "sanitized_export_boundary",
        "title": "Sanitized export boundary",
        "priority": "high",
        "metric": "sanitized_export_coverage",
        "threshold": 1.0,
        "comparison": "greater_or_equal",
        "missing_fields": ("audit_export_event", "export_type", "queued_status", "no_raw_file_path", "no_secret_payload"),
        "blocked_claims": ("support_export_safety", "external_review_packet", "compliance_approval_claim"),
        "safe_next_action": "Queue audit exports through the control plane and keep exported metadata free of secrets, raw paths, broker account identifiers, and credentials.",
        "done_when": "Audit export evidence is queued, typed, and sanitized.",
    },
    {
        "key": "safety_ledger_visibility",
        "title": "Safety ledger visibility",
        "priority": "high",
        "metric": "safety_ledger_record_count",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "missing_fields": ("safety_ledger_record", "status", "blocker", "next_action"),
        "blocked_claims": ("operational_safety_claim", "automation_recovery_claim", "paper_to_live_readiness"),
        "safe_next_action": "Keep trading safety state and ledger summaries visible with status, blocker, and next action context.",
        "done_when": "Safety ledger summary includes recent status evidence.",
    },
    {
        "key": "read_only_governance_boundary",
        "title": "Read-only governance boundary",
        "priority": "critical",
        "metric": "read_only_boundary",
        "threshold": 1,
        "comparison": "greater_or_equal",
        "missing_fields": (),
        "blocked_claims": ("risk_gate_bypass", "broker_route_change", "order_submission", "ranking_mutation"),
        "safe_next_action": "Keep this hardening report read-only; do not let proof reports mutate execution, broker, risk, or ranking configuration.",
        "done_when": "The report carries explicit false authority flags for orders, broker routes, risk settings, and ranking weights.",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _records(items: Iterable[Any] | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict):
            rows.append(dict(item))
    return rows


def _looks_like_local_path(value: str) -> bool:
    cleaned = value.strip()
    return (len(cleaned) >= 3 and cleaned[1:3] in {":\\", ":/"}) or cleaned.startswith("\\\\")


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    key_lower = key.lower()
    if any(marker in key_lower for marker in SECRET_KEY_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(child_key): _sanitize_value(child_value, key=str(child_key)) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, tuple) or isinstance(value, set):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[local_path_redacted]"
    return value


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _passes_threshold(value: Any, threshold: float, comparison: str) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    if comparison == "greater_than":
        return numeric > threshold
    if comparison == "less_or_equal":
        return numeric <= threshold
    return numeric >= threshold


def _has_any_limit(policy: dict[str, Any]) -> bool:
    for key in (
        "max_daily_loss",
        "max_weekly_loss",
        "max_drawdown_pct",
        "max_position_notional",
        "max_order_notional",
        "max_open_positions",
        "requires_approval_above",
    ):
        if _present(policy.get(key)):
            return True
    return False


def _lineage_complete(row: dict[str, Any], fields: tuple[str, ...]) -> bool:
    return all(_present(row.get(field)) for field in fields)


def _event_type_contains(row: dict[str, Any], needle: str) -> bool:
    return needle in str(row.get("event_type") or "").lower()


def _payload_text(row: dict[str, Any]) -> str:
    return str(row.get("payload") or row.get("payload_json") or {}).lower()


def _decision_traceable(row: dict[str, Any]) -> bool:
    return (
        _present(row.get("risk_snapshot"))
        and _present(row.get("readiness_snapshot"))
        and _present(row.get("market_snapshot"))
        and _present(row.get("broker_snapshot"))
        and int(row.get("replay_event_count") or len(row.get("replay") or [])) > 0
    )


def _export_sanitized(row: dict[str, Any]) -> bool:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    export_type = row.get("export_type") or payload.get("export_type")
    status = row.get("status") or payload.get("status") or ("queued" if _event_type_contains(row, "audit.export") else None)
    raw_text = f"{row} {payload}".lower()
    forbidden_markers = ("api_key", "secret", "token", "password", "credential", "\\users\\", "/users/", "account_id")
    has_raw_file_path = bool(row.get("file_path") or payload.get("file_path"))
    return bool(export_type) and status == "queued" and not has_raw_file_path and not any(marker in raw_text for marker in forbidden_markers)


def _safety_record_count(safety_summary: dict[str, Any] | None) -> int:
    if not isinstance(safety_summary, dict):
        return 0
    for key in ("record_count", "count", "event_count", "ledger_event_count"):
        try:
            value = int(safety_summary.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            return value
    records = safety_summary.get("items") or safety_summary.get("records") or safety_summary.get("events")
    return len(records) if isinstance(records, list) else 0


def build_risk_audit_hardening_plan(
    *,
    risk_policies: Iterable[Any] | None = None,
    risk_events: Iterable[Any] | None = None,
    audit_events: Iterable[Any] | None = None,
    audit_exports: Iterable[Any] | None = None,
    trade_replays: Iterable[Any] | None = None,
    safety_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policies = _records(risk_policies)
    events = _records(risk_events)
    audits = _records(audit_events)
    exports = _records(audit_exports)
    replays = _records(trade_replays)

    active_policies = [row for row in policies if str(row.get("status") or "").lower() == "active"]
    active_policy_with_limits = [row for row in active_policies if _present(row.get("scope")) and _has_any_limit(row)]
    risk_lineage_rows = [
        row
        for row in events
        if _lineage_complete(row, ("event_type", "created_at"))
        and (_present(row.get("severity")) or _present(row.get("action_taken")))
        and _present(row.get("payload"))
    ]
    kill_switch_audit_events = [
        row
        for row in audits + events
        if _event_type_contains(row, "kill")
        and (_present(row.get("actor_email")) or _present(row.get("action_taken")))
        and ("reason" in _payload_text(row) or "kill" in _payload_text(row))
    ]
    audit_lineage_rows = [row for row in audits if _lineage_complete(row, ("event_type", "actor_email", "created_at", "payload"))]
    traceable_replays = [row for row in replays if _decision_traceable(row)]
    export_events = [row for row in audits if _event_type_contains(row, "audit.export")]
    sanitized_exports = [row for row in exports + export_events if _export_sanitized(row)]
    safety_records = _safety_record_count(safety_summary)
    read_only_boundary = int(
        SAFETY_FLAGS["can_submit_orders"] is False
        and SAFETY_FLAGS["can_submit_live_orders"] is False
        and SAFETY_FLAGS["can_change_broker_routes"] is False
        and SAFETY_FLAGS["can_bypass_risk_gates"] is False
        and SAFETY_FLAGS["can_clear_kill_switch"] is False
        and SAFETY_FLAGS["writes_risk_config"] is False
        and SAFETY_FLAGS["writes_risk_limits"] is False
        and SAFETY_FLAGS["writes_ranking_config"] is False
    )

    metrics = {
        "active_policy_count": len(active_policy_with_limits),
        "risk_event_lineage_coverage": _ratio(len(risk_lineage_rows), len(events)),
        "kill_switch_audit_event_count": len(kill_switch_audit_events),
        "audit_event_lineage_coverage": _ratio(len(audit_lineage_rows), len(audits)),
        "decision_replay_traceability_coverage": _ratio(len(traceable_replays), len(replays)),
        "sanitized_export_coverage": _ratio(len(sanitized_exports), len(exports + export_events)),
        "safety_ledger_record_count": safety_records,
        "read_only_boundary": read_only_boundary,
    }

    items: list[dict[str, Any]] = []
    for definition in RISK_AUDIT_HARDENING_DEFINITIONS:
        key = str(definition["key"])
        value = metrics.get(str(definition["metric"]))
        passed = _passes_threshold(value, float(definition["threshold"]), str(definition["comparison"]))
        no_records = (
            key != "read_only_governance_boundary"
            and not policies
            and not events
            and not audits
            and not exports
            and not replays
            and not safety_records
        )
        status = "ready" if passed else "no_records" if no_records else "needs_evidence"
        items.append(
            {
                "key": key,
                "title": definition["title"],
                "priority": definition["priority"],
                "status": status,
                "passed": passed,
                "metric": definition["metric"],
                "value": value,
                "threshold": definition["threshold"],
                "comparison": definition["comparison"],
                "missing_fields": [] if passed else list(definition.get("missing_fields") or ()),
                "blocked_claims": list(definition.get("blocked_claims") or ()),
                "safe_next_action": definition["safe_next_action"],
                "done_when": definition["done_when"],
                "claim_boundary": "Risk and audit hardening is internal proof review only; it is not risk approval, live-trading readiness, compliance approval, or permission to change controls.",
                "manual_review_only": True,
                "research_only": True,
                "audit_only": True,
                "changes_execution": False,
                "changes_order_submission": False,
                "changes_broker_routes": False,
                "changes_risk_gates": False,
                "changes_ranking_weights": False,
                "clears_kill_switch": False,
            }
        )

    open_items = [row for row in items if row["status"] != "ready"]
    critical_open_items = [row for row in open_items if row.get("priority") == "critical"]
    ready_for_review = bool(items) and not open_items
    return serialize_value(
        {
            "status": "ready_for_human_review" if ready_for_review else "blocked_by_evidence",
            "summary": {
                "item_count": len(items),
                "open_item_count": len(open_items),
                "critical_open_items": len(critical_open_items),
                "ready_item_count": len(items) - len(open_items),
                "top_hardening_item": open_items[0]["title"] if open_items else None,
                "proof_first_rule": "Ambition is allowed. Proof decides priority.",
                "claim_permissions": {
                    "cautious_internal_risk_audit_review": ready_for_review,
                    "risk_gate_authority_claim": False,
                    "audit_completeness_claim": False,
                    "kill_switch_clearance": False,
                    "broker_route_change": False,
                    "automatic_execution_mutation": False,
                    "compliance_approval_claim": False,
                    "live_trading_readiness": False,
                },
                "blocked_claims": list(BLOCKED_CLAIMS),
                "safe_boundary": "Risk and audit hardening only records proof gaps and authority boundaries. It does not authorize orders, route changes, risk-gate changes, kill-switch clears, or ranking-weight mutation.",
            },
            "metrics": metrics,
            "items": items,
            "safe_next_actions": [row["safe_next_action"] for row in open_items],
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def build_risk_audit_hardening_report(
    *,
    risk_policies: Iterable[Any] | None = None,
    risk_events: Iterable[Any] | None = None,
    audit_events: Iterable[Any] | None = None,
    audit_exports: Iterable[Any] | None = None,
    trade_replays: Iterable[Any] | None = None,
    safety_summary: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    policies = _records(risk_policies)
    events = _records(risk_events)
    audits = _records(audit_events)
    exports = _records(audit_exports)
    replays = _records(trade_replays)
    hardening_plan = build_risk_audit_hardening_plan(
        risk_policies=policies,
        risk_events=events,
        audit_events=audits,
        audit_exports=exports,
        trade_replays=replays,
        safety_summary=safety_summary,
    )
    event_type_counts = Counter(str(row.get("event_type") or "unknown") for row in audits + events)
    summary = {
        "status": hardening_plan["status"],
        "risk_policy_count": len(policies),
        "active_policy_count": sum(1 for row in policies if str(row.get("status") or "").lower() == "active"),
        "risk_event_count": len(events),
        "audit_event_count": len(audits),
        "audit_export_count": len(exports),
        "decision_replay_count": len(replays),
        "safety_ledger_record_count": _safety_record_count(safety_summary),
        "event_type_counts": [{"event_type": key, "count": value} for key, value in sorted(event_type_counts.items())],
        "risk_audit_hardening_status": hardening_plan["status"],
        "risk_audit_hardening_open_items": hardening_plan["summary"]["open_item_count"],
        "risk_audit_hardening_critical_open_items": hardening_plan["summary"]["critical_open_items"],
        "top_hardening_item": hardening_plan["summary"]["top_hardening_item"],
        "claim_permissions": hardening_plan["summary"]["claim_permissions"],
        **SAFETY_FLAGS,
    }
    warnings: list[str] = []
    if hardening_plan["summary"]["open_item_count"]:
        warnings.append("Risk and audit hardening still blocks risk authority, audit completeness, paper-to-live, compliance, and live-readiness claims.")
    if not events:
        warnings.append("No recent risk events were available for lineage review.")
    if not audits:
        warnings.append("No recent audit events were available for lineage review.")

    return serialize_value(
        {
            "status": hardening_plan["status"],
            "generated_at": generated_at or _utc_now(),
            "summary": summary,
            "risk_policies": _sanitize_value(policies[:100]),
            "risk_events": _sanitize_value(events[:100]),
            "audit_events": _sanitize_value(audits[:100]),
            "audit_exports": _sanitize_value(exports[:100]),
            "trade_replays": _sanitize_value(replays[:100]),
            "safety_summary": _sanitize_value(safety_summary or {}),
            "risk_audit_hardening_plan": hardening_plan,
            "warnings": list(dict.fromkeys(warnings)),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
            "finish_tracker": build_project_finish_tracker(report_name="risk_audit_hardening"),
        }
    )
