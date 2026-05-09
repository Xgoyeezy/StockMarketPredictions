from __future__ import annotations

import unittest

from backend.core.config import settings
from backend.services.live_order_intent_service import approve_live_order, create_live_order_intent
from backend.services.live_trading_authorization_service import create_live_authorization
from backend.services.live_trading_session_service import arm_live_strategy, start_live_strategy
from tests.live_control_test_support import seed_live_ready_strategy
from tests.productized_control_plane_test_support import build_test_context


class LiveOrderFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_test_context(slug="live-order-test", plan_key="professional")
        self.fixture = seed_live_ready_strategy(self.context)
        self.old_flags = (settings.feature_live_trading, settings.alpaca_live_trading_enabled)
        object.__setattr__(settings, "feature_live_trading", True)
        object.__setattr__(settings, "alpaca_live_trading_enabled", True)
        authorization_id = create_live_authorization(
            self.context.db,
            current_user=self.context.current_user,
            request={
                "strategy_id": self.fixture.strategy.id,
                "strategy_version_id": self.fixture.version.id,
                "linked_account_id": self.fixture.account.id,
                "signed": True,
            },
        )["authorization"]["id"]
        arm_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id, request={"authorization_id": authorization_id})
        start_live_strategy(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id, request={})

    def tearDown(self) -> None:
        object.__setattr__(settings, "feature_live_trading", self.old_flags[0])
        object.__setattr__(settings, "alpaca_live_trading_enabled", self.old_flags[1])
        self.context.close()

    def test_live_order_intent_requires_approval_and_records_receipt_on_approval(self) -> None:
        created = create_live_order_intent(
            self.context.db,
            current_user=self.context.current_user,
            strategy_id=self.fixture.strategy.id,
            request={"symbol": "AAPL", "instrument_type": "equity", "side": "buy", "quantity": 5, "limit_price": 100, "notional_value": 500},
        )
        self.assertEqual(created["order_intent"]["status"], "pending_approval")
        self.assertTrue(created["order_intent"]["requires_user_approval"])

        approved = approve_live_order(self.context.db, current_user=self.context.current_user, order_intent_id=created["order_intent"]["id"], request={"note": "test"})
        self.assertEqual(approved["order_intent"]["status"], "approved")
        self.assertEqual(approved["broker_submission_status"], "not_submitted")
        self.assertEqual(approved["receipt"]["status"], "not_submitted")

    def test_duplicate_order_is_blocked_by_pretrade_risk(self) -> None:
        create_live_order_intent(
            self.context.db,
            current_user=self.context.current_user,
            strategy_id=self.fixture.strategy.id,
            request={"symbol": "AAPL", "instrument_type": "equity", "side": "buy", "quantity": 1, "limit_price": 100, "notional_value": 100, "duplicate_key": "dup-1"},
        )
        duplicate = create_live_order_intent(
            self.context.db,
            current_user=self.context.current_user,
            strategy_id=self.fixture.strategy.id,
            request={"symbol": "AAPL", "instrument_type": "equity", "side": "buy", "quantity": 1, "limit_price": 100, "notional_value": 100, "duplicate_key": "dup-1"},
        )
        self.assertEqual(duplicate["order_intent"]["status"], "blocked")
        blockers = duplicate["order_intent"]["latest_risk_check"]["blockers"]
        self.assertTrue(any(item["key"] == "duplicate_order" for item in blockers))


if __name__ == "__main__":
    unittest.main()
