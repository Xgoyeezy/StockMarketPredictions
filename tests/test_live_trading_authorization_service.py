from __future__ import annotations

import unittest

from backend.services.live_trading_authorization_service import create_live_authorization, revoke_live_authorization
from tests.live_control_test_support import seed_live_ready_strategy
from tests.productized_control_plane_test_support import build_test_context


class LiveTradingAuthorizationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_test_context(slug="live-auth-test", plan_key="professional")
        self.fixture = seed_live_ready_strategy(self.context)

    def tearDown(self) -> None:
        self.context.close()

    def test_create_and_revoke_signed_live_authorization(self) -> None:
        result = create_live_authorization(
            self.context.db,
            current_user=self.context.current_user,
            request={
                "strategy_id": self.fixture.strategy.id,
                "strategy_version_id": self.fixture.version.id,
                "linked_account_id": self.fixture.account.id,
                "max_capital_allocation": 25000,
                "max_daily_loss": 500,
                "max_order_notional": 2500,
                "allowed_symbols": ["AAPL"],
                "allowed_instruments": ["equity"],
                "signed": True,
            },
        )

        authorization = result["authorization"]
        self.assertEqual(authorization["status"], "signed")
        self.assertEqual(authorization["strategy_id"], self.fixture.strategy.id)

        revoked = revoke_live_authorization(
            self.context.db,
            current_user=self.context.current_user,
            authorization_id=authorization["id"],
            request={"reason": "test revoke"},
        )
        self.assertEqual(revoked["authorization"]["status"], "revoked")


if __name__ == "__main__":
    unittest.main()
