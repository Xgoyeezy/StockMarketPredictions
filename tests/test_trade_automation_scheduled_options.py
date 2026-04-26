from __future__ import annotations

from contextlib import ExitStack
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _resolved_permissions(*, membership_role: str = "owner", platform_role: str = "admin", mode: str = "demo") -> tuple[str, ...]:
    from backend.services.permissions import resolve_user_permissions

    return resolve_user_permissions(
        membership_role=membership_role,
        platform_role=platform_role,
        api_token_scopes=(),
        mode=mode,
    )


class TradeAutomationScheduledOptionsTests(unittest.TestCase):
    def setUp(self) -> None:
        import backend.models.saas  # noqa: F401
        from backend.core.database import Base
        from backend.services import tenant_service

        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                self.db,
                auth_subject="demo-trade-automation-user",
                email="demo-automation@example.test",
                name="Demo Automation User",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        self.tenant = identity["active_tenant"]
        self.current_user = SimpleNamespace(
            tenant_id=self.tenant.id,
            tenant_slug=self.tenant.slug,
            auth_subject="demo-trade-automation-user",
            user_id="demo-trade-automation-user",
            email="demo-automation@example.test",
            name="Demo Automation User",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
        )

    @staticmethod
    def _empty_frame() -> pd.DataFrame:
        return pd.DataFrame()

    @staticmethod
    def _finalize_stub(*args, **kwargs):
        from backend.services import trade_automation_service as service

        state = kwargs["state"]
        return {
            "runtime": service.serialize_value(state.get("runtime") or {}),
            "history": service.serialize_value(state.get("history") or []),
            "settings": service.serialize_value(state.get("settings") or {}),
        }

    @staticmethod
    def _profile_context(current_user):
        return {
            "current_user": current_user,
            "linked_account": None,
            "effective_funds": 100000.0,
            "actual_funds": 100000.0,
            "execution_intent": "broker_paper",
            "account_summary": {
                "equity": 100000.0,
                "cash": 100000.0,
                "portfolio_value": 100000.0,
                "buying_power": 100000.0,
            },
        }

    def test_profile_defaults_keep_scheduled_options_opt_in(self) -> None:
        from backend.services import trade_automation_service as service

        equity_state = service._normalize_trade_automation_profile_state(
            {"settings": {"instrument_type": "equity"}, "runtime": {}, "history": []}
        )
        option_state = service._normalize_trade_automation_profile_state(
            {"settings": {"instrument_type": "listed_option"}, "runtime": {}, "history": []}
        )

        self.assertTrue(equity_state["settings"]["auto_trade_equities"])
        self.assertFalse(equity_state["settings"]["auto_trade_listed_options"])
        self.assertFalse(equity_state["settings"]["regular_hours_only"])
        self.assertEqual(equity_state["settings"]["time_in_force"], "day_ext")
        self.assertFalse(option_state["settings"]["auto_trade_equities"])
        self.assertTrue(option_state["settings"]["auto_trade_listed_options"])
        self.assertTrue(option_state["settings"]["regular_hours_only"])
        self.assertEqual(option_state["settings"]["time_in_force"], "day")

    def _state(self):
        from backend.services import trade_automation_service as service

        return service._normalize_trade_automation_profile_state(
            {
                "settings": {
                    "enabled": True,
                    "armed": True,
                    "execution_intent": "broker_paper",
                    "tickers": ["SPY"],
                    "interval": "5m",
                    "horizon": 5,
                    "cycle_interval_seconds": 60,
                    "cooldown_minutes": 0,
                    "risk_percent": 0.5,
                    "auto_trade_equities": False,
                    "auto_trade_listed_options": True,
                    "order_type": "market",
                    "time_in_force": "day",
                    "regular_hours_only": True,
                    "auto_sync_orders": True,
                    "auto_manage_positions": True,
                    "auto_flatten_before_close": False,
                    "max_open_positions": 3,
                    "max_notional_per_trade": 2500.0,
                    "max_total_open_notional": 10000.0,
                    "max_daily_loss_r": 2.0,
                    "max_consecutive_losses": 3,
                    "max_daily_entries": 5,
                    "max_daily_entries_per_symbol": 2,
                    "max_error_streak": 3,
                    "long_only": True,
                    "equities_only": False,
                    "fractional_shares_only": False,
                    "use_fast_model": True,
                },
                "runtime": {},
                "history": [],
            }
        )

    def test_cycle_uses_scanned_option_contract_for_scheduled_entry(self) -> None:
        from backend.services import trade_automation_service as service

        state = self._state()
        scan_snapshot = {
            "status": "completed",
            "blocked_reason": None,
            "candidate_count": 1,
            "ready_candidate_count": 1,
            "candidates": [
                {
                    "underlying": "SPY",
                    "contract_symbol": "SPY260515C00500000",
                    "right": "call",
                    "expiration": "2026-05-15",
                    "strike": 500.0,
                    "bid": 1.2,
                    "ask": 1.3,
                    "mid": 1.25,
                    "entry_limit_price": 1.25,
                    "spread_pct": 0.08,
                    "volume": 250,
                    "open_interest": 1000,
                    "quote_timestamp": "2026-04-24T15:00:00+00:00",
                    "quote_age_seconds": 2.0,
                    "premium_notional": 130.0,
                    "ready_to_execute": True,
                    "selection_score": 0.1,
                    "source": "alpaca_options_chain",
                    "underlying_price": 500.0,
                }
            ],
        }
        watchlist = {
            "rows": [
                {
                    "ticker": "SPY",
                    "verdict": "BULLISH",
                    "route_family": "current",
                    "route_version": "ranked_entry_v1",
                    "live_price": 500.0,
                    "auto_entry_eligible": False,
                }
            ],
            "path_evaluations": [],
        }

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    service,
                    "_resolve_trade_automation_profile_account_context",
                    return_value=self._profile_context(self.current_user),
                )
            )
            stack.enter_context(patch.object(service, "get_trade_summary", return_value={"rollout_readiness": {}}))
            stack.enter_context(
                patch.object(service, "_build_collection_phase_state", return_value={"collection_phase_active": False})
            )
            stack.enter_context(patch.object(service, "_apply_collection_phase_runtime", return_value=None))
            stack.enter_context(
                patch.object(
                    service,
                    "_apply_collection_phase_controls",
                    side_effect=lambda settings_state, *_args, **_kwargs: settings_state,
                )
            )
            stack.enter_context(patch.object(service, "sync_pending_orders_from_broker", return_value=None))
            stack.enter_context(
                patch.object(
                    service,
                    "_build_session_snapshot",
                    return_value={
                        "regular_session": True,
                        "cleanup_window": False,
                        "phase": "regular_session",
                        "session_mode": "regular",
                        "extended_session": False,
                        "new_entries_allowed": True,
                        "minutes_to_close": 120,
                        "flatten_at": service._utc_now(),
                        "now_et": service._utc_now().astimezone(service._MARKET_TIMEZONE),
                    },
                )
            )
            stack.enter_context(patch.object(service.sdm, "read_open_trades", return_value=self._empty_frame()))
            stack.enter_context(patch.object(service.sdm, "read_pending_orders", return_value=self._empty_frame()))
            stack.enter_context(patch.object(service.sdm, "read_closed_trades", return_value=self._empty_frame()))
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "compute_current_equity",
                    return_value={"current_equity_estimate": 100000.0},
                )
            )
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "update_high_water_runtime",
                    return_value={"current_equity_estimate": 100000.0, "drawdown_pct": 0.0},
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_build_trade_automation_guardrail_snapshot",
                    return_value={"status": {"locked": False}, "entries_by_target": {}},
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_run_scheduled_options_refresh",
                    return_value={
                        "status": "blocked",
                        "reason": "No open long option paper positions are available for quote refresh.",
                        "refreshed_count": 0,
                        "sell_ready_count": 0,
                        "items": [],
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_manage_automation_positions",
                    return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []},
                )
            )
            stack.enter_context(patch.object(service, "_select_candidates_from_watchlist", return_value=([], watchlist)))
            stack.enter_context(patch.object(service, "_persist_watchlist_validation_snapshot", return_value=None))
            stack.enter_context(patch.object(service, "_run_scheduled_options_scan", return_value=scan_snapshot))
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "evaluate_candidate_risk_controls",
                    return_value=SimpleNamespace(allowed=True, reason="risk_controls_ok", detail="ok", metrics={}),
                )
            )
            open_trade_mock = stack.enter_context(
                patch.object(
                    service,
                    "open_trade_from_request",
                    return_value={
                        "position_opened": True,
                        "record": {"trade_id": "trade-option-1", "order_id": "order-option-1"},
                        "execution": {"status": "submitted"},
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    service.sdm,
                    "update_open_trade",
                    return_value={"trade_id": "trade-option-1", "order_id": "order-option-1"},
                )
            )
            stack.enter_context(patch.object(service, "_finalize_trade_automation_cycle", side_effect=self._finalize_stub))
            result = service._run_trade_automation_cycle(
                self.db,
                tenant=self.tenant,
                state=state,
                profile_key="personal_paper",
                forced=True,
                actor=self.current_user,
            )

        request = open_trade_mock.call_args.args[0]
        self.assertEqual(request.instrument_type, "listed_option")
        self.assertEqual(request.option_strategy, "long_option")
        self.assertEqual(request.order_type, "limit")
        self.assertEqual(request.limit_price, 1.25)
        self.assertEqual(request.contract_symbol, "SPY260515C00500000")
        self.assertEqual(request.execution_mode, "automated_entry")
        self.assertEqual(request.source, "options_automation_scheduled")
        self.assertEqual(result["runtime"]["last_options_scan_status"], "completed")
        self.assertEqual(result["runtime"]["last_option_entry"]["contract_symbol"], "SPY260515C00500000")
        self.assertIsNone(result["runtime"]["last_options_blocker"])

    def test_cycle_records_scan_blocker_without_submitting_option_order(self) -> None:
        from backend.services import trade_automation_service as service

        state = self._state()
        blocked_scan_snapshot = {
            "status": "blocked",
            "blocked_reason": "OPRA real-time options feed is required for automation.",
            "candidate_count": 0,
            "ready_candidate_count": 0,
            "candidates": [],
        }
        watchlist = {"rows": [{"ticker": "SPY", "verdict": "BULLISH"}], "path_evaluations": []}

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    service,
                    "_resolve_trade_automation_profile_account_context",
                    return_value=self._profile_context(self.current_user),
                )
            )
            stack.enter_context(patch.object(service, "get_trade_summary", return_value={"rollout_readiness": {}}))
            stack.enter_context(
                patch.object(service, "_build_collection_phase_state", return_value={"collection_phase_active": False})
            )
            stack.enter_context(patch.object(service, "_apply_collection_phase_runtime", return_value=None))
            stack.enter_context(
                patch.object(
                    service,
                    "_apply_collection_phase_controls",
                    side_effect=lambda settings_state, *_args, **_kwargs: settings_state,
                )
            )
            stack.enter_context(patch.object(service, "sync_pending_orders_from_broker", return_value=None))
            stack.enter_context(
                patch.object(
                    service,
                    "_build_session_snapshot",
                    return_value={
                        "regular_session": True,
                        "cleanup_window": False,
                        "phase": "regular_session",
                        "session_mode": "regular",
                        "extended_session": False,
                        "new_entries_allowed": True,
                        "minutes_to_close": 120,
                        "flatten_at": service._utc_now(),
                        "now_et": service._utc_now().astimezone(service._MARKET_TIMEZONE),
                    },
                )
            )
            stack.enter_context(patch.object(service.sdm, "read_open_trades", return_value=self._empty_frame()))
            stack.enter_context(patch.object(service.sdm, "read_pending_orders", return_value=self._empty_frame()))
            stack.enter_context(patch.object(service.sdm, "read_closed_trades", return_value=self._empty_frame()))
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "compute_current_equity",
                    return_value={"current_equity_estimate": 100000.0},
                )
            )
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "update_high_water_runtime",
                    return_value={"current_equity_estimate": 100000.0, "drawdown_pct": 0.0},
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_build_trade_automation_guardrail_snapshot",
                    return_value={"status": {"locked": False}, "entries_by_target": {}},
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_run_scheduled_options_refresh",
                    return_value={
                        "status": "blocked",
                        "reason": "No open long option paper positions are available for quote refresh.",
                        "refreshed_count": 0,
                        "sell_ready_count": 0,
                        "items": [],
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_manage_automation_positions",
                    return_value={"acted_count": 0, "failed_count": 0, "items": [], "failed_items": []},
                )
            )
            stack.enter_context(patch.object(service, "_select_candidates_from_watchlist", return_value=([], watchlist)))
            stack.enter_context(patch.object(service, "_persist_watchlist_validation_snapshot", return_value=None))
            stack.enter_context(patch.object(service, "_run_scheduled_options_scan", return_value=blocked_scan_snapshot))
            open_trade_mock = stack.enter_context(patch.object(service, "open_trade_from_request"))
            stack.enter_context(patch.object(service, "_finalize_trade_automation_cycle", side_effect=self._finalize_stub))
            result = service._run_trade_automation_cycle(
                self.db,
                tenant=self.tenant,
                state=state,
                profile_key="personal_paper",
                forced=True,
                actor=self.current_user,
            )

        open_trade_mock.assert_not_called()
        self.assertEqual(result["runtime"]["last_options_scan_status"], "blocked")
        self.assertEqual(result["runtime"]["last_options_blocker"], "OPRA real-time options feed is required for automation.")

    def test_cycle_updates_option_exit_runtime_from_position_management(self) -> None:
        from backend.services import trade_automation_service as service

        state = self._state()

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    service,
                    "_resolve_trade_automation_profile_account_context",
                    return_value=self._profile_context(self.current_user),
                )
            )
            stack.enter_context(patch.object(service, "get_trade_summary", return_value={"rollout_readiness": {}}))
            stack.enter_context(
                patch.object(service, "_build_collection_phase_state", return_value={"collection_phase_active": False})
            )
            stack.enter_context(patch.object(service, "_apply_collection_phase_runtime", return_value=None))
            stack.enter_context(
                patch.object(
                    service,
                    "_apply_collection_phase_controls",
                    side_effect=lambda settings_state, *_args, **_kwargs: settings_state,
                )
            )
            stack.enter_context(patch.object(service, "sync_pending_orders_from_broker", return_value=None))
            stack.enter_context(
                patch.object(
                    service,
                    "_build_session_snapshot",
                    return_value={
                        "regular_session": True,
                        "cleanup_window": False,
                        "phase": "regular_session",
                        "session_mode": "regular",
                        "extended_session": False,
                        "new_entries_allowed": True,
                        "minutes_to_close": 120,
                        "flatten_at": service._utc_now(),
                        "now_et": service._utc_now().astimezone(service._MARKET_TIMEZONE),
                    },
                )
            )
            stack.enter_context(patch.object(service.sdm, "read_open_trades", return_value=self._empty_frame()))
            stack.enter_context(patch.object(service.sdm, "read_pending_orders", return_value=self._empty_frame()))
            stack.enter_context(patch.object(service.sdm, "read_closed_trades", return_value=self._empty_frame()))
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "compute_current_equity",
                    return_value={"current_equity_estimate": 100000.0},
                )
            )
            stack.enter_context(
                patch.object(
                    service.risk_control_service,
                    "update_high_water_runtime",
                    return_value={"current_equity_estimate": 100000.0, "drawdown_pct": 0.0},
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_build_trade_automation_guardrail_snapshot",
                    return_value={"status": {"locked": False}, "entries_by_target": {}},
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_run_scheduled_options_refresh",
                    return_value={
                        "status": "completed",
                        "refreshed_count": 2,
                        "sell_ready_count": 1,
                        "items": [{"trade_id": "trade-option-1", "sell_ready": True}],
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    service,
                    "_manage_automation_positions",
                    return_value={
                        "acted_count": 1,
                        "failed_count": 0,
                        "items": [
                            {
                                "ticker": "SPY",
                                "trade_id": "trade-option-1",
                                "instrument_type": "listed_option",
                                "status": "closed",
                                "monitor_action": "EXIT FULLY NOW",
                                "option_execution": {"selected_contract": "SPY260515C00500000"},
                            }
                        ],
                        "failed_items": [],
                    },
                )
            )
            stack.enter_context(patch.object(service, "_finalize_trade_automation_cycle", side_effect=self._finalize_stub))
            result = service._run_trade_automation_cycle(
                self.db,
                tenant=self.tenant,
                state=state,
                profile_key="personal_paper",
                forced=True,
                actor=self.current_user,
            )

        self.assertEqual(result["runtime"]["open_option_position_count"], 2)
        self.assertEqual(result["runtime"]["sell_ready_option_count"], 1)
        self.assertEqual(result["runtime"]["last_option_exit"]["trade_id"], "trade-option-1")


if __name__ == "__main__":
    unittest.main()
