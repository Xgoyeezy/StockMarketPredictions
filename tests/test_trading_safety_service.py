from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.productized_control_plane_test_support import build_test_client, build_test_context, clear_test_overrides

from backend.services.trading_safety_service import (
    SAFETY_STATE_BLOCKED,
    SAFETY_STATE_DEGRADED,
    SAFETY_STATE_KILLED,
    SAFETY_STATE_READY,
    SAFETY_EVENT_TYPES,
    append_trading_safety_ledger_event,
    build_hft_watchdog_latest,
    build_trade_automation_safety_state,
    build_trading_safety_daily_summary,
    compact_trading_safety_ledger,
    evaluate_trade_automation_preflight_gate,
    read_trading_safety_ledger,
    read_last_known_safety_state,
    safety_state_severity,
    write_safety_state_snapshot,
)


class TradingSafetyServiceTests(unittest.TestCase):
    def _settings(self) -> dict:
        return {
            "enabled": True,
            "armed": True,
            "kill_switch": False,
            "max_open_positions": 5,
        }

    def _session(self) -> dict:
        return {"phase": "regular", "session_mode": "regular", "new_entries_allowed": True}

    def test_preflight_ready_for_alpaca_paper_route(self) -> None:
        gate = evaluate_trade_automation_preflight_gate(
            settings_state=self._settings(),
            runtime_state={},
            session=self._session(),
            session_profile=SimpleNamespace(equity_entries_allowed=True),
            execution_intent="broker_paper",
            pending_order_count=0,
            open_position_count=1,
        )

        self.assertEqual(gate["status"], SAFETY_STATE_READY)
        self.assertTrue(gate["safe_to_trade"])
        self.assertEqual(gate["reason"], "preflight_ready")

    def test_preflight_blocks_non_paper_route(self) -> None:
        gate = evaluate_trade_automation_preflight_gate(
            settings_state=self._settings(),
            runtime_state={},
            session=self._session(),
            session_profile=SimpleNamespace(equity_entries_allowed=True),
            execution_intent="broker_live",
        )

        self.assertEqual(gate["status"], SAFETY_STATE_BLOCKED)
        self.assertFalse(gate["safe_to_trade"])
        self.assertEqual(gate["reason"], "non_alpaca_paper_route")

    def test_preflight_killed_when_kill_switch_active(self) -> None:
        settings = self._settings()
        settings["kill_switch"] = True

        gate = evaluate_trade_automation_preflight_gate(
            settings_state=settings,
            runtime_state={},
            session=self._session(),
            session_profile=SimpleNamespace(equity_entries_allowed=True),
            execution_intent="broker_paper",
        )

        self.assertEqual(gate["status"], SAFETY_STATE_KILLED)
        self.assertEqual(gate["reason"], "kill_switch_active")

    def test_preflight_blocks_collective_target_lock(self) -> None:
        gate = evaluate_trade_automation_preflight_gate(
            settings_state=self._settings(),
            runtime_state={"daily_objective_last_report": {"target_reached": True, "entry_block_reason": "target_reached_protect_streak"}},
            session=self._session(),
            session_profile=SimpleNamespace(equity_entries_allowed=True),
            execution_intent="broker_paper",
        )

        self.assertEqual(gate["status"], SAFETY_STATE_BLOCKED)
        self.assertEqual(gate["reason"], "objective_protect_streak")

    def test_safety_state_degrades_when_worker_is_stale_during_market_session(self) -> None:
        snapshot = {
            "tenant": {"slug": "unit-test"},
            "settings": {
                "enabled": True,
                "armed": True,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "max_open_positions": 5,
                "account_size": 100000,
            },
            "runtime": {},
            "session": {"phase": "regular_session", "session_mode": "regular", "new_entries_allowed": True},
            "counts": {"pending_orders": 0, "open_positions": 0},
            "broker_routes": {"broker_paper": {"connected": True, "detail": "Alpaca paper execution is connected."}},
        }
        desks = {
            "items": [
                {
                    "desk_key": "fast_scalper",
                    "label": "Fast Scalper",
                    "enabled": True,
                    "armed": True,
                    "runtime": {"due": True},
                    "top_blocker": None,
                }
            ]
        }
        worker = {
            "enabled": True,
            "running": True,
            "status": "running_but_stale",
            "stale": True,
            "current_stage": "trade_automation_cycles",
        }

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "backend.services.trade_automation_service.get_tenant_trade_automation_snapshot",
                    return_value=snapshot,
                ),
                patch("backend.services.trade_automation_service.list_tenant_trade_automation_desks", return_value=desks),
                patch("backend.services.job_queue_service.get_job_worker_status", return_value=worker),
            ):
                payload = build_trade_automation_safety_state(
                    SimpleNamespace(),
                    current_user=SimpleNamespace(user_id="unit-test"),
                    ledger_dir=Path(tmp),
                )

        self.assertEqual(payload["status"], SAFETY_STATE_DEGRADED)
        self.assertEqual(payload["blocker"], "The automation worker is running but stale.")
        self.assertIn("Restart the backend worker/runtime", payload["next_action"])
        self.assertTrue(payload["worker"]["stale"])

    def test_safety_state_blocks_when_loss_containment_blocks_entries(self) -> None:
        snapshot = {
            "tenant": {"slug": "unit-test"},
            "settings": {
                "enabled": True,
                "armed": True,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "max_open_positions": 5,
                "account_size": 100000,
            },
            "runtime": {},
            "session": {"phase": "regular_session", "session_mode": "regular", "new_entries_allowed": True},
            "counts": {"pending_orders": 0, "open_positions": 1},
            "broker_routes": {"broker_paper": {"connected": True, "detail": "Alpaca paper execution is connected."}},
            "loss_containment": {
                "status": "blocked",
                "entries_blocked": True,
                "blockers": [{"key": "stale_quote", "detail": "AMZN quote age is stale."}],
            },
        }
        desks = {
            "items": [
                {
                    "desk_key": "fast_scalper",
                    "label": "Fast Scalper",
                    "enabled": True,
                    "armed": True,
                    "runtime": {"due": False},
                    "top_blocker": None,
                }
            ]
        }
        worker = {"enabled": True, "running": True, "status": "running", "stale": False}

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "backend.services.trade_automation_service.get_tenant_trade_automation_snapshot",
                    return_value=snapshot,
                ),
                patch("backend.services.trade_automation_service.list_tenant_trade_automation_desks", return_value=desks),
                patch("backend.services.job_queue_service.get_job_worker_status", return_value=worker),
            ):
                payload = build_trade_automation_safety_state(
                    SimpleNamespace(),
                    current_user=SimpleNamespace(user_id="unit-test"),
                    ledger_dir=Path(tmp),
                )

        self.assertEqual(payload["status"], SAFETY_STATE_BLOCKED)
        self.assertEqual(payload["blocker"], "AMZN quote age is stale.")
        self.assertIn("loss_containment_block", payload["degraded_reasons"])
        self.assertTrue(payload["loss_containment"]["entries_blocked"])

    def test_kill_switch_state_surfaces_loss_containment_cause(self) -> None:
        snapshot = {
            "tenant": {"slug": "unit-test"},
            "settings": {
                "enabled": True,
                "armed": False,
                "kill_switch": True,
                "execution_intent": "broker_paper",
                "max_open_positions": 5,
                "account_size": 100000,
            },
            "runtime": {
                "state_control_last_transition": {
                    "from": "watch",
                    "to": "halt",
                    "reason": "loss_containment_breach",
                    "detail": "Loss containment found defensive-exit risk.",
                }
            },
            "session": {"phase": "regular_session", "session_mode": "regular", "new_entries_allowed": True},
            "counts": {"pending_orders": 0, "open_positions": 1},
            "broker_routes": {"broker_paper": {"connected": True, "detail": "Alpaca paper execution is connected."}},
            "loss_containment": {
                "status": "action_required",
                "entries_blocked": True,
                "blockers": [
                    {
                        "key": "position_mae_breach",
                        "detail": "ORCL adverse excursion is 0.42%, beyond the configured limit.",
                    }
                ],
                "defensive_actions": [{"ticker": "ORCL", "action": "EXIT FULLY NOW"}],
            },
        }
        desks = {"items": []}
        worker = {"enabled": True, "running": True, "status": "running", "stale": False}

        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch(
                    "backend.services.trade_automation_service.get_tenant_trade_automation_snapshot",
                    return_value=snapshot,
                ),
                patch("backend.services.trade_automation_service.list_tenant_trade_automation_desks", return_value=desks),
                patch("backend.services.job_queue_service.get_job_worker_status", return_value=worker),
            ):
                payload = build_trade_automation_safety_state(
                    SimpleNamespace(),
                    current_user=SimpleNamespace(user_id="unit-test"),
                    ledger_dir=Path(tmp),
                )

        self.assertEqual(payload["status"], SAFETY_STATE_KILLED)
        self.assertIn("ORCL adverse excursion", payload["blocker"])
        self.assertEqual(payload["kill_switch_context"]["source"], "loss_containment")
        self.assertIn("kill_switch_loss_containment", payload["degraded_reasons"])

    def test_ledger_append_and_read_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc)
            append_trading_safety_ledger_event(
                "test.event",
                status="ready",
                message="preflight ok",
                tenant_slug="unit-test",
                metadata={"route": "broker_paper"},
                ledger_dir=Path(tmp),
                now=now,
            )

            payload = read_trading_safety_ledger(ledger_dir=Path(tmp), day="2026-04-30", limit=10)

        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["event_type"], "test.event")
        self.assertEqual(payload["items"][0]["metadata"]["route"], "broker_paper")

    def test_ledger_filtering_and_cursor_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp)
            for index, status in enumerate([SAFETY_STATE_READY, SAFETY_STATE_BLOCKED, SAFETY_STATE_BLOCKED], start=1):
                append_trading_safety_ledger_event(
                    "test.cursor",
                    status=status,
                    message=f"event {index}",
                    ledger_dir=ledger_dir,
                    now=datetime(2026, 4, 30, 14, index, tzinfo=timezone.utc),
                )

            first = read_trading_safety_ledger(ledger_dir=ledger_dir, day="2026-04-30", limit=1, status=SAFETY_STATE_BLOCKED)
            second = read_trading_safety_ledger(
                ledger_dir=ledger_dir,
                day="2026-04-30",
                limit=1,
                status=SAFETY_STATE_BLOCKED,
                cursor=first["next_cursor"],
            )

        self.assertEqual(first["filtered_count"], 2)
        self.assertEqual(first["items"][0]["message"], "event 3")
        self.assertEqual(second["items"][0]["message"], "event 2")

    def test_daily_summary_reports_strongest_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp)
            append_trading_safety_ledger_event(
                "test.ready",
                status=SAFETY_STATE_READY,
                message="ready",
                ledger_dir=ledger_dir,
                now=datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc),
            )
            append_trading_safety_ledger_event(
                "test.blocked",
                status=SAFETY_STATE_BLOCKED,
                message="blocked",
                ledger_dir=ledger_dir,
                now=datetime(2026, 4, 30, 14, 1, tzinfo=timezone.utc),
            )

            summary = build_trading_safety_daily_summary(ledger_dir=ledger_dir, day="2026-04-30")

        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["strongest_status"], SAFETY_STATE_BLOCKED)
        self.assertGreater(safety_state_severity(SAFETY_STATE_BLOCKED), safety_state_severity(SAFETY_STATE_DEGRADED))

    def test_compaction_writes_non_destructive_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp)
            append_trading_safety_ledger_event(
                "test.compact",
                status=SAFETY_STATE_READY,
                message="ready",
                ledger_dir=ledger_dir,
                now=datetime(2026, 4, 30, 14, 0, tzinfo=timezone.utc),
            )

            payload = compact_trading_safety_ledger(ledger_dir=ledger_dir, day="2026-04-30")
            summary_exists = Path(payload["summary_path"]).exists()

        self.assertTrue(payload["source_preserved"])
        self.assertTrue(summary_exists)

    def test_safety_state_snapshot_records_state_change_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger_dir = Path(tmp)
            first = write_safety_state_snapshot(
                {"status": SAFETY_STATE_READY, "blocker": None, "checked_at": "2026-04-30T14:00:00+00:00"},
                ledger_dir=ledger_dir,
            )
            second = write_safety_state_snapshot(
                {"status": SAFETY_STATE_BLOCKED, "blocker": "blocked", "checked_at": "2026-04-30T14:01:00+00:00"},
                ledger_dir=ledger_dir,
            )
            latest = read_last_known_safety_state(ledger_dir=ledger_dir)
            ledger = read_trading_safety_ledger(ledger_dir=ledger_dir, day="2026-04-30", event_type="trading_safety.state_changed")

        self.assertTrue(first["state_changed"])
        self.assertTrue(second["state_changed"])
        self.assertEqual(second["previous_status"], SAFETY_STATE_READY)
        self.assertEqual(latest["status"], SAFETY_STATE_BLOCKED)
        self.assertEqual(ledger["filtered_count"], 1)

    def test_hft_watchdog_latest_reads_artifact_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            latest_dir = Path(tmp) / "millisecond_watchdog"
            latest_dir.mkdir(parents=True)
            (latest_dir / "latest.json").write_text(
                json.dumps({"run_id": "run-1", "status": "max_slices_reached", "metrics": {"decision_count": 2}}),
                encoding="utf-8",
            )

            payload = build_hft_watchdog_latest(base_dir=Path(tmp))

        self.assertTrue(payload["available"])
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["metrics"]["decision_count"], 2)

    def test_org_safety_state_route_returns_envelope(self) -> None:
        context = build_test_context(slug="trading-safety-route-test")
        client = build_test_client(context)
        self.addCleanup(clear_test_overrides)
        self.addCleanup(context.close)

        response = client.get("/api/orgs/trade-automation/safety-state")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn(payload["data"]["status"], {"ready", "degraded", "blocked", "killed"})
        self.assertEqual(payload["data"]["route"]["provider"], "alpaca")
        self.assertEqual(payload["data"]["route_enforcement"]["allowed_routes"], ["broker_paper"])
        self.assertTrue(payload["data"]["trade_proof"]["no_live_order_autonomy"])
        self.assertIn("account_safety", payload["data"])
        self.assertIn("objective_evidence", payload["data"])

    def test_safety_event_type_registry_covers_core_events(self) -> None:
        self.assertIn("trading_safety.preflight", SAFETY_EVENT_TYPES)
        self.assertIn("trading_safety.objective_lock", SAFETY_EVENT_TYPES)
        self.assertIn("trading_safety.loss_lock", SAFETY_EVENT_TYPES)


if __name__ == "__main__":
    unittest.main()
