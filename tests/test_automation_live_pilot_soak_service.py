from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
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
    automation_live_pilot_soak_service,
    notes_service,
    trade_automation_service,
)
from backend.services.execution.types import CancelOrderResult, ClosePositionResult, SubmitOrderResult


FIXED_NOW = datetime(2026, 4, 24, 20, 40, tzinfo=timezone.utc)


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

    def close_trade_by_index(
        self,
        trade_index: int,
        close_underlying_price: float,
        close_contract_mid: float,
        close_fraction: float = 1.0,
        close_updates: dict | None = None,
    ) -> dict | None:
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


class _FakeLiveAdapter:
    adapter_name = "alpaca_live"

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
            "broker_name": "alpaca_live",
            "broker_order_id": "live-broker-entry-1",
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
                broker_name="alpaca_live",
                broker_order_id="live-broker-entry-1",
                broker_status="filled",
                broker_response={"id": "live-broker-entry-1", "client_order_id": order_id, "symbol": request.ticker, "status": "filled"},
            )
        self.ledger.pending.append(dict(row))
        return SubmitOrderResult(
            position_opened=False,
            record=dict(row),
            pending_order=dict(row),
            broker_name="alpaca_live",
            broker_order_id="live-broker-entry-1",
            broker_status="new",
            broker_response={"id": "live-broker-entry-1", "client_order_id": order_id, "symbol": request.ticker, "status": "new"},
        )

    def cancel_order(self, *, order_id: str) -> CancelOrderResult | None:
        self.canceled += 1
        row = self.ledger.cancel_pending(order_id)
        if row is None:
            return None
        row["broker_status"] = "canceled"
        return CancelOrderResult(
            canceled_order=row,
            broker_name="alpaca_live",
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
                "broker_close_order_id": "live-broker-close-1",
                "broker_close_status": "filled",
            },
        )
        return ClosePositionResult(
            closed_trade=closed,
            broker_name="alpaca_live",
            broker_order_id="live-broker-close-1",
            broker_status="filled",
            broker_response={"id": "live-broker-close-1", "status": "filled"},
        )


class AutomationLivePilotSoakTests(unittest.TestCase):
    def _db(self):
        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, future=True)
        db = SessionLocal()
        tenant = Tenant(slug="live-pilot-soak-test", name="Live Pilot Soak Test", status="active")
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
        self.addCleanup(engine.dispose)
        self.addCleanup(db.close)
        return db, tenant

    def _paper_state(self, *, enabled: bool = True) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": False,
                "armed": False,
                "kill_switch": False,
                "execution_intent": "broker_paper",
                "live_pilot_soak_enabled": enabled,
                "live_pilot_max_notional": 10.0,
                "live_pilot_symbol": "SPY",
                "live_pilot_approval_ttl_minutes": 15,
                "live_pilot_cancel_timeout_seconds": 30,
            }
        )
        state["runtime"]["live_pilot_readiness_last_report"] = {
            "status": "ready_to_request_approval",
            "broker_live_gate_status": "open",
            "safety_lock_status": "clear",
            "warnings": [],
        }
        return state

    def _live_state(self, *, enabled: bool = False, armed: bool = False, kill_switch: bool = False) -> dict:
        state = trade_automation_service._normalize_trade_automation_profile_state({})
        state["settings"].update(
            {
                "enabled": enabled,
                "armed": armed,
                "kill_switch": kill_switch,
                "execution_intent": "broker_live",
            }
        )
        return state

    def _rollout(self) -> dict:
        return {"allows_live_rollout": True, "status": "open"}

    def _patch_io(self, ledger: _Ledger, adapter: _FakeLiveAdapter, *, sync_state: str = "working"):
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
                    "detail": f"Broker live order is {sync_state}.",
                    "broker_order_id": "live-broker-entry-1",
                    "broker_status": "filled" if sync_state == "filled" else "new",
                    "slippage_bps": 0.0,
                }
            ],
        }
        return (
            patch.object(notes_service, "NOTES_PATH", notes_path),
            patch.object(
                automation_live_pilot_soak_service,
                "settings",
                SimpleNamespace(
                    alpaca_live_trading_enabled=True,
                    alpaca_live_api_key_id="live-key",
                    alpaca_live_api_secret_key="live-secret",
                    alpaca_api_key_id="",
                    alpaca_api_secret_key="",
                ),
            ),
            patch.object(automation_live_pilot_soak_service, "get_execution_adapter_for", return_value=adapter),
            patch.object(automation_live_pilot_soak_service, "sync_pending_orders_from_broker", return_value=sync_payload),
            patch.object(automation_live_pilot_soak_service.sdm, "get_live_price", return_value=500.0),
            patch.object(automation_live_pilot_soak_service.sdm, "read_pending_orders", side_effect=ledger.read_pending),
            patch.object(automation_live_pilot_soak_service.sdm, "read_open_trades", side_effect=ledger.read_open),
            patch.object(automation_live_pilot_soak_service.sdm, "read_closed_trades", side_effect=ledger.read_closed),
            patch.object(automation_live_pilot_soak_service.sdm, "update_pending_order", side_effect=ledger.update_pending),
            patch.object(automation_live_pilot_soak_service.sdm, "cancel_pending_order", side_effect=ledger.cancel_pending),
            patch.object(automation_live_pilot_soak_service.sdm, "update_open_trade", side_effect=ledger.update_open),
            patch.object(automation_live_pilot_soak_service.sdm, "close_trade_by_index", side_effect=ledger.close_trade_by_index),
            notes_path,
        )

    def test_action_schema_accepts_live_pilot_soak_actions(self) -> None:
        prepare = OrganizationTradeAutomationActionRequest(action="prepare_live_pilot_soak")
        run = OrganizationTradeAutomationActionRequest(action="run_live_pilot_soak")

        self.assertEqual(prepare.action, "prepare_live_pilot_soak")
        self.assertEqual(run.action, "run_live_pilot_soak")

    def test_disabled_soak_blocks_prepare_without_order_path(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state(enabled=False)
        live_state = self._live_state()
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger)
        patches = self._patch_io(ledger, adapter)
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1], patch.object(
            automation_live_pilot_soak_service,
            "get_execution_adapter_for",
            side_effect=AssertionError("prepare must not reach adapter"),
        ):
            report = automation_live_pilot_soak_service.prepare_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "blocked")
        self.assertIn("live_pilot_soak_disabled", {item["key"] for item in report["blockers"]})
        self.assertEqual(adapter.submitted, 0)
        self.assertEqual(len([note for note in notes if "live-pilot-soak" in note.get("tags", [])]), 1)

    def test_prepare_creates_fresh_approval_note_and_audit(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger)
        patches = self._patch_io(ledger, adapter)
        *context_patches, notes_path = patches

        with context_patches[0], context_patches[1]:
            report = automation_live_pilot_soak_service.prepare_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "approved")
        self.assertEqual(adapter.submitted, 0)
        self.assertEqual(paper_state["runtime"]["live_pilot_soak_approval"]["status"], "approved")
        self.assertEqual(len([note for note in notes if "live-pilot-soak" in note.get("tags", [])]), 1)
        audit_types = [row.event_type for row in db.query(AuditEvent).all()]
        self.assertIn("trade_automation.live_pilot_soak_prepared", audit_types)

    def test_stale_approval_blocks_run_without_submit(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        paper_state["runtime"]["live_pilot_soak_approval"] = {
            "approval_id": "old-approval",
            "status": "approved",
            "expires_at": (FIXED_NOW - timedelta(minutes=1)).isoformat(),
            "symbol": "SPY",
            "notional_cap": 10,
            "cancel_timeout_seconds": 30,
        }
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger)
        patches = self._patch_io(ledger, adapter)
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1]:
            report = automation_live_pilot_soak_service.run_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("approval_expired", {item["key"] for item in report["blockers"]})
        self.assertEqual(adapter.submitted, 0)

    def test_clean_submit_cancel_path_records_terminal_evidence(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger, mode="working")
        patches = self._patch_io(ledger, adapter, sync_state="working")
        *context_patches, notes_path = patches
        current_user = SimpleNamespace(tenant_id=tenant.id, tenant_slug=tenant.slug, auth_subject="live-soak", user_id="live-soak")

        with context_patches[0], context_patches[1]:
            automation_live_pilot_soak_service.prepare_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
        with context_patches[0], context_patches[1], context_patches[2], context_patches[3], context_patches[4], context_patches[5], context_patches[6], context_patches[7], context_patches[8], context_patches[9], context_patches[10], context_patches[11]:
            report = automation_live_pilot_soak_service.run_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                current_user=current_user,
                now=FIXED_NOW,
            )
            db.commit()
            notes = json.loads(notes_path.read_text(encoding="utf-8"))

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["terminal_state"], "canceled")
        self.assertEqual(report["reconciliation_status"], "clean")
        self.assertEqual(report["limit_price"], 475.0)
        self.assertEqual(report["quantity"], 0.001)
        self.assertEqual(adapter.submitted, 1)
        self.assertEqual(adapter.canceled, 1)
        self.assertFalse(ledger.pending)
        self.assertEqual(len([note for note in notes if "live-pilot-soak" in note.get("tags", [])]), 1)

    def test_unexpected_fill_closes_and_reconciles(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger, mode="filled")
        patches = self._patch_io(ledger, adapter, sync_state="filled")
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1]:
            automation_live_pilot_soak_service.prepare_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )
        with context_patches[0], context_patches[1], context_patches[2], context_patches[4], context_patches[5], context_patches[6], context_patches[7], context_patches[8], context_patches[9], context_patches[10], context_patches[11]:
            report = automation_live_pilot_soak_service.run_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["terminal_state"], "closed")
        self.assertEqual(report["reconciliation_status"], "clean")
        self.assertEqual(adapter.closed, 1)
        self.assertFalse(ledger.open)
        self.assertEqual(len(ledger.closed), 1)

    def test_live_profile_enabled_or_armed_blocks_prepare(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state(enabled=True, armed=True)
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger)
        patches = self._patch_io(ledger, adapter)
        *context_patches, _notes_path = patches

        with context_patches[0], context_patches[1]:
            report = automation_live_pilot_soak_service.prepare_live_pilot_soak(
                db,
                tenant=tenant,
                paper_state=paper_state,
                live_state=live_state,
                rollout_readiness=self._rollout(),
                now=FIXED_NOW,
            )

        self.assertEqual(report["status"], "blocked")
        self.assertIn("live_profile_enabled_or_armed", {item["key"] for item in report["blockers"]})
        self.assertEqual(adapter.submitted, 0)

    def test_action_path_persists_snapshot_without_live_gate_mutation(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()
        before_live_settings = json.loads(json.dumps(live_state["settings"], sort_keys=True))
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger, mode="working")
        patches = self._patch_io(ledger, adapter, sync_state="working")
        *context_patches, _notes_path = patches
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="live-soak-action",
            user_id="live-soak-action",
            permissions=("tenant.manage_support",),
        )

        with (
            context_patches[0],
            context_patches[1],
            patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
            patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
            patch.object(
                trade_automation_service,
                "get_trade_summary",
                return_value={"rollout_readiness": self._rollout()},
            ),
            patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"provider": "alpaca_paper", "connected": True, "status": "connected", "equity": 10000}),
            patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
            patch.object(trade_automation_service.sdm, "read_open_trades", side_effect=ledger.read_open),
            patch.object(trade_automation_service.sdm, "read_pending_orders", side_effect=ledger.read_pending),
            patch.object(trade_automation_service.sdm, "read_closed_trades", side_effect=ledger.read_closed),
            patch.object(trade_automation_service.sdm, "monitor_open_trades", side_effect=ledger.read_open),
        ):
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="prepare_live_pilot_soak"),
            )

        after_live_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(after_live_state["settings"], before_live_settings)
        self.assertEqual(snapshot["live_pilot_soak"]["status"], "approved")
        self.assertTrue(snapshot["available_actions"]["can_prepare_live_pilot_soak"])
        self.assertTrue(snapshot["available_actions"]["can_run_live_pilot_soak"])
        self.assertEqual(adapter.submitted, 0)

    def test_action_run_consumes_approval_and_submits_once(self) -> None:
        db, tenant = self._db()
        paper_state = self._paper_state()
        live_state = self._live_state()
        trade_automation_service._write_trade_automation_state(tenant, paper_state, profile_key="personal_paper")
        trade_automation_service._write_trade_automation_state(tenant, live_state, profile_key="personal_live")
        db.commit()
        before_live_settings = json.loads(json.dumps(live_state["settings"], sort_keys=True))
        ledger = _Ledger()
        adapter = _FakeLiveAdapter(ledger, mode="working")
        patches = self._patch_io(ledger, adapter, sync_state="working")
        *context_patches, _notes_path = patches
        current_user = SimpleNamespace(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            auth_subject="live-soak-run-action",
            user_id="live-soak-run-action",
            permissions=("tenant.manage_support",),
        )
        def action_patches():
            return (
                patch.object(trade_automation_service, "_resolve_tenant_for_current_user", return_value=tenant),
                patch.object(trade_automation_service, "_resolve_user_for_current_user", return_value=None),
                patch.object(
                    trade_automation_service,
                    "get_trade_summary",
                    return_value={"rollout_readiness": self._rollout()},
                ),
                patch.object(trade_automation_service, "_build_personal_account_summary", return_value={"provider": "alpaca_paper", "connected": True, "status": "connected", "equity": 10000}),
                patch.object(trade_automation_service, "get_latest_trade_automation_equity_snapshot", return_value=None),
                patch.object(trade_automation_service.sdm, "monitor_open_trades", side_effect=ledger.read_open),
            )

        with ExitStack() as stack:
            for patcher in (context_patches[0], context_patches[1], *action_patches()):
                stack.enter_context(patcher)
            trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="prepare_live_pilot_soak"),
            )

        with ExitStack() as stack:
            for patcher in (
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
                context_patches[11],
                *action_patches(),
            ):
                stack.enter_context(patcher)
            snapshot = trade_automation_service.run_tenant_trade_automation_action(
                db,
                current_user=current_user,
                request=OrganizationTradeAutomationActionRequest(action="run_live_pilot_soak"),
            )

        after_live_state = trade_automation_service._read_trade_automation_state(tenant, profile_key="personal_live")
        self.assertEqual(after_live_state["settings"], before_live_settings)
        self.assertEqual(snapshot["live_pilot_soak"]["status"], "completed")
        self.assertEqual(snapshot["live_pilot_soak"]["terminal_state"], "canceled")
        self.assertEqual(adapter.submitted, 1)
        self.assertEqual(adapter.canceled, 1)


if __name__ == "__main__":
    unittest.main()
