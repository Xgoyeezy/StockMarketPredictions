from __future__ import annotations

import unittest

from backend.services.strategy_readiness_score_service import evaluate_promotion_rules


class StrategyPromotionGateTests(unittest.TestCase):
    def test_draft_to_paper_requires_score_policy_and_audit(self) -> None:
        result = evaluate_promotion_rules(
            from_stage="draft",
            to_stage="paper",
            score=60,
            blockers=[],
            paper_state={},
            broker_state={},
            risk_state={"policy_active": True},
            audit_state={"decision_logging_ok": True},
            execution_quality_score=0,
        )

        self.assertTrue(result["can_promote"])
        self.assertEqual(result["status"], "approved")

    def test_paper_to_validated_requires_paper_evidence(self) -> None:
        blocked = evaluate_promotion_rules(
            from_stage="paper",
            to_stage="validated",
            score=80,
            blockers=[],
            paper_state={"closed_trade_count": 19, "session_count": 3, "evidence_coverage_pct": 85},
            broker_state={},
            risk_state={},
            audit_state={},
            execution_quality_score=80,
        )
        approved = evaluate_promotion_rules(
            from_stage="paper",
            to_stage="validated",
            score=80,
            blockers=[],
            paper_state={"closed_trade_count": 20, "session_count": 3, "evidence_coverage_pct": 81},
            broker_state={},
            risk_state={},
            audit_state={},
            execution_quality_score=80,
        )

        self.assertFalse(blocked["can_promote"])
        self.assertTrue(approved["can_promote"])

    def test_validated_to_live_candidate_requires_broker_risk_audit_and_execution_quality(self) -> None:
        result = evaluate_promotion_rules(
            from_stage="validated",
            to_stage="live_candidate",
            score=85,
            blockers=[],
            paper_state={},
            broker_state={"connection_status": "active", "balance_verified": True},
            risk_state={"policy_active": True},
            audit_state={"audit_replay_active": True},
            execution_quality_score=71,
        )

        self.assertTrue(result["can_promote"])

    def test_live_candidate_to_scaled_live_requires_locked_version_and_manual_approval(self) -> None:
        result = evaluate_promotion_rules(
            from_stage="live_candidate",
            to_stage="scaled_live",
            score=92,
            blockers=[],
            paper_state={"live_candidate_sessions": 10},
            broker_state={},
            risk_state={"critical_risk_breach": False, "slippage_inside_policy": True},
            audit_state={},
            execution_quality_score=90,
            version_state={"version_locked": True, "manual_approval_recorded": True},
        )

        self.assertTrue(result["can_promote"])


if __name__ == "__main__":
    unittest.main()
