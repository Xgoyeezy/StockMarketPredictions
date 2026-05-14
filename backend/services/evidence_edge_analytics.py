from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend import stock_direction_model as sdm
from backend.services.project_finish_tracker import build_project_finish_tracker
from backend.services.serialization import serialize_value

SAFETY_FLAGS: dict[str, Any] = {
    "research_only": True,
    "paper_route_only": True,
    "can_submit_orders": False,
    "can_submit_live_orders": False,
    "mutation": "none",
}

FALSE_BLOCK_RETURN_THRESHOLD_PCT = 0.25
DEFAULT_ROOT = Path(".")


@dataclass(frozen=True)
class EvidenceRecord:
    candidate_lifecycle_id: str | None
    symbol: str | None
    timestamp: str | None
    engine: str
    setup_type: str
    score: float | None
    score_bucket: str
    blockers: tuple[str, ...]
    ai_verdict: str | None
    route: str | None
    regime: str | None
    allowed: bool
    blocked: bool
    forward_returns: dict[str, float | None]
    forward_return_pct: float | None
    paper_trade_outcome: dict[str, Any] | None
    missed_move_outcome: dict[str, Any] | None
    missing_fields: tuple[str, ...]
    source: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return float(parsed)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        cleaned = value.strip().lower()
        if cleaned in {"1", "true", "yes", "on", "allowed", "eligible"}:
            return True
        if cleaned in {"0", "false", "no", "off", "blocked", "rejected"}:
            return False
    return bool(value)


def _clean_text(value: Any, default: str | None = None) -> str | None:
    text = str(value or "").strip()
    return text or default


def _score_bucket(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 90:
        return "90_100"
    if score >= 80:
        return "80_89"
    if score >= 60:
        return "60_79"
    if score >= 40:
        return "40_59"
    return "0_39"


def _confidence_bucket(count: int) -> str:
    if count >= 50:
        return "high"
    if count >= 20:
        return "medium"
    if count >= 5:
        return "low"
    return "insufficient"


def _latest_files(files: Iterable[Path], *, limit: int = 60) -> list[Path]:
    rows: list[tuple[float, Path]] = []
    for path in files:
        try:
            rows.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(rows, key=lambda item: item[0], reverse=True)[:limit]]


def _read_jsonl(path: Path, *, max_rows: int = 5000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if len(rows) >= max_rows:
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    payload = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except OSError:
        return []
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _count_lines(path: Path, *, max_lines: int | None = None) -> int:
    try:
        with path.open("r", encoding="utf-8") as handle:
            count = 0
            for count, _ in enumerate(handle, start=1):
                if max_lines is not None and count >= max_lines:
                    return count
            return count
    except OSError:
        return 0


def _tenant_slug_from_user(current_user: Any) -> str:
    return (
        _clean_text(getattr(current_user, "tenant_slug", None))
        or _clean_text(getattr(current_user, "slug", None))
        or _clean_text(getattr(current_user, "tenant_id", None))
        or "systematic-equities"
    )


def _candidate_files(root: Path, tenant_slug: str) -> list[Path]:
    base = root / "runtime-exports" / "candidate-lifecycle"
    if not base.exists():
        return []
    files = list(base.glob(f"*/{tenant_slug}.jsonl"))
    if not files:
        files = list(base.glob("*/candidate-diagnostics.jsonl"))
    return _latest_files(files)


def _accelerator_files(root: Path, tenant_slug: str) -> list[Path]:
    base = root / "runtime-exports" / "evidence-accelerator"
    if not base.exists():
        return []
    return _latest_files(base.glob(f"*/{tenant_slug}.jsonl"), limit=20)


def _simulation_files(root: Path, tenant_slug: str) -> list[Path]:
    base = root / "runtime-exports" / "simulation-evidence"
    if not base.exists():
        return []
    return _latest_files(base.glob(f"*/{tenant_slug}.jsonl"), limit=20)


def _market_day_reports(root: Path) -> list[dict[str, Any]]:
    base = root / "runtime-exports" / "market-days"
    if not base.exists():
        return []
    return [_read_json(path) for path in _latest_files(base.glob("*/market-day-report.json"), limit=30)]


def _extract_blockers(payload: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    raw = payload.get("blockers")
    if isinstance(raw, list):
        values.extend(str(item).strip() for item in raw if str(item or "").strip())
    blocker = _clean_text(payload.get("blocker") or payload.get("diagnostic_blocker") or payload.get("reason"))
    if blocker and blocker.lower() not in {"none", "eligible", "allowed"}:
        values.append(blocker)
    return tuple(dict.fromkeys(values))


def _extract_score(payload: dict[str, Any]) -> float | None:
    for key in (
        "opportunity_score",
        "stage_one_score",
        "deep_score",
        "evidence_edge_score",
        "ranking_score",
        "setup_score",
        "score",
        "max_score",
    ):
        parsed = _safe_float(payload.get(key))
        if parsed is not None:
            return parsed
    nested = payload.get("opportunity_capture")
    if isinstance(nested, dict):
        return _safe_float(nested.get("score"))
    return None


def _extract_forward_returns(payload: dict[str, Any]) -> tuple[dict[str, float | None], float | None, list[str]]:
    returns: dict[str, float | None] = {"5m": None, "15m": None, "30m": None}
    missing: list[str] = []
    explicit_keys = {
        "5m": ("forward_return_5m_pct", "return_5m_pct", "move_5m_pct"),
        "15m": ("forward_return_15m_pct", "return_15m_pct", "move_15m_pct"),
        "30m": ("forward_return_30m_pct", "return_30m_pct", "move_30m_pct"),
    }
    for window, keys in explicit_keys.items():
        for key in keys:
            value = _safe_float(payload.get(key))
            if value is not None:
                returns[window] = value
                break
        follow_up = payload.get(f"after_{window}")
        if returns[window] is None and isinstance(follow_up, dict):
            returns[window] = _safe_float(follow_up.get("move_pct"))

    followup = payload.get("post_move_followup")
    if isinstance(followup, dict):
        for window in ("5m", "15m", "30m"):
            if returns[window] is None:
                nested = followup.get(f"after_{window}")
                if isinstance(nested, dict):
                    returns[window] = _safe_float(nested.get("move_pct"))
        current_move = _safe_float(followup.get("current_move_pct") or followup.get("move_pct"))
    else:
        current_move = None

    missed = payload.get("missed_move")
    if current_move is None and isinstance(missed, dict):
        current_move = _safe_float(missed.get("magnitude_pct") or missed.get("move_pct"))
    if current_move is None:
        current_move = _safe_float(payload.get("missed_move_pct") or payload.get("current_move_pct"))

    observed_returns = [value for value in returns.values() if value is not None]
    if observed_returns:
        return returns, float(observed_returns[-1]), missing
    if current_move is not None:
        return returns, float(current_move), missing
    missing.append("forward_returns")
    return returns, None, missing


def _paper_trade_return(row: dict[str, Any]) -> tuple[dict[str, Any] | None, float | None]:
    realized = _safe_float(row.get("realized_pnl") or row.get("pnl"))
    notional = _safe_float(row.get("position_cost") or row.get("broker_notional") or row.get("expected_notional"))
    entry = _safe_float(row.get("live_price_at_open") or row.get("actual_fill_price") or row.get("broker_filled_avg_price"))
    close = _safe_float(row.get("live_price_at_close") or row.get("close_price"))
    return_pct = None
    if realized is not None and notional not in (None, 0):
        return_pct = round((realized / abs(float(notional))) * 100.0, 6)
    elif entry not in (None, 0) and close is not None:
        return_pct = round(((float(close) - float(entry)) / float(entry)) * 100.0, 6)
    if realized is None and return_pct is None:
        return None, None
    return (
        {
            "realized_pnl": realized,
            "return_pct": return_pct,
            "status": row.get("status") or row.get("broker_status"),
            "closed_at": row.get("closed_at"),
        },
        return_pct,
    )


def _index_trade_rows(frames: Iterable[pd.DataFrame]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    by_candidate: dict[str, dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    for frame in frames:
        if frame is None or frame.empty:
            continue
        try:
            records = frame.to_dict(orient="records")
        except Exception:
            continue
        for row in records:
            if not isinstance(row, dict):
                continue
            all_rows.append(row)
            for key in ("candidate_lifecycle_id", "automation_candidate_id", "signal_id"):
                value = _clean_text(row.get(key))
                if value:
                    by_candidate[value] = row
    return by_candidate, all_rows


def _normalize_candidate_row(payload: dict[str, Any], trade_by_candidate: dict[str, dict[str, Any]]) -> EvidenceRecord | None:
    if (
        _safe_bool(payload.get("simulation_evidence"))
        or str(payload.get("source") or "").lower() == "simulation_evidence"
        or str(payload.get("evidence_pool") or "").lower() == "simulation_evidence"
    ):
        return None
    symbol = _clean_text(payload.get("ticker") or payload.get("symbol"))
    if symbol:
        symbol = symbol.upper()
    if symbol in {"API_DETAIL", "UNKNOWN"}:
        return None
    lifecycle_id = _clean_text(payload.get("candidate_lifecycle_id") or payload.get("automation_candidate_id"))
    final_state = str(payload.get("final_state") or payload.get("status") or "").strip().lower()
    blockers = _extract_blockers(payload)
    allowed = bool(final_state == "eligible" or _safe_bool(payload.get("allowed")) or _safe_bool(payload.get("eligible")))
    blocked = bool(blockers or final_state in {"rejected_or_waiting", "blocked", "rejected", "waiting"})
    if allowed:
        blocked = False
    score = _extract_score(payload)
    returns, forward_return, missing = _extract_forward_returns(payload)
    trade_row = trade_by_candidate.get(lifecycle_id or "")
    paper_outcome = None
    paper_return = None
    if trade_row:
        paper_outcome, paper_return = _paper_trade_return(trade_row)
    if forward_return is None and paper_return is not None:
        forward_return = paper_return
    missing_fields = list(missing)
    regime = _clean_text(payload.get("regime") or payload.get("market_regime") or payload.get("regime_state"))
    if not regime:
        missing_fields.append("regime")
    if score is None:
        missing_fields.append("score")
    if not symbol:
        missing_fields.append("symbol")
    missed_move = payload.get("missed_move") if isinstance(payload.get("missed_move"), dict) else None
    if missed_move is None and blocked and forward_return is not None:
        missed_move = {"magnitude_pct": forward_return, "source": "derived_forward_return"}
    return EvidenceRecord(
        candidate_lifecycle_id=lifecycle_id,
        symbol=symbol,
        timestamp=_clean_text(payload.get("scan_time") or payload.get("timestamp") or payload.get("observed_at")),
        engine=_clean_text(payload.get("desk_key") or payload.get("engine") or payload.get("strategy_desk_key"), "unknown") or "unknown",
        setup_type=_clean_text(
            payload.get("opportunity_type")
            or payload.get("setup_type")
            or payload.get("stage")
            or payload.get("automation_entry_reason"),
            "unknown",
        )
        or "unknown",
        score=score,
        score_bucket=_score_bucket(score),
        blockers=blockers,
        ai_verdict=_clean_text(payload.get("ai_verdict") or payload.get("ai_evidence_verdict")),
        route=_clean_text(payload.get("route") or payload.get("execution_route") or payload.get("automation_execution_intent")),
        regime=regime,
        allowed=allowed,
        blocked=blocked,
        forward_returns=returns,
        forward_return_pct=forward_return,
        paper_trade_outcome=paper_outcome,
        missed_move_outcome=missed_move,
        missing_fields=tuple(dict.fromkeys(missing_fields)),
        source=_clean_text(payload.get("source"), "candidate_lifecycle") or "candidate_lifecycle",
    )


def _trade_row_to_record(row: dict[str, Any]) -> EvidenceRecord | None:
    symbol = _clean_text(row.get("ticker") or row.get("symbol"))
    if not symbol:
        return None
    outcome, return_pct = _paper_trade_return(row)
    score = _extract_score(row)
    missing = []
    if return_pct is None:
        missing.append("forward_returns")
    regime = _clean_text(row.get("regime") or row.get("market_regime") or row.get("validation_sample_bucket"))
    if not regime:
        missing.append("regime")
    return EvidenceRecord(
        candidate_lifecycle_id=_clean_text(row.get("candidate_lifecycle_id") or row.get("automation_candidate_id")),
        symbol=symbol.upper(),
        timestamp=_clean_text(row.get("opened_at") or row.get("submitted_at") or row.get("closed_at")),
        engine=_clean_text(row.get("strategy_desk_key") or row.get("desk_key"), "unknown") or "unknown",
        setup_type=_clean_text(row.get("accuracy_pattern_key") or row.get("automation_entry_reason"), "paper_trade") or "paper_trade",
        score=score,
        score_bucket=_score_bucket(score),
        blockers=tuple(),
        ai_verdict=_clean_text(row.get("ai_verdict")),
        route=_clean_text(row.get("automation_execution_intent") or row.get("route_family") or row.get("broker_name")),
        regime=regime,
        allowed=True,
        blocked=False,
        forward_returns={"5m": None, "15m": None, "30m": None},
        forward_return_pct=return_pct,
        paper_trade_outcome=outcome,
        missed_move_outcome=None,
        missing_fields=tuple(dict.fromkeys(missing)),
        source="paper_trade_book",
    )


def _market_day_missed_records(reports: list[dict[str, Any]], trade_by_candidate: dict[str, dict[str, Any]]) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for report in reports:
        containers = [
            report.get("missed_move_leaderboard"),
            (report.get("no_trade_report") or {}).get("missed_move_leaderboard")
            if isinstance(report.get("no_trade_report"), dict)
            else None,
        ]
        for container in containers:
            if not isinstance(container, dict):
                continue
            for item in list(container.get("items") or []):
                if not isinstance(item, dict):
                    continue
                payload = {
                    **item,
                    "ticker": item.get("ticker"),
                    "blocker": item.get("blocker"),
                    "setup_type": item.get("setup_type"),
                    "opportunity_score": item.get("max_score"),
                    "final_state": "rejected_or_waiting",
                    "source": "market_day_missed_move",
                }
                record = _normalize_candidate_row(payload, trade_by_candidate)
                if record is not None:
                    records.append(record)
    return records


def _records_to_rows(records: list[EvidenceRecord]) -> list[dict[str, Any]]:
    return [serialize_value(record.__dict__) for record in records]


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _stats_for_group(rows: list[EvidenceRecord], *, key: str, label: str) -> dict[str, Any]:
    observed = [row.forward_return_pct for row in rows if row.forward_return_pct is not None]
    wins = [value for value in observed if value > 0]
    return {
        key: label,
        "candidate_count": len(rows),
        "observed_outcome_count": len(observed),
        "average_forward_return_pct": _average([float(value) for value in observed]),
        "win_rate": round(len(wins) / len(observed), 6) if observed else None,
        "best_forward_return_pct": round(max(observed), 6) if observed else None,
        "worst_forward_return_pct": round(min(observed), 6) if observed else None,
        "confidence_bucket": _confidence_bucket(len(observed)),
        "data_status": "ready" if observed else "insufficient_data",
    }


def _group_stats(records: list[EvidenceRecord], attr: str, key_name: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for row in records:
        grouped[str(getattr(row, attr) or "unknown")].append(row)
    stats = [_stats_for_group(rows, key=key_name, label=label) for label, rows in grouped.items()]
    return sorted(
        stats,
        key=lambda item: (item["data_status"] != "ready", -(item["average_forward_return_pct"] or -9999), -item["candidate_count"]),
    )


def _blocker_effectiveness(records: list[EvidenceRecord]) -> list[dict[str, Any]]:
    grouped: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for row in records:
        for blocker in row.blockers:
            grouped[blocker].append(row)
    items: list[dict[str, Any]] = []
    for blocker, rows in grouped.items():
        observed = [float(row.forward_return_pct) for row in rows if row.forward_return_pct is not None]
        avg_return = _average(observed)
        false_blocks = [value for value in observed if value >= FALSE_BLOCK_RETURN_THRESHOLD_PCT]
        estimated_value = round(-avg_return, 6) if avg_return is not None else None
        false_rate = round(len(false_blocks) / len(observed), 6) if observed else None
        if not observed:
            recommendation = "insufficient_data"
        elif estimated_value is not None and estimated_value > 0.05 and (false_rate or 0.0) < 0.3:
            recommendation = "keep_blocker_strict"
        elif estimated_value is not None and (estimated_value < -0.1 or (false_rate or 0.0) >= 0.4):
            recommendation = "review_blocker"
        else:
            recommendation = "monitor_blocker"
        items.append(
            {
                "blocker": blocker,
                "times_seen": len(rows),
                "times_blocked": sum(1 for row in rows if row.blocked),
                "observed_outcome_count": len(observed),
                "average_forward_return_after_block": avg_return,
                "win_rate_after_block": round(sum(1 for value in observed if value > 0) / len(observed), 6)
                if observed
                else None,
                "estimated_blocker_value": estimated_value,
                "false_block_rate": false_rate,
                "false_block_count": len(false_blocks),
                "confidence_bucket": _confidence_bucket(len(observed)),
                "recommendation": recommendation,
            }
        )
    return sorted(items, key=lambda item: (item["confidence_bucket"] == "insufficient", item["estimated_blocker_value"] or -9999))


def _feature_stats(records: list[EvidenceRecord]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in records:
        if row.forward_return_pct is None:
            continue
        features = [
            f"setup:{row.setup_type}",
            f"engine:{row.engine}",
            f"score_bucket:{row.score_bucket}",
        ]
        if row.regime:
            features.append(f"regime:{row.regime}")
        if row.ai_verdict:
            features.append(f"ai_verdict:{row.ai_verdict}")
        for feature in features:
            grouped[feature].append(float(row.forward_return_pct))
    rows = [
        {
            "feature": feature,
            "observed_outcome_count": len(values),
            "average_forward_return_pct": _average(values),
            "win_rate": round(sum(1 for value in values if value > 0) / len(values), 6) if values else None,
            "confidence_bucket": _confidence_bucket(len(values)),
        }
        for feature, values in grouped.items()
    ]
    positives = sorted(rows, key=lambda item: (-(item["average_forward_return_pct"] or -9999), -item["observed_outcome_count"]))[:10]
    negatives = sorted(rows, key=lambda item: ((item["average_forward_return_pct"] or 9999), -item["observed_outcome_count"]))[:10]
    return positives, negatives


def _score_bucket_outcomes(records: list[EvidenceRecord]) -> list[dict[str, Any]]:
    order = {"90_100": 0, "80_89": 1, "60_79": 2, "40_59": 3, "0_39": 4, "unknown": 5}
    rows = _group_stats(records, "score_bucket", "score_bucket")
    return sorted(rows, key=lambda item: order.get(item["score_bucket"], 99))


def _recommendations(
    *,
    blockers: list[dict[str, Any]],
    setups: list[dict[str, Any]],
    engines: list[dict[str, Any]],
    regimes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recommendations: list[dict[str, Any]] = []
    for item in blockers:
        action = item.get("recommendation")
        if action in {"keep_blocker_strict", "review_blocker"}:
            recommendations.append(
                {
                    "type": action,
                    "target": item["blocker"],
                    "basis": "blocker_effectiveness",
                    "confidence_bucket": item.get("confidence_bucket"),
                    "estimated_value": item.get("estimated_blocker_value"),
                    "detail": (
                        "This blocker appears to prevent negative forward outcomes."
                        if action == "keep_blocker_strict"
                        else "This blocker has a high false-block profile and should be reviewed manually."
                    ),
                    **SAFETY_FLAGS,
                }
            )
    for item in setups:
        avg = item.get("average_forward_return_pct")
        if item.get("confidence_bucket") == "insufficient" or avg is None:
            continue
        if avg >= 0.25:
            action = "increase_rank_weight"
        elif avg <= -0.1:
            action = "decrease_rank_weight"
        else:
            continue
        recommendations.append(
            {
                "type": action,
                "target": item["setup_type"],
                "basis": "setup_forward_return",
                "confidence_bucket": item.get("confidence_bucket"),
                "average_forward_return_pct": avg,
                "detail": "Manual review only; do not mutate ranking weights automatically.",
                **SAFETY_FLAGS,
            }
        )
    for item in engines:
        avg = item.get("average_forward_return_pct")
        if item.get("confidence_bucket") == "insufficient" or avg is None:
            continue
        if avg <= -0.15:
            recommendations.append(
                {
                    "type": "decrease_rank_weight",
                    "target": item["engine"],
                    "basis": "engine_forward_return",
                    "confidence_bucket": item.get("confidence_bucket"),
                    "average_forward_return_pct": avg,
                    "detail": "Engine underperformed in observed outcomes; review by regime before changing settings.",
                    **SAFETY_FLAGS,
                }
            )
    if not recommendations and not any(item.get("observed_outcome_count") for item in [*setups, *engines, *regimes]):
        recommendations.append(
            {
                "type": "insufficient_data",
                "target": "evidence_edge",
                "basis": "observed_forward_outcomes",
                "detail": "Collect more forward-return, missed-move, and closed paper trade outcomes before changing ranking policy.",
                **SAFETY_FLAGS,
            }
        )
    return recommendations[:25]


def _source_counts(
    *,
    candidate_files: list[Path],
    accelerator_files: list[Path],
    simulation_files: list[Path],
    market_day_reports: list[dict[str, Any]],
    open_trades: pd.DataFrame,
    closed_trades: pd.DataFrame,
    pending_orders: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "candidate_lifecycle_files": len(candidate_files),
        "candidate_lifecycle_rows": sum(_count_lines(path) for path in candidate_files),
        "evidence_accelerator_files": len(accelerator_files),
        "evidence_accelerator_rows": sum(_count_lines(path, max_lines=250000) for path in accelerator_files),
        "simulation_evidence_files": len(simulation_files),
        "simulation_evidence_rows_excluded": sum(_count_lines(path, max_lines=250000) for path in simulation_files),
        "market_day_reports": len(market_day_reports),
        "open_trade_rows": 0 if open_trades is None or open_trades.empty else int(len(open_trades)),
        "closed_trade_rows": 0 if closed_trades is None or closed_trades.empty else int(len(closed_trades)),
        "pending_order_rows": 0 if pending_orders is None or pending_orders.empty else int(len(pending_orders)),
    }


def _accelerated_blocker_frequency(files: list[Path]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for path in files:
        for row in _read_jsonl(path, max_rows=5000):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            blocker = _clean_text(metadata.get("blocker"))
            if blocker and blocker.lower() != "none":
                counts[blocker] += 1
    return dict(counts.most_common(20))


def build_evidence_edge_report(
    *,
    tenant_slug: str,
    root: Path | str = DEFAULT_ROOT,
    open_trades: pd.DataFrame | None = None,
    closed_trades: pd.DataFrame | None = None,
    pending_orders: pd.DataFrame | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    candidate_files = _candidate_files(root_path, tenant_slug)
    accelerator_files = _accelerator_files(root_path, tenant_slug)
    simulation_files = _simulation_files(root_path, tenant_slug)
    market_day_reports = _market_day_reports(root_path)
    open_frame = open_trades if open_trades is not None else sdm.read_open_trades()
    closed_frame = closed_trades if closed_trades is not None else sdm.read_closed_trades()
    pending_frame = pending_orders if pending_orders is not None else sdm.read_pending_orders()
    trade_by_candidate, trade_rows = _index_trade_rows([open_frame, closed_frame, pending_frame])

    records: list[EvidenceRecord] = []
    seen_keys: set[str] = set()
    for path in candidate_files:
        for payload in _read_jsonl(path, max_rows=10000):
            record = _normalize_candidate_row(payload, trade_by_candidate)
            if record is None:
                continue
            key = record.candidate_lifecycle_id or f"{record.symbol}:{record.timestamp}:{record.engine}:{record.setup_type}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            records.append(record)
    records.extend(_market_day_missed_records(market_day_reports, trade_by_candidate))
    for row in trade_rows:
        candidate_id = _clean_text(row.get("candidate_lifecycle_id") or row.get("automation_candidate_id"))
        if candidate_id and candidate_id in seen_keys:
            continue
        record = _trade_row_to_record(row)
        if record is not None:
            records.append(record)

    blocker_rows = _blocker_effectiveness(records)
    setup_rows = _group_stats(records, "setup_type", "setup_type")
    engine_rows = _group_stats(records, "engine", "engine")
    regime_rows = _group_stats(records, "regime", "regime")
    score_bucket_rows = _score_bucket_outcomes(records)
    positive_features, negative_features = _feature_stats(records)
    recommendations = _recommendations(
        blockers=blocker_rows,
        setups=setup_rows,
        engines=engine_rows,
        regimes=regime_rows,
    )
    missing_counter: Counter[str] = Counter()
    for row in records:
        missing_counter.update(row.missing_fields)
    blocker_frequency = {item["blocker"]: item["times_seen"] for item in blocker_rows}
    source_counts = _source_counts(
        candidate_files=candidate_files,
        accelerator_files=accelerator_files,
        simulation_files=simulation_files,
        market_day_reports=market_day_reports,
        open_trades=open_frame,
        closed_trades=closed_frame,
        pending_orders=pending_frame,
    )
    observed_outcome_count = sum(1 for row in records if row.forward_return_pct is not None)
    missed_move_count = sum(1 for row in records if row.blocked and row.forward_return_pct is not None and row.forward_return_pct >= FALSE_BLOCK_RETURN_THRESHOLD_PCT)
    summary = {
        "tenant_slug": tenant_slug,
        "generated_at": _utc_now(),
        "candidate_count": len(records),
        "allowed_count": sum(1 for row in records if row.allowed),
        "blocked_count": sum(1 for row in records if row.blocked),
        "missed_move_count": missed_move_count,
        "observed_outcome_count": observed_outcome_count,
        "blocker_frequency": blocker_frequency,
        "blocker_positive_value": {
            item["blocker"]: item["estimated_blocker_value"]
            for item in blocker_rows
            if item.get("estimated_blocker_value") is not None and item.get("estimated_blocker_value") > 0
        },
        "blocker_false_negative_rate": {
            item["blocker"]: item["false_block_rate"]
            for item in blocker_rows
            if item.get("false_block_rate") is not None
        },
        "missing_fields": dict(missing_counter),
        "source_counts": source_counts,
        "accelerated_blocker_frequency": _accelerated_blocker_frequency(accelerator_files),
        "data_status": "ready" if records else "empty",
        "next_action": (
            "Review blocker value, setup outcomes, and recommendations before making manual ranking-policy changes."
            if records
            else "Collect candidate lifecycle rows or closed paper trades before Evidence Edge can estimate blocker value."
        ),
        **SAFETY_FLAGS,
    }
    return serialize_value(
        {
            "summary": summary,
            "blocker_effectiveness": blocker_rows,
            "setup_forward_return_stats": setup_rows,
            "engine_forward_return_stats": engine_rows,
            "regime_forward_return_stats": regime_rows,
            "score_bucket_outcomes": score_bucket_rows,
            "top_positive_features": positive_features,
            "top_negative_features": negative_features,
            "recommended_ranking_adjustments": recommendations,
            "candidate_rows": _records_to_rows(records[:250]),
            "finish_tracker": build_project_finish_tracker(report_name="evidence_edge"),
            **SAFETY_FLAGS,
        }
    )


def get_evidence_edge_summary(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    return build_evidence_edge_report(tenant_slug=_tenant_slug_from_user(current_user))


def get_evidence_edge_blockers(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_edge_summary(db, current_user=current_user)
    return {
        "summary": report["summary"],
        "items": report["blocker_effectiveness"],
        "finish_tracker": report.get("finish_tracker") or build_project_finish_tracker(report_name="evidence_edge_blockers"),
        **SAFETY_FLAGS,
    }


def get_evidence_edge_setups(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_edge_summary(db, current_user=current_user)
    return {
        "summary": report["summary"],
        "items": report["setup_forward_return_stats"],
        "score_bucket_outcomes": report["score_bucket_outcomes"],
        "finish_tracker": report.get("finish_tracker") or build_project_finish_tracker(report_name="evidence_edge_setups"),
        **SAFETY_FLAGS,
    }


def get_evidence_edge_engines(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_edge_summary(db, current_user=current_user)
    return {
        "summary": report["summary"],
        "items": report["engine_forward_return_stats"],
        "regime_forward_return_stats": report["regime_forward_return_stats"],
        "finish_tracker": report.get("finish_tracker") or build_project_finish_tracker(report_name="evidence_edge_engines"),
        **SAFETY_FLAGS,
    }


def get_evidence_edge_recommendations(db: Any = None, *, current_user: Any) -> dict[str, Any]:
    report = get_evidence_edge_summary(db, current_user=current_user)
    return {
        "summary": report["summary"],
        "items": report["recommended_ranking_adjustments"],
        "top_positive_features": report["top_positive_features"],
        "top_negative_features": report["top_negative_features"],
        "finish_tracker": report.get("finish_tracker") or build_project_finish_tracker(report_name="evidence_edge_recommendations"),
        **SAFETY_FLAGS,
    }
