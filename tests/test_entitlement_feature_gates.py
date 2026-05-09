from __future__ import annotations

import unittest

from backend.services.billing_service import PLAN_CATALOG, require_entitlement
from backend.services.exceptions import ForbiddenError
from backend.services.live_trading_authorization_service import create_live_authorization
from tests.live_control_test_support import seed_live_ready_strategy
from tests.productized_control_plane_test_support import build_test_context


class EntitlementFeatureGatesTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(self, "context"):
            self.context.close()

    def test_professional_plan_has_live_control_entitlements(self) -> None:
        entitlements = PLAN_CATALOG["professional"]["entitlements"]
        self.assertTrue(entitlements["live_canary"]["enabled"])
        self.assertTrue(entitlements["live_authorizations"]["enabled"])
        self.assertTrue(entitlements["live_sessions"]["enabled"])
        self.assertTrue(entitlements["live_order_approvals"]["enabled"])

    def test_starter_cannot_create_live_authorization(self) -> None:
        self.context = build_test_context(slug="live-starter-gate-test", plan_key="starter")
        fixture = seed_live_ready_strategy(self.context)

        with self.assertRaises(ForbiddenError):
            create_live_authorization(
                self.context.db,
                current_user=self.context.current_user,
                request={"strategy_id": fixture.strategy.id, "strategy_version_id": fixture.version.id, "linked_account_id": fixture.account.id, "signed": True},
            )

    def test_pro_can_use_assisted_live_authorizations_but_not_sessions(self) -> None:
        self.context = build_test_context(slug="live-pro-gate-test", plan_key="pro")
        require_entitlement(self.context.db, self.context.current_user, "live_order_approvals")
        require_entitlement(self.context.db, self.context.current_user, "live_authorizations")
        with self.assertRaises(ForbiddenError):
            require_entitlement(self.context.db, self.context.current_user, "live_sessions")


if __name__ == "__main__":
    unittest.main()
