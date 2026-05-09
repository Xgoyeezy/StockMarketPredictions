from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median, pstdev
from typing import Any, Iterable

from backend.services.evidence_reward_engine import get_evidence_reward_summary
from backend.services.productized_control_plane_service import execution_quality_summary
from backend.services.serialization import serialize_value

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "mutation": "none",
    "writes_execution_config": False,
    "writes_broker_config": False,
    "writes_risk_config": False,
    "writes_ranking_config": False,
}

SAFETY_NOTES: tuple[str, ...] = (
    "Research only. Does not affect trading.",
    "Paper-route evidence only.",
    "Does not place orders.",
    "Does not change order routing.",
    "Does not change broker routes.",
    "Does not bypass risk gates.",
    "Does not change ranking weights automatically.",
    "Does not grant AI order authority.",
)

TERMINAL_MISSED_STATUSES = {"missed", "no_fill", "rejected", "canceled", "cancelled", "expired"}
PARTIAL_STATUSES = {"partial", "partially_filled", "partially filled"}
SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "apikey", "access_key", "private_key", "account_id")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")):
        return None
    return parsed


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on", "filled", "partial", "partially_filled"}:
            return True
        if cleaned in {"0", "false", "no", "off", "none"}:
            return False
    return bool(value)


def _mean(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(sum(clean) / len(clean), 6) if clean else None


def _median(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    return round(float(median(clean)), 6) if clean else None


def _dispersion(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in (_safe_float(item) for item in values) if value is not None]
    if not clean:
        return None
    if len(clean) == 1:
        return 0.0
    return round(float(pstdev(clean)), 6)


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def _parse_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _seconds_between(start: Any, end: Any) -> float | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if not start_dt or not end_dt:
        return None
    return round(max(0.0, (end_dt - start_dt).total_seconds()), 6)


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple) or isinstance(value, set):
        return list(value)
    return [value]


def _nested_sources(row: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [row]
    for key in ("payload", "paper_trade_outcome", "execution", "order", "fill", "candidate", "prediction_contract", "reward_components"):
        value = row.get(key)
        if isinstance(value, dict):
            sources.append(value)
    return sources


def _first_value(row: dict[str, Any], fields: Iterable[str]) -> Any:
    for source in _nested_sources(row):
        for field in fields:
            value = source.get(field)
            if value is not None and value != "":
                return value
    return None


def _first_number(row: dict[str, Any], fields: Iterable[str]) -> float | None:
    for field in fields:
        value = _safe_float(_first_value(row, (field,)))
        if value is not None:
            return value
    return None


def _first_text(row: dict[str, Any], fields: Iterable[str], fallback: str = "") -> str:
    for field in fields:
        value = _first_value(row, (field,))
        if value is not None and str(value).strip():
            return str(value).strip()
    return fallback


def _looks_like_local_path(value: str) -> bool:
    cleaned = value.strip()
    return (len(cleaned) >= 3 and cleaned[1:3] in {":\\", ":/"}) or cleaned.startswith("\\\\")


def _sanitize_value(value: Any, *, key: str = "") -> Any:
    key_lower = key.lower()
    if any(marker in key_lower for marker in SECRET_KEY_MARKERS):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): _sanitize_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, tuple) or isinstance(value, set):
        return [_sanitize_value(item, key=key) for item in value]
    if isinstance(value, str) and _looks_like_local_path(value):
        return "[local_path_redacted]"
    return value


def _is_paper_route(row: dict[str, Any]) -> bool:
    text = " ".join(
        str(_first_value(row, fields) or "")
        for fields in (
            ("route", "route_state", "broker", "execution_route", "paper_route", "mode"),
            ("source",),
        )
    ).lower()
    if "live" in text and "paper" not in text:
        return False
    if "paper" in text or "alpaca" in text or "internal" in text or "broker_paper" in text:
        return True
    # Existing local snapshots often omit route detail; keep them only as paper-only analytics rows.
    return not text.strip()


def compute_slippage_bps(intended_price: Any, fill_price: Any, explicit_slippage: Any = None) -> float | None:
    explicit = _safe_float(explicit_slippage)
    if explicit is not None:
        return round(explicit, 6)
    intended = _safe_float(intended_price)
    fill = _safe_float(fill_price)
    if intended is None or fill is None or intended <= 0:
        return None
    return round(((fill - intended) / intended) * 10000.0, 6)


def compute_spread_cost_bps(spread_at_signal: Any) -> float | None:
    spread = _safe_float(spread_at_signal)
    if spread is None:
        return None
    return round(max(0.0, spread), 6)


def compute_fill_delay_seconds(row: dict[str, Any]) -> float | None:
    explicit = _first_number(row, ("fill_delay_seconds", "time_to_fill", "time_to_fill_seconds"))
    if explicit is not None:
        return round(max(0.0, explicit), 6)
    latency_ms = _first_number(row, ("latency_ms", "fill_delay_ms"))
    if latency_ms is not None:
        return round(max(0.0, latency_ms) / 1000.0, 6)
    return _seconds_between(
        _first_value(row, ("submitted_at", "created_at", "timestamp", "order_submitted_at")),
        _first_value(row, ("filled_at", "completed_at", "order_filled_at")),
    )


def compute_alpha_decay(row: dict[str, Any]) -> float | None:
    explicit = _first_number(row, ("alpha_decay", "alpha_decay_pct"))
    if explicit is not None:
        return round(explicit, 6)
    alpha_at_signal = _first_number(row, ("alpha_at_signal", "expected_alpha", "expected_move_pct", "predicted_target_pct"))
    alpha_after_fill = _first_number(row, ("alpha_after_fill", "post_fill_alpha", "actual_forward_return", "paper_return_pct"))
    if alpha_at_signal is not None and alpha_after_fill is not None:
        return round(alpha_at_signal - alpha_after_fill, 6)
    expected_return = _first_number(row, ("expected_forward_return", "expected_return_pct"))
    actual_return = _first_number(row, ("actual_forward_return", "realized_return", "paper_return_pct"))
    if expected_return is not None and actual_return is not None:
        return round(expected_return - actual_return, 6)
    return None


def compute_execution_adjusted_reward(row: dict[str, Any], slippage_bps: float | None, spread_bps: float | None) -> float | None:
    explicit = _first_number(row, ("execution_adjusted_reward", "slippage_adjusted_reward"))
    if explicit is not None:
        return round(explicit, 6)
    reward = _first_number(row, ("total_reward", "reward", "actual_forward_return", "realized_return", "paper_return_pct"))
    if reward is None:
        return None
    adjusted = reward
    if slippage_bps is not None:
        adjusted -= abs(slippage_bps) / 100.0
    if spread_bps is not None:
        adjusted -= max(0.0, spread_bps) / 100.0
    return round(adjusted, 6)


def normalize_execution_quality_record(row: dict[str, Any], index: int = 0) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not _is_paper_route(row):
        return None
    intended_price = _first_number(row, ("intended_price", "expected_entry_price", "expected_price", "submitted_price", "limit_price"))
    fill_price = _first_number(row, ("actual_fill_price", "fill_price", "filled_price", "broker_filled_avg_price", "filled_avg_price"))
    spread_at_signal = _first_number(row, ("spread_at_signal", "spread_bps", "bid_ask_spread_bps"))
    explicit_slippage = _first_number(row, ("slippage", "slippage_bps", "fill_slippage_bps"))
    slippage = compute_slippage_bps(intended_price, fill_price, explicit_slippage)
    spread_cost = compute_spread_cost_bps(spread_at_signal)
    fill_delay = compute_fill_delay_seconds(row)
    alpha_decay = compute_alpha_decay(row)
    execution_adjusted_reward = compute_execution_adjusted_reward(row, slippage, spread_cost)
    baseline_return = _first_number(row, ("baseline_forward_return", "baseline_return"))
    actual_return = _first_number(row, ("actual_forward_return", "realized_return", "paper_return_pct"))
    cost_adjusted_edge = None
    if actual_return is not None and baseline_return is not None:
        raw_edge = actual_return - baseline_return
        cost_drag = (abs(slippage or 0.0) + max(0.0, spread_cost or 0.0)) / 100.0
        cost_adjusted_edge = round(raw_edge - cost_drag, 6)
    status = _first_text(row, ("status", "route_state", "paper_fill_status"), "").lower()
    quantity = _first_number(row, ("quantity", "qty", "submitted_qty"))
    filled_quantity = _first_number(row, ("filled_quantity", "filled_qty"))
    partial_fill = _safe_bool(_first_value(row, ("partial_fill",)), False) or status in PARTIAL_STATUSES
    if quantity is not None and filled_quantity is not None and 0 < filled_quantity < quantity:
        partial_fill = True
    missed_fill = _safe_bool(_first_value(row, ("missed_fill",)), False) or status in TERMINAL_MISSED_STATUSES
    if fill_price is None and status in TERMINAL_MISSED_STATUSES:
        missed_fill = True
    quote_freshness = _first_number(row, ("quote_freshness", "quote_freshness_seconds", "quote_age_seconds", "quote_age"))
    liquidity_score = _first_number(row, ("liquidity_score",))
    warnings: list[str] = []
    missing_fields: list[str] = []
    for field, value in (
        ("intended_price", intended_price),
        ("fill_price", fill_price),
        ("spread_at_signal", spread_at_signal),
        ("slippage", slippage),
        ("fill_delay_seconds", fill_delay),
    ):
        if value is None:
            missing_fields.append(field)
    if quote_freshness is not None and quote_freshness > 60:
        warnings.append("Quote was stale at signal/fill review time.")
    if spread_at_signal is not None and spread_at_signal > 25:
        warnings.append("Spread at signal was wide.")
    if liquidity_score is not None and liquidity_score < 0.4:
        warnings.append("Liquidity score was weak.")
    if missed_fill:
        warnings.append("Order evidence indicates a missed fill.")
    if partial_fill:
        warnings.append("Order evidence indicates a partial fill.")
    if fill_price is None and not missed_fill:
        warnings.append("Fill price is missing, so full TCA could not be computed.")
    normalized = {
        "trade_id": _first_text(row, ("trade_id",), "") or None,
        "order_id": _first_text(row, ("order_id", "order_event_id", "broker_order_id", "id"), f"order-{index + 1}"),
        "linked_candidate_id": _first_text(row, ("linked_candidate_id", "candidate_lifecycle_id", "automation_candidate_id"), "") or None,
        "symbol": _first_text(row, ("symbol", "ticker"), "unknown").upper(),
        "timestamp": _first_text(row, ("timestamp", "created_at", "submitted_at", "filled_at"), ""),
        "engine": _first_text(row, ("engine", "desk_key", "strategy_desk_key"), "unknown"),
        "setup_type": _first_text(row, ("setup_type", "opportunity_type"), "unknown"),
        "regime": _first_text(row, ("regime", "market_regime", "regime_state"), "unknown"),
        "route": _first_text(row, ("route", "execution_route", "route_state", "broker"), "broker_paper"),
        "paper_only": True,
        "intended_price": intended_price,
        "fill_price": fill_price,
        "spread_at_signal": spread_at_signal,
        "expected_entry_price": intended_price,
        "actual_fill_price": fill_price,
        "slippage": slippage,
        "slippage_bps": slippage,
        "fill_delay_seconds": fill_delay,
        "time_to_fill": fill_delay,
        "partial_fill": partial_fill,
        "missed_fill": missed_fill,
        "alpha_decay": alpha_decay,
        "execution_adjusted_reward": execution_adjusted_reward,
        "spread_cost": spread_cost,
        "cost_adjusted_edge": cost_adjusted_edge,
        "quote_freshness": quote_freshness,
        "liquidity_warning": bool(warnings),
        "warnings": warnings,
        "missing_fields": sorted(set(missing_fields + [str(item) for item in _listify(row.get("missing_fields"))])),
    }
    return _sanitize_value(normalized)


def normalize_execution_quality_records(records: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    rows = []
    for index, row in enumerate(records or []):
        normalized = normalize_execution_quality_record(row, index)
        if normalized is not None:
            rows.append(normalized)
    return rows


def _group_metric(rows: list[dict[str, Any]], key: str, value_key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    items = []
    for label, group in grouped.items():
        values = [row.get(value_key) for row in group]
        items.append(
            {
                key: label,
                "count": len(group),
                "average": _mean(values),
                "median": _median(values),
                "dispersion": _dispersion(values),
                "missing_count": sum(1 for row in group if row.get(value_key) is None),
            }
        )
    return sorted(items, key=lambda item: (item["average"] is None, item["average"] if item["average"] is not None else 9999, -item["count"]))


def _group_setup_reward(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key) or "unknown")].append(row)
    items = []
    for label, group in grouped.items():
        items.append(
            {
                key: label,
                "count": len(group),
                "execution_adjusted_reward": _mean(row.get("execution_adjusted_reward") for row in group),
                "spread_cost": _mean(row.get("spread_cost") for row in group),
                "average_slippage": _mean(row.get("slippage") for row in group),
                "missed_fill_rate": _ratio(sum(1 for row in group if row.get("missed_fill")), len(group)),
                "partial_fill_rate": _ratio(sum(1 for row in group if row.get("partial_fill")), len(group)),
            }
        )
    return sorted(items, key=lambda item: (item["execution_adjusted_reward"] is None, -(item["execution_adjusted_reward"] or -9999), -item["count"]))


def compute_execution_quality_aggregations(records: list[dict[str, Any]]) -> dict[str, Any]:
    slippages = [row.get("slippage") for row in records if row.get("slippage") is not None]
    fill_delays = [row.get("fill_delay_seconds") for row in records if row.get("fill_delay_seconds") is not None]
    missed_count = sum(1 for row in records if row.get("missed_fill"))
    partial_count = sum(1 for row in records if row.get("partial_fill"))
    cost_penalty = _mean((abs(row.get("slippage") or 0.0) + max(0.0, row.get("spread_cost") or 0.0)) for row in records if row.get("slippage") is not None or row.get("spread_cost") is not None)
    fill_delay_penalty = min((_mean(fill_delays) or 0.0) / 60.0, 30.0) if fill_delays else 0.0
    execution_quality_score = round(
        max(
            0.0,
            100.0
            - min(float(cost_penalty or 0.0), 50.0)
            - min(fill_delay_penalty, 30.0)
            - (missed_count / len(records) * 25.0 if records else 0.0)
            - (partial_count / len(records) * 10.0 if records else 0.0),
        ),
        2,
    )
    return {
        "average_slippage": _mean(slippages),
        "median_slippage": _median(slippages),
        "slippage_by_engine": _group_metric(records, "engine", "slippage"),
        "slippage_by_setup_type": _group_metric(records, "setup_type", "slippage"),
        "slippage_by_symbol": _group_metric(records, "symbol", "slippage"),
        "slippage_by_regime": _group_metric(records, "regime", "slippage"),
        "fill_delay_by_engine": _group_metric(records, "engine", "fill_delay_seconds"),
        "alpha_decay_by_engine": _group_metric(records, "engine", "alpha_decay"),
        "execution_adjusted_reward_by_setup": _group_setup_reward(records, "setup_type"),
        "spread_cost_by_setup": _group_setup_reward(records, "setup_type"),
        "missed_fill_rate": _ratio(missed_count, len(records)),
        "partial_fill_rate": _ratio(partial_count, len(records)),
        "execution_quality_score": execution_quality_score if records else None,
    }


def _load_runtime_rows(db: Any = None, current_user: Any = None) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    try:
        report = execution_quality_summary(db, current_user=current_user)
        rows.extend(list(report.get("rows") or []))
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Execution snapshot source unavailable: {exc.__class__.__name__}.")
    try:
        reward_report = get_evidence_reward_summary(db, current_user=current_user)
        for row in list(reward_report.get("records") or reward_report.get("candidate_rows") or []):
            if not isinstance(row, dict):
                continue
            has_execution_fields = any(
                _first_value(row, fields) is not None
                for fields in (
                    ("fill_price", "filled_price", "actual_fill_price"),
                    ("slippage_bps", "fill_slippage_bps"),
                    ("spread_bps", "spread_at_signal"),
                    ("paper_trade_outcome",),
                )
            )
            if has_execution_fields:
                rows.append(row)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        warnings.append(f"Evidence Reward execution source unavailable: {exc.__class__.__name__}.")
    return rows, warnings


def build_execution_quality_tca_report(
    *,
    records: Iterable[dict[str, Any]] | None = None,
    db: Any = None,
    current_user: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    source_warnings: list[str] = []
    if records is None:
        records, source_warnings = _load_runtime_rows(db, current_user)
    normalized = normalize_execution_quality_records(records)
    aggregations = compute_execution_quality_aggregations(normalized)
    missing_counter: Counter[str] = Counter()
    for row in normalized:
        missing_counter.update(row.get("missing_fields") or [])
    status = "ready" if normalized else "empty"
    warnings = [*source_warnings]
    if missing_counter:
        warnings.append("Some paper execution rows are missing fields required for complete TCA.")
    if any(row.get("liquidity_warning") for row in normalized):
        warnings.append("Liquidity, spread, quote freshness, missed-fill, or partial-fill warnings were observed.")
    summary = {
        "status": status,
        "trade_count": len(normalized),
        "paper_only": True,
        "average_slippage": aggregations.get("average_slippage"),
        "median_slippage": aggregations.get("median_slippage"),
        "average_fill_delay_seconds": _mean(row.get("fill_delay_seconds") for row in normalized),
        "average_alpha_decay": _mean(row.get("alpha_decay") for row in normalized),
        "average_execution_adjusted_reward": _mean(row.get("execution_adjusted_reward") for row in normalized),
        "average_spread_cost": _mean(row.get("spread_cost") for row in normalized),
        "average_cost_adjusted_edge": _mean(row.get("cost_adjusted_edge") for row in normalized),
        "missed_fill_rate": aggregations.get("missed_fill_rate"),
        "partial_fill_rate": aggregations.get("partial_fill_rate"),
        "execution_quality_score": aggregations.get("execution_quality_score"),
        **SAFETY_FLAGS,
    }
    return serialize_value(
        {
            "status": status,
            "generated_at": generated_at or _utc_now(),
            "research_only": True,
            "paper_only": True,
            "summary": summary,
            "records": normalized[:250],
            "aggregations": aggregations,
            "warnings": list(dict.fromkeys(warnings)),
            "missing_fields": dict(missing_counter),
            "safety_notes": list(SAFETY_NOTES),
            **SAFETY_FLAGS,
        }
    )


def _subset(report: dict[str, Any], *, records: list[dict[str, Any]], aggregations: dict[str, Any]) -> dict[str, Any]:
    return serialize_value({**report, "records": records, "aggregations": aggregations, "research_only": True, "paper_only": True, "safety_notes": list(SAFETY_NOTES), **SAFETY_FLAGS})


def get_execution_quality_tca_summary(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    return build_execution_quality_tca_report(db=db, current_user=current_user)


def get_execution_quality_tca_trades(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_execution_quality_tca_report(db=db, current_user=current_user)
    return _subset(report, records=report.get("records", []), aggregations=report.get("aggregations", {}))


def get_execution_quality_tca_slippage(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_execution_quality_tca_report(db=db, current_user=current_user)
    records = [row for row in report.get("records", []) if row.get("slippage") is not None]
    return _subset(
        report,
        records=records,
        aggregations={
            "average_slippage": report.get("aggregations", {}).get("average_slippage"),
            "median_slippage": report.get("aggregations", {}).get("median_slippage"),
            "slippage_by_engine": report.get("aggregations", {}).get("slippage_by_engine", []),
            "slippage_by_setup_type": report.get("aggregations", {}).get("slippage_by_setup_type", []),
            "slippage_by_symbol": report.get("aggregations", {}).get("slippage_by_symbol", []),
            "slippage_by_regime": report.get("aggregations", {}).get("slippage_by_regime", []),
        },
    )


def get_execution_quality_tca_alpha_decay(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_execution_quality_tca_report(db=db, current_user=current_user)
    records = [row for row in report.get("records", []) if row.get("alpha_decay") is not None]
    return _subset(report, records=records, aggregations={"alpha_decay_by_engine": report.get("aggregations", {}).get("alpha_decay_by_engine", [])})


def get_execution_quality_tca_engines(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_execution_quality_tca_report(db=db, current_user=current_user)
    return _subset(
        report,
        records=report.get("aggregations", {}).get("slippage_by_engine", []),
        aggregations={
            "slippage_by_engine": report.get("aggregations", {}).get("slippage_by_engine", []),
            "fill_delay_by_engine": report.get("aggregations", {}).get("fill_delay_by_engine", []),
            "alpha_decay_by_engine": report.get("aggregations", {}).get("alpha_decay_by_engine", []),
        },
    )


def get_execution_quality_tca_setups(db: Any = None, *, current_user: Any = None) -> dict[str, Any]:
    report = build_execution_quality_tca_report(db=db, current_user=current_user)
    return _subset(
        report,
        records=report.get("aggregations", {}).get("execution_adjusted_reward_by_setup", []),
        aggregations={
            "execution_adjusted_reward_by_setup": report.get("aggregations", {}).get("execution_adjusted_reward_by_setup", []),
            "spread_cost_by_setup": report.get("aggregations", {}).get("spread_cost_by_setup", []),
        },
    )
