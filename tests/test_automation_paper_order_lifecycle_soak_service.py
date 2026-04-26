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
from backend.models.saas import AuditEvent, Tenant
from backend.schemas import OrganizationTradeAutomationActionRequest
from backend.services import (
    automation_paper_broker_reconciliation_service,
    automation_paper_order_lifecycle_soak_service,
    notes_service,
    trade_automation_service,
)
from backend.services.exceptions import ValidationServiceError
from backend.services.execution.types import CancelOrderResult, ClosePositionResult, SubmitOrderResult


FIXED_NOW = datetime(2026, 4, 24, 20, 25, tzinfo=timezone.utc)


class _Ledger:
    def __init__(self) -> None:
        self.pending: list[dict] = []
        self.open: list[dict] = []
        self.closed: list[dict] = []

    def frame(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def read_pending(self) -> pd.DataFrame:
        return self.frame(self.pending)

    def read_open(self) -> pd.DataFrame:
        return self.frame(self.open)

    def read_closed(self) -> pd.DataFrame:
        return self.frame(self.closed)

    def update_pending(self, order_id: str, updates: dict) -> dict | None:
        for row in self.pending:
            if str(row.get("order_id") or "") == str(order_id or ""):
                row.update(dict(updates))
                return dict(row)
        return None

    def cancel_pending(self, order_id: str) -> dict | None:
        for index, row in enumerate(list(self.pending)):
            if str(row.get("order_id") or "") == str(order_id or ""):
                return dict(self.pending.pop(index))
        return None

    def update_open(self, updates: dict, *, trade_id: str | None = None, order_id: str | None = None) -> dict | None:
        for row in self.open:
            if (trade_id and str(row.get("trade_id") or "") == str(trade_id)) or (
                order_id and str(row.get("order_id") or "") == str(order_id)
            ):
                row.update(dict(updates))
                return dict(row)
        return None

    def close_trade_by_index(self, trade_index: int, close_underlying_price: float, close_contract_mid: float, close_fraction: float = 1.0, close_updates: dict | None = None) -> dict | None:
        if trade_index < 0 or trade_index >= len(self.open):
            return None
        row = dict(self.open.pop(trade_index))
        row.update(
            {
                "closed_at": FIXED_NOW.isoformat(),
                "live_price_at_close": close_underlying_price,
                "contract_mid_at_close": close_contract_mid,
                "close_fraction": close_fraction,
                "status": "CLOSED",
                **dict(close_updates or {}),
            }
        )
        self.closed.append(row)
        return dict(row)


class _FakePaperAdapter:
    adapter_name = "alpaca_paper"

    def __init__(self, ledger: _Ledger, *, mode: str = "working", credential_error: Exception | None = None) -> None:
        self.ledger = ledger
        self.mode = mode
        self.credential_error = credential_error
        self.submitted = 0
        self.canceled = 0
        self.closed = 0

    def _ensure_credentials(self) -> None:
        if self.credential_error is not None:
            raise self.credential_error

    def submit_order(self, *, request, report, live_price, position, trade_id, order_id, order_ticket) -> SubmitOrderResult:
        self.submitted += 1
        broker_status = "filled" if self.mode == "filled" else "new"
        row = {
            "trade_id": trade_id,
            "order_id": order_id,
            "ticker": request.ticker,
            "instrument_type": "equity",
            "suggested_contracts": position["suggested_contracts"],
            "broker_name": "alpaca_paper",
            "broker_order_id": "broker-entry-1",
            "broker_client_order_id": order_id,
            "broker_status": broker_status,
            "broker_filled_avg_price": request.limit_price if self.mode == "filled" else None,
            "actual_fill_price": request.limit_price if self.mode == "filled" else None,
            "live_price_at_open": request.limit_price,
            "order_type": "limit",
            "time_in_force": "day",
            "contract_mid_at_open": request.limit_price / 100.0,
            "broker_side": "BUY",
            "broker_fractionable": True,
            "route_correlation_id": order_ticket.get("route_correlation_id"),
        }
        if self.mode == "filled":
            self.ledger.open.append(dict(row))
            return SubmitOrderResult(
                position_opened=True,
                record=dict(row),
                pending_order=None,
                broker_name="alpaca_paper",
                broker_order_id="broker-entry-1",
                broker_status="filled",
                broker_response={"id": "broker-entry-1", "client_order_id": order_id, "symbol": request.ticker, "status": "filled", "filled_avg_price": request.limit_price, "qty": position["suggested_contracts"]},
            )
        self.ledger.pending.append(dict(row))
        return SubmitOrderResult(
            position_opened=False,
            record=dict(row),
            pending_order=dict(row),
            broker_name="alpaca_paper",
            broker_order_id="broker-entry-1",
            broker_status="new",
            broker_response={"id": "broker-entry-1", "client_order_id": order_id, "symbol": request.ticker, "status": "new", "qty": position["suggested_contracts"]},
        )

    def cancel_order(self, *, order_id: str) -> CancelOrderResult | None:
        self.canceled += 1
        row = self.ledger.cancel_pending(order_id)
        if row is None:
            return None
        row["broker_status"] = "canceled"
        return CancelOrderResult(
            canceled_order=row,
            broker_name="alpaca_paper",
            broker_order_id=row.get("broker_order_id"),
            broker_status="canceled",
            broker_response={"id": row.get("broker_order_id"), "status": "canceled"},
        )

    def close_position(self, *, request, target_trade) -> ClosePositionResult:
        self.closed += 1
        closed = self.ledger.close_trade_by_index(
            request.trade_index,
            close_underlying_price=request.close_underlying_price,
            close_contract_mid=request.close_contract_mid,
            close_fraction=1.0,
            close_updates={
                "broker_close_order_id": "broker-close-1",
                "broker_close_status": "filled",
            },
        )
        return ClosePositionResult(
            closed_trade=closed,
            broker_name="alpaca_paper",
            broker_order_id="broker-close-1",
            broker_status="filled",
            broker_response={"id": "broker-close-1", "status": "filled"},
        )


class AutomationPaperOrderLifecycleSoakTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="paper-lifecycle-test", name="Paper Lifecycle Test", status="active")
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
                "enabled": False,
                "armed": False,
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

    def _broker_snapshot(self, *, terminal: str = "canceled") -> dict:
        order_status = "canceled" if terminal == "canceled" else "filled"
        return {
            "broker_available": True,
            "account": {"id": "paper-account", "equity": "10000"},
            "orders": [
                {
                    "id": "broker-entry-1",
                    "client_order_id": "unused",
                    "symbol": "SPY",
                    "status": order_status,
                    "qty": "0.001",
                    "filled_qty": "0.001" if terminal == "closed" else "0",
                    "filled_avg_price": "0.01" if terminal == "closed" else None,
                }
            ],
            "positions": [],
        }

    def _patch_io(self, ledger: _Ledger, adapter: _FakePaperAdapter, *, terminal: str = "canceled", sync_state: str = "working"):
        notes_dir = tempfile.TemporaryDirectory()
        self.addCleanup(notes_dir.cleanup)
        notes_path = Path(notes_dir.name) / "notes.json"
        notes_path.write_text("[]", encoding="utf-8")
        sync_payload = {
            "synced": True,
            "summary": {"processed": 1, "changed": 0, sync_state: 1},
            "items": [
                {
                    "state": sync_state,
                    "changed": sync_state != "working",
                    "detail": f"Broker order is {sync_state}.",
                    "broker_order_id": "broker-entry-1",
                    "broker_status": "filled" if sync_state == "filled" else "new",
                    "slippage_bps": 0.0,
                }
            ],
        }
        return (
            patch.object(notes_service, "NOTES_PATH", notes_path),
            patch.object(automation_paper_order_lifecycle_soak_service, "get_execution_adapter_for", return_value=adapter),
            patch.object(automation_paper_order_lifecycle_soak_service, "sync_pending_orders_from_broker", return_value=sync_payload),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "read_pending_orders", side_effect=ledger.read_pending),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "read_open_trades", side_effect=ledger.read_open),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "read_closed_trades", side_effect=ledger.read_closed),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "update_pending_order", side_effect=ledger.update_pending),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "cancel_pending_order", side_effect=ledger.cancel_pending),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "update_open_trade", side_effect=ledger.update_open),
            patch.object(automation_paper_order_lifecycle_soak_service.sdm, "close_trade_by_index", side_effect=ledger.close_trade_by_index),
            patch.object(
                automation_paper_broker_reconciliation_service,
                "_fetch_broker_snapshot",
                return_value=(self._broker_snapshot(terminal=terminal), []),
            ),
            notes_path,
        )

    def test_action_schema_accepts_lifecycle_soak(self) -> None:
        payload = OrganizationTradeAutomationActionRequest(action="run_paper_order_lifecycle_soak")

        self.assertEqual(payload.action, "run_paper_order_lifecycle_soak")

    def test_missing_paper_credentials_warns_without_order_mutation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        ledger = _Ledger()
        adapter = _FakePaperAdapter(ledger, credential_error=RuntimeError("missing paper keys"))
        patches = self._patch_io(ledger, adapter)
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1]:
            report = automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "warning")
        self.assertEqual(adapter.submitted, 0)
        self.assertFalse(ledger.pending)
        self.assertFalse(ledger.open)
        self.assertIn("paper_credentials_missing", {item["key"] for item in report["warnings"]})
        self.assertEqual(len([note for note in notes if "order-lifecycle-soak" in note.get("tags", [])]), 1)

    def test_preflight_rejects_live_profile(self) -> None:
        db, tenant = self._db()
        state = self._state()

        with self.assertRaises(ValidationServiceError):
            automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_live",
                now=FIXED_NOW,
            )

    def test_submit_unfilled_cancel_path_records_terminal_evidence(self) -> None:
        db, tenant = self._db()
        state = self._state()
        ledger = _Ledger()
        adapter = _FakePaperAdapter(ledger, mode="working")
        patches = self._patch_io(ledger, adapter, terminal="canceled", sync_state="working")
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3], context_patches[4], context_patches[5], context_patches[6], context_patches[7], context_patches[8], context_patches[9], context_patches[10]:
            report = automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["terminal_state"], "canceled")
        self.assertEqual(report["reconciliation_status"], "clean")
        self.assertTrue(report["cancel_evidence"]["canceled"])
        self.assertEqual(adapter.submitted, 1)
        self.assertEqual(adapter.canceled, 1)
        self.assertFalse(ledger.pending)
        self.assertEqual(state["runtime"]["paper_order_lifecycle_soak_last_report"]["status"], "completed")
        self.assertEqual(len([note for note in notes if "order-lifecycle-soak" in note.get("tags", [])]), 1)
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.paper_order_lifecycle_soaked", audit_types)

    def test_submit_filled_path_closes_and_reconciles(self) -> None:
        db, tenant = self._db()
        state = self._state()
        ledger = _Ledger()
        adapter = _FakePaperAdapter(ledger, mode="filled")
        patches = self._patch_io(ledger, adapter, terminal="closed", sync_state="filled")
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1], context_patches[2], context_patches[3], context_patches[4], context_patches[5], context_patches[6], context_patches[7], context_patches[8], context_patches[9], context_patches[10]:
            report = automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )
            db.commit()

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["terminal_state"], "closed")
        self.assertEqual(report["reconciliation_status"], "clean")
        self.assertTrue(report["fill_evidence"])
        self.assertTrue(report["close_evidence"]["closed"])
        self.assertEqual(adapter.closed, 1)
        self.assertFalse(ledger.open)
        self.assertEqual(len(ledger.closed), 1)

    def test_reconciliation_blocker_blocks_lifecycle_soak(self) -> None:
        db, tenant = self._db()
        state = self._state()
        ledger = _Ledger()
        adapter = _FakePaperAdapter(ledger, mode="working")
        patches = self._patch_io(ledger, adapter, terminal="canceled", sync_state="working")
        *context_patches, _notes_path = patches
        blocked_reconciliation = {
            "status": "blocked",
            "blockers": [{"key": "orphan_broker_order", "detail": "Mismatch."}],
            "warnings": [],
            "related_note_id": "recon-note",
        }

        with (
            context_patches[0],
            context_patches[1],
            context_patches[2],
            context_patches[3],
            context_patches[4],
            context_patches[5],
            context_patches[6],
            context_patches[7],
            context_patches[8],
            context_patches[9],
            patch.object(
                automation_paper_order_lifecycle_soak_service.automation_paper_broker_reconciliation_service,
                "run_paper_broker_reconciliation",
                return_value=blocked_reconciliation,
            ),
        ):
            report = automation_paper_order_lifecycle_soak_service.run_paper_order_lifecycle_soak(
                db,
                tenant=tenant,
                state=state,
                profile_key="personal_paper",
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("reconciliation_blocked", {item["key"] for item in report["blockers"]})

    def test_action_path_persists_snapshot_without_setting_or_live_gate_mutation(self) -> None:
        db, tenant = self._db()
        state = self._state()
        trade_automation_service._write_trade_automation_state(tenant, state, profile_key="personal_paper")
        db.commit()
        before_settings = json.loads(json.dumps(state["settings"], sort_keys=True))
        ledger = _Ledger()
        adapter = _FakePaperAdapter(ledger, mode="working")
        patches = self._patch_io(ledger, adapter, terminal="canceled", sync_state="working")
        *context_patches, notes_path = patches
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="paper-lifecycle",
            user_id="paper-lifecycle",
            permissions=("tenant.manage_support",),
        )

        with (
            context_patches[0],
            context_patches[1],
            context_patches[2],
            context_patches[3],
            context_patches[4],
            context_patches[5],
            context_patches[6],
            context_patches[7],
            context_patches[8],
            context_patches[9],
            context_patches[10],
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
            patch.object(
                trade_automation_service,
                "get_trade_summary",
                return_value={"rollout_readiness": {"allows_live_rollout": False}},
            ),
            patch.object(
                trade_automation_service,
                "_build_personal_account_summary",
                return_value={"provider": "alpaca_paper", "connected": True, "status": "connected", "equity": 10000},
            ),
            patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", side_effect=ledger.read_open),
        ):
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="run_paper_order_lifecycle_soak"),
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        after_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_paper")
        self.assertEqual(after_state["settings"], before_settings)
        self.assertEqual(snapshot["paper_order_lifecycle_soak"]["status"], "completed")
        self.assertEqual(snapshot["paper_order_lifecycle_soak"]["terminal_state"], "canceled")
        self.assertTrue(snapshot["available_actions"]["can_run_paper_order_lifecycle_soak"])
        self.assertEqual(len([note for note in notes if "order-lifecycle-soak" in note.get("tags", [])]), 1)


if __name__ == "__main__":
    unittest.main()
