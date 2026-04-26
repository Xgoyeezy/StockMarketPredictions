from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from backend.services.serialization import serialize_value

MACRO_EVENTS_PATH = Path(__file__).resolve().parents[1] / "data" / "macro_events.csv"


def _parse_event_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except Exception:
        return None
    if parsed is None or pd.isna(parsed):
        return None
    if isinstance(parsed, pd.Timestamp):
        return parsed.date()
    try:
        return parsed.date()
    except Exception:
        return None


def _coerce_int(value: Any) -> int | None:
    try:
        if pd.isna(value):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _infer_macro_kind(event_name: str) -> str:
    normalized = str(event_name or "").strip().lower()
    if "fomc" in normalized or "fed" in normalized:
        return "rates"
    if "cpi" in normalized or "pce" in normalized or "inflation" in normalized:
        return "inflation"
    if "jobs" in normalized or "payroll" in normalized or "employment" in normalized:
        return "labor"
    if "gdp" in normalized:
        return "growth"
    return "macro"


def _infer_macro_impact(kind: str) -> str:
    return "high" if kind in {"rates", "inflation", "labor"} else "medium"


def load_macro_events(limit: int = 8, lookahead_days: int = 120) -> list[dict[str, Any]]:
    if not MACRO_EVENTS_PATH.exists():
        return []
    try:
        frame = pd.read_csv(MACRO_EVENTS_PATH)
    except Exception:
        return []
    if frame.empty:
        return []

    today = date.today()
    cutoff = today + timedelta(days=max(1, int(lookahead_days)))
    rows: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        event_day = _parse_event_date(row.get("event_date"))
        if event_day is None or event_day < today or event_day > cutoff:
            continue
        event_name = str(row.get("event_name") or "Macro Event").strip() or "Macro Event"
        days_until = (event_day - today).days
        kind = _infer_macro_kind(event_name)
        impact = _infer_macro_impact(kind)
        tone = "negative" if impact == "high" and days_until <= 3 else "warning" if impact == "high" else "info"
        rows.append(
            {
                "key": f"macro:{event_name}:{event_day.isoformat()}",
                "source": "macro_calendar",
                "kind": kind,
                "title": event_name,
                "ticker": "",
                "event_date": event_day.isoformat(),
                "days_until": days_until,
                "impact": impact,
                "tone": tone,
                "label": "Macro window",
                "detail": f"{event_name} is scheduled for {event_day.isoformat()}.",
            }
        )
    rows.sort(key=lambda item: (item.get("days_until", 9999), str(item.get("title") or "")))
    return rows[: max(1, int(limit))]


def _build_watchlist_calendar_item(row: dict[str, Any]) -> dict[str, Any] | None:
    ticker = str(row.get("ticker") or "").strip().upper()
    next_event_name = str(row.get("next_event_name") or "").strip()
    next_event_date = row.get("next_event_date")
    event_day = _parse_event_date(next_event_date)
    if not ticker or not next_event_name or event_day is None:
        return None
    days_until = _coerce_int(row.get("next_event_days"))
    if days_until is None:
        days_until = (event_day - date.today()).days
    event_context = dict(row.get("event_context") or {})
    trade_posture = str(event_context.get("trade_posture") or row.get("trade_posture") or "").strip().lower()
    event_severity = str(event_context.get("event_severity") or row.get("event_severity") or "").strip().lower()
    event_window_label = str(event_context.get("event_window_label") or row.get("event_window_label") or "").strip().lower()
    tone = (
        "negative"
        if trade_posture == "defer" or event_severity in {"critical", "high"}
        else "warning"
        if trade_posture == "caution" or event_severity == "medium"
        else "info"
    )
    label = (
        "Earnings window"
        if event_window_label == "earnings_window" or "earnings" in next_event_name.lower()
        else "Macro window"
        if event_window_label == "macro_window" or "macro" in next_event_name.lower()
        else "Catalyst window"
    )
    summary = str(event_context.get("summary") or row.get("event_reason") or "").strip()
    return {
        "key": f"ticker:{ticker}:{next_event_name}:{event_day.isoformat()}",
        "source": "ticker_event",
        "kind": event_window_label or "ticker_event",
        "title": next_event_name,
        "ticker": ticker,
        "event_date": event_day.isoformat(),
        "days_until": days_until,
        "impact": "high" if tone == "negative" else "medium" if tone == "warning" else "low",
        "tone": tone,
        "label": label,
        "detail": summary or f"{ticker} has {next_event_name} scheduled for {event_day.isoformat()}.",
        "trade_posture": trade_posture,
        "event_severity": event_severity,
        "ranking_score": row.get("ranking_score"),
        "ranking_tier": row.get("ranking_tier"),
    }


def build_event_calendar_snapshot(
    *,
    watchlist_rows: Iterable[dict[str, Any]] | None = None,
    macro_limit: int = 8,
    limit: int = 8,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    rows.extend(load_macro_events(limit=macro_limit))

    for row in list(watchlist_rows or []):
        if not isinstance(row, dict):
            continue
        item = _build_watchlist_calendar_item(row)
        if item:
            rows.append(item)

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        key = (
            str(row.get("source") or ""),
            str(row.get("ticker") or ""),
            str(row.get("title") or ""),
            str(row.get("event_date") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    tone_rank = {"negative": 0, "warning": 1, "info": 2, "positive": 3}
    impact_rank = {"high": 0, "medium": 1, "low": 2}
    deduped.sort(
        key=lambda item: (
            _coerce_int(item.get("days_until")) if _coerce_int(item.get("days_until")) is not None else 9999,
            tone_rank.get(str(item.get("tone") or "").strip().lower(), 4),
            impact_rank.get(str(item.get("impact") or "").strip().lower(), 3),
            str(item.get("ticker") or ""),
            str(item.get("title") or ""),
        )
    )

    visible = deduped[: max(1, int(limit))]
    macro_count = sum(1 for row in deduped if str(row.get("source") or "").strip().lower() == "macro_calendar")
    ticker_count = sum(1 for row in deduped if str(row.get("source") or "").strip().lower() == "ticker_event")
    high_impact_count = sum(1 for row in deduped if str(row.get("impact") or "").strip().lower() == "high")
    caution_count = sum(1 for row in deduped if str(row.get("tone") or "").strip().lower() in {"negative", "warning"})
    next_item = visible[0] if visible else None

    return {
        "count": len(visible),
        "total": len(deduped),
        "items": serialize_value(visible),
        "summary": {
            "macro_count": macro_count,
            "ticker_count": ticker_count,
            "high_impact_count": high_impact_count,
            "caution_count": caution_count,
            "next_item": serialize_value(next_item),
            "board_label": "Macro and catalyst calendar",
        },
    }
