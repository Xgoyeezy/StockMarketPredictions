from __future__ import annotations

import unittest

from backend.models.saas import BrokerageLinkedAccount, ReadinessBlocker, ReadinessSnapshot, RiskPolicy, StrategyDesk
from backend.services.strategy_readiness_score_service import (
    apply_hard_blocker_caps,
    calculate_weighted_score,
    derive_readiness_status,
    evaluate_strategy_readiness,
)
from tests.productized_control_plane_test_support import build_test_context


class StrategyReadinessScoreServiceTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(self, "context"):
            self.context.close()

    def test_weighted_score_math_and_hard_caps(self) -> None:
        components = {
            "broker_connectivity": 100,
            "data_freshness": 80,
            "capital_exposure": 60,
            "risk_controls": 90,
            "paper_evidence": 70,
            "execution_quality": 50,
            "audit_integrity": 100,
        }

        self.assertEqual(calculate_weighted_score(components), 80)
        self.assertEqual(apply_hard_blocker_caps(95, [{"key": "no_linked_broker"}]), 45)
        self.assertEqual(apply_hard_blocker_caps(95, [{"key": "kill_switch_active"}, {"key": "stale_data"}]), 35)
        self.assertEqual(derive_readiness_status(76, [], trading_mode="paper"), "validated")

    def test_evaluate_strategy_readiness_persists_snapshot_and_blockers(self) -> None:
        self.context = build_test_context(slug="readiness-service-test", plan_key="professional")
        db = self.context.db
        tenant = self.context.tenant
        user = self.context.user
        strategy = StrategyDesk(
            tenant_id=tenant.id,
            desk_key="readiness-alpha",
            name="Readiness Alpha",
            category="productized",
            lifecycle_stage="validated",
            trading_mode="live_enabled",
            paper_trading_enabled=True,
            config_json={"symbols": ["SPY"], "allocation_cap": 25000},
            runtime_json={
                "paper_evidence": {"closed_trade_count": 25, "session_count": 4, "evidence_coverage_pct": 90},
                "audit_replay_active": True,
                "execution_quality_score": 82,
                "balance_verified": True,
            },
        )
        db.add(strategy)
        db.flush()
        db.add(
            BrokerageLinkedAccount(
                tenant_id=tenant.id,
                owner_user_id=user.id,
                provider="alpaca",
                account_environment="paper",
                connection_status="active",
                external_account_id="paper-1",
            )
        )
        db.add(
            RiskPolicy(
                tenant_id=tenant.id,
                strategy_desk_id=strategy.id,
                scope="strategy",
                status="active",
                max_daily_loss=500,
                max_order_notional=5000,
            )
        )
        db.commit()

        snapshot = evaluate_strategy_readiness(db, current_user=self.context.current_user, strategy_id=strategy.id)

        self.assertGreaterEqual(snapshot["score"], 90)
        self.assertIn(snapshot["status"], {"scale_ready", "live_ready"})
        self.assertFalse([item for item in snapshot["blockers"] if item["severity"] == "critical"])
        self.assertEqual(db.query(ReadinessSnapshot).count(), 1)
        self.assertEqual(db.query(ReadinessBlocker).count(), 0)

    def test_missing_broker_and_paper_evidence_block_readiness(self) -> None:
        self.context = build_test_context(slug="readiness-blocker-test", plan_key="professional")
        db = self.context.db
        strategy = StrategyDesk(
            tenant_id=self.context.tenant.id,
            desk_key="blocked-alpha",
            name="Blocked Alpha",
            category="productized",
            lifecycle_stage="draft",
            trading_mode="paper",
            config_json={"symbols": ["SPY"]},
            runtime_json={},
        )
        db.add(strategy)
        db.commit()

        snapshot = evaluate_strategy_readiness(db, current_user=self.context.current_user, strategy_id=strategy.id)

        self.assertEqual(snapshot["status"], "blocked")
        self.assertLessEqual(snapshot["score"], 45)
        self.assertIn("no_linked_broker", {item["key"] for item in snapshot["blockers"]})
        self.assertIn("no_paper_evidence", {item["key"] for item in snapshot["blockers"]})


if __name__ == "__main__":
    unittest.main()
