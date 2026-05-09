from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from backend import stock_direction_model as sdm
from backend.schemas import CloseTradeRequest, OpenTradeRequest
from backend.services import portfolio_service, trade_automation_service, trade_service
from backend.services.execution.alpaca_paper_adapter import AlpacaPaperExecutionAdapter
from backend.services.execution.mappers import build_alpaca_option_order_payload
from backend.services.market_data.types import OptionChainSnapshot, OptionContractQuote


class RolloutSupportTests(unittest.TestCase):
    @staticmethod
    def _build_trade_row(
        *,
        ticker: str = "SPY",
        verdict: str = "BULLISH",
        instrument_type: str = "listed_option",
        contract_symbol: str = "SPY260417C00560000",
        entry_price: float = 100.0,
        invalidation_price: float = 95.0,
        opened_at: str = "2026-04-22T14:00:00+00:00",
        horizon_bars: int = 5,
        interval: str = "5m",
        tp1_taken_at: str = "",
        tp2_taken_at: str = "",
    ) -> dict[str, object]:
        return {
            "trade_id": f"trade-{ticker.lower()}-{instrument_type}",
            "order_id": f"order-{ticker.lower()}-{instrument_type}",
            "ticker": ticker,
            "interval": interval,
            "verdict": verdict,
            "instrument_type": instrument_type,
            "contract_symbol": contract_symbol,
            "contract_mid_at_open": 2.0 if instrument_type == "listed_option" else entry_price / 100.0,
            "suggested_contracts": 2.0,
            "opened_at": opened_at,
            "live_price_at_open": entry_price,
            "invalidation_price": invalidation_price,
            "horizon_bars": horizon_bars,
            "tp1_taken_at": tp1_taken_at,
            "tp2_taken_at": tp2_taken_at,
            "active_stop_price": float("nan"),
            "last_exit_reason": "",
            "exit_plan": sdm.build_exit_plan(
                verdict=verdict,
                entry_reference_price=entry_price,
                invalidation_price=invalidation_price,
                time_stop_bars=horizon_bars,
                entry_reference_source="test_fixture",
            ),
        }

    def test_build_alpaca_option_order_payload_uses_contract_symbol_and_integer_contracts(self) -> None:
        payload = build_alpaca_option_order_payload(
            OpenTradeRequest(
                ticker="SPY",
                instrument_type="listed_option",
                option_right="call",
                contract_symbol="SPY260417C00560000",
                order_type="limit",
                time_in_force="day",
                limit_price=2.45,
            ),
            contract_symbol="SPY260417C00560000",
            quantity=2,
            client_order_id="order-1",
        )

        self.assertEqual(payload["symbol"], "SPY260417C00560000")
        self.assertEqual(payload["qty"], "2")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(payload["type"], "limit")
        self.assertEqual(payload["time_in_force"], "day")
        self.assertEqual(payload["limit_price"], "2.45")
        self.assertEqual(payload["client_order_id"], "order-1")

    def test_adapter_build_submit_payload_supports_listed_options_without_asset_lookup(self) -> None:
        client = MagicMock()
        adapter = AlpacaPaperExecutionAdapter(client=client)

        payload, asset_metadata = adapter._build_submit_payload(
            request=OpenTradeRequest(
                ticker="SPY",
                instrument_type="listed_option",
                option_right="call",
                contract_symbol="SPY260417C00560000",
                order_type="market",
                time_in_force="day",
            ),
            position={"suggested_contracts": 3},
            side="buy",
        )

        self.assertEqual(payload["symbol"], "SPY260417C00560000")
        self.assertEqual(payload["qty"], "3")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(asset_metadata["class"], "option")
        self.assertFalse(asset_metadata["fractionable"])
        client.get_asset.assert_not_called()

    def test_adapter_close_position_supports_listed_options_with_contract_symbol(self) -> None:
        client = MagicMock()
        client.submit_order.return_value = {"id": "close-order-1", "status": "accepted"}
        adapter = AlpacaPaperExecutionAdapter(client=client)

        with (
            patch.object(
                sdm,
                "close_trade_by_index",
                return_value={
                    "ticker": "SPY",
                    "instrument_type": "listed_option",
                    "contract_symbol": "SPY260417C00560000",
                    "suggested_contracts": 2,
                },
            ),
            patch.object(adapter, "_ensure_credentials"),
            patch.object(adapter, "_assert_local_ledger_persisted"),
        ):
            result = adapter.close_position(
                request=CloseTradeRequest(
                    trade_index=0,
                    close_underlying_price=560.0,
                    close_contract_mid=2.75,
                    close_fraction=1.0,
                ),
                target_trade={
                    "ticker": "SPY",
                    "instrument_type": "listed_option",
                    "contract_symbol": "SPY260417C00560000",
                    "suggested_contracts": 2,
                },
            )

        payload = client.submit_order.call_args.args[0]
        self.assertEqual(payload["symbol"], "SPY260417C00560000")
        self.assertEqual(payload["qty"], "2")
        self.assertEqual(payload["side"], "sell")
        self.assertEqual(payload["type"], "limit")
        self.assertEqual(payload["limit_price"], "2.75")
        self.assertEqual(payload["time_in_force"], "day")
        self.assertEqual(result.broker_order_id, "close-order-1")

    def test_get_contract_quote_from_chain_returns_exact_real_contract(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        provider = SimpleNamespace(
            get_option_expirations=MagicMock(return_value=["2026-04-17"]),
            get_option_chain=MagicMock(
                return_value=OptionChainSnapshot(
                    expiration="2026-04-17",
                    calls=[
                        OptionContractQuote(
                            contract_symbol="SPY260417C00560000",
                            strike=560.0,
                            bid=2.4,
                            ask=2.5,
                            last_price=2.45,
                            implied_volatility=0.22,
                            volume=250,
                            open_interest=1000,
                            in_the_money=False,
                            quote_timestamp=now,
                        )
                    ],
                    puts=[],
                )
            ),
        )

        with patch.object(sdm, "get_market_data_provider", return_value=provider):
            contract = sdm.get_contract_quote_from_chain("SPY", "SPY260417C00560000", option_side="call")

        self.assertIsNotNone(contract)
        self.assertEqual(contract["contract_symbol"], "SPY260417C00560000")
        self.assertAlmostEqual(contract["mid"], 2.45)
        self.assertAlmostEqual(contract["spread_pct"], (2.5 - 2.4) / 2.45)

    def test_option_chain_scorer_prefers_liquid_contract_over_closer_illiquid_contract(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        provider = SimpleNamespace(
            get_option_expirations=MagicMock(return_value=["2026-05-15"]),
            get_option_chain=MagicMock(
                return_value=OptionChainSnapshot(
                    expiration="2026-05-15",
                    calls=[
                        OptionContractQuote(
                            contract_symbol="SPY260515C00500000",
                            strike=500.0,
                            bid=2.0,
                            ask=3.0,
                            last_price=2.5,
                            implied_volatility=0.3,
                            volume=1,
                            open_interest=5,
                            in_the_money=False,
                            quote_timestamp=now,
                        ),
                        OptionContractQuote(
                            contract_symbol="SPY260515C00505000",
                            strike=505.0,
                            bid=2.4,
                            ask=2.5,
                            last_price=2.45,
                            implied_volatility=0.22,
                            volume=250,
                            open_interest=1000,
                            in_the_money=False,
                            quote_timestamp=now,
                        ),
                    ],
                    puts=[],
                )
            ),
        )

        with patch.object(sdm, "get_market_data_provider", return_value=provider):
            contract = sdm.get_recommended_contract("SPY", "CALL", 500.0, "21-45 DTE")

        self.assertIsNotNone(contract)
        self.assertEqual(contract["contract_symbol"], "SPY260515C00505000")

    def test_automation_option_refresh_replaces_stale_selected_contract_with_fresh_equivalent(self) -> None:
        stale_contract = {
            "contract_symbol": "SPY260515C00500000",
            "expiration": "2026-05-15",
            "strike": 500.0,
            "bid": 2.4,
            "ask": 2.5,
            "mid": 2.45,
            "spread_pct": 0.04,
            "volume": 250,
            "open_interest": 1000,
            "quote_timestamp": (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=10)).isoformat(),
        }
        fresh_contract = {
            **stale_contract,
            "contract_symbol": "SPY260515C00505000",
            "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        }

        with (
            patch.object(sdm, "get_contract_quote_from_chain", return_value=stale_contract),
            patch.object(sdm, "get_recommended_contract", return_value=fresh_contract),
        ):
            refresh = trade_automation_service._refresh_automation_option_candidate(
                {
                    "ticker": "SPY",
                    "verdict": "BULLISH",
                    "option_right": "call",
                    "contract_symbol": "SPY260515C00500000",
                    "live_price": 500.0,
                    "days_to_expiration": "21-45 DTE",
                },
                option_right="call",
                now=datetime.now(timezone.utc),
            )

        self.assertTrue(refresh["allowed"])
        self.assertEqual(refresh["candidate"]["contract_symbol"], "SPY260515C00505000")
        self.assertEqual(refresh["diagnostics"]["option_scan_status"], "replaced")

    def test_automation_option_refresh_blocks_wide_or_stale_quotes(self) -> None:
        wide_contract = {
            "contract_symbol": "SPY260515C00500000",
            "expiration": "2026-05-15",
            "strike": 500.0,
            "bid": 2.0,
            "ask": 2.6,
            "mid": 2.3,
            "spread_pct": 0.26,
            "volume": 250,
            "open_interest": 1000,
            "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        }

        with (
            patch.object(sdm, "get_contract_quote_from_chain", return_value=wide_contract),
            patch.object(sdm, "get_recommended_contract", return_value=None),
        ):
            refresh = trade_automation_service._refresh_automation_option_candidate(
                {
                    "ticker": "SPY",
                    "verdict": "BULLISH",
                    "option_right": "call",
                    "contract_symbol": "SPY260515C00500000",
                    "live_price": 500.0,
                    "days_to_expiration": "21-45 DTE",
                },
                option_right="call",
                now=datetime.now(timezone.utc),
            )

        self.assertFalse(refresh["allowed"])
        self.assertEqual(refresh["reason"], "option_spread_too_wide")

    def test_option_close_refresh_uses_current_contract_quote(self) -> None:
        fresh_contract = {
            "contract_symbol": "SPY260515C00500000",
            "expiration": "2026-05-15",
            "strike": 500.0,
            "bid": 3.1,
            "ask": 3.3,
            "mid": 3.2,
            "spread_pct": 0.0625,
            "volume": 250,
            "open_interest": 1000,
            "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
        }

        with (
            patch.object(trade_automation_service, "settings", SimpleNamespace(options_data_provider="alpaca")),
            patch.object(sdm, "get_contract_quote_from_chain", return_value=fresh_contract),
        ):
            refresh = trade_automation_service._refresh_option_quote_for_close(
                {
                    "ticker": "SPY",
                    "instrument_type": "listed_option",
                    "option_right": "call",
                    "contract_symbol": "SPY260515C00500000",
                    "contract_mid_at_open": 2.0,
                },
                now=datetime.now(timezone.utc),
            )

        self.assertTrue(refresh["allowed"])
        self.assertEqual(refresh["close_contract_mid"], 3.2)
        self.assertEqual(refresh["diagnostics"]["selected_contract"], "SPY260515C00500000")

    def test_automation_order_fields_use_recommended_contract_mid_for_listed_option_limits(self) -> None:
        with patch.object(sdm, "get_contract_mid_from_symbol", return_value=2.65):
            fields = trade_automation_service._build_automation_order_fields(
                candidate={"contract_symbol": "SPY260417C00560000"},
                settings_state={
                    "order_type": "limit",
                    "time_in_force": "day",
                },
                instrument_type="listed_option",
            )

        self.assertEqual(fields["order_type"], "limit")
        self.assertEqual(fields["time_in_force"], "day")
        self.assertEqual(fields["limit_price"], 2.65)

    def test_automation_order_fields_normalize_equity_limit_price_for_alpaca(self) -> None:
        fields = trade_automation_service._build_automation_order_fields(
            candidate={"live_price": 280.635},
            settings_state={
                "order_type": "limit",
                "time_in_force": "day",
                "instrument_type": "equity",
            },
            instrument_type="equity",
        )

        self.assertEqual(fields["limit_price"], 280.64)

    def test_update_trade_automation_settings_allows_listed_options_and_normalizes_constraints(self) -> None:
        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk", metadata_json={})
        actor = SimpleNamespace(id="user-1", auth_subject="demo-trader")
        db = MagicMock()

        with (
            patch.object(trade_automation_service, "_assert_trade_automation_operator"),
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=actor),
            patch.object(trade_automation_service, "_write_trade_automation_state"),
            patch.object(trade_automation_service, "record_audit_event"),
            patch.object(
                trade_automation_service,
                "get_trade_summary",
                return_value={
                    "rollout_readiness": {
                        "ranked_entry_rollout": {
                            "accepted": True,
                            "status": "accepted",
                        },
                        "metrics": {
                            "current_route_fill_count": 12,
                            "current_route_closed_trade_count": 6,
                            "current_route_sample_status": "sufficient",
                            "mark_to_market_coverage_status": "complete",
                            "ledger_snapshot_consistency": "consistent",
                        },
                    }
                },
            ),
            patch.object(
                trade_automation_service,
                "_build_snapshot_payload",
                side_effect=lambda tenant, state, **kwargs: {"settings": dict(state["settings"])},
            ),
        ):
            payload = trade_automation_service.update_tenant_trade_automation_settings(
                db,
                current_user=SimpleNamespace(),
                updates={
                    "instrument_type": "listed_option",
                    "auto_trade_equities": True,
                    "auto_trade_listed_options": True,
                    "regular_hours_only": False,
                    "time_in_force": "day_ext",
                    "equities_only": True,
                    "fractional_shares_only": True,
                },
            )

        self.assertEqual(payload["settings"]["instrument_type"], "listed_option")
        self.assertTrue(payload["settings"]["auto_trade_equities"])
        self.assertTrue(payload["settings"]["auto_trade_listed_options"])
        self.assertTrue(payload["settings"]["regular_hours_only"])
        self.assertEqual(payload["settings"]["time_in_force"], "day")
        self.assertFalse(payload["settings"]["equities_only"])
        self.assertFalse(payload["settings"]["fractional_shares_only"])

    def test_update_trade_automation_settings_keeps_collection_phase_locked_to_paper(self) -> None:
        tenant = SimpleNamespace(id="tenant-1", slug="alpha-desk", metadata_json={})
        actor = SimpleNamespace(id="user-1", auth_subject="demo-trader")
        db = MagicMock()

        with (
            patch.object(trade_automation_service, "_assert_trade_automation_operator"),
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=actor),
            patch.object(trade_automation_service, "_write_trade_automation_state"),
            patch.object(trade_automation_service, "record_audit_event"),
            patch.object(
                trade_automation_service,
                "get_trade_summary",
                return_value={
                    "rollout_readiness": {
                        "ranked_entry_rollout": {
                            "accepted": False,
                            "status": "partial",
                        },
                        "metrics": {
                            "current_route_fill_count": 4,
                            "current_route_closed_trade_count": 1,
                            "current_route_sample_status": "insufficient",
                            "mark_to_market_coverage_status": "missing",
                            "ledger_snapshot_consistency": "unavailable",
                        },
                    }
                },
            ),
            patch.object(
                trade_automation_service,
                "_build_snapshot_payload",
                side_effect=lambda tenant, state, **kwargs: {
                    "settings": dict(state["settings"]),
                    "runtime": dict(state["runtime"]),
                    "rollout_readiness": dict(kwargs.get("rollout_readiness") or {}),
                },
            ),
        ):
            payload = trade_automation_service.update_tenant_trade_automation_settings(
                db,
                current_user=SimpleNamespace(),
                updates={
                    "execution_intent": "broker_live",
                    "regular_hours_only": False,
                    "time_in_force": "day_ext",
                },
        )

        self.assertEqual(payload["settings"]["execution_intent"], "broker_paper")
        self.assertFalse(payload["settings"]["regular_hours_only"])
        self.assertEqual(payload["settings"]["time_in_force"], "day_ext")
        self.assertEqual(payload["settings"]["order_type"], "limit")
        self.assertTrue(payload["runtime"]["collection_phase_active"])
        self.assertEqual(payload["runtime"]["collection_phase_label"], "Collecting sample")

    def test_validate_directional_entry_request_allows_long_puts_when_long_only(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_right="put",
            contract_symbol="SPY260417P00560000",
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
            long_only=True,
        )

        trade_service._validate_directional_entry_request(request, {"verdict": "BEARISH"})

    def test_alpaca_fill_slippage_falls_back_from_nan_limit_to_live_price(self) -> None:
        adapter = AlpacaPaperExecutionAdapter()

        expected, actual, slippage_dollars, slippage_bps = adapter._build_fill_slippage(
            {"limit_price": float("nan"), "live_price_at_submit": 418.6749877929688},
            {"filled_avg_price": 418.73},
        )

        self.assertAlmostEqual(expected or 0.0, 418.6749877929688, places=6)
        self.assertAlmostEqual(actual or 0.0, 418.73, places=6)
        self.assertIsNotNone(slippage_dollars)
        self.assertIsNotNone(slippage_bps)
        self.assertGreater(slippage_bps or 0.0, 0.0)

    def test_execution_review_derives_slippage_from_submit_and_broker_fill(self) -> None:
        row = pd.Series(
            {
                "broker_status": "filled",
                "live_price_at_submit": 418.6749877929688,
                "broker_filled_avg_price": 418.73,
            }
        )

        review = portfolio_service._build_execution_review(row)

        self.assertAlmostEqual(review["expected_fill_price"], 418.6749877929688, places=6)
        self.assertAlmostEqual(review["actual_fill_price"], 418.73, places=6)
        self.assertIsNotNone(review["fill_slippage_bps"])
        self.assertEqual(review["execution_review_key"], "clean_fill")

    def test_automation_order_event_derives_slippage_from_synced_order(self) -> None:
        row = type(
            "OrderEventRow",
            (),
            {
                "id": "evt-1",
                "trade_id": "trade-1",
                "ticker": "MSFT",
                "event_key": "order.filled",
                "status": "filled",
                "detail": "Filled.",
                "created_at": pd.Timestamp("2026-04-21T12:00:00+00:00").to_pydatetime(),
                "payload_json": {
                    "synced_order": {
                        "live_price_at_submit": 418.6749877929688,
                        "broker_filled_avg_price": 418.73,
                    }
                },
            },
        )()

        payload = trade_automation_service._serialize_automation_order_event(row)

        self.assertIsNotNone(payload["slippage_bps"])
        self.assertGreater(payload["slippage_bps"], 0.0)

    def test_lifecycle_health_ignores_rejects_followed_by_later_fill(self) -> None:
        event_items = [
            {
                "trade_id": "trade-fill",
                "ticker": "MSFT",
                "status": "filled",
                "created_at": "2026-04-20T17:05:00+00:00",
            },
            {
                "trade_id": "trade-reject",
                "ticker": "MSFT",
                "status": "rejected",
                "created_at": "2026-04-20T17:00:00+00:00",
            },
        ]
        with (
            patch.object(trade_service, "get_pending_orders_snapshot", return_value={"items": []}),
            patch.object(trade_service, "get_order_events_snapshot", return_value={"items": event_items}),
        ):
            snapshot = trade_service.get_order_lifecycle_health_snapshot()

        self.assertEqual(snapshot["summary"]["reject_count"], 0)
        self.assertEqual(snapshot["checks"][1]["status"], "healthy")
        self.assertEqual(snapshot["unresolved_rejections"], [])

    def test_validate_option_execution_request_accepts_clean_contract(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_right="call",
            contract_symbol="SPY260417C00560000",
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
        )
        report = {
            "option_plan": {
                "option_side": "call",
                "recommended_contract": {
                    "contract_symbol": "SPY260417C00560000",
                    "expiration": "2026-04-17",
                    "strike": 560.0,
                    "bid": 2.4,
                    "ask": 2.5,
                    "mid": 2.45,
                    "spread_pct": 0.04,
                    "volume": 250,
                    "open_interest": 1000,
                    "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                }
            }
        }
        position = {"contract_mid": 2.45}

        review = trade_service._validate_option_execution_request(request, report=report, position=position)

        self.assertIsNotNone(review)
        self.assertEqual(review["status"], "pass")
        self.assertEqual(review["contract_symbol"], "SPY260417C00560000")

    def test_validate_option_execution_request_rejects_wide_spread(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_right="call",
            contract_symbol="SPY260417C00560000",
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
        )
        report = {
            "option_plan": {
                "option_side": "call",
                "recommended_contract": {
                    "contract_symbol": "SPY260417C00560000",
                    "expiration": "2026-04-17",
                    "strike": 560.0,
                    "bid": 2.0,
                    "ask": 2.5,
                    "mid": 2.25,
                    "spread_pct": 0.25,
                    "volume": 250,
                    "open_interest": 1000,
                    "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                }
            }
        }
        position = {"contract_mid": 2.25}

        with self.assertRaisesRegex(Exception, "wider than 15% spread"):
            trade_service._validate_option_execution_request(request, report=report, position=position)

    def test_validate_option_execution_request_rejects_stale_quote(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_right="call",
            contract_symbol="SPY260417C00560000",
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
        )
        report = {
            "option_plan": {
                "option_side": "call",
                "recommended_contract": {
                    "contract_symbol": "SPY260417C00560000",
                    "expiration": "2026-04-17",
                    "strike": 560.0,
                    "bid": 2.4,
                    "ask": 2.5,
                    "mid": 2.45,
                    "spread_pct": 0.04,
                    "volume": 250,
                    "open_interest": 1000,
                    "quote_timestamp": (pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=10)).isoformat(),
                }
            }
        }
        position = {"contract_mid": 2.45}

        with self.assertRaisesRegex(Exception, "stale contract quote"):
            trade_service._validate_option_execution_request(request, report=report, position=position)

    def test_preview_trade_from_request_returns_ready_long_option_route(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_strategy="long_option",
            option_right="call",
            contract_symbol="SPY260417C00560000",
            contract_expiration="2026-04-17",
            contract_strike=560.0,
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
            account_size=10000,
            risk_percent=1.0,
        )
        analysis = {
            "live_price": 558.0,
            "report": {
                "ticker": "SPY",
                "trade_decision": "VALID TRADE",
                "reject_reason": "",
                "verdict": "BULLISH",
                "option_plan": {
                    "option_side": "call",
                    "expected_underlying_target": 566.0,
                    "invalidation_price": 552.0,
                    "recommended_contract": {
                        "contract_symbol": "SPY260417C00560000",
                        "expiration": "2026-04-17",
                        "strike": 560.0,
                        "bid": 2.4,
                        "ask": 2.5,
                        "mid": 2.45,
                        "spread_pct": 0.04,
                        "volume": 250,
                        "open_interest": 1000,
                        "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                    },
                },
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 490.0,
            "total_max_loss": 490.0,
            "effective_max_risk_dollars": 100.0,
            "entry_unit_price": 2.45,
            "contract_mid": 2.45,
            "affordable": True,
            "status": "VALID TRADE",
            "reason": "Sizing is ready for review.",
            "unit_label": "contracts",
        }

        with (
            patch.object(trade_service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "_find_existing_pending_order_for_ticker", return_value=None),
            patch.object(trade_service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service, "_scoped_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "analyze_market", return_value=analysis),
            patch.object(sdm, "calculate_position_sizing", return_value=position),
            patch.object(sdm, "get_contract_quote_from_chain", return_value=None),
        ):
            preview = trade_service.preview_trade_from_request(request)

        self.assertTrue(preview["preview"])
        self.assertFalse(preview["blocked"])
        self.assertTrue(preview["route_eligibility"]["allowed"])
        self.assertEqual(preview["liquidity_execution"]["status"], "pass")
        self.assertEqual(preview["pre_trade_risk"]["contract_multiplier"], 100)
        self.assertEqual(preview["pre_trade_risk"]["premium_at_risk"], 490.0)
        self.assertEqual(preview["option_execution_review"]["status"], "pass")

    def test_preview_trade_from_request_blocks_wide_option_quote(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_strategy="long_option",
            option_right="call",
            contract_symbol="SPY260417C00560000",
            contract_expiration="2026-04-17",
            contract_strike=560.0,
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
            account_size=10000,
            risk_percent=1.0,
        )
        analysis = {
            "live_price": 558.0,
            "report": {
                "ticker": "SPY",
                "trade_decision": "VALID TRADE",
                "reject_reason": "",
                "verdict": "BULLISH",
                "option_plan": {
                    "option_side": "call",
                    "expected_underlying_target": 566.0,
                    "invalidation_price": 552.0,
                    "recommended_contract": {
                        "contract_symbol": "SPY260417C00560000",
                        "expiration": "2026-04-17",
                        "strike": 560.0,
                        "bid": 2.0,
                        "ask": 2.5,
                        "mid": 2.25,
                        "spread_pct": 0.25,
                        "volume": 250,
                        "open_interest": 1000,
                        "quote_timestamp": pd.Timestamp.now(tz="UTC").isoformat(),
                    },
                },
            },
        }
        position = {
            "suggested_contracts": 2,
            "total_position_cost": 450.0,
            "total_max_loss": 450.0,
            "effective_max_risk_dollars": 100.0,
            "entry_unit_price": 2.25,
            "contract_mid": 2.25,
            "affordable": True,
            "status": "VALID TRADE",
            "reason": "Sizing is ready for review.",
            "unit_label": "contracts",
        }

        with (
            patch.object(trade_service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "_find_existing_pending_order_for_ticker", return_value=None),
            patch.object(trade_service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service, "_scoped_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "analyze_market", return_value=analysis),
            patch.object(sdm, "calculate_position_sizing", return_value=position),
            patch.object(sdm, "get_contract_quote_from_chain", return_value=None),
        ):
            preview = trade_service.preview_trade_from_request(request)

        self.assertTrue(preview["blocked"])
        self.assertFalse(preview["route_eligibility"]["allowed"])
        self.assertEqual(preview["option_execution_review"]["status"], "fail")
        self.assertIn("Spread", " ".join(preview["route_eligibility"]["block_reasons"]))
        self.assertEqual(preview["liquidity_execution"]["status"], "blocked")

    def test_preview_trade_from_request_blocks_short_premium_as_review_only(self) -> None:
        request = OpenTradeRequest(
            ticker="SPY",
            instrument_type="listed_option",
            option_strategy="short_premium",
            option_right="call",
            contract_symbol="SPY260417C00560000",
            contract_expiration="2026-04-17",
            contract_strike=560.0,
            broker_side="sell",
            order_type="limit",
            time_in_force="day",
            limit_price=2.45,
            account_size=10000,
            risk_percent=1.0,
        )
        analysis = {
            "live_price": 558.0,
            "report": {
                "ticker": "SPY",
                "trade_decision": "VALID TRADE",
                "reject_reason": "",
                "verdict": "BULLISH",
                "option_plan": {
                    "option_side": "call",
                    "expected_underlying_target": 566.0,
                    "invalidation_price": 552.0,
                },
            },
        }

        with (
            patch.object(trade_service, "_scoped_open_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "_find_existing_pending_order_for_ticker", return_value=None),
            patch.object(trade_service, "_scoped_pending_orders", return_value=pd.DataFrame()),
            patch.object(trade_service, "_scoped_closed_trades", return_value=pd.DataFrame()),
            patch.object(trade_service, "analyze_market", return_value=analysis),
        ):
            preview = trade_service.preview_trade_from_request(request)

        self.assertTrue(preview["blocked"])
        self.assertFalse(preview["route_eligibility"]["allowed"])
        self.assertIn("Short premium", preview["route_eligibility"]["detail"])

    def test_open_trade_record_persists_option_contract_audit_fields(self) -> None:
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "verdict": "BULLISH",
            "alignment_label": "Aligned",
            "conviction_label": "High",
            "setup_score": 8.5,
            "setup_grade": "A",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "event_label": "",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "forecast": {
                "forecast_horizon_bars": 5,
            },
            "option_plan": {
                "expected_underlying_target": 715.0,
                "invalidation_price": 704.0,
                "take_profit_1": 0.25,
                "take_profit_2": 0.5,
                "recommended_contract": {
                    "contract_symbol": "SPY260417C00560000",
                    "mid": 2.45,
                    "bid": 2.4,
                    "ask": 2.5,
                    "spread_pct": 0.04,
                    "volume": 250,
                    "open_interest": 1000,
                    "quote_timestamp": "2026-04-21T12:00:00+00:00",
                },
            },
        }
        position = {"suggested_contracts": 2, "total_position_cost": 490.0, "max_risk_dollars": 250.0}

        record = sdm.open_trade_record(
            report,
            live_price=710.0,
            position=position,
            order_ticket={"instrument_type": "listed_option", "option_right": "call"},
        )

        self.assertEqual(record["contract_symbol"], "SPY260417C00560000")
        self.assertEqual(record["contract_volume"], 250)
        self.assertEqual(record["contract_open_interest"], 1000)
        self.assertEqual(record["contract_quote_timestamp"], "2026-04-21T12:00:00+00:00")
        self.assertEqual(record["horizon_bars"], 5)
        self.assertEqual(record["current_exit_stage"], "INITIAL")
        self.assertEqual(record["tp1_taken_at"], "")
        self.assertEqual(record["tp2_taken_at"], "")
        self.assertEqual(record["last_exit_reason"], "")
        self.assertEqual(record["active_stop_price"], 704.0)
        self.assertAlmostEqual(record["next_target_price"], 716.0)
        self.assertEqual(record["tp1_pct"], 0.25)
        self.assertEqual(record["tp2_pct"], 0.5)
        self.assertAlmostEqual(record["exit_plan"]["entry_reference_price"], 710.0)
        self.assertAlmostEqual(record["exit_plan"]["initial_stop_price"], 704.0)
        self.assertAlmostEqual(record["exit_plan"]["risk_unit"], 6.0)
        self.assertAlmostEqual(record["exit_plan"]["tp1_price"], 716.0)
        self.assertAlmostEqual(record["exit_plan"]["tp2_price"], 722.0)
        self.assertEqual(record["exit_plan"]["time_stop_bars"], 5)
        self.assertIn('"tp1_price": 716.0', json.dumps(record["exit_plan"], allow_nan=True))

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "open_trades.csv"
            sdm.append_open_trade(record, file_path=file_path)
            restored = sdm.read_open_trades(file_path=file_path).iloc[0].to_dict()

        self.assertIsInstance(restored["exit_plan"], dict)
        self.assertAlmostEqual(restored["exit_plan"]["tp1_price"], 716.0)
        self.assertEqual(restored["tp1_pct"], 0.25)
        self.assertEqual(restored["tp2_pct"], 0.5)

    def test_fill_pending_order_rebuilds_exit_plan_from_actual_fill_price(self) -> None:
        report = {
            "ticker": "SPY",
            "interval": "5m",
            "verdict": "BULLISH",
            "alignment_label": "Aligned",
            "conviction_label": "High",
            "setup_score": 8.5,
            "setup_grade": "A",
            "trade_decision": "VALID TRADE",
            "reject_reason": "",
            "event_risk": False,
            "event_label": "",
            "event_reason": "",
            "next_event_name": "",
            "next_event_date": "",
            "forecast": {
                "forecast_horizon_bars": 6,
            },
            "option_plan": {
                "expected_underlying_target": 715.0,
                "invalidation_price": 704.0,
                "take_profit_1": 0.25,
                "take_profit_2": 0.5,
                "recommended_contract": {
                    "contract_symbol": "SPY260417C00560000",
                    "mid": 2.45,
                    "bid": 2.4,
                    "ask": 2.5,
                    "spread_pct": 0.04,
                    "volume": 250,
                    "open_interest": 1000,
                    "quote_timestamp": "2026-04-21T12:00:00+00:00",
                },
            },
        }
        position = {"suggested_contracts": 2, "total_position_cost": 490.0, "max_risk_dollars": 250.0}
        pending = sdm.pending_order_record(
            report,
            live_price=710.0,
            position=position,
            order_ticket={"instrument_type": "listed_option", "option_right": "call"},
            trade_id="trade-fill-1",
            order_id="order-fill-1",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            pending_path = Path(tmpdir) / "pending.csv"
            open_path = Path(tmpdir) / "open.csv"
            sdm.append_pending_order(pending, file_path=pending_path)

            filled = sdm.fill_pending_order(
                "order-fill-1",
                fill_underlying_price=712.0,
                file_path_pending=pending_path,
                file_path_open=open_path,
            )
            restored = sdm.read_open_trades(file_path=open_path).iloc[0].to_dict()
            remaining_pending = sdm.read_pending_orders(file_path=pending_path)

        assert filled is not None
        self.assertEqual(filled["live_price_at_open"], 712.0)
        self.assertEqual(filled["active_stop_price"], 704.0)
        self.assertAlmostEqual(filled["next_target_price"], 720.0)
        self.assertEqual(filled["current_exit_stage"], "INITIAL")
        self.assertEqual(filled["tp1_taken_at"], "")
        self.assertEqual(filled["tp2_taken_at"], "")
        self.assertEqual(filled["exit_plan"]["entry_reference_source"], "filled_open")
        self.assertAlmostEqual(filled["exit_plan"]["entry_reference_price"], 712.0)
        self.assertAlmostEqual(filled["exit_plan"]["risk_unit"], 8.0)
        self.assertAlmostEqual(filled["exit_plan"]["tp1_price"], 720.0)
        self.assertAlmostEqual(filled["exit_plan"]["tp2_price"], 728.0)
        self.assertIsInstance(restored["exit_plan"], dict)
        self.assertAlmostEqual(restored["exit_plan"]["entry_reference_price"], 712.0)
        self.assertAlmostEqual(restored["next_target_price"], 720.0)
        self.assertTrue(remaining_pending.empty)

    def test_evaluate_open_trade_exit_bullish_stages(self) -> None:
        now = datetime(2026, 4, 22, 14, 20, tzinfo=timezone.utc)
        base_trade = self._build_trade_row()

        with patch.object(sdm, "utc_now", return_value=now):
            cases = [
                ("hold", base_trade, 104.0, "HOLD", "INITIAL", 95.0, 105.0),
                ("tp1", base_trade, 105.0, "SELL 50% NOW", "INITIAL", 95.0, 105.0),
                ("stop_before_tp1", base_trade, 94.0, "STOP HIT", "INITIAL", 95.0, 105.0),
                (
                    "tp2_after_tp1",
                    {**base_trade, "tp1_taken_at": "2026-04-22T14:05:00+00:00"},
                    110.0,
                    "SELL MORE NOW",
                    "TP1_LOCKED",
                    100.0,
                    110.0,
                ),
                (
                    "breakeven_stop_after_tp1",
                    {**base_trade, "tp1_taken_at": "2026-04-22T14:05:00+00:00"},
                    100.0,
                    "STOP HIT",
                    "TP1_LOCKED",
                    100.0,
                    110.0,
                ),
                (
                    "locked_stop_after_tp2",
                    {
                        **base_trade,
                        "tp1_taken_at": "2026-04-22T14:05:00+00:00",
                        "tp2_taken_at": "2026-04-22T14:10:00+00:00",
                    },
                    104.5,
                    "STOP HIT",
                    "TP2_LOCKED",
                    105.0,
                    None,
                ),
            ]

            for label, trade_row, underlying_price, expected_action, expected_stage, expected_stop, expected_target in cases:
                with self.subTest(label=label):
                    result = sdm.evaluate_open_trade_exit(
                        trade_row,
                        current_underlying_price=underlying_price,
                        current_contract_mid=2.5,
                    )
                    self.assertEqual(result["monitor_action"], expected_action)
                    self.assertEqual(result["current_exit_stage"], expected_stage)
                    self.assertAlmostEqual(result["active_stop_price"], expected_stop)
                    if expected_target is None:
                        self.assertTrue(pd.isna(result["next_target_price"]))
                    else:
                        self.assertAlmostEqual(result["next_target_price"], expected_target)

            time_stop_trade = self._build_trade_row(opened_at="2026-04-22T13:45:00+00:00")
            time_stop_result = sdm.evaluate_open_trade_exit(
                time_stop_trade,
                current_underlying_price=101.0,
                current_contract_mid=2.1,
            )

        self.assertEqual(time_stop_result["monitor_action"], "TIME STOP")
        self.assertEqual(time_stop_result["exit_reason"], "time_stop")
        self.assertEqual(time_stop_result["time_stop_bars"], 5)
        self.assertEqual(time_stop_result["bars_held"], 7)

    def test_evaluate_open_trade_exit_bearish_stages(self) -> None:
        now = datetime(2026, 4, 22, 14, 20, tzinfo=timezone.utc)
        base_trade = self._build_trade_row(
            ticker="QQQ",
            verdict="BEARISH",
            contract_symbol="QQQ260417P00400000",
            invalidation_price=105.0,
        )

        with patch.object(sdm, "utc_now", return_value=now):
            cases = [
                ("hold", base_trade, 96.0, "HOLD", "INITIAL", 105.0, 95.0),
                ("tp1", base_trade, 95.0, "SELL 50% NOW", "INITIAL", 105.0, 95.0),
                ("stop_before_tp1", base_trade, 106.0, "STOP HIT", "INITIAL", 105.0, 95.0),
                (
                    "tp2_after_tp1",
                    {**base_trade, "tp1_taken_at": "2026-04-22T14:05:00+00:00"},
                    90.0,
                    "SELL MORE NOW",
                    "TP1_LOCKED",
                    100.0,
                    90.0,
                ),
                (
                    "breakeven_stop_after_tp1",
                    {**base_trade, "tp1_taken_at": "2026-04-22T14:05:00+00:00"},
                    100.0,
                    "STOP HIT",
                    "TP1_LOCKED",
                    100.0,
                    90.0,
                ),
                (
                    "locked_stop_after_tp2",
                    {
                        **base_trade,
                        "tp1_taken_at": "2026-04-22T14:05:00+00:00",
                        "tp2_taken_at": "2026-04-22T14:10:00+00:00",
                    },
                    96.0,
                    "STOP HIT",
                    "TP2_LOCKED",
                    95.0,
                    None,
                ),
            ]

            for label, trade_row, underlying_price, expected_action, expected_stage, expected_stop, expected_target in cases:
                with self.subTest(label=label):
                    result = sdm.evaluate_open_trade_exit(
                        trade_row,
                        current_underlying_price=underlying_price,
                        current_contract_mid=2.5,
                    )
                    self.assertEqual(result["monitor_action"], expected_action)
                    self.assertEqual(result["current_exit_stage"], expected_stage)
                    self.assertAlmostEqual(result["active_stop_price"], expected_stop)
                    if expected_target is None:
                        self.assertTrue(pd.isna(result["next_target_price"]))
                    else:
                        self.assertAlmostEqual(result["next_target_price"], expected_target)

            time_stop_trade = self._build_trade_row(
                ticker="QQQ",
                verdict="BEARISH",
                contract_symbol="QQQ260417P00400000",
                invalidation_price=105.0,
                opened_at="2026-04-22T13:45:00+00:00",
            )
            time_stop_result = sdm.evaluate_open_trade_exit(
                time_stop_trade,
                current_underlying_price=99.0,
                current_contract_mid=2.1,
            )

        self.assertEqual(time_stop_result["monitor_action"], "TIME STOP")
        self.assertEqual(time_stop_result["exit_reason"], "time_stop")
        self.assertEqual(time_stop_result["time_stop_bars"], 5)
        self.assertEqual(time_stop_result["bars_held"], 7)

    def test_monitor_open_trades_matches_shared_exit_evaluator_for_equities_and_options(self) -> None:
        now = datetime(2026, 4, 22, 14, 20, tzinfo=timezone.utc)
        option_trade = self._build_trade_row()
        equity_trade = self._build_trade_row(
            ticker="QQQ",
            verdict="BEARISH",
            instrument_type="equity",
            contract_symbol="EQUITY:QQQ",
            invalidation_price=105.0,
        )

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(sdm, "utc_now", return_value=now),
            patch.object(sdm, "batch_get_live_prices", return_value={"SPY": 105.0, "QQQ": 106.0}),
            patch.object(sdm, "batch_get_contract_mids", return_value={"SPY260417C00560000": 2.6}),
        ):
            file_path = Path(tmpdir) / "open_trades.csv"
            pd.DataFrame([option_trade, equity_trade]).to_csv(file_path, index=False)
            monitored = sdm.monitor_open_trades(file_path=file_path)

        monitored_by_trade = {row["trade_id"]: row for row in monitored.to_dict(orient="records")}
        option_eval = sdm.evaluate_open_trade_exit(
            option_trade,
            current_underlying_price=105.0,
            current_contract_mid=2.6,
        )
        equity_eval = sdm.evaluate_open_trade_exit(
            equity_trade,
            current_underlying_price=106.0,
            current_contract_mid=1.06,
        )

        self.assertEqual(monitored_by_trade[option_trade["trade_id"]]["monitor_action"], option_eval["monitor_action"])
        self.assertEqual(monitored_by_trade[option_trade["trade_id"]]["current_exit_stage"], option_eval["current_exit_stage"])
        self.assertAlmostEqual(monitored_by_trade[option_trade["trade_id"]]["active_stop_price"], option_eval["active_stop_price"])
        self.assertEqual(monitored_by_trade[equity_trade["trade_id"]]["monitor_action"], equity_eval["monitor_action"])
        self.assertEqual(monitored_by_trade[equity_trade["trade_id"]]["current_exit_stage"], equity_eval["current_exit_stage"])
        self.assertAlmostEqual(monitored_by_trade[equity_trade["trade_id"]]["current_contract_mid"], 1.06)

    def test_monitor_open_trades_overwrites_stale_entry_quote_age_with_current_price_age(self) -> None:
        equity_trade = self._build_trade_row(
            ticker="QQQ",
            verdict="BULLISH",
            instrument_type="equity",
            contract_symbol="EQUITY:QQQ",
            invalidation_price=95.0,
        )
        equity_trade["quote_age_seconds"] = 9999.0

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch.object(sdm.time, "monotonic", return_value=1000.0),
            patch.dict(sdm._LIVE_PRICE_CACHE_TS, {"QQQ": 995.0}, clear=True),
            patch.object(sdm, "batch_get_live_prices", return_value={"QQQ": 106.0}),
            patch.object(sdm, "batch_get_contract_mids", return_value={}),
        ):
            file_path = Path(tmpdir) / "open_trades.csv"
            pd.DataFrame([equity_trade]).to_csv(file_path, index=False)
            monitored = sdm.monitor_open_trades(file_path=file_path)

        row = monitored.to_dict(orient="records")[0]
        self.assertEqual(row["quote_age_seconds"], 5.0)
        self.assertEqual(row["market_data_age_seconds"], 5.0)
        self.assertEqual(row["quote_freshness_source"], "live_equity_price")

    def test_reconcile_local_broker_paper_state_uses_option_contract_symbol(self) -> None:
        open_trades = pd.DataFrame(
            [
                {
                    "trade_id": "trade-1",
                    "ticker": "SPY",
                    "instrument_type": "listed_option",
                    "contract_symbol": "SPY260417C00560000",
                    "broker_name": "alpaca_paper",
                    "automation_tenant_id": "",
                }
            ]
        )

        with (
            patch.object(sdm, "read_open_trades", return_value=open_trades),
            patch.object(sdm, "read_closed_trades", return_value=pd.DataFrame()),
        ):
            snapshot = portfolio_service._reconcile_local_broker_paper_state(
                current_user=SimpleNamespace(tenant_id="tenant-1"),
                broker_account={
                    "connected": True,
                    "positions": [{"symbol": "SPY260417C00560000"}],
                },
            )

        self.assertTrue(snapshot["performed"])
        self.assertEqual(snapshot["reconciled_open_trades"], 0)


if __name__ == "__main__":
    unittest.main()
