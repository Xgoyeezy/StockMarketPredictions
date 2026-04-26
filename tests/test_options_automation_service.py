from __future__ import annotations

import io
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

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


def _test_settings(**overrides):
    values = {
        "alpaca_options_feed": "opra",
        "options_scan_interval_seconds": 30,
        "options_quote_max_age_seconds": 30,
        "options_max_spread_pct": 0.15,
        "options_min_volume": 25,
        "options_min_open_interest": 100,
        "options_min_dte_days": 7,
        "options_max_dte_days": 45,
        "options_scan_candidate_limit": 30,
        "options_max_premium_risk_pct": 1.0,
        "options_max_open_positions": 4,
        "alpaca_api_key_id": "paper-key",
        "alpaca_api_secret_key": "paper-secret",
        "alpaca_use_sandbox": False,
        "alpaca_stock_feed": "iex",
        "alpaca_market_data_request_timeout_seconds": 10,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class OptionsAutomationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        import backend.models.saas  # noqa: F401
        from backend.core.database import Base
        from backend.services import tenant_service

        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.Session()
        self._export_tmpdir = TemporaryDirectory()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.db.close)
        self.addCleanup(self._export_tmpdir.cleanup)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="alpha-desk",
            demo_tenant_name="Alpha Desk",
            demo_tenant_plan="pro",
        )
        with patch.object(tenant_service, "settings", fake_settings):
            identity = tenant_service.ensure_demo_tenant_for_user(
                self.db,
                auth_subject="demo-options-user",
                email="demo-options@example.test",
                name="Demo Options User",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        self.tenant = identity["active_tenant"]
        self.current_user = SimpleNamespace(
            tenant_id=self.tenant.id,
            tenant_slug=self.tenant.slug,
            auth_subject="demo-options-user",
            user_id="demo-options-user",
            email="demo-options@example.test",
            name="Demo Options User",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
        )

        from backend.services import options_validation_service

        self.export_dir = Path(self._export_tmpdir.name)
        self._export_patcher = patch.object(options_validation_service, "OPTIONS_VALIDATION_EXPORTS_DIR", self.export_dir)
        self._export_patcher.start()
        self.addCleanup(self._export_patcher.stop)

    @staticmethod
    def _open_option_row(**overrides):
        row = {
            "trade_id": "trade-option-1",
            "order_id": "order-option-1",
            "opened_at": "2026-04-24T15:00:00+00:00",
            "ticker": "SPY",
            "instrument_type": "listed_option",
            "option_right": "call",
            "direction": "CALL",
            "contract_symbol": "SPY260515C00500000",
            "contract_expiration": "2026-05-15",
            "contract_strike": 500.0,
            "contract_mid_at_open": 1.25,
            "position_cost": 250.0,
            "suggested_contracts": 2.0,
            "filled_contracts": 2.0,
            "live_price_at_open": 500.0,
            "broker_name": "alpaca_paper",
            "automation_execution_intent": "broker_paper",
        }
        row.update(overrides)
        return row

    def _record_scheduled_lifecycle(self, cycle_index: int, *, sync_trigger: str = "scheduled") -> dict:
        from backend.services import options_automation_service as service

        trade_id = f"trade-option-{cycle_index}"
        order_id = f"order-option-{cycle_index}"
        entry_broker_order_id = f"broker-entry-{cycle_index}"
        close_broker_order_id = f"broker-close-{cycle_index}"
        contract_symbol = f"SPY260515C0050{cycle_index:03d}00"
        closed_row = self._open_option_row(
            trade_id=trade_id,
            order_id=order_id,
            contract_symbol=contract_symbol,
            broker_order_id=entry_broker_order_id,
            broker_close_order_id=close_broker_order_id,
            broker_close_status="filled",
            closed_at=f"2026-04-24T15:{10 + cycle_index:02d}:00+00:00",
        )
        self.db.add(
            service.OrderEventRecord(
                tenant_id=self.tenant.id,
                trade_id=trade_id,
                ticker="SPY",
                event_key="order.filled",
                status="filled",
                detail="Paper option order filled.",
                payload_json={"order_id": order_id, "broker_order_id": entry_broker_order_id},
            )
        )
        self.db.add(
            service.OrderEventRecord(
                tenant_id=self.tenant.id,
                trade_id=trade_id,
                ticker="SPY",
                event_key="order.closed",
                status="filled",
                detail="Paper option close filled.",
                payload_json={"order_id": order_id, "broker_order_id": close_broker_order_id},
            )
        )
        service.record_domain_event(
            self.db,
            tenant_id=self.tenant.id,
            event_type="options.paper_entry_submitted",
            aggregate_type="option_position",
            aggregate_id=trade_id,
            payload={
                "trade_id": trade_id,
                "order_id": order_id,
                "broker_order_id": entry_broker_order_id,
                "ticker": "SPY",
                "contract_symbol": contract_symbol,
                "automation_trigger": "scheduled",
            },
            metadata={"automation_trigger": "scheduled"},
        )
        service.record_domain_event(
            self.db,
            tenant_id=self.tenant.id,
            event_type="options.position_quote_refreshed",
            aggregate_type="option_position",
            aggregate_id=trade_id,
            payload={
                "trade_id": trade_id,
                "contract_symbol": contract_symbol,
                "ticker": "SPY",
                "sell_ready": True,
                "refreshed_at": f"2026-04-24T15:{11 + cycle_index:02d}:00+00:00",
                "quote_timestamp": f"2026-04-24T15:{11 + cycle_index:02d}:00+00:00",
                "automation_trigger": "scheduled",
            },
            metadata={"automation_trigger": "scheduled"},
        )
        service.record_domain_event(
            self.db,
            tenant_id=self.tenant.id,
            event_type="options.paper_exit_submitted",
            aggregate_type="option_position",
            aggregate_id=trade_id,
            payload={
                "trade_id": trade_id,
                "order_id": order_id,
                "broker_close_order_id": close_broker_order_id,
                "ticker": "SPY",
                "contract_symbol": contract_symbol,
                "automation_trigger": "scheduled",
            },
            metadata={"automation_trigger": "scheduled"},
        )
        service.record_domain_event(
            self.db,
            tenant_id=self.tenant.id,
            event_type="options.lifecycle_synced",
            aggregate_type="options_automation",
            aggregate_id=None,
            payload={
                "automation_trigger": sync_trigger,
                "synced_item_count": 1,
            },
            metadata={"automation_trigger": sync_trigger},
        )
        return closed_row

    def test_candidate_blocks_stale_wide_and_low_liquidity_quote(self) -> None:
        from backend.services import options_automation_service as service

        scan_started_at = datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc)
        row = {
            "symbol": "SPY260515C00500000",
            "details": {"contract_type": "call", "expiration_date": "2026-05-15", "strike_price": 500},
            "latest_quote": {
                "bid_price": 1.0,
                "ask_price": 1.5,
                "timestamp": (scan_started_at - timedelta(seconds=120)).isoformat(),
            },
            "latest_trade": {"price": 1.2},
            "day": {"volume": 1},
            "open_interest": 2,
        }

        with patch.object(service, "settings", _test_settings()):
            candidate = service._extract_contract_candidate(
                ticker="SPY",
                row=row,
                underlying_price=500,
                required_right="call",
                feed="opra",
                scan_started_at=scan_started_at,
            )

        self.assertIsNotNone(candidate)
        self.assertFalse(candidate["ready_to_execute"])
        reasons = " ".join(candidate["rejection_reasons"])
        self.assertIn("stale", reasons)
        self.assertIn("wide", reasons)
        self.assertIn("volume", reasons)
        self.assertIn("open interest", reasons)

    def test_scan_persists_ready_candidates_and_runs_faster_than_trading_engine(self) -> None:
        from backend.services import options_automation_service as service

        ready_candidate = {
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
            "rejection_reasons": [],
            "selection_score": 0.1,
        }
        fake_client = SimpleNamespace(is_configured=True)

        with (
            patch.object(service, "settings", _test_settings(options_scan_interval_seconds=20)),
            patch.object(service, "_build_alpaca_options_client", return_value=fake_client),
            patch.object(service, "_automation_settings", return_value={"tickers": ["SPY"], "cycle_interval_seconds": 60}),
            patch.object(service, "_account_summary", return_value={"effective_funds": 100000.0, "funds_source": "equity"}),
            patch.object(service, "_fetch_candidates_for_ticker", return_value=([ready_candidate], [], {"signal_right": "call"})),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:00:05+00:00",
                        "last_option_entry": {"at": "2026-04-24T15:00:04+00:00"},
                        "last_option_exit": {"at": "2026-04-24T15:00:03+00:00"},
                        "last_options_blocker": None,
                    },
                },
            ),
            patch.object(service, "record_domain_event", return_value=None),
        ):
            snapshot = service.run_options_automation_scan(self.db, current_user=self.current_user)

        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["ready_candidate_count"], 1)
        self.assertEqual(snapshot["scan_interval_seconds"], 20)
        self.assertLessEqual(snapshot["scan_interval_seconds"], 60)
        self.assertEqual(snapshot["candidates"][0]["contract_symbol"], "SPY260515C00500000")
        self.assertEqual(snapshot["automation_profile_key"], "personal_paper")
        self.assertTrue(snapshot["automation_enabled"])
        self.assertTrue(snapshot["automation_armed"])
        self.assertEqual(snapshot["last_scheduled_cycle_at"], "2026-04-24T15:00:05+00:00")

    def test_scan_surfaces_explicit_opra_entitlement_blocker(self) -> None:
        from backend.services import options_automation_service as service

        fake_client = SimpleNamespace(is_configured=True)
        entitlement_error = HTTPError(
            url="https://data.alpaca.markets/v1beta1/options/snapshots/SPY?feed=opra&limit=1",
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"message":"subscription does not permit querying OPRA data"}'),
        )

        with (
            patch.object(service, "settings", _test_settings(options_scan_interval_seconds=20)),
            patch.object(service, "_build_alpaca_options_client", return_value=fake_client),
            patch.object(service, "_automation_settings", return_value={"tickers": ["SPY"], "cycle_interval_seconds": 60}),
            patch.object(service, "_account_summary", return_value={"effective_funds": 100000.0, "funds_source": "equity"}),
            patch.object(service, "_fetch_candidates_for_ticker", side_effect=entitlement_error),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:00:05+00:00",
                        "last_option_entry": None,
                        "last_option_exit": None,
                        "last_options_blocker": None,
                    },
                },
            ),
            patch.object(service, "record_domain_event", return_value=None),
        ):
            snapshot = service.run_options_automation_scan(self.db, current_user=self.current_user)

        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(
            snapshot["blocked_reason"],
            "Alpaca subscription does not permit querying OPRA data. Real-time OPRA is required for paper options automation.",
        )
        self.assertEqual(
            snapshot["summary"]["blockers_by_ticker"]["SPY"],
            ["Alpaca HTTP 403: subscription does not permit querying OPRA data"],
        )

    def test_scan_without_ready_contracts_keeps_opra_entitlement_ready(self) -> None:
        from backend.services import options_automation_service as service

        blocked_candidate = {
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
            "quote_age_seconds": 120.0,
            "premium_notional": 130.0,
            "ready_to_execute": False,
            "rejection_reasons": ["Option quote is stale."],
            "selection_score": 0.1,
        }
        fake_client = SimpleNamespace(is_configured=True)

        with (
            patch.object(service, "settings", _test_settings(options_scan_interval_seconds=20)),
            patch.object(service, "_build_alpaca_options_client", return_value=fake_client),
            patch.object(service, "_automation_settings", return_value={"tickers": ["SPY"], "cycle_interval_seconds": 60}),
            patch.object(service, "_account_summary", return_value={"effective_funds": 100000.0, "funds_source": "equity"}),
            patch.object(service, "_fetch_candidates_for_ticker", return_value=([blocked_candidate], [], {"signal_right": "call"})),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:00:05+00:00",
                        "last_option_entry": None,
                        "last_option_exit": None,
                        "last_options_blocker": None,
                    },
                },
            ),
            patch.object(service, "record_domain_event", return_value=None),
        ):
            snapshot = service.run_options_automation_scan(self.db, current_user=self.current_user)

        self.assertEqual(snapshot["status"], "blocked")
        self.assertEqual(
            snapshot["blocked_reason"],
            "No option contracts passed the current price, liquidity, entitlement, and risk gates.",
        )
        self.assertEqual(snapshot["lifecycle"]["opra_entitlement_status"], "ready")
        self.assertEqual(snapshot["readiness_state"], "collecting_lifecycle_evidence")
        self.assertEqual(
            snapshot["validation_artifact"]["next_step"],
            "Keep collecting unchanged until 5 clean scheduled paper-option lifecycles are recorded.",
        )

    def test_refresh_positions_persists_sell_ready_snapshot(self) -> None:
        from backend.services import options_automation_service as service

        open_row = self._open_option_row()
        refresh_response = {
            "allowed": True,
            "contract": {
                "bid": 1.40,
                "ask": 1.50,
                "mid": 1.45,
                "spread_pct": 0.068,
                "volume": 320,
                "open_interest": 600,
                "quote_timestamp": "2026-04-24T15:00:02+00:00",
            },
            "close_contract_mid": 1.45,
            "reason": None,
            "detail": "Fresh option quote available for sell-to-close.",
            "diagnostics": {
                "option_quote_age_seconds": 2.0,
                "option_spread_pct": 0.068,
                "bid": 1.40,
                "ask": 1.50,
                "mid": 1.45,
                "volume": 320,
                "open_interest": 600,
                "quote_source": "exact_chain",
            },
        }
        fake_client = SimpleNamespace(is_configured=True, get_latest_prices=lambda tickers: {"SPY": 505.0})

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame([open_row])),
            patch.object(service, "_refresh_option_quote_for_close", return_value=refresh_response),
            patch.object(service, "_build_alpaca_options_client", return_value=fake_client),
            patch.object(service, "_resolve_scan_interval", return_value=20),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:00:05+00:00",
                        "last_option_entry": {"at": "2026-04-24T15:00:04+00:00"},
                        "last_option_exit": {"at": "2026-04-24T15:00:03+00:00"},
                        "last_options_blocker": None,
                    },
                },
            ),
        ):
            refresh_snapshot = service.refresh_options_positions(self.db, current_user=self.current_user)
            persisted_snapshot = service.get_options_automation_snapshot(self.db, current_user=self.current_user)

        self.assertEqual(refresh_snapshot["status"], "completed")
        self.assertEqual(refresh_snapshot["sell_ready_count"], 1)
        self.assertEqual(refresh_snapshot["items"][0]["sell_limit_price"], 1.40)
        self.assertEqual(refresh_snapshot["items"][0]["current_underlying_price"], 505.0)
        self.assertEqual(persisted_snapshot["open_positions"][0]["sell_ready"], True)
        self.assertEqual(persisted_snapshot["open_positions"][0]["sell_limit_price"], 1.40)
        self.assertEqual(persisted_snapshot["latest_quote_refresh"]["event_type"], "options.position_quote_refreshed")
        self.assertEqual(persisted_snapshot["automation_profile_key"], "personal_paper")
        self.assertEqual(persisted_snapshot["last_scheduled_cycle_at"], "2026-04-24T15:00:05+00:00")
        self.assertEqual(persisted_snapshot["last_scheduled_entry_at"], "2026-04-24T15:00:04+00:00")
        self.assertEqual(persisted_snapshot["last_scheduled_exit_at"], "2026-04-24T15:00:03+00:00")

    def test_close_paper_blocks_stale_quote(self) -> None:
        from backend.services import options_automation_service as service
        from backend.services.exceptions import ValidationError

        open_row = self._open_option_row()
        refresh_response = {
            "allowed": False,
            "contract": {},
            "close_contract_mid": None,
            "reason": "stale_option_quote",
            "detail": "The selected option contract quote is stale or missing a quote timestamp.",
            "diagnostics": {
                "option_quote_age_seconds": 301.0,
                "option_spread_pct": None,
                "quote_source": "exact_chain",
            },
        }
        fake_client = SimpleNamespace(is_configured=True, get_latest_prices=lambda tickers: {"SPY": 500.0})

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame([open_row])),
            patch.object(service, "_refresh_option_quote_for_close", return_value=refresh_response),
            patch.object(service, "_build_alpaca_options_client", return_value=fake_client),
        ):
            with self.assertRaisesRegex(ValidationError, "stale"):
                service.close_options_paper(self.db, current_user=self.current_user)

        events = list(
            self.db.execute(
                service.select(service.DomainEventLog)
                .where(service.DomainEventLog.event_type == "options.paper_exit_blocked")
                .order_by(service.DomainEventLog.created_at.desc())
            ).scalars()
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload_json.get("sell_block_reason"), "stale_option_quote")

    def test_close_paper_uses_refreshed_bid_limit(self) -> None:
        from backend.services import options_automation_service as service

        open_row = self._open_option_row()
        refresh_response = {
            "allowed": True,
            "contract": {
                "bid": 1.80,
                "ask": 1.90,
                "mid": 1.85,
                "spread_pct": 0.054,
                "volume": 540,
                "open_interest": 900,
                "quote_timestamp": "2026-04-24T15:05:02+00:00",
            },
            "close_contract_mid": 1.85,
            "reason": None,
            "detail": "Fresh option quote available for sell-to-close.",
            "diagnostics": {
                "option_quote_age_seconds": 1.0,
                "option_spread_pct": 0.054,
                "bid": 1.80,
                "ask": 1.90,
                "mid": 1.85,
                "volume": 540,
                "open_interest": 900,
                "quote_source": "exact_chain",
            },
        }
        fake_client = SimpleNamespace(is_configured=True, get_latest_prices=lambda tickers: {"SPY": 506.0})

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame([open_row])),
            patch.object(service, "_refresh_option_quote_for_close", return_value=refresh_response),
            patch.object(service, "_build_alpaca_options_client", return_value=fake_client),
            patch.object(
                service,
                "close_trade_from_request",
                return_value={
                    "status": "submitted",
                    "execution": {"broker_order_id": "broker-close-1"},
                    "closed_trade_preview": {"trade_id": "trade-option-1"},
                },
            ) as close_trade_mock,
        ):
            result = service.close_options_paper(self.db, current_user=self.current_user)

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["close_limit_price"], 1.80)
        close_request = close_trade_mock.call_args.args[0]
        self.assertEqual(close_request.trade_index, 0)
        self.assertEqual(close_request.close_limit_price, 1.80)
        self.assertEqual(close_request.close_contract_mid, 1.85)

        events = list(
            self.db.execute(
                service.select(service.DomainEventLog)
                .where(service.DomainEventLog.event_type == "options.paper_exit_submitted")
                .order_by(service.DomainEventLog.created_at.desc())
            ).scalars()
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].payload_json.get("close_limit_price"), 1.80)

    def test_snapshot_collects_lifecycle_evidence_until_five_clean_cycles(self) -> None:
        from backend.services import options_automation_service as service

        closed_row = self._record_scheduled_lifecycle(1)
        self.db.commit()

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_closed_trades", return_value=pd.DataFrame([closed_row])),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:12:00+00:00",
                        "last_option_entry": {"at": "2026-04-24T15:09:00+00:00"},
                        "last_option_exit": {"at": "2026-04-24T15:11:00+00:00"},
                        "last_options_blocker": None,
                    },
                },
            ),
        ):
            snapshot = service.get_options_automation_snapshot(self.db, current_user=self.current_user)

        artifact = snapshot["validation_artifact"]
        self.assertEqual(snapshot["readiness_state"], "collecting_lifecycle_evidence")
        self.assertEqual(snapshot["readiness_label"], "collecting lifecycle evidence")
        self.assertEqual(artifact["required_clean_cycles"], 5)
        self.assertEqual(artifact["clean_cycle_count"], 1)
        self.assertEqual(artifact["clean_entry_count"], 1)
        self.assertEqual(artifact["clean_exit_count"], 1)
        self.assertEqual(artifact["blocked_entry_count"], 0)
        self.assertEqual(artifact["blocked_exit_count"], 0)
        self.assertIsNotNone(artifact["last_broker_sync_at"])
        self.assertIsNotNone(artifact["last_clean_lifecycle_at"])
        self.assertEqual(len(artifact["recent_clean_cycles"]), 1)

    def test_snapshot_marks_ready_after_five_clean_scheduled_cycles_and_exports(self) -> None:
        from backend.services import options_automation_service as service

        closed_rows = [self._record_scheduled_lifecycle(index) for index in range(1, 6)]
        self.db.commit()

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_closed_trades", return_value=pd.DataFrame(closed_rows)),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:20:00+00:00",
                        "last_option_entry": {"at": "2026-04-24T15:19:00+00:00"},
                        "last_option_exit": {"at": "2026-04-24T15:20:00+00:00"},
                        "last_options_blocker": None,
                    },
                },
            ),
        ):
            snapshot = service.get_options_automation_snapshot(self.db, current_user=self.current_user)

        artifact = snapshot["validation_artifact"]
        self.assertEqual(snapshot["readiness_state"], "ready")
        self.assertEqual(artifact["clean_cycle_count"], 5)
        self.assertEqual(artifact["required_clean_cycles"], 5)
        self.assertEqual(len(artifact["recent_clean_cycles"]), 5)
        summary_path = self.export_dir / "latest" / "summary.json"
        validation_path = self.export_dir / "latest" / "options_paper_validation.json"
        self.assertTrue(summary_path.exists())
        self.assertTrue(validation_path.exists())
        summary_payload = service.json.loads(summary_path.read_text(encoding="utf-8"))
        validation_payload = service.json.loads(validation_path.read_text(encoding="utf-8"))
        self.assertEqual(summary_payload["readiness_state"], "ready")
        self.assertEqual(summary_payload["clean_cycle_count"], 5)
        self.assertEqual(validation_payload["validation_artifact"]["readiness_state"], "ready")

    def test_manual_option_lifecycle_events_do_not_increment_clean_cycle_count(self) -> None:
        from backend.services import options_automation_service as service

        closed_row = self._record_scheduled_lifecycle(1, sync_trigger="manual")
        service.record_domain_event(
            self.db,
            tenant_id=self.tenant.id,
            event_type="options.paper_entry_submitted",
            aggregate_type="option_position",
            aggregate_id="manual-trade-1",
            payload={
                "trade_id": "manual-trade-1",
                "order_id": "manual-order-1",
                "broker_order_id": "manual-broker-1",
                "ticker": "SPY",
                "contract_symbol": "SPY260515C00599900",
                "automation_trigger": "manual",
            },
            metadata={"automation_trigger": "manual"},
        )
        self.db.commit()

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_closed_trades", return_value=pd.DataFrame([closed_row])),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:20:00+00:00",
                        "last_option_entry": {"at": "2026-04-24T15:19:00+00:00"},
                        "last_option_exit": {"at": "2026-04-24T15:20:00+00:00"},
                        "last_options_blocker": None,
                    },
                },
            ),
        ):
            snapshot = service.get_options_automation_snapshot(self.db, current_user=self.current_user)

        self.assertEqual(snapshot["validation_artifact"]["clean_cycle_count"], 1)

    def test_snapshot_blocks_when_sell_side_quote_evidence_is_not_ready(self) -> None:
        from backend.services import options_automation_service as service

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame([self._open_option_row()])),
            patch.object(service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_closed_trades", return_value=pd.DataFrame()),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:12:00+00:00",
                        "last_option_entry": {"at": "2026-04-24T15:09:00+00:00"},
                        "last_option_exit": None,
                        "last_options_blocker": None,
                    },
                },
            ),
            patch.object(
                service,
                "_latest_refresh_payloads",
                return_value={
                    "trade:trade-option-1": {
                        "trade_id": "trade-option-1",
                        "contract_symbol": "SPY260515C00500000",
                        "sell_ready": False,
                        "sell_block_reason": "stale_option_quote",
                        "sell_block_detail": "The selected option contract quote is stale or missing a quote timestamp.",
                    }
                },
            ),
        ):
            snapshot = service.get_options_automation_snapshot(self.db, current_user=self.current_user)

        self.assertEqual(snapshot["readiness_state"], "blocked")
        self.assertIn("stale", " ".join(snapshot["validation_artifact"]["blockers"]).lower())

    def test_snapshot_blocks_on_orphaned_scheduled_lifecycle_event_and_export_matches(self) -> None:
        from backend.services import options_automation_service as service

        service.record_domain_event(
            self.db,
            tenant_id=self.tenant.id,
            event_type="options.paper_entry_submitted",
            aggregate_type="option_position",
            aggregate_id="orphan-trade-1",
            payload={
                "trade_id": "orphan-trade-1",
                "order_id": "orphan-order-1",
                "ticker": "SPY",
                "contract_symbol": "SPY260515C00577700",
                "automation_trigger": "scheduled",
            },
            metadata={"automation_trigger": "scheduled"},
        )
        self.db.commit()

        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_closed_trades", return_value=pd.DataFrame()),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {"last_options_blocker": None},
                },
            ),
        ):
            snapshot = service.get_options_automation_snapshot(self.db, current_user=self.current_user)

        summary_payload = service.json.loads((self.export_dir / "latest" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["readiness_state"], "blocked")
        self.assertEqual(snapshot["validation_artifact"]["orphan_event_count"], 1)
        self.assertEqual(summary_payload["readiness_state"], "blocked")
        self.assertEqual(summary_payload["orphan_event_count"], 1)

    def test_sync_options_automation_records_sync_event(self) -> None:
        from backend.services import options_automation_service as service

        pending_row = self._open_option_row(order_id="order-option-pending-1", trade_id="trade-option-pending-1")
        with (
            patch.object(service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(service, "_scoped_pending_orders", return_value=pd.DataFrame([pending_row])),
            patch.object(service, "_scoped_closed_trades", return_value=pd.DataFrame()),
            patch.object(
                service,
                "get_tenant_trade_automation_snapshot",
                return_value={
                    "profile_key": "personal_paper",
                    "settings": {"enabled": True, "armed": True},
                    "runtime": {
                        "last_options_cycle_at": "2026-04-24T15:12:00+00:00",
                        "last_option_entry": None,
                        "last_option_exit": None,
                        "last_options_blocker": None,
                    },
                },
            ),
            patch.object(
                service,
                "sync_pending_orders_from_broker",
                return_value={"status": "completed", "items": [{"order_id": "order-option-pending-1"}]},
            ) as sync_mock,
        ):
            snapshot = service.sync_options_automation(self.db, current_user=self.current_user)

        sync_mock.assert_called_once()
        self.assertIsNotNone(snapshot["latest_broker_sync"])
        self.assertIsNotNone(snapshot["last_broker_sync_at"])
        self.assertEqual(snapshot["validation_artifact"]["validation_scope"], "personal_paper")


if __name__ == "__main__":
    unittest.main()
