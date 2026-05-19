from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import Tenant
from backend.services.exceptions import ValidationError
from backend.services import automation_daily_objective_service, trade_automation_service


class TradeAutomationMultiDeskSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine, future=True)
        self.db = SessionLocal()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        self.tenant = Tenant(slug="multi-desk", name="Multi Desk", status="active")
        self.db.add(self.tenant)
        self.db.commit()
        self.db.refresh(self.tenant)
        self.current_user = SimpleNamespace(
            tenant_id=self.tenant.id,
            tenant_slug=self.tenant.slug,
            auth_subject="multi-desk-user",
            user_id="multi-desk-user",
            email="multi-desk@example.test",
            name="Multi Desk User",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=("tenant.manage_support", "trade.execute", "market.read", "tenant.read"),
        )
        self._clear_deep_analysis_state()
        self.addCleanup(self._clear_deep_analysis_state)

    def _clear_deep_analysis_state(self) -> None:
        with trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_LOCK:
            trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_CACHE.clear()
            trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_INFLIGHT.clear()
            trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_CIRCUIT_OPEN_UNTIL = None
            trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_LAST_ERROR = None

    def test_default_desks_have_distinct_cadences_and_risk_budgets(self) -> None:
        payload = trade_automation_service.list_tenant_trade_automation_desks(
            self.db,
            current_user=self.current_user,
        )

        desks = {item["desk_key"]: item for item in payload["items"]}
        self.assertEqual(set(desks), {"fast_scalper", "stat_arb", "intraday_momentum", "swing_position", "macro"})
        self.assertEqual(desks["fast_scalper"]["cadence"]["interval"], "1m")
        self.assertEqual(desks["fast_scalper"]["cadence"]["cycle_interval_seconds"], 30)
        self.assertEqual(len(desks["fast_scalper"]["allowed_tickers"]), 45)
        self.assertEqual(desks["intraday_momentum"]["cadence"]["interval"], "5m")
        self.assertEqual(desks["macro"]["cadence"]["interval"], "1d")
        self.assertTrue(desks["macro"]["schedule"]["market_aware"])
        self.assertEqual(desks["macro"]["schedule"]["entry_scan_time_et"], "10:15")
        self.assertIn("Market-aware", desks["macro"]["schedule_detail"])
        self.assertEqual(desks["fast_scalper"]["objective_role"], "primary_intraday")
        self.assertEqual(desks["stat_arb"]["objective_role"], "primary_intraday")
        self.assertEqual(desks["intraday_momentum"]["objective_role"], "primary_intraday")
        self.assertEqual(desks["swing_position"]["objective_role"], "secondary_position")
        self.assertEqual(desks["macro"]["objective_role"], "carry_macro")
        self.assertIn("runtime_counters", desks["fast_scalper"])
        self.assertIn("actions", desks["fast_scalper"])
        self.assertIn("execution_intelligence", desks["fast_scalper"])
        self.assertIn("trade_readiness", desks["fast_scalper"]["execution_intelligence"])
        self.assertEqual(desks["fast_scalper"]["execution_intelligence"]["handoff_evidence"]["paper_execution_route"], "broker_paper")
        self.assertIn("allocator_snapshot", payload["global"])
        self.assertIn("position_policy", payload["global"])
        self.assertIn("position_promotion", payload["global"])
        self.assertEqual(payload["global"]["position_policy"]["mode"], "institutional_risk_allocated")
        self.assertEqual(payload["global"]["position_policy"]["recommended_max_open_positions"], 12)
        self.assertEqual(payload["global"]["position_promotion"]["auto_promotion_mode"], "paper_only")
        self.assertIn("quant_evidence_control_plane", payload["global"])
        self.assertEqual(payload["global"]["quant_evidence_control_plane"]["product_name"], "Quant Evidence Operating System")
        self.assertIn("due_desks", payload["global"])
        self.assertIn("desk_intelligence", payload["global"])
        self.assertIn("trade_readiness_counts", payload["global"]["desk_intelligence"])
        self.assertEqual(desks["macro"]["overnight_exposure_warning"].startswith("This desk can hold overnight"), True)
        self.assertEqual(payload["global"]["daily_objective"]["objective_mode"], "collective_account_weekly_1_to_2pct")
        self.assertEqual(payload["global"]["risk_budget_total_pct"], 100.0)
        self.assertEqual(payload["global"]["max_open_notional_total_pct"], 100.0)
        self.assertEqual(len(payload["active_desks"]), 5)
        catalog = {item["desk_key"]: item for item in payload["desk_catalog"]}
        for desk_key in {
            "equity_long_short",
            "etf_index",
            "volatility_options",
            "event_driven",
            "relative_value",
            "sector_rotation",
            "macro_proxy",
            "algorithmic_execution",
        }:
            self.assertIn(desk_key, catalog)
        self.assertEqual(catalog["fast_scalper"]["execution_status"], "active")
        self.assertEqual(catalog["macro_proxy"]["execution_status"], "proxy_only")
        self.assertEqual(catalog["volatility_options"]["execution_status"], "research_only")
        self.assertEqual(catalog["spot_fx"]["execution_status"], "unsupported")
        self.assertFalse(catalog["macro_proxy"]["can_submit_orders"])
        self.assertEqual(catalog["fast_scalper"]["paper_execution_route"], "broker_paper")
        self.assertTrue(payload["global"]["institutional_catalog"]["enabled"])
        for entry in catalog.values():
            self.assertEqual(
                set(entry["engine_coverage"]),
                {"fast_scalper", "stat_arb", "intraday_momentum", "swing_position", "macro"},
            )
            self.assertIn("engine_coverage_summary", entry)
            self.assertIn("primary_engines", entry)
            self.assertIn("disabled_engines", entry)
            self.assertIn("support_maturity", entry)
            self.assertIn("provider_capability", entry)
            self.assertIn("data_requirements", entry)
            self.assertIn("promotion_requirements", entry)
            self.assertIn("proxy_instruments", entry)
            self.assertIn("routeability_reason", entry)
        self.assertEqual(catalog["fast_scalper"]["engine_coverage"]["fast_scalper"], "active")
        self.assertEqual(catalog["fast_scalper"]["engine_coverage"]["stat_arb"], "disabled")
        self.assertTrue(catalog["fast_scalper"]["support_maturity"]["paper_routeable"])
        self.assertEqual(catalog["fast_scalper"]["paper_execution_route"], "broker_paper")
        self.assertEqual(catalog["macro_proxy"]["engine_coverage"]["macro"], "proxy_only")
        self.assertEqual(catalog["macro_proxy"]["engine_coverage"]["swing_position"], "research_only")
        self.assertEqual(catalog["macro_proxy"]["engine_coverage"]["fast_scalper"], "disabled")
        self.assertEqual(catalog["macro_proxy"]["engine_coverage"]["stat_arb"], "disabled")
        self.assertEqual(catalog["macro_proxy"]["engine_coverage"]["intraday_momentum"], "disabled")
        self.assertEqual(catalog["algorithmic_execution"]["engine_coverage"]["fast_scalper"], "research_only")
        self.assertEqual(catalog["algorithmic_execution"]["engine_coverage"]["macro"], "research_only")
        self.assertEqual(catalog["spot_fx"]["engine_coverage"]["macro"], "unsupported")
        for desk_key in {
            "equity_long_short",
            "etf_index",
            "relative_value",
            "sector_rotation",
            "volatility_options",
            "algorithmic_execution",
        }:
            self.assertEqual(catalog[desk_key]["promotion_wave"], "equity_etf_vol_wave_1")
            self.assertTrue(catalog[desk_key]["support_maturity"]["data_connected"])
            self.assertTrue(catalog[desk_key]["promotion_requirements"])
        self.assertEqual(catalog["equity_long_short"]["support_maturity_stage"], "proxy_scannable")
        self.assertIn("XLK", catalog["sector_rotation"]["proxy_instrument_symbols"])
        self.assertIn("VXX", catalog["volatility_options"]["proxy_instrument_symbols"])
        self.assertFalse(catalog["algorithmic_execution"]["can_submit_orders"])
        self.assertIn("VWAP", " ".join(catalog["algorithmic_execution"]["promotion_requirements"]))
        self.assertFalse(catalog["spot_fx"]["support_maturity"]["data_connected"])
        self.assertIn("UUP", catalog["spot_fx"]["proxy_instrument_symbols"])
        self.assertIn("support_stage_counts", payload["global"]["institutional_catalog"])
        self.assertEqual(payload["global"]["institutional_catalog"]["first_promotion_wave"], "equity_etf_vol")

    def test_fast_two_stage_scan_mode_uses_full_universe_even_when_batch_size_is_small(self) -> None:
        tickers = [f"T{index:02d}" for index in range(45)]

        fast_scan_tickers, fast_metadata = trade_automation_service._resolve_trade_automation_scan_tickers(
            {
                "tickers": tickers,
                "scan_batch_size": 10,
                "scan_mode": trade_automation_service._AUTOMATION_SCAN_MODE_FAST_TWO_STAGE,
            },
            {"cycle_count": 3},
        )
        legacy_scan_tickers, legacy_metadata = trade_automation_service._resolve_trade_automation_scan_tickers(
            {"tickers": tickers, "scan_batch_size": 10},
            {"cycle_count": 3},
        )

        self.assertEqual(fast_scan_tickers, tickers)
        self.assertEqual(fast_metadata["mode"], trade_automation_service._AUTOMATION_SCAN_MODE_FAST_TWO_STAGE)
        self.assertEqual(fast_metadata["active_scan_count"], 45)
        self.assertEqual(fast_metadata["rotation_total"], 1)
        self.assertEqual(len(legacy_scan_tickers), 10)
        self.assertEqual(legacy_metadata["mode"], "rotating_liquid_batch")

    def test_market_session_commander_reports_all_five_desks_and_active_paper_evidence(self) -> None:
        payload = trade_automation_service.get_tenant_trade_automation_market_session(
            self.db,
            current_user=self.current_user,
        )

        self.assertIn(payload["status"], {"ready", "degraded", "blocked", "killed"})
        self.assertEqual(payload["mutation"], "paper_evidence_state")
        self.assertFalse(payload["can_submit_orders"])
        self.assertTrue(payload["paper_route_only"])
        self.assertEqual(payload["desks"]["count"], 5)
        self.assertEqual(
            {item["desk_key"] for item in payload["desks"]["items"]},
            {"fast_scalper", "stat_arb", "intraday_momentum", "swing_position", "macro"},
        )
        components = {item["key"]: item for item in payload["components"]}
        for key in {
            "backend_api",
            "alpaca_paper",
            "worker_heartbeat",
            "desk_scans",
            "candidate_diagnostics",
            "entry_window",
            "risk_allocator",
            "alpaca_reconciliation",
            "order_evidence_packets",
            "deep_analysis",
            "ai_referee",
            "hft_watchdog",
            "close_report",
            "readiness_cache",
            "runtime_supervisor",
            "settings_proof",
            "candidate_lifecycle",
            "missed_move_intelligence",
            "production_weakness_closure",
            "roadmap_evidence_activation",
            "read_only_activation_audit",
            "next_50_trading_intelligence",
            "next_50_institutional_edge",
            "next_50_enterprise_diligence",
            "next_50_market_edge_trade_capture",
            "next_50_research_memory_strategy_promotion",
            "next_100_edge_factory_production_scale",
            "next_500_quant_evidence_os_edge",
            "next_1000_quant_evidence_os_scale",
            "next_500_quant_evidence_os_compounding",
            "next_500_quant_evidence_os_institutional_moat",
            "next_500_quant_evidence_os_adaptive_edge",
            "next_500_quant_evidence_os_decision_intelligence",
            "next_500_quant_evidence_os_autonomous_improvement",
            "next_500_quant_evidence_os_market_adaptation",
            "next_1000_quant_evidence_os_frontier_edge",
            "next_500_quant_evidence_os_trade_selection_edge",
            "next_500_quant_evidence_os_realtime_alpha_ops",
            "next_500_quant_evidence_os_adaptive_execution_intelligence",
            "next_500_quant_evidence_os_portfolio_outcome_intelligence",
            "next_5000_quant_evidence_os_institutional_operating_edge",
        }:
            self.assertIn(key, components)
        self.assertIn("entry_window_explainer", payload)
        self.assertIn("current_blocker", payload["entry_window_explainer"])
        self.assertIn("deep_analysis_monitor", payload)
        self.assertIn("desk_sla_command_center", payload)
        self.assertEqual(payload["desk_sla_command_center"]["active_desk_count"], 5)
        self.assertIn("institutional_risk_allocator", payload)
        self.assertTrue(payload["institutional_risk_allocator"]["slots_secondary"])
        self.assertIn("sector_correlation_heat", payload)
        self.assertTrue(payload["sector_correlation_heat"]["slots_secondary_to_heat"])
        self.assertIn("alpaca_reconciliation_console", payload)
        self.assertIn("local_counts", payload["alpaca_reconciliation_console"])
        self.assertIn("order_evidence_packets", payload)
        self.assertTrue(payload["order_evidence_packets"]["paper_route_only"])
        self.assertIn("readiness_cache", payload)
        self.assertIn("runtime_supervisor", payload)
        self.assertIn("expected_settings_proof", payload)
        self.assertIn("incident_timeline", payload)
        self.assertIn("close_artifact_index", payload)
        self.assertIn("candidate_lifecycle_artifact", payload)
        self.assertIn("missed_move_leaderboard", payload)
        self.assertIn("ai_referee_dashboard", payload)
        self.assertIn("allocator_dashboard", payload)
        self.assertIn("execution_quality_summary", payload)
        self.assertIn("production_weakness_closure", payload)
        self.assertEqual(payload["production_weakness_closure"]["item_count"], 50)
        self.assertFalse(payload["production_weakness_closure"]["read_only"])
        self.assertTrue(payload["production_weakness_closure"]["paper_operational"])
        self.assertEqual(payload["production_weakness_closure"]["mutation"], "paper_evidence_state")
        self.assertTrue(payload["production_weakness_closure"]["can_write_artifacts"])
        self.assertFalse(payload["production_weakness_closure"]["can_submit_orders"])
        self.assertTrue(payload["production_weakness_closure"]["paper_route_only"])
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
        self.assertEqual(activation_audit["mutation"], "paper_evidence_state")
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
        self.assertFalse(evidence_million["live_mirror"]["enabled"])
        self.assertGreaterEqual(evidence_million["active_paper_evidence_item_count"], 12900)
        self.assertIn("next_50_trading_intelligence", payload)
        self.assertEqual(payload["next_50_trading_intelligence"]["item_count"], 50)
        self.assertFalse(payload["next_50_trading_intelligence"]["read_only"])
        self.assertTrue(payload["next_50_trading_intelligence"]["paper_operational"])
        self.assertEqual(payload["next_50_trading_intelligence"]["mutation"], "paper_evidence_state")
        self.assertFalse(payload["next_50_trading_intelligence"]["can_submit_orders"])
        self.assertTrue(payload["next_50_trading_intelligence"]["paper_route_only"])
        self.assertEqual(
            set(payload["next_50_trading_intelligence"]["group_counts"]),
            {"Trade Capture", "AI Evidence", "Desk Intelligence", "Risk Allocator", "Execution Proof"},
        )
        self.assertIn("next_50_institutional_edge", payload)
        self.assertEqual(payload["next_50_institutional_edge"]["item_count"], 50)
        self.assertFalse(payload["next_50_institutional_edge"]["can_submit_orders"])
        self.assertFalse(payload["next_50_institutional_edge"]["can_submit_live_orders"])
        self.assertTrue(payload["next_50_institutional_edge"]["paper_route_only"])
        self.assertEqual(
            set(payload["next_50_institutional_edge"]["group_counts"]),
            {"Research Memory", "Data Quality", "Strategy Governance", "Customer Ops", "Scale and Integrations"},
        )
        self.assertIn("next_50_enterprise_diligence", payload)
        self.assertEqual(payload["next_50_enterprise_diligence"]["item_count"], 50)
        self.assertFalse(payload["next_50_enterprise_diligence"]["can_submit_orders"])
        self.assertFalse(payload["next_50_enterprise_diligence"]["can_submit_live_orders"])
        self.assertTrue(payload["next_50_enterprise_diligence"]["paper_route_only"])
        self.assertEqual(
            set(payload["next_50_enterprise_diligence"]["group_counts"]),
            {"Security and Trust", "Compliance and Audit", "Reliability and Performance", "Deployment and Ops", "Commercial Readiness"},
        )
        self.assertIn("next_50_market_edge_trade_capture", payload)
        self.assertEqual(payload["next_50_market_edge_trade_capture"]["item_count"], 50)
        self.assertFalse(payload["next_50_market_edge_trade_capture"]["can_submit_orders"])
        self.assertFalse(payload["next_50_market_edge_trade_capture"]["can_submit_live_orders"])
        self.assertTrue(payload["next_50_market_edge_trade_capture"]["paper_route_only"])
        self.assertEqual(
            set(payload["next_50_market_edge_trade_capture"]["group_counts"]),
            {"Setup Detection", "Market Confirmation", "Missed-Move Intelligence", "AI Evidence Review", "Desk Allocation and Heat"},
        )
        self.assertIn("next_50_research_memory_strategy_promotion", payload)
        self.assertEqual(payload["next_50_research_memory_strategy_promotion"]["item_count"], 50)
        self.assertFalse(payload["next_50_research_memory_strategy_promotion"]["can_submit_orders"])
        self.assertFalse(payload["next_50_research_memory_strategy_promotion"]["can_submit_live_orders"])
        self.assertTrue(payload["next_50_research_memory_strategy_promotion"]["paper_route_only"])
        self.assertEqual(
            set(payload["next_50_research_memory_strategy_promotion"]["group_counts"]),
            {"Replay and Backtest", "Research Memory", "Regime Intelligence", "Promotion Gates", "Research Reports"},
        )
        self.assertIn("next_100_edge_factory_production_scale", payload)
        next_100 = payload["next_100_edge_factory_production_scale"]
        self.assertEqual(next_100["item_count"], 100)
        self.assertEqual(next_100["implemented_count"], 100)
        self.assertEqual(next_100["live_item_count"], 100)
        self.assertEqual(next_100["live_enabled_count"], 0)
        self.assertEqual(next_100["live_available_disabled_count"], 100)
        self.assertFalse(next_100["read_only"])
        self.assertTrue(next_100["paper_operational"])
        self.assertEqual(next_100["mutation"], "paper_evidence_state")
        self.assertTrue(next_100["can_write_artifacts"])
        self.assertFalse(next_100["can_submit_orders"])
        self.assertFalse(next_100["can_submit_live_orders"])
        self.assertFalse(next_100["live_mirror"]["enabled"])
        self.assertTrue(next_100["paper_route_only"])
        self.assertEqual(
            set(next_100["group_counts"]),
            {
                "Real-Time Data Quality Engine",
                "Signal Quality Engine",
                "Entry Timing Optimizer",
                "Exit and Trade Management Intelligence",
                "Position Sizing Intelligence",
                "Desk Competition Engine",
                "Market Regime Learning",
                "Trade Outcome Memory",
                "Research Promotion System",
                "Customer-Grade Ops and Trust",
            },
        )
        self.assertTrue(all(not item["live_enabled"] for item in next_100["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_100["items"]))
        self.assertIn("next_500_quant_evidence_os_edge", payload)
        next_500 = payload["next_500_quant_evidence_os_edge"]
        self.assertEqual(next_500["item_count"], 500)
        self.assertEqual(next_500["implemented_count"], 500)
        self.assertEqual(next_500["workstream_count"], 20)
        self.assertEqual(next_500["items_per_workstream"], 25)
        self.assertEqual(next_500["live_item_count"], 500)
        self.assertEqual(next_500["live_enabled_count"], 0)
        self.assertEqual(next_500["live_available_disabled_count"], 500)
        self.assertFalse(next_500["read_only"])
        self.assertTrue(next_500["paper_operational"])
        self.assertEqual(next_500["mutation"], "paper_evidence_state")
        self.assertTrue(next_500["can_write_artifacts"])
        self.assertFalse(next_500["can_submit_orders"])
        self.assertFalse(next_500["can_submit_live_orders"])
        self.assertFalse(next_500["live_mirror"]["enabled"])
        self.assertTrue(next_500["paper_route_only"])
        self.assertEqual(
            set(next_500["group_counts"]),
            {
                "Evidence Graph Core",
                "Missed Opportunity Intelligence",
                "Market Regime Engine",
                "Opportunity Capture V3",
                "AI Evidence Referee V3",
                "Research Memory Layer",
                "Promotion Engine",
                "Desk Capital Allocator",
                "Risk Intelligence",
                "Execution Evidence Studio",
                "Alpaca Paper Reliability",
                "Deep Analysis Queue",
                "Multi-Desk Expansion",
                "HFT Supervision",
                "Market Session Commander",
                "Customer Product Edge",
                "Operator UI",
                "Data Platform",
                "Adapter and Enterprise Readiness",
                "Validation, CI, and Production Hardening",
            },
        )
        self.assertTrue(all(not item["live_enabled"] for item in next_500["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500["items"]))
        self.assertIn("next_1000_quant_evidence_os_scale", payload)
        next_1000 = payload["next_1000_quant_evidence_os_scale"]
        self.assertEqual(next_1000["item_count"], 1000)
        self.assertEqual(next_1000["implemented_count"], 1000)
        self.assertEqual(next_1000["workstream_count"], 40)
        self.assertEqual(next_1000["items_per_workstream"], 25)
        self.assertEqual(next_1000["live_item_count"], 1000)
        self.assertEqual(next_1000["live_enabled_count"], 0)
        self.assertEqual(next_1000["live_available_disabled_count"], 1000)
        self.assertFalse(next_1000["read_only"])
        self.assertTrue(next_1000["paper_operational"])
        self.assertEqual(next_1000["mutation"], "paper_evidence_state")
        self.assertTrue(next_1000["can_write_artifacts"])
        self.assertFalse(next_1000["can_submit_orders"])
        self.assertFalse(next_1000["can_submit_live_orders"])
        self.assertFalse(next_1000["live_mirror"]["enabled"])
        self.assertTrue(next_1000["paper_route_only"])
        self.assertEqual(len(next_1000["group_counts"]), 40)
        self.assertTrue(all(not item["live_enabled"] for item in next_1000["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_1000["items"]))
        self.assertIn("next_500_quant_evidence_os_compounding", payload)
        next_500_compounding = payload["next_500_quant_evidence_os_compounding"]
        self.assertEqual(next_500_compounding["item_count"], 500)
        self.assertEqual(next_500_compounding["implemented_count"], 500)
        self.assertEqual(next_500_compounding["workstream_count"], 20)
        self.assertEqual(next_500_compounding["items_per_workstream"], 25)
        self.assertEqual(next_500_compounding["prior_scale_item_count"], 1000)
        self.assertEqual(next_500_compounding["cumulative_scale_item_count"], 1500)
        self.assertEqual(next_500_compounding["live_item_count"], 500)
        self.assertEqual(next_500_compounding["live_enabled_count"], 0)
        self.assertEqual(next_500_compounding["live_available_disabled_count"], 500)
        self.assertFalse(next_500_compounding["read_only"])
        self.assertTrue(next_500_compounding["paper_operational"])
        self.assertEqual(next_500_compounding["mutation"], "paper_evidence_state")
        self.assertTrue(next_500_compounding["can_write_artifacts"])
        self.assertFalse(next_500_compounding["can_submit_orders"])
        self.assertFalse(next_500_compounding["can_submit_live_orders"])
        self.assertFalse(next_500_compounding["live_mirror"]["enabled"])
        self.assertTrue(next_500_compounding["paper_route_only"])
        self.assertEqual(len(next_500_compounding["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in next_500_compounding["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500_compounding["items"]))
        self.assertIn("next_500_quant_evidence_os_institutional_moat", payload)
        next_500_moat = payload["next_500_quant_evidence_os_institutional_moat"]
        self.assertEqual(next_500_moat["item_count"], 500)
        self.assertEqual(next_500_moat["implemented_count"], 500)
        self.assertEqual(next_500_moat["workstream_count"], 20)
        self.assertEqual(next_500_moat["items_per_workstream"], 25)
        self.assertEqual(next_500_moat["prior_cumulative_item_count"], 1500)
        self.assertEqual(next_500_moat["cumulative_scale_item_count"], 2000)
        self.assertEqual(next_500_moat["live_item_count"], 500)
        self.assertEqual(next_500_moat["live_enabled_count"], 0)
        self.assertEqual(next_500_moat["live_available_disabled_count"], 500)
        self.assertFalse(next_500_moat["read_only"])
        self.assertTrue(next_500_moat["paper_operational"])
        self.assertEqual(next_500_moat["mutation"], "paper_evidence_state")
        self.assertTrue(next_500_moat["can_write_artifacts"])
        self.assertFalse(next_500_moat["can_submit_orders"])
        self.assertFalse(next_500_moat["can_submit_live_orders"])
        self.assertFalse(next_500_moat["live_mirror"]["enabled"])
        self.assertTrue(next_500_moat["paper_route_only"])
        self.assertEqual(len(next_500_moat["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in next_500_moat["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500_moat["items"]))
        self.assertIn("next_500_quant_evidence_os_adaptive_edge", payload)
        next_500_adaptive = payload["next_500_quant_evidence_os_adaptive_edge"]
        self.assertEqual(next_500_adaptive["item_count"], 500)
        self.assertEqual(next_500_adaptive["implemented_count"], 500)
        self.assertEqual(next_500_adaptive["workstream_count"], 20)
        self.assertEqual(next_500_adaptive["items_per_workstream"], 25)
        self.assertEqual(next_500_adaptive["prior_cumulative_item_count"], 2000)
        self.assertEqual(next_500_adaptive["cumulative_scale_item_count"], 2500)
        self.assertEqual(next_500_adaptive["live_item_count"], 500)
        self.assertEqual(next_500_adaptive["live_enabled_count"], 0)
        self.assertEqual(next_500_adaptive["live_available_disabled_count"], 500)
        self.assertFalse(next_500_adaptive["read_only"])
        self.assertTrue(next_500_adaptive["paper_operational"])
        self.assertEqual(next_500_adaptive["mutation"], "paper_evidence_state")
        self.assertTrue(next_500_adaptive["can_write_artifacts"])
        self.assertFalse(next_500_adaptive["can_submit_orders"])
        self.assertFalse(next_500_adaptive["can_submit_live_orders"])
        self.assertFalse(next_500_adaptive["live_mirror"]["enabled"])
        self.assertTrue(next_500_adaptive["paper_route_only"])
        self.assertEqual(len(next_500_adaptive["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in next_500_adaptive["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500_adaptive["items"]))
        self.assertIn("next_500_quant_evidence_os_decision_intelligence", payload)
        next_500_decision = payload["next_500_quant_evidence_os_decision_intelligence"]
        self.assertEqual(next_500_decision["item_count"], 500)
        self.assertEqual(next_500_decision["implemented_count"], 500)
        self.assertEqual(next_500_decision["workstream_count"], 20)
        self.assertEqual(next_500_decision["items_per_workstream"], 25)
        self.assertEqual(next_500_decision["prior_cumulative_item_count"], 2500)
        self.assertEqual(next_500_decision["cumulative_scale_item_count"], 3000)
        self.assertEqual(next_500_decision["live_item_count"], 500)
        self.assertEqual(next_500_decision["live_enabled_count"], 0)
        self.assertEqual(next_500_decision["live_available_disabled_count"], 500)
        self.assertFalse(next_500_decision["read_only"])
        self.assertTrue(next_500_decision["paper_operational"])
        self.assertEqual(next_500_decision["mutation"], "paper_evidence_state")
        self.assertTrue(next_500_decision["can_write_artifacts"])
        self.assertFalse(next_500_decision["can_submit_orders"])
        self.assertFalse(next_500_decision["can_submit_live_orders"])
        self.assertFalse(next_500_decision["live_mirror"]["enabled"])
        self.assertTrue(next_500_decision["paper_route_only"])
        self.assertEqual(len(next_500_decision["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in next_500_decision["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500_decision["items"]))
        self.assertIn("next_500_quant_evidence_os_autonomous_improvement", payload)
        next_500_improvement = payload["next_500_quant_evidence_os_autonomous_improvement"]
        self.assertEqual(next_500_improvement["item_count"], 500)
        self.assertEqual(next_500_improvement["implemented_count"], 500)
        self.assertEqual(next_500_improvement["workstream_count"], 20)
        self.assertEqual(next_500_improvement["items_per_workstream"], 25)
        self.assertEqual(next_500_improvement["prior_cumulative_item_count"], 3000)
        self.assertEqual(next_500_improvement["cumulative_scale_item_count"], 3500)
        self.assertEqual(next_500_improvement["live_item_count"], 500)
        self.assertEqual(next_500_improvement["live_enabled_count"], 0)
        self.assertEqual(next_500_improvement["live_available_disabled_count"], 500)
        self.assertFalse(next_500_improvement["read_only"])
        self.assertTrue(next_500_improvement["paper_operational"])
        self.assertEqual(next_500_improvement["mutation"], "paper_evidence_state")
        self.assertTrue(next_500_improvement["can_write_artifacts"])
        self.assertFalse(next_500_improvement["can_submit_orders"])
        self.assertFalse(next_500_improvement["can_submit_live_orders"])
        self.assertFalse(next_500_improvement["live_mirror"]["enabled"])
        self.assertTrue(next_500_improvement["paper_route_only"])
        self.assertEqual(len(next_500_improvement["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in next_500_improvement["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500_improvement["items"]))
        self.assertIn("next_500_quant_evidence_os_market_adaptation", payload)
        next_500_market = payload["next_500_quant_evidence_os_market_adaptation"]
        self.assertEqual(next_500_market["item_count"], 500)
        self.assertEqual(next_500_market["implemented_count"], 500)
        self.assertEqual(next_500_market["workstream_count"], 20)
        self.assertEqual(next_500_market["items_per_workstream"], 25)
        self.assertEqual(next_500_market["prior_cumulative_item_count"], 3500)
        self.assertEqual(next_500_market["cumulative_scale_item_count"], 4000)
        self.assertEqual(next_500_market["live_item_count"], 500)
        self.assertEqual(next_500_market["live_enabled_count"], 0)
        self.assertEqual(next_500_market["live_available_disabled_count"], 500)
        self.assertFalse(next_500_market["read_only"])
        self.assertTrue(next_500_market["paper_operational"])
        self.assertEqual(next_500_market["mutation"], "paper_evidence_state")
        self.assertTrue(next_500_market["can_write_artifacts"])
        self.assertFalse(next_500_market["can_submit_orders"])
        self.assertFalse(next_500_market["can_submit_live_orders"])
        self.assertFalse(next_500_market["live_mirror"]["enabled"])
        self.assertTrue(next_500_market["paper_route_only"])
        self.assertEqual(len(next_500_market["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in next_500_market["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_500_market["items"]))
        self.assertIn("next_1000_quant_evidence_os_frontier_edge", payload)
        next_1000_frontier = payload["next_1000_quant_evidence_os_frontier_edge"]
        self.assertEqual(next_1000_frontier["item_count"], 1000)
        self.assertEqual(next_1000_frontier["implemented_count"], 1000)
        self.assertEqual(next_1000_frontier["workstream_count"], 40)
        self.assertEqual(next_1000_frontier["items_per_workstream"], 25)
        self.assertEqual(next_1000_frontier["prior_cumulative_item_count"], 4000)
        self.assertEqual(next_1000_frontier["cumulative_scale_item_count"], 5000)
        self.assertEqual(next_1000_frontier["live_item_count"], 1000)
        self.assertEqual(next_1000_frontier["live_enabled_count"], 0)
        self.assertEqual(next_1000_frontier["live_available_disabled_count"], 1000)
        self.assertFalse(next_1000_frontier["read_only"])
        self.assertTrue(next_1000_frontier["paper_operational"])
        self.assertEqual(next_1000_frontier["mutation"], "paper_evidence_state")
        self.assertTrue(next_1000_frontier["can_write_artifacts"])
        self.assertFalse(next_1000_frontier["can_submit_orders"])
        self.assertFalse(next_1000_frontier["can_submit_live_orders"])
        self.assertFalse(next_1000_frontier["live_mirror"]["enabled"])
        self.assertTrue(next_1000_frontier["paper_route_only"])
        self.assertEqual(len(next_1000_frontier["group_counts"]), 40)
        self.assertTrue(all(not item["live_enabled"] for item in next_1000_frontier["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in next_1000_frontier["items"]))
        self.assertIn("next_500_quant_evidence_os_trade_selection_edge", payload)
        trade_selection_edge = payload["next_500_quant_evidence_os_trade_selection_edge"]
        self.assertEqual(trade_selection_edge["item_count"], 500)
        self.assertEqual(trade_selection_edge["implemented_count"], 500)
        self.assertEqual(trade_selection_edge["workstream_count"], 20)
        self.assertEqual(trade_selection_edge["items_per_workstream"], 25)
        self.assertEqual(trade_selection_edge["live_item_count"], 500)
        self.assertEqual(trade_selection_edge["live_enabled_count"], 0)
        self.assertEqual(trade_selection_edge["live_available_disabled_count"], 500)
        self.assertFalse(trade_selection_edge["read_only"])
        self.assertTrue(trade_selection_edge["paper_operational"])
        self.assertEqual(trade_selection_edge["mutation"], "paper_evidence_state")
        self.assertTrue(trade_selection_edge["can_write_artifacts"])
        self.assertFalse(trade_selection_edge["writes_trade_state"])
        self.assertFalse(trade_selection_edge["can_submit_orders"])
        self.assertFalse(trade_selection_edge["can_submit_live_orders"])
        self.assertFalse(trade_selection_edge["live_mirror"]["enabled"])
        self.assertTrue(trade_selection_edge["paper_route_only"])
        self.assertEqual(len(trade_selection_edge["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in trade_selection_edge["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in trade_selection_edge["items"]))
        self.assertIn("trade_selection_edge_context", payload)
        edge_context = payload["trade_selection_edge_context"]
        self.assertEqual(edge_context["usage_mode"], "influence_ranking")
        self.assertFalse(edge_context["can_submit_orders"])
        self.assertFalse(edge_context["can_submit_live_orders"])
        self.assertTrue(edge_context["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_500_quant_evidence_os_realtime_alpha_ops", payload)
        realtime_alpha_ops = payload["next_500_quant_evidence_os_realtime_alpha_ops"]
        self.assertEqual(realtime_alpha_ops["item_count"], 500)
        self.assertEqual(realtime_alpha_ops["implemented_count"], 500)
        self.assertEqual(realtime_alpha_ops["workstream_count"], 20)
        self.assertEqual(realtime_alpha_ops["items_per_workstream"], 25)
        self.assertEqual(realtime_alpha_ops["prior_cumulative_item_count"], 6400)
        self.assertEqual(realtime_alpha_ops["cumulative_scale_item_count"], 6900)
        self.assertEqual(realtime_alpha_ops["live_item_count"], 500)
        self.assertEqual(realtime_alpha_ops["live_enabled_count"], 0)
        self.assertEqual(realtime_alpha_ops["live_available_disabled_count"], 500)
        self.assertFalse(realtime_alpha_ops["read_only"])
        self.assertTrue(realtime_alpha_ops["paper_operational"])
        self.assertEqual(realtime_alpha_ops["mutation"], "paper_evidence_state")
        self.assertTrue(realtime_alpha_ops["can_write_artifacts"])
        self.assertFalse(realtime_alpha_ops["writes_trade_state"])
        self.assertFalse(realtime_alpha_ops["can_submit_orders"])
        self.assertFalse(realtime_alpha_ops["can_submit_live_orders"])
        self.assertFalse(realtime_alpha_ops["live_mirror"]["enabled"])
        self.assertTrue(realtime_alpha_ops["paper_route_only"])
        self.assertEqual(len(realtime_alpha_ops["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in realtime_alpha_ops["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in realtime_alpha_ops["items"]))
        self.assertIn("realtime_alpha_ops_context", payload)
        realtime_context = payload["realtime_alpha_ops_context"]
        self.assertEqual(realtime_context["usage_mode"], "influence_ranking")
        self.assertFalse(realtime_context["can_submit_orders"])
        self.assertFalse(realtime_context["can_submit_live_orders"])
        self.assertTrue(realtime_context["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_500_quant_evidence_os_adaptive_execution_intelligence", payload)
        adaptive_execution = payload["next_500_quant_evidence_os_adaptive_execution_intelligence"]
        self.assertEqual(adaptive_execution["item_count"], 500)
        self.assertEqual(adaptive_execution["implemented_count"], 500)
        self.assertEqual(adaptive_execution["workstream_count"], 20)
        self.assertEqual(adaptive_execution["items_per_workstream"], 25)
        self.assertEqual(adaptive_execution["prior_cumulative_item_count"], 6900)
        self.assertEqual(adaptive_execution["cumulative_scale_item_count"], 7400)
        self.assertEqual(adaptive_execution["live_item_count"], 500)
        self.assertEqual(adaptive_execution["live_enabled_count"], 0)
        self.assertEqual(adaptive_execution["live_available_disabled_count"], 500)
        self.assertFalse(adaptive_execution["read_only"])
        self.assertTrue(adaptive_execution["paper_operational"])
        self.assertEqual(adaptive_execution["mutation"], "paper_evidence_state")
        self.assertTrue(adaptive_execution["can_write_artifacts"])
        self.assertFalse(adaptive_execution["writes_trade_state"])
        self.assertFalse(adaptive_execution["can_submit_orders"])
        self.assertFalse(adaptive_execution["can_submit_live_orders"])
        self.assertFalse(adaptive_execution["live_mirror"]["enabled"])
        self.assertTrue(adaptive_execution["paper_route_only"])
        self.assertEqual(len(adaptive_execution["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in adaptive_execution["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in adaptive_execution["items"]))
        self.assertIn("adaptive_execution_intelligence_context", payload)
        adaptive_context = payload["adaptive_execution_intelligence_context"]
        self.assertEqual(adaptive_context["usage_mode"], "influence_ranking_and_allocator")
        self.assertFalse(adaptive_context["can_submit_orders"])
        self.assertFalse(adaptive_context["can_submit_live_orders"])
        self.assertEqual(adaptive_context["score_influence"]["max_uprank"], 2.5)
        self.assertEqual(adaptive_context["score_influence"]["max_downrank"], -7.0)
        self.assertTrue(adaptive_context["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_500_quant_evidence_os_portfolio_outcome_intelligence", payload)
        portfolio_outcome = payload["next_500_quant_evidence_os_portfolio_outcome_intelligence"]
        self.assertEqual(portfolio_outcome["item_count"], 500)
        self.assertEqual(portfolio_outcome["implemented_count"], 500)
        self.assertEqual(portfolio_outcome["workstream_count"], 20)
        self.assertEqual(portfolio_outcome["items_per_workstream"], 25)
        self.assertEqual(portfolio_outcome["prior_cumulative_item_count"], 7400)
        self.assertEqual(portfolio_outcome["cumulative_scale_item_count"], 7900)
        self.assertEqual(portfolio_outcome["live_item_count"], 500)
        self.assertEqual(portfolio_outcome["live_enabled_count"], 0)
        self.assertEqual(portfolio_outcome["live_available_disabled_count"], 500)
        self.assertFalse(portfolio_outcome["read_only"])
        self.assertTrue(portfolio_outcome["paper_operational"])
        self.assertEqual(portfolio_outcome["mutation"], "paper_evidence_state")
        self.assertTrue(portfolio_outcome["can_write_artifacts"])
        self.assertFalse(portfolio_outcome["writes_trade_state"])
        self.assertFalse(portfolio_outcome["can_submit_orders"])
        self.assertFalse(portfolio_outcome["can_submit_live_orders"])
        self.assertFalse(portfolio_outcome["live_mirror"]["enabled"])
        self.assertTrue(portfolio_outcome["paper_route_only"])
        self.assertEqual(len(portfolio_outcome["group_counts"]), 20)
        self.assertTrue(all(not item["live_enabled"] for item in portfolio_outcome["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in portfolio_outcome["items"]))
        self.assertIn("portfolio_outcome_intelligence_context", payload)
        portfolio_context = payload["portfolio_outcome_intelligence_context"]
        self.assertEqual(portfolio_context["usage_mode"], "influence_portfolio_ranking_and_allocator")
        self.assertFalse(portfolio_context["can_submit_orders"])
        self.assertFalse(portfolio_context["can_submit_live_orders"])
        self.assertEqual(portfolio_context["score_influence"]["max_uprank"], 2.0)
        self.assertEqual(portfolio_context["score_influence"]["max_downrank"], -8.0)
        self.assertTrue(portfolio_context["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("next_5000_quant_evidence_os_institutional_operating_edge", payload)
        institutional_edge = payload["next_5000_quant_evidence_os_institutional_operating_edge"]
        self.assertEqual(institutional_edge["item_count"], 5000)
        self.assertEqual(institutional_edge["implemented_count"], 5000)
        self.assertEqual(institutional_edge["workstream_count"], 100)
        self.assertEqual(institutional_edge["items_per_workstream"], 50)
        self.assertEqual(institutional_edge["prior_cumulative_item_count"], 7900)
        self.assertEqual(institutional_edge["cumulative_scale_item_count"], 12900)
        self.assertEqual(institutional_edge["live_item_count"], 5000)
        self.assertEqual(institutional_edge["live_enabled_count"], 0)
        self.assertEqual(institutional_edge["live_available_disabled_count"], 5000)
        self.assertFalse(institutional_edge["read_only"])
        self.assertTrue(institutional_edge["paper_operational"])
        self.assertEqual(institutional_edge["mutation"], "paper_evidence_state")
        self.assertTrue(institutional_edge["can_write_artifacts"])
        self.assertFalse(institutional_edge["writes_trade_state"])
        self.assertFalse(institutional_edge["can_submit_orders"])
        self.assertFalse(institutional_edge["can_submit_live_orders"])
        self.assertFalse(institutional_edge["live_mirror"]["enabled"])
        self.assertTrue(institutional_edge["paper_route_only"])
        self.assertEqual(len(institutional_edge["group_counts"]), 100)
        self.assertTrue(all(not item["live_enabled"] for item in institutional_edge["items"]))
        self.assertTrue(all(item["live_version"]["status"] == "available_disabled" for item in institutional_edge["items"]))
        self.assertIn("institutional_operating_edge_context", payload)
        institutional_context = payload["institutional_operating_edge_context"]
        self.assertEqual(institutional_context["usage_mode"], "influence_operating_ranking_allocator_and_market_ops")
        self.assertFalse(institutional_context["can_submit_orders"])
        self.assertFalse(institutional_context["can_submit_live_orders"])
        self.assertEqual(institutional_context["score_influence"]["max_uprank"], 1.25)
        self.assertEqual(institutional_context["score_influence"]["max_downrank"], -9.0)
        self.assertTrue(institutional_context["score_influence"]["hard_gates_remain_authoritative"])
        self.assertIn("evidence_million_target", payload)
        self.assertGreaterEqual(payload["evidence_million_target"]["observed_event_count"], 0)
        self.assertIn("customer_safe_empty_states", payload)
        self.assertFalse(payload["ai_referee_dashboard"]["operator_notes"]["can_override_risk_gates"])
        self.assertIn("diagnostics_exports", payload)
        self.assertIn("candidate_diagnostics", payload["diagnostics_exports"])
        self.assertIn("/api/orgs/trade-automation/no-trade-report", payload["links"]["no_trade_report"])
        self.assertIn(
            payload["no_trade_escalation"]["stage"],
            {"monitoring", "opportunity_refresh_after_1030", "why_no_trade_by_noon"},
        )
        self.assertIn("quant_evidence_control_plane", payload)
        self.assertEqual(payload["quant_evidence_control_plane"]["product_name"], "Quant Evidence Operating System")
        self.assertTrue(payload["quant_evidence_control_plane"]["operator_questions"]["prove_no_live_bypass"]["answerable"])
        self.assertIn("institutional_position_policy", payload)

    def test_default_profile_uses_institutional_position_baseline(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state({})

        self.assertEqual(state["settings"]["max_open_positions"], 12)
        policy = trade_automation_service._build_institutional_position_policy(state["settings"])
        self.assertEqual(policy["institutional_baseline_positions"], 12)
        self.assertEqual(policy["mode"], "institutional_risk_allocated")
        self.assertFalse(policy["buying_power_is_ceiling"] is False)

    def test_market_session_degrades_when_active_worker_is_stale(self) -> None:
        active_phase = {
            "phase": "active_session_monitor",
            "description": "Active-session monitor",
            "timezone": "America/New_York",
            "now_utc": "2026-04-29T16:00:00+00:00",
            "now_et": "2026-04-29T12:00:00-04:00",
            "market_day": True,
            "active_window": True,
            "next_checkpoint": "15:55 ET stop-new-paper-orders check",
        }
        worker_status = {
            "status": "running_but_stale",
            "stale": True,
            "stale_seconds": 300,
            "last_loop_at": "2026-04-29T15:55:00+00:00",
        }

        with (
            patch.object(trade_automation_service, "_market_ops_session_phase", return_value=active_phase),
            patch("backend.services.job_queue_service.get_job_worker_status", return_value=worker_status),
        ):
            payload = trade_automation_service.get_tenant_trade_automation_market_session(
                self.db,
                current_user=self.current_user,
            )

        components = {item["key"]: item for item in payload["components"]}
        self.assertEqual(components["worker_heartbeat"]["status"], "blocked")
        self.assertIn(payload["status"], {"blocked", "degraded"})
        self.assertIn("Restart", components["worker_heartbeat"]["next_action"])

    def test_market_watchdog_reports_cards_without_order_authority(self) -> None:
        with patch.object(
            trade_automation_service,
            "_write_market_watchdog_observation",
            return_value={"written": True, "events_path": "test-events.jsonl", "summary_path": "test-summary.json"},
        ):
            payload = trade_automation_service.get_tenant_trade_automation_watchdog(
                self.db,
                current_user=self.current_user,
            )

        self.assertIn(payload["status"], {"ready", "watching", "degraded", "blocked", "killed"})
        self.assertTrue(payload["read_only"])
        self.assertEqual(payload["mutation"], "watchdog_observation_artifact")
        self.assertFalse(payload["writes_trade_state"])
        self.assertFalse(payload["can_submit_orders"])
        self.assertFalse(payload["can_submit_live_orders"])
        self.assertFalse(payload["can_clear_kill_switch"])
        self.assertFalse(payload["can_loosen_risk_gates"])
        self.assertTrue(payload["paper_route_only"])
        self.assertIn("evidence_million_target", payload)
        self.assertEqual(payload["evidence_million_target"]["target_event_count"], 100000000)
        self.assertFalse(payload["evidence_million_target"]["can_submit_orders"])
        self.assertFalse(payload["evidence_million_target"]["can_submit_live_orders"])
        components = {item["key"]: item for item in payload["components"]}
        for key in {
            "backend_api",
            "frontend",
            "continuous_ops",
            "alpaca_paper",
            "worker_heartbeat",
            "desk_scans",
            "deep_analysis",
            "candidate_diagnostics",
            "no_trade_checkpoint",
            "hft_watchdog",
            "reconciliation",
            "kill_switch",
            "risk_gates",
        }:
            self.assertIn(key, components)
        self.assertIn("daily_ledger", payload["links"])
        self.assertEqual(payload["artifacts"]["written"], True)

    def test_market_watchdog_does_not_report_stale_desks_as_error_when_profile_unarmed(self) -> None:
        state = trade_automation_service._read_trade_automation_state(self.tenant)
        state["settings"]["enabled"] = True
        state["settings"]["armed"] = False
        state["settings"]["kill_switch"] = False
        trade_automation_service._write_trade_automation_state(self.tenant, state)
        self.db.commit()

        active_phase = {
            "phase": "active_session_monitor",
            "description": "Active-session monitor",
            "timezone": "America/New_York",
            "now_utc": "2026-04-29T16:00:00+00:00",
            "now_et": "2026-04-29T12:00:00-04:00",
            "market_day": True,
            "active_window": True,
            "next_checkpoint": "15:55 ET stop-new-paper-orders check",
        }
        stale_desk_rows = [
            {"desk_key": "fast_scalper", "stale": True, "due_state": "due_now", "scanned_count": 0},
            {"desk_key": "stat_arb", "stale": True, "due_state": "due_now", "scanned_count": 0},
            {"desk_key": "intraday_momentum", "stale": True, "due_state": "due_now", "scanned_count": 0},
            {"desk_key": "swing_position", "stale": True, "due_state": "due_now", "scanned_count": 0},
            {"desk_key": "macro", "stale": True, "due_state": "due_now", "scanned_count": 0},
        ]

        with (
            patch.object(trade_automation_service, "_market_ops_session_phase", return_value=active_phase),
            patch.object(trade_automation_service, "_market_ops_desk_sla_rows", return_value=stale_desk_rows),
            patch.object(
                trade_automation_service,
                "_write_market_watchdog_observation",
                return_value={"written": True, "events_path": "test-events.jsonl", "summary_path": "test-summary.json"},
            ),
        ):
            payload = trade_automation_service.get_tenant_trade_automation_watchdog(
                self.db,
                current_user=self.current_user,
            )

        components = {item["key"]: item for item in payload["components"]}
        self.assertEqual(components["desk_scans"]["status"], "watching")
        self.assertIsNone(components["desk_scans"]["blocker"])
        self.assertIn("not armed", components["desk_scans"]["detail"])
        self.assertEqual(components["risk_gates"]["status"], "watching")
        self.assertIsNone(components["risk_gates"]["blocker"])

    def test_market_watchdog_blocks_active_stale_worker(self) -> None:
        active_phase = {
            "phase": "active_session_monitor",
            "description": "Active-session monitor",
            "timezone": "America/New_York",
            "now_utc": "2026-04-29T16:00:00+00:00",
            "now_et": "2026-04-29T12:00:00-04:00",
            "market_day": True,
            "active_window": True,
            "next_checkpoint": "15:55 ET stop-new-paper-orders check",
        }
        worker_status = {
            "status": "running_but_stale",
            "stale": True,
            "stale_seconds": 300,
            "last_loop_at": "2026-04-29T15:55:00+00:00",
        }

        with (
            patch.object(trade_automation_service, "_market_ops_session_phase", return_value=active_phase),
            patch("backend.services.job_queue_service.get_job_worker_status", return_value=worker_status),
            patch.object(
                trade_automation_service,
                "_write_market_watchdog_observation",
                return_value={"written": True, "events_path": "test-events.jsonl", "summary_path": "test-summary.json"},
            ),
        ):
            payload = trade_automation_service.get_tenant_trade_automation_watchdog(
                self.db,
                current_user=self.current_user,
            )

        components = {item["key"]: item for item in payload["components"]}
        self.assertEqual(components["worker_heartbeat"]["status"], "blocked")
        self.assertEqual(payload["status"], "blocked")
        self.assertIn("worker", components["worker_heartbeat"]["blocker"].lower())
        self.assertFalse(payload["can_submit_orders"])

    def test_market_watchdog_does_not_raise_no_trade_checkpoint_before_1030(self) -> None:
        active_phase = {
            "phase": "active_session_monitor",
            "description": "Active-session monitor",
            "timezone": "America/New_York",
            "now_utc": "2026-04-29T13:45:00+00:00",
            "now_et": "2026-04-29T09:45:00-04:00",
            "market_day": True,
            "active_window": True,
            "next_checkpoint": "15:55 ET stop-new-paper-orders check",
        }

        with (
            patch.object(trade_automation_service, "_market_ops_session_phase", return_value=active_phase),
            patch.object(
                trade_automation_service,
                "_write_market_watchdog_observation",
                return_value={"written": True, "events_path": "test-events.jsonl", "summary_path": "test-summary.json"},
            ),
        ):
            payload = trade_automation_service.get_tenant_trade_automation_watchdog(
                self.db,
                current_user=self.current_user,
            )

        components = {item["key"]: item for item in payload["components"]}
        self.assertEqual(components["no_trade_checkpoint"]["status"], "watching")
        self.assertIsNone(components["no_trade_checkpoint"]["blocker"])
        self.assertFalse(payload["no_trade_checkpoints"]["by_1030"])

    def test_market_watchdog_treats_unconfigured_trust_center_as_watching(self) -> None:
        with (
            patch.object(
                trade_automation_service,
                "_production_trust_center_from_market_session",
                return_value={
                    "status": "needs_attention",
                    "next_action": "Finish not-configured trust sections.",
                    "can_submit_orders": False,
                    "can_submit_live_orders": False,
                },
            ),
            patch.object(
                trade_automation_service,
                "_write_market_watchdog_observation",
                return_value={"written": True, "events_path": "test-events.jsonl", "summary_path": "test-summary.json"},
            ),
        ):
            payload = trade_automation_service.get_tenant_trade_automation_watchdog(
                self.db,
                current_user=self.current_user,
            )

        components = {item["key"]: item for item in payload["components"]}
        self.assertEqual(components["production_trust"]["status"], "watching")
        self.assertIsNone(components["production_trust"]["blocker"])
        self.assertFalse(payload["can_submit_orders"])

    def test_market_watchdog_warning_detail_is_not_reported_as_top_level_blocker(self) -> None:
        def component_by_key(market_session, key):
            if key == "evidence_accelerator":
                return {
                    "status": "degraded",
                    "detail": "1500 useful live observations captured this heartbeat.",
                    "next_action": "Keep collecting without inflating stale or duplicate evidence.",
                    "metadata": {},
                }
            return {}

        with (
            patch.object(trade_automation_service, "_market_watchdog_component_by_key", side_effect=component_by_key),
            patch.object(
                trade_automation_service,
                "_write_market_watchdog_observation",
                return_value={"written": True, "events_path": "test-events.jsonl", "summary_path": "test-summary.json"},
            ),
        ):
            payload = trade_automation_service.get_tenant_trade_automation_watchdog(
                self.db,
                current_user=self.current_user,
            )

        warning = next(item for item in payload["warnings"] if item["key"] == "evidence_accelerator")
        self.assertEqual(warning["detail"], "1500 useful live observations captured this heartbeat.")
        self.assertIsNone(warning["blocker"])
        self.assertNotEqual(payload.get("blocker"), warning["detail"])

    def test_desk_scan_state_applies_desk_specific_sizing(self) -> None:
        base_state = trade_automation_service._normalize_trade_automation_profile_state({})
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        fast_state = trade_automation_service._build_trade_automation_desk_scan_state(
            base_state,
            desks["fast_scalper"]["settings"],
            effective_equity=100000.0,
        )
        macro_state = trade_automation_service._build_trade_automation_desk_scan_state(
            base_state,
            desks["macro"]["settings"],
            effective_equity=100000.0,
        )

        self.assertEqual(fast_state["settings"]["interval"], "1m")
        self.assertEqual(fast_state["settings"]["deep_scan_limit"], 5)
        self.assertTrue(fast_state["settings"]["daily_objective_ranking_enabled"])
        self.assertAlmostEqual(fast_state["settings"]["max_total_open_notional"], 10000.0)
        self.assertAlmostEqual(fast_state["settings"]["max_notional_per_trade"], 3333.3333333333335)
        self.assertEqual(macro_state["settings"]["interval"], "1d")
        self.assertFalse(macro_state["settings"]["daily_objective_ranking_enabled"])
        self.assertAlmostEqual(macro_state["settings"]["max_total_open_notional"], 20000.0)

    def test_catalog_only_proxy_desks_cannot_submit_orders(self) -> None:
        result = trade_automation_service.scan_tenant_trade_automation_desk(
            self.db,
            current_user=self.current_user,
            desk_key="macro_proxy",
            force=True,
        )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["mutation"], "none")
        self.assertFalse(result["desk"]["can_submit_orders"])
        self.assertFalse(result["diagnostics"]["summary"]["can_trade"])
        self.assertEqual(result["diagnostics"]["execution_intent"], "broker_paper")
        self.assertEqual(result["diagnostics"]["summary"]["top_blocker"], "proxy_only_not_executable")
        self.assertEqual(result["diagnostics"]["summary"]["engine_coverage"]["macro"], "proxy_only")
        self.assertIn("macro proxy evidence", result["diagnostics"]["summary"]["promotion_evidence_needed"])
        self.assertEqual(result["diagnostics"]["summary"]["support_maturity_stage"], "proxy_scannable")
        self.assertIn("TLT", result["diagnostics"]["summary"]["proxy_instrument_symbols"])
        self.assertIn("alpaca", result["diagnostics"]["summary"]["provider_capability"].lower())

    def test_algorithmic_execution_is_cross_cutting_research_only(self) -> None:
        diagnostics = trade_automation_service.get_tenant_trade_automation_desk_candidate_diagnostics(
            self.db,
            current_user=self.current_user,
            desk_key="algorithmic_execution",
        )

        self.assertEqual(diagnostics["desk"]["execution_status"], "research_only")
        self.assertFalse(diagnostics["desk"]["can_submit_orders"])
        self.assertEqual(
            set(diagnostics["desk"]["primary_engines"]),
            {"fast_scalper", "stat_arb", "intraday_momentum", "swing_position", "macro"},
        )
        self.assertTrue(
            all(state == "research_only" for state in diagnostics["desk"]["engine_coverage"].values())
        )
        self.assertEqual(diagnostics["desk"]["support_maturity_stage"], "data_connected")
        self.assertFalse(diagnostics["desk"]["support_maturity"]["paper_routeable"])
        self.assertIn("VWAP", " ".join(diagnostics["summary"]["promotion_requirements"]))
        self.assertIn("execution", diagnostics["summary"]["risk_model"].lower())

    def test_catalog_scan_route_bypasses_write_guard(self) -> None:
        from tests.productized_control_plane_test_support import (
            build_test_client,
            build_test_context,
            clear_test_overrides,
        )

        context = build_test_context(slug="catalog-scan-route-test", plan_key="professional")
        client = build_test_client(context)

        @contextmanager
        def forbidden_write_guard():
            raise AssertionError("Catalog-only desk scans must not acquire the automation write guard.")
            yield

        try:
            with patch("backend.routers.orgs.trade_automation_write_guard", forbidden_write_guard):
                response = client.post("/api/orgs/trade-automation/desks/algorithmic_execution/scan", json={})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["mutation"], "none")
            self.assertEqual(payload["data"]["reason"], "research_only_not_executable")
        finally:
            clear_test_overrides()
            context.close()

    def test_unsupported_catalog_desks_are_visible_but_not_armable(self) -> None:
        diagnostics = trade_automation_service.get_tenant_trade_automation_desk_candidate_diagnostics(
            self.db,
            current_user=self.current_user,
            desk_key="spot_fx",
        )

        self.assertEqual(diagnostics["desk"]["execution_status"], "unsupported")
        self.assertFalse(diagnostics["desk"]["can_submit_orders"])
        self.assertIn("connected", diagnostics["summary"]["missing_capability"].lower())
        with self.assertRaises(ValidationError):
            trade_automation_service.update_tenant_trade_automation_desk(
                self.db,
                current_user=self.current_user,
                desk_key="spot_fx",
                updates={"enabled": True, "armed": True},
            )

    def test_scheduler_runs_only_due_desks(self) -> None:
        now = trade_automation_service._utc_now()
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        for desk_key, desk_state in desks.items():
            desk_state["runtime"]["last_scan_at"] = trade_automation_service._serialize_datetime(now)
        desks["fast_scalper"]["runtime"]["last_scan_at"] = trade_automation_service._serialize_datetime(
            now - timedelta(minutes=5)
        )
        trade_automation_service._write_trade_automation_desks_store(self.tenant, desks)
        self.db.commit()

        calls: list[str] = []

        def fake_scan(*args, **kwargs):
            calls.append(kwargs["desk_key"])
            return {"desk": {"desk_key": kwargs["desk_key"]}, "status": "scanned"}

        with patch.object(trade_automation_service, "scan_tenant_trade_automation_desk", side_effect=fake_scan):
            result = trade_automation_service.run_due_tenant_trade_automation_desks(
                self.db,
                current_user=self.current_user,
            )

        self.assertEqual(calls, ["fast_scalper"])
        self.assertEqual(result["ran_count"], 1)
        self.assertEqual(result["skipped_count"], 4)

    def test_worker_scheduler_batches_due_desks_and_marks_deferred(self) -> None:
        now = trade_automation_service._utc_now()
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        for desk_state in desks.values():
            desk_state["runtime"]["last_scan_at"] = trade_automation_service._serialize_datetime(
                now - timedelta(hours=2)
            )
        desks["macro"]["runtime"]["last_scan_at"] = trade_automation_service._serialize_datetime(
            now - timedelta(days=2)
        )
        trade_automation_service._write_trade_automation_desks_store(self.tenant, desks)
        self.db.commit()

        calls: list[str] = []

        def fake_scan(*args, **kwargs):
            self.assertTrue(kwargs.get("worker_scan"))
            calls.append(kwargs["desk_key"])
            return {"desk": {"desk_key": kwargs["desk_key"]}, "status": "scanned"}

        with patch.object(trade_automation_service, "scan_tenant_trade_automation_desk", side_effect=fake_scan):
            result = trade_automation_service.run_due_tenant_trade_automation_desks(
                self.db,
                current_user=self.current_user,
                max_desk_scans=1,
                worker_scan=True,
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(result["ran_count"], 1)
        self.assertEqual(result["scanned_due_desks"], calls)
        self.assertEqual(
            set(result["deferred_due_desks"]),
            set(result["due_desks"]) - set(calls),
        )

        payload = trade_automation_service.list_tenant_trade_automation_desks(self.db, current_user=self.current_user)
        by_key = {item["desk_key"]: item for item in payload["items"]}
        self.assertEqual(by_key["stat_arb"]["runtime"]["worker_state"], "worker_deferred")
        self.assertIn("Queued behind another due desk", by_key["stat_arb"]["next_action"])

    def test_worker_scheduler_prioritizes_deferred_due_desks(self) -> None:
        now = trade_automation_service._utc_now()
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        for desk_state in desks.values():
            desk_state["runtime"]["last_scan_at"] = trade_automation_service._serialize_datetime(
                now - timedelta(hours=2)
            )
            desk_state["runtime"]["worker_deferred_at"] = None
        desks["swing_position"]["runtime"]["worker_deferred_at"] = trade_automation_service._serialize_datetime(
            now - timedelta(minutes=10)
        )
        desks["swing_position"]["runtime"]["worker_deferred_reason"] = "worker_batch_limit"
        desks["swing_position"]["runtime"]["worker_deferred_behind"] = ["fast_scalper"]
        trade_automation_service._write_trade_automation_desks_store(self.tenant, desks)
        self.db.commit()

        calls: list[str] = []

        def fake_scan(*args, **kwargs):
            calls.append(kwargs["desk_key"])
            return {"desk": {"desk_key": kwargs["desk_key"]}, "status": "scanned"}

        with patch.object(trade_automation_service, "scan_tenant_trade_automation_desk", side_effect=fake_scan):
            result = trade_automation_service.run_due_tenant_trade_automation_desks(
                self.db,
                current_user=self.current_user,
                max_desk_scans=1,
                worker_scan=True,
            )

        self.assertEqual(calls, ["swing_position"])
        self.assertEqual(result["scanned_due_desks"], ["swing_position"])
        self.assertIn("fast_scalper", result["deferred_due_desks"])

    def test_worker_fast_two_stage_scan_queues_deep_analysis_without_waiting(self) -> None:
        settings_state = {
            "tickers": ["AAPL", "MSFT", "NVDA"],
            "interval": "5m",
            "horizon": 5,
            "max_open_positions": 5,
            "deep_scan_limit": 2,
            "use_fast_model": True,
            "worker_scan": True,
            "tenant_id": self.tenant.id,
            "desk_key": "intraday_momentum",
        }
        stage_rows = [
            {"ticker": "AAPL", "stage_one_score": 91.0, "stage_one_rank": 1},
            {"ticker": "MSFT", "stage_one_score": 87.0, "stage_one_rank": 2},
            {"ticker": "NVDA", "stage_one_score": 75.0, "stage_one_rank": 3},
        ]
        stage_metadata = {
            "stage_one_ms": 4.0,
            "stage_one_count": 3,
            "configured_ticker_count": 3,
            "period": "5d",
            "interval": "5m",
            "stage_one_error": None,
        }

        def forbidden_deep_watchlist(*args, **kwargs):
            raise AssertionError("worker scan should not call slow deep analysis")

        def fake_enqueue(*args, **kwargs):
            return {
                "status": "deep_analysis_pending",
                "queued_count": 2,
                "queued_tickers": ["AAPL", "MSFT"],
                "inflight_count": 2,
                "next_action": "Async deep-analysis tasks were queued.",
            }

        with patch.object(
            trade_automation_service,
            "_build_stage_one_scan_rows",
            return_value=(stage_rows, stage_metadata),
        ), patch.object(
            trade_automation_service,
            "_enqueue_trade_automation_deep_analysis",
            side_effect=fake_enqueue,
        ), patch.object(trade_automation_service, "build_watchlist", side_effect=forbidden_deep_watchlist):
            watchlist = trade_automation_service._build_fast_two_stage_watchlist({}, settings_state)

        self.assertEqual(watchlist["automation_scan"]["worker_scan"], True)
        self.assertEqual(watchlist["automation_scan"]["deep_analyzed_count"], 0)
        self.assertFalse(watchlist["automation_scan"]["deep_analysis_deferred"])
        self.assertEqual(watchlist["automation_scan"]["deep_analysis_status"], "deep_analysis_pending")
        self.assertEqual(watchlist["automation_scan"]["deep_analysis_pending_count"], 2)
        blockers = {row.get("diagnostic_blocker") for row in watchlist["rows"][:2]}
        self.assertEqual(blockers, {"waiting_for_deep_analysis"})

    def test_worker_scan_uses_fresh_cached_deep_analysis_rows(self) -> None:
        now = trade_automation_service._utc_now()
        settings_state = {
            "tickers": ["AAPL", "MSFT"],
            "interval": "5m",
            "horizon": 5,
            "max_open_positions": 5,
            "deep_scan_limit": 2,
            "use_fast_model": True,
            "worker_scan": True,
            "tenant_id": self.tenant.id,
            "desk_key": "intraday_momentum",
        }
        cache_key = trade_automation_service._deep_analysis_cache_key(
            self.tenant.id,
            "intraday_momentum",
            "AAPL",
            "5m",
            5,
        )
        with trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_LOCK:
            trade_automation_service._TRADE_AUTOMATION_DEEP_ANALYSIS_CACHE[cache_key] = {
                "status": "deep_analysis_ready",
                "tenant_id": self.tenant.id,
                "desk_key": "intraday_momentum",
                "ticker": "AAPL",
                "interval": "5m",
                "horizon": 5,
                "row": {
                    "ticker": "AAPL",
                    "ranking_score": 96.0,
                    "setup_score": 94.0,
                    "trade_decision": "VALID TRADE",
                    "ranking_tier": "entry_candidate",
                    "verdict": "BULLISH",
                },
                "completed_at": trade_automation_service._serialize_datetime(now),
                "expires_at": trade_automation_service._serialize_datetime(now + timedelta(seconds=90)),
            }

        stage_rows = [
            {"ticker": "AAPL", "stage_one_score": 91.0, "stage_one_rank": 1},
            {"ticker": "MSFT", "stage_one_score": 87.0, "stage_one_rank": 2},
        ]
        stage_metadata = {
            "stage_one_ms": 4.0,
            "stage_one_count": 2,
            "configured_ticker_count": 2,
            "period": "5d",
            "interval": "5m",
            "stage_one_error": None,
        }

        def fake_enqueue(*args, **kwargs):
            return {"status": "deep_analysis_pending", "queued_count": 1, "queued_tickers": ["MSFT"], "inflight_count": 1}

        with patch.object(
            trade_automation_service,
            "_build_stage_one_scan_rows",
            return_value=(stage_rows, stage_metadata),
        ), patch.object(
            trade_automation_service,
            "_enqueue_trade_automation_deep_analysis",
            side_effect=fake_enqueue,
        ), patch.object(trade_automation_service, "build_watchlist", side_effect=AssertionError("worker should not block on deep analysis")):
            watchlist = trade_automation_service._build_fast_two_stage_watchlist({}, settings_state)

        rows = {row["ticker"]: row for row in watchlist["rows"]}
        self.assertEqual(rows["AAPL"]["stage"], "deep_analyzed")
        self.assertEqual(rows["AAPL"]["deep_analysis_status"], "deep_analysis_ready")
        self.assertNotIn("diagnostic_blocker", rows["AAPL"])
        self.assertEqual(rows["MSFT"]["diagnostic_blocker"], "waiting_for_deep_analysis")
        self.assertEqual(watchlist["automation_scan"]["deep_analysis_ready_count"], 1)
        self.assertEqual(watchlist["automation_scan"]["deep_analyzed_count"], 1)

    def test_trade_selection_edge_influences_ranking_without_overriding_hard_gates(self) -> None:
        now = trade_automation_service._utc_now()
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "tickers": ["AAPL", "MSFT"],
                    "account_size": 100000.0,
                    "instrument_type": "equity",
                    "max_open_positions": 12,
                    "risk_percent": 0.25,
                    "cycle_entry_rank_limit": 2,
                    "min_opportunity_score": 72.0,
                    "max_spread_bps_for_opportunity": 35.0,
                }
            }
        )
        rows = [
            {
                "ticker": "AAPL",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_tier": "entry_candidate",
                "execution_score": 80.0,
                "portfolio_score": 80.0,
                "opportunity_score": 95.0,
                "quote_age_seconds": 300.0,
                "spread_bps": 100.0,
            },
            {
                "ticker": "MSFT",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_tier": "entry_candidate",
                "execution_score": 80.0,
                "portfolio_score": 80.0,
                "stage_one_score": 92.0,
                "ranking_score": 92.0,
                "setup_score": 92.0,
                "opportunity_score": 95.0,
                "rapid_confirmed": True,
                "deep_analysis_status": "deep_analysis_ready",
                "quote_age_seconds": 10.0,
                "spread_bps": 5.0,
                "ai_evidence_review": {"verdict": "approve_evidence", "status": "reviewed"},
            },
        ]

        ranked = trade_automation_service._rank_automation_candidates(
            state=state,
            rows=rows,
            now=now,
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            current_equity=100000.0,
        )

        by_ticker = {item["ticker"]: item for item in ranked}
        self.assertEqual(ranked[0]["ticker"], "MSFT")
        self.assertEqual(by_ticker["MSFT"]["edge_usage_status"], "active_paper_ranking_input")
        self.assertGreater(by_ticker["MSFT"]["edge_priority_adjustment"], 0)
        self.assertLessEqual(by_ticker["MSFT"]["edge_priority_adjustment"], 5.0)
        self.assertEqual(by_ticker["AAPL"]["edge_usage_status"], "hard_gate_observed_no_uprank")
        self.assertLessEqual(by_ticker["AAPL"]["edge_priority_adjustment"], 0)
        self.assertIn("stale_quote_guard", by_ticker["AAPL"]["edge_reason_codes"])
        self.assertIn("wide_spread_guard", by_ticker["AAPL"]["edge_reason_codes"])
        self.assertFalse(by_ticker["AAPL"]["trade_selection_edge"]["can_submit_orders"])
        self.assertFalse(by_ticker["MSFT"]["trade_selection_edge"]["can_submit_live_orders"])
        self.assertEqual(by_ticker["MSFT"]["realtime_alpha_usage_status"], "active_paper_ranking_input")
        self.assertGreater(by_ticker["MSFT"]["realtime_alpha_priority_adjustment"], 0)
        self.assertLessEqual(by_ticker["MSFT"]["realtime_alpha_priority_adjustment"], 3.0)
        self.assertEqual(by_ticker["AAPL"]["realtime_alpha_usage_status"], "hard_gate_observed_no_uprank")
        self.assertLessEqual(by_ticker["AAPL"]["realtime_alpha_priority_adjustment"], 0)
        self.assertIn("stale_quote_guard", by_ticker["AAPL"]["realtime_alpha_reason_codes"])
        self.assertIn("wide_spread_guard", by_ticker["AAPL"]["realtime_alpha_reason_codes"])
        self.assertFalse(by_ticker["AAPL"]["realtime_alpha_ops"]["can_submit_orders"])
        self.assertFalse(by_ticker["MSFT"]["realtime_alpha_ops"]["can_submit_live_orders"])
        self.assertEqual(by_ticker["MSFT"]["adaptive_execution_usage_status"], "active_paper_ranking_and_allocator_input")
        self.assertGreater(by_ticker["MSFT"]["adaptive_execution_priority_adjustment"], 0)
        self.assertLessEqual(by_ticker["MSFT"]["adaptive_execution_priority_adjustment"], 2.5)
        self.assertEqual(by_ticker["AAPL"]["adaptive_execution_usage_status"], "hard_gate_observed_no_uprank")
        self.assertLessEqual(by_ticker["AAPL"]["adaptive_execution_priority_adjustment"], 0)
        self.assertIn("stale_quote_guard", by_ticker["AAPL"]["execution_learning_reason_codes"])
        self.assertIn("wide_spread_guard", by_ticker["AAPL"]["execution_learning_reason_codes"])
        self.assertFalse(by_ticker["AAPL"]["adaptive_execution_intelligence"]["can_submit_orders"])
        self.assertFalse(by_ticker["MSFT"]["adaptive_execution_intelligence"]["can_submit_live_orders"])
        self.assertEqual(by_ticker["MSFT"]["portfolio_outcome_usage_status"], "active_paper_portfolio_ranking_and_allocator_input")
        self.assertGreater(by_ticker["MSFT"]["portfolio_outcome_priority_adjustment"], 0)
        self.assertLessEqual(by_ticker["MSFT"]["portfolio_outcome_priority_adjustment"], 2.0)
        self.assertEqual(by_ticker["AAPL"]["portfolio_outcome_usage_status"], "hard_gate_observed_no_uprank")
        self.assertLessEqual(by_ticker["AAPL"]["portfolio_outcome_priority_adjustment"], 0)
        self.assertIn("stale_quote_guard", by_ticker["AAPL"]["portfolio_learning_reason_codes"])
        self.assertIn("wide_spread_guard", by_ticker["AAPL"]["portfolio_learning_reason_codes"])
        self.assertFalse(by_ticker["AAPL"]["portfolio_outcome_intelligence"]["can_submit_orders"])
        self.assertFalse(by_ticker["MSFT"]["portfolio_outcome_intelligence"]["can_submit_live_orders"])
        self.assertEqual(by_ticker["MSFT"]["institutional_operating_edge_usage_status"], "active_paper_institutional_ranking_and_allocator_input")
        self.assertGreater(by_ticker["MSFT"]["institutional_operating_edge_priority_adjustment"], 0)
        self.assertLessEqual(by_ticker["MSFT"]["institutional_operating_edge_priority_adjustment"], 1.25)
        self.assertEqual(by_ticker["AAPL"]["institutional_operating_edge_usage_status"], "hard_gate_observed_no_uprank")
        self.assertLessEqual(by_ticker["AAPL"]["institutional_operating_edge_priority_adjustment"], 0)
        self.assertIn("stale_quote_guard", by_ticker["AAPL"]["institutional_edge_reason_codes"])
        self.assertIn("wide_spread_guard", by_ticker["AAPL"]["institutional_edge_reason_codes"])
        self.assertFalse(by_ticker["AAPL"]["institutional_operating_edge"]["can_submit_orders"])
        self.assertFalse(by_ticker["MSFT"]["institutional_operating_edge"]["can_submit_live_orders"])

    def test_against_market_proxy_promotes_inverse_proxy_without_short_authority(self) -> None:
        now = trade_automation_service._utc_now()
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "tickers": ["SH", "PSQ", "SPY", "SDS"],
                    "account_size": 100000.0,
                    "instrument_type": "equity",
                    "max_open_positions": 12,
                    "risk_percent": 0.25,
                    "cycle_entry_rank_limit": 2,
                    "min_opportunity_score": 72.0,
                    "against_market_proxy_enabled": True,
                    "against_market_proxy_tickers": ["SH", "PSQ", "DOG", "RWM"],
                    "max_spread_bps_for_opportunity": 35.0,
                }
            }
        )
        rows = [
            {
                "ticker": "SH",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_tier": "entry_candidate",
                "execution_score": 88.0,
                "portfolio_score": 88.0,
                "stage_one_score": 90.0,
                "setup_score": 90.0,
                "opportunity_score": 92.0,
                "opportunity_type": "broad_index_risk_off",
                "confirmation_reason": "market weakness and downside acceleration",
                "relative_volume": 1.8,
                "rapid_confirmed": True,
                "deep_analysis_status": "deep_analysis_ready",
                "quote_age_seconds": 8.0,
                "spread_bps": 4.0,
            },
            {
                "ticker": "SPY",
                "trade_decision": "VALID TRADE",
                "verdict": "BEARISH",
                "ranking_tier": "entry_candidate",
                "execution_score": 95.0,
                "portfolio_score": 95.0,
                "opportunity_score": 98.0,
                "opportunity_type": "failed_breakout",
                "quote_age_seconds": 8.0,
                "spread_bps": 4.0,
            },
            {
                "ticker": "SDS",
                "trade_decision": "VALID TRADE",
                "verdict": "BULLISH",
                "ranking_tier": "entry_candidate",
                "execution_score": 86.0,
                "portfolio_score": 86.0,
                "opportunity_score": 94.0,
                "opportunity_type": "broad_index_risk_off",
                "relative_volume": 2.0,
                "rapid_confirmed": True,
                "deep_analysis_status": "deep_analysis_ready",
                "quote_age_seconds": 8.0,
                "spread_bps": 4.0,
            },
        ]

        ranked = trade_automation_service._rank_automation_candidates(
            state=state,
            rows=rows,
            now=now,
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            current_equity=100000.0,
        )

        by_ticker = {item["ticker"]: item for item in ranked}
        self.assertEqual(ranked[0]["ticker"], "SH")
        self.assertNotIn("SPY", by_ticker)
        self.assertTrue(by_ticker["SH"]["paper_routeable_against_market"])
        self.assertEqual(by_ticker["SH"]["proxy_symbol"], "SH")
        self.assertEqual(by_ticker["SH"]["against_market_proxy"]["route"], "broker_paper")
        self.assertEqual(by_ticker["SH"]["against_market_proxy"]["order_side"], "buy")
        self.assertFalse(by_ticker["SH"]["against_market_proxy"]["direct_short_authority"])
        self.assertFalse(by_ticker["SH"]["against_market_proxy"]["can_submit_orders"])
        self.assertFalse(by_ticker["SH"]["against_market_proxy"]["can_submit_live_orders"])
        self.assertGreater(by_ticker["SH"]["against_market_priority_adjustment"], 0)
        self.assertLessEqual(by_ticker["SH"]["against_market_priority_adjustment"], 2.0)
        self.assertFalse(by_ticker["SDS"]["paper_routeable_against_market"])
        self.assertIn("leveraged_inverse_excluded", by_ticker["SDS"]["against_market_reason_codes"])

    def test_deep_analysis_enqueue_records_pending_status_without_running_task(self) -> None:
        submitted: list[dict[str, object]] = []

        class FakeExecutor:
            def submit(self, fn, **kwargs):
                submitted.append({"fn": fn, "kwargs": kwargs})
                return None

        settings_state = {
            "tenant_id": self.tenant.id,
            "desk_key": "stat_arb",
            "interval": "1m",
            "horizon": 5,
            "max_open_positions": 4,
            "use_fast_model": True,
        }
        with patch.object(trade_automation_service, "_get_deep_analysis_executor", return_value=FakeExecutor()):
            summary = trade_automation_service._enqueue_trade_automation_deep_analysis(
                settings_state,
                ["AAPL", "MSFT"],
                include_contract_lookup=False,
            )

        self.assertEqual(summary["status"], "deep_analysis_pending")
        self.assertEqual(summary["queued_count"], 2)
        self.assertEqual(len(submitted), 2)
        status = trade_automation_service._build_trade_automation_deep_analysis_status(
            tenant_id=self.tenant.id,
            desk_key="stat_arb",
        )
        self.assertEqual(status["status"], "deep_analysis_pending")
        self.assertEqual(status["inflight_count"], 2)
        self.assertEqual({task["ticker"] for task in status["tasks"]}, {"AAPL", "MSFT"})

    def test_deep_analysis_enqueue_batches_work_to_worker_capacity(self) -> None:
        submitted: list[dict[str, object]] = []

        class FakeExecutor:
            def submit(self, fn, **kwargs):
                submitted.append({"fn": fn, "kwargs": kwargs})
                return None

        tickers = ["AAPL", "MSFT", "NVDA", "AMD", "AMZN", "META", "GOOGL", "TSLA"]
        settings_state = {
            "tenant_id": self.tenant.id,
            "desk_key": "intraday_momentum",
            "interval": "5m",
            "horizon": 5,
            "max_open_positions": 12,
            "use_fast_model": True,
        }

        with (
            patch.object(trade_automation_service, "_get_deep_analysis_executor", return_value=FakeExecutor()),
            patch.object(trade_automation_service, "_deep_analysis_max_workers", return_value=2),
            patch.object(trade_automation_service, "_deep_analysis_timeout_seconds", return_value=20),
        ):
            summary = trade_automation_service._enqueue_trade_automation_deep_analysis(
                settings_state,
                tickers,
                include_contract_lookup=False,
            )

        self.assertEqual(summary["queued_count"], 8)
        self.assertEqual(len(submitted), 2)
        self.assertEqual(
            sorted(len(item["kwargs"]["task_keys"]) for item in submitted),
            [4, 4],
        )
        status = trade_automation_service._build_trade_automation_deep_analysis_status(
            tenant_id=self.tenant.id,
            desk_key="intraday_momentum",
        )
        self.assertEqual(status["inflight_count"], 8)
        self.assertTrue(all(int(task["timeout_seconds"]) > 20 for task in status["tasks"]))
        self.assertEqual({task["task_group_size"] for task in status["tasks"]}, {4})

    def test_enabled_worker_invokes_due_desk_scheduler(self) -> None:
        calls: list[str] = []
        profile_cycles: list[str] = []
        state = trade_automation_service._read_trade_automation_state(self.tenant)
        state["settings"]["enabled"] = True
        state["settings"]["armed"] = True
        state["settings"]["kill_switch"] = False
        state["runtime"]["next_run_at"] = None
        trade_automation_service._write_trade_automation_state(self.tenant, state)
        self.db.commit()
        self.db.refresh(self.tenant)

        def fake_run_due(*args, **kwargs):
            self.assertEqual(kwargs.get("max_desk_scans"), 5)
            self.assertTrue(kwargs.get("worker_scan"))
            calls.append(str(kwargs["current_user"].tenant_id))
            return {
                "ran_count": 1,
                "skipped_count": 4,
                "due_desks": ["fast_scalper", "intraday_momentum"],
                "scanned_due_desks": ["fast_scalper"],
                "deferred_due_desks": ["intraday_momentum"],
            }

        def fake_run_cycle(*args, **kwargs):
            profile_cycles.append(str(kwargs.get("profile_key")))
            return {
                "status": {"key": "scheduled"},
                "runtime": {"last_action": {"type": "stand_down"}},
            }

        with (
            patch.object(trade_automation_service, "run_due_tenant_trade_automation_desks", side_effect=fake_run_due),
            patch.object(trade_automation_service, "_run_trade_automation_cycle", side_effect=fake_run_cycle),
        ):
            result = trade_automation_service.run_enabled_trade_automation_cycles(
                self.db,
                limit=1,
                worker_scan=True,
            )

        self.assertEqual(calls, [self.tenant.id])
        self.assertEqual(result["desks_processed"], 1)
        self.assertEqual(result["desks_skipped"], 4)
        self.assertEqual(result["desk_errors"], 0)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(profile_cycles, ["personal_paper"])
        self.assertEqual(result["desk_items"][0]["due_desks"], ["fast_scalper", "intraday_momentum"])
        self.assertEqual(result["desk_items"][0]["scanned_due_desks"], ["fast_scalper"])
        self.assertEqual(result["desk_items"][0]["deferred_due_desks"], ["intraday_momentum"])

    def test_enabled_worker_invokes_killed_profile_for_defensive_paper_exit(self) -> None:
        profile_cycles: list[str] = []
        state = trade_automation_service._read_trade_automation_state(self.tenant)
        state["settings"]["enabled"] = True
        state["settings"]["armed"] = False
        state["settings"]["kill_switch"] = True
        state["settings"]["auto_manage_positions"] = True
        state["settings"]["execution_intent"] = "broker_paper"
        state["runtime"]["next_run_at"] = "2099-01-01T00:00:00+00:00"
        trade_automation_service._write_trade_automation_state(self.tenant, state)
        self.db.commit()
        self.db.refresh(self.tenant)

        def fake_run_cycle(*args, **kwargs):
            profile_cycles.append(str(kwargs.get("profile_key")))
            self.assertTrue(kwargs["state"]["settings"]["kill_switch"])
            return {
                "status": {"key": "killed"},
                "runtime": {"last_action": {"type": "loss_containment_exit_while_killed"}},
            }

        with (
            patch.object(
                trade_automation_service,
                "run_due_tenant_trade_automation_desks",
                return_value={
                    "ran_count": 0,
                    "skipped_count": 5,
                    "due_desks": [],
                    "scanned_due_desks": [],
                    "deferred_due_desks": [],
                },
            ),
            patch.object(trade_automation_service, "_run_trade_automation_cycle", side_effect=fake_run_cycle),
        ):
            result = trade_automation_service.run_enabled_trade_automation_cycles(
                self.db,
                limit=1,
                worker_scan=True,
            )

        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["eligible"], 1)
        self.assertEqual(profile_cycles, ["personal_paper"])
        self.assertEqual(result["items"][0]["last_action"]["type"], "loss_containment_exit_while_killed")

    def test_collective_objective_lock_blocks_all_desk_entries(self) -> None:
        base_state = trade_automation_service._normalize_trade_automation_profile_state(
            {"settings": {"enabled": True, "armed": True, "kill_switch": False}}
        )
        objective = {"entries_blocked": True, "entry_block_reason": "target_reached_protect_streak"}

        blocker, detail = trade_automation_service._desk_global_blocker(base_state, objective)

        self.assertEqual(blocker, "target_reached_protect_streak")
        self.assertIn("weekly objective", detail.lower())

    def test_collective_loss_budget_lock_blocks_all_desk_entries(self) -> None:
        base_state = trade_automation_service._normalize_trade_automation_profile_state(
            {"settings": {"enabled": True, "armed": True, "kill_switch": False}}
        )
        objective = {"entries_blocked": True, "entry_block_reason": "daily_loss_budget_lock"}

        blocker, detail = trade_automation_service._desk_global_blocker(base_state, objective)

        self.assertEqual(blocker, "daily_loss_budget_lock")
        self.assertIn("daily loss-budget", detail.lower())

    def test_global_open_heat_blocks_all_desks(self) -> None:
        base_state = trade_automation_service._normalize_trade_automation_profile_state(
            {"settings": {"enabled": True, "armed": True, "kill_switch": False}}
        )
        open_rows = pd.DataFrame(
            [
                {
                    "automation_origin": "trade_automation",
                    "tenant_id": self.tenant.id,
                    "profile_key": "personal_paper",
                    "ticker": "SPY",
                    "total_position_cost": 101000.0,
                }
            ]
        )
        allocator = trade_automation_service._build_trade_automation_global_risk_allocator(
            current_equity=100000.0,
            owned_open=open_rows,
            owned_pending=pd.DataFrame(),
        )

        blocker, detail = trade_automation_service._desk_global_blocker(base_state, {}, allocator)

        self.assertTrue(allocator["blocked"])
        self.assertEqual(blocker, "global_open_heat_cap")
        self.assertIn("open heat", detail.lower())

    def test_position_desks_do_not_receive_daily_objective_ranking_pressure(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "daily_objective_enabled": True,
                    "daily_objective_ranking_enabled": False,
                    "daily_profit_target_pct": 1.0,
                    "daily_profit_target_dollars": 1000.0,
                    "account_size": 100000.0,
                },
                "runtime": {"daily_objective_last_report": {"target_gap": 1000.0}},
            }
        )
        candidates = [{"ticker": "SPY", "portfolio_score": 80.0, "execution_score": 80.0}]

        result = automation_daily_objective_service.apply_daily_objective_candidate_overlay(
            candidates,
            state=state,
            current_equity=100000.0,
        )

        self.assertEqual(result, candidates)

    def test_primary_intraday_desks_receive_daily_objective_context(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "daily_objective_enabled": True,
                    "daily_objective_ranking_enabled": True,
                    "daily_profit_target_pct": 1.0,
                    "daily_profit_target_dollars": 1000.0,
                    "account_size": 100000.0,
                },
                "runtime": {"daily_objective_last_report": {"target_gap": 1000.0}},
            }
        )
        candidates = [
            {
                "ticker": "SPY",
                "portfolio_score": 80.0,
                "execution_score": 80.0,
                "expected_edge_bps": 20.0,
                "edge_to_cost_ratio": 2.0,
                "projected_position_cost": 10000.0,
            }
        ]

        result = automation_daily_objective_service.apply_daily_objective_candidate_overlay(
            candidates,
            state=state,
            current_equity=100000.0,
        )

        self.assertIn("daily_objective_score", result[0])
        self.assertGreater(result[0]["daily_objective_score"], 0.0)

    def test_global_kill_switch_blocks_desk_entries(self) -> None:
        base_state = trade_automation_service._normalize_trade_automation_profile_state(
            {"settings": {"enabled": True, "armed": True, "kill_switch": True}}
        )

        blocker, detail = trade_automation_service._desk_global_blocker(base_state, {})

        self.assertEqual(blocker, "kill_switch_active")
        self.assertIn("kill switch", detail.lower())

    def test_kill_switch_clear_readiness_blocks_stale_loss_containment_action(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {"settings": {"enabled": True, "armed": False, "kill_switch": True, "execution_intent": "broker_paper"}}
        )
        state["runtime"]["loss_containment_last_report"] = {
            "status": "action_required",
            "entries_blocked": True,
            "defensive_actions": [
                {
                    "ticker": "AAPL",
                    "action": "EXIT FULLY NOW",
                    "auto_close_eligible": True,
                }
            ],
        }
        report = {
            "broker_available": True,
            "status": "warning",
            "current_route_reconciliation_status": "clean",
            "current_route_orphan_order_event_count": 0,
            "ledger_consistency": "consistent",
            "blockers": [],
        }

        readiness = trade_automation_service._build_kill_switch_clear_readiness(
            state,
            paper_broker_report=report,
        )

        blocker_keys = {item["key"] for item in readiness["blockers"]}
        self.assertFalse(readiness["can_clear"])
        self.assertIn("loss_containment_action_required", blocker_keys)
        self.assertIn("loss-containment review", readiness["blockers"][0]["detail"])

    def test_recovered_global_kill_switch_does_not_leave_desk_summary_blocked(self) -> None:
        base_state = trade_automation_service._read_trade_automation_state(self.tenant)
        base_state["settings"]["enabled"] = True
        base_state["settings"]["armed"] = True
        base_state["settings"]["kill_switch"] = False
        trade_automation_service._write_trade_automation_state(self.tenant, base_state)

        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        desks["macro"]["settings"]["enabled"] = True
        desks["macro"]["settings"]["armed"] = True
        desks["macro"]["runtime"]["top_blocker"] = "kill_switch_active"
        desks["macro"]["runtime"]["last_decision"] = {
            "decision": "stand_down",
            "reason": "kill_switch_active",
            "detail": "The global kill switch is active.",
        }
        trade_automation_service._write_trade_automation_desks_store(self.tenant, desks)

        payload = trade_automation_service.list_tenant_trade_automation_desks(
            self.db,
            current_user=self.current_user,
        )
        macro = {item["desk_key"]: item for item in payload["items"]}["macro"]

        self.assertIsNone(macro["top_blocker"])
        self.assertTrue(macro["safe_to_trade"])
        self.assertEqual(macro["runtime"]["recovered_blocker"], "kill_switch_active")
        self.assertEqual(macro["execution_intelligence"]["no_trade_root_cause"], "waiting_for_schedule")

    def _promotion_ready_state(self, *, max_open_positions: int = 12) -> dict:
        return trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "enabled": True,
                    "armed": True,
                    "kill_switch": False,
                    "execution_intent": "broker_paper",
                    "max_open_positions": max_open_positions,
                    "max_total_open_notional": 50000.0,
                    "max_daily_loss_pct": 0.5,
                    "risk_percent": 0.25,
                    "daily_objective_apply_to_live": False,
                    "loss_containment_auto_close_live": False,
                    "exit_watchdog_apply_to_live": False,
                },
                "runtime": {
                    "current_route_reconciliation_status": "clean",
                    "current_route_orphan_order_event_count": 0,
                    "legacy_orphan_order_event_count": 0,
                    "last_safety_preflight": {
                        "status": "ready",
                        "safe_to_trade": True,
                        "reason": "preflight_ready",
                    },
                },
            }
        )

    def test_position_promotion_clean_cycles_raise_slots_without_changing_risk_caps(self) -> None:
        state = self._promotion_ready_state(max_open_positions=12)
        original_caps = {
            "max_total_open_notional": state["settings"]["max_total_open_notional"],
            "max_daily_loss_pct": state["settings"]["max_daily_loss_pct"],
            "risk_percent": state["settings"]["risk_percent"],
            "execution_intent": state["settings"]["execution_intent"],
        }

        for index in range(40):
            payload = trade_automation_service._record_position_promotion_evidence(
                state,
                now=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
                cycle_id=f"cycle-{index}",
            )

        self.assertEqual(state["settings"]["max_open_positions"], 16)
        self.assertEqual(payload["clean_cycle_count"], 40)
        self.assertEqual(payload["last_promotion"]["to"], 16)
        for key, value in original_caps.items():
            self.assertEqual(state["settings"][key], value)

        for index in range(40, 75):
            payload = trade_automation_service._record_position_promotion_evidence(
                state,
                now=datetime(2026, 5, 1, 15, 0, tzinfo=timezone.utc),
                cycle_id=f"cycle-{index}",
            )

        self.assertEqual(state["settings"]["max_open_positions"], 20)
        self.assertEqual(payload["next_target_positions"], 24)

    def test_position_promotion_failed_or_stale_cycle_does_not_increment(self) -> None:
        state = self._promotion_ready_state(max_open_positions=12)
        state["settings"]["kill_switch"] = True

        payload = trade_automation_service._record_position_promotion_evidence(
            state,
            now=datetime(2026, 5, 1, 14, 0, tzinfo=timezone.utc),
            cycle_id="blocked-cycle",
            worker_status={"status": "running_but_stale", "stale": True},
        )

        self.assertEqual(state["settings"]["max_open_positions"], 12)
        self.assertEqual(payload["clean_cycle_count"], 0)
        self.assertIn("kill_switch_active", payload["blockers"])
        self.assertIn("worker_stale", payload["blockers"])

    def test_position_promotion_clean_session_counts_once_per_market_day(self) -> None:
        state = self._promotion_ready_state(max_open_positions=12)
        daily_summary = {
            "day": "2026-05-01",
            "status_counts": {"ready": 24},
            "top_blockers": [],
        }

        first = trade_automation_service._record_position_promotion_evidence(
            state,
            now=datetime(2026, 5, 1, 20, 5, tzinfo=timezone.utc),
            session_day="2026-05-01",
            daily_summary=daily_summary,
        )
        second = trade_automation_service._record_position_promotion_evidence(
            state,
            now=datetime(2026, 5, 1, 20, 10, tzinfo=timezone.utc),
            session_day="2026-05-01",
            daily_summary=daily_summary,
        )

        self.assertEqual(first["clean_session_count"], 1)
        self.assertEqual(second["clean_session_count"], 1)
        self.assertEqual(second["last_clean_session_day"], "2026-05-01")

    def test_position_promotion_blocks_non_paper_route(self) -> None:
        state = self._promotion_ready_state(max_open_positions=12)
        state["settings"]["execution_intent"] = "broker_live"

        payload = trade_automation_service._build_position_promotion_payload(state)

        self.assertIn("route_not_broker_paper", payload["blockers"])
        self.assertFalse(payload["milestone_met"])

    def test_position_promotion_uses_latest_clean_reconciliation_report(self) -> None:
        state = self._promotion_ready_state(max_open_positions=30)
        state["runtime"]["current_route_reconciliation_status"] = "issues_present"
        state["runtime"]["current_route_orphan_order_event_count"] = 0
        state["runtime"]["paper_broker_reconciliation_last_report"] = {
            "current_route_reconciliation_status": "clean",
            "current_route_orphan_order_event_count": 0,
            "status": "warning",
        }

        payload = trade_automation_service._build_position_promotion_payload(state)

        self.assertNotIn("route_reconciliation_not_clean", payload["blockers"])
        self.assertNotIn("current_route_orphans_present", payload["blockers"])

    def test_macro_next_scan_before_entry_window_is_same_market_day_1015_et(self) -> None:
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        settings = desks["macro"]["settings"]
        now = datetime(2026, 4, 30, 13, 0, tzinfo=timezone.utc)  # 9:00 AM ET

        next_scan = trade_automation_service._desk_next_scan_at(settings, {}, now)

        self.assertEqual(next_scan, datetime(2026, 4, 30, 14, 15, tzinfo=timezone.utc))

    def test_macro_is_due_after_entry_time_when_not_scanned_today(self) -> None:
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        settings = desks["macro"]["settings"]
        runtime = {"last_scan_at": "2026-04-29T14:15:00+00:00"}
        now = datetime(2026, 4, 30, 15, 0, tzinfo=timezone.utc)  # 11:00 AM ET

        next_scan = trade_automation_service._desk_next_scan_at(settings, runtime, now)

        self.assertEqual(next_scan, datetime(2026, 4, 30, 14, 15, tzinfo=timezone.utc))
        self.assertTrue(trade_automation_service._is_trade_automation_desk_due(settings, runtime, now))

    def test_macro_next_scan_after_today_scan_moves_to_next_market_day_1015_et(self) -> None:
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        settings = desks["macro"]["settings"]
        runtime = {"last_scan_at": "2026-04-30T14:20:00+00:00"}
        now = datetime(2026, 4, 30, 15, 0, tzinfo=timezone.utc)

        next_scan = trade_automation_service._desk_next_scan_at(settings, runtime, now)

        self.assertEqual(next_scan, datetime(2026, 5, 1, 14, 15, tzinfo=timezone.utc))
        self.assertFalse(trade_automation_service._is_trade_automation_desk_due(settings, runtime, now))

    def test_macro_weekend_next_scan_moves_to_next_market_day(self) -> None:
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        settings = desks["macro"]["settings"]
        now = datetime(2026, 5, 2, 16, 0, tzinfo=timezone.utc)  # Saturday

        next_scan = trade_automation_service._desk_next_scan_at(settings, {}, now)

        self.assertEqual(next_scan, datetime(2026, 5, 4, 14, 15, tzinfo=timezone.utc))

    def test_macro_known_holiday_next_scan_moves_to_next_market_day(self) -> None:
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        settings = desks["macro"]["settings"]
        now = datetime(2026, 12, 25, 15, 0, tzinfo=timezone.utc)

        next_scan = trade_automation_service._desk_next_scan_at(settings, {}, now)

        self.assertEqual(next_scan, datetime(2026, 12, 28, 15, 15, tzinfo=timezone.utc))

    def test_macro_preclose_review_window_does_not_allow_new_entries_after_cutoff(self) -> None:
        desks = trade_automation_service._read_trade_automation_desks_store(self.tenant)
        settings = desks["macro"]["settings"]
        now = datetime(2026, 4, 30, 19, 31, tzinfo=timezone.utc)  # 3:31 PM ET

        self.assertFalse(trade_automation_service._macro_new_entries_allowed_now(settings, now))

    def test_desk_export_import_and_reset_are_auditable(self) -> None:
        exported = trade_automation_service.export_tenant_trade_automation_desk(
            self.db,
            current_user=self.current_user,
            desk_key="fast_scalper",
        )
        self.assertEqual(exported["desk_key"], "fast_scalper")
        self.assertIn("state", exported)

        imported = trade_automation_service.import_tenant_trade_automation_desk(
            self.db,
            current_user=self.current_user,
            desk_key="fast_scalper",
            payload={"settings": {"enabled": True, "armed": True, "cycle_interval_seconds": 30}},
        )
        self.assertEqual(imported["desk_key"], "fast_scalper")

        reset = trade_automation_service.reset_tenant_trade_automation_desk_runtime(
            self.db,
            current_user=self.current_user,
            desk_key="fast_scalper",
            note="unit test reset",
        )
        self.assertTrue(reset["reset"])

    def test_production_trust_alert_delivery_is_disabled_until_configured(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            payload = trade_automation_service._production_trust_alert_delivery_status()

        self.assertEqual(payload["status"], "not_configured")
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["can_submit_orders"])
        self.assertFalse(payload["can_submit_live_orders"])
        self.assertIn("watchdog_blocked", payload["triggers"])

    def test_production_trust_evidence_quality_scores_useful_events(self) -> None:
        payload = trade_automation_service._production_trust_evidence_quality(
            {
                "observed_event_count": 100,
                "event_sources": {
                    "candidate_lifecycle_rows": 40,
                    "diagnostic_summaries": 5,
                    "market_session_snapshots": 3,
                    "ai_review_observations": 8,
                    "blocker_observations": 20,
                    "missed_move_observations": 7,
                    "read_only_audit_snapshots": 4,
                    "roadmap_activation_snapshots": 4,
                },
            }
        )

        self.assertGreater(payload["quality_score"], 0)
        self.assertIn("useful", payload["categories"])
        self.assertIn("missed_move", payload["categories"])
        self.assertFalse(payload["can_submit_orders"])
        self.assertFalse(payload["can_submit_live_orders"])

    def test_replay_report_is_evidence_only(self) -> None:
        payload = trade_automation_service._production_trust_replay_report(
            tenant_slug=self.tenant.slug,
            candidate_diagnostics={"summary": {"blockers_by_reason": {"waiting_for_deep_analysis": 2}}},
            no_trade_report={"missed_opportunities": [{"would_catch_now": True}], "strong_missed_opportunity_count": 1},
            persist=False,
        )

        self.assertTrue(payload["evidence_only"])
        self.assertTrue(payload["would_catch_now"])
        self.assertFalse(payload["can_submit_orders"])
        self.assertFalse(payload["can_submit_live_orders"])

    def test_production_trust_center_cannot_change_order_authority(self) -> None:
        payload = trade_automation_service._production_trust_center_from_market_session(
            tenant_slug=self.tenant.slug,
            market_session={
                "phase": {"phase": "active_session_monitor"},
                "safety_state": {"status": "ready"},
                "desks": {"active_armed_count": 5, "count": 5},
                "expected_settings_proof": {"status": "ready"},
                "alpaca_reconciliation_console": {"status": "ready"},
                "evidence_million_target": {"observed_event_count": 10},
                "paper_route_only": True,
            },
            candidate_diagnostics={"summary": {"scanned_count": 45}},
            no_trade_report={},
            evidence_million={"observed_event_count": 10, "event_sources": {"candidate_lifecycle_rows": 10}},
            components=[],
        )

        self.assertFalse(payload["can_submit_orders"])
        self.assertFalse(payload["can_submit_live_orders"])
        self.assertFalse(payload["can_clear_kill_switch"])
        self.assertIn("alert_delivery", payload)
        self.assertIn("support_bundle", payload)


if __name__ == "__main__":
    unittest.main()
