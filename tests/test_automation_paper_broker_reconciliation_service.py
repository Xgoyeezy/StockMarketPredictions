from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.database import Base
from backend.models.saas import AuditEvent, OrderEventRecord, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import (
    automation_paper_broker_reconciliation_service,
    notes_service,
    trade_automation_service,
)


FIXED_NOW = datetime(2026, 4, 24, 20, 15, tzinfo=timezone.utc)
BEFORE_CLOSE_NOW = datetime(2026, 4, 24, 19, 30, tzinfo=timezone.utc)


class AutomationPaperBrokerReconciliationServiceTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="paper-broker-test", name="Paper Broker Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _state(self) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": True,
                "armed": True,
                "kill_switch": False,
                "execution_intent": "broker_paper",
            }
        )
        state["runtime"].update(
            {
                "current_route_reconciliation_status": "clean",
                "ledger_snapshot_consistency": "consistent",
            }
        )
        return state

    def _row(self, tenant: Tenant, **overrides) -> dict:
        row = {
            "order_id": "local-order-1",
            "trade_id": "trade-1",
            "ticker": "SPY",
            "broker_name": "alpaca_paper",
            "broker_order_id": "broker-order-1",
            "broker_client_order_id": "local-order-1",
            "suggested_contracts": 1,
            "quantity": 1,
            "automation_origin": "trade_automation",
            "automation_tenant_id": tenant.id,
            "automation_profile_key": "personal_paper",
        }
        row.update(overrides)
        return row

    def _broker_snapshot(self, *, orders=None, positions=None, available: bool = True) -> dict:
        return {
            "broker_available": available,
            "account": {"id": "paper-account", "equity": "10000"},
            "orders": list(orders or []),
            "positions": list(positions or []),
        }

    def _run_reconciliation(
        self,
        db,
        tenant: Tenant,
        state: dict,
        *,
        pending=None,
        open_rows=None,
        closed=None,
        broker_snapshot=None,
    ):
        pending = list(pending or [])
        open_rows = list(open_rows or [])
        closed = list(closed or [])
        broker_snapshot = broker_snapshot or self._broker_snapshot()
        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "read_pending_orders",
                    return_value=pd.DataFrame(pending),
                ),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "read_open_trades",
                    return_value=pd.DataFrame(open_rows),
                ),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "read_closed_trades",
                    return_value=pd.DataFrame(closed),
                ),
            ):
                report = automation_paper_broker_reconciliation_service.run_paper_broker_reconciliation(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=FIXED_NOW,
                    broker_snapshot=broker_snapshot,
                )
                db.commit()
                notes = json.loads(notes_path.read_text(encoding="utf-8"))
        return report, notes

    def test_action_schema_accepts_paper_broker_reconciliation(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_paper_broker_reconciliation")

        self.assertEqual(payload.action, "run_paper_broker_reconciliation")

    def test_clean_broker_local_state_reports_clean_and_writes_note(self) -> None:
        db, tenant = self._db()
        state = self._state()
        pending = [self._row(tenant)]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "new",
                }
            ]
        )

        report, notes = self._run_reconciliation(db, tenant, state, pending=pending, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(report["ledger_consistency"], "consistent")
        self.assertFalse(report["blockers"])
        broker_notes = [note for note in notes if "paper-broker" in note.get("tags", [])]
        self.assertEqual(len(broker_notes), 1)
        self.assertIn("Matched orders: 1", broker_notes[0]["body"])
        self.assertEqual(state["runtime"]["paper_broker_reconciliation_last_report"]["status"], "clean")
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.paper_broker_reconciled", audit_types)

    def test_broker_order_missing_locally_blocks(self) -> None:
        db, tenant = self._db()
        state = self._state()
        broker_snapshot = self._broker_snapshot(
            orders=[{"id": "broker-orphan", "client_order_id": "broker-orphan", "symbol": "SPY", "status": "new"}]
        )

        report, _notes = self._run_reconciliation(db, tenant, state, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["orphan_broker_order_count"], 1)
        self.assertIn("orphan_broker_order", {item["key"] for item in report["blockers"]})

    def test_local_pending_order_missing_at_broker_blocks(self) -> None:
        db, tenant = self._db()
        state = self._state()

        report, _notes = self._run_reconciliation(
            db,
            tenant,
            state,
            pending=[self._row(tenant)],
            broker_snapshot=self._broker_snapshot(orders=[]),
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["orphan_local_order_count"], 1)
        self.assertIn("orphan_local_order", {item["key"] for item in report["blockers"]})

    def test_broker_fill_without_local_open_or_closed_trade_blocks(self) -> None:
        db, tenant = self._db()
        state = self._state()
        pending = [self._row(tenant)]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "filled",
                }
            ]
        )

        report, _notes = self._run_reconciliation(db, tenant, state, pending=pending, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["fill_mismatch_count"], 1)
        self.assertIn("fill_mismatch", {item["key"] for item in report["blockers"]})

    def test_position_quantity_mismatch_blocks(self) -> None:
        db, tenant = self._db()
        state = self._state()
        open_rows = [self._row(tenant)]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "filled",
                }
            ],
            positions=[{"symbol": "SPY", "qty": "2"}],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, open_rows=open_rows, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["position_mismatch_count"], 1)
        self.assertIn("position_mismatch", {item["key"] for item in report["blockers"]})

    def test_missing_broker_snapshot_warns_without_synthetic_mismatch_blocker(self) -> None:
        db, tenant = self._db()
        state = self._state()
        open_rows = [self._row(tenant)]

        report, _notes = self._run_reconciliation(
            db,
            tenant,
            state,
            open_rows=open_rows,
            broker_snapshot=self._broker_snapshot(available=False),
        )

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["blockers"])
        self.assertEqual(report["position_mismatch_count"], 0)
        self.assertIn("broker_snapshot_unavailable", {item["key"] for item in report["warnings"]})

    def test_equity_snapshot_drift_is_advisory_warning(self) -> None:
        db, tenant = self._db()
        state = self._state()
        pending = [self._row(tenant)]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "new",
                }
            ]
        )

        with patch.object(
            automation_paper_broker_reconciliation_service.equity_snapshot_service,
            "get_latest_trade_automation_equity_snapshot",
            return_value={
                "snapshot_at": "2026-04-24T20:10:00+00:00",
                "cycle_at": "2026-04-24T20:05:00+00:00",
                "equity": 9200.0,
            },
        ):
            report, _notes = self._run_reconciliation(
                db,
                tenant,
                state,
                pending=pending,
                broker_snapshot=broker_snapshot,
            )

        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["equity_snapshot"]["status"], "drift")
        self.assertFalse(report["blockers"])
        self.assertIn("equity_snapshot_drift", {item["key"] for item in report["warnings"]})

    def test_action_path_persists_report_without_order_or_setting_mutation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        pending = [self._row(tenant)]
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="paper-broker",
            user_id="paper-broker",
            permissions=("tenant.manage_support",),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
                patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": {"allows_live_rollout": False}},
                ),
                patch.object(
                    automation_paper_broker_reconciliation_service,
                    "_fetch_broker_snapshot",
                    return_value=(
                        self._broker_snapshot(
                            orders=[
                                {
                                    "id": "broker-order-1",
                                    "client_order_id": "local-order-1",
                                    "symbol": "SPY",
                                    "status": "new",
                                }
                            ]
                        ),
                        [],
                    ),
                ),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame(pending)),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", return_value=pd.DataFrame()),
                patch.object(
                    trade_automation_service,
                    "_build_personal_account_summary",
                    return_value={
                        "provider": "alpaca_paper",
                        "label": "Paper account",
                        "connected": False,
                        "status": "unavailable",
                    },
                ),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
                patch.object(trade_automation_service, "open_trade_from_request") as open_trade_mock,
                patch.object(trade_automation_service, "sync_pending_orders_from_broker") as sync_orders_mock,
                patch.object(trade_automation_service, "_manage_automation_positions") as manage_positions_mock,
            ):
                snapshot = trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="run_paper_broker_reconciliation"),
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        open_trade_mock.assert_not_called()
        sync_orders_mock.assert_not_called()
        manage_positions_mock.assert_not_called()
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["paper_broker_reconciliation"]["status"], "clean")
        self.assertEqual(snapshot["paper_broker_reconciliation"]["matched_count"], 1)
        self.assertTrue(snapshot["paper_broker_reconciliation"]["related_note_id"])
        self.assertTrue(snapshot["available_actions"]["can_run_paper_broker_reconciliation"])
        broker_notes = [note for note in notes if "paper-broker" in note.get("tags", [])]
        self.assertEqual(len(broker_notes), 1)
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.paper_broker_reconciled", audit_types)

    def test_scheduled_reconciliation_runs_once_after_post_close_for_paper_profile(self) -> None:
        db, tenant = self._db()
        state = self._state()
        pending = [self._row(tenant)]
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(
                    automation_paper_broker_reconciliation_service,
                    "_fetch_broker_snapshot",
                    return_value=(
                        self._broker_snapshot(
                            orders=[
                                {
                                    "id": "broker-order-1",
                                    "client_order_id": "local-order-1",
                                    "symbol": "SPY",
                                    "status": "new",
                                }
                            ]
                        ),
                        [],
                    ),
                ),
                patch.object(trade_automation_service.sdm, "read_pending_orders", return_value=pd.DataFrame(pending)),
                patch.object(trade_automation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
                patch.object(trade_automation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            ):
                summary = trade_automation_service.run_trade_automation_paper_broker_reconciliations(
                    db,
                    now=FIXED_NOW,
                )
                second_summary = trade_automation_service.run_trade_automation_paper_broker_reconciliations(
                    db,
                    now=FIXED_NOW,
                )
                notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(summary["reviewed"], 1)
        self.assertEqual(summary["session_day"], "2026-04-24")
        self.assertEqual(second_summary["reviewed"], 0)
        self.assertEqual(second_summary["skipped"], 1)
        self.assertEqual(second_summary["items"][0]["reason"], "already_reconciled_for_session")
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        report = after_state["runtime"]["paper_broker_reconciliation_last_report"]
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["run_source"], "scheduled")
        self.assertEqual(after_state["runtime"]["paper_broker_reconciliation_last_scheduled_session_day"], "2026-04-24")
        broker_notes = [note for note in notes if "paper-broker" in note.get("tags", [])]
        self.assertEqual(len(broker_notes), 1)
        self.assertIn("Run source: scheduled", broker_notes[0]["body"])

    def test_scheduled_reconciliation_skips_before_close_buffer(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_broker_reconciliations(
            db,
            now=BEFORE_CLOSE_NOW,
        )

        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["skipped"], 1)
        self.assertEqual(summary["items"][0]["reason"], "review_window_not_open")
        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertFalse(after_state["runtime"].get("paper_broker_reconciliation_last_report"))

    def test_scheduled_reconciliation_ignores_live_profile(self) -> None:
        db, tenant = self._db()
        paper_state = self._state()
        paper_state["runtime"]["paper_broker_reconciliation_last_scheduled_session_day"] = "2026-04-24"
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        live_state = self._state()
        live_state["settings"]["execution_intent"] = "broker_live"
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()

        summary = trade_automation_service.run_trade_automation_paper_broker_reconciliations(
            db,
            now=FIXED_NOW,
        )

        self.assertEqual(summary["processed"], 1)
        self.assertEqual(summary["reviewed"], 0)
        self.assertEqual(summary["items"][0]["profile_key"], "personal_paper")
        live_after = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertFalse(live_after["runtime"].get("paper_broker_reconciliation_last_report"))


if __name__ == "__main__":
    unittest.main()
