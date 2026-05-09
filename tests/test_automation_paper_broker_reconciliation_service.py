from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from backend.services.exceptions import ValidationError


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
        repair_payload = OrganizationTradeAutomationActionRequest(action="reconcile_paper_orders")
        prepare_payload = OrganizationTradeAutomationActionRequest(action="reconcile_and_prepare_clear_kill_switch")

        self.assertEqual(payload.action, "run_paper_broker_reconciliation")
        self.assertEqual(repair_payload.action, "reconcile_paper_orders")
        self.assertEqual(prepare_payload.action, "reconcile_and_prepare_clear_kill_switch")

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

    def test_stale_runtime_orphan_recomputes_as_warning_when_fresh_state_is_clean(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["runtime"]["current_route_reconciliation_status"] = "orphaned"
        state["runtime"]["current_route_orphan_order_event_count"] = 9
        state["runtime"]["ledger_snapshot_consistency"] = "inconsistent"
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

        report, _notes = self._run_reconciliation(db, tenant, state, pending=pending, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["blockers"])
        self.assertIn("previous_current_route_reconciliation_fault", {item["key"] for item in report["warnings"]})
        self.assertEqual(report["current_route_reconciliation_status"], "clean")
        self.assertEqual(report["current_route_orphan_order_event_count"], 0)
        self.assertEqual(state["runtime"]["current_route_reconciliation_status"], "clean")
        self.assertEqual(state["runtime"]["current_route_orphan_order_event_count"], 0)
        self.assertEqual(state["runtime"]["ledger_snapshot_consistency"], "consistent")

    def test_repair_promotes_filled_pending_order_before_recomputing_reconciliation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        pending = [self._row(tenant)]
        open_row = self._row(
            tenant,
            order_status="FILLED",
            route_state="filled",
            book_state="open",
            status="OPEN",
            broker_filled_qty=1,
            broker_filled_avg_price=501.25,
            actual_fill_price=501.25,
        )
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "filled_avg_price": "501.25",
                }
            ],
            positions=[{"symbol": "SPY", "qty": "1"}],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            notes_path = Path(tmpdir) / "notes.json"
            notes_path.write_text("[]", encoding="utf-8")
            with (
                patch.object(notes_service, "NOTES_PATH", notes_path),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "read_pending_orders",
                    side_effect=[pd.DataFrame(pending), pd.DataFrame()],
                ),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "read_open_trades",
                    side_effect=[pd.DataFrame(), pd.DataFrame([open_row])],
                ),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "read_closed_trades",
                    return_value=pd.DataFrame(),
                ),
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "fill_pending_order",
                    return_value=open_row,
                ) as fill_mock,
                patch.object(
                    automation_paper_broker_reconciliation_service.sdm,
                    "update_open_trade",
                    return_value=open_row,
                ) as update_open_mock,
            ):
                report = automation_paper_broker_reconciliation_service.run_paper_broker_reconciliation(
                    db,
                    tenant=tenant,
                    state=state,
                    profile_key="personal_paper",
                    now=FIXED_NOW,
                    broker_snapshot=broker_snapshot,
                    repair_local_book=True,
                )

        fill_mock.assert_called_once_with("local-order-1", 501.25)
        update_open_mock.assert_called_once()
        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["repair_actions"][0]["type"], "promoted_pending_to_open")
        self.assertEqual(report["current_route_reconciliation_status"], "clean")
        self.assertEqual(report["current_route_orphan_order_event_count"], 0)

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

    def test_close_side_fill_matches_existing_closed_trade_evidence(self) -> None:
        db, tenant = self._db()
        state = self._state()
        closed = [
            self._row(
                tenant,
                status="CLOSED",
                closed_at="2026-04-24T20:14:50+00:00",
                closed_contracts=1,
                remaining_contracts_after_close=0,
            )
        ]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "updated_at": "2026-04-24T20:14:00+00:00",
                },
                {
                    "id": "broker-close-order-1",
                    "client_order_id": "local-close-order-1",
                    "symbol": "SPY",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "updated_at": "2026-04-24T20:14:55+00:00",
                },
            ],
            positions=[],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, closed=closed, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(report["fill_mismatch_count"], 0)
        self.assertEqual(report["inferred_close_match_count"], 1)

    def test_close_order_id_on_closed_trade_is_not_counted_as_orphan_fill(self) -> None:
        db, tenant = self._db()
        state = self._state()
        closed = [
            self._row(
                tenant,
                status="CLOSED",
                closed_at="2026-04-24T20:14:50+00:00",
                closed_contracts=1,
                remaining_contracts_after_close=0,
                broker_close_order_id="broker-close-order-1",
                broker_close_status="filled",
            )
        ]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "SPY",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "updated_at": "2026-04-24T20:14:00+00:00",
                },
                {
                    "id": "broker-close-order-1",
                    "client_order_id": "broker-generated-close-client-id",
                    "symbol": "SPY",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "updated_at": "2026-04-24T20:14:55+00:00",
                },
            ],
            positions=[],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, closed=closed, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(report["fill_mismatch_count"], 0)
        self.assertEqual(report["current_route_reconciliation_status"], "clean")
        self.assertEqual(report["current_route_orphan_order_event_count"], 0)

    def test_filled_close_order_linked_by_order_event_is_not_counted_as_orphan_fill(self) -> None:
        db, tenant = self._db()
        state = self._state()
        closed = [
            self._row(
                tenant,
                status="RECONCILED",
                closed_at="2026-04-24T19:25:00+00:00",
                closed_contracts=1,
                remaining_contracts_after_close=0,
                broker_close_order_id="older-close-order",
                broker_close_status="filled",
            )
        ]
        db.add(
            OrderEventRecord(
                tenant_id=tenant.id,
                trade_id="trade-1",
                ticker="SPY",
                event_key="order.close_working",
                status="working",
                route_state="close_working",
                book_state="open",
                detail="Broker-paper close order is working.",
                payload_json={
                    "automation_cycle_id": "cycle-1",
                    "automation_profile_key": "personal_paper",
                    "broker_order_id": "broker-later-close-order",
                    "broker_status": "pending_new",
                    "trade": {
                        "trade_id": "trade-1",
                        "order_id": "local-order-1",
                        "ticker": "SPY",
                        "automation_origin": "trade_automation",
                        "automation_profile_key": "personal_paper",
                        "broker_close_order_id": "broker-later-close-order",
                        "broker_close_status": "pending_new",
                    },
                },
                created_at=FIXED_NOW - timedelta(minutes=5),
                updated_at=FIXED_NOW - timedelta(minutes=5),
            )
        )
        db.commit()
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-later-close-order",
                    "client_order_id": "broker-generated-close-client-id",
                    "symbol": "SPY",
                    "side": "sell",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "updated_at": "2026-04-24T20:10:00+00:00",
                }
            ],
            positions=[],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, closed=closed, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["matched_count"], 1)
        self.assertEqual(report["fill_mismatch_count"], 0)
        self.assertEqual(report["inferred_close_match_count"], 1)
        self.assertEqual(report["current_route_reconciliation_status"], "clean")
        self.assertEqual(report["current_route_orphan_order_event_count"], 0)

    def test_partial_close_fill_with_matching_broker_position_is_not_orphan_fill(self) -> None:
        db, tenant = self._db()
        state = self._state()
        open_row = self._row(
            tenant,
            ticker="AVGO",
            broker_order_id="broker-open-order-1",
            broker_client_order_id="local-open-order-1",
            order_id="local-open-order-1",
            status="OPEN",
            book_state="open",
            suggested_contracts=9,
            quantity=9,
            reconciliation_source="alpaca_paper_filled_close_order_deduped",
        )
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-open-order-1",
                    "client_order_id": "local-open-order-1",
                    "symbol": "AVGO",
                    "side": "buy",
                    "status": "filled",
                    "qty": "17",
                    "filled_qty": "17",
                    "updated_at": "2026-04-24T19:50:00+00:00",
                },
                {
                    "id": "broker-partial-close-1",
                    "client_order_id": "broker-partial-close-client-1",
                    "symbol": "AVGO",
                    "side": "sell",
                    "status": "filled",
                    "qty": "8",
                    "filled_qty": "8",
                    "filled_avg_price": "880.00",
                    "updated_at": "2026-04-24T20:14:00+00:00",
                },
            ],
            positions=[{"symbol": "AVGO", "qty": "9"}],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, open_rows=[open_row], broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["matched_count"], 2)
        self.assertEqual(report["fill_mismatch_count"], 0)
        self.assertEqual(report["inferred_close_match_count"], 1)
        self.assertEqual(report["current_route_reconciliation_status"], "clean")
        self.assertEqual(report["current_route_orphan_order_event_count"], 0)

    def test_flat_account_close_fragment_is_not_counted_as_orphan_fill(self) -> None:
        db, tenant = self._db()
        state = self._state()
        closed = [
            self._row(
                tenant,
                ticker="GOOGL",
                status="RECONCILED",
                closed_at="2026-04-24T20:02:00+00:00",
                closed_contracts=1,
                remaining_contracts_after_close=0,
                broker_close_order_id="older-close-order",
                broker_close_status="filled",
                reconciliation_source="alpaca_paper_flat_account",
            )
        ]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-final-close-order",
                    "client_order_id": "broker-final-close-client",
                    "symbol": "GOOGL",
                    "side": "sell",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "filled_avg_price": "384.95",
                    "updated_at": "2026-04-24T20:14:00+00:00",
                }
            ],
            positions=[],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, closed=closed, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "clean")
        self.assertEqual(report["fill_mismatch_count"], 0)
        self.assertEqual(report["inferred_close_match_count"], 1)
        self.assertEqual(report["current_route_reconciliation_status"], "clean")
        self.assertEqual(report["current_route_orphan_order_event_count"], 0)

    def test_flat_account_close_fragment_still_blocks_when_broker_position_remains_open(self) -> None:
        db, tenant = self._db()
        state = self._state()
        closed = [
            self._row(
                tenant,
                ticker="GOOGL",
                status="RECONCILED",
                closed_at="2026-04-24T20:02:00+00:00",
                closed_contracts=1,
                remaining_contracts_after_close=0,
                broker_close_order_id="older-close-order",
                broker_close_status="filled",
                reconciliation_source="alpaca_paper_flat_account",
            )
        ]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-final-close-order",
                    "client_order_id": "broker-final-close-client",
                    "symbol": "GOOGL",
                    "side": "sell",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                    "updated_at": "2026-04-24T20:14:00+00:00",
                }
            ],
            positions=[{"symbol": "GOOGL", "qty": "1"}],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, closed=closed, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["fill_mismatch_count"], 1)

    def test_working_close_order_restores_local_open_position_before_compare(self) -> None:
        _db, tenant = self._db()
        closed = [
            self._row(
                tenant,
                status="CLOSED",
                closed_at="2026-04-24T20:14:50+00:00",
                closed_contracts=3,
                remaining_contracts_after_close=0,
                suggested_contracts=3,
                broker_close_order_id="broker-close-order-1",
                broker_close_status="accepted",
            )
        ]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-close-order-1",
                    "client_order_id": "broker-generated-close-client-id",
                    "symbol": "SPY",
                    "side": "sell",
                    "status": "accepted",
                    "qty": "3",
                    "filled_qty": "0",
                }
            ],
            positions=[{"symbol": "SPY", "qty": "3"}],
        )

        writes: list[tuple[object, pd.DataFrame]] = []

        def capture_write(path, frame):
            writes.append((path, frame.copy()))

        with (
            patch.object(automation_paper_broker_reconciliation_service.sdm, "read_open_trades", return_value=pd.DataFrame()),
            patch.object(automation_paper_broker_reconciliation_service.sdm, "read_closed_trades", return_value=pd.DataFrame(closed)),
            patch.object(automation_paper_broker_reconciliation_service.sdm, "write_dataframe_csv", side_effect=capture_write),
            patch.object(automation_paper_broker_reconciliation_service.sdm, "_invalidate_file_read_cache"),
        ):
            actions = automation_paper_broker_reconciliation_service._restore_working_close_attempts(
                tenant=tenant,
                profile_key="personal_paper",
                broker_snapshot=broker_snapshot,
            )

        self.assertEqual(actions[0]["type"], "restored_working_close_to_open")
        self.assertEqual(len(writes), 2)
        restored_open = writes[0][1].iloc[0].to_dict()
        remaining_closed = writes[1][1]
        self.assertEqual(restored_open["status"], "OPEN")
        self.assertEqual(restored_open["route_state"], "close_working")
        self.assertEqual(float(restored_open["suggested_contracts"]), 3.0)
        self.assertEqual(restored_open["broker_close_order_id"], "broker-close-order-1")
        self.assertTrue(remaining_closed.empty)

    def test_repair_closes_open_trade_when_broker_close_order_is_filled(self) -> None:
        _db, tenant = self._db()
        open_row = self._row(
            tenant,
            ticker="GOOGL",
            instrument_type="equity",
            suggested_contracts=19,
            quantity=19,
            live_price_at_open=382.25,
            broker_close_order_id="broker-close-order-1",
            broker_close_status="accepted",
        )
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-close-order-1",
                    "client_order_id": "broker-generated-close-client-id",
                    "symbol": "GOOGL",
                    "side": "sell",
                    "status": "filled",
                    "qty": "19",
                    "filled_qty": "19",
                    "filled_avg_price": "383.50",
                    "updated_at": "2026-04-24T20:14:55+00:00",
                }
            ],
            positions=[],
        )

        with (
            patch.object(automation_paper_broker_reconciliation_service.sdm, "read_pending_orders", return_value=pd.DataFrame()),
            patch.object(
                automation_paper_broker_reconciliation_service.sdm,
                "read_open_trades",
                side_effect=[pd.DataFrame([open_row]), pd.DataFrame([open_row]), pd.DataFrame()],
            ),
            patch.object(automation_paper_broker_reconciliation_service.sdm, "read_closed_trades", return_value=pd.DataFrame()),
            patch.object(
                automation_paper_broker_reconciliation_service.sdm,
                "close_trade_by_index",
                return_value={"trade_id": "trade-1", "status": "CLOSED"},
            ) as close_mock,
        ):
            actions = automation_paper_broker_reconciliation_service._repair_local_paper_books(
                tenant=tenant,
                profile_key="personal_paper",
                broker_snapshot=broker_snapshot,
            )

        self.assertEqual(actions[0]["type"], "closed_open_from_filled_close_order")
        self.assertEqual(actions[0]["broker_close_order_id"], "broker-close-order-1")
        close_mock.assert_called_once()
        _index_arg, kwargs = close_mock.call_args
        self.assertEqual(_index_arg[0], 0)
        self.assertEqual(kwargs["close_underlying_price"], 383.50)
        self.assertEqual(kwargs["close_fraction"], 1.0)
        self.assertEqual(kwargs["close_updates"]["broker_close_status"], "filled")

    def test_filled_close_order_already_recorded_clears_remaining_open_reference(self) -> None:
        _db, tenant = self._db()
        open_row = self._row(
            tenant,
            ticker="GOOGL",
            instrument_type="equity",
            suggested_contracts=5,
            quantity=19,
            broker_close_order_id="broker-close-order-1",
            broker_close_status="accepted",
        )
        closed_row = self._row(
            tenant,
            ticker="GOOGL",
            status="PARTIAL",
            closed_contracts=14,
            remaining_contracts_after_close=5,
            broker_close_order_id="broker-close-order-1",
            broker_close_status="filled",
        )
        close_order = {
            "id": "broker-close-order-1",
            "client_order_id": "broker-generated-close-client-id",
            "symbol": "GOOGL",
            "status": "filled",
            "qty": "14",
            "filled_qty": "14",
            "filled_avg_price": "383.50",
        }

        with (
            patch.object(automation_paper_broker_reconciliation_service.sdm, "read_open_trades", return_value=pd.DataFrame([open_row])),
            patch.object(automation_paper_broker_reconciliation_service.sdm, "read_closed_trades", return_value=pd.DataFrame([closed_row])),
            patch.object(
                automation_paper_broker_reconciliation_service.sdm,
                "update_open_trade",
                return_value={**open_row, "broker_close_order_id": ""},
            ) as update_mock,
            patch.object(automation_paper_broker_reconciliation_service.sdm, "close_trade_by_index") as close_mock,
        ):
            action = automation_paper_broker_reconciliation_service._close_open_trade_from_filled_close_order(close_order)

        self.assertEqual(action["type"], "cleared_stale_filled_close_reference")
        close_mock.assert_not_called()
        update_mock.assert_called_once()
        self.assertEqual(update_mock.call_args.args[0]["broker_close_order_id"], "")
        self.assertEqual(update_mock.call_args.args[0]["reconciliation_source"], "alpaca_paper_filled_close_order_deduped")

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

    def test_known_readiness_probe_order_is_excluded_from_automation_route_reconciliation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        open_rows = [self._row(tenant, ticker="AAPL", suggested_contracts=27, quantity=27, broker_filled_qty=27)]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "AAPL",
                    "status": "filled",
                    "qty": "27",
                    "filled_qty": "27",
                },
                {
                    "id": "readiness-probe-order",
                    "client_order_id": "codex-alpaca-paper-e2e-123",
                    "symbol": "AAPL",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                },
            ],
            positions=[{"symbol": "AAPL", "qty": "28"}],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, open_rows=open_rows, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["position_mismatch_count"], 0)
        self.assertFalse(report["blockers"])
        self.assertIn("external_readiness_probe_orders", {item["key"] for item in report["warnings"]})
        self.assertEqual(report["current_route_reconciliation_status"], "clean")

    def test_stale_readiness_probe_does_not_create_false_position_mismatch_when_raw_broker_matches_local(self) -> None:
        db, tenant = self._db()
        state = self._state()
        open_rows = [self._row(tenant, ticker="AAPL", suggested_contracts=9, quantity=9, broker_filled_qty=9)]
        broker_snapshot = self._broker_snapshot(
            orders=[
                {
                    "id": "broker-order-1",
                    "client_order_id": "local-order-1",
                    "symbol": "AAPL",
                    "status": "filled",
                    "qty": "9",
                    "filled_qty": "9",
                },
                {
                    "id": "old-readiness-probe-order",
                    "client_order_id": "codex-alpaca-paper-e2e-123",
                    "symbol": "AAPL",
                    "status": "filled",
                    "qty": "1",
                    "filled_qty": "1",
                },
            ],
            positions=[{"symbol": "AAPL", "qty": "9"}],
        )

        report, _notes = self._run_reconciliation(db, tenant, state, open_rows=open_rows, broker_snapshot=broker_snapshot)

        self.assertEqual(report["status"], "warning")
        self.assertEqual(report["position_mismatch_count"], 0)
        self.assertFalse(report["blockers"])
        self.assertIn("external_readiness_probe_orders", {item["key"] for item in report["warnings"]})
        self.assertEqual(report["current_route_reconciliation_status"], "clean")

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

    def test_clear_kill_switch_rejects_when_reconciliation_remains_orphaned(self) -> None:
        db, tenant = self._db()
        state = self._state()
        state["settings"]["kill_switch"] = True
        state["settings"]["armed"] = False
        state["runtime"]["current_route_reconciliation_status"] = "orphaned"
        state["runtime"]["current_route_orphan_order_event_count"] = 9
        state["runtime"]["ledger_snapshot_consistency"] = "inconsistent"
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="paper-broker",
            user_id="paper-broker",
            permissions=("tenant.manage_support",),
        )

        with (
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
        ):
            with self.assertRaises(ValidationError):
                trade_automation_service.run_tenant_trade_automation_action(
                    db,
                    current_user=current_user,
                    request=OrganizationTradeAutomationActionRequest(action="clear_kill_switch"),
                )

        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertTrue(after_state["settings"]["kill_switch"])

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
