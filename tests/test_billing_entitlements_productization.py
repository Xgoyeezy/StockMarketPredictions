from __future__ import annotations

import unittest

from backend.models.saas import EntitlementUsage
from backend.services.billing_service import (
    PLAN_CATALOG,
    get_billing_entitlements,
    increment_entitlement_usage,
    list_billing_plans,
    require_entitlement,
)
from backend.services.exceptions import ForbiddenError
from tests.productized_control_plane_test_support import build_test_context


class BillingEntitlementsProductizationTests(unittest.TestCase):
    def tearDown(self) -> None:
        if hasattr(self, "context"):
            self.context.close()

    def test_professional_plan_and_product_entitlements_exist(self) -> None:
        self.assertIn("professional", PLAN_CATALOG)
        self.assertTrue(PLAN_CATALOG["professional"]["recommended"])
        entitlements = PLAN_CATALOG["professional"]["entitlements"]
        for key in [
            "strategy_lifecycle",
            "strategy_versions",
            "automation_basic",
            "automation_advanced",
            "readiness_scoring",
            "promotion_gates",
            "risk_engine",
            "audit_replay",
            "audit_exports",
            "execution_quality",
            "options_automation",
            "live_canary",
            "live_authorizations",
            "live_sessions",
            "live_order_approvals",
            "priority_support",
        ]:
            self.assertIn(key, entitlements)
            self.assertTrue(entitlements[key]["enabled"])
        self.assertIn("multi_account_controls", entitlements)
        self.assertFalse(entitlements["multi_account_controls"]["enabled"])

    def test_public_plan_pricing_and_positioning_metadata(self) -> None:
        expected = {
            "personal": (29, 249, "Paper-first research"),
            "pro": (79, 699, "Paper-first + readiness"),
            "desk": (199, 1799, "Operator desk"),
            "enterprise": (2499, 24990, "Custom control plane"),
        }

        payload = list_billing_plans()
        items = {item["key"]: item for item in payload["items"]}

        for key, (monthly, annual, live_mode) in expected.items():
            with self.subTest(plan=key):
                plan = items[key]
                self.assertTrue(plan["public"])
                self.assertEqual(plan["monthly_price"], monthly)
                self.assertEqual(plan["annual_price"], annual)
                self.assertEqual(plan["live_mode"], live_mode)
                self.assertIn("proof_points", plan)

        self.assertTrue(items["desk"]["recommended"])
        self.assertFalse(items["white-label"]["public"])

    def test_live_mode_entitlement_ladder_matches_public_tiers(self) -> None:
        starter = PLAN_CATALOG["starter"]["entitlements"]
        pro = PLAN_CATALOG["pro"]["entitlements"]
        professional = PLAN_CATALOG["professional"]["entitlements"]
        team = PLAN_CATALOG["team"]["entitlements"]

        self.assertEqual(starter["strategy_lifecycle"]["limit"], 1)
        self.assertTrue(starter["automation_basic"]["enabled"])
        self.assertFalse(starter["live_sessions"]["enabled"])
        self.assertTrue(pro["live_order_approvals"]["enabled"])
        self.assertTrue(pro["live_authorizations"]["enabled"])
        self.assertFalse(pro["live_sessions"]["enabled"])
        self.assertTrue(professional["live_sessions"]["enabled"])
        self.assertEqual(professional["strategy_lifecycle"]["limit"], 10)
        self.assertTrue(team["multi_account_controls"]["enabled"])

    def test_starter_blocks_premium_audit_exports(self) -> None:
        self.context = build_test_context(slug="billing-starter-test", plan_key="starter")

        with self.assertRaises(ForbiddenError):
            require_entitlement(self.context.db, self.context.current_user, "audit_exports")

    def test_entitlement_usage_persists_monthly_usage(self) -> None:
        self.context = build_test_context(slug="billing-usage-test", plan_key="professional")

        usage = increment_entitlement_usage(self.context.db, self.context.current_user, "strategy_lifecycle", amount=2, period_key="2026-04")
        self.context.db.commit()

        self.assertEqual(usage["used_count"], 2)
        row = self.context.db.query(EntitlementUsage).one()
        self.assertEqual(row.metric_key, "strategy_lifecycle")
        self.assertEqual(row.period_key, "2026-04")

    def test_white_label_product_entitlements_remain_enabled(self) -> None:
        self.context = build_test_context(slug="billing-white-label-test", plan_key="white-label")

        entitlements = {item["key"]: item for item in get_billing_entitlements(self.context.db, self.context.current_user)["items"]}

        self.assertTrue(entitlements["strategy_lifecycle"]["enabled"])
        self.assertTrue(entitlements["audit_exports"]["enabled"])
        self.assertTrue(entitlements["multi_account_controls"]["enabled"])


if __name__ == "__main__":
    unittest.main()
