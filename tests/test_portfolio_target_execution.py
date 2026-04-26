from __future__ import annotations

import unittest
from datetime import datetime, timezone
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


def _build_frame(prices: list[float], *, base_volume: float = 1_000_000.0) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=len(prices), freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "Open": [price * 0.998 for price in prices],
            "High": [price * 1.004 for price in prices],
            "Low": [price * 0.996 for price in prices],
            "Close": prices,
            "Volume": [base_volume + (idx * 5000.0) for idx in range(len(prices))],
        },
        index=index,
    )


def _build_market_state(symbols: tuple[str, ...]) -> dict[str, object]:
    base_series = {
        "SPY": [100 + (idx * 0.55) for idx in range(260)],
        "QQQ": [98 + (idx * 0.62) for idx in range(260)],
        "IWM": [80 + (idx * 0.35) for idx in range(260)],
        "XLE": [70 + (idx * 0.42) for idx in range(260)],
        "XOP": [68 + (idx * 0.38) + ((idx % 7) * 0.04) for idx in range(260)],
    }
    bars = {symbol: _build_frame(base_series[symbol]) for symbol in symbols}
    return {
        "as_of": datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc).isoformat(),
        "provider_name": "test_provider",
        "requirements": [],
        "bars": bars,
    }


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame()


class PortfolioTargetExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        import backend.models.saas  # noqa: F401
        from backend.core.database import Base
        from backend.services import tenant_service
        from backend.services.strategy_engine import service as strategy_service

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
                auth_subject="demo-portfolio-exec-user",
                email="demo-portfolio-exec@example.test",
                name="Demo Portfolio Exec User",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        self.identity = identity
        self.current_user = SimpleNamespace(
            tenant_id=identity["active_tenant"].id,
            auth_subject="demo-portfolio-exec-user",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
        )
        strategy_service.ensure_strategy_desks(self.db, current_user=self.current_user)

    def _build_manual_portfolio_run(self, *, status: str = "accepted", targets: list[dict[str, object]]) -> object:
        from backend.models.saas import PortfolioTargetRun

        row = PortfolioTargetRun(
            tenant_id=self.current_user.tenant_id,
            status=status,
            source_run_ids_json={},
            desk_inputs_json={},
            allocator_config_json={},
            portfolio_targets_json={"targets": targets},
            order_plan_json={"targets": [target.get("order_plan") for target in targets]},
            metrics_json={
                "allowed": status == "accepted",
                "gross_ok": status == "accepted",
                "net_ok": status == "accepted",
                "target_count": len(targets),
                "desk_count": len({(target.get("desk_contributions") or [{}])[0].get("desk_key") for target in targets if target.get("desk_contributions")}),
            },
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _build_execution_run_with_item(
        self,
        *,
        action: str = "open",
        status: str = "submitted",
        reconciliation_status: str = "submitted",
        symbol: str = "SPY",
        requested_delta_quantity: float = 10.0,
        strategy_desk_key: str = "macro_trend",
        submitted_trade_id: str = "trade-sync-1",
        submitted_order_id: str = "order-sync-1",
        broker_order_id: str = "broker-sync-1",
    ) -> tuple[object, object]:
        from backend.models.saas import PortfolioTargetExecutionItem, PortfolioTargetExecutionRun

        run = PortfolioTargetExecutionRun(
            tenant_id=self.current_user.tenant_id,
            status="working",
            execution_intent="broker_paper",
            dry_run=False,
            summary_json={"status": "working"},
            metadata_json={"scope": "personal_paper"},
            started_at=datetime(2026, 4, 24, 14, 35, tzinfo=timezone.utc),
        )
        self.db.add(run)
        self.db.flush()
        item = PortfolioTargetExecutionItem(
            execution_run_id=run.id,
            symbol=symbol,
            action=action,
            status=status,
            requested_target_weight=0.1,
            requested_target_notional=1000.0,
            requested_delta_quantity=requested_delta_quantity,
            resolved_route="broker_paper",
            strategy_desk_key=strategy_desk_key,
            submitted_trade_id=submitted_trade_id,
            submitted_order_id=submitted_order_id,
            broker_order_id=broker_order_id,
            broker_status="accepted",
            filled_quantity=0.0,
            remaining_quantity=requested_delta_quantity,
            reconciliation_status=reconciliation_status,
            source_metadata_json={"route_correlation_id": "route-sync-1"},
            result_json={},
        )
        self.db.add(item)
        self.db.commit()
        self.db.refresh(run)
        self.db.refresh(item)
        return run, item

    def test_execute_macro_targets_opens_long_paper_entry_and_persists_execution_run(self) -> None:
        from backend.services.portfolio_target_execution.service import (
            execute_portfolio_targets,
            get_latest_portfolio_target_execution,
        )
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="macro_trend",
            updates={"config": {"universe": ["SPY"], "max_positions": 1}},
        )
        market_state = _build_market_state(("SPY",))
        with (
            patch("backend.services.feature_store.service.load_market_state", return_value=market_state),
            patch("backend.services.strategy_engine.service.record_audit_event", return_value=None),
        ):
            strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="macro_trend")
            latest_targets = strategy_service.build_allocator_snapshot(self.db, current_user=self.current_user)

        captured_requests: list[object] = []

        def _fake_open(request, *, db=None, current_user=None):
            captured_requests.append(request)
            return {
                "opened": True,
                "position_opened": True,
                "record": {"trade_id": "trade-macro-1", "order_id": "order-macro-1"},
                "pending_order": None,
                "execution": {"broker_status": "filled"},
            }

        with (
            patch("backend.services.portfolio_target_execution.service.analyze_market", return_value={"live_price": 100.0}),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service.open_trade_from_request", side_effect=_fake_open),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
        ):
            snapshot = execute_portfolio_targets(
                self.db,
                current_user=self.current_user,
                request=SimpleNamespace(portfolio_target_run_id=latest_targets["latest_run_id"], execution_intent="broker_paper", dry_run=False),
            )

        self.assertEqual(snapshot["status"], "filled")
        self.assertEqual(snapshot["summary"]["executed_count"], 1)
        self.assertEqual(len(captured_requests), 1)
        self.assertEqual(captured_requests[0].ticker, "SPY")
        self.assertEqual(captured_requests[0].broker_side, "buy")
        self.assertEqual(captured_requests[0].execution_mode, "portfolio_target_execution")
        self.assertEqual(captured_requests[0].order_type, "limit")
        self.assertEqual(captured_requests[0].time_in_force, "day_ext")
        self.assertTrue(captured_requests[0].extended_hours)
        self.assertFalse(captured_requests[0].regular_hours_only)
        self.assertEqual(captured_requests[0].source, "strategy_allocator")
        self.assertEqual(captured_requests[0].portfolio_target_run_id, latest_targets["latest_run_id"])
        self.assertEqual(snapshot["items"][0]["strategy_desk_key"], "macro_trend")

        latest_execution = get_latest_portfolio_target_execution(self.db, current_user=self.current_user)
        self.assertEqual(latest_execution["latest_execution_run_id"], snapshot["latest_execution_run_id"])
        self.assertEqual(latest_execution["items"][0]["submitted_trade_id"], "trade-macro-1")

    def test_execute_stat_arb_targets_route_paired_long_short_entries(self) -> None:
        from backend.services.portfolio_target_execution.service import execute_portfolio_targets
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="stat_arb",
            updates={"config": {"pairs": [["SPY", "QQQ"]], "max_pairs": 1}},
        )
        market_state = _build_market_state(("SPY", "QQQ"))
        with (
            patch("backend.services.feature_store.service.load_market_state", return_value=market_state),
            patch("backend.services.strategy_engine.service.record_audit_event", return_value=None),
        ):
            strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="stat_arb")
            latest_targets = strategy_service.build_allocator_snapshot(self.db, current_user=self.current_user)

        captured_sides: list[tuple[str, str]] = []

        def _fake_open(request, *, db=None, current_user=None):
            captured_sides.append((request.ticker, request.broker_side))
            return {
                "opened": True,
                "position_opened": True,
                "record": {"trade_id": f"trade-{request.ticker}", "order_id": f"order-{request.ticker}"},
                "pending_order": None,
                "execution": {"broker_status": "filled"},
            }

        with (
            patch("backend.services.portfolio_target_execution.service.analyze_market", return_value={"live_price": 100.0}),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service.open_trade_from_request", side_effect=_fake_open),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
        ):
            snapshot = execute_portfolio_targets(
                self.db,
                current_user=self.current_user,
                request=SimpleNamespace(portfolio_target_run_id=latest_targets["latest_run_id"], execution_intent="broker_paper", dry_run=False),
            )

        self.assertEqual(snapshot["status"], "filled")
        self.assertEqual(len(snapshot["items"]), 2)
        self.assertEqual({side for _, side in captured_sides}, {"buy", "sell"})
        self.assertEqual({symbol for symbol, _ in captured_sides}, {"SPY", "QQQ"})

    def test_execute_portfolio_targets_blocks_when_latest_allocator_snapshot_is_blocked(self) -> None:
        from backend.services.portfolio_target_execution.service import execute_portfolio_targets

        self._build_manual_portfolio_run(
            status="blocked",
            targets=[
                {
                    "symbol": "SPY",
                    "target_weight": 0.2,
                    "target_notional": 20000.0,
                    "desk_contributions": [{"desk_key": "macro_trend", "target_weight": 0.2, "target_notional": 20000.0}],
                    "order_plan": {"side": "buy", "notional": 20000.0, "order_type": "limit", "time_in_force": "day"},
                }
            ],
        )

        with patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None):
            snapshot = execute_portfolio_targets(
                self.db,
                current_user=self.current_user,
                request=SimpleNamespace(portfolio_target_run_id=None, execution_intent="broker_paper", dry_run=False),
            )

        self.assertEqual(snapshot["status"], "blocked")
        self.assertIn("blocked", snapshot["summary"]["blocked_reason"].lower())
        self.assertEqual(snapshot["items"], [])

    def test_execute_portfolio_targets_nets_pending_orders_without_double_submitting(self) -> None:
        from backend.services.portfolio_target_execution.service import execute_portfolio_targets

        portfolio_run = self._build_manual_portfolio_run(
            targets=[
                {
                    "symbol": "SPY",
                    "target_weight": 0.01,
                    "target_notional": 1000.0,
                    "desk_contributions": [{"desk_key": "macro_trend", "target_weight": 0.01, "target_notional": 1000.0}],
                    "order_plan": {"side": "buy", "notional": 1000.0, "order_type": "limit", "time_in_force": "day"},
                }
            ],
        )
        pending_orders = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "suggested_contracts": 10.0,
                    "broker_side": "BUY",
                    "order_id": "pending-1",
                    "trade_id": "trade-pending-1",
                }
            ]
        )

        with (
            patch("backend.services.portfolio_target_execution.service.analyze_market", return_value={"live_price": 100.0}),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=pending_orders),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
        ):
            snapshot = execute_portfolio_targets(
                self.db,
                current_user=self.current_user,
                request=SimpleNamespace(portfolio_target_run_id=portfolio_run.id, execution_intent="broker_paper", dry_run=False),
            )

        self.assertEqual(snapshot["status"], "completed")
        self.assertEqual(snapshot["summary"]["executed_count"], 0)
        self.assertEqual(snapshot["items"][0]["action"], "skip")
        self.assertIn("pending orders", snapshot["items"][0]["reason"].lower())

    def test_execute_portfolio_targets_flattens_sign_flips_without_reversing(self) -> None:
        from backend.services.portfolio_target_execution.service import execute_portfolio_targets

        portfolio_run = self._build_manual_portfolio_run(
            targets=[
                {
                    "symbol": "SPY",
                    "target_weight": -0.01,
                    "target_notional": -1000.0,
                    "desk_contributions": [{"desk_key": "stat_arb", "target_weight": -0.01, "target_notional": -1000.0}],
                    "order_plan": {"side": "sell_short", "notional": 1000.0, "order_type": "limit", "time_in_force": "day"},
                }
            ],
        )
        open_trades = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "trade_id": "open-long-1",
                    "order_id": "order-long-1",
                    "suggested_contracts": 10.0,
                    "broker_side": "BUY",
                }
            ]
        )
        close_calls: list[object] = []

        def _fake_close(request, *, db=None, current_user=None):
            close_calls.append(request)
            return {
                "closed": True,
                "closed_trade_preview": {"trade_id": "open-long-1", "broker_close_order_id": "close-order-1"},
            }

        with (
            patch("backend.services.portfolio_target_execution.service.analyze_market", return_value={"live_price": 100.0}),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=open_trades),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service.close_trade_from_request", side_effect=_fake_close),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
        ):
            snapshot = execute_portfolio_targets(
                self.db,
                current_user=self.current_user,
                request=SimpleNamespace(portfolio_target_run_id=portfolio_run.id, execution_intent="broker_paper", dry_run=False),
            )

        self.assertEqual(snapshot["status"], "filled")
        self.assertEqual(len(close_calls), 1)
        self.assertEqual(snapshot["items"][0]["action"], "close")
        self.assertIn("sign flip", snapshot["items"][0]["reason"].lower())

    def test_sync_execution_marks_submitted_item_as_working_when_order_remains_pending(self) -> None:
        from backend.services.portfolio_target_execution.service import sync_portfolio_target_execution

        run, item = self._build_execution_run_with_item()
        pending_orders = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "trade_id": item.submitted_trade_id,
                    "order_id": item.submitted_order_id,
                    "broker_order_id": item.broker_order_id,
                    "broker_status": "accepted",
                    "suggested_contracts": 10.0,
                    "route_correlation_id": "route-sync-1",
                }
            ]
        )

        with (
            patch("backend.services.portfolio_target_execution.service.sync_pending_orders_from_broker", return_value={"synced": True}),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=pending_orders),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_closed_trades", return_value=_empty_trades()),
            patch(
                "backend.services.portfolio_target_execution.service.get_order_events_snapshot",
                return_value={
                    "items": [
                        {
                            "id": "event-working-1",
                            "trade_id": item.submitted_trade_id,
                            "ticker": "SPY",
                            "event_key": "order.accepted",
                            "status": "working",
                            "payload": {"route_correlation_id": "route-sync-1", "broker_order_id": item.broker_order_id},
                            "created_at": datetime(2026, 4, 24, 14, 36, tzinfo=timezone.utc).isoformat(),
                        }
                    ]
                },
            ),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
            patch("backend.services.portfolio_target_execution.service.record_domain_event", return_value=None),
        ):
            snapshot = sync_portfolio_target_execution(self.db, current_user=self.current_user, execution_run_id=run.id)

        self.assertEqual(snapshot["status"], "working")
        self.assertEqual(snapshot["working_count"], 1)
        self.assertEqual(snapshot["items"][0]["reconciliation_status"], "working")
        self.assertEqual(snapshot["items"][0]["remaining_quantity"], 10.0)

    def test_sync_execution_updates_partial_fill_quantities_and_rollups(self) -> None:
        from backend.services.portfolio_target_execution.service import sync_portfolio_target_execution

        run, item = self._build_execution_run_with_item(requested_delta_quantity=10.0)
        pending_orders = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "trade_id": item.submitted_trade_id,
                    "order_id": item.submitted_order_id,
                    "broker_order_id": item.broker_order_id,
                    "broker_status": "partially_filled",
                    "broker_filled_qty": 4.0,
                    "suggested_contracts": 10.0,
                    "route_correlation_id": "route-sync-1",
                }
            ]
        )

        with (
            patch("backend.services.portfolio_target_execution.service.sync_pending_orders_from_broker", return_value={"synced": True}),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=pending_orders),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_closed_trades", return_value=_empty_trades()),
            patch(
                "backend.services.portfolio_target_execution.service.get_order_events_snapshot",
                return_value={"items": []},
            ),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
            patch("backend.services.portfolio_target_execution.service.record_domain_event", return_value=None),
        ):
            snapshot = sync_portfolio_target_execution(self.db, current_user=self.current_user, execution_run_id=run.id)

        self.assertEqual(snapshot["status"], "partially_filled")
        self.assertEqual(snapshot["partial_fill_count"], 1)
        self.assertEqual(snapshot["items"][0]["filled_quantity"], 4.0)
        self.assertEqual(snapshot["items"][0]["remaining_quantity"], 6.0)

    def test_sync_execution_marks_filled_when_open_position_matches_execution_item(self) -> None:
        from backend.services.portfolio_target_execution.service import sync_portfolio_target_execution

        run, item = self._build_execution_run_with_item(requested_delta_quantity=5.0)
        open_trades = pd.DataFrame(
            [
                {
                    "ticker": "SPY",
                    "trade_id": item.submitted_trade_id,
                    "order_id": item.submitted_order_id,
                    "broker_order_id": item.broker_order_id,
                    "broker_status": "filled",
                    "suggested_contracts": 5.0,
                    "actual_fill_price": 101.25,
                    "route_correlation_id": "route-sync-1",
                }
            ]
        )

        with (
            patch("backend.services.portfolio_target_execution.service.sync_pending_orders_from_broker", return_value={"synced": True}),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=open_trades),
            patch("backend.services.portfolio_target_execution.service._scoped_closed_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service.get_order_events_snapshot", return_value={"items": []}),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
            patch("backend.services.portfolio_target_execution.service.record_domain_event", return_value=None),
        ):
            snapshot = sync_portfolio_target_execution(self.db, current_user=self.current_user, execution_run_id=run.id)

        self.assertEqual(snapshot["status"], "filled")
        self.assertEqual(snapshot["filled_count"], 1)
        self.assertEqual(snapshot["items"][0]["reconciliation_status"], "filled")
        self.assertEqual(snapshot["items"][0]["average_fill_price"], 101.25)
        self.assertEqual(snapshot["validation_artifact"]["readiness_state"], "ready")
        self.assertEqual(snapshot["validation_artifact"]["readiness_label"], "multi-desk paper execution ready")
        self.assertEqual(snapshot["validation_artifact"]["filled_count"], 1)
        self.assertEqual(snapshot["validation_artifact"]["orphan_event_count"], 0)
        self.assertTrue(snapshot["validation_artifact"]["clean_run"])
        self.assertEqual(snapshot["summary"]["validation_artifact"]["readiness_state"], "ready")

    def test_sync_execution_counts_orphan_events_for_unmatched_symbol_activity(self) -> None:
        from backend.services.portfolio_target_execution.service import sync_portfolio_target_execution

        run, _item = self._build_execution_run_with_item()

        with (
            patch("backend.services.portfolio_target_execution.service.sync_pending_orders_from_broker", return_value={"synced": True}),
            patch("backend.services.portfolio_target_execution.service._scoped_pending_orders", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_open_trades", return_value=_empty_trades()),
            patch("backend.services.portfolio_target_execution.service._scoped_closed_trades", return_value=_empty_trades()),
            patch(
                "backend.services.portfolio_target_execution.service.get_order_events_snapshot",
                return_value={
                    "items": [
                        {
                            "id": "orphan-event-1",
                            "trade_id": "manual-trade-1",
                            "ticker": "SPY",
                            "event_key": "order.accepted",
                            "status": "working",
                            "payload": {"route_correlation_id": "manual-route-1"},
                            "created_at": datetime(2026, 4, 24, 14, 37, tzinfo=timezone.utc).isoformat(),
                        }
                    ]
                },
            ),
            patch("backend.services.portfolio_target_execution.service.record_audit_event", return_value=None),
            patch("backend.services.portfolio_target_execution.service.record_domain_event", return_value=None),
        ):
            snapshot = sync_portfolio_target_execution(self.db, current_user=self.current_user, execution_run_id=run.id)

        self.assertEqual(snapshot["orphan_event_count"], 1)
        self.assertEqual(snapshot["items"][0]["reconciliation_status"], "orphan")
        self.assertEqual(snapshot["validation_artifact"]["readiness_state"], "blocked")
        self.assertEqual(snapshot["validation_artifact"]["orphan_event_count"], 1)
        self.assertEqual(snapshot["validation_artifact"]["orphan_events"][0]["id"], "orphan-event-1")
        self.assertIn("Unmatched", snapshot["validation_artifact"]["orphan_events"][0]["reason"])

    def test_latest_execution_snapshot_handles_multiple_historical_runs(self) -> None:
        from backend.services.portfolio_target_execution.service import get_latest_portfolio_target_execution

        first_run, _first_item = self._build_execution_run_with_item(
            submitted_trade_id="trade-old",
            submitted_order_id="order-old",
            broker_order_id="broker-old",
        )
        second_run, _second_item = self._build_execution_run_with_item(
            submitted_trade_id="trade-new",
            submitted_order_id="order-new",
            broker_order_id="broker-new",
        )

        snapshot = get_latest_portfolio_target_execution(self.db, current_user=self.current_user)

        self.assertIn(snapshot["latest_execution_run_id"], {first_run.id, second_run.id})
        self.assertEqual(snapshot["validation_artifact"]["validation_scope"], "personal_paper")


if __name__ == "__main__":
    unittest.main()
