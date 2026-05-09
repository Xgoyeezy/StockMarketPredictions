from __future__ import annotations

import unittest

from backend.services.live_kill_switch_service import clear_live_kill_switch, get_live_kill_switch_state, trigger_live_kill_switch
from tests.live_control_test_support import seed_live_ready_strategy
from tests.productized_control_plane_test_support import build_test_context


class LiveKillSwitchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = build_test_context(slug="live-kill-test", plan_key="professional")
        self.fixture = seed_live_ready_strategy(self.context)

    def tearDown(self) -> None:
        self.context.close()

    def test_trigger_and_clear_strategy_kill_switch(self) -> None:
        triggered = trigger_live_kill_switch(
            self.context.db,
            current_user=self.context.current_user,
            request={"strategy_id": self.fixture.strategy.id, "scope": "strategy", "reason": "test kill"},
        )
        self.assertEqual(triggered["kill_switch"]["status"], "active")
        active = get_live_kill_switch_state(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id)
        self.assertTrue(active["active"])

        cleared = clear_live_kill_switch(
            self.context.db,
            current_user=self.context.current_user,
            request={"strategy_id": self.fixture.strategy.id, "scope": "strategy", "reason": "test clear"},
        )
        self.assertGreaterEqual(cleared["cleared_count"], 1)
        active = get_live_kill_switch_state(self.context.db, current_user=self.context.current_user, strategy_id=self.fixture.strategy.id)
        self.assertFalse(active["active"])


if __name__ == "__main__":
    unittest.main()
