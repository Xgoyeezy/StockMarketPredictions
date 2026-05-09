from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import Tenant
from backend.services import trade_automation_service


class TradeAutomationCandidateDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        SessionLocal = sessionmaker(bind=self.engine, future=True)
        self.db = SessionLocal()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        with trade_automation_service._TRADE_AUTOMATION_STAGE_ONE_CACHE_LOCK:
            trade_automation_service._TRADE_AUTOMATION_STAGE_ONE_FRAME_CACHE.clear()
        self.addCleanup(self._clear_stage_one_cache)

        self.tenant = Tenant(slug="candidate-diagnostics", name="Candidate Diagnostics", status="active")
        self.db.add(self.tenant)
        self.db.commit()
        self.db.refresh(self.tenant)
        self.current_user = SimpleNamespace(
            tenant_id=self.tenant.id,
            tenant_slug=self.tenant.slug,
            auth_subject="diagnostic-user",
            user_id="diagnostic-user",
            email="diagnostic@example.test",
            name="Diagnostic User",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=("tenant.manage_support", "trade.execute", "market.read", "tenant.read"),
        )

    def _clear_stage_one_cache(self) -> None:
        with trade_automation_service._TRADE_AUTOMATION_STAGE_ONE_CACHE_LOCK:
            trade_automation_service._TRADE_AUTOMATION_STAGE_ONE_FRAME_CACHE.clear()

    def _regular_session_snapshot(self) -> dict:
        now_et = datetime(2026, 4, 29, 12, 0, tzinfo=trade_automation_service._MARKET_TIMEZONE)
        session_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return {
            "now_et": now_et,
            "regular_session": True,
            "cleanup_window": False,
            "phase": "regular_session",
            "session_mode": "regular",
            "session_label": "Regular session",
            "extended_session": False,
            "new_entries_allowed": True,
            "minutes_to_close": 240,
            "session_open": now_et.replace(hour=9, minute=30, second=0, microsecond=0),
            "session_close": session_close,
            "flatten_at": session_close - timedelta(minutes=15),
        }

    def _seed_state(self, tickers: list[str] | None = None) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "tickers": tickers or ["CLEAN", "WEAK", "NEWS"],
                "execution_intent": "broker_paper",
                "auto_trade_equities": True,
                "auto_trade_listed_options": False,
                "instrument_type": "equity",
                "dynamic_sizing_enabled": True,
                "dynamic_ticket_pct_of_equity": 10.0,
                "dynamic_total_open_pct_of_equity": 50.0,
                "account_size": 100000.0,
                "cycle_entry_rank_limit": 2,
                "require_liquidity_fields": True,
                "require_edge_fields": True,
                "max_spread_bps": 25.0,
                "min_average_dollar_volume": 1_000_000.0,
                "min_edge_to_cost_ratio": 2.5,
            }
        )
        trade_automation_service._write_trade_automation_state(self.tenant, state)
        self.db.commit()
        return state

    @staticmethod
    def _watchlist_payload() -> dict:
        return {
            "rows": [
                {
                    "ticker": "CLEAN",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_score": 92.0,
                    "execution_score": 84.0,
                    "portfolio_score": 88.0,
                    "expected_edge_bps": 90.0,
                    "spread_bps": 8.0,
                    "average_dollar_volume": 10_000_000.0,
                    "live_price": 100.0,
                },
                {
                    "ticker": "WEAK",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_score": 55.0,
                    "execution_score": 50.0,
                    "portfolio_score": 90.0,
                    "expected_edge_bps": 90.0,
                    "spread_bps": 8.0,
                    "average_dollar_volume": 10_000_000.0,
                    "live_price": 100.0,
                },
                {
                    "ticker": "NEWS",
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_score": 90.0,
                    "event_risk": True,
                    "execution_score": 90.0,
                    "portfolio_score": 90.0,
                    "live_price": 100.0,
                },
            ]
        }

    @staticmethod
    def _download_payload_for_tickers(tickers: list[str]) -> dict[str, pd.DataFrame]:
        index = pd.date_range(
            datetime(2026, 4, 29, 13, 0, tzinfo=timezone.utc),
            periods=30,
            freq="5min",
        )
        payload: dict[str, pd.DataFrame] = {}
        for offset, ticker in enumerate(tickers):
            base = 90.0 + offset
            payload[ticker] = pd.DataFrame(
                {
                    "Open": [base + step * 0.08 for step in range(30)],
                    "High": [base + step * 0.08 + 0.4 for step in range(30)],
                    "Low": [base + step * 0.08 - 0.3 for step in range(30)],
                    "Close": [base + step * 0.1 + offset * 0.02 for step in range(30)],
                    "Volume": [1_000_000 + offset * 20_000 for _ in range(30)],
                },
                index=index,
            )
        return payload

    def test_stage_one_uses_short_period_for_fast_full_universe_scan(self) -> None:
        self.assertEqual(trade_automation_service._stage_one_period_for_interval("1m"), "5d")
        self.assertEqual(trade_automation_service._stage_one_period_for_interval("5m"), "5d")
        self.assertEqual(trade_automation_service._stage_one_period_for_interval("15m"), "30d")

    def test_stage_one_frames_can_serve_full_universe_from_cache_without_provider_call(self) -> None:
        tickers = list(trade_automation_service._AUTOMATION_LIQUID_45_TICKERS)
        trade_automation_service._cache_stage_one_frames(
            self._download_payload_for_tickers(tickers),
            period="5d",
            interval="5m",
        )

        with patch.object(
            trade_automation_service.sdm,
            "batch_download_ohlcv",
            side_effect=AssertionError("provider should not be called when every stage-one frame is cached"),
        ):
            frames, error, timed_out, metadata = trade_automation_service._download_stage_one_frames(
                tickers,
                period="5d",
                interval="5m",
                timeout_seconds=0.01,
            )

        self.assertFalse(timed_out)
        self.assertIsNone(error)
        self.assertEqual(len(frames), 45)
        self.assertEqual(metadata["cache_hit_count"], 45)
        self.assertEqual(metadata["downloaded_count"], 0)
        self.assertEqual(metadata["missing_count"], 0)

    def test_stage_one_timeout_preserves_cached_symbols_and_reports_missing_symbols(self) -> None:
        trade_automation_service._cache_stage_one_frames(
            self._download_payload_for_tickers(["CLEAN"]),
            period="5d",
            interval="5m",
        )

        def slow_download(*args, **kwargs):
            time.sleep(0.05)
            return self._download_payload_for_tickers(["SLOW"])

        with patch.object(trade_automation_service.sdm, "batch_download_ohlcv", side_effect=slow_download):
            frames, error, timed_out, metadata = trade_automation_service._download_stage_one_frames(
                ["CLEAN", "SLOW"],
                period="5d",
                interval="5m",
                timeout_seconds=0.001,
            )

        self.assertTrue(timed_out)
        self.assertIn("CLEAN", frames)
        self.assertNotIn("SLOW", frames)
        self.assertIn("symbols missed", error or "")
        self.assertEqual(metadata["cache_hit_count"], 1)
        self.assertEqual(metadata["missing_count"], 1)

    def test_candidate_diagnostics_explain_each_watchlist_row_without_mutating_state(self) -> None:
        before_state = self._seed_state()
        with (
            patch.object(trade_automation_service, "build_watchlist", return_value=self._watchlist_payload()),
            patch.object(
                trade_automation_service,
                "_build_session_snapshot",
                return_value=self._regular_session_snapshot(),
            ),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(
                trade_automation_service,
                "_resolve_trade_automation_profile_account_context",
                return_value={
                    "current_user": self.current_user,
                    "linked_account": None,
                    "effective_funds": 100000.0,
                    "actual_funds": 100000.0,
                    "execution_intent": "broker_paper",
                    "account_summary": {
                        "equity": 100000.0,
                        "portfolio_value": 100000.0,
                        "buying_power": 400000.0,
                    },
                },
            ),
        ):
            diagnostics = trade_automation_service.get_tenant_trade_automation_candidate_diagnostics(
                self.db,
                current_user=self.current_user,
                scope="personal_paper",
                refresh=True,
            )

        self.db.refresh(self.tenant)
        after_state = trade_automation_service._read_trade_automation_state(self.tenant)
        self.assertEqual(before_state["runtime"], after_state["runtime"])
        self.assertEqual(diagnostics["execution_intent"], "broker_paper")
        self.assertEqual(diagnostics["summary"]["scanned_count"], 3)
        self.assertEqual(diagnostics["summary"]["eligible_count"], 1)
        self.assertIsNone(diagnostics["summary"]["top_blocker"])
        self.assertEqual(diagnostics["summary"]["top_rejection_reason"], "execution_score_below_60")
        blockers = {item["ticker"]: item.get("blocker") for item in diagnostics["candidates"]}
        self.assertIsNone(blockers["CLEAN"])
        self.assertEqual(blockers["WEAK"], "execution_score_below_60")
        self.assertEqual(blockers["NEWS"], "event_risk")
        self.assertEqual(diagnostics["sizing"]["dynamic_max_notional_per_trade"], 10000.0)
        self.assertEqual(diagnostics["sizing"]["dynamic_max_total_open_notional"], 50000.0)
        weak_row = next(item for item in diagnostics["candidates"] if item["ticker"] == "WEAK")
        self.assertIn("component_breakdown", weak_row)
        self.assertIn("quote_freshness", weak_row)
        self.assertIn("candidate_lifecycle_id", weak_row)
        self.assertIn("routeability", weak_row)
        self.assertEqual(weak_row["routeability"]["execution_route"], "broker_paper")
        self.assertTrue(weak_row["routeability"]["paper_only"])
        self.assertIn("against_market_proxy", weak_row)
        self.assertIn("bearish_confirmation_score", weak_row)
        self.assertFalse(weak_row["against_market_proxy"]["direct_short_authority"])
        self.assertFalse(weak_row["against_market_proxy"]["can_submit_live_orders"])
        self.assertEqual(weak_row["routeability"]["against_market_order_side"], "buy")
        self.assertIn("market_possibility_engine", weak_row)
        self.assertIn("scenario_probability", weak_row)
        self.assertIn("expected_value_score", weak_row)
        self.assertFalse(weak_row["market_possibility_engine"]["counts_toward_live_million"])
        self.assertFalse(weak_row["market_possibility_engine"]["can_submit_orders"])
        self.assertIn("rapid_confirmation_proof", weak_row)
        self.assertIn("duplicate_guard_evidence", weak_row)
        self.assertIn("average_down_proof", weak_row)
        self.assertIn("pyramiding_proof", weak_row)
        self.assertIn("rejected_by_stage_counts", diagnostics["summary"])
        self.assertIn("cache", diagnostics["summary"])
        self.assertIn("missed_opportunities", diagnostics["summary"])
        self.assertIn("routeability_counts", diagnostics["summary"])
        self.assertIn("against_market_proxy_context", diagnostics["summary"])
        self.assertEqual(diagnostics["summary"]["against_market_proxy_context"]["execution_route"], "broker_paper")
        self.assertFalse(diagnostics["summary"]["against_market_proxy_context"]["direct_short_authority"])
        self.assertIn("SH", diagnostics["summary"]["against_market_proxy_context"]["proxy_universe"])
        self.assertIn("market_possibility_engine_context", diagnostics["summary"])
        self.assertFalse(diagnostics["summary"]["market_possibility_engine_context"]["counts_toward_live_million"])
        self.assertFalse(diagnostics["summary"]["market_possibility_engine_context"]["can_submit_orders"])
        self.assertIn("candidate_lifecycle", diagnostics["summary"])
        self.assertIn("missed_trade_ai_review", diagnostics["summary"])
        self.assertIn("candidate_lifecycle_artifact", diagnostics["summary"])
        self.assertIn("missed_move_leaderboard", diagnostics["summary"])
        self.assertTrue(diagnostics["summary"]["candidate_lifecycle_artifact"]["append_only"])
        self.assertIn("reason_code_counts", diagnostics["summary"]["ai_evidence_review"])
        self.assertIn("institutional_position_policy", diagnostics["summary"])
        self.assertEqual(
            diagnostics["summary"]["institutional_position_policy"]["mode"],
            "institutional_risk_allocated",
        )
        self.assertEqual(
            diagnostics["summary"]["institutional_position_policy"]["institutional_baseline_positions"],
            12,
        )
        self.assertIn("quant_evidence_control_plane", diagnostics["summary"])
        evidence_os = diagnostics["summary"]["quant_evidence_control_plane"]
        self.assertEqual(evidence_os["product_name"], "Quant Evidence Operating System")
        self.assertTrue(evidence_os["operator_questions"]["why_no_trade"]["answerable"])
        self.assertTrue(evidence_os["operator_questions"]["prove_no_live_bypass"]["answerable"])
        self.assertIn("entry_window_explainer", diagnostics)
        self.assertIn("entry_window_explainer", diagnostics["summary"])
        self.assertEqual(
            diagnostics["entry_window_explainer"]["plain_language"],
            diagnostics["summary"]["entry_window_explainer"]["plain_language"],
        )

    def test_no_trade_report_is_read_only_and_explains_root_causes(self) -> None:
        self._seed_state(["CLEAN", "WEAK", "NEWS"])
        cached_diagnostics = {
            "source": "cached_cycle",
            "evaluated_at": "2026-04-29T16:00:00+00:00",
            "universe": {
                "label": "45-symbol liquid scan board",
                "ticker_count": 45,
                "scanned_count": 45,
                "scan_mode": "fast_two_stage_full_universe",
            },
            "summary": {
                "scanned_count": 45,
                "eligible_count": 0,
                "top_blocker": "waiting_for_deep_analysis",
                "blockers_by_reason": {"waiting_for_deep_analysis": 8, "weak_opportunity_score": 20},
                "opportunity_capture": {
                    "opportunity_candidates_count": 4,
                    "rapid_confirmed_count": 0,
                    "missed_breakout_report": {
                        "rows": [
                            {
                                "ticker": "SPY",
                                "opportunity_score": 82,
                                "would_priority_deep_scan": True,
                                "reason": "waiting_for_deep_analysis",
                            }
                        ]
                    },
                },
                "against_market_proxy_context": {
                    "mode": "against_market_proxy",
                    "execution_route": "broker_paper",
                    "signal_count": 1,
                    "routeable_proxy_count": 0,
                    "proxy_universe": ["SH", "PSQ", "DOG", "RWM"],
                    "direct_short_authority": False,
                    "can_submit_live_orders": False,
                },
                "missed_opportunities": [],
            },
        }
        active_phase = {
            "phase": "active_session_monitor",
            "now_et": "2026-04-29T12:00:00-04:00",
            "active_window": True,
        }

        with (
            patch.object(trade_automation_service, "_market_ops_session_phase", return_value=active_phase),
            patch.object(
                trade_automation_service,
                "get_tenant_trade_automation_candidate_diagnostics",
                return_value=cached_diagnostics,
            ),
        ):
            report = trade_automation_service.get_tenant_trade_automation_no_trade_report(
                self.db,
                current_user=self.current_user,
            )

        self.assertTrue(report["read_only"])
        self.assertEqual(report["mutation"], "none")
        self.assertFalse(report["can_submit_orders"])
        self.assertEqual(report["escalation_stage"], "why_no_trade_by_noon")
        self.assertEqual(report["issue_category"], "worker_or_deep_analysis_delay")
        self.assertEqual(report["strong_missed_opportunity_count"], 1)
        self.assertIn("opportunity_refresh", {item["key"] for item in report["operator_actions"]})
        self.assertIn("entry_window_explainer", report)
        self.assertIn("opportunity_refresh", report)
        self.assertTrue(report["opportunity_refresh"]["read_only"])
        self.assertIn("missed_move_intelligence", report)
        self.assertIn("candidate_lifecycle_artifact", report)
        self.assertIn("missed_move_leaderboard", report)
        self.assertIn("against_market_proxy_context", report)
        self.assertIn("market_possibility_engine_context", report)
        self.assertFalse(report["market_possibility_engine_context"]["can_submit_orders"])
        self.assertEqual(
            report["against_market_proxy_context"]["no_trade_language"],
            "Broad market was weak, but no routeable proxy passed confirmation.",
        )
        self.assertFalse(report["against_market_proxy_context"]["direct_short_authority"])
        self.assertIn("follow_up_windows", report["missed_move_intelligence"])
        self.assertIn("diagnostics_exports", report)
        self.assertIn("candidate_diagnostics", report["diagnostics_exports"])

    def test_market_possibility_engine_is_bounded_and_cannot_override_hard_gates(self) -> None:
        now = datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
        settings_state = trade_automation_service._normalize_trade_automation_profile_state({})["settings"]
        strong_candidate = {
            "ticker": "SPY",
            "trade_decision": "VALID TRADE",
            "verdict": "BULLISH",
            "opportunity_score": 94.0,
            "stage_one_priority_score": 91.0,
            "deep_score": 88.0,
            "realtime_alpha_score": 86.0,
            "adaptive_execution_score": 84.0,
            "portfolio_outcome_score": 82.0,
            "institutional_operating_edge_score": 80.0,
            "relative_volume": 2.1,
            "spread_bps": 6.0,
            "quote_age_seconds": 8.0,
            "rapid_confirmed": True,
            "average_dollar_volume": 20_000_000.0,
            "expected_edge_bps": 95.0,
        }
        overlay = trade_automation_service._market_possibility_engine_overlay(
            strong_candidate,
            settings_state=settings_state,
            now=now,
        )

        self.assertLessEqual(overlay["priority_adjustment"], 4.0)
        self.assertGreaterEqual(overlay["priority_adjustment"], -8.0)
        self.assertFalse(overlay["can_submit_orders"])
        self.assertFalse(overlay["can_submit_live_orders"])
        self.assertFalse(overlay["counts_toward_live_million"])
        self.assertIn("scenario_probability", overlay)

        blocked_overlay = trade_automation_service._market_possibility_engine_overlay(
            {**strong_candidate, "blocker": "kill_switch_active"},
            settings_state=settings_state,
            now=now,
        )

        self.assertLessEqual(blocked_overlay["priority_adjustment"], 0.0)
        self.assertEqual(blocked_overlay["usage_status"], "hard_gate_observed_no_uprank")
        self.assertTrue(blocked_overlay["hard_gates_remain_authoritative"])

    def test_evidence_accelerator_separates_live_and_simulated_evidence(self) -> None:
        now = datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
        diagnostics = {
            "evaluated_at": now.isoformat(),
            "summary": {"scanned_count": 1, "eligible_count": 1, "top_blocker": None},
            "candidates": [
                {
                    "candidate_lifecycle_id": "cand_spy",
                    "ticker": "SPY",
                    "status": "eligible",
                    "stage": "deep_analyzed",
                    "quote_age_seconds": 5,
                    "quote_freshness": {"status": "fresh", "age_seconds": 5},
                    "opportunity_capture": {"type": "vwap_reclaim", "score": 86},
                    "ai_evidence_review": {"verdict": "approve_evidence", "confidence": 0.82},
                    "routeability": {"candidate_routeable": True, "execution_route": "broker_paper"},
                    "market_possibility_engine": {
                        "model_version": "market_possibility_engine_v1",
                        "scenario_count": 250,
                        "scenario_probability": 0.72,
                        "expected_move_pct": 1.1,
                        "expected_value_score": 77,
                        "downside_probability": 0.28,
                        "scenario_rank": 28,
                    },
                }
            ],
        }

        accelerator = trade_automation_service._market_ops_evidence_accelerator_snapshot(
            tenant_slug="candidate-diagnostics",
            candidate_diagnostics=diagnostics,
            now=now,
            persist=False,
        )
        simulation = trade_automation_service._market_ops_simulation_evidence_snapshot(
            tenant_slug="candidate-diagnostics",
            candidate_diagnostics=diagnostics,
            now=now,
            persist=False,
        )

        self.assertGreater(accelerator["current_useful_event_count"], 0)
        self.assertTrue(accelerator["counts_toward_live_million"])
        self.assertEqual(simulation["current_simulation_event_count"], 1)
        self.assertFalse(simulation["counts_toward_live_million"])
        self.assertFalse(simulation["can_submit_orders"])

    def test_evidence_accelerator_aggressive_mode_expands_live_dimensions(self) -> None:
        now = datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
        diagnostics = {
            "evaluated_at": now.isoformat(),
            "summary": {"scanned_count": 1, "eligible_count": 1, "top_blocker": None},
            "candidates": [
                {
                    "candidate_lifecycle_id": "cand_aggressive_spy",
                    "ticker": "SPY",
                    "status": "eligible",
                    "stage": "rapid_confirmed",
                    "ranking_score": 91.0,
                    "quote_age_seconds": 2,
                    "quote_freshness": {"status": "fresh", "age_seconds": 2},
                    "spread_bps": 4.5,
                    "average_dollar_volume": 25_000_000,
                    "opportunity_capture": {
                        "type": "vwap_reclaim",
                        "score": 92,
                        "time_window": "active_session",
                        "trigger_price": 501.25,
                        "invalid_if": "below_vwap",
                        "rapid_confirmed": True,
                    },
                    "ai_evidence_review": {"verdict": "approve_evidence", "confidence": 0.82},
                    "routeability": {"candidate_routeable": True, "execution_route": "broker_paper"},
                    "market_possibility_engine": {"scenario_probability": 0.73, "priority_adjustment": 3.0},
                    "adaptive_execution_intelligence": {
                        "adaptive_execution_score": 74,
                        "slippage_risk_score": 8,
                        "reward_risk_fit_score": 80,
                    },
                }
            ],
        }

        accelerator = trade_automation_service._market_ops_evidence_accelerator_snapshot(
            tenant_slug="candidate-diagnostics",
            candidate_diagnostics=diagnostics,
            now=now,
            persist=False,
        )

        self.assertEqual(accelerator["mode"], "aggressive_guarded")
        self.assertEqual(accelerator["configured_max_events_per_minute"], 5000)
        self.assertGreaterEqual(accelerator["attempted_event_count"], 75)
        self.assertFalse(accelerator["backoff_active"])
        self.assertFalse(accelerator["can_submit_orders"])
        self.assertFalse(accelerator["can_submit_live_orders"])
        dimensions = {row["dimension"] for row in accelerator["latest_rows"]}
        self.assertIn("score_bucket", dimensions)
        self.assertIn("spread_quality", dimensions)

    def test_evidence_accelerator_backs_off_when_stale_ratio_is_high(self) -> None:
        now = datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
        candidates = [
            {
                "candidate_lifecycle_id": f"cand_stale_{idx}",
                "ticker": f"T{idx}",
                "status": "blocked",
                "stage": "rapid_confirmed",
                "blocker": "stale_quote",
                "quote_age_seconds": 999,
                "quote_freshness": {"status": "stale", "age_seconds": 999},
                "opportunity_capture": {"type": "vwap_reclaim", "score": 70},
                "routeability": {"candidate_routeable": False, "execution_route": "broker_paper"},
            }
            for idx in range(60)
        ]
        diagnostics = {
            "evaluated_at": now.isoformat(),
            "summary": {"scanned_count": len(candidates), "eligible_count": 0, "top_blocker": "stale_quote"},
            "candidates": candidates,
        }

        accelerator = trade_automation_service._market_ops_evidence_accelerator_snapshot(
            tenant_slug="candidate-diagnostics",
            candidate_diagnostics=diagnostics,
            now=now,
            persist=False,
        )

        self.assertTrue(accelerator["backoff_active"])
        self.assertIn("stale_ratio_guard", accelerator["backoff_reasons"])
        self.assertEqual(accelerator["effective_max_events_per_minute"], 1500)
        self.assertGreater(accelerator["rate_limited_count"], 0)
        self.assertEqual(accelerator["current_useful_event_count"], 0)

    def test_evidence_accelerator_counts_active_desk_symbol_coverage(self) -> None:
        now = datetime(2026, 4, 29, 16, 0, tzinfo=timezone.utc)
        diagnostics = {
            "evaluated_at": now.isoformat(),
            "summary": {"scanned_count": 0, "eligible_count": 0, "top_blocker": None},
            "candidates": [],
        }
        desks_payload = {
            "active_desks": [
                {
                    "desk_key": "fast_scalper",
                    "execution_status": "active",
                    "routeable": True,
                    "can_submit_orders": True,
                    "paper_execution_route": "broker_paper",
                    "provider": "alpaca",
                    "allowed_tickers": ["SPY", "QQQ", "MSFT"],
                    "cadence": {"cycle_interval_seconds": 30},
                    "risk_budget": {"max_positions": 3},
                    "runtime": {
                        "last_scan_at": (now - timedelta(seconds=20)).isoformat(),
                        "next_scan_at": (now + timedelta(seconds=10)).isoformat(),
                        "scanned_count": 3,
                        "eligible_count": 1,
                        "top_blocker": "weak_opportunity_score",
                    },
                }
            ]
        }

        accelerator = trade_automation_service._market_ops_evidence_accelerator_snapshot(
            tenant_slug="candidate-diagnostics",
            candidate_diagnostics=diagnostics,
            desks_payload=desks_payload,
            now=now,
            persist=False,
        )

        self.assertEqual(accelerator["candidate_count"], 0)
        self.assertEqual(accelerator["attempted_event_count"], 12)
        self.assertEqual(accelerator["current_useful_event_count"], 12)
        dimensions = {row["dimension"] for row in accelerator["latest_rows"]}
        self.assertIn("desk_symbol_scan_state", dimensions)
        self.assertIn("desk_symbol_route_state", dimensions)
        self.assertFalse(accelerator["backoff_active"])

    def test_liquid_45_universe_metadata_is_reported(self) -> None:
        tickers = list(trade_automation_service._AUTOMATION_LIQUID_45_TICKERS)
        self._seed_state(tickers)
        deep_requests: list[list[str]] = []

        def deep_collect(settings_state, deep_scan_tickers, **kwargs):
            requested = [str(item).strip().upper() for item in deep_scan_tickers]
            deep_requests.append(requested)
            rows_by_ticker = {
                ticker: {
                    "ticker": ticker,
                    "trade_decision": "VALID TRADE",
                    "verdict": "BULLISH",
                    "ranking_score": 91.0 - index,
                    "execution_score": 80.0,
                    "portfolio_score": 85.0,
                    "expected_edge_bps": 90.0,
                    "spread_bps": 8.0,
                    "average_dollar_volume": 10_000_000.0,
                    "live_price": 100.0 + index,
                }
                for index, ticker in enumerate(requested)
            }
            return {
                "rows_by_ticker": rows_by_ticker,
                "status_by_ticker": {
                    ticker: {"status": "deep_analysis_ready", "cache_fresh": True}
                    for ticker in requested
                },
                "summary": {
                    "status": "deep_analysis_ready",
                    "cache_fresh": True,
                    "ready_count": len(requested),
                    "pending_count": 0,
                    "failed_count": 0,
                    "requested_count": len(requested),
                    "inflight_count": 0,
                    "last_completed_at": trade_automation_service._serialize_datetime(
                        trade_automation_service._utc_now()
                    ),
                    "last_error": None,
                    "next_action": "Fresh deep-analysis rows are available.",
                },
            }

        with (
            patch.object(
                trade_automation_service.sdm,
                "batch_download_ohlcv",
                return_value=self._download_payload_for_tickers(tickers),
            ),
            patch.object(
                trade_automation_service,
                "_collect_trade_automation_deep_analysis",
                side_effect=deep_collect,
            ),
            patch.object(trade_automation_service, "_download_stage_one_live_prices", return_value={}),
            patch.object(
                trade_automation_service,
                "_build_session_snapshot",
                return_value=self._regular_session_snapshot(),
            ),
            patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(
                trade_automation_service,
                "_resolve_trade_automation_profile_account_context",
                return_value={
                    "current_user": self.current_user,
                    "linked_account": None,
                    "effective_funds": 120000.0,
                    "actual_funds": 120000.0,
                    "execution_intent": "broker_paper",
                    "account_summary": {
                        "equity": 120000.0,
                        "portfolio_value": 120000.0,
                        "buying_power": 400000.0,
                    },
                },
            ),
        ):
            diagnostics = trade_automation_service.get_tenant_trade_automation_candidate_diagnostics(
                self.db,
                current_user=self.current_user,
                scope="personal_paper",
                refresh=True,
            )

        expected_scanned_count = 45 + len(trade_automation_service._AGAINST_MARKET_PROXY_TICKERS)
        self.assertEqual(diagnostics["universe"]["ticker_count"], 45)
        self.assertEqual(diagnostics["universe"]["stage"], "stage_1_liquid_45")
        self.assertEqual(diagnostics["universe"]["scanned_count"], expected_scanned_count)
        self.assertIn("SH", diagnostics["universe"]["against_market_proxy_universe"])
        self.assertEqual(diagnostics["universe"]["scan_mode"], "fast_two_stage_full_universe")
        self.assertEqual(diagnostics["summary"]["stage_one_count"], expected_scanned_count)
        self.assertGreaterEqual(diagnostics["summary"]["deep_analyzed_count"], 8)
        self.assertIn("opportunity_capture", diagnostics["summary"])
        self.assertIn("ai_evidence_review", diagnostics["summary"])
        self.assertIn("quant_evidence_control_plane", diagnostics["summary"])
        self.assertIn("desk_capital_allocator", {item["key"] for item in diagnostics["summary"]["quant_evidence_control_plane"]["evidence_pillars"]})
        self.assertEqual(len(deep_requests), 1)
        self.assertGreaterEqual(len(deep_requests[0]), 8)
        self.assertLessEqual(len(deep_requests[0]), 20)
        self.assertEqual(len(diagnostics["candidates"]), expected_scanned_count)
        stages = {item["stage"] for item in diagnostics["candidates"]}
        self.assertIn("stage_one_only", stages)
        self.assertIn("deep_analyzed", stages)
        self.assertEqual(
            diagnostics["summary"]["rejected_by_stage_counts"]["stage_one_only"],
            expected_scanned_count - diagnostics["summary"]["deep_analyzed_count"],
        )
        self.assertIn("opportunity_capture", diagnostics["candidates"][0])
        self.assertIn("ai_evidence_review", diagnostics["candidates"][0])
        self.assertIn("candidate_lifecycle_id", diagnostics["candidates"][0])
        self.assertIn("routeability", diagnostics["candidates"][0])
        self.assertIn("market_possibility_engine", diagnostics["candidates"][0])
        self.assertIn("market_possibility_engine_context", diagnostics["summary"])
        self.assertIn("timings", diagnostics["summary"])
        self.assertIn("daily_export", diagnostics["summary"])
        self.assertIn("endpoint", diagnostics["summary"]["daily_export"])
        self.assertEqual(diagnostics["sizing"]["dynamic_max_notional_per_trade"], 12000.0)
        self.assertEqual(diagnostics["sizing"]["dynamic_max_total_open_notional"], 60000.0)

    def test_opportunity_capture_promotes_rank_16_breakout_into_priority_deep_scan(self) -> None:
        tickers = [f"T{index:02d}" for index in range(45)]
        tickers[15] = "SPY"
        stage_rows = []
        for index, ticker in enumerate(tickers, start=1):
            opportunity_score = 91.0 if ticker == "SPY" else 40.0
            stage_rows.append(
                {
                    "ticker": ticker,
                    "stage": "stage_one_only",
                    "scan_stage": "stage_one_only",
                    "stage_one_score": 100.0 - index,
                    "stage_one_priority_score": max(100.0 - index, opportunity_score),
                    "stage_one_rank": index,
                    "opportunity_score": opportunity_score,
                    "opportunity_type": "opening_range_break" if ticker == "SPY" else "none",
                    "priority_deep_scan": ticker == "SPY",
                    "diagnostic_blocker": "opportunity_needs_confirmation"
                    if ticker == "SPY"
                    else "opportunity_score_below_threshold",
                    "trade_decision": "STAGE ONE ONLY",
                    "ranking_tier": "stand_down",
                    "verdict": "NEUTRAL",
                }
            )

        deep_requests: list[list[str]] = []

        def deep_watchlist(request):
            requested = [str(item).strip().upper() for item in request.tickers]
            deep_requests.append(requested)
            return {
                "rows": [
                    {
                        "ticker": ticker,
                        "trade_decision": "VALID TRADE",
                        "verdict": "BULLISH",
                        "ranking_score": 80.0,
                        "execution_score": 75.0,
                        "portfolio_score": 80.0,
                        "expected_edge_bps": 80.0,
                        "spread_bps": 8.0,
                        "average_dollar_volume": 10_000_000.0,
                    }
                    for ticker in requested
                ]
            }

        settings_state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "tickers": tickers,
                    "execution_intent": "broker_paper",
                    "interval": "5m",
                    "horizon": 5,
                    "deep_scan_limit": 8,
                    "priority_deep_scan_limit": 12,
                    "opportunity_capture_enabled": True,
                    "min_opportunity_score": 72.0,
                    "rapid_confirmation_enabled": True,
                }
            }
        )["settings"]
        settings_state["async_deep_scan"] = False

        with (
            patch.object(
                trade_automation_service,
                "_build_stage_one_scan_rows",
                return_value=(
                    stage_rows,
                    {
                        "stage_one_ms": 2.0,
                        "stage_one_count": 45,
                        "configured_ticker_count": 45,
                        "period": "5d",
                        "interval": "5m",
                        "stage_one_error": None,
                    },
                ),
            ),
            patch.object(trade_automation_service, "build_watchlist", side_effect=deep_watchlist),
        ):
            watchlist = trade_automation_service._build_fast_two_stage_watchlist(
                {"settings": settings_state, "runtime": {}},
                settings_state,
            )

        self.assertEqual(len(deep_requests), 1)
        self.assertIn("SPY", deep_requests[0])
        self.assertGreater(deep_requests[0].index("SPY"), 7)
        self.assertIn("SPY", watchlist["automation_scan"]["priority_deep_scan_tickers"])

    def test_opportunity_capture_catches_intraday_opening_breakout_after_volume_fades(self) -> None:
        index = pd.date_range(
            datetime(2026, 4, 29, 9, 30, tzinfo=trade_automation_service._MARKET_TIMEZONE),
            periods=151,
            freq="1min",
        )
        closes: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        volumes: list[int] = []
        for offset, _timestamp in enumerate(index):
            if offset < 16:
                close = 100.0 + min(offset, 15) * 0.004
                volume = 30_000
            elif offset < 30:
                close = 100.20 + (offset - 16) * 0.09
                volume = 90_000 if offset == 20 else 54_000
            else:
                close = 101.55 + min(offset - 30, 100) * 0.012
                volume = 13_500
            closes.append(close)
            highs.append(close + 0.05)
            lows.append(close - 0.05)
            volumes.append(volume)
        frame = pd.DataFrame(
            {"Open": closes, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
            index=index,
        )
        settings_state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "opportunity_capture_enabled": True,
                    "rapid_confirmation_enabled": True,
                    "min_opportunity_score": 72.0,
                    "min_breakout_relative_volume": 1.4,
                    "max_spread_bps_for_opportunity": 35.0,
                    "min_average_dollar_volume": 1_000_000.0,
                }
            }
        )["settings"]

        row = trade_automation_service._build_stage_one_row(
            "QCOM",
            frame,
            settings_state=settings_state,
            interval="1m",
            now=datetime(2026, 4, 29, 12, 0, tzinfo=trade_automation_service._MARKET_TIMEZONE),
        )

        self.assertEqual(row["opportunity_type"], "opening_range_break")
        self.assertGreaterEqual(row["opportunity_score"], 72.0)
        self.assertTrue(row["priority_deep_scan"])
        self.assertTrue(row["rapid_confirmed"])
        self.assertEqual(row["trade_decision"], "VALID TRADE")
        self.assertIsNotNone(row["invalidation_price"])
        self.assertLess(row["invalidation_price"], row["live_price"])
        self.assertIn("Opening-range break triggered", row["confirmation_reason"])

    def test_rapid_confirmed_can_rank_but_raw_stage_one_cannot(self) -> None:
        state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "tickers": ["RAPID", "RAW"],
                    "execution_intent": "broker_paper",
                    "auto_trade_equities": True,
                    "auto_trade_listed_options": False,
                    "instrument_type": "equity",
                    "require_liquidity_fields": True,
                    "require_edge_fields": True,
                    "max_spread_bps": 35.0,
                    "min_average_dollar_volume": 1_000_000.0,
                    "min_edge_to_cost_ratio": 2.5,
                    "cycle_entry_rank_limit": 3,
                    "max_notional_per_trade": 10_000.0,
                }
            }
        )
        rows = [
            {
                "ticker": "RAPID",
                "stage": "rapid_confirmed",
                "trade_decision": "VALID TRADE",
                "ranking_tier": "opportunity_capture",
                "verdict": "BULLISH",
                "ranking_score": 88.0,
                "execution_score": 78.0,
                "portfolio_score": 82.0,
                "expected_edge_bps": 90.0,
                "spread_bps": 8.0,
                "edge_to_cost_ratio": 10.0,
                "average_dollar_volume": 25_000_000.0,
                "opportunity_score": 88.0,
                "relative_volume": 2.0,
                "rapid_confirmed": True,
                "auto_entry_eligible": True,
            },
            {
                "ticker": "RAW",
                "stage": "stage_one_only",
                "trade_decision": "STAGE ONE ONLY",
                "ranking_tier": "stand_down",
                "verdict": "BULLISH",
                "ranking_score": 99.0,
                "execution_score": 99.0,
                "portfolio_score": 99.0,
                "expected_edge_bps": 120.0,
                "spread_bps": 5.0,
                "edge_to_cost_ratio": 12.0,
                "average_dollar_volume": 25_000_000.0,
                "opportunity_score": 40.0,
            },
        ]

        ranked = trade_automation_service._rank_automation_candidates(
            state=state,
            rows=rows,
            now=trade_automation_service._utc_now(),
            owned_open=pd.DataFrame(),
            owned_pending=pd.DataFrame(),
            current_equity=100000.0,
        )

        self.assertEqual([candidate["ticker"] for candidate in ranked], ["RAPID"])
        self.assertTrue(ranked[0]["auto_entry_eligible"])
        self.assertEqual(ranked[0]["ai_evidence_review"]["verdict"], "approve_evidence")
        self.assertFalse(ranked[0]["ai_evidence_review"]["can_override_risk_gates"])

    def test_live_price_overlay_can_confirm_stale_opportunity_quote(self) -> None:
        settings_state = trade_automation_service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "opportunity_capture_enabled": True,
                    "rapid_confirmation_enabled": True,
                    "min_opportunity_score": 72.0,
                }
            }
        )["settings"]
        row = {
            "ticker": "AMD",
            "stage": "stage_one_only",
            "scan_stage": "stage_one_only",
            "trade_decision": "STAGE ONE ONLY",
            "ranking_tier": "stand_down",
            "verdict": "NEUTRAL",
            "ranking_score": 60.0,
            "opportunity_score": 91.0,
            "priority_deep_scan": True,
            "rejected_reason": "stale_quote",
            "spread_estimate_bps": 10.0,
            "diagnostic_blocker": "opportunity_needs_confirmation",
            "diagnostic_detail": "Stale historical bar.",
        }

        confirmed = trade_automation_service._apply_live_price_opportunity_confirmation(
            row,
            live_price=123.45,
            settings_state=settings_state,
            now=trade_automation_service._utc_now(),
        )

        self.assertEqual(confirmed["stage"], "rapid_confirmed")
        self.assertEqual(confirmed["trade_decision"], "VALID TRADE")
        self.assertTrue(confirmed["rapid_confirmed"])
        self.assertTrue(confirmed["auto_entry_eligible"])
        self.assertIsNone(confirmed["diagnostic_blocker"])

    def test_first_wave_proxy_workflow_refresh_returns_candidate_handoff_metadata(self) -> None:
        def proxy_frames(tickers, *args, **kwargs):
            return self._download_payload_for_tickers([str(ticker).upper() for ticker in tickers])

        with patch.object(trade_automation_service.sdm, "batch_download_ohlcv", side_effect=proxy_frames):
            diagnostics = trade_automation_service.get_tenant_trade_automation_desk_candidate_diagnostics(
                self.db,
                current_user=self.current_user,
                desk_key="equity_long_short",
                refresh=True,
            )

        self.assertEqual(diagnostics["source"], "catalog_proxy_workflow")
        self.assertEqual(diagnostics["execution_intent"], "broker_paper")
        self.assertEqual(diagnostics["summary"]["mutation"], "none")
        self.assertTrue(diagnostics["summary"]["proxy_workflow_operational"])
        self.assertTrue(diagnostics["summary"]["execution_blocked_for_catalog_desk"])
        self.assertTrue(diagnostics["summary"]["paper_routeable_via_existing_engine"])
        self.assertTrue(diagnostics["summary"]["requires_operator_review"])
        self.assertGreater(diagnostics["summary"]["scanned_count"], 0)
        self.assertEqual(diagnostics["summary"]["eligible_count"], 0)
        self.assertGreater(len(diagnostics["candidates"]), 0)
        candidate = diagnostics["candidates"][0]
        self.assertEqual(candidate["stage"], "proxy_workflow")
        self.assertIn(candidate["suggested_engine"], trade_automation_service._TRADE_AUTOMATION_ENGINE_KEYS)
        self.assertTrue(candidate["paper_routeable_via_existing_engine"])
        self.assertTrue(candidate["requires_operator_review"])
        self.assertTrue(candidate["execution_blocked_for_catalog_desk"])
        self.assertIn("component_breakdown", candidate)
        self.assertIn("data_freshness", candidate)
        self.assertIn("handoff_reason", candidate)
        self.assertIn("proxy_liquidity_score", candidate)
        self.assertIn("proxy_risk_model", candidate)
        self.assertIn("promotion_readiness", candidate)
        self.assertEqual(diagnostics["candidate_handoff"]["mutation"], "none")
        self.assertEqual(diagnostics["candidate_handoff"]["execution_route"], "broker_paper")
        self.assertIn("promotion_readiness_summary", diagnostics["summary"])
        self.assertIn("catalog_diagnostics_export", diagnostics["summary"])

    def test_all_first_wave_proxy_workflows_return_read_only_candidates(self) -> None:
        first_wave = {
            "equity_long_short",
            "etf_index",
            "relative_value",
            "sector_rotation",
            "volatility_options",
            "algorithmic_execution",
        }

        def proxy_frames(tickers, *args, **kwargs):
            return self._download_payload_for_tickers([str(ticker).upper() for ticker in tickers])

        with patch.object(trade_automation_service.sdm, "batch_download_ohlcv", side_effect=proxy_frames):
            for desk_key in sorted(first_wave):
                with self.subTest(desk_key=desk_key):
                    diagnostics = trade_automation_service.get_tenant_trade_automation_desk_candidate_diagnostics(
                        self.db,
                        current_user=self.current_user,
                        desk_key=desk_key,
                        refresh=True,
                    )
                    self.assertEqual(diagnostics["source"], "catalog_proxy_workflow")
                    self.assertEqual(diagnostics["summary"]["mutation"], "none")
                    self.assertFalse(diagnostics["summary"]["can_trade"])
                    self.assertGreater(diagnostics["summary"]["scanned_count"], 0)
                    self.assertEqual(diagnostics["summary"]["eligible_count"], 0)
                    self.assertGreater(len(diagnostics["candidates"]), 0)
                    self.assertTrue(all(item["suggested_engine"] in trade_automation_service._TRADE_AUTOMATION_ENGINE_KEYS for item in diagnostics["candidates"]))
                    self.assertTrue(all(item["execution_blocked_for_catalog_desk"] for item in diagnostics["candidates"]))
                    self.assertTrue(all(item["requires_operator_review"] for item in diagnostics["candidates"]))


if __name__ == "__main__":
    unittest.main()
