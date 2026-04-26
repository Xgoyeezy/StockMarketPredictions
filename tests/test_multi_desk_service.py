from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class MultiDeskServiceTests(unittest.TestCase):
    def test_demo_tenant_seed_creates_three_desks_and_canonical_systematic_default(self) -> None:
        from backend.core.database import Base
        from backend.services import tenant_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="systematic-equities",
            demo_tenant_name="Systematic Equities Desk",
            demo_tenant_plan="pro",
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            payload = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="alpha-desk",
            )

        membership_slugs = sorted(item.tenant.slug for item in payload["memberships"])
        default_memberships = [item for item in payload["memberships"] if item.is_default]

        self.assertEqual(
            membership_slugs,
            ["macro", "stat-arb", "systematic-equities"],
        )
        self.assertEqual(payload["active_tenant"].slug, "systematic-equities")
        self.assertEqual(len(default_memberships), 1)
        self.assertEqual(default_memberships[0].tenant.slug, "systematic-equities")
        self.assertEqual(
            sorted(item["tenant"]["slug"] for item in payload["memberships_payload"]),
            ["macro", "stat-arb", "systematic-equities"],
        )

    def test_filter_frame_to_tenant_keeps_legacy_rows_only_for_systematic_desk(self) -> None:
        from backend.services.desk_service import filter_frame_to_tenant

        frame = pd.DataFrame(
            [
                {"trade_id": "legacy", "ticker": "SPY"},
                {"trade_id": "systematic", "ticker": "QQQ", "tenant_slug": "systematic-equities"},
                {"trade_id": "alpha", "ticker": "IWM", "tenant_slug": "alpha-desk"},
                {"trade_id": "stat", "ticker": "XLF", "tenant_slug": "stat-arb"},
                {"trade_id": "macro", "ticker": "TLT", "automation_tenant_slug": "macro"},
            ]
        )

        systematic = filter_frame_to_tenant(frame, tenant_slug="systematic-equities")
        stat_arb = filter_frame_to_tenant(frame, tenant_slug="stat-arb")
        macro = filter_frame_to_tenant(frame, tenant_slug="macro")

        self.assertEqual(set(systematic["trade_id"]), {"legacy", "systematic", "alpha"})
        self.assertEqual(set(stat_arb["trade_id"]), {"stat"})
        self.assertEqual(set(macro["trade_id"]), {"macro"})

    def test_build_desk_summaries_aggregates_counts_without_merging_desks(self) -> None:
        from backend.core.database import Base
        from backend.models.saas import BrokerageLinkedAccount, OrderEventRecord, TradeApprovalIntent, User
        from backend.services import desk_service, tenant_service, workspace_service

        engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        Base.metadata.create_all(bind=engine)

        fake_settings = SimpleNamespace(
            demo_tenant_slug="systematic-equities",
            demo_tenant_name="Systematic Equities Desk",
            demo_tenant_plan="pro",
        )

        open_trades = pd.DataFrame(
            [
                {
                    "trade_id": "legacy-open",
                    "ticker": "SPY",
                    "updated_at": "2026-04-20T14:00:00Z",
                },
                {
                    "trade_id": "stat-open",
                    "ticker": "XLF",
                    "tenant_id": "STAT_ID",
                    "tenant_slug": "stat-arb",
                    "updated_at": "2026-04-21T14:00:00Z",
                },
            ]
        )
        pending_orders = pd.DataFrame(
            [
                {
                    "order_id": "stat-pending",
                    "ticker": "XLF",
                    "tenant_id": "STAT_ID",
                    "tenant_slug": "stat-arb",
                    "updated_at": "2026-04-21T14:05:00Z",
                }
            ]
        )
        closed_trades = pd.DataFrame(
            [
                {
                    "trade_id": "macro-close",
                    "ticker": "TLT",
                    "tenant_id": "MACRO_ID",
                    "tenant_slug": "macro",
                    "closed_at": "2026-04-22T15:00:00Z",
                }
            ]
        )
        forecast_journal = pd.DataFrame(
            [
                {
                    "ticker": "TLT",
                    "tenant_id": "MACRO_ID",
                    "tenant_slug": "macro",
                    "forecast_at": "2026-04-22T15:05:00Z",
                }
            ]
        )
        monitored_open_trades = pd.DataFrame(
            [
                {
                    "trade_id": "legacy-open",
                    "tenant_slug": "systematic-equities",
                    "monitor_action": "HOLD",
                },
                {
                    "trade_id": "stat-open",
                    "tenant_slug": "stat-arb",
                    "monitor_action": "EXIT",
                },
            ]
        )

        with (
            Session() as db,
            patch.object(tenant_service, "settings", fake_settings),
        ):
            identity = tenant_service.ensure_demo_tenant_for_user(
                db,
                auth_subject="demo-trader",
                email="demo@example.test",
                name="Demo Trader",
                provider="local-demo",
                requested_tenant_slug="systematic-equities",
            )
            actor = db.query(User).filter(User.auth_subject == "demo-trader").one()
            tenants_by_slug = {
                membership.tenant.slug: membership.tenant
                for membership in identity["memberships"]
            }
            systematic = tenants_by_slug["systematic-equities"]
            stat_arb = tenants_by_slug["stat-arb"]
            macro = tenants_by_slug["macro"]

            open_trades.loc[open_trades["trade_id"] == "stat-open", "tenant_id"] = stat_arb.id
            pending_orders.loc[:, "tenant_id"] = stat_arb.id
            closed_trades.loc[:, "tenant_id"] = macro.id
            forecast_journal.loc[:, "tenant_id"] = macro.id

            db.add_all(
                [
                    BrokerageLinkedAccount(
                        tenant=systematic,
                        owner_user=actor,
                        provider="alpaca",
                        label="Shared Paper - Systematic",
                        account_environment="paper",
                        connection_status="connected",
                        token_health="healthy",
                    ),
                    BrokerageLinkedAccount(
                        tenant=stat_arb,
                        owner_user=actor,
                        provider="alpaca",
                        label="Shared Paper - Stat Arb",
                        account_environment="paper",
                        connection_status="connected",
                        token_health="expired",
                    ),
                    BrokerageLinkedAccount(
                        tenant=macro,
                        owner_user=actor,
                        provider="alpaca",
                        label="Shared Live - Macro",
                        account_environment="live",
                        connection_status="connected",
                        token_health="healthy",
                    ),
                    OrderEventRecord(
                        tenant=macro,
                        ticker="TLT",
                        event_key="order.accepted",
                        status="accepted",
                    ),
                    TradeApprovalIntent(
                        tenant=stat_arb,
                        requester_user=actor,
                        approver_user=actor,
                        linked_account_id="linked-account-placeholder",
                        provider="alpaca",
                        execution_lane="linked_client",
                        status="pending_approval",
                        ticker="XLF",
                        instrument_type="equity",
                        account_environment="paper",
                        updated_at=pd.Timestamp("2026-04-21T14:10:00Z").to_pydatetime(),
                    ),
                ]
            )
            db.commit()

            current_user = SimpleNamespace(
                auth_subject="demo-trader",
                user_id="demo-trader",
                tenant_id=systematic.id,
                tenant_slug=systematic.slug,
            )

            def _workspace_items(user_id: str, *, tenant_slug: str | None = None, **_: object) -> dict[str, object]:
                items = {
                    "systematic-equities": [{"updated_at": "2026-04-20T13:00:00Z"}],
                    "stat-arb": [{"updated_at": "2026-04-21T13:30:00Z"}],
                    "macro": [{"updated_at": "2026-04-22T16:00:00Z"}],
                }
                return {"items": items.get(str(tenant_slug), [])}

            with (
                patch.object(desk_service.sdm, "read_open_trades", return_value=open_trades),
                patch.object(desk_service.sdm, "read_pending_orders", return_value=pending_orders),
                patch.object(desk_service.sdm, "read_closed_trades", return_value=closed_trades),
                patch.object(desk_service.sdm, "read_forecast_journal", return_value=forecast_journal),
                patch.object(desk_service.sdm, "monitor_open_trades", return_value=monitored_open_trades),
                patch.object(workspace_service, "list_workspaces", side_effect=_workspace_items),
            ):
                payload = desk_service.build_desk_summaries(db, current_user=current_user)

        self.assertEqual(payload["count"], 3)
        items_by_slug = {item["tenant_slug"]: item for item in payload["items"]}

        self.assertEqual(items_by_slug["systematic-equities"]["open_trades"], 1)
        self.assertEqual(items_by_slug["systematic-equities"]["pending_orders"], 0)
        self.assertEqual(items_by_slug["systematic-equities"]["paper_account_status"], "connected")
        self.assertEqual(items_by_slug["systematic-equities"]["live_account_status"], "not_linked")

        self.assertEqual(items_by_slug["stat-arb"]["open_trades"], 1)
        self.assertEqual(items_by_slug["stat-arb"]["pending_orders"], 1)
        self.assertEqual(items_by_slug["stat-arb"]["alerts"], 1)
        self.assertEqual(items_by_slug["stat-arb"]["paper_account_status"], "attention")
        self.assertEqual(items_by_slug["stat-arb"]["live_account_status"], "not_linked")

        self.assertEqual(items_by_slug["macro"]["open_trades"], 0)
        self.assertEqual(items_by_slug["macro"]["pending_orders"], 0)
        self.assertEqual(items_by_slug["macro"]["paper_account_status"], "not_linked")
        self.assertEqual(items_by_slug["macro"]["live_account_status"], "connected")
        self.assertIsNotNone(items_by_slug["macro"]["last_activity_at"])


if __name__ == "__main__":
    unittest.main()
