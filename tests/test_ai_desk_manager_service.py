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
    }
    return {
        "as_of": datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc).isoformat(),
        "provider_name": "test_provider",
        "requirements": [],
        "bars": {symbol: _build_frame(base_series[symbol]) for symbol in symbols},
    }


class AiDeskManagerServiceTests(unittest.TestCase):
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
                auth_subject="demo-ai-manager-user",
                email="demo-ai-manager@example.test",
                name="Demo AI Manager User",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        self.identity = identity
        self.current_user = SimpleNamespace(
            tenant_id=identity["active_tenant"].id,
            auth_subject="demo-ai-manager-user",
            role="owner",
            platform_role="admin",
            mode="demo",
            permissions=_resolved_permissions(membership_role="owner", platform_role="admin"),
        )

    def _manual_target_run(self, *, desk_key: str = "macro_trend", symbol: str = "SPY", status: str = "accepted"):
        from backend.models.saas import PortfolioTargetRun

        target = {
            "symbol": symbol,
            "target_weight": 0.1,
            "target_notional": 10000.0,
            "directions": ["long"],
            "desk_contributions": [
                {
                    "desk_key": desk_key,
                    "target_weight": 0.1,
                    "target_notional": 10000.0,
                    "direction": "long",
                    "confidence_score": 0.8,
                }
            ],
            "risk_flags": [],
            "order_plan": {"side": "buy", "notional": 10000.0, "order_type": "limit", "time_in_force": "day"},
        }
        row = PortfolioTargetRun(
            tenant_id=self.current_user.tenant_id,
            status=status,
            source_run_ids_json={},
            desk_inputs_json={"targets": [target]},
            allocator_config_json={},
            portfolio_targets_json={"targets": [target]},
            order_plan_json={"targets": [target["order_plan"]]},
            metrics_json={
                "allowed": status == "accepted",
                "gross_ok": status == "accepted",
                "net_ok": status == "accepted",
                "target_count": 1,
                "desk_count": 1,
            },
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _paper_evidence_run(self) -> object:
        from backend.models.saas import PortfolioTargetExecutionRun

        now = datetime.now(timezone.utc)
        row = PortfolioTargetExecutionRun(
            tenant_id=self.current_user.tenant_id,
            status="filled",
            execution_intent="broker_paper",
            dry_run=False,
            filled_count=1,
            rejected_count=0,
            working_count=0,
            partial_fill_count=0,
            summary_json={"executed_count": 1},
            metadata_json={"scope": "personal_paper"},
            started_at=now,
            completed_at=now,
            last_sync_at=now,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def _live_account(self) -> object:
        from backend.models.saas import BrokerageLinkedAccount

        row = BrokerageLinkedAccount(
            tenant_id=self.current_user.tenant_id,
            owner_user_id=self.identity["user"].id,
            provider="alpaca",
            label="Live review account",
            account_environment="live",
            connection_status="connected",
            token_health="healthy",
            approval_policy="approval_required",
            oauth_access_token="live-token",
            metadata_json={"account": {"equity": "10000", "portfolio_value": "10000"}},
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def test_snapshot_aggregates_multiple_desks_and_classifies_states(self) -> None:
        from backend.services.ai_desk_manager_service import build_ai_desk_manager_snapshot
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.ensure_strategy_desks(self.db, current_user=self.current_user)
        strategy_service.update_strategy_desk(
            self.db,
            current_user=self.current_user,
            desk_key="event_driven",
            updates={"enabled": False},
        )
        with (
            patch("backend.services.feature_store.service.load_market_state", return_value=_build_market_state(("SPY", "QQQ", "IWM"))),
            patch("backend.services.strategy_engine.service.record_audit_event", return_value=None),
        ):
            strategy_service.run_strategy_desk(self.db, current_user=self.current_user, desk_key="macro_trend")

        snapshot = build_ai_desk_manager_snapshot(self.db, current_user=self.current_user)
        states = {item["desk_key"]: item["state"] for item in snapshot["desk_states"]}

        self.assertEqual(snapshot["command_center"]["desk_count"], 5)
        self.assertEqual(states["macro_trend"], "ready")
        self.assertEqual(states["event_driven"], "blocked")
        self.assertIn("stale", set(states.values()))
        self.assertTrue(snapshot["next_actions"])

    def test_trade_plan_generation_uses_latest_target_and_preview(self) -> None:
        from backend.schemas import AiTradePlanRequest
        from backend.services.ai_desk_manager_service import build_ai_trade_plan

        self._manual_target_run(desk_key="macro_trend", symbol="SPY")
        preview_payload = {
            "preview": True,
            "blocked": False,
            "route_eligibility": {"allowed": True, "warnings": []},
        }
        with patch("backend.services.ai_desk_manager_service.preview_trade_from_request", return_value=preview_payload) as preview:
            plan = build_ai_trade_plan(
                self.db,
                current_user=self.current_user,
                request=AiTradePlanRequest(target_symbol="SPY", desk_key="macro_trend", live_price=100.0),
            )

        self.assertTrue(plan["allowed"])
        self.assertEqual(plan["open_trade_request"]["ticker"], "SPY")
        self.assertEqual(plan["open_trade_request"]["instrument_type"], "equity")
        self.assertEqual(plan["open_trade_request"]["broker_side"], "buy")
        self.assertEqual(plan["open_trade_request"]["order_type"], "limit")
        self.assertTrue(plan["open_trade_request"]["long_only"])
        preview.assert_called_once()

    def test_paper_execution_blocks_non_paper_desks(self) -> None:
        from backend.schemas import AiPaperExecutionRequest
        from backend.services.ai_desk_manager_service import execute_ai_paper_execution
        from backend.services.exceptions import ValidationError
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.ensure_strategy_desks(self.db, current_user=self.current_user)
        run = self._manual_target_run(desk_key="event_driven", symbol="SPY")

        with self.assertRaises(ValidationError) as raised:
            execute_ai_paper_execution(
                self.db,
                current_user=self.current_user,
                request=AiPaperExecutionRequest(portfolio_target_run_id=run.id),
            )

        self.assertIn("not enabled for paper trading", " ".join(raised.exception.details["blockers"]))

    def test_paper_execution_hands_off_to_existing_executor_for_paper_desks(self) -> None:
        from backend.schemas import AiPaperExecutionRequest
        from backend.services.ai_desk_manager_service import execute_ai_paper_execution
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.ensure_strategy_desks(self.db, current_user=self.current_user)
        run = self._manual_target_run(desk_key="macro_trend", symbol="SPY")
        with patch(
            "backend.services.ai_desk_manager_service.execute_portfolio_targets",
            return_value={"status": "filled", "items": []},
        ) as executor:
            snapshot = execute_ai_paper_execution(
                self.db,
                current_user=self.current_user,
                request=AiPaperExecutionRequest(portfolio_target_run_id=run.id),
            )

        self.assertEqual(snapshot["status"], "filled")
        executor.assert_called_once()

    def test_live_intent_blocks_when_live_gates_are_missing(self) -> None:
        from backend.schemas import AiLiveIntentRequest
        from backend.services.ai_desk_manager_service import create_ai_live_intent
        from backend.services.exceptions import ValidationError

        self._manual_target_run(desk_key="macro_trend", symbol="SPY")

        with self.assertRaises(ValidationError) as raised:
            create_ai_live_intent(
                self.db,
                current_user=self.current_user,
                request=AiLiveIntentRequest(target_symbol="SPY", frontend_confirmation=False),
            )

        blockers = " ".join(raised.exception.details["blockers"])
        self.assertIn("EXECUTION_ADAPTER", blockers)
        self.assertIn("confirmed", blockers)

    def test_live_intent_creation_creates_pending_intent_without_submission(self) -> None:
        from backend.schemas import AiLiveIntentRequest
        from backend.services import ai_desk_manager_service as service

        account = self._live_account()
        self._manual_target_run(desk_key="macro_trend", symbol="SPY")
        self._paper_evidence_run()
        preview_payload = {
            "preview": True,
            "blocked": False,
            "route_eligibility": {"allowed": True, "warnings": []},
        }
        intent_payload = {
            "trade_intent": {
                "id": "intent-live-1",
                "ticker": "SPY",
                "status": "pending_approval",
                "linked_account_id": account.id,
            }
        }
        with (
            patch(
                "backend.services.ai_desk_manager_service.settings",
                SimpleNamespace(execution_adapter="alpaca_live", alpaca_live_trading_enabled=True),
            ),
            patch("backend.services.ai_desk_manager_service.preview_trade_from_request", return_value=preview_payload),
            patch("backend.services.ai_desk_manager_service.create_trade_intent_from_request", return_value=intent_payload) as creator,
        ):
            payload = service.create_ai_live_intent(
                self.db,
                current_user=self.current_user,
                request=AiLiveIntentRequest(
                    target_symbol="SPY",
                    linked_account_id=account.id,
                    frontend_confirmation=True,
                    live_price=100.0,
                ),
            )

        self.assertTrue(payload["created"])
        self.assertEqual(payload["trade_intent"]["status"], "pending_approval")
        creator.assert_called_once()

    def test_policy_defaults_disabled_unarmed_and_live_submit_false(self) -> None:
        from backend.services.ai_desk_manager_service import get_ai_desk_policy

        payload = get_ai_desk_policy(self.db, current_user=self.current_user)
        policy = payload["manifest"]

        self.assertFalse(policy["enabled"])
        self.assertFalse(policy["armed"])
        self.assertFalse(policy["kill_switch"])
        self.assertEqual(policy["autonomy_boundary"], "paper_plus_live_intent")
        self.assertEqual(policy["allowed_desks"], ["macro_trend", "stat_arb"])
        self.assertFalse(policy["allow_live_submit"])
        self.assertTrue(payload["policy_digest"])

    def test_policy_update_rejects_autonomous_live_submit(self) -> None:
        from pydantic import ValidationError as PydanticValidationError
        from backend.schemas import AiDeskPolicyUpdateRequest

        with self.assertRaises(PydanticValidationError):
            AiDeskPolicyUpdateRequest(allow_live_submit=True)

    def test_kill_switch_blocks_autonomous_cycle(self) -> None:
        from backend.schemas import AiAutonomousCycleRequest, AiDeskControlRequest
        from backend.services.ai_desk_manager_service import run_ai_autonomous_cycle, run_ai_desk_control

        run_ai_desk_control(self.db, current_user=self.current_user, request=AiDeskControlRequest(action="arm"))
        run_ai_desk_control(self.db, current_user=self.current_user, request=AiDeskControlRequest(action="kill_switch"))

        result = run_ai_autonomous_cycle(
            self.db,
            current_user=self.current_user,
            request=AiAutonomousCycleRequest(trigger="manual"),
        )

        self.assertEqual(result["status"], "blocked")
        self.assertIn("kill switch", " ".join(result["blockers"]).lower())

    def test_duplicate_autonomous_cycle_queue_is_blocked(self) -> None:
        from backend.schemas import AiAutonomousCycleRequest, AiDeskControlRequest
        from backend.services.ai_desk_manager_service import queue_ai_autonomous_cycle, run_ai_desk_control

        run_ai_desk_control(self.db, current_user=self.current_user, request=AiDeskControlRequest(action="arm"))
        first = queue_ai_autonomous_cycle(
            self.db,
            current_user=self.current_user,
            request=AiAutonomousCycleRequest(trigger="manual", enqueue=True),
        )
        second = queue_ai_autonomous_cycle(
            self.db,
            current_user=self.current_user,
            request=AiAutonomousCycleRequest(trigger="manual", enqueue=True),
        )

        self.assertTrue(first["queued"])
        self.assertFalse(second["queued"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["job_id"], first["job_id"])

    def test_ai_desk_job_queue_dispatches_autonomous_cycle_handler(self) -> None:
        from datetime import timedelta

        from backend.models.saas import AsyncJob
        from backend.schemas import AiAutonomousCycleRequest, AiDeskControlRequest
        from backend.services import ai_desk_manager_service, job_queue_service
        from backend.services.ai_desk_manager_service import queue_ai_autonomous_cycle, run_ai_desk_control

        run_ai_desk_control(self.db, current_user=self.current_user, request=AiDeskControlRequest(action="arm"))
        queued = queue_ai_autonomous_cycle(
            self.db,
            current_user=self.current_user,
            request=AiAutonomousCycleRequest(trigger="manual", enqueue=True),
        )
        job = self.db.get(AsyncJob, queued["job_id"])
        job.available_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()

        with patch.object(
            ai_desk_manager_service,
            "run_ai_autonomous_cycle",
            return_value={
                "cycle_id": "cycle-1",
                "status": "completed",
                "trigger": "scheduled",
                "dry_run": False,
                "policy_digest": queued["policy_digest"],
                "decisions": [],
                "agents": [],
                "actions": [],
                "blockers": [],
            },
        ) as runner:
            summary = job_queue_service.run_due_jobs(self.db, limit=1)

        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(self.db.get(AsyncJob, queued["job_id"]).status, "succeeded")
        runner.assert_called_once()

    def test_autonomous_cycle_filters_to_policy_allowed_desks(self) -> None:
        from backend.schemas import AiAutonomousCycleRequest, AiDeskControlRequest, AiDeskPolicyUpdateRequest
        from backend.services.ai_desk_manager_service import run_ai_autonomous_cycle, run_ai_desk_control, update_ai_desk_policy
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.ensure_strategy_desks(self.db, current_user=self.current_user)
        update_ai_desk_policy(
            self.db,
            current_user=self.current_user,
            request=AiDeskPolicyUpdateRequest(allowed_desks=["macro_trend"]),
        )
        run_ai_desk_control(self.db, current_user=self.current_user, request=AiDeskControlRequest(action="arm"))

        result = run_ai_autonomous_cycle(
            self.db,
            current_user=self.current_user,
            request=AiAutonomousCycleRequest(trigger="manual", dry_run=True),
        )
        market_agent = next(item for item in result["agents"] if item["key"] == "market_data_sentinel")

        self.assertIn("macro_trend", market_agent["evidence"]["executable_desks"])
        self.assertNotIn("event_driven", market_agent["evidence"]["executable_desks"])

    def test_autonomous_cycle_paper_execution_hands_off_with_policy(self) -> None:
        from backend.schemas import AiAutonomousCycleRequest, AiDeskControlRequest, AiDeskPolicyUpdateRequest
        from backend.services.ai_desk_manager_service import run_ai_autonomous_cycle, run_ai_desk_control, update_ai_desk_policy
        from backend.services.strategy_engine import service as strategy_service

        strategy_service.ensure_strategy_desks(self.db, current_user=self.current_user)
        run = self._manual_target_run(desk_key="macro_trend", symbol="SPY")
        ready_desks = {
            "items": [
                {
                    "desk_key": "macro_trend",
                    "name": "Macro Trend Desk",
                    "enabled": True,
                    "paper_trading_enabled": True,
                    "trading_mode": "paper",
                    "lifecycle_stage": "paper",
                    "latest_run": {"id": "run-macro", "status": "accepted", "completed_at": datetime.now(timezone.utc).isoformat()},
                    "latest_publication": {"targets": [{"symbol": "SPY"}]},
                }
            ]
        }
        update_ai_desk_policy(
            self.db,
            current_user=self.current_user,
            request=AiDeskPolicyUpdateRequest(allowed_desks=["macro_trend"]),
        )
        run_ai_desk_control(self.db, current_user=self.current_user, request=AiDeskControlRequest(action="arm"))

        with (
            patch("backend.services.ai_desk_manager_service.list_strategy_desks", return_value=ready_desks),
            patch("backend.services.ai_desk_manager_service.run_strategy_desk", return_value={"desk_key": "macro_trend", "status": "accepted"}),
            patch("backend.services.ai_desk_manager_service.build_allocator_snapshot", return_value={"status": "accepted"}),
            patch(
                "backend.services.ai_desk_manager_service.build_ai_trade_plan",
                return_value={
                    "allowed": True,
                    "blocked": False,
                    "blockers": [],
                    "warnings": [],
                    "open_trade_request": {
                        "ticker": "SPY",
                        "instrument_type": "equity",
                        "broker_side": "buy",
                        "order_type": "limit",
                        "risk_percent": 0.5,
                        "equities_only": True,
                        "long_only": True,
                        "limit_orders_only": True,
                    },
                    "preview": {},
                    "target": {},
                },
            ),
            patch(
                "backend.services.ai_desk_manager_service.execute_ai_paper_execution",
                return_value={"latest_execution_run_id": "paper-1", "portfolio_target_run_id": run.id, "status": "queued"},
            ) as executor,
        ):
            result = run_ai_autonomous_cycle(
                self.db,
                current_user=self.current_user,
                request=AiAutonomousCycleRequest(trigger="manual"),
            )

        self.assertIn(result["status"], {"completed", "blocked"})
        executor.assert_called_once()
        self.assertEqual(executor.call_args.kwargs["request"].portfolio_target_run_id, run.id)

    def test_policy_digest_can_be_stamped_on_paper_execution_metadata(self) -> None:
        from backend.services import ai_desk_manager_service as service

        run = self._paper_evidence_run()
        service._attach_policy_metadata_to_execution(
            self.db,
            execution_run_id=run.id,
            policy_digest="digest-123",
            cycle_id="cycle-123",
        )
        self.db.commit()
        self.db.refresh(run)

        self.assertEqual(run.metadata_json["ai_desk_policy_digest"], "digest-123")
        self.assertEqual(run.metadata_json["ai_desk_cycle_id"], "cycle-123")


if __name__ == "__main__":
    unittest.main()
