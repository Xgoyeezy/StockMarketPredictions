from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.core.config import settings
from backend.models.saas import BrokerageLinkedAccount
from backend.services.brokerage_account_service import build_linked_account_execution_client
from backend.services.desk_service import filter_frame_to_current_user
from backend.services.execution.alpaca_client import (
    AlpacaApiError,
    build_alpaca_live_client,
    build_alpaca_paper_client,
)
from backend.services.serialization import serialize_dataframe, serialize_value
from backend.services.storage_utils import write_dataframe_csv
from backend.services.tenant_service import _resolve_tenant_for_current_user
from backend.services.trade_service import (
    _build_capital_preservation_snapshot,
    get_order_events_snapshot,
    resolve_trade_identifier,
    sync_pending_orders_from_broker,
)
from backend.services.workspace_service import list_workspaces


def _coerce_float(value: Any) -> float | None:
    if value in (None, "", "nan"):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if pd.notna(normalized) else None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _format_money_delta(value: float | None) -> str:
    if value is None:
        return "$0.00"
    return f"${abs(float(value)):.2f}"


def _build_execution_review(row: pd.Series) -> dict[str, Any]:
    expected_fill_price = _coerce_float(row.get("expected_fill_price"))
    actual_fill_price = _coerce_float(row.get("actual_fill_price"))
    slippage_dollars = _coerce_float(row.get("fill_slippage_dollars"))
    slippage_bps = _coerce_float(row.get("fill_slippage_bps"))
    broker_status = str(row.get("broker_status") or row.get("order_status") or "").strip().lower()

    if expected_fill_price is None:
        expected_fill_price = (
            _coerce_float(row.get("live_price_at_submit"))
            or _coerce_float(row.get("limit_price"))
        )
    if actual_fill_price is None:
        actual_fill_price = (
            _coerce_float(row.get("broker_filled_avg_price"))
            or _coerce_float(row.get("live_price_at_open"))
        )

    if slippage_dollars is None and expected_fill_price is not None and actual_fill_price is not None:
        slippage_dollars = float(actual_fill_price - expected_fill_price)
    if (
        slippage_bps is None
        and slippage_dollars is not None
        and expected_fill_price is not None
        and expected_fill_price > 0
    ):
        slippage_bps = float((slippage_dollars / expected_fill_price) * 10000.0)

    abs_bps = abs(float(slippage_bps)) if slippage_bps is not None else None
    detail = "No expected versus realized fill record was saved for this close."
    key = "manual_review"
    label = "Manual fill record"
    tone = "neutral"

    if broker_status == "rejected":
        key = "rejected_route"
        label = "Rejected route"
        tone = "negative"
        detail = "The broker rejected this route. Review the order path before reusing the setup."
    elif broker_status == "partially_filled":
        key = "partial_fill"
        label = "Partial fill"
        tone = "warning"
        detail = "The route only partially filled. Review queue quality and urgency before sizing up."
    elif abs_bps is not None and abs_bps >= 40:
        key = "fragile_fill"
        label = "Fragile fill"
        tone = "negative"
        detail = f"Filled {abs_bps:.1f} bps away from the expected route."
    elif abs_bps is not None and abs_bps >= 15:
        key = "slipped_fill"
        label = "Slipped fill"
        tone = "warning"
        detail = f"Filled {abs_bps:.1f} bps away from the expected route."
    elif actual_fill_price is not None or broker_status == "filled":
        key = "clean_fill"
        label = "Clean fill"
        tone = "positive"
        detail = "Expected versus realized fill quality stayed controlled."

    return {
        "execution_review_key": key,
        "execution_review_label": label,
        "execution_review_tone": tone,
        "execution_review_detail": detail,
        "expected_fill_price": expected_fill_price,
        "actual_fill_price": actual_fill_price,
        "fill_slippage_dollars": slippage_dollars,
        "fill_slippage_bps": slippage_bps,
    }


def _build_trade_attribution(row: pd.Series, execution_review: dict[str, Any]) -> dict[str, Any]:
    pnl_dollars = _coerce_float(row.get("pnl_dollars")) or 0.0
    max_risk_dollars = _coerce_float(row.get("max_risk_dollars"))
    order_type = str(row.get("order_type") or "").strip().lower()
    event_risk = _coerce_bool(row.get("event_risk"))
    extended_hours = _coerce_bool(row.get("extended_hours"))
    execution_key = str(execution_review.get("execution_review_key") or "")
    slippage_bps = _coerce_float(execution_review.get("fill_slippage_bps"))
    abs_slippage_bps = abs(float(slippage_bps)) if slippage_bps is not None else None

    if pnl_dollars < 0 and max_risk_dollars is not None and max_risk_dollars > 0 and abs(pnl_dollars) > max_risk_dollars * 1.1:
        overshoot_pct = ((abs(pnl_dollars) / max_risk_dollars) - 1.0) * 100.0
        return {
            "attribution_key": "sizing_wrong",
            "attribution_label": "Sizing wrong",
            "attribution_tone": "negative",
            "attribution_detail": f"Realized loss outran the planned risk budget by {overshoot_pct:.0f}%.",
        }

    if pnl_dollars < 0 and (extended_hours or (event_risk and order_type == "market")):
        return {
            "attribution_key": "rule_review",
            "attribution_label": "Rule review",
            "attribution_tone": "warning",
            "attribution_detail": "The trade ran through an event or timing rule that deserved a slower route review.",
        }

    if pnl_dollars > 0 and execution_key in {"fragile_fill", "slipped_fill", "rejected_route", "partial_fill"}:
        slip_note = f" after giving back {abs_slippage_bps:.1f} bps on the fill" if abs_slippage_bps is not None else ""
        return {
            "attribution_key": "thesis_right_execution_wrong",
            "attribution_label": "Thesis right / execution wrong",
            "attribution_tone": "warning",
            "attribution_detail": f"The idea still finished green{slip_note}.",
        }

    if pnl_dollars < 0 and execution_key == "clean_fill":
        return {
            "attribution_key": "thesis_wrong_execution_fine",
            "attribution_label": "Thesis wrong / execution fine",
            "attribution_tone": "negative",
            "attribution_detail": "Fill quality stayed controlled, so the miss points back to thesis, timing, or invalidation.",
        }

    if pnl_dollars < 0 and execution_key in {"fragile_fill", "slipped_fill", "rejected_route", "partial_fill"}:
        return {
            "attribution_key": "execution_drift",
            "attribution_label": "Execution drift",
            "attribution_tone": "negative",
            "attribution_detail": "The close came with fragile fill quality, so execution review should come before reusing the setup.",
        }

    if pnl_dollars > 0:
        return {
            "attribution_key": "clean_win",
            "attribution_label": "Clean win",
            "attribution_tone": "positive",
            "attribution_detail": "The trade closed green without a clear sizing or fill-quality issue.",
        }

    if pnl_dollars < 0:
        return {
            "attribution_key": "thesis_miss",
            "attribution_label": "Thesis miss",
            "attribution_tone": "negative",
            "attribution_detail": "The idea closed red without a clear fill-quality issue. Review the entry thesis and invalidation.",
        }

    return {
        "attribution_key": "flat_review",
        "attribution_label": "Flat review",
        "attribution_tone": "neutral",
        "attribution_detail": "Closed flat. Review timing, costs, and conviction quality.",
    }


def _build_attribution_summary(journal: pd.DataFrame) -> dict[str, Any]:
    if journal.empty or "attribution_key" not in journal.columns:
        return {
            "total_reviewed": 0,
            "execution_review_count": 0,
            "thesis_review_count": 0,
            "risk_review_count": 0,
            "clean_win_count": 0,
            "flat_review_count": 0,
            "latest_review": None,
        }

    normalized_keys = journal["attribution_key"].astype(str).str.lower()
    execution_review_count = int(normalized_keys.isin({"thesis_right_execution_wrong", "execution_drift"}).sum())
    thesis_review_count = int(normalized_keys.isin({"thesis_wrong_execution_fine", "thesis_miss"}).sum())
    risk_review_count = int(normalized_keys.isin({"sizing_wrong", "rule_review"}).sum())
    clean_win_count = int((normalized_keys == "clean_win").sum())
    flat_review_count = int((normalized_keys == "flat_review").sum())

    latest_row = journal.iloc[0].to_dict() if len(journal.index) else None
    latest_review = None
    if latest_row is not None:
        latest_review = {
            "ticker": str(latest_row.get("ticker") or "").strip().upper() or "UNKNOWN",
            "label": latest_row.get("attribution_label") or latest_row.get("result_label") or "Review",
            "detail": latest_row.get("attribution_detail") or latest_row.get("execution_review_detail") or "",
            "closed_at": latest_row.get("closed_at") or latest_row.get("timestamp") or "",
        }

    return {
        "total_reviewed": int(len(journal.index)),
        "execution_review_count": execution_review_count,
        "thesis_review_count": thesis_review_count,
        "risk_review_count": risk_review_count,
        "clean_win_count": clean_win_count,
        "flat_review_count": flat_review_count,
        "latest_review": latest_review,
    }


def _build_empty_validation_snapshot() -> dict[str, Any]:
    return {
        "scorecards": [],
        "route_quality": {
            "clean_fill_count": 0,
            "slipped_fill_count": 0,
            "fragile_fill_count": 0,
            "rejected_route_count": 0,
            "partial_fill_count": 0,
            "average_abs_slippage_bps": None,
            "latest_execution_review": None,
        },
        "board_snapshot_history": {
            "count": 0,
            "items": [],
        },
        "replay_comparisons": {
            "board_outcomes": {
                "count": 0,
                "resolved_count": 0,
                "open_count": 0,
                "items": [],
            },
            "paper_live_slippage": {
                "count": 0,
                "average_signed_slippage_bps": None,
                "average_abs_slippage_bps": None,
                "worst_abs_slippage_bps": None,
                "items": [],
            },
        },
        "ranked_entry_rollout": _build_empty_ranked_entry_rollout_snapshot(),
    }


def _build_empty_ranked_entry_rollout_snapshot() -> dict[str, Any]:
    return {
        "available": False,
        "accepted": False,
        "status": "missing",
        "label": "Ranked-entry validation missing",
        "detail": "Run the ranked-entry strategy validation export before using it as a broker-live promotion gate.",
        "basis": "Ranked-entry promotion is still validation-only because no acceptance artifact is available.",
        "failure_basis": "Ranked-entry promotion is still validation-only because no acceptance artifact is available.",
        "baseline_key": "A",
        "candidate_key": "M",
        "promotion_candidate_key": "M",
        "baseline_label": None,
        "candidate_label": None,
        "promotion_candidate_label": None,
        "baseline": None,
        "candidate": None,
        "drawdown_limit_pct": None,
        "gross_cap_dollars": None,
        "scenario_count": 0,
        "summary_generated_at": None,
        "stress_matrix_generated_at": None,
        "metrics_source": None,
        "mark_to_market_coverage_status": None,
        "ledger_snapshot_consistency": None,
        "current_route_sample_status": None,
        "route_window_start": None,
        "route_window_end": None,
        "route_window_snapshot_count": 0,
        "current_route_fill_count": 0,
        "current_route_directional_fill_count": 0,
        "current_route_closed_trade_count": 0,
        "current_route_reconciliation_status": None,
        "current_route_orphan_order_event_count": 0,
        "legacy_orphan_order_event_count": 0,
        "last_submitted_current_route_order_at": None,
        "last_current_route_fill_at": None,
        "last_current_route_close_at": None,
        "all_history_validation_integrity": {},
        "current_route_validation_integrity": {},
        "prediction_stack_validation": {},
    }


def _read_validation_export_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_ranked_entry_rollout_snapshot() -> dict[str, Any]:
    snapshot = _build_empty_ranked_entry_rollout_snapshot()
    try:
        from backend.services.strategy_validation_service import (
            RUNTIME_EXPORTS_DIR,
            evaluate_ranked_entry_rollout_acceptance,
        )
    except Exception:
        snapshot["status"] = "invalid"
        snapshot["label"] = "Ranked-entry validation unavailable"
        snapshot["detail"] = "The ranked-entry validation service could not be loaded."
        snapshot["basis"] = snapshot["detail"]
        snapshot["failure_basis"] = snapshot["detail"]
        return snapshot

    latest_dir = RUNTIME_EXPORTS_DIR / "latest"
    summary_path = latest_dir / "summary.json"
    stress_matrix_path = latest_dir / "stress_matrix.json"
    tracker_path = latest_dir / "validation_tracker.json"

    summary_payload: dict[str, Any] = {}
    tracker_payload: dict[str, Any] = {}
    try:
        raw_summary = _read_validation_export_json(summary_path)
        if isinstance(raw_summary, dict):
            summary_payload = raw_summary
    except FileNotFoundError:
        summary_payload = {}
    except json.JSONDecodeError:
        snapshot["status"] = "invalid"
        snapshot["label"] = "Ranked-entry validation invalid"
        snapshot["detail"] = "The ranked-entry validation summary export could not be parsed."
        snapshot["basis"] = snapshot["detail"]
        snapshot["failure_basis"] = snapshot["detail"]
        return snapshot

    try:
        raw_tracker = _read_validation_export_json(tracker_path)
        if isinstance(raw_tracker, dict):
            tracker_payload = raw_tracker
    except FileNotFoundError:
        tracker_payload = {}
    except json.JSONDecodeError:
        tracker_payload = {}

    try:
        stress_matrix_payload = _read_validation_export_json(stress_matrix_path)
    except FileNotFoundError:
        return snapshot
    except json.JSONDecodeError:
        snapshot["status"] = "invalid"
        snapshot["label"] = "Ranked-entry validation invalid"
        snapshot["detail"] = "The ranked-entry stress-matrix export could not be parsed."
        snapshot["basis"] = snapshot["detail"]
        snapshot["failure_basis"] = snapshot["detail"]
        return snapshot

    if not isinstance(stress_matrix_payload, list):
        snapshot["status"] = "invalid"
        snapshot["label"] = "Ranked-entry validation invalid"
        snapshot["detail"] = "The ranked-entry stress-matrix export is not in the expected list format."
        snapshot["basis"] = snapshot["detail"]
        snapshot["failure_basis"] = snapshot["detail"]
        return snapshot

    starting_capital = _coerce_float(summary_payload.get("starting_capital")) or 100000.0
    validation_integrity = dict(summary_payload.get("validation_integrity") or {})
    current_route_validation_integrity = dict(summary_payload.get("current_route_validation_integrity") or {})
    current_route_execution_realism = dict(summary_payload.get("current_route_execution_realism") or {})
    broker_reconciliation = dict(summary_payload.get("broker_reconciliation") or {})
    prediction_stack_validation = dict(summary_payload.get("intraday_prediction_validation") or {})
    prediction_candidate_configuration = (
        str(
            prediction_stack_validation.get("active_candidate_configuration")
            or prediction_stack_validation.get("candidate_key")
            or prediction_stack_validation.get("prediction_promotion_tier")
            or ""
        ).strip()
        or "hybrid_stock_only"
    )
    current_route_stress_matrix = list(current_route_execution_realism.get("stress_matrix") or [])
    acceptance = evaluate_ranked_entry_rollout_acceptance(
        current_route_stress_matrix,
        starting_capital=starting_capital,
        baseline_key="A",
        candidate_key="M",
    )
    matrix_by_key = {
        str(item.get("key") or "").strip().upper(): item
        for item in current_route_stress_matrix
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    baseline_key = str(acceptance.get("baseline_key") or "A").strip().upper() or "A"
    candidate_key = str(acceptance.get("candidate_key") or "M").strip().upper() or "M"
    baseline_row = dict(matrix_by_key.get(baseline_key) or {})
    candidate_row = dict(matrix_by_key.get(candidate_key) or {})
    status = str(acceptance.get("status") or "missing").strip().lower() or "missing"
    accepted = bool(acceptance.get("accepted"))
    basis = str(acceptance.get("basis") or snapshot["basis"]).strip() or snapshot["basis"]
    signal_execution_alignment = dict(summary_payload.get("signal_execution_alignment") or {})
    current_route_directional_fill_count = int(
        signal_execution_alignment.get("current_route_directional_fill_count")
        or signal_execution_alignment.get("current_route_fill_count")
        or 0
    )
    current_route_fill_count = current_route_directional_fill_count
    current_route_closed_trade_count = int(current_route_execution_realism.get("closed_trade_count") or 0)
    checklist_by_key = {
        str(item.get("key") or "").strip(): dict(item)
        for item in list(tracker_payload.get("checklist") or [])
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    }
    accounting_status = str((checklist_by_key.get("accounting") or {}).get("status") or "").strip().lower()
    execution_realism_status = str((checklist_by_key.get("execution_realism") or {}).get("status") or "").strip().lower()
    ranked_gate_status = str((checklist_by_key.get("ranked_entry_rollout") or {}).get("status") or "").strip().lower()
    current_route_sample_status = str(current_route_validation_integrity.get("current_route_sample_status") or "").strip().lower() or "insufficient"
    validation_gate_blocked = (
        accounting_status not in {"", "pass"}
        or execution_realism_status not in {"", "pass"}
        or ranked_gate_status == "partial"
        or str(current_route_validation_integrity.get("ledger_snapshot_consistency") or "").strip().lower() == "inconsistent"
    )
    if current_route_sample_status != "sufficient":
        accepted = False
        status = "blocked"
        basis = "Ranked-entry promotion remains blocked while the current-route validation sample is still being collected."
    elif str(prediction_stack_validation.get("status") or "").strip().lower() != "pass":
        accepted = False
        status = "blocked"
        basis = (
            str(prediction_stack_validation.get("basis") or "").strip()
            or f"The {prediction_candidate_configuration} intraday prediction tier has not passed validation yet."
        )
    elif accepted and validation_gate_blocked:
        accepted = False
        status = "blocked"
        basis = (
            str((checklist_by_key.get("ranked_entry_rollout") or {}).get("detail") or "").strip()
            or str(current_route_validation_integrity.get("basis") or "").strip()
            or "Ranked-entry promotion remains blocked until validation integrity clears."
        )
    if accepted:
        label = "Ranked-entry rollout accepted"
        detail = (
            f"The widened ranked-entry profile {candidate_key} clears the baseline promotion gate and can be reviewed for broker-live rollout."
        )
    elif status == "blocked":
        if current_route_sample_status != "sufficient":
            label = "Ranked-entry sample collecting"
            detail = "Collect more current-route paper fills and closed trades before scoring the widened ranked-entry profile for broker-live promotion."
        else:
            label = "Ranked-entry rollout blocked"
            detail = "The ranked-entry candidate passed the scenario matrix, but validation integrity has not cleared for broker-live promotion."
    elif status == "rejected":
        label = "Ranked-entry rollout rejected"
        detail = (
            f"The widened ranked-entry profile {candidate_key} stays in validation-only mode until the export improves."
        )
    elif status == "missing":
        label = "Ranked-entry validation missing"
        detail = snapshot["detail"]
    else:
        label = "Ranked-entry validation incomplete"
        detail = "The ranked-entry promotion artifact is present, but the required baseline or candidate row is missing."

    return serialize_value(
        {
            "available": True,
            "accepted": accepted,
            "status": status,
            "label": label,
            "detail": detail,
            "basis": basis,
            "failure_basis": None if accepted else basis,
            "baseline_key": baseline_key,
            "candidate_key": candidate_key,
            "promotion_candidate_key": candidate_key,
            "baseline_label": str(baseline_row.get("label") or baseline_key).strip() or baseline_key,
            "candidate_label": str(candidate_row.get("label") or candidate_key).strip() or candidate_key,
            "promotion_candidate_label": str(candidate_row.get("label") or candidate_key).strip() or candidate_key,
            "baseline": dict(acceptance.get("baseline") or {}),
            "candidate": dict(acceptance.get("candidate") or {}),
            "drawdown_limit_pct": acceptance.get("drawdown_limit_pct"),
            "gross_cap_dollars": acceptance.get("gross_cap_dollars"),
            "scenario_count": len(current_route_stress_matrix),
            "summary_generated_at": summary_payload.get("generated_at"),
            "stress_matrix_generated_at": summary_payload.get("generated_at"),
            "metrics_source": current_route_validation_integrity.get("metrics_source"),
            "mark_to_market_coverage_status": current_route_validation_integrity.get("mark_to_market_coverage_status"),
            "ledger_snapshot_consistency": current_route_validation_integrity.get("ledger_snapshot_consistency"),
            "current_route_sample_status": current_route_sample_status,
            "route_window_start": current_route_validation_integrity.get("route_window_start"),
            "route_window_end": current_route_validation_integrity.get("route_window_end"),
            "route_window_snapshot_count": current_route_validation_integrity.get("route_window_snapshot_count"),
            "current_route_fill_count": current_route_fill_count,
            "current_route_directional_fill_count": current_route_directional_fill_count,
            "current_route_closed_trade_count": current_route_closed_trade_count,
            "current_route_reconciliation_status": broker_reconciliation.get("current_route_reconciliation_status"),
            "current_route_orphan_order_event_count": int(
                broker_reconciliation.get("current_route_orphan_order_event_count") or 0
            ),
            "legacy_orphan_order_event_count": int(
                broker_reconciliation.get("legacy_orphan_order_event_count") or 0
            ),
            "last_submitted_current_route_order_at": broker_reconciliation.get("last_submitted_current_route_order_at"),
            "last_current_route_fill_at": broker_reconciliation.get("last_current_route_fill_at"),
            "last_current_route_close_at": broker_reconciliation.get("last_current_route_close_at"),
            "all_history_validation_integrity": validation_integrity,
            "current_route_validation_integrity": current_route_validation_integrity,
            "prediction_stack_validation": prediction_stack_validation,
            "prediction_active_candidate_configuration": prediction_candidate_configuration,
            "prediction_preferred_candidate_configuration": str(
                prediction_stack_validation.get("preferred_candidate_configuration") or prediction_candidate_configuration
            ).strip() or prediction_candidate_configuration,
            "prediction_promotion_tier": str(
                prediction_stack_validation.get("prediction_promotion_tier") or prediction_candidate_configuration
            ).strip() or prediction_candidate_configuration,
        }
    )


def _build_account_snapshot_from_client(*, client, provider: str, label: str, detail: str) -> dict[str, Any]:
    account = client.get_account()
    positions = client.list_positions()
    normalized_positions: list[dict[str, Any]] = []
    for row in positions:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        normalized_positions.append(
            {
                "symbol": symbol,
                "side": str(row.get("side") or "").strip().lower() or None,
                "qty": _coerce_float(row.get("qty")) or 0.0,
                "market_value": _coerce_float(row.get("market_value")) or 0.0,
                "cost_basis": _coerce_float(row.get("cost_basis")) or 0.0,
                "unrealized_pl": _coerce_float(row.get("unrealized_pl")) or 0.0,
                "unrealized_plpc": _coerce_float(row.get("unrealized_plpc")),
                "current_price": _coerce_float(row.get("current_price")),
                "avg_entry_price": _coerce_float(row.get("avg_entry_price")),
            }
        )
    return {
        "provider": provider,
        "label": label,
        "connected": True,
        "status": "connected",
        "detail": detail,
        "equity": _coerce_float(account.get("equity")),
        "cash": _coerce_float(account.get("cash")),
        "portfolio_value": _coerce_float(account.get("portfolio_value")),
        "buying_power": _coerce_float(account.get("buying_power")),
        "position_market_value": _coerce_float(account.get("position_market_value")),
        "daytrade_count": _coerce_float(account.get("daytrade_count")),
        "pattern_day_trader": _coerce_bool(account.get("pattern_day_trader")),
        "position_count": len(normalized_positions),
        "positions": normalized_positions,
        "last_updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_unavailable_broker_account_snapshot(*, provider: str, label: str, detail: str, status: str = "unavailable") -> dict[str, Any]:
    return {
        "provider": provider,
        "label": label,
        "connected": False,
        "status": status,
        "detail": detail,
        "equity": None,
        "cash": None,
        "portfolio_value": None,
        "buying_power": None,
        "position_market_value": None,
        "daytrade_count": None,
        "pattern_day_trader": None,
        "position_count": 0,
        "positions": [],
        "last_updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _build_broker_account_snapshot(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
    account_profile: str | None = None,
    linked_account_id: str | None = None,
) -> dict[str, Any]:
    normalized_profile = str(account_profile or "personal_paper").strip().lower() or "personal_paper"
    if normalized_profile == "personal_live":
        if not settings.alpaca_api_key_id or not settings.alpaca_api_secret_key:
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_live",
                label="Live account",
                detail="Alpaca live credentials are not configured.",
            )
        if not settings.alpaca_live_trading_enabled:
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_live",
                label="Live account",
                detail="Alpaca live trading is not enabled.",
                status="locked",
            )
        try:
            return _build_account_snapshot_from_client(
                client=build_alpaca_live_client(),
                provider="alpaca_live",
                label="Live account",
                detail="Live balances and positions are coming from Alpaca.",
            )
        except AlpacaApiError as exc:
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_live",
                label="Live account",
                detail=str(exc),
                status="error",
            )

    if normalized_profile == "brokerage":
        if db is None or current_user is None or not str(linked_account_id or "").strip():
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_oauth",
                label="Brokerage account",
                detail="A bound linked broker account is required for the Brokerage profile.",
                status="locked",
            )
        tenant = _resolve_tenant_for_current_user(db, current_user)
        linked_account = db.execute(
            select(BrokerageLinkedAccount).where(
                BrokerageLinkedAccount.id == str(linked_account_id or "").strip(),
                BrokerageLinkedAccount.tenant_id == tenant.id,
            )
        ).scalar_one_or_none()
        if linked_account is None:
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_oauth",
                label="Brokerage account",
                detail="The bound linked broker account could not be found.",
                status="locked",
            )
        connection_status = str(linked_account.connection_status or "").strip().lower()
        token_health = str(linked_account.token_health or "").strip().lower()
        if connection_status != "connected" or token_health not in {"healthy", "unknown"}:
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_oauth",
                label=linked_account.label or "Brokerage account",
                detail="The bound linked broker account is disconnected or needs to be relinked.",
                status="locked",
            )
        try:
            return _build_account_snapshot_from_client(
                client=build_linked_account_execution_client(linked_account),
                provider="alpaca_oauth",
                label=linked_account.label or linked_account.linked_identity_label or "Brokerage account",
                detail="Brokerage balances and positions are coming from the bound Alpaca OAuth account.",
            )
        except AlpacaApiError as exc:
            return _build_unavailable_broker_account_snapshot(
                provider="alpaca_oauth",
                label=linked_account.label or linked_account.linked_identity_label or "Brokerage account",
                detail=str(exc),
                status="error",
            )

    if not settings.alpaca_api_key_id or not settings.alpaca_api_secret_key:
        return _build_unavailable_broker_account_snapshot(
            provider="alpaca_paper",
            label="Paper account",
            detail="Alpaca paper credentials are not configured.",
        )
    try:
        return _build_account_snapshot_from_client(
            client=build_alpaca_paper_client(),
            provider="alpaca_paper",
            label="Paper account",
            detail="Live paper balances and positions are coming from Alpaca.",
        )
    except AlpacaApiError as exc:
        return _build_unavailable_broker_account_snapshot(
            provider="alpaca_paper",
            label="Paper account",
            detail=str(exc),
            status="error",
        )


def _build_reconciled_closed_trade_row(row: dict[str, Any]) -> dict[str, Any]:
    close_underlying_price = (
        _coerce_float(row.get("broker_filled_avg_price"))
        or _coerce_float(row.get("live_price_at_open"))
        or _coerce_float(row.get("live_price_at_submit"))
        or 0.0
    )
    instrument_type = str(row.get("instrument_type") or "listed_option").strip().lower()
    close_contract_mid = (
        close_underlying_price / 100.0
        if instrument_type == "equity" and close_underlying_price > 0
        else (_coerce_float(row.get("contract_mid_at_open")) or 0.0)
    )
    suggested_contracts = _coerce_float(row.get("suggested_contracts")) or 0.0
    return {
        **row,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "live_price_at_close": close_underlying_price,
        "contract_mid_at_close": close_contract_mid,
        "closed_contracts": suggested_contracts,
        "remaining_contracts_after_close": 0.0,
        "close_fraction": 1.0,
        "pnl_per_contract": _coerce_float(row.get("pnl_per_contract")) or 0.0,
        "realized_pnl": _coerce_float(row.get("realized_pnl")) or 0.0,
        "status": "RECONCILED",
        "order_status": "CLOSED",
        "route_state": "reconciled",
        "book_state": "flat",
        "reconciliation_note": "Removed stale local broker-paper open trade after Alpaca reported no open position.",
        "reconciliation_source": "alpaca_paper_flat_account",
    }


def _reconcile_local_broker_paper_state(
    *,
    current_user: Any | None,
    broker_account: dict[str, Any],
) -> dict[str, Any]:
    if current_user is None:
        return {"performed": False, "reconciled_open_trades": 0, "items": []}
    if not broker_account.get("connected"):
        return {"performed": False, "reconciled_open_trades": 0, "items": []}

    open_trades = sdm.read_open_trades()
    if open_trades.empty:
        return {"performed": True, "reconciled_open_trades": 0, "items": []}

    scoped_open_trades = filter_frame_to_current_user(open_trades, current_user)
    if scoped_open_trades.empty:
        return {"performed": True, "reconciled_open_trades": 0, "items": []}

    broker_positions = {
        str(item.get("symbol") or "").strip().upper(): item
        for item in list(broker_account.get("positions") or [])
        if str(item.get("symbol") or "").strip()
    }

    broker_name_series = scoped_open_trades.get("broker_name", pd.Series("", index=scoped_open_trades.index)).astype(str).str.strip().str.lower()
    scoped_mask = broker_name_series.eq("alpaca_paper")

    if not scoped_mask.any():
        return {"performed": True, "reconciled_open_trades": 0, "items": []}

    closed_trades = sdm.read_closed_trades()
    next_open = open_trades.copy()
    next_closed = closed_trades.copy()
    reconciled_items: list[dict[str, Any]] = []
    stale_indices: list[int] = []

    for trade_index in scoped_open_trades.index[scoped_mask]:
        row = next_open.loc[trade_index].to_dict()
        ticker = str(row.get("ticker") or "").strip().upper()
        instrument_type = str(row.get("instrument_type") or "listed_option").strip().lower()
        contract_symbol = str(row.get("contract_symbol") or "").strip().upper()
        broker_position_key = contract_symbol if instrument_type == "listed_option" and contract_symbol else ticker
        if not broker_position_key or broker_position_key in broker_positions:
            continue

        stale_indices.append(int(trade_index))
        trade_id = resolve_trade_identifier(row)
        detail = (
            "Local broker-paper option trade was removed because Alpaca shows no open position for this contract."
            if instrument_type == "listed_option"
            else "Local broker-paper trade was removed because Alpaca shows no open position for this symbol."
        )
        reconciled_items.append(
            {
                "trade_id": trade_id,
                "ticker": ticker,
                "contract_symbol": contract_symbol or None,
                "detail": detail,
            }
        )

        if not next_closed.empty and "trade_id" in next_closed.columns:
            matches = next_closed["trade_id"].astype(str).str.strip() == trade_id
            if matches.any():
                closed_index = next_closed.index[matches][-1]
                next_closed.loc[closed_index, "status"] = "RECONCILED"
                next_closed.loc[closed_index, "remaining_contracts_after_close"] = 0.0
                next_closed.loc[closed_index, "close_fraction"] = 1.0
                next_closed.loc[closed_index, "closed_contracts"] = _coerce_float(row.get("suggested_contracts")) or 0.0
                if not str(next_closed.loc[closed_index].get("closed_at") or "").strip():
                    next_closed.loc[closed_index, "closed_at"] = datetime.now(timezone.utc).isoformat()
                next_closed.loc[closed_index, "reconciliation_note"] = (
                    "Marked reconciled after Alpaca reported no open position for this symbol."
                )
                next_closed.loc[closed_index, "reconciliation_source"] = "alpaca_paper_flat_account"
                continue

        reconciled_row = _build_reconciled_closed_trade_row(row)
        next_closed = pd.concat([next_closed, pd.DataFrame([reconciled_row])], ignore_index=True)

    if not stale_indices:
        return {"performed": True, "reconciled_open_trades": 0, "items": []}

    next_open = next_open.drop(index=stale_indices).reset_index(drop=True)
    write_dataframe_csv(sdm.OPEN_TRADES_PATH, next_open)
    write_dataframe_csv(sdm.CLOSED_TRADES_PATH, next_closed)
    sdm._invalidate_file_read_cache(sdm.OPEN_TRADES_PATH)
    sdm._invalidate_file_read_cache(sdm.CLOSED_TRADES_PATH)
    return {
        "performed": True,
        "reconciled_open_trades": len(stale_indices),
        "items": reconciled_items,
        "performed_at": datetime.now(timezone.utc).isoformat(),
    }


def _coerce_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _build_validation_scorecards(journal: pd.DataFrame) -> list[dict[str, Any]]:
    if journal.empty:
        return []

    total_reviewed = int(len(journal.index))
    pnl_series = pd.to_numeric(
        journal["pnl_dollars"] if "pnl_dollars" in journal.columns else pd.Series(0.0, index=journal.index, dtype="float64"),
        errors="coerce",
    ).fillna(0.0)
    wins = int((pnl_series > 0).sum())
    ranking_scores = pd.to_numeric(
        journal["setup_score"] if "setup_score" in journal.columns else pd.Series(index=journal.index, dtype="float64"),
        errors="coerce",
    )
    ranking_score_values = ranking_scores.dropna()
    avg_ranking_score = float(ranking_score_values.mean()) if not ranking_score_values.empty else None

    def _win_rate(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        pnl = pd.to_numeric(
            frame["pnl_dollars"] if "pnl_dollars" in frame.columns else pd.Series(0.0, index=frame.index, dtype="float64"),
            errors="coerce",
        ).fillna(0.0)
        if pnl.empty:
            return None
        return float((pnl > 0).sum() / len(pnl.index))

    def _avg_pnl(frame: pd.DataFrame) -> float | None:
        if frame.empty:
            return None
        pnl = pd.to_numeric(
            frame["pnl_dollars"] if "pnl_dollars" in frame.columns else pd.Series(0.0, index=frame.index, dtype="float64"),
            errors="coerce",
        ).fillna(0.0)
        if pnl.empty:
            return None
        return float(pnl.mean())

    event_mask = journal.get("event_risk", pd.Series(False, index=journal.index)).fillna(False).astype(bool)
    event_rows = journal[event_mask]
    execution_keys = journal.get("execution_review_key", pd.Series("", index=journal.index)).astype(str).str.lower()
    clean_execution_rows = journal[execution_keys == "clean_fill"]
    fragile_execution_rows = journal[execution_keys.isin({"fragile_fill", "slipped_fill", "partial_fill", "rejected_route"})]

    average_probability_up = pd.to_numeric(
        journal["average_probability_up"] if "average_probability_up" in journal.columns else pd.Series(index=journal.index, dtype="float64"),
        errors="coerce",
    )
    probability_up = pd.to_numeric(
        journal["probability_up"] if "probability_up" in journal.columns else pd.Series(index=journal.index, dtype="float64"),
        errors="coerce",
    )
    if average_probability_up.dropna().empty:
        baseline_delta = probability_up - 0.5 if probability_up is not None else pd.Series(dtype="float64")
    else:
        baseline_delta = probability_up - average_probability_up
    benchmark_ready_rows = journal[baseline_delta.fillna(0) >= 0]

    ranking_tone = "positive" if wins / total_reviewed >= 0.55 else "warning" if wins / total_reviewed >= 0.45 else "negative"
    event_tone = "positive" if event_rows.empty else "warning" if (_win_rate(event_rows) or 0) >= 0.45 else "negative"
    execution_tone = "positive" if fragile_execution_rows.empty else "warning" if len(fragile_execution_rows.index) <= max(1, total_reviewed // 4) else "negative"
    benchmark_tone = "positive" if benchmark_ready_rows.empty or (_win_rate(benchmark_ready_rows) or 0) >= 0.5 else "warning"

    return [
        {
            "key": "ranking_board",
            "label": "Ranking board",
            "tone": ranking_tone,
            "value": f"{round((wins / total_reviewed) * 100)}% win",
            "helper": f"{total_reviewed} reviewed | Avg board {avg_ranking_score:.1f}" if avg_ranking_score is not None else f"{total_reviewed} reviewed",
            "detail": "Closed-trade results for names that came through the board and later resolved into journal history.",
        },
        {
            "key": "event_windows",
            "label": "Event windows",
            "tone": event_tone,
            "value": str(int(len(event_rows.index))),
            "helper": (
                f"{round((_win_rate(event_rows) or 0) * 100)}% win | Avg {_format_money_delta(_avg_pnl(event_rows))}"
                if not event_rows.empty
                else "No event-tagged closes yet"
            ),
            "detail": "How event-risk trades have behaved after they made it through the desk and into a real close.",
        },
        {
            "key": "execution_quality",
            "label": "Execution quality",
            "tone": execution_tone,
            "value": str(int(len(clean_execution_rows.index))),
            "helper": f"{len(fragile_execution_rows.index)} fragile routes",
            "detail": "Clean fills versus slipped, partial, rejected, or fragile routes from resolved trades.",
        },
        {
            "key": "benchmark_check",
            "label": "Benchmark check",
            "tone": benchmark_tone,
            "value": str(int(len(benchmark_ready_rows.index))),
            "helper": (
                f"{round((_win_rate(benchmark_ready_rows) or 0) * 100)}% win on rows at or above baseline"
                if not benchmark_ready_rows.empty
                else "Baseline comparison is still thin"
            ),
            "detail": "Simple benchmark comparison using the saved forecast probability against its available baseline.",
        },
    ]


def _build_route_quality_snapshot(journal: pd.DataFrame) -> dict[str, Any]:
    if journal.empty:
        return _build_empty_validation_snapshot()["route_quality"]

    execution_keys = journal.get("execution_review_key", pd.Series("", index=journal.index)).astype(str).str.lower()
    slippage_bps = pd.to_numeric(
        journal["fill_slippage_bps"] if "fill_slippage_bps" in journal.columns else pd.Series(index=journal.index, dtype="float64"),
        errors="coerce",
    )
    latest_row = journal.iloc[0].to_dict() if len(journal.index) else None
    latest_execution_review = None
    if latest_row is not None:
        latest_execution_review = {
            "ticker": str(latest_row.get("ticker") or "").strip().upper() or "UNKNOWN",
            "label": latest_row.get("execution_review_label") or "Execution review",
            "detail": latest_row.get("execution_review_detail") or "",
            "slippage_bps": latest_row.get("fill_slippage_bps"),
        }

    return {
        "clean_fill_count": int((execution_keys == "clean_fill").sum()),
        "slipped_fill_count": int((execution_keys == "slipped_fill").sum()),
        "fragile_fill_count": int((execution_keys == "fragile_fill").sum()),
        "rejected_route_count": int((execution_keys == "rejected_route").sum()),
        "partial_fill_count": int((execution_keys == "partial_fill").sum()),
        "average_abs_slippage_bps": float(slippage_bps.abs().mean()) if not slippage_bps.dropna().empty else None,
        "latest_execution_review": latest_execution_review,
    }


def _extract_validation_artifact_from_workspace(row: dict[str, Any]) -> dict[str, Any] | None:
    payload = dict(row.get("payload") or {})
    artifact = payload.get("validation_artifact")
    if not isinstance(artifact, dict):
        return None
    summary = dict(artifact.get("summary") or {})
    leader = dict(artifact.get("leader") or {})
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or "").strip(),
        "page": str(row.get("page") or "").strip().lower(),
        "updated_at": row.get("updated_at") or row.get("created_at") or "",
        "board_name": str(artifact.get("board_name") or "Candidate board snapshot").strip(),
        "source": str(artifact.get("source") or "board").strip().lower(),
        "interval": str(artifact.get("interval") or "").strip().lower(),
        "horizon": artifact.get("horizon"),
        "leader_ticker": str(leader.get("ticker") or "").strip().upper(),
        "leader_score": leader.get("ranking_score"),
        "leader_label": leader.get("ranking_label"),
        "candidate_count": int(summary.get("candidate_count", 0) or 0),
        "promote_count": int(summary.get("promote_count", 0) or 0),
        "review_count": int(summary.get("review_count", 0) or 0),
        "stand_down_count": int(summary.get("stand_down_count", 0) or 0),
        "event_window_count": int(summary.get("event_window_count", 0) or 0),
        "fragile_execution_count": int(summary.get("fragile_execution_count", 0) or 0),
    }


def _build_board_snapshot_history(current_user: Any | None) -> dict[str, Any]:
    if current_user is None:
        return _build_empty_validation_snapshot()["board_snapshot_history"]

    listing = list_workspaces(current_user.user_id, tenant_slug=getattr(current_user, "tenant_slug", None))
    rows = list(listing.get("items") or [])
    artifacts = []
    for row in rows:
        item = _extract_validation_artifact_from_workspace(row)
        if item is not None:
            artifacts.append(item)
    artifacts.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return {
        "count": len(artifacts),
        "items": serialize_value(artifacts[:6]),
    }


def _build_board_outcome_replay(
    journal: pd.DataFrame,
    board_snapshot_history: dict[str, Any],
) -> dict[str, Any]:
    items = list(board_snapshot_history.get("items") or [])
    if not items:
        return _build_empty_validation_snapshot()["replay_comparisons"]["board_outcomes"]

    replay_items: list[dict[str, Any]] = []
    resolved_count = 0
    open_count = 0

    for item in items[:6]:
        leader_ticker = str(item.get("leader_ticker") or "").strip().upper()
        snapshot_timestamp = _coerce_timestamp(item.get("updated_at"))
        matching_row = None

        if leader_ticker and not journal.empty:
            for _, row in journal.iterrows():
                if str(row.get("ticker") or "").strip().upper() != leader_ticker:
                    continue
                closed_at = _coerce_timestamp(row.get("closed_at") or row.get("timestamp"))
                if snapshot_timestamp is not None and closed_at is not None and closed_at < snapshot_timestamp:
                    continue
                matching_row = row
                break

        if matching_row is None:
            open_count += 1
            replay_items.append(
                {
                    "board_name": item.get("board_name"),
                    "leader_ticker": leader_ticker or "--",
                    "saved_at": item.get("updated_at"),
                    "status": "awaiting_resolution",
                    "status_label": "Awaiting resolution",
                    "status_tone": "warning",
                    "detail": "No later closed trade has resolved this leader snapshot yet.",
                    "result_label": None,
                    "attribution_label": None,
                    "pnl_dollars": None,
                }
            )
            continue

        resolved_count += 1
        pnl_dollars = _coerce_float(matching_row.get("pnl_dollars"))
        result_label = str(matching_row.get("result_label") or "").strip() or ("Win" if (pnl_dollars or 0) > 0 else "Loss" if (pnl_dollars or 0) < 0 else "Flat")
        attribution_label = str(matching_row.get("attribution_label") or "").strip() or "Review"
        tone = "positive" if (pnl_dollars or 0) > 0 else "negative" if (pnl_dollars or 0) < 0 else "neutral"
        replay_items.append(
            {
                "board_name": item.get("board_name"),
                "leader_ticker": leader_ticker or "--",
                "saved_at": item.get("updated_at"),
                "resolved_at": matching_row.get("closed_at") or matching_row.get("timestamp"),
                "status": "resolved",
                "status_label": "Resolved",
                "status_tone": tone,
                "detail": str(matching_row.get("attribution_detail") or matching_row.get("execution_review_detail") or "").strip(),
                "result_label": result_label,
                "attribution_label": attribution_label,
                "pnl_dollars": pnl_dollars,
                "execution_review_label": matching_row.get("execution_review_label"),
                "fill_slippage_bps": matching_row.get("fill_slippage_bps"),
            }
        )

    return {
        "count": len(replay_items),
        "resolved_count": resolved_count,
        "open_count": open_count,
        "items": serialize_value(replay_items),
    }


def _build_paper_live_slippage_replay(journal: pd.DataFrame) -> dict[str, Any]:
    if journal.empty:
        return _build_empty_validation_snapshot()["replay_comparisons"]["paper_live_slippage"]

    comparable = journal.copy()
    comparable["expected_fill_price"] = pd.to_numeric(
        comparable["expected_fill_price"] if "expected_fill_price" in comparable.columns else pd.Series(index=comparable.index, dtype="float64"),
        errors="coerce",
    )
    comparable["actual_fill_price"] = pd.to_numeric(
        comparable["actual_fill_price"] if "actual_fill_price" in comparable.columns else pd.Series(index=comparable.index, dtype="float64"),
        errors="coerce",
    )
    comparable["fill_slippage_bps"] = pd.to_numeric(
        comparable["fill_slippage_bps"] if "fill_slippage_bps" in comparable.columns else pd.Series(index=comparable.index, dtype="float64"),
        errors="coerce",
    )
    comparable["fill_slippage_dollars"] = pd.to_numeric(
        comparable["fill_slippage_dollars"] if "fill_slippage_dollars" in comparable.columns else pd.Series(index=comparable.index, dtype="float64"),
        errors="coerce",
    )
    comparable = comparable[
        comparable["expected_fill_price"].notna()
        & comparable["actual_fill_price"].notna()
        & comparable["fill_slippage_bps"].notna()
    ]

    if comparable.empty:
        return _build_empty_validation_snapshot()["replay_comparisons"]["paper_live_slippage"]

    slippage_bps = comparable["fill_slippage_bps"]
    replay_items = []
    for _, row in comparable.head(6).iterrows():
        signed_bps = _coerce_float(row.get("fill_slippage_bps"))
        tone = "negative" if signed_bps is not None and abs(signed_bps) >= 25 else "warning" if signed_bps is not None and abs(signed_bps) >= 10 else "positive"
        replay_items.append(
            {
                "ticker": str(row.get("ticker") or "").strip().upper() or "--",
                "closed_at": row.get("closed_at") or row.get("timestamp"),
                "expected_fill_price": _coerce_float(row.get("expected_fill_price")),
                "actual_fill_price": _coerce_float(row.get("actual_fill_price")),
                "slippage_bps": signed_bps,
                "slippage_dollars": _coerce_float(row.get("fill_slippage_dollars")),
                "execution_review_label": row.get("execution_review_label"),
                "tone": tone,
            }
        )

    return {
        "count": int(len(comparable.index)),
        "average_signed_slippage_bps": float(slippage_bps.mean()) if not slippage_bps.empty else None,
        "average_abs_slippage_bps": float(slippage_bps.abs().mean()) if not slippage_bps.empty else None,
        "worst_abs_slippage_bps": float(slippage_bps.abs().max()) if not slippage_bps.empty else None,
        "items": serialize_value(replay_items),
    }


def _build_validation_snapshot(journal: pd.DataFrame, current_user: Any | None = None) -> dict[str, Any]:
    snapshot = _build_empty_validation_snapshot()
    snapshot["scorecards"] = serialize_value(_build_validation_scorecards(journal))
    snapshot["route_quality"] = serialize_value(_build_route_quality_snapshot(journal))
    snapshot["board_snapshot_history"] = _build_board_snapshot_history(current_user)
    snapshot["replay_comparisons"] = {
        "board_outcomes": _build_board_outcome_replay(journal, snapshot["board_snapshot_history"]),
        "paper_live_slippage": _build_paper_live_slippage_replay(journal),
    }
    snapshot["ranked_entry_rollout"] = _build_ranked_entry_rollout_snapshot()
    return snapshot


def _normalize_closed_trades_for_journal(closed_trades: pd.DataFrame) -> pd.DataFrame:
    if closed_trades.empty:
        return pd.DataFrame()

    journal = closed_trades.copy()
    index = journal.index
    default_series = pd.Series("", index=index, dtype="object")
    default_numeric = pd.Series(0.0, index=index, dtype="float64")

    closed_at = journal.get("closed_at", default_series).fillna("")
    opened_at = journal.get("opened_at", default_series).fillna("")
    timestamp = closed_at.where(closed_at.astype(str).str.strip().ne(""), opened_at)
    journal["timestamp"] = timestamp

    instrument_type = journal.get("instrument_type", default_series).fillna("").astype(str).str.strip().str.lower()
    instrument_label = journal.get("instrument_label", default_series).fillna("").astype(str).str.strip()
    instrument_label = instrument_label.where(
        instrument_label.ne(""),
        instrument_type.map(
            {
                "equity": "Equity",
                "listed_option": "Listed option",
            }
        ).fillna("Trade"),
    )
    journal["instrument_type"] = instrument_type
    journal["instrument_label"] = instrument_label
    journal["interval"] = journal.get("interval", default_series).fillna("").astype(str)

    option_right = journal.get("option_right", default_series).fillna("").astype(str).str.strip().str.upper()
    verdict = journal.get("verdict", default_series).fillna("").astype(str).str.strip().str.upper()
    journal["direction"] = option_right.where(option_right.ne(""), verdict)
    journal["entry_contract_mid"] = pd.to_numeric(
        journal.get("entry_contract_mid", journal.get("contract_mid_at_open", default_numeric)),
        errors="coerce",
    )
    journal["close_contract_mid"] = pd.to_numeric(
        journal.get("close_contract_mid", journal.get("contract_mid_at_close", default_numeric)),
        errors="coerce",
    )
    journal["pnl_dollars"] = pd.to_numeric(
        journal.get("pnl_dollars", journal.get("realized_pnl", default_numeric)),
        errors="coerce",
    ).fillna(0.0)

    row_snapshots = [row for _, row in journal.iterrows()]
    execution_reviews = [_build_execution_review(row) for row in row_snapshots]
    attribution_reviews = [
        _build_trade_attribution(row, execution_reviews[index])
        for index, row in enumerate(row_snapshots)
    ]

    pnl_series = journal["pnl_dollars"]
    journal["result_label"] = pnl_series.apply(lambda value: "Win" if value > 0 else "Loss" if value < 0 else "Flat")
    journal["execution_review_key"] = [item["execution_review_key"] for item in execution_reviews]
    journal["execution_review_label"] = [item["execution_review_label"] for item in execution_reviews]
    journal["execution_review_tone"] = [item["execution_review_tone"] for item in execution_reviews]
    journal["execution_review_detail"] = [item["execution_review_detail"] for item in execution_reviews]
    journal["expected_fill_price"] = [item["expected_fill_price"] for item in execution_reviews]
    journal["actual_fill_price"] = [item["actual_fill_price"] for item in execution_reviews]
    journal["fill_slippage_dollars"] = [item["fill_slippage_dollars"] for item in execution_reviews]
    journal["fill_slippage_bps"] = [item["fill_slippage_bps"] for item in execution_reviews]
    journal["attribution_key"] = [item["attribution_key"] for item in attribution_reviews]
    journal["attribution_label"] = [item["attribution_label"] for item in attribution_reviews]
    journal["attribution_tone"] = [item["attribution_tone"] for item in attribution_reviews]
    journal["attribution_detail"] = [item["attribution_detail"] for item in attribution_reviews]
    journal["journal_source"] = "closed_trade"
    return journal.sort_values(by="timestamp", ascending=False, na_position="last").reset_index(drop=True)


def _load_trade_journal_frame(limit: int, offset: int, *, current_user: Any | None = None) -> pd.DataFrame:
    closed_trades = filter_frame_to_current_user(sdm.read_closed_trades(), current_user)
    normalized_closed_trades = _normalize_closed_trades_for_journal(closed_trades)
    if not normalized_closed_trades.empty:
        return normalized_closed_trades

    legacy_journal = sdm.read_trade_journal(limit=max(limit + offset, 100))
    legacy_journal = filter_frame_to_current_user(legacy_journal, current_user)
    if legacy_journal.empty:
        return legacy_journal

    journal = legacy_journal.copy()
    journal["journal_source"] = "legacy"
    if "pnl_dollars" not in journal.columns and "realized_pnl" in journal.columns:
        journal["pnl_dollars"] = pd.to_numeric(journal["realized_pnl"], errors="coerce").fillna(0.0)
    return journal


def _build_latest_order_lookup(order_events: dict[str, Any]) -> dict[str, dict[str, Any]]:
    latest_by_trade: dict[str, dict[str, Any]] = {}
    for item in list(order_events.get("items") or []):
        trade_id = str(item.get("trade_id") or "").strip()
        if trade_id and trade_id not in latest_by_trade:
            latest_by_trade[trade_id] = item
    return latest_by_trade


def _attach_latest_order_events_to_rows(
    rows: list[dict[str, Any]],
    latest_by_trade: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    for row in rows:
        trade_id = resolve_trade_identifier(row)
        row["trade_id"] = trade_id
        latest_event = latest_by_trade.get(trade_id)
        if latest_event is not None:
            row["latest_order_event"] = latest_event
    return rows


def _attach_trade_events_to_open_and_monitor_rows(
    open_trade_rows: list[dict[str, Any]],
    monitor_rows: list[dict[str, Any]],
    latest_by_trade: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    open_trade_by_key: dict[tuple[str, str], str] = {}
    open_trade_by_ticker: dict[str, list[str]] = {}
    for row in open_trade_rows:
        trade_id = resolve_trade_identifier(row)
        row["trade_id"] = trade_id
        ticker = str(row.get("ticker") or "").strip().upper()
        opened_at = str(row.get("opened_at") or "").strip()
        if ticker and opened_at:
            open_trade_by_key[(ticker, opened_at)] = trade_id
        if ticker:
            open_trade_by_ticker.setdefault(ticker, []).append(trade_id)
        latest_event = latest_by_trade.get(trade_id)
        if latest_event is not None:
            row["latest_order_event"] = latest_event
    for row in monitor_rows:
        trade_id = resolve_trade_identifier(row)
        ticker = str(row.get("ticker") or "").strip().upper()
        opened_at = str(row.get("opened_at") or "").strip()
        if not str(row.get("trade_id") or "").strip() and ticker:
            matched_trade_id = open_trade_by_key.get((ticker, opened_at))
            if matched_trade_id is None and len(open_trade_by_ticker.get(ticker, [])) == 1:
                matched_trade_id = open_trade_by_ticker[ticker][0]
            if matched_trade_id:
                trade_id = matched_trade_id
        row["trade_id"] = trade_id
        latest_event = latest_by_trade.get(trade_id)
        if latest_event is not None:
            row["latest_order_event"] = latest_event
    return open_trade_rows, monitor_rows


def _filter_monitor_rows_for_trades(trades: pd.DataFrame, monitored: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or monitored.empty:
        return monitored.iloc[0:0].copy() if not monitored.empty else pd.DataFrame()
    trade_ids = {
        resolve_trade_identifier(row)
        for row in trades.to_dict(orient="records")
    }
    if not trade_ids:
        return monitored.iloc[0:0].copy()
    monitored_trade_ids = monitored.apply(resolve_trade_identifier, axis=1)
    return monitored.loc[monitored_trade_ids.isin(trade_ids)].copy()


def get_open_trades(
    search: str = "",
    limit: int = 250,
    offset: int = 0,
    action_filter: str = "all",
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    trades = filter_frame_to_current_user(sdm.read_open_trades(), current_user)
    monitored = filter_frame_to_current_user(sdm.monitor_open_trades(), current_user)
    if search.strip() and not trades.empty:
        needle = search.strip().lower()
        mask = trades.astype(str).apply(lambda col: col.str.lower().str.contains(needle, na=False))
        trades = trades[mask.any(axis=1)]
        monitored = _filter_monitor_rows_for_trades(trades, monitored)
    if action_filter.strip().lower() != "all" and not monitored.empty and "monitor_action" in monitored.columns:
        wanted = action_filter.strip().upper()
        monitored = monitored[monitored["monitor_action"].astype(str).str.upper() == wanted]
        if not trades.empty:
            visible_trade_ids = {resolve_trade_identifier(row) for row in monitored.to_dict(orient="records")}
            trade_ids = trades.apply(resolve_trade_identifier, axis=1)
            trades = trades.loc[trade_ids.isin(visible_trade_ids)]
    total = len(trades)
    trades = trades.iloc[offset: offset + limit]
    monitored = _filter_monitor_rows_for_trades(trades, monitored)
    order_events = get_order_events_snapshot(db, current_user, limit=30)
    latest_lookup = _build_latest_order_lookup(order_events)
    serialized_trades = serialize_dataframe(trades)
    serialized_monitored = serialize_dataframe(monitored)
    serialized_trades, serialized_monitored = _attach_trade_events_to_open_and_monitor_rows(
        serialized_trades,
        serialized_monitored,
        latest_lookup,
    )
    return {
        "open_trades": serialized_trades,
        "monitor": serialized_monitored,
        "count": int(len(trades)),
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "action_filter": action_filter,
        "order_events": order_events,
    }


def get_trade_journal(
    limit: int = 100,
    offset: int = 0,
    search: str = "",
    result_filter: str = "all",
    direction_filter: str = "all",
    attribution_filter: str = "all",
    current_user: Any | None = None,
) -> dict[str, Any]:
    journal = _load_trade_journal_frame(limit=limit, offset=offset, current_user=current_user)
    if search.strip() and not journal.empty:
        needle = search.strip().lower()
        mask = journal.astype(str).apply(lambda col: col.str.lower().str.contains(needle, na=False))
        journal = journal[mask.any(axis=1)]
    if not journal.empty and direction_filter.strip().lower() != "all" and "direction" in journal.columns:
        wanted_direction = direction_filter.strip().upper()
        journal = journal[journal["direction"].astype(str).str.upper() == wanted_direction]
    if not journal.empty and result_filter.strip().lower() != "all":
        pnl_series = pd.to_numeric(journal.get("pnl_dollars", journal.get("realized_pnl", 0)), errors="coerce").fillna(0.0)
        if result_filter.strip().lower() == "win":
            journal = journal[pnl_series >= 0]
        elif result_filter.strip().lower() == "loss":
            journal = journal[pnl_series < 0]
    if not journal.empty and attribution_filter.strip().lower() != "all" and "attribution_key" in journal.columns:
        wanted_attribution = attribution_filter.strip().lower()
        attribution_groups = {
            "execution": {"thesis_right_execution_wrong", "execution_drift"},
            "thesis": {"thesis_wrong_execution_fine", "thesis_miss"},
            "risk": {"sizing_wrong", "rule_review"},
            "clean": {"clean_win"},
            "flat": {"flat_review"},
        }
        allowed = attribution_groups.get(wanted_attribution, {wanted_attribution})
        journal = journal[journal["attribution_key"].astype(str).str.lower().isin(allowed)]
    total = len(journal)
    journal = journal.iloc[offset: offset + limit]
    replay = sdm.build_trade_replay(journal) if not journal.empty else pd.DataFrame()
    return {
        "journal": serialize_dataframe(journal),
        "replay": serialize_dataframe(replay),
        "validation_snapshot": _build_validation_snapshot(journal, current_user=current_user),
        "count": int(len(journal)),
        "total": int(total),
        "limit": int(limit),
        "offset": int(offset),
        "result_filter": result_filter,
        "direction_filter": direction_filter,
        "attribution_filter": attribution_filter,
    }


def get_portfolio(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
) -> dict[str, Any]:
    broker_pending_sync = {"synced": False, "summary": {"processed": 0, "changed": 0}}
    if db is not None and current_user is not None:
        broker_pending_sync = sync_pending_orders_from_broker(db=db, current_user=current_user)

    broker_account = _build_broker_account_snapshot()
    broker_reconciliation = _reconcile_local_broker_paper_state(
        current_user=current_user,
        broker_account=broker_account,
    )

    open_trades = filter_frame_to_current_user(sdm.read_open_trades(), current_user)
    pending_orders = filter_frame_to_current_user(sdm.read_pending_orders(), current_user)
    closed_trades = filter_frame_to_current_user(sdm.read_closed_trades(), current_user)
    normalized_closed_trades = _normalize_closed_trades_for_journal(closed_trades)
    monitored = filter_frame_to_current_user(sdm.monitor_open_trades(), current_user)
    order_events = get_order_events_snapshot(db, current_user, limit=40)
    latest_lookup = _build_latest_order_lookup(order_events)
    serialized_open_trades = serialize_dataframe(open_trades)
    serialized_monitored = serialize_dataframe(monitored)
    serialized_pending_orders = serialize_dataframe(pending_orders)
    serialized_open_trades, serialized_monitored = _attach_trade_events_to_open_and_monitor_rows(
        serialized_open_trades,
        serialized_monitored,
        latest_lookup,
    )
    serialized_pending_orders = _attach_latest_order_events_to_rows(serialized_pending_orders, latest_lookup)
    return {
        "summary": serialize_value(sdm.portfolio_summary(open_trades, closed_trades)),
        "trade_summary": serialize_value(sdm.trade_summary(closed_trades)),
        "attribution_summary": serialize_value(_build_attribution_summary(normalized_closed_trades)),
        "validation_snapshot": _build_validation_snapshot(normalized_closed_trades, current_user=current_user),
        "capital_preservation": _build_capital_preservation_snapshot(open_trades, pending_orders, closed_trades),
        "analytics": serialize_value(sdm.performance_analytics(closed_trades)),
        "risk_dashboard": serialize_value(sdm.open_risk_dashboard(monitored, account_size=10000.0)),
        "open_trades": serialized_open_trades,
        "pending_orders": serialized_pending_orders,
        "closed_trades": serialize_dataframe(closed_trades),
        "monitored_open_trades": serialized_monitored,
        "order_events": order_events,
        "broker_account": broker_account,
        "broker_reconciliation": broker_reconciliation,
        "broker_pending_sync": serialize_value(broker_pending_sync.get("summary") or {}),
    }


def get_portfolio_dashboard_snapshot(
    *,
    db: Session | None = None,
    current_user: Any | None = None,
    account_profile: str | None = None,
    linked_account_id: str | None = None,
) -> dict[str, Any]:
    broker_pending_sync = {"synced": False, "summary": {"processed": 0, "changed": 0}}
    if db is not None and current_user is not None:
        broker_pending_sync = sync_pending_orders_from_broker(db=db, current_user=current_user)

    broker_account = _build_broker_account_snapshot(
        db=db,
        current_user=current_user,
        account_profile=account_profile,
        linked_account_id=linked_account_id,
    )
    broker_reconciliation = _reconcile_local_broker_paper_state(
        current_user=current_user,
        broker_account=broker_account,
    )

    open_trades = filter_frame_to_current_user(sdm.read_open_trades(), current_user)
    pending_orders = filter_frame_to_current_user(sdm.read_pending_orders(), current_user)
    closed_trades = filter_frame_to_current_user(sdm.read_closed_trades(), current_user)
    normalized_closed_trades = _normalize_closed_trades_for_journal(closed_trades)
    monitored = filter_frame_to_current_user(sdm.monitor_open_trades(), current_user)
    order_events = get_order_events_snapshot(db, current_user, limit=20)
    latest_lookup = _build_latest_order_lookup(order_events)
    serialized_open_trades = serialize_dataframe(open_trades)
    serialized_monitored = serialize_dataframe(monitored)
    serialized_pending_orders = serialize_dataframe(pending_orders)
    serialized_open_trades, serialized_monitored = _attach_trade_events_to_open_and_monitor_rows(
        serialized_open_trades,
        serialized_monitored,
        latest_lookup,
    )
    serialized_pending_orders = _attach_latest_order_events_to_rows(serialized_pending_orders, latest_lookup)
    return {
        "summary": serialize_value(sdm.portfolio_summary(open_trades, closed_trades)),
        "trade_summary": serialize_value(sdm.trade_summary(closed_trades)),
        "attribution_summary": serialize_value(_build_attribution_summary(normalized_closed_trades)),
        "validation_snapshot": _build_validation_snapshot(normalized_closed_trades, current_user=current_user),
        "capital_preservation": _build_capital_preservation_snapshot(open_trades, pending_orders, closed_trades),
        "open_trades": serialized_open_trades,
        "pending_orders": serialized_pending_orders,
        "monitored_open_trades": serialized_monitored,
        "order_events": order_events,
        "broker_account": broker_account,
        "broker_reconciliation": broker_reconciliation,
        "broker_pending_sync": serialize_value(broker_pending_sync.get("summary") or {}),
    }


def get_portfolio_equity_curve(*, current_user: Any | None = None) -> dict[str, Any]:
    closed_trades = filter_frame_to_current_user(sdm.read_closed_trades(), current_user)
    equity = sdm.equity_curve(closed_trades) if not closed_trades.empty else pd.DataFrame()
    return {
        "points": serialize_dataframe(equity),
        "count": int(len(equity)),
    }



def get_portfolio_performance(*, current_user: Any | None = None) -> dict[str, Any]:
    closed_trades = filter_frame_to_current_user(sdm.read_closed_trades(), current_user)
    if closed_trades.empty:
        return {
            "monthly": [],
            "streaks": {"current": 0, "best_win": 0, "worst_loss": 0},
            "expectancy": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "profit_factor": 0.0,
            "trade_count": 0,
        }

    df = closed_trades.copy()
    pnl_col = 'pnl_dollars' if 'pnl_dollars' in df.columns else 'realized_pnl'
    pnl = pd.to_numeric(df.get(pnl_col, 0), errors='coerce').fillna(0.0)

    time_col = None
    for candidate in ('close_time', 'exit_time', 'closed_at', 'timestamp'):
        if candidate in df.columns:
            time_col = candidate
            break
    if time_col is not None:
        timestamps = pd.to_datetime(df[time_col], errors='coerce')
    else:
        timestamps = pd.Series(pd.date_range(end=pd.Timestamp.utcnow(), periods=len(df), freq='D'))

    perf = pd.DataFrame({'timestamp': timestamps, 'pnl': pnl}).dropna(subset=['timestamp']).sort_values('timestamp')
    perf['month'] = perf['timestamp'].dt.to_period('M').astype(str)
    monthly = perf.groupby('month', as_index=False).agg(
        pnl=('pnl', 'sum'),
        trades=('pnl', 'size'),
        wins=('pnl', lambda s: int((s >= 0).sum())),
        losses=('pnl', lambda s: int((s < 0).sum())),
    )
    monthly_rows = monthly.to_dict(orient='records')

    signs = [1 if value >= 0 else -1 for value in perf['pnl'].tolist()]
    current = 0
    best_win = 0
    worst_loss = 0
    prev = None
    run = 0
    for sign in signs:
        if sign == prev:
            run += sign
        else:
            run = sign
            prev = sign
        current = run
        best_win = max(best_win, run if run > 0 else 0)
        worst_loss = min(worst_loss, run if run < 0 else 0)

    wins = pnl[pnl >= 0]
    losses = pnl[pnl < 0]
    average_win = float(wins.mean()) if not wins.empty else 0.0
    average_loss = float(losses.mean()) if not losses.empty else 0.0
    win_rate = float((pnl >= 0).mean()) if len(pnl) else 0.0
    loss_rate = float((pnl < 0).mean()) if len(pnl) else 0.0
    expectancy = (win_rate * average_win) + (loss_rate * average_loss)
    gross_profit = float(wins.sum()) if not wins.empty else 0.0
    gross_loss = abs(float(losses.sum())) if not losses.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else gross_profit

    return {
        "monthly": serialize_value(monthly_rows),
        "streaks": {
            "current": int(current),
            "best_win": int(best_win),
            "worst_loss": int(abs(worst_loss)),
        },
        "expectancy": round(float(expectancy), 2),
        "average_win": round(float(average_win), 2),
        "average_loss": round(float(average_loss), 2),
        "profit_factor": round(float(profit_factor), 2),
        "trade_count": int(len(pnl)),
    }
