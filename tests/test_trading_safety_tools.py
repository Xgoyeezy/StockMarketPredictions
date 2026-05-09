from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from scripts import trading_safety_tools


class TradingSafetyToolsTests(unittest.TestCase):
    def _continuous_probe(self, url: str, timeout_seconds: float = 3.0) -> dict:
        payloads = {
            "/healthz": {"status": "ok"},
            "/readyz": {"status": "ok"},
            "/ops/status": {
                "status": "running",
                "running": True,
                "stale": False,
                "last_loop_at": "2026-05-04T14:00:00+00:00",
            },
            "/orgs/trade-automation/watchdog": {
                "status": "ready",
                "cards": [
                    {"key": "worker_heartbeat", "status": "ready", "metadata": {"stale": False}},
                    {"key": "reconciliation", "status": "ready", "metadata": {"current_route_orphan_order_event_count": 0}},
                    {"key": "kill_switch", "status": "ready"},
                ],
            },
            "/orgs/trade-automation/safety-state": {"status": "ready", "reason": None},
            "/orgs/trade-automation/market-session": {
                "status": "ready",
                "phase": {"phase": "active_session_monitor"},
                "evidence_million_target": {
                    "observed_event_count": 1000,
                    "live_observed_evidence": 1000,
                    "simulation_evidence": 250,
                    "target_event_count": 100000000,
                    "remaining_event_count": 99999000,
                    "simulation_counts_toward_live_million": False,
                },
            },
            "/orgs/trade-automation/desks": {"status": "ready", "items": []},
            "/orgs/trade-automation/deep-analysis/status": {"status": "ready"},
            "/orgs/trade-automation/alpaca-paper-readiness": {"status": "ready", "reconciliation_status": "clean"},
            "/orgs/trade-automation": {"settings": {"kill_switch": False}, "status": "ready"},
        }
        for suffix, payload in payloads.items():
            if url.endswith(suffix):
                return {
                    "ok": True,
                    "reachable": True,
                    "status_code": 200,
                    "url": url,
                    "payload": payload,
                    "error": None,
                }
        return {"ok": False, "reachable": False, "status_code": None, "url": url, "payload": None, "error": "missing"}

    def test_route_table_snapshot_reports_api_prefix(self) -> None:
        payload = trading_safety_tools.build_route_table_snapshot()

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["api_prefix_present"])
        self.assertGreater(payload["route_count"], 0)

    def test_weak_strong_sweep_returns_separate_buckets(self) -> None:
        payload = trading_safety_tools.build_weak_strong_sweep()

        self.assertIn("strong_failure_count", payload)
        self.assertIn("weak_failure_count", payload)
        self.assertIsInstance(payload["weak_failures"], list)
        self.assertIn("provider_scan", payload)
        self.assertIn("live_autonomy_scan", payload)

    def test_validation_report_includes_changed_files_and_route_table(self) -> None:
        payload = trading_safety_tools.build_validation_report(
            env_file=trading_safety_tools.ROOT / ".env",
            tenant_slug="systematic-equities",
        )

        self.assertIn("route_table", payload)
        self.assertIn("changed_files", payload)
        self.assertIn("weak_strong_sweep", payload)
        self.assertIn("market_day_sections", payload)
        self.assertIn("production_readiness", payload["market_day_sections"])
        self.assertIn("entry_window_explainer", payload["market_day_sections"])
        self.assertIn("next_50_enterprise_diligence", payload["market_day_sections"])
        self.assertIn("next_50_market_edge_trade_capture", payload["market_day_sections"])
        self.assertIn("next_50_research_memory_strategy_promotion", payload["market_day_sections"])
        self.assertIn("next_100_edge_factory_production_scale", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_edge", payload["market_day_sections"])
        self.assertIn("next_1000_quant_evidence_os_scale", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_compounding", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_institutional_moat", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_adaptive_edge", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_decision_intelligence", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_autonomous_improvement", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_market_adaptation", payload["market_day_sections"])
        self.assertIn("next_1000_quant_evidence_os_frontier_edge", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_trade_selection_edge", payload["market_day_sections"])
        self.assertIn("trade_selection_edge_context", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_realtime_alpha_ops", payload["market_day_sections"])
        self.assertIn("realtime_alpha_ops_context", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_adaptive_execution_intelligence", payload["market_day_sections"])
        self.assertIn("adaptive_execution_intelligence_context", payload["market_day_sections"])
        self.assertIn("next_500_quant_evidence_os_portfolio_outcome_intelligence", payload["market_day_sections"])
        self.assertIn("portfolio_outcome_intelligence_context", payload["market_day_sections"])
        self.assertIn("next_5000_quant_evidence_os_institutional_operating_edge", payload["market_day_sections"])
        self.assertIn("institutional_operating_edge_context", payload["market_day_sections"])
        self.assertIn("evidence_million_target", payload["market_day_sections"])
        self.assertIn("evidence_accelerator_context", payload["market_day_sections"])
        self.assertIn("simulation_evidence_store", payload["market_day_sections"])
        self.assertIn("market_possibility_engine_context", payload["market_day_sections"])
        self.assertIn("roadmap_evidence_activation", payload["market_day_sections"])
        self.assertIn("read_only_activation_audit", payload["market_day_sections"])

    def test_continuous_ops_computes_evidence_eta_from_observed_progress_only(self) -> None:
        now = datetime(2026, 5, 4, 16, 0, tzinfo=ZoneInfo("UTC"))
        previous = {
            "generated_at": (now - timedelta(hours=1)).isoformat(),
            "evidence_million": {"observed_event_count": 1000},
        }

        progress = trading_safety_tools._continuous_ops_evidence_progress(
            market_session={
                "evidence_million_target": {
                    "observed_event_count": 1100,
                    "target_event_count": 100000000,
                    "remaining_event_count": 998900,
                }
            },
            previous=previous,
            now=now,
        )

        self.assertEqual(progress["observed_delta_since_last_heartbeat"], 100)
        self.assertEqual(progress["simulation_counts_toward_live_million"], False)
        self.assertEqual(progress["rate_per_hour"], 100.0)
        self.assertEqual(progress["rate_source"], "real_observed_progress_delta")
        self.assertGreater(progress["eta_hours"], 0)

    def test_continuous_ops_does_not_auto_clear_kill_switch(self) -> None:
        def probe(url: str, timeout_seconds: float = 3.0) -> dict:
            payload = self._continuous_probe(url, timeout_seconds)
            if url.endswith("/orgs/trade-automation/safety-state"):
                payload["payload"] = {
                    "status": "killed",
                    "reason": "kill_switch_active",
                    "next_action": "Operator must review and clear manually.",
                }
            if url.endswith("/orgs/trade-automation"):
                payload["payload"] = {"settings": {"kill_switch": True}, "status": "killed"}
            return payload

        with (
            patch.object(trading_safety_tools, "_probe_json_url", side_effect=probe),
            patch.object(trading_safety_tools, "write_continuous_ops_artifacts", return_value={"written": True}),
            patch.object(trading_safety_tools, "_read_json_file", return_value={}),
            patch.object(trading_safety_tools, "_continuous_ops_restart_backend") as restart,
        ):
            payload = trading_safety_tools.build_continuous_watch_snapshot(
                env_file=trading_safety_tools.ROOT / ".env",
                allow_restart=True,
            )

        self.assertEqual(payload["status"], "killed")
        self.assertTrue(payload["kill_switch"]["active"])
        self.assertTrue(payload["kill_switch"]["ready_for_operator_clear"])
        self.assertFalse(payload["safe_recovery_policy"]["auto_clear_kill_switch"])
        self.assertFalse(payload["can_submit_orders"])
        restart.assert_not_called()

    def test_continuous_ops_restarts_managed_backend_for_stale_worker(self) -> None:
        def probe(url: str, timeout_seconds: float = 3.0) -> dict:
            payload = self._continuous_probe(url, timeout_seconds)
            if url.endswith("/ops/status"):
                payload["payload"] = {
                    "status": "running_but_stale",
                    "running": True,
                    "stale": True,
                    "stale_seconds": 420,
                    "last_loop_at": "2026-05-04T13:53:00+00:00",
                }
            return payload

        restart_payload = {"attempted": True, "ok": True, "reason": "worker_stale", "at": "2026-05-04T14:00:00+00:00"}
        with (
            patch.object(trading_safety_tools, "_probe_json_url", side_effect=probe),
            patch.object(trading_safety_tools, "write_continuous_ops_artifacts", return_value={"written": True}),
            patch.object(trading_safety_tools, "_read_json_file", return_value={}),
            patch.object(trading_safety_tools, "_continuous_ops_restart_backend", return_value=restart_payload) as restart,
        ):
            payload = trading_safety_tools.build_continuous_watch_snapshot(
                env_file=trading_safety_tools.ROOT / ".env",
                allow_restart=True,
                restart_cooldown_seconds=300,
            )

        restart.assert_called_once()
        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(payload["worker"]["stale"])
        self.assertEqual(payload["supervisor"]["restart_action"]["reason"], "worker_stale")
        self.assertEqual(payload["supervisor"]["restart_count"], 1)

    def test_market_session_report_includes_market_ops_routes(self) -> None:
        payload = trading_safety_tools.build_market_session_report(
            env_file=trading_safety_tools.ROOT / ".env",
            tenant_slug="systematic-equities",
        )

        self.assertFalse(payload["read_only"])
        self.assertEqual(payload["mutation"], "paper_evidence_state")
        self.assertTrue(payload["paper_route_only"])
        self.assertIn("market_session", payload["api_links"])
        self.assertIn("missing_market_ops_routes", payload["route_table"])
        self.assertIn("entry_window_explainer", payload)
        self.assertIn("production_readiness", payload)
        self.assertIn("proof_sections", payload["production_readiness"])
        self.assertIn("live_api_state", payload)
        self.assertIn("readiness_cache", payload)
        self.assertIn("runtime_supervisor", payload)
        self.assertIn("expected_settings_proof", payload)
        self.assertIn("incident_timeline", payload)
        self.assertIn("close_artifact_index", payload)
        self.assertIn("production_weakness_closure", payload)
        self.assertEqual(payload["production_weakness_closure"]["item_count"], 50)
        self.assertFalse(payload["production_weakness_closure"]["read_only"])
        self.assertTrue(payload["production_weakness_closure"]["paper_operational"])
        self.assertEqual(payload["production_weakness_closure"]["mutation"], "paper_evidence_state")
        self.assertTrue(payload["production_weakness_closure"]["can_write_artifacts"])
        self.assertFalse(payload["production_weakness_closure"]["can_submit_orders"])
        self.assertIn("roadmap_evidence_activation", payload)
        activation = payload["roadmap_evidence_activation"]
        self.assertEqual(activation["active_bundle_count"], activation["bundle_count"])
        self.assertGreaterEqual(activation["active_item_count"], 12900)
        self.assertEqual(activation["mutation"], "paper_evidence_state")
        self.assertTrue(activation["can_write_artifacts"])
        self.assertFalse(activation["can_submit_orders"])
        self.assertFalse(activation["can_submit_live_orders"])
        self.assertIn("read_only_activation_audit", payload)
        activation_audit = payload["read_only_activation_audit"]
        self.assertEqual(activation_audit["checked_bundle_count"], activation["bundle_count"])
        self.assertEqual(activation_audit["active_count"], activation["active_bundle_count"])
        self.assertEqual(activation_audit["read_only_count"], 0)
        self.assertEqual(activation_audit["inactive_count"], 0)
        self.assertEqual(activation_audit["item_read_only_count"], 0)
        self.assertEqual(activation_audit["inactive_item_count"], 0)
        self.assertGreaterEqual(activation_audit["active_item_count"], 12900)
        self.assertFalse(activation_audit["can_submit_orders"])
        self.assertFalse(activation_audit["can_submit_live_orders"])
        self.assertIn("evidence_million_target", payload)
        evidence_million = payload["evidence_million_target"]
        self.assertEqual(evidence_million["target_event_count"], 100000000)
        self.assertEqual(evidence_million["usage_mode"], "evidence_memory_target")
        self.assertEqual(evidence_million["mutation"], "paper_evidence_state")
        self.assertTrue(evidence_million["can_write_artifacts"])
        self.assertFalse(evidence_million["writes_trade_state"])
        self.assertFalse(evidence_million["can_submit_orders"])
        self.assertFalse(evidence_million["can_submit_live_orders"])
        self.assertTrue(evidence_million["paper_route_only"])
        self.assertIn("live_observed_evidence", evidence_million)
        self.assertIn("simulation_evidence", evidence_million)
        self.assertFalse(evidence_million["simulation_counts_toward_live_million"])
        self.assertIn("evidence_quality", evidence_million)
        self.assertIn("evidence_accelerator_context", payload)
        self.assertFalse(payload["evidence_accelerator_context"]["can_submit_orders"])
        self.assertIn("simulation_evidence_store", payload)
        self.assertFalse(payload["simulation_evidence_store"]["counts_toward_live_million"])
        self.assertIn("market_possibility_engine_context", payload)
        self.assertFalse(payload["market_possibility_engine_context"]["can_submit_orders"])
        self.assertIn("next_50_trading_intelligence", payload)
        self.assertEqual(payload["next_50_trading_intelligence"]["item_count"], 50)
        self.assertFalse(payload["next_50_trading_intelligence"]["read_only"])
        self.assertTrue(payload["next_50_trading_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_50_trading_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_50_trading_intelligence"]["can_submit_orders"])
        self.assertIn("next_50_institutional_edge", payload)
        self.assertEqual(payload["next_50_institutional_edge"]["item_count"], 50)
        self.assertFalse(payload["next_50_institutional_edge"]["can_submit_orders"])
        self.assertIn("next_50_enterprise_diligence", payload)
        self.assertEqual(payload["next_50_enterprise_diligence"]["item_count"], 50)
        self.assertFalse(payload["next_50_enterprise_diligence"]["can_submit_orders"])
        self.assertIn("next_50_market_edge_trade_capture", payload)
        self.assertEqual(payload["next_50_market_edge_trade_capture"]["item_count"], 50)
        self.assertFalse(payload["next_50_market_edge_trade_capture"]["can_submit_orders"])
        self.assertIn("next_50_research_memory_strategy_promotion", payload)
        self.assertEqual(payload["next_50_research_memory_strategy_promotion"]["item_count"], 50)
        self.assertFalse(payload["next_50_research_memory_strategy_promotion"]["can_submit_orders"])
        self.assertIn("next_100_edge_factory_production_scale", payload)
        self.assertEqual(payload["next_100_edge_factory_production_scale"]["item_count"], 100)
        self.assertEqual(payload["next_100_edge_factory_production_scale"]["live_item_count"], 100)
        self.assertEqual(payload["next_100_edge_factory_production_scale"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_100_edge_factory_production_scale"]["read_only"])
        self.assertTrue(payload["next_100_edge_factory_production_scale"]["paper_operational"])
        self.assertEqual(payload["next_100_edge_factory_production_scale"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_100_edge_factory_production_scale"]["can_submit_orders"])
        self.assertFalse(payload["next_100_edge_factory_production_scale"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_edge", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_edge"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_edge"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_edge"]["can_submit_live_orders"])
        self.assertIn("next_1000_quant_evidence_os_scale", payload)
        self.assertEqual(payload["next_1000_quant_evidence_os_scale"]["item_count"], 1000)
        self.assertEqual(payload["next_1000_quant_evidence_os_scale"]["workstream_count"], 40)
        self.assertEqual(payload["next_1000_quant_evidence_os_scale"]["live_item_count"], 1000)
        self.assertEqual(payload["next_1000_quant_evidence_os_scale"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_1000_quant_evidence_os_scale"]["can_submit_orders"])
        self.assertFalse(payload["next_1000_quant_evidence_os_scale"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_compounding", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["prior_scale_item_count"], 1000)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["cumulative_scale_item_count"], 1500)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_compounding"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_compounding"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_institutional_moat", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["prior_cumulative_item_count"], 1500)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["cumulative_scale_item_count"], 2000)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_institutional_moat"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_institutional_moat"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_adaptive_edge", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["prior_cumulative_item_count"], 2000)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["cumulative_scale_item_count"], 2500)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_edge"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_adaptive_edge"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_edge"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_decision_intelligence", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["prior_cumulative_item_count"], 2500)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["cumulative_scale_item_count"], 3000)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_decision_intelligence"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_decision_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_decision_intelligence"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_decision_intelligence"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_autonomous_improvement", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["prior_cumulative_item_count"], 3000)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["cumulative_scale_item_count"], 3500)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_autonomous_improvement"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_autonomous_improvement"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_autonomous_improvement"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_autonomous_improvement"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_market_adaptation", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["prior_cumulative_item_count"], 3500)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["cumulative_scale_item_count"], 4000)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_market_adaptation"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_market_adaptation"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_market_adaptation"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_market_adaptation"]["can_submit_live_orders"])
        self.assertIn("next_1000_quant_evidence_os_frontier_edge", payload)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["item_count"], 1000)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["workstream_count"], 40)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["prior_cumulative_item_count"], 4000)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["cumulative_scale_item_count"], 5000)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["live_item_count"], 1000)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_1000_quant_evidence_os_frontier_edge"]["read_only"])
        self.assertTrue(payload["next_1000_quant_evidence_os_frontier_edge"]["paper_operational"])
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_1000_quant_evidence_os_frontier_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_1000_quant_evidence_os_frontier_edge"]["can_submit_live_orders"])
        self.assertIn("next_500_quant_evidence_os_trade_selection_edge", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["prior_cumulative_item_count"], 5000)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["cumulative_scale_item_count"], 5500)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_trade_selection_edge"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_trade_selection_edge"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_trade_selection_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_trade_selection_edge"]["can_submit_live_orders"])
        self.assertIn("trade_selection_edge_context", payload)
        self.assertEqual(payload["trade_selection_edge_context"]["usage_mode"], "influence_ranking")
        self.assertFalse(payload["trade_selection_edge_context"]["can_submit_orders"])
        self.assertIn("next_500_quant_evidence_os_realtime_alpha_ops", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["prior_cumulative_item_count"], 6400)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["cumulative_scale_item_count"], 6900)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["can_submit_live_orders"])
        self.assertIn("realtime_alpha_ops_context", payload)
        self.assertEqual(payload["realtime_alpha_ops_context"]["usage_mode"], "influence_ranking")
        self.assertFalse(payload["realtime_alpha_ops_context"]["can_submit_orders"])
        self.assertIn("next_500_quant_evidence_os_adaptive_execution_intelligence", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["prior_cumulative_item_count"], 6900)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["cumulative_scale_item_count"], 7400)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["can_submit_live_orders"])
        self.assertIn("adaptive_execution_intelligence_context", payload)
        self.assertEqual(payload["adaptive_execution_intelligence_context"]["usage_mode"], "influence_ranking_and_allocator")
        self.assertFalse(payload["adaptive_execution_intelligence_context"]["can_submit_orders"])
        self.assertIn("next_500_quant_evidence_os_portfolio_outcome_intelligence", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["workstream_count"], 20)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["prior_cumulative_item_count"], 7400)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["cumulative_scale_item_count"], 7900)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["live_item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["can_submit_live_orders"])
        self.assertIn("portfolio_outcome_intelligence_context", payload)
        self.assertEqual(payload["portfolio_outcome_intelligence_context"]["usage_mode"], "influence_portfolio_ranking_and_allocator")
        self.assertFalse(payload["portfolio_outcome_intelligence_context"]["can_submit_orders"])
        self.assertIn("next_5000_quant_evidence_os_institutional_operating_edge", payload)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["item_count"], 5000)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["implemented_count"], 5000)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["workstream_count"], 100)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["items_per_workstream"], 50)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["prior_cumulative_item_count"], 7900)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["cumulative_scale_item_count"], 12900)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["live_item_count"], 5000)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["live_enabled_count"], 0)
        self.assertFalse(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["read_only"])
        self.assertTrue(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["paper_operational"])
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["can_submit_live_orders"])
        self.assertIn("institutional_operating_edge_context", payload)
        self.assertEqual(payload["institutional_operating_edge_context"]["usage_mode"], "influence_operating_ranking_allocator_and_market_ops")
        self.assertFalse(payload["institutional_operating_edge_context"]["can_submit_orders"])
        self.assertIn("monday_open_rehearsal", payload)

    def test_no_trade_report_is_local_read_only_proof(self) -> None:
        payload = trading_safety_tools.build_no_trade_report(tenant_slug="systematic-equities")

        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["mutation"], "none")
        self.assertFalse(payload["can_submit_orders"])
        self.assertIn("api_detail_endpoint", payload)
        self.assertIn("opportunity_refresh", payload)
        self.assertTrue(payload["opportunity_refresh"]["read_only"])
        self.assertIn("missed_move_intelligence", payload)
        self.assertIn("candidate_lifecycle_artifact", payload)
        self.assertIn("missed_move_leaderboard", payload)

    def test_market_day_report_writes_artifact(self) -> None:
        payload = trading_safety_tools.build_market_day_report(
            env_file=trading_safety_tools.ROOT / ".env",
            tenant_slug="systematic-equities",
        )

        self.assertIn("market_session", payload)
        self.assertIn("no_trade_report", payload)
        self.assertIn("post_close_proof_sections", payload)
        self.assertIn("readiness_cache", payload)
        self.assertIn("runtime_supervisor", payload)
        self.assertIn("missed_move_leaderboard", payload)
        self.assertIn("production_weakness_closure", payload)
        self.assertEqual(payload["production_weakness_closure"]["item_count"], 50)
        self.assertFalse(payload["production_weakness_closure"]["read_only"])
        self.assertTrue(payload["production_weakness_closure"]["paper_operational"])
        self.assertIn("roadmap_evidence_activation", payload)
        self.assertEqual(
            payload["roadmap_evidence_activation"]["active_bundle_count"],
            payload["roadmap_evidence_activation"]["bundle_count"],
        )
        self.assertIn("read_only_activation_audit", payload)
        self.assertEqual(payload["read_only_activation_audit"]["read_only_count"], 0)
        self.assertEqual(payload["read_only_activation_audit"]["inactive_count"], 0)
        self.assertEqual(payload["read_only_activation_audit"]["item_read_only_count"], 0)
        self.assertEqual(payload["read_only_activation_audit"]["inactive_item_count"], 0)
        self.assertIn("next_50_trading_intelligence", payload)
        self.assertEqual(payload["next_50_trading_intelligence"]["item_count"], 50)
        self.assertIn("next_50_institutional_edge", payload)
        self.assertEqual(payload["next_50_institutional_edge"]["item_count"], 50)
        self.assertIn("next_50_enterprise_diligence", payload)
        self.assertEqual(payload["next_50_enterprise_diligence"]["item_count"], 50)
        self.assertIn("next_50_market_edge_trade_capture", payload)
        self.assertEqual(payload["next_50_market_edge_trade_capture"]["item_count"], 50)
        self.assertIn("next_50_research_memory_strategy_promotion", payload)
        self.assertEqual(payload["next_50_research_memory_strategy_promotion"]["item_count"], 50)
        self.assertIn("next_100_edge_factory_production_scale", payload)
        self.assertEqual(payload["next_100_edge_factory_production_scale"]["item_count"], 100)
        self.assertEqual(payload["next_100_edge_factory_production_scale"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_edge", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_edge"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_edge"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_edge"]["live_enabled_count"], 0)
        self.assertIn("next_1000_quant_evidence_os_scale", payload)
        self.assertEqual(payload["next_1000_quant_evidence_os_scale"]["item_count"], 1000)
        self.assertEqual(payload["next_1000_quant_evidence_os_scale"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_compounding", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_compounding"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_institutional_moat", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["item_count"], 500)
        self.assertEqual(payload["next_500_quant_evidence_os_institutional_moat"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_adaptive_edge", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_edge"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_adaptive_edge"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_edge"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_decision_intelligence", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_decision_intelligence"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_decision_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_decision_intelligence"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_autonomous_improvement", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_autonomous_improvement"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_autonomous_improvement"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_autonomous_improvement"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_market_adaptation", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_market_adaptation"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_market_adaptation"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_market_adaptation"]["live_enabled_count"], 0)
        self.assertIn("next_1000_quant_evidence_os_frontier_edge", payload)
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["item_count"], 1000)
        self.assertFalse(payload["next_1000_quant_evidence_os_frontier_edge"]["read_only"])
        self.assertTrue(payload["next_1000_quant_evidence_os_frontier_edge"]["paper_operational"])
        self.assertEqual(payload["next_1000_quant_evidence_os_frontier_edge"]["live_enabled_count"], 0)
        self.assertIn("next_500_quant_evidence_os_trade_selection_edge", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_trade_selection_edge"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_trade_selection_edge"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_trade_selection_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_trade_selection_edge"]["can_submit_live_orders"])
        self.assertEqual(payload["next_500_quant_evidence_os_trade_selection_edge"]["live_enabled_count"], 0)
        self.assertIn("trade_selection_edge_context", payload)
        self.assertEqual(payload["trade_selection_edge_context"]["usage_mode"], "influence_ranking")
        self.assertFalse(payload["trade_selection_edge_context"]["can_submit_orders"])
        self.assertTrue(payload["trade_selection_edge_context"]["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_500_quant_evidence_os_realtime_alpha_ops", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["can_submit_live_orders"])
        self.assertEqual(payload["next_500_quant_evidence_os_realtime_alpha_ops"]["live_enabled_count"], 0)
        self.assertIn("realtime_alpha_ops_context", payload)
        self.assertEqual(payload["realtime_alpha_ops_context"]["usage_mode"], "influence_ranking")
        self.assertFalse(payload["realtime_alpha_ops_context"]["can_submit_orders"])
        self.assertTrue(payload["realtime_alpha_ops_context"]["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_500_quant_evidence_os_adaptive_execution_intelligence", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["can_submit_live_orders"])
        self.assertEqual(payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]["live_enabled_count"], 0)
        self.assertIn("adaptive_execution_intelligence_context", payload)
        self.assertEqual(payload["adaptive_execution_intelligence_context"]["usage_mode"], "influence_ranking_and_allocator")
        self.assertFalse(payload["adaptive_execution_intelligence_context"]["can_submit_orders"])
        self.assertTrue(payload["adaptive_execution_intelligence_context"]["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_500_quant_evidence_os_portfolio_outcome_intelligence", payload)
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["item_count"], 500)
        self.assertFalse(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["read_only"])
        self.assertTrue(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["can_submit_orders"])
        self.assertFalse(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["can_submit_live_orders"])
        self.assertEqual(payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]["live_enabled_count"], 0)
        self.assertIn("portfolio_outcome_intelligence_context", payload)
        self.assertEqual(payload["portfolio_outcome_intelligence_context"]["usage_mode"], "influence_portfolio_ranking_and_allocator")
        self.assertFalse(payload["portfolio_outcome_intelligence_context"]["can_submit_orders"])
        self.assertTrue(payload["portfolio_outcome_intelligence_context"]["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_5000_quant_evidence_os_institutional_operating_edge", payload)
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["item_count"], 5000)
        self.assertFalse(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["read_only"])
        self.assertTrue(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["paper_operational"])
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["can_submit_live_orders"])
        self.assertEqual(payload["next_5000_quant_evidence_os_institutional_operating_edge"]["live_enabled_count"], 0)
        self.assertIn("institutional_operating_edge_context", payload)
        self.assertEqual(payload["institutional_operating_edge_context"]["usage_mode"], "influence_operating_ranking_allocator_and_market_ops")
        self.assertFalse(payload["institutional_operating_edge_context"]["can_submit_orders"])
        self.assertTrue(payload["institutional_operating_edge_context"]["score_influence"]["hard_gates_remain_authoritative"])
        self.assertTrue(payload["artifact"]["written"])
        self.assertTrue(payload["artifact"]["path"].endswith("market-day-report.json"))


if __name__ == "__main__":
    unittest.main()
