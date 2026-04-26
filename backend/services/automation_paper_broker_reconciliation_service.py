from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, Tenant
from backend.services import automation_ai_review_service, equity_snapshot_service, notes_service
from backend.services.audit_service import record_audit_event
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.mappers import normalize_alpaca_status
from backend.services.execution.provider_registry import get_execution_adapter_for
from backend.services.serialization import serialize_value

PAPER_BROKER_NOTE_OWNER = "automation-ai"
PAPER_BROKER_HISTORY_LIMIT = 8
PAPER_BROKER_NOTE_LIMIT = 250
PAPER_BROKER_PERSONAL_PAPER_PROFILE = "personal_paper"
PAPER_BROKER_ORDER_LOOKBACK_DAYS = 7

MARKET_TIMEZONE = automation_ai_review_service.MARKET_TIMEZONE


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


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if pd.isna(parsed):
        return float(default)
    return float(parsed)


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _session_day_for(value: datetime | None = None) -> str:
    now = value or _utc_now()
    return now.astimezone(MARKET_TIMEZONE).strftime("%Y-%m-%d")


def _session_bounds_for_day(session_day: str) -> tuple[datetime, datetime]:
    local_day = date.fromisoformat(session_day)
    local_start = datetime.combine(local_day, time.min, tzinfo=MARKET_TIMEZONE)
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _profile_tag(profile_key: str) -> str:
    return "profile-" + str(profile_key or PAPER_BROKER_PERSONAL_PAPER_PROFILE).strip().lower().replace(":", "-")


def normalize_paper_broker_reconciliation_runtime(runtime_state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(runtime_state or {})
    last_report = runtime.get("paper_broker_reconciliation_last_report")
    if not isinstance(last_report, dict):
        last_report = {}
    history = [
        serialize_value(item)
        for item in list(runtime.get("paper_broker_reconciliation_history") or [])[:PAPER_BROKER_HISTORY_LIMIT]
        if isinstance(item, dict)
    ]
    return {
        "paper_broker_reconciliation_last_report": serialize_value(last_report),
        "paper_broker_reconciliation_last_note_id": str(
            runtime.get("paper_broker_reconciliation_last_note_id") or ""
        ).strip() or None,
        "paper_broker_reconciliation_note_session_day": str(
            runtime.get("paper_broker_reconciliation_note_session_day") or ""
        ).strip() or None,
        "paper_broker_reconciliation_last_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_broker_reconciliation_last_run_at"))
        ),
        "paper_broker_reconciliation_last_scheduled_run_at": _serialize_datetime(
            _parse_datetime(runtime.get("paper_broker_reconciliation_last_scheduled_run_at"))
        ),
        "paper_broker_reconciliation_last_scheduled_session_day": str(
            runtime.get("paper_broker_reconciliation_last_scheduled_session_day") or ""
        ).strip() or None,
        "paper_broker_reconciliation_last_error": str(
            runtime.get("paper_broker_reconciliation_last_error") or ""
        ).strip() or None,
        "paper_broker_reconciliation_history": history,
    }


def build_paper_broker_reconciliation_snapshot(state: dict[str, Any] | None) -> dict[str, Any]:
    runtime = normalize_paper_broker_reconciliation_runtime((state or {}).get("runtime"))
    report = dict(runtime.get("paper_broker_reconciliation_last_report") or {})
    if not report:
        return {
            "status": "not_run",
            "label": "Not run",
            "checked_at": None,
            "matched_count": 0,
            "orphan_broker_order_count": 0,
            "orphan_local_order_count": 0,
            "position_mismatch_count": 0,
            "fill_mismatch_count": 0,
            "ledger_consistency": "unknown",
            "blockers": [],
            "warnings": [],
            "related_note_id": runtime.get("paper_broker_reconciliation_last_note_id"),
            "last_run_at": runtime.get("paper_broker_reconciliation_last_run_at"),
            "last_scheduled_run_at": runtime.get("paper_broker_reconciliation_last_scheduled_run_at"),
            "last_error": runtime.get("paper_broker_reconciliation_last_error"),
            "broker_available": False,
            "run_source": None,
        }
    report.setdefault("related_note_id", runtime.get("paper_broker_reconciliation_last_note_id"))
    report.setdefault("note_id", runtime.get("paper_broker_reconciliation_last_note_id"))
    report.setdefault("last_run_at", runtime.get("paper_broker_reconciliation_last_run_at"))
    report.setdefault("last_scheduled_run_at", runtime.get("paper_broker_reconciliation_last_scheduled_run_at"))
    report.setdefault("last_error", runtime.get("paper_broker_reconciliation_last_error"))
    return serialize_value(report)


def _safe_frame(reader: Any) -> pd.DataFrame:
    try:
        frame = reader()
    except Exception:
        return pd.DataFrame()
    return frame if isinstance(frame, pd.DataFrame) else pd.DataFrame()


def _owned_rows(frame: pd.DataFrame, *, tenant_id: str, profile_key: str) -> pd.DataFrame:
    return automation_ai_review_service._owned_automation_rows(
        frame,
        tenant_id=str(tenant_id),
        profile_key=profile_key,
    )


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return [dict(item) for item in frame.to_dict(orient="records")]


def _normalized_id(value: Any) -> str:
    return str(value or "").strip()


def _broker_order_id(row: dict[str, Any]) -> str:
    for key in ("broker_order_id", "broker_close_order_id"):
        value = _normalized_id(row.get(key))
        if value:
            return value
    return ""


def _broker_client_order_id(row: dict[str, Any]) -> str:
    for key in ("broker_client_order_id", "order_id", "client_order_id"):
        value = _normalized_id(row.get(key))
        if value:
            return value
    return ""


def _order_identity(order: dict[str, Any]) -> tuple[str, str]:
    return _normalized_id(order.get("id")), _normalized_id(order.get("client_order_id"))


def _is_working_status(status: Any) -> bool:
    normalized = normalize_alpaca_status(status)
    return normalized in {"new", "accepted", "pending_new", "partially_filled", "accepted_for_bidding"}


def _is_filled_status(status: Any) -> bool:
    return normalize_alpaca_status(status) == "filled"


def _symbol_for_row(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or row.get("underlying_symbol") or "").strip().upper()


def _symbol_for_position(position: dict[str, Any]) -> str:
    return str(position.get("symbol") or position.get("ticker") or "").strip().upper()


def _quantity_for_row(row: dict[str, Any]) -> float:
    for key in ("suggested_contracts", "quantity", "qty", "broker_qty"):
        if row.get(key) not in (None, ""):
            return abs(_coerce_float(row.get(key), 0.0))
    return 0.0


def _quantity_for_position(position: dict[str, Any]) -> float:
    return abs(_coerce_float(position.get("qty") or position.get("quantity"), 0.0))


def _local_order_lookup(local_rows: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_broker_id: dict[str, dict[str, Any]] = {}
    by_client_id: dict[str, dict[str, Any]] = {}
    for row in local_rows:
        broker_id = _broker_order_id(row)
        client_id = _broker_client_order_id(row)
        if broker_id:
            by_broker_id[broker_id] = row
        if client_id:
            by_client_id[client_id] = row
    return by_broker_id, by_client_id


def _recent_order_events(
    db: Session | None,
    *,
    tenant: Tenant,
    profile_key: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    if db is None:
        return []
    rows = (
        db.execute(
            select(OrderEventRecord)
            .where(OrderEventRecord.tenant_id == tenant.id)
            .where(OrderEventRecord.created_at >= start_at)
            .where(OrderEventRecord.created_at < end_at)
            .order_by(OrderEventRecord.created_at.desc())
            .limit(500)
        )
        .scalars()
        .all()
    )
    normalized_profile = str(profile_key or PAPER_BROKER_PERSONAL_PAPER_PROFILE).strip().lower()
    events: list[dict[str, Any]] = []
    for row in rows:
        payload = dict(row.payload_json or {})
        payload_trade = dict(payload.get("trade") or {})
        row_profile = str(
            payload.get("automation_profile_key")
            or payload_trade.get("automation_profile_key")
            or ""
        ).strip().lower()
        if row_profile and row_profile != normalized_profile:
            continue
        if not (
            row_profile
            or str(payload.get("automation_cycle_id") or "").strip()
            or str(payload_trade.get("automation_origin") or "").strip().lower() == "trade_automation"
        ):
            continue
        events.append(
            {
                "event_key": row.event_key,
                "status": row.status,
                "ticker": row.ticker,
                "detail": row.detail,
                "created_at": _serialize_datetime(row.created_at),
                "payload": serialize_value(payload),
            }
        )
    return events


def _fetch_broker_snapshot(now: datetime) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    try:
        adapter = get_execution_adapter_for("alpaca_paper")
        ensure = getattr(adapter, "_ensure_credentials", None)
        if callable(ensure):
            ensure()
        client = getattr(adapter, "client", None)
        if client is None:
            raise ValidationServiceError("Alpaca paper adapter does not expose a readable trading client.")
        after = _serialize_datetime(now - timedelta(days=PAPER_BROKER_ORDER_LOOKBACK_DAYS))
        orders = client.list_orders(status="all", limit=100, after=after, nested=False)
        return {
            "broker_available": True,
            "account": serialize_value(client.get_account()),
            "orders": serialize_value(orders),
            "positions": serialize_value(client.list_positions()),
        }, warnings
    except Exception as exc:
        warnings.append(
            {
                "key": "broker_snapshot_unavailable",
                "detail": str(exc) or "Broker-paper snapshot is unavailable.",
            }
        )
        return {
            "broker_available": False,
            "account": {},
            "orders": [],
            "positions": [],
        }, warnings


def _latest_equity_snapshot(tenant: Tenant, profile_key: str) -> dict[str, Any] | None:
    try:
        snapshot = equity_snapshot_service.get_latest_trade_automation_equity_snapshot(
            tenant_id=str(tenant.id),
            tenant_slug=getattr(tenant, "slug", None),
            profile_key=profile_key,
        )
    except Exception:
        return None
    return serialize_value(snapshot) if isinstance(snapshot, dict) else None


def _compare_reconciliation(
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    broker_snapshot: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    pending_rows = _records(_owned_rows(_safe_frame(sdm.read_pending_orders), tenant_id=tenant.id, profile_key=profile_key))
    open_rows = _records(_owned_rows(_safe_frame(sdm.read_open_trades), tenant_id=tenant.id, profile_key=profile_key))
    closed_rows = _records(_owned_rows(_safe_frame(sdm.read_closed_trades), tenant_id=tenant.id, profile_key=profile_key))
    local_rows = [*pending_rows, *open_rows, *closed_rows]
    local_broker_lookup, local_client_lookup = _local_order_lookup(local_rows)
    settled_broker_lookup, settled_client_lookup = _local_order_lookup([*open_rows, *closed_rows])
    broker_orders = [dict(item) for item in list(broker_snapshot.get("orders") or []) if isinstance(item, dict)]
    broker_positions = [dict(item) for item in list(broker_snapshot.get("positions") or []) if isinstance(item, dict)]
    broker_account = dict(broker_snapshot.get("account") or {})
    broker_available = bool(broker_snapshot.get("broker_available"))
    latest_equity_snapshot = _latest_equity_snapshot(tenant, profile_key)

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    matched_count = 0
    orphan_broker_orders: list[dict[str, Any]] = []
    fill_mismatches: list[dict[str, Any]] = []
    broker_order_ids: set[str] = set()
    broker_client_ids: set[str] = set()

    if broker_available:
        for order in broker_orders:
            broker_id, client_id = _order_identity(order)
            status = normalize_alpaca_status(order.get("status")) or str(order.get("status") or "").strip().lower()
            if broker_id:
                broker_order_ids.add(broker_id)
            if client_id:
                broker_client_ids.add(client_id)
            local_match = (
                (broker_id and local_broker_lookup.get(broker_id))
                or (client_id and local_client_lookup.get(client_id))
            )
            settled_match = (
                (broker_id and settled_broker_lookup.get(broker_id))
                or (client_id and settled_client_lookup.get(client_id))
            )
            if _is_filled_status(status):
                if settled_match:
                    matched_count += 1
                    continue
                fill_mismatches.append(
                    {
                        "broker_order_id": broker_id,
                        "client_order_id": client_id,
                        "symbol": order.get("symbol"),
                        "status": status,
                        "detail": "Broker shows a filled paper order with no matching local open or closed trade.",
                    }
                )
                continue
            if local_match:
                matched_count += 1
                continue
            if _is_working_status(status):
                orphan_broker_orders.append(
                    {
                        "broker_order_id": broker_id,
                        "client_order_id": client_id,
                        "symbol": order.get("symbol"),
                        "status": status,
                        "detail": "Broker shows a working paper order with no matching local pending order.",
                    }
                )

    orphan_local_orders = (
        [
            {
                "order_id": row.get("order_id"),
                "broker_order_id": _broker_order_id(row),
                "client_order_id": _broker_client_order_id(row),
                "ticker": _symbol_for_row(row),
                "detail": "Local pending order is missing from the broker-paper order snapshot.",
            }
            for row in pending_rows
            if _broker_order_id(row)
            and _broker_order_id(row) not in broker_order_ids
            and _broker_client_order_id(row) not in broker_client_ids
        ]
        if broker_available
        else []
    )

    local_open_by_symbol: dict[str, float] = Counter()
    for row in open_rows:
        symbol = _symbol_for_row(row)
        if symbol:
            local_open_by_symbol[symbol] += _quantity_for_row(row)
    broker_position_by_symbol = (
        {
            _symbol_for_position(position): _quantity_for_position(position)
            for position in broker_positions
            if _symbol_for_position(position)
        }
        if broker_available
        else {}
    )
    position_mismatches: list[dict[str, Any]] = []
    if broker_available:
        for symbol in sorted(set(local_open_by_symbol.keys()) | set(broker_position_by_symbol.keys())):
            local_qty = float(local_open_by_symbol.get(symbol, 0.0))
            broker_qty = float(broker_position_by_symbol.get(symbol, 0.0))
            tolerance = max(0.001, min(local_qty, broker_qty) * 0.001)
            if abs(local_qty - broker_qty) > tolerance:
                position_mismatches.append(
                    {
                        "symbol": symbol,
                        "local_qty": local_qty,
                        "broker_qty": broker_qty,
                        "detail": "Local open position quantity does not match the broker-paper position quantity.",
                    }
                )

    runtime = dict(state.get("runtime") or {})
    runtime_consistency = str(runtime.get("ledger_snapshot_consistency") or "").strip().lower()
    runtime_reconciliation = str(runtime.get("current_route_reconciliation_status") or "").strip().lower()
    if runtime_consistency == "inconsistent":
        blockers.append({"key": "ledger_snapshot_inconsistent", "detail": "Ledger and snapshot accounting are inconsistent."})
    if runtime_reconciliation in {"failed", "issues_present", "orphaned", "inconsistent"}:
        blockers.append({"key": "current_route_reconciliation_fault", "detail": "Current-route reconciliation has unresolved issues."})

    if orphan_broker_orders:
        blockers.append({"key": "orphan_broker_order", "detail": f"{len(orphan_broker_orders)} broker-paper order(s) are missing locally."})
    if orphan_local_orders:
        blockers.append({"key": "orphan_local_order", "detail": f"{len(orphan_local_orders)} local pending order(s) are missing at the broker."})
    if fill_mismatches:
        blockers.append({"key": "fill_mismatch", "detail": f"{len(fill_mismatches)} filled broker-paper order(s) are missing local trade rows."})
    if position_mismatches:
        blockers.append({"key": "position_mismatch", "detail": f"{len(position_mismatches)} broker/local position mismatch(es) were found."})

    failed_event_count = sum(1 for event in events if str(event.get("status") or "").strip().lower() in {"failed", "error"})
    rejected_event_count = sum(1 for event in events if str(event.get("status") or "").strip().lower() == "rejected")
    if failed_event_count:
        blockers.append({"key": "order_event_failed", "detail": f"{failed_event_count} failed order event(s) were recorded."})
    if rejected_event_count:
        warnings.append({"key": "order_event_rejected", "detail": f"{rejected_event_count} rejected order event(s) were recorded."})
    if not broker_available:
        warnings.append({"key": "broker_snapshot_unavailable", "detail": "Broker-paper account, orders, and positions could not be read."})

    broker_equity = _coerce_float(
        broker_account.get("equity") or broker_account.get("portfolio_value") or broker_account.get("last_equity"),
        0.0,
    )
    snapshot_equity = _coerce_float((latest_equity_snapshot or {}).get("equity"), 0.0)
    equity_delta = abs(broker_equity - snapshot_equity) if broker_available and broker_equity > 0 and snapshot_equity > 0 else None
    equity_tolerance = max(10.0, broker_equity * 0.05) if broker_equity > 0 else 10.0
    equity_status = "missing"
    if latest_equity_snapshot and not broker_available:
        equity_status = "broker_unavailable"
    elif latest_equity_snapshot and equity_delta is not None and equity_delta <= equity_tolerance:
        equity_status = "matched"
    elif latest_equity_snapshot and equity_delta is not None:
        equity_status = "drift"
        warnings.append(
            {
                "key": "equity_snapshot_drift",
                "detail": (
                    f"Latest local equity snapshot differs from broker-paper equity by {equity_delta:.2f}, "
                    f"above the {equity_tolerance:.2f} advisory tolerance."
                ),
            }
        )
    elif latest_equity_snapshot:
        equity_status = "available"

    ledger_consistency = "consistent" if not blockers else "inconsistent"
    return {
        "matched_count": matched_count,
        "orphan_broker_order_count": len(orphan_broker_orders),
        "orphan_local_order_count": len(orphan_local_orders),
        "position_mismatch_count": len(position_mismatches),
        "fill_mismatch_count": len(fill_mismatches),
        "ledger_consistency": ledger_consistency,
        "blockers": blockers,
        "warnings": warnings,
        "orphan_broker_orders": orphan_broker_orders[:10],
        "orphan_local_orders": orphan_local_orders[:10],
        "position_mismatches": position_mismatches[:10],
        "fill_mismatches": fill_mismatches[:10],
        "local_counts": {
            "pending": len(pending_rows),
            "open": len(open_rows),
            "closed": len(closed_rows),
            "order_events": len(events),
        },
        "broker_counts": {
            "orders": len(broker_orders),
            "positions": len(broker_positions),
        },
        "equity_snapshot": {
            "status": equity_status,
            "available": bool(latest_equity_snapshot),
            "snapshot_at": (latest_equity_snapshot or {}).get("snapshot_at"),
            "cycle_at": (latest_equity_snapshot or {}).get("cycle_at"),
            "local_equity": snapshot_equity if latest_equity_snapshot else None,
            "broker_equity": broker_equity if broker_available and broker_equity > 0 else None,
            "delta": equity_delta,
            "tolerance": equity_tolerance if broker_equity > 0 else None,
        },
    }


def _find_existing_note_id(profile_key: str, session_day: str) -> str | None:
    try:
        payload = notes_service.list_notes(
            status="all",
            tag="automation-ai",
            owner=PAPER_BROKER_NOTE_OWNER,
            limit=PAPER_BROKER_NOTE_LIMIT,
            sort_by="updated_desc",
            note_type="risk_review",
        )
    except Exception:
        return None
    required_tags = {
        "automation-ai",
        "paper-broker",
        "reconciliation",
        _profile_tag(profile_key),
        f"session-{session_day}",
    }
    for item in list(payload.get("items") or []):
        tags = {str(tag or "").strip().lower() for tag in item.get("tags", [])}
        if required_tags.issubset(tags):
            return str(item.get("id") or "").strip() or None
    return None


def _build_note_body(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str:
    equity_snapshot = dict(report.get("equity_snapshot") or {})
    lines = [
        f"Automation paper broker reconciliation for {tenant.name or tenant.slug} / {profile_key}",
        f"Session day: {report.get('session_day')}",
        f"Run source: {str(report.get('run_source') or 'manual').replace('_', ' ')}",
        f"Status: {str(report.get('status') or '').upper()}",
        f"Checked at: {report.get('checked_at')}",
        f"Matched orders: {report.get('matched_count', 0)}",
        f"Broker orphan orders: {report.get('orphan_broker_order_count', 0)}",
        f"Local orphan orders: {report.get('orphan_local_order_count', 0)}",
        f"Position mismatches: {report.get('position_mismatch_count', 0)}",
        f"Fill mismatches: {report.get('fill_mismatch_count', 0)}",
        f"Ledger consistency: {str(report.get('ledger_consistency') or 'unknown').replace('_', ' ')}",
        f"Equity snapshot: {str(equity_snapshot.get('status') or 'missing').replace('_', ' ')}",
        "",
        "This reconciliation is advisory and read-mostly. It does not place orders, cancel orders, tune baseline settings, enable live trading, arm automation, or clear locks.",
        "",
        "Blockers",
    ]
    blockers = [item for item in list(report.get("blockers") or []) if isinstance(item, dict)]
    if blockers:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in blockers[:12])
    else:
        lines.append("- None.")
    warnings = [item for item in list(report.get("warnings") or []) if isinstance(item, dict)]
    lines.extend(["", "Warnings"])
    if warnings:
        lines.extend(f"- {item.get('key')}. {item.get('detail')}" for item in warnings[:12])
    else:
        lines.append("- None.")
    for heading, key in (
        ("Broker orphan orders", "orphan_broker_orders"),
        ("Local orphan orders", "orphan_local_orders"),
        ("Position mismatches", "position_mismatches"),
        ("Fill mismatches", "fill_mismatches"),
    ):
        items = [item for item in list(report.get(key) or []) if isinstance(item, dict)]
        if not items:
            continue
        lines.extend(["", heading])
        for item in items[:8]:
            identifier = item.get("broker_order_id") or item.get("order_id") or item.get("symbol") or item.get("ticker") or "--"
            lines.append(f"- {identifier}: {item.get('detail')}")
    return "\n".join(lines).strip()


def _sync_note(*, tenant: Tenant, profile_key: str, report: dict[str, Any]) -> str | None:
    session_day = str(report.get("session_day") or "").strip() or _session_day_for()
    tags = ["automation-ai", "paper-broker", "reconciliation", _profile_tag(profile_key), f"session-{session_day}"]
    title = f"Automation paper broker reconciliation - {profile_key} - {session_day}"
    body = _build_note_body(tenant=tenant, profile_key=profile_key, report=report)
    note_id = str(report.get("related_note_id") or report.get("note_id") or "").strip() or _find_existing_note_id(profile_key, session_day)
    payload = {
        "title": title,
        "body": body,
        "tags": tags,
        "owner": PAPER_BROKER_NOTE_OWNER,
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


def run_paper_broker_reconciliation(
    db: Session | None,
    *,
    tenant: Tenant,
    state: dict[str, Any],
    profile_key: str,
    linked_account: BrokerageLinkedAccount | None = None,
    actor: Any = None,
    now: datetime | None = None,
    run_source: str = "manual",
    broker_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    session_day = _session_day_for(now)
    start_at, end_at = _session_bounds_for_day(session_day)
    normalized_run_source = str(run_source or "manual").strip().lower().replace(" ", "_") or "manual"
    fetched_warnings: list[dict[str, Any]] = []
    if broker_snapshot is None:
        broker_snapshot, fetched_warnings = _fetch_broker_snapshot(now)
    else:
        broker_snapshot = {
            "broker_available": bool(broker_snapshot.get("broker_available", True)),
            "account": serialize_value(broker_snapshot.get("account") or {}),
            "orders": serialize_value(list(broker_snapshot.get("orders") or [])),
            "positions": serialize_value(list(broker_snapshot.get("positions") or [])),
        }
    events = _recent_order_events(db, tenant=tenant, profile_key=profile_key, start_at=start_at, end_at=end_at)
    compared = _compare_reconciliation(
        tenant=tenant,
        state=state,
        profile_key=profile_key,
        broker_snapshot=broker_snapshot,
        events=events,
    )
    warnings: list[dict[str, Any]] = []
    seen_warnings: set[tuple[str, str]] = set()
    for item in [*fetched_warnings, *list(compared.get("warnings") or [])]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        detail = str(item.get("detail") or "").strip()
        signature = (key, detail)
        if signature in seen_warnings:
            continue
        seen_warnings.add(signature)
        warnings.append(item)
    blockers = list(compared.get("blockers") or [])
    if profile_key != PAPER_BROKER_PERSONAL_PAPER_PROFILE:
        warnings.insert(
            0,
            {
                "key": "paper_profile_required",
                "detail": "V1 paper broker reconciliation targets the personal paper automation profile.",
            },
        )
    status = "blocked" if blockers else "warning" if warnings else "clean"
    report = serialize_value(
        {
            "status": status,
            "label": {
                "clean": "Paper broker clean",
                "warning": "Paper broker warning",
                "blocked": "Paper broker blocked",
            }.get(status, "Paper broker reconciliation"),
            "profile_key": profile_key,
            "linked_account_id": getattr(linked_account, "id", None),
            "session_day": session_day,
            "checked_at": _serialize_datetime(now),
            "run_source": normalized_run_source,
            "broker_available": bool(broker_snapshot.get("broker_available")),
            "broker_account": broker_snapshot.get("account") or {},
            "blockers": blockers[:20],
            "warnings": warnings[:20],
            **{key: value for key, value in compared.items() if key not in {"blockers", "warnings"}},
        }
    )
    note_id = _sync_note(tenant=tenant, profile_key=profile_key, report=report)
    if note_id:
        report["note_id"] = note_id
        report["related_note_id"] = note_id

    runtime = state.setdefault("runtime", {})
    summary_keys = {
        "status",
        "label",
        "profile_key",
        "linked_account_id",
        "session_day",
        "checked_at",
        "run_source",
        "broker_available",
        "matched_count",
        "orphan_broker_order_count",
        "orphan_local_order_count",
        "position_mismatch_count",
        "fill_mismatch_count",
        "ledger_consistency",
        "blockers",
        "warnings",
        "local_counts",
        "broker_counts",
        "equity_snapshot",
        "note_id",
        "related_note_id",
    }
    summary = {key: report.get(key) for key in summary_keys if key in report}
    runtime["paper_broker_reconciliation_last_report"] = serialize_value(summary)
    runtime["paper_broker_reconciliation_last_note_id"] = note_id
    runtime["paper_broker_reconciliation_note_session_day"] = session_day
    runtime["paper_broker_reconciliation_last_run_at"] = report.get("checked_at")
    runtime["paper_broker_reconciliation_last_error"] = None
    if normalized_run_source == "scheduled":
        runtime["paper_broker_reconciliation_last_scheduled_run_at"] = report.get("checked_at")
        runtime["paper_broker_reconciliation_last_scheduled_session_day"] = session_day
    history = list(runtime.get("paper_broker_reconciliation_history") or [])
    history.insert(
        0,
        {
            "at": report.get("checked_at"),
            "session_day": session_day,
            "status": report.get("status"),
            "matched_count": report.get("matched_count"),
            "blocker_count": len(blockers),
            "warning_count": len(warnings),
            "note_id": note_id,
            "run_source": normalized_run_source,
        },
    )
    runtime["paper_broker_reconciliation_history"] = serialize_value(history[:PAPER_BROKER_HISTORY_LIMIT])
    if db is not None:
        record_audit_event(
            db,
            event_type="trade_automation.paper_broker_reconciled",
            tenant=tenant,
            user=actor,
            payload={
                "profile_key": profile_key,
                "linked_account_id": getattr(linked_account, "id", None),
                "session_day": session_day,
                "status": report.get("status"),
                "matched_count": report.get("matched_count"),
                "orphan_broker_order_count": report.get("orphan_broker_order_count"),
                "orphan_local_order_count": report.get("orphan_local_order_count"),
                "position_mismatch_count": report.get("position_mismatch_count"),
                "fill_mismatch_count": report.get("fill_mismatch_count"),
                "note_id": note_id,
                "run_source": normalized_run_source,
            },
        )
    return serialize_value(report)
