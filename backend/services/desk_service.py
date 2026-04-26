from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend import stock_direction_model as sdm
from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, TradeApprovalIntent

_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")

SYSTEMATIC_DESK_SLUG = "systematic-equities"
STAT_ARB_DESK_SLUG = "stat-arb"
MACRO_DESK_SLUG = "macro"

LEGACY_DESK_SLUG_ALIASES: dict[str, str] = {
    "alpha-desk": SYSTEMATIC_DESK_SLUG,
}

DESK_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "slug": SYSTEMATIC_DESK_SLUG,
        "name": "Systematic Equities Desk",
        "legacy_aliases": ("alpha-desk",),
        "default_tags": ("research", "execution", "risk", "paper", "live", "review"),
    },
    {
        "slug": STAT_ARB_DESK_SLUG,
        "name": "Stat Arb Desk",
        "legacy_aliases": (),
        "default_tags": ("research", "execution", "risk", "paper", "live", "review"),
    },
    {
        "slug": MACRO_DESK_SLUG,
        "name": "Macro Desk",
        "legacy_aliases": (),
        "default_tags": ("research", "execution", "risk", "paper", "live", "review"),
    },
)

_DESK_NAME_BY_SLUG = {str(item["slug"]): str(item["name"]) for item in DESK_DEFINITIONS}
_DESK_ALIASES_BY_SLUG = {
    str(item["slug"]): tuple(str(alias) for alias in item.get("legacy_aliases") or ())
    for item in DESK_DEFINITIONS
}


@dataclass(frozen=True)
class DeskSummarySnapshot:
    tenant_slug: str
    tenant_name: str
    paper_account_status: str
    live_account_status: str
    open_trades: int
    pending_orders: int
    alerts: int
    last_activity_at: str | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "tenant_slug": self.tenant_slug,
            "tenant_name": self.tenant_name,
            "paper_account_status": self.paper_account_status,
            "live_account_status": self.live_account_status,
            "open_trades": self.open_trades,
            "pending_orders": self.pending_orders,
            "alerts": self.alerts,
            "last_activity_at": self.last_activity_at,
        }


def normalize_desk_slug(value: Any, *, fallback: str = SYSTEMATIC_DESK_SLUG) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = _SLUG_PATTERN.sub("-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = fallback
    return LEGACY_DESK_SLUG_ALIASES.get(cleaned, cleaned)


def resolve_desk_name(slug: Any, fallback_name: str | None = None) -> str:
    normalized_slug = normalize_desk_slug(slug)
    resolved = _DESK_NAME_BY_SLUG.get(normalized_slug)
    if resolved:
        return resolved
    cleaned_fallback = str(fallback_name or "").strip()
    return cleaned_fallback or normalized_slug.replace("-", " ").title() or "Desk"


def is_default_systematic_desk(slug: Any) -> bool:
    return normalize_desk_slug(slug) == SYSTEMATIC_DESK_SLUG


def resolve_scope_slug_values(slug: Any) -> set[str]:
    normalized_slug = normalize_desk_slug(slug)
    aliases = set(_DESK_ALIASES_BY_SLUG.get(normalized_slug, ()))
    aliases.add(normalized_slug)
    return aliases


def default_desk_definitions() -> tuple[dict[str, Any], ...]:
    return tuple(dict(item) for item in DESK_DEFINITIONS)


def apply_tenant_scope_to_record(
    record: dict[str, Any] | None,
    *,
    tenant_id: Any = None,
    tenant_slug: Any = None,
) -> dict[str, Any]:
    scoped = dict(record or {})
    normalized_tenant_id = str(tenant_id or "").strip()
    normalized_tenant_slug = normalize_desk_slug(tenant_slug) if tenant_slug else ""
    if normalized_tenant_id:
        scoped["tenant_id"] = normalized_tenant_id
    if normalized_tenant_slug:
        scoped["tenant_slug"] = normalized_tenant_slug
    return scoped


def filter_frame_to_tenant(
    frame: pd.DataFrame,
    *,
    tenant_id: Any = None,
    tenant_slug: Any = None,
    include_unscoped_legacy_rows: bool = True,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()

    normalized_tenant_id = str(tenant_id or "").strip()
    normalized_tenant_slug = normalize_desk_slug(tenant_slug) if tenant_slug else ""
    if not normalized_tenant_id and not normalized_tenant_slug:
        return frame.copy()

    index = frame.index

    def _id_series(column: str) -> pd.Series:
        return frame.get(column, pd.Series("", index=index)).fillna("").astype(str).str.strip()

    def _slug_series(column: str) -> pd.Series:
        raw = frame.get(column, pd.Series("", index=index)).fillna("").astype(str).str.strip().str.lower()
        return raw.map(lambda value: normalize_desk_slug(value) if value else "")

    tenant_id_series = _id_series("tenant_id")
    tenant_slug_series = _slug_series("tenant_slug")
    automation_tenant_id_series = _id_series("automation_tenant_id")
    automation_tenant_slug_series = _slug_series("automation_tenant_slug")

    explicit_scope = (
        tenant_id_series.ne("")
        | tenant_slug_series.ne("")
        | automation_tenant_id_series.ne("")
        | automation_tenant_slug_series.ne("")
    )

    matches = pd.Series(False, index=index)
    if normalized_tenant_id:
        matches = matches | tenant_id_series.eq(normalized_tenant_id) | automation_tenant_id_series.eq(normalized_tenant_id)
    if normalized_tenant_slug:
        valid_slugs = resolve_scope_slug_values(normalized_tenant_slug)
        matches = matches | tenant_slug_series.isin(valid_slugs) | automation_tenant_slug_series.isin(valid_slugs)
        if include_unscoped_legacy_rows and is_default_systematic_desk(normalized_tenant_slug):
            matches = matches | ~explicit_scope

    return frame.loc[matches].copy()


def filter_frame_to_current_user(frame: pd.DataFrame, current_user: Any | None) -> pd.DataFrame:
    if current_user is None:
        return frame.copy()
    return filter_frame_to_tenant(
        frame,
        tenant_id=getattr(current_user, "tenant_id", None),
        tenant_slug=getattr(current_user, "tenant_slug", None),
    )


def _latest_timestamp_from_frame(frame: pd.DataFrame, *columns: str) -> str | None:
    if frame.empty:
        return None
    timestamps: list[pd.Series] = []
    for column in columns:
        if column in frame.columns:
            parsed = pd.to_datetime(frame[column], errors="coerce", utc=True)
            if not parsed.empty:
                timestamps.append(parsed.dropna())
    if not timestamps:
        return None
    combined = pd.concat(timestamps, ignore_index=True).dropna()
    if combined.empty:
        return None
    latest = combined.max()
    if pd.isna(latest):
        return None
    return latest.isoformat()


def _latest_timestamp_from_workspace_items(items: list[dict[str, Any]]) -> str | None:
    latest_value = None
    for item in items:
        for candidate in (item.get("updated_at"), item.get("created_at")):
            if not candidate:
                continue
            parsed = pd.to_datetime(candidate, errors="coerce", utc=True)
            if pd.isna(parsed):
                continue
            if latest_value is None or parsed > latest_value:
                latest_value = parsed
    return latest_value.isoformat() if latest_value is not None else None


def _max_timestamp(values: list[str | None]) -> str | None:
    parsed_values: list[datetime] = []
    for value in values:
        if not value:
            continue
        parsed = pd.to_datetime(value, errors="coerce", utc=True)
        if pd.isna(parsed):
            continue
        parsed_values.append(parsed.to_pydatetime())
    if not parsed_values:
        return None
    return max(parsed_values).isoformat()


def _linked_account_status(rows: list[BrokerageLinkedAccount], environment: str) -> str:
    environment_rows = [
        row for row in rows
        if str(row.account_environment or "").strip().lower() == environment
    ]
    if not environment_rows:
        return "not_linked"
    if any(
        str(row.connection_status or "").strip().lower() == "connected"
        and str(row.token_health or "").strip().lower() in {"healthy", "unknown"}
        for row in environment_rows
    ):
        return "connected"
    if any(
        str(row.connection_status or "").strip().lower() in {"connected", "attention", "reauth_required"}
        or str(row.token_health or "").strip().lower() in {"degraded", "expired"}
        for row in environment_rows
    ):
        return "attention"
    return "disconnected"


def build_desk_summaries(db: Session, *, current_user: Any) -> dict[str, Any]:
    from backend.services.tenant_service import _resolve_user_for_current_user, list_user_memberships
    from backend.services.workspace_service import list_workspaces

    actor = _resolve_user_for_current_user(db, current_user)
    if actor is None:
        return {"items": [], "count": 0}

    memberships = list_user_memberships(db, actor)
    open_trades = sdm.read_open_trades()
    pending_orders = sdm.read_pending_orders()
    closed_trades = sdm.read_closed_trades()
    forecast_journal = sdm.read_forecast_journal()
    monitored_open_trades = sdm.monitor_open_trades() if not open_trades.empty else pd.DataFrame()

    summaries: list[DeskSummarySnapshot] = []
    for membership in memberships:
        tenant = membership.tenant
        scoped_open_trades = filter_frame_to_tenant(open_trades, tenant_id=tenant.id, tenant_slug=tenant.slug)
        scoped_pending_orders = filter_frame_to_tenant(pending_orders, tenant_id=tenant.id, tenant_slug=tenant.slug)
        scoped_closed_trades = filter_frame_to_tenant(closed_trades, tenant_id=tenant.id, tenant_slug=tenant.slug)
        scoped_forecast_journal = filter_frame_to_tenant(forecast_journal, tenant_id=tenant.id, tenant_slug=tenant.slug)
        scoped_monitored = filter_frame_to_tenant(monitored_open_trades, tenant_id=tenant.id, tenant_slug=tenant.slug)

        linked_accounts = db.execute(
            select(BrokerageLinkedAccount).where(BrokerageLinkedAccount.tenant_id == tenant.id)
        ).scalars().all()
        workspace_snapshot = list_workspaces(current_user.user_id, tenant_slug=tenant.slug)
        workspaces = list(workspace_snapshot.get("items") or [])
        order_events = db.execute(
            select(OrderEventRecord).where(OrderEventRecord.tenant_id == tenant.id).order_by(OrderEventRecord.created_at.desc()).limit(1)
        ).scalars().all()
        trade_intents = db.execute(
            select(TradeApprovalIntent).where(TradeApprovalIntent.tenant_id == tenant.id).order_by(TradeApprovalIntent.updated_at.desc()).limit(1)
        ).scalars().all()

        urgent_actions = 0
        if not scoped_monitored.empty and "monitor_action" in scoped_monitored.columns:
            urgent_actions = int((scoped_monitored["monitor_action"].astype(str).str.upper() != "HOLD").sum())

        last_activity_at = _max_timestamp(
            [
                _latest_timestamp_from_frame(scoped_open_trades, "updated_at", "submitted_at", "opened_at"),
                _latest_timestamp_from_frame(scoped_pending_orders, "updated_at", "submitted_at", "opened_at"),
                _latest_timestamp_from_frame(scoped_closed_trades, "closed_at", "updated_at", "opened_at"),
                _latest_timestamp_from_frame(scoped_forecast_journal, "forecast_at", "resolved_at"),
                _latest_timestamp_from_workspace_items(workspaces),
                order_events[0].created_at.isoformat() if order_events else None,
                trade_intents[0].updated_at.isoformat() if trade_intents and trade_intents[0].updated_at else None,
                linked_accounts[0].updated_at.isoformat() if linked_accounts and linked_accounts[0].updated_at else None,
            ]
        )

        summaries.append(
            DeskSummarySnapshot(
                tenant_slug=tenant.slug,
                tenant_name=tenant.name,
                paper_account_status=_linked_account_status(linked_accounts, "paper"),
                live_account_status=_linked_account_status(linked_accounts, "live"),
                open_trades=int(len(scoped_open_trades)),
                pending_orders=int(len(scoped_pending_orders)),
                alerts=urgent_actions,
                last_activity_at=last_activity_at,
            )
        )

    return {
        "items": [item.to_payload() for item in summaries],
        "count": len(summaries),
    }
