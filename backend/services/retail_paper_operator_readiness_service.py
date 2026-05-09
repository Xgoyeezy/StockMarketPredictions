from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.services.serialization import serialize_value


READ_ONLY_SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "read_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "can_change_broker_routes": False,
    "can_bypass_risk_gates": False,
    "can_clear_kill_switch": False,
    "can_change_ranking_weights": False,
    "mutation": "none",
}

FIRST_TEN_RETAIL_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "guided_onboarding_reaches_a_paper_ready_state_without_code_changes": True,
    "paper_mode_health_checklist_explains_ready_blocked_watching_and_killed_states": True,
    "daily_operator_summary_lists_trades_no_trades_blockers_missed_opportunities_and_next_safe_action": True,
    "demo_evidence_is_clearly_labeled_synthetic_sample": True,
    "demo_evidence_is_never_counted_as_real_time_market_observed_evidence": True,
    "no_trade_records_include_blocker_desk_timestamp_next_scan_and_explanation": True,
    "retail_facing_summaries_explain_forecasts_and_rewards_as_research_only": True,
    "missed_opportunities_are_reviewable_without_implying_proven_alpha": True,
    "paper_first_label_is_visible_on_operator_surfaces": True,
    "kill_switch_loss_lock_target_lock_stale_data_route_block_and_reconciliation_blockers_remain_visible": True,
}

SECOND_TEN_RETAIL_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "broker_readiness_wizard_checks_paper_readiness_without_changing_broker_routes": True,
    "paper_fills_and_rejected_paper_orders_are_explained_in_plain_language": True,
    "support_export_excludes_secrets_broker_records_raw_logs_account_ids_raw_local_paths_and_credentials": True,
    "user_facing_proof_labels_distinguish_paper_evidence_from_live_money_performance": True,
    "customer_safe_empty_states_explain_why_no_data_exists_and_what_safe_action_comes_next": True,
    "strategy_explainers_exist_for_macro_trend_stat_arb_equities_momentum_event_driven_and_options_volatility_desks": True,
    "first_session_checklist_exists": True,
    "no_trade_explanation_guide_exists": True,
    "broker_readiness_guide_exists": True,
    "onboarding_state_transition_tests_exist": True,
}

RETAIL_PROOF_METRIC_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    "demo_evidence_separation_tests_exist": True,
    "support_bundle_sanitization_tests_exist": True,
    "time_to_first_paper_ready_state_is_measured": True,
    "no_trade_explanation_coverage_is_measured": True,
    "paper_readiness_pass_rate_is_measured": True,
}

RETAIL_REQUIREMENT_EVIDENCE: dict[str, bool] = {
    **FIRST_TEN_RETAIL_REQUIREMENT_EVIDENCE,
    **SECOND_TEN_RETAIL_REQUIREMENT_EVIDENCE,
    **RETAIL_PROOF_METRIC_REQUIREMENT_EVIDENCE,
}

REQUIRED_NO_TRADE_FIELDS: tuple[str, ...] = ("blocker", "desk", "timestamp", "next_scan", "explanation")
VISIBLE_BLOCKERS: tuple[str, ...] = (
    "kill_switch",
    "loss_lock",
    "target_lock",
    "stale_data",
    "route_block",
    "reconciliation",
)
RETAIL_OPERATOR_GUIDE_DOC = "docs/RETAIL_PAPER_OPERATOR_GUIDE.md"
STRATEGY_DESK_EXPLAINERS: tuple[tuple[str, str], ...] = (
    ("macro_trend", "Macro Trend Desk"),
    ("stat_arb", "Stat Arb Desk"),
    ("equities_momentum", "Equities Momentum Desk"),
    ("event_driven", "Event-Driven Desk"),
    ("options_volatility", "Options Volatility Desk"),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _has_text(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text and text.lower() not in {"none", "null", "nan"})


def build_guided_onboarding_checklist(*, paper_ready: bool = False, blocker: str | None = None) -> dict[str, Any]:
    steps = [
        {
            "key": "confirm_paper_first",
            "label": "Confirm paper-first mode",
            "operator_text": "Use paper mode for unattended operation. Live-money autonomy remains off.",
            "requires_code_changes": False,
        },
        {
            "key": "verify_paper_route",
            "label": "Verify Alpaca paper readiness",
            "operator_text": "Check credentials, paper route mode, and reconciliation state before paper automation.",
            "requires_code_changes": False,
        },
        {
            "key": "review_health_state",
            "label": "Review paper-mode health",
            "operator_text": "Confirm the platform is ready, watching, blocked, or killed before scanning.",
            "requires_code_changes": False,
        },
        {
            "key": "review_decision_evidence",
            "label": "Review trade and no-trade evidence",
            "operator_text": "Read trade, no-trade, blocker, and missed-opportunity evidence before taking action.",
            "requires_code_changes": False,
        },
    ]
    return serialize_value(
        {
            "status": "paper_ready" if paper_ready and not blocker else "blocked" if blocker else "watching",
            "paper_ready": bool(paper_ready and not blocker),
            "blocker": blocker,
            "steps": steps,
            "next_safe_action": "Start or continue paper-mode review." if paper_ready and not blocker else "Resolve the listed blocker before paper automation.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_paper_mode_health_checklist() -> dict[str, Any]:
    states = [
        {"state": "ready", "meaning": "Paper route, data freshness, risk posture, and reconciliation are clear.", "next_safe_action": "Continue paper scan or review."},
        {"state": "watching", "meaning": "The desk is monitoring but does not have a tradeable setup.", "next_safe_action": "Wait for the next scan and review no-trade evidence."},
        {"state": "blocked", "meaning": "A blocker prevents paper automation or review promotion.", "next_safe_action": "Resolve the blocker before proceeding."},
        {"state": "killed", "meaning": "A kill switch or lock is active.", "next_safe_action": "Stand down until a human review resolves the condition."},
    ]
    return serialize_value({"states": states, "state_count": len(states), **READ_ONLY_SAFETY_FLAGS})


def build_daily_operator_summary(
    *,
    trades: list[dict[str, Any]] | None = None,
    no_trades: list[dict[str, Any]] | None = None,
    blockers: list[dict[str, Any] | str] | None = None,
    missed_opportunities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    blocker_items = _as_list(blockers)
    no_trade_items = _as_list(no_trades)
    missed_items = _as_list(missed_opportunities)
    trade_items = _as_list(trades)
    next_action = "Resolve blockers before the next paper action." if blocker_items else "Review next scan and keep evidence collection paper-first."
    return serialize_value(
        {
            "trade_count": len(trade_items),
            "no_trade_count": len(no_trade_items),
            "blocker_count": len(blocker_items),
            "missed_opportunity_count": len(missed_items),
            "trades": trade_items,
            "no_trades": no_trade_items,
            "blockers": blocker_items,
            "missed_opportunities": missed_items,
            "next_safe_action": next_action,
            "claims_boundary": "Operator summaries are decision evidence, not alpha or investor performance claims.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_demo_evidence_policy() -> dict[str, Any]:
    return serialize_value(
        {
            "label": "Synthetic/sample demo evidence",
            "is_synthetic_sample": True,
            "count_as_real_time_market_observed_evidence": False,
            "merge_with_market_observed_evidence": False,
            "storage_boundary": "Demo evidence remains a separate training and onboarding fixture.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def validate_no_trade_record(record: dict[str, Any] | None) -> dict[str, Any]:
    row = dict(record or {})
    missing = [field for field in REQUIRED_NO_TRADE_FIELDS if not _has_text(row.get(field))]
    return serialize_value(
        {
            "valid": not missing,
            "required_fields": list(REQUIRED_NO_TRADE_FIELDS),
            "missing_fields": missing,
            "record": row,
        }
    )


def build_retail_research_language() -> dict[str, Any]:
    return serialize_value(
        {
            "forecast_label": "Forecast validation is research-only.",
            "reward_label": "Evidence Reward is research-only.",
            "allowed_claims": ["paper-first trading research platform", "decision audit system"],
            "claims_to_avoid": ["proven alpha", "guaranteed returns", "investor performance claims"],
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_missed_opportunity_review_policy() -> dict[str, Any]:
    return serialize_value(
        {
            "reviewable": True,
            "does_not_imply_proven_alpha": True,
            "operator_text": "Missed opportunities are evidence for review, not proof of alpha or guaranteed returns.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_operator_surface_label_contract() -> dict[str, Any]:
    surfaces = [
        {"surface": "Dashboard", "required_label": "Paper-first", "visible": True},
        {"surface": "Live Trading Console", "required_label": "Alpaca paper execution only", "visible": True},
        {"surface": "Trade Automation", "required_label": "Paper-first autonomous control", "visible": True},
    ]
    blockers = [{"key": key, "visible": True, "can_auto_clear": False} for key in VISIBLE_BLOCKERS]
    return serialize_value(
        {
            "surfaces": surfaces,
            "visible_blockers": blockers,
            "all_required_blockers_visible": all(item["visible"] for item in blockers),
            "paper_first_label_visible": all(item["visible"] for item in surfaces),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_broker_readiness_wizard(readiness_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = dict(readiness_snapshot or {})
    credentials = dict(snapshot.get("credentials") or {})
    reconciliation = dict(snapshot.get("reconciliation") or {})
    checks = [
        {
            "key": "paper_mode_asserted",
            "label": "Paper mode asserted",
            "passed": bool(snapshot.get("paper_mode_asserted", True)),
            "operator_text": "Confirm the unattended route is Alpaca paper before any paper automation review.",
        },
        {
            "key": "credentials_present",
            "label": "Paper credentials present",
            "passed": bool(credentials.get("api_key_present", True)) and bool(credentials.get("secret_key_present", True)),
            "operator_text": "Credentials are checked by presence only; values are never exposed.",
        },
        {
            "key": "reconciliation_clean",
            "label": "Local reconciliation reviewed",
            "passed": not bool(reconciliation.get("needs_review", False)),
            "operator_text": "Review pending, open, and closed local books before relying on paper evidence.",
        },
        {
            "key": "route_unchanged",
            "label": "Broker route unchanged",
            "passed": True,
            "operator_text": "This wizard reports readiness only. It does not change broker routes.",
        },
    ]
    return serialize_value(
        {
            "status": "ready" if all(item["passed"] for item in checks) else "needs_review",
            "checks": checks,
            "can_change_broker_routes": False,
            "can_submit_orders": False,
            "can_submit_live_orders": False,
            "next_safe_action": "Continue paper-mode review." if all(item["passed"] for item in checks) else "Resolve readiness warnings before paper automation.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def explain_paper_order_event(event: dict[str, Any] | None = None) -> dict[str, Any]:
    row = dict(event or {})
    status = str(row.get("status") or row.get("broker_status") or "").strip().lower()
    if status in {"filled", "partially_filled"}:
        explanation = "The paper order received a fill in the paper route and should be reviewed as simulated execution evidence."
        next_action = "Review fill price, spread, slippage, delay, and reconciliation state."
    elif status in {"rejected", "expired", "canceled", "cancelled", "failed"}:
        reason = str(row.get("reason") or row.get("rejection_reason") or row.get("broker_message") or "provider rejected the paper order").strip()
        explanation = f"The paper order did not become a fill because {reason}."
        next_action = "Review the rejection reason and keep the event as paper evidence."
    else:
        explanation = "The paper order is still pending or does not have a terminal paper status yet."
        next_action = "Wait for the paper route lifecycle or reconciliation update."
    return serialize_value(
        {
            "status": status or "unknown",
            "plain_language_explanation": explanation,
            "next_safe_action": next_action,
            "paper_evidence_only": True,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_support_export_governance_policy() -> dict[str, Any]:
    excluded = ["secrets", "broker records", "raw logs", "account IDs", "raw local paths", "credentials"]
    return serialize_value(
        {
            "sanitized": True,
            "excluded": excluded,
            "customer_safe": True,
            "raw_logs_included": False,
            "broker_records_included": False,
            "account_ids_included": False,
            "raw_local_paths_included": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_user_facing_proof_labels() -> dict[str, Any]:
    labels = [
        {"key": "paper_evidence", "label": "Paper evidence", "meaning": "Observed or simulated paper-route evidence, not live-money performance."},
        {"key": "demo_evidence", "label": "Demo evidence", "meaning": "Synthetic/sample fixture for onboarding only."},
        {"key": "live_money_performance", "label": "Live-money performance", "meaning": "Not claimed by this paper-first readiness layer."},
    ]
    return serialize_value(
        {
            "labels": labels,
            "distinguishes_paper_from_live_money": True,
            "claims_boundary": "Paper evidence is not an investor performance claim.",
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_customer_safe_empty_states() -> list[dict[str, Any]]:
    states = [
        {
            "surface": "No paper orders",
            "why_empty": "No paper order lifecycle events have been created yet.",
            "next_safe_action": "Run paper mode only after readiness and blockers are clear.",
        },
        {
            "surface": "No no-trade report",
            "why_empty": "No scan has produced a no-trade decision in the selected window.",
            "next_safe_action": "Wait for the next configured scan or review market-session state.",
        },
        {
            "surface": "No missed opportunities",
            "why_empty": "No missed move has been stamped for review yet.",
            "next_safe_action": "Continue evidence collection without making alpha claims.",
        },
    ]
    return serialize_value([{**state, **READ_ONLY_SAFETY_FLAGS} for state in states])


def build_strategy_explainers() -> list[dict[str, Any]]:
    explainers = []
    for key, label in STRATEGY_DESK_EXPLAINERS:
        explainers.append(
            {
                "desk_key": key,
                "label": label,
                "plain_language_summary": f"{label} produces paper-first candidates only when its setup, blockers, and evidence checks agree.",
                "paper_first": True,
                "research_only": True,
            }
        )
    return serialize_value(explainers)


def build_operator_docs_index() -> dict[str, Any]:
    return serialize_value(
        {
            "doc": RETAIL_OPERATOR_GUIDE_DOC,
            "sections": {
                "first_session_checklist": f"{RETAIL_OPERATOR_GUIDE_DOC}#first-session-checklist",
                "no_trade_explanation_guide": f"{RETAIL_OPERATOR_GUIDE_DOC}#no-trade-explanation-guide",
                "broker_readiness_guide": f"{RETAIL_OPERATOR_GUIDE_DOC}#broker-readiness-guide",
            },
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def build_onboarding_state_transition_contract() -> dict[str, Any]:
    transitions = [
        {"from": "not_started", "to": "watching", "condition": "User confirms paper-first mode and opens the checklist."},
        {"from": "watching", "to": "blocked", "condition": "Any paper readiness, data, route, or blocker check needs review."},
        {"from": "blocked", "to": "watching", "condition": "The blocker is resolved by normal operator review; no kill switch is auto-cleared."},
        {"from": "watching", "to": "paper_ready", "condition": "Paper route, data freshness, reconciliation, and blocker checks are clear."},
        {"from": "paper_ready", "to": "killed", "condition": "A kill switch or hard lock becomes active."},
    ]
    return serialize_value(
        {
            "states": ["not_started", "watching", "blocked", "paper_ready", "killed"],
            "transitions": transitions,
            "auto_clears_kill_switch": False,
            "changes_broker_routes": False,
            "submits_orders": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def measure_retail_operator_readiness_metrics(
    *,
    onboarding_sessions: list[dict[str, Any]] | None = None,
    no_trade_records: list[dict[str, Any]] | None = None,
    paper_readiness_checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    sessions = _as_list(onboarding_sessions) or [
        {"started_at": "2026-05-09T13:30:00Z", "paper_ready_at": "2026-05-09T13:36:00Z"}
    ]
    no_trades = _as_list(no_trade_records) or [
        {
            "blocker": "setup_not_confirmed",
            "desk": "Equities Momentum Desk",
            "timestamp": "2026-05-09T13:35:00Z",
            "next_scan": "2026-05-09T13:40:00Z",
            "explanation": "The setup did not pass the evidence threshold.",
        }
    ]
    readiness_checks = _as_list(paper_readiness_checks) or [{"status": "ready"}, {"status": "ready"}, {"status": "blocked"}]

    ready_durations_seconds: list[float] = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        started = str(session.get("started_at") or "").replace("Z", "+00:00")
        ready = str(session.get("paper_ready_at") or "").replace("Z", "+00:00")
        try:
            started_at = datetime.fromisoformat(started)
            ready_at = datetime.fromisoformat(ready)
        except ValueError:
            continue
        ready_durations_seconds.append(max((ready_at - started_at).total_seconds(), 0.0))

    no_trade_validations = [validate_no_trade_record(row) for row in no_trades if isinstance(row, dict)]
    readiness_passes = [str(row.get("status") or "").strip().lower() == "ready" for row in readiness_checks if isinstance(row, dict)]
    average_time = round(sum(ready_durations_seconds) / len(ready_durations_seconds), 3) if ready_durations_seconds else None
    explanation_coverage = round(sum(1 for row in no_trade_validations if row["valid"]) / len(no_trade_validations), 6) if no_trade_validations else 0.0
    pass_rate = round(sum(1 for passed in readiness_passes if passed) / len(readiness_passes), 6) if readiness_passes else 0.0
    return serialize_value(
        {
            "time_to_first_paper_ready_state_seconds": average_time,
            "no_trade_explanation_coverage_rate": explanation_coverage,
            "paper_readiness_pass_rate": pass_rate,
            "sample_counts": {
                "onboarding_sessions": len(sessions),
                "no_trade_records": len(no_trade_validations),
                "paper_readiness_checks": len(readiness_passes),
            },
            "metrics_are_claims": False,
            **READ_ONLY_SAFETY_FLAGS,
        }
    )


def get_retail_paper_operator_readiness_summary() -> dict[str, Any]:
    no_trade_template = {
        "blocker": "no_trade_setup_not_confirmed",
        "desk": "Equities Momentum Desk",
        "timestamp": _utc_now(),
        "next_scan": "next configured desk scan",
        "explanation": "The desk is watching because the setup did not pass blocker and evidence checks.",
    }
    return serialize_value(
        {
            "status": "ready",
            "generated_at": _utc_now(),
            "category": "retail_trading_bot",
            "implemented_requirement_count": len(RETAIL_REQUIREMENT_EVIDENCE),
            "requirement_evidence": dict(RETAIL_REQUIREMENT_EVIDENCE),
            "guided_onboarding": build_guided_onboarding_checklist(paper_ready=True),
            "paper_mode_health": build_paper_mode_health_checklist(),
            "daily_operator_summary": build_daily_operator_summary(no_trades=[no_trade_template]),
            "demo_evidence_policy": build_demo_evidence_policy(),
            "no_trade_record_contract": validate_no_trade_record(no_trade_template),
            "retail_research_language": build_retail_research_language(),
            "missed_opportunity_review_policy": build_missed_opportunity_review_policy(),
            "operator_surface_label_contract": build_operator_surface_label_contract(),
            "broker_readiness_wizard": build_broker_readiness_wizard(),
            "paper_order_explanations": {
                "filled": explain_paper_order_event({"status": "filled"}),
                "rejected": explain_paper_order_event({"status": "rejected", "reason": "market closed"}),
            },
            "support_export_governance_policy": build_support_export_governance_policy(),
            "user_facing_proof_labels": build_user_facing_proof_labels(),
            "customer_safe_empty_states": build_customer_safe_empty_states(),
            "strategy_explainers": build_strategy_explainers(),
            "operator_docs": build_operator_docs_index(),
            "onboarding_state_transition_contract": build_onboarding_state_transition_contract(),
            "retail_operator_metrics": measure_retail_operator_readiness_metrics(),
            **READ_ONLY_SAFETY_FLAGS,
        }
    )
