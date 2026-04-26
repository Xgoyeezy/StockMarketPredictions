from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models.saas import (
    BacktestRun,
    DeskPnlSnapshot,
    DomainEventLog,
    PortfolioTargetRun,
    StrategyDesk,
    StrategyRun,
    StrategyTargetPublication,
)
from backend.services.audit_service import record_audit_event
from backend.services.backtesting.service import run_backtest
from backend.services.exceptions import NotFoundError, ValidationError
from backend.services.permissions import require_current_user_permission
from backend.services.portfolio_allocator.service import allocate_portfolio_targets, build_risk_state
from backend.services.strategy_engine.events import record_domain_event, serialize_domain_event
from backend.services.strategy_engine.registry import (
    StrategyDeskDefinition,
    build_strategy_desk,
    get_strategy_desk_definition,
    list_strategy_desk_definitions,
)
from backend.services.strategy_engine.types import DeskTargetProposal
from backend.services.tenant_service import _resolve_tenant_for_current_user, _resolve_user_for_current_user


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def _coerce_dict(value: Any) -> dict[str, Any]:
    return dict(value or {}) if isinstance(value, dict) else {}


def _assert_strategy_reader(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "tenant.read",
        "You do not have permission to view strategy desks.",
    )


def _assert_strategy_operator(current_user: Any) -> None:
    require_current_user_permission(
        current_user,
        "trade.execute",
        "Only traders, admins, or owners can operate strategy desks.",
    )


def _sanitize_desk_config(definition: StrategyDeskDefinition, config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(definition.default_config)
    for key, value in dict(config or {}).items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool, list, dict)):
            merged[key] = value
    return merged


def _serialize_publication(publication: StrategyTargetPublication | None) -> dict[str, Any] | None:
    if publication is None:
        return None
    return {
        "id": publication.id,
        "desk_key": publication.desk_key,
        "publication_kind": publication.publication_kind,
        "active": publication.active,
        "confidence_score": publication.confidence_score,
        "risk_estimate": publication.risk_estimate,
        "required_capital": publication.required_capital,
        "expected_holding_period": publication.expected_holding_period,
        "targets": list((publication.targets_json or {}).get("targets") or []),
        "metrics": dict(publication.metrics_json or {}),
        "published_at": _iso(publication.published_at),
    }


def _serialize_strategy_run(run: StrategyRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "desk_key": run.desk_key,
        "run_type": run.run_type,
        "status": run.status,
        "timeframe_label": run.timeframe_label,
        "universe_size": run.universe_size,
        "target_count": run.target_count,
        "market_state": dict(run.market_state_json or {}),
        "features": dict(run.features_json or {}),
        "signal": dict(run.signal_json or {}),
        "targets": dict(run.targets_json or {}),
        "validation": dict(run.validation_json or {}),
        "metrics": dict(run.metrics_json or {}),
        "error_message": run.error_message,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "created_at": _iso(run.created_at),
    }


def _serialize_pnl_snapshot(snapshot: DeskPnlSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "gross_exposure": snapshot.gross_exposure,
        "net_exposure": snapshot.net_exposure,
        "realized_pnl": snapshot.realized_pnl,
        "unrealized_pnl": snapshot.unrealized_pnl,
        "max_drawdown_pct": snapshot.max_drawdown_pct,
        "metrics": dict(snapshot.metrics_json or {}),
        "snapshot_at": _iso(snapshot.snapshot_at),
    }


def _serialize_market_state_for_storage(market_state: dict[str, Any]) -> dict[str, Any]:
    bars_summary: dict[str, Any] = {}
    for symbol, frame in (market_state.get("bars") or {}).items():
        if getattr(frame, "empty", True):
            bars_summary[str(symbol).upper()] = {"row_count": 0, "latest_at": None}
            continue
        latest_at = None
        try:
            latest_index = frame.index[-1]
            latest_at = latest_index.isoformat() if hasattr(latest_index, "isoformat") else str(latest_index)
        except Exception:
            latest_at = None
        bars_summary[str(symbol).upper()] = {
            "row_count": int(len(frame.index)),
            "latest_at": latest_at,
        }
    return {
        "as_of": market_state.get("as_of"),
        "provider_name": market_state.get("provider_name"),
        "requirements": list(market_state.get("requirements") or []),
        "bars_summary": bars_summary,
    }


def _serialize_backtest_run(run: BacktestRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "desk_key": run.desk_key,
        "status": run.status,
        "request": dict(run.request_json or {}),
        "summary": dict(run.summary_json or {}),
        "artifacts": dict(run.artifacts_json or {}),
        "error_message": run.error_message,
        "started_at": _iso(run.started_at),
        "completed_at": _iso(run.completed_at),
        "created_at": _iso(run.created_at),
    }


def ensure_strategy_desks(db: Session, *, current_user: Any) -> list[StrategyDesk]:
    tenant = _resolve_tenant_for_current_user(db, current_user)
    existing = {
        desk.desk_key: desk
        for desk in db.execute(
            select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id)
        ).scalars().all()
    }
    items: list[StrategyDesk] = []
    for definition in list_strategy_desk_definitions():
        desk = existing.get(definition.key)
        if desk is None:
            desk = StrategyDesk(
                tenant_id=tenant.id,
                desk_key=definition.key,
                name=definition.label,
                category=definition.category,
                lifecycle_stage=definition.lifecycle_stage,
                run_mode="manual",
                trading_mode=definition.trading_mode,
                enabled=True,
                paper_trading_enabled=definition.paper_trading_enabled,
                config_json=dict(definition.default_config),
                runtime_json={"last_status": "idle"},
                metadata_json={"description": definition.description},
            )
            db.add(desk)
            db.flush()
        items.append(desk)
    db.commit()
    return items


def _latest_publication_for_desk(db: Session, *, tenant_id: str, desk_key: str) -> StrategyTargetPublication | None:
    return db.execute(
        select(StrategyTargetPublication)
        .where(
            StrategyTargetPublication.tenant_id == tenant_id,
            StrategyTargetPublication.desk_key == desk_key,
            StrategyTargetPublication.active.is_(True),
        )
        .order_by(StrategyTargetPublication.published_at.desc())
    ).scalars().first()


def _latest_pnl_snapshot_for_desk(db: Session, *, tenant_id: str, desk_key: str) -> DeskPnlSnapshot | None:
    return db.execute(
        select(DeskPnlSnapshot)
        .where(DeskPnlSnapshot.tenant_id == tenant_id, DeskPnlSnapshot.desk_key == desk_key)
        .order_by(DeskPnlSnapshot.snapshot_at.desc())
    ).scalars().first()


def _latest_strategy_run_for_desk(db: Session, *, tenant_id: str, desk_key: str) -> StrategyRun | None:
    return db.execute(
        select(StrategyRun)
        .where(StrategyRun.tenant_id == tenant_id, StrategyRun.desk_key == desk_key)
        .order_by(StrategyRun.created_at.desc())
    ).scalars().first()


def _latest_backtest_for_desk(db: Session, *, tenant_id: str, desk_key: str) -> BacktestRun | None:
    return db.execute(
        select(BacktestRun)
        .where(BacktestRun.tenant_id == tenant_id, BacktestRun.desk_key == desk_key)
        .order_by(BacktestRun.created_at.desc())
    ).scalars().first()


def list_strategy_desks(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    desks = ensure_strategy_desks(db, current_user=current_user)
    items: list[dict[str, Any]] = []
    for desk in desks:
        publication = _latest_publication_for_desk(db, tenant_id=tenant.id, desk_key=desk.desk_key)
        pnl_snapshot = _latest_pnl_snapshot_for_desk(db, tenant_id=tenant.id, desk_key=desk.desk_key)
        latest_run = _latest_strategy_run_for_desk(db, tenant_id=tenant.id, desk_key=desk.desk_key)
        latest_backtest = _latest_backtest_for_desk(db, tenant_id=tenant.id, desk_key=desk.desk_key)
        items.append(
            {
                "desk_key": desk.desk_key,
                "name": desk.name,
                "category": desk.category,
                "lifecycle_stage": desk.lifecycle_stage,
                "trading_mode": desk.trading_mode,
                "enabled": desk.enabled,
                "paper_trading_enabled": desk.paper_trading_enabled,
                "config": dict(desk.config_json or {}),
                "runtime": dict(desk.runtime_json or {}),
                "metadata": dict(desk.metadata_json or {}),
                "latest_publication": _serialize_publication(publication),
                "latest_pnl_snapshot": _serialize_pnl_snapshot(pnl_snapshot),
                "latest_run": _serialize_strategy_run(latest_run) if latest_run is not None else None,
                "latest_backtest": _serialize_backtest_run(latest_backtest) if latest_backtest is not None else None,
            }
        )
    return {"items": items, "count": len(items)}


def get_strategy_desk_snapshot(db: Session, *, current_user: Any, desk_key: str) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    desk = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id, StrategyDesk.desk_key == desk_key)
    ).scalar_one_or_none()
    if desk is None:
        raise NotFoundError("The requested strategy desk could not be found.")
    runs = db.execute(
        select(StrategyRun)
        .where(StrategyRun.tenant_id == tenant.id, StrategyRun.desk_key == desk_key)
        .order_by(StrategyRun.created_at.desc())
    ).scalars().all()[:10]
    backtests = db.execute(
        select(BacktestRun)
        .where(BacktestRun.tenant_id == tenant.id, BacktestRun.desk_key == desk_key)
        .order_by(BacktestRun.created_at.desc())
    ).scalars().all()[:10]
    latest_publication = _latest_publication_for_desk(db, tenant_id=tenant.id, desk_key=desk_key)
    latest_pnl = _latest_pnl_snapshot_for_desk(db, tenant_id=tenant.id, desk_key=desk_key)
    return {
        "desk": {
            "desk_key": desk.desk_key,
            "name": desk.name,
            "category": desk.category,
            "lifecycle_stage": desk.lifecycle_stage,
            "trading_mode": desk.trading_mode,
            "enabled": desk.enabled,
            "paper_trading_enabled": desk.paper_trading_enabled,
            "config": dict(desk.config_json or {}),
            "runtime": dict(desk.runtime_json or {}),
            "metadata": dict(desk.metadata_json or {}),
        },
        "latest_publication": _serialize_publication(latest_publication),
        "latest_pnl_snapshot": _serialize_pnl_snapshot(latest_pnl),
        "runs": [_serialize_strategy_run(run) for run in runs],
        "backtests": [_serialize_backtest_run(run) for run in backtests],
    }


def update_strategy_desk(db: Session, *, current_user: Any, desk_key: str, updates: dict[str, Any]) -> dict[str, Any]:
    _assert_strategy_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    desk = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id, StrategyDesk.desk_key == desk_key)
    ).scalar_one_or_none()
    if desk is None:
        raise NotFoundError("The requested strategy desk could not be found.")
    definition = get_strategy_desk_definition(desk_key)
    payload = dict(updates or {})
    if "enabled" in payload and payload["enabled"] is not None:
        desk.enabled = bool(payload["enabled"])
    if "paper_trading_enabled" in payload and payload["paper_trading_enabled"] is not None:
        desk.paper_trading_enabled = bool(payload["paper_trading_enabled"])
    if "lifecycle_stage" in payload and payload["lifecycle_stage"]:
        desk.lifecycle_stage = str(payload["lifecycle_stage"]).strip().lower()
    if "trading_mode" in payload and payload["trading_mode"]:
        desk.trading_mode = str(payload["trading_mode"]).strip().lower()
    if "config" in payload:
        desk.config_json = _sanitize_desk_config(definition, _coerce_dict(payload.get("config")))
    desk.runtime_json = {**dict(desk.runtime_json or {}), "updated_at": _iso(_utc_now())}
    record_audit_event(
        db,
        event_type="strategy_desk.updated",
        tenant=tenant,
        user=user,
        payload={"desk_key": desk_key, "updates": payload},
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="desk.config_updated",
        aggregate_type="strategy_desk",
        aggregate_id=desk.id,
        payload={"desk_key": desk_key, "updates": payload},
    )
    db.commit()
    return get_strategy_desk_snapshot(db, current_user=current_user, desk_key=desk_key)


def _build_publication_payload(run_payload: dict[str, Any]) -> dict[str, Any]:
    signal = dict(run_payload.get("signal") or {})
    targets = list(run_payload.get("targets", {}).get("targets") or run_payload.get("targets_list") or [])
    return {
        "signal_summary": signal,
        "targets": targets,
        "validation": dict(run_payload.get("validation") or {}),
        "metrics": dict(run_payload.get("metrics") or {}),
    }


def _record_pnl_snapshot(
    db: Session,
    *,
    tenant_id: str,
    desk: StrategyDesk,
    targets: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> DeskPnlSnapshot:
    gross_exposure = float(sum(abs(float(target.get("target_weight") or 0.0)) for target in targets))
    net_exposure = float(sum(float(target.get("target_weight") or 0.0) for target in targets))
    snapshot = DeskPnlSnapshot(
        tenant_id=tenant_id,
        strategy_desk_id=desk.id,
        desk_key=desk.desk_key,
        gross_exposure=gross_exposure,
        net_exposure=net_exposure,
        realized_pnl=0.0,
        unrealized_pnl=0.0,
        max_drawdown_pct=0.0,
        metrics_json=dict(metrics or {}),
        snapshot_at=_utc_now(),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def run_strategy_desk(db: Session, *, current_user: Any, desk_key: str, run_type: str = "manual") -> dict[str, Any]:
    _assert_strategy_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    user = _resolve_user_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    desk_row = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id, StrategyDesk.desk_key == desk_key)
    ).scalar_one_or_none()
    if desk_row is None:
        raise NotFoundError("The requested strategy desk could not be found.")
    if not desk_row.enabled:
        raise ValidationError("This strategy desk is disabled.")

    strategy = build_strategy_desk(desk_key, config=dict(desk_row.config_json or {}))
    requirements = strategy.get_data_requirements()
    from backend.services.feature_store.service import load_market_state

    market_state = load_market_state(requirements)
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="market_data.updated",
        aggregate_type="strategy_desk",
        aggregate_id=desk_row.id,
        payload={"desk_key": desk_key, "requirements": [item.to_dict() for item in requirements], "provider": market_state.get("provider_name")},
    )
    risk_state = build_risk_state({"capital_base": float((desk_row.config_json or {}).get("capital_base") or 100000.0)})
    run_record = strategy.run(market_state=market_state, risk_state=risk_state)
    strategy_run = StrategyRun(
        tenant_id=tenant.id,
        strategy_desk_id=desk_row.id,
        desk_key=desk_key,
        run_type=run_type,
        status=run_record.status,
        timeframe_label=str((desk_row.config_json or {}).get("interval") or "1d"),
        universe_size=len(list(run_record.features.feature_rows)),
        target_count=len(run_record.targets),
        market_state_json=_serialize_market_state_for_storage(run_record.market_state),
        features_json=run_record.features.to_dict(),
        signal_json=run_record.signal.to_dict(),
        targets_json={"targets": [target.to_dict() for target in run_record.targets]},
        validation_json=run_record.validation.to_dict(),
        metrics_json=dict(run_record.metrics),
        started_at=_utc_now(),
        completed_at=_utc_now(),
    )
    db.add(strategy_run)
    db.flush()

    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="features.materialized",
        aggregate_type="strategy_run",
        aggregate_id=strategy_run.id,
        payload=run_record.features.to_dict(),
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="desk.signal_generated",
        aggregate_type="strategy_run",
        aggregate_id=strategy_run.id,
        payload=run_record.signal.to_dict(),
    )
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="risk.pretrade_passed" if run_record.validation.allowed else "risk.pretrade_rejected",
        aggregate_type="strategy_run",
        aggregate_id=strategy_run.id,
        payload=run_record.validation.to_dict(),
    )

    publication: StrategyTargetPublication | None = None
    if run_record.targets:
        prior_publications = db.execute(
            select(StrategyTargetPublication).where(
                StrategyTargetPublication.tenant_id == tenant.id,
                StrategyTargetPublication.desk_key == desk_key,
                StrategyTargetPublication.active.is_(True),
            )
        ).scalars().all()
        for prior in prior_publications:
            prior.active = False
        publication = StrategyTargetPublication(
            tenant_id=tenant.id,
            strategy_desk_id=desk_row.id,
            strategy_run_id=strategy_run.id,
            desk_key=desk_key,
            publication_kind="desk_targets",
            active=True,
            confidence_score=run_record.signal.confidence_score,
            risk_estimate=run_record.signal.risk_estimate,
            required_capital=run_record.signal.required_capital,
            expected_holding_period=run_record.signal.expected_holding_period,
            targets_json={"targets": [target.to_dict() for target in run_record.targets]},
            metrics_json=dict(run_record.metrics),
            published_at=_utc_now(),
        )
        db.add(publication)
        db.flush()
        record_domain_event(
            db,
            tenant_id=tenant.id,
            event_type="desk.targets_published",
            aggregate_type="strategy_run",
            aggregate_id=strategy_run.id,
            payload=_serialize_publication(publication) or {},
        )
        _record_pnl_snapshot(
            db,
            tenant_id=tenant.id,
            desk=desk_row,
            targets=[target.to_dict() for target in run_record.targets],
            metrics=run_record.metrics,
        )

    desk_row.runtime_json = {
        **dict(desk_row.runtime_json or {}),
        "last_run_id": strategy_run.id,
        "last_status": run_record.status,
        "last_signal_type": run_record.signal.signal_type,
        "last_generated_at": run_record.generated_at,
        "last_target_count": len(run_record.targets),
    }
    record_audit_event(
        db,
        event_type="strategy_desk.run",
        tenant=tenant,
        user=user,
        payload={"desk_key": desk_key, "run_id": strategy_run.id, "status": run_record.status},
    )
    db.commit()
    event_rows = db.execute(
        select(DomainEventLog)
        .where(DomainEventLog.tenant_id == tenant.id, DomainEventLog.aggregate_id.in_((desk_row.id, strategy_run.id)))
        .order_by(DomainEventLog.created_at.desc())
    ).scalars().all()
    return {
        "run": _serialize_strategy_run(strategy_run),
        "publication": _serialize_publication(publication),
        "events": [serialize_domain_event(event) for event in event_rows],
    }


def get_strategy_desk_metrics(db: Session, *, current_user: Any, desk_key: str) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    desk = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id, StrategyDesk.desk_key == desk_key)
    ).scalar_one_or_none()
    if desk is None:
        raise NotFoundError("The requested strategy desk could not be found.")
    publication = _latest_publication_for_desk(db, tenant_id=tenant.id, desk_key=desk_key)
    pnl_snapshot = _latest_pnl_snapshot_for_desk(db, tenant_id=tenant.id, desk_key=desk_key)
    run_rows = db.execute(
        select(StrategyRun.id)
        .where(StrategyRun.tenant_id == tenant.id, StrategyRun.desk_key == desk_key)
        .order_by(StrategyRun.created_at.desc())
    ).all()
    backtest_rows = db.execute(
        select(BacktestRun.id)
        .where(BacktestRun.tenant_id == tenant.id, BacktestRun.desk_key == desk_key)
        .order_by(BacktestRun.created_at.desc())
    ).all()
    strategy_run_ids = {str(row[0]) for row in run_rows}
    backtest_ids = {str(row[0]) for row in backtest_rows}
    candidate_events = db.execute(
        select(DomainEventLog)
        .where(
            DomainEventLog.tenant_id == tenant.id,
            DomainEventLog.aggregate_type.in_(("strategy_desk", "strategy_run", "backtest_run")),
        )
        .order_by(DomainEventLog.created_at.desc())
    ).scalars().all()[:100]
    event_rows: list[DomainEventLog] = []
    for event in candidate_events:
        payload = _coerce_dict(event.payload_json)
        aggregate_id = str(event.aggregate_id or "").strip()
        if event.aggregate_type == "strategy_desk" and aggregate_id == str(desk.id):
            event_rows.append(event)
        elif event.aggregate_type == "strategy_run" and aggregate_id in strategy_run_ids:
            event_rows.append(event)
        elif event.aggregate_type == "backtest_run" and aggregate_id in backtest_ids:
            event_rows.append(event)
        elif str(payload.get("desk_key") or "").strip().lower() == desk_key:
            event_rows.append(event)
        if len(event_rows) >= 25:
            break
    return {
        "desk_key": desk_key,
        "latest_publication": _serialize_publication(publication),
        "latest_pnl_snapshot": _serialize_pnl_snapshot(pnl_snapshot),
        "events": [serialize_domain_event(event) for event in event_rows],
    }


def run_backtest_for_desk(
    db: Session,
    *,
    current_user: Any,
    desk_key: str,
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _assert_strategy_operator(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    desk = db.execute(
        select(StrategyDesk).where(StrategyDesk.tenant_id == tenant.id, StrategyDesk.desk_key == desk_key)
    ).scalar_one_or_none()
    if desk is None:
        raise NotFoundError("The requested strategy desk could not be found.")
    result = run_backtest(desk_key=desk_key, desk_config=dict(desk.config_json or {}), request_payload=request_payload)
    backtest = BacktestRun(
        tenant_id=tenant.id,
        strategy_desk_id=desk.id,
        desk_key=desk_key,
        status=result.status,
        request_json=dict(request_payload or {}),
        summary_json=dict(result.summary),
        artifacts_json=dict(result.artifacts),
        started_at=_utc_now(),
        completed_at=_utc_now(),
    )
    db.add(backtest)
    db.flush()
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="backtest.completed",
        aggregate_type="backtest_run",
        aggregate_id=backtest.id,
        payload=_serialize_backtest_run(backtest),
    )
    db.commit()
    return _serialize_backtest_run(backtest)


def get_backtest_run_snapshot(db: Session, *, current_user: Any, run_id: str) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    run = db.execute(
        select(BacktestRun).where(BacktestRun.tenant_id == tenant.id, BacktestRun.id == run_id)
    ).scalar_one_or_none()
    if run is None:
        raise NotFoundError("The requested backtest run could not be found.")
    return _serialize_backtest_run(run)


def build_allocator_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    ensure_strategy_desks(db, current_user=current_user)
    publications = db.execute(
        select(StrategyTargetPublication)
        .where(StrategyTargetPublication.tenant_id == tenant.id, StrategyTargetPublication.active.is_(True))
        .order_by(StrategyTargetPublication.published_at.desc())
    ).scalars().all()
    raw_targets: list[DeskTargetProposal] = []
    for publication in publications:
        for item in list((publication.targets_json or {}).get("targets") or []):
            raw_targets.append(
                DeskTargetProposal(
                    desk_key=str(item.get("desk_key") or publication.desk_key),
                    symbol=str(item.get("symbol") or "").upper(),
                    direction=str(item.get("direction") or "long"),
                    target_weight=float(item.get("target_weight") or 0.0),
                    target_notional=float(item.get("target_notional") or 0.0),
                    confidence_score=float(item.get("confidence_score") or publication.confidence_score or 0.0),
                    expected_holding_period=str(item.get("expected_holding_period") or publication.expected_holding_period or ""),
                    risk_estimate=float(item.get("risk_estimate") or publication.risk_estimate or 0.0),
                    required_capital=float(item.get("required_capital") or publication.required_capital or 0.0),
                    metadata=_coerce_dict(item.get("metadata")),
                )
            )
    allocator = allocate_portfolio_targets(raw_targets)
    portfolio_run = PortfolioTargetRun(
        tenant_id=tenant.id,
        status="accepted" if allocator["risk"]["allowed"] else "blocked",
        source_run_ids_json={"publication_ids": [publication.id for publication in publications]},
        desk_inputs_json={"targets": [target.to_dict() for target in raw_targets]},
        allocator_config_json={},
        portfolio_targets_json={"targets": allocator["targets"]},
        order_plan_json={"targets": [target.get("order_plan") for target in allocator["targets"]]},
        metrics_json={**allocator["metrics"], **allocator["risk"]},
    )
    db.add(portfolio_run)
    db.flush()
    record_domain_event(
        db,
        tenant_id=tenant.id,
        event_type="allocator.targets_aggregated",
        aggregate_type="portfolio_target_run",
        aggregate_id=portfolio_run.id,
        payload={
            "metrics": allocator["metrics"],
            "risk": allocator["risk"],
            "targets": allocator["targets"],
        },
    )
    db.commit()
    return {
        "latest_run_id": portfolio_run.id,
        "status": portfolio_run.status,
        "targets": allocator["targets"],
        "metrics": allocator["metrics"],
        "risk": allocator["risk"],
    }


def get_latest_portfolio_targets(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    tenant = _resolve_tenant_for_current_user(db, current_user)
    latest = db.execute(
        select(PortfolioTargetRun)
        .where(PortfolioTargetRun.tenant_id == tenant.id)
        .order_by(PortfolioTargetRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if latest is None:
        return build_allocator_snapshot(db, current_user=current_user)
    return {
        "latest_run_id": latest.id,
        "status": latest.status,
        "targets": list((latest.portfolio_targets_json or {}).get("targets") or []),
        "metrics": dict(latest.metrics_json or {}),
        "risk": {
            "allowed": bool((latest.metrics_json or {}).get("allowed", latest.status == "accepted")),
            "gross_ok": bool((latest.metrics_json or {}).get("gross_ok", True)),
            "net_ok": bool((latest.metrics_json or {}).get("net_ok", True)),
        },
        "order_plan": dict(latest.order_plan_json or {}),
        "created_at": _iso(latest.created_at),
    }


def get_risk_snapshot(db: Session, *, current_user: Any) -> dict[str, Any]:
    _assert_strategy_reader(current_user)
    latest_targets = get_latest_portfolio_targets(db, current_user=current_user)
    targets = list(latest_targets.get("targets") or [])
    gross_exposure = sum(abs(float(target.get("target_weight") or 0.0)) for target in targets)
    net_exposure = sum(float(target.get("target_weight") or 0.0) for target in targets)
    symbol_count = len({str(target.get("symbol") or "").upper() for target in targets if str(target.get("symbol") or "").strip()})
    return {
        "gross_exposure": round(gross_exposure, 6),
        "net_exposure": round(net_exposure, 6),
        "symbol_count": symbol_count,
        "allowed": bool(latest_targets.get("risk", {}).get("allowed", True)),
        "target_count": len(targets),
        "source_run_id": latest_targets.get("latest_run_id"),
    }
