import unittest
from datetime import datetime, timezone

from backend.services import automation_limited_live_safety_ladder_service as ladder_service
from backend.services import automation_trade_readiness_service as readiness_service
from backend.services import trade_automation_service


def _ready_snapshot():
    snapshot = {
        "settings": {"kill_switch": False},
        "available_actions": {f"can_{action}": False for action in readiness_service.EXPECTED_ACTION_FLAGS},
        "limited_live_hard_faults": {"status": "clear", "blockers": [], "warnings": []},
    }
    for key, _label in readiness_service.REQUIRED_MODULES:
        snapshot[key] = {"status": "ready"}
    return snapshot


def _ready_deployment():
    return {
        "summary": {
            "status": "ready",
            "blockers": [],
            "warnings": [],
        },
        "trade_automation_route_status": {
            "status": "ready",
            "trade_automation_ready": True,
            "trade_automation_latency_ms": 120.0,
            "blockers": [],
            "warnings": [],
        },
    }


class TradeAutomationReadinessServiceTests(unittest.TestCase):
    def test_ready_snapshot_scores_all_categories_at_100(self):
        report = readiness_service.build_trade_automation_readiness_snapshot(
            _ready_snapshot(),
            route_health={"status": "ready", "load_ms": 120.0, "blockers": [], "warnings": []},
            deployment_readiness=_ready_deployment(),
        )

        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["overall_percent"], 100)
        self.assertEqual([item["percent"] for item in report["categories"]], [100, 100, 100, 100, 100])
        self.assertEqual(report["real_market_evidence_status"], "system_ready_for_evidence_collection")

    def test_missing_module_and_action_are_backend_blockers(self):
        snapshot = _ready_snapshot()
        snapshot.pop("paper_canary")
        snapshot["available_actions"].pop("can_run_ai_review")

        report = readiness_service.build_trade_automation_readiness_snapshot(
            snapshot,
            route_health={"status": "ready", "load_ms": 120.0, "blockers": [], "warnings": []},
            deployment_readiness=_ready_deployment(),
        )

        backend = next(item for item in report["categories"] if item["key"] == "backend_feature_coverage")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(backend["status"], "blocked")
        self.assertIn("Paper canary snapshot is missing.", backend["blockers"])
        self.assertIn("Trade Automation action is unavailable: run_ai_review", backend["blockers"])

    def test_hard_fault_blocks_safety_ladder(self):
        snapshot = _ready_snapshot()
        snapshot["limited_live_hard_faults"] = {
            "status": "blocked",
            "blockers": ["State control is halted."],
            "warnings": [],
        }

        report = readiness_service.build_trade_automation_readiness_snapshot(
            snapshot,
            route_health={"status": "ready", "load_ms": 120.0, "blockers": [], "warnings": []},
            deployment_readiness=_ready_deployment(),
        )

        safety = next(item for item in report["categories"] if item["key"] == "safety_risk_ladder")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(safety["status"], "blocked")
        self.assertIn("State control is halted.", safety["blockers"])

    def test_cached_rollout_readiness_keeps_snapshot_lightweight(self):
        state = {"runtime": {"rollout_readiness": {"status": "ready", "allows_live_rollout": True}}}

        cached = trade_automation_service._build_cached_rollout_readiness_for_snapshot(state)

        self.assertEqual(cached["status"], "ready")
        self.assertEqual(cached["source"], "cached_trade_automation_runtime")

    def test_hard_fault_snapshot_detects_stale_market_data_and_disables_allowances(self):
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["runtime"]["last_option_execution"] = {
            "option_quote_age_seconds": 500,
            "option_spread_pct": 0.02,
        }
        state["runtime"]["limited_live_rollout_gate_allowance"] = {
            "status": "active",
            "expires_at": datetime.now(timezone.utc).isoformat(),
        }

        report = ladder_service.build_limited_live_hard_fault_snapshot(state)

        self.assertEqual(report["status"], "blocked")
        self.assertTrue(report["should_disable_allowances"])
        self.assertTrue(any("quote age" in item.lower() for item in report["blockers"]))


if __name__ == "__main__":
    unittest.main()
