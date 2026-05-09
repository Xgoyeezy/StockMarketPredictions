from __future__ import annotations

from dataclasses import dataclass

from backend.models.saas import BrokerageLinkedAccount, ReadinessSnapshot, RiskPolicy, StrategyDesk, StrategyVersion


@dataclass
class LiveReadyFixture:
    strategy: StrategyDesk
    version: StrategyVersion
    account: BrokerageLinkedAccount
    policy: RiskPolicy
    readiness: ReadinessSnapshot


def seed_live_ready_strategy(context, *, desk_key: str = "live-ready-strategy", auto_approval: bool = False) -> LiveReadyFixture:
    db = context.db
    account = BrokerageLinkedAccount(
        tenant_id=context.tenant.id,
        owner_user_id=context.user.id,
        provider="alpaca",
        label="Alpaca live test",
        account_environment="live",
        connection_status="active",
        token_health="healthy",
        external_account_id=f"acct-{desk_key}",
    )
    strategy = StrategyDesk(
        tenant_id=context.tenant.id,
        desk_key=desk_key,
        name="Live Ready Strategy",
        category="productized",
        lifecycle_stage="live_candidate",
        run_mode="manual",
        trading_mode="live_enabled",
        enabled=True,
        paper_trading_enabled=False,
        config_json={"symbols": ["AAPL", "MSFT"], "allocation_cap": 25000, "risk_profile": {"max_order_notional": 5000}},
        runtime_json={
            "paper_evidence": {"closed_trade_count": 25, "session_count": 4, "evidence_coverage_pct": 95},
            "audit_replay_active": True,
            "audit_logging_available": True,
            "balance_verified": True,
            "execution_quality_score": 82,
            "options_quotes_and_liquidity_ok": True,
            "stale_data": False,
        },
        metadata_json={"status": "live_candidate"},
    )
    db.add_all([account, strategy])
    db.flush()
    version = StrategyVersion(
        tenant_id=context.tenant.id,
        strategy_desk_id=strategy.id,
        version_number=1,
        name="Live Ready Strategy v1",
        status="active",
        config_json=dict(strategy.config_json or {}),
        risk_profile_json={"max_order_notional": 5000},
        created_by_user_id=context.user.id,
    )
    policy = RiskPolicy(
        tenant_id=context.tenant.id,
        strategy_desk_id=strategy.id,
        scope="strategy",
        status="active",
        max_daily_loss=500,
        max_weekly_loss=1000,
        max_drawdown_pct=4,
        max_position_notional=10000,
        max_order_notional=5000,
        max_open_positions=5,
        allowed_symbols_json=["AAPL", "MSFT"],
        blocked_symbols_json=[],
        allowed_instruments_json=["EQUITY"],
        requires_approval_above=0,
        config_json={"allow_policy_auto_approval": auto_approval},
    )
    readiness = ReadinessSnapshot(
        tenant_id=context.tenant.id,
        strategy_desk_id=strategy.id,
        score=90,
        status="live_candidate",
        recommendation="Ready for supervised live candidate flow.",
        components_json={
            "broker_connectivity": 100,
            "data_freshness": 100,
            "capital_exposure": 85,
            "risk_controls": 90,
            "paper_evidence": 90,
            "execution_quality": 82,
            "audit_integrity": 100,
        },
        hard_blockers_json=[],
        warnings_json=[],
    )
    db.add_all([version, policy, readiness])
    db.commit()
    return LiveReadyFixture(strategy=strategy, version=version, account=account, policy=policy, readiness=readiness)
